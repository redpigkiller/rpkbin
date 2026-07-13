# ir.py — backward-compatibility shim
#
# The canonical LIR definition has moved to ``lir.py``.  This file re-exports
# everything so that existing code importing from ``rpkbin.codegen.ir`` or
# ``rpkbin.codegen`` continues to work without modification.
#
# Do NOT add new definitions here.  Add them to ``lir.py`` instead.
from .lir import *  # noqa: F401, F403
from .lir import (  # noqa: F401  (explicit for static analysers)
    Assign,
    BinOp,
    BitOp,
    Block,
    BrCmp,
    BrIf,
    Call,
    CallAssign,
    CallStmt,
    Cmp,
    Const,
    Extend,
    Expr,
    Fragment,
    FragmentBinding,
    FragmentExit,
    FullExpr,
    Function,
    Jump,
    MemLoad,
    MemStore,
    MultiReturn,
    Return,
    SourceLoc,
    SpillSlot,
    Stmt,
    Terminator,
    VReg,
    Var,
    binop,
    cmp,
    const,
    format_expr,
    format_fragment,
    format_function,
    validate_fragment,
    validate_function,
    var,
    walk_expr,
)
