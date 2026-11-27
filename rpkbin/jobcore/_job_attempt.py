"""Attempt state and the Job event lifecycle."""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from ._events import Capabilities, Finished, PROCESS_CAPABILITIES, SourceError, StatusChanged
from ._job_log import _AttemptLogFile
from .model import JobStatus, Report

if TYPE_CHECKING:
    from ._receiver import AttemptReceiver


class _LockedDeque(deque):
    """Thread-safe bounded deque whose iteration yields a consistent tuple snapshot."""

    def __init__(self, iterable=(), maxlen=100, *, lock=None):
        super().__init__(iterable, maxlen=maxlen)
        self._lock = threading.RLock() if lock is None else lock

    def append(self, item):
        with self._lock:
            super().append(item)

    def appendleft(self, item):
        with self._lock:
            super().appendleft(item)

    def extend(self, items):
        with self._lock:
            super().extend(items)

    def extendleft(self, items):
        with self._lock:
            super().extendleft(items)

    def pop(self):
        with self._lock:
            return super().pop()

    def popleft(self):
        with self._lock:
            return super().popleft()

    def clear(self):
        with self._lock:
            super().clear()

    def remove(self, value):
        with self._lock:
            super().remove(value)

    def __iter__(self):
        with self._lock:
            return iter(tuple(super().__iter__()))

    def snapshot_rows(self):
        with self._lock:
            return [dict(row) for row in super().__iter__()]


@dataclass
class _Attempt:
    id: str
    submission_id: str
    number: int
    retry_index: int
    max_lines: int
    status: JobStatus = JobStatus.PENDING
    returncode: int | None = None
    pid: int | None = None
    started: float | None = None
    ended: float | None = None
    started_mono: float | None = None
    ended_mono: float | None = None
    errors: deque = field(default_factory=lambda: _LockedDeque(maxlen=100))
    diagnostics: deque = field(default_factory=lambda: _LockedDeque(maxlen=100))
    log_file_error: dict | None = None
    error_count: int = 0
    termination: deque = field(default_factory=lambda: _LockedDeque(maxlen=100))
    records: deque = field(init=False)
    total_lines: int = 0
    stop_requested: bool = False
    interrupt_requested: bool = False
    interrupt_source: str | None = None
    suspended: bool = False
    force_requested: bool = False
    stop_started: bool = False
    timed_out: bool = False
    finalized: bool = False
    retry_scheduled: bool = False
    report: Report | None = None
    final_snapshot: dict | None = None
    history_summary: dict | None = field(default=None, repr=False, compare=False)
    pending_reason: str = "Not started"
    process: Any = None
    reader_done: threading.Event = field(default_factory=threading.Event)
    reader_progress_at: float = 0.0
    reader_cutoff: str | None = None
    reserved: bool = False
    stop_thread: Any = None
    execution_failed: bool = False
    output_error: dict | None = None
    output_decode_count: int = 0
    capabilities: Capabilities = PROCESS_CAPABILITIES
    completion_reason: str | None = None
    previous_status: JobStatus = JobStatus.PENDING
    force_sent: bool = False
    priority: int = 0
    held: bool = False
    sink: AttemptReceiver = field(init=False, repr=False)
    log_file: _AttemptLogFile = field(default_factory=_AttemptLogFile, repr=False)
    lock: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        lock = threading.RLock() if self.lock is None else self.lock
        self.errors = _LockedDeque(self.errors, maxlen=100, lock=lock)
        self.diagnostics = _LockedDeque(self.diagnostics, maxlen=100, lock=lock)
        self.termination = _LockedDeque(self.termination, maxlen=100, lock=lock)
        self.records = deque(maxlen=self.max_lines)

    def can_rerun(self, *, finalizing=False):
        return (self.capabilities.can_rerun and self.status.is_finished
                and (self.finalized or finalizing) and not self.reserved)

    @property
    def elapsed(self):
        if self.started_mono is None:
            return 0.0
        return max(
            0.0,
            (self.ended_mono if self.ended_mono is not None else time.monotonic())
            - self.started_mono,
        )


class _JobEvents:
    """Reentrant Job lock that prevents nesting a different Job lock."""

    _local = threading.local()

    def __init__(self):
        self._lock = threading.RLock()

    @classmethod
    def held(cls):
        owners = getattr(cls._local, "owners", None)
        owner = owners[-1] if owners else getattr(cls._local, "owner", None)
        callback = getattr(cls._local, "input", None)
        return owner if owner is not None or callback is None else callback[0]

    def input_attempt(self):
        callback = getattr(self._local, "input", None)
        return callback[1] if callback is not None and callback[0] is self else None

    @contextmanager
    def input_callback(self, attempt):
        previous = getattr(self._local, "input", None)
        self._local.input = (self, attempt, [])
        try:
            yield
        finally:
            self._local.input = previous

    def defer_input_log(self, attempt, notify, record):
        # A reader can reenter Job methods that produce management Log lines.
        # Dispatch their listeners when the actual event lock is released.
        if self.input_attempt() is attempt and self._lock._is_owned():
            self._local.input[2].append((notify, record))
            return True
        return False

    def _is_owned(self):
        owners = getattr(self._local, "owners", None)
        return (owners is not None and self in owners) or self.held() is self

    def acquire(self, blocking=True, timeout=-1):
        owner = self.held()
        if (
            owner is not None
            and owner is not self
            and not self._lock._is_owned()
            and not getattr(self._local, "batch", False)
        ):
            if not blocking:
                return False
            raise RuntimeError("Cannot lock another Job while handling a Job event")
        if not self._lock.acquire(blocking, timeout):
            return False
        owners = getattr(self._local, "owners", None)
        if owners is None:
            owners = self._local.owners = []
        if self not in owners:
            owners.append(self)
        self._local.owner = owners[-1]
        return True

    def release(self):
        self._lock.release()
        if not self._lock._is_owned():
            owners = getattr(self._local, "owners", None)
            if owners and self in owners:
                owners.remove(self)
            self._local.owner = owners[-1] if owners else None
            scope = getattr(self._local, "input", None)
            if scope is not None and scope[0] is self and scope[2]:
                _, attempt, pending = scope
                notifications = pending[:]
                pending.clear()
                for notify, record in notifications:
                    if attempt.finalized or attempt.reader_cutoff is not None:
                        break
                    notify(record)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


class _JobAttemptLifecycle:
    def _error(self, a, source, exc, *, output_error=False):
        if a.finalized:
            return
        error = dict(
            source=source,
            message=f"{type(exc).__name__}: {exc}",
            evidence=None,
            time=time.time(),
        )
        if output_error and a.output_error is None:
            a.output_error = error
        # Observers capture under the data lock without waiting for the event lock.
        with self._lock:
            a.error_count += 1
            a.errors.append(error)
        a.sink.emit(SourceError(error))

    def _set_status(self, a, status):
        with self._events_lock:
            if a.finalized or (
                a.status is JobStatus.STOPPING
                and status in (JobStatus.RUNNING, JobStatus.WAITING_INPUT)
            ):
                return
            old, a.status = a.status, status
            a.sink.emit(StatusChanged(old.value, status.value))

    def _guard(self, expected_attempt=None, expected_submission=None):
        a = self._attempt
        origin = self._events_lock.input_attempt()
        if origin is not None and (origin is not a or origin.finalized or origin.reader_cutoff is not None):
            raise ValueError("Input callback belongs to an abandoned attempt")
        if expected_attempt is not None and a.id != expected_attempt:
            raise ValueError("stale attempt: return to the current execution")
        if expected_submission is not None and a.submission_id != expected_submission:
            raise ValueError("stale submission")
        return a

    def _finish(self, a, *, cancelled=False):
        with self._events_lock:
            if a.finalized or a.ended is not None:
                return
            if a.sink.input_pending:
                a.sink.abandon_input("Output callback still active during attempt finish")
            if a.reader_cutoff is not None:
                self._error(a, "output_reader", RuntimeError(a.reader_cutoff), output_error=True)
            a.ended, a.ended_mono = time.time(), time.monotonic()
            a.previous_status = JobStatus.PENDING if cancelled and a.status is JobStatus.CANCELLED else a.status
            a.status = (
                JobStatus.CANCELLED
                if cancelled or (a.stop_requested and not a.timed_out)
                else (
                    JobStatus.DONE
                    if a.returncode == 0 and not a.timed_out
                    else JobStatus.FAILED
                )
            )
            complete = not (cancelled or a.stop_requested or a.output_error is not None
                            or a.execution_failed or a.error_count)
            if cancelled:
                reason = "cancelled"
            elif a.timed_out:
                reason = "timeout"
            elif a.stop_requested:
                reason = "stopped"
            elif not complete:
                reason = "source_error"
            else:
                reason = "process_exit"
            try:
                a.completion_reason = reason
                a.sink.emit(Finished(complete, reason))
            except Exception as exc:
                a.sink.abandon_input(f"Receiver finish failed: {type(exc).__name__}: {exc}")
                self._error(a, "output_reader", RuntimeError(a.reader_cutoff), output_error=True)
            if not a.reserved and not a.sink.pending:
                self._seal_attempt(a)

    def _seal_attempt(self, a, *, persist=False):
        # Callers hold the event lock. All attempt writes precede this gate.
        if a.finalized:
            return
        a.sink.seal()
        with self._lock:
            a.report = self._report(a, complete=True)
            a.final_snapshot = self._capture_attempt(a, include_details=True)
            can_rerun = a.can_rerun(finalizing=True)
            a.final_snapshot.update(finalized=True, can_rerun=can_rerun)
            a.final_snapshot["capabilities"]["can_rerun"] = can_rerun
            if persist:
                # A cancelled queued Job has no worker to keep drain pending.
                a.sink.publish_state(flush=True, snapshot=a.final_snapshot)
            a.finalized = True
