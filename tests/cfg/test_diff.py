"""Tests for rpkbin.cfg.diff."""

import pytest

from rpkbin.cfg import (
    CFG, Program, CallRef, Assignment,
    diff_cfgs, diff_programs,
    cfg_structurally_equal, program_structurally_equal,
    CFGDiffResult, ProgramDiffResult,
    BlockDelta, EdgeDelta,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _cfg_ab(
    a_label="A", b_label="B",
    a_insns=None, b_insns=None,
    a_meta=None, b_meta=None,
    edge_attrs=None,
):
    """Two-block CFG: a --[edge_attrs]--> b."""
    cfg = CFG()
    cfg.add_block("a", label=a_label, insns=a_insns or [], meta=a_meta or {})
    cfg.add_block("b", label=b_label, insns=b_insns or [], meta=b_meta or {})
    cfg.add_edge("a", "b", **(edge_attrs or {"cond": None, "priority": 0}))
    cfg.set_entry("a")
    return cfg


def _simple_program(fn_names, entry="main"):
    """Program with single-block CFGs, no calls."""
    cfgs = {}
    for fn in fn_names:
        c = CFG()
        c.add_block("b")
        c.set_entry("b")
        cfgs[fn] = c
    return Program(cfgs, entry_fn=entry)


# ---------------------------------------------------------------------------
# CFG diff — block changes
# ---------------------------------------------------------------------------

class TestCFGDiffBlocks:
    def test_invalid_align_by_raises(self):
        old = _cfg_ab()
        with pytest.raises(ValueError, match="align_by"):
            diff_cfgs(old, old, align_by="oops")  # type: ignore[arg-type]

    def test_identical_cfgs_no_changes(self):
        cfg = _cfg_ab()
        result = diff_cfgs(cfg, cfg)
        assert not result.has_changes()

    def test_identical_cfgs_structurally_equal(self):
        cfg = _cfg_ab()
        assert cfg_structurally_equal(cfg, cfg)

    def test_added_block(self):
        old = _cfg_ab()
        new = _cfg_ab()
        new.add_block("c", label="C")
        result = diff_cfgs(old, new)
        assert "c" in result.added_blocks

    def test_removed_block(self):
        old = _cfg_ab()
        old.add_block("c", label="C")
        new = _cfg_ab()
        result = diff_cfgs(old, new)
        assert "c" in result.removed_blocks

    def test_changed_label(self):
        old = _cfg_ab(a_label="A")
        new = _cfg_ab(a_label="ALPHA")
        result = diff_cfgs(old, new)
        assert "a" in result.changed_blocks
        delta = result.changed_blocks["a"]
        assert delta.old_label == "A"
        assert delta.new_label == "ALPHA"

    def test_changed_insns_detected(self):
        old = _cfg_ab(a_insns=[Assignment("x", [])])
        new = _cfg_ab(a_insns=[Assignment("y", [])])
        result = diff_cfgs(old, new)
        assert "a" in result.changed_blocks
        assert result.changed_blocks["a"].insns_changed

    def test_compare_insns_false_ignores_insn_changes(self):
        old = _cfg_ab(a_insns=[Assignment("x", [])])
        new = _cfg_ab(a_insns=[Assignment("y", [])])
        result = diff_cfgs(old, new, compare_insns=False)
        # No label change, no meta change → should not appear
        assert "a" not in result.changed_blocks

    def test_meta_ignored_by_default(self):
        old = _cfg_ab(a_meta={"k": "v1"})
        new = _cfg_ab(a_meta={"k": "v2"})
        result = diff_cfgs(old, new)
        assert "a" not in result.changed_blocks

    def test_meta_detected_when_enabled(self):
        old = _cfg_ab(a_meta={"k": "v1"})
        new = _cfg_ab(a_meta={"k": "v2"})
        result = diff_cfgs(old, new, compare_meta=True)
        assert "a" in result.changed_blocks
        assert result.changed_blocks["a"].meta_changed


# ---------------------------------------------------------------------------
# CFG diff — edge changes
# ---------------------------------------------------------------------------

class TestCFGDiffEdges:
    def test_added_edge(self):
        old = _cfg_ab()
        new = _cfg_ab()
        new.add_block("c")
        new.add_edge("b", "c")
        result = diff_cfgs(old, new)
        assert ("b", "c") in result.added_edges

    def test_removed_edge(self):
        old = _cfg_ab()
        old.add_block("c")
        old.add_edge("b", "c")
        new = _cfg_ab()
        result = diff_cfgs(old, new)
        assert ("b", "c") in result.removed_edges

    def test_changed_edge_attrs(self):
        old = _cfg_ab(edge_attrs={"cond": "go", "priority": 0})
        new = _cfg_ab(edge_attrs={"cond": "stop", "priority": 0})
        result = diff_cfgs(old, new)
        assert ("a", "b") in result.changed_edges

    def test_compare_edge_attrs_false_ignores_attr_changes(self):
        old = _cfg_ab(edge_attrs={"cond": "go", "priority": 0})
        new = _cfg_ab(edge_attrs={"cond": "stop", "priority": 0})
        result = diff_cfgs(old, new, compare_edge_attrs=False)
        assert not result.changed_edges


# ---------------------------------------------------------------------------
# CFG diff — CallRef tracking
# ---------------------------------------------------------------------------

class TestCFGDiffCalls:
    def _cfg_with_call(self, callee: str, block_id="a"):
        cfg = CFG()
        cfg.add_block(block_id, insns=[CallRef(callee)])
        cfg.set_entry(block_id)
        return cfg

    def test_added_call_detected(self):
        old = self._cfg_with_call("foo")
        new = self._cfg_with_call("bar")
        result = diff_cfgs(old, new)
        assert ("a", "bar") in result.added_calls
        assert ("a", "foo") in result.removed_calls

    def test_removed_call_detected(self):
        old = self._cfg_with_call("foo")
        # new has no calls
        new = CFG()
        new.add_block("a")
        new.set_entry("a")
        result = diff_cfgs(old, new)
        assert ("a", "foo") in result.removed_calls
        assert not result.added_calls

    def test_calls_detected_even_when_compare_insns_false(self):
        """CallRef diff is independent of compare_insns."""
        old = self._cfg_with_call("foo")
        new = self._cfg_with_call("bar")
        result = diff_cfgs(old, new, compare_insns=False)
        assert ("a", "bar") in result.added_calls
        assert ("a", "foo") in result.removed_calls
        assert result.has_changes()


# ---------------------------------------------------------------------------
# CFG diff — align_by="label"
# ---------------------------------------------------------------------------

class TestCFGDiffAlignByLabel:
    def test_align_by_label_matches_same_label_different_ids(self):
        old = CFG()
        old.add_block("x1", label="X")
        old.add_block("y1", label="Y")
        old.add_edge("x1", "y1")
        old.set_entry("x1")

        new = CFG()
        new.add_block("x2", label="X")
        new.add_block("y2", label="Y")
        new.add_edge("x2", "y2")
        new.set_entry("x2")

        result = diff_cfgs(old, new, align_by="label")
        assert not result.has_changes()

    def test_align_by_label_raises_on_duplicate_labels(self):
        cfg = CFG()
        cfg.add_block("a1", label="SAME")
        cfg.add_block("a2", label="SAME")
        cfg.add_edge("a1", "a2")
        cfg.set_entry("a1")
        other = _cfg_ab()

        with pytest.raises(ValueError, match="duplicate label"):
            diff_cfgs(cfg, other, align_by="label")

    def test_align_by_label_raises_on_none_label(self):
        cfg = CFG()
        cfg.add_block("a", label=None)   # no label
        cfg.set_entry("a")
        other = CFG()
        other.add_block("b", label="B")
        other.set_entry("b")

        with pytest.raises(ValueError, match="label=None"):
            diff_cfgs(cfg, other, align_by="label")


# ---------------------------------------------------------------------------
# Program diff
# ---------------------------------------------------------------------------

class TestProgramDiff:
    def test_invalid_align_by_raises(self):
        p = _simple_program(["main"])
        with pytest.raises(ValueError, match="align_by"):
            diff_programs(p, p, align_by="oops")  # type: ignore[arg-type]

    def test_identical_programs_structurally_equal(self):
        p = _simple_program(["main", "helper"])
        assert program_structurally_equal(p, p)

    def test_entry_fn_changed(self):
        p1 = _simple_program(["main", "helper"], entry="main")
        p2 = _simple_program(["main", "helper"], entry="helper")
        result = diff_programs(p1, p2)
        assert result.entry_fn_changed
        assert result.old_entry_fn == "main"
        assert result.new_entry_fn == "helper"
        assert result.has_changes()

    def test_added_function(self):
        old = _simple_program(["main"])
        new = _simple_program(["main", "helper"])
        result = diff_programs(old, new)
        assert "helper" in result.added_functions
        assert result.has_changes()

    def test_removed_function(self):
        old = _simple_program(["main", "helper"])
        new = _simple_program(["main"])
        result = diff_programs(old, new)
        assert "helper" in result.removed_functions
        assert result.has_changes()

    def test_changed_function_appears_in_changed_functions(self):
        old = _simple_program(["main"])
        new = _simple_program(["main"])

        # mutate new's main CFG so it differs
        new.cfgs["main"].add_block("extra")

        result = diff_programs(old, new)
        assert "main" in result.changed_functions
        assert result.has_changes()

    def test_unchanged_function_not_in_changed_functions(self):
        p = _simple_program(["main", "helper"])
        result = diff_programs(p, p)
        assert not result.changed_functions
