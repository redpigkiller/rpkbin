"""Small public vocabulary shared by the runner, Python API and GUI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    STOPPING = "STOPPING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_active(self) -> bool:
        return self in (self.RUNNING, self.WAITING_INPUT, self.STOPPING)

    @property
    def is_finished(self) -> bool:
        return self in (self.DONE, self.FAILED, self.CANCELLED)


@dataclass(frozen=True)
class ControlReceipt:
    status: str
    reason: str

    def __post_init__(self):
        if self.status not in ("accepted", "rejected", "failed"):
            raise ValueError("Invalid control receipt status")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("Control receipt reason must be nonempty text")

    @property
    def accepted(self):
        return self.status == "accepted"

    def __bool__(self):
        return self.accepted


class StopPolicy(str, Enum):
    GRACEFUL_THEN_FORCE = "GRACEFUL_THEN_FORCE"
    FORCE = "FORCE"


def freeze(value: Any) -> Any:
    """Detach and recursively freeze a result, including nested evidence."""
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


class ProcessVerdict(str, Enum):
    FAIL = "FAIL"
    ERROR = "ERROR"
    INCOMPLETE = "INCOMPLETE"
    NA = "N/A"


@dataclass(frozen=True)
class Report:
    verdict: ProcessVerdict | None
    execution: Any
    warnings: tuple
    errors: tuple
    termination: tuple
