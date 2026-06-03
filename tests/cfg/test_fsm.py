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


# ---------------------------------------------------------------------------
# FSM custom order
# ---------------------------------------------------------------------------

class TestFSMLinearizeCustomOrder:
    """Tests for fsm.linearize(strategy='custom', order=[...])."""

    def _make_prog(self):
        """IDLE -> FETCH -> PROCESS loop."""
        cfg = CFG()
        for bid in ("IDLE", "FETCH", "PROCESS"):
            cfg.add_block(bid)
        cfg.add_edge("IDLE", "FETCH", cond="start")
        cfg.add_edge("IDLE", "IDLE",  cond="wait")
        cfg.add_edge("FETCH", "PROCESS")
        cfg.add_edge("PROCESS", "IDLE")
        cfg.set_entry("IDLE")
        return Program({"main": cfg})

    def test_custom_full_order(self):
        """Full custom order is respected in FSMLayout."""
        layout = fsm.linearize(
            self._make_prog(),
            strategy="custom",
            order=["IDLE", "FETCH", "PROCESS"],
        )
        assert [s.block.id for s in layout.slots] == ["IDLE", "FETCH", "PROCESS"]

    def test_custom_different_order(self):
        """Non-default custom order changes slot ordering."""
        layout = fsm.linearize(
            self._make_prog(),
            strategy="custom",
            order=["IDLE", "PROCESS", "FETCH"],
        )
        assert [s.block.id for s in layout.slots] == ["IDLE", "PROCESS", "FETCH"]

    def test_custom_partial_order_appends_missing_in_rpo(self):
        """Missing blocks are appended in RPO after the preference list."""
        layout = fsm.linearize(
            self._make_prog(),
            strategy="custom",
            order=["IDLE"],
        )
        ids = [s.block.id for s in layout.slots]
        assert ids[0] == "IDLE"
        assert set(ids[1:]) == {"FETCH", "PROCESS"}

    def test_custom_unknown_block_raises(self):
        """Unknown block id in order raises ValueError."""
        with pytest.raises(ValueError, match="unknown block"):
            fsm.linearize(
                self._make_prog(),
                strategy="custom",
                order=["IDLE", "GHOST"],
            )

    def test_custom_unreachable_block_raises(self):
        """Unreachable block in order raises ValueError."""
        cfg = CFG()
        for bid in ("IDLE", "FETCH", "orphan"):
            cfg.add_block(bid)
        cfg.add_edge("IDLE", "FETCH")
        cfg.set_entry("IDLE")
        with pytest.raises(ValueError, match="unreachable block"):
            fsm.linearize(
                Program({"main": cfg}),
                strategy="custom",
                order=["IDLE", "orphan"],
            )

    def test_custom_without_order_raises(self):
        """strategy='custom' without order raises ValueError."""
        with pytest.raises(ValueError, match="'order' parameter"):
            fsm.linearize(self._make_prog(), strategy="custom")

    def test_custom_exits_preserved(self):
        """FSMSlot exits are correct under custom ordering."""
        layout = fsm.linearize(
            self._make_prog(),
            strategy="custom",
            order=["IDLE", "FETCH", "PROCESS"],
        )
        idle_slot = next(s for s in layout.slots if s.block.id == "IDLE")
        assert {e.target for e in idle_slot.exits} == {"FETCH", "IDLE"}

    def test_existing_rpo_unchanged(self):
        """RPO strategy behaviour is unaffected."""
        prog = self._make_prog()
        ids_default = [s.block.id for s in fsm.linearize(prog).slots]
        ids_rpo     = [s.block.id for s in fsm.linearize(prog, strategy="rpo").slots]
        assert ids_default == ids_rpo
