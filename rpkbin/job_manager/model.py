"""Small public vocabulary shared by the runner, Python API and GUI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable
import re


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


class Result(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    INCOMPLETE = "INCOMPLETE"
    NA = "N/A"


class Expect(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"


class StopPolicy(str, Enum):
    GRACEFUL_THEN_FORCE = "GRACEFUL_THEN_FORCE"
    FORCE = "FORCE"


class Event(str, Enum):
    LOG = "log"
    TICK = "tick"


class CheckState(str, Enum):
    UNDECIDED = "-"
    OK = "OK"
    FAIL = "FAIL"
    ERROR = "ERROR"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class EventRef:
    owner: object
    kind: str


class Events:
    """Immutable named event references; event identity is not user-editable."""

    __slots__ = ("_members",)

    def __init__(self, owner, *kinds):
        object.__setattr__(
            self, "_members", MappingProxyType({k: EventRef(owner, k) for k in kinds})
        )

    def __getattr__(self, name):
        try:
            return self._members[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        raise AttributeError("event definitions are immutable")


class _Definition:
    def __setattr__(self, name, value):
        job = getattr(self, "job", None)
        if job is not None and job._frozen:
            raise AttributeError("monitor definition is frozen")
        object.__setattr__(self, name, value)


def freeze(value: Any) -> Any:
    """Detach and recursively freeze a result, including nested evidence."""
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class Report:
    verdict: Result | None
    execution: Any
    checks: tuple
    warnings: tuple
    errors: tuple
    termination: tuple


def matcher(pattern: Any) -> Any:
    if isinstance(pattern, str):
        return re.compile(pattern)
    if isinstance(pattern, re.Pattern) or callable(pattern):
        return pattern
    raise TypeError("match must be a regex string, compiled regex, or callable")


class Track(_Definition):
    def __init__(self, job: Any, name: str, pattern: Any, cast: Callable):
        self.job, self.name = job, name
        self.pattern = matcher(pattern)
        if not isinstance(self.pattern, re.Pattern) or self.pattern.groups != 1:
            raise ValueError("track requires a regex with exactly one capture group")
        if not callable(cast):
            raise TypeError("cast must be callable")
        self.cast = cast
        self.events = Events(self, "changed", "error")

    @property
    def value(self):
        return freeze(self.job._callback_attempt().data.get(self.name))


class Check(_Definition):
    def __init__(
        self,
        job: Any,
        name: str,
        pattern: Any,
        expect: Expect,
        source: Any,
        every: float | None,
    ):
        self.job, self.name = job, name
        self.pattern, self.expect, self.source, self.every = (
            matcher(pattern),
            expect,
            source,
            every,
        )
        self.events = Events(self, "failed", "satisfied", "error")

    @property
    def state(self):
        return self.job._callback_attempt().checks[self.name]["state"]

    @property
    def hits(self):
        return self.job._callback_attempt().checks[self.name]["hits"]


@dataclass(frozen=True)
class Subscription:
    source: Any
    pattern: Any
    warn: bool
    stop: bool
    callback: Callable | None
    name: str
    message: str | None
    once: bool
    every: float | None
