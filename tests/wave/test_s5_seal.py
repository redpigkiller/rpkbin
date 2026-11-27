"""Event-gated regressions for the attempt commit and seal boundary."""

from rpkbin.wave import WaveWorkspace

from concurrent.futures import ThreadPoolExecutor
import sys
import threading

import pytest

from rpkbin.flow import Automation, Flow, Parser, Result
from rpkbin.jobcore import Job, JobManager, JobStatus
from rpkbin.wave.web import WaveServer


def command(name, **settings):
    return Job(name, [sys.executable, "-c", "pass"], pty=False, **settings)


@pytest.mark.parametrize("held_callback", ["parser", "aggregate"])
def test_cleanup_failure_seals_while_callback_is_still_running(tmp_path, monkeypatch, held_callback):
    from rpkbin.jobcore import _process

    entered, release, saved = (threading.Event() for _ in range(3))

    class Process:
        pid = 123

        def __init__(self, *args, **kwargs):
            self.chunks = iter((b"line\n", b""))

        def start(self):
            pass

        def poll(self):
            return 0 if entered.is_set() else None

        def read(self):
            return next(self.chunks)

        def cleanup(self):
            raise OSError("cleanup failed in test")

        def close(self):
            pass

    def parse(line, ctx):
        if held_callback == "parser":
            entered.set()
            release.wait(15)
        return {"value": line}

    def aggregate(analyzers):
        entered.set()
        release.wait(15)
        raise ValueError("late aggregation error")

    monkeypatch.setattr(_process, "ProcessHandle", Process)
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    result = aggregate if held_callback == "aggregate" else None
    job = manager.add(command("cleanup"), flow=Flow("cleanup", parsers=[Parser("held", parse)], result=result))
    original = manager._save_attempt

    def save(job, attempt, **kwargs):
        original(job, attempt, **kwargs)
        if attempt.finalized and kwargs.get("flush"):
            saved.set()

    monkeypatch.setattr(manager, "_save_attempt", save)
    try:
        manager.start()
        assert entered.wait(10)
        assert saved.wait(6) and not release.is_set()
        assert manager._drain(6)
        state = manager.archive.attempt(job.id, job.attempt_id)
        assert state["finalized"] and state["result"] == Result.ERROR.value
        assert not state["flow"]["complete"]
        assert any("cleanup failed" in d["message"] for d in state["diagnostics"])
        flow = job._attempt.sink.flow_session.snapshot()
        release.set()
        assert job._attempt.reader_done.wait(10)
        assert job._attempt.sink.flow_session.snapshot() == flow
        if held_callback == "parser":
            assert not job.data
    finally:
        entered.set()
        release.set()
        manager.close()


def test_finalized_discards_automation_and_external_log_writes(tmp_path):
    entered, release, returned = (threading.Event() for _ in range(3))

    def handle(event, ctx):
        if event.name == "started":
            entered.set()
            release.wait(15)
            job.log("late automation")
            returned.set()

    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    job = manager.add(command("seal"), flow=Flow("seal", automations=[Automation("held", on_event=handle)]))
    attempt = job._attempt
    try:
        manager.validate()
        attempt.sink.flow_session.start()
        assert entered.wait(10)
        with job._events_lock:
            attempt.returncode = 0
            attempt.sink.abandon_input("test output deadline")
            job._finish(attempt)
            manager._save_attempt(job, attempt, flush=True)
        before = job.to_snapshot_dict(include_logs=True)
        job.log("late external log")
        release.set()
        assert returned.wait(10)
        assert attempt.sink.flow_session.wait_automation(10)
        assert job.to_snapshot_dict(include_logs=True) == before
        archived = manager.archive.attempt(job.id, attempt.id)
        assert archived["total_lines"] == before["total_lines"]
        assert job.set_max_log_lines(1) == 0
        assert job.to_snapshot_dict()["attempt_max_log_lines"] == before["attempt_max_log_lines"]
        with job._events_lock:
            manager._save_attempt(job, attempt, flush=True)
        assert manager.archive.attempt(job.id, attempt.id) == archived
    finally:
        release.set()
        manager.close()


def test_delayed_flow_read_cannot_overwrite_terminal_projection(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    workspace = WaveWorkspace()
    job = workspace.add(command("cache"), flow=Flow("cache", parsers=[Parser("read", lambda line, ctx: None)]))
    attempt = job._attempt
    original = attempt.sink.flow_session.snapshot
    observer = None

    def snapshot():
        view = original()
        if threading.current_thread() is observer:
            entered.set()
            release.wait(10)
        return view

    monkeypatch.setattr(attempt.sink.flow_session, "snapshot", snapshot)

    def read():
        attempt.sink.flow_snapshot
        entered.set()

    observer = threading.Thread(target=read, daemon=True)
    observer.start()
    try:
        assert entered.wait(10)
        attempt.returncode = 0
        job._finish(attempt)
        release.set()
        observer.join(10)
        assert not observer.is_alive()
        state = job.to_snapshot_dict(include_details=True)
        assert state["finalized"] and state["flow"]["finished"]
    finally:
        release.set()
        observer.join(10)


def test_wave_reads_only_the_committed_input_version(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def hold(line, ctx):
        if line == "encoding":
            job.log("\ud800")
        else:
            entered.set()
            release.wait(10)

    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    job = manager.add(command("version", log_file=tmp_path / "version.log"), flow=Flow("version", parsers=[
        Parser("first", lambda line, ctx: {"value": line}), Parser("held", hold),
    ]))
    server = WaveServer(manager)
    try:
        manager.validate()
        with ThreadPoolExecutor(max_workers=1) as pool:
            feeder = pool.submit(job._line, job._attempt, "new")
            try:
                assert entered.wait(10)
                pending = server._state(job.id, log_after=0, log_attempt=job.attempt_id)
                row, detail = pending["jobs"][0], pending["detail"]
                assert row["data"] == detail["data"] == {}
                assert detail["flow"]["parsers"][0]["latest"] == {}
                assert row["total_lines"] == detail["total_lines"] == detail["log_chunk"]["next_line"] == 0
            finally:
                release.set()
            feeder.result(timeout=10)
        committed = server._state(job.id, log_after=0, log_attempt=job.attempt_id)
        row, detail = committed["jobs"][0], committed["detail"]
        assert row["data"] == detail["data"]
        assert row["data"]["first.value"] == detail["flow"]["parsers"][0]["latest"]["value"] == "new"
        assert row["total_lines"] == detail["total_lines"] == detail["log_chunk"]["next_line"] == 1
        public_flow = job._attempt.sink.flow_session.snapshot()
        public_flow["parsers"][0]["latest"]["value"] = "caller edit"
        assert server._state(job.id)["detail"]["flow"]["parsers"][0]["latest"]["value"] == "new"
        job._line(job._attempt, "encoding")
        updated = server._state(job.id, log_after=1, log_attempt=job.attempt_id)["detail"]
        assert updated["log_file_error"]["count"] == 1
        assert updated["data"]["first.value"] == updated["flow"]["parsers"][0]["latest"]["value"] == "encoding"
        assert updated["total_lines"] == updated["log_chunk"]["next_line"] == 3
    finally:
        release.set()
        manager.close()


def test_seal_marks_an_unreturned_control_receipt(tmp_path, monkeypatch):
    sent, release, returned = (threading.Event() for _ in range(3))

    def handle(event, ctx):
        if event.name == "started":
            ctx.actions.send("go")
            returned.set()

    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    job = manager.add(command("receipt"), flow=Flow("receipt", automations=[Automation("send", on_event=handle)]))
    attempt = job._attempt

    class Process:
        def poll(self):
            return 0 if sent.is_set() else None

        def write(self, text):
            sent.set()

    attempt.process = Process()
    attempt.status = JobStatus.RUNNING
    send = job.send

    def hold_receipt(*args, **kwargs):
        receipt = send(*args, **kwargs)
        assert receipt
        release.wait(15)
        return receipt

    monkeypatch.setattr(job, "send", hold_receipt)
    try:
        manager.validate()
        attempt.sink.flow_session.start()
        assert sent.wait(10)
        with job._events_lock:
            attempt.returncode = 0
            attempt.sink.abandon_input("test output deadline")
            job._finish(attempt)
            manager._save_attempt(job, attempt, flush=True)
        rows = manager.archive.automation_history(job.id, attempt.id)["rows"]
        assert len(rows) == 1
        assert rows[0]["data"]["phase"] != "requested"
        assert "lost" in rows[0]["data"]["receipt"]["reason"]
        release.set()
        assert returned.wait(10)
        assert manager.archive.automation_history(job.id, attempt.id)["rows"] == rows
    finally:
        release.set()
        manager.close()
