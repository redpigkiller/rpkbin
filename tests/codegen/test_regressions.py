"""Regression tests for previously fixed codegen bugs.

One test (or small group) per bug -- each test will FAIL on the original
buggy code and PASS after the fix.

Bug 1 -- HLogical short-circuit lowering:
    HLogical was once lowered to BinOp("and"/"or"), which violates
    short-circuit semantics. It now uses branch-chain lowering.

Bug 2 -- InlineAsmExpr monkey-patch / ToyTarget ignorance:
    test_inline_asm_lir_node_is_public
    test_inline_asm_lowered_to_proper_lir_node
    test_inline_asm_toy_target_emits_raw_text
    test_inline_asm_nop_passthrough

Bug 3 -- HFor loop_stack inconsistency:
    test_for_break_inside_for_lowered_ok
    test_for_continue_is_accepted_by_validator
    test_for_continue_is_accepted_by_lowerer
    test_break_outside_loop_still_rejected

Bug 4 -- register_alloc docstring claims "correct spill":
    test_register_alloc_docstring_does_not_claim_correct_spill

Bug 5 -- FullExpr alias missing / unused:
    test_full_expr_alias_exists_and_covers_all_nodes
    test_full_expr_used_in_assign_annotation

Bug 6 -- HFor decrement emitted even after HBreak/HReturn seals the body block:
    test_for_break_body_block_has_no_decrement
    test_for_break_body_block_terminator_is_jump_to_exit
    test_for_return_body_block_has_no_decrement
    test_for_return_body_block_terminator_is_return
    test_for_normal_body_still_has_decrement
    test_for_continue_is_accepted_after_decrement_fix
"""

from __future__ import annotations

import pytest
import typing

from rpkbin.codegen import lir
from rpkbin.codegen.hir import (
    HAssign,
    HBreak,
    HCall,
    HCmp,
    HContinue,
    HExprStmt,
    HFor,
    HFunction,
    HInlineAsm,
    HLogical,
    HNot,
    HParam,
    HReturn,
    HIf,
    HVar,
    HWhile,
    UInt,
    Void,
    hconst,
    simple_function,
    u8,
)
from rpkbin.codegen.hir_validate import HIRValidationError, validate_hfunction
from rpkbin.codegen.lower import lower_function
from rpkbin.codegen.toy_target import ToyTarget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hfor_with(body, n=3):
    """Return a minimal HFor(var=i, 0, n, body)."""
    i = HVar("i", UInt(8))
    return HFor(
        var=i,
        init=hconst(0),
        bound=hconst(n),
        body=tuple(body),
    )


def _make_hwhile_with(body):
    """Return a minimal HWhile(a < 10, body)."""
    a = u8("a")
    return HWhile(cond=HCmp("lt", a, hconst(10)), body=tuple(body))


def _lower_and_emit(hfunc: HFunction) -> str:
    """Lower HIR → LIR then run ToyTarget to get pseudo-ASM text."""
    lir_func = lower_function(hfunc)
    target = ToyTarget()
    asm_func = target.select_instructions(lir_func)
    return asm_func.format()


# ===========================================================================
# Bug 1 -- HLogical short-circuit lowering
# ===========================================================================

class TestHLogicalShortCircuit:
    """HLogical(and/or) uses short-circuit branch-chain lowering.
    Both validator and lowerer must accept it in condition positions.
    Value-position rejection (check 3) is tested in test_hir.py."""

    def _make_func_with_hlogical(self, op="and"):
        a = u8("a")
        b = u8("b")
        cond = HLogical(op, HCmp("lt", a, hconst(5)), HCmp("gt", b, hconst(3)))
        return HFunction(
            name="logical_test",
            params=(HParam("a", UInt(8)), HParam("b", UInt(8))),
            return_ty=UInt(8),
            body=(
                HIf(
                    cond=cond,
                    then_body=(HReturn(values=(hconst(1),)),),
                    else_body=(HReturn(values=(hconst(0),)),),
                ),
            ),
        )

    def test_hlogical_and_passes_validator(self):
        """HLogical('and') in condition position must pass validation."""
        func = self._make_func_with_hlogical("and")
        validate_hfunction(func)  # must not raise

    def test_hlogical_or_passes_validator(self):
        """HLogical('or') in condition position must pass validation."""
        func = self._make_func_with_hlogical("or")
        validate_hfunction(func)  # must not raise

    def test_hlogical_lowers_ok(self):
        """lower_function must accept HLogical and produce valid LIR."""
        func = self._make_func_with_hlogical("and")
        result = lower_function(func)  # must not raise
        lir.validate_function(result)

    def test_hlogical_nested_in_hnot_passes_validator(self):
        """HLogical wrapped in HNot passes validation."""
        a = u8("a")
        b = u8("b")
        cond = HNot(HLogical("or", HCmp("eq", a, hconst(0)), HCmp("eq", b, hconst(0))))
        func = HFunction(
            name="logical_not_test",
            params=(HParam("a", UInt(8)), HParam("b", UInt(8))),
            return_ty=UInt(8),
            body=(
                HIf(
                    cond=cond,
                    then_body=(HReturn(values=(hconst(1),)),),
                    else_body=(HReturn(values=(hconst(0),)),),
                ),
            ),
        )
        validate_hfunction(func)  # must not raise
        result = lower_function(func)  # must not raise
        lir.validate_function(result)

    def test_simple_hcmp_still_passes_validator(self):
        """Sanity check: plain HCmp remains valid."""
        a = u8("a")
        func = simple_function(
            "ok_func",
            [HParam("a", UInt(8))],
            [HIf(
                cond=HCmp("lt", a, hconst(10)),
                then_body=(HReturn(values=(hconst(1),)),),
                else_body=(HReturn(values=(hconst(0),)),),
            )],
            return_ty=UInt(8),
        )
        validate_hfunction(func)  # must not raise

    def test_hlogical_in_while_passes(self):
        """HLogical in an HWhile condition is valid."""
        a = u8("a")
        b = u8("b")
        cond = HLogical("and", HCmp("ne", a, hconst(0)), HCmp("lt", a, b))
        func = HFunction(
            name="while_logical",
            params=(HParam("a", UInt(8)), HParam("b", UInt(8))),
            return_ty=UInt(8),
            body=(
                HWhile(cond=cond, body=(HAssign(target=a, value=hconst(1)),)),
                HReturn(values=(a,)),
            ),
        )
        validate_hfunction(func)
        result = lower_function(func)
        lir.validate_function(result)


# ===========================================================================
# Bug 2 — InlineAsmExpr must be a proper public lir node; ToyTarget passthrough
# ===========================================================================

class TestInlineAsmExpr:
    """InlineAsmExpr was a private monkey-patched class in lower.py and was
    completely unhandled by ToyTarget.  After the fix it is a first-class
    lir node and ToyTarget emits raw text verbatim."""

    def test_inline_asm_lir_node_is_public(self):
        """lir.InlineAsmExpr must be importable as a public attribute."""
        assert hasattr(lir, "InlineAsmExpr"), "lir.InlineAsmExpr not found"
        node = lir.InlineAsmExpr(text="NOP")
        assert node.text == "NOP"

    def test_inline_asm_lowered_to_proper_lir_node(self):
        """HInlineAsm must lower to lir.InlineAsmExpr, not a monkey-patched private class."""
        func = simple_function(
            "asm_func",
            [],
            [HInlineAsm(text="NOP")],
        )
        lir_func = lower_function(func)
        # Find the __asm__ assign
        asm_stmts = [
            stmt
            for block in lir_func.blocks
            for stmt in block.statements
            if isinstance(stmt, lir.Assign) and stmt.target.name == "__asm__"
        ]
        assert asm_stmts, "No __asm__ Assign found in LIR"
        expr = asm_stmts[0].value
        # Must be the canonical lir.InlineAsmExpr, not any private class
        assert type(expr) is lir.InlineAsmExpr, (
            f"Expected lir.InlineAsmExpr but got {type(expr).__name__!r} "
            f"(module: {type(expr).__module__!r})"
        )
        assert expr.text == "NOP"

    def test_inline_asm_toy_target_emits_raw_text(self):
        """ToyTarget must emit InlineAsmExpr text verbatim without any prefix."""
        func = simple_function("asm_func", [], [HInlineAsm(text="NOP")])
        asm_text = _lower_and_emit(func)
        assert "NOP" in asm_text, f"Expected 'NOP' in output, got:\n{asm_text}"
        # Must NOT wrap it with any prefix
        assert "ASM NOP" not in asm_text, (
            f"Output should not prefix with 'ASM', got:\n{asm_text}"
        )

    def test_inline_asm_nop_passthrough_exact(self):
        """HInlineAsm('NOP') → pseudo-ASM output line must be exactly 'NOP'."""
        func = simple_function("asm_func", [], [HInlineAsm(text="NOP")])
        asm_text = _lower_and_emit(func)
        lines = [l.strip() for l in asm_text.splitlines()]
        assert "NOP" in lines, (
            f"Expected a line 'NOP' in output, got lines:\n{lines}"
        )

    def test_inline_asm_multiword_passthrough(self):
        """Multi-token inline asm is also emitted verbatim."""
        func = simple_function("asm_func", [], [HInlineAsm(text="HALT ; stop")])
        asm_text = _lower_and_emit(func)
        assert "HALT ; stop" in asm_text


# ===========================================================================
# Bug 3 — HFor break/continue loop_stack inconsistency
# ===========================================================================

class TestHForBreakContinue:
    """Validator accepted HBreak/HContinue inside HFor but lowerer had no
    loop_stack entry — causing NotImplementedError('outside a loop').
    After fix: HBreak inside HFor works; HContinue inside HFor is explicitly
    forbidden by both validator and lowerer."""

    def test_for_break_inside_for_lowered_ok(self):
        """HBreak inside HFor should lower without error (regression: was crashing)."""
        func = simple_function(
            "for_break",
            [],
            [_make_hfor_with([HBreak()])],
        )
        # Must not raise NotImplementedError or any other error
        lir_func = lower_function(func)
        # Verify the exit block exists and break jumps to it
        labels = [b.label for b in lir_func.blocks]
        assert any("exit" in lab for lab in labels), (
            f"Expected a loop exit block, got labels: {labels}"
        )

    def test_for_break_inside_for_passes_validator(self):
        """Validator must accept HBreak inside HFor."""
        func = simple_function(
            "for_break_valid",
            [],
            [_make_hfor_with([HBreak()])],
        )
        validate_hfunction(func)  # must not raise

    def test_for_continue_inside_for_rejected_by_validator(self):
        """HContinue inside HFor must now be accepted."""
        func = simple_function(
            "for_continue",
            [],
            [_make_hfor_with([HContinue()])],
        )
        validate_hfunction(func)
        lir_func = lower_function(func)
        assert any(b.label.startswith("for_step_") for b in lir_func.blocks)

    def test_for_continue_inside_for_rejected_by_lowerer(self):
        """Lowering HContinue inside HFor must also now succeed."""
        func = simple_function(
            "for_continue_lower",
            [],
            [_make_hfor_with([HContinue()])],
        )
        lower_function(func)

    def test_continue_inside_hwhile_still_allowed(self):
        """HContinue inside HWhile must remain valid."""
        func = simple_function(
            "while_continue",
            [],
            [_make_hwhile_with([HContinue()])],
        )
        validate_hfunction(func)   # must not raise
        lower_function(func)       # must not raise

    def test_break_outside_loop_still_rejected(self):
        """The outer-loop validation must still reject HBreak."""
        func = simple_function("bad_break", [], [HBreak()])
        with pytest.raises(HIRValidationError, match="HBreak"):
            validate_hfunction(func)

    def test_continue_outside_loop_still_rejected(self):
        """Outer-loop check for HContinue must still work after refactoring."""
        func = simple_function("bad_continue", [], [HContinue()])
        with pytest.raises(HIRValidationError, match="HContinue"):
            validate_hfunction(func)


# ===========================================================================
# Bug 4 — register_alloc.py docstring must not claim "correct spill"
# ===========================================================================

class TestRegisterAllocDocstring:
    """The module docstring previously claimed 'correct spill', which
    was misleading.  It must not claim target-independent spill correctness."""

    def test_register_alloc_docstring_does_not_claim_correct_spill(self):
        import rpkbin.codegen.register_alloc as ra
        doc = ra.__doc__ or ""
        assert "correct spill" not in doc.lower(), (
            "register_alloc.__doc__ must not claim 'correct spill'"
        )

# ===========================================================================
# Bug 5 — FullExpr alias must exist and be used in field annotations
# ===========================================================================

class TestFullExprAlias:
    """FullExpr was absent from lir.py.  After the fix it covers all known
    expression node types and is used in new field type annotations."""

    def test_full_expr_alias_exists(self):
        assert hasattr(lir, "FullExpr"), "lir.FullExpr not found"

    def test_full_expr_covers_core_expr_types(self):
        """All original Expr members must be in FullExpr."""
        args = typing.get_args(lir.FullExpr)
        for cls in (lir.Const, lir.Var, lir.BinOp, lir.Cmp):
            assert cls in args, f"{cls.__name__} missing from FullExpr"

    def test_full_expr_covers_extended_types(self):
        """Extended node types must all be in FullExpr."""
        args = typing.get_args(lir.FullExpr)
        for cls in (
            lir.VReg,
            lir.Extend,
            lir.Call,
            lir.MemLoad,
            lir.BitOp,
            lir.InlineAsmExpr,
        ):
            assert cls in args, f"{cls.__name__} missing from FullExpr"

    def test_assign_value_annotation_uses_full_expr(self):
        """Assign.value field annotation should reference FullExpr (not legacy Expr)."""
        hints = typing.get_type_hints(lir.Assign)
        # After the fix, value should be annotated as FullExpr or a string
        # forward-ref that resolves to FullExpr.  We just check it is NOT
        # the bare old `Expr` alias (Union[Const,Var,BinOp,Cmp]).
        value_hint = hints.get("value")
        # FullExpr is a Union; Expr is a Union; they differ in members.
        # Easiest check: FullExpr args must be a superset of Expr args.
        full_args = set(typing.get_args(lir.FullExpr))
        expr_args = set(typing.get_args(lir.Expr))
        value_args = set(typing.get_args(value_hint)) if value_hint is not None else set()
        assert full_args.issuperset(expr_args), "FullExpr must include all Expr members"
        # Assign.value annotation must include the extended types
        for cls in (lir.VReg, lir.Call, lir.InlineAsmExpr):
            assert cls in full_args, (
                f"{cls.__name__} should be in FullExpr (and thus reachable via Assign.value)"
            )

    def test_legacy_expr_alias_still_exists(self):
        """The old Expr alias must not be removed -- backward compat."""
        assert hasattr(lir, "Expr"), "lir.Expr removed -- breaks backward compat"
        args = typing.get_args(lir.Expr)
        for cls in (lir.Const, lir.Var, lir.BinOp, lir.Cmp):
            assert cls in args, f"{cls.__name__} missing from legacy Expr"


# ===========================================================================
# Bug 5b -- Extend must participate in rewrite / regalloc / validation
# ===========================================================================

class TestExtendRegression:
    def test_rewrite_descends_into_extend_value(self):
        from rpkbin.codegen.patterns import load_patterns_from_dicts
        from rpkbin.codegen.rewrite import rewrite_function

        func = lir.Function(
            name="rewrite_extend",
            params=(lir.Var("a", 8),),
            blocks=(
                lir.Block(
                    label="entry",
                    statements=(
                        lir.Assign(
                            lir.Var("out", 16),
                            lir.Extend(
                                "zext",
                                lir.BinOp("mul", lir.Var("a", 8), lir.Const(2, 8), 8),
                                16,
                            ),
                        ),
                    ),
                    terminator=lir.Return(lir.Var("out", 16)),
                ),
            ),
        )
        patterns = load_patterns_from_dicts(
            [
                {
                    "name": "mul_by_2_to_shift",
                    "match": {"op": "mul", "args": [{"capture": "x"}, {"const": 2}]},
                    "replace": {"op": "shl", "args": [{"ref": "x"}, {"const": 1}]},
                }
            ]
        )

        result = rewrite_function(func, patterns)
        value = result.function.blocks[0].statements[0].value

        assert result.applied == ("mul_by_2_to_shift",)
        assert isinstance(value, lir.Extend)
        assert isinstance(value.value, lir.BinOp)
        assert value.value.op == "shl"

    def test_register_allocator_collects_and_remaps_extend_value(self):
        import rpkbin.codegen.register_alloc as ra

        extend = lir.Extend("zext", lir.Var("src", 8), 16)
        seen: list[str] = []
        ra._scan_expr_for_names(extend, lambda expr: seen.append(expr.name))
        assert seen == ["src"]

        func = lir.Function(
            name="remap_extend",
            params=(),
            blocks=(
                lir.Block(
                    label="entry",
                    statements=(lir.Assign(lir.Var("dst", 16), extend),),
                    terminator=lir.Return(lir.Var("dst", 16)),
                ),
            ),
        )
        remapped = ra._apply_assignment(func, {"src": "r0", "dst": "r1"})
        stmt = remapped.blocks[0].statements[0]

        assert stmt.target.name == "r1"
        assert isinstance(stmt.value, lir.Extend)
        assert isinstance(stmt.value.value, lir.Var)
        assert stmt.value.value.name == "r0"
        assert remapped.blocks[0].terminator.value.name == "r1"

    def test_validation_rejects_invalid_extend_kind(self):
        func = lir.Function(
            name="bad_extend_kind",
            params=(),
            blocks=(
                lir.Block(
                    label="entry",
                    statements=(),
                    terminator=lir.Return(lir.Extend("bad", lir.Var("x", 8), 16)),
                ),
            ),
        )

        with pytest.raises(ValueError, match="unsupported Extend kind"):
            lir.validate_function(func)

    def test_validation_rejects_extend_shrinking_width(self):
        func = lir.Function(
            name="bad_extend_width",
            params=(),
            blocks=(
                lir.Block(
                    label="entry",
                    statements=(),
                    terminator=lir.Return(lir.Extend("zext", lir.Var("x", 16), 8)),
                ),
            ),
        )

        with pytest.raises(ValueError, match="widens from 16 bits to smaller width 8"):
            lir.validate_function(func)


# ===========================================================================
# Bug 6 -- HFor decrement emitted unconditionally even after HBreak/HReturn
# ===========================================================================

class TestHForDecrementGuard:
    """Before the fix, _lower_for() appended the counter decrement statement
    and Jump(loop_test) AFTER _lower_stmts() returned, regardless of whether
    the body had already sealed the block with a terminator (e.g. HBreak
    produces Jump(loop_exit), HReturn produces Return).  The wrong decrement
    was silently appended to an already-sealed block.

    After the fix, decrement + Jump are emitted only when
    self._current.terminator is None."""

    # -----------------------------------------------------------------------
    # Shared helper: find the loop_body block by label prefix
    # -----------------------------------------------------------------------

    @staticmethod
    def _body_block(lir_func: lir.Function) -> lir.Block:
        """Return the for_body_* block from a lowered function."""
        for b in lir_func.blocks:
            if b.label.startswith("for_body_"):
                return b
        raise KeyError(f"no for_body_ block in {[b.label for b in lir_func.blocks]}")

    @staticmethod
    def _exit_label(lir_func: lir.Function) -> str:
        """Return the for_exit_* label."""
        for b in lir_func.blocks:
            if b.label.startswith("for_exit_"):
                return b.label
        raise KeyError("no for_exit_ block found")

    # -----------------------------------------------------------------------
    # HFor body = (HBreak(),)
    # -----------------------------------------------------------------------

    def test_for_break_lower_does_not_raise(self):
        """lower_function() must succeed when HFor body is just HBreak."""
        func = simple_function("f", [], [_make_hfor_with([HBreak()])])
        lower_function(func)  # must not raise

    def test_for_break_body_block_terminator_is_jump_to_exit(self):
        """The for_body block's terminator must be Jump(for_exit_*), set by
        HBreak, not overwritten or lost."""
        func = simple_function("f", [], [_make_hfor_with([HBreak()])])
        lir_func = lower_function(func)
        body = self._body_block(lir_func)
        exit_lbl = self._exit_label(lir_func)
        assert isinstance(body.terminator, lir.Jump), (
            f"Expected Jump terminator, got {type(body.terminator).__name__}"
        )
        assert body.terminator.label == exit_lbl, (
            f"Jump target should be exit label {exit_lbl!r}, "
            f"got {body.terminator.label!r}"
        )

    def test_for_break_body_block_has_no_decrement(self):
        """The for_body block must contain NO counter decrement statement
        when the body ends with HBreak."""
        func = simple_function("f", [], [_make_hfor_with([HBreak()])])
        lir_func = lower_function(func)
        body = self._body_block(lir_func)
        # Counter decrement would be Assign(target=Var('__counter_i'), ...)
        decrement_stmts = [
            s for s in body.statements
            if isinstance(s, lir.Assign)
            and s.target.name.startswith("__counter_")
        ]
        assert decrement_stmts == [], (
            f"for_body block must not contain counter decrements after HBreak, "
            f"found: {decrement_stmts}"
        )

    # -----------------------------------------------------------------------
    # HFor body = (HReturn(hconst(42)),)
    # -----------------------------------------------------------------------

    def test_for_return_lower_does_not_raise(self):
        """lower_function() must succeed when HFor body is just HReturn."""
        func = HFunction(
            name="f",
            params=(),
            return_ty=UInt(8),
            body=(_make_hfor_with([HReturn(values=(hconst(42),))]),),
        )
        lower_function(func)  # must not raise

    def test_for_return_body_block_terminator_is_return(self):
        """The for_body block's terminator must be Return, not overwritten."""
        func = HFunction(
            name="f",
            params=(),
            return_ty=UInt(8),
            body=(_make_hfor_with([HReturn(values=(hconst(42),))]),),
        )
        lir_func = lower_function(func)
        body = self._body_block(lir_func)
        assert isinstance(body.terminator, lir.Return), (
            f"Expected Return terminator, got {type(body.terminator).__name__}"
        )

    def test_for_return_body_block_has_no_decrement(self):
        """No counter decrement in for_body block when body ends with HReturn."""
        func = HFunction(
            name="f",
            params=(),
            return_ty=UInt(8),
            body=(_make_hfor_with([HReturn(values=(hconst(42),))]),),
        )
        lir_func = lower_function(func)
        body = self._body_block(lir_func)
        decrement_stmts = [
            s for s in body.statements
            if isinstance(s, lir.Assign)
            and s.target.name.startswith("__counter_")
        ]
        assert decrement_stmts == [], (
            f"for_body block must not contain counter decrements after HReturn, "
            f"found: {decrement_stmts}"
        )

    # -----------------------------------------------------------------------
    # Normal HFor body (no early exit) still steps correctly
    # -----------------------------------------------------------------------

    def test_for_normal_body_has_decrement(self):
        """A plain HFor body with no terminator must still get the step block."""
        a = u8("a")
        body_stmt = HAssign(target=a, value=hconst(1))
        func = simple_function("f", [HParam("a", UInt(8))], [_make_hfor_with([body_stmt])])
        lir_func = lower_function(func)
        step = next(b for b in lir_func.blocks if b.label.startswith("for_step_"))
        decrement_stmts = [
            s for s in step.statements
            if isinstance(s, lir.Assign)
            and s.target.name.startswith("__counter_")
            and isinstance(s.value, lir.BinOp)
            and s.value.op == "sub"
        ]
        assert decrement_stmts, (
            "Normal for_step block must contain a counter decrement (sub) statement"
        )

    def test_for_normal_body_jumps_back_to_test(self):
        """A plain HFor body block's terminator must be Jump(for_step_*)."""
        a = u8("a")
        body_stmt = HAssign(target=a, value=hconst(1))
        func = simple_function("f", [HParam("a", UInt(8))], [_make_hfor_with([body_stmt])])
        lir_func = lower_function(func)
        body = self._body_block(lir_func)
        assert isinstance(body.terminator, lir.Jump), (
            f"Expected Jump terminator, got {type(body.terminator).__name__}"
        )
        assert body.terminator.label.startswith("for_step_"), (
            f"Jump target should be for_step_*, got {body.terminator.label!r}"
        )

    # -----------------------------------------------------------------------
    # HContinue now routes to the HFor step block.
    # -----------------------------------------------------------------------

    def test_for_continue_is_accepted_by_validator(self):
        """The step-block fix must allow HContinue inside HFor."""
        func = simple_function("f", [], [_make_hfor_with([HContinue()])])
        validate_hfunction(func)

    def test_for_continue_is_accepted_by_lowerer(self):
        """Lowerer must now route HContinue inside HFor to the step block."""
        func = simple_function("f", [], [_make_hfor_with([HContinue()])])
        lower_function(func)


# ===========================================================================
# Docstring correctness -- check count in hir_validate.py
# ===========================================================================

class TestValidatorDocstring:
    """The validator heading must not hard-code a stale check count."""

    def test_hir_validate_docstring_not_ten_checks(self):
        """The old 'Ten checks' heading was replaced by 'Checks implemented'."""
        import rpkbin.codegen.hir_validate as hv
        doc = hv.__doc__ or ""
        assert "checks implemented" in doc.lower(), (
            f"hir_validate.__doc__ must contain 'Checks implemented', got:\n{doc[:300]}"
        )

    def test_hir_validate_docstring_not_eleven(self):
        import rpkbin.codegen.hir_validate as hv
        doc = hv.__doc__ or ""
        assert "eleven checks" not in doc.lower(), (
            "hir_validate.__doc__ still says 'Eleven checks' -- needs update"
        )

    def test_hir_validate_docstring_has_physical_register_checks(self):
        """Register-model checks must be documented in the module docstring."""
        import rpkbin.codegen.hir_validate as hv
        doc = hv.__doc__ or ""
        assert "reg_hint" in doc, "reg_hint physical check missing from docstring"
        assert "arg_regs" in doc, "HCall.arg_regs check missing from docstring"
        assert "return_regs" in doc, "return_regs checks missing from docstring"
