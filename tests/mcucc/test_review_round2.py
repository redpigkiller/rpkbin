"""Source scope, declared effects, cycle temporaries and module symbols."""

from dataclasses import replace

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import InternalError, SourceError, UnsupportedError
from rpkbin.mcucc.expand import MInstr, _Context, expand
from rpkbin.mcucc.formcheck import check_forms
from rpkbin.mcucc.ir import Instr, VReg
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.layout import _renumber
from rpkbin.mcucc.target import Cls, Form, R, RegLoc
from rpkbin.mcucc.verify import _verify
from test_formcheck import toy
from test_review import _execute


CONFIG = CompileConfig({}, False)
HEADER = 'from rpkbin.mcucc.lang import u8, Ptr, mem, extern, asm\n'


@pytest.mark.parametrize("engine", ("compile", "reference"))
@pytest.mark.parametrize("binding,name", (
    ("K = 7\n", "K"),
    ('P = Ptr("sram")\n', "P"),
    ("def helper(v:u8)->u8:\n    return v\n", "helper"),
    ('@extern(args=("r0",),ret="r0",clobbers=())\ndef existing(v:u8)->u8:\n    return v\n', "existing"),
    ("", "mem"),
    ("", "u16"),
))
def test_cr12_parameters_cannot_shadow_module_bindings(engine, binding, name):
    source = HEADER + binding + f"def f({name}:u8)->u8:\n    return {name}\n"
    with pytest.raises(SourceError, match=rf":\d+:.*{name}"):
        if engine == "compile":
            compile_source(source, toy("t1", 8)[0], CONFIG, filename="shadow.py")
        else:
            run_reference(source, "f", (42,), {}, addr_bits={"sram": 8})


@pytest.mark.parametrize("engine", ("compile", "reference"))
def test_cr12_loop_counter_cannot_shadow_later_module_constant(engine):
    source = HEADER + 'def f(a:u8)->u8:\n    for K in range(2):\n        pass\n    return a\nK = 7\n'
    with pytest.raises(SourceError, match=r":\d+:.*K"):
        if engine == "compile":
            compile_source(source, toy("t1", 8)[0], CONFIG)
        else:
            run_reference(source, "f", (42,), {}, addr_bits={"sram": 8})


@pytest.mark.parametrize("op", ("ld", "ld_ind"))
@pytest.mark.parametrize("word", ("zero", "carry"))
def test_cr13_load_flag_semantics_are_checked(op, word):
    target, binding, _ = toy("t1" if op == "ld" else "t2", 8)
    load = next(f for f in target.forms if f.op == op)
    target = replace(target, long_branch="none", forms=tuple(
        replace(f, flags_write={"Z": word}) if f.id == load.id else f
        for f in target.forms if f.id == load.id or f.op in ("jmp", "ret")))
    failures = check_forms(target, binding, (0,))
    assert any(f.form_id == load.id and f.check == "C4" for f in failures)


@pytest.mark.parametrize("pool", ({}, {"sram": (96, 111)}))
def test_cr14_cycle_uses_next_reachable_scratch(pool):
    target = toy("t2", 8)[0]
    target = replace(target, classes=tuple(replace(c, members=tuple(n for n in c.members if n != "r2"))
                                           if c.name == "b" else c for c in target.classes))
    h, lo, d = VReg(0, 8), VReg(1, 8), VReg(2, 16)
    result = expand(_Context(target, pool), Instr("pack", d, (h, lo)),
                    {h: RegLoc("r0"), lo: RegLoc("r1"), d: RegLoc("x0")}, set())
    assert result is not None and result.cost == (3, 3)
    assert [m.values for m in result.mir] == [
        {"d": "r3", "s": "r1"}, {"d": "r1", "s": "r0"}, {"d": "r0", "s": "r3"}]


@pytest.mark.parametrize("external", (False, True))
def test_cr15_module_labels_cannot_collide_with_functions_or_externs(external):
    target = replace(toy("t1", 8)[0], local_label="{fn}_{n}")
    source = HEADER + '''def f(a:u8,b:u8)->u8:
    if a == b:
        return 1
    return 2
'''
    if external:
        source += '@extern(args=("r0",),ret="r0",clobbers=())\n'
    source += 'def f_1(a:u8)->u8:\n    return a\n'
    with pytest.raises(UnsupportedError, match="f_1") as exc:
        compile_source(source, target, CONFIG)
    assert "f" in str(exc.value)


def test_obs01_missing_indirect_store_is_rejected():
    target = toy("t2", 8)[0]
    source = HEADER + 'P = Ptr("sram")\ndef f(p:P,v:u8):\n    mem.sram[p] = v\n'
    function = compile_source(source, target, CONFIG).functions["f"]
    mir = tuple(m for m in function.mir if m.op != "st_ind")
    broken = replace(function, mir=mir, bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match="V6.*store"):
        _verify(broken, target, CONFIG, {"f": function._ir.abi})


def test_obs01_missing_unused_call_is_rejected():
    target = toy("t1", 8)[0]
    source = HEADER + '''@extern(args=("r0",),ret="r0",clobbers=())
def helper(a:u8)->u8:
    return a ^ 7
def f(a:u8):
    helper(a)
'''
    function = compile_source(source, target, CONFIG).functions["f"]
    mir = tuple(m for m in function.mir if m.op != "call")
    broken = replace(function, mir=mir, bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match="V6.*call"):
        _verify(broken, target, CONFIG, {"f": function._ir.abi})


@pytest.mark.parametrize("comparison", ("==", "!=", "<", "<=", ">", ">="))
def test_generated_identical_comparison_operands_are_valid(comparison):
    target, binding, _ = toy("t0", 8)
    source = HEADER + f'def f(a:u8)->u8:\n    if not (a {comparison} a):\n        return 1\n    return 2\n'
    result = compile_source(source, target, CompileConfig({"sram": (0x20, 0x2f)}, False))
    expected = 2 if comparison in ("==", "<=", ">=") else 1
    assert _execute(result, target, binding, (42,))[0] == expected


def test_cr14_scratch_must_support_the_return_leg():
    target = toy("t2", 8)[0]
    classes = (*target.classes, Cls("save_dest", ("r2", "r3")), Cls("original", ("r0", "r1")), Cls("restore_source", ("r3",)))
    forms = tuple(f for f in target.forms if f.op != "mov") + (
        Form("save", "mov", 8, (R("d", "save_dest"), R("s", "original")), {"dst": "d", "a": "s"}, 1, "save {d}, {s}"),
        Form("inner", "mov", 8, (R("d", "original"), R("s", "original")), {"dst": "d", "a": "s"}, 1, "inner {d}, {s}"),
        Form("restore", "mov", 8, (R("d", "original"), R("s", "restore_source")), {"dst": "d", "a": "s"}, 1, "restore {d}, {s}"))
    ctx = _Context(replace(target, classes=classes, forms=forms), {})
    h, lo, d = VReg(0, 8), VReg(1, 8), VReg(2, 16)
    result = expand(ctx, Instr("pack", d, (h, lo)), {h: RegLoc("r0"), lo: RegLoc("r1"), d: RegLoc("x0")}, set())
    assert result is not None and result.cost == (3, 3)
    assert result.mir[0].values == {"d": "r3", "s": "r1"}


@pytest.mark.parametrize("effect", ("store", "asm"))
def test_obs01_other_missing_effects_are_rejected(effect):
    target = toy("t1", 8)[0]
    body = 'mem.sram[48] = a' if effect == "store" else 'asm("opaque {0}", ins=((a,"r0"),), outs=(), clobbers=(), nbytes=1)'
    function = compile_source(HEADER + f'def f(a:u8):\n    {body}\n', target, CONFIG).functions["f"]
    mir = tuple(m for m in function.mir if m.op != ("st" if effect == "store" else "asm"))
    broken = replace(function, mir=mir, bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match=f"V6.*{effect}"):
        _verify(broken, target, CONFIG, {"f": function._ir.abi})


def test_cr15_local_renumbering_preserves_global_call_symbols():
    target = replace(toy("t1", 8)[0], local_label="{fn}_{n}")
    call = next(f for f in target.forms if f.op == "call")
    mir = (MInstr(None, {}, _label="f"), MInstr(None, {}, _label="f_5"),
           MInstr(call, {"t": "f_1"}), MInstr(None, {}, _label="f_1"))
    renamed = _renumber(mir, target, "f")
    assert [m._label for m in renamed if m._label] == ["f", "f_1", "f_2"]
    assert next(m for m in renamed if m.op == "call").values == {"t": "f_1"}
