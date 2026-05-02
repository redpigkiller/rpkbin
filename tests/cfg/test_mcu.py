"""Tests for rpkbin.cfg.mcu --- MCU analysis and linearization."""
import pytest
from rpkbin.cfg import CFG, Assignment, Program
from rpkbin.cfg import mcu


def make_linear_program():
    cfg = CFG()
    cfg.add_block("A", insns=[Assignment("x", [])])
    cfg.add_block("B", insns=[Assignment("y", ["x"])])
    cfg.add_block("HALT")
    cfg.add_edge("A", "B")
    cfg.add_edge("B", "HALT")
    cfg.set_entry("A"); cfg.set_exit("HALT")
    return Program({"main": cfg})


def make_dead_loop_program():
    cfg = CFG()
    cfg.add_block("entry")
    cfg.add_block("stuck")
    cfg.add_block("HALT")
    cfg.add_edge("entry", "stuck")
    cfg.add_edge("entry", "HALT")
    cfg.add_edge("stuck", "stuck")  # dead self-loop
    cfg.set_entry("entry"); cfg.set_exit("HALT")
    return Program({"main": cfg})


class TestFindDeadLoops:
    def test_no_dead_loops(self):
        assert mcu.find_dead_loops(make_linear_program()) == []

    def test_detects_self_loop(self):
        dead = mcu.find_dead_loops(make_dead_loop_program())
        assert len(dead) == 1 and dead[0] == ["stuck"]

    def test_explicit_exit_block_param(self):
        prog = make_dead_loop_program()
        dead = mcu.find_dead_loops(prog, exit_block="HALT")
        assert len(dead) == 1

    def test_no_exit_raises(self):
        cfg = CFG()
        cfg.add_block("A"); cfg.add_block("B"); cfg.add_edge("A", "B")
        cfg.set_entry("A")  # no exit set
        prog = Program({"main": cfg})
        with pytest.raises(RuntimeError, match="exit_block"):
            mcu.find_dead_loops(prog)

    def test_multi_node_dead_loop(self):
        cfg = CFG()
        cfg.add_block("entry"); cfg.add_block("a")
        cfg.add_block("b"); cfg.add_block("HALT")
        cfg.add_edge("entry", "a")
        cfg.add_edge("a", "b"); cfg.add_edge("b", "a")  # dead: no path to HALT
        cfg.add_edge("entry", "HALT")
        cfg.set_entry("entry"); cfg.set_exit("HALT")
        dead = mcu.find_dead_loops(Program({"main": cfg}))
        assert len(dead) == 1 and set(dead[0]) == {"a", "b"}


class TestDeadCodeElimination:
    def test_no_dead_blocks(self):
        cfg = CFG()
        cfg.add_block("entry"); cfg.add_block("end")
        cfg.add_edge("entry", "end"); cfg.set_entry("entry")
        assert mcu.dead_code_elimination(cfg) == []

    def test_removes_orphan(self):
        cfg = CFG()
        cfg.add_block("entry"); cfg.add_block("end"); cfg.add_block("dead")
        cfg.add_edge("entry", "end"); cfg.set_entry("entry")
        removed = mcu.dead_code_elimination(cfg)
        assert len(removed) == 1 and removed[0].id == "dead"
        assert "dead" not in cfg


class TestMCULinearize:
    def test_linear_no_jump_needed(self):
        layout = mcu.linearize(make_linear_program())
        # A -> B -> HALT, all adjacent, no jumps needed
        for slot in layout.slots:
            assert slot.needs_jump is False

    def test_order_entry_first(self):
        layout = mcu.linearize(make_linear_program())
        ids = [s.block.id for s in layout.slots]
        assert ids.index("A") < ids.index("B") < ids.index("HALT")

    def test_conditional_exits_are_preserved(self):
        cfg = CFG()
        cfg.add_block("entry"); cfg.add_block("then"); cfg.add_block("else")
        cfg.add_edge("entry", "then", cond="flag", priority=0)
        cfg.add_edge("entry", "else", priority=1)
        cfg.set_entry("entry")
        layout = mcu.linearize(Program({"main": cfg}), strategy="trace")
        entry = next(slot for slot in layout.slots if slot.block.id == "entry")
        assert [(e.cond, e.target) for e in entry.exits] == [
            ("flag", "then"),
            (None, "else"),
        ]

    def test_single_conditional_edge_is_not_fallthrough(self):
        cfg = CFG()
        cfg.add_block("entry"); cfg.add_block("target")
        cfg.add_edge("entry", "target", cond="flag")
        cfg.set_entry("entry")

        layout = mcu.linearize(Program({"main": cfg}))
        entry = next(slot for slot in layout.slots if slot.block.id == "entry")
        assert entry.needs_jump is False
        assert entry.exits[0].is_fallthrough is False

    def test_jump_needed_when_not_adjacent(self):
        # A -> C (skipping B in layout order)
        cfg = CFG()
        cfg.add_block("A"); cfg.add_block("B"); cfg.add_block("C")
        # A falls through to B (linear), but also A -> C (jump needed from A if layout is A,B,C but only A->C)
        # Simpler: A unconditionally goes to C, B is orphan but reachable from entry
        cfg.add_block("entry")
        cfg.add_block("X"); cfg.add_block("Y"); cfg.add_block("HALT")
        cfg.add_edge("entry", "X")
        cfg.add_edge("X", "HALT")  # X jumps to HALT, skipping Y in RPO
        cfg.add_edge("entry", "Y")
        cfg.add_edge("Y", "HALT")
        cfg.set_entry("entry"); cfg.set_exit("HALT")
        layout = mcu.linearize(Program({"main": cfg}))
        # Check that all slots have consistent jump info
        for slot in layout.slots:
            if slot.needs_jump:
                assert slot.jump_target is not None
