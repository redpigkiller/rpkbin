"""Fragment HIR contract and validator tests."""

import pytest

from rpkbin.codegen.hir import (
    HAssign, HBinOp, HBitSet, HCall, HCallAssign, HCmp, HConst, HExit,
    HExprStmt, HExternalSymbol, HFor, HFragment, HFragmentBinding,
    HFunction, HIf, HInlineAsm, HModule, HParam, HPoll, HReturn,
    HStore, HSymbolAddr, HVar, HWhile,
    SInt, UInt, Void,
    hconst, u8,
)
from rpkbin.codegen.hir_validate import (
    HIRValidationError,
    validate_hfragment,
    validate_hmodule,
)
from rpkbin.codegen.lir import SourceLoc


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

_LOC = SourceLoc("test.rpk", 1, 1)


def _mkbinding(
    name: str,
    ty=UInt(8),
    reg: str = "r0",
    mode: str = "in",
) -> HFragmentBinding:
    return HFragmentBinding(name=name, ty=ty, reg=reg, mode=mode)


def _simple_fragment(
    name: str = "frag",
    bindings: tuple = (),
    scratch: tuple = (),
    body: tuple = (),
) -> HFragment:
    return HFragment(
        name=name,
        bindings=bindings,
        scratch_regs=scratch,
        body=body,
        loc=_LOC,
    )


# ======================================================================
# 1.  Valid straight-line fragment
# ======================================================================

def test_valid_straight_line():
    """A fragment with one in, one out, assign and exit is valid."""
    inp = _mkbinding("x", mode="in", reg="r0")
    out = _mkbinding("y", mode="out", reg="r1")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=HVar("x", UInt(8)), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(inp, out), body=body)
    validate_hfragment(frag)  # must not raise


def test_valid_no_bindings():
    """Fragment with no bindings and just an exit is valid."""
    frag = _simple_fragment("empty", body=(HExit(loc=_LOC),))
    validate_hfragment(frag)


# ======================================================================
# 2.  Valid if/else with both branches writing out and exiting
# ======================================================================

def test_valid_if_else_both_exit():
    """Both branches write out and exit."""
    out = _mkbinding("out", mode="out", reg="r1")
    cond = HCmp("eq", HConst(1, UInt(8)), HConst(1, UInt(8)))
    body = (
        HIf(
            cond=cond,
            then_body=(
                HAssign(target=HVar("out", UInt(8)), value=hconst(1), loc=_LOC),
                HExit(loc=_LOC),
            ),
            else_body=(
                HAssign(target=HVar("out", UInt(8)), value=hconst(2), loc=_LOC),
                HExit(loc=_LOC),
            ),
            loc=_LOC,
        ),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    validate_hfragment(frag)


# ======================================================================
# 3.  Write to 'in' binding rejected
# ======================================================================

def test_write_to_in_rejected():
    inp = _mkbinding("x", mode="in", reg="r0")
    body = (
        HAssign(target=HVar("x", UInt(8)), value=hconst(1), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(inp,), body=body)
    with pytest.raises(HIRValidationError, match="cannot write to .in. binding"):
        validate_hfragment(frag)


# ======================================================================
# 4.  Out binding read-before-write rejected
# ======================================================================

def test_out_read_before_write_rejected():
    out = _mkbinding("y", mode="out", reg="r1")
    cond = HCmp("eq", HVar("y", UInt(8)), hconst(1))
    body = (
        HIf(
            cond=cond,
            then_body=(HExit(loc=_LOC),),
            else_body=(HExit(loc=_LOC),),
            loc=_LOC,
        ),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="read of unassigned out"):
        validate_hfragment(frag)


# ======================================================================
# 5.  Exit without assigning out rejected
# ======================================================================

def test_exit_without_out_assigned_rejected():
    out = _mkbinding("y", mode="out", reg="r1")
    body = (HExit(loc=_LOC),)
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
        validate_hfragment(frag)


# ======================================================================
# 6.  Inout binding — readable and writable
# ======================================================================

def test_inout_read_write():
    io = _mkbinding("x", mode="inout", reg="r0")
    body = (
        HAssign(target=HVar("x", UInt(8)), value=hconst(1), loc=_LOC),
        HAssign(target=HVar("x", UInt(8)), value=HVar("x", UInt(8)), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(io,), body=body)
    validate_hfragment(frag)


# ======================================================================
# 7.  Duplicate binding name rejected
# ======================================================================

def test_duplicate_binding_name():
    b1 = _mkbinding("x", reg="r0")
    b2 = _mkbinding("x", reg="r1")
    frag = _simple_fragment("test", bindings=(b1, b2), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="duplicate binding name"):
        validate_hfragment(frag)


# ======================================================================
# 8.  Duplicate interface register rejected
# ======================================================================

def test_duplicate_interface_reg():
    b1 = _mkbinding("x", reg="r0")
    b2 = _mkbinding("y", reg="r0")
    frag = _simple_fragment("test", bindings=(b1, b2), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="overlap"):
        validate_hfragment(frag)


# ======================================================================
# 9.  Duplicate scratch register rejected
# ======================================================================

def test_duplicate_scratch_reg():
    frag = _simple_fragment("test", scratch=("r5", "r5"), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="duplicate scratch"):
        validate_hfragment(frag)


# ======================================================================
# 10. Scratch/interface overlap rejected
# ======================================================================

def test_scratch_interface_overlap():
    b = _mkbinding("x", reg="r0")
    frag = _simple_fragment("test", bindings=(b,), scratch=("r0",), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="overlap"):
        validate_hfragment(frag)


# ======================================================================
# 11. Invalid binding mode rejected
# ======================================================================

def test_invalid_binding_mode():
    b = HFragmentBinding(name="x", ty=UInt(8), reg="r0", mode="invalid")
    frag = _simple_fragment("test", bindings=(b,), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="mode"):
        validate_hfragment(frag)


# ======================================================================
# 12. Void binding type rejected
# ======================================================================

def test_void_binding_type():
    b = HFragmentBinding(name="x", ty=Void(), reg="r0", mode="in")
    frag = _simple_fragment("test", bindings=(b,), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="type must be UInt or SInt"):
        validate_hfragment(frag)


# ======================================================================
# 13. HReturn rejected inside fragment
# ======================================================================

def test_return_rejected():
    body = (HReturn(values=(hconst(0),), loc=_LOC),)
    frag = _simple_fragment("test", body=body)
    with pytest.raises(HIRValidationError, match="HReturn is not allowed"):
        validate_hfragment(frag)


# ======================================================================
# 14. HFor / HWhile / HPoll rejected inside fragment
# ======================================================================

def test_for_rejected():
    i = HVar("i", UInt(8))
    body = (
        HFor(var=i, init=hconst(0), bound=hconst(3), body=(), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", body=body)
    with pytest.raises(HIRValidationError, match="HFor is not allowed"):
        validate_hfragment(frag)


def test_while_rejected():
    body = (
        HWhile(cond=HCmp("eq", hconst(1), hconst(1)), body=(), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", body=body)
    with pytest.raises(HIRValidationError, match="HWhile is not allowed"):
        validate_hfragment(frag)


def test_poll_rejected():
    body = (
        HPoll(cond=HCmp("eq", hconst(1), hconst(1)), body=(), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", body=body)
    with pytest.raises(HIRValidationError, match="HPoll is not allowed"):
        validate_hfragment(frag)


# ======================================================================
# 15. HBreak / HContinue rejected inside fragment
# ======================================================================

def test_break_rejected():
    from rpkbin.codegen.hir import HBreak
    body = (
        HBreak(loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", body=body)
    with pytest.raises(HIRValidationError, match="HBreak is not allowed"):
        validate_hfragment(frag)


def test_continue_rejected():
    from rpkbin.codegen.hir import HContinue
    body = (
        HContinue(loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", body=body)
    with pytest.raises(HIRValidationError, match="HContinue is not allowed"):
        validate_hfragment(frag)


# ======================================================================
# 16. Fragment path missing HExit
# ======================================================================

def test_missing_exit():
    """Fragment that falls through without HExit is rejected."""
    frag = _simple_fragment("test", body=(HAssign(target=u8("x"), value=hconst(1), loc=_LOC),))
    with pytest.raises(HIRValidationError, match="does not terminate"):
        validate_hfragment(frag)


# ======================================================================
# 17. If without else causing out not definite assigned
# ======================================================================

def test_if_no_else_out_undef():
    """If without else: out written only in then-branch → not definite after."""
    out = _mkbinding("out", mode="out", reg="r1")
    cond = HCmp("eq", hconst(1), hconst(1))
    body = (
        HIf(
            cond=cond,
            then_body=(
                HAssign(target=HVar("out", UInt(8)), value=hconst(1), loc=_LOC),
            ),
            loc=_LOC,
        ),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
        validate_hfragment(frag)


# ======================================================================
# 18. Statement after terminator (HExit) rejected
# ======================================================================

def test_unreachable_after_exit():
    out = _mkbinding("y", mode="out", reg="r1")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=hconst(1), loc=_LOC),
        HExit(loc=_LOC),
        HAssign(target=HVar("y", UInt(8)), value=hconst(2), loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="unreachable statement"):
        validate_hfragment(frag)


# ======================================================================
# 19. HCallAssign target correctly updates out assignment state
# ======================================================================

def test_call_assign_writes_out():
    """HCallAssign target writes to out binding, making it assigned."""
    out = _mkbinding("out", mode="out", reg="r1")
    call = HCall(name="get_val", args=(hconst(1),), return_ty=Void())
    body = (
        HCallAssign(targets=(HVar("out", UInt(8)),), call=call, loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    validate_hfragment(frag)


# ======================================================================
# 20. Nested expression / call argument read-before-write on out
# ======================================================================

def test_nested_expr_out_read_before_write():
    """Out binding read in sub-expression before being written."""
    out = _mkbinding("y", mode="out", reg="r1")
    body = (
        HAssign(
            target=u8("z"),
            value=HVar("y", UInt(8)),  # read before write
            loc=_LOC,
        ),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="read of unassigned out"):
        validate_hfragment(frag)


def test_call_arg_out_read_before_write():
    """Out binding used as call argument before being written."""
    out = _mkbinding("y", mode="out", reg="r1")
    call = HCall(name="foo", args=(HVar("y", UInt(8)),), return_ty=Void())
    body = (
        HExprStmt(expr=call, loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="read of unassigned out"):
        validate_hfragment(frag)


# ======================================================================
# 21. Fragment name conflicts with function/module symbol
# ======================================================================

def test_fragment_name_conflict_with_function():
    """Fragment name matching a function name is rejected at module level."""
    fn = HFunction(name="dup", params=(), return_ty=Void(), body=(HReturn(values=(hconst(0),)),))
    frag = _simple_fragment("dup", body=(HExit(loc=_LOC),))
    mod = HModule(functions=(fn,), fragments=(frag,))
    with pytest.raises(HIRValidationError, match="Duplicate name"):
        validate_hmodule(mod)


def test_fragment_name_conflict_with_symbol():
    """Fragment name matching an external symbol is rejected."""
    sym = HExternalSymbol(name="BASE", address_ty=UInt(8))
    frag = _simple_fragment("BASE", body=(HExit(loc=_LOC),))
    mod = HModule(external_symbols=(sym,), fragments=(frag,))
    with pytest.raises(HIRValidationError, match="Duplicate name"):
        validate_hmodule(mod)


# ======================================================================
# 22. Undefined call / HSymbolAddr inside fragment rejected by module validator
# ======================================================================

def test_fragment_undefined_call():
    """Call to undefined function inside fragment is caught by module validator."""
    call = HCall(name="nonexistent", args=(), return_ty=Void())
    body = (
        HExprStmt(expr=call, loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", body=body)
    mod = HModule(fragments=(frag,))
    with pytest.raises(HIRValidationError, match="undefined"):
        validate_hmodule(mod)


def test_fragment_undefined_symbol():
    """HSymbolAddr referencing undeclared symbol inside fragment."""
    addr = HSymbolAddr(name="UNDEF", ty=UInt(8))
    body = (
        HAssign(target=u8("x"), value=addr, loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", body=body)
    mod = HModule(fragments=(frag,))
    with pytest.raises(HIRValidationError, match="undeclared"):
        validate_hmodule(mod)


# ======================================================================
# 23. SourceLoc preserved in errors
# ======================================================================

def test_source_loc_in_error():
    """Validation error carries the correct file/line info."""
    out = _mkbinding("y", mode="out", reg="r1")
    body = (HExit(loc=_LOC),)
    frag = _simple_fragment("test", bindings=(out,), body=body)
    try:
        validate_hfragment(frag)
        assert False, "expected exception"
    except HIRValidationError as e:
        assert e.loc is not None
        assert e.loc.file == "test.rpk"


def test_error_loc_missing_out():
    """Error for unassigned out mentions the binding name."""
    out = _mkbinding("y", mode="out", reg="r1")
    body = (HExit(loc=_LOC),)
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="'y'"):
        validate_hfragment(frag)


# ======================================================================
# 24. HFunction containing HExit rejected
# ======================================================================

def test_hfunction_hir_contains_exit():
    """HExit inside an HFunction body must be rejected by validate_hfunction."""
    from rpkbin.codegen.hir_validate import validate_hfunction

    func = HFunction(
        name="bad",
        params=(),
        return_ty=Void(),
        body=(HExit(loc=_LOC),),
    )
    with pytest.raises(HIRValidationError, match="HExit is not allowed"):
        validate_hfunction(func)


# ======================================================================
# Additional edge-case tests
# ======================================================================

def test_if_all_branches_exit_then_after_is_unreachable():
    """If all branches terminate, following statement is unreachable."""
    out = _mkbinding("y", mode="out", reg="r1")
    cond = HCmp("eq", hconst(1), hconst(1))
    body = (
        HIf(
            cond=cond,
            then_body=(
                HAssign(target=HVar("y", UInt(8)), value=hconst(1), loc=_LOC),
                HExit(loc=_LOC),
            ),
            else_body=(
                HAssign(target=HVar("y", UInt(8)), value=hconst(2), loc=_LOC),
                HExit(loc=_LOC),
            ),
            loc=_LOC,
        ),
        HAssign(target=HVar("y", UInt(8)), value=hconst(3), loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="unreachable statement"):
        validate_hfragment(frag)


def test_binding_name_matches_physical_reg_rejected():
    """Binding name must not equal a declared physical register name."""
    b = HFragmentBinding(name="r0", ty=UInt(8), reg="r0", mode="in")
    frag = _simple_fragment("test", bindings=(b,), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="matches a physical register name"):
        validate_hfragment(frag)


def test_sint_binding_accepted():
    """SInt binding type is accepted (only Void is rejected)."""
    b = HFragmentBinding(name="x", ty=SInt(8), reg="r0", mode="in")
    frag = _simple_fragment("test", bindings=(b,), body=(HExit(loc=_LOC),))
    validate_hfragment(frag)


def test_store_reads_out_before_write():
    """HStore address containing out binding triggers read-before-write."""
    out = _mkbinding("y", mode="out", reg="r1")
    body = (
        HStore(ptr_expr=HVar("y", UInt(8)), value_expr=hconst(0xFF), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="read of unassigned out"):
        validate_hfragment(frag)


def test_inline_asm_preserves_assignment():
    """HInlineAsm is opaque and does not change assignment state."""
    out = _mkbinding("y", mode="out", reg="r1")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=hconst(1), loc=_LOC),
        HInlineAsm(text="NOP", loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    validate_hfragment(frag)


def test_bit_set_on_out_requires_prior_assign():
    """HBitSet on out binding requires prior assignment (read-before-write)."""
    out = _mkbinding("y", mode="out", reg="r1")
    body = (
        HBitSet(var=HVar("y", UInt(8)), bit_idx=0, value=1, loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="read of unassigned out"):
        validate_hfragment(frag)


def test_bit_set_on_out_after_assign():
    """HBitSet after prior assign works and keeps out assigned."""
    out = _mkbinding("y", mode="out", reg="r1")
    body = (
        HAssign(target=HVar("y", UInt(8)), value=hconst(0), loc=_LOC),
        HBitSet(var=HVar("y", UInt(8)), bit_idx=0, value=1, loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    validate_hfragment(frag)


def test_bit_set_on_in_rejected():
    """HBitSet on 'in' binding is rejected (write disallowed)."""
    inp = _mkbinding("x", mode="in", reg="r1")
    body = (
        HBitSet(var=HVar("x", UInt(8)), bit_idx=0, value=1, loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(inp,), body=body)
    with pytest.raises(HIRValidationError, match="cannot write to .in. binding"):
        validate_hfragment(frag)


def test_elif_fallthrough_merge():
    """elif branch fallthrough merges assigned set by intersection."""
    out = _mkbinding("y", mode="out", reg="r1")
    cond = HCmp("eq", hconst(1), hconst(1))
    body = (
        HIf(
            cond=cond,
            then_body=(
                HAssign(target=HVar("y", UInt(8)), value=hconst(1), loc=_LOC),
            ),
            elif_branches=(
                (HCmp("eq", hconst(2), hconst(2)), (
                    HAssign(target=HVar("y", UInt(8)), value=hconst(2), loc=_LOC),
                )),
            ),
            # no else → implicit fallthrough where y is not assigned
            loc=_LOC,
        ),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    # y is not definitely assigned (only in branches, not in implicit else)
    with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
        validate_hfragment(frag)


def test_empty_binding_name_rejected():
    b = HFragmentBinding(name="", ty=UInt(8), reg="r0", mode="in")
    frag = _simple_fragment("test", bindings=(b,), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="non-empty"):
        validate_hfragment(frag)


def test_empty_reg_name_rejected():
    b = HFragmentBinding(name="x", ty=UInt(8), reg="", mode="in")
    frag = _simple_fragment("test", bindings=(b,), body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="non-empty"):
        validate_hfragment(frag)


def test_fragment_name_empty_rejected():
    frag = _simple_fragment(name="", body=(HExit(loc=_LOC),))
    with pytest.raises(HIRValidationError, match="fragment name must be non-empty"):
        validate_hfragment(frag)


# ======================================================================
# Fragment regression tests
# ======================================================================

# --- 1. Local assignment target name equals scratch reg ---

def test_local_target_name_equals_scratch_reg():
    """Local HVar name matching a scratch register name is rejected."""
    body = (
        HAssign(target=HVar("r5", UInt(8)), value=hconst(1), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", scratch=("r5",), body=body)
    with pytest.raises(HIRValidationError, match="matches a physical register name"):
        validate_hfragment(frag)


# --- 2. Local name in nested expression equals interface reg ---

def test_local_in_expr_equals_interface_reg():
    """Local HVar used in a sub-expression matching an interface register."""
    inp = _mkbinding("x", mode="in", reg="r0")
    body = (
        HAssign(
            target=HVar("y", UInt(8)),
            value=HBinOp("add", HVar("x", UInt(8)), HVar("r0", UInt(8)), UInt(8)),
            loc=_LOC,
        ),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(inp,), body=body)
    with pytest.raises(HIRValidationError, match="matches a physical register name"):
        validate_hfragment(frag)


# --- 3. Scratch-to-scratch alias with register_model ---

class _AliasModel:
    """Minimal RegisterModel with aliasing registers."""
    @staticmethod
    def allocatable_registers():
        return ["r0", "r1", "r2", "r3"]
    @staticmethod
    def register_width(reg):
        return 8
    @staticmethod
    def register_aliases():
        return [("dp", ["dp_low", "dp_high"])]
    @staticmethod
    def spill_slots():
        return []


def test_scratch_scratch_alias_rejected():
    """Two scratch registers that alias each other via register_model."""
    frag = _simple_fragment(
        "test",
        scratch=("dp_low", "dp_high"),
        body=(HExit(loc=_LOC),),
    )
    with pytest.raises(HIRValidationError, match="aliases"):
        validate_hfragment(frag, register_model=_AliasModel())


def test_scratch_scratch_no_alias_ok():
    """Scratch registers that don't alias (same model) pass."""
    frag = _simple_fragment(
        "test",
        scratch=("r2", "r3"),
        body=(HExit(loc=_LOC),),
    )
    validate_hfragment(frag, register_model=_AliasModel())


# --- 4. Binding UInt(8) read with UInt(16) HVar ---

def test_binding_read_wrong_width():
    """Binding declared UInt(8), HVar read with UInt(16) is rejected."""
    inp = _mkbinding("x", mode="in", reg="r0")
    body = (
        HAssign(
            target=HVar("y", UInt(8)),
            value=HVar("x", UInt(16)),  # width mismatch
            loc=_LOC,
        ),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(inp,), body=body)
    with pytest.raises(HIRValidationError, match="type mismatch"):
        validate_hfragment(frag)


# --- 5. Binding UInt(8) write with UInt(16) HVar ---

def test_binding_write_wrong_width():
    """Binding declared UInt(8), HVar assignment target with UInt(16)."""
    out = _mkbinding("y", mode="out", reg="r1")
    body = (
        HAssign(target=HVar("y", UInt(16)), value=hconst(1), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="type mismatch"):
        validate_hfragment(frag)


# --- 6. Signedness mismatch (same width) ---

def test_binding_signedness_mismatch():
    """Binding SInt(8), HVar with UInt(8) is rejected."""
    b = _mkbinding("x", ty=SInt(8), mode="in", reg="r0")
    body = (
        HAssign(
            target=HVar("y", SInt(8)),
            value=HVar("x", UInt(8)),  # unsigned vs signed
            loc=_LOC,
        ),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(b,), body=body)
    with pytest.raises(HIRValidationError, match="type mismatch"):
        validate_hfragment(frag)


# --- 7. HCallAssign target binding type mismatch ---

def test_call_assign_target_type_mismatch():
    """HCallAssign target HVar type differs from binding declaration."""
    out = _mkbinding("y", mode="out", reg="r1", ty=UInt(8))
    call = HCall(name="get_val", args=(), return_ty=Void())
    body = (
        HCallAssign(
            targets=(HVar("y", UInt(16)),),
            call=call,
            loc=_LOC,
        ),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="type mismatch"):
        validate_hfragment(frag)


# --- 8. HBitSet binding type mismatch ---

def test_bitset_type_mismatch():
    """HBitSet var HVar type differs from binding declaration."""
    out = _mkbinding("y", mode="out", reg="r1", ty=UInt(8))
    body = (
        HAssign(target=HVar("y", UInt(8)), value=hconst(0), loc=_LOC),
        HBitSet(var=HVar("y", UInt(16)), bit_idx=0, value=1, loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(out,), body=body)
    with pytest.raises(HIRValidationError, match="type mismatch"):
        validate_hfragment(frag)


# --- 9. Binding HVar reg_hint conflicts with binding.reg ---

def test_binding_var_reg_hint_conflict():
    """HVar reg_hint differs from binding.reg."""
    inp = _mkbinding("x", mode="in", reg="r0")
    body = (
        HAssign(
            target=HVar("y", UInt(8)),
            value=HVar("x", UInt(8), reg_hint="r1"),  # conflict: binding.reg=r0
            loc=_LOC,
        ),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(inp,), body=body)
    with pytest.raises(HIRValidationError, match="reg_hint.*conflict"):
        validate_hfragment(frag)


# --- 10. Binding error uses b.loc not fragment.loc ---

def test_binding_error_uses_binding_loc():
    """Declaration error for binding uses the binding's own SourceLoc."""
    bl = SourceLoc("binding.rpk", 5, 10)
    b = HFragmentBinding(name="x", ty=Void(), reg="r0", mode="in", loc=bl)
    frag = HFragment(
        name="test", bindings=(b,), body=(HExit(loc=_LOC),),
        loc=SourceLoc("frag.rpk", 1, 1),
    )
    try:
        validate_hfragment(frag)
        assert False, "expected exception"
    except HIRValidationError as e:
        assert e.loc is not None
        assert e.loc.file == "binding.rpk"
        assert e.loc.line == 5


# --- 11. Legal local and binding HVar still pass ---

def test_legal_local_and_binding_hvars():
    """Local HVars (not shadowing regs) and correct binding HVars work."""
    inp = _mkbinding("x", mode="in", reg="r0")
    body = (
        HAssign(target=HVar("tmp", UInt(8)), value=HVar("x", UInt(8)), loc=_LOC),
        HAssign(target=HVar("out", UInt(8)), value=HVar("tmp", UInt(8)), loc=_LOC),
        HExit(loc=_LOC),
    )
    frag = _simple_fragment("test", bindings=(inp,), body=body)
    validate_hfragment(frag)
