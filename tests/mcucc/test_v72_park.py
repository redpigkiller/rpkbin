import pytest

from rpkbin.mcucc import CompileConfig, compile_source, compiler
from rpkbin.mcucc.cli import _parser
from rpkbin.mcucc.errors import ConfigError
from test_formcheck import toy


POLICIES = ("smallest", "stack_first", "slot_first")


@pytest.mark.parametrize("policy", (*POLICIES, "invalid", None, []))
def test_park_policy_config(policy):
    if policy in POLICIES:
        assert CompileConfig({}, False, park_policy=policy).park_policy == policy
    else:
        with pytest.raises(ConfigError, match="park_policy"):
            CompileConfig({}, False, park_policy=policy)


@pytest.mark.parametrize("command", ("compile", "verify"))
@pytest.mark.parametrize("policy", POLICIES)
def test_park_policy_cli(command, policy):
    args = _parser().parse_args([command, "source.py", "--target", "toy.py",
                                 "--spill-pool", "sram:96-111", "--park-policy", policy])
    assert args.park_policy == policy


@pytest.mark.parametrize("policy", POLICIES)
def test_no_vreg_slot_skips_variant_b(monkeypatch, policy):
    target, _, _ = toy("t1", 8)
    source = "from rpkbin.mcucc.lang import fn, u8\n@fn()\ndef f(a: u8) -> u8:\n    return a + 1\n"
    actual = compiler._compile_variant

    def compile_a(*args, **kwargs):
        assert not kwargs.get("park_budget")
        return actual(*args, **kwargs)

    monkeypatch.setattr(compiler, "_compile_variant", compile_a)
    baseline = compile_source(source, target, CompileConfig({}, True))
    zero = compile_source(source, target, CompileConfig({}, True, park_policy=policy))
    enabled = compile_source(source, target, CompileConfig({}, True, park_depth=8, park_policy=policy))
    assert zero.asm == enabled.asm == baseline.asm
    assert zero.report() == baseline.report()
    assert enabled.report()["functions"]["f"] == {
        **baseline.report()["functions"]["f"], "parks": 0, "push_depth": 0}


def test_stack_first_chooses_larger_usable_variant():
    from types import SimpleNamespace

    first = SimpleNamespace(bytes=5, _has_vreg_slots=True)
    second = SimpleNamespace(bytes=7)
    assert compiler._select_variant(first, second, "smallest") is first
    assert compiler._select_variant(first, second, "stack_first") is second
    assert compiler._select_variant(first, None, "stack_first") is first



def test_register_only_b_matches_a_when_a_has_no_vreg_slot(monkeypatch):
    from rpkbin.mcucc.assemble import assemble
    from rpkbin.mcucc.regalloc import _Allocator
    from rpkbin.mcucc.target import SlotLoc

    target, _, _ = toy("t2", 16)
    source = "from rpkbin.mcucc.lang import fn, mem\n@fn()\ndef f():\n    mem.sram[145] = mem.sram[144] + 1\n"
    config = CompileConfig({"sram": (96, 111)}, True, park_depth=8)
    compile_variant = compiler._compile_variant
    original = _Allocator._refresh_candidates
    checked = []

    def refresh(allocator, gaps):
        original(allocator, gaps)
        assert any(isinstance(loc, SlotLoc) for group in allocator.base_candidates.values() for loc in group)
        assert not allocator.pinned_slots
        assert not any(isinstance(loc, SlotLoc) for group in allocator.candidates.values() for loc in group)
        checked.append(True)

    def compare(*args, **kwargs):
        assert not kwargs.get("park_budget")
        first = compile_variant(*args, **kwargs)
        assert not first._has_vreg_slots
        second = compile_variant(*args, **kwargs, park_budget=8)
        assert first.mir == second.mir
        first_result = compiler.CompileResult("", {"f": first})
        second_result = compiler.CompileResult("", {"f": second})
        assert first_result.report() == second_result.report()
        assert assemble(first_result, target, 0).cells == assemble(second_result, target, 0).cells
        return first

    monkeypatch.setattr(_Allocator, "_refresh_candidates", refresh)
    monkeypatch.setattr(compiler, "_compile_variant", compare)
    compile_source(source, target, config)
    assert checked == [True]



def test_family_naturally_parks_across_loop():
    from corpus.park import park_source
    from rpkbin.mcucc.park import _regions

    target, _, _ = toy("t2", 16)
    source = park_source(1, 3, True)
    pool = {"sram": (96, 111)}
    baseline = compile_source(source, target, CompileConfig(pool, False)).functions["pressure"]
    fn = compile_source(source, target, CompileConfig(pool, False, park_depth=8)).functions["pressure"]
    assert baseline.slots and fn.bytes < baseline.bytes and fn._parks == 1
    push, pop = [m for m in fn.mir if m._role == "park"]
    assert push.op == "push" and pop.op == "pop" and push._park == pop._park
    before, after, loop = next(iter(_regions(fn._ir)))
    blocks = {b.id: b for b in fn._ir.blocks}
    assert push._origin == (before, len(blocks[before].instrs))
    assert pop._origin == (after, 0)
    assert before not in loop and after not in loop



def test_nested_parks_with_preservation_fall_back_at_depth_one(monkeypatch):
    from corpus.park import park_source
    from rpkbin.mcucc.assemble import assemble
    from rpkbin.mcucc.errors import UnsupportedError

    target, _, _ = toy("t2", 16)
    source = park_source(2, 5, False).replace("@fn()", '@fn(preserves=("x0",))')
    pool = {"sram": (96, 111)}
    baseline = compile_source(source, target, CompileConfig(pool, True))
    roomy = compile_source(source, target, CompileConfig(pool, True, park_depth=8))
    expected = baseline.functions["pressure"]
    nested = roomy.functions["pressure"]
    assert expected.slots and nested.bytes < expected.bytes
    assert nested._parks == 2 and nested._push_depth == 3
    events = [(m.op, m._park) for m in nested.mir if m._role == "park"]
    assert [op for op, _ in events] == ["push", "push", "pop", "pop"]
    assert [marker for _, marker in events[:2]] == [marker for _, marker in reversed(events[2:])]

    original = compiler._compile_variant
    rejections = []

    def observe(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except UnsupportedError as error:
            rejections.append(str(error))
            raise

    monkeypatch.setattr(compiler, "_compile_variant", observe)
    shallow = compile_source(source, target, CompileConfig(pool, True, park_depth=1))
    assert rejections == ["pressure: park variant exceeds push budget 1"]
    assert shallow.asm == baseline.asm
    assert assemble(shallow, target, 0).cells == assemble(baseline, target, 0).cells
    assert shallow.report()["functions"]["pressure"] == {
        **baseline.report()["functions"]["pressure"], "parks": 0, "push_depth": 1}
