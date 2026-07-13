from __future__ import annotations

import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.hir import (
    HExternFn,
    HFragment,
    HFragmentBinding,
    HFunction,
    HInlineAsm,
    HExit,
    HReturn,
    UInt,
    Void,
)
from rpkbin.codegen.hir_validate import (
    HIRValidationError,
    validate_extern_fn,
    validate_hfragment,
    validate_hfunction,
)
from rpkbin.codegen.register_alloc import (
    RegisterAllocationError,
    allocate_registers,
)
from rpkbin.codegen.target import can_allocate, registers_overlap


class _OverlapWidthModel:
    _WIDTHS = {
        "wide": 16,
        "lo": 8,
        "hi": 8,
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


MODEL = _OverlapWidthModel()


def _live_return_function(specs: list[tuple[str, int]]) -> lir.Function:
    vars_ = [lir.Var(name, width) for name, width in specs]
    return lir.Function(
        name="f",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                tuple(
                    lir.Assign(var, lir.Const(index + 1, var.width))
                    for index, var in enumerate(vars_)
                ),
                lir.MultiReturn(vars_),
            ),
        ),
    )


def _fragment_binding(name: str, reg: str, width: int, mode: str = "in") -> HFragmentBinding:
    return HFragmentBinding(name=name, ty=UInt(width), reg=reg, mode=mode)


def test_can_allocate_rejects_width_incompatible_register():
    assert can_allocate(MODEL, "wide", 16)
    assert can_allocate(MODEL, "lo", 8)
    assert can_allocate(MODEL, "hi", 8)
    assert not can_allocate(MODEL, "lo", 16)
    assert not can_allocate(MODEL, "hi", 16)


def test_registers_overlap_handles_partial_subregisters():
    assert registers_overlap(MODEL, "wide", "lo")
    assert registers_overlap(MODEL, "wide", "hi")
    assert not registers_overlap(MODEL, "lo", "hi")


def test_allocator_assigns_two_live_byte_values_to_lo_and_hi():
    _, assignment = allocate_registers(
        _live_return_function([("a", 8), ("b", 8)]),
        MODEL,
    )

    assert assignment["a"] == "lo"
    assert assignment["b"] == "hi"


def test_allocator_rejects_simultaneous_wide_and_byte_overlap():
    with pytest.raises(RegisterAllocationError, match="No register available"):
        allocate_registers(
            _live_return_function([("byte", 8), ("word", 16)]),
            MODEL,
        )


def test_fragment_bindings_allow_lo_and_hi_together():
    validate_hfragment(
        HFragment(
            name="ok",
            bindings=(
                _fragment_binding("lo_in", "lo", 8),
                _fragment_binding("hi_in", "hi", 8),
            ),
            scratch_regs=(),
            body=(HExit(),),
        ),
        MODEL,
    )


def test_fragment_binding_conflict_uses_registers_overlap():
    with pytest.raises(HIRValidationError, match="overlaps"):
        validate_hfragment(
            HFragment(
                name="bad",
                bindings=(
                    _fragment_binding("word", "wide", 16),
                    _fragment_binding("byte", "lo", 8),
                ),
                scratch_regs=(),
                body=(HExit(),),
            ),
            MODEL,
        )


def test_fragment_binding_width_uses_can_allocate():
    with pytest.raises(HIRValidationError, match="cannot hold 16-bit binding"):
        validate_hfragment(
            HFragment(
                name="bad_width",
                bindings=(_fragment_binding("word", "lo", 16),),
                scratch_regs=(),
                body=(HExit(),),
            ),
            MODEL,
        )


def test_extern_clobber_alias_duplicate_uses_registers_overlap():
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


def test_inline_asm_is_opaque_with_register_model():
    text = "mov special, wide ; arbitrary register-like text: r99"
    asm = HInlineAsm(text=text)

    validate_hfunction(
        HFunction(
            name="opaque_asm",
            params=(),
            return_ty=Void(),
            body=(asm, HReturn(())),
        ),
        MODEL,
    )

    assert asm.text == text
