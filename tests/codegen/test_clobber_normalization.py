from __future__ import annotations

import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.hir import HCall, HExprStmt, HExternFn, HFunction, HReturn, Void
from rpkbin.codegen.hir_validate import HIRValidationError, validate_extern_fn, validate_hfunction
from rpkbin.codegen.register_alloc import RegisterAllocationError, allocate_registers


class _AliasModel:
    _WIDTHS = {
        "lo": 8,
        "hi": 8,
        "wide": 16,
        "special": 8,
    }

    def allocatable_registers(self):
        return ["lo", "hi", "wide"]

    def is_physical_register(self, reg: str) -> bool:
        return reg in self._WIDTHS

    def register_width(self, reg: str) -> int:
        return self._WIDTHS[reg]

    def can_allocate(self, reg: str, width: int) -> bool:
        return self._WIDTHS.get(reg, 0) >= width

    def register_aliases(self):
        return [("wide", ["lo", "hi"])]

    def registers_overlap(self, lhs: str, rhs: str) -> bool:
        return lhs == rhs or frozenset((lhs, rhs)) in {
            frozenset(("wide", "lo")),
            frozenset(("wide", "hi")),
        }

    def spill_slots(self):
        return []


class _AliasFixedModel(_AliasModel):
    def fixed_register_hints(self) -> bool:
        return True


class _AliasSpillModel(_AliasModel):
    def spill_slots(self):
        return [lir.SpillSlot(id=0, address=0x20)]


MODEL = _AliasModel()
FIXED_MODEL = _AliasFixedModel()
SPILL_MODEL = _AliasSpillModel()


def _call_stmt_function(call: HCall) -> HFunction:
    return HFunction(
        name="caller",
        params=(),
        return_ty=Void(),
        body=(HExprStmt(expr=call), HReturn(values=())),
    )


def test_hexternfn_clobber_exact_duplicate_rejected():
    with pytest.raises(HIRValidationError, match="duplicate clobber"):
        validate_extern_fn(
            HExternFn(
                name="ext",
                params=(),
                return_ty=Void(),
                clobbers=("lo", "lo"),
            ),
            MODEL,
        )


def test_hexternfn_clobber_alias_duplicate_rejected():
    with pytest.raises(HIRValidationError, match="overlaps"):
        validate_extern_fn(
            HExternFn(
                name="ext",
                params=(),
                return_ty=Void(),
                clobbers=("wide", "lo"),
            ),
            MODEL,
        )


def test_hexternfn_clobber_physical_only_non_allocatable_register_accepted():
    validate_extern_fn(
        HExternFn(
            name="ext",
            params=(),
            return_ty=Void(),
            clobbers=("special",),
        ),
        MODEL,
    )


def test_hexternfn_clobber_unknown_physical_rejected():
    with pytest.raises(HIRValidationError, match="not a valid register"):
        validate_extern_fn(
            HExternFn(
                name="ext",
                params=(),
                return_ty=Void(),
                clobbers=("ghost",),
            ),
            MODEL,
        )


def test_hcall_clobber_exact_duplicate_rejected():
    with pytest.raises(HIRValidationError, match="duplicate clobber"):
        validate_hfunction(
            _call_stmt_function(
                HCall(
                    name="ext",
                    args=(),
                    return_ty=Void(),
                    clobbers=("lo", "lo"),
                )
            ),
            MODEL,
        )


def test_hcall_clobber_alias_duplicate_rejected():
    # Alias duplicates stay rejected until a generic canonical storage API
    # exists for target-independent clobber normalization.
    with pytest.raises(HIRValidationError, match="overlaps"):
        validate_hfunction(
            _call_stmt_function(
                HCall(
                    name="ext",
                    args=(),
                    return_ty=Void(),
                    clobbers=("wide", "lo"),
                )
            ),
            MODEL,
        )


def test_hcall_clobber_unknown_physical_rejected():
    with pytest.raises(HIRValidationError, match="not a valid register"):
        validate_hfunction(
            _call_stmt_function(
                HCall(
                    name="ext",
                    args=(),
                    return_ty=Void(),
                    clobbers=("ghost",),
                )
            ),
            MODEL,
        )


def test_allocator_treats_overlapping_clobber_as_live_value_conflict():
    func = lir.Function(
        name="caller",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.Assign(lir.Var("x", 16), lir.Const(1, 16)),
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), clobbers=("lo",)),
                    ),
                ),
                lir.Return(lir.Var("x", 16)),
            ),
        ),
    )

    with pytest.raises(
        RegisterAllocationError,
        match="register spilling is not implemented safely",
    ):
        allocate_registers(func, SPILL_MODEL)


def test_fixed_value_live_across_alias_overlapping_clobber_errors():
    func = lir.Function(
        name="caller",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.Assign(lir.Var("x", 16), lir.Const(1, 16)),
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), clobbers=("lo",)),
                    ),
                ),
                lir.Return(lir.Var("x", 16)),
            ),
        ),
    )

    with pytest.raises(RegisterAllocationError, match="conflicts with 'lo'"):
        allocate_registers(func, FIXED_MODEL, var_hints={"x": "wide"})


def test_fixed_value_not_live_across_alias_overlapping_clobber_is_accepted():
    func = lir.Function(
        name="caller",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.Assign(lir.Var("x", 16), lir.Const(1, 16)),
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), clobbers=("lo",)),
                    ),
                ),
                lir.Return(lir.Const(0, 8)),
            ),
        ),
    )

    _, assignment = allocate_registers(func, FIXED_MODEL, var_hints={"x": "wide"})

    assert assignment["x"] == "wide"


def test_empty_clobbers_do_not_block_live_value():
    func = lir.Function(
        name="caller",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.Assign(lir.Var("x", 8), lir.Const(1, 8)),
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), clobbers=()),
                    ),
                ),
                lir.Return(lir.Var("x", 8)),
            ),
        ),
    )

    _, assignment = allocate_registers(func, FIXED_MODEL, var_hints={"x": "lo"})

    assert assignment["x"] == "lo"


def test_unknown_clobbers_keep_may_clobber_all_allocatable_behavior():
    func = lir.Function(
        name="caller",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.Assign(lir.Var("x", 8), lir.Const(1, 8)),
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), clobbers=None),
                    ),
                ),
                lir.Return(lir.Var("x", 8)),
            ),
        ),
    )

    with pytest.raises(RegisterAllocationError, match="conflicts with 'lo'"):
        allocate_registers(func, FIXED_MODEL, var_hints={"x": "lo"})
