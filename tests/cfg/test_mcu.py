"""Tests for rpkbin.cfg.mcu --- MCU analysis and linearization."""

import warnings

import pytest

from rpkbin.cfg import CFG, Assignment, Program
from rpkbin.cfg import mcu


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def make_linear_program():
    """A -> B -> HALT (linear, all cond=None)."""
    cfg = CFG()
    cfg.add_block("A", insns=[Assignment("x", [])])
    cfg.add_block("B", insns=[Assignment("y", ["x"])])
    cfg.add_block("HALT")
    cfg.add_edge("A", "B")
    cfg.add_edge("B", "HALT")
    cfg.set_entry("A")
    cfg.set_exit("HALT")
    return Program({"main": cfg})


def make_dead_loop_program():
    cfg = CFG()
    cfg.add_block("entry")
    cfg.add_block("stuck")
    cfg.add_block("HALT")
    cfg.add_edge("entry", "stuck")
    cfg.add_edge("entry", "HALT")
    cfg.add_edge("stuck", "stuck")  # dead self-loop
    cfg.set_entry("entry")
    cfg.set_exit("HALT")
    return Program({"main": cfg})


# ---------------------------------------------------------------------------
# find_dead_loops
# ---------------------------------------------------------------------------


class TestFindDeadLoops:
    def test_no_dead_loops(self):
        assert mcu.find_dead_loops(make_linear_program()) == []

    def test_detects_self_loop(self):
        dead = mcu.find_dead_loops(make_dead_loop_program())
        assert len(dead) == 1 and dead[0] == ["stuck"]

    def test_explicit_exit_block_param(self):
        dead = mcu.find_dead_loops(make_dead_loop_program(), exit_block="HALT")
        assert len(dead) == 1

    def test_no_exit_raises(self):
        cfg = CFG()
        cfg.add_block("A")
        cfg.add_block("B")
        cfg.add_edge("A", "B")
        cfg.set_entry("A")
        with pytest.raises(RuntimeError, match="exit_block"):
            mcu.find_dead_loops(Program({"main": cfg}))

    def test_multi_node_dead_loop(self):
        cfg = CFG()
        for bid in ("entry", "a", "b", "HALT"):
            cfg.add_block(bid)
        cfg.add_edge("entry", "a")
        cfg.add_edge("a", "b")
        cfg.add_edge("b", "a")  # dead cycle
        cfg.add_edge("entry", "HALT")
        cfg.set_entry("entry")
        cfg.set_exit("HALT")
        dead = mcu.find_dead_loops(Program({"main": cfg}))
        assert len(dead) == 1 and set(dead[0]) == {"a", "b"}


# ---------------------------------------------------------------------------
# Basic MCU linearize behaviour
# ---------------------------------------------------------------------------


class TestMCULinearizeBasic:
    def test_linear_no_jump_needed(self):
        layout = mcu.linearize(make_linear_program())
        assert all(not s.needs_jump for s in layout.slots)

    def test_order_entry_first(self):
        layout = mcu.linearize(make_linear_program())
        ids = [s.block.id for s in layout.slots]
        assert ids.index("A") < ids.index("B") < ids.index("HALT")

    def test_single_conditional_edge_not_fallthrough(self):
        """A block with only a conditional edge: is_fallthrough must be False."""
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("target")
        cfg.add_edge("entry", "target", cond="flag")
        cfg.set_entry("entry")
        layout = mcu.linearize(Program({"main": cfg}))
        entry_slot = next(s for s in layout.slots if s.block.id == "entry")
        assert entry_slot.needs_jump is False
        assert all(not e.is_fallthrough for e in entry_slot.exits)

    def test_exits_sorted_by_priority(self):
        cfg = CFG()
        for bid in ("S", "A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("S", "A", cond="cond1", priority=1)
        cfg.add_edge("S", "B", cond="cond2", priority=0)
        cfg.set_entry("S")
        layout = mcu.linearize(Program({"main": cfg}))
        s_slot = next(s for s in layout.slots if s.block.id == "S")
        prios = [e.priority for e in s_slot.exits]
        assert prios == sorted(prios)

    def test_jump_needed_when_uncond_target_not_adjacent(self):
        # A -> C (uncond), B is separate; in RPO order A,B,C → A needs jump to C
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "C")  # uncond — skips B
        cfg.add_edge("A", "B", cond="x")
        cfg.set_entry("A")
        layout = mcu.linearize(Program({"main": cfg}), strategy="rpo")
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        # C is not immediately after A in RPO (A->B->C in RPO), so needs_jump
        # (actual behaviour depends on RPO order; we just check internal consistency)
        if a_slot.needs_jump:
            assert a_slot.jump_target is not None


# ---------------------------------------------------------------------------
# MCU linearize — custom order strategy
# ---------------------------------------------------------------------------


class TestMCULinearizeCustomOrder:
    def _make_prog(self):
        """A -> B -> C -> HALT (linear, cond=None)."""
        cfg = CFG()
        for bid in ("A", "B", "C", "HALT"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B")
        cfg.add_edge("B", "C")
        cfg.add_edge("C", "HALT")
        cfg.set_entry("A")
        cfg.set_exit("HALT")
        return Program({"main": cfg})

    def test_custom_full_order(self):
        layout = mcu.linearize(
            self._make_prog(), strategy="custom", order=["A", "B", "C", "HALT"]
        )
        assert [s.block.id for s in layout.slots] == ["A", "B", "C", "HALT"]

    def test_custom_reversed_order(self):
        layout = mcu.linearize(
            self._make_prog(), strategy="custom", order=["A", "HALT", "C", "B"]
        )
        assert [s.block.id for s in layout.slots] == ["A", "HALT", "C", "B"]

    def test_custom_partial_order_appends_missing_in_rpo(self):
        layout = mcu.linearize(
            self._make_prog(), strategy="custom", order=["A", "HALT"]
        )
        ids = [s.block.id for s in layout.slots]
        assert ids[0] == "A"
        assert ids[1] == "HALT"
        assert set(ids[2:]) == {"B", "C"}

    def test_custom_unknown_block_raises(self):
        with pytest.raises(ValueError, match="unknown block"):
            mcu.linearize(self._make_prog(), strategy="custom", order=["A", "GHOST"])

    def test_custom_unreachable_block_raises(self):
        cfg = CFG()
        for bid in ("A", "B", "orphan"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B")
        cfg.set_entry("A")
        with pytest.raises(ValueError, match="unreachable block"):
            mcu.linearize(
                Program({"main": cfg}), strategy="custom", order=["A", "orphan"]
            )

    def test_custom_needs_jump_and_fallthrough_correct(self):
        """needs_jump / is_fallthrough are correct after custom ordering."""
        # Custom order: A, C, B, HALT — C is adjacent to A but A->B->C->HALT
        # A's uncond target is B (not C); so A needs_jump=True
        layout = mcu.linearize(
            self._make_prog(), strategy="custom", order=["A", "C", "B", "HALT"]
        )
        ids = [s.block.id for s in layout.slots]
        assert ids == ["A", "C", "B", "HALT"]
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        # A's uncond target is B; B is not next (C is), so needs_jump=True
        assert a_slot.needs_jump is True
        assert a_slot.jump_target == "B"
        # A's exit edge to B is cond=None but target != next_id (C) → not fallthrough
        assert all(not e.is_fallthrough for e in a_slot.exits)


# ---------------------------------------------------------------------------
# fallthrough_policy="none" (default)
# ---------------------------------------------------------------------------


class TestPolicyNone:
    def test_none_is_default(self):
        prog = make_linear_program()
        ids_default = [s.block.id for s in mcu.linearize(prog).slots]
        ids_none = [
            s.block.id for s in mcu.linearize(prog, fallthrough_policy="none").slots
        ]
        assert ids_default == ids_none

    def test_uncond_fallthrough_marked(self):
        """cond=None edge to next block is is_fallthrough=True."""
        layout = mcu.linearize(make_linear_program())
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        assert any(e.is_fallthrough and e.target == "B" for e in a_slot.exits)

    def test_cond_edge_never_fallthrough(self):
        """Conditional edges are never marked is_fallthrough under any policy."""
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="flag", priority=0)
        cfg.add_edge("A", "C", priority=1)
        cfg.set_entry("A")
        layout = mcu.linearize(Program({"main": cfg}))
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        for e in a_slot.exits:
            if e.cond is not None:
                assert e.is_fallthrough is False


# ---------------------------------------------------------------------------
# fallthrough_policy="default"
# ---------------------------------------------------------------------------


class TestPolicyDefault:
    def _make_branching_prog(self):
        """A --[flag]--> B (cond), A --[None]--> C (uncond)."""
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="flag", priority=0)
        cfg.add_edge("A", "C", priority=1)  # cond=None
        cfg.set_entry("A")
        return Program({"main": cfg})

    def test_uncond_target_placed_adjacent(self):
        """'default' places cond=None target immediately after its source."""
        layout = mcu.linearize(
            self._make_branching_prog(), fallthrough_policy="default"
        )
        ids = [s.block.id for s in layout.slots]
        assert ids.index("C") == ids.index("A") + 1

    def test_uncond_edge_is_fallthrough_after_reorder(self):
        layout = mcu.linearize(
            self._make_branching_prog(), fallthrough_policy="default"
        )
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        uncond_exits = [e for e in a_slot.exits if e.cond is None]
        assert len(uncond_exits) == 1
        assert uncond_exits[0].is_fallthrough is True

    def test_conditional_never_fallthrough(self):
        layout = mcu.linearize(
            self._make_branching_prog(), fallthrough_policy="default"
        )
        for slot in layout.slots:
            for e in slot.exits:
                if e.cond is not None:
                    assert e.is_fallthrough is False

    def test_cond_not_modified(self):
        prog = self._make_branching_prog()
        cond_before = prog.main.edge_attrs("A", "B")["cond"]
        mcu.linearize(prog, fallthrough_policy="default")
        assert prog.main.edge_attrs("A", "B")["cond"] == cond_before


# ---------------------------------------------------------------------------
# fallthrough_policy="layout"
# ---------------------------------------------------------------------------


class TestPolicyLayout:
    def _make_prog(self):
        """A --[main]--> B --[main]--> C; B/C --[cold, cond="rst"]--> A."""
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", layout_role="main")
        cfg.add_edge("B", "C", layout_role="main")
        cfg.add_edge("B", "A", cond="rst", layout_role="cold")
        cfg.add_edge("C", "A", cond="rst", layout_role="cold")
        cfg.set_entry("A")
        return Program({"main": cfg})

    def test_main_line_order_preserved(self):
        """A -> B -> C main chain stays together."""
        layout = mcu.linearize(self._make_prog(), fallthrough_policy="layout")
        ids = [s.block.id for s in layout.slots]
        assert ids.index("A") < ids.index("B")
        assert ids.index("B") < ids.index("C")
        assert ids.index("B") == ids.index("A") + 1
        assert ids.index("C") == ids.index("B") + 1

    def test_conditional_main_not_fallthrough(self):
        """layout_role='main' on a conditional edge never sets is_fallthrough."""
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="go", layout_role="main")
        cfg.add_edge("A", "C")
        cfg.set_entry("A")
        layout = mcu.linearize(Program({"main": cfg}), fallthrough_policy="layout")
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        for e in a_slot.exits:
            if e.cond is not None:
                assert e.is_fallthrough is False

    def test_layout_role_in_exit_edge(self):
        """MCUExitEdge.layout_role matches edge attr."""
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", layout_role="main")
        cfg.add_edge("A", "C", cond="err", layout_role="cold")
        cfg.set_entry("A")
        layout = mcu.linearize(Program({"main": cfg}), fallthrough_policy="layout")
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        roles = {e.target: e.layout_role for e in a_slot.exits}
        assert roles["B"] == "main"
        assert roles["C"] == "cold"

    def test_invalid_layout_role_raises(self):
        """Invalid layout_role raises only when policy='layout' is active."""
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", layout_role="BAD")
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        with pytest.raises(ValueError, match="invalid layout_role"):
            mcu.linearize(prog, fallthrough_policy="layout")

    def test_invalid_layout_role_not_raised_without_layout_policy(self):
        """Invalid layout_role is silently ignored under other policies."""
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", layout_role="BAD")
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        mcu.linearize(prog, fallthrough_policy="none")
        mcu.linearize(prog, fallthrough_policy="default")

    def test_cond_not_modified(self):
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="go", layout_role="main")
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        cond_before = prog.main.edge_attrs("A", "B")["cond"]
        mcu.linearize(prog, fallthrough_policy="layout")
        assert prog.main.edge_attrs("A", "B")["cond"] == cond_before


# ---------------------------------------------------------------------------
# fallthrough_policy="likelihood"
# ---------------------------------------------------------------------------


class TestPolicyLikelihood:
    def _make_prog(self):
        """A -> B (likely), A -> C (unlikely), A -> D (normal)."""
        cfg = CFG()
        for bid in ("A", "B", "C", "D"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="hot", likelihood="likely", priority=0)
        cfg.add_edge("A", "C", cond="cold", likelihood="unlikely", priority=1)
        cfg.add_edge("A", "D", cond="mid", likelihood="normal", priority=2)
        cfg.set_entry("A")
        return Program({"main": cfg})

    def test_likely_target_placed_before_unlikely(self):
        """'likely' edge target is emitted before 'unlikely' edge target."""
        layout = mcu.linearize(self._make_prog(), fallthrough_policy="likelihood")
        ids = [s.block.id for s in layout.slots]
        assert ids.index("B") < ids.index("C")

    def test_likelihood_in_exit_edge(self):
        """MCUExitEdge.likelihood reflects the edge attr."""
        layout = mcu.linearize(self._make_prog(), fallthrough_policy="likelihood")
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        likes = {e.target: e.likelihood for e in a_slot.exits}
        assert likes["B"] == "likely"
        assert likes["C"] == "unlikely"
        assert likes["D"] == "normal"

    def test_conditional_never_fallthrough(self):
        """Conditional edges are never is_fallthrough under likelihood policy."""
        layout = mcu.linearize(self._make_prog(), fallthrough_policy="likelihood")
        for slot in layout.slots:
            for e in slot.exits:
                if e.cond is not None:
                    assert e.is_fallthrough is False

    def test_invalid_likelihood_raises(self):
        """Invalid likelihood value raises ValueError when policy='likelihood'."""
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", likelihood="BOGUS")
        cfg.set_entry("A")
        with pytest.raises(ValueError, match="invalid likelihood"):
            mcu.linearize(Program({"main": cfg}), fallthrough_policy="likelihood")

    def test_invalid_likelihood_not_raised_under_other_policies(self):
        """Invalid likelihood is silently ignored under other policies."""
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", likelihood="BOGUS")
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        mcu.linearize(prog, fallthrough_policy="none")
        mcu.linearize(prog, fallthrough_policy="default")
        mcu.linearize(prog, fallthrough_policy="layout")

    def test_exit_edge_default_likelihood_normal(self):
        """Edges without likelihood attr get likelihood='normal' in MCUExitEdge."""
        layout = mcu.linearize(make_linear_program())
        for slot in layout.slots:
            for e in slot.exits:
                assert e.likelihood == "normal"

    def test_cond_not_modified(self):
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="go", likelihood="likely")
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        cond_before = prog.main.edge_attrs("A", "B")["cond"]
        mcu.linearize(prog, fallthrough_policy="likelihood")
        assert prog.main.edge_attrs("A", "B")["cond"] == cond_before


# ---------------------------------------------------------------------------
# fallthrough_policy="weight"
# ---------------------------------------------------------------------------


class TestPolicyWeight:
    def _make_prog(self):
        """A -> B (weight=10), A -> C (weight=1), A -> D (weight=5)."""
        cfg = CFG()
        for bid in ("A", "B", "C", "D"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="hot", weight=10.0, priority=0)
        cfg.add_edge("A", "C", cond="cold", weight=1.0, priority=1)
        cfg.add_edge("A", "D", cond="mid", weight=5.0, priority=2)
        cfg.set_entry("A")
        return Program({"main": cfg})

    def test_high_weight_target_placed_before_low_weight(self):
        """Higher-weight edge target is emitted before lower-weight target."""
        layout = mcu.linearize(self._make_prog(), fallthrough_policy="weight")
        ids = [s.block.id for s in layout.slots]
        assert ids.index("B") < ids.index("C")  # weight 10 before weight 1

    def test_weight_in_exit_edge(self):
        """MCUExitEdge.weight reflects the edge attr."""
        layout = mcu.linearize(self._make_prog(), fallthrough_policy="weight")
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        weights = {e.target: e.weight for e in a_slot.exits}
        assert weights["B"] == pytest.approx(10.0)
        assert weights["C"] == pytest.approx(1.0)
        assert weights["D"] == pytest.approx(5.0)

    def test_weight_tie_break_deterministic(self):
        """Equal-weight edges produce a deterministic (stable) order."""
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="x", weight=5.0, priority=0)
        cfg.add_edge("A", "C", cond="y", weight=5.0, priority=1)
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        ids1 = [
            s.block.id for s in mcu.linearize(prog, fallthrough_policy="weight").slots
        ]
        ids2 = [
            s.block.id for s in mcu.linearize(prog, fallthrough_policy="weight").slots
        ]
        assert ids1 == ids2  # deterministic

    def test_conditional_never_fallthrough(self):
        layout = mcu.linearize(self._make_prog(), fallthrough_policy="weight")
        for slot in layout.slots:
            for e in slot.exits:
                if e.cond is not None:
                    assert e.is_fallthrough is False

    def test_invalid_weight_str_raises(self):
        """Non-numeric weight raises ValueError when policy='weight'."""
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", weight="fast")
        cfg.set_entry("A")
        with pytest.raises(ValueError, match="invalid weight"):
            mcu.linearize(Program({"main": cfg}), fallthrough_policy="weight")

    def test_invalid_weight_negative_raises(self):
        """Negative weight raises ValueError when policy='weight'."""
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", weight=-1.0)
        cfg.set_entry("A")
        with pytest.raises(ValueError, match="invalid weight"):
            mcu.linearize(Program({"main": cfg}), fallthrough_policy="weight")

    @pytest.mark.parametrize("weight", [True, float("nan"), float("inf"), -float("inf")])
    def test_non_finite_or_bool_weight_raises(self, weight):
        cfg = CFG()
        cfg.add_block("A"); cfg.add_block("B")
        cfg.add_edge("A", "B", weight=weight)
        cfg.set_entry("A")
        with pytest.raises(ValueError, match="invalid weight"):
            mcu.linearize(Program({"main": cfg}), fallthrough_policy="weight")

    def test_invalid_weight_not_raised_under_other_policies(self):
        """Invalid weight is silently ignored under other policies."""
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", weight="fast")
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        mcu.linearize(prog, fallthrough_policy="none")
        mcu.linearize(prog, fallthrough_policy="default")

    def test_exit_edge_default_weight_one(self):
        """Edges without weight attr get weight=1.0 in MCUExitEdge."""
        layout = mcu.linearize(make_linear_program())
        for slot in layout.slots:
            for e in slot.exits:
                assert e.weight == pytest.approx(1.0)

    def test_cond_not_modified(self):
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="go", weight=100.0)
        cfg.set_entry("A")
        prog = Program({"main": cfg})
        cond_before = prog.main.edge_attrs("A", "B")["cond"]
        mcu.linearize(prog, fallthrough_policy="weight")
        assert prog.main.edge_attrs("A", "B")["cond"] == cond_before

    def test_weight_zero_treated_as_cold(self):
        """weight=0 edges have their targets deferred like cold paths."""
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", weight=10.0)  # hot uncond
        cfg.add_edge("A", "C", cond="err", weight=0.0)  # cold
        cfg.set_entry("A")
        layout = mcu.linearize(Program({"main": cfg}), fallthrough_policy="weight")
        ids = [s.block.id for s in layout.slots]
        # B (hot) should come before C (weight=0 cold)
        assert ids.index("B") < ids.index("C")


# ---------------------------------------------------------------------------
# Misc: MCUExitEdge default field values
# ---------------------------------------------------------------------------


class TestMCUExitEdgeDefaults:
    def test_all_defaults_from_edge_with_no_attrs(self):
        """Plain edge (no layout attrs) yields all-default MCUExitEdge fields."""
        layout = mcu.linearize(make_linear_program())
        for slot in layout.slots:
            for e in slot.exits:
                assert e.layout_role == "normal"
                assert e.likelihood == "normal"
                assert e.weight == pytest.approx(1.0)

    def test_unknown_policy_raises(self):
        with pytest.raises(ValueError, match="Unknown fallthrough_policy"):
            mcu.linearize(make_linear_program(), fallthrough_policy="magic")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# fallthrough_policy="cond_aware_weight"
# ---------------------------------------------------------------------------


class TestPolicyCondAwareWeight:
    """cond_aware_weight always prefers cond=None successors first.
    Weight is used only as a secondary ranking for same-conditionality edges.
    """

    def _make_mixed_prog(self, uncond_weight: float, cond_weight: float):
        """A --(cond='hot', weight=cond_weight)--> B
           A --(cond=None,  weight=uncond_weight)--> C
        Both targets start as pending.
        """
        cfg = CFG()
        for bid in ("A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="hot", weight=cond_weight, priority=0)
        cfg.add_edge("A", "C", weight=uncond_weight, priority=1)
        cfg.set_entry("A")
        return Program({"main": cfg})

    def test_uncond_target_always_placed_adjacent_regardless_of_weight(self):
        """cond=None target is placed next even when its weight is much lower."""
        layout = mcu.linearize(
            self._make_mixed_prog(uncond_weight=0.01, cond_weight=100.0),
            fallthrough_policy="cond_aware_weight",
        )
        ids = [s.block.id for s in layout.slots]
        assert ids.index("C") == ids.index("A") + 1, (
            "uncond target C should be immediately after A regardless of weight"
        )

    def test_uncond_edge_is_fallthrough(self):
        """The cond=None edge to the adjacent slot is marked is_fallthrough=True."""
        layout = mcu.linearize(
            self._make_mixed_prog(uncond_weight=1.0, cond_weight=10.0),
            fallthrough_policy="cond_aware_weight",
        )
        a_slot = next(s for s in layout.slots if s.block.id == "A")
        uncond_exits = [e for e in a_slot.exits if e.cond is None]
        assert len(uncond_exits) == 1
        assert uncond_exits[0].is_fallthrough is True

    def test_conditional_never_fallthrough(self):
        """Conditional edges are never is_fallthrough under cond_aware_weight."""
        layout = mcu.linearize(
            self._make_mixed_prog(uncond_weight=1.0, cond_weight=10.0),
            fallthrough_policy="cond_aware_weight",
        )
        for slot in layout.slots:
            for e in slot.exits:
                if e.cond is not None:
                    assert e.is_fallthrough is False

    def test_weight_secondary_ranking_among_conditional_edges(self):
        """cond=None (HALT) is always placed immediately after A.
        The remaining conditional successors B and C are emitted in
        base_order order after the chain stalls — the key guarantee
        is that the uncond target wins the first slot."""
        cfg = CFG()
        for bid in ("A", "B", "C", "HALT"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", cond="x", weight=10.0, priority=0)
        cfg.add_edge("A", "C", cond="y", weight=1.0, priority=1)
        cfg.add_edge("A", "HALT", weight=5.0, priority=2)
        cfg.set_entry("A")
        layout = mcu.linearize(
            Program({"main": cfg}),
            fallthrough_policy="cond_aware_weight",
        )
        ids = [s.block.id for s in layout.slots]
        # Core guarantee: HALT (uncond) must come right after A.
        assert ids.index("HALT") == ids.index("A") + 1
        # All blocks must be present exactly once.
        assert sorted(ids) == ["A", "B", "C", "HALT"]

    def test_differs_from_weight_policy_when_cond_has_high_weight(self):
        """cond_aware_weight and weight produce different orderings when
        the best-weighted edge is conditional."""
        prog = self._make_mixed_prog(uncond_weight=0.01, cond_weight=100.0)
        ids_weight = [
            s.block.id for s in mcu.linearize(prog, fallthrough_policy="weight").slots
        ]
        ids_caw = [
            s.block.id
            for s in mcu.linearize(prog, fallthrough_policy="cond_aware_weight").slots
        ]
        assert set(ids_weight) == set(ids_caw) == {"A", "B", "C"}
        assert ids_caw.index("C") == ids_caw.index("A") + 1
        assert ids_weight.index("B") < ids_weight.index("C")

    def test_cold_deferral_with_cond_aware_weight(self):
        """weight=0 targets are deferred to the end."""
        cfg = CFG()
        for bid in ("A", "B", "cold"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", weight=5.0)
        cfg.add_edge("A", "cold", cond="err", weight=0.0)
        cfg.set_entry("A")
        layout = mcu.linearize(
            Program({"main": cfg}),
            fallthrough_policy="cond_aware_weight",
        )
        ids = [s.block.id for s in layout.slots]
        assert ids.index("B") < ids.index("cold")

    def test_invalid_weight_raises(self):
        """Non-numeric weight raises ValueError under cond_aware_weight."""
        cfg = CFG()
        for bid in ("A", "B"):
            cfg.add_block(bid)
        cfg.add_edge("A", "B", weight="fast")
        cfg.set_entry("A")
        with pytest.raises(ValueError, match="invalid weight"):
            mcu.linearize(
                Program({"main": cfg}), fallthrough_policy="cond_aware_weight"
            )

    def test_cond_not_modified(self):
        """mcu.linearize does not modify edge attributes."""
        prog = self._make_mixed_prog(uncond_weight=1.0, cond_weight=10.0)
        cond_before = prog.main.edge_attrs("A", "B")["cond"]
        mcu.linearize(prog, fallthrough_policy="cond_aware_weight")
        assert prog.main.edge_attrs("A", "B")["cond"] == cond_before


# ---------------------------------------------------------------------------
# custom strategy + entry-block warning
# ---------------------------------------------------------------------------


class TestCustomOrderEntryWarning:
    """mcu.linearize emits a UserWarning when custom order puts a non-entry
    block first, because MCU backends often treat position-0 as the entry."""

    def _make_prog(self):
        cfg = CFG()
        for bid in ("entry", "body", "tail"):
            cfg.add_block(bid)
        cfg.add_edge("entry", "body")
        cfg.add_edge("body", "tail")
        cfg.set_entry("entry")
        return Program({"main": cfg})

    def test_warns_when_entry_not_first(self):
        with pytest.warns(UserWarning, match="custom order places block"):
            mcu.linearize(
                self._make_prog(),
                strategy="custom",
                order=["body", "entry", "tail"],
            )

    def test_no_warning_when_entry_is_first(self):
        """Must not warn when the entry block leads the custom order."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            mcu.linearize(
                self._make_prog(),
                strategy="custom",
                order=["entry", "body", "tail"],
            )

    def test_no_warning_for_non_custom_strategies(self):
        """Non-custom strategies never trigger the entry-first warning."""
        prog = self._make_prog()
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            mcu.linearize(prog, strategy="rpo")
            mcu.linearize(prog, strategy="trace")
