"""External command definitions and isolated, bounded execution attempts."""

from __future__ import annotations

import codecs
import contextvars
import math
import os
import re
import shlex
import threading
import time
import uuid
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Sequence

from .model import (
    Check,
    CheckState,
    Event,
    EventRef,
    Events,
    Expect,
    JobStatus,
    Report,
    Result,
    StopPolicy,
    Subscription,
    Track,
    freeze,
    matcher,
)

_CALLBACK = contextvars.ContextVar("wave_callback_attempt", default=None)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _positive(value, name, *, zero=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (value < 0 if zero else value <= 0)
    ):
        raise ValueError(
            f"{name} must be a finite {'nonnegative' if zero else 'positive'} number"
        )
    return value


def _integer(value, name, *, minimum=None):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or (minimum is not None and value < minimum)
    ):
        raise ValueError(
            f"{name} must be an integer"
            + (f" >= {minimum}" if minimum is not None else "")
        )
    return value


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
    data: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    warnings: dict = field(default_factory=dict)
    errors: deque = field(default_factory=lambda: deque(maxlen=100))
    diagnostics: deque = field(default_factory=lambda: deque(maxlen=100))
    error_count: int = 0
    termination: deque = field(default_factory=lambda: deque(maxlen=100))
    disabled: set = field(default_factory=set)
    fired: set = field(default_factory=set)
    last_tick: dict = field(default_factory=dict)
    records: deque = field(init=False)
    total_lines: int = 0
    stop_requested: bool = False
    force_requested: bool = False
    stop_started: bool = False
    timed_out: bool = False
    finalized: bool = False
    retry_scheduled: bool = False
    report: Report | None = None
    pending_reason: str = "Not started"
    process: Any = None
    reader_done: threading.Event = field(default_factory=threading.Event)
    reserved: bool = False
    stop_thread: Any = None
    execution_failed: bool = False
    previous_status: JobStatus = JobStatus.PENDING
    force_sent: bool = False
    priority: int = 0

    def __post_init__(self):
        self.records = deque(maxlen=self.max_lines)

    @property
    def elapsed(self):
        if self.started_mono is None:
            return 0.0
        return max(
            0.0,
            (self.ended_mono if self.ended_mono is not None else time.monotonic())
            - self.started_mono,
        )


class _BoundJob:
    """Callbacks observe their original attempt; controls cannot follow a rerun."""

    def __init__(self, job, attempt):
        self._job, self._attempt = job, attempt

    def __getattr__(self, name):
        a, j = self._attempt, self._job
        if name == "context":
            return a.context
        if name == "data":
            return freeze(dict(a.data))
        if name == "result":
            return j._report(a)
        if name == "status":
            return a.status
        if name == "elapsed":
            return a.elapsed
        if name == "attempt_id":
            return a.id
        if name == "submission_id":
            return a.submission_id
        if name == "attempt_number":
            return a.number
        if name in ("returncode", "pid"):
            return getattr(a, name)
        if name == "logs":
            return lambda tail=None: j._logs(a, tail)
        if name == "log":
            return lambda text: j._record(a, str(text), "wave")
        if name in (
            "send",
            "send_signal",
            "send_ctrl_c",
            "stop",
            "kill",
            "set_max_log_lines",
        ):
            return lambda *args, **kwargs: getattr(j, name)(
                *args, expected_attempt=a.id, **kwargs
            )
        return getattr(j, name)


class Job:
    """A frozen command definition. Monitoring and data are isolated per attempt."""

    _DEFINITION = frozenset(
        (
            "name",
            "cmd",
            "cwd",
            "env",
            "pty",
            "prompts",
            "pty_rows",
            "pty_cols",
            "resources",
            "timeout",
            "max_retries",
            "stop_policy",
            "stop_ctrl_c",
            "stop_cmd",
            "stop_timeout",
            "max_log_lines",
            "log_file",
        )
    )

    def __setattr__(self, name, value):
        if name in ("id", "events") and name in self.__dict__:
            raise AttributeError(f"{name} is immutable")
        if getattr(self, "_frozen", False) and name in self._DEFINITION:
            raise AttributeError(
                f"{name} is frozen; define a new Job to change its meaning"
            )
        object.__setattr__(self, name, value)

    def __init__(
        self,
        name: str,
        cmd: str | Sequence[str],
        *,
        cwd=None,
        env=None,
        pty=None,
        prompts=(),
        pty_rows=None,
        pty_cols=None,
        resources=None,
        priority=0,
        timeout=None,
        max_retries=0,
        stop_policy=StopPolicy.GRACEFUL_THEN_FORCE,
        stop_ctrl_c=False,
        stop_cmd=None,
        stop_timeout=3.0,
        max_log_lines=10000,
        log_file=None,
    ):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be nonempty")
        if not isinstance(cmd, str) and (
            not isinstance(cmd, Sequence) or not all(isinstance(x, str) for x in cmd)
        ):
            raise TypeError("cmd must be a command string or sequence of strings")
        if (
            not cmd
            or (isinstance(cmd, str) and not cmd.strip())
            or (not isinstance(cmd, str) and not cmd[0])
        ):
            raise ValueError("cmd must be nonempty")
        if not isinstance(stop_policy, StopPolicy):
            raise TypeError("use a StopPolicy enum")
        if not isinstance(stop_ctrl_c, bool):
            raise TypeError("stop_ctrl_c must be bool")
        if stop_cmd is not None and not isinstance(stop_cmd, str):
            raise TypeError("stop_cmd must be text")
        if not isinstance(prompts, (tuple, list)) or not all(
            isinstance(x, str) and x for x in prompts
        ):
            raise ValueError("prompts must be a sequence of nonempty literal tokens")
        self._frozen = False
        self.id = "job_" + uuid.uuid4().hex
        self.name, self.cmd = name.strip(), cmd if isinstance(cmd, str) else tuple(cmd)
        self.cwd = str(Path(cwd).resolve()) if cwd is not None else None
        self.env = dict(env or {})
        if not all(
            isinstance(k, str) and isinstance(v, str) for k, v in self.env.items()
        ):
            raise TypeError("env must contain text keys and values")
        self.pty = os.name == "posix" if pty is None else pty
        if not isinstance(self.pty, bool):
            raise TypeError("pty must be bool")
        if self.pty and os.name != "posix":
            raise ValueError("PTY is available on POSIX; Windows uses pipes")
        if (pty_rows is not None or pty_cols is not None) and not self.pty:
            raise ValueError("PTY dimensions require pty=True")
        self.pty_rows = (
            None if pty_rows is None else _integer(pty_rows, "pty_rows", minimum=1)
        )
        self.pty_cols = (
            None if pty_cols is None else _integer(pty_cols, "pty_cols", minimum=1)
        )
        if any(x is not None and x > 65535 for x in (pty_rows, pty_cols)):
            raise ValueError("PTY dimensions must be <= 65535")
        self.prompts = tuple(prompts)
        self.resources = dict(resources or {})
        for key, count in self.resources.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("resource names must be nonempty")
            _integer(count, f"resource {key}", minimum=1)
        self._priority = _integer(priority, "priority")
        self.timeout = None if timeout is None else float(_positive(timeout, "timeout"))
        self.max_retries = _integer(max_retries, "max_retries", minimum=0)
        self.stop_policy, self.stop_ctrl_c, self.stop_cmd = (
            stop_policy,
            stop_ctrl_c,
            stop_cmd,
        )
        self.stop_timeout = float(_positive(stop_timeout, "stop_timeout"))
        self.max_log_lines = _integer(max_log_lines, "max_log_lines", minimum=1)
        self.log_file = Path(log_file) if log_file is not None else None
        self._lock, self._events_lock = threading.RLock(), threading.RLock()
        self._tracks, self._checks, self._subscriptions = [], [], []
        self._names = set()
        self.events = Events(self, "error", "status_changed", "attempt_finished")
        self._manager_ref = None
        self._history = deque()
        self._history_truncated = 0
        self._attempt_counter = 0
        self._transaction = False
        self._event_queue, self._actions = [], []
        self._attempt = self._new_attempt(uuid.uuid4().hex, 0)

    @property
    def priority(self):
        return self._priority

    @property
    def status(self):
        return self._attempt.status

    @property
    def returncode(self):
        return self._attempt.returncode

    @property
    def pid(self):
        return self._attempt.pid

    @property
    def elapsed(self):
        return self._attempt.elapsed

    @property
    def elapsed_str(self):
        return self._elapsed_str(self.elapsed)

    @property
    def data(self):
        return freeze(dict(self._attempt.data))

    @property
    def context(self):
        return self._attempt.context

    @property
    def result(self):
        return self._report(self._attempt)

    @property
    def attempt_id(self):
        return self._attempt.id

    @property
    def submission_id(self):
        return self._attempt.submission_id

    @property
    def attempt_number(self):
        return self._attempt.number

    @property
    def cmd_str(self):
        return self.cmd if isinstance(self.cmd, str) else shlex.join(self.cmd)

    @property
    def history(self):
        with self._events_lock:
            return tuple(freeze(self._snapshot_attempt(a)) for a in self._history)

    @staticmethod
    def _elapsed_str(elapsed):
        seconds = int(elapsed)
        return (
            f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
            if seconds >= 3600
            else f"{seconds // 60:02d}:{seconds % 60:02d}"
        )

    def _new_attempt(self, submission, retry):
        self._attempt_counter += 1
        a = _Attempt(
            uuid.uuid4().hex,
            submission,
            self._attempt_counter,
            retry,
            self.max_log_lines,
        )
        a.priority = self._priority
        for check in getattr(self, "_checks", []):
            a.checks[check.name] = dict(
                name=check.name,
                expect=check.expect.value,
                state=CheckState.UNDECIDED.value,
                hits=0,
                evidence=None,
            )
        return a

    def _name(self, name):
        if self._frozen:
            raise RuntimeError("monitoring definitions are frozen")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("monitor name must be nonempty")
        if name in self._names:
            raise ValueError(f"duplicate monitor name: {name}")
        self._names.add(name)
        return name

    def _source(self, source, every):
        if isinstance(source, EventRef):
            owner = source.owner
            if owner is not self and getattr(owner, "job", None) is not self:
                raise ValueError("event belongs to a different Job")
            if source not in owner.events._members.values():
                raise ValueError("unknown event")
        elif not isinstance(source, Event):
            raise TypeError("use Event.LOG, Event.TICK or a job-bound event")
        if source == Event.TICK:
            if every is None:
                raise ValueError("TICK requires every=seconds")
            _positive(every, "every")
        elif every is not None:
            raise ValueError("every is only valid for Event.TICK")

    def track(self, name, pattern, *, cast=str):
        track = Track(self, name, pattern, cast)
        self._name(name)
        self._tracks.append(track)
        return track

    def check(self, name, *, match, expect=Expect.PRESENT, on=Event.LOG, every=None):
        if not isinstance(expect, Expect):
            raise TypeError("use Expect.PRESENT or Expect.ABSENT")
        self._source(on, every)
        if isinstance(on, EventRef) and (
            not isinstance(on.owner, Track) or on.kind != "changed"
        ):
            raise ValueError("checks observe logs, ticks or tracked changes")
        check = Check(self, name, match, expect, on, every)
        if on != Event.LOG and isinstance(check.pattern, re.Pattern):
            raise TypeError("non-text sources require a callable match")
        self._name(name)
        self._checks.append(check)
        self._attempt.checks[name] = dict(
            name=name, expect=expect.value, state="-", hits=0, evidence=None
        )
        return check

    def on(
        self,
        event=Event.LOG,
        *,
        match=None,
        warn=False,
        stop=False,
        callback=None,
        name=None,
        message=None,
        once=True,
        every=None,
    ):
        self._source(event, every)
        if self._frozen:
            raise RuntimeError("monitoring definitions are frozen")
        if not all(isinstance(v, bool) for v in (warn, stop, once)):
            raise TypeError("warn, stop and once must be bool")
        if callback is not None and (not callable(callback) or warn or stop):
            raise ValueError("choose callback OR built-in warn/stop actions")
        if callback is None and not (warn or stop):
            raise ValueError("on needs an action")
        pattern = None if match is None else matcher(match)
        if event != Event.LOG and isinstance(pattern, re.Pattern):
            raise TypeError("non-text sources require callable match")
        if event == Event.LOG and pattern is None and callback is None:
            raise ValueError("log actions require match")
        label = name or f"on_{len(self._subscriptions) + 1}"
        self._name(label)
        subscription = Subscription(
            event, pattern, warn, stop, callback, label, message, once, every
        )
        self._subscriptions.append(subscription)
        return subscription

    def _freeze(self):
        if self._frozen:
            return
        if self.cwd is None:
            self.cwd = os.getcwd()
        if not Path(self.cwd).is_dir():
            raise ValueError(f"cwd is not a directory: {self.cwd}")
        # Exact contradictory declarations can be rejected without guessing callable behavior.
        seen = {}
        for check in self._checks:
            if isinstance(check.pattern, re.Pattern):
                key = (check.source, check.pattern.pattern, check.pattern.flags)
                if key in seen and seen[key] != check.expect:
                    raise ValueError(
                        "the same match cannot be required present and absent"
                    )
                seen[key] = check.expect
        self.env = MappingProxyType(dict(self.env))
        self.resources = MappingProxyType(dict(self.resources))
        self._frozen = True

    def _logs(self, a, tail=None):
        with self._lock:
            lines = [record["text"] for record in a.records]
        return lines[-tail:] if tail is not None and tail > 0 else lines

    def logs(self, tail=None):
        return self._logs(self._attempt, tail)

    def set_max_log_lines(
        self, max_log_lines, *, expected_attempt=None, expected_submission=None
    ):
        lines = _integer(max_log_lines, "max_log_lines", minimum=1)
        with self._events_lock:
            a = self._guard(expected_attempt, expected_submission)
            with self._lock:
                evicted = max(0, len(a.records) - lines)
                object.__setattr__(self, "max_log_lines", lines)
                a.max_lines = lines
                a.records = deque(a.records, maxlen=lines)
                return evicted

    def _record(self, a, text, source):
        with self._lock:
            a.total_lines += 1
            record = dict(
                line_no=a.total_lines, text=text, source=source, time=time.time()
            )
            a.records.append(record)
            path = self.log_file
            if path is None and self._manager_ref and self._manager_ref.log_dir:
                path = self._manager_ref.log_dir / f"{self.id}.log"
            if path is not None:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(f"[{a.id}][{source}] {text}\n")
                except OSError as exc:
                    a.diagnostics.append(
                        dict(source="log_file", message=str(exc), time=time.time())
                    )
        if self._manager_ref:
            self._manager_ref._publish(
                dict(
                    record,
                    job_id=self.id,
                    job_name=self.name,
                    attempt_id=a.id,
                    attempt_number=a.number,
                )
            )
        return dict(line_no=record["line_no"], line=text, time=record["time"])

    def log(self, message):
        attempt = self._callback_attempt()
        self._record(attempt, str(message), "wave")

    def _callback_attempt(self):
        current = _CALLBACK.get()
        return current[1] if current and current[0] is self else self._attempt

    def _error(self, a, source, exc, evidence=None, *, notify=True):
        error = dict(
            source=source,
            message=f"{type(exc).__name__}: {exc}",
            evidence=evidence,
            time=time.time(),
        )
        if a.finalized or not notify:
            a.diagnostics.append(error)
            self._record(a, f"Diagnostic [{source}]: {error['message']}", "wave")
            return
        a.error_count += 1
        a.errors.append(error)
        self._record(a, f"Monitor error [{source}]: {error['message']}", "wave")
        self._event_queue.append((self.events.error, (error,), evidence))

    def _matches(self, pattern, source, args, bound):
        if pattern is None:
            return True
        if isinstance(pattern, re.Pattern):
            return bool(pattern.search(args[0]))
        value = pattern(*args) if source == Event.LOG else pattern(bound, *args)
        if not isinstance(value, bool):
            raise TypeError("match callable must return bool")
        return value

    def _due(self, a, key, every):
        elapsed = a.elapsed
        if elapsed - a.last_tick.get(key, 0.0) < every:
            return False
        a.last_tick[key] = elapsed
        return True

    def _dispatch(self, a, initial, *, final=False):
        """Complete observations/checks before performing any subscribed action."""
        with self._events_lock:
            if self._transaction:
                self._event_queue.extend(initial)
                return
            self._transaction = True
            self._event_queue = [*self._event_queue, *initial]
            self._actions = []
            bound = _BoundJob(self, a)
            token = _CALLBACK.set((self, a))
            try:
                while self._event_queue or self._actions:
                    while self._event_queue:
                        source, args, evidence = self._event_queue.pop(0)
                        for check in self._checks:
                            key = ("check", check.name)
                            state = a.checks[check.name]
                            if (
                                check.source != source
                                or state["state"] != "-"
                                or key in a.disabled
                            ):
                                continue
                            if source == Event.TICK and not self._due(
                                a, key, check.every
                            ):
                                continue
                            try:
                                hit = self._matches(check.pattern, source, args, bound)
                            except Exception as exc:
                                state["state"] = CheckState.ERROR.value
                                state["evidence"] = evidence
                                a.disabled.add(key)
                                self._error(a, check.name, exc, evidence)
                                error = a.errors[-1]
                                self._event_queue.append(
                                    (check.events.error, (error,), evidence)
                                )
                                continue
                            if hit:
                                state["hits"] += 1
                                state["evidence"] = evidence
                                failed = check.expect is Expect.ABSENT
                                state["state"] = (
                                    CheckState.FAIL.value
                                    if failed
                                    else CheckState.OK.value
                                )
                                self._event_queue.append(
                                    (
                                        (
                                            check.events.failed
                                            if failed
                                            else check.events.satisfied
                                        ),
                                        (freeze(dict(state)),),
                                        evidence,
                                    )
                                )
                        for index, subscription in enumerate(self._subscriptions):
                            key = ("on", index)
                            if subscription.source != source or key in a.disabled:
                                continue
                            if source == Event.TICK and not self._due(
                                a, key, subscription.every
                            ):
                                continue
                            try:
                                hit = self._matches(
                                    subscription.pattern, source, args, bound
                                )
                            except Exception as exc:
                                a.disabled.add(key)
                                error_event = (
                                    isinstance(source, EventRef)
                                    and source.kind == "error"
                                )
                                self._error(
                                    a,
                                    subscription.name,
                                    exc,
                                    evidence,
                                    notify=not error_event and not final,
                                )
                                continue
                            if hit:
                                self._actions.append(
                                    (index, subscription, args, evidence, final)
                                )
                    if self._actions:
                        index, subscription, args, evidence, final_action = (
                            self._actions.pop(0)
                        )
                        key = ("on", index)
                        if subscription.warn and not a.finalized:
                            warning = a.warnings.setdefault(
                                subscription.name,
                                dict(
                                    name=subscription.name,
                                    count=0,
                                    first=time.time(),
                                    last=time.time(),
                                    evidence=evidence,
                                ),
                            )
                            warning["count"] += 1
                            warning["last"], warning["evidence"] = time.time(), evidence
                        if subscription.once and key in a.fired:
                            continue
                        a.fired.add(key)
                        try:
                            if subscription.callback:
                                subscription.callback(bound, *args)
                            else:
                                if subscription.warn:
                                    self._record(
                                        a,
                                        f"{'Notification' if a.finalized else 'Warning'} [{subscription.name}]: {subscription.message or 'condition matched'}",
                                        "wave",
                                    )
                                if subscription.stop:
                                    self.stop(
                                        source="monitor",
                                        reason=subscription.message
                                        or subscription.name,
                                        expected_attempt=a.id,
                                    )
                        except Exception as exc:
                            a.disabled.add(key)
                            error_event = (
                                isinstance(subscription.source, EventRef)
                                and subscription.source.kind == "error"
                            )
                            self._error(
                                a,
                                subscription.name,
                                exc,
                                evidence,
                                notify=not final_action and not error_event,
                            )
            finally:
                _CALLBACK.reset(token)
                self._transaction = False
                self._event_queue, self._actions = [], []
            if a.stop_requested and not a.finalized:
                self._begin_stop(a)

    def _line(self, a, text):
        text = strip_ansi(text)
        with self._events_lock:
            evidence = self._record(a, text, "stdout")
            events = [(Event.LOG, (text,), evidence)]
            for track in self._tracks:
                key = ("track", track.name)
                if key in a.disabled:
                    continue
                hit = track.pattern.search(text)
                if hit is None:
                    continue
                try:
                    value = track.cast(hit.group(1))
                    # Snapshots must remain JSON-compatible; fail here rather than breaking HTTP later.
                    import json

                    value = json.loads(json.dumps(value, allow_nan=False))
                except Exception as exc:
                    a.disabled.add(key)
                    self._error(a, track.name, exc, evidence)
                    error = a.errors[-1]
                    events.append((track.events.error, (error,), evidence))
                    for check in self._checks:
                        state = a.checks[check.name]
                        if (
                            check.source == track.events.changed
                            and state["state"] == "-"
                        ):
                            state["state"], state["evidence"] = "ERROR", evidence
                            events.append((check.events.error, (error,), evidence))
                    continue
                old = a.data.get(track.name)
                had = track.name in a.data
                a.data[track.name] = value
                if not had or old != value:
                    events.append((track.events.changed, (old, value), evidence))
            # Track failures can enqueue job.errors before this transaction starts.
            events.extend(self._event_queue)
            self._event_queue = []
            self._dispatch(a, events)

    def _set_status(self, a, status, *, final=False):
        with self._events_lock:
            if a.finalized or (
                a.status is JobStatus.STOPPING
                and status in (JobStatus.RUNNING, JobStatus.WAITING_INPUT)
            ):
                return
            old, a.status = a.status, status
            if old != status:
                self._dispatch(
                    a, [(self.events.status_changed, (old, status), None)], final=final
                )

    def _guard(self, expected_attempt=None, expected_submission=None):
        a = self._attempt
        callback = _CALLBACK.get()
        if expected_attempt is None and callback and callback[0] is self:
            expected_attempt = callback[1].id
        if expected_attempt is not None and a.id != expected_attempt:
            raise ValueError("stale attempt: return to the current execution")
        if expected_submission is not None and a.submission_id != expected_submission:
            raise ValueError("stale submission")
        return a

    def send(self, text, *, newline=True, expected_attempt=None):
        if not isinstance(text, str):
            raise TypeError("send requires text")
        with self._events_lock:
            a = self._guard(expected_attempt)
            if not a.process or a.finalized or a.process.poll() is not None:
                return False
            try:
                a.process.write(
                    text + ("\n" if newline and not text.endswith("\n") else "")
                )
            except Exception as exc:
                self._error(a, "send", exc)
                queued, self._event_queue = list(self._event_queue), []
                self._dispatch(a, queued)
                return False
            self._record(a, f"Input sent: {text.rstrip()}", "wave")
            if a.status is JobStatus.WAITING_INPUT:
                self._set_status(a, JobStatus.RUNNING)
            return True

    def send_signal(self, sig, *, expected_attempt=None):
        return self._signal(sig, expected_attempt)

    def send_ctrl_c(self, *, expected_attempt=None):
        return self._signal(None, expected_attempt)

    def _signal(self, sig, expected_attempt):
        with self._events_lock:
            a = self._guard(expected_attempt)
            if not a.process or a.finalized or a.process.poll() is not None:
                return False
            try:
                a.process.interrupt() if sig is None else a.process.send_signal(sig)
            except Exception as exc:
                self._error(a, "signal", exc)
                queued, self._event_queue = list(self._event_queue), []
                self._dispatch(a, queued)
                return False
            self._record(
                a, f"Signal sent: {'interrupt' if sig is None else sig}", "wave"
            )
            return True

    def stop(self, *, source="user", reason=None, expected_attempt=None, force=False):
        with self._events_lock:
            a = self._guard(expected_attempt)
            if a.finalized:
                return False
            if a.process is not None and a.process.poll() is not None:
                return False
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
            a.force_requested |= force or self.stop_policy is StopPolicy.FORCE
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
                return True
            if not self._transaction:
                self._begin_stop(a)
            return True

    def kill(self, *, expected_attempt=None):
        return self.stop(force=True, expected_attempt=expected_attempt)

    def _begin_stop(self, a):
        if a.process is None:
            return  # A dispatched attempt checks this before it spawns.
        if a.finalized or a.process.poll() is not None:
            return
        if a.force_requested:
            if a.force_sent:
                return
            try:
                a.process.kill()
                a.force_sent = True
                for entry in a.termination:
                    entry["actual"] = "FORCE"
            except Exception as exc:
                self._error(a, "force_stop", exc)
            self._set_status(a, JobStatus.STOPPING)
            return
        if a.stop_started:
            return
        a.stop_started = True
        self._set_status(a, JobStatus.STOPPING)

        def sequence():
            process = a.process  # Never consult a later attempt's process.
            deadline = time.monotonic() + self.stop_timeout
            try:
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
                with self._events_lock:
                    self._error(a, "stop", exc)
                    queued, self._event_queue = list(self._event_queue), []
                    self._dispatch(a, queued)
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            if process.poll() is None:
                with self._events_lock:
                    self._record(
                        a, "Stop timeout reached; escalating to force stop.", "wave"
                    )
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
        a.stop_thread.start()

    def _verdict(self, a, *, complete=False):
        if (
            a.execution_failed
            or a.timed_out
            or any(c["state"] == "FAIL" for c in a.checks.values())
            or (a.returncode is not None and a.returncode != 0 and not a.stop_requested)
        ):
            return Result.FAIL
        if a.error_count:
            return Result.ERROR
        if a.finalized or complete:
            if a.stop_requested or any(
                c["state"] == "INCOMPLETE" for c in a.checks.values()
            ):
                return Result.INCOMPLETE
            return Result.PASS if a.checks else Result.NA
        return None

    def _report(self, a):
        if a.report is not None:
            return a.report
        return Report(
            self._verdict(a),
            freeze(
                dict(status=a.status.value, returncode=a.returncode, elapsed=a.elapsed)
            ),
            tuple(freeze(dict(c)) for c in a.checks.values()),
            tuple(freeze(dict(w)) for w in a.warnings.values()),
            tuple(freeze(dict(e)) for e in a.errors),
            tuple(freeze(dict(t)) for t in a.termination),
        )

    def _finish(self, a, *, cancelled=False):
        with self._events_lock:
            if a.finalized:
                return
            a.ended, a.ended_mono = time.time(), time.monotonic()
            events = []
            for check in self._checks:
                state = a.checks[check.name]
                if state["state"] != "-":
                    continue
                if cancelled or a.stop_requested:
                    state["state"] = CheckState.INCOMPLETE.value
                else:
                    state["state"] = (
                        CheckState.OK.value
                        if check.expect is Expect.ABSENT
                        else CheckState.FAIL.value
                    )
                    events.append(
                        (
                            (
                                check.events.satisfied
                                if state["state"] == "OK"
                                else check.events.failed
                            ),
                            (freeze(dict(state)),),
                            None,
                        )
                    )
            events.extend(self._event_queue)
            self._event_queue = []
            if events:
                self._dispatch(a, events)
            a.previous_status = a.status
            a.status = (
                JobStatus.CANCELLED
                if cancelled or (a.stop_requested and not a.timed_out)
                else (
                    JobStatus.DONE
                    if a.returncode == 0 and not a.timed_out
                    else JobStatus.FAILED
                )
            )
            a.finalized = True
            a.report = self._report(a)

    def _read(self, a):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ""
        try:
            while True:
                chunk = a.process.read()
                if not chunk:
                    break
                buffer += (
                    decoder.decode(chunk).replace("\r\n", "\n").replace("\r", "\n")
                )
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self._output_line(a, line)
                if buffer and any(
                    strip_ansi(buffer).rstrip().endswith(p.rstrip())
                    for p in self.prompts
                ):
                    self._output_line(a, buffer)
                    buffer = ""
            buffer += decoder.decode(b"", final=True)
            if buffer:
                self._output_line(a, buffer)
        except Exception as exc:
            with self._events_lock:
                self._error(a, "output_reader", exc)
                queued, self._event_queue = list(self._event_queue), []
                self._dispatch(a, queued)
        finally:
            a.reader_done.set()

    def _output_line(self, a, text):
        # A configured prompt can end a complete line or a partial read. Publish
        # the state before callbacks, so sending a response can clear it again.
        prompt = any(
            strip_ansi(text).rstrip().endswith(p.rstrip()) for p in self.prompts
        )
        if prompt:
            self._set_status(a, JobStatus.WAITING_INPUT)
        elif a.status is JobStatus.WAITING_INPUT and strip_ansi(text).strip():
            self._set_status(a, JobStatus.RUNNING)
        self._line(a, text)

    def _execute(self, a):
        from ._process import ProcessHandle

        reader, launched = None, False
        try:
            with self._events_lock:
                if a.stop_requested:
                    self._finish(a, cancelled=True)
                    return
                a.started, a.started_mono = time.time(), time.monotonic()
                a.process = ProcessHandle(
                    self.cmd,
                    cwd=self.cwd,
                    env=dict(self.env),
                    pty=self.pty,
                    pty_rows=self.pty_rows,
                    pty_cols=self.pty_cols,
                )
                a.process.start()
                launched = True
                a.pid = a.process.pid
                a.pending_reason = ""
                self._set_status(a, JobStatus.RUNNING)
            reader = threading.Thread(
                target=self._read, args=(a,), daemon=True, name=f"Wave-output-{a.id}"
            )
            reader.start()
            while a.process.poll() is None:
                with self._events_lock:
                    if (
                        self.timeout is not None
                        and a.elapsed >= self.timeout
                        and not a.timed_out
                    ):
                        a.timed_out = True
                        self.stop(
                            source="timeout",
                            reason=f"timeout after {self.timeout:g}s",
                            expected_attempt=a.id,
                        )
                    self._dispatch(a, [(Event.TICK, (a.elapsed,), None)])
                time.sleep(0.025)
            a.returncode = a.process.poll()
            a.process.cleanup()
            reader.join(timeout=3)
            if reader.is_alive():
                self._error(
                    a,
                    "output_reader",
                    RuntimeError("output did not drain within cleanup deadline"),
                )
                a.process.close()
                reader.join(timeout=1)
            if a.stop_thread:
                a.stop_thread.join(timeout=self.stop_timeout + 2)
        except Exception as exc:
            with self._events_lock:
                if not launched:
                    a.execution_failed = True
                    a.returncode = -1
                    a.termination.append(
                        dict(
                            source="execution",
                            reason=f"{type(exc).__name__}: {exc}",
                            requested=None,
                            actual=None,
                            time=time.time(),
                        )
                    )
                    self._record(a, "Execution failed: " + str(exc), "wave")
                else:
                    self._error(a, "runner", exc)
                if a.process:
                    try:
                        a.process.kill()
                    except Exception:
                        pass
        finally:
            if a.process:
                try:
                    a.process.close()
                except Exception:
                    pass
            if reader and reader.is_alive():
                reader.join(timeout=2)
            if a.stop_thread and a.stop_thread.is_alive():
                a.stop_thread.join(timeout=self.stop_timeout + 2)
            self._finish(a)

    def _monitor_definitions(self):
        def describe(value):
            if isinstance(value, re.Pattern):
                return f"regex {value.pattern!r} (flags={value.flags})"
            if isinstance(value, EventRef):
                owner = "job" if value.owner is self else value.owner.name
                return f"{owner}.events.{value.kind}"
            if isinstance(value, Event):
                return f"Event.{value.name}"
            if value is None:
                return "all events"
            return f"callable {getattr(value, '__module__', type(value).__module__)}.{getattr(value, '__qualname__', type(value).__qualname__)}"

        rows = [
            f"track {t.name}: {describe(t.pattern)} -> {describe(t.cast)}"
            for t in self._tracks
        ]
        rows.extend(
            f"check {c.name}: {describe(c.source)}; {describe(c.pattern)}; expect={c.expect.value}"
            + (f"; every={c.every}s" if c.every is not None else "")
            for c in self._checks
        )
        for sub in self._subscriptions:
            actions = ", ".join(
                x
                for x in (
                    "warn" if sub.warn else "",
                    "stop" if sub.stop else "",
                    describe(sub.callback) if sub.callback else "",
                )
                if x
            )
            rows.append(
                f"on {sub.name}: {describe(sub.source)}; {describe(sub.pattern)} -> {actions}; once={sub.once}"
                + (f"; every={sub.every}s" if sub.every is not None else "")
            )
        return rows

    def _snapshot_attempt(
        self, a, *, include_logs=False, include_monitor_definitions=False
    ):
        report = self._report(a)
        out = dict(
            id=self.id,
            name=self.name,
            cmd=self.cmd_str,
            cwd=self.cwd or os.getcwd(),
            env=dict(self.env),
            pty=self.pty,
            prompts=list(self.prompts),
            resources=dict(self.resources),
            priority=a.priority,
            timeout=self.timeout,
            max_retries=self.max_retries,
            stop_policy=self.stop_policy.value,
            stop_ctrl_c=self.stop_ctrl_c,
            stop_cmd=self.stop_cmd,
            stop_timeout=self.stop_timeout,
            status=a.status.value,
            result=report.verdict.value if report.verdict else None,
            returncode=a.returncode,
            pid=a.pid,
            elapsed=round(a.elapsed, 2),
            elapsed_str=self._elapsed_str(a.elapsed),
            pending_reason=a.pending_reason,
            data=dict(a.data),
            checks=[dict(c) for c in a.checks.values()],
            warnings=[dict(w) for w in a.warnings.values()],
            warning_count=sum(w["count"] for w in a.warnings.values()),
            errors=[dict(e) for e in a.errors],
            error_count=a.error_count,
            diagnostics=list(a.diagnostics),
            termination=[dict(t) for t in a.termination],
            attempt_id=a.id,
            submission_id=a.submission_id,
            attempt_number=a.number,
            retry_index=a.retry_index,
            retry_scheduled=a.retry_scheduled,
            log_count=len(a.records),
            total_lines=a.total_lines,
            max_log_lines=self.max_log_lines,
            attempt_max_log_lines=a.max_lines,
            finalized=a.finalized,
            can_rerun=a.finalized and not a.reserved,
            history_truncated=self._history_truncated,
        )
        if include_logs:
            out["logs"] = self._logs(a)
            out["log_records"] = [dict(r) for r in a.records]
        if include_logs or include_monitor_definitions:
            out["monitor_definitions"] = self._monitor_definitions()
        return deepcopy(out)

    def _find_attempt(self, attempt_id):
        a = self._attempt
        if attempt_id and attempt_id != a.id:
            a = next((h for h in self._history if h.id == attempt_id), None)
            if a is None:
                raise ValueError("attempt history is no longer retained")
        return a

    def _log_snapshot(self, *, attempt_id=None, after_line=None, cursor_attempt=None):
        """Read one consistent retained log interval for presentation clients.

        A cursor belongs to an attempt. Missing, mismatched or evicted cursors
        reset to the retained interval; increasing retention cannot restore data.
        This does not truncate input before monitoring or change saved evidence.
        """
        if after_line is not None:
            _integer(after_line, "after_line", minimum=0)
        with self._events_lock, self._lock:
            a = self._find_attempt(attempt_id)
            first = a.records[0]["line_no"] if a.records else a.total_lines + 1
            reset = (
                after_line is None
                or cursor_attempt != a.id
                or after_line < first - 1
                or after_line > a.total_lines
            )
            # The unchanged case must not copy or serialize the whole ring.
            records = (
                [dict(r) for r in a.records if reset or r["line_no"] > after_line]
                if reset or after_line < a.total_lines
                else []
            )
            return dict(
                job_id=self.id,
                attempt_id=a.id,
                first_line=first,
                next_line=a.total_lines,
                retained_count=len(a.records),
                capacity=a.max_lines,
                reset=reset,
                records=records,
            )

    def to_snapshot_dict(
        self, *, include_logs=False, attempt_id=None, include_monitor_definitions=False
    ):
        with self._events_lock:
            a = self._find_attempt(attempt_id)
            out = self._snapshot_attempt(
                a,
                include_logs=include_logs,
                include_monitor_definitions=include_monitor_definitions,
            )
            out["history"] = [
                {
                    k: snapshot[k]
                    for k in (
                        "attempt_id",
                        "attempt_number",
                        "status",
                        "result",
                        "elapsed_str",
                        "returncode",
                        "warning_count",
                        "error_count",
                        "retry_scheduled",
                    )
                }
                for h in self._history
                for snapshot in [self._snapshot_attempt(h)]
            ]
            return out
