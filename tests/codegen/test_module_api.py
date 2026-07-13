"""Module API tests: HFor validation, HExternFn clobbers, external symbols,
HCallAssign, and module-level containers."""

import pytest

from rpkbin.codegen.hir import (
    HAssign, HBinOp, HBitSet, HCall, HCallAssign, HCmp, HConst, HExternFn,
    HExprStmt, HExternalSymbol, HExtract, HFor, HFunction, HIf,
    HLoad, HModule, HParam, HReturn, HStore, HSymbolAddr, HVar,
    SInt, UInt, Void,
    hconst, simple_function, u8, u16,
)
from rpkbin.codegen.hir_validate import (
    HIRValidationError,
    validate_extern_fn,
    validate_hfunction,
    validate_hmodule,
)
from rpkbin.codegen import lir
from rpkbin.codegen.lower import lower_function, lower_module
from rpkbin.codegen.patterns import load_patterns_from_dicts
from rpkbin.codegen.toy_target import ToyTarget
from rpkbin.codegen.pipeline import run_codegen_from_hir


# ======================================================================
# Part 1: HFor loop-variable write detection (name-based)
# ======================================================================

def test_codegen_exports_call_stmt():
    """Top-level codegen package re-exports lir.CallStmt."""
    from rpkbin.codegen import CallStmt

    assert CallStmt is lir.CallStmt


def test_hfor_name_based_write_rejected():
    """Different HVar instance with same name must be rejected."""
    loop_var = HVar("i", UInt(8))
    write_target = HVar("i", UInt(8))  # different instance, same name
    func = simple_function(
        "bad_for",
        [HParam("x", UInt(8))],
        [
            HFor(
                var=loop_var, init=hconst(0), bound=hconst(3),
                body=(HAssign(target=write_target, value=hconst(1)),),
            )
        ],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="loop variable"):
        validate_hfunction(func)
    with pytest.raises(NotImplementedError, match="loop variable"):
        lower_function(func)


def test_hfor_name_based_bitset_rejected():
    """HBitSet on different instance of same-name var must be rejected."""
    loop_var = HVar("i", UInt(8))
    bitset_var = HVar("i", UInt(8))  # different instance
    func = simple_function(
        "bad_for_bitset",
        [],
        [
            HFor(
                var=loop_var, init=hconst(0), bound=hconst(3),
                body=(HBitSet(var=bitset_var, bit_idx=0, value=1),),
            ),
            HReturn(values=(hconst(0),)),
        ],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="loop variable"):
        validate_hfunction(func)
    with pytest.raises(NotImplementedError, match="loop variable"):
        lower_function(func)


def test_hfor_nested_same_name_rejected():
    """Nested HFor using same loop-variable name must be rejected."""
    i = HVar("i", UInt(8))
    inner_for = HFor(
        var=HVar("i", UInt(8)),  # same name
        init=hconst(0), bound=hconst(2),
        body=(),
    )
    outer_for = HFor(
        var=i, init=hconst(0), bound=hconst(3),
        body=(inner_for,),
    )
    func = simple_function(
        "nested_same_name",
        [],
        [outer_for, HReturn(values=(hconst(0),))],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="Nested HFor"):
        validate_hfunction(func)


def test_hfor_init_type_mismatch_rejected():
    """HFor init type must match loop variable type exactly."""
    i = HVar("i", UInt(8))
    func = simple_function(
        "bad_init_type",
        [],
        [
            HFor(
                var=i,
                init=HConst(0, UInt(16)),  # 16-bit init, 8-bit var
                bound=HConst(5, UInt(8)),
                body=(),
            ),
            HReturn(values=(hconst(0),)),
        ],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="init type"):
        validate_hfunction(func)


def test_hfor_bound_type_mismatch_rejected():
    """HFor bound type must match loop variable type exactly."""
    i = HVar("i", UInt(8))
    func = simple_function(
        "bad_bound_type",
        [],
        [
            HFor(
                var=i,
                init=HConst(0, UInt(8)),
                bound=HConst(5, SInt(8)),  # signed bound, unsigned var
                body=(),
            ),
            HReturn(values=(hconst(0),)),
        ],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="bound type"):
        validate_hfunction(func)


def test_hfor_bound_lt_init_rejected():
    """HFor bound < init must be rejected."""
    i = HVar("i", UInt(8))
    func = simple_function(
        "bad_range",
        [],
        [
            HFor(
                var=i,
                init=hconst(5),
                bound=hconst(3),  # bound < init
                body=(),
            ),
            HReturn(values=(hconst(0),)),
        ],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="must be >= init"):
        validate_hfunction(func)


def test_hfor_value_out_of_range_rejected():
    """HFor init/bound value must fit in the declared type."""
    i = HVar("i", UInt(8))
    func = simple_function(
        "bad_value",
        [],
        [
            HFor(
                var=i,
                init=HConst(256, UInt(8)),  # 256 does not fit in u8
                bound=hconst(0),
                body=(),
            ),
            HReturn(values=(hconst(0),)),
        ],
        Void(),
    )
    with pytest.raises(HIRValidationError, match="cannot be represented"):
        validate_hfunction(func)


# ======================================================================
# Part 1: HExtract SInt storage rejection
# ======================================================================

def test_extract_sint_storage_rejected():
    """HExtract with SInt storage type must be rejected."""
    a = HVar("a", UInt(16))
    bad = HExtract(a, msb=7, lsb=0, ty=SInt(8))
    stmt = HAssign(target=HVar("x", SInt(8)), value=bad)
    func = simple_function("bad", [HParam("a", UInt(16))], [stmt], Void())
    with pytest.raises(HIRValidationError, match="storage type must be UInt"):
        validate_hfunction(func)


# ======================================================================
# Part 2: HExternFn clobbers
# ======================================================================

def test_extern_fn_clobbers_round_trip():
    """HExternFn clobbers and loc are preserved."""
    loc = lir.SourceLoc("test.rpk", 10, 5)
    efn = HExternFn(
        name="foo",
        params=(HParam("x", UInt(8)),),
        return_ty=Void(),
        clobbers=("r0", "r1"),
        loc=loc,
    )
    assert efn.clobbers == ("r0", "r1")
    assert efn.loc == loc
    assert efn.name == "foo"


def test_extern_fn_default_clobbers():
    """HExternFn default clobbers is empty."""
    efn = HExternFn(name="foo", params=(), return_ty=Void())
    assert efn.clobbers == ()
    assert efn.loc is None


def test_extern_fn_duplicate_clobber_rejected():
    """Duplicate clobber names must be rejected."""
    efn = HExternFn(
        name="bad",
        params=(),
        return_ty=Void(),
        clobbers=("r0", "r0"),  # duplicate
    )
    with pytest.raises(HIRValidationError, match="duplicate clobber"):
        validate_extern_fn(efn)


def test_extern_fn_empty_clobber_rejected():
    """Empty-string clobber must be rejected."""
    efn = HExternFn(
        name="bad",
        params=(),
        return_ty=Void(),
        clobbers=("",),  # empty
    )
    with pytest.raises(HIRValidationError, match="non-empty"):
        validate_extern_fn(efn)


# ======================================================================
# Part 3: External symbols
# ======================================================================

def test_external_symbol_hsymboladdr_lower():
    """HSymbolAddr lowers to lir.SymbolAddr and ToyTarget emits &name."""
    sym = HExternalSymbol(name="UART_BASE", address_ty=UInt(16), value_ty=UInt(8), volatile=True)
    addr = HSymbolAddr(name="UART_BASE", ty=UInt(16))
    load = HLoad(ptr_expr=addr, ty=UInt(8))
    result_var = HVar("val", UInt(8))
    func = HFunction(
        name="read_uart",
        params=(),
        return_ty=UInt(8),
        body=(
            HAssign(target=result_var, value=load),
            HReturn(values=(result_var,)),
        ),
    )
    mod = HModule(functions=(func,), external_symbols=(sym,))
    validate_hmodule(mod)

    lir_mod = lower_module(mod)
    assert len(lir_mod.functions) == 1

    result = run_codegen_from_hir(func, ToyTarget())
    assert "&UART_BASE" in result.asm_text


def test_external_symbol_load_store_volatile():
    """Volatile symbol data goes through HLoad/HStore, preserving volatility."""
    sym = HExternalSymbol(name="STATUS_REG", address_ty=UInt(8), value_ty=UInt(8), volatile=True)
    addr = HSymbolAddr(name="STATUS_REG", ty=UInt(8))
    func = HFunction(
        name="poll_status",
        params=(),
        return_ty=Void(),
        body=(
            HStore(ptr_expr=addr, value_expr=HConst(0xFF, UInt(8))),
        ),
    )
    mod = HModule(functions=(func,), external_symbols=(sym,))
    validate_hmodule(mod)
    result = run_codegen_from_hir(func, ToyTarget())
    assert "&STATUS_REG" in result.asm_text
    assert "MOV" in result.asm_text


def test_undeclared_symbol_rejected():
    """HSymbolAddr referencing undeclared symbol must be rejected by module validator."""
    addr = HSymbolAddr(name="UNDECLARED", ty=UInt(8))
    func = simple_function(
        "bad",
        [],
        [HReturn(values=(addr,))],
        UInt(8),
    )
    mod = HModule(functions=(func,))
    with pytest.raises(HIRValidationError, match="undeclared"):
        validate_hmodule(mod)


def test_symbol_addr_not_rewritten():
    """SymbolAddr must survive rewrite pass unchanged."""
    addr = HSymbolAddr(name="BASE", ty=UInt(16))
    func = HFunction(
        name="get_base",
        params=(),
        return_ty=UInt(16),
        body=(HReturn(values=(addr,)),),
    )
    result = run_codegen_from_hir(func, ToyTarget())
    assert "&BASE" in result.asm_text


# ======================================================================
# Part 4: HCallAssign
# ======================================================================

def test_call_assign_multi_return():
    """HCallAssign with multiple targets produces one CALL statement."""
    a = HVar("a", UInt(8))
    b = HVar("b", UInt(8))
    call = HCall(name="divmod", args=(hconst(10), hconst(3)), return_ty=Void())
    stmt = HCallAssign(targets=(a, b), call=call)

    func = HFunction(
        name="use_divmod",
        params=(),
        return_ty=Void(),
        body=(stmt, HReturn(values=(hconst(0),))),
    )

    externs = (HExternFn("divmod", (HParam("x", UInt(8)), HParam("y", UInt(8))), (UInt(8), UInt(8))),)
    mod = HModule(functions=(func,), extern_functions=externs)
    validate_hmodule(mod)

    lir_mod = lower_module(mod)
    assert len(lir_mod.functions) == 1

    lir_func = lir_mod.functions[0]
    entry = lir_func.blocks[0]
    call_stmts = [s for s in entry.statements if isinstance(s, lir.CallAssign)]
    assert len(call_stmts) == 1, "HCallAssign must produce exactly one CallAssign"

    ca = call_stmts[0]
    assert len(ca.targets) == 2
    assert ca.targets[0].name == "a"
    assert ca.targets[1].name == "b"
    assert ca.call.name == "divmod"

    result = run_codegen_from_hir(func, ToyTarget())
    assert "CALL" in result.asm_text
    assert "divmod" in result.asm_text
    # Must only contain one CALL
    call_count = result.asm_text.count("CALL")
    assert call_count == 1, f"Expected 1 CALL, got {call_count}"


def test_lower_module_preserves_extern_return_regs_on_call_assign():
    """Extern return_regs must survive lowering without overwriting dest hints."""
    a = HVar("a", UInt(8), reg_hint="r4")
    b = HVar("b", UInt(8), reg_hint="r5")
    call = HCall(name="pair", args=(), return_ty=Void())
    func = HFunction(
        name="use_pair",
        params=(),
        return_ty=Void(),
        body=(HCallAssign(targets=(a, b), call=call), HReturn(values=(hconst(0),))),
    )
    ext = HExternFn("pair", (), (UInt(8), UInt(8)), return_regs=("r1", "r2"))
    mod = HModule(functions=(func,), extern_functions=(ext,))

    validate_hmodule(mod)
    lir_mod = lower_module(mod)
    call_stmt = next(
        stmt
        for stmt in lir_mod.functions[0].blocks[0].statements
        if isinstance(stmt, lir.CallAssign)
    )

    assert isinstance(call_stmt.targets[0], lir.VReg)
    assert isinstance(call_stmt.targets[1], lir.VReg)
    assert tuple(target.hint for target in call_stmt.targets) == ("r4", "r5")
    assert call_stmt.abi_return_regs == ("r1", "r2")


def test_call_assign_with_none():
    """HCallAssign with None in targets discards that return value."""
    a = HVar("a", UInt(8))
    call = HCall(name="divmod", args=(hconst(10), hconst(3)), return_ty=Void())
    stmt = HCallAssign(targets=(a, None), call=call)

    func = HFunction(
        name="use_divmod_discard",
        params=(),
        return_ty=Void(),
        body=(stmt, HReturn(values=(hconst(0),))),
    )

    externs = (HExternFn("divmod", (HParam("x", UInt(8)), HParam("y", UInt(8))), (UInt(8), UInt(8))),)
    mod = HModule(functions=(func,), extern_functions=externs)
    validate_hmodule(mod)
    lir_func = lower_function(func)

    entry = lir_func.blocks[0]
    call_stmts = [s for s in entry.statements if isinstance(s, lir.CallAssign)]
    assert len(call_stmts) == 1


def test_call_assign_target_count_mismatch_rejected():
    """HCallAssign with wrong target count must be rejected."""
    a = HVar("a", UInt(8))
    call = HCall(name="divmod", args=(hconst(10), hconst(3)), return_ty=Void())
    stmt = HCallAssign(targets=(a,), call=call)  # only 1 target, need 2

    func = HFunction(
        name="bad_assign",
        params=(),
        return_ty=Void(),
        body=(stmt, HReturn(values=(hconst(0),))),
    )

    externs = (HExternFn("divmod", (HParam("x", UInt(8)), HParam("y", UInt(8))), (UInt(8), UInt(8))),)
    mod = HModule(functions=(func,), extern_functions=externs)
    with pytest.raises(HIRValidationError, match="expected 2 target"):
        validate_hmodule(mod)


def test_call_assign_type_mismatch_rejected():
    """HCallAssign target type must match return type."""
    a = HVar("a", UInt(16))  # 16-bit target for 8-bit return
    call = HCall(name="get_byte", args=(), return_ty=Void())
    stmt = HCallAssign(targets=(a,), call=call)

    func = simple_function("bad", [], [stmt, HReturn(values=(hconst(0),))], Void())

    externs = (HExternFn("get_byte", (), UInt(8)),)
    mod = HModule(functions=(func,), extern_functions=externs)
    with pytest.raises(HIRValidationError, match="type"):
        validate_hmodule(mod)


def test_call_assign_only_one_call_in_lir():
    """HCallAssign must produce exactly one CallAssign (not also an Assign)."""
    a = HVar("a", UInt(8))
    call = HCall(name="foo", args=(), return_ty=Void())
    stmt = HCallAssign(targets=(a,), call=call)
    func = simple_function("test", [], [stmt, HReturn(values=(hconst(0),))], Void())

    externs = (HExternFn("foo", (), (UInt(8),)),)
    mod = HModule(functions=(func,), extern_functions=externs)
    validate_hmodule(mod)
    lir_func = lower_function(func)

    call_assigns = [s for s in lir_func.blocks[0].statements if isinstance(s, lir.CallAssign)]
    regular_assigns = [s for s in lir_func.blocks[0].statements if isinstance(s, lir.Assign)]
    assert len(call_assigns) == 1
    assert not any(isinstance(s.value, lir.Call) for s in regular_assigns), \
        "Must not produce an Assign with a Call value"


# ======================================================================
# Part 5: Module-level containers
# ======================================================================

def test_module_duplicate_function_name_rejected():
    """Duplicate function name in module must be rejected."""
    fn = simple_function("dup", [], [HReturn(values=(hconst(0),))], UInt(8))
    mod = HModule(functions=(fn, fn))  # same function twice
    with pytest.raises(HIRValidationError, match="Duplicate"):
        validate_hmodule(mod)


def test_module_duplicate_extern_name_rejected():
    """Duplicate extern function name must be rejected."""
    efn = HExternFn("dup", (), Void())
    mod = HModule(extern_functions=(efn, efn))
    with pytest.raises(HIRValidationError, match="Duplicate"):
        validate_hmodule(mod)


def test_module_duplicate_symbol_name_rejected():
    """Duplicate external symbol name must be rejected."""
    sym = HExternalSymbol("SIG", UInt(8))
    mod = HModule(external_symbols=(sym, sym))
    with pytest.raises(HIRValidationError, match="Duplicate"):
        validate_hmodule(mod)


def test_module_call_to_undefined_rejected():
    """Call to undefined function in module must be rejected."""
    call = HCall(name="nonexistent", args=(), return_ty=Void())
    func = simple_function("test", [], [HExprStmt(expr=call)], Void())
    mod = HModule(functions=(func,))
    with pytest.raises(HIRValidationError, match="undefined"):
        validate_hmodule(mod)


def test_module_call_arity_mismatch_rejected():
    """Call with wrong argument count must be rejected."""
    call = HCall(name="foo", args=(hconst(1), hconst(2)), return_ty=Void())
    func = simple_function("test", [], [HExprStmt(expr=call)], Void())
    efn = HExternFn("foo", (HParam("x", UInt(8)),), Void())  # only 1 param
    mod = HModule(functions=(func,), extern_functions=(efn,))
    with pytest.raises(HIRValidationError, match="arity"):
        validate_hmodule(mod)


def test_module_lower_preserves_metadata():
    """lower_module preserves extern and symbol metadata."""
    int_ty = UInt(8)
    ext = HExternFn("ext_fn", (HParam("a", int_ty),), int_ty)
    sym = HExternalSymbol("LABEL", int_ty)
    fn = simple_function("main", [], [HReturn(values=(hconst(0),))], int_ty)
    mod = HModule(functions=(fn,), extern_functions=(ext,), external_symbols=(sym,))

    lir_mod = lower_module(mod)
    assert len(lir_mod.functions) == 1
    assert lir_mod.functions[0].name == "main"
    assert len(lir_mod.extern_functions) == 1
    assert len(lir_mod.external_symbols) == 1


def test_module_functions_independently_lowered():
    """Each function in module is independently lowered."""
    fn1 = simple_function("fn1", [], [HReturn(values=(hconst(1),))], UInt(8))
    fn2 = simple_function("fn2", [], [HReturn(values=(hconst(2),))], UInt(8))
    mod = HModule(functions=(fn1, fn2))
    lir_mod = lower_module(mod)
    assert len(lir_mod.functions) == 2
    names = {f.name for f in lir_mod.functions}
    assert names == {"fn1", "fn2"}


# ======================================================================
# Part 6: Module API regression tests
# ======================================================================

def test_regression_undeclared_call_in_hif_condition():
    """Undeclared HCall inside HIf condition (HCmp sub-expr) must be rejected."""
    val = u8("val")
    hidden_call = HCall(name="nonexistent", args=(val,), return_ty=UInt(8))
    cond = HCmp("lt", hidden_call, hconst(10))
    func = HFunction(
        name="bad_if",
        params=(HParam("val", UInt(8)),),
        return_ty=Void(),
        body=(HIf(cond=cond, then_body=(HReturn(values=(hconst(0),)),), else_body=(HReturn(values=(hconst(1),)),)),),
    )
    mod = HModule(functions=(func,))
    with pytest.raises(HIRValidationError, match="undefined"):
        validate_hmodule(mod)


def test_regression_nested_call_arg_undeclared():
    """Nested HCall inside a call argument referencing undeclared func."""
    inner = HCall(name="inner_undef", args=(hconst(1),), return_ty=UInt(8))
    outer = HCall(name="outer", args=(inner,), return_ty=UInt(8))
    func = simple_function("test", [], [HReturn(values=(outer,))], UInt(8))
    decl = HExternFn("outer", (HParam("x", UInt(8)),), UInt(8))
    mod = HModule(functions=(func,), extern_functions=(decl,))
    with pytest.raises(HIRValidationError, match="undefined"):
        validate_hmodule(mod)


def test_regression_call_arg_type_mismatch():
    """Call arg u16 passed to u8 param must be rejected."""
    val = HVar("val", UInt(16))
    call = HCall(name="need_u8", args=(val,), return_ty=Void())
    func = simple_function("test", [HParam("val", UInt(16))], [HExprStmt(expr=call)], Void())
    decl = HExternFn("need_u8", (HParam("x", UInt(8)),), Void())
    mod = HModule(functions=(func,), extern_functions=(decl,))
    with pytest.raises(HIRValidationError, match="type mismatch"):
        validate_hmodule(mod)


def test_regression_call_return_ty_mismatch():
    """HCall.return_ty says UInt(16) but declaration says UInt(8)."""
    call = HCall(name="get_byte", args=(), return_ty=UInt(16))
    func = simple_function("test", [], [HReturn(values=(call,))], UInt(16))
    decl = HExternFn("get_byte", (), UInt(8))
    mod = HModule(functions=(func,), extern_functions=(decl,))
    with pytest.raises(HIRValidationError, match="return type mismatch"):
        validate_hmodule(mod)


def test_regression_call_assign_signedness_mismatch():
    """HCallAssign target signedness differs from return type (same width)."""
    a = HVar("a", SInt(8))  # signed target for unsigned return
    call = HCall(name="get_byte", args=(), return_ty=Void())
    stmt = HCallAssign(targets=(a,), call=call)
    func = simple_function("bad", [], [stmt, HReturn(values=(hconst(0),))], Void())
    decl = HExternFn("get_byte", (), UInt(8))  # declares unsigned return
    mod = HModule(functions=(func,), extern_functions=(decl,))
    with pytest.raises(HIRValidationError, match="does not match return type"):
        validate_hmodule(mod)


def test_regression_function_symbol_name_conflict():
    """Function and external symbol with same name must be rejected."""
    fn = simple_function("SIG", [], [HReturn(values=(hconst(0),))], Void())
    sym = HExternalSymbol("SIG", UInt(8))
    mod = HModule(functions=(fn,), external_symbols=(sym,))
    with pytest.raises(HIRValidationError, match="Duplicate name"):
        validate_hmodule(mod)


def test_regression_symbol_addr_type_mismatch():
    """HSymbolAddr type differs from declaration address type."""
    sym = HExternalSymbol("BASE", address_ty=UInt(16))
    addr = HSymbolAddr(name="BASE", ty=UInt(8))  # 8-bit, decl says 16-bit
    func = simple_function("get_base", [], [HReturn(values=(addr,))], UInt(8))
    mod = HModule(functions=(func,), external_symbols=(sym,))
    with pytest.raises(HIRValidationError, match="does not match"):
        validate_hmodule(mod)


def test_regression_universal_rewrite_does_not_touch_symboladdr():
    """A universal capture/replacement rule must not rewrite SymbolAddr."""
    addr = HSymbolAddr(name="BASE", ty=UInt(16))
    result = u16("result")
    func = HFunction(
        name="get_base",
        params=(),
        return_ty=UInt(16),
        body=(
            # A BinOp that the universal pattern CAN match
            HAssign(target=result, value=HBinOp("add", hconst(1), hconst(2), UInt(16))),
            # A SymbolAddr that must NOT be rewritten
            HReturn(values=(addr,)),
        ),
    )
    # Universal pattern: capture anything, replace with Const(0)
    patterns = load_patterns_from_dicts([
        {
            "name": "capture_all",
            "match": {"capture": "x"},
            "replace": {"const": 0},
            "cost_delta": -100,
        },
    ])
    result_out = run_codegen_from_hir(func, ToyTarget(), patterns=patterns)
    # The pattern MUST have fired on the BinOp
    assert "capture_all" in result_out.applied_patterns, \
        "Universal pattern must fire on at least one node"
    # The SymbolAddr in the return must survive (not be rewritten to Const(0))
    assert "&BASE" in result_out.asm_text, \
        "SymbolAddr must survive universal rewrite"
    # The SymbolAddr &BASE must appear in the asm output
    # (Const would not contain &BASE)
    assert "&BASE" in result_out.asm_text


def test_regression_lower_module_metadata_is_lir_native():
    """lower_module must produce only LIR-native dataclass instances."""
    int_ty = UInt(8)
    ext = HExternFn("ext_fn", (HParam("a", int_ty),), int_ty)
    sym = HExternalSymbol("LABEL", int_ty)
    fn = simple_function("main", [], [HReturn(values=(hconst(0),))], int_ty)
    mod = HModule(functions=(fn,), extern_functions=(ext,), external_symbols=(sym,))
    lir_mod = lower_module(mod)

    # Extern functions must be ExternFunctionDecl, not HExternFn
    for decl in lir_mod.extern_functions:
        assert isinstance(decl, lir.ExternFunctionDecl), \
            f"Expected ExternFunctionDecl, got {type(decl).__name__}"
        assert hasattr(decl, "param_widths")
        assert hasattr(decl, "return_widths")
        assert hasattr(decl, "clobbers")

    # External symbols must be ExternalSymbolDecl, not HExternalSymbol
    for decl in lir_mod.external_symbols:
        assert isinstance(decl, lir.ExternalSymbolDecl), \
            f"Expected ExternalSymbolDecl, got {type(decl).__name__}"
        assert hasattr(decl, "address_width")
        assert hasattr(decl, "value_width")
        assert hasattr(decl, "volatile")

    # No HIR types in LIR module
    from rpkbin.codegen.hir import HExternFn as _HExternFn, \
        HExternalSymbol as _HExternalSymbol
    assert not any(isinstance(d, _HExternFn) for d in lir_mod.extern_functions)
    assert not any(isinstance(d, _HExternalSymbol) for d in lir_mod.external_symbols)


def test_regression_valid_single_and_multi_return_pass():
    """Valid single-return HCall and multi-return HCallAssign still pass."""
    # Single-return call
    result_var = u8("r")
    single_call = HCall(name="get_val", args=(hconst(1),), return_ty=UInt(8))
    fn1 = HFunction(
        name="use_single",
        params=(),
        return_ty=UInt(8),
        body=(
            HAssign(target=result_var, value=single_call),
            HReturn(values=(result_var,)),
        ),
    )
    decl1 = HExternFn("get_val", (HParam("x", UInt(8)),), UInt(8))

    # Multi-return call
    a = u8("a")
    b = u8("b")
    multi_call = HCall(name="get_pair", args=(), return_ty=Void())
    multi_stmt = HCallAssign(targets=(a, b), call=multi_call)
    fn2 = HFunction(
        name="use_multi",
        params=(),
        return_ty=Void(),
        body=(multi_stmt, HReturn(values=(hconst(0),))),
    )
    decl2 = HExternFn("get_pair", (), (UInt(8), UInt(8)))

    mod = HModule(functions=(fn1, fn2), extern_functions=(decl1, decl2))
    # Must not raise
    validate_hmodule(mod)

    # Lowering must also work
    lir_mod = lower_module(mod)
    assert len(lir_mod.functions) == 2


# ======================================================================
# Part 7: HExternalSymbol field validation
# ======================================================================

def test_external_symbol_address_void_rejected():
    """HExternalSymbol with address_ty=Void must be rejected."""
    sym = HExternalSymbol("BAD", address_ty=Void())
    mod = HModule(external_symbols=(sym,))
    with pytest.raises(HIRValidationError, match="address type must be UInt"):
        validate_hmodule(mod)


def test_external_symbol_address_sint_rejected():
    """HExternalSymbol with address_ty=SInt must be rejected."""
    sym = HExternalSymbol("BAD", address_ty=SInt(16))
    mod = HModule(external_symbols=(sym,))
    with pytest.raises(HIRValidationError, match="address type must be UInt"):
        validate_hmodule(mod)


def test_external_symbol_value_void_rejected():
    """HExternalSymbol with value_ty=Void must be rejected."""
    sym = HExternalSymbol("BAD", address_ty=UInt(16), value_ty=Void())
    mod = HModule(external_symbols=(sym,))
    with pytest.raises(HIRValidationError, match="value type must be UInt or SInt"):
        validate_hmodule(mod)


def test_external_symbol_volatile_without_value_rejected():
    """HExternalSymbol with volatile=True and value_ty=None must be rejected."""
    sym = HExternalSymbol("BAD", address_ty=UInt(16), volatile=True)
    mod = HModule(external_symbols=(sym,))
    with pytest.raises(HIRValidationError, match="volatile=True requires a non-None value_ty"):
        validate_hmodule(mod)


def test_external_symbol_uint_address_value_ok():
    """UInt address + UInt/SInt value is valid."""
    sym1 = HExternalSymbol("REG", address_ty=UInt(8), value_ty=UInt(8))
    sym2 = HExternalSymbol("SREG", address_ty=UInt(8), value_ty=SInt(8))
    sym3 = HExternalSymbol("VREG", address_ty=UInt(16), value_ty=UInt(8), volatile=True)
    mod = HModule(external_symbols=(sym1, sym2, sym3))
    validate_hmodule(mod)


def test_external_symbol_plain_label_ok():
    """Plain label: UInt address, value_ty=None, volatile=False is valid."""
    sym = HExternalSymbol("START", address_ty=UInt(16))
    mod = HModule(external_symbols=(sym,))
    validate_hmodule(mod)
