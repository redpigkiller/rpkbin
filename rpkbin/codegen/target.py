"""Protocols for MCU-specific target packages.

The open package must not define real MCU registers, opcodes, flag semantics,
or costs.  Company-side code should implement these protocols and inject them
into the pipeline via ``run_codegen_from_hir(register_model=...)``.

Protocols defined here
----------------------
* ``Target``           — lowers LIR Function to pseudo ASM (instruction selection).
* ``FragmentTarget``   — lowers LIR Fragment to pseudo ASM (instruction selection).
* ``RegisterModel``    — describes the physical register file.

None of these protocols import or reference any private/target package.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from .asm import AsmFunction
from .lir import Function, Fragment, SpillSlot


class Target(Protocol):
    """Converts a target-neutral LIR ``Function`` to pseudo ASM.

    Implementations live in private target packages; this package only
    defines the interface.
    """

    name: str

    def select_instructions(self, func: Function) -> AsmFunction:
        """Lower target-neutral LIR to abstract target instructions.

        The returned ``AsmFunction`` contains opcode strings and operand
        strings but no binary encoding — encoding is the real-ASM layer's
        responsibility.
        """


class FragmentTarget(Protocol):
    """Converts a target-neutral LIR ``Fragment`` to pseudo ASM.

    A ``Fragment`` is a code fragment without function-call prologue/epilogue.
    ``FragmentExit`` has no intrinsic return/exit opcode; a target may still
    emit an internal jump when multiple fragment paths must converge.  The
    preceding ``InlineAsm`` carries any externally visible exit opcode.

    Implementations live in private target packages; this package only
    defines the interface.
    """

    name: str

    def select_fragment_instructions(self, fragment: Fragment) -> AsmFunction:
        """Lower target-neutral LIR Fragment to abstract target instructions.

        Rules
        -----
        * ``FragmentExit`` has no intrinsic return/exit instruction.  Internal
          control-flow convergence may still require a target-emitted jump.
        * ``VReg`` operands use their ``hint`` as the physical register name.
        * ``InlineAsmExpr`` is emitted verbatim.
        * Block labels follow the same convention as ``select_instructions``.
        """


class RegisterModel(Protocol):
    """Describes the physical register file.

    Implementations are provided by the private target package.  The
    register allocator uses this Protocol exclusively; the framework
    never accesses real register names directly.

    Method summary
    --------------
    ``allocatable_registers()``
        Names of registers that the allocator may freely assign.

    ``is_physical_register(reg)``
        Return whether *reg* is a legal physical storage location on this
        target.  A physical register may appear in fixed bindings, clobber
        lists, ABI ``arg_regs`` / ``return_regs``, and fragment bindings
        even when it is **not** in the automatic allocation pool.

        Default implementation (backward-compatible): returns ``True`` when
        *reg* appears in ``allocatable_registers()`` **or** is mentioned in
        any alias group returned by ``register_aliases()``.

        Override this method to expose additional physical registers that the
        allocator should not automatically assign (e.g. status/flag registers,
        implicit ABI-only registers).

    ``fixed_register_hints()``
        Whether source ``@hint`` annotations are hard constraints.

    ``register_width(reg)``
        Width in bits of a named register.

    ``register_aliases()``
        Alias groups, e.g. ``[("wide", ["wide_hi", "wide_lo"])]``.
        Used by the HIR validator to detect aliasing conflicts between
        @hint-annotated variables.

    The allocator currently fails closed on register pressure.  The legacy
    spill hooks below are retained for compatibility but are not consumed by
    the production pipeline.
    """

    def allocatable_registers(self) -> Sequence[str]:
        """Return the names of all registers the allocator may assign."""

    def is_physical_register(self, reg: str) -> bool:
        """Return whether *reg* is a legal physical storage location.

        Physical registers may be used in fixed bindings, clobber lists,
        ABI register annotations, and fragment bindings regardless of
        whether they are in the automatic allocation pool.

        The default implementation returns ``True`` when *reg* appears in
        ``allocatable_registers()`` or in any alias group returned by
        ``register_aliases()``.
        """
        if reg in set(self.allocatable_registers()):
            return True
        for composite, members in self.register_aliases():
            if reg in ({composite} | set(members)):
                return True
        return False

    def fixed_register_hints(self) -> bool:
        """Return whether source register hints are hard constraints.

        When ``True``, any hinted value must keep that exact physical
        register for its whole live range or allocation fails.
        """

    def register_width(self, reg: str) -> int:
        """Return the width in bits of the named physical register."""

    def can_allocate(self, reg: str, width: int) -> bool:
        """Return whether *reg* can hold a value of *width* bits."""
        return self.register_width(reg) >= width

    def register_aliases(self) -> Sequence[tuple[str, list[str]]]:
        """Return alias groups.

        Each entry is ``(composite_name, [sub_register, ...])``.
        For example: ``[("wide", ["wide_hi", "wide_lo"])]``.

        This remains the backward-compatible fallback shape for targets that
        do not override ``registers_overlap()`` / ``is_physical_register()``.
        """

    def registers_overlap(self, lhs: str, rhs: str) -> bool:
        """Return whether two register names share physical storage."""
        if lhs == rhs:
            return True
        return any(
            lhs in ({composite} | set(members))
            and rhs in ({composite} | set(members))
            for composite, members in self.register_aliases()
        )

    def spill_slots(self) -> Sequence[SpillSlot]:
        """Legacy spill prototype hook; currently not consumed."""

    def emit_spill(self, reg: str, slot: SpillSlot) -> Sequence[str]:
        """Legacy spill prototype hook; currently not consumed."""

    def emit_reload(self, slot: SpillSlot, reg: str) -> Sequence[str]:
        """Legacy spill prototype hook; currently not consumed."""


def is_physical_register(register_model, reg: str) -> bool:
    """Dispatch to the optional query while preserving legacy models.

    If the *register_model* does not implement ``is_physical_register``,
    the backward-compatible default is used: *reg* is physical when it
    appears in ``allocatable_registers()`` or in any alias group.
    """
    query = getattr(register_model, "is_physical_register", None)
    if query:
        return query(reg)
    # Backward-compatible default for models that pre-date this API
    if reg in set(register_model.allocatable_registers()):
        return True
    for composite, members in register_model.register_aliases():
        if reg in ({composite} | set(members)):
            return True
    return False


def can_allocate(register_model, reg: str, width: int) -> bool:
    """Dispatch to the optional query while preserving structural models."""
    query = getattr(register_model, "can_allocate", None)
    return query(reg, width) if query else register_model.register_width(reg) >= width


def registers_overlap(register_model, lhs: str, rhs: str) -> bool:
    """Dispatch to the optional query while preserving legacy alias groups."""
    query = getattr(register_model, "registers_overlap", None)
    if query:
        return query(lhs, rhs)
    if lhs == rhs:
        return True
    return any(
        lhs in ({composite} | set(members))
        and rhs in ({composite} | set(members))
        for composite, members in register_model.register_aliases()
    )
