"""Generic physical-register contract tests.

Fake target
-----------
  allocatable:    g0, g1          (allocator pool)
  physical-only:  special, status (legal physical; not in allocatable pool)
  alias group:    wide = { lo, hi }  (lo/hi do NOT overlap each other)

Test matrix
-----------
1.  HParam.reg_hint / HVar.reg_hint
    a. "special" (non-allocatable physical) → accepted.
    b. "bogus"   (unknown)                  → rejected.
2.  HCall.arg_regs
    a. ("special",)  → accepted.
    b. ("bogus",)    → rejected.
    c. (None,)       → accepted (None means 'unspecified').
3.  HCall.return_regs
    a. ("special",)  → accepted.
    b. ("bogus",)    → rejected.
    c. (None,)       → accepted.
4.  HExternFn.return_regs
    a. ("special",)  → accepted.
    b. ("bogus",)    → rejected.
5.  HExternFn.clobbers
    a. "special" / "status" → accepted.
    b. "bogus"              → rejected.
6.  HFunction.return_regs
    a. ("special",)  → accepted.
    b. ("bogus",)    → rejected.
7.  Fragment binding / scratch
    a. binding reg "special"    → accepted.
    b. binding reg "bogus"      → rejected.
    c. scratch reg "bogus"      → rejected.
8.  Allocator pool remains g0/g1 only (special/status not auto-assigned).
9.  Backward compatibility: alias-derived regs count as physical.
10. is_physical_register free-function API smoke test.

Design boundary
---------------
* No UC / 8051 / MCU-specific names anywhere in this file.
* No allocator save/restore, fixed live-across-call, or fragment
  control-flow behavior is specified here.
"""
from __future__ import annotations

import pytest

from rpkbin.codegen.hir import (
    HCall,
    HCallAssign,
    HExternFn,
    HFragment,
    HFragmentBinding,
    HFunction,
    HModule,
    HParam,
    HVar,
    UInt,
    Void,
    HAssign,
    HConst,
    HExit,
    HReturn,
)
from rpkbin.codegen.hir_validate import (
    HIRValidationError,
    validate_extern_fn,
    validate_hfragment,
    validate_hfunction,
    validate_hmodule,
)
from rpkbin.codegen.register_alloc import (
    RegisterAllocationError,
    allocate_registers,
)
from rpkbin.codegen.target import is_physical_register
from rpkbin.codegen import lir


# ---------------------------------------------------------------------------
# Fake generic target register model
# ---------------------------------------------------------------------------

class _GenericFakeModel:
    """Minimal generic RegisterModel for physical-register tests.

    Registers
    ---------
    allocatable : g0, g1                    (auto-assignment pool)
    physical-only: special, status          (legal physical; not allocatable)
    alias group : wide overlaps lo and hi
                  (lo and hi do NOT overlap each other)
    Width table : all 8-bit except wide=16.
    """

    _WIDTHS = {
        "g0": 8, "g1": 8,
        "special": 8, "status": 8,
        "wide": 16, "lo": 8, "hi": 8,
    }

    def allocatable_registers(self):
        return ["g0", "g1"]

    def is_physical_register(self, reg: str) -> bool:
        return reg in self._WIDTHS

    def fixed_register_hints(self) -> bool:
        return True

    def register_width(self, reg: str) -> int:
        return self._WIDTHS[reg]

    def can_allocate(self, reg: str, width: int) -> bool:
        return self._WIDTHS.get(reg, 0) >= width

    def register_aliases(self):
        # wide is the composite; lo and hi are its sub-registers.
        # lo and hi are NOT listed as each other's aliases.
        return [("wide", ["lo", "hi"])]

    def registers_overlap(self, lhs: str, rhs: str) -> bool:
        if lhs == rhs:
            return True
        for composite, members in self.register_aliases():
            member_set = set(members)
            if lhs == composite and rhs in member_set:
                return True
            if rhs == composite and lhs in member_set:
                return True
        return False

    def spill_slots(self):
        return []

    def emit_spill(self, reg, slot):
        return [f"STORE {reg}, [{slot.address}]"]

    def emit_reload(self, slot, reg):
        return [f"LOAD {reg}, [{slot.address}]"]


MODEL = _GenericFakeModel()


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _make_fragment(
    name: str,
    bindings: list,
    scratch_regs: list,
    body: tuple = (),
) -> HFragment:
    """Build a minimal HFragment that ends with HExit."""
    if not body:
        stmts = []
        for b in bindings:
            if b.mode in ("out", "inout"):
                stmts.append(HAssign(HVar(b.name, b.ty), HConst(0, b.ty)))
        stmts.append(HExit())
        body = tuple(stmts)
    return HFragment(
        name=name,
        bindings=tuple(bindings),
        scratch_regs=tuple(scratch_regs),
        body=body,
    )


def _make_function_with_param_hint(hint: str | None, width: int = 8) -> HFunction:
    """Minimal HFunction with a single param carrying *hint*."""
    p = HParam("x", UInt(width), reg_hint=hint)
    body = (HReturn((HVar("x", UInt(width)),)),)
    return HFunction(name="f", params=(p,), return_ty=UInt(width), body=body)


def _make_function_with_var_hint(hint: str | None, width: int = 8) -> HFunction:
    """Minimal HFunction with a local HVar carrying *hint*."""
    y = HVar("y", UInt(width), reg_hint=hint)
    body = (
        HAssign(y, HConst(0, UInt(width))),
        HReturn((y,)),
    )
    return HFunction(name="f", params=(), return_ty=UInt(width), body=body)


def _make_function_with_call(
    call_name: str = "callee",
    arg_regs: tuple = (),
    return_regs: tuple = (),
) -> HFunction:
    """HFunction that contains one HCall with the given ABI annotations."""
    call = HCall(
        name=call_name,
        args=(HConst(0, UInt(8)),),
        return_ty=UInt(8),
        arg_regs=arg_regs,
        return_regs=return_regs,
    )
    body = (HReturn((call,)),)
    # Callee is declared as an extern so validate_hmodule won't complain about
    # an unknown callee name.  We use validate_hfunction directly for most
    # call-level tests so the callee lookup is not performed.
    return HFunction(name="caller", params=(), return_ty=UInt(8), body=body)


def _make_function_with_call_assign(
    target_hint: str | None = None,
    arg_hint: str | None = None,
) -> HFunction:
    """HFunction that contains one HCallAssign with hinted HVar target/arg."""
    arg = HVar("arg", UInt(8), reg_hint=arg_hint)
    target = HVar("ret", UInt(8), reg_hint=target_hint)
    call = HCall(
        name="callee",
        args=(arg,),
        return_ty=Void(),
    )
    body = (
        HCallAssign(targets=(target,), call=call),
        HReturn(()),
    )
    return HFunction(name="caller", params=(), return_ty=Void(), body=body)


# ===========================================================================
# 1.  HParam.reg_hint validation
# ===========================================================================

class TestParamHintPhysical:
    def test_param_hint_special_accepted(self):
        """HParam.reg_hint='special' is legal physical → must not raise."""
        fn = _make_function_with_param_hint("special")
        validate_hfunction(fn, MODEL)

    def test_param_hint_g0_accepted(self):
        """HParam.reg_hint='g0' is allocatable and physical → accepted."""
        fn = _make_function_with_param_hint("g0")
        validate_hfunction(fn, MODEL)

    def test_param_hint_status_accepted(self):
        """HParam.reg_hint='status' is non-allocatable physical → accepted."""
        fn = _make_function_with_param_hint("status")
        validate_hfunction(fn, MODEL)

    def test_param_hint_none_accepted(self):
        """HParam.reg_hint=None (no hint) is always valid."""
        fn = _make_function_with_param_hint(None)
        validate_hfunction(fn, MODEL)

    def test_param_hint_bogus_rejected(self):
        """HParam.reg_hint='bogus' is not a physical register → must raise."""
        fn = _make_function_with_param_hint("bogus")
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfunction(fn, MODEL)

    def test_param_hint_no_model_skips_check(self):
        """Without register_model the physical check is skipped entirely."""
        fn = _make_function_with_param_hint("totally_unknown_reg")
        validate_hfunction(fn, register_model=None)  # must not raise


# ===========================================================================
# 2.  HVar.reg_hint validation
# ===========================================================================

class TestVarHintPhysical:
    def test_var_hint_special_accepted(self):
        """HVar.reg_hint='special' is legal physical → accepted."""
        fn = _make_function_with_var_hint("special")
        validate_hfunction(fn, MODEL)

    def test_var_hint_g1_accepted(self):
        """HVar.reg_hint='g1' is allocatable physical → accepted."""
        fn = _make_function_with_var_hint("g1")
        validate_hfunction(fn, MODEL)

    def test_var_hint_none_accepted(self):
        """HVar.reg_hint=None is always valid."""
        fn = _make_function_with_var_hint(None)
        validate_hfunction(fn, MODEL)

    def test_var_hint_bogus_rejected(self):
        """HVar.reg_hint='bogus' is not physical → must raise."""
        fn = _make_function_with_var_hint("bogus")
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfunction(fn, MODEL)

    def test_var_hint_no_model_skips_check(self):
        """Without register_model the physical check is skipped."""
        fn = _make_function_with_var_hint("totally_unknown_reg")
        validate_hfunction(fn, register_model=None)


# ===========================================================================
# 3.  HCallAssign HVar.reg_hint validation
# ===========================================================================

class TestCallAssignVarHintPhysical:
    def test_target_hint_special_accepted(self):
        fn = _make_function_with_call_assign(target_hint="special")
        validate_hfunction(fn, MODEL)

    def test_target_hint_bogus_rejected(self):
        fn = _make_function_with_call_assign(target_hint="bogus")
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfunction(fn, MODEL)

    def test_arg_hint_special_accepted(self):
        fn = _make_function_with_call_assign(arg_hint="special")
        validate_hfunction(fn, MODEL)

    def test_arg_hint_bogus_rejected(self):
        fn = _make_function_with_call_assign(arg_hint="bogus")
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfunction(fn, MODEL)


# ===========================================================================
# 4.  HCall.arg_regs validation
# ===========================================================================

class TestCallArgRegs:
    def _fn_with_call(self, arg_regs, return_regs=()):
        return _make_function_with_call(arg_regs=arg_regs, return_regs=return_regs)

    def test_arg_regs_special_accepted(self):
        """arg_regs=('special',) → legal physical, accepted."""
        fn = self._fn_with_call(arg_regs=("special",))
        validate_hfunction(fn, MODEL)

    def test_arg_regs_g0_accepted(self):
        """arg_regs=('g0',) → allocatable physical, accepted."""
        fn = self._fn_with_call(arg_regs=("g0",))
        validate_hfunction(fn, MODEL)

    def test_arg_regs_none_accepted(self):
        """arg_regs=(None,) → unspecified, always accepted."""
        fn = self._fn_with_call(arg_regs=(None,))
        validate_hfunction(fn, MODEL)

    def test_arg_regs_bogus_rejected(self):
        """arg_regs=('bogus',) → not physical, must raise."""
        fn = self._fn_with_call(arg_regs=("bogus",))
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfunction(fn, MODEL)

    def test_arg_regs_no_model_skips_check(self):
        """Without register_model the check is skipped."""
        fn = self._fn_with_call(arg_regs=("totally_unknown",))
        validate_hfunction(fn, register_model=None)


# ===========================================================================
# 5.  HCall.return_regs validation
# ===========================================================================

class TestCallReturnRegs:
    def _fn_with_call(self, return_regs):
        return _make_function_with_call(return_regs=return_regs)

    def test_return_regs_special_accepted(self):
        """return_regs=('special',) → legal physical, accepted."""
        fn = self._fn_with_call(return_regs=("special",))
        validate_hfunction(fn, MODEL)

    def test_return_regs_g0_accepted(self):
        """return_regs=('g0',) → allocatable physical, accepted."""
        fn = self._fn_with_call(return_regs=("g0",))
        validate_hfunction(fn, MODEL)

    def test_return_regs_none_accepted(self):
        """return_regs=(None,) → unspecified, always accepted."""
        fn = self._fn_with_call(return_regs=(None,))
        validate_hfunction(fn, MODEL)

    def test_return_regs_bogus_rejected(self):
        """return_regs=('bogus',) → not physical, must raise."""
        fn = self._fn_with_call(return_regs=("bogus",))
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfunction(fn, MODEL)

    def test_return_regs_no_model_skips_check(self):
        """Without register_model the check is skipped."""
        fn = self._fn_with_call(return_regs=("totally_unknown",))
        validate_hfunction(fn, register_model=None)


# ===========================================================================
# 6.  HExternFn.return_regs validation
# ===========================================================================

class TestExternFnReturnRegs:
    def _efn(self, return_regs=(), clobbers=()):
        return HExternFn(
            name="ext",
            params=(),
            return_ty=UInt(8),
            return_regs=return_regs,
            clobbers=clobbers,
        )

    def test_return_regs_special_accepted(self):
        """HExternFn.return_regs=('special',) → legal physical, accepted."""
        validate_extern_fn(self._efn(return_regs=("special",)), MODEL)

    def test_return_regs_g0_accepted(self):
        """HExternFn.return_regs=('g0',) → allocatable physical, accepted."""
        validate_extern_fn(self._efn(return_regs=("g0",)), MODEL)

    def test_return_regs_bogus_rejected(self):
        """HExternFn.return_regs=('bogus',) → not physical, must raise."""
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_extern_fn(self._efn(return_regs=("bogus",)), MODEL)

    def test_return_regs_no_model_skips_check(self):
        """Without register_model the check is skipped."""
        validate_extern_fn(self._efn(return_regs=("totally_unknown",)), register_model=None)

    def test_clobber_and_return_regs_both_validated(self):
        """Both clobbers and return_regs are validated when model is present."""
        # Both physical → no error
        validate_extern_fn(self._efn(return_regs=("g0",), clobbers=("special",)), MODEL)

    def test_clobber_bogus_still_rejected(self):
        """Clobber validation still works independently of return_regs."""
        with pytest.raises(HIRValidationError, match="not a valid register"):
            validate_extern_fn(self._efn(clobbers=("bogus_clobber",)), MODEL)


# ===========================================================================
# 7.  HExternFn.clobbers validation  (pre-existing, kept for regression)
# ===========================================================================

class TestExternFnClobbers:
    def _efn(self, clobbers, name="ext"):
        return HExternFn(name=name, params=(), return_ty=Void(), clobbers=clobbers)

    def test_clobber_special_accepted(self):
        """special is a legal physical register → accepted as clobber."""
        validate_extern_fn(self._efn(("special",)), MODEL)

    def test_clobber_status_accepted(self):
        """status is a legal physical register → accepted as clobber."""
        validate_extern_fn(self._efn(("status",)), MODEL)

    def test_clobber_allocatable_accepted(self):
        """g0 (allocatable) is also physical → accepted as clobber."""
        validate_extern_fn(self._efn(("g0", "g1")), MODEL)

    def test_clobber_unknown_rejected(self):
        """Completely unknown register must be rejected."""
        with pytest.raises(HIRValidationError, match="not a valid register"):
            validate_extern_fn(self._efn(("bogus_reg",)), MODEL)

    def test_clobber_duplicate_rejected(self):
        """Duplicate clobber entry must still be rejected."""
        with pytest.raises(HIRValidationError, match="duplicate clobber"):
            validate_extern_fn(self._efn(("g0", "g0")), MODEL)

    def test_no_register_model_skips_check(self):
        """Without a register model, the physical check is skipped."""
        validate_extern_fn(self._efn(("anything_goes",)), register_model=None)

    def test_clobber_alias_derived_accepted(self):
        """wide, lo, hi are alias-derived registers → physical by default."""
        validate_extern_fn(self._efn(("wide",)), MODEL)
        validate_extern_fn(self._efn(("lo",)), MODEL)
        validate_extern_fn(self._efn(("hi",)), MODEL)


# ===========================================================================
# 8.  HFunction.return_regs validation
# ===========================================================================

class TestFunctionReturnRegs:
    def _fn(self, return_regs):
        p = HParam("x", UInt(8))
        body = (HReturn((HVar("x", UInt(8)),)),)
        return HFunction(
            name="f",
            params=(p,),
            return_ty=UInt(8),
            return_regs=return_regs,
            body=body,
        )

    def test_return_regs_special_accepted(self):
        """HFunction.return_regs=('special',) → legal physical, accepted."""
        validate_hfunction(self._fn(("special",)), MODEL)

    def test_return_regs_g0_accepted(self):
        """HFunction.return_regs=('g0',) → allocatable physical, accepted."""
        validate_hfunction(self._fn(("g0",)), MODEL)

    def test_return_regs_empty_accepted(self):
        """HFunction.return_regs=() → no ABI hints, always accepted."""
        validate_hfunction(self._fn(()), MODEL)

    def test_return_regs_bogus_rejected(self):
        """HFunction.return_regs=('bogus',) → not physical, must raise."""
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfunction(self._fn(("bogus",)), MODEL)

    def test_return_regs_no_model_skips_check(self):
        """Without register_model the check is skipped."""
        validate_hfunction(self._fn(("totally_unknown",)), register_model=None)


# ===========================================================================
# 9.  Allocator does NOT auto-assign `special` / `status` to unbound locals
# ===========================================================================

class TestAllocatorPool:
    """The allocator's candidate pool must remain g0/g1 only."""

    def _two_var_func(self):
        """LIR function with two unbound local variables."""
        x = lir.Var("x", 8)
        y = lir.Var("y", 8)
        block = lir.Block(
            label="entry",
            statements=[
                lir.Assign(x, lir.Const(1, 8)),
                lir.Assign(y, lir.Const(2, 8)),
            ],
            terminator=lir.Return(x),
        )
        return lir.Function(name="f", params=(), blocks=(block,))

    def test_allocator_uses_only_g0_g1(self):
        """Unbound locals must only be assigned to g0 or g1, not special/status."""
        func = self._two_var_func()
        _, assignment = allocate_registers(func, MODEL)
        for phys in assignment.values():
            assert phys in {"g0", "g1"}, (
                f"Allocator assigned '{phys}' which is outside the allocatable pool"
            )

    def test_allocator_spills_not_to_special(self):
        """When the allocatable pool (g0, g1) is exhausted, the allocator
        must raise RegisterAllocationError rather than silently overflow
        into non-allocatable registers such as 'special' or 'status'.
        """
        a = lir.Var("a", 8)
        b = lir.Var("b", 8)
        x = lir.Var("x", 8)
        # All three vars are live at block exit, so they mutually interfere.
        block = lir.Block(
            label="entry",
            statements=[
                lir.Assign(a, lir.Const(1, 8)),
                lir.Assign(b, lir.Const(2, 8)),
                lir.Assign(x, lir.Const(3, 8)),
            ],
            terminator=lir.MultiReturn([a, b, x]),
        )
        func = lir.Function(name="f", params=(), blocks=(block,))
        # With only 2 allocatable registers and 3 live return values,
        # the allocator must raise — not silently assign to 'special'.
        with pytest.raises(RegisterAllocationError):
            allocate_registers(func, MODEL)


# ===========================================================================
# 10.  Fragment binding / scratch physical validation
# ===========================================================================

class TestFragmentBindingPhysical:
    def test_binding_special_accepted(self):
        """Binding reg=special → legal physical, must pass validation."""
        b = HFragmentBinding(name="v", ty=UInt(8), reg="special", mode="inout")
        frag = _make_fragment("frag_special", [b], scratch_regs=["g0"])
        validate_hfragment(frag, MODEL)

    def test_binding_g0_accepted(self):
        """Binding reg=g0 (allocatable) → still legal physical."""
        b = HFragmentBinding(name="v", ty=UInt(8), reg="g0", mode="inout")
        frag = _make_fragment("frag_g0", [b], scratch_regs=["g1"])
        validate_hfragment(frag, MODEL)

    def test_binding_unknown_rejected(self):
        """Unknown binding register must be rejected."""
        b = HFragmentBinding(name="v", ty=UInt(8), reg="bogus_reg", mode="inout")
        frag = _make_fragment("frag_bogus", [b], scratch_regs=["g0"])
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfragment(frag, MODEL)

    def test_scratch_unknown_rejected(self):
        """Unknown scratch register must be rejected."""
        b = HFragmentBinding(name="v", ty=UInt(8), reg="g0", mode="inout")
        frag = _make_fragment("frag_bad_scratch", [b], scratch_regs=["bogus_scratch"])
        with pytest.raises(HIRValidationError, match="not a valid physical register"):
            validate_hfragment(frag, MODEL)

    def test_scratch_allocatable_accepted(self):
        """Allocatable register g1 is physical → OK as scratch."""
        b = HFragmentBinding(name="v", ty=UInt(8), reg="g0", mode="inout")
        frag = _make_fragment("frag_g1_scratch", [b], scratch_regs=["g1"])
        validate_hfragment(frag, MODEL)


# ===========================================================================
# 11.  Fake model overlap contract
# ===========================================================================

class TestFakeModelOverlap:
    def test_overlap_matches_comment(self):
        assert MODEL.registers_overlap("wide", "lo")
        assert MODEL.registers_overlap("wide", "hi")
        assert not MODEL.registers_overlap("lo", "hi")


# ===========================================================================
# 12.  Backward compatibility: alias-derived registers count as physical
#      via the default implementation (no override needed)
# ===========================================================================

class TestAliasBackwardCompat:
    """Registers known only through alias groups are physical by default."""

    def test_is_physical_register_wide(self):
        assert is_physical_register(MODEL, "wide")

    def test_is_physical_register_lo(self):
        assert is_physical_register(MODEL, "lo")

    def test_is_physical_register_hi(self):
        assert is_physical_register(MODEL, "hi")

    def test_is_physical_register_g0(self):
        assert is_physical_register(MODEL, "g0")

    def test_is_physical_register_special(self):
        assert is_physical_register(MODEL, "special")

    def test_is_physical_register_unknown(self):
        assert not is_physical_register(MODEL, "no_such_reg")

    def test_alias_compat_model_without_override(self):
        """A model that does NOT override is_physical_register uses the
        backward-compatible default: alias-derived regs count as physical."""

        class _LegacyModel:
            """Simulates an older model with no is_physical_register override."""
            def allocatable_registers(self):
                return ["g0"]

            def register_aliases(self):
                return [("wide", ["lo", "hi"])]

            def fixed_register_hints(self):
                return False

            def register_width(self, reg):
                return {"g0": 8, "wide": 16, "lo": 8, "hi": 8}[reg]

            def spill_slots(self):
                return []

            def emit_spill(self, reg, slot):
                return []

            def emit_reload(self, slot, reg):
                return []

        legacy = _LegacyModel()
        assert is_physical_register(legacy, "g0")
        assert is_physical_register(legacy, "wide")
        assert is_physical_register(legacy, "lo")
        assert is_physical_register(legacy, "hi")
        assert not is_physical_register(legacy, "mystery_reg")


# ===========================================================================
# 13.  is_physical_register free-function API surface smoke test
# ===========================================================================

class TestIsPhysicalRegisterAPI:
    def test_returns_bool(self):
        result = is_physical_register(MODEL, "g0")
        assert isinstance(result, bool)

    def test_allocatable_is_physical(self):
        for reg in MODEL.allocatable_registers():
            assert is_physical_register(MODEL, reg), f"{reg!r} should be physical"

    def test_non_allocatable_but_physical(self):
        assert is_physical_register(MODEL, "special")
        assert is_physical_register(MODEL, "status")

    def test_unknown_not_physical(self):
        assert not is_physical_register(MODEL, "xxxxxx")
        assert not is_physical_register(MODEL, "")

    def test_method_delegate_called(self):
        """is_physical_register() dispatches to the model's own method."""
        calls = []

        class _SpyModel:
            def is_physical_register(self, reg):
                calls.append(reg)
                return reg == "spy_reg"

            def allocatable_registers(self):
                return []

            def register_aliases(self):
                return []

        spy = _SpyModel()
        result = is_physical_register(spy, "spy_reg")
        assert result is True
        assert calls == ["spy_reg"]

    def test_no_override_falls_back_to_allocatable(self):
        """Without is_physical_register override, falls back to allocatable + aliases."""

        class _NoOverride:
            def allocatable_registers(self):
                return ["r0"]

            def register_aliases(self):
                return []

        m = _NoOverride()
        assert is_physical_register(m, "r0")
        assert not is_physical_register(m, "r1")
