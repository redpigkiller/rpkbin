"""Default attempt receiver and shared core Log operations."""

from contextlib import contextmanager
from contextvars import ContextVar
import time

from ._events import Line, SourceError, StatusChanged


_IN_RECEIVER = ContextVar("jobcore_receiver_callback", default=False)


def in_receiver_callback():
    return _IN_RECEIVER.get()


@contextmanager
def receiver_callback():
    token = _IN_RECEIVER.set(True)
    try:
        yield
    finally:
        _IN_RECEIVER.reset(token)


class AttemptReceiver:
    """Receive events for one attempt, with a default retained Log implementation.

    The factory gets the Job and its pending attempt before the attempt is
    dispatched. It may retain that attempt, but never mutate a newer one.
    Only stdout/input Line deliveries can arrive without the Job event lock.
    Drain and close must not wait while holding that lock. Projection hooks
    can also run from the observer path with only the Job data lock held.
    """

    def __init__(self, job, attempt):
        self._job = job
        self._attempt = attempt

    @property
    def data(self):
        return {}

    @property
    def input_pending(self):
        return False

    @property
    def pending(self):
        return False

    def drain(self, timeout):
        """Finish already submitted receiver work within the supplied deadline."""

    def close(self):
        """Release outstanding receiver work during manager shutdown."""

    def emit(self, event):
        job, attempt = self._job, self._attempt
        with job._events_lock:
            if attempt.finalized:
                return
            if isinstance(event, Line):
                if attempt.reader_cutoff is not None and event.source == "stdout":
                    return
                record = self._record(event.text, event.source, notify=False)
                if event.monitor or event.source == "stdout":
                    attempt.reader_progress_at = time.monotonic()
            elif isinstance(event, SourceError):
                source, message = event.data["source"], event.data["message"]
                self._record(f"Monitor error [{source}]: {message}", "wave",
                             notify=attempt.reader_cutoff is None)
                return
            elif isinstance(event, StatusChanged):
                if event.old != event.new:
                    self.publish_state()
                return
            else:
                return
        if event.monitor or event.source == "stdout":
            with job._events_lock.input_callback(attempt):
                if not attempt.finalized and attempt.reader_cutoff is None:
                    self._notify_log(record)
        elif not job._events_lock.defer_input_log(attempt, self._notify_log, record):
            self._notify_log(record)

    def abandon_input(self, reason):
        """Install the single cutoff before discarding late input."""
        attempt = self._attempt
        if attempt.finalized or attempt.reader_cutoff is not None:
            return
        attempt.reader_cutoff = reason
        attempt.diagnostics.append(dict(
            source="output_reader", level="error", time=time.time(),
            message=f"{reason}; late output and publications will be discarded",
        ))

    def seal(self):
        """Close the Log under the Job data lock before terminal publication."""
        with self._job._lock:
            self._attempt.log_file.close(self._attempt)

    def publish_state(self, *, flush=False, snapshot=None):
        """Publish current state or a staged terminal snapshot under the event lock."""

    def project_report(self, report):
        return report

    def project_snapshot(self, snapshot, *, include_details):
        """Add committed receiver state to a detached snapshot."""

    def history_fields(self, snapshot):
        return {}

    def omit_details(self, snapshot):
        """Remove receiver detail from the list view."""

    def copy_details(self, detail, row):
        """Copy receiver detail when the list view includes the selected Logs."""

    def management_log(self, text):
        job, attempt = self._job, self._attempt
        with job._events_lock:
            try:
                job._guard(attempt.id)
            except ValueError:
                return
            if attempt.finalized:
                return
            record = self._record(text, "wave", notify=False)
        if not job._events_lock.defer_input_log(attempt, self._notify_log, record):
            self._notify_log(record)

    def _record(self, text, source, *, notify=True):
        job, attempt = self._job, self._attempt
        with job._lock:
            record = self._new_record(text, source, attempt.total_lines + 1)
            self._append_log(record)
        if notify and not job._events_lock.defer_input_log(attempt, self._notify_log, record):
            self._notify_log(record)
        return record

    @staticmethod
    def _new_record(text, source, line_no):
        return dict(line_no=line_no, text=text, source=source, time=time.time())

    def _append_log(self, record):
        attempt = self._attempt
        attempt.total_lines = record["line_no"]
        attempt.records.append(record)
        attempt.log_file.append(self._job, attempt, record)

    def _notify_log(self, record):
        job, attempt = self._job, self._attempt
        if job._manager_ref:
            job._manager_ref._publish(dict(
                record, job_id=job.id, job_name=job.name,
                attempt_id=attempt.id, attempt_number=attempt.number,
            ))
