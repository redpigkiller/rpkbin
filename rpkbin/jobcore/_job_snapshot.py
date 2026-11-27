"""Job snapshots and detached attempt projections."""

from __future__ import annotations

import os
from copy import deepcopy

from ._job_config import _integer
from .model import ProcessVerdict, Report, freeze


_HISTORY_KEYS = (
    "attempt_id",
    "attempt_number",
    "status",
    "result",
    "elapsed_str",
    "returncode",
    "warning_count",
    "error_count",
    "retry_scheduled",
    "completion_reason",
    "capabilities",
)


_ATOMIC = frozenset((str, int, float, bool, bytes, type(None)))


def _detach(value):
    """Copy plain containers recursively; anything else falls back to ``deepcopy``.

    Faster than ``deepcopy`` for JSON-like snapshots (no memo, scalars shared) while
    still isolating every mutable sub-object from the live attempt.
    """
    try:
        return _detach_value(value)
    except RecursionError:
        return deepcopy(value)


def _detach_value(value, _atomic=_ATOMIC):
    kind = type(value)
    if kind in _atomic:
        return value
    if kind is dict:
        return {
            (k if type(k) in _atomic else _detach_value(k)): (
                v if type(v) in _atomic else _detach_value(v)
            )
            for k, v in value.items()
        }
    if kind is list:
        return [v if type(v) in _atomic else _detach_value(v) for v in value]
    if kind is tuple:
        return tuple(v if type(v) in _atomic else _detach_value(v) for v in value)
    return deepcopy(value)


def _rows(rows):
    """Copy a diagnostic deque/list of dicts without racing its writers."""
    snapshot = getattr(rows, "snapshot_rows", None)
    if snapshot is not None:
        return snapshot()
    for _ in range(16):
        try:
            return [dict(row) for row in tuple(rows)]
        except RuntimeError:
            continue
    return [dict(row) for row in tuple(rows)]


class _JobSnapshots:
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

    def _verdict(self, a, *, complete=False):
        if (
            a.execution_failed
            or a.timed_out
            or (a.returncode is not None and a.returncode != 0 and not a.stop_requested)
        ):
            return ProcessVerdict.FAIL
        if a.error_count or a.reader_cutoff is not None:
            return ProcessVerdict.ERROR
        if a.finalized or complete:
            if a.stop_requested:
                return ProcessVerdict.INCOMPLETE
            return ProcessVerdict.NA
        return None

    def _report(self, a, *, complete=False):
        if a.report is not None:
            return a.report
        report = Report(
            self._verdict(a, complete=complete),
            freeze(
                dict(status=a.status.value, returncode=a.returncode, elapsed=a.elapsed)
            ),
            (),
            tuple(freeze(e) for e in _rows(a.errors)),
            tuple(freeze(t) for t in _rows(a.termination)),
        )

        return a.sink.project_report(report)

    def _snapshot_attempt(self, a, *, include_details=False):
        if a.finalized:
            out = _detach(a.final_snapshot)
            if not include_details:
                a.sink.omit_details(out)
            return out
        return _detach(self._capture_attempt(a, include_details=include_details))

    def _capture_attempt(self, a, *, include_logs=False, include_details=False):
        # Receiver details are detached when published.
        # Copy the mutable containers here and serialize the captured view later.
        report = self._report(a)
        can_rerun = a.can_rerun()
        errors, diagnostics, termination = _rows(a.errors), _rows(a.diagnostics), _rows(a.termination)
        out = dict(
            id=self.id,
            name=self.name,
            cmd=self.cmd_str,
            cwd=self.cwd or os.getcwd(),
            env=dict(self.env),
            encoding=self.encoding,
            decode_errors=self.decode_errors,
            completion_reason=a.completion_reason,
            capabilities=a.capabilities.snapshot(can_rerun=can_rerun),
            pty=self.pty,
            prompts=list(self.prompts),
            resources=dict(self.resources),
            priority=a.priority,
            held=a.held,
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
            pending_code=None,
            data=dict(a.sink.data),
            warnings=[dict(warning) for warning in report.warnings],
            warning_count=sum(d.get("level") == "warning" for d in diagnostics),
            errors=errors,
            error_count=a.error_count,
            diagnostics=diagnostics,
            output_decode_count=a.output_decode_count,
            log_file_error=a.log_file_error,
            termination=termination,
            attempt_id=a.id,
            submission_id=a.submission_id,
            attempt_number=a.number,
            retry_index=a.retry_index,
            retry_scheduled=a.retry_scheduled,
            interrupt_requested=a.interrupt_requested,
            interrupt_source=a.interrupt_source,
            suspended=a.suspended,
            log_count=len(a.records),
            total_lines=a.total_lines,
            max_log_lines=self.max_log_lines,
            attempt_max_log_lines=a.max_lines,
            finalized=a.finalized,
            can_rerun=can_rerun,
            history_truncated=self._history_truncated,
        )
        if include_logs:
            out["logs"] = self._logs(a)
            out["log_records"] = [dict(r) for r in a.records]
        a.sink.project_snapshot(out, include_details=include_logs or include_details)
        return out

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
        with self._events_lock:
            return self._capture_log(self._find_attempt(attempt_id), after_line, cursor_attempt)

    def _capture_log(self, a, after_line, cursor_attempt):
        if after_line is not None:
            _integer(after_line, "after_line", minimum=0)
        with self._lock:
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
        self, *, include_logs=False, attempt_id=None, include_details=False
    ):
        with self._events_lock:
            captured = self._capture_snapshot(include_logs=include_logs, attempt_id=attempt_id,
                                              include_details=include_details)
        return _detach(captured)

    def _capture_snapshot(self, *, include_logs=False, attempt_id=None, include_details=False):
        a = self._find_attempt(attempt_id)
        out = self._capture_attempt(
            a,
            include_logs=include_logs,
            include_details=include_details,
        )
        out["history"] = self._capture_history(self._history)
        return out

    def _capture_history(self, history):
        rows = []
        for h in history:
            summary = h.history_summary
            if summary is None:
                snapshot = (
                    h.final_snapshot
                    if h.finalized and h.final_snapshot is not None
                    else self._capture_attempt(h)
                )
                summary = {k: snapshot[k] for k in _HISTORY_KEYS} | h.sink.history_fields(snapshot)
                if h.finalized:
                    # A finalized attempt is immutable, so its summary is computed once.
                    h.history_summary = summary
            rows.append(dict(summary, capabilities=dict(summary["capabilities"])))
        return rows

    def _publish_observer_roster(self):
        # Publish identity and retention together, independently of the cached projection.
        with self._lock:
            self._observer_roster = (self._attempt, tuple(self._history), self._history_truncated)

    def _capture_observer(self, *, selected, attempt_id, include_logs, log_cursor):
        acquired = self._events_lock.acquire(blocking=False)
        try:
            with self._lock:
                return self._capture_committed_state(selected, attempt_id, include_logs, log_cursor)
        finally:
            if acquired:
                self._events_lock.release()

    def _capture_committed_state(self, selected, attempt_id, include_logs, log_cursor):
        current, history, truncated = self._observer_roster
        # Receiver details are expensive; build them only for a selected current attempt.
        current_detail = selected and (not attempt_id or attempt_id == current.id)
        captured = self._capture_attempt(current, include_details=current_detail)
        captured.update(history=self._capture_history(history), history_truncated=truncated)
        row = dict(captured)
        if current_detail:
            current.sink.omit_details(row)
        if not selected:
            return row, None
        if current_detail:
            a, detail = current, dict(captured)
        else:
            a = next((old for old in history if old.id == attempt_id), None)
            if a is None:
                return row, None
            detail = self._capture_attempt(a, include_details=True)
            detail["history"] = captured["history"]
        if log_cursor is None and not include_logs:
            return row, detail
        after, cursor_attempt = log_cursor if log_cursor is not None else (None, None)
        chunk = self._capture_log(a, after, cursor_attempt)
        # Management Log writes can interleave even with a fresh capture.
        counts = dict(log_count=chunk["retained_count"], total_lines=chunk["next_line"],
                      attempt_max_log_lines=chunk["capacity"])
        detail.update(counts)
        if a is current:
            row.update(counts)
        if after is None:
            detail["log_records"] = chunk["records"]
        else:
            detail["log_chunk"] = chunk
        if include_logs:
            detail["logs"] = [record["text"] for record in chunk["records"]]
            if not attempt_id:
                row.update(logs=detail["logs"], log_records=chunk["records"])
                current.sink.copy_details(detail, row)
        return row, detail
