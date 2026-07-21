"""Tests for rpkbin.cfg.analysis --- call graph and liveness."""
import pytest
from rpkbin.cfg import CFG, Assignment, CallRef, OtherInsn, Program
from rpkbin.cfg.analysis import (
    build_call_graph, check_call_depth,
    interprocedural_liveness, FunctionSummary, LivenessResult,
    _intraprocedural_liveness,
)


def _make_sub(name):
    cfg = CFG()
    cfg.add_block(f"{name}_body", insns=[Assignment("y", ["x"])])
    cfg.add_block(f"{name}_ret")
    cfg.add_edge(f"{name}_body", f"{name}_ret")
    cfg.set_entry(f"{name}_body")
    cfg.set_exit(f"{name}_ret")
    return cfg


def _make_main_with_call(callee):
    cfg = CFG()
    cfg.add_block("IDLE", insns=[Assignment("x", []), CallRef(callee)])
    cfg.add_block("DONE")
    cfg.add_edge("IDLE", "DONE", cond="ok", priority=0)
    cfg.set_entry("IDLE")
    return cfg


def test_function_summary_positional_contract_and_repr_order():
    summary = FunctionSummary({"d"}, {"u"}, {"m"})

    assert summary.defs == {"d"}
    assert summary.uses == {"u"}
    assert summary.must_defs == {"m"}
    assert repr(summary) == "FunctionSummary(defs=['d'], uses=['u'], must_defs=['m'])"


def _summary(cfg):
    return _intraprocedural_liveness(cfg, {})[1]


class TestBuildCallGraph:
    def test_no_calls(self):
        main = CFG()
        main.add_block("A"); main.add_block("B")
        main.add_edge("A", "B"); main.set_entry("A")
        prog = Program({"main": main})
        cg = build_call_graph(prog)
        assert list(cg.edges()) == []

    def test_single_call(self):
        sub = _make_sub("s1")
        main = _make_main_with_call("s1")
        prog = Program({"main": main, "s1": sub})
        cg = build_call_graph(prog)
        assert cg.has_edge("main", "s1")

    def test_unknown_callee_raises(self):
        main = CFG()
        main.add_block("A", insns=[CallRef("ghost")])
        main.set_entry("A")
        with pytest.raises(KeyError, match="ghost"):
            Program({"main": main})


class TestCheckCallDepth:
    def test_no_calls_depth_zero(self):
        main = CFG()
        main.add_block("A"); main.set_entry("A")
        assert check_call_depth(Program({"main": main})) == 0

    def test_depth_one(self):
        sub = _make_sub("s1")
        main = _make_main_with_call("s1")
        prog = Program({"main": main, "s1": sub})
        assert check_call_depth(prog) == 1

    def test_depth_exceeds_raises(self):
        s1 = _make_sub("s1")
        # make s1 call s2
        s1.get_block("s1_body").insns.append(CallRef("s2"))
        s2 = _make_sub("s2")
        main = _make_main_with_call("s1")
        prog = Program({"main": main, "s1": s1, "s2": s2})
        with pytest.raises(ValueError, match="2"):
            check_call_depth(prog, max_depth=1)


class TestInterproceduralLiveness:
    def test_single_function_linear(self):
        cfg = CFG()
        cfg.add_block("a", insns=[Assignment("x", [])])
        cfg.add_block("b", insns=[Assignment("y", ["x"])])
        cfg.add_block("c")
        cfg.add_edge("a", "b"); cfg.add_edge("b", "c")
        cfg.set_entry("a"); cfg.set_exit("c")
        prog = Program({"main": cfg})
        results = interprocedural_liveness(prog)
        r = results["main"]
        assert "x" not in r.live_in["a"]
        assert "x" in r.live_out["a"]
        assert "x" in r.live_in["b"]

    def test_callee_defs_propagate(self):
        # sub defines "y"
        sub = CFG()
        sub.add_block("s", insns=[Assignment("y", [])])
        sub.add_block("r")
        sub.add_edge("s", "r"); sub.set_entry("s"); sub.set_exit("r")
        # main: B uses y, A calls sub before B
        main = CFG()
        main.add_block("A", insns=[CallRef("sub")])
        main.add_block("B", insns=[Assignment("z", ["y"])])
        main.add_block("C")
        main.add_edge("A", "B"); main.add_edge("B", "C")
        main.set_entry("A"); main.set_exit("C")
        prog = Program({"main": main, "sub": sub})
        results = interprocedural_liveness(prog)
        # y is live-in of B
        assert "y" in results["main"].live_in["B"]

    def test_liveness_result_helpers(self):
        cfg = CFG()
        cfg.add_block("a", insns=[Assignment("x", [])])
        cfg.add_block("b", insns=[Assignment("z", ["x"])])
        cfg.add_edge("a", "b"); cfg.set_entry("a")
        results = interprocedural_liveness(Program({"main": cfg}))
        r = results["main"]
        assert r.is_live_at_exit("a", "x") is True
        assert r.is_live_at_entry("a", "x") is False

    def test_conditional_callee_may_def_does_not_kill_caller_value(self):
        sub = CFG()
        sub.add_block("entry")
        sub.add_block("write", insns=[Assignment("x", [])])
        sub.add_block("ret")
        sub.add_edge("entry", "write", cond="write", priority=0)
        sub.add_edge("entry", "ret", cond=None, priority=1)
        sub.add_edge("write", "ret")
        sub.set_entry("entry"); sub.set_exit("ret")

        main = CFG()
        main.add_block("call", insns=[CallRef("sub")])
        main.add_block("use", insns=[Assignment("z", ["x"])])
        main.add_edge("call", "use")
        main.set_entry("call")

        result = interprocedural_liveness(Program({"main": main, "sub": sub}))
        assert "x" in result["main"].live_in["call"]
        summary_caller = CFG()
        summary_caller.add_block("call", insns=[CallRef("sub")])
        summary_caller.set_entry("call")
        summary = _intraprocedural_liveness(summary_caller, {"sub": _summary(sub)})[1]
        assert summary.defs == {"x"}
        assert summary.must_defs == set()

    def test_definite_callee_write_kills_caller_value(self):
        sub = CFG()
        sub.add_block("entry", insns=[Assignment("x", [])])
        sub.add_block("ret")
        sub.add_edge("entry", "ret")
        sub.set_entry("entry"); sub.set_exit("ret")

        main = CFG()
        main.add_block("call", insns=[CallRef("sub")])
        main.add_block("use", insns=[Assignment("z", ["x"])])
        main.add_edge("call", "use")
        main.set_entry("call")

        result = interprocedural_liveness(Program({"main": main, "sub": sub}))
        assert "x" not in result["main"].live_in["call"]
        summary_caller = CFG()
        summary_caller.add_block("call", insns=[CallRef("sub")])
        summary_caller.set_entry("call")
        summary = _intraprocedural_liveness(summary_caller, {"sub": _summary(sub)})[1]
        assert summary.defs == {"x"}
        assert summary.must_defs == {"x"}

    def test_may_def_propagates_through_multiple_call_levels(self):
        leaf = CFG()
        leaf.add_block("entry")
        leaf.add_block("write", insns=[Assignment("x", [])])
        leaf.add_block("ret")
        leaf.add_edge("entry", "write", cond="write", priority=0)
        leaf.add_edge("entry", "ret", cond=None, priority=1)
        leaf.add_edge("write", "ret")
        leaf.set_entry("entry"); leaf.set_exit("ret")

        middle = CFG()
        middle.add_block("entry", insns=[CallRef("leaf")])
        middle.add_block("ret")
        middle.add_edge("entry", "ret")
        middle.set_entry("entry"); middle.set_exit("ret")

        top = CFG()
        top.add_block("entry", insns=[CallRef("middle")])
        top.add_block("ret")
        top.add_edge("entry", "ret")
        top.set_entry("entry"); top.set_exit("ret")

        leaf_summary = _summary(leaf)
        middle_summary = _intraprocedural_liveness(middle, {"leaf": leaf_summary})[1]
        top_summary = _intraprocedural_liveness(top, {"middle": middle_summary})[1]
        assert middle_summary.defs == {"x"}
        assert middle_summary.must_defs == set()
        assert top_summary.defs == {"x"}
        assert top_summary.must_defs == set()


class TestFunctionSummaryContracts:
    def test_positional_compatibility(self):
        summary = FunctionSummary({"d"}, {"u"})
        assert summary.defs == {"d"}
        assert summary.uses == {"u"}
        assert summary.must_defs == set()

    def test_unreachable_def_is_not_may_def(self):
        cfg = CFG()
        cfg.add_block("entry", insns=[Assignment("live", [])])
        cfg.add_block("ret")
        cfg.add_block("dead", insns=[Assignment("ghost", [])])
        cfg.add_edge("entry", "ret")
        cfg.set_entry("entry"); cfg.set_exit("ret")
        summary = _summary(cfg)
        assert summary.defs == {"live"}
        assert "ghost" not in summary.defs

    def test_no_entry_has_no_must_def(self):
        cfg = CFG()
        cfg.add_block("ret", insns=[Assignment("x", [])])
        assert _summary(cfg).must_defs == set()

    def test_unreachable_terminal_does_not_clear_must_def(self):
        cfg = CFG()
        cfg.add_block("entry", insns=[Assignment("x", [])])
        cfg.add_block("ret")
        cfg.add_block("dead")
        cfg.add_edge("entry", "ret")
        cfg.set_entry("entry")
        assert _summary(cfg).must_defs == {"x"}

    def test_non_returning_loop_has_no_must_def(self):
        cfg = CFG()
        cfg.add_block("entry", insns=[Assignment("x", [])])
        cfg.add_edge("entry", "entry")
        cfg.set_entry("entry")
        assert _summary(cfg).must_defs == set()

    def test_unreachable_designated_exit_has_no_must_def(self):
        cfg = CFG()
        cfg.add_block("entry", insns=[Assignment("x", [])])
        cfg.add_edge("entry", "entry")
        cfg.add_block("ret")
        cfg.set_entry("entry"); cfg.set_exit("ret")
        assert _summary(cfg).must_defs == set()

    def test_optional_loop_write_is_not_must_def(self):
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("loop", insns=[Assignment("x", [])])
        cfg.add_block("ret")
        cfg.add_edge("entry", "loop", cond="again", priority=0)
        cfg.add_edge("entry", "ret", cond=None, priority=1)
        cfg.add_edge("loop", "loop", cond="again", priority=0)
        cfg.add_edge("loop", "ret", cond=None, priority=1)
        cfg.set_entry("entry"); cfg.set_exit("ret")
        assert _summary(cfg).must_defs == set()

    def test_all_return_paths_write_is_must_def(self):
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("left", insns=[Assignment("x", [])])
        cfg.add_block("right", insns=[Assignment("x", [])])
        cfg.add_block("ret")
        cfg.add_edge("entry", "left", cond="left", priority=0)
        cfg.add_edge("entry", "right", cond=None, priority=1)
        cfg.add_edge("left", "ret")
        cfg.add_edge("right", "ret")
        cfg.set_entry("entry"); cfg.set_exit("ret")
        assert _summary(cfg).must_defs == {"x"}
