"""External command definitions and isolated, bounded execution attempts."""

from __future__ import annotations

import os
import shlex
import threading
import uuid
from collections import deque
from pathlib import Path
from types import MappingProxyType
from typing import Sequence

from ._job_attempt import _Attempt, _JobAttemptLifecycle, _JobEvents
from ._job_config import _integer, _positive
from ._job_control import _JobControls
from ._job_log import _JobLogs
from ._job_output import _JobOutput
from .output import _decode_policy, _output_encoding
from ._job_snapshot import _JobSnapshots
from ._receiver import AttemptReceiver
from .model import StopPolicy, freeze


def _normalize_cmd(cmd):
    if isinstance(cmd, str):
        if not cmd.strip():
            raise ValueError("cmd must be nonempty")
        return cmd
    if isinstance(cmd, os.PathLike):
        part = os.fspath(cmd)
        if not isinstance(part, str):
            raise TypeError("cmd must be a command string or sequence of strings")
        if not part.strip():
            raise ValueError("cmd must be nonempty")
        return (part,)
    if isinstance(cmd, (bytes, bytearray)) or not isinstance(cmd, Sequence):
        raise TypeError("cmd must be a command string or sequence of strings")
    parts = []
    for item in cmd:
        if isinstance(item, os.PathLike):
            item = os.fspath(item)
        if not isinstance(item, str):
            raise TypeError("cmd must be a command string or sequence of strings")
        parts.append(item)
    if not parts or not parts[0]:
        raise ValueError("cmd must be nonempty")
    return tuple(parts)


def _normalize_cwd(cwd):
    if cwd is None:
        return None
    raw = os.fspath(cwd)
    if not isinstance(raw, str):
        raise TypeError("cwd must be text or PathLike")
    if not raw.strip():
        raise ValueError("cwd must not be empty")
    return str(Path(raw).resolve())


def _normalize_log_file(log_file):
    if log_file is None:
        return None
    raw = os.fspath(log_file)
    if not isinstance(raw, str):
        raise TypeError("log_file must be text or PathLike")
    if not raw.strip():
        raise ValueError("log_file must not be empty")
    resolved = Path(raw).absolute()
    if resolved.is_dir():
        raise ValueError(f"log_file must not be a directory: {resolved}")
    return resolved


class Job(_JobAttemptLifecycle, _JobControls, _JobLogs, _JobOutput, _JobSnapshots):
    """A frozen command definition. Monitoring and data are isolated per attempt."""

    _DEFINITION = frozenset(
        (
            "name",
            "cmd",
            "cwd",
            "env",
            "encoding",
            "decode_errors",
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
            "output_drain_timeout",
            "max_log_lines",
            "log_file",
        )
    )

    def __setattr__(self, name, value):
        if name == "id" and name in self.__dict__:
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
        encoding="utf-8",
        decode_errors="replace",
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
        output_drain_timeout=30.0,
        max_log_lines=10000,
        log_file=None,
    ):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be nonempty")
        normalized_cmd = _normalize_cmd(cmd)
        if not isinstance(stop_policy, StopPolicy):
            raise TypeError("use a StopPolicy enum")
        if not isinstance(stop_ctrl_c, bool):
            raise TypeError("stop_ctrl_c must be bool")
        if stop_cmd is not None and not isinstance(stop_cmd, str):
            raise TypeError("stop_cmd must be text")
        if not isinstance(prompts, (tuple, list)) or not all(
            isinstance(x, str) and x.rstrip() for x in prompts
        ):
            raise ValueError("prompts must be a sequence of nonempty literal tokens")
        self._frozen = False
        self.id = "job_" + uuid.uuid4().hex
        self.name, self.cmd = name.strip(), normalized_cmd
        self.cwd = _normalize_cwd(cwd)
        self.env = dict(env or {})
        if not all(
            isinstance(k, str) and isinstance(v, str) for k, v in self.env.items()
        ):
            raise TypeError("env must contain text keys and values")
        self.encoding = _output_encoding(encoding)
        self.decode_errors = _decode_policy(decode_errors)
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
        self.output_drain_timeout = float(_positive(output_drain_timeout, "output_drain_timeout"))
        self.max_log_lines = _integer(max_log_lines, "max_log_lines", minimum=1)
        self.log_file = _normalize_log_file(log_file)
        self._lock, self._events_lock = threading.RLock(), _JobEvents()
        self._manager_ref = None
        self._history = deque()
        self._history_truncated = 0
        self._attempt_counter = 0
        self._receiver_factory = AttemptReceiver
        self._attempt = self._new_attempt(uuid.uuid4().hex, 0)
        self._publish_observer_roster()

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
        with self._lock:
            return freeze(dict(self._attempt.sink.data))

    @property
    def result(self):
        with self._events_lock:
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

    def _new_attempt(self, submission, retry):
        self._attempt_counter += 1
        a = _Attempt(
            uuid.uuid4().hex,
            submission,
            self._attempt_counter,
            retry,
            self.max_log_lines,
            lock=self._lock,
        )
        a.priority = self._priority
        a.sink = self._receiver_factory(self, a)
        return a

    def _freeze(self):
        if self._frozen:
            return
        self.encoding = _output_encoding(self.encoding)
        self.decode_errors = _decode_policy(self.decode_errors)
        if self.cwd is None:
            self.cwd = os.getcwd()
        else:
            self.cwd = _normalize_cwd(self.cwd)
        if not Path(self.cwd).is_dir():
            raise ValueError(f"cwd is not a directory: {self.cwd}")
        self.log_file = _normalize_log_file(self.log_file)
        self.env = MappingProxyType(dict(self.env))
        self.resources = MappingProxyType(dict(self.resources))
        self._frozen = True

    def __repr__(self):
        return f"Job(id={self.id!r}, name={self.name!r}, status={self.status.value!r})"
