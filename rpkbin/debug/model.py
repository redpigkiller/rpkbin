"""Target-neutral contracts for instruction-oriented debuggers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Event
from typing import Any, Protocol, runtime_checkable


class StopReason(str, Enum):
    """Why execution returned control to the debugger."""

    ENTRY = "entry"
    STEP = "step"
    BREAKPOINT = "breakpoint"
    WATCHPOINT = "watchpoint"
    DATA_BREAKPOINT = "data breakpoint"
    PAUSE = "pause"
    COMPLETE = "complete"
    EXCEPTION = "exception"


@dataclass(frozen=True, slots=True)
class DebugCapabilities:
    """Optional behavior exposed by a debug target."""

    supports_instruction_breakpoints: bool = True
    supports_source_breakpoints: bool = False
    supports_pause: bool = True
    supports_reverse_step: bool = False
    supports_single_thread_execution: bool = False


@dataclass(frozen=True, slots=True)
class AddressSpace:
    """An address space whose units need not be bytes."""

    name: str
    unit_bits: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("address-space name must not be empty")
        if self.unit_bits <= 0:
            raise ValueError("address-space unit_bits must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """One independently selectable execution context."""

    id: int
    name: str

    def __post_init__(self) -> None:
        if self.id <= 0:
            raise ValueError("execution-context id must be positive")
        if not self.name:
            raise ValueError("execution-context name must not be empty")


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Optional source position associated with an instruction."""

    path: str
    line: int
    column: int = 1

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("source path must not be empty")
        if self.line <= 0 or self.column <= 0:
            raise ValueError("source line and column must be positive")


@dataclass(frozen=True, slots=True)
class DebugLocation:
    """Current instruction and its optional source/assembly presentation."""

    address_space: str
    address: int
    source: SourceLocation | None = None
    instruction: str | None = None

    def __post_init__(self) -> None:
        if not self.address_space:
            raise ValueError("location address_space must not be empty")
        if self.address < 0:
            raise ValueError("location address must be non-negative")


@dataclass(frozen=True, slots=True)
class DebugScope:
    """A target-owned group of debugger variables."""

    id: str
    name: str
    expensive: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("scope id and name must not be empty")


@dataclass(frozen=True, slots=True)
class DebugVariable:
    """A rendered debugger value; the target owns its schema."""

    name: str
    value: str
    type_name: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("variable name must not be empty")


@dataclass(frozen=True, slots=True)
class InstructionBreakpoint:
    """A breakpoint in target address units.

    ``condition_text`` is opaque. The target may interpret or reject it.
    """

    address_space: str
    address: int
    condition_text: str | None = None

    def __post_init__(self) -> None:
        if not self.address_space:
            raise ValueError("breakpoint address_space must not be empty")
        if self.address < 0:
            raise ValueError("breakpoint address must be non-negative")


@dataclass(frozen=True, slots=True)
class BreakpointResult:
    """Target validation result for an instruction breakpoint."""

    verified: bool
    address_space: str
    address: int
    id: int | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if not self.address_space:
            raise ValueError("breakpoint result address_space must not be empty")
        if self.address < 0:
            raise ValueError("breakpoint result address must be non-negative")


@dataclass(frozen=True, slots=True)
class SourceBreakpoint:
    """A source breakpoint whose condition remains target-owned."""

    path: str
    line: int
    condition_text: str | None = None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("source-breakpoint path must not be empty")
        if self.line <= 0:
            raise ValueError("source-breakpoint line must be positive")


@dataclass(frozen=True, slots=True)
class SourceBreakpointResult:
    """Target validation result for a source breakpoint."""

    verified: bool
    path: str
    line: int
    id: int | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("source-breakpoint result path must not be empty")
        if self.line <= 0:
            raise ValueError("source-breakpoint result line must be positive")


@dataclass(frozen=True, slots=True)
class StopResult:
    """Result of a step, continue, or reverse-step operation."""

    reason: StopReason
    context_id: int
    location: DebugLocation | None
    description: str | None = None
    breakpoint_id: int | None = None

    def __post_init__(self) -> None:
        if self.context_id <= 0:
            raise ValueError("stop context_id must be positive")


@runtime_checkable
class DebugTarget(Protocol):
    """Minimal adapter implemented by a target-specific debugger."""

    @property
    def capabilities(self) -> DebugCapabilities: ...

    def address_spaces(self) -> tuple[AddressSpace, ...]: ...

    def contexts(self) -> tuple[ExecutionContext, ...]: ...

    def location(self, context_id: int) -> DebugLocation: ...

    def scopes(self, context_id: int) -> tuple[DebugScope, ...]: ...

    def variables(
        self, context_id: int, scope_id: str
    ) -> tuple[DebugVariable, ...]: ...

    def set_instruction_breakpoints(
        self, breakpoints: tuple[InstructionBreakpoint, ...]
    ) -> tuple[BreakpointResult, ...]: ...

    def step_instruction(self, context_id: int) -> StopResult: ...

    def continue_execution(
        self, context_id: int | None, cancel: Event
    ) -> StopResult: ...


@runtime_checkable
class SupportsReverseStep(Protocol):
    """Optional reverse-step capability checked at runtime."""

    def reverse_step(self, context_id: int) -> StopResult: ...


@runtime_checkable
class SupportsSourceBreakpoints(Protocol):
    """Optional source-mapping capability."""

    def set_source_breakpoints(
        self, path: str, breakpoints: tuple[SourceBreakpoint, ...]
    ) -> tuple[SourceBreakpointResult, ...]: ...


@runtime_checkable
class SupportsClose(Protocol):
    """Optional target cleanup hook."""

    def close(self) -> None: ...


@runtime_checkable
class DebugSessionFactory(Protocol):
    """Target-owned boundary for creating launch sessions."""

    @property
    def capabilities(self) -> DebugCapabilities: ...

    @property
    def supports_attach(self) -> bool: ...

    def launch(self, arguments: dict[str, Any]) -> DebugTarget: ...


@runtime_checkable
class SupportsAttach(Protocol):
    """Optional factory capability for attaching to an existing session."""

    def attach(self, arguments: dict[str, Any]) -> DebugTarget: ...
