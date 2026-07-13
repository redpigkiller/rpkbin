"""High-level IR (HIR) — the compiler-frontend-facing intermediate representation.

HIR is the *input* to the codegen package.  It is produced by a DSL parser
(which lives outside this package) and consumed by:

1. ``hir_validate.py`` — checks structural and type-level correctness.
2. ``lower.py``        — translates HIR to LIR.

Design principles
-----------------
* HIR nodes carry full type information (``HType``).  LIR nodes only carry
  bit-widths.  The type information is stripped during lowering.
* Control flow in HIR is *structured* (if/while/for).  Lowering flattens it
  into basic blocks with explicit labels and branch terminators.
* HIR never interprets physical-register or MCU-specific names.  Register
  hints are represented as plain Python strings so that the
  framework has zero MCU knowledge.

See ``docs/codegen/status_zh.md`` for supported and deferred capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from .lir import SourceLoc  # re-use the same SourceLoc definition


# ---------------------------------------------------------------------------
# Type system
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UInt:
    """Unsigned integer type.  Lowering currently supports width 8 or 16."""
    width: int

    def __repr__(self) -> str:
        return f"u{self.width}"


@dataclass(frozen=True)
class SInt:
    """Signed integer type.  Lowering currently supports width 8 or 16."""
    width: int

    def __repr__(self) -> str:
        return f"s{self.width}"


@dataclass(frozen=True)
class Void:
    """Unit / no-value type.  Used for functions that do not return a value."""

    def __repr__(self) -> str:
        return "void"


HType = Union[UInt, SInt, Void]


# ---------------------------------------------------------------------------
# Expression nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HConst:
    """Compile-time integer constant.

    ``value`` is a Python int.  ``ty`` carries the declared type so that the
    validator and lowerer know the bit-width without additional inference.
    """
    value: int
    ty: HType


@dataclass(frozen=True)
class HVar:
    """A named variable (local or parameter).

    ``reg_hint`` is an optional physical-register preference string such as
    ``"g0"``.  It maps directly to ``VReg.hint`` after lowering.  The
    framework never validates the hint against a real register table unless
    a ``RegisterModel`` is provided to the HIR validator.
    """
    name: str
    ty: HType
    reg_hint: str | None = None


@dataclass(frozen=True)
class HBinOp:
    """Binary arithmetic or bitwise operation.

    Supported ``op`` values:
    ``add``, ``sub``, ``mul``, ``and``, ``or``, ``xor``,
    ``shl``, ``shr``, ``ror``, ``rol``.

    The validator checks that shift amounts are compile-time constants
    (``HConst``), not variables.
    """
    op: str
    left: "HExpr"
    right: "HExpr"
    ty: HType


@dataclass(frozen=True)
class HCmp:
    """Comparison expression.  Only valid in condition positions (HIf, HWhile,
    HFor, HPoll).  The validator rejects it elsewhere.

    Supported ``op`` values: ``eq``, ``ne``, ``lt``, ``le``, ``gt``, ``ge``.
    """
    op: str
    left: "HExpr"
    right: "HExpr"
    signed: bool = False


@dataclass(frozen=True)
class HBitTest:
    """Test a single bit of a variable.  Only valid in condition positions.

    ``bit_idx`` is a compile-time integer (not ``HConst``).
    The result is truthy when the specified bit of ``var`` is set.

    Lowered to ``lir.BitOp("test")`` in condition positions.
    """
    var: HVar
    bit_idx: int


@dataclass(frozen=True)
class HLogical:
    """Short-circuit logical AND / OR.  Only valid in condition positions.

    ``op`` is ``"and"`` or ``"or"``.  Lowering expands this into a branch
    chain (lazy evaluation semantics).
    """
    op: str
    left: "HCondExpr"
    right: "HCondExpr"


@dataclass(frozen=True)
class HNot:
    """Logical NOT.  Only valid in condition positions."""
    expr: "HCondExpr"


# Condition expressions — only usable as the ``cond`` of control-flow nodes.
HCondExpr = Union[HCmp, HBitTest, HLogical, HNot]


@dataclass(frozen=True)
class HCast:
    """Type cast / bit-field extraction helper.

    ``kind`` selects the cast semantics:

    * ``"low_byte"``     — keep the low 8 bits of a u16 (→ ``and 0xFF``).
    * ``"high_byte"``    — keep the high 8 bits of a u16 (→ ``shr 8``).
    * ``"u16_from"``     — zero-extend a u8 to u16; lowers to ``lir.Extend("zext", ...)``.
    * ``"s16_from"``     — sign-extend a s8 to s16; lowers to ``lir.Extend("sext", ...)``.
    * ``"as_signed"``    — reinterpret bits as signed (no-op in LIR).
    * ``"as_unsigned"``  — reinterpret bits as unsigned (no-op in LIR).

    ``to_ty`` is the result type.
    """
    kind: str
    expr: "HExpr"
    to_ty: HType


@dataclass(frozen=True)
class HExtract:
    """Extract a contiguous bit range from an expression.

    ``msb`` and ``lsb`` are compile-time constants (bit indices, inclusive).
    ``ty`` is the storage type for the extracted field, so its width is the
    destination storage width rather than the exact field width.  The field
    width must satisfy ``1 <= msb - lsb + 1 <= ty.width``.

    Lowered to ``(expr >> lsb) & mask`` in storage-width LIR with zero-
    extension semantics.
    """
    expr: "HExpr"
    msb: int
    lsb: int
    ty: HType


@dataclass(frozen=True)
class HInsert:
    """Return a new value with bits ``[msb:lsb]`` replaced by ``value``.

    ``dst`` is the original value; ``value`` is the replacement.
    ``msb`` and ``lsb`` are compile-time constants.

    Lowered with masks, shifts, and bitwise-or.
    """
    dst: "HExpr"
    value: "HExpr"
    msb: int
    lsb: int


@dataclass(frozen=True)
class HConcat:
    """Concatenate two values: ``result = (hi << width(lo)) | lo``.

    ``ty.width`` must equal ``width(hi) + width(lo)``; the validator checks
    this invariant.
    """
    hi: "HExpr"
    lo: "HExpr"
    ty: HType


@dataclass(frozen=True)
class HLoad:
    """Volatile memory read: ``*ptr_expr``.

    All loads are treated as volatile (no caching or reordering).
    ``ptr_expr`` must evaluate to a pointer or address expression.
    ``ty`` is the type of the loaded value.

    Lowered to ``lir.MemLoad`` with ``volatile=True``.
    """
    ptr_expr: "HExpr"
    ty: HType


@dataclass(frozen=True)
class HCall:
    """Function call expression.

    ``name`` is the callee name (string).  ``args`` is the list of argument
    expressions.  ``return_ty`` is the declared return type.

    Contract
    --------
    * When used as a **value expression** (standalone ``HCall``), ``return_ty``
      must be a single ``HType`` (not a tuple).  The callee must declare
      exactly one return value.  ``return_ty`` must match the declaration.
    * When used inside an ``HCallAssign``, ``return_ty`` must be ``Void()``.
      The true return signature is taken from the callee's declaration; the
      targets in ``HCallAssign`` supply the types.

    If any argument carries a ``reg_hint`` and the corresponding parameter
    also declares a hint, the validator checks they do not conflict.
    """
    name: str
    args: tuple
    return_ty: HType | tuple
    arg_regs: tuple[str | None, ...] = ()
    return_regs: tuple[str | None, ...] = ()
    clobbers: tuple[str, ...] | None = ()


# ---------------------------------------------------------------------------
# External / linker symbols (must be defined before HExpr)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HSymbolAddr:
    """Address-of a linker/global symbol, used as an expression.

    ``name`` must match a declared ``HExternalSymbol.name``.
    ``ty`` is the address type (must match ``HExternalSymbol.address_ty``).

    Lowered to ``lir.SymbolAddr(name, width)``.
    """
    name: str
    ty: HType


# Full expression union
HExpr = Union[
    HConst, HVar, HBinOp, HCmp, HBitTest, HLogical, HNot,
    HCast, HExtract, HInsert, HConcat, HLoad, HCall, HSymbolAddr,
]


# ---------------------------------------------------------------------------
# Statement nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HAssign:
    """Assign an expression value to a variable.

    The validator requires the target and value types to match exactly.
    """
    target: HVar
    value: HExpr
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HBitSet:
    """Set a single bit of a variable to 0 or 1.

    ``bit_idx`` is a compile-time integer.  ``value`` must be 0 or 1.

    Lowered to ``lir.BitOp("set")`` (value=1) or
    ``lir.BitOp("clr")`` (value=0).
    """
    var: HVar
    bit_idx: int
    value: int  # 0 or 1
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HStore:
    """Volatile memory write: ``*ptr_expr = value_expr``.

    All stores are treated as volatile.

    Lowered to ``lir.MemStore`` with ``volatile=True``.
    """
    ptr_expr: HExpr
    value_expr: HExpr
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HIf:
    """Structured if / else-if / else statement.

    ``cond`` is the primary condition (an ``HCondExpr``).
    ``then_body`` is the list of statements executed when ``cond`` is true.
    ``elif_branches`` is a (possibly empty) sequence of ``(cond, body)`` pairs.
    ``else_body`` is a (possibly empty) list of statements for the final else.

    Lowering produces one block per branch plus a merge block.
    """
    cond: HCondExpr
    then_body: tuple  # tuple[HStmt, ...]
    elif_branches: tuple = field(default_factory=tuple)  # tuple[(HCondExpr, tuple[HStmt, ...]), ...]
    else_body: tuple = field(default_factory=tuple)      # tuple[HStmt, ...]
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HWhile:
    """While loop.

    ``cond`` is re-evaluated before each iteration.
    HBreak inside body exits the loop; HContinue re-tests the condition.
    """
    cond: HCondExpr
    body: tuple  # tuple[HStmt, ...]
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HFor:
    """Fixed-count for loop.

    ``var`` is the loop counter variable.  ``init`` is its initial value
    (``HConst``).  ``bound`` is the exclusive upper bound (``HConst``).
    ``body`` is the list of statements, and may read ``var`` but must not
    write it.

    Lowering materializes ``var`` from ``init``, then emits a counter block,
    body block, dedicated step block, and exit block.  The step block
    increments ``var``, decrements the internal counter, and jumps back to the
    test block so targets can still match ``djnz``-style shapes.
    """
    var: HVar
    init: HConst
    bound: HConst
    body: tuple  # tuple[HStmt, ...]
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HPoll:
    """Blocking poll loop: execute body at least once,
    then repeat until cond is true.

    HBreak inside body exits the loop immediately.
    HContinue jumps to the condition-check point (cond is re-evaluated
    before the next iteration), matching the ``do-while`` continue
    contract.
    """
    cond: HCondExpr
    body: tuple  # tuple[HStmt, ...]
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HBreak:
    """Break out of the nearest enclosing loop.

    Valid inside HWhile, HPoll, and HFor.
    """
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HContinue:
    """Continue to the next iteration of the nearest enclosing loop
    Valid in HWhile, HPoll, and HFor.

    For HWhile: jumps to the condition test point.
    For HPoll: jumps to the condition-check point (poll_check),
    so the condition is re-evaluated before the next iteration.
    For HFor: jumps to the dedicated step block.
    """
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HReturn:
    """Return from the current function.

    ``values`` is a tuple of return expressions.  A single-element tuple
    produces a ``Return`` in LIR; a multi-element tuple produces a
    ``MultiReturn``.
    """
    values: tuple  # tuple[HExpr, ...]
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HInlineAsm:
    """Opaque inline assembly block.

    ``text`` is opaque to validation and effect analysis.  Lowering passes it
    to the pseudo-ASM layer verbatim.
    """
    text: str
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HCallAssign:
    """Multi-return function call with explicit target tuple.

    ``targets`` is a tuple of ``HVar | None``, one per return value.
    ``None`` means the corresponding return value is discarded.
    The number and types of targets must match the callee's declaration.

    Contract
    --------
    *``call.return_ty`` must be ``Void()``.  The true return signature is
    derived from the callee's declaration, not from ``call.return_ty``.
    * The validator checks each non-``None`` target's type against the
      corresponding declaration return type using structural equality
      (both signedness and width).
    * A single ``HCallAssign`` produces exactly one ``lir.CallAssign``
      statement — the call is never duplicated or reordered.
    """
    targets: tuple[HVar | None, ...]
    call: HCall
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HExprStmt:
    """A function call used as a statement (return value discarded).

    ``expr`` must be an ``HCall``.
    """
    expr: HCall
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HExit:
    """Fragment exit point (terminator).

    ``HExit`` is a terminator statement that marks the end of a control-flow
    path inside an ``HFragment``.  It carries no jump target, opcode, or
    target-specific payload — those are emitted as a preceding
    ``HInlineAsm`` by the frontend if needed.

    ``HExit`` is **not** allowed inside ``HFunction`` — the function-level
    validator rejects it.
    """
    loc: SourceLoc | None = None


# ---------------------------------------------------------------------------
# Fragment nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HFragmentBinding:
    """A binding between a logical name and a physical register on a fragment
    interface.

    ``name`` is the logical variable name visible to the fragment body.
    ``ty`` is the HIR type (must be ``UInt`` or ``SInt``, not ``Void``).
    ``reg`` is the physical register name this binding maps to.
    ``mode`` is ``"in"``, ``"out"``, or ``"inout"``.
    """
    name: str
    ty: HType
    reg: str
    mode: str  # Literal["in", "out", "inout"]
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HFragment:
    """An MCU-agnostic fragment definition.

    A fragment is a straight-line (acyclic) code block with a well-defined
    interface of ``bindings`` that map logical names to physical registers.
    The body may contain structured ``HIf`` but **must not** contain
    ``HReturn``, ``HWhile``, ``HPoll``, ``HFor``, ``HBreak``, or
    ``HContinue``.  Every reachable control-flow path must end with
    ``HExit``.

    ``scratch_regs`` lists physical registers the fragment may freely use
    without saving/restoring (caller-saved / killable registers).
    """
    name: str
    bindings: tuple = ()  # tuple[HFragmentBinding, ...]
    scratch_regs: tuple = ()  # tuple[str, ...]
    body: tuple = ()          # tuple[HStmt, ...]
    loc: SourceLoc | None = None


# Full statement union
HStmt = Union[
    HAssign, HBitSet, HStore, HIf, HWhile, HFor, HPoll,
    HBreak, HContinue, HReturn, HInlineAsm, HExprStmt, HCallAssign,
    HExit,
]


# ---------------------------------------------------------------------------
# Function nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HParam:
    """A function parameter declaration.

    ``reg_hint`` mirrors ``HVar.reg_hint``: an optional physical-register
    preference for the caller to honour when passing this argument.
    """
    name: str
    ty: HType
    reg_hint: str | None = None


@dataclass(frozen=True)
class HFunction:
    """A function definition in HIR.

    Fields
    ------
    name : str
        Function name (used as the label in LIR / pseudo ASM).
    params : tuple[HParam, ...]
        Formal parameters in declaration order.
    return_ty : HType | tuple[HType, ...]
        Return type.  A tuple means the function returns multiple values.
    return_regs : tuple[str, ...]
        Physical-register hints for each return value (parallel to
        ``return_ty``).  Empty tuple if no hints are declared.
    body : tuple[HStmt, ...]
        The function body as a sequence of HIR statements.
    is_inline : bool
        If ``True``, the lowerer will inline call sites instead of emitting
        a ``call`` instruction.
    loc : SourceLoc | None
        Source location of the function definition (optional).
    """
    name: str
    params: tuple  # tuple[HParam, ...]
    return_ty: HType | tuple
    return_regs: tuple = field(default_factory=tuple)  # tuple[str, ...]
    body: tuple = field(default_factory=tuple)          # tuple[HStmt, ...]
    is_inline: bool = False
    loc: SourceLoc | None = None


@dataclass(frozen=True)
class HExternFn:
    """An extern function declaration (no body, not lowered).

    The lowerer treats calls to ``HExternFn`` as opaque black-box calls;
    it emits a ``Call`` LIR node without inlining.

    ``clobbers`` lists register names that this function side-effects
    (e.g. ``("g0", "g1")``).  The names are opaque strings; the generic
    package does not know any MCU register set.  When a ``RegisterModel``
    is provided to the validator, each name is checked for validity.

    ``loc`` is the optional source location of the declaration.
    """
    name: str
    params: tuple   # tuple[HParam, ...]
    return_ty: HType | tuple
    return_regs: tuple = field(default_factory=tuple)
    clobbers: tuple[str, ...] = ()
    loc: SourceLoc | None = None


# ---------------------------------------------------------------------------
# External / linker symbols (declaration node)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HExternalSymbol:
    """A global / linker symbol visible to the program.

    ``name`` is the linker-level symbol name (e.g. ``"_uart_base"``).
    ``address_ty`` is the type of the address expression (typically an
    unsigned integer wide enough to hold the address).
    ``value_ty``, when set, declares the type of the value stored at that
    address (for *volatile data* symbols).  ``None`` means the symbol is a
    plain label (``extern const`` or linker section start/end).
    ``volatile`` marks a data symbol as volatile (implies the program uses
    ``HLoad`` / ``HStore`` to access it).
    ``loc`` is the optional source location.
    """
    name: str
    address_ty: HType
    value_ty: HType | None = None
    volatile: bool = False
    loc: SourceLoc | None = None


# ---------------------------------------------------------------------------
# Module-level container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HModule:
    """A compilation unit consisting of functions, extern declarations,
    and global/linker symbols.

    ``functions`` is the list of function definitions (``HFunction``).
    ``extern_functions`` is the list of extern function declarations
    (``HExternFn``).  ``external_symbols`` is the list of global/linker
    symbols (``HExternalSymbol``).

    All names across these three lists must be unique.  Fragment names share
    the same namespace as functions, extern functions, and external symbols.
    """
    functions: tuple[HFunction, ...] = ()
    extern_functions: tuple[HExternFn, ...] = ()
    external_symbols: tuple[HExternalSymbol, ...] = ()
    fragments: tuple[HFragment, ...] = ()


# ---------------------------------------------------------------------------
# Builder helpers (convenience for tests and documentation examples)
# ---------------------------------------------------------------------------

def u8(name: str, reg_hint: str | None = None) -> HVar:
    """Create an 8-bit unsigned variable."""
    return HVar(name=name, ty=UInt(8), reg_hint=reg_hint)


def u16(name: str, reg_hint: str | None = None) -> HVar:
    """Create a 16-bit unsigned variable."""
    return HVar(name=name, ty=UInt(16), reg_hint=reg_hint)


def s8(name: str, reg_hint: str | None = None) -> HVar:
    """Create an 8-bit signed variable."""
    return HVar(name=name, ty=SInt(8), reg_hint=reg_hint)


def s16(name: str, reg_hint: str | None = None) -> HVar:
    """Create a 16-bit signed variable."""
    return HVar(name=name, ty=SInt(16), reg_hint=reg_hint)


def hconst(value: int, width: int = 8, signed: bool = False) -> HConst:
    """Create a typed constant node."""
    ty: HType = SInt(width) if signed else UInt(width)
    return HConst(value=value, ty=ty)


def simple_function(
    name: str,
    params: list[HParam],
    body: list[HStmt],
    return_ty: HType = Void(),
) -> HFunction:
    """Create a simple non-inline function with no return-register hints."""
    return HFunction(
        name=name,
        params=tuple(params),
        return_ty=return_ty,
        return_regs=(),
        body=tuple(body),
        is_inline=False,
    )
