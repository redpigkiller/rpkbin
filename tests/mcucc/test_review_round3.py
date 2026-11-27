"""Independent regressions for sampling, conversions and allocation estimates."""

from dataclasses import replace
from itertools import combinations, product
import random

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import InternalError
from rpkbin.mcucc.expand import MInstr, _Context, expand
from rpkbin.mcucc.formcheck import _sample, check_forms
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.ir import Imm, ir_eval
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.lower import _lower
from rpkbin.mcucc.opt import _optimize
from rpkbin.mcucc.regalloc import _Allocator
from rpkbin.mcucc.target import Cls, Form, I, R, RegLoc, T, units
from rpkbin.mcucc.verify import _verify
from test_formcheck import toy
from test_fuzz import run_case
from test_review import _execute


CONFIG = CompileConfig({}, False)
HEADER = 'from rpkbin.mcucc.lang import u8, u16, Ptr, mem\n'


def _three_target():
    base = toy("t1", 8)[0]
    three = Form("add_three", "add", 8,
                 (R("d", "g"), R("left", "g"), R("right", "g")),
                 {"dst": "d", "a": "left", "b": "right"}, 1,
                 "add3 {d}, {left}, {right}")
    from machine import verified
    return verified(replace(base, forms=(three, *base.forms)), ())[:2]


@pytest.mark.parametrize("seed", (0, 7, 71))
def test_cr17_encoding_b_as_a_is_detected(seed):
    target, binding = _three_target()
    correct = target.forms[0].encode
    bad = replace(target.forms[0], encode=lambda v: correct({**v, "right": v["left"]}))
    target = replace(target, forms=(bad, *target.forms[1:]))
    failures = check_forms(target, binding, (seed,))
    assert any(f.form_id == "add_three" and f.check == "C2" for f in failures)


def test_cr17_correct_independent_sources_pass_and_execute():
    target, binding = _three_target()
    assert check_forms(target, binding, (0,)) == []
    source = HEADER + 'def f(a:u8,b:u8)->u8:\n    return a+b\n'
    result = compile_source(source, target, CONFIG)
    assert _execute(result, target, binding, (3, 4))[0] == 7


@pytest.mark.parametrize("order", (("d", "left", "right"), ("right", "d", "left")))
def test_cr17_samples_cover_all_feasible_overlap_patterns(order):
    target = toy("t1", 8)[0]
    form = Form("three", "add", 8, tuple(R(n, "g") for n in order),
                {"dst": "d", "a": "left", "b": "right"}, 1, "three {d}, {left}, {right}")
    rng = random.Random(0)
    patterns = set()
    for index in range(12):
        sample = _sample(target, form, index, rng)
        patterns.add(tuple(bool(units(target, sample[a]) & units(target, sample[b]))
                           for a, b in combinations(("d", "left", "right"), 2)))
    assert patterns == {(True, True, True), (True, False, False),
                        (False, True, False), (False, False, True), (False, False, False)}


def test_cr17_alias_part_overlap_and_disjoint_samples_are_retained():
    target = toy("t2", 16)[0]
    form = next(f for f in target.forms if f.op == "ld_ind")
    rng = random.Random(0)
    names = tuple(o.name for o in form.operands if hasattr(o, "cls"))
    patterns = set()
    for index in range(12):
        sample = _sample(target, form, index, rng)
        patterns.add(bool(units(target, sample[names[0]]) & units(target, sample[names[1]])))
    assert patterns == {True, False}


@pytest.mark.parametrize("expression,expected", (("1+2", 3), ("1<<2", 4),
    ("(3*4)//2", 6), ("(~1)&7", 6), ("(-3)+8", 5), ("+3", 3), ("(1<<8)+3", 259)))
def test_cr18_module_constant_arithmetic_across_execution_engines(expression, expected):
    target, binding, _ = toy("t2", 8)
    source = HEADER + f'K = {expression}\ndef f()->u16:\n    return K\n'
    result = compile_source(source, target, CONFIG)
    memory = {"sram": [0] * 256}
    assert run_reference(source, "f", (), memory, addr_bits={"sram": 8})[0] == expected
    module = _lower(_parse(source, target, "constant.py"))
    assert ir_eval(module["f"], (), memory, module)[0] == expected
    module["f"] = _optimize(module["f"])
    assert ir_eval(module["f"], (), memory, module)[0] == expected
    assert _execute(result, target, binding, ())[0] == expected


@pytest.mark.parametrize("bits", (8, 16))
@pytest.mark.parametrize("operation", ("return", "load", "store"))
def test_cr19_pointer_constant_wraps_before_use(bits, operation):
    target, binding, _ = toy("t2", bits)
    value = (1 << bits) + 1
    body = {"return": f'def f()->P:\n    return P({value})\n',
            "load": f'def f()->u8:\n    return mem.sram[P({value})]\n',
            "store": f'def f():\n    mem.sram[P({value})] = u8(9)\n'}[operation]
    source = HEADER + 'P = Ptr("sram")\n' + body
    module = _lower(_parse(source, target, "pointer.py"))
    for block in module["f"].blocks:
        for instruction in (*block.instrs, block.term):
            for argument in instruction.args:
                if isinstance(argument, Imm):
                    assert 0 <= argument.value < 1 << argument.width
    result = compile_source(source, target, CONFIG)
    actual, after, memory = run_case(result, target, binding, {}, "f", (), 19)
    expected, reference_memory = run_reference(source, "f", (), memory, addr_bits={"sram": bits})
    assert actual == expected
    assert after == reference_memory
    if operation == "return":
        assert actual == 1
    elif operation == "store":
        assert after["sram"][1] == 9
    assert ir_eval(module["f"], (), memory, module) == (expected, reference_memory)
    module["f"] = _optimize(module["f"])
    assert ir_eval(module["f"], (), memory, module) == (expected, reference_memory)


def test_cr19_pointer_conversion_uses_space_width():
    target, binding, _ = toy("t2", 16)
    source = HEADER + 'P = Ptr("sram")\ndef f()->P:\n    return P(257)\n'
    assert _execute(compile_source(source, target, CONFIG), target, binding, ())[0] == 257


def _estimate_fixture(op):
    base = toy("t1", 8)[0]
    native = Form("native_" + op, op, 8, (R("d", "g"),),
                  {"dst": "d", "a": "d"}, 2, "native {d}")
    if op == "not":
        equivalent = Form("tiny_xor", "xor", 8, (R("d", "tiny"), I("k", 8)),
                          {"dst": "d", "a": "d", "b": "k"}, 1, "tiny {d}, {k}")
    else:
        equivalent = Form("tiny_sub", "sub", 8,
                          (R("d", "tiny"), I("zero", 8, values=(0,)), R("s", "tiny")),
                          {"dst": "d", "a": "zero", "b": "s"}, 1, "tiny {d}, {zero}, {s}")
    target = replace(base, classes=(*base.classes, Cls("tiny", ("r1",))),
                     forms=tuple(f for f in base.forms if f.op not in (op, equivalent.op)) + (native, equivalent))
    source = HEADER + f'def f(a:u8)->u8:\n    return {"~" if op == "not" else "-"}a\n'
    function = _optimize(_lower(_parse(source, target, "estimate.py"))["f"])
    ctx = _Context(target, {})
    allocator = _Allocator(function, ctx, {"f": function.abi})
    bid, index, instruction = next(item for item in allocator.instructions if item[2].op == op)
    return target, ctx, allocator, bid, index, instruction


@pytest.mark.parametrize("op", ("not", "neg"))
def test_cr20_joint_estimate_matches_complete_small_target_enumeration(op):
    target, ctx, allocator, bid, index, instruction = _estimate_fixture(op)
    operands = (instruction.args[0], instruction.dst)
    domain = tuple(RegLoc(r.name) for r in target.regs)
    # This single-use unary source is dead after the instruction; there are no
    # interfering values or slots. Enumerate all sixteen physical assignments.
    assert allocator.points[bid, index] == {instruction.dst}
    costs = {}
    for positions in product(domain, repeat=2):
        result = expand(ctx, instruction, dict(zip(operands, positions)), set())
        if result is not None:
            costs[positions] = (sum(m.bytes for m in result.mir), len(result.mir), 0)
    assert len(costs) == 16
    assert min(costs.values()) == (1, 1, 0)
    # All 25 partial assignments, including both-free and each one-free case.
    for partial in product((None, *domain), repeat=2):
        assignments = {v: loc for v, loc in zip(operands, partial) if loc is not None}
        expected = min(cost for positions, cost in costs.items()
                       if all(loc is None or loc == position for loc, position in zip(partial, positions)))
        assert allocator._instruction(bid, index, instruction, assignments)[0] == expected, partial


@pytest.mark.parametrize("branch", (False, True))
def test_obs02_reachable_fallthrough_without_machine_return_is_rejected(branch):
    target = toy("t1", 8)[0]
    body = '    if a == 0:\n        return 1\n    return a\n' if branch else '    return a\n'
    source = HEADER + 'def f(a:u8)->u8:\n' + body
    function = compile_source(source, target, CONFIG).functions["f"]
    last_return = max(i for i, m in enumerate(function.mir) if m.op == "ret")
    mir = tuple(m for i, m in enumerate(function.mir) if i != last_return)
    broken = replace(function, mir=mir, bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match="V7"):
        _verify(broken, target, CONFIG, {"f": function._ir.abi})


def test_obs02_empty_machine_body_is_rejected():
    target = toy("t1", 8)[0]
    function = compile_source(HEADER + 'def f(a:u8)->u8:\n    return a\n', target, CONFIG).functions["f"]
    with pytest.raises(InternalError, match="V7"):
        _verify(replace(function, mir=(), bytes=0), target, CONFIG, {"f": function._ir.abi})


def test_obs02_machine_return_must_correspond_to_ir_terminator():
    target = toy("t1", 8)[0]
    function = compile_source(HEADER + 'def f(a:u8)->u8:\n    return a\n', target, CONFIG).functions["f"]
    entry_origin = (function._ir.blocks[0].id, 0)
    mir = tuple(replace(m, _origin=entry_origin) if m.op == "ret" else m for m in function.mir)
    with pytest.raises(InternalError, match="V7"):
        _verify(replace(function, mir=mir), target, CONFIG, {"f": function._ir.abi})


def test_obs02_final_conditional_branch_cannot_fall_out():
    target = toy("t1", 8)[0]
    source = HEADER + 'def f(a:u8)->u8:\n    while a:\n        a = a-1\n    return a\n'
    function = compile_source(source, target, CONFIG).functions["f"]
    index = next(i for i, m in enumerate(function.mir) if m.op == "br")
    branch = function.mir[index]
    destination = next(o.name for o in branch.form.operands if isinstance(o, T))
    header = next(m._label for m in reversed(function.mir[:index]) if m._label is not None)
    # The taken edge remains inside the function; only the untaken edge falls out.
    mir = (*function.mir[:index], replace(branch, values={destination: header}))
    broken = replace(function, mir=mir, bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match="V7"):
        _verify(broken, target, CONFIG, {"f": function._ir.abi})


def test_obs02_ir_return_cannot_be_replaced_with_an_endless_machine_loop():
    target = toy("t1", 8)[0]
    function = compile_source(HEADER + 'def f(a:u8)->u8:\n    return a\n', target, CONFIG).functions["f"]
    jump = next(f for f in target.forms if f.op == "jmp")
    operand = next(o for o in jump.operands if isinstance(o, T))
    entry = function.mir[0]._label
    mir = tuple(replace(m, form=jump, values={operand.name: entry}) if m.op == "ret" else m
                for m in function.mir)
    broken = replace(function, mir=mir, bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match="V7"):
        _verify(broken, target, CONFIG, {"f": function._ir.abi})


def test_obs02_unreachable_tail_does_not_require_return():
    target = toy("t1", 8)[0]
    function = compile_source(HEADER + 'def f(a:u8)->u8:\n    return a\n', target, CONFIG).functions["f"]
    mir = (*function.mir, MInstr(None, {}, _label="unreachable_tail"))
    assert _verify(replace(function, mir=mir), target, CONFIG, {"f": function._ir.abi}) == ()


def test_obs02_infinite_loop_has_no_reachable_exit():
    target = toy("t1", 8)[0]
    result = compile_source(HEADER + 'def f():\n    while u8(1):\n        pass\n', target, CONFIG)
    assert result.functions["f"].verified is True
