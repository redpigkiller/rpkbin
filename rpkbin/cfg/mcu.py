"""MCU analysis and linearization for CFG Programs.

This module targets MCU (Microcontroller) programs where:

* The *main* CFG represents a sequential instruction flow with an explicit
  halt/exit block (``set_exit()`` must be called on the main CFG).
* Subroutine CFGs are invoked via :class:`~rpkbin.cfg.block.CallRef` and have
  a designated return block (``set_exit()``).
* Control flow is expressed via CFG edges (the DSL/frontend emits if/else
  constructs; explicit jump instructions are **not** in the instruction list).

MCU-specific analysis
---------------------
``find_dead_loops``
    SCCs that are reachable from the program entry but have no path to the
    MCU's exit block.  Unlike FSM sink detection, MCU infinite loops are
    always bugs (the machine should eventually reach HALT).

``dead_code_elimination``
    Removes blocks unreachable from the entry.  Operates in-place on a
    single CFG and returns the list of removed blocks.

MCU linearization
-----------------
``linearize`` returns an :class:`MCULayout`, an ordered list of
:class:`MCUSlot` objects.  Each slot records whether a jump instruction must
be emitted after the block's instructions (because the next physical block is
not the natural fallthrough target).  The caller uses this to emit the correct
assembly without the tool needing to know the target ISA's jump mnemonic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import networkx as nx

from .block import BasicBlock
from .cfg import CFG
from .program import Program


# ---------------------------------------------------------------------------
# Linearization data structures
# ---------------------------------------------------------------------------

@dataclass
class MCUExitEdge:
    """One outgoing transition from an MCU code block.

    Attributes:
        priority:       Evaluation order (lower = higher priority).
        cond:           Condition string, or ``None`` for an unconditional /
                        default transition.
        target:         Target block id.
        is_fallthrough: ``True`` only when this edge's target is the block's
                        sole unconditional successor **and** it is the
                        physically next slot in the layout.  Conditional edges
                        are never marked as fallthrough — they always require
                        an explicit branch instruction regardless of adjacency.
    """

    priority: int
    cond: str | None
    target: str
    is_fallthrough: bool = False


@dataclass
class MCUSlot:
    """One entry in an :class:`MCULayout`.

    Attributes:
        block:       The :class:`BasicBlock` for this code region.
        needs_jump:  ``True`` if the block's last instruction must be followed
                     by an explicit jump (because the physically next slot is
                     not the block's sole unconditional successor).
        jump_target: The block id to jump to when ``needs_jump`` is ``True``.
                     ``None`` when ``needs_jump`` is ``False``.
        exits:       Outgoing transitions sorted by priority.  ``is_fallthrough``
                     marks the edge represented by physical adjacency.
    """

    block: BasicBlock
    needs_jump: bool = False
    jump_target: str | None = None
    exits: list[MCUExitEdge] = field(default_factory=list)


@dataclass
class MCULayout:
    """Result of :func:`linearize`.

    Attributes:
        slots: Ordered list of :class:`MCUSlot` objects representing the
               physical emission order of code blocks.
    """

    slots: list[MCUSlot] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unconditional_successor(cfg: CFG, block_id: str) -> str | None:
    """Return the sole unconditional successor of *block_id*, or ``None``.

    A block has an unconditional successor only when it has exactly one
    outgoing edge with ``cond=None``.  A single conditional edge is still
    conditional; treating it as fallthrough would discard the condition.
    """
    uncond = [
        dst for _, dst, attrs in cfg.out_edges(block_id)
        if attrs.get("cond") is None
    ]
    return uncond[0] if len(uncond) == 1 else None


# ---------------------------------------------------------------------------
# Public analysis API
# ---------------------------------------------------------------------------

def find_dead_loops(program: Program, exit_block: str | None = None) -> list[list[str]]:
    """Return SCCs reachable from entry but with no path to the exit block.

    In an MCU program, every execution path should eventually reach a halt /
    exit block.  An SCC (cycle) from which no node can reach the exit is an
    infinite loop — a bug.

    Args:
        program:    The program to analyse (only the main CFG is examined).
        exit_block: Block id of the halt / exit block.  If ``None``, falls
                    back to ``main_cfg._exit``.  At least one must be provided.

    Returns:
        List of dead loops; each loop is a sorted list of block ids.

    Raises:
        RuntimeError: If neither *exit_block* nor ``main_cfg._exit`` is set.
    """
    cfg = program.main
    entry_id = cfg._entry
    if entry_id is None:
        raise RuntimeError("Main CFG entry is not set.  Call set_entry() first.")

    exit_id = exit_block or cfg._exit
    if exit_id is None:
        raise RuntimeError(
            "exit_block must be provided (or set via CFG.set_exit()) "
            "for MCU dead-loop detection."
        )

    g = cfg._graph
    reachable_from_entry: set[str] = nx.descendants(g, entry_id) | {entry_id}

    rev = g.reverse()
    can_reach_exit: set[str] = nx.descendants(rev, exit_id) | {exit_id}

    dead: list[list[str]] = []
    for scc in cfg.find_sccs():
        scc_set = set(scc)

        # Only consider reachable SCCs
        if not scc_set.intersection(reachable_from_entry):
            continue

        # Must have a cycle (self-loop or multi-node)
        has_cycle = len(scc) > 1 or g.has_edge(scc[0], scc[0])
        if not has_cycle:
            continue

        # Dead = no node can reach exit
        if not scc_set.intersection(can_reach_exit):
            dead.append(sorted(scc))

    return dead


def dead_code_elimination(
    cfg: CFG,
    start: str | None = None,
) -> list[BasicBlock]:
    """Remove unreachable blocks from *cfg* **in place**.

    Args:
        cfg:   The :class:`CFG` to modify.
        start: Entry block id.  Defaults to ``cfg._entry``.

    Returns:
        The list of :class:`BasicBlock` objects removed from the graph.

    Raises:
        RuntimeError: If no entry is set and *start* is not provided.
    """
    dead = cfg.find_unreachable(start)
    for bb in dead:
        cfg.remove_block(bb.id)
    return dead


# ---------------------------------------------------------------------------
# Linearization
# ---------------------------------------------------------------------------

def linearize(
    program: Program,
    strategy: Literal["rpo", "topological", "trace"] = "rpo",
) -> MCULayout:
    """Linearize the main MCU flow and produce an :class:`MCULayout`.

    After ordering blocks, each :class:`MCUSlot` is annotated with
    ``needs_jump`` / ``jump_target`` so that the caller knows where to insert
    an explicit jump instruction:

    * If a block's sole unconditional successor is the **next** slot in the
      layout, the block *falls through* — ``needs_jump=False``.
    * Otherwise (the successor is not adjacent, or the block has multiple /
      no successors) the block needs an explicit jump to its unconditional
      successor target — ``needs_jump=True``.
    * Blocks with zero successors (e.g. the halt block) or multiple
      conditional successors do not emit a trailing jump.

    Args:
        program:  The program whose main CFG is linearized.
        strategy: Block ordering — ``"rpo"`` (default) handles cycles;
                  ``"topological"`` raises if the main CFG contains a cycle.

    Returns:
        :class:`MCULayout` with slots in emission order.
    """
    cfg = program.main
    ordered_ids = cfg.linearize(strategy=strategy)

    slots: list[MCUSlot] = []
    for i, bid in enumerate(ordered_ids):
        bb = cfg.get_block(bid)
        next_id = ordered_ids[i + 1] if i + 1 < len(ordered_ids) else None
        target = _unconditional_successor(cfg, bid)
        exits = [
            MCUExitEdge(
                priority=attrs.get("priority", 0),
                cond=attrs.get("cond"),
                target=dst,
                is_fallthrough=(dst == next_id and dst == target),
            )
            for _, dst, attrs in cfg.out_edges(bid)
        ]

        if target is not None and target != next_id:
            slots.append(MCUSlot(
                block=bb,
                needs_jump=True,
                jump_target=target,
                exits=exits,
            ))
        else:
            slots.append(MCUSlot(
                block=bb,
                needs_jump=False,
                jump_target=None,
                exits=exits,
            ))

    return MCULayout(slots=slots)
