"""Pattern rewrite pass for pure LIR expression trees.

Any subtree containing ``Call``, volatile ``MemLoad``, ``InlineAsmExpr``, or
``SymbolAddr`` is an opaque rewrite boundary.  Statement effects such as
``MemStore`` and side-effecting ``BitOp`` are also passed through unchanged.
Rewrites are ordered and run to a fixpoint, with exact-cycle detection plus
per-expression transition and node budgets. Candidates are node-counted before
the next state is hashed, bounding malformed growth rules in the rewrite pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .ir import Assign, BinOp, Block, BrIf, Cmp, Function, InlineAsmExpr, MemLoad, Return
from .lir import BitOp, BrCmp, Call, Extend, Fragment, FullExpr, SymbolAddr
from .matcher import build_expr, match_expr
from .patterns import RewritePattern


DEFAULT_REWRITE_STEP_BUDGET = 256
DEFAULT_REWRITE_NODE_BUDGET = 16_384


@dataclass(frozen=True)
class RewriteResult:
    function: Function
    applied: Sequence[str]


class RewriteConvergenceError(ValueError):
    """Raised when rewrite rules cycle or exceed the per-expression safety budget."""


def _rewrite_block(
    block: Block,
    patterns: Sequence[RewritePattern],
    applied: List[str],
    max_steps: int,
    max_nodes: int,
) -> Block:
    """Rewrite expressions in a single block's statements and terminator.

    Shared helper used by both ``rewrite_function`` and ``rewrite_fragment``.
    ``FragmentExit`` is left untouched.  ``InlineAsmExpr``, ``MemLoad``,
    and ``SymbolAddr`` are never rewritten (handled by ``_rewrite_expr_once``).
    """
    new_statements = []
    for stmt in block.statements:
        if isinstance(stmt, Assign):
            value = _rewrite_expr_fixpoint(stmt.value, patterns, applied, max_steps, max_nodes)
            new_statements.append(Assign(stmt.target, value))
        else:
            new_statements.append(stmt)

    term = block.terminator
    if isinstance(term, BrIf):
        new_cond = _rewrite_expr_fixpoint(term.cond, patterns, applied, max_steps, max_nodes)
        term = BrIf(new_cond, term.true_label, term.false_label)
    elif isinstance(term, Return):
        new_value = _rewrite_expr_fixpoint(term.value, patterns, applied, max_steps, max_nodes)
        term = Return(new_value)
    elif isinstance(term, BrCmp):
        new_left = _rewrite_expr_fixpoint(term.left, patterns, applied, max_steps, max_nodes)
        new_right = _rewrite_expr_fixpoint(term.right, patterns, applied, max_steps, max_nodes)
        term = BrCmp(term.op, new_left, new_right, term.true_label, term.false_label)

    return Block(block.label, tuple(new_statements), term)


def rewrite_function(
    func: Function,
    patterns: Iterable[RewritePattern],
    *,
    max_steps: int = DEFAULT_REWRITE_STEP_BUDGET,
    max_nodes: int = DEFAULT_REWRITE_NODE_BUDGET,
) -> RewriteResult:
    """Rewrite pure expressions with 256 transitions and 16,384 nodes each."""
    _validate_budget("max_steps", max_steps)
    _validate_budget("max_nodes", max_nodes)
    pattern_list = list(patterns)
    applied: List[str] = []
    new_blocks = [_rewrite_block(b, pattern_list, applied, max_steps, max_nodes) for b in func.blocks]
    return RewriteResult(Function(func.name, tuple(func.params), tuple(new_blocks)), tuple(applied))


def rewrite_fragment(
    fragment: Fragment,
    patterns: Iterable[RewritePattern],
    *,
    max_steps: int = DEFAULT_REWRITE_STEP_BUDGET,
    max_nodes: int = DEFAULT_REWRITE_NODE_BUDGET,
) -> tuple[Fragment, Sequence[str]]:
    """Rewrite expression trees inside a LIR Fragment.

    Preserves fragment metadata (name, bindings, scratch_regs).
    ``FragmentExit`` terminators are left unchanged.
    Opaque/volatile expressions (``InlineAsmExpr``, ``MemLoad``, ``SymbolAddr``)
    are not rewritten.

    This function reuses the same expression rewrite semantics
    (``_rewrite_expr_fixpoint``) as ``rewrite_function`` — no fragment-specific
    pattern schema is needed.
    """
    _validate_budget("max_steps", max_steps)
    _validate_budget("max_nodes", max_nodes)
    pattern_list = list(patterns)
    applied: List[str] = []
    new_blocks = [_rewrite_block(b, pattern_list, applied, max_steps, max_nodes) for b in fragment.blocks]
    new_fragment = Fragment(
        name=fragment.name,
        bindings=fragment.bindings,
        scratch_regs=fragment.scratch_regs,
        blocks=tuple(new_blocks),
    )
    return new_fragment, tuple(applied)


def _rewrite_expr_fixpoint(
    expr: FullExpr,
    patterns: Sequence[RewritePattern],
    applied: List[str],
    max_steps: int,
    max_nodes: int,
) -> FullExpr:
    current = expr
    current_nodes = _expression_size(current)
    _raise_node_budget(current_nodes, max_nodes, 0, "<initial>", 0, max_steps)
    seen: dict[FullExpr, int] = {current: 0}
    local_applied: list[str] = []
    steps = 0
    while True:
        next_expr, pattern_names = _rewrite_expr_once(current, patterns)
        if next_expr == current:
            return current
        next_nodes = _expression_size(next_expr)
        last_rule = pattern_names[-1] if pattern_names else "<none>"
        _raise_node_budget(next_nodes, max_nodes, steps, last_rule, len(seen), max_steps)
        if next_expr in seen:
            cycle_start = seen[next_expr]
            cycle_names = " -> ".join(local_applied[cycle_start:] + list(pattern_names))
            raise RewriteConvergenceError(
                "rewrite did not converge: expression cycle detected "
                f"({cycle_names or 'unnamed rewrite cycle'})"
            )
        if steps >= max_steps:
            raise RewriteConvergenceError(
                "rewrite did not converge: step budget exceeded "
                f"(steps={steps}, max_steps={max_steps}, last_rule={last_rule}, "
                f"expression_nodes={current_nodes}, max_nodes={max_nodes}, "
                f"seen_states={len(seen)})"
            )
        applied.extend(pattern_names)
        local_applied.extend(pattern_names)
        seen[next_expr] = len(local_applied)
        current = next_expr
        current_nodes = next_nodes
        steps += 1


def _rewrite_expr_once(
    expr: FullExpr, patterns: Sequence[RewritePattern]
) -> tuple[FullExpr, tuple[str, ...]]:
    # Volatile/opaque expressions and linker-symbol leaves survive rewrite untouched
    if _contains_effectful_expr(expr):
        return expr, ()
    applied: tuple[str, ...] = ()
    if isinstance(expr, BinOp):
        left, left_applied = _rewrite_expr_once(expr.left, patterns)
        right, right_applied = _rewrite_expr_once(expr.right, patterns)
        expr = BinOp(expr.op, left, right, expr.width)
        applied = left_applied + right_applied
    elif isinstance(expr, Cmp):
        left, left_applied = _rewrite_expr_once(expr.left, patterns)
        right, right_applied = _rewrite_expr_once(expr.right, patterns)
        expr = Cmp(
            expr.op,
            left,
            right,
            expr.width,
            expr.signed,
        )
        applied = left_applied + right_applied
    elif isinstance(expr, Extend):
        value, value_applied = _rewrite_expr_once(expr.value, patterns)
        expr = Extend(expr.kind, value, expr.width)
        applied = value_applied

    for pattern in patterns:
        captures = match_expr(pattern.match, expr)
        if captures is not None:
            return build_expr(pattern.replace, captures, expr), applied + (pattern.name,)
    return expr, applied


def _contains_effectful_expr(expr: FullExpr) -> bool:
    """Return whether rewriting *expr* could erase or reorder an effect."""
    if isinstance(expr, (Call, MemLoad, InlineAsmExpr, SymbolAddr)):
        return True
    if isinstance(expr, (BinOp, Cmp)):
        return _contains_effectful_expr(expr.left) or _contains_effectful_expr(expr.right)
    if isinstance(expr, Extend):
        return _contains_effectful_expr(expr.value)
    return False


def _expression_size(expr: FullExpr) -> int:
    """Count nodes iteratively so diagnostics remain safe for deep bad rewrites."""
    count = 0
    pending = [expr]
    while pending:
        node = pending.pop()
        count += 1
        if isinstance(node, (BinOp, Cmp)):
            pending.extend((node.left, node.right))
        elif isinstance(node, Extend):
            pending.append(node.value)
        elif isinstance(node, BitOp) and node.kind == "test":
            pending.append(node.var)
    return count


def _raise_node_budget(
    nodes: int,
    max_nodes: int,
    steps: int,
    last_rule: str,
    seen_states: int,
    max_steps: int,
) -> None:
    if nodes > max_nodes:
        raise RewriteConvergenceError(
            "rewrite did not converge: node budget exceeded "
            f"(steps={steps}, max_steps={max_steps}, last_rule={last_rule}, "
            f"expression_nodes={nodes}, max_nodes={max_nodes}, seen_states={seen_states})"
        )


def _validate_budget(name: str, value: int) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
