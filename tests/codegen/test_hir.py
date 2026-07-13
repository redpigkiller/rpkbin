"""
Tests for HIR node construction and hir_validate.
"""
import pytest
from rpkbin.codegen.hir import (
    HVar, HConst, HBinOp, HCmp, HAssign, HIf, HFor, HReturn,
    HInlineAsm, HCall, HFunction, HParam, HExternFn, HExprStmt,
    HCast, HExtract, HConcat, HLogical, HNot, HBitTest, HWhile,
    UInt, SInt, Void, u8, u16, hconst, simple_function,
)
from rpkbin.codegen.hir_validate import validate_hfunction, HIRValidationError


# ---------------------------------------------------------------------------
# Test 1: All HIR nodes can be constructed
# ---------------------------------------------------------------------------

def test_hir_node_construction():
    """Every major HIR node type can be instantiated without error."""
    a = HVar("a", UInt(8))
    b = HConst(5, UInt(8))
    expr = HBinOp("add", a, b, UInt(8))
    cond = HCmp("lt", a, b)
    assign = HAssign(target=a, value=b)
    ret = HReturn(values=(b,))
    inline = HInlineAsm(text="NOP")
    call = HCall(name="foo", args=(a,), return_ty=UInt(8))
    if_stmt = HIf(cond=cond, then_body=(ret,))
    for_stmt = HFor(var=a, init=hconst(0), bound=hconst(5), body=(assign,))
    func = HFunction(
        name="test",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(if_stmt,),
    )
    assert func.name == "test"
    assert len(func.params) == 1


# ---------------------------------------------------------------------------
# Test 2: Valid HIR passes validation
# ---------------------------------------------------------------------------

def test_validate_valid_function():
    """A well-formed function must not raise any validation error."""
    a = u8("a")
    b = hconst(10)
    cond = HCmp("lt", a, b)
    func = simple_function(
        "ok_func",
        params=[HParam("a", UInt(8))],
        body=[HReturn(values=(a,))],
        return_ty=UInt(8),
    )
    validate_hfunction(func)  # must not raise


def test_validate_return_rejects_string_value():
    """HReturn values must be HIR expressions, not string placeholders."""
    func = simple_function(
        "bad_return", [], [HReturn(values=("@g0",))], UInt(8)
    )
    with pytest.raises(HIRValidationError, match="HReturn values must be HExpr"):
        validate_hfunction(func)


# ---------------------------------------------------------------------------
# Test 3: Assignment width mismatch raises error
# ---------------------------------------------------------------------------

def test_validate_assignment_width_mismatch():
    """Assigning a 16-bit value to an 8-bit variable must be rejected."""
    a = HVar("a", UInt(8))
    b = HConst(5, UInt(16))  # 16-bit value assigned to 8-bit var
    stmt = HAssign(target=a, value=b)
    func = simple_function("bad", [], [stmt], Void())
    with pytest.raises(HIRValidationError, match="width mismatch"):
        validate_hfunction(func)


# ---------------------------------------------------------------------------
# Test 4: Shift with non-constant amount raises error
# ---------------------------------------------------------------------------

def test_validate_shift_non_constant():
    """Using a variable as a shift amount must be rejected."""
    a = u8("a")
    n = u8("n")  # variable as shift amount — illegal
    bad_shift = HBinOp("shl", a, n, UInt(8))
    stmt = HAssign(target=a, value=bad_shift)
    func = simple_function(
        "bad",
        [HParam("a", UInt(8)), HParam("n", UInt(8))],
        [stmt],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="compile-time constant"):
        validate_hfunction(func)


# ---------------------------------------------------------------------------
# Test 5: HLogical in non-condition position raises error
# ---------------------------------------------------------------------------

def test_validate_logical_in_value_position():
    """HLogical as an assignment value must be rejected."""
    a = u8("a")
    b = u8("b")
    cond = HCmp("lt", a, b)
    logical = HLogical("and", cond, cond)
    stmt = HAssign(target=a, value=logical)  # type: ignore
    func = simple_function(
        "bad",
        [HParam("a", UInt(8)), HParam("b", UInt(8))],
        [stmt],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="condition position"):
        validate_hfunction(func)


# ---------------------------------------------------------------------------
# Test 6: HExtract storage-width validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msb, lsb, ty, target",
    [
        (0, 0, UInt(8), u8("x")),      # 1-bit field into u8 storage
        (7, 4, UInt(8), u8("x")),      # 4-bit field into u8 storage
        (8, 0, UInt(16), u16("x")),    # 9-bit field into u16 storage
        (15, 0, UInt(16), u16("x")),   # full-width u16 field
    ],
)
def test_validate_extract_valid_storage_width(msb, lsb, ty, target):
    """HExtract should accept field widths up to the declared storage width."""
    a = HVar("a", UInt(16))
    good = HExtract(a, msb=msb, lsb=lsb, ty=ty)
    stmt = HAssign(target=target, value=good)
    func = simple_function("ok_extract", [HParam("a", UInt(16))], [stmt], Void())
    validate_hfunction(func)


def test_validate_extract_invalid_range_rejected():
    """HExtract with msb < lsb must be rejected."""
    a = HVar("a", UInt(16))
    bad = HExtract(a, msb=3, lsb=4, ty=UInt(8))
    stmt = HAssign(target=u8("x"), value=bad)
    func = simple_function("bad", [HParam("a", UInt(16))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="invalid"):
        validate_hfunction(func)


def test_validate_extract_storage_too_small_rejected():
    """HExtract field width larger than the storage width must be rejected."""
    a = HVar("a", UInt(16))
    bad = HExtract(a, msb=8, lsb=0, ty=UInt(8))  # 9 bits into u8 storage
    stmt = HAssign(target=u8("x"), value=bad)
    func = simple_function("bad", [HParam("a", UInt(16))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="does not fit"):
        validate_hfunction(func)


def test_validate_extract_unsupported_storage_width_rejected():
    """HExtract storage widths remain limited to u8/u16."""
    a = HVar("a", UInt(8))
    bad = HExtract(a, msb=3, lsb=0, ty=UInt(4))
    stmt = HAssign(target=HVar("x", UInt(4)), value=bad)
    func = simple_function("bad", [HParam("a", UInt(8))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="storage width"):
        validate_hfunction(func)


# ---------------------------------------------------------------------------
# Test 7: HBitTest in non-condition position raises error
# ---------------------------------------------------------------------------

def test_validate_bittest_in_value_position():
    """HBitTest used as an assignment value must be rejected."""
    a = u8("a")
    bit = HBitTest(var=a, bit_idx=3)
    stmt = HAssign(target=a, value=bit)  # type: ignore
    func = simple_function("bad", [HParam("a", UInt(8))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="condition position"):
        validate_hfunction(func)


# ---------------------------------------------------------------------------
# Test 8: builder helpers work
# ---------------------------------------------------------------------------

def test_builder_helpers():
    """Convenience builder functions produce correctly typed nodes."""
    assert u8("x").ty == UInt(8)
    assert u16("y").ty == UInt(16)
    assert hconst(42).value == 42
    assert hconst(42).ty == UInt(8)
    f = simple_function("f", [], [], Void())
    assert f.name == "f"
    assert f.is_inline is False


# ---------------------------------------------------------------------------
# Bug 3 regression: condition validation
# ---------------------------------------------------------------------------

def test_validate_hlogical_xor_rejected():
    """HLogical('xor') must be rejected by validator."""
    a = u8("a")
    b = u8("b")
    cond = HLogical("xor", HCmp("lt", a, b), HCmp("eq", a, b))
    stmt = HIf(cond=cond, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [HParam("a", UInt(8)), HParam("b", UInt(8))], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="HLogical op"):
        validate_hfunction(func)


def test_validate_nested_invalid_hlogical_rejected():
    """Deeply nested HLogical with invalid op must be rejected."""
    a = u8("a")
    inner = HLogical("nand", HCmp("lt", a, hconst(5)), HCmp("gt", a, hconst(0)))
    outer = HLogical("and", inner, HCmp("eq", a, hconst(1)))
    stmt = HIf(cond=outer, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [HParam("a", UInt(8))], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="HLogical op"):
        validate_hfunction(func)


def test_validate_bittest_index_out_of_range():
    """HBitTest with bit index >= var width must be rejected."""
    a = u8("a")  # 8-bit
    cond = HBitTest(var=a, bit_idx=8)  # max valid is 7
    stmt = HIf(cond=cond, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [HParam("a", UInt(8))], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="out of range"):
        validate_hfunction(func)


def test_validate_hcmp_operand_illegal_value():
    """HCmp operand with illegal value expression (HLogical) must be rejected."""
    a = u8("a")
    bad_val = HLogical("and", HCmp("lt", a, hconst(5)), HCmp("gt", a, hconst(0)))
    cond = HCmp("eq", bad_val, hconst(1))  # type: ignore
    stmt = HIf(cond=cond, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [HParam("a", UInt(8))], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="condition position"):
        validate_hfunction(func)


def test_validate_valid_nested_hlogical_hnot_passes():
    """Valid nested HLogical('and')/HLogical('or')/HNot must pass validation."""
    a = u8("a")
    b = u8("b")
    # not (a < 5 and b > 3)
    inner = HLogical("and", HCmp("lt", a, hconst(5)), HCmp("gt", b, hconst(3)))
    cond = HNot(inner)
    stmt = HWhile(cond=cond, body=(HAssign(target=a, value=hconst(0)),))
    func = simple_function("valid", [HParam("a", UInt(8)), HParam("b", UInt(8))], [stmt], UInt(8))
    validate_hfunction(func)  # must not raise


def test_validate_hcmp_invalid_op_rejected():
    """HCmp with invalid op in condition must be rejected by validator."""
    a = u8("a")
    cond = HCmp("xx", a, hconst(5))  # "xx" is not a valid comparison op
    stmt = HIf(cond=cond, then_body=(HReturn(values=(hconst(1),)),))
    func = simple_function("bad", [HParam("a", UInt(8))], [stmt], UInt(8))
    with pytest.raises(HIRValidationError, match="HCmp op"):
        validate_hfunction(func)
