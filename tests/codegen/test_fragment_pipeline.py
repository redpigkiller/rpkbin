"""HFragment → rewrite → ToyTarget pseudo-ASM pipeline tests."""

import pytest

from rpkbin.codegen.hir import (
    HAssign, HBinOp, HBitSet, HCall, HCallAssign, HCmp, HConst, HExit,
    HFragment, HFragmentBinding, HIf, HInlineAsm, HReturn,
    HSymbolAddr,
    HVar,
    SInt, UInt, Void,
    hconst, u8,
)
from rpkbin.codegen.hir_validate import (
    HIRValidationError,
    validate_hfragment,
)
from rpkbin.codegen.patterns import load_patterns_from_dicts
from rpkbin.codegen.pipeline import (
    FragmentCodegenResult,
    run_codegen,
    run_codegen_from_fragment,
    run_codegen_from_hir,
)
from rpkbin.codegen.toy_target import ToyTarget
from rpkbin.codegen import lir


_LOC = None


def _mkbinding(
    name: str,
    ty=UInt(8),
    reg: str = "r0",
    mode: str = "in",
) -> HFragmentBinding:
    return HFragmentBinding(name=name, ty=ty, reg=reg, mode=mode)


# ======================================================================
# 1.  Straight-line HFragment → pseudo-ASM
# ======================================================================

def test_straight_line_fragment_pseudo_asm():
    """A simple fragment with assign + exit produces correct pseudo-ASM."""
    outp = _mkbinding("y", mode="out", reg="r5")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=hconst(42)),
        HExit(),
    )
    frag = HFragment(name="straight", bindings=(outp,), body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    assert isinstance(result, FragmentCodegenResult)
    asm_text = result.asm.format()
    assert "straight:" not in asm_text  # fragment name is AsmFunction.name, not a label
    assert "entry:" in asm_text
    assert "MOV" in asm_text
    assert "RET" not in asm_text


# ======================================================================
# 2.  Output binding destination uses declared physical register
# ======================================================================

def test_output_binding_uses_physical_reg():
    """Output binding assign target emits physical register name, not logical."""
    outp = _mkbinding("y", mode="out", reg="r5")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=hconst(42)),
        HExit(),
    )
    frag = HFragment(name="out_binding", bindings=(outp,), body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    asm_text = result.asm.format()
    # Must use r5 (the physical reg), not %y or y
    assert "MOV r5, #42" in asm_text, f"got {asm_text!r}"
    assert "%y" not in asm_text
    assert " y," not in asm_text.replace("entry:", "")


# ======================================================================
# 3.  Input binding operand uses declared physical register
# ======================================================================

def test_input_binding_uses_physical_reg():
    """Input binding var in expression uses physical register name."""
    inp = _mkbinding("x", mode="in", reg="r4")
    body = (
        HAssign(target=HVar("tmp", UInt(8)), value=HVar("x", UInt(8))),
        HExit(),
    )
    frag = HFragment(name="in_binding", bindings=(inp,), scratch_regs=("r7",), body=body)
    result = run_codegen_from_fragment(
        frag, ToyTarget(), register_model=_DummyRegModel()
    )
    asm_text = result.asm.format()
    # x is an input binding with reg=r4, and tmp must be allocated from scratch.
    assert "MOV r7, r4" in asm_text, f"got {asm_text!r}"
    assert "%x" not in asm_text
    assert "tmp" not in asm_text


# ======================================================================
# 4.  Inout binding reads and writes the same physical reg
# ======================================================================

def test_inout_binding_read_write():
    """Inout binding reads and writes the same physical register."""
    inout = _mkbinding("x", mode="inout", reg="r6")
    body = (
        HAssign(target=HVar("x", UInt(8)), value=HBinOp("add", HVar("x", UInt(8)), hconst(1), UInt(8))),
        HExit(),
    )
    frag = HFragment(name="inout", bindings=(inout,), body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    asm_text = result.asm.format()
    # Both read and write use r6:  MOV r6, r6  (or just ADD r6, #1 if MOV is elided)
    # The ADD uses r6 as both source and dest
    assert "ADD r6, #1" in asm_text, f"got {asm_text!r}"


# ======================================================================
# 5.  Local variable requires register_model for scratch allocation
# ======================================================================

def test_local_variable_requires_register_model():
    """Fragments with locals must not rely on selector symbolic fallback."""
    body = (
        HAssign(target=HVar("tmp", UInt(8)), value=hconst(99)),
        HExit(),
    )
    frag = HFragment(name="local", body=body)
    with pytest.raises(ValueError, match="require register_model"):
        run_codegen_from_fragment(frag, ToyTarget())


# ======================================================================
# 6.  FragmentExit does NOT output RET
# ======================================================================

def test_no_ret_for_fragment_exit():
    """FragmentExit must not produce any RET instruction."""
    body = (HExit(),)
    frag = HFragment(name="noop", body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    asm_text = result.asm.format()
    assert "RET" not in asm_text


# ======================================================================
# 7.  FragmentExit does NOT output any extra pseudo-opcode
# ======================================================================

def test_no_extra_pseudo_opcode_for_fragment_exit():
    """FragmentExit must produce zero instructions — no opcode at all."""
    body = (HExit(),)
    frag = HFragment(name="pure_exit", body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    asm_text = result.asm.format()
    # Only output should be the entry label
    assert asm_text == "entry:", (
        f"expected only label, got:\n{asm_text}"
    )


# ======================================================================
# 8.  If/else branch labels and jumps
# ======================================================================

def test_if_else_branch_labels():
    """If/else in fragment produces correct labels, CMP, branch, and JMP."""
    inp = _mkbinding("x", mode="in", reg="r4")
    cond = HCmp("eq", HVar("x", UInt(8)), hconst(0))
    body = (
        HIf(
            cond=cond,
            then_body=(HExit(),),
            else_body=(HExit(),),
        ),
    )
    frag = HFragment(name="if_else", bindings=(inp,), body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    asm_text = result.asm.format()
    # Check structure
    assert "entry:" in asm_text
    assert "if_then:" in asm_text
    assert "if_else:" in asm_text
    assert "CMP r4, #0" in asm_text, f"got {asm_text!r}"
    assert "BEQ" in asm_text or "BNE" in asm_text
    assert "JMP" in asm_text


# ======================================================================
# 9.  HInlineAsm + HExit outputs raw text with no RET
# ======================================================================

def test_inline_asm_then_exit_raw_output():
    """HInlineAsm('ljmp TARGET') + HExit emits only 'ljmp TARGET'."""
    body = (
        HInlineAsm(text="ljmp TARGET"),
        HExit(),
    )
    frag = HFragment(name="inline_exit", body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    asm_text = result.asm.format()
    assert "ljmp TARGET" in asm_text
    assert "RET" not in asm_text
    # Only the asm text and the entry label should be present
    assert asm_text.strip() == "entry:\nljmp TARGET", (
        f"got:\n{asm_text}"
    )


# ======================================================================
# 10.  HCallAssign binding targets use physical regs
# ======================================================================

def test_call_assign_binding_target_physical_reg():
    """HCallAssign target that is a binding uses physical register name."""
    out = _mkbinding("y", mode="out", reg="r5")
    call = HCall(name="get_val", args=(hconst(1),), return_ty=Void())
    body = (
        HCallAssign(targets=(HVar("y", UInt(8)),), call=call),
        HExit(),
    )
    frag = HFragment(name="call_assign", bindings=(out,), body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    asm_text = result.asm.format()
    # CALL destination should be r5, not %y or y
    assert "CALL (r5)" in asm_text, f"got {asm_text!r}"
    assert "%y" not in asm_text


# ======================================================================
# 11.  HBitSet binding uses physical reg
# ======================================================================

def test_bitset_binding_physical_reg():
    """HBitSet on a binding uses the physical register name."""
    out = _mkbinding("y", mode="out", reg="r5")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=hconst(0)),
        HBitSet(var=HVar("y", UInt(8)), bit_idx=3, value=1),
        HExit(),
    )
    frag = HFragment(name="bitset", bindings=(out,), body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    asm_text = result.asm.format()
    assert "MOV r5, #0" in asm_text, f"got {asm_text!r}"
    assert "BSET r5, #3" in asm_text, f"got {asm_text!r}"


# ======================================================================
# 12.  Rewrite pattern applies to fragment Assign expression
# ======================================================================

def test_rewrite_pattern_applies_to_fragment():
    """Pattern rewrite can simplify expressions inside fragments."""
    inp = _mkbinding("x", mode="in", reg="r4")
    out = _mkbinding("y", mode="out", reg="r5")
    body = (
        HAssign(
            target=HVar("y", UInt(8)),
            value=HBinOp("mul", HVar("x", UInt(8)), hconst(2), UInt(8)),
        ),
        HExit(),
    )
    frag = HFragment(name="rewrite_test", bindings=(inp, out), body=body)
    patterns = load_patterns_from_dicts([
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
        },
    ])
    result = run_codegen_from_fragment(frag, ToyTarget(), patterns=patterns)
    asm_text = result.asm.format()
    assert result.applied_patterns == ("mul_by_2_to_shift",)
    assert "SHL r5, #1" in asm_text, f"got {asm_text!r}"
    assert "MUL" not in asm_text


# ======================================================================
# 13.  Rewrite preserves bindings and scratch metadata
# ======================================================================

def test_rewrite_preserves_bindings_and_scratch():
    """rewrite_fragment must keep name, bindings, scratch_regs unchanged."""
    inp = _mkbinding("x", mode="in", reg="r4")
    out = _mkbinding("y", mode="out", reg="r5")
    body = (
        HAssign(
            target=HVar("y", UInt(8)),
            value=HBinOp("mul", HVar("x", UInt(8)), hconst(2), UInt(8)),
        ),
        HExit(),
    )
    frag = HFragment(
        name="meta_test",
        bindings=(inp, out),
        scratch_regs=("r6", "r7"),
        body=body,
    )
    patterns = load_patterns_from_dicts([
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
        },
    ])
    result = run_codegen_from_fragment(frag, ToyTarget(), patterns=patterns)
    rew = result.rewritten_lir
    assert rew.name == "meta_test"
    assert len(rew.bindings) == 2
    assert rew.bindings[0].name == "x"
    assert rew.bindings[0].reg == "r4"
    assert rew.bindings[1].name == "y"
    assert rew.bindings[1].reg == "r5"
    assert rew.scratch_regs == ("r6", "r7")


# ======================================================================
# 14.  Opaque InlineAsm/SymbolAddr not rewritten
# ======================================================================

def test_opaque_nodes_not_rewritten():
    """InlineAsmExpr and SymbolAddr must survive rewrite unchanged."""
    # Use a local variable (not a binding) for the SymbolAddr assignment
    body = (
        HAssign(target=HVar("tmp", UInt(8)), value=HSymbolAddr(name="_foo", ty=UInt(8))),
        HExit(),
    )
    frag = HFragment(name="opaque", scratch_regs=("r7",), body=body)
    result = run_codegen_from_fragment(
        frag, ToyTarget(), register_model=_DummyRegModel()
    )
    asm_text = result.asm.format()
    assert "&_foo" in asm_text, f"got {asm_text!r}"
    assert "tmp" not in asm_text


# ======================================================================
# 15.  FragmentCodegenResult field correctness
# ======================================================================

def test_fragment_codegen_result_fields():
    """FragmentCodegenResult contains correct input_hir, input_lir,
    rewritten_lir, asm, and applied_patterns."""
    body = (HExit(),)
    frag = HFragment(name="field_test", body=body)
    result = run_codegen_from_fragment(frag, ToyTarget())
    assert result.input_hir is frag
    assert isinstance(result.input_lir, lir.Fragment)
    assert isinstance(result.rewritten_lir, lir.Fragment)
    assert result.asm is not None
    assert result.applied_patterns == ()
    # Verify the pipeline stages produced distinct objects
    assert result.input_lir is not result.rewritten_lir


# ======================================================================
# 16.  register_model validates and enables scratch-only fragment allocation
# ======================================================================

class _DummyRegModel:
    """A minimal RegisterModel for fragment allocation tests."""
    def allocatable_registers(self):
        return ["r4", "r5", "r6", "r7"]
    def register_width(self, reg):
        return 8
    def register_aliases(self):
        return [("dp", ["dp_high", "dp_low"])]
    def spill_slots(self):
        return []
    def emit_spill(self, reg, slot):
        return [f"PUSH {reg}"]
    def emit_reload(self, slot, reg):
        return [f"POP {reg}"]


def test_register_model_validation_only():
    """Interface bindings remain fixed while fragment allocation does not spill."""
    inp = _mkbinding("x", mode="in", reg="r4")
    out = _mkbinding("y", mode="out", reg="r5")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=HVar("x", UInt(8))),
        HExit(),
    )
    frag = HFragment(
        name="reg_model_test",
        bindings=(inp, out),
        scratch_regs=("r7",),
        body=body,
    )
    result = run_codegen_from_fragment(frag, ToyTarget(), register_model=_DummyRegModel())
    asm_text = result.asm.format()
    # No PUSH or POP — only the MOV from the fragment body
    assert "MOV r5, r4" in asm_text, f"got {asm_text!r}"
    assert "PUSH" not in asm_text
    assert "POP" not in asm_text


def test_fragment_local_uses_declared_scratch_register():
    frag = HFragment(
        name="local",
        bindings=(
            _mkbinding("x", mode="in", reg="r4"),
            _mkbinding("y", mode="out", reg="r5"),
        ),
        scratch_regs=("r7",),
        body=(
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HAssign(HVar("y", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    result = run_codegen_from_fragment(
        frag, ToyTarget(), register_model=_DummyRegModel()
    )

    assert result.register_assignment["tmp"] == "r7"
    assert "tmp" not in result.asm.format()


class _PartialOverlapRegModel(_DummyRegModel):
    def allocatable_registers(self):
        return ["low", "high", "wide"]

    def register_width(self, reg):
        return {"low": 8, "high": 8, "wide": 16}[reg]

    def registers_overlap(self, lhs, rhs):
        return lhs == rhs or frozenset((lhs, rhs)) in {
            frozenset(("wide", "low")),
            frozenset(("wide", "high")),
        }


def test_partial_subregisters_do_not_overlap_each_other():
    model = _PartialOverlapRegModel()
    validate_hfragment(HFragment(
        "ok",
        bindings=(
            _mkbinding("lo", reg="low"),
            _mkbinding("hi", reg="high"),
        ),
        body=(HExit(),),
    ), model)

    with pytest.raises(HIRValidationError, match="overlaps"):
        validate_hfragment(HFragment(
            "bad",
            bindings=(
                _mkbinding("word", ty=UInt(16), reg="wide"),
                _mkbinding("lo", reg="low"),
            ),
            body=(HExit(),),
        ), model)


def test_register_model_rejects_bad_hint():
    """validate_hfragment rejects binding registers not in the physical register set.

    A binding reg must be a legal physical storage location
    (``is_physical_register()`` returns True).  'nonexistent_reg' is not
    in _DummyRegModel's allocatable pool nor in any alias group, so it is
    not a physical register and must be rejected.
    """
    inp = _mkbinding("x", mode="in", reg="nonexistent_reg")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=HVar("x", UInt(8))),
        HExit(),
    )
    frag = HFragment(
        name="bad_hint",
        bindings=(inp,),
        scratch_regs=("r7",),
        body=body,
    )
    # nonexistent_reg is not physical → must be rejected
    with pytest.raises(HIRValidationError, match="not a valid physical register"):
        run_codegen_from_fragment(frag, ToyTarget(), register_model=_DummyRegModel())


# ======================================================================
# 17.  Unsupported target raises TypeError
# ======================================================================

class _PlainTarget:
    """A target that only implements select_instructions, not fragment."""
    name = "plain"
    def select_instructions(self, func):
        from rpkbin.codegen.asm import AsmFunction
        return AsmFunction(func.name, ())


def test_unsupported_target_raises_type_error():
    """Target without select_fragment_instructions raises clear TypeError."""
    body = (HExit(),)
    frag = HFragment(name="bad_target", body=body)
    with pytest.raises(TypeError, match="select_fragment_instructions"):
        run_codegen_from_fragment(frag, _PlainTarget())


# ======================================================================
# 18.  Existing function pipeline and ToyTarget output unchanged
# ======================================================================

def test_existing_function_pipeline_unchanged():
    """Existing HFunction → ToyTarget pipeline must produce same output."""
    func = u8("x")
    hfunc = type("HFunction", (), {})()
    from rpkbin.codegen import HFunction, HParam, HReturn
    hfunc = HFunction(
        name="add_one",
        params=(HParam("x", UInt(8)),),
        return_ty=UInt(8),
        body=(HReturn(values=(HBinOp("add", HVar("x", UInt(8)), hconst(1), UInt(8)),)),),
    )
    result = run_codegen_from_hir(hfunc, ToyTarget())
    asm_text = result.asm_text
    # Check same ASM as before (uses %name for VReg since there's no hint)
    assert "entry:" in asm_text
    assert "ADD" in asm_text
    assert "RET" in asm_text


def test_existing_lir_pipeline_unchanged():
    """Existing LIR-first run_codegen must produce same output."""
    from rpkbin.codegen.ir import Assign, Block, Function, Return, binop, const, var
    v0 = var("v0")
    func = Function(
        name="test_fn",
        params=(var("a"),),
        blocks=(
            Block(
                label="entry",
                statements=(Assign(v0, binop("add", var("a"), const(1))),),
                terminator=Return(v0),
            ),
        ),
    )
    result = run_codegen(func, ToyTarget())
    assert result.asm_text == "\n".join([
        "entry:",
        "MOV v0, a",
        "ADD v0, #1",
        "RET v0",
    ])
