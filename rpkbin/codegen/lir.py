"""Low-level IR (LIR) used by the codegen pipeline.

LIR is the canonical intermediate representation between HIR lowering and
pseudo ASM emission.  It retains an *expression-tree* shape
(not SSA), which keeps it compatible with the existing rewrite.py /
matcher.py / toy_target.py without any changes to those modules.

Key design decisions
--------------------
* ``ir.py`` is now a thin shim: ``from .lir import *``.  All downstream
  imports of ``ir`` still work unchanged.
* ``VReg`` carries an optional opaque physical-register *hint* (e.g. ``"g0"``).
  The hint is advisory; the optional register allocator decides the final
  assignment.
* ``SpillSlot`` is retained as a legacy experimental API.  Production
  register allocation currently fails closed instead of spilling.
* ``BrCmp`` combines a compare and a conditional branch into one terminator,
  matching MCU instructions such as ``cjne`` / ``djnz``.
* ``MultiReturn`` expresses functions that return multiple values via pinned
  VRegs, avoiding the need for a single aggregated return expression.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Union


# ---------------------------------------------------------------------------
# Source location
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceLoc:
    file: str
    line: int
    column: int = 1


# ---------------------------------------------------------------------------
# Core expression nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Const:
    value: int
    width: int = 8


@dataclass(frozen=True)
class Var:
    name: str
    width: int = 8


@dataclass(frozen=True)
class BinOp:
    op: str
    left: "FullExpr"
    right: "FullExpr"
    width: int = 8


@dataclass(frozen=True)
class Cmp:
    op: str
    left: "FullExpr"
    right: "FullExpr"
    width: int = 1
    signed: bool = False


# ``Expr`` is the *legacy* alias kept for backward compatibility with
# code that predates VReg / MemLoad / BitOp / InlineAsmExpr.
# New code should use ``FullExpr`` which covers the full expression set.
Expr = Union[Const, Var, BinOp, Cmp]


# ---------------------------------------------------------------------------
# Extended expression nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Extend:
    """Widen an integer value while preserving explicit extension semantics."""

    kind: str  # "zext" | "sext"
    value: "FullExpr"
    width: int = 8

@dataclass(frozen=True)
class VReg:
    """Virtual register produced by HIR lowering.

    ``hint`` is an optional physical-register name (e.g. ``"g0"``).  The
    register allocator treats it as a hard constraint when the active
    ``RegisterModel.fixed_register_hints()`` says so; otherwise it remains a
    best-effort preference.

    ``width`` matches the lowered HIR type width.
    """
    name: str
    width: int = 8
    hint: str | None = None


@dataclass(frozen=True)
class Call:
    """LIR function-call expression node.

    Produced by the lowering pass when an ``HCall`` appears as an expression
    (i.e. its return value is used).  When an ``HCall`` is used as a void
    statement (``HExprStmt``), the lowerer emits a ``CallStmt`` instead.

    ``name``  — callee name string (target package resolves it to an address).
    ``args``  — tuple of LIR expression nodes passed as arguments.

    Note: ``Call`` is *not* included in the legacy ``Expr`` alias; use
    ``FullExpr`` instead, which covers the complete expression set.
    """
    name: str
    args: tuple  # tuple[FullExpr, ...]
    arg_regs: tuple[str | None, ...] = ()
    return_regs: tuple[str | None, ...] = ()
    clobbers: tuple[str, ...] | None = ()


@dataclass(frozen=True)
class SymbolAddr:
    """Address-of a linker/global symbol (opaque string).

    ``name`` is the linker symbol name (e.g. ``"_uart_base"``).
    ``width`` is the bit-width of the address.

    Produced by ``lower.py`` when lowering ``HSymbolAddr``.
    The target's instruction selector must emit a recognisable pseudo
    operand (e.g. ``&symbol``).  This package does *not* resolve
    addresses or produce relocations — that is the job of the private
    target / linker package.
    """
    name: str
    width: int = 8


@dataclass(frozen=True)
class InlineAsmExpr:
    """Raw inline-assembly passthrough expression.

    Produced by the lowering pass for ``HInlineAsm`` statements.  The
    ``text`` field contains the original assembly text exactly as written
    by the user; it must be emitted verbatim by the target's instruction
    selector without any wrapping or prefixing.

    Assigned to a ``Var(\"__asm__\", 0)`` target so that it can travel
    through the block/statement structures unchanged.
    """
    text: str


# ---------------------------------------------------------------------------
# Statement nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CallStmt:
    """Side-effecting void call statement (no return value captured).

    Produced by the lowering pass when an ``HExprStmt(HCall(...,
    return_ty=Void()))`` is encountered.  The call is executed for its
    side effects only; no result register is allocated or referenced.

    ``call`` carries the full ``Call`` expression including args, arg_regs,
    and clobbers so that the register allocator and instruction selector
    can handle them correctly.

    All passes treat this as the statement form of a void call.
    """
    call: Call


@dataclass(frozen=True)
class CallAssign:
    """Side-effecting call statement with explicit destination targets.

    ``targets`` is a tuple of ``Var | VReg | None``, one per return value.
    ``None`` means discard that return slot.
    The length of ``targets`` must equal the callee's arity (number of
    return values).  A single ``CallAssign`` represents exactly one call
    — the call is never duplicated or reordered by any pass.

    Produced by lowering an ``HCallAssign``.  Backward-compatible:
    single-return calls continue to use the existing ``Assign(Call(...))``
    pattern.

    ``abi_return_regs`` is optional callee ABI metadata: one physical register
    name per return slot, preserving slot order even when the corresponding
    ``targets`` entry is ``None``.  It is separate from ``VReg.hint``:
    ``hint`` is still the destination allocation preference.
    """
    targets: tuple[Var | VReg | None, ...]
    call: Call
    abi_return_regs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Assign:
    target: Var
    # value may be any expression including Call and InlineAsmExpr;
    # use FullExpr (defined below) for the complete union.
    value: "FullExpr"


# ---------------------------------------------------------------------------
# Memory and bit-operation nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemLoad:
    """Volatile memory read.

    ``addr`` is the address expression (a Var holding a pointer, or a Const
    for a fixed address).  All loads are volatile — the rewrite and isel
    passes must not reorder or eliminate them.
    ``width`` is the bit-width of the loaded value.
    """
    addr: "FullExpr"
    width: int = 8
    volatile: bool = True


@dataclass(frozen=True)
class MemStore:
    """Volatile memory write.

    ``addr`` is the destination address expression.
    ``value`` is the expression to store.
    All stores are volatile — the rewrite and isel passes must not
    reorder or eliminate them.
    """
    addr: "FullExpr"
    value: "FullExpr"
    volatile: bool = True


@dataclass(frozen=True)
class BitOp:
    """Single-bit read or write on a variable.

    ``kind``  : ``"set"`` (write 1), ``"clr"`` (write 0), ``"test"`` (read).
    ``var``   : the target Var.
    ``bit_idx``: compile-time integer bit index (0 = LSB).

    ``"test"`` returns a 1-bit boolean (use as BrIf condition).
    ``"set"`` / ``"clr"`` are used as Stmt (side-effecting, no return value).
    """
    kind: str    # "set" | "clr" | "test"
    var: "FullExpr"
    bit_idx: int


# ---------------------------------------------------------------------------
# FullExpr — complete expression union
# ---------------------------------------------------------------------------

# FullExpr covers every expression node that can appear in a LIR block.
# Use this for new code; the legacy ``Expr`` alias (Const/Var/BinOp/Cmp only)
# is kept for backward compatibility.
FullExpr = Union[
    Const, Var, VReg, BinOp, Cmp,   # core (same as legacy Expr + VReg)
    Extend,                           # explicit zero/sign extension
    Call,                             # function-call expression
    MemLoad,                          # volatile memory read
    BitOp,                            # single-bit test
    SymbolAddr,                       # linker/global symbol address
    InlineAsmExpr,                    # raw inline-asm passthrough
]


# Stmt includes all side-effecting statement forms:
# - Assign: expression result into a named target
# - CallStmt: void call (no return captured)
# - CallAssign: multi-return call with explicit targets
# - MemStore / BitOp: volatile memory / bit side-effects
Stmt = Union[Assign, CallStmt, CallAssign, MemStore, BitOp]


# ---------------------------------------------------------------------------
# Terminator nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrIf:
    cond: FullExpr  # typically Cmp or BitOp("test")
    true_label: str
    false_label: str


@dataclass(frozen=True)
class Jump:
    label: str


@dataclass(frozen=True)
class Return:
    value: FullExpr


# ---------------------------------------------------------------------------
# Additional terminator nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrCmp:
    """Compare-and-branch terminator.

    Combines a comparison with a conditional branch in a single terminator,
    matching MCU instructions such as ``cjne`` (compare-jump-if-not-equal)
    or ``djnz`` (decrement-jump-if-not-zero).

    ``left`` and ``right`` are LIR expressions.  The branch jumps to
    ``true_label`` when the condition holds, ``false_label`` otherwise.
    """
    op: str
    left: FullExpr
    right: FullExpr
    true_label: str
    false_label: str


@dataclass(frozen=True)
class MultiReturn:
    """Return multiple values, each pinned to a VReg.

    Used when an ``HFunction`` has ``return_ty`` that is a tuple.  The Target
    package is responsible for emitting the correct sequence of move/store
    instructions to place each value in its designated physical register.

    ``values`` is a tuple of LIR expressions, one per return slot.
    """
    values: tuple


@dataclass(frozen=True)
class FragmentExit:
    """Fragment path ends without Return/ret."""


Terminator = Union[BrIf, BrCmp, Jump, Return, MultiReturn, FragmentExit]


# ---------------------------------------------------------------------------
# Spill slots
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpillSlot:
    """Legacy experimental spill location.

    ``id`` uniquely identifies this slot within a function.

    The production allocator does not consume spill slots because the former
    pre-isel lowering could overwrite live registers.  This type remains only
    to avoid an unnecessary API break while a correct machine-level contract
    is designed.
    """
    id: int
    address: int | None = None


# ---------------------------------------------------------------------------
# Blocks and functions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    label: str
    statements: Sequence[Stmt]
    terminator: Terminator


@dataclass(frozen=True)
class Function:
    name: str
    params: Sequence[Var]
    blocks: Sequence[Block]


# ---------------------------------------------------------------------------
# LIR-native declaration dataclasses (no HIR imports)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExternFunctionDecl:
    """LIR-native declaration of an external function.

    Carries metadata needed by downstream passes (instruction selection,
    code emission) without importing any HIR types.

    ``param_widths`` is a tuple of bit-widths, one per parameter.
    ``return_widths`` is a tuple of bit-widths, one per return value.
    An empty tuple means the function returns ``void``.
    ``clobbers`` is a tuple of register-name strings that this function
    side-effects.
    """
    name: str
    param_widths: tuple[int, ...] = ()
    return_widths: tuple[int, ...] = ()
    clobbers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalSymbolDecl:
    """LIR-native declaration of a linker/global symbol.

    ``address_width`` is the bit-width of the address type.
    ``value_width`` is the bit-width of the value stored at the address,
    or ``None`` for plain labels.
    ``volatile`` marks data symbols whose accesses must not be reordered.
    """
    name: str
    address_width: int
    value_width: int | None = None
    volatile: bool = False


# ---------------------------------------------------------------------------
# LIR Module (no HIR imports)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FragmentBinding:
    """A binding between a logical name and a physical register on a fragment
    interface, lowered from ``HFragmentBinding``.

    ``name`` is the logical variable name visible to the fragment body.
    ``width`` is the bit-width (8 or 16).
    ``reg`` is the physical register name this binding maps to.
    ``mode`` is ``"in"``, ``"out"``, or ``"inout"``.
    """
    name: str
    width: int
    reg: str
    mode: str


@dataclass(frozen=True)
class Fragment:
    """An MCU-agnostic fragment definition in LIR.

    A fragment is a straight-line (acyclic) code block with a well-defined
    interface of ``bindings`` that map logical names to physical registers.
    The body is a list of basic blocks whose terminators must all eventually
    reach ``FragmentExit`` (never ``Return`` or ``MultiReturn``).
    ``scratch_regs`` lists physical registers the fragment may use freely.
    """
    name: str
    bindings: tuple[FragmentBinding, ...]
    scratch_regs: tuple[str, ...]
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class Module:
    """A lowered compilation unit.

    ``functions`` is a tuple of lowered ``lir.Function`` instances.
    ``fragments`` is a tuple of lowered ``lir.Fragment`` instances.
    ``extern_functions`` carries metadata about extern declarations
    (name, param types, return types, clobbers).
    ``external_symbols`` carries metadata about linker/global symbols.
    """
    functions: tuple[Function, ...] = ()
    fragments: tuple[Fragment, ...] = ()
    extern_functions: tuple[ExternFunctionDecl, ...] = ()
    external_symbols: tuple[ExternalSymbolDecl, ...] = ()


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------

def const(value: int, width: int = 8) -> Const:
    return Const(value=value, width=width)


def var(name: str, width: int = 8) -> Var:
    return Var(name=name, width=width)


def binop(op: str, left: Expr, right: Expr, width: int = 8) -> BinOp:
    return BinOp(op=op, left=left, right=right, width=width)


def cmp(op: str, left: Expr, right: Expr) -> Cmp:
    return Cmp(op=op, left=left, right=right)


# ---------------------------------------------------------------------------
# Traversal and formatting utilities
# ---------------------------------------------------------------------------

def walk_expr(expr: FullExpr) -> Iterable[FullExpr]:
    yield expr
    if isinstance(expr, (BinOp, Cmp)):
        yield from walk_expr(expr.left)
        yield from walk_expr(expr.right)
    elif isinstance(expr, Extend):
        yield from walk_expr(expr.value)
    elif isinstance(expr, MemLoad):
        yield from walk_expr(expr.addr)
    elif isinstance(expr, BitOp):
        yield from walk_expr(expr.var)
    elif isinstance(expr, Call):
        for arg in expr.args:
            yield from walk_expr(arg)


def format_expr(expr) -> str:
    if isinstance(expr, Const):
        return str(expr.value)
    if isinstance(expr, Var):
        return expr.name
    if isinstance(expr, VReg):
        hint_str = f"@{expr.hint}" if expr.hint else ""
        return f"%{expr.name}{hint_str}"
    if isinstance(expr, BinOp):
        return f"{expr.op}({format_expr(expr.left)}, {format_expr(expr.right)})"
    if isinstance(expr, Cmp):
        return f"cmp_{expr.op}({format_expr(expr.left)}, {format_expr(expr.right)})"
    if isinstance(expr, Extend):
        return f"{expr.kind}({format_expr(expr.value)}, w={expr.width})"
    if isinstance(expr, MemLoad):
        return f"mem_load[{format_expr(expr.addr)}, w={expr.width}]"
    if isinstance(expr, BitOp):
        return f"bit_{expr.kind}({format_expr(expr.var)}, #{expr.bit_idx})"
    if isinstance(expr, Call):
        args = ", ".join(format_expr(a) for a in expr.args)
        return f"call {expr.name}({args})"
    if isinstance(expr, SymbolAddr):
        return f"&{expr.name}"
    if isinstance(expr, InlineAsmExpr):
        return f"inline_asm({expr.text!r})"
    raise TypeError(f"unsupported expression: {expr!r}")


def format_function(func: Function) -> str:
    params = ", ".join(param.name for param in func.params)
    lines: List[str] = [f"func {func.name}({params}):"]
    for block in func.blocks:
        lines.append(f"{block.label}:")
        for stmt in block.statements:
            if isinstance(stmt, Assign):
                lines.append(f"  {stmt.target.name} = {format_expr(stmt.value)}")
            elif isinstance(stmt, CallStmt):
                lines.append(f"  call_void {format_expr(stmt.call)}")
            elif isinstance(stmt, CallAssign):
                targets = ", ".join(
                    t.name if t is not None else "_"
                    for t in stmt.targets
                )
                lines.append(f"  ({targets}) = {format_expr(stmt.call)}")
            elif isinstance(stmt, MemStore):
                lines.append(
                    f"  mem_store[{format_expr(stmt.addr)}] = {format_expr(stmt.value)}"
                )
            elif isinstance(stmt, BitOp):
                lines.append(
                    f"  bit_{stmt.kind}({format_expr(stmt.var)}, #{stmt.bit_idx})"
                )
            else:
                raise TypeError(f"unsupported statement: {stmt!r}")
        term = block.terminator
        if isinstance(term, BrIf):
            lines.append(
                f"  br_if {format_expr(term.cond)}, {term.true_label}, {term.false_label}"
            )
        elif isinstance(term, BrCmp):
            lines.append(
                f"  br_cmp_{term.op} {format_expr(term.left)}, "
                f"{format_expr(term.right)}, {term.true_label}, {term.false_label}"
            )
        elif isinstance(term, Jump):
            lines.append(f"  jump {term.label}")
        elif isinstance(term, Return):
            lines.append(f"  ret {format_expr(term.value)}")
        elif isinstance(term, MultiReturn):
            vals = ", ".join(format_expr(v) for v in term.values)
            lines.append(f"  ret {vals}")
        elif isinstance(term, FragmentExit):
            lines.append("  fragment_exit")
        else:
            raise TypeError(f"unsupported terminator: {term!r}")
    return "\n".join(lines)


def format_fragment(fragment: Fragment) -> str:
    """Render a ``Fragment`` for debugging / smoke-test output."""
    bindings_str = ", ".join(
        f"{b.name}:{b.width}@{b.reg}({b.mode})" for b in fragment.bindings
    )
    scratch_str = ", ".join(fragment.scratch_regs) if fragment.scratch_regs else "none"
    lines: List[str] = [
        f"fragment {fragment.name}",
        f"  bindings: [{bindings_str}]",
        f"  scratch: [{scratch_str}]",
    ]
    for block in fragment.blocks:
        lines.append(f"{block.label}:")
        for stmt in block.statements:
            if isinstance(stmt, Assign):
                lines.append(f"  {stmt.target.name} = {format_expr(stmt.value)}")
            elif isinstance(stmt, CallStmt):
                lines.append(f"  call_void {format_expr(stmt.call)}")
            elif isinstance(stmt, CallAssign):
                targets = ", ".join(
                    t.name if t is not None else "_"
                    for t in stmt.targets
                )
                lines.append(f"  ({targets}) = {format_expr(stmt.call)}")
            elif isinstance(stmt, MemStore):
                lines.append(
                    f"  mem_store[{format_expr(stmt.addr)}] = {format_expr(stmt.value)}"
                )
            elif isinstance(stmt, BitOp):
                lines.append(
                    f"  bit_{stmt.kind}({format_expr(stmt.var)}, #{stmt.bit_idx})"
                )
            else:
                raise TypeError(f"unsupported statement: {stmt!r}")
        term = block.terminator
        if isinstance(term, BrIf):
            lines.append(
                f"  br_if {format_expr(term.cond)}, {term.true_label}, {term.false_label}"
            )
        elif isinstance(term, BrCmp):
            lines.append(
                f"  br_cmp_{term.op} {format_expr(term.left)}, "
                f"{format_expr(term.right)}, {term.true_label}, {term.false_label}"
            )
        elif isinstance(term, Jump):
            lines.append(f"  jump {term.label}")
        elif isinstance(term, FragmentExit):
            lines.append("  fragment_exit")
        else:
            raise TypeError(f"unsupported terminator: {term!r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _expr_width(expr: FullExpr) -> int | None:
    if isinstance(expr, (Const, Var, VReg, BinOp, Extend, MemLoad, SymbolAddr)):
        return expr.width
    if isinstance(expr, (Cmp, BitOp)):
        return 1
    return None


def _validate_expr(expr: FullExpr, owner: str) -> None:
    if isinstance(expr, (Const, Var, VReg, SymbolAddr, InlineAsmExpr)):
        return
    if isinstance(expr, (BinOp, Cmp)):
        _validate_expr(expr.left, owner)
        _validate_expr(expr.right, owner)
        return
    if isinstance(expr, Extend):
        if expr.kind not in ("zext", "sext"):
            raise ValueError(f"{owner} uses unsupported Extend kind {expr.kind!r}")
        _validate_expr(expr.value, owner)
        src_width = _expr_width(expr.value)
        if src_width is not None and src_width > expr.width:
            raise ValueError(
                f"{owner} widens from {src_width} bits to smaller width {expr.width}"
            )
        return
    if isinstance(expr, MemLoad):
        _validate_expr(expr.addr, owner)
        return
    if isinstance(expr, BitOp):
        _validate_expr(expr.var, owner)
        return
    if isinstance(expr, Call):
        if expr.arg_regs and len(expr.arg_regs) != len(expr.args):
            raise ValueError(
                f"{owner} contains Call with {len(expr.args)} arguments "
                f"but {len(expr.arg_regs)} ABI registers"
            )
        for arg in expr.args:
            _validate_expr(arg, owner)
        return
    raise TypeError(f"{owner} contains unsupported expression {expr!r}")


def _validate_stmt(stmt: Stmt, owner: str) -> None:
    if isinstance(stmt, Assign):
        _validate_expr(stmt.value, owner)
    elif isinstance(stmt, CallStmt):
        if stmt.call.arg_regs and len(stmt.call.arg_regs) != len(stmt.call.args):
            raise ValueError(
                f"{owner} contains CallStmt with {len(stmt.call.args)} arguments "
                f"but {len(stmt.call.arg_regs)} ABI registers"
            )
        for arg in stmt.call.args:
            _validate_expr(arg, owner)
    elif isinstance(stmt, CallAssign):
        if stmt.abi_return_regs and len(stmt.abi_return_regs) != len(stmt.targets):
            raise ValueError(
                f"{owner} contains CallAssign with {len(stmt.targets)} targets "
                f"but {len(stmt.abi_return_regs)} ABI registers"
            )
        for arg in stmt.call.args:
            _validate_expr(arg, owner)
    elif isinstance(stmt, MemStore):
        _validate_expr(stmt.addr, owner)
        _validate_expr(stmt.value, owner)
    elif isinstance(stmt, BitOp):
        _validate_expr(stmt.var, owner)
    else:
        raise TypeError(f"{owner} contains unsupported statement {stmt!r}")


def _validate_terminator(term: Terminator, owner: str) -> None:
    if isinstance(term, BrIf):
        _validate_expr(term.cond, owner)
    elif isinstance(term, BrCmp):
        _validate_expr(term.left, owner)
        _validate_expr(term.right, owner)
    elif isinstance(term, Return):
        _validate_expr(term.value, owner)
    elif isinstance(term, MultiReturn):
        for value in term.values:
            _validate_expr(value, owner)

def validate_function(func: Function) -> None:
    """Check structural invariants of a LIR Function.

    Raises ``ValueError`` with a descriptive message if any invariant is
    violated.  Does *not* type-check expressions; that is HIR's job.
    """
    labels = {block.label for block in func.blocks}
    if len(labels) != len(func.blocks):
        raise ValueError(f"function {func.name!r} has duplicate block labels")
    for block in func.blocks:
        owner = f"function {func.name!r} block {block.label!r}"
        for stmt in block.statements:
            _validate_stmt(stmt, owner)
        term = block.terminator
        _validate_terminator(term, owner)
        if isinstance(term, FragmentExit):
            raise ValueError(
                f"function {func.name!r} block {block.label!r} "
                f"contains FragmentExit (only allowed in fragments)"
            )
        if isinstance(term, (BrIf, BrCmp)):
            missing = [
                label
                for label in (term.true_label, term.false_label)
                if label not in labels
            ]
            if missing:
                raise ValueError(
                    f"block {block.label!r} branches to missing labels: {missing}"
                )
        elif isinstance(term, Jump) and term.label not in labels:
            raise ValueError(
                f"block {block.label!r} jumps to missing label: {term.label!r}"
            )


def validate_fragment(fragment: Fragment) -> None:
    """Check structural invariants of a LIR Fragment.

    Raises ``ValueError`` with a descriptive message if any invariant is
    violated.

    Checks performed (via DFS from the first block as entry):

    1. At least one block exists.
    2. Duplicate block labels are rejected.
    3. Every block's terminator must be one of Jump, BrIf, BrCmp, FragmentExit.
    4. Return / MultiReturn are never allowed (even in unreachable blocks).
    5. Branch / jump targets must refer to existing block labels (all blocks).
    6. Reachable cycles are rejected (self-loop, two-block cycle, etc.).
    7. Every reachable path must eventually reach a ``FragmentExit`` leaf —
       i.e. reachable blocks with no outgoing edges within the reachable
       subgraph must have ``FragmentExit`` terminators.

    Unreachable blocks do not affect the reachable-path check but are still
    checked for Return/MultiReturn, valid terminator types, and missing
    targets (preserving existing policy).
    """
    # --- Pre-checks ---
    if not fragment.blocks:
        raise ValueError(f"fragment {fragment.name!r} has no blocks")

    # Build label → block map (also catches duplicates)
    label_map: dict[str, Block] = {}
    for block in fragment.blocks:
        if block.label in label_map:
            raise ValueError(
                f"fragment {fragment.name!r} has duplicate block label {block.label!r}"
            )
        label_map[block.label] = block

    # All blocks: check terminator type, Return/MultiReturn rejection, targets exist
    _ALLOWED_TERMINATORS = (Jump, BrIf, BrCmp, FragmentExit)
    for block in fragment.blocks:
        owner = f"fragment {fragment.name!r} block {block.label!r}"
        for stmt in block.statements:
            _validate_stmt(stmt, owner)
        term = block.terminator
        _validate_terminator(term, owner)
        if isinstance(term, (Return, MultiReturn)):
            raise ValueError(
                f"fragment {fragment.name!r} block {block.label!r} "
                f"contains Return/MultiReturn (not allowed in fragments)"
            )
        if not isinstance(term, _ALLOWED_TERMINATORS):
            raise ValueError(
                f"fragment {fragment.name!r} block {block.label!r} "
                f"has unsupported terminator {type(term).__name__}"
            )
        if isinstance(term, Jump) and term.label not in label_map:
            raise ValueError(
                f"fragment {fragment.name!r} block {block.label!r} "
                f"jumps to missing label {term.label!r}"
            )
        if isinstance(term, (BrIf, BrCmp)):
            for tgt in (term.true_label, term.false_label):
                if tgt not in label_map:
                    raise ValueError(
                        f"fragment {fragment.name!r} block {block.label!r} "
                        f"branches to missing label {tgt!r}"
                    )

    # --- DFS from entry (first block) ---
    entry_label = fragment.blocks[0].label
    visited: set[str] = set()
    on_path: set[str] = set()

    def _succ_labels(term) -> list[str]:
        if isinstance(term, Jump):
            return [term.label]
        if isinstance(term, (BrIf, BrCmp)):
            return [term.true_label, term.false_label]
        return []

    def _dfs(label: str) -> None:
        if label in on_path:
            raise ValueError(
                f"fragment {fragment.name!r} has a reachable cycle "
                f"involving block {label!r}"
            )
        if label in visited:
            return
        visited.add(label)
        on_path.add(label)
        blk = label_map[label]
        for succ in _succ_labels(blk.terminator):
            _dfs(succ)
        on_path.remove(label)

    _dfs(entry_label)

    # --- Ensure every reachable leaf has FragmentExit ---
    for label in visited:
        blk = label_map[label]
        if isinstance(blk.terminator, FragmentExit):
            continue
        succs = _succ_labels(blk.terminator)
        for s in succs:
            if s in visited:
                break
        else:
            raise ValueError(
                f"fragment {fragment.name!r} block {blk.label!r} "
                f"does not end with FragmentExit and has no reachable "
                f"successor"
            )
