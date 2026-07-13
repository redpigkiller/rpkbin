"""HLogical short-circuit lowering tests.

All tests confirm that HLogical("and"/"or") in condition positions:
1. Passes HIR validation
2. Lowers to short-circuit basic blocks (not BinOp)
3. Produces structurally valid LIR that passes lir.validate_function()
4. HLogical in value position is still rejected (check 3 regression)
"""

from __future__ import annotations

import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.hir import (
    HAssign,
    HCmp,
    HFunction,
    HIf,
    HLogical,
    HNot,
    HParam,
    HReturn,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _labels(func: lir.Function) -> list[str]:
    return [b.label for b in func.blocks]


def _find_block(func: lir.Function, label: str) -> lir.Block:
    for b in func.blocks:
        if b.label == label:
            return b
    raise KeyError(f"no block {label!r}")


# ---------------------------------------------------------------------------
# HLogical("and", ...) in HIf
# ---------------------------------------------------------------------------

def test_and_in_if_short_circuits():
    """HLogical('and', a<b, c>d): left false → goes directly to else."""
    a, b, c, d = (u8(n) for n in ("a", "b", "c", "d"))
    cond = HLogical("and", HCmp("lt", a, b), HCmp("gt", c, d))
    func = HFunction(
        name="and_if",
        params=tuple(HParam(n, UInt(8)) for n in ("a", "b", "c", "d")),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=cond,
                then_body=(HReturn(values=(hconst(1),)),),
                else_body=(HReturn(values=(hconst(0),)),),
            ),
        ),
    )
    validate_hfunction(func)
    result = lower_function(func)

    # Reachable blocks only: entry, then, else, plus the short-circuit mid block.
    assert len(result.blocks) >= 4
    assert any("logical_mid" in b.label for b in result.blocks)

    # entry → BrIf(a<b) → mid / else
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.BrIf)
    assert "if_else" in entry.terminator.false_label, (
        "left false must go directly to else"
    )
    mid_lbl = entry.terminator.true_label

    # mid → BrIf(c>d) → then / else
    mid_block = _find_block(result, mid_lbl)
    assert isinstance(mid_block.terminator, lir.BrIf)
    assert "if_then" in mid_block.terminator.true_label
    assert "if_else" in mid_block.terminator.false_label

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# HLogical("or", ...) in HIf
# ---------------------------------------------------------------------------

def test_or_in_if_short_circuits():
    """HLogical('or', a<b, c>d): left true → goes directly to then."""
    a, b, c, d = (u8(n) for n in ("a", "b", "c", "d"))
    cond = HLogical("or", HCmp("lt", a, b), HCmp("gt", c, d))
    func = HFunction(
        name="or_if",
        params=tuple(HParam(n, UInt(8)) for n in ("a", "b", "c", "d")),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=cond,
                then_body=(HReturn(values=(hconst(1),)),),
                else_body=(HReturn(values=(hconst(0),)),),
            ),
        ),
    )
    validate_hfunction(func)
    result = lower_function(func)

    assert len(result.blocks) >= 4
    assert any("logical_mid" in b.label for b in result.blocks)

    # entry → BrIf(a<b) → then / mid
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.BrIf)
    assert "if_then" in entry.terminator.true_label, (
        "left true must go directly to then"
    )
    mid_lbl = entry.terminator.false_label

    # mid → BrIf(c>d) → then / else
    mid_block = _find_block(result, mid_lbl)
    assert isinstance(mid_block.terminator, lir.BrIf)
    assert "if_then" in mid_block.terminator.true_label
    assert "if_else" in mid_block.terminator.false_label

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# HNot(HCmp) — label swap, no fake boolean
# ---------------------------------------------------------------------------

def test_hnot_swaps_labels():
    """HNot(HCmp) swaps true/false labels; condition is raw Cmp, not eq(..., 0)."""
    a, b = u8("a"), u8("b")
    cond = HNot(HCmp("lt", a, b))
    func = HFunction(
        name="hnot_test",
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
    validate_hfunction(func)
    result = lower_function(func)

    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.BrIf)
    # Condition must be a raw Cmp (not a fake eq(..., 0))
    assert isinstance(entry.terminator.cond, lir.Cmp)
    assert entry.terminator.cond.op == "lt"
    # Labels swapped
    assert "if_else" in entry.terminator.true_label
    assert "if_then" in entry.terminator.false_label

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Nested: HLogical("and", HNot(...), HLogical("or", ...))
# ---------------------------------------------------------------------------

def test_nested_logical():
    """Nested HLogical(HNot, HLogical('or')) produces valid LIR."""
    a, b, c, d = (u8(n) for n in ("a", "b", "c", "d"))
    cond = HLogical(
        "and",
        HNot(HCmp("eq", a, b)),
        HLogical("or", HCmp("lt", c, d), HCmp("gt", a, hconst(0))),
    )
    func = HFunction(
        name="nested_logical",
        params=tuple(HParam(n, UInt(8)) for n in ("a", "b", "c", "d")),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=cond,
                then_body=(HReturn(values=(hconst(1),)),),
                else_body=(HReturn(values=(hconst(0),)),),
            ),
        ),
    )
    validate_hfunction(func)
    result = lower_function(func)
    # Must pass LIR validation
    lir.validate_function(result)
    # Must have multiple intermediate blocks (nested logicals)
    assert len(result.blocks) >= 5
    mid_blocks = [b for b in result.blocks if "logical_mid" in b.label]
    assert len(mid_blocks) >= 2


# ---------------------------------------------------------------------------
# HLogical in value position still rejected (check 3 regression)
# ---------------------------------------------------------------------------

def test_hlogical_in_value_position_still_rejected():
    """HLogical as assignment value must still be rejected by validator."""
    a, b = u8("a"), u8("b")
    logical = HLogical("and", HCmp("lt", a, b), HCmp("gt", a, hconst(0)))
    stmt = HAssign(target=a, value=logical)
    func = simple_function(
        "bad", [HParam("a", UInt(8)), HParam("b", UInt(8))], [stmt], Void()
    )
    with pytest.raises(HIRValidationError, match="condition position"):
        validate_hfunction(func)


# ---------------------------------------------------------------------------
# HLogical in HWhile condition
# ---------------------------------------------------------------------------

def test_hlogical_in_while():
    """HLogical('and') in HWhile condition produces valid LIR."""
    a, b, c = (u8(n) for n in ("a", "b", "c"))
    cond = HLogical(
        "and",
        HCmp("ne", a, hconst(0)),
        HCmp("lt", b, c),
    )
    func = HFunction(
        name="while_logical",
        params=tuple(HParam(n, UInt(8)) for n in ("a", "b", "c")),
        return_ty=UInt(8),
        body=(
            HWhile(cond=cond, body=(HAssign(target=a, value=hconst(0)),)),
            HReturn(values=(hconst(1),)),
        ),
    )
    validate_hfunction(func)
    result = lower_function(func)
    lir.validate_function(result)
    assert any("logical_mid" in b.label for b in result.blocks)
    assert any("while_test" in b.label for b in result.blocks)
    assert any("while_body" in b.label for b in result.blocks)
    assert any("while_exit" in b.label for b in result.blocks)


# ---------------------------------------------------------------------------
# HLogical('or') — left true short-circuits to then
# ---------------------------------------------------------------------------

def test_or_left_true_shortcuts():
    """HLogical('or'): left=true → then, right never evaluated."""
    a = u8("a")
    cond = HLogical(
        "or",
        HCmp("eq", a, hconst(0)),
        HCmp("eq", a, hconst(1)),
    )
    func = HFunction(
        name="or_shortcut",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=cond,
                then_body=(HReturn(values=(hconst(1),)),),
                else_body=(HReturn(values=(hconst(0),)),),
            ),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.BrIf)
    # true_label → if_then (short-circuit)
    assert "if_then" in entry.terminator.true_label
    # false_label → mid block (evaluate right)
    mid_lbl = entry.terminator.false_label
    mid_block = _find_block(result, mid_lbl)
    assert isinstance(mid_block.terminator, lir.BrIf)
    assert "if_then" in mid_block.terminator.true_label
    assert "if_else" in mid_block.terminator.false_label
    lir.validate_function(result)


# ---------------------------------------------------------------------------
# HLogical('and') — left false short-circuits to else
# ---------------------------------------------------------------------------

def test_and_left_false_shortcuts():
    """HLogical('and'): left=false → else, right never evaluated."""
    a = u8("a")
    cond = HLogical(
        "and",
        HCmp("eq", a, hconst(0)),
        HCmp("eq", a, hconst(1)),
    )
    func = HFunction(
        name="and_shortcut",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=cond,
                then_body=(HReturn(values=(hconst(1),)),),
                else_body=(HReturn(values=(hconst(0),)),),
            ),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.BrIf)
    # false_label → if_else (short-circuit)
    assert "if_else" in entry.terminator.false_label
    # true_label → mid block (evaluate right)
    mid_lbl = entry.terminator.true_label
    mid_block = _find_block(result, mid_lbl)
    assert isinstance(mid_block.terminator, lir.BrIf)
    assert "if_then" in mid_block.terminator.true_label
    assert "if_else" in mid_block.terminator.false_label
    lir.validate_function(result)


# ---------------------------------------------------------------------------
# HLogical ('and' / 'or') with nested HIf — multiple logical_mid blocks
# ---------------------------------------------------------------------------

def test_two_independent_hlogical_and():
    """Two independent HLogical('and') uses each get their own mid block."""
    a, b, c, d = (u8(n) for n in ("a", "b", "c", "d"))
    cond1 = HLogical("and", HCmp("lt", a, b), HCmp("gt", c, d))
    cond2 = HLogical("and", HCmp("eq", a, hconst(0)), HCmp("ne", b, hconst(0)))
    func = HFunction(
        name="two_and",
        params=tuple(HParam(n, UInt(8)) for n in ("a", "b", "c", "d")),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=cond1,
                then_body=(
                    HIf(
                        cond=cond2,
                        then_body=(HReturn(values=(hconst(1),)),),
                        else_body=(HReturn(values=(hconst(2),)),),
                    ),
                ),
                else_body=(HReturn(values=(hconst(0),)),),
            ),
        ),
    )
    validate_hfunction(func)
    result = lower_function(func)
    lir.validate_function(result)
    # Two independent HLogical('and') → at least 2 logical_mid blocks
    mid_blocks = [b for b in result.blocks if "logical_mid" in b.label]
    assert len(mid_blocks) >= 2
