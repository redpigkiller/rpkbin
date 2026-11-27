"""Controls bound to an attempt and the stop sequence."""

from __future__ import annotations

import contextlib
import signal as _signal_mod
import threading
import time

from ._job_attempt import _JobEvents
from ._events import (
    PROCESS_CAPABILITIES,
    Line,
)
from .model import ControlReceipt, JobStatus, StopPolicy

_SIGTSTP = getattr(_signal_mod, "SIGTSTP", None)
_SIGCONT = getattr(_signal_mod, "SIGCONT", None)
_NON_INTERRUPTING_SIGNALS = frozenset(
    int(sig_val)
    for name in ("SIGUSR1", "SIGUSR2", "SIGTSTP", "SIGCONT")
    if (sig_val := getattr(_signal_mod, name, None)) is not None
)


class _ProcessControl:
    """Automation controls remain bound to their original execution attempt."""

    capabilities = PROCESS_CAPABILITIES

    def __init__(self, job, attempt):
        self._job, self._attempt = job, attempt

    def __getattr__(self, name):
        a, j = self._attempt, self._job
        if name in (
            "send",
            "send_signal",
            "send_ctrl_c",
            "stop",
            "kill",
        ):
            return lambda *args, **kwargs: getattr(j, name)(
                *args, expected_attempt=a.id, **kwargs
            )
        raise AttributeError(name)


class _JobControls:
    def _control_target(self, expected_attempt, expected_submission=None):
        try:
            return self._guard(expected_attempt, expected_submission), None
        except ValueError as exc:
            return None, ControlReceipt("rejected", str(exc))

    def _control_attempt(self, capability, expected_attempt, *, allow_pending=False):
        a, receipt = self._control_target(expected_attempt)
        if receipt is not None:
            return a, receipt
        if not getattr(a.capabilities, capability):
            return a, ControlReceipt("rejected", f"Attempt does not support {capability.removeprefix('can_')}")
        if a.finalized or a.ended is not None or a.status.is_finished:
            return a, ControlReceipt("rejected", "Attempt has already finished")
        if a.process is None:
            if allow_pending and a.status is JobStatus.PENDING:
                return a, None
            return a, ControlReceipt("rejected", "No process is available")
        try:
            if a.process.poll() is not None:
                return a, ControlReceipt("rejected", "Process has already exited")
        except Exception as exc:
            return a, ControlReceipt("failed", f"Could not inspect process state: {type(exc).__name__}: {exc}"[:2048])
        if not allow_pending and a.status not in (JobStatus.RUNNING, JobStatus.WAITING_INPUT):
            return a, ControlReceipt("rejected", f"Control unavailable while {a.status.value}")
        return a, None

    def _control_lock_conflict(self):
        owner = _JobEvents.held()
        return owner is not None and owner is not self._events_lock

    def send(self, text, *, newline=True, secret=False, expected_attempt=None):
        if not isinstance(text, str):
            return ControlReceipt("rejected", "send requires text")
        if not isinstance(secret, bool):
            return ControlReceipt("rejected", "secret must be bool")
        if self._control_lock_conflict():
            return ControlReceipt("rejected", "Cannot control another Job from a Job event")
        with self._events_lock:
            a, receipt = self._control_attempt("can_send", expected_attempt)
            if receipt is not None:
                return receipt
            proc, was_waiting = a.process, a.status is JobStatus.WAITING_INPUT
        # The transport write can block for its full deadline; never hold the
        # event lock meanwhile, or output, status and clock events stall.
        data = text + ("\n" if newline and not text.endswith("\n") else "")
        try:
            data.encode(self.encoding)
        except UnicodeEncodeError as exc:
            reason = (
                "Secret input cannot be encoded"
                if secret
                else f"Input cannot be encoded as {self.encoding}: {exc}"[:2048]
            )
            return ControlReceipt("rejected", reason)
        try:
            if secret:  # Only a secret needs the keyword (test doubles lack it).
                proc.write(data, secret=True)
            else:
                proc.write(data)
        except UnicodeEncodeError as exc:
            reason = (
                "Secret input cannot be encoded"
                if secret
                else f"Input cannot be encoded as {self.encoding}: {exc}"[:2048]
            )
            return ControlReceipt("rejected", reason)
        except Exception as exc:
            reason = "Secret input write failed" if secret else f"Input write failed: {type(exc).__name__}: {exc}"[:2048]
            with self._events_lock:
                if (
                    self._attempt is not a
                    or a.finalized
                    or a.ended is not None
                    or a.status.is_finished
                ):
                    if isinstance(exc, (BrokenPipeError, ProcessLookupError)):
                        return ControlReceipt("rejected", "Process has already exited")
                    return ControlReceipt("failed", reason)
                self._error(a, "send", RuntimeError(reason) if secret else exc)
            return ControlReceipt("failed", reason)
        with self._events_lock:
            if (
                self._attempt is a
                and not a.finalized
                and a.ended is None
                and not a.status.is_finished
            ):
                message = "Input sent: ***" if secret else f"Input sent: {text.rstrip()}"
                a.sink.emit(Line(message, "wave", monitor=False))
                if was_waiting and a.status is JobStatus.WAITING_INPUT:
                    self._set_status(a, JobStatus.RUNNING)
        return ControlReceipt("accepted", "Input sent: ***" if secret else "Input sent")

    def send_signal(self, sig, *, source="user", expected_attempt=None):
        if isinstance(sig, bool) or not isinstance(sig, int) or sig < 0:
            return ControlReceipt("rejected", "Signal must be an integer")
        return self._signal(sig, expected_attempt, source)

    def send_ctrl_c(self, *, source="user", expected_attempt=None):
        return self._signal(None, expected_attempt, source)

    def _signal(self, sig, expected_attempt, source):
        if self._control_lock_conflict():
            return ControlReceipt("rejected", "Cannot control another Job from a Job event")
        with self._events_lock:
            capability = "can_interrupt" if sig is None else "can_signal"
            a, receipt = self._control_attempt(capability, expected_attempt)
            if receipt is not None:
                return receipt
            try:
                a.process.interrupt() if sig is None else a.process.send_signal(sig)
            except ValueError as exc:
                return ControlReceipt("rejected", str(exc) or "Invalid signal")
            except Exception as exc:
                if isinstance(exc, ProcessLookupError) or (
                    isinstance(exc, OSError) and a.process.poll() is not None
                ):
                    # Lost the race with a normal exit; not an execution error.
                    return ControlReceipt("rejected", "Process has already exited")
                self._error(a, "signal", exc)
                return ControlReceipt("failed", f"Signal delivery failed: {type(exc).__name__}: {exc}"[:2048])
            if _SIGTSTP is not None and sig == int(_SIGTSTP):
                a.suspended = True
            elif _SIGCONT is not None and sig == int(_SIGCONT):
                a.suspended = False
            if sig is None or sig not in _NON_INTERRUPTING_SIGNALS:
                a.interrupt_requested = True
                a.interrupt_source = source
            a.sink.emit(Line(
                f"Signal sent: {'interrupt' if sig is None else sig}", "wave", monitor=False
            ))
            return ControlReceipt("accepted", "Interrupt sent" if sig is None else "Signal sent")

    def stop(self, *, source="user", reason=None, expected_attempt=None, force=False):
        if self._control_lock_conflict():
            return ControlReceipt("rejected", "Cannot control another Job from a Job event")
        with self._events_lock:
            capability = "can_kill" if force else "can_stop"
            a, receipt = self._control_attempt(capability, expected_attempt, allow_pending=True)
            if receipt is not None:
                return receipt
            force_stop = force or self.stop_policy is StopPolicy.FORCE
            if force_stop and not a.capabilities.can_kill:
                return ControlReceipt("rejected", "Attempt does not support the configured force stop")
            if a.process is not None and not force_stop:
                if self.stop_ctrl_c and not a.capabilities.can_interrupt:
                    return ControlReceipt("rejected", "Attempt does not support the configured stop interrupt")
                if self.stop_cmd and not a.capabilities.can_send:
                    return ControlReceipt("rejected", "Attempt does not support the configured stop command")
            requested = "FORCE" if force else self.stop_policy.value
            entry = dict(
                source=source,
                reason=reason or "stop requested",
                requested=requested,
                actual=None,
                time=time.time(),
            )
            if not any(
                e["source"] == entry["source"]
                and e["reason"] == entry["reason"]
                and e["requested"] == requested
                for e in a.termination
            ):
                a.termination.append(entry)
            a.stop_requested = True
            a.force_requested |= force_stop
            manager = self._manager_ref
            guard = manager._cond if manager is not None else self._lock
            cancelled = False
            with guard:
                if a.status is JobStatus.PENDING and not a.reserved:
                    # Block scheduler reservation before releasing its condition;
                    # perform callbacks only after that condition is released.
                    a.status = JobStatus.CANCELLED
                    cancelled = True
            if cancelled:
                self._finish(a, cancelled=True)
                if manager:
                    manager._cancelled(self, a)
                return ControlReceipt("accepted", "Pending attempt cancelled")
            return self._begin_stop(a)

    def kill(self, *, expected_attempt=None, source="user", reason=None):
        return self.stop(force=True, expected_attempt=expected_attempt, source=source, reason=reason)

    def _begin_stop(self, a):
        if a.process is None:
            # A dispatched attempt checks this before it spawns.
            return ControlReceipt("accepted", "Stop requested before process start")
        if a.force_requested:
            if a.force_sent:
                return ControlReceipt("accepted", "Force stop already requested")
            was_suspended = a.suspended
            a.suspended = False
            if was_suspended and _SIGCONT is not None and a.process.poll() is None:
                with contextlib.suppress(OSError):
                    a.process.send_signal(int(_SIGCONT))
            try:
                a.process.kill()
            except Exception as exc:
                self._error(a, "force_stop", exc)
                return ControlReceipt("failed", f"Force stop failed: {type(exc).__name__}: {exc}"[:2048])
            a.force_sent = True
            for entry in a.termination:
                entry["actual"] = "FORCE"
            self._set_status(a, JobStatus.STOPPING)
            return ControlReceipt("accepted", "Force stop sent")
        if a.stop_started:
            return ControlReceipt("accepted", "Stop already requested")
        a.stop_started = True
        was_suspended = a.suspended
        a.suspended = False

        def sequence():
            process = a.process  # Never consult a later attempt's process.
            deadline = time.monotonic() + self.stop_timeout
            try:
                if was_suspended and _SIGCONT is not None and process.poll() is None:
                    with contextlib.suppress(OSError):
                        process.send_signal(int(_SIGCONT))
                if self.stop_ctrl_c and process.poll() is None:
                    process.interrupt()
                if self.stop_cmd and process.poll() is None:
                    process.write(
                        self.stop_cmd
                        + ("\n" if not self.stop_cmd.endswith("\n") else "")
                    )
                elif not self.stop_ctrl_c and process.poll() is None:
                    process.terminate()
                with self._events_lock:
                    for entry in a.termination:
                        if not a.finalized and entry["actual"] is None:
                            entry["actual"] = "GRACEFUL"
            except Exception as exc:
                if not isinstance(exc, (BrokenPipeError, ProcessLookupError)) or (
                    process.poll() is None
                ):
                    with self._events_lock:
                        self._error(a, "stop", exc)
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            if process.poll() is None:
                with self._events_lock:
                    if a.finalized or a.force_sent or not a.capabilities.can_kill:
                        return
                    a.sink.emit(Line(
                        "Stop timeout reached; escalating to force stop.", "wave", monitor=False
                    ))
                    try:
                        process.kill()
                    except Exception as exc:
                        self._error(a, "stop", exc)
                    if not a.finalized:
                        for entry in a.termination:
                            entry["actual"] = "FORCE"

        a.stop_thread = threading.Thread(
            target=sequence, name=f"Wave-stop-{a.id}", daemon=True
        )
        try:
            a.stop_thread.start()
        except Exception as exc:
            a.stop_started = False
            a.stop_thread = None
            self._error(a, "stop", exc)
            return ControlReceipt("failed", f"Stop request failed: {type(exc).__name__}: {exc}"[:2048])
        self._set_status(a, JobStatus.STOPPING)
        return ControlReceipt("accepted", "Stop requested")
