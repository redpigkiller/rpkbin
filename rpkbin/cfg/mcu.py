"""MCU-oriented checks and layout recipes for CFG Programs.

This module is a target-neutral recipe layer for MCU (Microcontroller) style
flows. It assumes:

* The *main* CFG represents a sequential instruction flow with an explicit
  halt/exit block (``set_exit()`` must be called on the main CFG).
* Subroutine CFGs are invoked via :class:`~rpkbin.cfg.block.CallRef` and have
  a designated return block (``set_exit()``).
* Control flow is expressed via CFG edges (the DSL/frontend emits if/else
  constructs; explicit jump instructions are **not** in the instruction list).

MCU-specific checks
-------------------
``find_dead_loops``
    SCCs that are reachable from the program entry but have no path to the
    MCU's exit block.  Unlike FSM sink detection, MCU infinite loops are
    always bugs (the machine should eventually reach HALT).

``dead_code_elimination``
    Removes blocks unreachable from the entry.  Operates in-place on a
    single CFG and returns the list of removed blocks.

MCU layout
----------
``linearize`` returns an :class:`MCULayout`, an ordered list of
:class:`MCUSlot` objects.  Each slot records whether a jump instruction must
be emitted after the block's instructions (because the next physical block is
not the natural fallthrough target).  The caller uses these layout hints while
choosing target-specific branch mnemonics, condition forms, and jump syntax.

Edge layout hints
-----------------
The following optional edge attributes influence block ordering when the
matching ``fallthrough_policy`` is active.  All validation is deferred to
linearization time; :meth:`~rpkbin.cfg.CFG.add_edge` accepts them verbatim.

``layout_role`` (used by ``fallthrough_policy="layout"``)
    * ``"main"``   — prefer this edge's target as the physical successor.
    * ``"normal"`` — default; no special preference.
    * ``"cold"``   — prefer **not** to place this edge's target immediately
                     after the source block (e.g. cold reset / error paths).

``likelihood`` (used by ``fallthrough_policy="likelihood"``)
    * ``"likely"``   — this edge is taken most of the time at runtime.
    * ``"normal"``   — default; no special preference.
    * ``"unlikely"`` — this edge is rarely taken (cold path).

``weight`` (used by ``fallthrough_policy="weight"``)
    A non-negative ``int`` or ``float`` giving a *relative* execution-
    frequency score.  Higher values mean the edge is hotter.  The values
    need **not** sum to 1 — they are treated as relative scores, not
    probabilities.  Defaults to ``1.0`` when not set.

Physical fallthrough rule
--------------------------
Regardless of any layout hint or fallthrough policy, an :class:`MCUExitEdge`
is marked ``is_fallthrough=True`` only when **both** conditions hold:

1. ``edge.cond is None``  (unconditional edge)
2. ``edge.target == next_slot_id``

Conditional edges are **never** marked as fallthrough.  No branch inversion
is performed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import networkx as nx

from .block import BasicBlock
from .cfg import CFG
from .program import Program


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_VALID_LAYOUT_ROLES: frozenset[str] = frozenset({"main", "normal", "cold"})
_VALID_LIKELIHOODS: frozenset[str] = frozenset({"likely", "normal", "unlikely"})

# Ordering maps: lower rank → preferred first.
_LAYOUT_ROLE_RANK: dict[str, int] = {"main": 0, "normal": 1, "cold": 2}
_LIKELIHOOD_RANK: dict[str, int] = {"likely": 0, "normal": 1, "unlikely": 2}


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
        is_fallthrough: ``True`` only when this edge has ``cond=None`` **and**
                        its target is the physically next slot in the layout.
                        Conditional edges are *never* marked as fallthrough —
                        they always require an explicit branch instruction
                        regardless of physical adjacency.
        layout_role:    Value of the ``layout_role`` edge attribute.
                        Defaults to ``"normal"`` when not set on the edge.
        likelihood:     Value of the ``likelihood`` edge attribute.
                        Defaults to ``"normal"`` when not set on the edge.
        weight:         Value of the ``weight`` edge attribute.
                        Defaults to ``1.0`` when not set on the edge.
    """

    priority: int
    cond: str | None
    target: str
    is_fallthrough: bool = False
    layout_role: str = "normal"
    likelihood: str = "normal"
    weight: float = 1.0


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
# Internal helpers — validation
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


def _validate_attr(
    cfg: CFG,
    ordered_ids: list[str],
    attr: str,
    valid_values: frozenset[str],
) -> None:
    """Raise :class:`ValueError` if any edge carries an invalid string *attr*.

    Only edges of blocks in *ordered_ids* are examined.  Called at
    linearization time when the relevant policy is active.
    """
    for bid in ordered_ids:
        for _, dst, attrs in cfg.out_edges(bid):
            val = attrs.get(attr)
            if val is not None and val not in valid_values:
                raise ValueError(
                    f"Edge {bid!r} -> {dst!r} has invalid {attr} "
                    f"{val!r}. Valid values: {sorted(valid_values)}"
                )


def _validate_weights(cfg: CFG, ordered_ids: list[str]) -> None:
    """Raise :class:`ValueError` if any edge has an invalid ``weight`` value.

    ``weight`` must be an ``int`` or ``float`` and must be ``>= 0``.
    """
    for bid in ordered_ids:
        for _, dst, attrs in cfg.out_edges(bid):
            w = attrs.get("weight")
            if w is None:
                continue
            if not isinstance(w, (int, float)):
                raise ValueError(
                    f"Edge {bid!r} -> {dst!r} has invalid weight {w!r}: "
                    "must be int or float."
                )
            if w < 0:
                raise ValueError(
                    f"Edge {bid!r} -> {dst!r} has invalid weight {w!r}: "
                    "must be >= 0."
                )


# ---------------------------------------------------------------------------
# Internal helpers — greedy reorder
# ---------------------------------------------------------------------------

def _successor_score(
    attrs: dict,
    pos: dict[str, int],
    dst: str,
    n: int,
    policy: str,
) -> tuple:
    """Return a sort key for ranking successors under a given *policy*.

    Lower score = higher preference (the successor should come next).

    The tuple is always comparable and deterministic:
    ``(primary_rank, secondary_rank, tertiary_rank, base_order_pos, target_id)``
    """
    role_rank = _LAYOUT_ROLE_RANK.get(attrs.get("layout_role", "normal"), 1)
    like_rank = _LIKELIHOOD_RANK.get(attrs.get("likelihood", "normal"), 1)
    # weight: higher weight → lower rank (negate for ascending sort).
    # Use safe conversion: non-numeric weight falls back to 1.0 here
    # (validation is handled separately by _validate_weights when needed).
    raw_w = attrs.get("weight", 1.0)
    w = float(raw_w) if isinstance(raw_w, (int, float)) else 1.0
    weight_rank = -w          # negated: higher weight preferred
    prio = attrs.get("priority", 0)
    base_pos = pos.get(dst, n)

    if policy == "layout":
        return (role_rank, like_rank, weight_rank, prio, base_pos, dst)
    elif policy == "likelihood":
        return (like_rank, prio, base_pos, dst)
    elif policy == "weight":
        return (weight_rank, like_rank, role_rank, prio, base_pos, dst)
    else:
        # "default": unconditional edges first, then priority, then base_pos
        cond_rank = 0 if attrs.get("cond") is None else 1
        return (cond_rank, prio, base_pos, dst)


def _best_successor(
    cfg: CFG,
    bid: str,
    pending: set[str],
    pos: dict[str, int],
    n: int,
    policy: str,
) -> str | None:
    """Return the best pending successor of *bid* for *policy*, or ``None``.

    For ``policy="default"``, only ``cond=None`` targets qualify.
    For other policies, any pending successor qualifies (ranked by score).
    """
    candidates = []
    for _, dst, attrs in cfg.out_edges(bid):
        if dst not in pending:
            continue
        if policy == "default" and attrs.get("cond") is not None:
            continue  # "default" only pulls unconditional successors
        score = _successor_score(attrs, pos, dst, n, policy)
        candidates.append((score, dst))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _greedy_reorder(
    cfg: CFG,
    base_order: list[str],
    policy: str,
) -> list[str]:
    """Conservative greedy reorder driven by *policy*.

    Algorithm
    ---------
    1. Identify "cold-only" blocks (only reached via cold/unlikely/low-weight
       edges, never via main/likely/high-weight) and defer them to the end.
       The first block in *base_order* (typically the entry) is never deferred.
    2. Emit blocks in a greedy chain: after emitting block *B*, try to pull
       the best-scored pending successor forward.  Repeat until the chain
       stalls, then pick the next un-emitted non-deferred block from
       *base_order*.
    3. Emit deferred blocks at the end in *base_order* order.

    Guarantees
    ----------
    * Every block in *base_order* appears exactly once in the result.
    * No block is duplicated or dropped.
    * Unreachable blocks are neither added nor removed.
    * The CFG is not modified.

    These policies are best-effort heuristics and do **not** guarantee a
    globally optimal layout.
    """
    n = len(base_order)
    pos: dict[str, int] = {bid: i for i, bid in enumerate(base_order)}
    pending: set[str] = set(base_order)
    result: list[str] = []

    # -----------------------------------------------------------------
    # Build the "deferred" (cold-only) set — policy-dependent.
    # Blocks that are exclusively targeted by cold/unlikely/low-weight
    # edges are deferred so they don't interrupt hot chains.
    # The entry block is never deferred.
    # -----------------------------------------------------------------
    entry_block = base_order[0]
    hot_targets: set[str] = set()
    cold_targets: set[str] = set()

    for bid in base_order:
        for _, dst, attrs in cfg.out_edges(bid):
            if policy == "layout":
                role = attrs.get("layout_role", "normal")
                if role in ("main", "normal"):
                    hot_targets.add(dst)
                elif role == "cold":
                    cold_targets.add(dst)
            elif policy == "likelihood":
                like = attrs.get("likelihood", "normal")
                if like in ("likely", "normal"):
                    hot_targets.add(dst)
                elif like == "unlikely":
                    cold_targets.add(dst)
            elif policy == "weight":
                # Use median-ish heuristic: weight < 0.5 of max weight from
                # that src counts as cold.  Simple: weight=0 → cold.
                w = float(attrs.get("weight", 1.0))
                if w > 0:
                    hot_targets.add(dst)
                else:
                    cold_targets.add(dst)
            else:
                # "default": no deferral — all successors treated equally
                hot_targets.add(dst)

    deferred: set[str] = (cold_targets - hot_targets) - {entry_block}

    # -----------------------------------------------------------------
    # Greedy emit loop
    # -----------------------------------------------------------------
    i = 0
    base_list = list(base_order)

    while pending:
        # Pick next non-deferred block from base_order.
        candidate: str | None = None
        for j in range(i, len(base_list)):
            bid = base_list[j]
            if bid in pending and bid not in deferred:
                candidate = bid
                i = j + 1
                break

        if candidate is None:
            # Only deferred blocks remain — emit in base_order order.
            for bid in base_list:
                if bid in pending:
                    result.append(bid)
                    pending.discard(bid)
            break

        result.append(candidate)
        pending.discard(candidate)

        # Chain: greedily pull the best successor forward.
        cur = candidate
        while True:
            best = _best_successor(cfg, cur, pending, pos, n, policy)
            if best is None:
                break
            result.append(best)
            pending.discard(best)
            cur = best

    return result


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


def _safe_weight(val: object) -> float:
    """Convert *val* to float for use in :class:`MCUExitEdge`.

    When the active ``fallthrough_policy`` is not ``"weight"``, invalid weight
    values are not validated and this helper returns ``1.0`` as a safe default
    so that slot construction does not raise.  Validation for the ``"weight"``
    policy is performed earlier by :func:`_validate_weights`.
    """
    if isinstance(val, (int, float)):
        return float(val)
    return 1.0  # non-numeric → safe default (will be caught by _validate_weights if needed)


# ---------------------------------------------------------------------------
# Linearization
# ---------------------------------------------------------------------------

def linearize(
    program: Program,
    strategy: Literal["rpo", "topological", "trace", "custom"] = "rpo",
    order: list[str] | None = None,
    fallthrough_policy: Literal[
        "none", "default", "layout", "likelihood", "weight"
    ] = "none",
) -> MCULayout:
    """Linearize the main MCU flow and produce an :class:`MCULayout`.

    After ordering blocks, each :class:`MCUSlot` is annotated with
    ``needs_jump`` / ``jump_target`` so that the caller knows where to insert
    an explicit jump instruction:

    * If a block's unconditional (``cond=None``) successor is the **next**
      slot in the layout, that edge is physical fallthrough —
      ``needs_jump=False``.
    * If the unconditional successor is not adjacent, the block needs an
      explicit jump — ``needs_jump=True``.
    * Blocks with no unconditional successor (terminal or purely conditional)
      do not emit a trailing jump.

    **Physical fallthrough rule**: ``MCUExitEdge.is_fallthrough`` is set
    ``True`` only when ``edge.cond is None`` *and* ``edge.target`` equals the
    next slot.  Conditional edges are **never** marked as fallthrough.  No
    branch inversion is performed regardless of layout hints.

    The ``fallthrough_policy`` parameter controls block-ordering heuristics.
    All policies are conservative greedy passes and do **not** guarantee a
    globally optimal layout.

    Args:
        program:  The program whose main CFG is linearized.
        strategy: Block ordering strategy.  ``"rpo"`` (default) handles
                  cycles; ``"topological"`` raises on cycles; ``"trace"``
                  uses priority-guided tracing; ``"custom"`` uses *order*.
        order:    Preference list for ``strategy="custom"``.  Passed directly
                  to :meth:`~rpkbin.cfg.CFG.linearize`.
        fallthrough_policy: Post-ordering heuristic:

            * ``"none"`` (default) — no adjustments; preserves existing
              behaviour exactly.
            * ``"default"`` — prefer placing each block's ``cond=None``
              successor immediately after it.
            * ``"layout"`` — rank successors by ``layout_role``
              (``"main"`` > ``"normal"`` > ``"cold"``).  Validates
              ``layout_role`` values and raises :class:`ValueError` on
              invalid ones.
            * ``"likelihood"`` — rank successors by ``likelihood``
              (``"likely"`` > ``"normal"`` > ``"unlikely"``).  Validates
              ``likelihood`` values.
            * ``"weight"`` — rank successors by ``weight`` (higher = hotter).
              Validates ``weight`` values (must be ``int``/``float``,
              ``>= 0``).

    Returns:
        :class:`MCULayout` with slots in emission order.

    Raises:
        ValueError: On invalid edge attribute values when the corresponding
                    policy is active, or on unknown *fallthrough_policy*.
    """
    cfg = program.main
    ordered_ids = cfg.linearize(strategy=strategy, order=order)

    # Validate and apply fallthrough policy.
    if fallthrough_policy == "none":
        pass  # no reordering
    elif fallthrough_policy in ("default", "layout", "likelihood", "weight"):
        # Per-policy validation (only attributes used by the active policy).
        if fallthrough_policy == "layout":
            _validate_attr(cfg, ordered_ids, "layout_role", _VALID_LAYOUT_ROLES)
        elif fallthrough_policy == "likelihood":
            _validate_attr(cfg, ordered_ids, "likelihood", _VALID_LIKELIHOODS)
        elif fallthrough_policy == "weight":
            _validate_weights(cfg, ordered_ids)
        ordered_ids = _greedy_reorder(cfg, ordered_ids, fallthrough_policy)
    else:
        raise ValueError(
            f"Unknown fallthrough_policy {fallthrough_policy!r}. "
            "Valid values: 'none', 'default', 'layout', 'likelihood', 'weight'."
        )

    # Build MCULayout slots.
    slots: list[MCUSlot] = []
    for i, bid in enumerate(ordered_ids):
        bb = cfg.get_block(bid)
        next_id = ordered_ids[i + 1] if i + 1 < len(ordered_ids) else None
        # Only a sole cond=None edge can be a fallthrough.
        uncond_target = _unconditional_successor(cfg, bid)

        exits = sorted(
            [
                MCUExitEdge(
                    priority=attrs.get("priority", 0),
                    cond=attrs.get("cond"),
                    target=dst,
                    # is_fallthrough: strictly cond=None AND target == next slot.
                    # Conditional edges are NEVER fallthrough (no branch inversion).
                    is_fallthrough=(
                        attrs.get("cond") is None
                        and dst == next_id
                        and dst == uncond_target
                    ),
                    layout_role=attrs.get("layout_role", "normal"),
                    likelihood=attrs.get("likelihood", "normal"),
                    weight=_safe_weight(attrs.get("weight", 1.0)),
                )
                for _, dst, attrs in cfg.out_edges(bid)
            ],
            key=lambda e: e.priority,
        )

        if uncond_target is not None and uncond_target != next_id:
            slots.append(MCUSlot(
                block=bb,
                needs_jump=True,
                jump_target=uncond_target,
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
