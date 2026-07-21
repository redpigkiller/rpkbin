"""Tests for rpkbin.cfg.CFG and merge_cfgs."""
import warnings
import pytest
import networkx as nx
from rpkbin.cfg import (
    CFG, NaturalLoop, Assignment, BasicBlock,
    merge_cfgs,
    CFGMergeError, DuplicateLabelError, InsnConflictError,
    EdgeConflictError, MetaConflictError,
)


def make_linear():
    cfg = CFG()
    cfg.add_block("entry")
    cfg.add_block("bb1")
    cfg.add_block("end")
    cfg.add_edge("entry", "bb1")
    cfg.add_edge("bb1", "end")
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


def make_diamond():
    cfg = CFG()
    for b in ("entry", "bb1", "bb2", "end"):
        cfg.add_block(b)
    cfg.add_edge("entry", "bb1", cond="true")
    cfg.add_edge("entry", "bb2", cond="false")
    cfg.add_edge("bb1", "end")
    cfg.add_edge("bb2", "end")
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


def make_loop():
    cfg = CFG()
    for b in ("entry", "header", "body", "end"):
        cfg.add_block(b)
    cfg.add_edge("entry", "header")
    cfg.add_edge("header", "body", cond="loop")
    cfg.add_edge("header", "end", cond="done")
    cfg.add_edge("body", "header")
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


class TestConstruction:
    def test_add_block_label(self):
        cfg = CFG()
        bb = cfg.add_block("a", label="ALPHA")
        assert bb.id == "a" and bb.label == "ALPHA"

    def test_duplicate_block_raises(self):
        cfg = CFG()
        cfg.add_block("a")
        with pytest.raises(ValueError):
            cfg.add_block("a")

    def test_add_block_deep_copies_mutable_contents(self):
        source = BasicBlock(
            "a", insns=[Assignment("x", ["y"])], meta={"nested": {"v": 1}}
        )
        cfg = CFG()
        added = cfg.add_block(source)
        source.insns[0].rhs.append("z")
        source.meta["nested"]["v"] = 2
        assert added.insns[0].rhs == ["y"]
        assert added.meta == {"nested": {"v": 1}}

    def test_edge_default_cond_none(self):
        cfg = CFG()
        cfg.add_block("a"); cfg.add_block("b")
        cfg.add_edge("a", "b")
        assert cfg.edge_attrs("a", "b")["cond"] is None

    def test_edge_cond_priority(self):
        cfg = CFG()
        cfg.add_block("a"); cfg.add_block("b")
        cfg.add_edge("a", "b", cond="done", priority=2)
        attrs = cfg.edge_attrs("a", "b")
        assert attrs["cond"] == "done" and attrs["priority"] == 2

    def test_len_and_contains(self):
        cfg = CFG()
        cfg.add_block("a"); cfg.add_block("b")
        assert len(cfg) == 2 and "a" in cfg and "z" not in cfg


class TestOutEdges:
    def test_sorted_by_priority(self):
        cfg = CFG()
        for b in ("a", "b", "c"):
            cfg.add_block(b)
        cfg.add_edge("a", "c", cond="done", priority=2)
        cfg.add_edge("a", "b", cond="go", priority=1)
        edges = cfg.out_edges("a")
        assert edges[0][1] == "b" and edges[1][1] == "c"


class TestGraphApi:
    def test_add_prebuilt_block_and_edge_queries(self):
        cfg = CFG()
        cfg.add_block(BasicBlock("a", label="A", meta={"src": "x"}))
        cfg.add_block("b")
        cfg.add_edge("a", "b", cond="go", priority=2)
        assert cfg.has_edge("a", "b")
        assert cfg.edges[0][0:2] == ("a", "b")
        assert cfg.in_edges("b")[0][2]["cond"] == "go"

    def test_remove_edge_and_block(self):
        cfg = make_linear()
        attrs = cfg.remove_edge("entry", "bb1")
        assert attrs["cond"] is None
        assert not cfg.has_edge("entry", "bb1")
        removed = cfg.remove_block("end")
        assert removed.id == "end"
        assert cfg.exit is None

    def test_copy_preserves_entry_exit_and_is_independent(self):
        cfg = make_linear()
        clone = cfg.copy()
        assert clone.entry.id == "entry"
        assert clone.exit.id == "end"
        clone.get_block("entry").meta["changed"] = True
        assert "changed" not in cfg.get_block("entry").meta

    def test_validate_reports_common_shape_issues(self):
        cfg = CFG()
        cfg.add_block("a"); cfg.add_block("b"); cfg.add_block("c")
        cfg.add_edge("a", "b")
        cfg.add_edge("a", "c")
        cfg.set_entry("a")
        issues = cfg.validate()
        assert any("multiple default" in issue for issue in issues)

    def test_validate_reports_single_conditional_edge(self):
        cfg = CFG()
        cfg.add_block("a"); cfg.add_block("b")
        cfg.add_edge("a", "b", cond="flag")
        cfg.set_entry("a")
        issues = cfg.validate()
        assert any("single conditional" in issue for issue in issues)


class TestTraversal:
    def test_dfs_visits_all(self):
        assert {bb.id for bb in make_diamond().dfs()} == {"entry", "bb1", "bb2", "end"}

    def test_rpo_entry_first(self):
        ids = [bb.id for bb in make_loop().reverse_postorder()]
        assert ids.index("entry") < ids.index("header") < ids.index("body")

    def test_dfs_no_entry_raises(self):
        cfg = CFG(); cfg.add_block("a")
        with pytest.raises(RuntimeError):
            list(cfg.dfs())


class TestReachability:
    def test_can_reach_true(self):
        assert make_linear().can_reach("entry", "end") is True

    def test_can_reach_false(self):
        assert make_diamond().can_reach("bb1", "bb2") is False

    def test_find_unreachable_orphan(self):
        cfg = make_linear()
        cfg.add_block("orphan")
        dead = cfg.find_unreachable()
        assert len(dead) == 1 and dead[0].id == "orphan"

    def test_find_sccs_detects_cycle(self):
        multi = [s for s in make_loop().find_sccs() if len(s) > 1]
        assert len(multi) == 1 and set(multi[0]) == {"header", "body"}


class TestLoopAnalysis:
    def test_find_back_edges_no_loop(self):
        assert make_linear().find_back_edges() == []

    def test_find_back_edges_loop(self):
        backs = make_loop().find_back_edges()
        assert len(backs) == 1 and backs[0] == ("body", "header")

    def test_find_natural_loops(self):
        loops = make_loop().find_natural_loops()
        assert len(loops) == 1
        lp = loops[0]
        assert isinstance(lp, NaturalLoop)
        assert lp.header == "header" and "body" in lp.body


class TestDominance:
    def test_dominators_linear(self):
        idom = make_linear().dominators()
        assert idom["bb1"] == "entry" and idom["end"] == "bb1"

    def test_post_dominators_explicit_exit(self):
        idom = make_linear().post_dominators(exit_node="end")
        assert idom["end"] == "end" and idom["bb1"] == "end"

    def test_post_dominators_unknown_raises(self):
        with pytest.raises(KeyError):
            make_linear().post_dominators(exit_node="ghost")

    def test_dominator_tree_is_dag(self):
        assert nx.is_directed_acyclic_graph(make_diamond().dominator_tree())


class TestLinearize:
    def test_rpo_order(self):
        order = make_linear().linearize("rpo")
        assert order.index("entry") < order.index("bb1") < order.index("end")

    def test_topological_raises_on_cycle(self):
        with pytest.raises(ValueError, match="cycle"):
            make_loop().linearize("topological")

    def test_topological_only_returns_reachable_from_start(self):
        cfg = make_linear()
        cfg.add_block("orphan")
        assert cfg.linearize("topological") == ["entry", "bb1", "end"]

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            make_linear().linearize("magic")

    def test_trace_order_keeps_branch_chains_together(self):
        cfg = CFG()
        for bid in ("S", "A1", "A2", "An", "B1", "B2", "Bn", "C1", "E"):
            cfg.add_block(bid)
        cfg.add_edge("S", "A1", cond="cond1", priority=0)
        cfg.add_edge("S", "B1", cond="cond2", priority=1)
        cfg.add_edge("S", "C1", priority=2)
        cfg.add_edge("A1", "A2")
        cfg.add_edge("A2", "An")
        cfg.add_edge("An", "E")
        cfg.add_edge("B1", "B2")
        cfg.add_edge("B2", "Bn")
        cfg.add_edge("Bn", "E")
        cfg.add_edge("C1", "E")
        cfg.set_entry("S")

        assert cfg.linearize("trace") == [
            "S", "A1", "A2", "An", "B1", "B2", "Bn", "C1", "E",
        ]

    def test_trace_order_schedules_reducible_loop_header_before_body(self):
        cfg = CFG()
        for bid in ("S", "B", "A"):
            cfg.add_block(bid)
        cfg.add_edge("S", "A")
        cfg.add_edge("A", "B")
        cfg.add_edge("B", "A")
        cfg.set_entry("S")

        assert cfg.linearize("trace") == ["S", "A", "B"]

    def test_trace_order_does_not_block_loop_header_on_back_edge(self):
        cfg = CFG()
        for bid in ("entry", "A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("entry", "A")
        cfg.add_edge("A", "B", cond="loop", priority=0)
        cfg.add_edge("A", "C", cond="done", priority=1)
        cfg.add_edge("B", "A")
        cfg.set_entry("entry")

        assert cfg.linearize("trace") == ["entry", "A", "B", "C"]

    def test_trace_order_force_pick_makes_progress_when_no_node_ready(self):
        cfg = CFG()
        for bid in ("S", "A", "B", "C", "D"):
            cfg.add_block(bid)
        cfg.add_edge("S", "A")
        cfg.add_edge("S", "B")
        cfg.add_edge("A", "C")
        cfg.add_edge("B", "C")
        cfg.add_edge("C", "D")
        cfg.add_edge("D", "B")
        cfg.set_entry("S")

        order = cfg.linearize("trace")
        assert order[0] == "S"
        assert set(order) == {"S", "A", "B", "C", "D"}
        assert len(order) == 5


# ---------------------------------------------------------------------------
# CFG.linearize custom order
# ---------------------------------------------------------------------------

class TestLinearizeCustomOrder:
    """Tests for strategy='custom' (preference order) in CFG.linearize()."""

    def _make_chain(self):
        """entry -> A -> B -> C (linear chain)."""
        cfg = CFG()
        for bid in ("entry", "A", "B", "C"):
            cfg.add_block(bid)
        cfg.add_edge("entry", "A")
        cfg.add_edge("A", "B")
        cfg.add_edge("B", "C")
        cfg.set_entry("entry")
        return cfg

    def test_custom_full_order(self):
        """Exact full order is returned as-is (all reachable blocks listed)."""
        cfg = self._make_chain()
        result = cfg.linearize("custom", order=["entry", "A", "B", "C"])
        assert result == ["entry", "A", "B", "C"]

    def test_custom_full_order_reversed(self):
        """Preference order is respected even when reversed."""
        cfg = self._make_chain()
        result = cfg.linearize("custom", order=["entry", "C", "B", "A"])
        assert result == ["entry", "C", "B", "A"]

    def test_custom_partial_order_appends_missing_in_rpo(self):
        """Blocks not in order are appended after in RPO order."""
        cfg = self._make_chain()
        # Only specify entry and C; A and B must be appended in RPO order.
        result = cfg.linearize("custom", order=["entry", "C"])
        assert result[0] == "entry"
        assert result[1] == "C"
        # A and B follow in RPO order (A before B)
        remaining = result[2:]
        assert set(remaining) == {"A", "B"}
        assert remaining.index("A") < remaining.index("B")

    def test_custom_only_reachable_blocks_in_output(self):
        """Orphan blocks (unreachable from entry) do not appear in result."""
        cfg = self._make_chain()
        cfg.add_block("orphan")  # not connected
        result = cfg.linearize("custom", order=["entry", "A", "B", "C"])
        assert "orphan" not in result
        assert len(result) == 4  # only the 4 reachable blocks

    def test_custom_partial_order_only_reachable_appended(self):
        """Orphan is not appended even when order is partial."""
        cfg = self._make_chain()
        cfg.add_block("orphan")
        result = cfg.linearize("custom", order=["entry"])
        assert "orphan" not in result
        assert len(result) == 4

    def test_custom_no_duplicate_blocks(self):
        """Each reachable block appears exactly once."""
        cfg = self._make_chain()
        # Duplicate in order list — should be deduplicated.
        result = cfg.linearize("custom", order=["entry", "A", "A", "B", "C"])
        assert result.count("A") == 1
        assert len(result) == 4

    def test_custom_unknown_block_raises(self):
        """Block id not in CFG raises ValueError."""
        cfg = self._make_chain()
        with pytest.raises(ValueError, match="unknown block"):
            cfg.linearize("custom", order=["entry", "GHOST", "A", "B", "C"])

    def test_custom_unreachable_block_raises(self):
        """Block in CFG but not reachable from entry raises ValueError."""
        cfg = self._make_chain()
        cfg.add_block("orphan")
        with pytest.raises(ValueError, match="unreachable block"):
            cfg.linearize("custom", order=["entry", "orphan", "A", "B", "C"])

    def test_custom_requires_order_parameter(self):
        """strategy='custom' without order raises ValueError."""
        cfg = self._make_chain()
        with pytest.raises(ValueError, match="'order' parameter"):
            cfg.linearize("custom")

    def test_custom_single_block_order(self):
        """Single-block order with remaining blocks appended by RPO."""
        cfg = self._make_chain()
        result = cfg.linearize("custom", order=["B"])
        assert result[0] == "B"
        # Remaining: entry, A, C in RPO order
        assert set(result[1:]) == {"entry", "A", "C"}

    # Regression: existing strategies must not be affected
    def test_existing_rpo_unchanged(self):
        """RPO strategy is unaffected by the new order parameter."""
        cfg = self._make_chain()
        result = cfg.linearize("rpo")
        assert result == ["entry", "A", "B", "C"]

    def test_existing_trace_unchanged(self):
        """Trace strategy is unaffected by the new order parameter."""
        cfg = self._make_chain()
        result = cfg.linearize("trace")
        assert result == ["entry", "A", "B", "C"]

    def test_existing_topological_unchanged(self):
        """Topological strategy is unaffected by the new order parameter."""
        cfg = self._make_chain()
        result = cfg.linearize("topological")
        assert result == ["entry", "A", "B", "C"]


class TestEdgeCases:
    def test_empty_cfg(self):
        cfg = CFG()
        assert len(cfg) == 0 and cfg.blocks == []

    def test_self_loop_is_back_edge(self):
        cfg = CFG()
        cfg.add_block("a"); cfg.add_block("end")
        cfg.add_edge("a", "a"); cfg.add_edge("a", "end")
        cfg.set_entry("a")
        assert ("a", "a") in cfg.find_back_edges()


# ---------------------------------------------------------------------------
# merge_cfgs
# ---------------------------------------------------------------------------

def _cfg(*block_specs, edges=()):
    """Helper: build a CFG from (id, label, insns, meta) tuples + edge specs.

    block_specs items: (id, label) | (id, label, insns) | (id, label, insns, meta)
    edges items:       (src, dst) | (src, dst, dict_of_attrs)
    """
    cfg = CFG()
    for spec in block_specs:
        bid, label = spec[0], spec[1]
        insns = spec[2] if len(spec) > 2 else []
        meta  = spec[3] if len(spec) > 3 else {}
        cfg.add_block(bid, label=label, insns=insns, meta=meta)
    for e in edges:
        src, dst = e[0], e[1]
        attrs = e[2] if len(e) > 2 else {}
        cfg.add_edge(src, dst, **attrs)
    return cfg


class TestMergeCFGs:
    # ------------------------------------------------------------------ #
    # Happy-path cases                                                     #
    # ------------------------------------------------------------------ #

    def test_empty_returns_empty_cfg(self):
        """merge_cfgs() with no arguments returns an empty CFG."""
        merged = merge_cfgs()
        assert len(merged) == 0

    def test_single_cfg_identity(self):
        """merge_cfgs(cfg) returns an equivalent (not identical) CFG."""
        insns_a = [Assignment("x", [])]
        flow = _cfg(
            ("a", "A", insns_a),
            ("b", "B"),
            edges=[("a", "b", {"cond": "ok", "priority": 0})],
        )
        merged = merge_cfgs(flow)
        assert len(merged) == 2
        bb_a = next(bb for bb in merged.blocks if bb.label == "A")
        assert bb_a.insns == insns_a
        assert merged.out_edges(bb_a.id)[0][2]["cond"] == "ok"

    def test_merged_contents_are_independent_from_source(self):
        flow = _cfg(
            ("a", "A", [Assignment("x", ["y"])] , {"nested": {"v": 1}}),
        )
        merged = merge_cfgs(flow)
        flow.get_block("a").insns[0].rhs.append("z")
        flow.get_block("a").meta["nested"]["v"] = 2

        block = merged.get_block("a")
        assert block.insns[0].rhs == ["y"]
        assert block.meta == {"nested": {"v": 1}}

    def test_user_example_i(self):
        """Flow1: A-(1)->B, A-(2)->C  +  Flow2: E-->A'  ==>  E-->A-(1)->B, E-->A-(2)->C."""
        insns = {lbl: [Assignment(lbl, [])] for lbl in "ABCE"}

        flow1 = _cfg(
            ("a", "A", insns["A"]),
            ("b", "B", insns["B"]),
            ("c", "C", insns["C"]),
            edges=[
                ("a", "b", {"cond": "1", "priority": 0}),
                ("a", "c", {"cond": "2", "priority": 1}),
            ],
        )
        # Flow2: E --> A' (placeholder)
        flow2 = _cfg(
            ("e",  "E",  insns["E"]),
            ("a2", "A"),           # placeholder
            edges=[("e", "a2")],
        )

        merged = merge_cfgs(flow1, flow2)

        # All 4 labeled blocks present
        labels = {bb.label for bb in merged.blocks}
        assert {"A", "B", "C", "E"} <= labels

        a_id = next(bb.id for bb in merged.blocks if bb.label == "A")
        e_id = next(bb.id for bb in merged.blocks if bb.label == "E")
        b_id = next(bb.id for bb in merged.blocks if bb.label == "B")
        c_id = next(bb.id for bb in merged.blocks if bb.label == "C")

        # A has its original instructions
        assert merged.get_block(a_id).insns == insns["A"]
        # A keeps both out-edges
        out_a = {dst for _, dst, _ in merged.out_edges(a_id)}
        assert out_a == {b_id, c_id}
        # E points to A
        out_e = {dst for _, dst, _ in merged.out_edges(e_id)}
        assert a_id in out_e

    def test_user_example_ii(self):
        """Flow1: E-->A'-->B'  +  Flow2: A-->B  ==>  E-->A-->B."""
        insns = {lbl: [Assignment(lbl, [])] for lbl in "ABE"}

        flow1 = _cfg(
            ("e",  "E",  insns["E"]),
            ("a1", "A"),           # placeholder
            ("b1", "B"),           # placeholder
            edges=[("e", "a1"), ("a1", "b1")],
        )
        flow2 = _cfg(
            ("a", "A", insns["A"]),
            ("b", "B", insns["B"]),
            edges=[("a", "b")],
        )

        merged = merge_cfgs(flow1, flow2)

        a_id = next(bb.id for bb in merged.blocks if bb.label == "A")
        b_id = next(bb.id for bb in merged.blocks if bb.label == "B")
        e_id = next(bb.id for bb in merged.blocks if bb.label == "E")

        # A and B have real insns
        assert merged.get_block(a_id).insns == insns["A"]
        assert merged.get_block(b_id).insns == insns["B"]
        # Path E -> A -> B
        assert a_id in {dst for _, dst, _ in merged.out_edges(e_id)}
        assert b_id in {dst for _, dst, _ in merged.out_edges(a_id)}

    def test_placeholder_chain_three_flows(self):
        """EC-13: placeholder chain across 3 flows is resolved in one pass."""
        insns = {lbl: [Assignment(lbl, [])] for lbl in "ABC"}

        flow1 = _cfg(
            ("e", "E", [Assignment("E", [])]),
            ("a1", "A"), ("b1", "B"),
            edges=[("e", "a1"), ("a1", "b1")],
        )
        flow2 = _cfg(
            ("f", "F", [Assignment("F", [])]),
            ("b2", "B"), ("c1", "C"),
            edges=[("f", "b2"), ("b2", "c1")],
        )
        flow3 = _cfg(
            ("a", "A", insns["A"]),
            ("b", "B", insns["B"]),
            ("c", "C", insns["C"]),
            edges=[("a", "b"), ("b", "c")],
        )

        merged = merge_cfgs(flow1, flow2, flow3)
        a_id = next(bb.id for bb in merged.blocks if bb.label == "A")
        b_id = next(bb.id for bb in merged.blocks if bb.label == "B")
        c_id = next(bb.id for bb in merged.blocks if bb.label == "C")

        # A -> B -> C chain present
        assert b_id in {dst for _, dst, _ in merged.out_edges(a_id)}
        assert c_id in {dst for _, dst, _ in merged.out_edges(b_id)}

    def test_exact_duplicate_edge_deduplicated(self):
        """EC-11: identical edges across flows are silently dropped to one."""
        flow1 = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b", {"cond": "ok", "priority": 0})])
        flow2 = _cfg(("a2", "A"), ("b2", "B"), edges=[("a2", "b2", {"cond": "ok", "priority": 0})])

        merged = merge_cfgs(flow1, flow2)
        a_id = next(bb.id for bb in merged.blocks if bb.label == "A")
        out = merged.out_edges(a_id)
        assert len(out) == 1  # one edge, not two

    def test_none_label_blocks_are_independent(self):
        """EC-3: label=None blocks are never merged, even with identical insns."""
        flow1 = _cfg(("anon", None, [Assignment("x", [])]))
        flow2 = _cfg(("anon", None, [Assignment("x", [])]))

        merged = merge_cfgs(flow1, flow2)
        assert len(merged) == 2  # two separate anon blocks

    def test_none_label_id_collision_renamed(self):
        """EC-8: label=None blocks from different flows keep unique ids via prefix."""
        flow1 = _cfg(("bb1", None))
        flow2 = _cfg(("bb1", None))

        merged = merge_cfgs(flow1, flow2)
        ids = {bb.id for bb in merged.blocks}
        assert "__merge_0_bb1" in ids
        assert "__merge_1_bb1" in ids

    def test_entry_exit_always_reset(self):
        """EC-4: merged CFG always has entry=None and exit=None."""
        flow1 = _cfg(("a", "A"))
        flow1.set_entry("a")
        flow2 = _cfg(("b", "B"))
        flow2.set_entry("b")
        flow2.set_exit("b")

        merged = merge_cfgs(flow1, flow2)
        assert merged.entry is None
        assert merged.exit is None

    def test_disjoint_cfgs_merge_ok(self):
        """EC-12: completely disjoint label sets produce a disconnected CFG (valid)."""
        flow1 = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b")])
        flow2 = _cfg(("c", "C"), ("d", "D"), edges=[("c", "d")])

        merged = merge_cfgs(flow1, flow2)
        assert len(merged) == 4
        labels = {bb.label for bb in merged.blocks}
        assert labels == {"A", "B", "C", "D"}

    def test_two_placeholders_same_label_merged(self):
        """Two placeholder blocks with the same label merge into one placeholder."""
        flow1 = _cfg(("x1", "X"))  # placeholder
        flow2 = _cfg(("x2", "X"))  # placeholder

        merged = merge_cfgs(flow1, flow2)
        x_blocks = [bb for bb in merged.blocks if bb.label == "X"]
        assert len(x_blocks) == 1
        assert x_blocks[0].insns == []

    def test_cycle_warning_on_new_loop(self):
        """EC-10: UserWarning is issued when merge creates a cycle that didn't exist."""
        # Flow1: A -> B (DAG, both have insns)
        flow1 = _cfg(
            ("a", "A", [Assignment("x", [])]),
            ("b", "B", [Assignment("y", [])]),
            edges=[("a", "b")],
        )
        # Flow2: B' -> A' (DAG alone, but creates a cycle with flow1)
        flow2 = _cfg(("b2", "B"), ("a2", "A"), edges=[("b2", "a2")])

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            merged = merge_cfgs(flow1, flow2)

        assert len(caught) == 1
        assert issubclass(caught[0].category, UserWarning)
        assert "cycle" in str(caught[0].message).lower()
        # Verify the cycle is real
        assert not nx.is_directed_acyclic_graph(merged._graph)

    def test_no_warning_when_merged_cycle_already_exists_in_inputs(self):
        """No warning when merge preserves, rather than introduces, cycles."""
        # Both flows have back-edges — neither is a DAG.
        flow1 = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b"), ("b", "a")])
        flow2 = _cfg(("a2", "A"), ("b2", "B"), edges=[("a2", "b2"), ("b2", "a2")])

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            merge_cfgs(flow1, flow2)

        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert len(user_warnings) == 0

    def test_existing_anonymous_cycle_is_not_reported_as_introduced(self):
        flow = _cfg(("a", None), ("b", None), edges=[("a", "b"), ("b", "a")])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            merge_cfgs(flow)
        assert not caught

    def test_canonical_winner_id_does_not_make_existing_cycle_new(self):
        placeholder = _cfg(("a_old", "A"), ("b_old", "B"), edges=[("a_old", "b_old"), ("b_old", "a_old")])
        instructed = _cfg(
            ("a_new", "A", [Assignment("a", [])]),
            ("b_new", "B", [Assignment("b", [])]),
            edges=[("a_new", "b_new"), ("b_new", "a_new")],
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            merge_cfgs(placeholder, instructed)
        assert not caught

    def test_cyclic_and_unrelated_acyclic_input_do_not_warn(self):
        cyclic = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b"), ("b", "a")])
        acyclic = _cfg(("c", "C"), ("d", "D"), edges=[("c", "d")])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            merge_cfgs(cyclic, acyclic)
        assert not caught

    def test_new_cross_flow_cycle_warns_even_when_inputs_are_cyclic(self):
        flow1 = _cfg(
            ("a", "A"), ("b", "B"), ("c", "C"),
            edges=[("a", "b"), ("b", "a"), ("b", "c")],
        )
        flow2 = _cfg(
            ("a2", "A"), ("b2", "B"), ("c2", "C"),
            edges=[("a2", "b2"), ("b2", "a2"), ("c2", "a2")],
        )
        with pytest.warns(UserWarning, match="introduced 1 new cycle") as caught:
            merge_cfgs(flow1, flow2)
        assert "('a', 'b', 'c')" in str(caught[0].message)

    def test_cycle_warning_is_rotation_normalized_and_deterministic(self):
        flow1 = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b")])
        flow2 = _cfg(("b2", "B"), ("a2", "A"), edges=[("b2", "a2")])
        messages = []
        for _ in range(2):
            with pytest.warns(UserWarning) as caught:
                merge_cfgs(flow1, flow2)
            messages.append(str(caught[0].message))
        assert messages[0] == messages[1]
        assert "('a', 'b')" in messages[0]

    # ------------------------------------------------------------------ #
    # Error cases                                                          #
    # ------------------------------------------------------------------ #

    def test_duplicate_label_in_input_raises(self):
        """EC-7: input CFG with duplicate labels raises DuplicateLabelError."""
        bad = _cfg(("a1", "A"), ("a2", "A"))  # two blocks, same label
        with pytest.raises(DuplicateLabelError) as exc_info:
            merge_cfgs(bad)
        err = exc_info.value
        assert err.cfg_index == 0
        assert err.label == "A"
        assert set(err.block_ids) == {"a1", "a2"}

    def test_insn_conflict_raises(self):
        """EC-1: two blocks with same label but different insns raise InsnConflictError."""
        flow1 = _cfg(("a",  "A", [Assignment("x", [])]))
        flow2 = _cfg(("a2", "A", [Assignment("y", [])]))

        with pytest.raises(InsnConflictError) as exc_info:
            merge_cfgs(flow1, flow2)
        err = exc_info.value
        assert err.label == "A"

    def test_same_insns_no_conflict(self):
        """EC-1 (safe case): identical insns on same label is fine."""
        insns = [Assignment("x", ["a"])]
        flow1 = _cfg(("a",  "A", insns))
        flow2 = _cfg(("a2", "A", insns))
        merged = merge_cfgs(flow1, flow2)  # must not raise
        assert len([bb for bb in merged.blocks if bb.label == "A"]) == 1

    def test_edge_conflict_raises(self):
        """EC-2: same canonical edge with different cond raises EdgeConflictError."""
        flow1 = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b", {"cond": "ok",   "priority": 0})])
        flow2 = _cfg(("a2", "A"), ("b2", "B"), edges=[("a2", "b2", {"cond": "done", "priority": 0})])

        with pytest.raises(EdgeConflictError) as exc_info:
            merge_cfgs(flow1, flow2)
        err = exc_info.value
        assert err.src is not None and err.dst is not None

    def test_edge_conflict_priority_raises(self):
        """EC-2: same canonical edge with different priority raises EdgeConflictError."""
        flow1 = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b", {"cond": "ok", "priority": 0})])
        flow2 = _cfg(("a2", "A"), ("b2", "B"), edges=[("a2", "b2", {"cond": "ok", "priority": 1})])

        with pytest.raises(EdgeConflictError):
            merge_cfgs(flow1, flow2)

    def test_meta_conflict_raises(self):
        """EC-5: both blocks non-empty meta that differ raises MetaConflictError."""
        flow1 = _cfg(("a",  "A", [], {"src": "flow1.asm"}))
        flow2 = _cfg(("a2", "A", [], {"src": "flow2.asm"}))

        with pytest.raises(MetaConflictError) as exc_info:
            merge_cfgs(flow1, flow2)
        err = exc_info.value
        assert err.label == "A"

    def test_meta_no_conflict_one_empty(self):
        """EC-5 (safe case): one block has meta, the other is empty — no conflict."""
        flow1 = _cfg(("a",  "A", [], {"src": "flow1.asm"}))
        flow2 = _cfg(("a2", "A"))  # meta={}
        merged = merge_cfgs(flow1, flow2)  # must not raise
        assert len([bb for bb in merged.blocks if bb.label == "A"]) == 1

    def test_placeholder_metadata_preserved_when_real_block_wins(self):
        flow1 = _cfg(("placeholder", "A", [], {"src": "flow1.asm"}))
        flow2 = _cfg(("real", "A", [Assignment("x", [])]))
        merged = merge_cfgs(flow1, flow2)
        bb = next(bb for bb in merged.blocks if bb.label == "A")
        assert bb.id == "real"
        assert bb.meta == {"src": "flow1.asm"}

    def test_merge_error_is_value_error(self):
        """CFGMergeError is a subclass of ValueError for easy catching."""
        flow1 = _cfg(("a",  "A", [Assignment("x", [])]))
        flow2 = _cfg(("a2", "A", [Assignment("y", [])]))
        with pytest.raises(ValueError):
            merge_cfgs(flow1, flow2)


# ---------------------------------------------------------------------------
# CFG display  (__repr__ / format)
# ---------------------------------------------------------------------------

class TestCFGDisplay:
    def test_repr_no_entry(self):
        """`__repr__` shows block / edge count; no entry when unset."""
        cfg = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b")])
        r = repr(cfg)
        assert "2 blocks" in r
        assert "1 edge" in r
        assert "entry" not in r

    def test_repr_with_entry(self):
        """`__repr__` includes the entry label when set."""
        cfg = _cfg(("a", "A", [Assignment("x", [])]), ("b", "B"))
        cfg.set_entry("a")
        assert "entry='A'" in repr(cfg)

    def test_repr_entry_uses_id_when_no_label(self):
        """`__repr__` falls back to block id when label is None."""
        cfg = CFG()
        cfg.add_block("start", label=None)
        cfg.set_entry("start")
        assert "entry='start'" in repr(cfg)

    def test_format_returns_str(self):
        """`format()` always returns a plain str."""
        assert isinstance(make_diamond().format(), str)

    def test_str_equals_format(self):
        """`str(cfg)` produces the same output as `cfg.format()`."""
        cfg = make_linear()
        assert str(cfg) == cfg.format()

    def test_format_contains_all_block_ids(self):
        """Every block id appears somewhere in the formatted output."""
        cfg = make_diamond()
        text = cfg.format()
        for bb in cfg.blocks:
            assert bb.id in text, f"Block id {bb.id!r} missing from format output"

    def test_format_contains_cond_labels(self):
        """Edge conditions appear in the formatted output."""
        text = make_diamond().format()   # has cond="true" and cond="false"
        assert "true" in text
        assert "false" in text

    def test_format_terminal_block(self):
        """Blocks with no out-edges are marked '(terminal)'."""
        assert "(terminal)" in make_linear().format()

    def test_format_entry_marker(self):
        """Entry block is marked with the ▶ marker."""
        assert "▶" in make_linear().format()

    def test_format_empty_insns_shows_empty(self):
        """Blocks with no instructions show '(empty)'."""
        assert "(empty)" in _cfg(("a", "A")).format()

    def test_format_insns_preview_truncated(self):
        """Long raw instruction text is truncated with '…'."""
        long_insn = Assignment("x", [], raw="a" * 100)
        text = _cfg(("a", "A", [long_insn])).format(max_insn_chars=20)
        assert "\u2026" in text

    def test_format_orphan_appended(self):
        """Unreachable blocks still appear after the main traversal."""
        cfg = make_linear()
        cfg.add_block("orphan", label="ORPHAN")
        assert "orphan" in cfg.format()

    def test_format_can_hide_unreachable_blocks(self):
        cfg = make_linear()
        cfg.add_block("orphan", label="ORPHAN")
        text = cfg.format(show_unreachable=False)
        assert "orphan" not in text

    def test_format_can_start_from_specific_block(self):
        cfg = make_linear()
        text = cfg.format(start="bb1", show_unreachable=False)
        assert "bb1" in text
        assert "end" in text
        assert not any(line.startswith("▶entry") or line.startswith(" entry") for line in text.splitlines())

    def test_format_can_show_meta(self):
        cfg = CFG()
        cfg.add_block("a", label="A", meta={"src": "sheet1"})
        cfg.set_entry("a")
        text = cfg.format(show_meta=True)
        assert "Meta" in text
        assert "sheet1" in text

    def test_format_unknown_start_raises(self):
        with pytest.raises(KeyError, match="ghost"):
            make_linear().format(start="ghost")

    def test_format_edge_priority_shown(self):
        """Non-zero priority appears as '(N)' in the edge column."""
        cfg = CFG()
        for b in ("a", "b", "c"):
            cfg.add_block(b, label=b.upper())
        cfg.add_edge("a", "b", cond="fast", priority=0)
        cfg.add_edge("a", "c", cond="slow", priority=2)
        cfg.set_entry("a")
        assert "(2)" in cfg.format()

    def test_format_rpo_order_with_entry(self):
        """When entry is set, blocks appear in RPO order (entry first)."""
        text = make_linear().format()
        lines = text.splitlines()
        entry_line = next(i for i, l in enumerate(lines) if "entry" in l and "Block" not in l)
        bb1_line   = next(i for i, l in enumerate(lines) if "bb1"   in l)
        end_line   = next(i for i, l in enumerate(lines) if "end"   in l and "Out" not in l and "entry" not in l)
        assert entry_line < bb1_line < end_line

    # ------------------------------------------------------------------ #
    # Improved error-message content                                       #
    # ------------------------------------------------------------------ #

    def test_edge_conflict_error_has_flow_indices(self):
        """EdgeConflictError records cfg_a_index and cfg_b_index."""
        flow1 = _cfg(("a", "A"), ("b", "B"), edges=[("a", "b", {"cond": "ok",   "priority": 0})])
        flow2 = _cfg(("a2","A"), ("b2","B"), edges=[("a2","b2",{"cond": "done", "priority": 0})])
        with pytest.raises(EdgeConflictError) as exc_info:
            merge_cfgs(flow1, flow2)
        err = exc_info.value
        assert err.cfg_a_index == 0
        assert err.cfg_b_index == 1
        assert "CFG[0]" in str(err)
        assert "CFG[1]" in str(err)

    def test_insn_conflict_error_shows_content(self):
        """InsnConflictError message contains actual instruction previews."""
        flow1 = _cfg(("a",  "A", [Assignment("x", [], raw="MOV r0, #1")]))
        flow2 = _cfg(("a2", "A", [Assignment("y", [], raw="MOV r0, #2")]))
        with pytest.raises(InsnConflictError) as exc_info:
            merge_cfgs(flow1, flow2)
        msg = str(exc_info.value)
        assert "MOV r0, #1" in msg
        assert "MOV r0, #2" in msg

    def test_duplicate_label_error_collects_all_ids(self):
        """DuplicateLabelError lists ALL block ids with the duplicate label."""
        bad = _cfg(("a1", "A"), ("a2", "A"), ("a3", "A"))
        with pytest.raises(DuplicateLabelError) as exc_info:
            merge_cfgs(bad)
        err = exc_info.value
        assert set(err.block_ids) == {"a1", "a2", "a3"}


# ---------------------------------------------------------------------------
# CFG.rename_block
# ---------------------------------------------------------------------------

class TestRenameBlock:
    def _make(self):
        """entry --[cond=go]--> mid --[cond=done]--> exit_b (with a self-loop on mid)."""
        cfg = CFG()
        cfg.add_block("entry", label="ENTRY")
        cfg.add_block("mid",   label="MID",   meta={"k": "v"})
        cfg.add_block("end",   label="END")
        cfg.add_edge("entry", "mid",   cond="go",   priority=0)
        cfg.add_edge("mid",   "end",   cond="done", priority=1)
        cfg.add_edge("mid",   "mid",   cond="loop", priority=0)   # self-loop
        cfg.set_entry("entry")
        cfg.set_exit("end")
        return cfg

    def test_rename_returns_block(self):
        cfg = self._make()
        bb = cfg.rename_block("mid", "middle")
        assert isinstance(bb, BasicBlock)

    def test_old_id_no_longer_in_cfg(self):
        cfg = self._make()
        cfg.rename_block("mid", "middle")
        assert "mid" not in cfg

    def test_new_id_now_in_cfg(self):
        cfg = self._make()
        cfg.rename_block("mid", "middle")
        assert "middle" in cfg

    def test_block_id_updated(self):
        cfg = self._make()
        bb = cfg.rename_block("mid", "middle")
        assert bb.id == "middle"
        assert cfg.get_block("middle").id == "middle"

    def test_incoming_edges_preserved(self):
        cfg = self._make()
        cfg.rename_block("mid", "middle")
        assert cfg.has_edge("entry", "middle")

    def test_outgoing_edges_preserved(self):
        cfg = self._make()
        cfg.rename_block("mid", "middle")
        assert cfg.has_edge("middle", "end")

    def test_edge_attrs_preserved(self):
        cfg = self._make()
        cfg.rename_block("mid", "middle")
        assert cfg.edge_attrs("entry", "middle")["cond"] == "go"
        assert cfg.edge_attrs("middle", "end")["cond"] == "done"
        assert cfg.edge_attrs("middle", "end")["priority"] == 1

    def test_self_loop_preserved(self):
        cfg = self._make()
        cfg.rename_block("mid", "middle")
        assert cfg.has_edge("middle", "middle")
        assert cfg.edge_attrs("middle", "middle")["cond"] == "loop"

    def test_rename_entry_updates_entry_marker(self):
        cfg = self._make()
        cfg.rename_block("entry", "start")
        assert cfg.entry is not None
        assert cfg.entry.id == "start"

    def test_rename_exit_updates_exit_marker(self):
        cfg = self._make()
        cfg.rename_block("end", "halt")
        assert cfg.exit is not None
        assert cfg.exit.id == "halt"

    def test_unknown_old_id_raises_key_error(self):
        cfg = self._make()
        with pytest.raises(KeyError, match="ghost"):
            cfg.rename_block("ghost", "new")

    def test_existing_new_id_raises_value_error(self):
        cfg = self._make()
        with pytest.raises(ValueError, match="end"):
            cfg.rename_block("mid", "end")

    @pytest.mark.parametrize("new_id", ["", 1, None])
    def test_invalid_new_id_is_atomic(self, new_id):
        cfg = self._make()
        before_nodes = list(cfg._graph.nodes)
        before_edges = cfg.edges
        before_entry = cfg.entry.id if cfg.entry is not None else None
        before_exit = cfg.exit.id if cfg.exit is not None else None
        before_block_id = cfg.get_block("mid").id

        with pytest.raises(ValueError, match="non-empty string"):
            cfg.rename_block("mid", new_id)  # type: ignore[arg-type]

        assert list(cfg._graph.nodes) == before_nodes
        assert cfg.edges == before_edges
        assert cfg.get_block("mid").id == before_block_id
        assert (cfg.entry.id if cfg.entry is not None else None) == before_entry
        assert (cfg.exit.id if cfg.exit is not None else None) == before_exit
        assert cfg.has_edge("mid", "mid")
        assert cfg.has_edge("entry", "mid")
        assert cfg.has_edge("mid", "end")


def test_validate_detects_graph_key_mismatch_and_unknown_instruction():
    cfg = CFG()
    cfg.add_block("a")
    cfg.set_entry("a")
    object.__setattr__(cfg.get_block("a"), "id", "different")
    cfg.get_block("a").insns.append(object())

    issues = cfg.validate()

    assert any("does not match block id" in issue for issue in issues)
    assert any("unsupported type" in issue for issue in issues)


def test_irreducible_cycle_is_not_reported_as_natural_loop():
    cfg = CFG()
    for block_id in ("entry", "a", "b"):
        cfg.add_block(block_id)
    cfg.add_edge("entry", "a")
    cfg.add_edge("entry", "b")
    cfg.add_edge("a", "b")
    cfg.add_edge("b", "a")
    cfg.set_entry("entry")

    assert cfg.find_natural_loops() == []


class TestCoreGraphContracts:
    def test_add_edge_rejects_duplicate_without_mutation(self):
        cfg = CFG()
        cfg.add_block("a")
        cfg.add_block("b")
        nested = {"items": [1]}
        cfg.add_edge("a", "b", cond="go", priority=3, meta=nested)
        nested["items"].append(2)
        before = cfg.edge_attrs("a", "b")

        with pytest.raises(ValueError, match="already exists"):
            cfg.add_edge("a", "b", cond="other", meta={"items": [9]})

        assert cfg.edge_attrs("a", "b") == before == {
            "cond": "go", "priority": 3, "meta": {"items": [1]}
        }

    def test_add_edge_rejects_duplicate_self_loop(self):
        cfg = CFG()
        cfg.add_block("a")
        cfg.add_edge("a", "a", role="loop")
        with pytest.raises(ValueError):
            cfg.add_edge("a", "a")
        assert cfg.edge_attrs("a", "a")["role"] == "loop"

    def test_update_and_output_edge_attributes_are_detached(self):
        cfg = CFG()
        cfg.add_block("a")
        cfg.add_block("b")
        cfg.add_edge("a", "b", cond="old", payload={"values": [1]})
        update = {"values": [2]}
        cfg.update_edge("a", "b", cond=None, payload=update)
        update["values"].append(3)
        cfg.update_edge("a", "b")

        assert cfg.edge_attrs("a", "b") == {
            "cond": None, "priority": 0, "payload": {"values": [2]}
        }
        for attrs in (
            cfg.edges[0][2], cfg.edge_attrs("a", "b"),
            cfg.out_edges("a")[0][2], cfg.in_edges("b")[0][2],
        ):
            attrs["payload"]["values"].append(99)
        assert cfg.edge_attrs("a", "b")["payload"] == {"values": [2]}
        removed = cfg.remove_edge("a", "b")
        removed["payload"]["values"].append(4)
        assert not cfg.has_edge("a", "b")
        with pytest.raises(KeyError):
            cfg.update_edge("a", "b", cond="missing")

    def test_entry_exit_ids_are_read_only(self):
        cfg = make_linear()
        assert (cfg.entry_id, cfg.exit_id) == ("entry", "end")
        with pytest.raises(AttributeError):
            cfg.entry_id = "bb1"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            cfg.exit_id = "bb1"  # type: ignore[misc]

    def test_to_networkx_is_deep_snapshot(self):
        cfg = CFG()
        cfg.add_block("a", insns=[Assignment("x", {"n": [1]})], meta={"tags": []})
        cfg.add_block("b")
        cfg.add_edge("a", "b", nested={"values": [1]})
        snapshot = cfg.to_networkx()
        snapshot.nodes["a"]["block"].meta["tags"].append("snapshot")
        snapshot.nodes["a"]["block"].insns[0].rhs["n"].append(2)
        snapshot["a"]["b"]["nested"]["values"].append(2)
        snapshot.remove_node("b")

        assert cfg.get_block("a").meta == {"tags": []}
        assert cfg.get_block("a").insns[0].rhs == {"n": [1]}
        assert cfg.edge_attrs("a", "b")["nested"] == {"values": [1]}
        assert "b" in cfg


class TestCoreReachabilityAndDominance:
    def test_remove_unreachable_no_dead_blocks_and_orphan(self):
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("end")
        cfg.add_block("dead")
        cfg.add_edge("entry", "end")
        cfg.set_entry("entry")
        assert [bb.id for bb in cfg.remove_unreachable()] == ["dead"]
        assert cfg.remove_unreachable() == []

    def test_terminal_blocks_respect_reachability_and_order(self):
        cfg = CFG()
        for block_id in ("entry", "left", "right", "dead"):
            cfg.add_block(block_id)
        cfg.add_edge("entry", "left")
        cfg.add_edge("entry", "right")
        cfg.set_entry("entry")

        assert [bb.id for bb in cfg.terminal_blocks()] == ["left", "right"]
        assert [bb.id for bb in cfg.terminal_blocks(reachable_only=False)] == [
            "left", "right", "dead"
        ]
        with pytest.raises(KeyError):
            cfg.terminal_blocks("ghost")

    def test_terminal_blocks_without_entry_and_ignore_start_when_all(self):
        cfg = CFG()
        cfg.add_block("a")
        with pytest.raises(RuntimeError):
            cfg.terminal_blocks()
        assert [bb.id for bb in cfg.terminal_blocks("ghost", reachable_only=False)] == ["a"]

    def test_terminal_blocks_explicit_start_filters_to_that_subgraph(self):
        cfg = CFG()
        for block_id in ("entry", "left", "right", "left_end", "right_end"):
            cfg.add_block(block_id)
        cfg.add_edge("entry", "left")
        cfg.add_edge("entry", "right")
        cfg.add_edge("left", "left_end")
        cfg.add_edge("right", "right_end")
        cfg.set_entry("entry")
        assert [bb.id for bb in cfg.terminal_blocks("left")] == ["left_end"]

    def test_remove_unreachable_preserves_order_and_clears_exit(self):
        cfg = CFG()
        for block_id in ("entry", "live", "dead1", "dead2"):
            cfg.add_block(block_id)
        cfg.add_edge("entry", "live")
        cfg.set_entry("entry")
        cfg.set_exit("dead2")

        assert [bb.id for bb in cfg.remove_unreachable()] == ["dead1", "dead2"]
        assert cfg.exit_id is None
        assert list(bb.id for bb in cfg.blocks) == ["entry", "live"]

    def test_remove_unreachable_validates_start_and_clears_removed_entry(self):
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("alternate")
        cfg.add_block("end")
        cfg.add_edge("alternate", "end")
        cfg.set_entry("entry")
        with pytest.raises(KeyError):
            cfg.remove_unreachable("ghost")
        assert [bb.id for bb in cfg.remove_unreachable("alternate")] == ["entry"]
        assert cfg.entry_id is None

        no_entry = CFG()
        no_entry.add_block("only")
        with pytest.raises(RuntimeError):
            no_entry.remove_unreachable()

    def test_dominates_validates_and_ignores_unreachable_nodes(self):
        cfg = make_diamond()
        cfg.add_block("dead")
        assert cfg.dominates("entry", "end")
        assert cfg.dominates("end", "end")
        assert not cfg.dominates("bb1", "end")
        with pytest.raises(KeyError):
            cfg.dominates("ghost", "end")
        with pytest.raises(ValueError):
            cfg.dominates("entry", "dead")
        with pytest.raises(ValueError):
            cfg.dominates("dead", "end")
        with pytest.raises(KeyError):
            cfg.dominates("entry", "end", start="ghost")
        assert set(cfg.dominator_tree()) == {"entry", "bb1", "bb2", "end"}

    def test_post_dominators_configured_or_explicit_exit(self):
        cfg = make_linear()
        assert cfg.post_dominators() == cfg.post_dominators("end")
        cfg_without_exit = CFG()
        cfg_without_exit.add_block("a")
        with pytest.raises(RuntimeError, match=r"set_exit\(\) or pass exit_node explicitly"):
            cfg_without_exit.post_dominators()
        with pytest.raises(KeyError):
            cfg.post_dominators("ghost")

    def test_post_dominators_excludes_blocks_without_path_to_selected_exit(self):
        cfg = CFG()
        for block_id in ("entry", "return", "loop"):
            cfg.add_block(block_id)
        cfg.add_edge("entry", "return")
        cfg.add_edge("entry", "loop")
        cfg.add_edge("loop", "loop")
        cfg.set_entry("entry")
        post = cfg.post_dominators("return")
        assert post["return"] == "return"
        assert "loop" not in post

    def test_to_networkx_snapshot_isolated_from_later_cfg_mutation(self):
        cfg = CFG()
        cfg.add_block("a", insns=[Assignment("x", {"n": [1]})], meta={"tags": []})
        cfg.add_block("b")
        cfg.add_edge("a", "b", nested={"values": [1]})
        snapshot = cfg.to_networkx()

        cfg.get_block("a").meta["tags"].append("cfg")
        cfg.get_block("a").insns[0].rhs["n"].append(2)
        cfg.update_edge("a", "b", nested={"values": [2]})
        cfg.remove_block("b")

        assert snapshot.nodes["a"]["block"].meta == {"tags": []}
        assert snapshot.nodes["a"]["block"].insns[0].rhs == {"n": [1]}
        assert snapshot["a"]["b"]["nested"] == {"values": [1]}
        assert "b" in snapshot
