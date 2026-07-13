"""Control-flow and memory lowering tests.

All tests confirm structural correctness of lower_function() output,
and that the output passes lir.validate_function().
The end-to-end test runs through the full run_codegen_from_hir() pipeline.
"""

from __future__ import annotations

import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.hir import (
    HAssign,
    HBinOp,
    HBitSet,
    HBitTest,
    HBreak,
    HCall,
    HCmp,
    HContinue,
    HFor,
    HFunction,
    HIf,
    HLoad,
    HLogical,
    HParam,
    HPoll,
    HReturn,
    HStore,
    HVar,
    HWhile,
    UInt,
    Void,
    hconst,
    simple_function,
    u8,
    u16,
)
from rpkbin.codegen.hir_validate import HIRValidationError, validate_hfunction
from rpkbin.codegen.lower import lower_function
from rpkbin.codegen.patterns import load_patterns_from_dicts
from rpkbin.codegen.pipeline import run_codegen_from_hir
from rpkbin.codegen.toy_target import ToyTarget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _labels(func: lir.Function) -> list[str]:
    return [b.label for b in func.blocks]


def _find_block(func: lir.Function, label: str) -> lir.Block:
    for b in func.blocks:
        if b.label == label:
            return b
    raise KeyError(f"no block with label {label!r}")


def _all_terminators(func: lir.Function):
    return [b.terminator for b in func.blocks]


# ---------------------------------------------------------------------------
# Test: HWhile(a != b) → test/body/exit blocks
# ---------------------------------------------------------------------------

def test_while_ne_block_structure():
    """HWhile(a != b) produces while_test/while_body/while_exit blocks."""
    a = u8("a")
    b = u8("b")
    cond = HCmp("ne", a, b)
    func = HFunction(
        name="while_ne",
        params=(HParam("a", UInt(8)), HParam("b", UInt(8))),
        return_ty=UInt(8),
        body=(
            HWhile(
                cond=cond,
                body=(HAssign(target=a, value=HVar("a", UInt(8))),),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = lower_function(func)
    labels = _labels(result)

    assert any("while_test" in lbl for lbl in labels)
    assert any("while_body" in lbl for lbl in labels)
    assert any("while_exit" in lbl for lbl in labels)

    # entry block must jump to while_test
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.Jump)
    test_lbl = entry.terminator.label

    # while_test must be a BrIf (true → body, false → exit)
    test_block = _find_block(result, test_lbl)
    assert isinstance(test_block.terminator, lir.BrIf)
    body_lbl = test_block.terminator.true_label
    exit_lbl = test_block.terminator.false_label

    # body block must jump back to test
    body_block = _find_block(result, body_lbl)
    assert isinstance(body_block.terminator, lir.Jump)
    assert body_block.terminator.label == test_lbl

    # exit block must exist
    _find_block(result, exit_lbl)

    # LIR must be valid
    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HWhile(a < b) → also lowers (lt condition)
# ---------------------------------------------------------------------------

def test_while_lt_block_structure():
    """HWhile(a < b) with 'lt' condition also lowers correctly."""
    a = u8("a")
    b = u8("b")
    cond = HCmp("lt", a, b)
    func = HFunction(
        name="while_lt",
        params=(HParam("a", UInt(8)), HParam("b", UInt(8))),
        return_ty=UInt(8),
        body=(
            HWhile(
                cond=cond,
                body=(HAssign(target=a, value=HVar("a", UInt(8))),),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = lower_function(func)
    labels = _labels(result)

    assert any("while_test" in lbl for lbl in labels)
    assert any("while_body" in lbl for lbl in labels)
    assert any("while_exit" in lbl for lbl in labels)

    test_block = next(b for b in result.blocks if "while_test" in b.label)
    assert isinstance(test_block.terminator, lir.BrIf)

    # The BrIf condition must be a Cmp with op="lt"
    cond_lir = test_block.terminator.cond
    assert isinstance(cond_lir, lir.Cmp)
    assert cond_lir.op == "lt"

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HPoll → body executes first, then condition checked
# ---------------------------------------------------------------------------

def test_poll_block_structure():
    """HPoll produces poll_body → poll_check → poll_exit blocks."""
    a = u8("a")
    cond = HCmp("ne", a, hconst(1))
    func = HFunction(
        name="poll_test",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HPoll(
                cond=cond,
                body=(HAssign(target=a, value=hconst(0)),),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = lower_function(func)
    labels = _labels(result)

    assert any("poll_body" in lbl for lbl in labels)
    assert any("poll_check" in lbl for lbl in labels)
    assert any("poll_exit" in lbl for lbl in labels)

    # entry must jump directly to poll_body (body runs first)
    entry = _find_block(result, "entry")
    assert isinstance(entry.terminator, lir.Jump)
    body_lbl = entry.terminator.label
    assert "poll_body" in body_lbl

    # poll_body terminates with Jump to poll_check (not BrIf directly)
    body_block = _find_block(result, body_lbl)
    assert isinstance(body_block.terminator, lir.Jump)
    check_lbl = body_block.terminator.label
    assert "poll_check" in check_lbl

    # poll_check has BrIf: true → poll_exit, false → poll_body
    check_block = _find_block(result, check_lbl)
    assert isinstance(check_block.terminator, lir.BrIf)
    assert "poll_exit" in check_block.terminator.true_label
    assert body_lbl in check_block.terminator.false_label

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HBreak inside HWhile → Jump(while_exit)
# ---------------------------------------------------------------------------

def test_break_in_while():
    """HBreak inside HWhile produces Jump to the while_exit label."""
    a = u8("a")
    func = HFunction(
        name="break_test",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HWhile(
                cond=HCmp("ne", a, hconst(0)),
                body=(
                    HBreak(),
                ),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = lower_function(func)
    labels = _labels(result)

    # Find the while_exit block
    exit_blocks = [b for b in result.blocks if "while_exit" in b.label]
    assert len(exit_blocks) == 1
    exit_lbl = exit_blocks[0].label

    # Find the body block and verify its terminator jumps to exit
    body_block = next(b for b in result.blocks if "while_body" in b.label)
    assert isinstance(body_block.terminator, lir.Jump)
    assert body_block.terminator.label == exit_lbl

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HContinue inside HWhile → Jump(while_test)
# ---------------------------------------------------------------------------

def test_continue_in_while():
    """HContinue inside HWhile produces Jump to the while_test label."""
    a = u8("a")
    func = HFunction(
        name="continue_test",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HWhile(
                cond=HCmp("ne", a, hconst(0)),
                body=(
                    HContinue(),
                ),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = lower_function(func)

    # Find the while_test block
    test_block = next(b for b in result.blocks if "while_test" in b.label)
    test_lbl = test_block.label

    # Find the body block — must jump back to test
    body_block = next(b for b in result.blocks if "while_body" in b.label)
    assert isinstance(body_block.terminator, lir.Jump)
    assert body_block.terminator.label == test_lbl

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HBreak inside HPoll → Jump(poll_exit)
# ---------------------------------------------------------------------------

def test_break_in_poll():
    """HBreak inside HPoll produces Jump to poll_exit."""
    a = u8("a")
    func = HFunction(
        name="poll_break",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HPoll(
                cond=HCmp("ne", a, hconst(0)),
                body=(HBreak(),),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = lower_function(func)

    # Find poll_exit label
    exit_block = next(b for b in result.blocks if "poll_exit" in b.label)
    exit_lbl = exit_block.label

    # body block must jump to exit
    body_block = next(b for b in result.blocks if "poll_body" in b.label)
    assert isinstance(body_block.terminator, lir.Jump)
    assert body_block.terminator.label == exit_lbl

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HContinue inside HPoll → Jump(poll_check)
# ---------------------------------------------------------------------------

def test_continue_in_poll():
    """HContinue inside HPoll produces Jump to poll_check (re-evaluates cond)."""
    a = u8("a")
    func = HFunction(
        name="poll_continue",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HPoll(
                cond=HCmp("ne", a, hconst(0)),
                body=(HContinue(),),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = lower_function(func)

    # Find poll_check block
    check_block = next(b for b in result.blocks if "poll_check" in b.label)
    check_lbl = check_block.label

    # body block must jump to poll_check
    body_block = next(b for b in result.blocks if "poll_body" in b.label)
    assert isinstance(body_block.terminator, lir.Jump)
    assert body_block.terminator.label == check_lbl

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HWhile with HLogical condition → intermediate logical_mid block
# ---------------------------------------------------------------------------

def test_while_with_hlogical_block_structure():
    """HWhile(cond=HLogical) produces logical_mid intermediate block."""
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
    result = lower_function(func)
    labels = _labels(result)

    # Must have a logical_mid block from HLogical short-circuit
    assert any("logical_mid" in lbl for lbl in labels)
    # while_test must be present
    assert any("while_test" in lbl for lbl in labels)
    assert any("while_body" in lbl for lbl in labels)
    assert any("while_exit" in lbl for lbl in labels)

    # Must pass LIR validation
    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: End-to-end pipeline with HWhile + HLogical
# ---------------------------------------------------------------------------

def test_while_hlogical_end_to_end():
    """HWhile with HLogical runs through the full run_codegen_from_hir()."""
    a = u8("a")
    b = u8("b")
    cond = HLogical("and", HCmp("ne", a, hconst(0)), HCmp("lt", a, b))
    func = HFunction(
        name="while_logical_e2e",
        params=(HParam("a", UInt(8)), HParam("b", UInt(8))),
        return_ty=UInt(8),
        body=(
            HWhile(cond=cond, body=(HAssign(target=a, value=hconst(1)),)),
            HReturn(values=(a,)),
        ),
    )
    result = run_codegen_from_hir(func, ToyTarget())

    assert result.input_hir is func
    asm = result.asm_text
    # while_test and while_body must appear in pseudo ASM
    assert "while_test" in asm
    assert "while_body" in asm
    # Branch instruction must be present
    assert "CMP" in asm or "BR" in asm


# ---------------------------------------------------------------------------
# Complete pipeline with HPoll → pseudo ASM
# ---------------------------------------------------------------------------

def test_poll_end_to_end():
    """HPoll runs through run_codegen_from_hir"""
    a = u8("a")
    func = HFunction(
        name="poll_e2e",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HPoll(
                cond=HCmp("ne", a, hconst(0)),
                body=(HAssign(target=a, value=hconst(1)),),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = run_codegen_from_hir(func, ToyTarget())
    asm = result.asm_text
    assert "poll_body" in asm
    assert "poll_check" in asm
    assert "poll_exit" in asm


# ---------------------------------------------------------------------------
# Test: HLoad → MemLoad node assigned to temp var
# ---------------------------------------------------------------------------

def test_hload_produces_memload():
    """HLoad lowers to lir.MemLoad, assigned to a fresh __load_N variable."""
    ptr = u8("ptr")
    result_var = u8("result")
    func = HFunction(
        name="load_test",
        params=(HParam("ptr", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HAssign(target=result_var, value=HLoad(ptr_expr=ptr, ty=UInt(8))),
            HReturn(values=(result_var,)),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    # First statement should be an Assign whose target is __load_N
    # and whose value is a MemLoad
    assert len(entry.statements) >= 1
    load_stmt = entry.statements[0]
    assert isinstance(load_stmt, lir.Assign)
    assert load_stmt.target.name.startswith("__load_")
    assert isinstance(load_stmt.value, lir.MemLoad)
    assert load_stmt.value.volatile is True
    assert load_stmt.value.width == 8

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HStore → MemStore statement
# ---------------------------------------------------------------------------

def test_hstore_produces_memstore():
    """HStore lowers to a lir.MemStore statement in the block."""
    ptr = u8("ptr")
    val = u8("val")
    func = HFunction(
        name="store_test",
        params=(HParam("ptr", UInt(8)), HParam("val", UInt(8))),
        return_ty=Void(),
        body=(
            HStore(ptr_expr=ptr, value_expr=val),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    mem_stores = [s for s in entry.statements if isinstance(s, lir.MemStore)]
    assert len(mem_stores) == 1
    ms = mem_stores[0]
    assert isinstance(ms.addr, lir.Var)
    assert ms.addr.name == "ptr"
    assert isinstance(ms.value, lir.Var)
    assert ms.value.name == "val"
    assert ms.volatile is True

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HBitSet(value=1) → BitOp("set", var, idx)
# ---------------------------------------------------------------------------

def test_bitset_produces_bitop_set():
    """HBitSet(v, 3, 1) lowers to BitOp('set', v, 3) statement."""
    flags = u8("flags")
    func = HFunction(
        name="bitset_test",
        params=(HParam("flags", UInt(8)),),
        return_ty=Void(),
        body=(
            HBitSet(var=flags, bit_idx=3, value=1),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    bit_ops = [s for s in entry.statements if isinstance(s, lir.BitOp)]
    assert len(bit_ops) == 1
    bo = bit_ops[0]
    assert bo.kind == "set"
    assert bo.bit_idx == 3

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HBitSet(value=0) → BitOp("clr", var, idx)
# ---------------------------------------------------------------------------

def test_bitclr_produces_bitop_clr():
    """HBitSet(v, 3, 0) lowers to BitOp('clr', v, 3) statement."""
    flags = u8("flags")
    func = HFunction(
        name="bitclr_test",
        params=(HParam("flags", UInt(8)),),
        return_ty=Void(),
        body=(
            HBitSet(var=flags, bit_idx=3, value=0),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    bit_ops = [s for s in entry.statements if isinstance(s, lir.BitOp)]
    assert len(bit_ops) == 1
    bo = bit_ops[0]
    assert bo.kind == "clr"
    assert bo.bit_idx == 3

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HIf(HBitTest(v, 5)) → BrIf(BitOp("test", v, 5))
# ---------------------------------------------------------------------------

def test_bittest_in_if_condition():
    """HBitTest lowers to BrIf with BitOp('test')."""
    flags = u8("flags")
    result_var = u8("result")
    func = HFunction(
        name="bittest_test",
        params=(HParam("flags", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=HBitTest(var=flags, bit_idx=5),
                then_body=(HReturn(values=(hconst(1),)),),
                else_body=(HReturn(values=(hconst(0),)),),
            ),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    assert isinstance(entry.terminator, lir.BrIf)
    cond_lir = entry.terminator.cond
    assert isinstance(cond_lir, lir.BitOp)
    assert cond_lir.kind == "test"
    assert cond_lir.bit_idx == 5

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HLoad in expression (result = HLoad(ptr) + 1)
# ---------------------------------------------------------------------------

def test_hload_in_expression():
    """HLoad inside a larger expression: load temp precedes usage."""
    ptr = u8("ptr")
    result_var = u8("result")
    func = HFunction(
        name="load_expr",
        params=(HParam("ptr", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HAssign(
                target=result_var,
                value=HBinOp("add", HLoad(ptr_expr=ptr, ty=UInt(8)), hconst(1), UInt(8)),
            ),
            HReturn(values=(result_var,)),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    # First stmt: load temp = MemLoad(ptr)
    assert len(entry.statements) >= 2
    s0 = entry.statements[0]
    assert isinstance(s0, lir.Assign)
    assert s0.target.name.startswith("__load_")
    assert isinstance(s0.value, lir.MemLoad)

    # Second stmt: result = add(__load_N, 1)
    s1 = entry.statements[1]
    assert isinstance(s1, lir.Assign)
    assert s1.target.name == "result"
    assert isinstance(s1.value, lir.BinOp)
    assert s1.value.op == "add"
    assert isinstance(s1.value.left, lir.Var)
    assert s1.value.left.name.startswith("__load_")
    assert isinstance(s1.value.right, lir.Const)
    assert s1.value.right.value == 1

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: two HLoads preserve left-to-right order
# ---------------------------------------------------------------------------

def test_two_hloads_in_expression():
    """Two HLoads in one expression: distinct temps, left-to-right order."""
    ptr1 = u8("ptr1")
    ptr2 = u8("ptr2")
    result_var = u8("result")
    func = HFunction(
        name="two_loads",
        params=(HParam("ptr1", UInt(8)), HParam("ptr2", UInt(8))),
        return_ty=UInt(8),
        body=(
            HAssign(
                target=result_var,
                value=HBinOp(
                    "add",
                    HLoad(ptr_expr=ptr1, ty=UInt(8)),
                    HLoad(ptr_expr=ptr2, ty=UInt(8)),
                    UInt(8),
                ),
            ),
            HReturn(values=(result_var,)),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    # Three statements: __load_N, __load_M, result = add(...)
    assert len(entry.statements) == 3
    s0 = entry.statements[0]
    s1 = entry.statements[1]
    s2 = entry.statements[2]

    assert isinstance(s0, lir.Assign) and isinstance(s0.value, lir.MemLoad)
    assert isinstance(s1, lir.Assign) and isinstance(s1.value, lir.MemLoad)
    assert s0.target.name != s1.target.name  # distinct temps

    assert isinstance(s2, lir.Assign)
    assert s2.target.name == "result"
    assert isinstance(s2.value, lir.BinOp)
    assert s2.value.op == "add"
    assert isinstance(s2.value.left, lir.Var) and s2.value.left.name == s0.target.name
    assert isinstance(s2.value.right, lir.Var) and s2.value.right.name == s1.target.name

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HStore after HLoad preserves order
# ---------------------------------------------------------------------------

def test_hstore_after_hload():
    """HLoad then HStore preserves statement order."""
    ptr = u8("ptr")
    ptr2 = u8("ptr2")
    val = u8("val")
    func = HFunction(
        name="load_then_store",
        params=(HParam("ptr", UInt(8)), HParam("ptr2", UInt(8))),
        return_ty=UInt(8),
        body=(
            HAssign(target=val, value=HLoad(ptr_expr=ptr, ty=UInt(8))),
            HStore(ptr_expr=ptr2, value_expr=val),
            HReturn(values=(val,)),
        ),
    )
    result = lower_function(func)
    entry = _find_block(result, "entry")

    # Statements: __load_N = MemLoad(ptr), val = __load_N, MemStore(ptr2, val)
    assert len(entry.statements) >= 3
    s0 = entry.statements[0]
    s1 = entry.statements[1]
    s2 = entry.statements[2]

    assert isinstance(s0, lir.Assign)
    assert isinstance(s0.value, lir.MemLoad)  # load happens first

    assert isinstance(s1, lir.Assign)
    assert s1.target.name == "val"  # val gets the loaded value

    assert isinstance(s2, lir.MemStore)  # store happens after

    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: HStore produces MOV [addr], val in ToyTarget
# ---------------------------------------------------------------------------

def test_hstore_toy_asm():
    """HStore produces MOV [addr], val in ToyTarget pseudo ASM."""
    ptr = u8("ptr")
    val = u8("val")
    func = HFunction(
        name="store_asm",
        params=(HParam("ptr", UInt(8)), HParam("val", UInt(8))),
        return_ty=Void(),
        body=(
            HStore(ptr_expr=ptr, value_expr=val),
        ),
    )
    result = run_codegen_from_hir(func, ToyTarget())
    asm = result.asm_text
    assert "MOV" in asm
    assert "[ptr]" in asm


# ---------------------------------------------------------------------------
# Test: HLoad and HStore inside HIf
# ---------------------------------------------------------------------------

def test_hload_hstore_in_if():
    """HLoad and HStore inside HIf block structure."""
    ptr = u8("ptr")
    val = u8("val")
    result_var = u8("result")
    func = HFunction(
        name="load_store_if",
        params=(HParam("ptr", UInt(8)), HParam("val", UInt(8))),
        return_ty=UInt(8),
        body=(
            HIf(
                cond=HCmp("ne", val, hconst(0)),
                then_body=(
                    HStore(ptr_expr=ptr, value_expr=val),
                ),
                else_body=(
                    HAssign(target=result_var, value=HLoad(ptr_expr=ptr, ty=UInt(8))),
                    HReturn(values=(result_var,)),
                ),
            ),
            HReturn(values=(hconst(0),)),
        ),
    )
    result = lower_function(func)
    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Supported constructs pass lir.validate_function
# ---------------------------------------------------------------------------

def test_supported_output_passes_lir_validation():
    """Supported lowering output passes lir.validate_function()."""
    # Exercise the supported control-flow and memory features.
    a = u8("a")
    ptr = u8("ptr")
    flags = u8("flags")

    func = HFunction(
        name="supported_validation_test",
        params=(
            HParam("a", UInt(8)),
            HParam("ptr", UInt(8)),
            HParam("flags", UInt(8)),
        ),
        return_ty=UInt(8),
        body=(
            # HStore
            HStore(ptr_expr=ptr, value_expr=a),
            # HBitSet (set)
            HBitSet(var=flags, bit_idx=2, value=1),
            # HBitSet (clr)
            HBitSet(var=flags, bit_idx=7, value=0),
            # HWhile with HLoad and HBreak
            HWhile(
                cond=HCmp("ne", a, hconst(0)),
                body=(
                    HAssign(
                        target=a,
                        value=HLoad(ptr_expr=ptr, ty=UInt(8)),
                    ),
                    HBreak(),
                ),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = lower_function(func)
    # Must not raise
    lir.validate_function(result)


# ---------------------------------------------------------------------------
# Test: end-to-end pipeline with HWhile + HLoad
# ---------------------------------------------------------------------------

def test_m2_end_to_end_pipeline():
    """HWhile + HLoad runs all the way through run_codegen_from_hir."""
    a = u8("a")
    ptr = u8("ptr")

    func = HFunction(
        name="m2_e2e",
        params=(HParam("a", UInt(8)), HParam("ptr", UInt(8))),
        return_ty=UInt(8),
        body=(
            HWhile(
                cond=HCmp("ne", a, hconst(0)),
                body=(
                    HAssign(
                        target=a,
                        value=HLoad(ptr_expr=ptr, ty=UInt(8)),
                    ),
                ),
            ),
            HReturn(values=(a,)),
        ),
    )
    result = run_codegen_from_hir(func, ToyTarget())

    assert result.input_hir is func
    asm = result.asm_text

    # while_test and while_body labels must appear in pseudo ASM
    assert "while_test" in asm
    assert "while_body" in asm
    # Memory load instruction
    assert "MOV" in asm
    # The branch instruction
    assert "CMP" in asm


# ---------------------------------------------------------------------------
# Test: HBreak / HContinue outside loop raises HIRValidationError
# ---------------------------------------------------------------------------

def test_break_outside_loop_raises_validation_error():
    """HBreak at function top level must raise HIRValidationError."""
    func = simple_function(
        "bad_break",
        params=[HParam("a", UInt(8))],
        body=[HBreak()],
        return_ty=Void(),
    )
    with pytest.raises(HIRValidationError, match="outside"):
        validate_hfunction(func)


def test_continue_outside_loop_raises_validation_error():
    """HContinue at function top level must raise HIRValidationError."""
    func = simple_function(
        "bad_continue",
        params=[HParam("a", UInt(8))],
        body=[HContinue()],
        return_ty=Void(),
    )
    with pytest.raises(HIRValidationError, match="outside"):
        validate_hfunction(func)


# ---------------------------------------------------------------------------
# Test: pattern rewrite safety — MemLoad not removed or reordered
# ---------------------------------------------------------------------------

def test_hload_rewrite_safety():
    """Pattern rewrite does not remove or modify MemLoad nodes."""
    ptr = u8("ptr")
    result_var = u8("result")
    func = HFunction(
        name="load_rewrite",
        params=(HParam("ptr", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HAssign(target=result_var, value=HLoad(ptr_expr=ptr, ty=UInt(8))),
            HReturn(values=(result_var,)),
        ),
    )
    # Universal-capture pattern that would match any expression
    patterns = load_patterns_from_dicts([
        {
            "name": "everything_to_zero",
            "match": {"capture": "x"},
            "replace": {"const": 0},
            "cost_delta": -999,
        },
    ])
    result = run_codegen_from_hir(func, ToyTarget(), patterns=patterns)
    asm = result.asm_text
    # MemLoad must survive rewrite: we should still see a memory reference
    assert "[ptr]" in asm, "MemLoad was removed by pattern rewrite"
    # The Var reference after the load may be rewritten to Const(0), but the
    # MemLoad itself must not be eliminated — verify the load instruction exists
    assert "MOV __load" in asm or "MOV _" in asm


def test_hstore_rewrite_safety():
    """Pattern rewrite does not remove or reorder MemStore statements."""
    ptr = u8("ptr")
    val = u8("val")
    func = HFunction(
        name="store_rewrite",
        params=(HParam("ptr", UInt(8)), HParam("val", UInt(8))),
        return_ty=Void(),
        body=(
            HStore(ptr_expr=ptr, value_expr=val),
        ),
    )
    patterns = load_patterns_from_dicts([
        {
            "name": "everything_to_zero",
            "match": {"capture": "x"},
            "replace": {"const": 0},
            "cost_delta": -999,
        },
    ])
    result = run_codegen_from_hir(func, ToyTarget(), patterns=patterns)
    asm = result.asm_text
    # MemStore should survive as MOV [ptr], val
    assert "[ptr]" in asm


# ---------------------------------------------------------------------------
# Bug 1 regression: HCall expression uses unique temp vars
# ---------------------------------------------------------------------------

def test_hcall_unique_temp_vars():
    """HCall('f') + HCall('f') produces distinct LIR Var names."""
    f_result = u8("f_result")
    func = HFunction(
        name="double_call",
        params=(),
        return_ty=UInt(8),
        body=(
            HAssign(
                target=f_result,
                value=HBinOp(
                    "add",
                    HCall(name="f", args=(), return_ty=UInt(8)),
                    HCall(name="f", args=(), return_ty=UInt(8)),
                    UInt(8),
                ),
            ),
            HReturn(values=(f_result,)),
        ),
    )
    result = lower_function(func)
    entry = result.blocks[0]

    call_stmts = [
        s for s in entry.statements
        if isinstance(s, lir.Assign) and isinstance(s.value, lir.Call) and s.value.name == "f"
    ]
    assert len(call_stmts) == 2, f"Expected 2 call stmts, got {len(call_stmts)}"
    # Must have distinct target var names
    t0 = call_stmts[0].target.name
    t1 = call_stmts[1].target.name
    assert t0 != t1, f"Call temp vars must be unique: {t0} == {t1}"
    # Both must start with __ret_f_
    assert t0.startswith("__ret_f_")
    assert t1.startswith("__ret_f_")

    # The BinOp must reference the two distinct temps
    binop = next(
        s.value for s in entry.statements
        if isinstance(s, lir.Assign) and isinstance(s.value, lir.BinOp) and s.value.op == "add"
    )
    assert isinstance(binop, lir.BinOp)
    assert isinstance(binop.left, lir.Var) and binop.left.name == t0
    assert isinstance(binop.right, lir.Var) and binop.right.name == t1


# ---------------------------------------------------------------------------
# Bug 2 regression: HFor loop-var reads are accepted; writes are rejected
# ---------------------------------------------------------------------------

def test_hfor_loopvar_in_nested_while_cond():
    """A loop-var read in a nested while condition must be accepted."""
    i = HVar("i", UInt(8))
    a = u8("a")
    func = HFunction(
        name="bad_for_while",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HFor(
                var=i, init=hconst(0), bound=hconst(5),
                body=(
                    HWhile(
                        cond=HCmp("ne", i, hconst(0)),
                        body=(HAssign(target=a, value=hconst(0)),),
                    ),
                ),
            ),
            HReturn(values=(hconst(0),)),
        ),
    )
    lower_function(func)


def test_hfor_loopvar_in_nested_poll_cond():
    """A loop-var read in a nested poll condition must be accepted."""
    i = HVar("i", UInt(8))
    a = u8("a")
    func = HFunction(
        name="bad_for_poll",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HFor(
                var=i, init=hconst(0), bound=hconst(5),
                body=(
                    HPoll(
                        cond=HCmp("ne", i, hconst(0)),
                        body=(HAssign(target=a, value=hconst(0)),),
                    ),
                ),
            ),
            HReturn(values=(hconst(0),)),
        ),
    )
    lower_function(func)


def test_hfor_loopvar_in_if_elif_cond():
    """A loop-var read in an elif condition must be accepted."""
    i = HVar("i", UInt(8))
    a = u8("a")
    func = HFunction(
        name="bad_for_elif",
        params=(HParam("a", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HFor(
                var=i, init=hconst(0), bound=hconst(5),
                body=(
                    HIf(
                        cond=HCmp("eq", a, hconst(0)),
                        then_body=(HReturn(values=(hconst(1),)),),
                        elif_branches=(
                            (HCmp("ne", i, hconst(0)), (HReturn(values=(hconst(2),)),)),
                        ),
                        else_body=(HReturn(values=(hconst(0),)),),
                    ),
                ),
            ),
            HReturn(values=(hconst(0),)),
        ),
    )
    lower_function(func)


def test_hfor_loopvar_in_hstore():
    """A loop-var read in a store address must be accepted."""
    i = HVar("i", UInt(8))
    func = HFunction(
        name="bad_for_store",
        params=(),
        return_ty=Void(),
        body=(
            HFor(
                var=i, init=hconst(0), bound=hconst(5),
                body=(
                    HStore(ptr_expr=i, value_expr=hconst(0)),
                ),
            ),
        ),
    )
    lower_function(func)


def test_hfor_loopvar_in_hbitset():
    """A write to the loop variable via HBitSet must be rejected."""
    i = HVar("i", UInt(8))
    func = HFunction(
        name="bad_for_bitset",
        params=(),
        return_ty=Void(),
        body=(
            HFor(
                var=i, init=hconst(0), bound=hconst(5),
                body=(
                    HBitSet(var=i, bit_idx=3, value=1),
                ),
            ),
            HReturn(values=(hconst(0),)),
        ),
    )
    with pytest.raises(NotImplementedError, match="loop variable"):
        lower_function(func)


def test_hfor_loopvar_in_hload():
    """A loop-var read in a load address must be accepted."""
    i = HVar("i", UInt(8))
    result_var = u8("result")
    func = HFunction(
        name="bad_for_load",
        params=(),
        return_ty=UInt(8),
        body=(
            HFor(
                var=i, init=hconst(0), bound=hconst(5),
                body=(
                    HAssign(target=result_var, value=HLoad(ptr_expr=i, ty=UInt(8))),
                ),
            ),
            HReturn(values=(result_var,)),
        ),
    )
    lower_function(func)


def test_hfor_loopvar_in_hassign_target():
    """A write to the loop variable via HAssign target must be rejected."""
    i = HVar("i", UInt(8))
    func = HFunction(
        name="bad_for_assign_target",
        params=(),
        return_ty=UInt(8),
        body=(
            HFor(
                var=i, init=hconst(0), bound=hconst(5),
                body=(
                    HAssign(target=i, value=hconst(1)),
                ),
            ),
            HReturn(values=(hconst(0),)),
        ),
    )
    with pytest.raises(NotImplementedError, match="loop variable"):
        lower_function(func)
