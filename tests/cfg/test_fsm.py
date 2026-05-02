"""Tests for rpkbin.cfg.fsm --- FSM analysis and linearization."""
import pytest
from rpkbin.cfg import CFG, Assignment, CallRef, Program
from rpkbin.cfg import fsm


def make_simple_program():
    cfg = CFG()
    cfg.add_block("IDLE",  label="IDLE",  insns=[Assignment("x", [])])
    cfg.add_block("FETCH", label="FETCH")
    cfg.add_block("DONE",  label="DONE")
    cfg.add_edge("IDLE",  "FETCH", cond="start",  priority=0)
    cfg.add_edge("FETCH", "IDLE",  cond="loop",   priority=0)
    cfg.add_edge("FETCH", "DONE",  cond="halt",   priority=1)
    cfg.set_entry("IDLE")
    return Program({"main": cfg})


def make_program_with_sink():
    cfg = CFG()
    cfg.add_block("IDLE")
    cfg.add_block("WORK")
    cfg.add_block("STUCK")
    cfg.add_edge("IDLE", "WORK",  cond="go")
    cfg.add_edge("IDLE", "STUCK", cond="err")
    cfg.add_edge("WORK", "IDLE")
    cfg.add_edge("STUCK", "STUCK")  # self-loop sink
    cfg.set_entry("IDLE")
    return Program({"main": cfg})


def make_program_with_dead_state():
    cfg = CFG()
    cfg.add_block("IDLE")
    cfg.add_block("WORK")
    cfg.add_block("GHOST")  # no incoming edges
    cfg.add_edge("IDLE", "WORK", cond="go")
    cfg.add_edge("WORK", "IDLE")
    cfg.set_entry("IDLE")
    return Program({"main": cfg})


class TestDeadStates:
    def test_no_dead_states(self):
        assert fsm.find_dead_states(make_simple_program()) == []

    def test_finds_orphan_state(self):
        dead = fsm.find_dead_states(make_program_with_dead_state())
        assert len(dead) == 1 and dead[0].id == "GHOST"


class TestSinkSccs:
    def test_no_sink_in_healthy_fsm(self):
        assert fsm.find_sink_sccs(make_simple_program()) == []

    def test_detects_self_loop_sink(self):
        sinks = fsm.find_sink_sccs(make_program_with_sink())
        assert len(sinks) == 1 and sinks[0] == ["STUCK"]

    def test_multi_node_sink(self):
        cfg = CFG()
        cfg.add_block("IDLE")
        cfg.add_block("A")
        cfg.add_block("B")
        cfg.add_edge("IDLE", "A", cond="go")
        cfg.add_edge("A", "B")
        cfg.add_edge("B", "A")  # A<->B cycle, no path back to IDLE
        cfg.set_entry("IDLE")
        prog = Program({"main": cfg})
        sinks = fsm.find_sink_sccs(prog)
        assert len(sinks) == 1 and set(sinks[0]) == {"A", "B"}


class TestCheckConditionsComplete:
    def test_has_unconditional_ok(self):
        cfg = CFG()
        cfg.add_block("A"); cfg.add_block("B")
        cfg.add_edge("A", "B")  # cond=None = unconditional
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        assert fsm.check_conditions_complete(prog) == []

    def test_all_conditional_flagged(self):
        cfg = CFG()
        cfg.add_block("A"); cfg.add_block("B"); cfg.add_block("C")
        cfg.add_edge("A", "B", cond="x")
        cfg.add_edge("A", "C", cond="y")  # no default!
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        incomplete = fsm.check_conditions_complete(prog)
        assert "A" in incomplete


class TestFSMLinearize:
    def test_all_states_present(self):
        layout = fsm.linearize(make_simple_program())
        ids = {slot.block.id for slot in layout.slots}
        assert ids == {"IDLE", "FETCH", "DONE"}

    def test_exits_sorted_by_priority(self):
        layout = fsm.linearize(make_simple_program())
        fetch_slot = next(s for s in layout.slots if s.block.id == "FETCH")
        priorities = [e.priority for e in fetch_slot.exits]
        assert priorities == sorted(priorities)

    def test_exit_cond_preserved(self):
        layout = fsm.linearize(make_simple_program())
        idle_slot = next(s for s in layout.slots if s.block.id == "IDLE")
        assert any(e.cond == "start" for e in idle_slot.exits)

    def test_unconditional_edge_cond_none(self):
        cfg = CFG()
        cfg.add_block("A"); cfg.add_block("B")
        cfg.add_edge("A", "B")  # unconditional
        cfg.set_entry("A")
        layout = fsm.linearize(Program({"main": cfg}))
        a_slot = layout.slots[0]
        assert a_slot.exits[0].cond is None
