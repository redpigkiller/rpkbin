"""Pattern rewrite pass for LIR expressions.

Volatile/opaque expression handling
------------------------------------
``MemLoad`` and ``InlineAsmExpr`` nodes are never rewritten.  The
``_rewrite_expr_once`` function short-circuits on these node types
before any pattern matching occurs.  ``MemStore`` and side-effecting
``BitOp`` statements are passed through unchanged by the statement loop
in ``rewrite_function`` (only ``Assign`` values are rewritten).

This guarantees that volatile memory operations survive the rewrite pass
intact and in their original order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .ir import Assign, BinOp, Block, BrIf, Cmp, Function, InlineAsmExpr, MemLoad, Return
from .lir import BrCmp, Extend, Fragment, FullExpr, SymbolAddr
from .matcher import build_expr, match_expr
from .patterns import RewritePattern


@dataclass(frozen=True)
class RewriteResult:
    function: Function
    applied: Sequence[str]


def _rewrite_block(block: Block, patterns: Sequence[RewritePattern], applied: List[str]) -> Block:
    """Rewrite expressions in a single block's statements and terminator.

    Shared helper used by both ``rewrite_function`` and ``rewrite_fragment``.
    ``FragmentExit`` is left untouched.  ``InlineAsmExpr``, ``MemLoad``,
    and ``SymbolAddr`` are never rewritten (handled by ``_rewrite_expr_once``).
    """
    new_statements = []
    for stmt in block.statements:
        if isinstance(stmt, Assign):
            value = _rewrite_expr_fixpoint(stmt.value, patterns, applied)
            new_statements.append(Assign(stmt.target, value))
        else:
            new_statements.append(stmt)

    term = block.terminator
    if isinstance(term, BrIf):
        new_cond = _rewrite_expr_fixpoint(term.cond, patterns, applied)
        term = BrIf(new_cond, term.true_label, term.false_label)
    elif isinstance(term, Return):
        new_value = _rewrite_expr_fixpoint(term.value, patterns, applied)
        term = Return(new_value)
    elif isinstance(term, BrCmp):
        new_left = _rewrite_expr_fixpoint(term.left, patterns, applied)
        new_right = _rewrite_expr_fixpoint(term.right, patterns, applied)
        term = BrCmp(term.op, new_left, new_right, term.true_label, term.false_label)

    return Block(block.label, tuple(new_statements), term)


def rewrite_function(func: Function, patterns: Iterable[RewritePattern]) -> RewriteResult:
    pattern_list = list(patterns)
    applied: List[str] = []
    new_blocks = [_rewrite_block(b, pattern_list, applied) for b in func.blocks]
    return RewriteResult(Function(func.name, tuple(func.params), tuple(new_blocks)), tuple(applied))


def rewrite_fragment(
    fragment: Fragment, patterns: Iterable[RewritePattern]
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
    pattern_list = list(patterns)
    applied: List[str] = []
    new_blocks = [_rewrite_block(b, pattern_list, applied) for b in fragment.blocks]
    new_fragment = Fragment(
        name=fragment.name,
        bindings=fragment.bindings,
        scratch_regs=fragment.scratch_regs,
        blocks=tuple(new_blocks),
    )
    return new_fragment, tuple(applied)


def _rewrite_expr_fixpoint(
    expr: FullExpr, patterns: Sequence[RewritePattern], applied: List[str]
) -> FullExpr:
    prior = None
    current = expr
    while prior != current:
        prior = current
        current = _rewrite_expr_once(current, patterns, applied)
    return current


def _rewrite_expr_once(
    expr: FullExpr, patterns: Sequence[RewritePattern], applied: List[str]
) -> FullExpr:
    # Volatile/opaque expressions and linker-symbol leaves survive rewrite untouched
    if isinstance(expr, (MemLoad, InlineAsmExpr, SymbolAddr)):
        return expr
    if isinstance(expr, BinOp):
        expr = BinOp(expr.op, _rewrite_expr_once(expr.left, patterns, applied), _rewrite_expr_once(expr.right, patterns, applied), expr.width)
    elif isinstance(expr, Cmp):
        expr = Cmp(
            expr.op,
            _rewrite_expr_once(expr.left, patterns, applied),
            _rewrite_expr_once(expr.right, patterns, applied),
            expr.width,
            expr.signed,
        )
    elif isinstance(expr, Extend):
        expr = Extend(expr.kind, _rewrite_expr_once(expr.value, patterns, applied), expr.width)

    for pattern in patterns:
        captures = match_expr(pattern.match, expr)
        if captures is not None:
            applied.append(pattern.name)
            return build_expr(pattern.replace, captures)
    return expr
