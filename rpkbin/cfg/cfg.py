"""CFG — Control Flow Graph with structural analysis algorithms.

Backend: networkx DiGraph.  All graph operations delegate to networkx;
this class adds CFG semantics (entry/exit designation, basic block storage,
structural analysis).

Edge attributes
---------------
Every edge carries two standard attributes:

* ``cond``     — transition condition string, or ``None`` for an unconditional
                 edge (the block's only successor, or a default/else branch).
* ``priority`` — integer; lower values are evaluated first when multiple
                 conditional edges leave the same block.  Defaults to ``0``.

Additional keyword attributes may be stored via ``**attrs`` in
:meth:`add_edge`.

Entry vs Exit
-------------
* :meth:`set_entry` is **required** for most analysis helpers.
* :meth:`set_exit`  is **optional**:

  - Set for subroutine CFGs (marks the return block).
  - Set for MCU *main* CFGs (marks the halt block, used by MCU dead-loop
    detection).
  - Left unset for FSM *main* CFGs which loop forever.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Generator, Literal


import networkx as nx

from .block import BasicBlock, Insn


# ---------------------------------------------------------------------------
# Helper dataclass
# ---------------------------------------------------------------------------

@dataclass(repr=False)
class NaturalLoop:
    """Represents a natural loop identified by a DFS back-edge.

    Attributes:
        header:    Block id of the loop header.
        body:      All block ids in the loop (includes *header*).
        back_edge: The ``(tail, header)`` back-edge that identifies the loop.
    """

    header: str
    body: set[str]
    back_edge: tuple[str, str]

    def __repr__(self) -> str:
        return f"NaturalLoop(header={self.header!r}, body={sorted(self.body)!r})"


# ---------------------------------------------------------------------------
# Private helper for error messages
# ---------------------------------------------------------------------------

def _insn_short(insn: Any, max_chars: int = 60) -> str:
    """Return a single-line preview of *insn* (for error messages)."""
    raw: str = getattr(insn, "raw", "") or repr(insn)
    return raw if len(raw) <= max_chars else raw[: max_chars - 1] + "\u2026"


def _insns_preview_msg(insns: list, max_n: int = 3, max_chars: int = 60) -> str:
    """Return a short comma-joined preview of an instruction list."""
    if not insns:
        return "(empty)"
    parts = [_insn_short(i, max_chars) for i in insns[:max_n]]
    extra = f" \u2026 (+{len(insns) - max_n} more)" if len(insns) > max_n else ""
    return "; ".join(parts) + extra


# ---------------------------------------------------------------------------
# Merge exceptions
# ---------------------------------------------------------------------------

class CFGMergeError(ValueError):
    """Base class for all :func:`merge_cfgs` errors."""


class DuplicateLabelError(CFGMergeError):
    """Raised when a single input CFG contains duplicate labels.

    Attributes:
        cfg_index: Zero-based index of the offending input CFG.
        label:     The duplicated label string.
        block_ids: Ids of all blocks sharing *label* in that CFG.
    """

    def __init__(self, cfg_index: int, label: str, block_ids: list[str]) -> None:
        self.cfg_index = cfg_index
        self.label = label
        self.block_ids = block_ids
        super().__init__(
            f"CFG[{cfg_index}] has duplicate label {label!r} "
            f"on blocks {block_ids!r}. "
            "Each input CFG must have unique labels before merging."
        )


class InsnConflictError(CFGMergeError):
    """Raised when two blocks with the same label both carry different instructions.

    Attributes:
        label:   The shared label.
        block_a: First conflicting :class:`BasicBlock`.
        block_b: Second conflicting :class:`BasicBlock`.
    """

    def __init__(self, label: str, block_a: BasicBlock, block_b: BasicBlock) -> None:
        self.label = label
        self.block_a = block_a
        self.block_b = block_b
        super().__init__(
            f"Label {label!r}: both blocks have non-empty instructions that differ.\n"
            f"  Block {block_a.id!r}: {_insns_preview_msg(block_a.insns)}\n"
            f"  Block {block_b.id!r}: {_insns_preview_msg(block_b.insns)}"
        )


class EdgeConflictError(CFGMergeError):
    """Raised when the same canonical edge appears with differing attributes.

    Attributes:
        src:         Canonical source block id.
        dst:         Canonical destination block id.
        attrs_a:     Attribute dict from the first occurrence.
        attrs_b:     Attribute dict from the conflicting occurrence.
        cfg_a_index: Index of the CFG that contributed *attrs_a* (or ``None``).
        cfg_b_index: Index of the CFG that contributed *attrs_b* (or ``None``).
    """

    def __init__(
        self,
        src: str,
        dst: str,
        attrs_a: dict[str, Any],
        attrs_b: dict[str, Any],
        cfg_a_index: int | None = None,
        cfg_b_index: int | None = None,
    ) -> None:
        self.src = src
        self.dst = dst
        self.attrs_a = attrs_a
        self.attrs_b = attrs_b
        self.cfg_a_index = cfg_a_index
        self.cfg_b_index = cfg_b_index
        origin = (
            f" (CFG[{cfg_a_index}] vs CFG[{cfg_b_index}])"
            if cfg_a_index is not None and cfg_b_index is not None
            else ""
        )
        super().__init__(
            f"Edge {src!r} \u2192 {dst!r} has conflicting attributes{origin}:\n"
            f"  CFG[{cfg_a_index}]: {attrs_a!r}\n"
            f"  CFG[{cfg_b_index}]: {attrs_b!r}"
        )


class MetaConflictError(CFGMergeError):
    """Raised when two blocks with the same label have conflicting ``meta`` dicts.

    A conflict occurs only when *both* sides carry a non-empty ``meta`` dict
    and the dicts are not equal.

    Attributes:
        label:  The shared label.
        meta_a: ``meta`` dict from the first block.
        meta_b: ``meta`` dict from the conflicting block.
    """

    def __init__(self, label: str, meta_a: dict[str, Any], meta_b: dict[str, Any]) -> None:
        self.label = label
        self.meta_a = meta_a
        self.meta_b = meta_b
        super().__init__(
            f"Label {label!r}: both blocks have non-empty meta dicts that differ. "
            f"{meta_a!r} vs {meta_b!r}."
        )


# ---------------------------------------------------------------------------
# CFG
# ---------------------------------------------------------------------------

class CFG:
    """Control Flow Graph.

    Nodes are identified by string *block ids*.  Each node stores the
    corresponding :class:`BasicBlock` as a node attribute.

    Example::

        cfg = CFG()
        cfg.add_block("entry", label="IDLE", insns=[Assignment("x", [])])
        cfg.add_block("work",  label="FETCH")
        cfg.add_block("done",  label="DONE")
        cfg.add_edge("entry", "work", cond="start", priority=0)
        cfg.add_edge("entry", "done", cond=None,    priority=1)
        cfg.set_entry("entry")
        cfg.set_exit("done")   # optional
    """

    def __init__(self) -> None:
        self._g: nx.DiGraph = nx.DiGraph()
        self._entry: str | None = None
        self._exit: str | None = None

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def add_block(
        self,
        block_id: str | BasicBlock,
        label: str | None = None,
        insns: list[Insn] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> BasicBlock:
        """Add a :class:`BasicBlock` and return it.

        ``block_id`` may be either a string id or a pre-built
        :class:`BasicBlock`.  When a block object is provided, its contents are
        copied into this CFG.

        Raises :class:`ValueError` if *block_id* already exists.
        """
        if isinstance(block_id, BasicBlock):
            if label is not None or insns is not None or meta is not None:
                raise ValueError(
                    "When adding a BasicBlock object, label/insns/meta must "
                    "not be provided separately."
                )
            source = block_id
            block_id = source.id
            label = source.label
            insns = list(source.insns)
            meta = dict(source.meta)

        if block_id in self._g:
            raise ValueError(f"Block {block_id!r} already exists in CFG.")
        bb = BasicBlock(
            id=block_id,
            label=label,
            insns=list(insns or []),
            meta=dict(meta or {}),
        )
        self._g.add_node(block_id, block=bb)
        return bb

    def add_edge(
        self,
        src: str,
        dst: str,
        cond: str | None = None,
        priority: int = 0,
        **attrs: Any,
    ) -> None:
        """Add a directed edge from *src* to *dst*.

        Args:
            src:      Source block id.
            dst:      Destination block id.
            cond:     Transition condition string, or ``None`` for an
                      unconditional edge.
            priority: Evaluation order when multiple edges leave *src*.
                      Lower values are evaluated first.  Defaults to ``0``.
            **attrs:  Additional edge attributes stored verbatim.

        Both blocks must already exist.
        """
        for bid in (src, dst):
            if bid not in self._g:
                raise KeyError(f"Block {bid!r} not found in CFG.")
        self._g.add_edge(src, dst, cond=cond, priority=priority, **attrs)

    def remove_block(self, block_id: str) -> BasicBlock:
        """Remove *block_id* and all incident edges, returning the removed block."""
        bb = self.get_block(block_id)
        if block_id == self._entry:
            self._entry = None
        if block_id == self._exit:
            self._exit = None
        self._g.remove_node(block_id)
        return bb

    def remove_edge(self, src: str, dst: str) -> dict[str, Any]:
        """Remove edge *src* -> *dst*, returning a copy of its attributes."""
        if not self._g.has_edge(src, dst):
            raise KeyError(f"Edge {src!r} -> {dst!r} not found in CFG.")
        attrs = dict(self._g[src][dst])
        self._g.remove_edge(src, dst)
        return attrs

    def rename_block(self, old_id: str, new_id: str) -> BasicBlock:
        """Rename block *old_id* to *new_id* and return the updated block.

        All incoming and outgoing edges (including self-loops) are preserved
        with their original attributes.  The CFG entry and exit markers are
        updated if they referred to *old_id*.

        Raises:
            KeyError:   If *old_id* is not found in the CFG.
            ValueError: If *new_id* already exists in the CFG.
        """
        if old_id not in self._g:
            raise KeyError(f"Block {old_id!r} not found in CFG.")
        if new_id in self._g:
            raise ValueError(f"Block {new_id!r} already exists in CFG.")
        nx.relabel_nodes(self._g, {old_id: new_id}, copy=False)
        bb = self._g.nodes[new_id]["block"]
        bb.id = new_id
        if self._entry == old_id:
            self._entry = new_id
        if self._exit == old_id:
            self._exit = new_id
        return bb

    def set_entry(self, block_id: str) -> None:
        """Designate *block_id* as the CFG entry (start) block.  Required."""
        if block_id not in self._g:
            raise KeyError(f"Block {block_id!r} not found in CFG.")
        self._entry = block_id

    def set_exit(self, block_id: str) -> None:
        """Designate *block_id* as the CFG exit (return / halt) block.

        Optional.  Must be set for subroutines and MCU main CFGs.
        FSM main CFGs that loop forever may omit this.
        """
        if block_id not in self._g:
            raise KeyError(f"Block {block_id!r} not found in CFG.")
        self._exit = block_id

    # ------------------------------------------------------------------
    # Block / edge access
    # ------------------------------------------------------------------

    def get_block(self, block_id: str) -> BasicBlock:
        """Return the :class:`BasicBlock` for *block_id*."""
        if block_id not in self._g:
            raise KeyError(f"Block {block_id!r} not found in CFG.")
        return self._g.nodes[block_id]["block"]

    @property
    def blocks(self) -> list[BasicBlock]:
        """All blocks in insertion order."""
        return [data["block"] for _, data in self._g.nodes(data=True)]

    @property
    def edges(self) -> list[tuple[str, str, dict[str, Any]]]:
        """All edges as ``(src, dst, attrs)`` triples."""
        return [(u, v, dict(d)) for u, v, d in self._g.edges(data=True)]

    @property
    def entry(self) -> BasicBlock | None:
        return self._g.nodes[self._entry]["block"] if self._entry else None

    @property
    def exit(self) -> BasicBlock | None:
        return self._g.nodes[self._exit]["block"] if self._exit else None

    def predecessors(self, block_id: str) -> list[BasicBlock]:
        return [self.get_block(p) for p in self._g.predecessors(block_id)]

    def successors(self, block_id: str) -> list[BasicBlock]:
        return [self.get_block(s) for s in self._g.successors(block_id)]

    def edge_attrs(self, src: str, dst: str) -> dict[str, Any]:
        """Return a copy of the attribute dict for edge *src* → *dst*."""
        return dict(self._g[src][dst])

    def has_edge(self, src: str, dst: str) -> bool:
        """Return ``True`` if edge *src* -> *dst* exists."""
        return self._g.has_edge(src, dst)

    def out_edges(self, block_id: str) -> list[tuple[str, str, dict[str, Any]]]:
        """Return outgoing edges of *block_id* sorted by ``priority``.

        Returns a list of ``(src, dst, attrs)`` triples.
        """
        edges = [
            (u, v, dict(d))
            for u, v, d in self._g.out_edges(block_id, data=True)
        ]
        edges.sort(key=lambda e: e[2].get("priority", 0))
        return edges

    def in_edges(self, block_id: str) -> list[tuple[str, str, dict[str, Any]]]:
        """Return incoming edges of *block_id* sorted by ``priority``.

        Returns a list of ``(src, dst, attrs)`` triples.
        """
        edges = [
            (u, v, dict(d))
            for u, v, d in self._g.in_edges(block_id, data=True)
        ]
        edges.sort(key=lambda e: e[2].get("priority", 0))
        return edges

    def copy(self) -> "CFG":
        """Return a deep copy of blocks, edges, entry, and exit."""
        clone = CFG()
        for bb in self.blocks:
            clone.add_block(
                bb.id,
                label=bb.label,
                insns=deepcopy(bb.insns),
                meta=deepcopy(bb.meta),
            )
        for src, dst, attrs in self.edges:
            clone.add_edge(src, dst, **deepcopy(attrs))
        clone._entry = self._entry
        clone._exit = self._exit
        return clone

    def validate(self, *, require_entry: bool = True) -> list[str]:
        """Return structural issues found in the CFG.

        The result is empty when no issues are found.  The checks are kept
        generic: node existence, entry/exit consistency, isolated blocks, and
        common edge-shape mistakes such as duplicate priorities or multiple
        default edges from one block.
        """
        issues: list[str] = []

        if require_entry and self._entry is None:
            issues.append("entry is not set")
        if self._entry is not None and self._entry not in self._g:
            issues.append(f"entry block {self._entry!r} is not in the CFG")
        if self._exit is not None and self._exit not in self._g:
            issues.append(f"exit block {self._exit!r} is not in the CFG")

        for block_id in self._g.nodes:
            if self._g.in_degree(block_id) == 0 and self._g.out_degree(block_id) == 0:
                if block_id not in {self._entry, self._exit}:
                    issues.append(f"block {block_id!r} is isolated")

            out = list(self._g.out_edges(block_id, data=True))
            priorities = [d.get("priority", 0) for _, _, d in out]
            if len(priorities) != len(set(priorities)):
                issues.append(f"block {block_id!r} has duplicate outgoing priorities")

            default_count = sum(1 for _, _, d in out if d.get("cond") is None)
            if default_count > 1:
                issues.append(f"block {block_id!r} has multiple default outgoing edges")
            if len(out) == 1 and out[0][2].get("cond") is not None:
                issues.append(
                    f"block {block_id!r} has a single conditional outgoing edge "
                    "with no default path"
                )

        return issues

    def __contains__(self, block_id: str) -> bool:
        return block_id in self._g

    def __len__(self) -> int:
        return len(self._g)

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def _start(self) -> str:
        """Return the entry block id, or raise if not set."""
        if self._entry is None:
            raise RuntimeError("CFG entry is not set. Call set_entry() first.")
        return self._entry

    def dfs(self, start: str | None = None) -> Generator[BasicBlock, None, None]:
        """Yield blocks in depth-first pre-order starting from *start* (or entry)."""
        root = start or self._start()
        for nid in nx.dfs_preorder_nodes(self._g, root):
            yield self.get_block(nid)

    def bfs(self, start: str | None = None) -> Generator[BasicBlock, None, None]:
        """Yield blocks in breadth-first order starting from *start* (or entry)."""
        root = start or self._start()
        for nid in nx.bfs_tree(self._g, root).nodes:
            yield self.get_block(nid)

    def reverse_postorder(self, start: str | None = None) -> list[BasicBlock]:
        """Return blocks in Reverse Post-Order (RPO).

        RPO is the standard traversal order for forward dataflow analyses.
        Loop headers appear before their bodies; a block always appears before
        its successors in DAG portions of the graph.

        Uses ``networkx.dfs_postorder_nodes`` (iterative internally) to avoid
        Python's recursive call-stack limit on large graphs.
        """
        root = start or self._start()
        postorder = list(nx.dfs_postorder_nodes(self._g, root))
        return [self.get_block(nid) for nid in reversed(postorder)]

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def can_reach(self, src: str, dst: str) -> bool:
        """Return ``True`` if there is a path from *src* to *dst*."""
        return nx.has_path(self._g, src, dst)

    def find_unreachable(self, start: str | None = None) -> list[BasicBlock]:
        """Return all blocks that cannot be reached from *start* (or entry)."""
        root = start or self._start()
        reachable = nx.descendants(self._g, root) | {root}
        return [self.get_block(nid) for nid in self._g.nodes if nid not in reachable]

    def find_sccs(self) -> list[list[str]]:
        """Return all Strongly Connected Components as lists of block ids.

        SCCs are returned in topological order of the condensation DAG
        (source SCCs first).
        """
        condensation = nx.condensation(self._g)
        result: list[list[str]] = []
        for node in nx.topological_sort(condensation):
            members = sorted(condensation.nodes[node]["members"])
            result.append(members)
        return result

    # ------------------------------------------------------------------
    # Loop analysis
    # ------------------------------------------------------------------

    def find_back_edges(self, start: str | None = None) -> list[tuple[str, str]]:
        """Return all DFS back-edges (edges from a node to an ancestor).

        A back-edge ``(u -> v)`` means ``v`` is an ancestor of ``u`` in the
        DFS tree -- the hallmark of a natural loop.

        Uses an explicit stack to avoid Python recursion depth limits on large
        graphs.
        """
        root = start or self._start()
        back_edges: list[tuple[str, str]] = []
        visited: set[str] = set()
        in_stack: set[str] = set()
        # Stack entries: (node, iterator-over-successors)
        stack: list[tuple[str, Any]] = [(root, iter(self._g.successors(root)))]
        visited.add(root)
        in_stack.add(root)

        while stack:
            node, children = stack[-1]
            try:
                succ = next(children)
                if succ not in visited:
                    visited.add(succ)
                    in_stack.add(succ)
                    stack.append((succ, iter(self._g.successors(succ))))
                elif succ in in_stack:
                    back_edges.append((node, succ))
            except StopIteration:
                stack.pop()
                in_stack.discard(node)

        return back_edges

    def find_natural_loops(self, start: str | None = None) -> list[NaturalLoop]:
        """Return all natural loops identified by their back-edges.

        For each back-edge ``(tail → header)``, the natural loop body is the
        set of nodes from which *tail* is reachable without passing through
        *header* (plus *header* itself).
        """
        back_edges = self.find_back_edges(start)
        loops: list[NaturalLoop] = []
        rev = self._g.reverse()
        for tail, header in back_edges:
            body: set[str] = {header}
            worklist = [tail]
            while worklist:
                node = worklist.pop()
                if node in body:
                    continue
                body.add(node)
                for pred in rev.successors(node):
                    if pred != header and pred not in body:
                        worklist.append(pred)
            loops.append(NaturalLoop(header=header, body=body, back_edge=(tail, header)))
        return loops

    # ------------------------------------------------------------------
    # Dominance
    # ------------------------------------------------------------------

    def dominators(self, start: str | None = None) -> dict[str, str]:
        """Return the immediate dominator mapping ``{node: idom}``.

        Uses Lengauer-Tarjan via networkx.  The entry node maps to itself.
        """
        root = start or self._start()
        idom = nx.immediate_dominators(self._g, root)
        idom.setdefault(root, root)
        return idom

    def post_dominators(self, exit_node: str) -> dict[str, str]:
        """Return the immediate post-dominator mapping ``{node: ipost_dom}``.

        Computed as dominators on the reversed graph from *exit_node*.

        Args:
            exit_node: The block id to treat as the exit / sink for
                       post-dominator computation.  Must be provided explicitly;
                       this method does **not** fall back to ``self._exit``.
        """
        if exit_node not in self._g:
            raise KeyError(f"Block {exit_node!r} not found in CFG.")
        idom = nx.immediate_dominators(self._g.reverse(), exit_node)
        idom.setdefault(exit_node, exit_node)
        return idom

    def dominator_tree(self, start: str | None = None) -> nx.DiGraph:
        """Return the dominator tree as a networkx DiGraph (idom → node edges)."""
        idom = self.dominators(start)
        tree = nx.DiGraph()
        tree.add_nodes_from(self._g.nodes)
        for node, dom in idom.items():
            if node != dom:
                tree.add_edge(dom, node)
        return tree

    def _trace_linearize(self, start: str | None = None) -> list[str]:
        """Return a priority-guided trace order that delays common joins.

        Uses an explicit stack to avoid Python recursion-depth limits on
        large graphs.  Nodes whose reachable predecessors have not all been
        visited are skipped on the stack and picked up later — this keeps
        branch chains grouped while deferring common join points.
        """
        root = start or self._start()
        reachable = nx.descendants(self._g, root) | {root}
        node_rank = {bb.id: i for i, bb in enumerate(self.blocks)}
        back_edges = {
            edge for edge in self.find_back_edges(root)
            if edge[0] in reachable and edge[1] in reachable
        }
        visited: set[str] = set()
        order: list[str] = []

        def _preds_ready(node: str) -> bool:
            preds = [
                p for p in self._g.predecessors(node)
                if p in reachable and (p, node) not in back_edges
            ]
            return len(preds) <= 1 or all(p in visited for p in preds)

        def _follow_chain(seed: str, *, force_seed: bool = False) -> None:
            """Iterative DFS from *seed*, respecting priority and readiness."""
            stack: list[str] = [seed]
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                if not _preds_ready(node):
                    if force_seed and node == seed:
                        force_seed = False
                    else:
                        continue
                visited.add(node)
                order.append(node)
                # Push successors in reverse priority so highest-priority
                # (lowest value) ends up on top of the LIFO stack.
                succs = [
                    dst for _, dst, _ in self.out_edges(node)
                    if dst not in visited and dst in reachable
                ]
                stack.extend(reversed(succs))

        _follow_chain(root)

        # Handle remaining unvisited nodes (disconnected or deferred joins).
        remaining = reachable - visited
        while remaining:
            ready = [n for n in remaining if _preds_ready(n)]
            candidate = min(ready, key=lambda n: node_rank[n]) if ready else None
            if candidate is None:
                # Irreducible CFG: force-pick an arbitrary node.
                candidate = min(remaining, key=lambda n: node_rank[n])
            before = len(visited)
            _follow_chain(candidate, force_seed=(not ready))
            if len(visited) == before:
                # Defensive guard: trace linearization must always make
                # progress, even for unusual irreducible graphs.
                visited.add(candidate)
                order.append(candidate)
            remaining = reachable - visited

        return order

    # ------------------------------------------------------------------
    # Linearization
    # ------------------------------------------------------------------

    def linearize(
        self,
        strategy: Literal["rpo", "topological", "trace", "custom"] = "rpo",
        start: str | None = None,
        order: list[str] | None = None,
    ) -> list[str]:
        """Return an ordered list of block ids suitable for code ordering.

        Args:
            strategy: ``"rpo"`` (default) — Reverse Post-Order; handles cycles.
                      ``"topological"`` — raises if the graph contains a cycle.
                      ``"trace"``       — priority-guided trace order.
                      ``"custom"``      — use *order* as a preference list.
                        Blocks in *order* are emitted first (in the given
                        sequence), followed by any reachable blocks not covered
                        by *order* (appended in RPO order).  Only blocks
                        reachable from *start* appear in the result.
            start:    Entry block (defaults to ``self._entry``).
            order:    Required when *strategy* is ``"custom"``; ignored
                      otherwise.  Must be a list of block ids that exist in
                      the CFG **and** are reachable from *start*.  Raises
                      :class:`ValueError` if any id is unknown or unreachable.
                      Reachable blocks not listed in *order* are appended in
                      RPO order after the preference list.
        """
        if strategy == "rpo":
            return [bb.id for bb in self.reverse_postorder(start)]
        elif strategy == "trace":
            return self._trace_linearize(start)
        elif strategy == "topological":
            root = start or self._start()
            reachable = nx.descendants(self._g, root) | {root}
            ordered_reachable = [bb.id for bb in self.blocks if bb.id in reachable]
            subgraph = self._g.subgraph(ordered_reachable)
            try:
                return list(nx.topological_sort(subgraph))
            except nx.NetworkXUnfeasible as exc:
                raise ValueError(
                    "Cannot use topological strategy: CFG contains a cycle. "
                    "Use strategy='rpo' instead."
                ) from exc
        elif strategy == "custom":
            return self._custom_linearize(start, order)
        else:
            raise ValueError(f"Unknown linearize strategy: {strategy!r}")

    def _custom_linearize(
        self,
        start: str | None,
        order: list[str] | None,
    ) -> list[str]:
        """Implement the ``"custom"`` linearization strategy.

        *order* is a *preference* list: blocks in it are emitted first (in
        sequence), then any remaining reachable blocks are appended in RPO
        order.  Only blocks reachable from *start* appear in the result.
        """
        if order is None:
            raise ValueError(
                "strategy='custom' requires the 'order' parameter."
            )
        root = start or self._start()
        reachable: set[str] = nx.descendants(self._g, root) | {root}

        # Validate every id in order: must exist and be reachable.
        for bid in order:
            if bid not in self._g:
                raise ValueError(
                    f"custom order contains unknown block {bid!r}."
                )
            if bid not in reachable:
                raise ValueError(
                    f"custom order contains unreachable block {bid!r} "
                    f"(not reachable from {root!r})."
                )

        # Emit preferred blocks first (deduplicate while preserving order).
        result: list[str] = []
        seen: set[str] = set()
        for bid in order:
            if bid not in seen:
                result.append(bid)
                seen.add(bid)

        # Append remaining reachable blocks in RPO order.
        rpo_ids = [bb.id for bb in self.reverse_postorder(root)]
        for bid in rpo_ids:
            if bid not in seen:
                result.append(bid)
                seen.add(bid)

        return result

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return a concise one-line description (useful in REPL / debuggers)."""
        n_blocks = len(self)
        n_edges  = self._g.number_of_edges()
        entry_str = ""
        if self._entry is not None:
            ebb = self.get_block(self._entry)
            entry_name = ebb.label or self._entry
            entry_str = f", entry={entry_name!r}"
        return f"CFG({n_blocks} blocks, {n_edges} edges{entry_str})"

    def __str__(self) -> str:
        """Return the same formatted table as :meth:`format`."""
        return self.format()

    def format(
        self,
        max_insns: int = 2,
        max_insn_chars: int = 35,
        *,
        start: str | None = None,
        show_unreachable: bool = True,
        show_meta: bool = False,
    ) -> str:
        """Return a human-readable table of the CFG as a plain string.

        Blocks are ordered by Reverse Post-Order from entry (if set), so the
        display follows control flow; unreachable blocks append at the end.
        Out-edges per block are listed by priority (lowest value = highest
        priority = listed first, matching :meth:`out_edges`).

        Args:
            max_insns:      Maximum instructions to preview per block (default 2).
            max_insn_chars: Maximum characters per single instruction preview
                            before truncation with "\u2026" (default 35).
            start:          Optional block id to use as the display root.
                            Defaults to the CFG entry when set.
            show_unreachable:
                            If ``True``, append blocks not reachable from the
                            display root after the main traversal.
            show_meta:      If ``True``, include a metadata preview column.

        Returns:
            A multi-line ``str``.  Pass to ``print()`` or write to a file.
        """
        # -- helpers ------------------------------------------------------
        def _preview(bb: BasicBlock) -> str:
            if not bb.insns:
                return "(empty)"
            parts: list[str] = []
            for insn in bb.insns[:max_insns]:
                raw: str = getattr(insn, "raw", "") or repr(insn)
                if len(raw) > max_insn_chars:
                    raw = raw[: max_insn_chars - 1] + "\u2026"
                parts.append(raw)
            return "; ".join(parts) + (" \u2026" if len(bb.insns) > max_insns else "")

        def _meta_preview(bb: BasicBlock) -> str:
            return repr(bb.meta) if bb.meta else ""

        def _edge_line(dst_id: str, attrs: dict[str, Any]) -> str:
            dst_bb = self.get_block(dst_id)
            cond   = attrs.get("cond")
            prio   = attrs.get("priority", 0)
            if dst_bb.label and dst_bb.label != dst_id:
                target = f"{dst_bb.label} ({dst_id})"
            elif dst_bb.label:
                target = dst_bb.label
            else:
                target = dst_id
            tokens: list[str] = []
            if prio:
                tokens.append(f"({prio})")
            if cond is not None:
                tokens.append(cond)
            tokens.append(f"\u2500\u25ba {target}")
            return " ".join(tokens)

        # -- block order: RPO from display root, orphans optionally appended -
        display_root = start if start is not None else self._entry
        if display_root is not None and display_root not in self._g:
            raise KeyError(f"Block {display_root!r} not found in CFG.")
        if display_root is not None:
            try:
                ordered: list[BasicBlock] = self.reverse_postorder(display_root)
                seen = {bb.id for bb in ordered}
                if show_unreachable:
                    for bb in self.blocks:
                        if bb.id not in seen:
                            ordered.append(bb)
            except RuntimeError:
                ordered = list(self.blocks)
        else:
            ordered = list(self.blocks)

        # -- column widths ------------------------------------------------
        id_w    = max(max((len(bb.id)           for bb in ordered), default=0), 8)
        label_w = max(max((len(bb.label or "")  for bb in ordered), default=0), 5)
        insns_w = max(max((len(_preview(bb))    for bb in ordered), default=0), 15)
        meta_w  = max(max((len(_meta_preview(bb)) for bb in ordered), default=0), 4)

        SEP          = "  \u2502  "
        blank_marker = " "
        blank_id     = " " * id_w
        blank_label  = " " * label_w
        blank_insns  = " " * insns_w
        blank_meta   = " " * meta_w

        # -- header -------------------------------------------------------
        n_b, n_e = len(self), self._g.number_of_edges()
        hdr = [f"CFG  {n_b} block{'s' if n_b != 1 else ''} \u00b7 "
               f"{n_e} edge{'s' if n_e != 1 else ''}"]
        if self._entry is not None:
            ebb = self.get_block(self._entry)
            hdr.append(f"entry='{ebb.label or self._entry}'")
        if self._exit is not None:
            xbb = self.get_block(self._exit)
            hdr.append(f"exit='{xbb.label or self._exit}'")

        col_parts = [
            f" {'Block ID':<{id_w}}",
            f"{'Label':<{label_w}}",
            f"{'Insns (preview)':<{insns_w}}",
        ]
        if show_meta:
            col_parts.append(f"{'Meta':<{meta_w}}")
        col_parts.append("Out-edges")
        col_hdr = SEP.join(col_parts)
        rule = "\u2500" * len(col_hdr)

        lines: list[str] = ["  ".join(hdr), col_hdr, rule]

        # -- rows ---------------------------------------------------------
        for bb in ordered:
            if bb.id == self._entry:
                marker = "\u25b6"
            elif bb.id == self._exit:
                marker = "\u23f9"
            else:
                marker = " "

            out = self.out_edges(bb.id)       # sorted by priority already
            first_edge = _edge_line(out[0][1], out[0][2]) if out else "(terminal)"

            row_parts = [
                f"{marker}{bb.id:<{id_w}}",
                f"{bb.label or '':<{label_w}}",
                f"{_preview(bb):<{insns_w}}",
            ]
            if show_meta:
                row_parts.append(f"{_meta_preview(bb):<{meta_w}}")
            row_parts.append(first_edge)
            lines.append(SEP.join(row_parts))
            for _, dst, attrs in out[1:]:
                edge_parts = [
                    f"{blank_marker}{blank_id}",
                    blank_label,
                    blank_insns,
                ]
                if show_meta:
                    edge_parts.append(blank_meta)
                edge_parts.append(_edge_line(dst, attrs))
                lines.append(SEP.join(edge_parts))

        lines.append(rule)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Visualization (optional — requires pydot)
    # ------------------------------------------------------------------

    def export_dot(
        self,
        path: str | None = None,
        *,
        show_insns: bool = False,
    ) -> str:
        """Export the CFG as a Graphviz DOT string.

        Each node shows the block ``id`` (and optionally its instructions).
        Edges are labelled with ``cond`` and ``priority`` where applicable.

        Args:
            path:       If given, write the DOT string to this file path.
            show_insns: If ``True``, include each block's instruction list in
                        the node label.

        Returns:
            The DOT source as a string.

        Raises:
            ImportError: If the ``pydot`` package is not installed.  Install
                         it with ``pip install rpkbin[dot]`` or ``pip install pydot``.
        """
        try:
            import pydot  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "export_dot() requires the 'pydot' package.  "
                "Install it with: pip install pydot"
            ) from exc

        dot_graph = pydot.Dot(graph_type="digraph", rankdir="TB")

        for nid, data in self._g.nodes(data=True):
            bb = data["block"]
            if show_insns and bb.insns:
                insn_lines = "\n".join(repr(insn) for insn in bb.insns)
                label = f"{nid}\n{insn_lines}"
            else:
                label = nid if bb.label is None else f"{nid}\n({bb.label})"
            shape = "rectangle"
            if nid == self._entry:
                shape = "ellipse"
            elif nid == self._exit:
                shape = "doubleoctagon"
            dot_graph.add_node(pydot.Node(
                nid,
                label=label,
                shape=shape,
                fontname="Courier New",
            ))

        for u, v, d in self._g.edges(data=True):
            cond = d.get("cond")
            prio = d.get("priority", 0)
            if cond is not None:
                edge_label = f"[{prio}] {cond}"
            else:
                edge_label = f"[{prio}] (default)" if prio else ""
            style = "solid" if cond is None else "dashed"
            dot_graph.add_edge(pydot.Edge(
                u, v,
                label=edge_label,
                style=style,
                fontname="Courier New",
                fontsize=10,
            ))

        dot_src: str = dot_graph.to_string()
        if path is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(dot_src)
        return dot_src

    # ------------------------------------------------------------------
    # Internal graph access (for subclasses / advanced use)
    # ------------------------------------------------------------------

    @property
    def _graph(self) -> nx.DiGraph:
        """Direct access to the underlying networkx DiGraph (advanced use)."""
        return self._g


# ---------------------------------------------------------------------------
# CFG merge
# ---------------------------------------------------------------------------

def merge_cfgs(*cfgs: CFG) -> CFG:
    """Merge multiple CFGs into one by unifying blocks that share the same label.

    Matching behaviour
    ------------------
    * ``label`` is the merge key.  Two blocks with the same ``label`` are
      considered the same logical node and are unified in the result.
    * A block with ``insns=[]`` acts as a *placeholder*: it only marks a
      link point and carries no instructions.  When a placeholder and a
      block with instructions share the same label, the instructed block
      wins (its ``id`` and ``insns`` become canonical).
    * ``label=None`` blocks are **never** merged.  They represent anonymous
      sequential code that requires no ASM label; they are kept independent
      in the result, with their ids prefixed to prevent collisions.

      .. important::

          ``label=None`` blocks are **local to their input CFG** and cannot
          act as cross-flow connection points.  If you need a block to be a
          merge target across flows, give it a ``label``.

    Tip: ``merge_cfgs(cfg)`` with a single argument effectively copies the
    CFG (entry/exit are still reset to ``None``).

    Edge rules
    ----------
    * Identical edges (same canonical ``src``/``dst`` **and** same attrs)
      are silently deduplicated.
    * Edges with the same canonical ``src``/``dst`` but *different* attrs
      raise :class:`EdgeConflictError`.

    Entry / Exit
    ------------
    The merged CFG's ``entry`` and ``exit`` are always reset to ``None``.
    Call :meth:`CFG.set_entry` / :meth:`CFG.set_exit` after merging.

    Cycle warning
    -------------
    If at least one input CFG was acyclic but the merged result contains
    cycles, a :class:`UserWarning` is issued listing the new cycles.
    Detection uses :func:`networkx.is_directed_acyclic_graph` and
    :func:`networkx.simple_cycles` — no custom cycle-detection code.

    Args:
        *cfgs: One or more :class:`CFG` objects to merge.

    Returns:
        A new :class:`CFG` containing the merged graph.  Input CFGs are
        not modified.

    Raises:
        DuplicateLabelError: A single input CFG has two blocks sharing the
            same label (invalid input; merge cannot proceed).
        InsnConflictError:   Two blocks with the same label both carry
            non-empty, *different* instruction lists.
        MetaConflictError:   Two blocks with the same label both carry
            non-empty, *different* ``meta`` dicts.
        EdgeConflictError:   The same canonical edge appears with different
            attributes in two flows.
    """
    if not cfgs:
        return CFG()

    # ------------------------------------------------------------------
    # Step 1: Pre-validate — each input CFG must have unique labels.
    # ------------------------------------------------------------------
    for i, cfg in enumerate(cfgs):
        label_to_ids: dict[str, list[str]] = {}
        for bb in cfg.blocks:
            if bb.label is None:
                continue
            label_to_ids.setdefault(bb.label, []).append(bb.id)
        for label, ids in label_to_ids.items():
            if len(ids) > 1:
                raise DuplicateLabelError(i, label, ids)

    # ------------------------------------------------------------------
    # Step 2: Build canonical map  { label -> canonical BasicBlock }
    # ------------------------------------------------------------------
    canonical: dict[str, BasicBlock] = {}
    canonical_meta: dict[str, dict[str, Any]] = {}

    for cfg in cfgs:
        for bb in cfg.blocks:
            if bb.label is None:
                continue
            label = bb.label

            if label not in canonical:
                canonical[label] = bb
                canonical_meta[label] = dict(bb.meta)
                continue

            existing = canonical[label]

            # insns resolution ----------------------------------------
            if existing.insns and bb.insns:
                if existing.insns != bb.insns:
                    raise InsnConflictError(label, existing, bb)
                # Identical insns — keep existing.
            elif bb.insns:
                # New block has content; it wins.
                canonical[label] = bb
            # else: existing already has insns, or both are placeholders;
            #       keep existing in both cases.

            # meta resolution (independent of the insns winner) -------
            # Conflict only when both carry non-empty, differing meta.
            if existing.meta and bb.meta and existing.meta != bb.meta:
                raise MetaConflictError(label, existing.meta, bb.meta)
            if bb.meta:
                canonical_meta[label] = dict(bb.meta)

    # ------------------------------------------------------------------
    # Step 3: Guard — canonical block ids must be unique across labels.
    # ------------------------------------------------------------------
    canon_id_to_label: dict[str, str] = {}
    for label, bb in canonical.items():
        if bb.id in canon_id_to_label:
            raise CFGMergeError(
                f"Blocks with labels {canon_id_to_label[bb.id]!r} and {label!r} "
                f"share id={bb.id!r}.  Labeled blocks must have unique ids across "
                "all input CFGs."
            )
        canon_id_to_label[bb.id] = label

    # ------------------------------------------------------------------
    # Step 4: Build the id-remap table.
    #   (flow_index, original_id)  ->  canonical_id_in_result
    # label=None blocks are prefixed to guarantee uniqueness.
    # ------------------------------------------------------------------
    all_id_map: dict[tuple[int, str], str] = {}

    for i, cfg in enumerate(cfgs):
        for bb in cfg.blocks:
            if bb.label is not None:
                all_id_map[(i, bb.id)] = canonical[bb.label].id
            else:
                all_id_map[(i, bb.id)] = f"__merge_{i}_{bb.id}"

    # Guard: a renamed label=None id must not collide with a canonical id.
    canonical_ids: set[str] = set(canon_id_to_label)
    for (i, orig_id), new_id in all_id_map.items():
        if cfgs[i].get_block(orig_id).label is None and new_id in canonical_ids:
            raise CFGMergeError(
                f"Renamed label=None block id {new_id!r} "
                f"(CFG[{i}] block {orig_id!r}) collides with a canonical labeled "
                "block id.  Rename the block to avoid the '__merge_N_' prefix."
            )

    # ------------------------------------------------------------------
    # Step 5: Construct result CFG — add blocks.
    # ------------------------------------------------------------------
    result = CFG()

    for label, bb in canonical.items():
        result.add_block(bb.id, label=bb.label, insns=list(bb.insns), meta=canonical_meta[label])

    for i, cfg in enumerate(cfgs):
        for bb in cfg.blocks:
            if bb.label is None:
                new_id = all_id_map[(i, bb.id)]
                result.add_block(new_id, label=None, insns=list(bb.insns), meta=dict(bb.meta))

    # ------------------------------------------------------------------
    # Step 6: Remap and add edges.
    # ------------------------------------------------------------------
    edge_registry: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}

    for i, cfg in enumerate(cfgs):
        for u, v, attrs in cfg._graph.edges(data=True):
            src = all_id_map[(i, u)]
            dst = all_id_map[(i, v)]
            key = (src, dst)
            attrs_copy = dict(attrs)
            if key not in edge_registry:
                edge_registry[key] = (i, attrs_copy)
            elif edge_registry[key][1] == attrs_copy:
                pass  # exact duplicate — silently deduplicate
            else:
                prev_i, prev_attrs = edge_registry[key]
                raise EdgeConflictError(src, dst, prev_attrs, attrs_copy, prev_i, i)

    for (src, dst), (_, attrs) in edge_registry.items():
        result.add_edge(src, dst, **attrs)

    # ------------------------------------------------------------------
    # Step 7: Warn if merge introduced new cycles (EC-10).
    # Uses nx.is_directed_acyclic_graph + nx.simple_cycles — no custom logic.
    # ------------------------------------------------------------------
    if any(nx.is_directed_acyclic_graph(cfg._graph) for cfg in cfgs):
        if not nx.is_directed_acyclic_graph(result._graph):
            new_cycles = list(nx.simple_cycles(result._graph))
            warnings.warn(
                f"merge_cfgs introduced {len(new_cycles)} new cycle(s): {new_cycles}",
                UserWarning,
                stacklevel=2,
            )

    return result
