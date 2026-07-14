"""Discovery and concrete execution of small pure LIR regions."""

from __future__ import annotations

from dataclasses import dataclass

from .lir import (
    Assign,
    BinOp,
    BitOp,
    Block,
    BrCmp,
    BrIf,
    Cmp,
    Const,
    Extend,
    Fragment,
    Function,
    Jump,
    MultiReturn,
    Return,
    Var,
    VReg,
)
from .register_alloc import terminator_live_before


class RegionError(ValueError):
    pass


@dataclass(frozen=True)
class BoundedRegion:
    entry_label: str
    exit_label: str
    blocks: tuple[Block, ...]
    state_widths: tuple[tuple[str, int], ...]
    observed: tuple[str, ...]


def _successors(block: Block) -> tuple[str, ...]:
    term = block.terminator
    if isinstance(term, (BrIf, BrCmp)):
        return term.true_label, term.false_label
    if isinstance(term, Jump):
        return (term.label,)
    return ()


def _expr_width(expr) -> int:
    if isinstance(expr, (Const, Var, VReg, BinOp, Extend)):
        return expr.width
    if isinstance(expr, (Cmp, BitOp)):
        return 1
    raise RegionError(f"effectful or unsupported expression: {expr!r}")


def _record_expr(expr, widths: dict[str, int]) -> None:
    if isinstance(expr, (Var, VReg)):
        prior = widths.setdefault(expr.name, expr.width)
        if prior != expr.width:
            raise RegionError(f"conflicting widths for {expr.name!r}")
        return
    if isinstance(expr, Const):
        return
    if isinstance(expr, (BinOp, Cmp)):
        _record_expr(expr.left, widths)
        _record_expr(expr.right, widths)
        return
    if isinstance(expr, Extend):
        _record_expr(expr.value, widths)
        return
    if isinstance(expr, BitOp) and expr.kind == "test":
        _record_expr(expr.var, widths)
        return
    raise RegionError(f"effectful or unsupported expression: {expr!r}")


def _record_block(block: Block, widths: dict[str, int]) -> None:
    for statement in block.statements:
        if not isinstance(statement, Assign) or statement.target.width <= 0:
            raise RegionError(f"effectful or unsupported statement: {statement!r}")
        _record_expr(statement.target, widths)
        _record_expr(statement.value, widths)
    term = block.terminator
    if isinstance(term, BrIf):
        _record_expr(term.cond, widths)
    elif isinstance(term, BrCmp):
        if term.op not in {"eq", "ne"}:
            raise RegionError("BrCmp relational signedness is not explicit")
        _record_expr(term.left, widths)
        _record_expr(term.right, widths)
    elif not isinstance(term, Jump):
        raise RegionError(f"region terminates before its exit: {term!r}")


def _record_exit(block: Block, widths: dict[str, int]) -> None:
    if block.statements:
        raise RegionError("bounded-region exit must begin at a block boundary")
    term = block.terminator
    if isinstance(term, Return):
        _record_expr(term.value, widths)
    elif isinstance(term, MultiReturn):
        for value in term.values:
            _record_expr(value, widths)
    elif isinstance(term, BrIf):
        _record_expr(term.cond, widths)
    elif isinstance(term, BrCmp):
        _record_expr(term.left, widths)
        _record_expr(term.right, widths)


def _region_nodes(
    entry: str,
    exit_label: str,
    successors: dict[str, tuple[str, ...]],
    predecessors: dict[str, set[str]],
    max_blocks: int,
) -> set[str] | None:
    nodes: set[str] = set()
    pending = [entry]
    while pending:
        label = pending.pop()
        if label == exit_label or label in nodes:
            continue
        nodes.add(label)
        if len(nodes) > max_blocks or not successors[label]:
            return None
        pending.extend(successors[label])

    if not any(exit_label in successors[label] for label in nodes):
        return None
    if any(
        predecessors[label] - nodes for label in nodes if label != entry
    ):
        return None

    visiting: set[str] = set()
    visited: set[str] = set()

    def acyclic(label: str) -> bool:
        if label == exit_label:
            return True
        if label in visiting:
            return False
        if label in visited:
            return True
        visiting.add(label)
        valid = all(
            successor == exit_label
            or successor in nodes and acyclic(successor)
            for successor in successors[label]
        )
        visiting.remove(label)
        visited.add(label)
        return valid

    return nodes if acyclic(entry) and visited == nodes else None


def discover_bounded_regions(
    unit: Function | Fragment, *, max_blocks: int = 4
) -> tuple[BoundedRegion, ...]:
    """Discover pure acyclic single-entry/single-exit regions, fail closed."""
    if max_blocks < 1:
        raise ValueError("max_blocks must be positive")
    blocks = tuple(unit.blocks)
    by_label = {block.label: block for block in blocks}
    if len(by_label) != len(blocks):
        raise RegionError("duplicate block labels")
    successors = {block.label: _successors(block) for block in blocks}
    predecessors = {block.label: set() for block in blocks}
    for label, targets in successors.items():
        for target in targets:
            if target not in predecessors:
                raise RegionError(f"missing successor block {target!r}")
            predecessors[target].add(label)

    live_before_term = terminator_live_before(unit)
    regions: list[BoundedRegion] = []
    for entry_block in blocks:
        for exit_block in blocks:
            if entry_block is exit_block:
                continue
            nodes = _region_nodes(
                entry_block.label,
                exit_block.label,
                successors,
                predecessors,
                max_blocks,
            )
            if nodes is None:
                continue
            selected = tuple(block for block in blocks if block.label in nodes)
            widths: dict[str, int] = {}
            try:
                for block in selected:
                    _record_block(block, widths)
                _record_exit(exit_block, widths)
            except RegionError:
                continue
            observed = tuple(sorted(live_before_term[exit_block.label]))
            if not observed or any(name not in widths for name in observed):
                continue
            regions.append(
                BoundedRegion(
                    entry_block.label,
                    exit_block.label,
                    selected,
                    tuple(sorted(widths.items())),
                    observed,
                )
            )
    return tuple(regions)


def _mask(width: int) -> int:
    return (1 << width) - 1


def _signed(value: int, width: int) -> int:
    value &= _mask(width)
    sign = 1 << (width - 1)
    return value - (1 << width) if value & sign else value


def _eval_expr(expr, state: dict[str, int]) -> int:
    if isinstance(expr, Const):
        return expr.value & _mask(expr.width)
    if isinstance(expr, (Var, VReg)):
        if expr.name not in state:
            raise RegionError(f"missing initial value for {expr.name!r}")
        return state[expr.name] & _mask(expr.width)
    if isinstance(expr, Extend):
        value = _eval_expr(expr.value, state)
        if expr.kind == "sext":
            value = _signed(value, _expr_width(expr.value))
        elif expr.kind != "zext":
            raise RegionError(f"unsupported extension {expr.kind!r}")
        return value & _mask(expr.width)
    if isinstance(expr, BinOp):
        left, right = _eval_expr(expr.left, state), _eval_expr(expr.right, state)
        width = expr.width
        operations = {
            "add": lambda: left + right,
            "sub": lambda: left - right,
            "mul": lambda: left * right,
            "and": lambda: left & right,
            "or": lambda: left | right,
            "xor": lambda: left ^ right,
            "shl": lambda: left << right,
            "shr": lambda: left >> right,
            "rol": lambda: (left << (right % width))
            | (left >> ((-right) % width)),
            "ror": lambda: (left >> (right % width))
            | (left << ((-right) % width)),
        }
        if expr.op not in operations:
            raise RegionError(f"unsupported pure operation {expr.op!r}")
        return operations[expr.op]() & _mask(width)
    if isinstance(expr, Cmp):
        left, right = _eval_expr(expr.left, state), _eval_expr(expr.right, state)
        if expr.signed:
            left = _signed(left, _expr_width(expr.left))
            right = _signed(right, _expr_width(expr.right))
        comparisons = {
            "eq": left == right,
            "ne": left != right,
            "lt": left < right,
            "le": left <= right,
            "gt": left > right,
            "ge": left >= right,
        }
        if expr.op not in comparisons:
            raise RegionError(f"unsupported comparison {expr.op!r}")
        return int(comparisons[expr.op])
    if isinstance(expr, BitOp) and expr.kind == "test":
        return (_eval_expr(expr.var, state) >> expr.bit_idx) & 1
    raise RegionError(f"effectful or unsupported expression: {expr!r}")


def execute_region(
    region: BoundedRegion, initial_state: dict[str, int]
) -> tuple[int, ...]:
    widths = dict(region.state_widths)
    try:
        state = {
            name: initial_state[name] & _mask(width)
            for name, width in region.state_widths
        }
    except KeyError as exc:
        raise RegionError(f"missing initial value for {exc.args[0]!r}") from exc
    by_label = {block.label: block for block in region.blocks}
    label = region.entry_label
    visited: set[str] = set()
    while label != region.exit_label:
        if label in visited or label not in by_label:
            raise RegionError("region execution escaped or encountered a cycle")
        visited.add(label)
        block = by_label[label]
        for statement in block.statements:
            state[statement.target.name] = _eval_expr(statement.value, state)
        term = block.terminator
        if isinstance(term, Jump):
            label = term.label
        elif isinstance(term, BrIf):
            label = (
                term.true_label if _eval_expr(term.cond, state) else term.false_label
            )
        elif isinstance(term, BrCmp):
            comparison = Cmp(term.op, term.left, term.right)
            label = (
                term.true_label if _eval_expr(comparison, state) else term.false_label
            )
        else:
            raise RegionError(f"region terminated before its exit: {term!r}")
    return tuple(state[name] & _mask(widths[name]) for name in region.observed)
