"""Negative inputs to the final MIR verifier, using compiled toy controls."""

from dataclasses import replace

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import InternalError, TargetError
from rpkbin.mcucc.expand import MInstr
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.lower import _lower
from rpkbin.mcucc.target import MemLoc, RegLoc, T
from rpkbin.mcucc.verify import _verify
from test_formcheck import toy
from test_review import _execute


HEADER = "from rpkbin.mcucc.lang import u8, fn, extern, mem, Ptr, asm\n"
POOL = CompileConfig({"sram": (96, 111)}, False)
IDENTITY = "def f(x: u8) -> u8:\n    return x\n"
ADD = "def f(x: u8, y: u8) -> u8:\n    return x + y\n"
BRANCH = """def f(x: u8) -> u8:
    if x == 7:
        return 2
    return 3
"""
MEMORY_CALL = """@fn(args=(("sram", 48),))
def g(x: u8):
    pass
def f(x: u8):
    g(x)
"""


@pytest.fixture(scope="module")
def t1():
    return toy("t1", 8)


@pytest.fixture(scope="module")
def t2():
    return toy("t2", 8)


def form(target, name):
    return next(form for form in target.forms if form.id == name)


def compiled(target, body):
    source = HEADER + body
    result = compile_source(source, target, POOL)
    # The authority comes from source/Target and the clean compiled callee,
    # before any MIR changes.
    interfaces = {
        name: dict(ir.abi)
        for name, ir in _lower(_parse(source, target, "negative.py")).items()
    }
    for name, function in result.functions.items():
        interfaces[name]["clobbers"] = function.clobbers
    function = result.functions["f"]
    assert _verify(function, target, POOL, interfaces) == function._coverage
    return result, function, interfaces


def with_mir(function, mir):
    mir = tuple(mir)
    return replace(function, mir=mir, bytes=sum(m.bytes for m in mir))


def with_instruction(function, index, *instructions):
    return with_mir(function, (*function.mir[:index], *instructions, *function.mir[index + 1:]))


def find(function, op):
    return next((i, m) for i, m in enumerate(function.mir) if m.op == op)


def reject(function, target, interfaces, diagnostic, config=POOL, *, error_type=InternalError):
    with pytest.raises(error_type, match=diagnostic):
        _verify(function, target, config, interfaces)


def install_forms(target, *forms):
    replacements = {form.id: form for form in forms}
    return replace(target, forms=tuple(replacements.get(f.id, f) for f in target.forms))


def test_v1_immediate_in_range_but_outside_values(t1):
    target = t1[0]
    _, function, interfaces = compiled(target, "def f(x: u8) -> u8:\n    return x << 1\n")
    index, shift = find(function, "shl")
    reject(with_instruction(function, index, replace(shift, values={**shift.values, "n": 0})),
           target, interfaces, r"V1.*shl\.n=0")


def test_v1_relaxed_branch_out_of_range(t1):
    target = t1[0]
    _, function, interfaces = compiled(target, BRANCH)
    index, branch = find(function, "br")
    narrow = replace(branch.form, operands=(T("t", rel_bits=2),))
    variant = install_forms(target, narrow)
    reject(with_instruction(function, index, replace(branch, form=narrow)),
           variant, interfaces, r"V1/V2.*branch offset .* outside 2 bits")


@pytest.mark.parametrize("which", ["long-add", "short-ret", "both"])
def test_v2_wrong_encoder_lengths_with_cancelling_total(t1, which):
    target = t1[0]
    _, function, interfaces = compiled(target, ADD)
    add = form(target, "add_rr")
    ret = form(target, "ret")
    faults = {
        "long-add": replace(add, encode=lambda values: (*add.encode(values), 0)),
        "short-ret": replace(ret, encode=lambda values: ()),
    }
    changed = faults.values() if which == "both" else (faults[which],)
    variant = install_forms(target, *changed)
    mir = tuple(replace(m, form=form(variant, m.form.id), _target=variant) if m.form else m
                for m in function.mir)
    if which == "both":
        assert sum(len(m.form.encode(m.values)) for m in mir if m.form) == function.bytes
    first_fault = ret if which == "short-ret" else add
    actual = 0 if which == "short-ret" else add.bytes + 1
    diagnostic = (rf"form '{first_fault.id}': encode length/cells disagree with form.*"
                  rf"expected {first_fault.bytes}, got {actual}")
    reject(with_mir(function, mir), variant, interfaces, diagnostic, error_type=TargetError)


def test_v3_push_and_pop_on_different_paths(t2):
    target = t2[0]
    source = """def f(x: u8):
    if x == 0:
        mem.sram[48] = x
    mem.sram[49] = x
"""
    _, function, interfaces = compiled(target, source)
    index, conditional_store = find(function, "st")
    push = replace(conditional_store, form=form(target, "push"), values={"s": "x0"})
    ret_index, ret = find(function, "ret")
    pop = replace(ret, form=form(target, "pop"), values={"d": "x0"}, _role="return")
    mir = (*function.mir[:index], push, *function.mir[index:ret_index], pop, *function.mir[ret_index:])
    reject(with_mir(function, mir), target, interfaces, r"V3.*(join|unmatched pop)")


def test_v4_missing_branch_with_flag_producer_intact(t1):
    target = t1[0]
    source = """def f(x: u8):
    if x == 0:
        mem.sram[48] = x
    mem.sram[49] = x
"""
    _, function, interfaces = compiled(target, source)
    index, _ = find(function, "br")
    reject(with_instruction(function, index), target, interfaces, r"V4.*missing conditional")


def test_v5_overlapping_live_arguments(t1):
    target = t1[0]
    _, function, interfaces = compiled(target, ADD)
    x, y = function._ir.params
    assert function._locations[x] != function._locations[y]
    bad = replace(function, _locations={**function._locations, y: function._locations[x]})
    reject(bad, target, interfaces, r"V5.*overlap")


@pytest.mark.parametrize("field, wrong", [("preserves", ()), ("clobbers", ()), ("ret", RegLoc("r1"))])
def test_v5_call_abi_loses_authoritative_field(t1, field, wrong):
    target = t1[0]
    source = """@fn(preserves=("r3",))
def g(x: u8) -> u8:
    return x + 1
def f(x: u8) -> u8:
    return g(x)
"""
    _, function, interfaces = compiled(target, source)
    index, call = find(function, "call")
    assert interfaces["g"][field] != wrong
    bad = replace(call, _callee_abi={**call._callee_abi, field: wrong})
    reject(with_instruction(function, index, bad), target, interfaces, rf"V5.*'{field}'")


def test_v5_missing_call_saves_cannot_be_excused_by_forged_clobbers(t1):
    target = t1[0]
    source = """@extern(args=("r0",), ret="r0", clobbers=("r1", "r2", "r3"))
def g(x: u8) -> u8:
    return x ^ 90
def f(x: u8, y: u8) -> u8:
    return g(x) + y
"""
    _, function, interfaces = compiled(target, source)
    saves = {"call-save", "call-restore"}
    assert saves <= {m._role for m in function.mir}
    mir = tuple(replace(m, _callee_abi={**m._callee_abi, "clobbers": ("r2", "r3")})
                if m.op == "call" else m for m in function.mir if m._role not in saves)
    reject(with_mir(function, mir), target, interfaces, r"V5.*'clobbers'")


@pytest.mark.parametrize("role, anchor, after", [
    ("entry", "entry", False),
    ("return", "ret", False),
    ("body", "add", False),
    ("call-args", "call", False),
    ("call-result", "call", True),
    ("call-save", "call", False),
    ("call-restore", "call", True),
    ("asm-save", "asm", False),
    ("asm-restore", "asm", True),
    ("preserve-save", "entry", False),
    ("preserve-restore", "ret", False),
])
def test_v6_role_cannot_write_unrelated_user_memory(t1, role, anchor, after):
    target = t1[0]
    source = """@fn(args=(("sram", 48),))
def g(x: u8):
    pass
def f(x: u8) -> u8:
    g(x)
    asm("opaque", nbytes=1)
    return x + 1
"""
    _, function, interfaces = compiled(target, source)
    assert function.verified == "unverified: asm"
    if anchor == "entry":
        index = 1
        block = function._ir.blocks[0]
        position = -1 if role == "preserve-save" else 0
        origin = (block.id, position)
        if role == "entry":
            assert block.instrs[position].op == "entry"
    else:
        index, instruction = find(function, anchor)
        origin = instruction._origin
    index += after
    store = MInstr(form(target, "st"), {"m": MemLoc("sram", 55), "s": "r0"},
                   _origin=origin, _block=origin[0], _role=role, _target=target, _abi=function._ir.abi)
    bad = with_mir(function, (*function.mir[:index], store, *function.mir[index:]))
    reject(bad, target, interfaces, rf"V6.*unauthorized st.*role '{role}'")


def test_v6_call_args_write_to_real_abi_cell_after_call(t1):
    target, sim, _ = t1
    result, function, interfaces = compiled(target, MEMORY_CALL)
    index, call = find(function, "call")
    store = function.mir[index - 1]
    assert store._role == "call-args" and store.values["m"] == MemLoc("sram", 48)
    # Keeping the valid pre-call store hides the extra effect from the argument check.
    late = replace(store, values={**store.values, "s": "r1"})
    bad = with_instruction(function, index, call, late)
    for candidate, expected in ((result, 7), (replace(result, functions={**result.functions, "f": bad}), 0)):
        assert _execute(candidate, target, sim, (7,))[1].read("sram[48]") == expected
    reject(bad, target, interfaces, r"V6.*(call|store)")


@pytest.mark.parametrize("correct_after", [False, True], ids=["wrong-only", "wrong-then-correct"])
def test_v6_wrong_store_hidden_by_correct_store(t1, correct_after):
    target, binding, _ = t1
    _, function, interfaces = compiled(target, "def f(x: u8, y: u8):\n    mem.sram[48] = x\n")
    index, store = find(function, "st")
    assert store.values["s"] == "r0"
    first = replace(store, values={**store.values, "s": "r1"})
    sequence = (first, store) if correct_after else (first,)
    bad = with_instruction(function, index, *sequence)
    machine = binding.make_sim()
    cells = [cell for m in sequence for cell in m.form.encode({"m": 48, "s": m.values["s"]})]
    machine.load_cells(binding.program_space, dict(enumerate(cells)))
    machine.poke("r0", 7)
    machine.poke("r1", 9)
    for expected in ((9, 7) if correct_after else (9,)):
        machine.step()
        assert machine.read("sram[48]") == expected
    reject(bad, target, interfaces, r"V6.*store")


@pytest.mark.parametrize("effect", ["unverified-asm", "partial-indirect-store", "partial-call"])
def test_v6_missing_effect_in_abstract_region(t1, t2, effect):
    target, source, op, status = {
        "unverified-asm": (t1[0], 'def f():\n    asm("opaque", nbytes=1)\n', "asm", "unverified:"),
        "partial-indirect-store": (t2[0], 'P = Ptr("sram")\ndef f(p: P, x: u8):\n    mem.sram[p] = x\n',
                                   "st_ind", "partial:"),
        "partial-call": (t1[0], "def g():\n    pass\ndef f():\n    g()\n", "call", "partial:"),
    }[effect]
    _, function, interfaces = compiled(target, source)
    assert function.verified.startswith(status)
    index, _ = find(function, op)
    reject(with_instruction(function, index), target, interfaces, r"V6.*missing .*effect")


def test_v6_partial_indirect_store_uses_wrong_legal_pointer_register(t2):
    target, sim, _ = t2
    result, function, interfaces = compiled(
        target, 'P = Ptr("sram")\ndef f(p: P, x: u8):\n    mem.sram[p] = x\n')
    assert function.verified.startswith("partial:")
    index, store = find(function, "st_ind")
    assert store.values == {"p": "r0", "s": "r1"}
    bad = with_instruction(function, index, replace(store, values={**store.values, "p": "r1"}))
    altered = replace(result, functions={"f": bad})
    for candidate, expected in ((result, (49, 1)), (altered, (1, 49))):
        _, machine = _execute(candidate, target, sim, (48, 49))
        assert (machine.read("sram[48]"), machine.read("sram[49]")) == expected
    reject(bad, target, interfaces, r"V6.*pointer differs")


def test_v6_store_present_in_mir_but_unreachable(t1):
    target, sim, _ = t1
    result, function, interfaces = compiled(target, "def f(x: u8):\n    mem.sram[48] = x\n")
    index, store = find(function, "st")
    jump = replace(store, form=form(target, "jmp"), values={"t": ".Ldone"})
    dead = replace(function.mir[0], _label=".Ldead")
    done = replace(function.mir[0], _label=".Ldone")
    bad = with_instruction(function, index, jump, dead, store, done)
    for candidate, expected in ((result, 7), (replace(result, functions={"f": bad}), 1)):
        assert _execute(candidate, target, sim, (7,))[1].read("sram[48]") == expected
    reject(bad, target, interfaces, r"V[46]")


def test_v6_duplicate_partial_call_runs_callee_twice(t1):
    target, sim, _ = t1
    result, function, interfaces = compiled(target, "def g():\n    mem.sram[48] += 1\ndef f():\n    g()\n")
    assert function.verified.startswith("partial:")
    index, call = find(function, "call")
    bad = with_instruction(function, index, call, call)
    for candidate, expected in ((result, 2), (replace(result, functions={**result.functions, "f": bad}), 3)):
        assert _execute(candidate, target, sim, ())[1].read("sram[48]") == expected
    reject(bad, target, interfaces, r"V6.*call")


def test_v6_duplicate_unverified_asm(t1):
    target = t1[0]
    _, function, interfaces = compiled(target, 'def f():\n    asm("add r1, #1", clobbers=("r1",), nbytes=2)\n')
    assert function.verified == "unverified: asm"
    index, raw = find(function, "asm")
    reject(with_instruction(function, index, raw, raw), target, interfaces, r"V6.*asm")


def test_v7_instruction_after_ret_before_next_label(t1):
    target = t1[0]
    _, function, interfaces = compiled(target, BRANCH)
    index, ret = find(function, "ret")
    assert function.mir[index + 1]._label is not None
    reject(with_instruction(function, index, ret, ret), target, interfaces, r"V7.*follows ret")


@pytest.mark.parametrize("reg, expected", [("r0", "wrong return value"), ("r1", "preserved register")])
def test_v7_ret_clobbers_protected_register(t1, reg, expected):
    target = t1[0]
    _, function, interfaces = compiled(target, '@fn(preserves=("r1",))\n' + IDENTITY)
    index, ret = find(function, "ret")
    form_with_clobber = replace(ret.form, clobbers=(reg,))
    variant = install_forms(target, form_with_clobber)
    bad = replace(ret, form=form_with_clobber, _target=variant, _abi={**ret._abi, "preserves": ()})
    reject(with_instruction(function, index, bad), variant, interfaces, rf"V7.*{expected}")


@pytest.mark.parametrize("slot", [("sram", 48, 1), ("sram", 49, 2)], ids=["callee-abi", "overflow"])
def test_v8_slot_must_avoid_callee_abi_and_fit_pool(t1, slot):
    target = t1[0]
    _, function, interfaces = compiled(target, MEMORY_CALL)
    config = CompileConfig({"sram": (48, 49)}, False)
    assert _verify(function, target, config, interfaces) == function._coverage
    reject(replace(function, slots=(slot,)), target, interfaces, r"V8.*slot", config)


@pytest.mark.parametrize("op", ["call", "ret"])
def test_v9_clobber_report_cannot_omit_one_alias_part(t2, op):
    target = t2[0]
    changed = replace(form(target, op), clobbers=("x1",))
    variant = install_forms(target, changed)
    source = IDENTITY if op == "ret" else "def g():\n    pass\ndef f():\n    g()\n"
    _, function, interfaces = compiled(variant, source)
    assert {"r2", "r3"} <= set(function.clobbers)
    bad = replace(function, clobbers=tuple(r for r in function.clobbers if r != "r3"))
    reject(bad, variant, interfaces, r"V9.*clobbers")
