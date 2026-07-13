"""Bit-operation validation and stable HIR -> LIR -> ToyTarget coverage.

Covers:

  - HLogical / HNot condition
  - HIf (with elif/else)
  - HWhile (with HBreak)
  - HLoad / HStore
  - HBitSet / HBitTest
  - HReturn

Also includes validation regression tests for HBitSet and HBitTest.
"""

from __future__ import annotations

import pytest

from rpkbin.codegen.hir import (
    HAssign,
    HBinOp,
    HBitSet,
    HBitTest,
    HBreak,
    HCmp,
    HFunction,
    HIf,
    HLoad,
    HLogical,
    HNot,
    HParam,
    HReturn,
    HStore,
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
from rpkbin.codegen.pipeline import run_codegen_from_hir
from rpkbin.codegen.toy_target import ToyTarget

import rpkbin.codegen.lir as lir


# ===================================================================
# Supported-feature end-to-end pipeline test
# ===================================================================

def test_supported_bit_operations_end_to_end():
    """Supported bit and control-flow constructs pass the full pipeline.

    Exercises: HBitSet, HStore, HNot(HBitTest), HIf, HLoad,
    HWhile, HLogical, HBreak, HReturn.
    """
    flags = u8("flags")
    ptr = u8("ptr")
    result = u8("result")

    func = HFunction(
        name="m2_basic_e2e",
        params=(
            HParam("flags", UInt(8)),
            HParam("ptr", UInt(8)),
        ),
        return_ty=UInt(8),
        body=(
            # HBitSet — set bit 3 (flags = 0b00001000 = 8)
            HBitSet(var=flags, bit_idx=3, value=1),
            # HStore — write flags to memory
            HStore(ptr_expr=ptr, value_expr=flags),
            # HIf with HNot(HBitTest): NOT(bit 3 of flags)
            # Since bit 3 IS set, NOT makes it False -> else_body
            HIf(
                cond=HNot(HBitTest(var=flags, bit_idx=3)),
                then_body=(
                    HAssign(target=result, value=hconst(42)),
                ),
                else_body=(
                    HAssign(target=result, value=HLoad(ptr_expr=ptr, ty=UInt(8))),
                ),
            ),
            # HWhile with HLogical("and") short-circuit
            HWhile(
                cond=HLogical("and",
                    HCmp("ne", result, hconst(0)),
                    HCmp("lt", result, hconst(10)),
                ),
                body=(HBreak(),),
            ),
            HReturn(values=(result,)),
        ),
    )

    result = run_codegen_from_hir(func, ToyTarget())
    asm = result.asm_text

    # Bit operation appears in pseudo ASM (HBitSet)
    assert "BSET" in asm, f"BSET missing from ASM:\n{asm}"

    # Memory operations survive rewrite (HStore / HLoad)
    assert "[ptr]" in asm, f"[ptr] missing from ASM:\n{asm}"
    assert "MOV" in asm, f"MOV missing from ASM:\n{asm}"

    # Branch labels from HIf and HWhile
    assert "if_then" in asm, f"if_then missing from ASM:\n{asm}"
    assert "if_else" in asm, f"if_else missing from ASM:\n{asm}"
    assert "while_test" in asm, f"while_test missing from ASM:\n{asm}"
    assert "while_body" in asm, f"while_body missing from ASM:\n{asm}"
    assert "while_exit" in asm, f"while_exit missing from ASM:\n{asm}"

    # HLogical short-circuit creates logical_mid block
    assert "logical_mid" in asm, f"logical_mid missing from ASM:\n{asm}"

    # Terminator present
    assert "RET" in asm, f"RET missing from ASM:\n{asm}"

    # HNot label-swap: since we see BTEST, HNot swapped correctly
    assert "BTEST" in asm, f"BTEST missing from ASM:\n{asm}"


# ===================================================================
# HBitSet validation regression tests
# ===================================================================

def test_bitset_valid_passes():
    """Valid HBitSet (value=0, value=1, various indices) passes validation."""
    flags = u8("flags")
    for val in (0, 1):
        for idx in (0, 3, 7):
            func = simple_function(
                "ok",
                [HParam("flags", UInt(8))],
                [HBitSet(var=flags, bit_idx=idx, value=val)],
                Void(),
            )
            validate_hfunction(func)  # must not raise


def test_bitset_var_not_hvar():
    """HBitSet var that is not an HVar must be rejected."""
    stmt = HBitSet(var=hconst(5), bit_idx=0, value=1)  # type: ignore
    func = simple_function("bad", [], [stmt], Void())
    with pytest.raises(HIRValidationError, match="must be an HVar"):
        validate_hfunction(func)


def test_bitset_bitidx_not_int():
    """HBitSet bit_idx that is not an int must be rejected."""
    flags = u8("flags")
    stmt = HBitSet(var=flags, bit_idx="3", value=1)  # type: ignore
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="strict int"):
        validate_hfunction(func)


def test_bitset_bitidx_is_bool():
    """HBitSet bit_idx = True must be rejected (bool passes isinstance(x,int))."""
    flags = u8("flags")
    stmt = HBitSet(var=flags, bit_idx=True, value=1)  # type: ignore
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="strict int"):
        validate_hfunction(func)


def test_bitset_bitidx_negative():
    """HBitSet with negative bit_idx must be rejected."""
    flags = u8("flags")
    stmt = HBitSet(var=flags, bit_idx=-1, value=1)
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="out of range"):
        validate_hfunction(func)


def test_bitset_bitidx_out_of_range():
    """HBitSet bit_idx >= var width must be rejected."""
    flags = u8("flags")  # 8-bit
    stmt = HBitSet(var=flags, bit_idx=8, value=1)
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="out of range"):
        validate_hfunction(func)


def test_bitset_value_not_int():
    """HBitSet value that is not an int must be rejected."""
    flags = u8("flags")
    stmt = HBitSet(var=flags, bit_idx=3, value="1")  # type: ignore
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="strict int"):
        validate_hfunction(func)


def test_bitset_value_is_bool():
    """HBitSet value = True must be rejected (bool is not a strict int)."""
    flags = u8("flags")
    stmt = HBitSet(var=flags, bit_idx=3, value=True)  # type: ignore
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="strict int"):
        validate_hfunction(func)


def test_bitset_value_out_of_range():
    """HBitSet value not 0 or 1 must be rejected."""
    flags = u8("flags")
    for bad_val in (2, -1, 255):
        stmt = HBitSet(var=flags, bit_idx=3, value=bad_val)
        func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], Void())
        with pytest.raises(HIRValidationError, match="must be 0 or 1"):
            validate_hfunction(func)


# ===================================================================
# HBitTest validation regression tests
# ===================================================================

def test_bittest_var_not_hvar():
    """HBitTest var that is not an HVar must be rejected."""
    cond = HBitTest(var=hconst(5), bit_idx=3)  # type: ignore
    stmt = HIf(cond=cond, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="must be an HVar"):
        validate_hfunction(func)


def test_bittest_bitidx_is_bool():
    """HBitTest bit_idx = True must be rejected (strict int)."""
    flags = u8("flags")
    cond = HBitTest(var=flags, bit_idx=True)  # type: ignore
    stmt = HIf(cond=cond, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="strict int"):
        validate_hfunction(func)


def test_bittest_bitidx_negative():
    """HBitTest with negative bit_idx must be rejected."""
    flags = u8("flags")
    cond = HBitTest(var=flags, bit_idx=-1)
    stmt = HIf(cond=cond, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="out of range"):
        validate_hfunction(func)


def test_bittest_bitidx_out_of_range():
    """HBitTest bit_idx >= var width must be rejected."""
    flags = u8("flags")
    cond = HBitTest(var=flags, bit_idx=8)
    stmt = HIf(cond=cond, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [HParam("flags", UInt(8))], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="out of range"):
        validate_hfunction(func)


# ===================================================================
# Lowering correctness: BitOp("set"/"clr") for each value
# ===================================================================

def test_bitset_lower_set():
    """HBitSet(value=1) lowers to BitOp('set') -> BSET in ASM."""
    flags = u8("flags")
    func = simple_function(
        "t",
        [HParam("flags", UInt(8))],
        [HBitSet(var=flags, bit_idx=2, value=1)],
        Void(),
    )
    result = run_codegen_from_hir(func, ToyTarget())
    assert "BSET" in result.asm_text


def test_bitset_lower_clr():
    """HBitSet(value=0) lowers to BitOp('clr') -> BCLR in ASM."""
    flags = u8("flags")
    func = simple_function(
        "t",
        [HParam("flags", UInt(8))],
        [HBitSet(var=flags, bit_idx=5, value=0)],
        Void(),
    )
    result = run_codegen_from_hir(func, ToyTarget())
    assert "BCLR" in result.asm_text


def test_bittest_in_while_cond():
    """HBitTest as HWhile condition lowers to BTEST in ASM."""
    flags = u8("flags")
    func = HFunction(
        name="t",
        params=(HParam("flags", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HWhile(
                cond=HBitTest(var=flags, bit_idx=0),
                body=(HBreak(),),
            ),
            HReturn(values=(hconst(0),)),
        ),
    )
    result = run_codegen_from_hir(func, ToyTarget())
    asm = result.asm_text
    assert "BTEST" in asm
    assert "while_test" in asm
