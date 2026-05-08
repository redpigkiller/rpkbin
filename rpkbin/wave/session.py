"""
session.py — Session lifecycle manager and module-level singleton.

Session is the central coordinator between the user's wave file, the
scheduler's JobManager, and the TUI.

Lifecycle
---------
1. User's wave file calls ``session.configure()`` and ``session.add()``.
   Jobs are buffered in ``_pending`` until ``_start()`` is called.
2. ``runner.py`` calls ``session._start(tui_notify=...)`` after the wave
   file has been fully loaded (and any CLI overrides applied).
3. ``_start()`` creates a JobManager, injects the tui_notify callback into
   every job, and starts the scheduling loop + timer thread.
4. ``runner.py`` calls ``session._stop()`` when the TUI exits or the batch
   completes.

Singleton reset
---------------
``session.reset()`` is called by ``runner.py`` at the beginning of each
``run()`` invocation.  This handles the edge case where ``wave`` is used
programmatically inside a long-lived Python process and ``run()`` is called
more than once.
"""

from __future__ import annotations

import threading
import time
import logging
import warnings
from datetime import datetime
from typing import Callable

from rpkbin.job_manager import JobManager
from rpkbin.wave.hook import Hook
from rpkbin.wave.job import WaveJobMixin
from rpkbin.wave._util import TUI_BUILTIN_DASHBOARD_COLUMNS as _TUI_BUILTIN_DASHBOARD_COLUMNS

logger = logging.getLogger(__name__)


class Session:
    """Manages a single Wave batch run."""

    def __init__(self) -> None:
        self._reset_state()

    # ------------------------------------------------------------------
    # Public API (wave file side)
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        max_workers: int = 4,
        resources: dict[str, int | Callable[[], int]] | None = None,
        log_dir: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """Configure the session.

        Must be called before ``session.add()``.  Calling it again after
        ``_start()`` has no effect (the JobManager is already running).

        Parameters
        ----------
        max_workers:
            Maximum number of concurrently running jobs.
        resources:
            Resource capacity limits, e.g. ``{"gpu": 2}``.
        log_dir:
            Directory for per-job log files.  ``None`` disables file logging.
        timeout:
            Optional session-wide timeout in seconds.  Once exceeded, all
            active jobs are cancelled and the batch finishes failed.
        """
        if self._manager is not None:
            warnings.warn(
                "session.configure() called after the session has already started; "
                "settings are ignored. Call configure() before the session starts.",
                stacklevel=2,
            )
            return
        if timeout is not None and timeout <= 0:
            raise ValueError("session timeout must be > 0")
        self._config["max_workers"] = max_workers
        self._config["resources"] = resources or {}
        self._config["log_dir"] = log_dir
        self._config["timeout"] = timeout

    def configure_tui(self, *, dashboard_columns: list | tuple | None = None) -> None:
        """Configure TUI presentation details from a wave file.

        ``dashboard_columns`` accepts built-in column names plus parsed-data
        column specs, for example::

            session.configure_tui(dashboard_columns=[
                "name",
                "status",
                {"label": "Final", "data": "FINAL_RESULT"},
                "exit_code",
            ])

        Built-ins are: name, id, status, elapsed, progress, retries, exit_code,
        tags. Parsed-data specs must provide non-empty string ``label`` and
        ``data`` fields.
        """
        if dashboard_columns is None:
            self._tui_config["dashboard_columns"] = None
            return
        self._tui_config["dashboard_columns"] = self._normalize_dashboard_columns(dashboard_columns)

    def tui_config(self) -> dict:
        """Return a shallow copy of TUI configuration."""
        config = dict(self._tui_config)
        columns = config.get("dashboard_columns")
        if columns is not None:
            config["dashboard_columns"] = tuple(dict(column) for column in columns)
        return config

    def add(self, job, *, timeout: float | None = None) -> None:
        """Add a job to the session.

        Parameters
        ----------
        job:
            Any scheduler Job (typically a ``WaveCmdJob`` or ``WaveFuncJob``).
        timeout:
            Optional maximum runtime in seconds.  If the job is still running
            after *timeout* seconds, it is force-killed exactly as if
            ``Hook.action_kill()`` were fired.  Only effective for
            ``WaveJobMixin`` subclasses; silently ignored for plain scheduler jobs.
            Internally this mutates the job by injecting an
            ``elapsed_exceeds`` hook.

        If called before ``_start()``, the job is buffered.
        If called after ``_start()`` (dynamic add from a callback), the job
        is injected with the tui_notify callback and dispatched immediately.
        The ``tui_job_added`` callback (if registered) is also invoked so the
        TUI can append a new row to its job list.

        Raises
        ------
        RuntimeError
            If the session has already been finalized.  In particular,
            ``session.add()`` is not valid from ``on_finish`` / ``on_done`` /
            ``on_fail`` session callbacks, because the manager has already
            stopped by then.
        """
        if self._finalized:
            raise RuntimeError(
                "session.add() called after the session has already finished. "
                "Add follow-up jobs from job-level callbacks before finalization, "
                "not from session finish callbacks."
            )
        if timeout is not None and isinstance(job, WaveJobMixin):
            job.add_hook(Hook(
                Hook.elapsed_exceeds(timeout),
                Hook.action_kill(),
                policy="once",
            ))
        elif timeout is not None:
            warnings.warn(
                f"session.add(..., timeout=...) is ignored for non-Wave job {getattr(job, 'name', job)!r}. "
                "Use a Wave job class if you want timeout hooks.",
                stacklevel=2,
            )
        if self._manager is None:
            self._pending.append(job)
        else:
            self._inject_notify(job)
            self._manager.add(job)
            if self._tui_job_added is not None:
                try:
                    self._tui_job_added(job)
                except Exception:
                    logger.exception("tui_job_added callback raised")

    def jobs(self) -> list:
        """Return all known jobs (buffered or in the manager).

        Before ``_start()`` is called, returns only jobs registered via
        ``session.add()`` that are still waiting to be submitted (the
        pending buffer).  After ``_start()``, returns all jobs the manager
        knows about, including those already DONE, FAILED, or CANCELLED.
        """
        if self._manager is not None:
            return self._manager.jobs()
        return list(self._pending)

    def running(self) -> list:
        """Return jobs currently in RUNNING status."""
        if self._manager is not None:
            return self._manager.running()
        return []

    def pending(self) -> list:
        """Return jobs waiting to run (PENDING status).

        Before ``_start()``, returns the buffered job list.
        """
        if self._manager is not None:
            return self._manager.pending()
        return list(self._pending)

    def done(self) -> list:
        """Return jobs that completed successfully (DONE status)."""
        if self._manager is None:
            return []
        return [j for j in self._manager.jobs() if j.status == "done"]

    def failed(self, *, include_skipped: bool = False) -> list:
        """Return jobs that genuinely failed.

        Parameters
        ----------
        include_skipped:
            Backward-compatible flag. If ``True``, also include jobs skipped
            via ``job.skip()`` even though they do not affect session outcome
            or exit-code semantics.
        """
        if self._manager is None:
            return []
        failed = [j for j in self._manager.jobs() if j.status == "failed"]
        if include_skipped:
            failed.extend(j for j in self._manager.jobs() if getattr(j, "is_skipped", False))
        return failed

    def skipped(self) -> list:
        """Return jobs skipped via ``job.skip()``."""
        if self._manager is None:
            return []
        return [j for j in self._manager.jobs() if getattr(j, "is_skipped", False)]

    def stats(self) -> dict:
        """Return a snapshot of worker and resource usage from the JobManager.

        Returns an empty dict if the session has not started yet.
        """
        if self._manager is None:
            return {}
        return self._manager.stats()

    def emit(self, tag: str, message: str) -> None:
        """Record a batch-level event for this session."""
        self._record_event(tag, message, source="user")

    def pause(self) -> None:
        """Pause job dispatch; jobs currently RUNNING are not affected.

        Has no effect if the session has not started yet.
        """
        if self._manager is not None:
            self._manager.pause()
            self._record_event("session.pause", "Job dispatch paused", source="system")

    def resume(self) -> None:
        """Resume job dispatch after a :meth:`pause`.

        Has no effect if the session has not started yet.
        """
        if self._manager is not None:
            self._manager.resume()
            self._record_event("session.resume", "Job dispatch resumed", source="system")

    def peek_events(self) -> list[dict]:
        """Return a thread-safe snapshot of session-level events."""
        with self._session_lock:
            return list(self._events)

    def cancel_group(self, tag: str) -> int:
        """Cancel all non-terminal jobs that have *tag* in their ``tags`` set.

        Returns the number of jobs cancelled.  Works both before and after
        the session starts.
        """
        if self._manager is None:
            cancelled = 0
            for job in list(self._pending):
                tags = getattr(job, "tags", ())
                status = getattr(job, "status", None)
                if tag in tags and status in ("pending", "running"):
                    job.cancel()
                    cancelled += 1
            return cancelled
        cancelled = self._manager.cancel_by_tag(tag)
        if cancelled:
            self._record_event(
                "session.cancel_group",
                f"Cancelled {cancelled} job(s) with tag={tag!r}",
                source="system",
            )
        return cancelled

    def on_finish(self, cb: Callable[["Session"], None]) -> None:
        """Register a callback fired once after the batch fully finishes.

        Callback registrations belong to the current session run only.
        ``session.reset()`` clears them before the next run starts.
        """
        with self._session_lock:
            self._on_finish_cbs.append(cb)

    def on_done(self, cb: Callable[["Session"], None]) -> None:
        """Register a callback fired once if the batch finishes successfully.

        Callback registrations belong to the current session run only.
        ``session.reset()`` clears them before the next run starts.
        """
        with self._session_lock:
            self._on_done_cbs.append(cb)

    def on_fail(self, cb: Callable[["Session"], None]) -> None:
        """Register a callback fired once if the batch finishes with failures.

        Callback registrations belong to the current session run only.
        ``session.reset()`` clears them before the next run starts.
        """
        with self._session_lock:
            self._on_fail_cbs.append(cb)

    def summary(self) -> dict:
        """Return a batch-level summary snapshot of the current session state."""
        jobs = self.jobs()
        done_jobs = [j for j in jobs if getattr(j, "status", None) == "done"]
        failed_jobs = [j for j in jobs if getattr(j, "status", None) == "failed"]
        skipped_jobs = [j for j in jobs if getattr(j, "is_skipped", False)]
        pending_jobs = [j for j in jobs if getattr(j, "status", None) == "pending"]
        running_jobs = [j for j in jobs if getattr(j, "status", None) == "running"]
        cancelled_jobs = [j for j in jobs if getattr(j, "status", None) == "cancelled" and not getattr(j, "is_skipped", False)]

        if pending_jobs or running_jobs:
            outcome = "running"
        elif failed_jobs or self._session_timeout_fired:
            outcome = "failed"
        else:
            outcome = "done"

        started_at = self._started_at
        finished_at = self._finished_at
        duration_s = None
        if started_at is not None:
            end_t = finished_at if finished_at is not None else time.time()
            duration_s = max(0.0, end_t - started_at)

        return {
            "total": len(jobs),
            "pending": len(pending_jobs),
            "running": len(running_jobs),
            "done": len(done_jobs),
            "failed": len([j for j in jobs if getattr(j, "status", None) == "failed"]),
            "cancelled": len(cancelled_jobs),
            "skipped": len(skipped_jobs),
            "outcome": outcome,
            "exit_code": 1 if failed_jobs or self._session_timeout_fired else 0,
            "done_names": [j.name for j in done_jobs],
            "failed_names": [j.name for j in failed_jobs],
            "skipped_names": [j.name for j in skipped_jobs],
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": duration_s,
        }

    # ------------------------------------------------------------------
    # Internal lifecycle (called by runner.py, not by the user)
    # ------------------------------------------------------------------

    def _start(self, tui_notify=None, tui_job_added=None) -> None:
        """Start the JobManager and timer thread.

        Called by ``runner.py`` after the wave file is fully loaded.

        Parameters
        ----------
        tui_notify:
            ``(job) -> None`` — called from a worker thread whenever a Wave
            job's observable state changes.  The TUI should forward this to
            its main thread via ``call_from_thread``.
        tui_job_added:
            ``(job) -> None`` — called on the main thread when a new job is
            dynamically added via ``session.add()`` after the session has
            already started (e.g. from an ``on_done`` callback).  Used by
            the TUI to append a new row to the job list.
        """
        self._tui_notify = tui_notify
        self._tui_job_added = tui_job_added
        self._started_at = time.time()
        self._started_monotonic = time.monotonic()
        self._finished_at = None
        self._finalized = False
        self._session_timeout_fired = False
        self._manager = JobManager(
            max_workers=self._config["max_workers"],
            resources=self._config["resources"],
            log_dir=self._config["log_dir"],
        )
        for job in self._pending:
            self._inject_notify(job)
            self._manager.add(job)
        self._pending.clear()
        self._manager.start()
        self._start_timer()

    def _stop(self) -> None:
        """Stop the timer thread and wait for all jobs to finish.

        Called by ``runner.py`` when the TUI exits or when running headless.
        This method includes the final manager ``wait()``, so callers do not
        need to wait separately before cleanup.
        """
        self._stop_timer.set()
        if self._timer_thread is not None:
            try:
                self._timer_thread.join()
            except KeyboardInterrupt:
                pass  # WaveTimerThread is a daemon; tolerable to not join cleanly.
                      # The manager cleanup below always runs regardless.

        if self._manager is not None:
            try:
                self._manager.wait()
            except KeyboardInterrupt:
                self._manager.cancel_all()
            except RuntimeError:
                # Already stopped or never fully started; still run finalization below.
                pass
            finally:
                self._manager.stop()
            self._finalize_session()

    def wait(self, timeout: float | None = None, *, job=None) -> bool:
        """Wait until the session is idle, or until a specific job completes.

        Parameters
        ----------
        job:
            If provided, wait only for this specific job to reach a terminal
            state (``done``, ``failed``, or ``cancelled``).  If omitted,
            wait for the whole session to become quiescent (all jobs terminal
            and all async callbacks drained).
        timeout:
            Maximum number of seconds to wait.  ``None`` means wait forever.
            Returns ``False`` if the timeout expires before the condition is met.
        """
        if self._manager is None:
            if job is not None:
                return getattr(job, "status", None) in ("done", "failed", "cancelled")
            return not self._pending
        if job is not None:
            return self._manager.wait(target_id=job.id, timeout=timeout)
        return self._manager.wait(timeout=timeout)

    def reset(self) -> None:
        """Reset the session to a clean state.

        Called by ``runner.py`` at the start of each ``run()`` invocation
        so that the same Python process can run multiple wave files without
        accumulating stale state in the singleton.
        """
        # Stop any running infrastructure first (defensive)
        if self._manager is not None or self._timer_thread is not None:
            try:
                self._stop()
            except Exception:
                pass
        self._reset_state()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_state(self) -> None:
        """Initialize (or re-initialize) all instance variables."""
        self._manager: JobManager | None = None
        self._pending: list = []
        self._tui_notify = None
        self._tui_job_added = None
        self._session_lock = threading.RLock()
        self._events: list[dict] = []
        self._on_finish_cbs: list[Callable[["Session"], None]] = []
        self._on_done_cbs: list[Callable[["Session"], None]] = []
        self._on_fail_cbs: list[Callable[["Session"], None]] = []
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._started_monotonic: float | None = None
        self._finalized: bool = False
        self._config: dict = {
            "max_workers": 4,
            "resources": {},
            "log_dir": None,
            "timeout": None,
        }
        self._tui_config: dict = {
            "dashboard_columns": None,
        }
        self._session_timeout_fired: bool = False
        self._timer_thread: threading.Thread | None = None
        self._stop_timer = threading.Event()

    def _normalize_dashboard_columns(self, columns: list | tuple) -> tuple:
        if not isinstance(columns, (list, tuple)):
            raise TypeError("dashboard_columns must be a list or tuple")
        if not columns:
            raise ValueError("dashboard_columns must not be empty")

        normalized = []
        for idx, column in enumerate(columns):
            if isinstance(column, str):
                key = column.strip().lower()
                if key not in _TUI_BUILTIN_DASHBOARD_COLUMNS:
                    allowed = ", ".join(sorted(_TUI_BUILTIN_DASHBOARD_COLUMNS))
                    raise ValueError(
                        f"Unknown dashboard column {column!r} at index {idx}. "
                        f"Use one of: {allowed}, or {{'label': ..., 'data': ...}}."
                    )
                normalized.append({"type": "builtin", "key": key})
                continue

            if isinstance(column, dict):
                label = column.get("label")
                data_key = column.get("data")
                if not isinstance(label, str) or not label.strip():
                    raise ValueError(f"dashboard column at index {idx} needs a non-empty string label")
                if not isinstance(data_key, str) or not data_key.strip():
                    raise ValueError(f"dashboard column {label!r} needs a non-empty string data key")
                normalized.append({
                    "type": "parsed_data",
                    "label": label.strip(),
                    "data": data_key.strip(),
                })
                continue

            raise TypeError(
                f"dashboard column at index {idx} must be a built-in name string "
                "or {'label': ..., 'data': ...}"
            )

        return tuple(normalized)

    def _inject_notify(self, job) -> None:
        """Wire the tui_notify callback into a Wave-aware job."""
        if isinstance(job, WaveJobMixin):
            job._tui_notify = self._tui_notify

    def _start_timer(self) -> None:
        """Start the background timer thread for elapsed_exceeds hooks."""
        self._stop_timer.clear()

        def _loop() -> None:
            interval = 1.0
            next_tick = time.monotonic() + interval
            while True:
                wait_s = max(0.0, next_tick - time.monotonic())
                if self._stop_timer.wait(timeout=wait_s):
                    break
                mgr = self._manager  # capture local ref — avoids TOCTOU if _manager
                if mgr is None:      # is set to None by reset() on another thread
                    next_tick += interval
                    continue
                self._check_session_timeout(mgr)
                for job in mgr.running():
                    if isinstance(job, WaveJobMixin):
                        try:
                            job._check_timer_hooks()
                        except Exception:
                            logger.exception(
                                "Unexpected error in _check_timer_hooks for job %r",
                                getattr(job, "name", repr(job)),
                            )
                next_tick += interval

        self._timer_thread = threading.Thread(
            target=_loop, daemon=True, name="WaveTimerThread"
        )
        self._timer_thread.start()

    def _check_session_timeout(self, mgr: JobManager) -> None:
        """Cancel all active jobs once the session-wide timeout is exceeded."""
        timeout = self._config.get("timeout")
        started = self._started_monotonic
        if timeout is None or started is None or self._session_timeout_fired:
            return
        elapsed = time.monotonic() - started
        if elapsed < timeout:
            return
        self._session_timeout_fired = True
        self._record_event(
            "session.timeout",
            f"Session timeout exceeded after {timeout:.1f}s; cancelling active jobs",
            source="system",
        )
        mgr.cancel_all()

    def _finalize_session(self) -> None:
        """Fire session-level lifecycle callbacks once after the batch settles."""
        with self._session_lock:
            if self._finalized:
                return
            self._finalized = True
            self._finished_at = time.time()
            summary = self.summary()
            self._record_event(
                "session.finish",
                f"Session finished with outcome={summary['outcome']}",
                source="system",
            )
            finish_cbs = list(self._on_finish_cbs)
            done_cbs = list(self._on_done_cbs)
            fail_cbs = list(self._on_fail_cbs)

        for cb in finish_cbs:
            try:
                cb(self)
            except Exception:
                logger.exception("session.on_finish callback raised")

        terminal_cbs = done_cbs if summary["exit_code"] == 0 else fail_cbs
        for cb in terminal_cbs:
            try:
                cb(self)
            except Exception:
                if summary["exit_code"] == 0:
                    logger.exception("session.on_done callback raised")
                else:
                    logger.exception("session.on_fail callback raised")

    def _record_event(self, tag: str, message: str, *, source: str) -> None:
        """Append a session-level event with an explicit source marker."""
        event = {
            "tag": tag,
            "message": message,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": source,
        }
        with self._session_lock:
            self._events.append(event)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

session = Session()
