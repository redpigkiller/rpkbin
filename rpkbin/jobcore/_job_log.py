"""Retained Job logs and their capacity."""

from __future__ import annotations

import time
from collections import deque

from ._job_config import _integer


class _JobLogs:
    def _logs(self, a, tail=None):
        if tail is not None:
            tail = _integer(tail, "tail", minimum=0)
        with self._lock:
            if tail == 0:
                return []
            lines = [record["text"] for record in a.records]
        return lines[-tail:] if tail is not None else lines

    def logs(self, tail=None):
        return self._logs(self._attempt, tail)

    def set_max_log_lines(
        self, max_log_lines, *, expected_attempt=None, expected_submission=None
    ):
        lines = _integer(max_log_lines, "max_log_lines", minimum=1)
        with self._events_lock:
            a = self._guard(expected_attempt, expected_submission)
            with self._lock:
                object.__setattr__(self, "max_log_lines", lines)
                if a.finalized:
                    return 0
                evicted = max(0, len(a.records) - lines)
                a.max_lines = lines
                a.records = deque(a.records, maxlen=lines)
                return evicted

    def log(self, message):
        self._attempt.sink.management_log(str(message))


class _AttemptLogFile:
    """Append with one line-buffered stream while the attempt can emit Log records.

    The Sink calls this under the Job lock. Line buffering keeps completed
    lines visible and attributes flush failures to the write that caused them.
    """

    def __init__(self):
        self._path = None
        self._stream = None

    def append(self, job, attempt, record):
        path = job.log_file
        if path is None and job._manager_ref and job._manager_ref.log_dir:
            path = job._manager_ref.log_dir / f"{job.id}.log"
        absolute = path.absolute() if path is not None else None
        if self._path != absolute:
            self.close(attempt)
        if absolute is None:
            return
        try:
            if self._stream is None:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._stream = path.open("a", encoding="utf-8", buffering=1)
                self._path = absolute
            self._stream.write(f"[{attempt.id}][{record['source']}] {record['text']}\n")
        except (OSError, UnicodeError) as exc:
            self._close_stream()
            self._failure(attempt, absolute, exc)

    def _close_stream(self):
        path, stream = self._path, self._stream
        self._path = self._stream = None
        if stream is not None:
            try:
                stream.close()
            except (OSError, UnicodeError) as exc:
                return path, exc
        return None

    def close(self, attempt):
        failure = self._close_stream()
        if failure is not None:
            self._failure(attempt, *failure)

    @staticmethod
    def _failure(attempt, path, exc):
        previous = attempt.log_file_error
        failure = dict(
            path=str(path),
            message=f"{type(exc).__name__}: {exc}",
            count=(previous["count"] if previous else 0) + 1,
        )
        attempt.log_file_error = failure
        if previous is None or any(
            previous[key] != failure[key] for key in ("path", "message")
        ):
            attempt.diagnostics.append(dict(
                source="log_file", message=f"{failure['path']}: {failure['message']}",
                time=time.time(),
            ))
