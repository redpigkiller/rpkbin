"""Structural diff and equality for CFG / Program objects.

This module compares graph structure and caller-owned annotations as stored.
It does **not** perform semantic equivalence checking.

Public API
----------

* :class:`BlockDelta`   — changed block record
* :class:`EdgeDelta`    — changed edge record
* :class:`CFGDiffResult`     — result of :func:`diff_cfgs`
* :class:`ProgramDiffResult` — result of :func:`diff_programs`
* :func:`diff_cfgs`          — compare two :class:`~rpkbin.cfg.CFG` objects
* :func:`diff_programs`      — compare two :class:`~rpkbin.cfg.program.Program` objects
* :func:`cfg_structurally_equal`     — convenience boolean wrapper
* :func:`program_structurally_equal` — convenience boolean wrapper
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .block import CallRef
from .cfg import CFG
from .program import Program


# ---------------------------------------------------------------------------
# Delta dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BlockDelta:
    """Records a difference between two aligned blocks.

    Attributes:
        old_id:         Block id in the *old* CFG.
        new_id:         Block id in the *new* CFG.
        old_label:      Label in the *old* block (may be ``None``).
        new_label:      Label in the *new* block (may be ``None``).
        insns_changed:  ``True`` when the instruction lists differ and
                        ``compare_insns=True`` was requested.
        meta_changed:   ``True`` when the ``meta`` dicts differ and
                        ``compare_meta=True`` was requested.
    """

    old_id: str
    new_id: str
    old_label: str | None
    new_label: str | None
    insns_changed: bool = False
    meta_changed: bool = False


@dataclass
class EdgeDelta:
    """Records a difference between two aligned edges that share the same key.

    Attributes:
        src:       Aligned source block key.
        dst:       Aligned destination block key.
        old_attrs: Full attribute dict from the *old* CFG edge.
        new_attrs: Full attribute dict from the *new* CFG edge.
    """

    src: str
    dst: str
    old_attrs: dict[str, Any]
    new_attrs: dict[str, Any]


@dataclass
class CFGDiffResult:
    """Result of :func:`diff_cfgs`.

    All block / edge keys use the *aligned key* (``block.id`` when
    ``align_by="id"``, ``block.label`` when ``align_by="label"``).

    Attributes:
        added_blocks:   Keys present in *new* but not in *old*.
        removed_blocks: Keys present in *old* but not in *new*.
        changed_blocks: Aligned blocks that differ (see :class:`BlockDelta`).
        added_edges:    ``(src_key, dst_key)`` pairs in *new* but not in *old*.
        removed_edges:  ``(src_key, dst_key)`` pairs in *old* but not in *new*.
        changed_edges:  Edge keys whose attributes changed.
        added_calls:    ``(block_key, callee)`` call relationships in *new* only.
        removed_calls:  ``(block_key, callee)`` call relationships in *old* only.
        entry_changed:  Whether the aligned entry designation changed.
        old_entry:      Old aligned entry key, or ``None``.
        new_entry:      New aligned entry key, or ``None``.
        exit_changed:   Whether the aligned exit designation changed.
        old_exit:       Old aligned exit key, or ``None``.
        new_exit:       New aligned exit key, or ``None``.
    """

    added_blocks: set[str] = field(default_factory=set)
    removed_blocks: set[str] = field(default_factory=set)
    changed_blocks: dict[str, BlockDelta] = field(default_factory=dict)
    added_edges: set[tuple[str, str]] = field(default_factory=set)
    removed_edges: set[tuple[str, str]] = field(default_factory=set)
    changed_edges: dict[tuple[str, str], EdgeDelta] = field(default_factory=dict)
    added_calls: set[tuple[str, str]] = field(default_factory=set)
    removed_calls: set[tuple[str, str]] = field(default_factory=set)
    entry_changed: bool = False
    old_entry: str | None = None
    new_entry: str | None = None
    exit_changed: bool = False
    old_exit: str | None = None
    new_exit: str | None = None

    def has_changes(self) -> bool:
        """Return ``True`` if any structural difference was recorded."""
        return bool(
            self.added_blocks
            or self.removed_blocks
            or self.changed_blocks
            or self.added_edges
            or self.removed_edges
            or self.changed_edges
            or self.added_calls
            or self.removed_calls
            or self.entry_changed
            or self.exit_changed
        )


@dataclass
class ProgramDiffResult:
    """Result of :func:`diff_programs`.

    Attributes:
        entry_fn_changed:  ``True`` when the two programs have different entry
                           function names.
        old_entry_fn:      Old entry name, set only when *entry_fn_changed*.
        new_entry_fn:      New entry name, set only when *entry_fn_changed*.
        added_functions:   Function names present in *new* but not in *old*.
        removed_functions: Function names present in *old* but not in *new*.
        changed_functions: Functions whose :class:`CFGDiffResult` has changes.
    """

    entry_fn_changed: bool = False
    old_entry_fn: str | None = None
    new_entry_fn: str | None = None
    added_functions: set[str] = field(default_factory=set)
    removed_functions: set[str] = field(default_factory=set)
    changed_functions: dict[str, CFGDiffResult] = field(default_factory=dict)

    def has_changes(self) -> bool:
        """Return ``True`` if any structural difference was recorded."""
        return bool(
            self.entry_fn_changed
            or self.added_functions
            or self.removed_functions
            or self.changed_functions
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_key_map(
    cfg: CFG,
    align_by: Literal["id", "label"],
) -> dict[str, str]:
    """Return ``{aligned_key: block_id}`` for every block in *cfg*.

    Raises:
        ValueError: If ``align_by="label"`` and any block has ``label=None``
                    or there are duplicate labels.
    """
    key_to_id: dict[str, str] = {}
    if align_by == "id":
        for bb in cfg.blocks:
            key_to_id[bb.id] = bb.id
    else:  # align_by == "label"
        for bb in cfg.blocks:
            if bb.label is None:
                raise ValueError(
                    f"align_by='label' requires all blocks to have a label, "
                    f"but block {bb.id!r} has label=None."
                )
            if bb.label in key_to_id:
                raise ValueError(
                    f"align_by='label' found duplicate label {bb.label!r} "
                    f"on blocks {key_to_id[bb.label]!r} and {bb.id!r}."
                )
            key_to_id[bb.label] = bb.id
    return key_to_id


def _extract_calls(
    cfg: CFG,
    key_map: dict[str, str],
) -> set[tuple[str, str]]:
    """Return ``{(block_key, callee)}`` for all CallRef insns in *cfg*."""
    # Build reverse: block_id -> aligned key
    id_to_key: dict[str, str] = {}
    for key, bid in key_map.items():
        id_to_key[bid] = key

    calls: set[tuple[str, str]] = set()
    for bb in cfg.blocks:
        block_key = id_to_key.get(bb.id)
        if block_key is None:
            continue
        for insn in bb.insns:
            if isinstance(insn, CallRef):
                calls.add((block_key, insn.callee))
    return calls


def _build_edge_map(
    cfg: CFG,
    key_map: dict[str, str],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return ``{(src_key, dst_key): attrs}`` for every edge in *cfg*."""
    id_to_key: dict[str, str] = {bid: k for k, bid in key_map.items()}
    edge_map: dict[tuple[str, str], dict[str, Any]] = {}
    for src_id, dst_id, attrs in cfg.edges:
        src_key = id_to_key.get(src_id)
        dst_key = id_to_key.get(dst_id)
        if src_key is None or dst_key is None:
            # Edges to/from blocks outside the aligned key set — skip
            continue
        edge_map[(src_key, dst_key)] = dict(attrs)
    return edge_map


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_cfgs(
    old: CFG,
    new: CFG,
    *,
    align_by: Literal["id", "label"] = "id",
    compare_meta: bool = False,
    compare_insns: bool = True,
    compare_edge_attrs: bool = True,
) -> CFGDiffResult:
    """Compare two :class:`~rpkbin.cfg.CFG` objects structurally.

    Args:
        old:               The baseline CFG.
        new:               The CFG to compare against *old*.
        align_by:          How blocks are matched across the two CFGs.
                           ``"id"`` (default) uses ``block.id``;
                           ``"label"`` uses ``block.label`` (every block must
                           have a non-``None``, unique label).
        compare_meta:      When ``True``, include ``meta`` dict differences in
                           :attr:`CFGDiffResult.changed_blocks`.
        compare_insns:     When ``True`` (default), include instruction-list
                           differences in :attr:`CFGDiffResult.changed_blocks`.
                           CallRef relationships are always compared regardless
                           of this flag.
        compare_edge_attrs: When ``True`` (default), detect edges whose
                            attribute dicts changed.

    Returns:
        A :class:`CFGDiffResult`.  Call :meth:`CFGDiffResult.has_changes` to
        test for any difference.

    Raises:
        ValueError: If ``align_by="label"`` and any block is missing a label
                    or has a duplicate label within either CFG.
                    Also raises if ``align_by`` is not ``"id"`` or ``"label"``.
    """
    if align_by not in ("id", "label"):
        raise ValueError(f"align_by must be 'id' or 'label', got {align_by!r}")

    result = CFGDiffResult()

    old_keys = _build_key_map(old, align_by)  # key -> old_id
    new_keys = _build_key_map(new, align_by)  # key -> new_id

    old_id_to_key = {block_id: key for key, block_id in old_keys.items()}
    new_id_to_key = {block_id: key for key, block_id in new_keys.items()}
    old_entry = old_id_to_key.get(old.entry_id) if old.entry_id is not None else None
    new_entry = new_id_to_key.get(new.entry_id) if new.entry_id is not None else None
    old_exit = old_id_to_key.get(old.exit_id) if old.exit_id is not None else None
    new_exit = new_id_to_key.get(new.exit_id) if new.exit_id is not None else None
    if old_entry != new_entry:
        result.entry_changed, result.old_entry, result.new_entry = True, old_entry, new_entry
    if old_exit != new_exit:
        result.exit_changed, result.old_exit, result.new_exit = True, old_exit, new_exit

    old_key_set = set(old_keys)
    new_key_set = set(new_keys)

    result.added_blocks   = new_key_set - old_key_set
    result.removed_blocks = old_key_set - new_key_set

    # Compare aligned blocks
    for key in old_key_set & new_key_set:
        old_bb = old.get_block(old_keys[key])
        new_bb = new.get_block(new_keys[key])

        label_changed  = old_bb.label != new_bb.label
        insns_changed  = compare_insns  and old_bb.insns != new_bb.insns
        meta_changed   = compare_meta   and old_bb.meta  != new_bb.meta

        if label_changed or insns_changed or meta_changed:
            result.changed_blocks[key] = BlockDelta(
                old_id=old_bb.id,
                new_id=new_bb.id,
                old_label=old_bb.label,
                new_label=new_bb.label,
                insns_changed=insns_changed,
                meta_changed=meta_changed,
            )

    # Edges
    old_edges = _build_edge_map(old, old_keys)
    new_edges = _build_edge_map(new, new_keys)

    old_edge_set = set(old_edges)
    new_edge_set = set(new_edges)

    result.added_edges   = new_edge_set - old_edge_set
    result.removed_edges = old_edge_set - new_edge_set

    if compare_edge_attrs:
        for edge_key in old_edge_set & new_edge_set:
            if old_edges[edge_key] != new_edges[edge_key]:
                src, dst = edge_key
                result.changed_edges[edge_key] = EdgeDelta(
                    src=src,
                    dst=dst,
                    old_attrs=old_edges[edge_key],
                    new_attrs=new_edges[edge_key],
                )

    # CallRef relationships — always compared regardless of compare_insns
    old_calls = _extract_calls(old, old_keys)
    new_calls = _extract_calls(new, new_keys)

    result.added_calls   = new_calls - old_calls
    result.removed_calls = old_calls - new_calls

    return result


def cfg_structurally_equal(old: CFG, new: CFG, **kwargs: Any) -> bool:
    """Return ``True`` if *old* and *new* are structurally equal.

    All keyword arguments are forwarded to :func:`diff_cfgs`.
    """
    return not diff_cfgs(old, new, **kwargs).has_changes()


def diff_programs(
    old: Program,
    new: Program,
    *,
    align_by: Literal["id", "label"] = "id",
    compare_meta: bool = False,
    compare_insns: bool = True,
    compare_edge_attrs: bool = True,
) -> ProgramDiffResult:
    """Compare two :class:`~rpkbin.cfg.program.Program` objects structurally.

    Functions are matched by their exact name (key in ``program.cfgs``).
    There is no function-name alignment; a renamed function appears as a
    removal + addition.

    Args:
        old:  The baseline program.
        new:  The program to compare against *old*.

    Remaining keyword arguments are forwarded to :func:`diff_cfgs` for each
    pair of common functions.

    Returns:
        A :class:`ProgramDiffResult`.
    """
    if align_by not in ("id", "label"):
        raise ValueError(f"align_by must be 'id' or 'label', got {align_by!r}")

    result = ProgramDiffResult()

    if old.entry_fn != new.entry_fn:
        result.entry_fn_changed = True
        result.old_entry_fn = old.entry_fn
        result.new_entry_fn = new.entry_fn

    old_fns = set(old.cfgs)
    new_fns = set(new.cfgs)

    result.added_functions   = new_fns - old_fns
    result.removed_functions = old_fns - new_fns

    for fn_name in old.cfgs:
        if fn_name in new.cfgs:
            cfg_diff = diff_cfgs(
                old.cfgs[fn_name], new.cfgs[fn_name], align_by=align_by,
                compare_meta=compare_meta, compare_insns=compare_insns,
                compare_edge_attrs=compare_edge_attrs,
            )
            if cfg_diff.has_changes():
                result.changed_functions[fn_name] = cfg_diff

    return result


def program_structurally_equal(old: Program, new: Program, **kwargs: Any) -> bool:
    """Return ``True`` if *old* and *new* are structurally equal.

    All keyword arguments are forwarded to :func:`diff_programs`.
    """
    return not diff_programs(old, new, **kwargs).has_changes()
