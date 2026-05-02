"""Tests for rpkbin.cfg.analysis --- call graph and liveness."""
import pytest
from rpkbin.cfg import CFG, Assignment, CallRef, OtherInsn, Program
from rpkbin.cfg.analysis import (
    build_call_graph, check_call_depth,
    interprocedural_liveness, FunctionSummary, LivenessResult,
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
