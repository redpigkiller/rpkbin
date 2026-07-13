"""Small compiler backend laboratory — public API.

This package is MCU-agnostic.  Real targets supply their own Target /
RegisterModel implementation and inject it into the pipeline.

Quick start (HIR → pseudo ASM)
-------------------------------
::

    from rpkbin.codegen import (
        run_codegen_from_hir,
        HFunction, HParam, HReturn, HAssign,
        HBinOp, HVar, HConst,
        UInt, Void,
    )
    from rpkbin.codegen.toy_target import ToyTarget

    func = HFunction(
        name=\"add_one\",
        params=(HParam(\"x\", UInt(8)),),
        return_ty=UInt(8),
        body=(HReturn(values=(HBinOp(\"add\", HVar(\"x\", UInt(8)), HConst(1, UInt(8)), UInt(8)),)),),
    )
    result = run_codegen_from_hir(func, ToyTarget())
    print(result.asm_text)
"""

# ---------------------------------------------------------------------------
# LIR nodes (original API — unchanged)
# ---------------------------------------------------------------------------
from .ir import (
    Assign,
    BinOp,
    Block,
    BrIf,
    CallStmt,
    Cmp,
    Const,
    Extend,
    Function,
    FullExpr,
    Jump,
    Return,
    Var,
)

# ---------------------------------------------------------------------------
# HIR type system
# ---------------------------------------------------------------------------
from .hir import (
    SInt,
    UInt,
    Void,
)

# ---------------------------------------------------------------------------
# HIR expression nodes
# ---------------------------------------------------------------------------
from .hir import (
    HBinOp,
    HBitTest,
    HCall,
    HCast,
    HCmp,
    HConcat,
    HConst,
    HExtract,
    HInsert,
    HLoad,
    HLogical,
    HNot,
    HVar,
)

# ---------------------------------------------------------------------------
# HIR statement nodes
# ---------------------------------------------------------------------------
from .hir import (
    HAssign,
    HBitSet,
    HBreak,
    HCallAssign,
    HContinue,
    HExit,
    HExprStmt,
    HFor,
    HIf,
    HInlineAsm,
    HPoll,
    HReturn,
    HStore,
    HWhile,
)

# ---------------------------------------------------------------------------
# HIR function nodes
# ---------------------------------------------------------------------------
from .hir import (
    HExternFn,
    HFunction,
    HParam,
)

# ---------------------------------------------------------------------------
# HIR fragment nodes
# ---------------------------------------------------------------------------
from .hir import (
    HFragment,
    HFragmentBinding,
)

# ---------------------------------------------------------------------------
# HIR module-level
# ---------------------------------------------------------------------------
from .hir import (
    HExternalSymbol,
    HModule,
    HSymbolAddr,
)

# ---------------------------------------------------------------------------
# Target protocols
# ---------------------------------------------------------------------------
from .target import FragmentTarget

# ---------------------------------------------------------------------------
# HIR validator API
# ---------------------------------------------------------------------------
from .hir_validate import (
    HIRValidationError,
    validate_extern_fn,
    validate_hfragment,
    validate_hfunction,
    validate_hmodule,
)

# ---------------------------------------------------------------------------
# LIR module
# ---------------------------------------------------------------------------
from .lir import (
    Fragment,
    FragmentBinding,
    FragmentExit,
    Module,
    format_fragment,
    validate_fragment,
)

# ---------------------------------------------------------------------------
# Lowering API
# ---------------------------------------------------------------------------
from .lower import (
    lower_fragment,
    lower_function,
    lower_module,
)

# ---------------------------------------------------------------------------
# HIR builder helpers
# ---------------------------------------------------------------------------
from .hir import (
    hconst,
    s8,
    s16,
    simple_function,
    u8,
    u16,
)

# ---------------------------------------------------------------------------
# Pipeline API
# ---------------------------------------------------------------------------
from .pipeline import (
    CodegenResult,
    FragmentCodegenResult,
    run_codegen,
    run_codegen_from_fragment,
    run_codegen_from_hir,
)

# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------
__all__ = [
    # LIR (original)
    "Assign", "BinOp", "Block", "BrIf", "Cmp", "Const",
    "CallStmt", "Extend", "FullExpr", "Function", "Jump", "Return", "Var",
    "Module",  # LIR module
    # HIR types
    "UInt", "SInt", "Void",
    # HIR expressions
    "HBinOp", "HBitTest", "HCall", "HCast", "HCmp", "HConcat",
    "HConst", "HExtract", "HInsert", "HLoad", "HLogical", "HNot",
    "HSymbolAddr", "HVar",
    # HIR statements
    "HAssign", "HBitSet", "HBreak", "HCallAssign", "HContinue", "HExit",
    "HExprStmt", "HFor", "HIf", "HInlineAsm", "HPoll", "HReturn", "HStore",
    "HWhile",
    # HIR fragment
    "HFragment", "HFragmentBinding",
    # HIR functions
    "HExternFn", "HFunction", "HParam",
    # HIR module-level
    "HExternalSymbol", "HModule",
    # HIR builders
    "hconst", "s8", "s16", "simple_function", "u8", "u16",
    # HIR validator
    "FragmentTarget",
    "HIRValidationError", "validate_extern_fn", "validate_hfragment",
    "validate_hfunction", "validate_hmodule",
    # LIR fragment nodes
    "Fragment", "FragmentBinding", "FragmentExit",
    "format_fragment", "validate_fragment",
    # Lowering
    "lower_fragment", "lower_function", "lower_module",
    # Pipeline
    "CodegenResult", "FragmentCodegenResult",
    "run_codegen", "run_codegen_from_fragment", "run_codegen_from_hir",
]
