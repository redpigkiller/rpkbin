"""Independent controls for call metadata, preservation and memory effects."""

from dataclasses import replace

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import InternalError
from rpkbin.mcucc.formcheck import check_forms
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.lower import _lower
from rpkbin.mcucc.target import Abi, RegLoc
from rpkbin.mcucc.verify import _verify
from test_compile import source_for
from test_formcheck import toy
from test_fuzz import run_case
from test_review import _execute


HEADER = 'from rpkbin.mcucc.lang import u8, mem, extern\n'
POOL = CompileConfig({"sram": (96, 111)}, False)


def _replace_mir(function, mir):
    mir = tuple(mir)
    return replace(function, mir=mir, bytes=sum(m.bytes for m in mir))


def test_call_save_removal_with_underreported_mir_callee_clobbers_is_rejected():
    target, binding, stubs = toy("t1", 8)
    source = HEADER + '''@extern(args=("r0",),ret="r0",clobbers=("r1","r2","r3"))
def helper_b(x:u8)->u8:
    return x ^ 0x5a
def f(a:u8,b:u8)->u8:
    return helper_b(a)+b
'''
    # Obtain the authoritative interfaces from the source independently of MIR.
    ir = _lower(_parse(source, target, "callee.py"))
    interfaces = {name: dict(fn.abi) for name, fn in ir.items()}
    assert interfaces["helper_b"]["clobbers"] == ("r1", "r2", "r3")
    result = compile_source(source, target, POOL)
    function = result.functions["f"]
    assert {"call-save", "call-restore"} <= {m._role for m in function.mir}
    stub = {"helper_b": stubs["helper_b"]}
    assert run_case(result, target, binding, stub, "f", (3, 4), 0)[0] == 93

    no_saves = tuple(m for m in function.mir if m._role not in ("call-save", "call-restore"))
    # With intact MIR clobbers, the verifier already catches the missing save.
    with pytest.raises(InternalError, match="V5|V6"):
        _verify(_replace_mir(function, no_saves), target, POOL, interfaces)

    broken = _replace_mir(function, (
        replace(m, _callee_abi={**m._callee_abi, "clobbers": ()}) if m.op == "call" else m
        for m in no_saves
    ))
    altered = replace(result, functions={"f": broken})
    actual, _, memory = run_case(altered, target, binding, stub, "f", (3, 4), 0)
    expected, _ = run_reference(source, "f", (3, 4), memory, addr_bits={"sram": 8})
    assert (actual, expected) == (254, 93)
    with pytest.raises(InternalError):
        _verify(broken, target, POOL, interfaces)


def test_underreported_mir_preserves_does_not_remove_the_ir_preservation_obligation():
    target, binding, _ = toy("t1", 8)
    target = replace(target, regs=target.regs[:2],
                     classes=(replace(target.classes[0], members=("r0", "r1")),),
                     abi=Abi({8: ("r0", "r1")}, {8: "r0"}, ("r1",)))
    source = source_for("count_down").replace("def count_down(", "def f(")
    result = compile_source(source, target, POOL)
    function = result.functions["f"]
    assert function._ir.abi["preserves"] == ("r1",)
    assert {"preserve-save", "preserve-restore"} <= {m._role for m in function.mir}
    broken = _replace_mir(function, (
        replace(m, _abi={**m._abi, "preserves": ()}) if m.op == "ret" else m
        for m in function.mir if m._role not in ("preserve-save", "preserve-restore")
    ))
    assert _execute(result, target, binding, (3,))[1].read("r1") == 0
    altered = replace(result, functions={"f": broken})
    assert _execute(altered, target, binding, (3,))[1].read("r1") == 6
    with pytest.raises(InternalError, match="V7.*preserved register 'r1'"):
        _verify(broken, target, POOL, {"f": function._ir.abi})


@pytest.mark.parametrize("field,value", (
    ("args", ()), ("ret", RegLoc("r1")), ("clobbers", ()),
    ("preserves", ()), ("unexpected", True),
))
def test_compiled_callee_interface_is_checked_field_by_field(field, value):
    target = toy("t1", 8)[0]
    source = HEADER + '''from rpkbin.mcucc.lang import fn
def f(a:u8)->u8:
    return helper(a)
@fn(preserves=("r3",))
def helper(a:u8)->u8:
    return a+1
'''
    result = compile_source(source, target, POOL)
    function, callee = (result.functions[n] for n in ("f", "helper"))
    assert callee.clobbers == ("r0",)
    interfaces = {"f": function._ir.abi,
                  "helper": {**callee._ir.abi, "clobbers": callee.clobbers}}
    assert _verify(function, target, POOL, interfaces)
    broken = _replace_mir(function, (
        replace(m, _callee_abi={**m._callee_abi, field: value}) if m.op == "call" else m
        for m in function.mir
    ))
    with pytest.raises(InternalError, match=rf"V5.*helper.*{field}"):
        _verify(broken, target, POOL, interfaces)


def test_c5_rejects_wrong_value_written_to_a_sampled_address():
    target, binding, _ = toy("t1", 8)
    store = next(f for f in target.forms if f.op == "st")
    control = replace(target, forms=tuple(f for f in target.forms if f.op in ("st", "jmp", "ret")),
                      long_branch="none")
    assert check_forms(control, binding, (0,)) == []
    def encode(values):
        if values["m"] == 0:
            other = "r1" if values["s"] == "r0" else "r0"
            return store.encode({**values, "s": other})
        return store.encode(values)
    broken = replace(control, forms=tuple(replace(f, encode=encode) if f is store else f
                                          for f in control.forms))
    assert any(f.form_id == store.id and f.check == "C5"
               for f in check_forms(broken, binding, (0,)))


def test_v6_rejects_two_wrong_stores_even_when_the_final_memory_matches():
    target, binding, _ = toy("t1", 8)
    source = HEADER + 'def f():\n    mem.sram[48] += 1\n    mem.sram[48] += 1\n'
    result = compile_source(source, target, POOL)
    function = result.functions["f"]
    assert sum(m.op == "st" for m in function.mir) == 2
    adds = [i for i, m in enumerate(function.mir) if m.op == "add"]
    assert len(adds) == 2
    initial = {"sram": [1] * 256}
    _, expected = run_reference(source, "f", (), initial, addr_bits={"sram": 8})
    assert expected["sram"][48] == 3

    for edits, value in (({}, 3), ({adds[0]: 2}, 4), ({adds[1]: 0}, 2),
                         ({adds[0]: 2, adds[1]: 0}, 3)):
        mir = list(function.mir)
        for index, immediate in edits.items():
            operation = mir[index]
            key = operation.form.bind["b"]
            assert operation.values[key] == 1
            mir[index] = replace(operation, values={**operation.values, key: immediate})
        candidate = _replace_mir(function, mir)
        altered = replace(result, functions={"f": candidate})
        _, sim = _execute(altered, target, binding, ())
        assert sim.read("sram[48]") == value
        if len(edits) == 2:
            assert [sim.read(f"sram[{i}]") for i in range(256)] == expected["sram"]
        if edits:
            with pytest.raises(InternalError, match="V6.*wrong store"):
                _verify(candidate, target, POOL, {"f": function._ir.abi})
        else:
            assert _verify(candidate, target, POOL, {"f": function._ir.abi}) == ()
