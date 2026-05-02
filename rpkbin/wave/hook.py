"""
hook.py - Hook and HookWhen definitions for Wave.

A Hook binds a *when* condition (HookWhen) to an *action* callable.

Supported trigger types
-----------------------
log_matches     : fires when a log line matches a regex pattern
data_equals     : fires when job.parsed_data[key] == value
elapsed_exceeds : fires when job elapsed time >= seconds  (timer-driven)
on_start        : fires when the job transitions to RUNNING  (MRO-driven)
on_done         : fires when the job reaches DONE status  (event-driven)
on_fail         : fires when the job reaches FAILED status (event-driven)
on_retry        : fires when a job fails and is reset for retry (event-driven)
on_cancel       : fires when a job is cancelled or skipped (MRO-driven)

Design note: ``on_status`` from the original spec is replaced by the more
reliable ``on_done`` / ``on_fail`` pair.  Those are event-driven (wired into
the scheduler's on_done / on_fail callbacks), so they can never be missed by
polling.  Timer-driven ``on_status("running")`` checks were inherently racy
for fast-completing jobs.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    from rpkbin.wave.job import WaveJobMixin  # avoid circular import at runtime

logger = logging.getLogger(__name__)
HookAction = Callable[["WaveJobMixin", dict], None]
SLOW_HOOK_ACTION_WARNING_S = 1.0


# ---------------------------------------------------------------------------
# HookWhen - pure data describing the trigger condition
# ---------------------------------------------------------------------------

HookWhenType = Literal[
    "log_matches",
    "data_equals",
    "elapsed_exceeds",
    "on_start",
    "on_done",
    "on_fail",
    "on_retry",
    "on_cancel",
]


@dataclass
class HookWhen:
    """Descriptor for a hook trigger condition.

    Only the fields relevant to the chosen ``type`` need to be set.
    All others remain ``None``.
    """

    type: HookWhenType

    # log_matches
    pattern: re.Pattern | None = None

    # data_equals
    key: str | None = None
    value: str | None = None

    # elapsed_exceeds
    seconds: float | None = None

    # (on_start / on_done / on_fail carry no extra payload)


# ---------------------------------------------------------------------------
# Hook - binds a HookWhen to an action callable
# ---------------------------------------------------------------------------

class Hook:
    """A reactive rule: *when* some condition holds, run *action*.

    Parameters
    ----------
    when:
        A :class:`HookWhen` instance describing the trigger condition.
        Use the static factory methods (``Hook.log_matches``, etc.) for
        convenience.
    action:
        ``(job, ctx) -> None``.  ``ctx`` is a dict with context keys
        relevant to the trigger type (see below).  Exceptions inside
        ``action`` are logged and swallowed; hooks must never crash a job.
    policy:
        ``"once"`` (default): fire at most once per Hook instance.
        ``"always"``: fire every time the condition is satisfied.
        ``"every_n"``: fire every *n*-th time the condition is satisfied.
    n:
        Used only when ``policy="every_n"``.  Must be >= 1.

    Context keys per trigger type
    ------------------------------
    log_matches     : {"match": re.Match, "line": str}
    data_equals     : {"key": str, "value": str}
    elapsed_exceeds : {"elapsed": float}
    on_start        : {}
    on_done         : {}
    on_fail         : {"error": str}
    on_retry        : {"attempt": int}
    on_cancel       : {"skipped": bool}
    """

    def __init__(
        self,
        when: HookWhen,
        action: HookAction,
        policy: Literal["once", "always", "every_n"] = "once",
        n: int = 1,
    ) -> None:
        if policy == "every_n" and n < 1:
            raise ValueError(f"Hook: n must be >= 1, got {n!r}")
        self.when = when
        self.action = action
        self.policy = policy
        self.n = n
        self._exhausted = False
        self._fire_count: int = 0
        self._lock = threading.Lock()  # protect once/every_n state from concurrent fires

    # ------------------------------------------------------------------
    # Internal trigger
    # ------------------------------------------------------------------

    def _fire(self, job: "WaveJobMixin", ctx: dict) -> None:
        """Fire the hook action if policy allows.

        Thread-safe: multiple threads (log reader, timer, etc.) may race
        to fire the same hook.  The lock ensures exactly-once semantics
        under ``policy="once"``.
        """
        with self._lock:
            if self.policy == "once":
                if self._exhausted:
                    return
                self._exhausted = True
            self._fire_count += 1
            if self.policy == "every_n" and self._fire_count % self.n != 0:
                return

        try:
            t0 = time.perf_counter()
            self.action(job, ctx)  # type: ignore[arg-type]
            dt = time.perf_counter() - t0
            if dt > SLOW_HOOK_ACTION_WARNING_S:
                logger.warning(
                    "Slow hook action detected for hook type=%s (took %.2fs). "
                    "Avoid blocking work inside hook actions.",
                    self.when.type,
                    dt,
                )
        except Exception:
            logger.exception(
                "Hook action raised an exception (ignored). "
                "Hook type=%s, policy=%s.",
                self.when.type, self.policy,
            )
            # Surface the failure as a job event so it is visible in the TUI
            # Events panel even when the log console is not being watched.
            if hasattr(job, "emit"):
                try:
                    job.emit("hook_error", f"Hook type={self.when.type!r} raised an exception (see log)", source="system")
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Factory methods - convenience constructors for HookWhen
    # ------------------------------------------------------------------

    @staticmethod
    def log_matches(pattern: str) -> HookWhen:
        """Trigger when a log line matches *pattern* (regex)."""
        return HookWhen(type="log_matches", pattern=re.compile(pattern))

    @staticmethod
    def data_equals(key: str, value: str) -> HookWhen:
        """Trigger when ``job.parsed_data[key] == value``."""
        return HookWhen(type="data_equals", key=key, value=value)

    @staticmethod
    def elapsed_exceeds(seconds: float) -> HookWhen:
        """Trigger when the job has been running for *seconds* or more."""
        return HookWhen(type="elapsed_exceeds", seconds=seconds)

    @staticmethod
    def on_start() -> HookWhen:
        """Trigger when the job transitions to RUNNING (just before execution begins)."""
        return HookWhen(type="on_start")

    @staticmethod
    def on_done() -> HookWhen:
        """Trigger when the job completes successfully (DONE)."""
        return HookWhen(type="on_done")

    @staticmethod
    def on_fail() -> HookWhen:
        """Trigger when the job fails (FAILED)."""
        return HookWhen(type="on_fail")

    @staticmethod
    def on_retry() -> HookWhen:
        """Trigger when the job fails but is being reset for a retry.
        
        The context dictionary provided to the action will contain:
        - `attempt`: The retry attempt number (1 for first retry, etc.).
        """
        return HookWhen(type="on_retry")

    @staticmethod
    def on_cancel() -> HookWhen:
        """Trigger when the job is cancelled.

        Fired synchronously from :meth:`WaveJobMixin.cancel` **before** the
        cancel propagates further, so the job's status is already
        ``CANCELLED`` when the action runs.  Actions should be lightweight;
        avoid blocking calls.
        """
        return HookWhen(type="on_cancel")

    # ------------------------------------------------------------------
    # Action factories - pre-built helpers
    # ------------------------------------------------------------------

    @staticmethod
    def action_request_stop(force: bool = False) -> HookAction:
        """Stop the job according to its configured stop policy.

        Preferred over ``action_kill()`` when the job has a graceful stop
        policy configured (e.g. ``job.set_stop_policy(...)``).  Falls back
        to a force-cancel for plain scheduler jobs that do not implement
        ``request_stop()``.

        Parameters
        ----------
        force:
            If ``True``, bypass the graceful policy and force-kill immediately.
        """
        def _action(job, ctx):
            if hasattr(job, "request_stop"):
                job.request_stop(force=force)  # type: ignore[attr-defined]
            else:
                job.cancel()  # type: ignore[attr-defined]
        return _action

    @staticmethod
    def action_kill() -> HookAction:
        """Cancel the job and force-kill the underlying process.

        Note: This triggers cancellation by calling the job's ``cancel()``
        method, which in turn calls ``kill()`` directly on the process
        if applicable (e.g., for ``CmdJob``, sending SIGTERM then SIGKILL).
        This also triggers any ``on_cancel`` hooks before the process is killed.
        """
        def _action(job, ctx):
            job.cancel()  # type: ignore[attr-defined]
        return _action

    @staticmethod
    def action_send_signal(sig: int) -> HookAction:
        """Send an OS signal to the running job's process.

        Common values::

            import signal
            Hook.action_send_signal(signal.SIGINT)   # Ctrl+C / VCS UCLI
            Hook.action_send_signal(signal.SIGTERM)  # polite termination

        Only meaningful for ``CmdJob``; silently no-ops on ``FuncJob``.
        """
        def _action(job, ctx):
            if hasattr(job, "send_signal"):
                job.send_signal(sig)  # type: ignore[attr-defined]
            else:
                logger.warning(
                    "Hook.action_send_signal ignored: job %r does not support send_signal().",
                    getattr(job, "name", job),
                )
        return _action

    @staticmethod
    def action_send_input(text: str) -> HookAction:
        """Write *text* to the job's stdin.

        Only meaningful for ``CmdJob`` (requires ``stdin=PIPE``).
        Silently no-ops if the job is not running or has no stdin.
        """
        def _action(job, ctx):
            if hasattr(job, "send_input"):
                try:
                    job.send_input(text)  # type: ignore[attr-defined]
                except RuntimeError:
                    pass
            else:
                logger.warning(
                    "Hook.action_send_input ignored: job %r does not support send_input().",
                    getattr(job, "name", job),
                )
        return _action

    @staticmethod
    def action_emit(tag: str, message: str) -> HookAction:
        """Record a named event on the job.

        The event is visible via ``job.peek_events()`` and in the TUI
        Events panel.
        """
        def _action(job, ctx):
            if hasattr(job, "emit"):
                job.emit(tag, message)  # type: ignore[attr-defined]
            else:
                logger.warning(
                    "Hook.action_emit ignored: job %r does not support emit().",
                    getattr(job, "name", job),
                )
        return _action

    @staticmethod
    def action_set_data(key: str, value: str) -> HookAction:
        """Manually set *key* in ``parsed_data`` to *value*.

        Useful for manually advancing an FSM state or injecting a sentinel
        when the log output doesn't have a machine-readable form.
        """
        def _action(job, ctx):
            if hasattr(job, "update_parsed_data"):
                job.update_parsed_data({key: value})  # type: ignore[attr-defined]
            else:
                logger.warning(
                    "Hook.action_set_data ignored: job %r does not support update_parsed_data().",
                    getattr(job, "name", job),
                )
        return _action

    @staticmethod
    def action_chain(*actions: HookAction) -> HookAction:
        """Run multiple actions in sequence.

        If one action raises, the exception is logged and the remaining
        actions still run.

        Example - graceful shutdown flow::

            Hook.action_chain(
                Hook.action_send_input("exit\\n"),
                Hook.action_emit("shutdown", "graceful exit requested"),
            )
        """
        _log = logging.getLogger(__name__)

        def _action(job, ctx):
            for act in actions:
                try:
                    act(job, ctx)
                except Exception:
                    _log.exception(
                        "Hook.action_chain: action %r raised (ignored).", act
                    )
        return _action
