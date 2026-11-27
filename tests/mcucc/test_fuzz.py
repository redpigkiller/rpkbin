import ast
from collections import Counter
import json
import random
import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.assemble import _assemble, _placement
from rpkbin.mcucc.diff import _read, _write
from rpkbin.mcucc.errors import AllocationError, InternalError, UnsupportedError
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.target import MemLoc, T
from gen_programs import generate
from test_formcheck import toy


PREFIXES = {
    "t0": ('helper', '@extern(args=("w",),ret="w",clobbers=())'),
    "t1": ('helper_b', '@extern(args=("r0",),ret="r0",clobbers=("r1","r2","r3"))'),
    "t2": ('helper', '@extern(args=("r0",),ret="r0",clobbers=("r1","r2","r3","r4","r5"))'),
}


def coverage_counts(sources, helper):
    counts = Counter()
    for source in sources:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.BinOp, ast.BoolOp, ast.UnaryOp)):
                counts[type(node.op).__name__] += 1
                if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.LShift, ast.RShift)):
                    counts[f"Shift{node.right.value}"] += 1
            elif isinstance(node, ast.Compare):
                counts.update(type(op).__name__ for op in node.ops)
            elif isinstance(node, ast.If):
                counts["If"] += 1
                counts["Else"] += bool(node.orelse)
            elif isinstance(node, ast.For):
                bound = node.iter.args[-1]
                counts["ConstantLoop" if isinstance(bound, ast.Constant) else "MaskedLoop"] += 1
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == helper:
                counts["ExternCall"] += 1
            elif isinstance(node, ast.Subscript):
                counts["MemoryRead" if isinstance(node.ctx, ast.Load) else "MemoryWrite"] += 1
    return counts


def require_coverage(counts):
    required = {"Add", "Sub", "BitAnd", "BitOr", "BitXor", "Invert", "LShift", "RShift",
                "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "And", "Or", "Not", "If", "Else",
                "ConstantLoop", "MaskedLoop", "MemoryRead", "MemoryWrite", "ExternCall", "Shift1", "Shift2", "Shift3"}
    assert not required - counts.keys(), (sorted(required - counts.keys()), counts)


def test_cr16_generator_covers_all_required_ast_kinds():
    require_coverage(coverage_counts((generate(seed, "helper") for seed in range(300)), "helper"))


@pytest.mark.parametrize("helper", (None, "helper"))
def test_generator_respects_program_limits(helper):
    def depth(statements):
        return max((1 + max(depth(node.body), depth(node.orelse))
                    for node in statements if isinstance(node, (ast.If, ast.For))), default=0)
    for seed in range(300):
        tree = ast.parse(generate(seed, helper))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        assert 1 <= len(function.args.args) <= 3
        assert len({n.id for n in ast.walk(function) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}) <= 6
        assert sum(isinstance(n, ast.stmt) and n is not function for n in ast.walk(function)) <= 12
        assert depth(function.body) <= 2
        for node in ast.walk(function):
            if isinstance(node, ast.Subscript):
                assert isinstance(node.slice, ast.Constant) and 0x30 <= node.slice.value <= 0x3f
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.LShift, ast.RShift)):
                assert isinstance(node.right, ast.Constant) and 1 <= node.right.value <= 3
            if isinstance(node, ast.For):
                bound = node.iter.args[0]
                if isinstance(bound, ast.Constant):
                    assert 0 <= bound.value <= 8
                else:
                    assert isinstance(bound, ast.BinOp) and isinstance(bound.op, ast.BitAnd) and bound.right.value == 7


def run_case(result, target, binding, stubs, name, arguments, seed):
    rng = random.Random(seed)
    function = result.functions[name]
    call = next(f for f in target.forms if f.op == "call")
    origin = call.bytes + len(binding.halt_cells)
    _, _, _, end = _placement(result.functions, origin)
    external, extra = {}, {}
    for symbol, cells in stubs.items():
        external[symbol] = end
        extra.update((end + i, c) for i, c in enumerate(cells))
        end += len(cells)
    program = _assemble(result, target, origin, external)
    operand = next(o for o in call.operands if isinstance(o, T))
    driver = (*call.encode({operand.name: program.symbols[name]}), *binding.halt_cells)
    sim = binding.make_sim()
    if binding.init:
        binding.init(sim)
    sim.load_cells(binding.program_space, {**program.cells, **extra, **dict(enumerate(driver))})
    native = binding.names or {}
    for reg in target.regs:
        sim.poke(native.get(reg.name, reg.name), rng.getrandbits(reg.bits))
    memory = {}
    for space in target.spaces:
        if space.kind == "data":
            memory[space.name] = [rng.getrandbits(target.cell_bits) for _ in range(min(256,1 << space.addr_bits))]
            sim.load_cells(native.get(space.name, space.name), dict(enumerate(memory[space.name])))
    for location, value in zip(function._ir.abi["args"], arguments):
        _write(sim, binding, target, location, value)
        if isinstance(location, MemLoc):
            memory[location.space][location.addr] = value
    registers = {reg.name: sim.read(native.get(reg.name, reg.name)) for reg in target.regs}
    depth = sim.read("sp")
    outcome = sim.run(max_steps=200000)
    assert outcome.stop_reason == "halted", (outcome, result.asm)
    assert sim.read("sp") == depth
    for reg in target.regs:
        if reg.name not in function.clobbers:
            assert sim.read(native.get(reg.name, reg.name)) == registers[reg.name], (reg.name, result.asm)
    actual = _read(sim, binding, target, function._ir.abi["ret"]) if function._ir.abi["ret"] else None
    after = {space: [sim.read(f"{native.get(space, space)}[{i}]") for i in range(len(cells))] for space, cells in memory.items()}
    return actual, after, memory


@pytest.mark.parametrize("target_name,width", (("t0",8),("t1",8),("t2",8),("t2",16)))
def test_three_hundred_generated_programs(target_name,width,record_testsuite_property):
    target, binding, stubs = toy(target_name, width)
    pool = (0x20, 0x2f) if target_name == "t0" else (0x60, 0x6f)
    config = CompileConfig({"sram": pool}, False)
    helper, decorator = PREFIXES[target_name]
    prefix = f"from rpkbin.mcucc.lang import u8, extern\n{decorator}\ndef {helper}(x:u8)->u8:\n    return x ^ 0x5A\n"
    rejections, sources = [], []
    for seed in range(300):
        generated = generate(seed, helper)
        sources.append(generated)
        source = prefix + generated
        try:
            result = compile_source(source, target, config)
        except (UnsupportedError, AllocationError) as exc:
            rejections.append((seed, str(exc)))
            continue
        except InternalError as exc:
            pytest.fail(f"{target_name}/{width} seed={seed}: {exc}\n{source}")
        count = len(result.functions["generated"]._ir.params)
        rng = random.Random(seed)
        for arguments in ((0,) * count, (255,) * count, tuple(rng.randrange(256) for _ in range(count))):
            try:
                actual, after, memory = run_case(result, target, binding, stubs, "generated", arguments, seed)
            except AssertionError as exc:
                raise AssertionError(f"{target_name}/{width} seed={seed}\n{source}\n{result.asm}\n{exc}") from exc
            expected, reference = run_reference(source, "generated", arguments, memory, addr_bits={s.name:s.addr_bits for s in target.spaces if s.kind=="data"})
            assert actual == expected, (target_name, seed, source, result.asm, arguments, actual, expected)
            for address, value in enumerate(reference["sram"]):
                if not pool[0] <= address <= pool[1]:
                    assert after["sram"][address] == value, (target_name, seed, source, result.asm, address)
    assert len(rejections) <= 15, rejections
    counts = coverage_counts(sources, helper)
    require_coverage(counts)
    tag = f"fuzz.{target_name}.{width}"
    for key, value in (("generated", len(sources)), ("accepted", len(sources)-len(rejections)),
                       ("rejected", len(rejections)), ("machine_runs", 3*(len(sources)-len(rejections))),
                       ("ast", json.dumps(dict(sorted(counts.items()))))):
        record_testsuite_property(tag + "." + key, value)
