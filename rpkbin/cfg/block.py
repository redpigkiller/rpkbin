"""BasicBlock and Instruction types for the Control Flow Graph IR.

Instruction hierarchy
---------------------
Every element in ``BasicBlock.insns`` must be one of:

* :class:`Assignment` — a variable assignment (``lhs = f(rhs…)``)
* :class:`CallRef`    — a subroutine call (``callee(…)``)
* :class:`OtherInsn`  — fall-back for anything else

The ``Insn`` type alias is the union of all three and is used in type
annotations throughout the package.

Design notes
------------
* ``raw`` fields carry the original source text and are used only for
  display / debugging; analysis tools rely on the structured fields.
* ``Assignment.rhs`` contains **variable names only** — constants and
  compile-time defines are excluded.  Callers (parsers / DSL frontends)
  are responsible for this filtering.
* ``CallRef.callee`` must correspond to a key in :class:`Program.cfgs`
  so the interprocedural analysis can locate the callee CFG.
  def/use information is derived automatically from the callee's summary.
* ``OtherInsn.defs`` / ``.uses`` must be filled manually by the caller
  when liveness accuracy matters; the defaults (empty sets) are safe but
  conservative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


# ---------------------------------------------------------------------------
# Instruction types
# ---------------------------------------------------------------------------

@dataclass
class Assignment:
    """A single variable assignment.

    Attributes:
        lhs: The variable being defined (written).
        rhs: The variables *read* on the right-hand side.  Constants and
             compile-time defines must **not** be included here — only
             runtime variable references that affect liveness.
        raw: Original source text, used only for display / debugging.
    """

    lhs: str
    rhs: list[str] = field(default_factory=list)
    raw: str = ""

    def __repr__(self) -> str:
        return f"Assignment({self.lhs!r} ← {self.rhs!r})"


@dataclass
class CallRef:
    """A subroutine call site.

    ``callee`` must match a key in the enclosing :class:`Program`'s
    ``cfgs`` dictionary.  def/use information (which variables the callee
    reads and writes) is derived automatically by the interprocedural
    liveness analysis; no manual annotation is needed.

    Attributes:
        callee: Name of the called function (matches ``Program.cfgs`` key).
        raw:    Original source text, used only for display / debugging.
    """

    callee: str
    raw: str = ""

    def __repr__(self) -> str:
        return f"CallRef({self.callee!r})"


@dataclass
class OtherInsn:
    """Fall-back instruction for anything not covered by the typed variants.

    Liveness analysis treats ``defs`` / ``uses`` as authoritative.  If the
    instruction has no data-flow effect, leave both empty (conservative but
    safe).

    Attributes:
        raw:  Original source text.
        defs: Variables written by this instruction.
        uses: Variables read by this instruction.
    """

    raw: str = ""
    defs: set[str] = field(default_factory=set)
    uses: set[str] = field(default_factory=set)

    def __repr__(self) -> str:
        return f"OtherInsn({self.raw!r})"


#: Union type for all instruction variants.
Insn = Union[Assignment, CallRef, OtherInsn]


# ---------------------------------------------------------------------------
# BasicBlock
# ---------------------------------------------------------------------------

@dataclass
class BasicBlock:
    """A straight-line sequence of instructions with a single entry point.

    Attributes:
        id:    Unique identifier used as the graph node key in :class:`CFG`.
        label: Human-readable name (e.g. the label from a Visio flowchart or
               DSL label declaration).  May be ``None`` if not provided.
        insns: Ordered list of instructions.  Each element must be an
               :class:`Assignment`, :class:`CallRef`, or :class:`OtherInsn`.
        meta:  Arbitrary key/value metadata (source location, block type, …).
    """

    id: str
    label: str | None = None
    insns: list[Insn] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"BasicBlock({self.id!r}, label={self.label!r}, insns={len(self.insns)})"

    def __hash__(self) -> int:          # so it can be used in sets / dict keys
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, BasicBlock):
            return self.id == other.id
        return NotImplemented
