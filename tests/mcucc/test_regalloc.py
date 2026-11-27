from dataclasses import replace
import os
import subprocess
import sys
import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import AllocationError, InternalError, UnsupportedError
from test_compile import source_for
from test_formcheck import toy


GOLDENS = (
    ("t1", "add2", None, "add2:\n    add r0, r1\n    ret\n", 2),
    ("t1", "inc", None, "inc:\n    add r0, #1\n    ret\n", 3),
    ("t1", "rsub", None, "rsub:\n    sub r1, r0\n    mov r0, r1\n    ret\n", 3),
    ("t1", "three", None, "three:\n    add r0, r2\n    ret\n", 2),
    ("t1", "fold", None, "fold:\n    add r0, #7\n    ret\n", 3),
    ("t0", "add2", None, "add2:\n    add w, [0x10]\n    ret\n", 3),
    ("t0", "mixw", "def mixw(a: u8, b: u8) -> u8:\n    return (a & 0x0F) | b\n", "mixw:\n    and w, #0xf\n    or w, [0x10]\n    ret\n", 5),
    ("t2", "add3", "def add3(a: u8, b: u8, c: u8) -> u8:\n    return a + b + c\n", "add3:\n    add r0, r1\n    add r0, r4\n    ret\n", 3),
    ("t2", "add16", None, "add16:\n    addw x0, x1\n    ret\n", 2),
    ("t2", "lo_plus", None, "lo_plus:\n    add r0, r4\n    ret\n", 2),
)


@pytest.mark.parametrize("target_name,name,body,expected,length", GOLDENS)
def test_allocation_goldens(target_name, name, body, expected, length):
    source = source_for(name) if body is None else "from rpkbin.mcucc.lang import u8, u16\n" + body
    pool = (0x20, 0x2f) if target_name == "t0" else (0x60, 0x6f)
    result = compile_source(source, toy(target_name, 8)[0], CompileConfig({"sram": pool}, False))
    assert result.asm == expected
    assert result.functions[name].bytes == length


def test_allocation_is_deterministic_and_needs_no_pool_for_small_expression():
    target = toy("t1", 8)[0]
    config = CompileConfig({}, False)
    first = compile_source(source_for("add2"), target, config)
    assert first.asm == GOLDENS[0][3]
    for _ in range(3):
        assert compile_source(source_for("add2"), target, config).report() == first.report()
    script = '''import sys
sys.path.insert(0,"tests/mcucc")
from test_formcheck import toy
from test_compile import source_for
from rpkbin.mcucc import CompileConfig,compile_source
print(compile_source(source_for("many"),toy("t2",8)[0],CompileConfig({"sram":(0x60,0x6f)},False)).asm)
'''
    results = [subprocess.check_output([sys.executable, "-c", script], env=dict(os.environ, PYTHONHASHSEED=str(seed))) for seed in (1, 7)]
    assert results[0] == results[1]


def test_resource_failure_names_function_line_and_resource():
    source = "from rpkbin.mcucc.lang import u8\ndef pressure(a:u8,b:u8)->u8:\n    return (a+b) ^ (b-a)\n"
    target = toy("t0", 8)[0]
    with pytest.raises(AllocationError, match="pressure.*(line|instruction|:3).*(slot|scratch|class|pool)"):
        compile_source(source, target, CompileConfig({}, False))


@pytest.mark.parametrize("name", ("abs_diff", "bits"))
def test_infeasible_trial_does_not_reject_supported_branch(name):
    result = compile_source(source_for(name), toy("t0", 8)[0], CompileConfig({"sram": (0x20, 0x2f)}, False))
    assert result.functions[name].verified


def test_preserved_register_is_saved_and_restored_when_written():
    target = toy("t1", 8)[0]
    from rpkbin.mcucc.target import Abi
    target = replace(target, regs=target.regs[:2], classes=(replace(target.classes[0], members=("r0", "r1")),), abi=Abi({8:("r0","r1")},{8:"r0"},("r1",)))
    result = compile_source(source_for("count_down"), target, CompileConfig({"sram": (0x60, 0x6f)}, False))
    function = result.functions["count_down"]
    assert any(m._role == "preserve-save" for m in function.mir)
    assert any(m._role == "preserve-restore" for m in function.mir)
    assert "r1" not in function.clobbers
    from rpkbin.mcucc.verify import _verify
    broken_mir = tuple(m for m in function.mir if m._role != "preserve-restore")
    with pytest.raises(InternalError, match="V7"):
        _verify(replace(function, mir=broken_mir, bytes=sum(m.bytes for m in broken_mir)), target,
                CompileConfig({"sram": (0x60, 0x6f)}, False), {function.name:function._ir.abi})


@pytest.mark.parametrize("body,args,expected", (
    ("def f(a:u16)->u16:\n    low:u8=u8(a)\n    return a+u16(low)\n", (0x1234,), 0x1268),
    ("def f(a:u16)->u16:\n    high:u8=u8(a>>8)\n    return a+u16(high)\n", (0x1234,), 0x1246),
    ("def f(a:u8,b:u16)->u16:\n    wide:u16=u16(a)\n    return wide+b+u16(a)\n", (0x12,0x1234), 0x1258),
    ("def f(h:u8,l:u8)->u16:\n    both:u16=(u16(h)<<8)|u16(l)\n    return both+u16(h)+u16(l)\n", (0x12,0x34), 0x127a),
    ("def f(a:u8)->u8:\n    copied:u8=a\n    return copied+a\n", (0x81,), 2),
))
def test_copy_views_with_source_still_live(body, args, expected):
    from test_compile import execute
    assert execute("t2", 8, "from rpkbin.mcucc.lang import u8,u16\n"+body, "f", args)[0] == expected


def test_native_word_memory_forms_allow_word_slot_without_byte_forms():
    from rpkbin.mcucc.expand import _Context
    from rpkbin.mcucc.frontend import _parse
    from rpkbin.mcucc.lower import _lower
    from rpkbin.mcucc.regalloc import _Allocator
    from rpkbin.mcucc.target import Form, M, R, SlotLoc
    target = toy("t2", 8)[0]
    forms = tuple(f for f in target.forms if f.op not in ("ld", "st")) + (
        Form("wordload", "ld", 16, (R("d","w"),M("m","sram")), {"dst":"d","a":"m"}, 3, "wordload {d}, [{m}]"),
        Form("wordstore", "st", 16, (M("m","sram"),R("s","w")), {"a":"m","b":"s"}, 3, "wordstore [{m}], {s}"),
    )
    target = replace(target, forms=forms)
    function = _lower(_parse("from rpkbin.mcucc.lang import u16\ndef f(a:u16)->u16:\n    return a\n", target, "word.py"))["f"]
    allocator = _Allocator(function, _Context(target, {"sram":(0x60,0x6f)}), {"f":function.abi})
    assert any(isinstance(c, SlotLoc) and c.n == 2 for c in allocator.candidates[function.params[0]])


def test_ret_effects_are_checked_after_return_moves():
    from rpkbin.mcucc.verify import _verify
    target = toy("t1", 8)[0]
    config = CompileConfig({"sram": (0x60,0x6f)}, False)
    function = compile_source(source_for("add2"), target, config).functions["add2"]
    bad_forms = tuple(replace(f, clobbers=("r0",)) if f.op == "ret" else f for f in target.forms)
    bad_target = replace(target, forms=bad_forms)
    with pytest.raises(UnsupportedError, match="ret.*overlap"):
        compile_source(source_for("add2"), bad_target, config)
    bad_return = next(f for f in bad_forms if f.op == "ret")
    mir = tuple(replace(m, form=bad_return if m.op == "ret" else m.form, _target=bad_target) for m in function.mir)
    with pytest.raises(InternalError, match="V7"):
        _verify(replace(function, mir=mir), bad_target, config, {function.name:function._ir.abi})
