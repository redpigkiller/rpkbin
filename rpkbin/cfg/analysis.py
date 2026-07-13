"""Interprocedural analysis utilities for CFG Programs.

This module provides analysis functions that operate across multiple CFGs
(i.e. across function boundaries) using the :class:`~rpkbin.cfg.program.Program`
container.

Key features
------------
* :func:`build_call_graph` — scans all ``CallRef`` instructions to construct
  the program's call graph automatically; no manual registration needed.
* :func:`check_call_depth` — verifies the call graph is a DAG and its depth
  does not exceed a hardware limit.
* :func:`interprocedural_liveness` — computes live-in / live-out sets for
  every block in every function using a bottom-up summary-based approach.

def/use derivation
------------------
``Assignment(lhs, rhs)``
    def = {lhs},  use = set(rhs)

``CallRef(callee)``
    def = callee's FunctionSummary.defs
    use = callee's FunctionSummary.uses

``OtherInsn(defs, uses)``
    def = defs,   use = uses
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, TYPE_CHECKING

import networkx as nx

from .block import Assignment, CallRef, Insn, OtherInsn
from .cfg import CFG

if TYPE_CHECKING:
    from .program import Program


# ---------------------------------------------------------------------------
# def/use helper
# ---------------------------------------------------------------------------

def _block_def_use(
    insn: Insn,
    summaries: dict[str, "FunctionSummary"],
) -> tuple[set[str], set[str]]:
    """Return ``(defs, uses)`` for a single instruction.

    For :class:`CallRef`, the summary of the callee is used if available;
    otherwise both sets are empty (safe but imprecise — run bottom-up to avoid
    this case).
    """
    if isinstance(insn, Assignment):
        return {insn.lhs}, set(insn.rhs)
    if isinstance(insn, CallRef):
        s = summaries.get(insn.callee)
        if s is not None:
            return set(s.defs), set(s.uses)
        return set(), set()   # callee not analysed yet (cycle guard)
    if isinstance(insn, OtherInsn):
        return set(insn.defs), set(insn.uses)
    return set(), set()      # unreachable with the current Insn union


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FunctionSummary:
    """Liveness summary exported from a single function.

    Attributes:
        defs: Variables the function *may* write on any execution path.
        uses: Variables the function reads before (possibly) writing them —
              i.e. the live-in set at the function's entry block.
    """

    defs: set[str] = field(default_factory=set)
    uses: set[str] = field(default_factory=set)

    def __repr__(self) -> str:
        return f"FunctionSummary(defs={sorted(self.defs)}, uses={sorted(self.uses)})"


@dataclass
class LivenessResult:
    """Per-block liveness sets for a single function.

    Attributes:
        live_in:  Mapping from block id to the set of variables live at the
                  *entry* of that block.
        live_out: Mapping from block id to the set of variables live at the
                  *exit* of that block.
    """

    live_in:  dict[str, FrozenSet[str]] = field(default_factory=dict)
    live_out: dict[str, FrozenSet[str]] = field(default_factory=dict)

    def is_live_at_entry(self, block_id: str, var: str) -> bool:
        """Return ``True`` if *var* is live at the entry of *block_id*."""
        return var in self.live_in.get(block_id, frozenset())

    def is_live_at_exit(self, block_id: str, var: str) -> bool:
        """Return ``True`` if *var* is live at the exit of *block_id*."""
        return var in self.live_out.get(block_id, frozenset())


# ---------------------------------------------------------------------------
# Call graph
# ---------------------------------------------------------------------------

def build_call_graph(program: Program) -> nx.DiGraph:
    """Build a directed call graph by scanning ``CallRef`` instructions.

    Each node in the returned graph is a function name (a key in
    ``program.cfgs``).  An edge ``(caller, callee)`` is added for every
    :class:`~rpkbin.cfg.block.CallRef` found in *caller*'s blocks.

    Returns:
        A :class:`networkx.DiGraph` with one node per function.

    Raises:
        KeyError: If a ``CallRef.callee`` does not exist in ``program.cfgs``.
    """
    cg: nx.DiGraph = nx.DiGraph()
    cg.add_nodes_from(program.cfgs.keys())

    for fn_name, cfg in program.cfgs.items():
        for bb in cfg.blocks:
            for insn in bb.insns:
                if isinstance(insn, CallRef):
                    if insn.callee not in program.cfgs:
                        raise KeyError(
                            f"CallRef to unknown function {insn.callee!r} "
                            f"in block {bb.id!r} of function {fn_name!r}."
                        )
                    cg.add_edge(fn_name, insn.callee)

    return cg


def check_call_depth(program: Program, max_depth: int | None = None) -> int:
    """Verify the call graph is acyclic and within the depth limit.

    Args:
        program:   The program to analyse.
        max_depth: Maximum allowed call-stack depth.  If ``None``, only the
                   actual depth is returned without raising.

    Returns:
        The actual maximum call depth (longest path length in the call graph).

    Raises:
        ValueError: If the call graph contains a cycle (recursive call).
        ValueError: If the actual depth exceeds *max_depth*.
    """
    cg = build_call_graph(program)
    try:
        depth: int = nx.dag_longest_path_length(cg)
    except nx.NetworkXUnfeasible:
        raise ValueError(
            "Call graph contains a cycle — recursive calls are not allowed."
        )
    if max_depth is not None and depth > max_depth:
        raise ValueError(
            f"Call depth {depth} exceeds the allowed maximum of {max_depth}."
        )
    return depth


# ---------------------------------------------------------------------------
# Intraprocedural liveness (single CFG)
# ---------------------------------------------------------------------------

def _intraprocedural_liveness(
    cfg: CFG,
    summaries: dict[str, FunctionSummary],
) -> tuple[LivenessResult, FunctionSummary]:
    """Run liveness analysis on a single CFG using pre-computed summaries.

    Returns the per-block :class:`LivenessResult` and the derived
    :class:`FunctionSummary` for this function.
    """
    g = cfg._graph

    # Compute per-block def/use sets (in sequential instruction order)
    block_def: dict[str, set[str]] = {}
    block_use: dict[str, set[str]] = {}
    for bb in cfg.blocks:
        blk_def: set[str] = set()
        blk_use: set[str] = set()
        for insn in bb.insns:
            d, u = _block_def_use(insn, summaries)
            # A variable used before it is defined in this block is live-in
            blk_use |= u - blk_def
            blk_def |= d
        block_def[bb.id] = blk_def
        block_use[bb.id] = blk_use

    # Initialize live sets
    live_in:  dict[str, FrozenSet[str]] = {bb.id: frozenset() for bb in cfg.blocks}
    live_out: dict[str, FrozenSet[str]] = {bb.id: frozenset() for bb in cfg.blocks}

    # Iterative worklist (backward)
    worklist: set[str] = {bb.id for bb in cfg.blocks}
    while worklist:
        bid = worklist.pop()

        new_out: set[str] = set()
        for succ in g.successors(bid):
            new_out |= live_in[succ]
        new_out_f = frozenset(new_out)

        new_in = frozenset(block_use[bid] | (new_out - block_def[bid]))

        if new_out_f != live_out[bid] or new_in != live_in[bid]:
            live_out[bid] = new_out_f
            live_in[bid]  = new_in
            for pred in g.predecessors(bid):
                worklist.add(pred)

    result = LivenessResult(live_in=live_in, live_out=live_out)

    # Build summary: defs = union of all block defs; uses = live_in at entry
    all_defs: set[str] = set()
    for d in block_def.values():
        all_defs |= d
    entry_uses: set[str] = set(live_in[cfg._entry]) if cfg._entry else set()
    summary = FunctionSummary(defs=all_defs, uses=entry_uses)

    return result, summary


# ---------------------------------------------------------------------------
# Interprocedural liveness
# ---------------------------------------------------------------------------

def interprocedural_liveness(
    program: Program,
) -> dict[str, LivenessResult]:
    """Bottom-up interprocedural liveness analysis.

    Analyses each function in reverse topological order of the call graph
    (leaf functions first) so that every callee's :class:`FunctionSummary`
    is available before its callers are analysed.

    The call graph must be a DAG (no recursion).  Use :func:`check_call_depth`
    to verify this beforehand if needed.

    Args:
        program: The program to analyse.

    Returns:
        A mapping from function name to its :class:`LivenessResult`.

    Raises:
        ValueError: If the call graph contains a cycle.
    """
    cg = build_call_graph(program)

    # Topological order: process leaves (callees) before callers
    try:
        topo_order: list[str] = list(nx.topological_sort(cg))
    except nx.NetworkXUnfeasible:
        raise ValueError(
            "Call graph contains a cycle — recursive calls are not allowed."
        )

    summaries: dict[str, FunctionSummary] = {}
    results:   dict[str, LivenessResult]  = {}

    for fn_name in reversed(topo_order):   # reversed → leaves first
        cfg = program.cfgs[fn_name]
        result, summary = _intraprocedural_liveness(cfg, summaries)
        results[fn_name]   = result
        summaries[fn_name] = summary

    return results
