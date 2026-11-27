import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import ConfigError
from rpkbin.mcucc.cli import _parser
from test_formcheck import toy


def test_park_depth_config():
    assert CompileConfig({}, False).park_depth == 0
    assert CompileConfig({}, False, park_depth=2).park_depth == 2
    with pytest.raises(ConfigError, match="park_depth"):
        CompileConfig({}, False, park_depth=-1)


@pytest.mark.parametrize("command", ("compile", "verify"))
def test_park_depth_cli(command):
    args = _parser().parse_args([command, "source.py", "--target", "toy.py",
                                 "--spill-pool", "sram:96-111", "--park-depth", "8"])
    assert args.park_depth == 8


def test_reports_park_only_when_enabled():
    target, _, _ = toy("t1", 8)
    source = "from rpkbin.mcucc.lang import fn, u8\n@fn()\ndef f(a: u8) -> u8:\n    return a + 1\n"
    plain = compile_source(source, target, CompileConfig({}, False, park_depth=0))
    enabled = compile_source(source, target, CompileConfig({}, False, park_depth=2))
    assert enabled.asm == plain.asm
    assert enabled.report()["functions"]["f"] == {
        **plain.report()["functions"]["f"], "parks": 0, "push_depth": 0}


def test_tie_keeps_variant_a():
    from types import SimpleNamespace
    from rpkbin.mcucc.compiler import _select_variant

    first = SimpleNamespace(bytes=42, _pool_contract="private")
    second = SimpleNamespace(bytes=42, _pool_contract=None)
    assert _select_variant(first, second) is first


def _forced_park(monkeypatch, source, kinds):
    from rpkbin.mcucc import compiler, regalloc
    from rpkbin.mcucc.frontend import _parse
    from rpkbin.mcucc.lower import _lower
    from rpkbin.mcucc.opt import _optimize
    from rpkbin.mcucc.widen import _widen_comparisons

    target, _, _ = toy("t3", 16)
    config = CompileConfig({"sram": (96, 111)}, False, park_depth=8)
    typed = _parse(source, target, "<test>")
    ir = _lower(typed)["f"]
    _optimize(ir)
    _widen_comparisons(ir, target)
    interfaces = {"f": ir.abi}
    chosen = []

    def allocate(function, ctx, interfaces):
        allocator = regalloc._Allocator(function, ctx, interfaces)
        for name, kind in kinds:
            wanted = next(
                g for g in allocator.park.gaps if g.kind == kind and g.value.name_hint == name
            )
            chosen.append(wanted)
        allocator._select_gaps(tuple(chosen))
        # Keep the chosen gaps; isolate verifier tests from R3' pruning.
        allocator._prune_gaps = lambda *args: None
        return allocator.run()

    monkeypatch.setattr(regalloc, "_allocate", allocate)
    fn = compiler._compile_function(ir, typed.functions["f"], target, config,
                                    interfaces, set(), pool_private=False, park_budget=8)
    return fn, target, config, interfaces, chosen


SINGLE = """from rpkbin.mcucc.lang import fn, mem, u16, u8
@fn()
def f(word: u16) -> u16:
    n = mem.sram[128]
    mem.sram[160] = n + 1
    return word + u16(n)
"""

NESTED = """from rpkbin.mcucc.lang import fn, mem, u16, u8
@fn()
def f(outer: u16, inner: u16) -> u16:
    n = mem.sram[128]
    mem.sram[160] = n + 1
    mem.sram[161] = u8(inner)
    return outer + inner
"""

LOOP = """from rpkbin.mcucc.lang import fn, mem, u16, u8
@fn()
def f(word: u16, n: u8, take: u8) -> u16:
    if take != 0:
        for i in range(n):
            mem.sram[160] = i
        mem.sram[162] = 1
    else:
        mem.sram[161] = n
    return word
"""


@pytest.mark.parametrize("mutation, diagnostic", [
    ("delete-pop", "V3"), ("wrong-pop", "V11\\(a\\)"),
    ("late-pop", "V11|V6"), ("restore-overlap", "V11\\(d\\)|V5"),
    ("reduced-budget", "V3"),
])
def test_block_park_mutations(monkeypatch, mutation, diagnostic):
    from dataclasses import replace
    from rpkbin.mcucc.errors import InternalError
    from rpkbin.mcucc.ir import _ir_defs_uses, _live
    from rpkbin.mcucc.target import part
    from rpkbin.mcucc.verify import _verify

    fn, target, config, interfaces, (gap,) = _forced_park(monkeypatch, SINGLE, (("word", "B"),))
    mir = list(fn.mir)
    assert fn._parks == 1
    pop = next(i for i, m in enumerate(mir) if m.op == "pop" and m._role == "park")
    if mutation == "delete-pop":
        mir.pop(pop)
    elif mutation == "wrong-pop":
        m = mir[pop]
        operand = m.form.operands[0]
        members = next(c.members for c in target.classes if c.name == operand.cls)
        alternative = next(r for r in members if r != m.values[operand.name])
        mir[pop] = replace(m, values={operand.name: alternative})
    elif mutation == "late-pop":
        m = mir.pop(pop)
        mir.insert(pop + 1, m)
    elif mutation == "restore-overlap":
        bid, index = gap.end
        b = next(b for b in fn._ir.blocks if b.id == bid)
        ins = (*b.instrs, b.term)[index]
        defs, uses = _ir_defs_uses(ins)
        live = (_live(fn._ir)[2][gap.end] - defs) | uses
        other = next(v for v in live if v != gap.value)
        home = fn._locations[gap.value]
        fn = replace(fn, _locations={**fn._locations,
                                     other: part(target, home, "lo") if other.width == 8 else home})
    else:
        config = replace(config, park_depth=fn._push_depth - 1)
    mutated = replace(fn, mir=tuple(mir), bytes=sum(m.bytes for m in mir))
    with pytest.raises(InternalError, match=diagnostic):
        _verify(mutated, target, config, interfaces)


def test_crossing_park_pushes(monkeypatch):
    from dataclasses import replace
    from rpkbin.mcucc.errors import InternalError
    from rpkbin.mcucc.verify import _verify

    fn, target, config, interfaces, _ = _forced_park(
        monkeypatch, NESTED, (("outer", "B"), ("inner", "B")))
    mir = list(fn.mir)
    one, two = [i for i, m in enumerate(mir) if m.op == "push" and m._role == "park"]
    first, second = mir[one], mir[two]
    mir[one] = replace(second, _origin=first._origin)
    mir[two] = replace(first, _origin=second._origin)
    with pytest.raises(InternalError, match="V11\\(a\\)"):
        _verify(replace(fn, mir=tuple(mir)), target, config, interfaces)


def test_loop_park_without_push(monkeypatch):
    from dataclasses import replace
    from rpkbin.mcucc.errors import InternalError
    from rpkbin.mcucc.verify import _verify

    fn, target, config, interfaces, (gap,) = _forced_park(monkeypatch, LOOP, (("word", "R"),))
    assert gap.start[0] != gap.end[0] and fn._parks == 1
    mir = tuple(m for m in fn.mir if not (m.op == "push" and m._role == "park"))
    with pytest.raises(InternalError, match="V3"):
        _verify(replace(fn, mir=mir, bytes=sum(m.bytes for m in mir)), target, config, interfaces)

def test_edge_bypassing_loop_park(monkeypatch):
    from dataclasses import replace
    from rpkbin.mcucc.errors import InternalError
    from rpkbin.mcucc.target import T
    from rpkbin.mcucc.verify import _verify

    fn, target, config, interfaces, (gap,) = _forced_park(monkeypatch, LOOP, (("word", "R"),))
    preheader = next(b for b in fn._ir.blocks if b.id == gap.start[0])
    head = preheader.term.meta["target"]
    label = next(m._label for m in fn.mir if m._block == head and m._label is not None)
    mir = list(fn.mir)
    branch = next(i for i, m in enumerate(mir) if m.op == "br" and m._block == 0)
    old = mir[branch]
    operand = next(o for o in old.form.operands if isinstance(o, T))
    mir[branch] = replace(old, values={operand.name: label})
    with pytest.raises(InternalError, match="V3|V11"):
        _verify(replace(fn, mir=tuple(mir)), target, config, interfaces)


def test_peephole_removes_empty_park():
    from dataclasses import replace
    from rpkbin.mcucc.diff import run_differential
    from rpkbin.mcucc.peephole import _peephole
    from rpkbin.mcucc.verify import _verify

    target, sim, _ = toy("t2", 16)
    config = CompileConfig({}, False, park_depth=2)
    source = "from rpkbin.mcucc.lang import fn, u16\n@fn()\ndef f(word: u16) -> u16:\n    return word\n"
    fn = compile_source(source, target, config).functions["f"]
    forms = {f.id: f for f in target.forms}
    label, ret = fn.mir
    operands = {"push": {"s": "x0"}, "pop": {"d": "x0"}, "ldi": {"d": "r4", "k": 1}}
    def instruction(op, role, marker=None):
        return replace(ret, form=forms[op], values=operands[op], _role=role, _park=marker)
    redundant = (label, instruction("push", "park", (0, 0)), instruction("ldi", "body"),
                 instruction("pop", "park", (0, 0)), ret)
    simplified = _peephole(redundant, target, "f")
    assert simplified == fn.mir
    assert _verify(replace(fn, mir=simplified), target, config, {"f": fn._ir.abi}) == fn._coverage
    report = run_differential(source, target, sim, config, stubs={}, seeds=(71,),
                              inputs_per_function=8, window=256)
    assert report.checked and not report.failures


def test_push_depth_at_each_call():
    from types import SimpleNamespace
    from rpkbin.mcucc.expand import MInstr
    from rpkbin.mcucc.verify import _stack_flow

    target, _, _ = toy("t2", 16)
    forms = {f.id: f for f in target.forms}
    def instruction(op, **values):
        return MInstr(forms[op], values)
    fn = SimpleNamespace(name="f", mir=(
        instruction("push", s="x0"), instruction("push", s="x1"),
        instruction("pop", d="x1"), instruction("pop", d="x0"),
        instruction("push", s="x0"), instruction("call", t="callee"),
        instruction("pop", d="x0"), instruction("call", t="external"),
        instruction("ret"),
    ))
    assert _stack_flow(fn, {"callee": 2})[0] == 3
