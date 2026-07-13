"""HIR validation layer for the codegen pipeline.

Validates structural and type-level correctness of ``HFunction`` nodes
*before* lowering begins.  Raises ``HIRValidationError`` on the first
detected violation.

Checks implemented (register-model checks require ``register_model`` != None):

1. Assignment width mismatch — lhs and rhs bit-widths must agree.
2. Shift amount must be ``HConst`` — variable shift amounts are rejected.
3. ``HLogical`` / ``HNot`` in non-condition position — these nodes are only
   valid as the ``cond`` of control-flow statements.
4. ``HBitTest`` in non-condition position — same restriction.
5. ``HExtract`` range and storage width must be valid; ``HConcat`` width
   must match the computed width.
6. ``HCall`` argument ``@hint`` conflict — two arguments sharing the same hint.
7. ``@hint`` aliasing conflict between live variables (requires
   ``register_model``).
8. ``HBreak`` / ``HContinue`` outside an enclosing loop.
9. ``HFor`` bodies must not write to their loop variable.
10. ``HParam.reg_hint`` / ``HVar.reg_hint`` must be a legal physical register
    (requires ``register_model``).  Non-allocatable physical registers are
    accepted; unknown registers are rejected.
11. ``HCall.arg_regs`` / ``HCall.return_regs`` non-None entries must be legal
    physical registers (requires ``register_model``).
12. ``HExternFn.return_regs`` non-None entries must be legal physical registers
    (requires ``register_model``).
13. ``HFunction.return_regs`` non-None entries must be legal physical registers
    (requires ``register_model``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .hir import (
    HAssign,
    HBinOp,
    HBitSet,
    HBitTest,
    HBreak,
    HCall,
    HCallAssign,
    HCast,
    HCmp,
    HConcat,
    HConst,
    HContinue,
    HExit,
    HExternFn,
    HExprStmt,
    HExtract,
    HFor,
    HFragment,
    HFragmentBinding,
    HFunction,
    HIf,
    HInlineAsm,
    HInsert,
    HLoad,
    HLogical,
    HModule,
    HNot,
    HPoll,
    HReturn,
    HStore,
    HSymbolAddr,
    HVar,
    HWhile,
    SInt,
    UInt,
    Void,
)
from .target import can_allocate, registers_overlap, is_physical_register


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class HIRValidationError(Exception):
    """Raised by :func:`validate_hfunction` when a validation check fails.

    ``loc`` carries the source location associated with the offending node,
    or ``None`` when location information is unavailable.
    """

    def __init__(self, message: str, loc=None) -> None:
        loc_str = f" at {loc.file}:{loc.line}" if loc else ""
        super().__init__(f"{message}{loc_str}")
        self.loc = loc


# ---------------------------------------------------------------------------
# Internal width helper
# ---------------------------------------------------------------------------

def _type_width(ty) -> int | None:
    """Return the bit-width of an ``HType``, or ``None`` for ``Void``."""
    if isinstance(ty, (UInt, SInt)):
        return ty.width
    return None  # Void


def _expr_width(expr) -> int | None:
    """Return the bit-width of an ``HExpr`` node, or ``None`` if unknown."""
    if isinstance(expr, HConst):
        return _type_width(expr.ty)
    if isinstance(expr, HVar):
        return _type_width(expr.ty)
    if isinstance(expr, HBinOp):
        return _type_width(expr.ty)
    if isinstance(expr, HCast):
        return _type_width(expr.to_ty)
    if isinstance(expr, HExtract):
        return _type_width(expr.ty)
    if isinstance(expr, HConcat):
        return _type_width(expr.ty)
    if isinstance(expr, HCall):
        if isinstance(expr.return_ty, (UInt, SInt)):
            return _type_width(expr.return_ty)
    if isinstance(expr, HSymbolAddr):
        return _type_width(expr.ty)
    return None


def _node_ty(expr):
    """Return the ``.ty`` attribute of an expression node, or ``None``."""
    return getattr(expr, "ty", None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_hfunction(func: HFunction, register_model=None) -> None:
    """Validate a :class:`~rpkbin.codegen.hir.HFunction`.

    Raises :class:`HIRValidationError` on the first detected violation.

    Parameters
    ----------
    func:
        The function to validate.
    register_model:
        Optional ``RegisterModel`` protocol instance.  When ``None``, checks
        that require target-specific register knowledge (``@hint`` aliasing
        and physical-register membership) are skipped.
    """
    # Check HFunction.return_regs physical membership.
    # return_regs is tuple[str, ...] — entries are always str, no None values.
    if register_model is not None:
        for i, rr in enumerate(func.return_regs):
            if not is_physical_register(register_model, rr):
                raise HIRValidationError(
                    f"HFunction '{func.name}' return_regs[{i}] '{rr}' is not a "
                    f"valid physical register in the current RegisterModel",
                    func.loc,
                )
    validator = _Validator(func, register_model)
    validator.run()


# ---------------------------------------------------------------------------
# Internal validator
# ---------------------------------------------------------------------------

def validate_extern_fn(efn: HExternFn, register_model=None) -> None:
    """Validate an ``HExternFn`` declaration.

    Checks:
    * Clobber entries are non-empty strings.
    * No duplicate or overlapping clobber registers within the same declaration.
    * When a ``RegisterModel`` is provided, each clobber name must be a
      legal physical register (``is_physical_register()``).
      Clobbers may be non-allocatable registers (e.g. flag/status registers).
    * When a ``RegisterModel`` is provided, each non-None ``return_regs``
      entry must be a legal physical register.
    """
    _validate_clobber_list(
        owner=f"HExternFn '{efn.name}'",
        clobbers=efn.clobbers,
        loc=efn.loc,
        register_model=register_model,
    )

    # Validate return_regs physical membership.
    # return_regs is tuple[str, ...] — entries are always str, no None values.
    if register_model is not None:
        for i, rr in enumerate(efn.return_regs):
            if not is_physical_register(register_model, rr):
                raise HIRValidationError(
                    f"HExternFn '{efn.name}' return_regs[{i}] '{rr}' is not a "
                    f"valid physical register in the current RegisterModel",
                    efn.loc,
                )


def _type_eq(a, b) -> bool:
    """Check if two HType instances are structurally equal."""
    if type(a) is not type(b):
        return False
    if isinstance(a, (UInt, SInt)):
        return a.width == b.width
    return isinstance(a, Void) and isinstance(b, Void)


def _validate_clobber_list(
    owner: str,
    clobbers: tuple[str, ...],
    loc,
    register_model,
) -> None:
    """Validate a clobber list using generic overlap semantics."""
    seen_names: set[str] = set()
    seen_regs: list[str] = []
    for clobber in clobbers:
        if not isinstance(clobber, str) or not clobber:
            raise HIRValidationError(
                f"{owner} clobber entries must be non-empty strings, got {clobber!r}",
                loc,
            )
        if clobber in seen_names:
            raise HIRValidationError(
                f"{owner} has duplicate clobber '{clobber}'",
                loc,
            )
        if register_model is not None:
            if not is_physical_register(register_model, clobber):
                raise HIRValidationError(
                    f"{owner} clobber '{clobber}' is not a valid register "
                    f"in the current RegisterModel",
                    loc,
                )
            for other in seen_regs:
                if registers_overlap(register_model, clobber, other):
                    # Alias duplicates stay rejected until RegisterModel has a
                    # generic canonical storage API for clobber normalization.
                    raise HIRValidationError(
                        f"{owner} clobber '{clobber}' overlaps with '{other}'",
                        loc,
                    )
        seen_names.add(clobber)
        seen_regs.append(clobber)


def _expr_type(expr):
    """Extract the HType from an expression for type-checking, or None."""
    if isinstance(expr, (HConst, HVar, HBinOp, HExtract, HConcat, HLoad, HSymbolAddr)):
        return getattr(expr, "ty", None)
    if isinstance(expr, HCast):
        return expr.to_ty
    if isinstance(expr, HCall):
        if not isinstance(expr.return_ty, tuple):
            return expr.return_ty
        return None
    return None


class _Validator:
    """Stateful validator — one instance per :func:`validate_hfunction` call."""

    def __init__(self, func: HFunction, register_model) -> None:
        self.func = func
        self.register_model = register_model
        # List of (hint_string, node) for every hinted var / param found.
        self._hinted_vars: list[tuple[str, object]] = []

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._collect_hints()
        self._check_hint_aliases()          # check 7 (requires register_model)
        self._check_hints_physical()        # check 10 (requires register_model)
        # Check 8: break/continue outside loop. Check 9: HFor body writes.
        self._check_break_continue(self.func.body, in_loop=False)
        for stmt in self._all_stmts(self.func.body):
            self._check_stmt(stmt)

    # ------------------------------------------------------------------
    # Statement traversal
    # ------------------------------------------------------------------

    def _all_stmts(self, body) -> Iterator:
        """Yield every statement in *body* recursively (depth-first)."""
        for stmt in body:
            yield stmt
            if isinstance(stmt, HIf):
                yield from self._all_stmts(stmt.then_body)
                for _cond, branch_body in stmt.elif_branches:
                    yield from self._all_stmts(branch_body)
                yield from self._all_stmts(stmt.else_body)
            elif isinstance(stmt, (HWhile, HPoll, HFor)):
                yield from self._all_stmts(stmt.body)

    # ------------------------------------------------------------------
    # Per-statement checks
    # ------------------------------------------------------------------

    def _check_stmt(self, stmt) -> None:
        loc = getattr(stmt, "loc", None)

        if isinstance(stmt, HExit):
            raise HIRValidationError(
                "HExit is not allowed inside HFunction", loc
            )

        if isinstance(stmt, HAssign):
            self._check_assign(stmt, loc)

        elif isinstance(stmt, HReturn):
            for val in stmt.values:
                if isinstance(val, str):
                    raise HIRValidationError(
                        f"HReturn values must be HExpr, got {val!r}", loc
                    )
                self._check_expr_value(val, loc)

        elif isinstance(stmt, HExprStmt):
            self._check_expr_value(stmt.expr, loc)

        elif isinstance(stmt, HStore):
            self._check_expr_value(stmt.ptr_expr, loc)
            self._check_expr_value(stmt.value_expr, loc)

        elif isinstance(stmt, HCallAssign):
            self._check_call_assign(stmt, loc)

        elif isinstance(stmt, HBitSet):
            self._check_bitset(stmt, loc)

        elif isinstance(stmt, HFor):
            self._check_for(stmt, loc)

        elif isinstance(stmt, (HIf, HWhile, HPoll)):
            self._check_cond(stmt.cond, loc)
            if isinstance(stmt, HIf):
                for elif_cond, _ in stmt.elif_branches:
                    self._check_cond(elif_cond, loc)

        # HBreak / HContinue: already validated by _check_break_continue.

    # ------------------------------------------------------------------
    # Check 8 + 9: HBreak / HContinue outside enclosing loop;
    #              HFor loop-variable write detection
    # ------------------------------------------------------------------

    def _check_break_continue(self, body, in_loop: bool) -> None:
        """Recursively verify that HBreak / HContinue only appear inside loops,
        including nested HFor bodies."""
        for stmt in body:
            loc = getattr(stmt, "loc", None)
            if isinstance(stmt, HBreak) and not in_loop:
                raise HIRValidationError(
                    "HBreak used outside of an enclosing loop", loc
                )
            elif isinstance(stmt, HContinue):
                if not in_loop:
                    raise HIRValidationError(
                        "HContinue used outside of an enclosing loop", loc
                    )
            elif isinstance(stmt, HIf):
                self._check_break_continue(stmt.then_body, in_loop)
                for _cond, branch_body in stmt.elif_branches:
                    self._check_break_continue(branch_body, in_loop)
                self._check_break_continue(stmt.else_body, in_loop)
            elif isinstance(stmt, (HFor, HWhile, HPoll)):
                self._check_break_continue(stmt.body, in_loop=True)

    def _check_for(self, stmt: HFor, loc) -> None:
        if not isinstance(stmt.init, HConst) or not isinstance(stmt.bound, HConst):
            raise HIRValidationError(
                "HFor init and bound must be HConst", loc
            )

        var_ty = stmt.var.ty
        init_ty = stmt.init.ty
        bound_ty = stmt.bound.ty

        # Check init/bound types match the loop variable type exactly
        if not _type_eq(var_ty, init_ty):
            raise HIRValidationError(
                f"HFor init type {init_ty!r} does not match loop variable "
                f"type {var_ty!r}",
                loc,
            )
        if not _type_eq(var_ty, bound_ty):
            raise HIRValidationError(
                f"HFor bound type {bound_ty!r} does not match loop variable "
                f"type {var_ty!r}",
                loc,
            )

        # Check value fits in the type
        width = var_ty.width if isinstance(var_ty, (UInt, SInt)) else 0
        max_val = (1 << width) - 1 if isinstance(var_ty, UInt) else (1 << (width - 1)) - 1
        min_val = 0 if isinstance(var_ty, UInt) else -(1 << (width - 1))
        for label, val in [("init", stmt.init.value), ("bound", stmt.bound.value)]:
            if val < min_val or val > max_val:
                raise HIRValidationError(
                    f"HFor {label} value {val} cannot be represented in {var_ty!r}",
                    loc,
                )

        # bound >= init
        if stmt.bound.value < stmt.init.value:
            raise HIRValidationError(
                f"HFor bound ({stmt.bound.value}) must be >= init ({stmt.init.value})",
                loc,
            )

        # Check nested HFor does not re-declare the same loop variable name
        if _nested_for_uses_name(stmt.body, stmt.var.name):
            raise HIRValidationError(
                f"Nested HFor uses the same loop-variable name '{stmt.var.name}'. "
                f"This is conservatively rejected.",
                loc,
            )

        if _body_writes_var(stmt.body, stmt.var):
            raise HIRValidationError(
                f"HFor body writes loop variable '{stmt.var.name}'", loc
            )

    # ------------------------------------------------------------------
    # Check 12+ : condition validation (HIf.cond, HWhile.cond, HPoll.cond)
    # ------------------------------------------------------------------

    def _check_cond(self, cond, loc) -> None:
        """Validate a condition expression tree recursively.

        Raises HIRValidationError for:
        - HLogical with op other than "and"/"or"
        - HCmp with invalid value operands
        - HBitTest with out-of-range bit index
        - HNot / HLogical with invalid children
        """
        if isinstance(cond, HCmp):
            if cond.op not in ("eq", "ne", "lt", "le", "gt", "ge"):
                raise HIRValidationError(
                    f"HCmp op must be one of eq/ne/lt/le/gt/ge, got {cond.op!r}",
                    loc,
                )
            self._check_expr_value(cond.left, loc)
            self._check_expr_value(cond.right, loc)
        elif isinstance(cond, HBitTest):
            if not isinstance(cond.var, HVar):
                raise HIRValidationError(
                    "HBitTest var must be an HVar", loc
                )
            if not isinstance(cond.bit_idx, int) or isinstance(cond.bit_idx, bool):
                raise HIRValidationError(
                    f"HBitTest bit index must be a strict int (not bool), "
                    f"got {cond.bit_idx!r}",
                    loc,
                )
            if cond.bit_idx < 0 or cond.bit_idx >= cond.var.ty.width:
                raise HIRValidationError(
                    f"HBitTest bit index {cond.bit_idx} out of range for "
                    f"{cond.var.ty.width}-bit variable",
                    loc,
                )
        elif isinstance(cond, HLogical):
            if cond.op not in ("and", "or"):
                raise HIRValidationError(
                    f"HLogical op must be 'and' or 'or', got {cond.op!r}",
                    loc,
                )
            self._check_cond(cond.left, loc)
            self._check_cond(cond.right, loc)
        elif isinstance(cond, HNot):
            self._check_cond(cond.expr, loc)
        else:
            raise HIRValidationError(
                f"unsupported condition node: {type(cond).__name__}",
                loc,
            )

    # ------------------------------------------------------------------
    # Check 1: assignment width mismatch
    # ------------------------------------------------------------------

    def _check_assign(self, stmt: HAssign, loc) -> None:
        lhs_w = _type_width(stmt.target.ty)
        rhs_w = _expr_width(stmt.value)

        # Check 1 — width mismatch
        if lhs_w is not None and rhs_w is not None and lhs_w != rhs_w:
            raise HIRValidationError(
                f"assignment width mismatch: lhs={lhs_w}, rhs={rhs_w}", loc
            )

        # Check 2, 3, 4, 5, 6 — recurse into the value expression
        self._check_expr_value(stmt.value, loc)

    # ------------------------------------------------------------------
    # Expression-level checks (non-condition positions)
    # ------------------------------------------------------------------

    def _check_expr_value(self, expr, loc) -> None:
        """Check an expression that appears in a *value* (non-cond) position."""
        # Check 3: HLogical / HNot in non-condition position
        if isinstance(expr, (HLogical, HNot)):
            raise HIRValidationError(
                "HLogical/HNot may only appear in condition positions "
                "(HIf.cond, HWhile.cond, etc.)",
                loc,
            )

        # Check 4: HBitTest in non-condition position
        if isinstance(expr, HBitTest):
            raise HIRValidationError(
                "HBitTest may only appear in condition positions", loc
            )

        # Recurse into sub-expressions
        self._recurse_expr(expr, loc)

    def _recurse_expr(self, expr, loc) -> None:
        """Recurse into child expressions, applying all value-position checks."""
        if isinstance(expr, HBinOp):
            self._check_binop(expr, loc)

        elif isinstance(expr, HExtract):
            self._check_extract(expr, loc)
            self._check_expr_value(expr.expr, loc)

        elif isinstance(expr, HConcat):
            self._check_concat(expr, loc)
            self._check_expr_value(expr.hi, loc)
            self._check_expr_value(expr.lo, loc)

        elif isinstance(expr, HCast):
            self._check_expr_value(expr.expr, loc)

        elif isinstance(expr, HInsert):
            self._check_expr_value(expr.dst, loc)
            self._check_expr_value(expr.value, loc)

        elif isinstance(expr, HLoad):
            self._check_expr_value(expr.ptr_expr, loc)

        elif isinstance(expr, HCall):
            self._check_call(expr, loc)
            for arg in expr.args:
                self._check_expr_value(arg, loc)

        elif isinstance(expr, HSymbolAddr):
            # Leaf node — nothing to recurse into
            pass

        # HConst, HVar — leaf nodes, nothing to recurse into

    # ------------------------------------------------------------------
    # Check 2: shift amount must be HConst
    # ------------------------------------------------------------------

    def _check_binop(self, expr: HBinOp, loc) -> None:
        if expr.op in ("shl", "shr", "ror", "rol"):
            if not isinstance(expr.right, HConst):
                raise HIRValidationError(
                    f"shift amount must be a compile-time constant (HConst), "
                    f"got {type(expr.right).__name__}",
                    loc,
                )
        # Recurse into operands (both are in value positions)
        self._check_expr_value(expr.left, loc)
        self._check_expr_value(expr.right, loc)

    # ------------------------------------------------------------------
    # Check 5a: HExtract indices and storage width must be valid
    # ------------------------------------------------------------------

    def _check_extract(self, expr: HExtract, loc) -> None:
        if isinstance(expr.ty, SInt):
            raise HIRValidationError(
                f"HExtract storage type must be UInt, got SInt({expr.ty.width})",
                loc,
            )
        storage_w = _type_width(expr.ty)
        if storage_w not in (8, 16):
            raise HIRValidationError(
                f"HExtract storage width must be 8 or 16, got {storage_w!r}",
                loc,
            )
        if not isinstance(expr.msb, int) or isinstance(expr.msb, bool):
            raise HIRValidationError(
                f"HExtract msb must be a strict int (not bool), got {expr.msb!r}",
                loc,
            )
        if not isinstance(expr.lsb, int) or isinstance(expr.lsb, bool):
            raise HIRValidationError(
                f"HExtract lsb must be a strict int (not bool), got {expr.lsb!r}",
                loc,
            )
        if expr.lsb < 0 or expr.msb < expr.lsb:
            raise HIRValidationError(
                f"HExtract range [{expr.msb}:{expr.lsb}] is invalid", loc
            )
        source_w = _expr_width(expr.expr)
        if source_w is not None and expr.msb >= source_w:
            raise HIRValidationError(
                f"HExtract msb {expr.msb} out of range for {source_w}-bit source",
                loc,
            )
        field_w = expr.msb - expr.lsb + 1
        if field_w < 1 or field_w > storage_w:
            raise HIRValidationError(
                f"HExtract field width {field_w} does not fit in {storage_w}-bit storage",
                loc,
            )

    # ------------------------------------------------------------------
    # Check 5b: HConcat width must equal width(hi) + width(lo)
    # ------------------------------------------------------------------

    def _check_concat(self, expr: HConcat, loc) -> None:
        hi_ty = _node_ty(expr.hi)
        lo_ty = _node_ty(expr.lo)
        hi_w = _type_width(hi_ty) if hi_ty is not None else None
        lo_w = _type_width(lo_ty) if lo_ty is not None else None

        if hi_w is not None and lo_w is not None:
            expected = hi_w + lo_w
            actual = _type_width(expr.ty)
            if actual is not None and actual != expected:
                raise HIRValidationError(
                    f"HConcat width mismatch: ty declares {actual} bits but "
                    f"hi({hi_w}) + lo({lo_w}) = {expected} bits",
                    loc,
                )

    # ------------------------------------------------------------------
    # Check 6: HCall @hint conflict (two args share the same hint)
    # ------------------------------------------------------------------

    def _check_call(self, expr: HCall, loc) -> None:
        seen_hints: dict[str, int] = {}  # hint -> first arg index
        for idx, arg in enumerate(expr.args):
            if isinstance(arg, HVar) and arg.reg_hint is not None:
                hint = arg.reg_hint
                if hint in seen_hints:
                    raise HIRValidationError(
                        f"HCall '{expr.name}': two arguments share the same "
                        f"@hint '{hint}'",
                        loc,
                    )
                seen_hints[hint] = idx

        if expr.clobbers is not None:
            _validate_clobber_list(
                owner=f"HCall '{expr.name}'",
                clobbers=expr.clobbers,
                loc=loc,
                register_model=self.register_model,
            )

        # check 11: HCall.arg_regs / return_regs physical membership
        if self.register_model is not None:
            for i, ar in enumerate(expr.arg_regs):
                if ar is not None and not is_physical_register(self.register_model, ar):
                    raise HIRValidationError(
                        f"HCall '{expr.name}' arg_regs[{i}] '{ar}' is not a "
                        f"valid physical register in the current RegisterModel",
                        loc,
                    )
            for i, rr in enumerate(expr.return_regs):
                if rr is not None and not is_physical_register(self.register_model, rr):
                    raise HIRValidationError(
                        f"HCall '{expr.name}' return_regs[{i}] '{rr}' is not a "
                        f"valid physical register in the current RegisterModel",
                        loc,
                    )

    # ------------------------------------------------------------------
    # HCallAssign validation
    # ------------------------------------------------------------------

    def _check_call_assign(self, stmt: HCallAssign, loc) -> None:
        call = stmt.call
        # Contract: inside HCallAssign, call.return_ty must be Void().
        # The true return signature is derived from the declaration at
        # module level (see _validate_calls_in_body).
        if not isinstance(call.return_ty, Void):
            raise HIRValidationError(
                f"HCallAssign call.return_ty must be Void(), got {call.return_ty!r}",
                loc,
            )
        # Validate the call itself (args, hints)
        self._check_call(call, loc)
        for arg in call.args:
            self._check_expr_value(arg, loc)

    # ------------------------------------------------------------------
    # HBitSet validation
    # ------------------------------------------------------------------

    def _check_bitset(self, stmt: HBitSet, loc) -> None:
        """Validate HBitSet statement node."""
        if not isinstance(stmt.var, HVar):
            raise HIRValidationError(
                "HBitSet var must be an HVar", loc
            )
        self._check_expr_value(stmt.var, loc)
        if not isinstance(stmt.bit_idx, int) or isinstance(stmt.bit_idx, bool):
            raise HIRValidationError(
                "HBitSet bit_idx must be a strict int (not bool)", loc
            )
        if stmt.bit_idx < 0 or stmt.bit_idx >= stmt.var.ty.width:
            raise HIRValidationError(
                f"HBitSet bit index {stmt.bit_idx} out of range for "
                f"{stmt.var.ty.width}-bit variable",
                loc,
            )
        if not isinstance(stmt.value, int) or isinstance(stmt.value, bool):
            raise HIRValidationError(
                "HBitSet value must be a strict int (not bool)", loc
            )
        if stmt.value not in (0, 1):
            raise HIRValidationError(
                f"HBitSet value must be 0 or 1, got {stmt.value!r}", loc
            )

    # ------------------------------------------------------------------
    # Check 7: @hint aliasing conflict (only when register_model is set)
    # ------------------------------------------------------------------

    def _collect_hints(self) -> None:
        """Gather every (hint, node) pair from params and all HVar nodes."""
        for param in self.func.params:
            if param.reg_hint is not None:
                self._hinted_vars.append((param.reg_hint, param))

        for stmt in self._all_stmts(self.func.body):
            self._collect_hints_in_stmt(stmt)

    def _collect_hints_in_stmt(self, stmt) -> None:
        for expr in self._exprs_in_stmt(stmt):
            self._collect_hints_in_expr(expr)

    def _exprs_in_stmt(self, stmt):
        """Yield top-level expressions referenced by a statement."""
        if isinstance(stmt, HAssign):
            yield stmt.target
            yield stmt.value
        elif isinstance(stmt, HCallAssign):
            for target in stmt.targets:
                if target is not None:
                    yield target
            yield stmt.call
        elif isinstance(stmt, HReturn):
            for v in stmt.values:
                if not isinstance(v, str):
                    yield v
        elif isinstance(stmt, HExprStmt):
            yield stmt.expr
        elif isinstance(stmt, HStore):
            yield stmt.ptr_expr
            yield stmt.value_expr
        elif isinstance(stmt, (HIf, HWhile, HPoll)):
            yield stmt.cond
        elif isinstance(stmt, HFor):
            yield stmt.var
            yield stmt.init
            yield stmt.bound
        elif isinstance(stmt, HBitSet):
            yield stmt.var

    def _collect_hints_in_expr(self, expr) -> None:
        """Walk an expression tree and collect all HVar reg_hints."""
        if isinstance(expr, HVar):
            if expr.reg_hint is not None:
                self._hinted_vars.append((expr.reg_hint, expr))
        elif isinstance(expr, HBinOp):
            self._collect_hints_in_expr(expr.left)
            self._collect_hints_in_expr(expr.right)
        elif isinstance(expr, HCast):
            self._collect_hints_in_expr(expr.expr)
        elif isinstance(expr, HExtract):
            self._collect_hints_in_expr(expr.expr)
        elif isinstance(expr, HConcat):
            self._collect_hints_in_expr(expr.hi)
            self._collect_hints_in_expr(expr.lo)
        elif isinstance(expr, HInsert):
            self._collect_hints_in_expr(expr.dst)
            self._collect_hints_in_expr(expr.value)
        elif isinstance(expr, HLoad):
            self._collect_hints_in_expr(expr.ptr_expr)
        elif isinstance(expr, HCall):
            for arg in expr.args:
                self._collect_hints_in_expr(arg)
        elif isinstance(expr, HSymbolAddr):
            pass
        elif isinstance(expr, HLogical):
            self._collect_hints_in_expr(expr.left)
            self._collect_hints_in_expr(expr.right)
        elif isinstance(expr, HNot):
            self._collect_hints_in_expr(expr.expr)

    def _check_hint_aliases(self) -> None:
        if self.register_model is None:
            return

        hints = [h for h, _ in self._hinted_vars]
        for i, h1 in enumerate(hints):
            for h2 in hints[i + 1:]:
                if h1 == h2:
                    continue  # same hint is fine (same physical reg intended)
                if registers_overlap(self.register_model, h1, h2):
                    raise HIRValidationError(
                        f"@hint aliasing conflict: '{h1}' and '{h2}' are aliases"
                    )

    def _check_hints_physical(self) -> None:
        """Check 10: every reg_hint on HParam / HVar must be a legal physical register.

        Skipped when ``register_model`` is None.
        Non-allocatable physical registers (e.g. 'special', 'status') are accepted;
        only completely unknown registers are rejected.
        """
        if self.register_model is None:
            return

        for hint, node in self._hinted_vars:
            if not is_physical_register(self.register_model, hint):
                loc = getattr(node, "loc", None)
                kind = type(node).__name__
                name = getattr(node, "name", "?")
                raise HIRValidationError(
                    f"{kind} '{name}' reg_hint '{hint}' is not a valid physical "
                    f"register in the current RegisterModel",
                    loc,
                )


def _nested_for_uses_name(body, name: str) -> bool:
    """Check if any nested HFor inside *body* uses the same loop-variable name."""
    for stmt in body:
        if isinstance(stmt, HFor):
            if stmt.var.name == name:
                return True
            if _nested_for_uses_name(stmt.body, name):
                return True
        elif isinstance(stmt, HIf):
            if _nested_for_uses_name(stmt.then_body, name):
                return True
            for _cond, branch_body in stmt.elif_branches:
                if _nested_for_uses_name(branch_body, name):
                    return True
            if _nested_for_uses_name(stmt.else_body, name):
                return True
        elif isinstance(stmt, (HWhile, HPoll)):
            if _nested_for_uses_name(stmt.body, name):
                return True
    return False


def _build_signature_table(
    functions: tuple[HFunction, ...],
    externs: tuple[HExternFn, ...],
) -> dict[str, tuple[tuple, HType | tuple]]:
    """Build a name -> (param_types, return_ty) table for call validation."""
    table: dict[str, tuple[tuple, HType | tuple]] = {}
    for fn in functions:
        if fn.name in table:
            raise HIRValidationError(
                f"Duplicate function name '{fn.name}'"
            )
        table[fn.name] = (fn.params, fn.return_ty)
    for ext in externs:
        if ext.name in table:
            raise HIRValidationError(
                f"Duplicate extern function name '{ext.name}'"
            )
        table[ext.name] = (ext.params, ext.return_ty)
    return table


def validate_hmodule(module: HModule, register_model=None) -> None:
    """Validate a complete :class:`~rpkbin.codegen.hir.HModule`.

    Checks performed:
    1. Validate each ``HFunction`` via ``validate_hfunction``.
    2. Validate each ``HExternFn`` via ``validate_extern_fn``.
    3. All names (functions, extern_functions, external_symbols) share one
       namespace — no cross-category duplicates.
    4. All ``HExternalSymbol`` names are unique and non-empty.
    5. Every ``HCall`` in any function body resolves to a declared
       function or extern (arity, argument types, and return-type match).
    6. Every ``HSymbolAddr`` resolves to a declared ``HExternalSymbol``
       and its type matches the declaration's address type.
    7. ``HCallAssign`` target count and types match the callee's return-type
       tuple; ``call.return_ty`` must be ``Void()``.
    8. HSymbolAddr.address_ty must not be Void.
    9. Nested calls in condition expressions (HCmp sub-expressions), call
       arguments, and all expression positions are validated.
    """
    # --- Step 1: validate each function independently ---
    for fn in module.functions:
        validate_hfunction(fn, register_model)
    for efn in module.extern_functions:
        validate_extern_fn(efn, register_model)

    # --- Step 1b: validate each fragment independently ---
    for frag in module.fragments:
        validate_hfragment(frag, register_model)

    # --- Step 2: module namespace — all names unique across all categories ---
    used_names: set[str] = set()
    for fn in module.functions:
        if fn.name in used_names:
            raise HIRValidationError(
                f"Duplicate name '{fn.name}' (conflicts with existing name "
                f"in functions/externs/symbols/fragments)"
            )
        used_names.add(fn.name)
    for efn in module.extern_functions:
        if efn.name in used_names:
            raise HIRValidationError(
                f"Duplicate name '{efn.name}' (conflicts with existing name "
                f"in functions/externs/symbols/fragments)"
            )
        used_names.add(efn.name)
    for sym in module.external_symbols:
        if not sym.name:
            raise HIRValidationError(
                "HExternalSymbol name must be a non-empty string", sym.loc
            )
        if sym.name in used_names:
            raise HIRValidationError(
                f"Duplicate name '{sym.name}' (conflicts with existing name "
                f"in functions/externs/symbols/fragments)",
                sym.loc,
            )
        used_names.add(sym.name)
    for frag in module.fragments:
        if frag.name in used_names:
            raise HIRValidationError(
                f"Duplicate name '{frag.name}' (conflicts with existing name "
                f"in functions/externs/symbols/fragments)",
                frag.loc,
            )
        used_names.add(frag.name)

    # --- Step 3: build symbol map and validate symbol invariants ---
    symbol_map: dict[str, HExternalSymbol] = {}
    for sym in module.external_symbols:
        symbol_map[sym.name] = sym

        # address_ty must be UInt (not SInt or Void)
        if not isinstance(sym.address_ty, UInt):
            raise HIRValidationError(
                f"HExternalSymbol '{sym.name}' address type must be UInt, "
                f"got {type(sym.address_ty).__name__}",
                sym.loc,
            )

        # value_ty if present must be UInt or SInt, not Void
        if sym.value_ty is not None and not isinstance(sym.value_ty, (UInt, SInt)):
            raise HIRValidationError(
                f"HExternalSymbol '{sym.name}' value type must be UInt or SInt "
                f"when provided, got {type(sym.value_ty).__name__}",
                sym.loc,
            )

        # volatile=True requires value_ty
        if sym.volatile and sym.value_ty is None:
            raise HIRValidationError(
                f"HExternalSymbol '{sym.name}': volatile=True requires a "
                f"non-None value_ty",
                sym.loc,
            )

    sig_table = _build_signature_table(module.functions, module.extern_functions)

    # --- Step 4: validate cross-references in every function body ---
    for fn in module.functions:
        _validate_calls_in_body(fn.body, sig_table, fn.loc, symbol_map)

    # --- Step 5: validate cross-references in every fragment body ---
    for frag in module.fragments:
        _validate_calls_in_body(frag.body, sig_table, frag.loc, symbol_map)


def _validate_calls_in_body(
    body: tuple,
    sig_table: dict,
    loc,
    symbol_map: dict[str, HExternalSymbol],
) -> None:
    """Walk a function body and validate all HCall / HCallAssign / HSymbolAddr."""
    for stmt in body:
        stmt_loc = getattr(stmt, "loc", None) or loc
        if isinstance(stmt, (HIf, HWhile, HFor, HPoll)):
            # Recurse into sub-bodies
            body_attr = "then_body" if isinstance(stmt, HIf) else "body"
            sub_body = getattr(stmt, body_attr, ())
            _validate_calls_in_body(sub_body, sig_table, stmt_loc, symbol_map)
            if isinstance(stmt, HIf):
                for _cond, branch_body in stmt.elif_branches:
                    _validate_calls_in_body(branch_body, sig_table, stmt_loc, symbol_map)
                _validate_calls_in_body(stmt.else_body, sig_table, stmt_loc, symbol_map)
            # Validate calls hidden in condition expressions (HCmp.left/right)
            if isinstance(stmt, (HIf, HWhile, HPoll)):
                _validate_cond_calls(stmt.cond, sig_table, stmt_loc, symbol_map)
                if isinstance(stmt, HIf):
                    for elif_cond, _ in stmt.elif_branches:
                        _validate_cond_calls(elif_cond, sig_table, stmt_loc, symbol_map)
        elif isinstance(stmt, HExprStmt):
            _validate_call_site(stmt.expr, sig_table, stmt_loc)
            for arg in stmt.expr.args:
                _validate_expr_call(arg, sig_table, stmt_loc, symbol_map)
        elif isinstance(stmt, HAssign):
            _validate_expr_call(stmt.value, sig_table, stmt_loc, symbol_map)
        elif isinstance(stmt, HCallAssign):
            _validate_call_assign_cross_ref(stmt, sig_table, stmt_loc, symbol_map)
        elif isinstance(stmt, HStore):
            _validate_expr_call(stmt.ptr_expr, sig_table, stmt_loc, symbol_map)
            _validate_expr_call(stmt.value_expr, sig_table, stmt_loc, symbol_map)
        elif isinstance(stmt, HReturn):
            for val in stmt.values:
                _validate_expr_call(val, sig_table, stmt_loc, symbol_map)
        elif isinstance(stmt, HExit):
            pass  # leaf terminator — no calls or symbol references


def _validate_cond_calls(
    cond,
    sig_table: dict,
    loc,
    symbol_map: dict[str, HExternalSymbol],
) -> None:
    """Walk a condition tree and validate HCall/HSymbolAddr in sub-expressions."""
    if isinstance(cond, HCmp):
        _validate_expr_call(cond.left, sig_table, loc, symbol_map)
        _validate_expr_call(cond.right, sig_table, loc, symbol_map)
    elif isinstance(cond, HLogical):
        _validate_cond_calls(cond.left, sig_table, loc, symbol_map)
        _validate_cond_calls(cond.right, sig_table, loc, symbol_map)
    elif isinstance(cond, HNot):
        _validate_cond_calls(cond.expr, sig_table, loc, symbol_map)
    # HBitTest: var is HVar, no sub-expressions — nothing to validate


def _validate_call_assign_cross_ref(
    stmt: HCallAssign,
    sig_table: dict,
    loc,
    symbol_map: dict[str, HExternalSymbol],
) -> None:
    """Validate an HCallAssign against the module's signature table."""
    call = stmt.call
    _validate_call_site(call, sig_table, loc, allow_multi_return=True)

    # Contract: call.return_ty must be Void()
    if not isinstance(call.return_ty, Void):
        raise HIRValidationError(
            f"HCallAssign call.return_ty must be Void(), got {call.return_ty!r}",
            loc,
        )

    callee_info = sig_table.get(call.name)
    if callee_info is not None:
        _params, ret_ty = callee_info
        # Determine the list of return types from the declaration
        if isinstance(ret_ty, tuple):
            ret_types = ret_ty
        elif isinstance(ret_ty, Void):
            ret_types = ()
        else:
            ret_types = (ret_ty,)

        if len(stmt.targets) != len(ret_types):
            raise HIRValidationError(
                f"HCallAssign to '{call.name}': expected {len(ret_types)} "
                f"target(s), got {len(stmt.targets)}",
                loc,
            )
        for i, (t, expected_ty) in enumerate(zip(stmt.targets, ret_types)):
            if t is not None and not _type_eq(t.ty, expected_ty):
                raise HIRValidationError(
                    f"HCallAssign target #{i} type {t.ty!r} does not match "
                    f"return type {expected_ty!r}",
                    loc,
                )

    # Recurse into args for nested calls / symbol references
    for arg in call.args:
        _validate_expr_call(arg, sig_table, loc, symbol_map)


def _validate_expr_call(
    expr,
    sig_table: dict,
    loc,
    symbol_map: dict[str, HExternalSymbol],
) -> None:
    """Recursively validate HCall / HSymbolAddr nodes inside expressions."""
    if isinstance(expr, HCall):
        _validate_call_site(expr, sig_table, loc)
        for arg in expr.args:
            _validate_expr_call(arg, sig_table, loc, symbol_map)
    elif isinstance(expr, (HBinOp, HCast)):
        for child in getattr(expr, "left", None), getattr(expr, "right", None), getattr(expr, "expr", None):
            if child is not None:
                _validate_expr_call(child, sig_table, loc, symbol_map)
    elif isinstance(expr, (HExtract, HInsert)):
        _validate_expr_call(expr.expr if hasattr(expr, "expr") else expr.dst, sig_table, loc, symbol_map)
        if hasattr(expr, "value"):
            _validate_expr_call(expr.value, sig_table, loc, symbol_map)
    elif isinstance(expr, HConcat):
        _validate_expr_call(expr.hi, sig_table, loc, symbol_map)
        _validate_expr_call(expr.lo, sig_table, loc, symbol_map)
    elif isinstance(expr, HLoad):
        _validate_expr_call(expr.ptr_expr, sig_table, loc, symbol_map)
    elif isinstance(expr, HSymbolAddr):
        if expr.name not in symbol_map:
            raise HIRValidationError(
                f"HSymbolAddr references undeclared external symbol '{expr.name}'",
                loc,
            )
        decl = symbol_map[expr.name]
        if not _type_eq(expr.ty, decl.address_ty):
            raise HIRValidationError(
                f"HSymbolAddr '{expr.name}' type {expr.ty!r} does not match "
                f"declaration address type {decl.address_ty!r}",
                loc,
            )


def _validate_call_site(
    call: HCall,
    sig_table: dict,
    loc,
    allow_multi_return: bool = False,
) -> None:
    """Validate a single call site against the module's signature table.

    Parameters
    ----------
    allow_multi_return:
        When ``True`` (HCallAssign context), the callee may return zero or
        multiple values.  When ``False`` (plain HCall expression), the callee
        must return exactly one value and ``call.return_ty`` must match.
    """
    callee = sig_table.get(call.name)
    if callee is None:
        raise HIRValidationError(
            f"Call to undefined function '{call.name}'", loc
        )
    params, ret_ty = callee
    # Check arity
    if len(call.args) != len(params):
        raise HIRValidationError(
            f"Call to '{call.name}' arity mismatch: expected {len(params)} "
            f"argument(s), got {len(call.args)}",
            loc,
        )
    # Check argument types
    for i, (arg, param) in enumerate(zip(call.args, params)):
        arg_ty = _expr_type(arg)
        if arg_ty is not None and not _type_eq(arg_ty, param.ty):
            raise HIRValidationError(
                f"Call to '{call.name}' arg #{i} type mismatch: expected "
                f"{param.ty!r}, got {arg_ty!r}",
                loc,
            )
    # Return-type validation (only for plain HCall — HCallAssign uses decl)
    if not allow_multi_return:
        if isinstance(ret_ty, tuple):
            raise HIRValidationError(
                f"Call to '{call.name}' returns {len(ret_ty)} values; "
                f"use HCallAssign for multi-return calls",
                loc,
            )
        if not _type_eq(call.return_ty, ret_ty):
            raise HIRValidationError(
                f"Call to '{call.name}' return type mismatch: declaration "
                f"{ret_ty!r}, call.return_ty {call.return_ty!r}",
                loc,
            )


def _body_writes_var(body, var: HVar) -> bool:
    for stmt in body:
        if _stmt_writes_var(stmt, var):
            return True
    return False


def _stmt_writes_var(stmt, var: HVar) -> bool:
    """Check if *stmt* writes to *var*, matching by name (not object identity).

    HIR uses name-based binding, so ``HVar("i", UInt(8))`` constructed by
    different callers is the same variable.  Nested ``HFor`` that re-declares
    the same loop-variable name is conservatively rejected at the HFor level
    (see ``_check_for``), so we don't need to handle shadowing here.
    """
    if isinstance(stmt, HAssign):
        return stmt.target.name == var.name
    if isinstance(stmt, HBitSet):
        return stmt.var.name == var.name
    if isinstance(stmt, HIf):
        return (
            _body_writes_var(stmt.then_body, var)
            or any(_body_writes_var(branch_body, var) for _cond, branch_body in stmt.elif_branches)
            or _body_writes_var(stmt.else_body, var)
        )
    if isinstance(stmt, (HWhile, HPoll)):
        return _body_writes_var(stmt.body, var)
    if isinstance(stmt, HFor):
        return stmt.var.name == var.name or _body_writes_var(stmt.body, var)
    return False


# ---------------------------------------------------------------------------
# Fragment validation
# ---------------------------------------------------------------------------

def validate_hfragment(fragment: HFragment, register_model=None) -> None:
    """Validate an :class:`~rpkbin.codegen.hir.HFragment`.

    Covers declaration invariants, binding access rules, definite-assignment
    tracking, and control-flow termination.

    Parameters
    ----------
    fragment:
        The fragment to validate.
    register_model:
        Optional ``RegisterModel`` protocol instance.  When provided, physical
        legality, overlap, and width checks use the model's generic register
        queries.
    """
    all_regs, phased_pair = _check_fragment_decl(fragment, register_model)
    scratch_regs = set(fragment.scratch_regs)

    bindings_map: dict[str, HFragmentBinding] = {
        b.name: b for b in fragment.bindings
    }
    out_names: set[str] = {b.name for b in fragment.bindings if b.mode == "out"}
    phased_output_to_input = (
        {phased_pair.output_name: phased_pair.input_name}
        if phased_pair is not None else
        {}
    )

    loc = fragment.loc

    # Check banned statements upfront (fast fail)
    _check_fragment_banned_stmts(fragment.body, loc)

    # Walk body with definite-assignment tracking
    result = _walk_fragment_body(
        fragment.body,
        bindings_map,
        out_names,
        _FragmentFlowState(frozenset(), frozenset()),
        phased_output_to_input,
        register_model,
        all_regs,
        scratch_regs,
        loc,
    )

    if result is not None:
        raise HIRValidationError(
            "fragment body does not terminate with HExit on all paths", loc,
        )


# ---------------------------------------------------------------------------
# Fragment declaration checks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FragmentPhasedReusePair:
    input_name: str
    output_name: str
    reg: str


@dataclass(frozen=True)
class _FragmentFlowState:
    assigned: frozenset[str]
    consumed_inputs: frozenset[str]


def _check_fragment_decl(
    fragment: HFragment, register_model
) -> tuple[set[str], _FragmentPhasedReusePair | None]:
    """Check fragment declaration invariants.  Returns the set of all
    physical register names (interface + scratch) for use downstream."""
    loc = fragment.loc

    # Fragment name non-empty
    if not fragment.name:
        raise HIRValidationError("fragment name must be non-empty", loc)

    # -- Binding checks --
    seen_binding_names: set[str] = set()
    seen_bindings: list[HFragmentBinding] = []
    seen_iface_regs: list[str] = []
    phased_pair: _FragmentPhasedReusePair | None = None

    for b in fragment.bindings:
        if not isinstance(b, HFragmentBinding):
            raise HIRValidationError(
                f"fragment binding must be HFragmentBinding, got {type(b).__name__}",
                b.loc or loc,
            )

        bl = b.loc or loc  # prefer binding's own source location

        # name non-empty
        if not b.name:
            raise HIRValidationError(
                "binding name must be a non-empty string", bl,
            )

        # name unique
        if b.name in seen_binding_names:
            raise HIRValidationError(
                f"duplicate binding name '{b.name}'", bl,
            )
        seen_binding_names.add(b.name)

        # reg non-empty
        if not b.reg:
            raise HIRValidationError(
                f"binding '{b.name}' reg must be a non-empty string", bl,
            )

        # mode valid
        if b.mode not in ("in", "out", "inout"):
            raise HIRValidationError(
                f"binding '{b.name}' mode must be 'in'/'out'/'inout', "
                f"got {b.mode!r}",
                bl,
            )

        # ty must be UInt or SInt, not Void
        if not isinstance(b.ty, (UInt, SInt)):
            raise HIRValidationError(
                f"binding '{b.name}' type must be UInt or SInt, "
                f"got {type(b.ty).__name__}",
                bl,
            )

        if register_model is not None:
            # Check that the register is a legal physical storage location.
            # This allows non-allocatable physical registers (e.g. flag registers)
            # to be used as binding registers.
            if not is_physical_register(register_model, b.reg):
                raise HIRValidationError(
                    f"binding '{b.name}' reg '{b.reg}' is not a valid physical "
                    f"register in the current RegisterModel",
                    bl,
                )
            # Width check: the register must be wide enough to hold the value.
            if not can_allocate(register_model, b.reg, b.ty.width):
                raise HIRValidationError(
                    f"register '{b.reg}' cannot hold {b.ty.width}-bit binding '{b.name}'",
                    bl,
                )

        # interface reg unique, except for one exact in/out phased-reuse pair
        for other in seen_bindings:
            same_reg = b.reg == other.reg
            overlaps = same_reg or _regs_overlap(b.reg, other.reg, register_model)
            if not overlaps:
                continue
            if same_reg and {b.mode, other.mode} == {"in", "out"} and phased_pair is None:
                in_binding = b if b.mode == "in" else other
                out_binding = b if b.mode == "out" else other
                phased_pair = _FragmentPhasedReusePair(
                    input_name=in_binding.name,
                    output_name=out_binding.name,
                    reg=b.reg,
                )
                continue
            if same_reg:
                raise HIRValidationError(
                    f"interface register '{b.reg}' overlaps with an existing binding; "
                    f"exact sharing is only allowed for exactly one 'in' binding and "
                    f"one 'out' binding",
                    bl,
                )
            raise HIRValidationError(
                f"interface register '{b.reg}' overlaps with '{other.reg}'",
                bl,
            )
        seen_bindings.append(b)
        seen_iface_regs.append(b.reg)

    # -- Scratch reg checks --
    seen_scratch: list[str] = []
    for s in fragment.scratch_regs:
        if not isinstance(s, str) or not s:
            raise HIRValidationError(
                "scratch register must be a non-empty string", loc,
            )

        # exact duplicate (string equality)
        if s in seen_scratch:
            raise HIRValidationError(
                f"duplicate scratch register '{s}'", loc,
            )

        # aliasing overlap with other scratch registers
        for other in seen_scratch:
            if _regs_overlap(s, other, register_model):
                raise HIRValidationError(
                    f"scratch register '{s}' aliases with scratch "
                    f"register '{other}'",
                    loc,
                )

        seen_scratch.append(s)

        # scratch must not overlap with interface regs
        for iface in seen_iface_regs:
            if _regs_overlap(s, iface, register_model):
                raise HIRValidationError(
                    f"scratch register '{s}' overlaps with interface "
                    f"register '{iface}'",
                    loc,
                )

    # -- Scratch register physical check (requires register_model) --
    # Scratch registers are the allocator's pool for fragment locals;
    # they must be legal physical registers but need not be in the
    # *global* allocatable_registers() pool (the fragment's scratch
    # pool may be a subset chosen by the target).
    if register_model is not None:
        for s in fragment.scratch_regs:
            if not is_physical_register(register_model, s):
                raise HIRValidationError(
                    f"scratch register '{s}' is not a valid physical "
                    f"register in the current RegisterModel",
                    loc,
                )

    # -- Binding name vs physical register name --
    all_regs = set(seen_iface_regs) | set(seen_scratch)
    for b in fragment.bindings:
        bl = b.loc or loc
        if b.name in all_regs:
            raise HIRValidationError(
                f"binding name '{b.name}' matches a physical register name",
                bl,
            )

    return all_regs, phased_pair


def _regs_overlap(r1: str, r2: str, register_model) -> bool:
    """Return True if *r1* and *r2* refer to the same or aliasing register."""
    return r1 == r2 if register_model is None else registers_overlap(
        register_model, r1, r2
    )


# ---------------------------------------------------------------------------
# Fragment banned-statement walker
# ---------------------------------------------------------------------------

def _check_fragment_banned_stmts(body, loc) -> None:
    """Reject HReturn, HWhile, HPoll, HFor, HBreak, HContinue inside fragment."""
    for stmt in body:
        stmt_loc = getattr(stmt, "loc", None) or loc
        if isinstance(stmt, HReturn):
            raise HIRValidationError(
                "HReturn is not allowed inside HFragment", stmt_loc,
            )
        if isinstance(stmt, (HWhile, HPoll, HFor)):
            raise HIRValidationError(
                f"{type(stmt).__name__} is not allowed inside HFragment",
                stmt_loc,
            )
        if isinstance(stmt, (HBreak, HContinue)):
            raise HIRValidationError(
                f"{type(stmt).__name__} is not allowed inside HFragment",
                stmt_loc,
            )
        if isinstance(stmt, HIf):
            _check_fragment_banned_stmts(stmt.then_body, stmt_loc)
            for _cond, branch_body in stmt.elif_branches:
                _check_fragment_banned_stmts(branch_body, stmt_loc)
            _check_fragment_banned_stmts(stmt.else_body, stmt_loc)


# ---------------------------------------------------------------------------
# Fragment body dataflow walker
# ---------------------------------------------------------------------------

def _walk_fragment_body(
    body,
    bindings_map: dict[str, HFragmentBinding],
    out_names: set[str],
    state: _FragmentFlowState | None,
    phased_output_to_input: dict[str, str],
    register_model,
    all_regs: set[str],
    scratch_regs: set[str],
    loc,
) -> _FragmentFlowState | None:
    """Walk a fragment statement list with definite-assignment tracking.

    Parameters
    ----------
    state:
        Flow state before *body*. ``None`` means the path has already
        terminated (unreachable).

    Returns
    -------
    ``None`` if all paths through *body* terminate with ``HExit``;
    otherwise the flow state after fallthrough.
    """
    for i, stmt in enumerate(body):
        stmt_loc = getattr(stmt, "loc", None) or loc

        # --- unreachability check ---
        if state is None:
            raise HIRValidationError(
                "unreachable statement after terminator", stmt_loc,
            )

        # --------------------------------------------------------------
        # HExit — terminator
        # --------------------------------------------------------------
        if isinstance(stmt, HExit):
            for oname in out_names:
                if oname not in state.assigned:
                    raise HIRValidationError(
                        f"out binding '{oname}' not definitely assigned "
                        f"before HExit",
                        stmt_loc,
                    )
            state = None  # terminated

        # --------------------------------------------------------------
        # HAssign
        # --------------------------------------------------------------
        elif isinstance(stmt, HAssign):
            _check_expr_reads(
                stmt.value, bindings_map, out_names, state,
                phased_output_to_input,
                all_regs, scratch_regs, register_model, stmt_loc,
            )
            state = _update_state_for_write(
                stmt.target, bindings_map, state, phased_output_to_input,
                all_regs, scratch_regs, register_model, stmt_loc,
            )

        # --------------------------------------------------------------
        # HCallAssign
        # --------------------------------------------------------------
        elif isinstance(stmt, HCallAssign):
            for arg in stmt.call.args:
                _check_expr_reads(
                    arg, bindings_map, out_names, state,
                    phased_output_to_input,
                    all_regs, scratch_regs, register_model, stmt_loc,
                )
            for target in stmt.targets:
                if target is not None:
                    state = _update_state_for_write(
                        target, bindings_map, state, phased_output_to_input,
                        all_regs, scratch_regs, register_model, stmt_loc,
                    )

        # --------------------------------------------------------------
        # HBitSet
        # --------------------------------------------------------------
        elif isinstance(stmt, HBitSet):
            _check_hvar_in_fragment(
                stmt.var, bindings_map, all_regs, scratch_regs,
                register_model, stmt_loc,
            )
            if stmt.var.name in bindings_map:
                mode = bindings_map[stmt.var.name].mode
                if mode == "in":
                    raise HIRValidationError(
                        f"cannot write to 'in' binding "
                        f"'{stmt.var.name}' via HBitSet",
                        stmt_loc,
                    )
                paired_input = phased_output_to_input.get(stmt.var.name)
                if (
                    mode == "out"
                    and stmt.var.name not in state.assigned
                    and (
                        paired_input is None
                        or paired_input in state.consumed_inputs
                    )
                ):
                    raise HIRValidationError(
                        f"read of unassigned out binding "
                        f"'{stmt.var.name}' in HBitSet",
                        stmt_loc,
                    )
                if mode in ("out", "inout"):
                    state = _update_state_for_write(
                        stmt.var,
                        bindings_map,
                        state,
                        phased_output_to_input,
                        all_regs,
                        scratch_regs,
                        register_model,
                        stmt_loc,
                    )

        # --------------------------------------------------------------
        # HExprStmt
        # --------------------------------------------------------------
        elif isinstance(stmt, HExprStmt):
            _check_expr_reads(
                stmt.expr, bindings_map, out_names, state,
                phased_output_to_input,
                all_regs, scratch_regs, register_model, stmt_loc,
            )

        # --------------------------------------------------------------
        # HStore
        # --------------------------------------------------------------
        elif isinstance(stmt, HStore):
            _check_expr_reads(
                stmt.ptr_expr, bindings_map, out_names, state,
                phased_output_to_input,
                all_regs, scratch_regs, register_model, stmt_loc,
            )
            _check_expr_reads(
                stmt.value_expr, bindings_map, out_names, state,
                phased_output_to_input,
                all_regs, scratch_regs, register_model, stmt_loc,
            )

        # --------------------------------------------------------------
        # HInlineAsm — opaque, no effect on assignment
        # --------------------------------------------------------------
        elif isinstance(stmt, HInlineAsm):
            pass

        # --------------------------------------------------------------
        # HIf
        # --------------------------------------------------------------
        elif isinstance(stmt, HIf):
            # Validate condition reads (all branches see the same state)
            _check_cond_reads(
                stmt.cond, bindings_map, out_names, state,
                phased_output_to_input,
                all_regs, scratch_regs, register_model, stmt_loc,
            )
            for elif_cond, _ in stmt.elif_branches:
                _check_cond_reads(
                    elif_cond, bindings_map, out_names, state,
                    phased_output_to_input,
                    all_regs, scratch_regs, register_model, stmt_loc,
                )

            # Walk each branch from the same incoming state.
            then_result = _walk_fragment_body(
                stmt.then_body,
                bindings_map,
                out_names,
                state,
                phased_output_to_input,
                register_model,
                all_regs,
                scratch_regs,
                stmt_loc,
            )
            elif_results = []
            for _cond, branch_body in stmt.elif_branches:
                r = _walk_fragment_body(
                    branch_body,
                    bindings_map,
                    out_names,
                    state,
                    phased_output_to_input,
                    register_model,
                    all_regs,
                    scratch_regs,
                    stmt_loc,
                )
                elif_results.append(r)
            else_result = _walk_fragment_body(
                stmt.else_body,
                bindings_map,
                out_names,
                state,
                phased_output_to_input,
                register_model,
                all_regs,
                scratch_regs,
                stmt_loc,
            )

            # Collect fall-through results (non-None = branch falls through)
            fallthrough: list[_FragmentFlowState] = []
            if then_result is not None:
                fallthrough.append(then_result)
            for r in elif_results:
                if r is not None:
                    fallthrough.append(r)
            if else_result is not None:
                fallthrough.append(else_result)

            if not fallthrough:
                state = None  # all branches terminate
            else:
                merged_assigned = set(fallthrough[0].assigned)
                merged_consumed = set()
                for r in fallthrough:
                    merged_consumed.update(r.consumed_inputs)
                for r in fallthrough[1:]:
                    merged_assigned &= set(r.assigned)
                state = _FragmentFlowState(
                    frozenset(merged_assigned),
                    frozenset(merged_consumed),
                )

        # --------------------------------------------------------------
        # Anything else
        # --------------------------------------------------------------
        else:
            raise HIRValidationError(
                f"unsupported statement in fragment: {type(stmt).__name__}",
                stmt_loc,
            )

    return state


# ---------------------------------------------------------------------------
# HVar validation in fragment context
# ---------------------------------------------------------------------------

def _check_hvar_in_fragment(
    hvar: HVar,
    bindings_map: dict[str, HFragmentBinding],
    all_regs: set[str],
    scratch_regs: set[str],
    register_model,
    loc,
) -> None:
    """Validate an ``HVar`` occurrence inside a fragment body.

    * If the name matches a binding: check type equality (signedness + width)
      and reg_hint consistency.
    * If the name is a local: check it does not shadow a physical register
      name and any reg_hint stays within fragment.scratch_regs.
    """
    if hvar.name in bindings_map:
        b = bindings_map[hvar.name]
        # Type must match structurally (same type class and width)
        if type(hvar.ty) is not type(b.ty) or hvar.ty.width != b.ty.width:
            raise HIRValidationError(
                f"binding '{hvar.name}' type mismatch: declared {b.ty!r}, "
                f"HVar has {hvar.ty!r}", loc,
            )
        # reg_hint must equal binding.reg when set
        if hvar.reg_hint is not None and hvar.reg_hint != b.reg:
            raise HIRValidationError(
                f"binding '{hvar.name}' reg_hint '{hvar.reg_hint}' "
                f"conflicts with binding reg '{b.reg}'", loc,
            )
    else:
        # Local variable — must not match any physical register name
        if hvar.name in all_regs:
            raise HIRValidationError(
                f"local variable name '{hvar.name}' matches a physical "
                f"register name", loc,
            )
        if hvar.reg_hint is not None:
            if hvar.reg_hint not in scratch_regs:
                raise HIRValidationError(
                    f"local variable '{hvar.name}' reg_hint '{hvar.reg_hint}' "
                    f"is not in fragment scratch_regs",
                    loc,
                )
            if (
                register_model is not None
                and not can_allocate(register_model, hvar.reg_hint, hvar.ty.width)
            ):
                raise HIRValidationError(
                    f"local variable '{hvar.name}' reg_hint '{hvar.reg_hint}' "
                    f"cannot hold {hvar.ty.width}-bit value",
                    loc,
                )


# ---------------------------------------------------------------------------
# Fragment binding write access
# ---------------------------------------------------------------------------

def _update_state_for_write(
    target: HVar,
    bindings_map: dict[str, HFragmentBinding],
    state: _FragmentFlowState,
    phased_output_to_input: dict[str, str],
    all_regs: set[str],
    scratch_regs: set[str],
    register_model,
    loc,
) -> _FragmentFlowState:
    """Check write access for a binding target; return updated flow state."""
    _check_hvar_in_fragment(
        target, bindings_map, all_regs, scratch_regs, register_model, loc,
    )
    assigned = set(state.assigned)
    consumed_inputs = set(state.consumed_inputs)
    if target.name in bindings_map:
        mode = bindings_map[target.name].mode
        if mode == "in":
            raise HIRValidationError(
                f"cannot write to 'in' binding '{target.name}'", loc,
            )
        assigned.add(target.name)
        paired_input = phased_output_to_input.get(target.name)
        if paired_input is not None:
            consumed_inputs.add(paired_input)
    return _FragmentFlowState(frozenset(assigned), frozenset(consumed_inputs))


# ---------------------------------------------------------------------------
# Fragment expression read checker
# ---------------------------------------------------------------------------

def _check_expr_reads(
    expr,
    bindings_map: dict[str, HFragmentBinding],
    out_names: set[str],
    state: _FragmentFlowState,
    phased_output_to_input: dict[str, str],
    all_regs: set[str],
    scratch_regs: set[str],
    register_model,
    loc,
) -> None:
    """Recursively check that no out-binding HVar is read before assignment,
    and that every HVar is valid against its binding declaration."""
    if isinstance(expr, HVar):
        _check_hvar_in_fragment(
            expr, bindings_map, all_regs, scratch_regs, register_model, loc,
        )
        if expr.name in state.consumed_inputs:
            raise HIRValidationError(
                f"read of consumed input binding '{expr.name}'", loc,
            )
        if expr.name in out_names and expr.name not in state.assigned:
            raise HIRValidationError(
                f"read of unassigned out binding '{expr.name}'", loc,
            )
    elif isinstance(expr, (HConst, HSymbolAddr)):
        pass
    elif isinstance(expr, HBinOp):
        _check_expr_reads(
            expr.left, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
        _check_expr_reads(
            expr.right, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
    elif isinstance(expr, HCast):
        _check_expr_reads(
            expr.expr, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
    elif isinstance(expr, HExtract):
        _check_expr_reads(
            expr.expr, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
    elif isinstance(expr, HConcat):
        _check_expr_reads(
            expr.hi, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
        _check_expr_reads(
            expr.lo, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
    elif isinstance(expr, HInsert):
        _check_expr_reads(
            expr.dst, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
        _check_expr_reads(
            expr.value, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
    elif isinstance(expr, HLoad):
        _check_expr_reads(
            expr.ptr_expr, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
    elif isinstance(expr, HCall):
        for arg in expr.args:
            _check_expr_reads(
                arg, bindings_map, out_names, state,
                phased_output_to_input,
                all_regs, scratch_regs, register_model, loc,
            )


def _check_cond_reads(
    cond,
    bindings_map: dict[str, HFragmentBinding],
    out_names: set[str],
    state: _FragmentFlowState,
    phased_output_to_input: dict[str, str],
    all_regs: set[str],
    scratch_regs: set[str],
    register_model,
    loc,
) -> None:
    """Check reads in a condition expression (HCmp, HBitTest, HLogical, HNot)."""
    if isinstance(cond, HCmp):
        _check_expr_reads(
            cond.left, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
        _check_expr_reads(
            cond.right, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
    elif isinstance(cond, HBitTest):
        _check_hvar_in_fragment(
            cond.var, bindings_map, all_regs, scratch_regs, register_model, loc,
        )
        if cond.var.name in bindings_map:
            _check_expr_reads(
                cond.var, bindings_map, out_names, state,
                phased_output_to_input,
                all_regs, scratch_regs, register_model, loc,
            )
    elif isinstance(cond, HLogical):
        _check_cond_reads(
            cond.left, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
        _check_cond_reads(
            cond.right, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
    elif isinstance(cond, HNot):
        _check_cond_reads(
            cond.expr, bindings_map, out_names, state,
            phased_output_to_input,
            all_regs, scratch_regs, register_model, loc,
        )
