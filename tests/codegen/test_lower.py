"""Tests for the HIR → LIR lowering pass (lower.py).

All tests confirm that lower_function() produces structurally valid
lir.Function instances with the expected block/terminator layout.
The existing test_pipeline.py tests are not touched; these tests only
import from hir.py, lir.py, and lower.py.
"""

from __future__ import annotations

import pytest

from rpkbin.codegen.hir import (
    HAssign,
    HBinOp,
    HCall,
    HCast,
    HCmp,
    HConst,
    HContinue,
    HExprStmt,
    HExtract,
    HFor,
    HFunction,
    HInlineAsm,
    HParam,
    HReturn,
    HIf,
    HVar,
    HWhile,
    UInt,
    Void,
    hconst,
    simple_function,
    s8,
    s16,
    u8,
    u16,
)
from rpkbin.codegen import lir
from rpkbin.codegen.lower import lower_function


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _labels(func: lir.Function) -> list[str]:
    return [b.label for b in func.blocks]


def _terminators(func: lir.Function):
    return [b.terminator for b in func.blocks]


def _find_block(func: lir.Function, label: str) -> lir.Block:
    for b in func.blocks:
        if b.label == label:
            return b
    raise KeyError(f"no block with label {label!r}")


# ---------------------------------------------------------------------------
# Test: HIf → correct block structure and labels
# ---------------------------------------------------------------------------

def test_if_else_block_structure():
    """All-terminating HIf/else removes the unreachable merge block."""
    a = u8("a")
    cond = HCmp("lt", a, hconst(10))
    func = HFunction(
        name="if_test",
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

    labels = _labels(result)
    assert "entry" in labels
    assert "if_then" in labels
    assert "if_else" in labels
    assert "if_merge" not in labels

    # entry must branch to then and else
    entry = _find_block(result, "entry")
    term = entry.terminator
    assert isinstance(term, lir.BrIf)
    assert term.true_label == "if_then"
    assert term.false_label == "if_else"

    # then block must end with Return(1)
    then_block = _find_block(result, "if_then")
    assert isinstance(then_block.terminator, lir.Return)

    # else block must end with Return(0)
    else_block = _find_block(result, "if_else")
    assert isinstance(else_block.terminator, lir.Return)


def test_comparison_snapshots_left_before_right_call():
    cond = HCmp(
        "eq",
        HVar("x", UInt(8), reg_hint="g1"),
        HCall("read", (), UInt(8), clobbers=("g1",)),
    )
    func = simple_function(
        "ordered_compare",
        params=[HParam("x", UInt(8), reg_hint="g1")],
        body=[HIf(cond, (HReturn((hconst(1),)),), else_body=(HReturn((hconst(0),)),))],
        return_ty=UInt(8),
    )

    entry = _find_block(lower_function(func), "entry")
    assert len(entry.statements) == 2
    snapshot, call = entry.statements
    assert isinstance(snapshot.value, lir.VReg)
    assert snapshot.value.name == "x"
    assert isinstance(call.value, lir.Call)
    assert isinstance(entry.terminator.cond.left, lir.Var)
    assert entry.terminator.cond.left.name == snapshot.target.name


def test_if_no_else():
    """HIf without else: false branch goes directly to merge."""
    a = u8("a")
    cond = HCmp("ne", a, hconst(0))
    stmt = HAssign(target=a, value=hconst(1))
    func = simple_function(
        "no_else",
        params=[HParam("a", UInt(8))],
        body=[HIf(cond=cond, then_body=(stmt,)), HReturn(values=(a,))],
        return_ty=UInt(8),
    )
    result = lower_function(func)

    labels = _labels(result)
    assert "if_then" in labels
    assert "if_merge" in labels
    # There must be NO if_else block
    assert not any("if_else" in lbl for lbl in labels)

    entry = _find_block(result, "entry")
    term = entry.terminator
    assert isinstance(term, lir.BrIf)
    assert term.false_label == "if_merge"


def test_if_elif_else():
    """HIf with one elif branch produces the correct block chain.

    Expected structure:
        entry  → BrIf(cond0) → if_then / if_elif_0_test
        if_then → Return(1)
        if_elif_0_test → BrIf(cond1) → if_elif_0_body / if_else
        if_elif_0_body → Return(2)
        if_else → Return(3)
    """
    a = u8("a")
    cond0 = HCmp("lt", a, hconst(5))
    cond1 = HCmp("eq", a, hconst(7))
    func = HFunction(
        name="elif_test",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=cond0,
                then_body=(HReturn(values=(hconst(1),)),),
                elif_branches=((cond1, (HReturn(values=(hconst(2),)),)),),
                else_body=(HReturn(values=(hconst(3),)),),
            ),
        ),
    )
    result = lower_function(func)
    labels = _labels(result)

    # All basic labels must be present
    assert "entry" in labels
    assert any("if_then" in l for l in labels)
    assert any("elif" in l for l in labels), f"No elif block in: {labels}"
    assert any("if_else" in l for l in labels)

    # Entry must branch
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.BrIf)
    true_lbl = entry.terminator.true_label
    false_lbl = entry.terminator.false_label

    # then-branch returns 1
    then_block = _find_block(result, true_lbl)
    assert isinstance(then_block.terminator, lir.Return)
    assert isinstance(then_block.terminator.value, lir.Const)
    assert then_block.terminator.value.value == 1

    # false-branch leads to elif test block (another BrIf)
    elif_test_block = _find_block(result, false_lbl)
    assert isinstance(elif_test_block.terminator, lir.BrIf)

    # elif true-branch returns 2
    elif_body_block = _find_block(result, elif_test_block.terminator.true_label)
    assert isinstance(elif_body_block.terminator, lir.Return)
    assert isinstance(elif_body_block.terminator.value, lir.Const)
    assert elif_body_block.terminator.value.value == 2

    # final else returns 3
    else_block = _find_block(result, elif_test_block.terminator.false_label)
    assert isinstance(else_block.terminator, lir.Return)
    assert isinstance(else_block.terminator.value, lir.Const)
    assert else_block.terminator.value.value == 3

    # All labels must be unique
    assert len(labels) == len(set(labels))


def test_if_elif_else_all_return_removes_unreachable_merge_regression():
    """All-return if/elif/else should not leave a dead merge/default-return block."""
    a = u8("a")
    func = HFunction(
        name="elif_all_return",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=HCmp("lt", a, hconst(5)),
                then_body=(HReturn(values=(hconst(1),)),),
                elif_branches=((HCmp("eq", a, hconst(7)), (HReturn(values=(hconst(2),)),)),),
                else_body=(HReturn(values=(hconst(3),)),),
            ),
        ),
    )

    result = lower_function(func)
    labels = _labels(result)

    assert not any("if_merge" in label for label in labels), labels
    assert len(result.blocks) == 5


def test_nested_if():
    """Nested HIf produces correct label sets for inner and outer."""
    a = u8("a")
    b = u8("b")
    inner_if = HIf(
        cond=HCmp("eq", b, hconst(0)),
        then_body=(HReturn(values=(hconst(99),)),),
        else_body=(HReturn(values=(b,)),),
    )
    outer_if = HIf(
        cond=HCmp("lt", a, hconst(5)),
        then_body=(inner_if,),
        else_body=(HReturn(values=(a,)),),
    )
    func = HFunction(
        name="nested_if",
        params=(HParam("a", UInt(8)), HParam("b", UInt(8))),
        return_ty=UInt(8),
        body=(outer_if,),
    )
    result = lower_function(func)
    labels = _labels(result)

    # Should have at least 6 blocks: entry, outer_then, outer_else, outer_merge,
    # inner_then, inner_else (inner_merge may be dead)
    assert len(labels) >= 5
    assert "entry" in labels
    # All labels must be unique (validate_function already checks this, but assert explicitly)
    assert len(labels) == len(set(labels))


# ---------------------------------------------------------------------------
# Test: HFor → counter block + BrCmp("ne") loop
# ---------------------------------------------------------------------------

def test_for_fixed_count():
    """HFor materializes the loop var, reads it in the body, and steps correctly."""
    x = u8("x")
    i = HVar("i", UInt(8))
    func = HFunction(
        name="for_test",
        params=(HParam("x", UInt(8)),),
        return_ty=Void(),
        body=(
            HFor(
                var=i,
                init=hconst(0),
                bound=hconst(5),
                body=(HAssign(target=x, value=i),),
            ),
        ),
    )
    result = lower_function(func)
    labels = _labels(result)

    assert any("for_test" in lbl for lbl in labels)
    assert any("for_body" in lbl for lbl in labels)
    assert any("for_step" in lbl for lbl in labels)
    assert any("for_exit" in lbl for lbl in labels)

    test_block = next(b for b in result.blocks if "for_test" in b.label)
    assert isinstance(test_block.terminator, lir.BrCmp)
    assert test_block.terminator.op == "ne"
    assert isinstance(test_block.terminator.right, lir.Const)
    assert test_block.terminator.right.value == 0

    entry = _find_block(result, "entry")
    assert len(entry.statements) >= 2
    init_stmt = entry.statements[0]
    counter_stmt = entry.statements[1]
    assert isinstance(init_stmt, lir.Assign)
    assert init_stmt.target.name == "i"
    assert isinstance(init_stmt.value, lir.Const)
    assert init_stmt.value.value == 0
    assert isinstance(counter_stmt, lir.Assign)
    assert counter_stmt.target.name.startswith("__counter_")
    assert isinstance(counter_stmt.value, lir.Const)
    assert counter_stmt.value.value == 5

    body_block = next(b for b in result.blocks if "for_body" in b.label)
    body_stmt = body_block.statements[0]
    assert isinstance(body_stmt, lir.Assign)
    assert isinstance(body_stmt.value, lir.Var)
    assert body_stmt.value.name == "i"
    assert isinstance(body_block.terminator, lir.Jump)
    assert body_block.terminator.label.startswith("for_step_")

    step_block = next(b for b in result.blocks if "for_step" in b.label)
    assert len(step_block.statements) == 2
    step_var = step_block.statements[0]
    step_counter = step_block.statements[1]
    assert isinstance(step_var, lir.Assign)
    assert step_var.target.name == "i"
    assert isinstance(step_var.value, lir.BinOp)
    assert step_var.value.op == "add"
    assert isinstance(step_counter, lir.Assign)
    assert step_counter.target.name.startswith("__counter_")
    assert isinstance(step_counter.value, lir.BinOp)
    assert step_counter.value.op == "sub"
    assert isinstance(step_block.terminator, lir.Jump)
    assert step_block.terminator.label.startswith("for_test_")


def test_for_body_writes_loop_var_rejected():
    """A write to the loop variable must be rejected by both layers."""
    from rpkbin.codegen.hir_validate import validate_hfunction, HIRValidationError

    i = HVar("i", UInt(8))
    write_target = HVar("i", UInt(8))  # different instance, same name

    func = simple_function(
        "bad_for",
        params=[HParam("x", UInt(8))],
        body=[
            HFor(
                var=i,
                init=hconst(0),
                bound=hconst(3),
                body=(HAssign(target=write_target, value=hconst(1)),),
            )
        ],
        return_ty=Void(),
    )
    with pytest.raises(HIRValidationError, match="loop variable"):
        validate_hfunction(func)
    with pytest.raises(NotImplementedError, match="loop variable"):
        lower_function(func)


def test_for_body_writes_loop_var_same_instance_rejected():
    """Same HVar instance write is also rejected."""
    from rpkbin.codegen.hir_validate import validate_hfunction, HIRValidationError

    i = HVar("i", UInt(8))
    func = simple_function(
        "bad_for2",
        params=[HParam("x", UInt(8))],
        body=[
            HFor(
                var=i,
                init=hconst(0),
                bound=hconst(3),
                body=(HAssign(target=i, value=hconst(1)),),
            )
        ],
        return_ty=Void(),
    )
    with pytest.raises(HIRValidationError, match="loop variable"):
        validate_hfunction(func)
    with pytest.raises(NotImplementedError, match="loop variable"):
        lower_function(func)


# ---------------------------------------------------------------------------
# Test: HReturn — single and multi-value
# ---------------------------------------------------------------------------

def test_return_single_value():
    """HReturn with one value produces lir.Return."""
    func = simple_function(
        "ret_one",
        params=[HParam("a", UInt(8))],
        body=[HReturn(values=(u8("a"),))],
        return_ty=UInt(8),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.Return)


def test_return_multi_value():
    """HReturn with multiple values produces lir.MultiReturn."""
    func = HFunction(
        name="ret_two",
        params=(),
        return_ty=(UInt(8), UInt(8)),
        body=(HReturn(values=(hconst(1), hconst(2))),),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.MultiReturn)
    assert len(entry.terminator.values) == 2


# ---------------------------------------------------------------------------
# Test: HInlineAsm passthrough
# ---------------------------------------------------------------------------

def test_inline_asm_passthrough():
    """HInlineAsm is emitted as an __asm__ assign in LIR."""
    func = simple_function(
        "asm_test",
        params=[],
        body=[HInlineAsm(text="NOP"), HReturn(values=(hconst(0),))],
        return_ty=UInt(8),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    # First statement should be the __asm__ assign
    assert len(entry.statements) >= 1
    asm_stmt = entry.statements[0]
    assert isinstance(asm_stmt, lir.Assign)
    assert asm_stmt.target.name == "__asm__"


# ---------------------------------------------------------------------------
# Test: HCall single-value return
# ---------------------------------------------------------------------------

def test_call_single_return():
    """HCall is lowered to an Assign of a lir.Call expression."""
    a = u8("a")
    call = HCall(
        name="ext_fn",
        args=(a,),
        return_ty=UInt(8),
        arg_regs=("r1",),
        return_regs=("r0",),
        clobbers=("r2",),
    )
    result_var = u8("result")
    func = HFunction(
        name="call_test",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HAssign(target=result_var, value=call),
            HReturn(values=(result_var,)),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    # First stmt should be an Assign whose value is a lir.Call
    assert len(entry.statements) >= 1
    call_stmt = entry.statements[0]
    assert isinstance(call_stmt, lir.Assign)
    call_expr = call_stmt.value
    assert isinstance(call_expr, lir.Call)
    assert call_expr.name == "ext_fn"
    assert call_expr.arg_regs == ("r1",)
    assert call_expr.return_regs == ("r0",)
    assert call_expr.clobbers == ("r2",)


def test_call_void_statement():
    """HExprStmt(HCall(...)) lowers to CallStmt without a dummy __ret temp."""
    a = u8("a")
    call = HCall(
        name="ext_fn_void",
        args=(a,),
        return_ty=Void(),
        arg_regs=("r1",),
        return_regs=(),
        clobbers=("r2",),
    )
    func = HFunction(
        name="call_void_test",
        params=(HParam("a", UInt(8)),),
        return_ty=Void(),
        body=(
            HExprStmt(expr=call),
            HReturn(values=()),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    # First stmt should be a CallStmt
    assert len(entry.statements) >= 1
    call_stmt = entry.statements[0]
    assert isinstance(call_stmt, lir.CallStmt)
    assert call_stmt.call.name == "ext_fn_void"
    assert call_stmt.call.arg_regs == ("r1",)
    assert call_stmt.call.return_regs == ()
    assert call_stmt.call.clobbers == ("r2",)
    assert not any(
        isinstance(stmt, lir.Assign) and stmt.target.name.startswith("__ret_")
        for stmt in entry.statements
    )


def test_call_stmt_preserves_unknown_clobbers():
    """Statement-form calls keep clobbers=None as unknown/all-clobber metadata."""
    call = HCall(name="ext_fn_void", args=(), return_ty=Void(), clobbers=None)
    func = HFunction(
        name="call_void_unknown_clobbers",
        params=(),
        return_ty=Void(),
        body=(HExprStmt(expr=call), HReturn(values=())),
    )

    result = lower_function(func)
    entry = _find_block(result, "entry")

    call_stmt = entry.statements[0]
    assert isinstance(call_stmt, lir.CallStmt)
    assert call_stmt.call.clobbers is None


@pytest.mark.parametrize(
    ("source", "target", "kind", "expected_kind"),
    [
        (u8("src"), u16("wide"), "u16_from", "zext"),
        (s8("src"), s16("wide"), "s16_from", "sext"),
    ],
)
def test_widening_cast_lowers_to_extend_node(source, target, kind, expected_kind):
    func = HFunction(
        name=f"{expected_kind}_cast",
        params=(HParam("src", source.ty),),
        return_ty=target.ty,
        body=(
            HAssign(target=target, value=HCast(kind=kind, expr=source, to_ty=target.ty)),
            HReturn(values=(target,)),
        ),
    )

    result = lower_function(func)
    entry = _find_block(result, "entry")
    cast_stmt = entry.statements[0]

    assert isinstance(cast_stmt, lir.Assign)
    assert isinstance(cast_stmt.value, lir.Extend)
    assert cast_stmt.value.kind == expected_kind
    assert cast_stmt.value.width == 16
    assert isinstance(cast_stmt.value.value, lir.Var)
    assert cast_stmt.value.value.name == "src"
    assert cast_stmt.value.value.width == 8


@pytest.mark.parametrize("kind", ["low_byte", "high_byte"])
def test_byte_cast_materializes_wide_nonleaf_before_narrowing(kind):
    source = u16("src")
    target = u8("byte")
    wide_expr = HBinOp("add", source, HConst(1, UInt(16)), UInt(16))
    func = HFunction(
        name=f"{kind}_nonleaf",
        params=(HParam("src", UInt(16)),),
        return_ty=UInt(8),
        body=(
            HAssign(target, HCast(kind, wide_expr, UInt(8))),
            HReturn(values=(target,)),
        ),
    )

    entry = _find_block(lower_function(func), "entry")
    wide, narrow = entry.statements[:2]
    assert isinstance(wide, lir.Assign)
    assert wide.target.width == 16
    assert isinstance(narrow, lir.Assign)
    assert isinstance(narrow.value, lir.BinOp)
    assert narrow.value.left == wide.target


# ---------------------------------------------------------------------------
# Structured-control and memory lowering
# ---------------------------------------------------------------------------

def test_while_no_longer_raises():
    """HWhile lowering is supported."""
    from rpkbin.codegen.hir import HWhile
    func = simple_function(
        "while_loop",
        params=[HParam("a", UInt(8))],
        body=[HWhile(cond=HCmp("ne", u8("a"), hconst(0)), body=())],
        return_ty=Void(),
    )
    # Must not raise.
    result = lower_function(func)
    labels = _labels(result)
    assert any("while_test" in lbl for lbl in labels)


def test_hstore_no_longer_raises():
    """HStore lowering is supported."""
    from rpkbin.codegen.hir import HStore
    func = simple_function(
        "store_value",
        params=[HParam("a", UInt(8))],
        body=[HStore(ptr_expr=u8("a"), value_expr=hconst(0))],
        return_ty=Void(),
    )
    # Must not raise.
    result = lower_function(func)
    from rpkbin.codegen import lir as _lir
    assert any(
        isinstance(s, _lir.MemStore)
        for b in result.blocks
        for s in b.statements
    )


# ---------------------------------------------------------------------------
# Test: validate_function is called and blocks have unique labels
# ---------------------------------------------------------------------------

def test_output_passes_lir_validation():
    """lower_function always returns a structurally valid lir.Function."""
    a = u8("a")
    func = HFunction(
        name="valid_lir",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=HCmp("ge", a, hconst(100)),
                then_body=(HReturn(values=(hconst(1),)),),
            ),
            HReturn(values=(hconst(0),)),
        ),
    )
    # Should not raise
    result = lower_function(func)
    labels = _labels(result)
    assert len(labels) == len(set(labels)), "block labels are not unique!"


# ---------------------------------------------------------------------------
# Extra HExtract / HFor lowering coverage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msb, lsb, ty, mask",
    [
        (0, 0, UInt(8), 0x1),
        (7, 4, UInt(8), 0xF),
        (8, 0, UInt(16), 0x1FF),
        (15, 0, UInt(16), 0xFFFF),
    ],
)
def test_extract_lower_uses_storage_width(msb, lsb, ty, mask):
    source = u16("a")
    target = u8("x") if ty == UInt(8) else HVar("x", UInt(16))
    func = HFunction(
        name="extract_test",
        params=(HParam("a", UInt(16)),),
        return_ty=Void(),
        body=(
            HAssign(target=target, value=HExtract(source, msb=msb, lsb=lsb, ty=ty)),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")
    assign = entry.statements[0]
    assert isinstance(assign, lir.Assign)
    assert assign.target.width == ty.width
    assert isinstance(assign.value, lir.BinOp)
    assert assign.value.op == "and"
    assert assign.value.width == ty.width
    assert isinstance(assign.value.right, lir.Const)
    assert assign.value.right.value == mask
    assert isinstance(assign.value.left, lir.BinOp)
    assert assign.value.left.op == "shr"
    assert assign.value.left.width == ty.width


def test_for_direct_continue_jumps_to_step():
    i = HVar("i", UInt(8))
    func = simple_function(
        "for_continue",
        [],
        [
            HFor(
                var=i,
                init=hconst(0),
                bound=hconst(2),
                body=(HContinue(),),
            )
        ],
        Void(),
    )
    result = lower_function(func)
    body_block = next(b for b in result.blocks if "for_body" in b.label)
    assert isinstance(body_block.terminator, lir.Jump)
    assert body_block.terminator.label.startswith("for_step_")


def test_for_conditional_continue_jumps_to_step():
    i = HVar("i", UInt(8))
    x = u8("x")
    func = simple_function(
        "for_cond_continue",
        [HParam("x", UInt(8))],
        [
            HFor(
                var=i,
                init=hconst(0),
                bound=hconst(2),
                body=(
                    HIf(
                        cond=HCmp("eq", x, hconst(0)),
                        then_body=(HContinue(),),
                        else_body=(HAssign(target=x, value=i),),
                    ),
                ),
            )
        ],
        Void(),
    )
    result = lower_function(func)
    then_block = next(b for b in result.blocks if "if_then" in b.label)
    assert isinstance(then_block.terminator, lir.Jump)
    assert then_block.terminator.label.startswith("for_step_")


def test_for_nested_while_continue_stays_inner():
    i = HVar("i", UInt(8))
    x = u8("x")
    func = simple_function(
        "for_nested_while_continue",
        [HParam("x", UInt(8))],
        [
            HFor(
                var=i,
                init=hconst(0),
                bound=hconst(2),
                body=(
                    HWhile(
                        cond=HCmp("ne", x, hconst(0)),
                        body=(HContinue(),),
                    ),
                ),
            )
        ],
        Void(),
    )
    result = lower_function(func)
    while_body = next(b for b in result.blocks if "while_body" in b.label)
    assert isinstance(while_body.terminator, lir.Jump)
    assert while_body.terminator.label.startswith("while_test")
