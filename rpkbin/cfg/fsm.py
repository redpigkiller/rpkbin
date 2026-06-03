"""FSM-oriented checks and layout recipes for CFG Programs.

This module is a target-neutral recipe layer for FSM (Finite State Machine)
flows. It assumes:

* The *main* CFG represents the top-level state flow (often an infinite loop
  back to the reset/idle state).
* Subroutine CFGs represent callable helper routines invoked via
  :class:`~rpkbin.cfg.block.CallRef`.
* State transitions (edges) carry ``cond`` and ``priority`` attributes.
* There is typically **no exit block** on the main CFG — the machine runs
  forever.

FSM-specific checks
-------------------
``find_dead_states``
    States unreachable from the reset (entry) state.

``find_sink_sccs``
    Strongly Connected Components from which no state can return to the
    reset state.  These are "trap" states — the FSM enters and never
    recovers.  This is the FSM-appropriate definition of a "dead loop":
    unlike MCU programs, a loop *back to the reset state* is by design,
    not a bug.

``check_conditions_complete``
    Reports states where all outgoing edges are conditional (no
    ``cond=None`` / unconditional edge) — a potential "stuck" condition
    if no condition is satisfied at runtime.

FSM layout
----------
``linearize`` returns an :class:`FSMLayout`, an ordered list of
:class:`FSMSlot` objects.  Each slot holds a :class:`BasicBlock` together
with its outgoing edges sorted by ``priority``.  The caller uses this
structure to build a target-specific emitter, table, or hardware description
without the CFG package needing to know the target ISA or output format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import networkx as nx

from .block import BasicBlock
from .program import Program


# ---------------------------------------------------------------------------
# Linearization data structures
# ---------------------------------------------------------------------------

@dataclass
class ExitEdge:
    """One outgoing transition from an FSM state.

    Attributes:
        priority: Evaluation order (lower = higher priority).
        cond:     Condition string, or ``None`` for an unconditional / default
                  transition.
        target:   Target block id.
    """

    priority: int
    cond: str | None
    target: str


@dataclass
class FSMSlot:
    """One entry in an :class:`FSMLayout`.

    Attributes:
        block: The :class:`BasicBlock` for this state.
        exits: Outgoing transitions sorted by ``priority`` (ascending).
    """

    block: BasicBlock
    exits: list[ExitEdge]


@dataclass
class FSMLayout:
    """Result of :func:`linearize`.

    Attributes:
        slots: Ordered list of :class:`FSMSlot` objects representing the
               emission order of states in the main FSM flow.
    """

    slots: list[FSMSlot] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public analysis API
# ---------------------------------------------------------------------------

def find_dead_states(program: Program) -> list[BasicBlock]:
    """Return states in the main FSM flow that are unreachable from the reset state.

    Delegates to :meth:`~rpkbin.cfg.cfg.CFG.find_unreachable` on the main CFG.

    Returns:
        List of unreachable :class:`BasicBlock` objects (may be empty).
    """
    return program.main.find_unreachable()


def find_sink_sccs(program: Program) -> list[list[str]]:
    """Return Strongly Connected Components that trap the FSM.

    A *sink SCC* is a non-trivial SCC (has a cycle) from which no state has
    a path back to the reset (entry) state.  Unlike MCU dead-loop detection,
    we do **not** require an exit block — the FSM's intended behaviour is to
    loop back to the reset state, so the absence of that path is the bug.

    A self-loop on a state is treated as a cycle.

    Args:
        program: The program to analyse (only the main CFG is examined).

    Returns:
        List of sink SCCs; each SCC is a sorted list of state ids.
    """
    cfg = program.main
    entry_id = cfg._entry
    if entry_id is None:
        raise RuntimeError("Main CFG entry is not set.  Call set_entry() first.")

    g = cfg._graph
    rev = g.reverse()

    # States that can reach the entry (reset) state via the reversed graph
    can_reach_entry: set[str] = nx.descendants(rev, entry_id) | {entry_id}

    # Reachable from entry (avoid reporting unreachable noise)
    reachable_from_entry: set[str] = nx.descendants(g, entry_id) | {entry_id}

    sinks: list[list[str]] = []
    for scc in cfg.find_sccs():
        scc_set = set(scc)

        # Only consider reachable SCCs
        if not scc_set.intersection(reachable_from_entry):
            continue

        # Check for a cycle (self-loop or multi-node SCC)
        has_cycle = len(scc) > 1 or g.has_edge(scc[0], scc[0])
        if not has_cycle:
            continue

        # Sink = no node in SCC can reach the reset state
        if not scc_set.intersection(can_reach_entry):
            sinks.append(sorted(scc))

    return sinks


def check_conditions_complete(program: Program) -> list[str]:
    """Return state ids that have no unconditional (default) outgoing edge.

    A state where every outgoing transition is conditional may "get stuck"
    at runtime if none of the conditions are satisfied.  States with no
    outgoing edges at all are excluded (they are better reported by
    :func:`find_dead_states`).

    Returns:
        Sorted list of state ids with potentially incomplete condition coverage.
    """
    cfg = program.main
    g = cfg._graph
    incomplete: list[str] = []
    for node in g.nodes:
        out_edges = list(g.out_edges(node, data=True))
        if not out_edges:
            continue   # terminal / dead — reported elsewhere
        has_unconditional = any(d.get("cond") is None for _, _, d in out_edges)
        if not has_unconditional:
            incomplete.append(node)
    return sorted(incomplete)


# ---------------------------------------------------------------------------
# Linearization
# ---------------------------------------------------------------------------

def linearize(
    program: Program,
    strategy: Literal["rpo", "topological", "trace", "custom"] = "rpo",
    order: list[str] | None = None,
) -> FSMLayout:
    """Linearize the main FSM flow and produce an :class:`FSMLayout`.

    Each :class:`FSMSlot` in the result contains the :class:`BasicBlock` for
    a state and its outgoing transitions sorted by ``priority`` (ascending).

    Args:
        program:  The program whose main CFG is linearized.
        strategy: Block ordering — ``"rpo"`` (default) handles cycles;
                  ``"topological"`` raises if the main CFG contains a cycle;
                  ``"trace"`` uses priority-guided trace order;
                  ``"custom"`` uses *order* as a preference list (requires
                  *order* to be provided).
        order:    Preference order for ``strategy="custom"``.  Passed directly
                  to :meth:`~rpkbin.cfg.CFG.linearize`.  Ignored for other
                  strategies.

    Returns:
        :class:`FSMLayout` with slots in emission order.
    """
    cfg = program.main
    g = cfg._graph
    ordered_ids = cfg.linearize(strategy=strategy, order=order)

    slots: list[FSMSlot] = []
    for bid in ordered_ids:
        bb = cfg.get_block(bid)
        raw_edges = list(g.out_edges(bid, data=True))
        exits = sorted(
            [
                ExitEdge(
                    priority=d.get("priority", 0),
                    cond=d.get("cond"),
                    target=v,
                )
                for _, v, d in raw_edges
            ],
            key=lambda e: e.priority,
        )
        slots.append(FSMSlot(block=bb, exits=exits))

    return FSMLayout(slots=slots)
