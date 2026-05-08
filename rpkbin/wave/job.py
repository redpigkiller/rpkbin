"""
job.py - WaveJobMixin, WaveCmdJob, WaveFuncJob.

WaveJobMixin adds the following on top of scheduler's CmdJob / FuncJob:
  parsed_data          : dict of key-value pairs extracted from log lines
  events               : list of timestamped events
  add_parser(fn)       : register a log-line -> dict parser
  add_hook(hook)       : register a reactive Hook
  emit(tag, message)   : append a named event (shown in TUI)
  update_parsed_data() : directly merge data and notify TUI
  peek_data()          : thread-safe snapshot of parsed_data
  peek_events()        : thread-safe snapshot of events
  state                : shorthand for peek_data().get('state')
  skip()               : skip this job without marking it as failed

Callback injection strategy
----------------------------
Each on_log / on_done / on_fail / on_retry callback is injected **at most once**,
regardless of how many parsers or hooks the user registers.  Boolean guard
flags make this intent explicit (vs. inferring it from list length, which
was brittle in the original spec).

Hook trigger wiring
-------------------
  log_matches     -> on_log callback (_handle_log)
  data_equals     -> checked inside update_parsed_data() - no on_log needed;
                    fires whether the change came from a parser or direct call
  elapsed_exceeds -> driven by Session's timer thread (_check_timer_hooks)
  on_start        -> WaveJobMixin._execute() - fires just before execution
  on_done         -> on_done callback (_handle_on_done)
  on_fail         -> on_fail callback (_handle_on_fail)
  on_retry        -> on_retry callback (_handle_on_retry)
  on_cancel       -> fired by WaveJobMixin.cancel() override - no injection needed
"""

from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from rpkbin.job_manager import CmdJob as _CmdJob, FuncJob as _FuncJob

logger = logging.getLogger(__name__)
SLOW_PARSER_WARNING_S = 1.0
_EXCEPTION_TRACEBACK_LIMIT = 6
_EVENT_LINE_SNIPPET_LIMIT = 240

if TYPE_CHECKING:
    from rpkbin.wave.hook import Hook


def _short_log_line(line: str) -> str:
    text = line.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= _EVENT_LINE_SNIPPET_LIMIT:
        return text
    return text[: _EVENT_LINE_SNIPPET_LIMIT - 3] + "..."


def _format_parser_exception_event(fn: Callable[[str], dict], line: str, exc: BaseException) -> str:
    tb = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__, limit=_EXCEPTION_TRACEBACK_LIMIT)
    ).rstrip()
    return (
        f"Parser {fn!r} raised {type(exc).__name__}: {exc}\n"
        f"Line: {_short_log_line(line)!r}\n"
        f"Traceback:\n{tb}"
    )


# ---------------------------------------------------------------------------
# WaveJobMixin
# ---------------------------------------------------------------------------

class WaveJobMixin:
    """Mixin that adds Wave observability to scheduler job classes.

    Must appear **before** the scheduler base class in the MRO so that
    Python's cooperative ``super().__init__()`` chain resolves correctly.
    ``_wave_init()`` must be called explicitly in each concrete subclass.
    """

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _wave_init(self) -> None:
        """Initialize Wave-specific state.  Call this from ``__init__``."""
        self.parsed_data: dict[str, str] = {}
        self.events: list[dict] = []
        self._wave_parsers: list[Callable[[str], dict]] = []
        self._wave_hooks: list["Hook"] = []
        self._wave_lock = threading.RLock()
        self._tui_notify: Callable[["WaveJobMixin"], None] | None = None
        self._wave_skipped: bool = False
        self._stop_policy: dict[str, object | None] = {
            "graceful_input": None,
            "graceful_signal": None,
            "graceful_timeout": 5.0,
        }

        # Injection guards - ensure each callback is registered exactly once,
        # no matter how many parsers / hooks the user adds later.
        # on_start has no scheduler callback to inject (fired via _execute override),
        # so it does not need a guard flag here.
        self._log_cb_injected:     bool = False
        self._on_done_cb_injected: bool = False
        self._on_fail_cb_injected: bool = False
        self._on_retry_cb_injected: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def remove_hook(self, hook: "Hook") -> bool:
        """Remove a previously added Hook instance.

        Returns ``True`` if the hook was found and removed, ``False``
        otherwise.  Uses object identity (``is``) for matching.

        Note: the underlying scheduler callback (``on_log``, ``on_done``, or
        ``on_fail``) remains injected even after all hooks of that type are
        removed.  The callback will simply have nothing to dispatch — this is
        harmless.  The ``_exhausted`` flag on a ``policy="once"`` hook is also
        an effective removal mechanism for single-fire rules.
        """
        with self._wave_lock:
            for i, h in enumerate(self._wave_hooks):
                if h is hook:
                    del self._wave_hooks[i]
                    return True
        return False

    def add_parser(self, fn: Callable[[str], dict]) -> None:
        """Register a log-line parser.

        *fn* receives one log line and must return a ``dict`` of extracted
        key-value pairs, or ``{}`` if the line doesn't match.  Results from
        all parsers are merged into ``job.parsed_data`` on each log line.

        Parser exceptions are logged and swallowed - a buggy parser must
        never crash a running job.
        """
        with self._wave_lock:
            self._wave_parsers.append(fn)
            need_inject = not self._log_cb_injected
            if need_inject:
                self._log_cb_injected = True
        # Inject the scheduler callback outside the lock to avoid cross-lock
        # ordering issues between _wave_lock and scheduler's internal lock.
        if need_inject:
            self.on_log(self._handle_log)  # type: ignore[attr-defined]

    def add_hook(self, hook: "Hook") -> None:
        """Register a reactive Hook.

        The appropriate scheduler callback is injected the first time a hook
        of each type is added:

        - ``log_matches``     -> one shared ``on_log`` callback
        - ``data_equals``     -> no callback; checked inside ``update_parsed_data``
        - ``elapsed_exceeds`` -> no callback; polled by Session's timer thread
        - ``on_start``        -> no callback; fired by ``WaveJobMixin._execute()``
        - ``on_done``         -> one ``on_done`` callback
        - ``on_fail``         -> one ``on_fail`` callback

        Thread-safe: ``_wave_lock`` guards the list and injection flags together
        so concurrent calls (e.g. from an on_done callback) never inject the
        same scheduler callback twice.
        """
        t = hook.when.type
        need_log = need_done = need_fail = need_retry = False
        with self._wave_lock:
            self._wave_hooks.append(hook)
            if t == "log_matches" and not self._log_cb_injected:
                self._log_cb_injected = True
                need_log = True
            elif t == "on_done" and not self._on_done_cb_injected:
                self._on_done_cb_injected = True
                need_done = True
            elif t == "on_fail" and not self._on_fail_cb_injected:
                self._on_fail_cb_injected = True
                need_fail = True
            elif t == "on_retry" and not self._on_retry_cb_injected:
                self._on_retry_cb_injected = True
                need_retry = True
            # on_start      : fired by WaveJobMixin._execute() - no injection needed
            # data_equals   : checked in update_parsed_data() - no injection needed
            # elapsed_exceeds : timer thread in Session - no injection needed
            # on_cancel     : fired by WaveJobMixin.cancel() override - no injection needed

        # Inject scheduler callbacks outside the lock - each call acquires the
        # scheduler's own internal lock; doing it inside _wave_lock would create
        # a nested-lock ordering that is harder to reason about.
        if need_log:  self.on_log(self._handle_log)   # type: ignore[attr-defined]
        if need_done: self.on_done(self._handle_on_done)         # type: ignore[attr-defined]
        if need_fail: self.on_fail(self._handle_on_fail)         # type: ignore[attr-defined]
        if need_retry: self.on_retry(self._handle_on_retry)      # type: ignore[attr-defined]

    def emit(self, tag: str, message: str, source: str = "user") -> None:
        """Record a named event, visible in the TUI Events panel."""
        event = {
            "tag": tag,
            "message": message,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": source,
        }
        with self._wave_lock:
            self.events.append(event)
        if self._tui_notify:
            self._tui_notify(self)

    def update_parsed_data(self, updates: dict) -> None:
        """Merge *updates* into ``parsed_data``, fire data_equals hooks, notify TUI.

        This is the single write path for ``parsed_data``.  Calling it from
        both parsers and user code ensures that ``data_equals`` hooks fire
        regardless of *how* the data changed.
        """
        if not updates:
            return
        with self._wave_lock:
            self.parsed_data.update(updates)
            hooks_snapshot = list(self._wave_hooks)
            data_snapshot = dict(self.parsed_data)
        # Check data_equals hooks after every change - covers both the
        # parser-driven path and direct update_parsed_data() calls.
        self._check_data_equals_hooks(hooks_snapshot, data_snapshot)
        if self._tui_notify:
            self._tui_notify(self)

    def peek_data(self) -> dict[str, str]:
        """Return a thread-safe snapshot of ``parsed_data``.

        Safe to call from any thread at any time; returns a copy that will
        not change even if parsers run concurrently.
        """
        with self._wave_lock:
            return dict(self.parsed_data)

    def peek_events(self) -> list[dict]:
        """Return a thread-safe snapshot of events recorded via ``emit()``.

        Safe to call from any thread at any time.
        """
        with self._wave_lock:
            return list(self.events)

    @property
    def state(self) -> str | None:
        """Shorthand for ``peek_data().get('state')``.

        Intended for FSM-style parsers that write the current phase name into
        ``parsed_data['state']`` (e.g. ``{'state': 'link_training'}``).  Returns
        ``None`` if no parser has written a ``'state'`` key yet.
        """
        return self.peek_data().get("state")

    @property
    def is_skipped(self) -> bool:
        """True if this job was skipped via ``skip()`` rather than cancelled by user."""
        with self._wave_lock:
            return self._wave_skipped

    @property
    def progress(self) -> float | None:
        """Current job progress (0.0–100.0), or ``None`` if not reported.

        Passthrough to the base job's ``progress`` property.  To update
        the value from a :class:`WaveFuncJob` task function, call
        ``self.set_progress(value)`` (inherited from the concrete base class).
        """
        return super().progress  # type: ignore[misc]

    def set_progress(self, value: float) -> None:
        """Set job progress (0.0–100.0). Passthrough to base class."""
        super().set_progress(float(value))  # type: ignore[misc]

    def set_stop_policy(
        self,
        *,
        graceful_input: str | None = None,
        graceful_signal: int | None = None,
        graceful_timeout: float = 5.0,
    ) -> None:
        """Declare how this job should be stopped gracefully.

        ``graceful_input`` is written to stdin first when available.
        ``graceful_signal`` is sent next when available.
        If the job is still RUNNING after ``graceful_timeout`` seconds,
        a force-cancel is issued unless the caller requested graceful-only.
        """
        if graceful_timeout <= 0:
            raise ValueError("graceful_timeout must be > 0")
        with self._wave_lock:
            self._stop_policy = {
                "graceful_input": graceful_input,
                "graceful_signal": graceful_signal,
                "graceful_timeout": graceful_timeout,
            }

    def peek_stop_policy(self) -> dict[str, object | None]:
        """Return a snapshot of the configured stop policy."""
        with self._wave_lock:
            return dict(self._stop_policy)

    def request_stop(self, *, force: bool = False, graceful_only: bool = False) -> str:
        """Stop the job according to its configured stop policy.

        Returns one of: ``already_finished``, ``cancelled_pending``,
        ``graceful``, ``force``, or ``unsupported``.
        """
        status = self.status  # type: ignore[attr-defined]
        if status in ("done", "failed", "cancelled"):
            return "already_finished"
        if status == "pending":
            self.cancel()  # type: ignore[attr-defined]
            return "cancelled_pending"
        if force:
            self.cancel()  # type: ignore[attr-defined]
            return "force"

        policy = self.peek_stop_policy()
        sent_graceful = False

        graceful_input = policy.get("graceful_input")
        if isinstance(graceful_input, str) and graceful_input:
            if hasattr(self, "send_input"):
                try:
                    self.send_input(graceful_input)  # type: ignore[attr-defined]
                    sent_graceful = True
                except RuntimeError:
                    logger.warning("Graceful input ignored for job %r: stdin unavailable.", self.name)  # type: ignore[attr-defined]

        graceful_signal = policy.get("graceful_signal")
        if isinstance(graceful_signal, int):
            if hasattr(self, "send_signal"):
                try:
                    self.send_signal(graceful_signal)  # type: ignore[attr-defined]
                    sent_graceful = True
                except RuntimeError:
                    logger.warning("Graceful signal ignored for job %r: process unavailable.", self.name)  # type: ignore[attr-defined]

        if not sent_graceful:
            if graceful_only:
                return "unsupported"
            self.cancel()  # type: ignore[attr-defined]
            return "force"

        if not graceful_only:
            timeout = policy.get("graceful_timeout")
            if isinstance(timeout, (int, float)):
                self._schedule_force_cancel(float(timeout))
        return "graceful"

    def _schedule_force_cancel(self, timeout: float) -> None:
        """Force-cancel the job if it is still running after *timeout* seconds."""

        def _fallback() -> None:
            time.sleep(timeout)
            if self.status == "running":  # type: ignore[attr-defined]
                self.cancel()  # type: ignore[attr-defined]

        threading.Thread(
            target=_fallback,
            daemon=True,
            name=f"WaveStopFallback-{self.name}",  # type: ignore[attr-defined]
        ).start()

    def skip(self) -> None:
        """Skip this job without counting it as a failure.

        Only effective on PENDING jobs.  Internally calls the scheduler's
        ``cancel()`` which sets ``status = CANCELLED``, but Wave separately
        tracks the skip reason via ``is_skipped``, so status displays can
        distinguish "skipped" from a user-initiated cancel.

        Typical use: in an ``on_fail`` hook, skip a dependent job rather than
        letting it sit pending forever after its prerequisite failed.
        """
        if self.status != "pending":  # type: ignore[attr-defined]
            return
        with self._wave_lock:
            self._wave_skipped = True
        self.cancel()  # calls WaveJobMixin.cancel() which fires on_cancel hooks

    def cancel(self) -> None:  # type: ignore[override]
        """Cancel the job and fire any ``on_cancel`` hooks.

        Hooks fire synchronously in the calling thread.  Actions should be
        lightweight; avoid blocking calls or operations that acquire the
        manager lock (although the manager lock is an RLock and re-entrancy
        is safe if cancel is called from within a manager-owned thread).
        """
        # Capture active state before the atomic cancel so we only fire hooks
        # when the job was actually transitioned (not already terminal).
        was_active = self.status not in (  # type: ignore[attr-defined]
            "done", "failed", "cancelled"
        )
        with self._wave_lock:
            is_skip = self._wave_skipped
        super().cancel()  # type: ignore[misc]
        if was_active:
            self._handle_on_cancel(skipped=is_skip)

    # ------------------------------------------------------------------
    # Internal: on_log handlers
    # ------------------------------------------------------------------

    def _execute(self, **kwargs) -> None:  # type: ignore[override]
        """MRO intercept: fire on_start hooks, then delegate to the concrete class.

        Via MRO (e.g. WaveCmdJob -> WaveJobMixin -> _CmdJob), this method is
        reached first when the scheduler calls ``job._execute()``.  It fires
        ``on_start`` hooks while the job is already in RUNNING state (guaranteed
        by the scheduler's ``_manager_try_start()`` which ran before this call),
        then hands off to ``_CmdJob._execute()`` or ``_FuncJob._execute()``.

        Using ``**kwargs`` to forward the ``log_file`` argument (and any future
        arguments) avoids a fragile dependency on the exact parameter name used
        by the concrete base class.
        """
        self._handle_on_start()
        super()._execute(**kwargs)  # type: ignore[misc]

    def _handle_on_start(self) -> None:
        """Fire all on_start hooks.  Called at the very start of _execute()."""
        with self._wave_lock:
            hooks = list(self._wave_hooks)  # snapshot
        for hook in hooks:
            if hook.when.type == "on_start":
                hook._fire(self, {})

    def _handle_log(self, job: "WaveJobMixin", line: str) -> None:
        """on_log callback: run parsers, then fire matching log hooks.

        NOTE: ``job`` is always ``self``; the parameter satisfies the
        scheduler's ``Callable[[Job, str], None]`` callback signature.
        NOTE: scheduler invokes on_log callbacks after releasing ``Job._lock``,
        so acquiring ``_wave_lock`` here does not create a nested lock cycle.
        """
        with self._wave_lock:
            hooks = list(self._wave_hooks)
            parsers = list(self._wave_parsers)
        updates: dict = {}
        for fn in parsers:
            try:
                t0 = time.perf_counter()
                result = fn(line)
                dt = time.perf_counter() - t0
                if dt > SLOW_PARSER_WARNING_S:
                    logger.warning(
                        "Slow parser detected for job %r (took %.2fs): %r. "
                        "Avoid blocking work inside parsers.",
                        self.name,  # type: ignore[attr-defined]
                        dt,
                        fn,
                    )
                if result:
                    updates.update(result)
            except Exception as exc:
                logger.exception("Parser %r raised an exception (ignored).", fn)
                # Surface the failure as a job event for TUI visibility.
                try:
                    self.emit("parser_error", _format_parser_exception_event(fn, line, exc), source="system")
                except Exception:
                    pass
        if updates:
            self.update_parsed_data(updates)
        for hook in hooks:
            if hook.when.type == "log_matches":
                if hook.when.pattern is None:
                    continue  # invariant: never happens, but be safe
                m = hook.when.pattern.search(line)
                if m:
                    hook._fire(self, {"match": m, "line": line})

    # ------------------------------------------------------------------
    # Internal: status callbacks (event-driven via scheduler)
    # ------------------------------------------------------------------

    def _handle_on_done(self, job: "WaveJobMixin") -> None:
        """on_done scheduler callback: fire all on_done hooks.

        NOTE: ``job`` is always ``self``; the parameter satisfies the
        scheduler's ``Callable[[Job], None]`` callback signature.
        """
        with self._wave_lock:
            hooks = list(self._wave_hooks)  # snapshot
        for hook in hooks:
            if hook.when.type == "on_done":
                hook._fire(self, {})

    def _handle_on_fail(self, job: "WaveJobMixin", err: str) -> None:
        """on_fail scheduler callback: fire all on_fail hooks.

        NOTE: ``job`` is always ``self``; the parameter satisfies the
        scheduler's ``Callable[[Job, str], None]`` callback signature.
        """
        with self._wave_lock:
            hooks = list(self._wave_hooks)  # snapshot
        for hook in hooks:
            if hook.when.type == "on_fail":
                hook._fire(self, {"error": err})

    def _handle_on_retry(self, job: "WaveJobMixin", attempt: int) -> None:
        """on_retry scheduler callback: fire all on_retry hooks."""
        with self._wave_lock:
            hooks = list(self._wave_hooks)  # snapshot
        for hook in hooks:
            if hook.when.type == "on_retry":
                hook._fire(self, {"attempt": attempt})

    def _handle_on_cancel(self, skipped: bool = False) -> None:
        """Fire all on_cancel hooks.  Called from :meth:`cancel`."""
        with self._wave_lock:
            hooks = list(self._wave_hooks)  # snapshot
        for hook in hooks:
            if hook.when.type == "on_cancel":
                hook._fire(self, {"skipped": skipped})

    # ------------------------------------------------------------------
    # Internal: data_equals check
    # ------------------------------------------------------------------

    def _check_data_equals_hooks(self, hooks: list["Hook"], data: dict[str, str]) -> None:
        """Fire any data_equals hooks whose condition is now satisfied.

        Called from ``update_parsed_data`` with a post-update snapshot captured
        while ``_wave_lock`` was still held.
        """
        for hook in hooks:
            if hook.when.type == "data_equals":
                key = hook.when.key
                actual = data.get(key)  # type: ignore[arg-type]
                if actual == hook.when.value:
                    hook._fire(self, {"key": key, "value": actual})

    # ------------------------------------------------------------------
    # Internal: timer-driven (called by Session's timer thread)
    # ------------------------------------------------------------------

    def _check_timer_hooks(self) -> None:
        """Fire elapsed_exceeds hooks if the job has run long enough.

        Called by Session's timer thread approximately every second while
        this job's status is RUNNING.
        """
        start = self.start_time  # type: ignore[attr-defined]  # Job.start_time
        if start is None:
            return
        elapsed = time.monotonic() - start
        with self._wave_lock:
            hooks = list(self._wave_hooks)  # snapshot
        for hook in hooks:
            if hook.when.type == "elapsed_exceeds":
                if hook.when.seconds is None:
                    continue  # invariant: never happens, but be safe
                if elapsed >= hook.when.seconds:
                    hook._fire(self, {"elapsed": elapsed})


# ---------------------------------------------------------------------------
# Concrete Wave job classes
# ---------------------------------------------------------------------------

class WaveCmdJob(WaveJobMixin, _CmdJob):
    """CmdJob with Wave observability: parsed_data, events, hooks, parsers."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._wave_init()
        # CmdJob wraps a subprocess — SIGINT is the natural graceful stop.
        # Users can override via set_stop_policy() if their process needs
        # different handling (e.g. stdin "quit" command).
        self.set_stop_policy(graceful_signal=signal.SIGINT)


class WaveFuncJob(WaveJobMixin, _FuncJob):
    """FuncJob with Wave observability: events, hooks (on_done/on_fail/elapsed).

    Note: ``add_parser`` and ``log_matches`` hooks are of limited use for
    FuncJob because it doesn't produce log lines.  Use ``emit()`` to record
    structured events, or ``update_parsed_data()`` directly.
    The ``on_done``, ``on_fail``, and ``elapsed_exceeds`` hooks work normally.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._wave_init()


