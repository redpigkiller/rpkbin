from rpkbin.codegen.ir import Assign, Block, BrIf, Function, Return, binop, cmp, const, var
from rpkbin.codegen.patterns import load_patterns_from_dicts
from rpkbin.codegen.pipeline import run_codegen
from rpkbin.codegen.toy_target import ToyTarget


def test_manual_ir_to_pseudo_asm_with_pattern_rewrite():
    a = var("a")
    b = var("b")
    v0 = var("v0")

    func = Function(
        name="main",
        params=(a, b),
        blocks=(
            Block(
                label="entry",
                statements=(Assign(v0, binop("mul", a, const(2))),),
                terminator=BrIf(cmp("ge", v0, b), "then", "else"),
            ),
            Block("then", (), Return(v0)),
            Block("else", (), Return(b)),
        ),
    )

    patterns = load_patterns_from_dicts(
        [
            {
                "name": "mul_by_2_to_shift",
                "match": {
                    "op": "mul",
                    "args": [{"capture": "x"}, {"const": 2}],
                },
                "replace": {
                    "op": "shl",
                    "args": [{"ref": "x"}, {"const": 1}],
                },
                "cost_delta": -1,
            }
        ]
    )

    result = run_codegen(func, ToyTarget(), patterns)

    assert result.applied_patterns == ("mul_by_2_to_shift",)
    assert result.asm_text == "\n".join(
        [
            "entry:",
            "MOV v0, a",
            "SHL v0, #1",
            "CMP v0, b",
            "BGE then",
            "JMP else",
            "then:",
            "RET v0",
            "else:",
            "RET b",
        ]
    )


def test_manual_ir_to_pseudo_asm_without_patterns():
    a = var("a")
    b = var("b")
    v0 = var("v0")

    func = Function(
        name="add_one",
        params=(a,),
        blocks=(
            Block(
                label="entry",
                statements=(Assign(v0, binop("add", a, const(1))),),
                terminator=Return(v0),
            ),
        ),
    )

    result = run_codegen(func, ToyTarget())

    assert result.applied_patterns == ()
    assert result.asm_text == "\n".join(
        [
            "entry:",
            "MOV v0, a",
            "ADD v0, #1",
            "RET v0",
        ]
    )


# ---------------------------------------------------------------------------
# New HIR end-to-end tests (Wave 4)
# ---------------------------------------------------------------------------

from rpkbin.codegen import (  # noqa: E402 — import after existing imports
    run_codegen_from_hir,
    HFunction, HParam, HReturn, HAssign,
    HBinOp, HCast, HCmp, HVar, HConst, HFor,
    HIf,
    SInt, UInt, Void,
    hconst, s8, s16, u8, u16,
)


def test_hir_to_pseudo_asm_if_else():
    """HIR if-else compiles end-to-end to correct pseudo-ASM block structure."""
    a = u8("a")
    cond = HCmp("lt", a, hconst(10))
    func = HFunction(
        name="if_else_fn",
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

    result = run_codegen_from_hir(func, ToyTarget())

    # Verify pipeline fields
    assert result.input_hir is func
    assert result.input_lir is not None
    assert result.applied_patterns == ()

    asm = result.asm_text
    # Must contain a branch and both labels
    assert "CMP" in asm
    assert "if_then" in asm
    assert "if_else" in asm
    assert "RET" in asm
    # backward-compat alias
    assert result.input_ir is result.input_lir


def test_hir_to_pseudo_asm_for_loop():
    """HIR fixed-count for-loop compiles to a BrCmp counter loop."""
    x = u8("x")
    func = HFunction(
        name="for_fn",
        params=(HParam("x", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HFor(
                var=u8("i"),
                init=hconst(0),
                bound=hconst(3),
                body=(HAssign(target=x, value=HBinOp("add", x, hconst(1), UInt(8))),),
            ),
            HReturn(values=(x,)),
        ),
    )

    result = run_codegen_from_hir(func, ToyTarget())

    asm = result.asm_text
    # Must contain a compare-branch for counter and a jump back
    assert "CMP" in asm         # BrCmp → CMP in toy target
    assert "BNE" in asm         # ne branch
    assert "SUB" in asm         # counter decrement
    assert result.input_hir is func


def test_hir_to_pseudo_asm_with_pattern():
    """HIR mul(x, 2) is rewritten to shl(x, 1) via a pattern."""
    x = u8("x")
    mul_expr = HBinOp("mul", x, hconst(2), UInt(8))
    result_var = u8("result")

    func = HFunction(
        name="mul_fn",
        params=(HParam("x", UInt(8)),),
        return_ty=UInt(8),
        body=(
            HAssign(target=result_var, value=mul_expr),
            HReturn(values=(result_var,)),
        ),
    )

    patterns = load_patterns_from_dicts(
        [
            {
                "name": "mul_by_2_to_shift",
                "match": {
                    "op": "mul",
                    "args": [{"capture": "x"}, {"const": 2}],
                },
                "replace": {
                    "op": "shl",
                    "args": [{"ref": "x"}, {"const": 1}],
                },
                "cost_delta": -1,
            }
        ]
    )

    result = run_codegen_from_hir(func, ToyTarget(), patterns=patterns)

    assert result.applied_patterns == ("mul_by_2_to_shift",)
    assert "SHL" in result.asm_text
    assert "MUL" not in result.asm_text


def test_hir_to_pseudo_asm_zero_extend_u8_ff_to_u16():
    src = u8("src")
    result_var = u16("result")
    func = HFunction(
        name="zext_fn",
        params=(),
        return_ty=UInt(16),
        body=(
            HAssign(target=src, value=hconst(0xFF)),
            HAssign(target=result_var, value=HCast("u16_from", src, UInt(16))),
            HReturn(values=(result_var,)),
        ),
    )

    result = run_codegen_from_hir(func, ToyTarget())

    assert "MOV src, #255" in result.asm_text
    assert "ZEXT result, src" in result.asm_text
    assert "SEXT" not in result.asm_text


def test_hir_to_pseudo_asm_sign_extend_negative_s8_to_s16():
    src = s8("src")
    result_var = s16("result")
    func = HFunction(
        name="sext_fn",
        params=(),
        return_ty=SInt(16),
        body=(
            HAssign(target=src, value=hconst(-1, width=8, signed=True)),
            HAssign(target=result_var, value=HCast("s16_from", src, SInt(16))),
            HReturn(values=(result_var,)),
        ),
    )

    result = run_codegen_from_hir(func, ToyTarget())

    assert "MOV src, #-1" in result.asm_text
    assert "SEXT result, src" in result.asm_text
    assert "ZEXT" not in result.asm_text
