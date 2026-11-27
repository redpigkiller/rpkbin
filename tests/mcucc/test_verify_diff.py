from dataclasses import replace
from pathlib import Path
import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.errors import InternalError
from rpkbin.mcucc.verify import _verify
from rpkbin.mcucc.target import MemLoc
from test_compile import source_for
from test_formcheck import toy


@pytest.mark.parametrize("name,width", (("t0",8),("t1",8),("t2",8),("t2",16)))
def test_corpus_differential(name,width):
    target, binding, stubs = toy(name, width)
    config = CompileConfig({"sram": (0x20, 0x2f) if name == "t0" else (0x60, 0x6f)}, False)
    depth = []
    def post_check(phase, sim):
        if phase == "before":
            depth[:] = [sim.read("sp")]
        else:
            assert sim.read("sp") == depth[0]
    report = run_differential((Path(__file__).parent / "corpus" / "programs.py").read_text(),
        target, binding, config, stubs=stubs, seeds=(17,), inputs_per_function=120,
        window=256, post_check=post_check)
    assert not report.failures, report
    assert report.checked >= 10 * 120


@pytest.mark.parametrize("corruption", ("operand", "size", "flags", "value", "return", "slot"))
def test_final_verifier_rejects_broken_mir(corruption, monkeypatch):
    from rpkbin.mcucc import regalloc
    from rpkbin.mcucc import compiler
    from rpkbin.mcucc.frames import _temporary_assignment
    # These mutations deliberately address the original all-slot M4 sequences.
    monkeypatch.setattr(regalloc, "_allocate", lambda fn, ctx, interfaces: _temporary_assignment(fn, ctx))
    monkeypatch.setattr(compiler, "_peephole", lambda mir, target, name: tuple(mir))
    target = toy("t1", 8)[0]
    config = CompileConfig({"sram": (0x60, 0x6f)}, False)
    name = "maxu" if corruption == "flags" else "add2"
    function = compile_source(source_for(name), target, config).functions[name]
    instructions = list(function.mir)
    if corruption == "operand":
        index = next(i for i, m in enumerate(instructions) if m.op == "ld")
        values = dict(instructions[index].values, d="absent")
        instructions[index] = replace(instructions[index], values=values)
    elif corruption == "size":
        function = replace(function, bytes=function.bytes + 1)
    elif corruption == "flags":
        instructions = [m for m in instructions if m.op != "cmp"]
    elif corruption == "value":
        index = next(i for i, m in enumerate(instructions) if m.op == "add")
        instructions[index] = replace(instructions[index], form=next(f for f in target.forms if f.op == "sub" and f.id.endswith("rr")))
    elif corruption == "return":
        index = next(i for i, m in enumerate(instructions) if m._role == "return" and m.op == "ld")
        instructions[index] = replace(instructions[index], values=dict(instructions[index].values, m=MemLoc("sram", 0x60)))
    else:
        function = replace(function, slots=(*function.slots, function.slots[0]))
    if corruption not in ("size", "slot"):
        function = replace(function, bytes=sum(m.bytes for m in instructions))
    function = replace(function, mir=tuple(instructions))
    with pytest.raises(InternalError):
        _verify(function, target, config, {name: function._ir.abi})


def test_stack_depth_and_branch_polarity_are_checked():
    target = toy("t2", 8)[0]
    config = CompileConfig({"sram": (0x60, 0x6f)}, False)
    function = compile_source(source_for("add2"), target, config).functions["add2"]
    from rpkbin.mcucc.expand import MInstr
    push = next(f for f in target.forms if f.op == "push")
    instruction = MInstr(push, {push.bind["a"]: "x0"}, _target=target, _abi=function._ir.abi)
    broken = replace(function, mir=(function.mir[0], instruction, *function.mir[1:]), bytes=function.bytes + push.bytes)
    with pytest.raises(InternalError, match="V3"):
        _verify(broken, target, config, {function.name: function._ir.abi})
    function = compile_source(source_for("maxu"), target, config).functions["maxu"]
    mir = list(function.mir)
    index = next(i for i, m in enumerate(mir) if m.op == "br")
    flag, value = next(iter(mir[index].form.flags_read.items()))
    opposite = next(f for f in target.forms if f.op == "br" and f.flags_read == {flag: 1 - value})
    mir[index] = replace(mir[index], form=opposite)
    with pytest.raises(InternalError, match="V4"):
        _verify(replace(function, mir=tuple(mir)), target, config, {function.name: function._ir.abi})


def test_extern_linking_and_reference_boundary():
    target, binding, stubs = toy("t1", 8)
    source = '''from rpkbin.mcucc.lang import u8, extern
@extern(args=("r0",), ret="r0", clobbers=("r1", "r2", "r3"))
def helper_b(x: u8) -> u8:
    return x ^ 0x5a
def fb(a: u8, b: u8) -> u8:
    return helper_b(a) + b
'''
    report = run_differential(source, target, binding, CompileConfig({"sram": (0x60, 0x6f)}, False),
        stubs=stubs, seeds=(9,), inputs_per_function=120, window=256)
    assert not report.failures, report
