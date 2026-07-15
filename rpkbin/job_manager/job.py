"""
job.py — Base job abstraction.

Classes:
    JobStatus — Type alias for job state literals.
    Job       — Abstract Base Class for schedulable units of work.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
import logging
from itertools import islice
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Callable, Literal, Pattern

JobStatus = Literal["pending", "running", "done", "failed", "cancelled"]

PENDING: JobStatus = "pending"
RUNNING: JobStatus = "running"
DONE: JobStatus = "done"
FAILED: JobStatus = "failed"
CANCELLED: JobStatus = "cancelled"

logger = logging.getLogger(__name__)
SLOW_ON_LOG_CALLBACK_WARNING_S = 0.1
SLOW_CALLBACK_WARNING_S = 1.0

class Job(ABC):
    """Abstract Base Class for schedulable units of work.
    
    Subclasses must implement `_execute()` and `kill()`.
    Users interact directly with Job instances, which manage their own state
    in a thread-safe manner.
    """

    def __init__(
        self,
        name: str,
        *,
        job_id: str | uuid.UUID | None = None,
        priority: int = 0,
        max_retries: int = 0,
        resources: dict[str, int] | None = None,
        max_log_lines: int = 10_000,
        tags: frozenset[str] | set[str] | None = None,
    ) -> None:
        if job_id is None:
            self._id: uuid.UUID = uuid.uuid4()
        elif isinstance(job_id, str):
            self._id: uuid.UUID = uuid.UUID(job_id)
        else:
            self._id: uuid.UUID = job_id
            
        self._name: str = name
        
        # Settings
        self.priority: int = priority
        self.max_retries: int = max_retries
        self.resources: dict[str, int] = resources or {}
        self.tags: frozenset[str] = frozenset(tags) if tags else frozenset()
        
        # State
        self._status: JobStatus = PENDING
        self._progress: float | None = None
        self._result: Any = None
        self._error: str | None = None
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._retry_count: int = 0
        
        # Threading & Control
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        
        # Logs
        self._output_buffer: deque[str] = deque(maxlen=max_log_lines)
        self._total_log_lines: int = 0
        
        # Callbacks (internal & external)
        self._on_state_change_cb: Callable[[], None] | None = None
        self._on_log_cbs: list[Callable[['Job', str], None]] = []
        self._on_done_cbs: list[Callable[['Job'], None]] = []
        self._on_fail_cbs: list[Callable[['Job', str], None]] = []
        self._on_retry_cbs: list[Callable[['Job', int], None]] = []
        
        # Watchers: pattern regex -> user callback(job, match)
        self._watchers: list[tuple[Pattern[str], Callable[['Job', re.Match], None]]] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def id(self) -> uuid.UUID:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> JobStatus:
        with self._lock:
            return self._status

    @property
    def progress(self) -> float | None:
        with self._lock:
            return self._progress

    @property
    def result(self) -> Any:
        with self._lock:
            return self._result

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def start_time(self) -> float | None:
        with self._lock:
            return self._start_time

    @property
    def end_time(self) -> float | None:
        with self._lock:
            return self._end_time

    @property
    def retry_count(self) -> int:
        """Number of retry attempts made so far (0 = first run, 1 = first retry, ...)."""
        with self._lock:
            return self._retry_count

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ------------------------------------------------------------------
    # Control API (Called by User or Manager)
    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """Cancel the job. Subclasses should respond to cancellation (e.g. killing process)."""
        with self._lock:
            if self._status in (DONE, FAILED, CANCELLED):
                return
            self._status = CANCELLED
            self._cancel_event.set()
        
        # If the job is running (e.g., subprocess), force it to terminate.
        self.kill()
        self._notify_state_change()

    def set_progress(self, value: float) -> None:
        """Update job progress (0.0 to 100.0)."""
        with self._lock:
            self._progress = max(0.0, min(100.0, value))
        self._notify_state_change()

    @abstractmethod
    def kill(self) -> None:
        """Forcefully terminate the running job. Must be implemented by subclasses."""
        pass

    # ------------------------------------------------------------------
    # Data & Observability
    # ------------------------------------------------------------------
    def logs(self) -> list[str]:
        """Return all captured log lines."""
        with self._lock:
            return list(self._output_buffer)

    def tail(self, n: int = 20) -> list[str]:
        """Return the last *n* lines of captured output."""
        with self._lock:
            if n <= 0:
                return []
            return list(self._output_buffer)[-n:]

    def log_snapshot_since(self, last_total: int) -> tuple[int, list[str]]:
        """Return log lines emitted after *last_total* and the current total.

        ``_total_log_lines`` is cumulative, while ``_output_buffer`` is bounded.
        If the requested cursor is older than the retained buffer, the returned
        lines start at the oldest retained line. Callers should keep the returned
        total as their next cursor.
        """
        with self._lock:
            total = self._total_log_lines
            if last_total >= total:
                return total, []
            buffered = len(self._output_buffer)
            if buffered == 0:
                return total, []

            oldest_seq = total - buffered + 1
            start_seq = max(last_total + 1, oldest_seq)
            start_idx = max(0, start_seq - oldest_seq)
            return total, list(islice(self._output_buffer, start_idx, None))

    def _emit_line(self, line: str) -> None:
        """Called by subclasses to append output lines and trigger matchers/cbs."""
        with self._lock:
            self._output_buffer.append(line)
            self._total_log_lines += 1
            cbs = list(self._on_log_cbs)
            watchers = list(self._watchers)
        
        for cb in cbs:
            try:
                t0 = time.perf_counter()
                cb(self, line)
                dt = time.perf_counter() - t0
                if dt > SLOW_ON_LOG_CALLBACK_WARNING_S:
                    logger.warning(
                        "Slow on_log callback detected for job %r (took %.2fs). "
                        "This blocks stdout reading and may cause the subprocess to hang! "
                        "Please move heavy computation to a background thread.",
                        self.name, dt
                    )
            except Exception:
                logger.exception(
                    "on_log callback %r raised (ignored). "
                    "This blocks stdout reading; move heavy work to a background thread.",
                    cb,
                )
                
        for pattern, wcb in watchers:
            try:
                t0 = time.perf_counter()
                m = pattern.search(line)
                if m:
                    wcb(self, m)
                dt = time.perf_counter() - t0
                if dt > SLOW_CALLBACK_WARNING_S:
                    logger.warning(
                        "Slow watch callback detected for job %r / pattern %r (took %.2fs).",
                        self.name,
                        pattern.pattern,
                        dt,
                    )
            except Exception:
                logger.exception(
                    "watch callback for pattern %r raised (ignored).",
                    pattern.pattern,
                )

    # ------------------------------------------------------------------
    # Event Bindings
    # ------------------------------------------------------------------
    def on_log(self, cb: Callable[['Job', str], None]) -> None:
        with self._lock:
            self._on_log_cbs.append(cb)

    def on_done(self, cb: Callable[['Job'], None]) -> None:
        with self._lock:
            self._on_done_cbs.append(cb)

    def on_fail(self, cb: Callable[['Job', str], None]) -> None:
        with self._lock:
            self._on_fail_cbs.append(cb)

    def on_retry(self, cb: Callable[['Job', int], None]) -> None:
        """Register a callback fired when the job is reset for a retry attempt.

        The callback receives ``(job, new_attempt)`` where *new_attempt* is
        the retry number (1 = first retry, 2 = second, ...).
        Dispatched via the manager's async event bus, same as on_done/on_fail.
        """
        with self._lock:
            self._on_retry_cbs.append(cb)

    def watch(self, pattern: str | Pattern[str], cb: Callable[['Job', re.Match], None]) -> None:
        if isinstance(pattern, str):
            pattern = re.compile(pattern)
        with self._lock:
            self._watchers.append((pattern, cb))

    # ------------------------------------------------------------------
    # Internal Lifecycle (Used by JobManager / Base Job)
    # ------------------------------------------------------------------
    @abstractmethod
    def _execute(self, log_file=None) -> None:
        """Execute the job's core workload. Subclasses must implement."""
        pass

    def _manager_try_start(self) -> bool:
        """[Internal] Atomically transition state from PENDING to RUNNING."""
        with self._lock:
            if self._status != PENDING:
                return False
            self._status = RUNNING
            self._start_time = time.monotonic()
            return True

    def _manager_reset_for_retry(self) -> None:
        """[Internal] Safely reset state and buffers for a retry attempt."""
        with self._lock:
            self._status = PENDING
            self._retry_count += 1
            self._result = None
            self._error = None
            self._output_buffer.clear()
            # _total_log_lines is intentionally NOT reset: it is a cumulative
            # counter so the TUI's incremental sync logic continues to work.
            self._cancel_event.clear()

    def _manager_set_end_time(self) -> None:
        """[Internal] Set the end time for a terminal job."""
        with self._lock:
            self._end_time = time.monotonic()

    def _manager_set_wakeup_cb(self, cb: Callable[[], None] | None) -> None:
        """[Internal] Register a callback to wake up the manager on state changes."""
        with self._lock:
            self._on_state_change_cb = cb

    def _notify_state_change(self) -> None:
        """Trigger the Manager wake-up callback if registered."""
        cb = None
        with self._lock:
            cb = self._on_state_change_cb
        if cb is not None:
            cb()

    def __repr__(self) -> str:
        dur = ""
        with self._lock:
            if self._start_time:
                end = self._end_time or time.monotonic()
                dur = f", duration={end - self._start_time:.1f}s"
            prog = f", progress={self._progress:.0f}%" if self._progress is not None else ""
            return f"<{type(self).__name__} {self.name!r} status={self._status}{dur}{prog}>"
