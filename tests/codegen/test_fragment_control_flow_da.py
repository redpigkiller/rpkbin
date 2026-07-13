"""HFragment structured control flow and definite-assignment tests.

This test file covers the contract for structured control flow inside HFragment:

* HIf supported with correct definite-assignment (DA) merge semantics.
* HWhile / HPoll / HFor / HBreak / HContinue explicitly rejected with clear errors
  at validate_hfragment() before the lowerer is ever reached.
* Lowering HIf through run_codegen_from_fragment() with a register model.

Generic: no UC-specific registers, targets, or MCU knowledge.
"""

import pytest

from rpkbin.codegen.hir import (
    HAssign, HBreak, HCmp, HContinue, HExit,
    HFor, HFragment, HFragmentBinding, HIf, HPoll, HReturn,
    HVar, HWhile,
    UInt,
    hconst,
)
from rpkbin.codegen.hir_validate import (
    HIRValidationError,
    validate_hfragment,
)
from rpkbin.codegen.lower import lower_fragment
from rpkbin.codegen import lir
from rpkbin.codegen.lir import SourceLoc
from rpkbin.codegen.pipeline import run_codegen_from_fragment

_LOC = SourceLoc("test.rpk", 1, 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mkbinding(name, ty=UInt(8), reg="r0", mode="in"):
    return HFragmentBinding(name=name, ty=ty, reg=reg, mode=mode)


def _simple_fragment(name="frag", bindings=(), scratch=(), body=()):
    return HFragment(
        name=name,
        bindings=bindings,
        scratch_regs=scratch,
        body=body,
        loc=_LOC,
    )


# ---------------------------------------------------------------------------
# Minimal RegisterModel for lowering/pipeline tests
# ---------------------------------------------------------------------------

class _SimpleRegModel:
    """Minimal generic RegisterModel for pipeline tests.

    Registers: g0–g7 (8-bit general purpose), g8–g15 (16-bit wide).
    No aliasing.  No spill slots (fragment-only allocation).
    """

    _REGS_8 = [f"g{i}" for i in range(8)]   # g0..g7 — 8-bit
    _REGS_16 = [f"g{i}" for i in range(8, 16)]  # g8..g15 — 16-bit

    def allocatable_registers(self):
        return self._REGS_8 + self._REGS_16

    def register_width(self, reg):
        if reg in self._REGS_8:
            return 8
        if reg in self._REGS_16:
            return 16
        raise ValueError(f"unknown register: {reg!r}")

    def register_aliases(self):
        return []  # no aliasing

    def spill_slots(self):
        return []  # no spill: scratch-only fragment allocation


# ============================================================================
# Section A: HIf definite-assignment merge
# ============================================================================

class TestHIfDAMerge:
    """Definite-assignment correctness for HIf branches."""

    # A1: if/else both assign out → accepted
    def test_if_else_both_assign_out_accepted(self):
        """Both branches write out binding — fragment is valid."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        validate_hfragment(frag)  # must not raise

    # A2: only then assigns out, else falls through → rejected
    def test_only_then_assigns_out_rejected(self):
        """then assigns out, else does not — not definitely assigned after."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                ),
                # else falls through without assigning y
                loc=_LOC,
            ),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
            validate_hfragment(frag)

    # A3: then exits, else assigns out → accepted
    def test_then_exits_else_assigns_accepted(self):
        """then branch exits (HExit); else assigns out and falls through → valid."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                ),
                loc=_LOC,
            ),
            # else falls through here with y assigned
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        validate_hfragment(frag)  # must not raise

    # A4: then assigns, else exits → accepted
    def test_then_assigns_else_exits_accepted(self):
        """then assigns out and falls through; else exits → valid."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        validate_hfragment(frag)  # must not raise

    # A5: both branches HExit without assigning required out binding → rejected
    def test_both_branches_exit_without_assigning_out_rejected(self):
        """Both HExit branches reached without assigning out binding → rejected.

        Every HExit is a reachable fragment exit path.  All reachable HExit
        points must have all required out bindings already assigned.
        """
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(HExit(loc=_LOC),),
                else_body=(HExit(loc=_LOC),),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
            validate_hfragment(frag)

    # A5b: both exit after assigning out → accepted
    def test_both_branches_exit_after_assigning_accepted(self):
        """Both branches assign out then exit — valid, no fallthrough needed."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        validate_hfragment(frag)  # must not raise

    # A6: read out before assignment inside then-branch → rejected
    def test_read_out_before_assignment_in_then_rejected(self):
        """Reading out binding before writing in then-branch is rejected."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    # read y before assigning it — illegal for out binding
                    HAssign(
                        target=HVar("tmp", UInt(8)),
                        value=HVar("y", UInt(8)),
                        loc=_LOC,
                    ),
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        with pytest.raises(HIRValidationError, match="read of unassigned out"):
            validate_hfragment(frag)

    # A7: read out before assignment inside else-branch → rejected
    def test_read_out_before_assignment_in_else_rejected(self):
        """Reading out binding before writing in else-branch is rejected."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(
                    # read y before assigning — illegal for out binding
                    HAssign(
                        target=HVar("tmp", UInt(8)),
                        value=HVar("y", UInt(8)),
                        loc=_LOC,
                    ),
                    HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        with pytest.raises(HIRValidationError, match="read of unassigned out"):
            validate_hfragment(frag)

    # A8: elif — conservative: only then and elif assign, no else → rejected
    def test_elif_no_else_conservative_rejected(self):
        """if/elif without else: only some branches assign → rejected."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                ),
                elif_branches=(
                    (HCmp("ne", hconst(1), hconst(2)), (
                        HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                    )),
                ),
                # no else: implicit fallthrough without assigning y
                loc=_LOC,
            ),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
            validate_hfragment(frag)

    # A9: elif — all branches (then + elif + else) assign → accepted
    def test_elif_all_branches_assign_accepted(self):
        """if/elif/else all assigning out → accepted."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                elif_branches=(
                    (HCmp("ne", hconst(1), hconst(2)), (
                        HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                        HExit(loc=_LOC),
                    )),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(30), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        validate_hfragment(frag)  # must not raise

    # A10: elif where then exits, elif assigns, else exits → only elif fallthrough
    def test_elif_then_exits_else_exits_elif_fallthrough(self):
        """then exits, elif assigns+falls through, else exits → out assigned after."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                elif_branches=(
                    (HCmp("ne", hconst(1), hconst(2)), (
                        HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                        # falls through
                    )),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(30), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
            # only elif falls through, with y assigned
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        validate_hfragment(frag)  # must not raise

    # A11: nested HIf — inner if/else both assign → outer can use it
    def test_nested_if_both_inner_branches_assign(self):
        """Nested HIf: inner if/else both assign out → assigned after outer if."""
        out = _mkbinding("y", mode="out", reg="g1")
        inp = _mkbinding("x", mode="in", reg="g0")
        cond_outer = HCmp("eq", hconst(1), hconst(1))
        cond_inner = HCmp("ne", hconst(2), hconst(2))
        body = (
            HIf(
                cond=cond_outer,
                then_body=(
                    HIf(
                        cond=cond_inner,
                        then_body=(
                            HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                        ),
                        else_body=(
                            HAssign(target=HVar("y", UInt(8)), value=hconst(11), loc=_LOC),
                        ),
                        loc=_LOC,
                    ),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                ),
                loc=_LOC,
            ),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", bindings=(inp, out), body=body)
        validate_hfragment(frag)  # must not raise

    # A12: nested HIf — inner if without else → outer merge conservative
    def test_nested_if_inner_no_else_conservative(self):
        """Nested HIf without inner else: outer merge is conservative."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond_outer = HCmp("eq", hconst(1), hconst(1))
        cond_inner = HCmp("ne", hconst(2), hconst(2))
        body = (
            HIf(
                cond=cond_outer,
                then_body=(
                    HIf(
                        cond=cond_inner,
                        then_body=(
                            HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                        ),
                        # inner: no else → y may not be assigned
                        loc=_LOC,
                    ),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                ),
                loc=_LOC,
            ),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        # then-branch: y may not be assigned (inner if has no else).
        # else-branch: y definitely assigned.
        # intersection → y not definitely assigned.
        with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
            validate_hfragment(frag)

    # A13: pre-HIf assignment + if without else → out remains assigned after
    def test_pre_if_assign_keeps_assigned_after_if_no_else(self):
        """Out assigned before HIf: stays assigned even if if-body doesn't write it."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            # Assign y before the if
            HAssign(target=HVar("y", UInt(8)), value=hconst(5), loc=_LOC),
            HIf(
                cond=cond,
                then_body=(
                    # If taken: reassign y
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                ),
                # No else
                loc=_LOC,
            ),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", bindings=(out,), body=body)
        validate_hfragment(frag)  # y assigned before if → still assigned after


# ============================================================================
# Section B: Loop constructs explicitly rejected (unsupported in fragment)
# ============================================================================

class TestLoopConstructsRejected:
    """HWhile / HPoll / HFor / HBreak / HContinue must be rejected with clear errors."""

    # B1: HWhile in fragment → clear HIRValidationError
    def test_hwhile_rejected_with_clear_error(self):
        """HWhile is not supported inside HFragment — clear error before lowerer."""
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HWhile(cond=cond, body=(), loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HWhile is not allowed inside HFragment"):
            validate_hfragment(frag)

    # B2: HPoll in fragment → clear HIRValidationError
    def test_hpoll_rejected_with_clear_error(self):
        """HPoll is not supported inside HFragment — clear error before lowerer."""
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HPoll(cond=cond, body=(), loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HPoll is not allowed inside HFragment"):
            validate_hfragment(frag)

    # B3: HFor in fragment → clear HIRValidationError
    def test_hfor_rejected_with_clear_error(self):
        """HFor is not supported inside HFragment — clear error before lowerer."""
        i = HVar("i", UInt(8))
        body = (
            HFor(var=i, init=hconst(0), bound=hconst(3), body=(), loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HFor is not allowed inside HFragment"):
            validate_hfragment(frag)

    # B4: HBreak in fragment → clear HIRValidationError
    def test_hbreak_rejected_with_clear_error(self):
        """HBreak is not supported inside HFragment — clear error before lowerer."""
        body = (
            HBreak(loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HBreak is not allowed inside HFragment"):
            validate_hfragment(frag)

    # B5: HContinue in fragment → clear HIRValidationError
    def test_hcontinue_rejected_with_clear_error(self):
        """HContinue is not supported inside HFragment — clear error before lowerer."""
        body = (
            HContinue(loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HContinue is not allowed inside HFragment"):
            validate_hfragment(frag)

    # B6: HWhile nested inside HIf → still rejected
    def test_hwhile_nested_in_if_rejected(self):
        """HWhile inside an HIf branch is still rejected."""
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HWhile(cond=HCmp("eq", hconst(0), hconst(0)), body=(), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(HExit(loc=_LOC),),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HWhile is not allowed inside HFragment"):
            validate_hfragment(frag)

    # B7: HPoll nested inside HIf → still rejected
    def test_hpoll_nested_in_if_rejected(self):
        """HPoll inside an HIf branch is still rejected."""
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HPoll(cond=HCmp("eq", hconst(0), hconst(0)), body=(), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(HExit(loc=_LOC),),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HPoll is not allowed inside HFragment"):
            validate_hfragment(frag)

    # B8: HFor nested inside HIf → still rejected
    def test_hfor_nested_in_if_rejected(self):
        """HFor inside an HIf branch is still rejected."""
        i = HVar("i", UInt(8))
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HFor(var=i, init=hconst(0), bound=hconst(3), body=(), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(HExit(loc=_LOC),),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HFor is not allowed inside HFragment"):
            validate_hfragment(frag)

    # B9: error from validator (HIRValidationError) not from lowerer (NotImplementedError)
    def test_hwhile_error_from_validator_not_lowerer(self):
        """validate_hfragment raises HIRValidationError, not NotImplementedError."""
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HWhile(cond=cond, body=(), loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        # Must be HIRValidationError, not NotImplementedError
        with pytest.raises(HIRValidationError):
            validate_hfragment(frag)
        # Should NOT raise NotImplementedError from validate_hfragment
        try:
            validate_hfragment(frag)
        except HIRValidationError:
            pass
        except NotImplementedError:
            pytest.fail(
                "validate_hfragment raised NotImplementedError instead of HIRValidationError"
            )

    # B10: direct lower_fragment() defensively rejects (NotImplementedError)
    def test_hwhile_lower_fragment_raises_not_implemented(self):
        """lower_fragment() defensively raises NotImplementedError for HWhile."""
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HWhile(cond=cond, body=(), loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        # Calling lower_fragment() directly (bypassing validator) raises NotImplementedError
        with pytest.raises(NotImplementedError):
            lower_fragment(frag)


# ============================================================================
# Section C: Lowering HIf through the full pipeline
# ============================================================================

class TestHIfLowering:
    """HIf lowers correctly through lower_fragment() and run_codegen_from_fragment()."""

    def _make_if_fragment(self, with_else=True):
        """Create a simple HIf fragment with in/out bindings."""
        inp = _mkbinding("x", mode="in", reg="g0")
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", HVar("x", UInt(8)), hconst(0))
        if with_else:
            body = (
                HIf(
                    cond=cond,
                    then_body=(
                        HAssign(target=HVar("y", UInt(8)), value=hconst(1), loc=_LOC),
                        HExit(loc=_LOC),
                    ),
                    else_body=(
                        HAssign(target=HVar("y", UInt(8)), value=hconst(2), loc=_LOC),
                        HExit(loc=_LOC),
                    ),
                    loc=_LOC,
                ),
            )
        else:
            # Without else: out assigned before the if
            body = (
                HAssign(target=HVar("y", UInt(8)), value=hconst(0), loc=_LOC),
                HIf(
                    cond=cond,
                    then_body=(
                        HAssign(target=HVar("y", UInt(8)), value=hconst(1), loc=_LOC),
                    ),
                    loc=_LOC,
                ),
                HExit(loc=_LOC),
            )
        return HFragment(
            name="if_frag",
            bindings=(inp, out),
            scratch_regs=(),
            body=body,
            loc=_LOC,
        )

    # C1: HIf lowers to lir.Fragment with multiple blocks
    def test_if_else_lowers_to_multi_block_fragment(self):
        """HIf lowers to a multi-block Fragment (then + else + merge or terminator)."""
        frag = self._make_if_fragment(with_else=True)
        result = lower_fragment(frag)
        assert isinstance(result, lir.Fragment)
        # HIf with two exit branches: we expect > 1 block (the merge block may be
        # pruned as unreachable since both branches exit)
        assert len(result.blocks) >= 1
        # All reachable blocks must have a terminator
        for block in result.blocks:
            assert block.terminator is not None

    # C2: all blocks have terminator
    def test_all_blocks_terminated(self):
        """Every block in the lowered fragment has a terminator."""
        frag = self._make_if_fragment(with_else=True)
        result = lower_fragment(frag)
        for block in result.blocks:
            assert block.terminator is not None, (
                f"block {block.label!r} has no terminator"
            )

    # C3: run_codegen_from_fragment with HIf and register_model
    def test_hif_fragment_pipeline_with_register_model(self):
        """HIf fragment runs through full pipeline with a register model."""
        from rpkbin.codegen.toy_target import ToyTarget
        frag = self._make_if_fragment(with_else=True)
        result = run_codegen_from_fragment(
            frag,
            target=ToyTarget(),
            register_model=_SimpleRegModel(),
        )
        from rpkbin.codegen.pipeline import FragmentCodegenResult
        assert isinstance(result, FragmentCodegenResult)
        assert result.asm is not None

    # C4: HIf without else (pre-assigned) also runs through pipeline
    def test_hif_no_else_fragment_pipeline(self):
        """HIf without else (out pre-assigned) works through full pipeline."""
        from rpkbin.codegen.toy_target import ToyTarget
        frag = self._make_if_fragment(with_else=False)
        result = run_codegen_from_fragment(
            frag,
            target=ToyTarget(),
            register_model=_SimpleRegModel(),
        )
        from rpkbin.codegen.pipeline import FragmentCodegenResult
        assert isinstance(result, FragmentCodegenResult)

    # C5: unsupported HWhile fails before selector with HIRValidationError
    def test_hwhile_fails_before_selector_clear_error(self):
        """HWhile in fragment causes HIRValidationError from run_codegen_from_fragment."""
        from rpkbin.codegen.toy_target import ToyTarget
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HWhile(cond=cond, body=(), loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        # Must fail with HIRValidationError (not NotImplementedError or a silent bad result)
        with pytest.raises(HIRValidationError, match="HWhile is not allowed inside HFragment"):
            run_codegen_from_fragment(frag, target=ToyTarget(), register_model=_SimpleRegModel())

    # C6: unsupported HFor fails before selector
    def test_hfor_fails_before_selector_clear_error(self):
        """HFor in fragment causes HIRValidationError from run_codegen_from_fragment."""
        from rpkbin.codegen.toy_target import ToyTarget
        i = HVar("i", UInt(8))
        body = (
            HFor(var=i, init=hconst(0), bound=hconst(3), body=(), loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HFor is not allowed inside HFragment"):
            run_codegen_from_fragment(frag, target=ToyTarget(), register_model=_SimpleRegModel())

    # C7: unsupported HPoll fails before selector
    def test_hpoll_fails_before_selector_clear_error(self):
        """HPoll in fragment causes HIRValidationError from run_codegen_from_fragment."""
        from rpkbin.codegen.toy_target import ToyTarget
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HPoll(cond=cond, body=(), loc=_LOC),
            HExit(loc=_LOC),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HPoll is not allowed inside HFragment"):
            run_codegen_from_fragment(frag, target=ToyTarget(), register_model=_SimpleRegModel())

    # C8: HIf with elif lowers successfully
    def test_hif_elif_else_lowers_successfully(self):
        """HIf with elif chain lowers to a valid Fragment."""
        out = _mkbinding("y", mode="out", reg="g1")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(10), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                elif_branches=(
                    (HCmp("ne", hconst(1), hconst(2)), (
                        HAssign(target=HVar("y", UInt(8)), value=hconst(20), loc=_LOC),
                        HExit(loc=_LOC),
                    )),
                ),
                else_body=(
                    HAssign(target=HVar("y", UInt(8)), value=hconst(30), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("elif_frag", bindings=(out,), body=body)
        result = lower_fragment(frag)
        assert isinstance(result, lir.Fragment)
        for block in result.blocks:
            assert block.terminator is not None


# ============================================================================
# Section D: inout binding DA
# ============================================================================

class TestInoutDA:
    """inout bindings are pre-assigned (readable immediately)."""

    def test_inout_readable_before_any_assignment(self):
        """inout binding is pre-assigned → readable in then-branch without prior write."""
        io = _mkbinding("x", mode="inout", reg="g0")
        cond = HCmp("eq", HVar("x", UInt(8)), hconst(0))
        body = (
            HIf(
                cond=cond,
                then_body=(
                    HAssign(
                        target=HVar("tmp", UInt(8)),
                        value=HVar("x", UInt(8)),  # read inout before write — ok
                        loc=_LOC,
                    ),
                    HAssign(target=HVar("x", UInt(8)), value=hconst(1), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                else_body=(
                    HAssign(target=HVar("x", UInt(8)), value=hconst(2), loc=_LOC),
                    HExit(loc=_LOC),
                ),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", bindings=(io,), body=body)
        validate_hfragment(frag)  # must not raise: inout is pre-assigned

    def test_inout_does_not_require_explicit_write_before_exit(self):
        """inout binding does not require an explicit write before HExit.

        The generic fragment contract: out_names tracks only mode=='out' bindings.
        inout is not in out_names, so no explicit write is required before HExit.
        """
        io = _mkbinding("x", mode="inout", reg="g0")
        cond = HCmp("eq", hconst(1), hconst(1))
        body = (
            HIf(
                cond=cond,
                then_body=(HExit(loc=_LOC),),
                else_body=(HExit(loc=_LOC),),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", bindings=(io,), body=body)
        validate_hfragment(frag)  # must not raise


# ============================================================================
# Section E: HReturn inside fragment rejected (not a loop construct, but baseline)
# ============================================================================

class TestHReturnRejected:
    """HReturn is not a loop construct but is also banned in fragment."""

    def test_hreturn_in_fragment_rejected(self):
        body = (HReturn(values=(hconst(0),), loc=_LOC),)
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HReturn is not allowed inside HFragment"):
            validate_hfragment(frag)

    def test_hreturn_nested_in_if_rejected(self):
        """HReturn nested inside HIf also rejected."""
        body = (
            HIf(
                cond=HCmp("eq", hconst(1), hconst(1)),
                then_body=(HReturn(values=(hconst(0),), loc=_LOC),),
                else_body=(HExit(loc=_LOC),),
                loc=_LOC,
            ),
        )
        frag = _simple_fragment("t", body=body)
        with pytest.raises(HIRValidationError, match="HReturn is not allowed inside HFragment"):
            validate_hfragment(frag)
