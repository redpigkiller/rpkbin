from pathlib import Path
import ast

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.errors import ConfigError, UnsupportedError
from test_formcheck import toy


def source_for(name):
    tree = ast.parse((Path(__file__).parent / "corpus" / "programs.py").read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if not isinstance(n, ast.FunctionDef) or n.name == name]
    return ast.unparse(tree)


def execute(name, address_width, source, function, args):
    target, binding, _ = toy(name, address_width)
    pool = {"sram": (0x20, 0x2f) if name == "t0" else (0x60, 0x6f)}
    result = compile_source(source, target, CompileConfig(pool, False))
    assert result.functions[function].verified
    program = assemble(result, target, 16)
    sim = binding.make_sim()
    sim.load_cells(binding.program_space, program.cells)
    call = next(f for f in target.forms if f.op == "call")
    cells = call.encode({"t": program.symbols[function]})
    sim.load_cells(binding.program_space, {i: c for i, c in enumerate((*cells, *binding.halt_cells))})
    native = binding.names or {}
    abi = result.functions[function]._ir.abi
    for loc, value in zip(abi["args"], args):
        if isinstance(loc, str):
            sim.poke(native.get(loc, loc), value)
        else:
            sim.poke(f"{native.get(loc.space, loc.space)}[{loc.addr}]", value)
    sim.load_cells(native.get("sram", "sram"), {i: 1 for i in range(256)})
    # Fixed memory ABI arguments must be installed after the initial memory.
    for loc, value in zip(abi["args"], args):
        if not isinstance(loc, str):
            sim.poke(f"{native.get(loc.space, loc.space)}[{loc.addr}]", value)
    before = sim.read("sp")
    outcome = sim.run(max_steps=200000)
    assert outcome.stop_reason == "halted", (result.asm, outcome)
    assert sim.read("sp") == before
    ret = abi["ret"]
    value = sim.read(native.get(ret, ret)) if isinstance(ret, str) else sim.read(f"{native.get(ret.space, ret.space)}[{ret.addr}]")
    return value, result


@pytest.mark.parametrize("name", ("t0", "t1", "t2"))
@pytest.mark.parametrize("function,args,expected", [
    ("add2", (7, 9), 16), ("rsub", (7, 9), 2), ("fold", (250,), 1),
    ("maxu", (7, 9), 9), ("clamp", (9, 7), 7), ("count_down", (3,), 6),
    ("widen", (0x12, 0x34), 0x1234), ("add16", (0x0102, 0x0304), 0x0406),
    ("load", (128,), 1), ("getc", (), 1),
])
def test_initial_end_to_end(name, function, args, expected):
    if name != "t2" and function in ("widen", "add16", "load"):
        target = toy(name, 8)[0]
        with pytest.raises(UnsupportedError):
            compile_source(source_for(function), target, CompileConfig({"sram": (0x60, 0x6f)}, False))
    else:
        assert execute(name, 8, source_for(function), function, args)[0] == expected


def test_invalid_pool_is_rejected():
    target = toy("t1", 8)[0]
    for pool in ({"missing": (0, 1)}, {"sram": (8, 1)}, {"sram": (0, 256)}):
        with pytest.raises(ConfigError):
            compile_source(source_for("add2"), target, CompileConfig(pool, False))
