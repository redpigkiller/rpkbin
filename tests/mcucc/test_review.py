"""Independent regressions for selection, lowering and validation boundaries."""

from copy import deepcopy
from dataclasses import replace
import inspect

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.assemble import _assemble
from rpkbin.mcucc.diff import _read, _write
from rpkbin.mcucc.errors import InternalError, TargetError, UnsupportedError
from rpkbin.mcucc.expand import MInstr, _Context, expand, expand_branch
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.ir import Imm, Instr, VReg, ir_eval
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.layout import _relax
from rpkbin.mcucc.lower import _lower
from rpkbin.mcucc.opt import _optimize
from rpkbin.mcucc.target import I, MemLoc, R, RegLoc, Space, T
from rpkbin.mcucc.verify import _verify
from test_formcheck import toy


HEADER = 'from rpkbin.mcucc.lang import u8, Ptr, mem\nP = Ptr("sram")\n'
POOL = CompileConfig({"sram": (0x60, 0x6f)}, False)


def _execute(result, target, binding, arguments, initial=1):
    function = result.functions["f"]
    call = next(f for f in target.forms if f.op == "call")
    origin = call.bytes + len(binding.halt_cells)
    program = _assemble(result, target, origin, {})
    operand = next(o for o in call.operands if isinstance(o, T))
    driver = (*call.encode({operand.name: program.symbols["f"]}), *binding.halt_cells)
    sim = binding.make_sim()
    if binding.init:
        binding.init(sim)
    sim.load_cells(binding.program_space, {**program.cells, **dict(enumerate(driver))})
    for space in target.spaces:
        if space.kind == "data":
            sim.load_cells((binding.names or {}).get(space.name, space.name),
                           {i: initial for i in range(min(256, 1 << space.addr_bits))})
    for location, value in zip(function._ir.abi["args"], arguments):
        _write(sim, binding, target, location, value)
    depth = sim.read("sp")
    outcome = sim.run(max_steps=200000)
    assert outcome.stop_reason == "halted"
    assert sim.read("sp") == depth
    value = _read(sim, binding, target, function._ir.abi["ret"]) if function._ir.abi["ret"] else None
    return value, sim


def test_cr01_immediate_value_is_not_a_memory_address():
    target, binding, _ = toy("t2", 8)
    target = replace(target, forms=tuple(sorted(target.forms, key=lambda f: f.op != "ld")))
    source = HEADER + 'def f(p: P):\n    mem.sram[p] = u8(7)\n'
    result = compile_source(source, target, CompileConfig({}, False))
    _, sim = _execute(result, target, binding, (128,))
    assert sim.read("sram[128]") == 7


@pytest.mark.parametrize("body,args", (("return mem.aux[p]", "p: A"),
                                      ("mem.aux[p] = v", "p: A, v: u8")))
def test_cr02_missing_space_form_is_rejected(body, args):
    target = toy("t2", 8)[0]
    target = replace(target, spaces=(*target.spaces, Space("aux", 8, "data")))
    annotation = " -> u8" if body.startswith("return") else ""
    source = HEADER + f'A = Ptr("aux")\ndef f({args}){annotation}:\n    {body}\n'
    with pytest.raises(UnsupportedError, match="f.*(load|store|aux)"):
        compile_source(source, target, POOL)


def test_cr03_augmented_address_runs_once_in_ir_and_machine():
    target, binding, _ = toy("t2", 8)
    source = HEADER + '''def idx() -> P:
    mem.sram[0x30] = mem.sram[0x30] + 1
    return P(0x40)
def f() -> u8:
    mem.sram[idx()] += 1
    return mem.sram[0x30]
'''
    memory = {"sram": [1] * 256}
    assert run_reference(source, "f", (), memory, addr_bits={"sram": 8})[0] == 2
    module = _lower(_parse(source, target, "aug.py"))
    assert ir_eval(module["f"], (), deepcopy(memory), module)[0] == 2
    for function in module.values():
        _optimize(function)
    assert ir_eval(module["f"], (), deepcopy(memory), module)[0] == 2
    result = compile_source(source, target, POOL)
    assert _execute(result, target, binding, ())[0] == 2


def _branch_clobbers(target, operations=("br",)):
    return replace(target, forms=tuple(replace(f, clobbers=("r2",)) if f.op in operations else f
                                       for f in target.forms))


def test_cr11_dead_branch_outputs_do_not_remove_control_flow():
    target, binding, _ = toy("t1", 8)
    target = _branch_clobbers(target)
    source = HEADER + '''def f(a: u8, b: u8) -> u8:
    if a == b:
        return 1
    return 2
'''
    result = compile_source(source, target, POOL)
    assert _execute(result, target, binding, (3, 4))[0] == 2
    assert _execute(result, target, binding, (3, 3))[0] == 1


def test_cr11_verifier_rejects_missing_conditional():
    target = toy("t1", 8)[0]
    result = compile_source(HEADER + 'def f(a:u8,b:u8)->u8:\n    if a == b:\n        return 1\n    return 2\n', target, POOL)
    function = result.functions["f"]
    mir = tuple(m for m in function.mir if m.op not in ("br", "cmp"))
    broken = replace(function, mir=mir, bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match="V4"):
        _verify(broken, target, POOL, {"f": function._ir.abi})


def _shift_target(target):
    forms = []
    for form in target.forms:
        if form.op in ("shl", "shr"):
            source = next(o for o in form.operands if o.name == form.bind["a"])
            forms.append(replace(form, operands=(R("d", source.cls), R("s", source.cls), I("k", 8, values=(1,))),
                                 bind={"dst": "d", "a": "s", "b": "k"},
                                 asm=form.op + " {d}, {s}, #{k}", encode=None))
        else:
            forms.append(form)
    return replace(target, forms=tuple(forms))


def test_cr04_repeated_three_operand_shift_feeds_back_result():
    target = _shift_target(toy("t1", 8)[0])
    ctx = _Context(target, {})
    a, d = VReg(0, 8), VReg(1, 8)
    result = expand(ctx, Instr("shl", d, (a, Imm(3, 8))), {a: RegLoc("r1"), d: RegLoc("r0")}, set())
    assert result is not None
    values = {"r0": 0, "r1": 3, "r2": 0, "r3": 0}
    for machine in result.mir:
        form = machine.form
        args = {key: machine.values[name] if isinstance(next(o for o in form.operands if o.name == name), I)
                else values[machine.values[name]] for key, name in form.bind.items() if key != "dst"}
        value = (args["a"] << args["b"]) & 255 if form.op == "shl" else args["a"]
        values[machine.values[form.bind["dst"]]] = value
    assert values["r0"] == 24
    compile_source(HEADER + 'def f(a:u8)->u8:\n    return (a << 3) + a\n', target, POOL)


def test_cr05_indirect_store_invalidates_both_symbolic_states():
    target, binding, _ = toy("t2", 8)
    source = HEADER + '''def f(p: P, a: u8) -> u8:
    mem.sram[0x30] = u8(1)
    mem.sram[p] = a
    return mem.sram[0x30]
'''
    result = compile_source(source, target, POOL)
    assert _execute(result, target, binding, (0x30, 9))[0] == 9
    assert _execute(result, target, binding, (0x40, 9))[0] == 1


def test_cr06_relaxation_visits_equal_size_larger_range():
    target = toy("t1", 8)[0]
    target = replace(target, forms=tuple(replace(f, bytes=2, encode=None) if f.id == "jmpf" else f for f in target.forms))
    short = next(f for f in target.forms if f.id == "jmp")
    const = next(f for f in target.forms if f.op == "const")
    mir = (MInstr(None, {}, _label="f"), MInstr(short, {"t": "far"}),
           *(MInstr(const, {"d": "r0", "k": 1}) for _ in range(80)), MInstr(None, {}, _label="far"))
    relaxed = _relax(mir, target, "f")
    assert next(m for m in relaxed if m.op == "jmp").form.id == "jmpf"
    assert sum(m.bytes for m in relaxed) == 162


def test_cr07_backend_interface_and_compiler_dispatch(monkeypatch):
    from rpkbin.mcucc import regalloc
    from types import MappingProxyType
    assert hasattr(regalloc, "assign")
    assert tuple(inspect.signature(regalloc.assign).parameters) == ("ir", "liveness", "expandcost_fn", "cand_fn")
    assert tuple(regalloc._BACKENDS) == ("heuristic",)
    original, calls, backend_calls = regalloc.assign, [], []
    backend = regalloc._BACKENDS["heuristic"]
    def registered(*args):
        backend_calls.append(args)
        return backend(*args)
    def traced(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(regalloc, "assign", traced)
    monkeypatch.setattr(regalloc, "_BACKENDS", MappingProxyType({"heuristic": registered}))
    compile_source(HEADER + 'def f(a:u8)->u8:\n    return a + 1\n', toy("t1", 8)[0], POOL)
    assert len(calls) == 1
    assert len(backend_calls) == 1 and backend_calls[0] == calls[0]


def test_cr08_bad_pair_encoder_returns_failures():
    from rpkbin.mcucc.formcheck import check_forms
    target, binding, _ = toy("t1", 8)
    target = replace(target, forms=tuple(replace(f, encode=lambda values: (0, 0)) if f.id == "cmp_rr" else f for f in target.forms))
    failures = check_forms(target, binding, (71,))
    assert any(f.form_id == "cmp_rr" and f.check == "C7" for f in failures)


@pytest.mark.parametrize("invalid", ("regs", 0, -1, 2))
def test_cr09_target_types_and_abi_cell_count_raise_target_error(invalid):
    target = toy("t1", 8)[0]
    with pytest.raises(TargetError, match="(regs|abi|location)"):
        if invalid == "regs":
            replace(target, regs=("a",))
        else:
            replace(target, abi=replace(target.abi, arg_locs={8: (MemLoc("sram", 3, invalid),)}))


def test_cr10_branch_clobbers_respect_busy_locations():
    target = _branch_clobbers(toy("t1", 8)[0])
    ctx = _Context(target, {})
    a, b = VReg(0, 8), VReg(1, 8)
    assert expand_branch(ctx, "eq", a, b, {a: RegLoc("r0"), b: RegLoc("r1")}, {"r2"}, 2) is None
    result = compile_source(HEADER + '''def f(a:u8,b:u8,c:u8)->u8:
    if a == b:
        return c + 1
    return c + 2
''', target, POOL)
    assert result.functions["f"].verified is True


@pytest.mark.parametrize("corruption", ("pointer", "value", "space", "constant"))
def test_indirect_verification_checks_operands_independently(corruption):
    target = toy("t2", 8)[0]
    indirect = next(f for f in target.forms if f.op == "st_ind")
    other = replace(indirect, id="aux_store", operands=tuple(replace(o, space="aux") if hasattr(o, "space") else o for o in indirect.operands), encode=None)
    target = replace(target, spaces=(*target.spaces, Space("aux", 8, "data")), forms=(*target.forms, other))
    source = HEADER + ('def f(p:P):\n    mem.sram[p] = u8(7)\n' if corruption == "constant" else 'def f(p:P,v:u8):\n    mem.sram[p] = v\n')
    function = compile_source(source, target, CompileConfig({}, False)).functions["f"]
    mir = list(function.mir)
    index = next(i for i, m in enumerate(mir) if m.op == ("const" if corruption == "constant" else "st_ind"))
    machine = mir[index]
    if corruption == "constant":
        load = next(f for f in target.forms if f.op == "ld")
        mir[index] = replace(machine, form=load, values={load.bind["dst"]: machine.values[machine.form.bind["dst"]], load.bind["a"]: MemLoc("sram", 7)})
    elif corruption == "space":
        mir[index] = replace(machine, form=other)
    else:
        values = dict(machine.values)
        values[machine.form.bind["a" if corruption == "pointer" else "b"]] = "r2"
        mir[index] = replace(machine, values=values)
    broken = replace(function, mir=tuple(mir), bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match="V6"):
        _verify(broken, target, CompileConfig({}, False), {"f": function._ir.abi})


def test_verifier_rejects_missing_unconditional_back_edge():
    from test_compile import source_for
    target = toy("t1", 8)[0]
    function = compile_source(source_for("count_down"), target, POOL).functions["count_down"]
    mir = tuple(m for m in function.mir if m.op != "jmp")
    broken = replace(function, mir=mir, bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match="V4"):
        _verify(broken, target, POOL, {function.name: function._ir.abi})


def test_cr10_appended_jump_clobbers_are_also_checked():
    target = _branch_clobbers(toy("t1", 8)[0], ("jmp",))
    ctx = _Context(target, {})
    a, b = VReg(0, 8), VReg(1, 8)
    assert expand_branch(ctx, "eq", a, b, {a: RegLoc("r0"), b: RegLoc("r1")}, {"r2"}, None) is None


@pytest.mark.parametrize("invalid", ("spaces", "forms", "operands", "bind", "abi", "arg_locs", "classes"))
def test_cr09_nested_declaration_types_are_checked_before_use(invalid):
    target = toy("t1", 8)[0]
    with pytest.raises(TargetError, match="target\\."):
        if invalid in ("spaces", "forms"):
            replace(target, **{invalid: ("bad",)})
        elif invalid in ("operands", "bind"):
            form = replace(target.forms[0], **{invalid: ("bad",) if invalid == "operands" else []})
            replace(target, forms=(form, *target.forms[1:]))
        elif invalid == "abi":
            replace(target, abi="bad")
        elif invalid == "arg_locs":
            replace(target, abi=replace(target.abi, arg_locs={8: "r0"}))
        else:
            replace(target, classes=(replace(target.classes[0], members=([],)),))


@pytest.mark.parametrize("broken,check", (("pop", "C8"), ("ret", "C9")))
def test_cr08_stack_and_return_failures_keep_actual_form(broken, check):
    from rpkbin.mcucc.formcheck import check_forms
    target, binding, _ = toy("t2", 8)
    target = replace(target, long_branch="none", forms=tuple(
        replace(f, encode=lambda values: (0, 0)) if f.op == broken else f
        for f in target.forms if f.op in ("push", "pop", "call", "ret", "jmp")))
    failures = check_forms(target, binding, (71,))
    assert any(f.form_id == broken and f.check == check for f in failures)
