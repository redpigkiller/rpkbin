"""The process attempt's sink for Flow, Log and archive publication."""

import sqlite3
import threading
import time
from contextlib import contextmanager, nullcontext
from copy import deepcopy

from rpkbin.flow import ActionOutcome, FlowSession

from ._archive_format import BATCH_BYTES, BATCH_RECORDS
from rpkbin.jobcore.events import (
    ClockTick,
    Finished,
    Line,
    AttemptReceiver,
    receiver_callback,
    SourceError,
    SourceEvent,
    Started,
    StatusChanged,
)
from rpkbin.jobcore import ControlReceipt, Report
from rpkbin.jobcore.model import freeze
from rpkbin.jobcore._job_control import _ProcessControl
from ._run import ExecutionFacts, Run, source_origin
from rpkbin.flow import Result


class _FlowActions:
    """Convert attempt-bound execution receipts at the Flow boundary."""

    def __init__(self, control):
        self._control = control

    def __getattr__(self, name):
        control = getattr(self._control, name)

        def invoke(*args, **kwargs):
            receipt = control(*args, **kwargs)
            if not isinstance(receipt, ControlReceipt):
                raise TypeError("Controls must return ControlReceipt")
            return ActionOutcome(receipt.status, receipt.reason)

        return invoke


class AttemptSink(AttemptReceiver):
    """Project events into the existing attempt and manager stores."""

    def __init__(self, workspace, job, attempt, flow, *, control=None, source_kind="process",
                 source_descriptor=None, clock_enabled=True, automation_enabled=True):
        super().__init__(job, attempt)
        self._workspace = workspace
        self.source_kind = source_kind
        self.source_descriptor = {} if source_descriptor is None else source_descriptor
        self._input = None
        self._input_thread = None
        self._archive_group = None if source_kind == "process" else []
        self._archive_bytes = 0
        self.source_interrupted = False
        self.run = None
        self.flow_session = None
        self.flow_snapshot = None
        self._data = {}
        self.flow_aliases = {}
        if control is None:
            control = _ProcessControl(job, attempt)
        attempt.capabilities = control.capabilities
        if flow is None:
            return
        self.run = Run(self._execution_facts(), control)
        self.flow_session = FlowSession(
            flow, execution_provider=self.run.execution,
            clock_enabled=clock_enabled, automation_enabled=automation_enabled,
            control=_FlowActions(self.run.control), publish=self.publish,
            on_automation_idle=self.automation_idle,
            _write_scope=self._write_scope,
            _callback_scope=receiver_callback,
            _automation_scope=lambda: job._events_lock.input_callback(attempt),
        )
        self.flow_snapshot = self.flow_session._projection()

    @property
    def archive(self):
        return self._workspace.archive

    @property
    def data(self):
        return self._data

    @property
    def pending(self):
        return self.flow_session is not None and self.flow_session.automation_pending

    def drain(self, timeout):
        session = self.flow_session
        if session is not None and not session.wait_automation(timeout):
            session.close_automation()

    def close(self):
        if self.pending:
            self.flow_session.close_automation()

    def publish_state(self, *, flush=False, snapshot=None):
        self._workspace._save_attempt(self._job, self._attempt, flush=flush, snapshot=snapshot)

    def _write_scope(self):
        # Publications on the input thread are still staged under Flow's lock.
        # If cutoff wins, Flow seals before clearing this batch. Live writes
        # and the batch commit itself always take the Job event lock.
        if self._input_thread == threading.get_ident():
            return nullcontext()
        return self._job._events_lock

    def emit(self, event: SourceEvent) -> None:
        if isinstance(event, Line) and (event.monitor or event.source == "stdout"):
            self._line(event.text, event.source, monitor=event.monitor)
            return
        with self._job._events_lock:
            if not self._attempt.finalized:
                self._emit(event)

    def _emit(self, event):
        a = self._attempt
        session = self.flow_session
        match event:
            case Line(text, source, _):
                self._record(text, source)
            case SourceError():
                self._error(event)
            case StatusChanged():
                self._status_changed(event)
            case Started():
                if session is not None:
                    session.start()
            case ClockTick(elapsed):
                if session is not None and self._input is None:
                    facts = self._execution_facts()
                    facts["elapsed"] = elapsed
                    self.run.facts = facts
                    with self._batch(session):
                        session.tick()
            case Finished(complete, _):
                if session is not None:
                    self.run.facts = self._execution_facts(finished=True)
                    if a.reader_cutoff is not None:
                        return
                    if a.previous_status != a.status:
                        session.emit_event("status_changed", dict(old=a.previous_status.value, new=a.status.value))
                    with self._batch(session):
                        session.finish(complete=complete)
            case _:
                raise TypeError(f"Unsupported source event: {type(event).__name__}")

    def _status_changed(self, event):
        session = self.flow_session
        if session is not None:
            self.run.facts = self._execution_facts()
        if event.old != event.new:
            if session is not None and not session.finished:
                session.emit_event("status_changed", dict(old=event.old, new=event.new))
            self.publish_state()

    def _error(self, event):
        a = self._attempt
        error = event.data
        source, message = error["source"], error["message"]
        self._record(f"Monitor error [{source}]: {message}", "wave", notify=a.reader_cutoff is None)
        if self.flow_session is not None and not self.flow_session.finished:
            self.run.facts = self._execution_facts()
            self.flow_session.emit_event("error", dict(source=source, message=message, evidence=error["evidence"]))

    def _execution_facts(self, *, finished=None) -> ExecutionFacts:
        a = self._attempt
        return ExecutionFacts(status=a.status.value, returncode=a.returncode, elapsed=a.elapsed,
                    finished=a.finalized if finished is None else finished,
                    stop_requested=a.stop_requested, timed_out=a.timed_out,
                    output_complete=a.output_error is None and a.reader_cutoff is None,
                    job_id=self._job.id, attempt_id=a.id, attempt_number=a.number)

    def _line(self, text, source, *, monitor, end_offset=None):
        job, a = self._job, self._attempt
        with job._events_lock:
            if a.finalized or a.reader_cutoff is not None:
                return
            session = self.flow_session if monitor and a.output_error is None else None
            if session is not None:
                self._start_input(session, end_offset=end_offset)
                self.run.facts = self._execution_facts()
            try:
                if session is None and end_offset is not None:
                    self.source_descriptor["end_offset"] = end_offset
                record = self._record(text, source, notify=False)
                if session is None:
                    a.reader_progress_at = time.monotonic()
                    self._save_large_source_batch()
            except BaseException:
                if session is not None:
                    with session._publication_lock:
                        self._commit_input(session)
                raise
        # Keep the callback's lock-order restrictions without holding the
        # actual event lock. The runner can seal this attempt at its deadline.
        try:
            with job._events_lock.input_callback(a):
                self._notify_log(record)
                if session is not None and not session.finished:
                    evidence = dict(line_no=record["line_no"], line=text, time=record["time"])
                    session.feed(text, evidence=evidence)
        finally:
            if session is not None:
                with job._events_lock, session._publication_lock:
                    if self._input is not None:
                        self._commit_input(session)
                        a.reader_progress_at = time.monotonic()

    def _start_input(self, session, *, end_offset=None):
        if self._input is not None:
            raise RuntimeError("Cannot recursively process a monitored input")
        with session._publication_lock:
            self._input = ([], [], end_offset)
            self._input_thread = threading.get_ident()
            session._input_pending = True

    @contextmanager
    def _batch(self, session):
        self._start_input(session)
        try:
            yield
        finally:
            with session._publication_lock:
                self._commit_input(session)

    @property
    def input_pending(self):
        return self._input is not None

    def _commit_input(self, session):
        # Readers see the preceding version until raw Log, latest data and
        # Flow state can be published together.
        raw, publications, end_offset = self._input
        self._input = None
        self._input_thread = None
        job = self._job
        store = self.archive
        try:
            with job._lock:
                if self._archive_group is None:
                    with (store.record_batch() if store else nullcontext()):
                        for record, elapsed in raw:
                            self._archive_record(record, elapsed=elapsed)
                        for publication in publications:
                            self._apply_publication(session, publication)
                else:
                    for record, elapsed in raw:
                        self._archive_record(record, elapsed=elapsed)
                    for publication in publications:
                        self._apply_publication(session, publication)
                for record, _ in raw:
                    self._append_log(record)
                if end_offset is not None:
                    self.source_descriptor["end_offset"] = end_offset
                # Offline imports have no live observer. Capture their Flow at
                # finish, avoiding a complete projection per ingested line.
                self._update_flow_snapshot(session, publications)
            if self.source_kind != "process" and any(item["kind"] == "action" for item in publications):
                self._save_source_actions()
            else:
                self._save_large_source_batch()
        finally:
            session._end_input()

    def _update_flow_snapshot(self, session, publications):
        if self.source_kind == "file" and not session.finished:
            return
        prev = self.flow_snapshot
        if (
            prev is not None
            and not publications
            and not session.finished
            and session.error_count == prev["error_count"]
            and session.automation_error_count == prev["automation_error_count"]
            and session._result == prev["result"]
            and session.publication_error == prev["publication_error"]
            and session.aggregation_error == prev["aggregation_error"]
            and len(session._diagnostics) == len(prev["diagnostics"])
        ):
            steps = session._steps
            updated = None
            for key in ("parsers", "analyzers", "automations"):
                items = prev[key]
                if any(item["processed"] != steps[item["name"]].processed for item in items):
                    if updated is None:
                        updated = dict(prev)
                    updated[key] = [
                        dict(item, processed=steps[item["name"]].processed)
                        if item["processed"] != steps[item["name"]].processed else item
                        for item in items
                    ]
            if updated is not None:
                self.flow_snapshot = updated
            return
        self.flow_snapshot = session._projection()

    def _apply_publication(self, session, publication):
        try:
            self._publish(publication)
        except Exception as exc:
            if session.publication_error is None:
                session.publication_error = f"{type(exc).__name__}: {exc}"

    def _interrupt_source(self):
        self.source_interrupted = True

    @property
    def has_source_batch(self):
        return bool(self._archive_group)

    def prepare_source_snapshot(self):
        # Offline imports project at checkpoints to avoid copying Flow state
        # on every line. While a callback is in flight, keep the last checkpoint.
        if self.source_kind == "file" and self.flow_session is not None and not self._attempt.finalized:
            if self.input_pending:
                return False
            with self.flow_session._publication_lock:
                self.flow_snapshot = self.flow_session._projection()
        return True

    def _save_large_source_batch(self):
        if self.has_source_batch and (
            len(self._archive_group) >= BATCH_RECORDS or self._archive_bytes >= BATCH_BYTES
        ):
            self.publish_state()

    def _save_source_actions(self):
        if self.archive:
            self.publish_state(flush=True)
            if self.archive.error is not None:
                raise OSError("Flow history save failed; inspect archive health")

    def flush_source(self):
        # Caller serializes input with the Job event lock. No user callback
        # runs inside this SQLite batch.
        if not self._archive_group:
            return True
        a = self._attempt
        store = self._workspace.archive
        group = self._archive_group
        self._archive_group = []
        self._archive_bytes = 0
        raw = [(record, elapsed) for record, elapsed in group if elapsed is not None]
        publications = [record for record, elapsed in group if elapsed is None]
        try:
            saved = store._write_file_batch(a.id, raw, publications, self._interrupt_source)
        except (sqlite3.Error, OSError):
            # record_batch already recorded the error and rolled back.
            if store.error is None:
                raise
            saved = False
        if not saved:
            if store.error is None and not store._closed:
                store._fail(OSError("Source history save failed"))
            session = self.flow_session
            if session is not None and session.publication_error is None:
                session.publication_error = "OSError: Flow history save failed; inspect archive health"
        return saved

    def abandon_input(self, reason):
        a = self._attempt
        if a.finalized or a.reader_cutoff is not None:
            return
        session = self.flow_session
        with (session._publication_lock if session is not None else nullcontext()):
            super().abandon_input(reason)
            if session is not None:
                session._seal(incomplete=True)
                if self._input is not None:
                    self._commit_input(session)
                else:
                    with self._job._lock:
                        self.flow_snapshot = session._projection()

    def seal(self):
        try:
            if self.flow_session is not None:
                with self.flow_session._publication_lock, self._job._lock:
                    self.flow_session._seal()
                    self.flow_snapshot = self.flow_session._projection()
        finally:
            super().seal()

    def _record(self, text, source, *, notify=True):
        job, a = self._job, self._attempt
        with job._lock:
            pending = len(self._input[0]) if self._input is not None else 0
            record = self._new_record(text, source, a.total_lines + pending + 1)
            if self._input is None:
                self._append_log(record)
                self._archive_record(record)
            else:
                raw, _, _ = self._input
                raw.append((record, a.elapsed))
        if notify and not job._events_lock.defer_input_log(a, self._notify_log, record):
            self._notify_log(record)
        return record

    def _archive_record(self, record, *, elapsed=None):
        a = self._attempt
        manager = self._workspace
        if manager.archive:
            elapsed = a.elapsed if elapsed is None else elapsed
            if self._archive_group is None:
                manager.archive.append(a.id, record, elapsed)
            else:
                self._archive_group.append((record, elapsed))
                self._archive_bytes += 4 * len(record["text"]) + 64

    def publish(self, publication):
        # Flow locks staged data. Direct publication also owns the Job event lock.
        a = self._attempt
        if a.finalized:
            return
        if self._input is not None:
            _, publications, _ = self._input
            publications.append(publication)
        else:
            with self._job._lock:
                self._publish(publication)
                if self.flow_session is not None:
                    self.flow_snapshot = self.flow_session._projection()
            if self.source_kind != "process" and publication["kind"] == "action":
                self._save_source_actions()

    def _publish(self, publication):
        a = self._attempt
        if publication["kind"] == "record":
            producer = publication["producer"]
            for name, value in publication["data"].items():
                copied = value if type(value) in (str, int, float, bool, type(None)) else deepcopy(value)
                self._data[f"{producer}.{name}"] = copied
                owners = self.flow_aliases.setdefault(name, set())
                owners.add(producer)
                if "." not in name:
                    if len(owners) == 1:
                        self._data[name] = copied
                    else:
                        self._data.pop(name, None)
        manager = self._workspace
        if manager.archive:
            if self._archive_group is None:
                if not manager.archive.append_flow(a.id, publication):
                    raise OSError("Flow history save failed; inspect archive health")
            else:
                self._archive_group.append((publication, None))
                self._archive_bytes += 6 * len(str(publication["data"])) + 6 * len(str(publication.get("evidence"))) + 128

    def automation_idle(self):
        job, a = self._job, self._attempt
        with job._events_lock:
            if a.finalized or a.reader_cutoff is not None:
                return
            if self._input is None:
                with job._lock:
                    self.flow_snapshot = self.flow_session._projection()
            if a.ended is not None and not a.reserved and not self.flow_session.automation_pending:
                job._seal_attempt(a, persist=True)
            if job._manager_ref:
                with job._manager_ref._cond:
                    job._manager_ref._cond.notify_all()

    @staticmethod
    def _flow_summary(flow):
        warnings = 0
        selected = None
        for result in ("ERROR", "FAIL"):
            for analyzer in flow["analyzers"]:
                if analyzer.get("result") == result:
                    selected = dict(reason=analyzer.get("reason") or result,
                                    producer=analyzer["name"], evidence=analyzer.get("evidence"))
                    break
            if selected is not None:
                break
        for analyzer in flow["analyzers"]:
            for issue in analyzer.get("issues", ()):
                warnings += issue.get("level") == "warning"
        if selected is None:
            for level in ("error", "warning"):
                for analyzer in flow["analyzers"]:
                    issue = next((item for item in analyzer.get("issues", ())
                                  if item.get("level") == level), None)
                    if issue is not None:
                        selected = dict(reason=issue.get("message") or issue.get("name", ""),
                                        producer=analyzer["name"], evidence=issue.get("evidence"))
                        break
                if selected is not None:
                    break
        return dict(selected or dict(reason="", producer=None, evidence=None), warning_count=warnings)

    def _warnings(self):
        if self.flow_snapshot is None:
            return ()
        return tuple(freeze(dict(issue, producer=analyzer["name"]))
                     for analyzer in self.flow_snapshot["analyzers"]
                     for issue in analyzer["issues"] if issue["level"] == "warning")

    def project_report(self, report):
        a = self._attempt
        if self.flow_session is None:
            return Report(Result(report.verdict) if report.verdict is not None else None,
                          report.execution, report.warnings, report.errors, report.termination)
        if a.output_error is not None or a.reader_cutoff is not None:
            verdict = Result.ERROR
        else:
            value = self.flow_snapshot["result"]
            verdict = Result(value) if value is not None else None
        return Report(verdict, report.execution, self._warnings(), report.errors, report.termination)

    def project_snapshot(self, snapshot, *, include_details):
        job, a = self._job, self._attempt
        if self.source_kind == "process":
            descriptor = dict(
                version=1, path=None, encoding=job.encoding, decode_errors=job.decode_errors,
                start_offset=None, end_offset=None, generation=0,
                completion_reason=a.completion_reason, time_basis="observed",
            )
        else:
            descriptor = dict(self.source_descriptor)
            snapshot["completion_reason"] = descriptor["completion_reason"]
        origin, label = source_origin(self.source_kind)
        snapshot.update(source_kind=self.source_kind, source_descriptor=descriptor,
                        origin=origin, origin_label=label)
        flow = self.flow_snapshot
        if flow is not None:
            summary = self._flow_summary(flow)
            snapshot.update(flow_name=flow["name"], flow_summary=summary,
                            warning_count=snapshot["warning_count"] + summary["warning_count"],
                            error_count=snapshot["error_count"] + flow["error_count"])
            snapshot["warnings"] = [dict(issue, producer=analyzer["name"])
                                    for analyzer in flow["analyzers"] for issue in analyzer["issues"]
                                    if issue["level"] == "warning"]
            if include_details:
                snapshot["flow"] = flow

    def history_fields(self, snapshot):
        return {key: snapshot[key] for key in ("source_kind", "origin", "origin_label")}

    def omit_details(self, snapshot):
        snapshot.pop("flow", None)

    def copy_details(self, detail, row):
        if "flow" in detail:
            row["flow"] = detail["flow"]
