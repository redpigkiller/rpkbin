from rpkbin.wave import WaveWorkspace

from concurrent.futures import ThreadPoolExecutor
import sys
import threading

import pytest

from rpkbin.flow import Automation, Flow, Parser, Result
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import RunArchive
from rpkbin.wave.web import WaveServer


def test_other_job_archive_commits_while_parser_is_held(tmp_path, monkeypatch):
    manager = WaveWorkspace(JobManager(max_workers=2), archive_dir=tmp_path)
    entered, release, saved = (threading.Event() for _ in range(3))

    def parse(line, ctx):
        entered.set()
        assert release.wait(15)
        return {"line": line}

    first = manager.add(Job("held", [sys.executable, "-u", "-c", "print('held')"],
                            pty=False), flow=Flow("held", parsers=[Parser("hold", parse)]))
    original = manager._save_attempt

    def save(job, attempt, **kwargs):
        original(job, attempt, **kwargs)
        if job.name == "peer" and attempt.finalized and kwargs.get("flush"):
            saved.set()

    monkeypatch.setattr(manager, "_save_attempt", save)
    try:
        manager.start()
        assert entered.wait(10)
        peer = manager.add(Job("peer", [sys.executable, "-u", "-c", "print('peer')"], pty=False))
        assert saved.wait(10)
        assert not release.is_set()
        store = RunArchive(manager.archive.path)
        try:
            assert store.attempt(peer.id, peer.attempt_id)["finalized"]
            assert [r["text"] for r in store.page(peer.id, peer.attempt_id)["rows"]] == ["peer"]
            assert store.page(first.id, first.attempt_id)["rows"] == []
        finally:
            store.close()
    finally:
        release.set()
        manager.close()


def test_drain_deadline_persists_terminal_attempt_before_callback_returns(tmp_path, monkeypatch):
    from rpkbin.jobcore import _process

    entered, release, saved = (threading.Event() for _ in range(3))

    class Process:
        pid = 123

        def __init__(self, *args, **kwargs):
            self.chunks = iter((b"first\ntail\n", b""))

        def start(self):
            pass

        def poll(self):
            return 0 if entered.is_set() else None

        def read(self):
            return next(self.chunks)

        def cleanup(self):
            pass

        def close(self):
            pass

    def parse(line, ctx):
        job.log("held listener")
        job.log("late management log")
        with pytest.raises(ValueError, match="abandoned attempt"):
            job.set_max_log_lines(1)
        return {"late": line}

    def listen(record):
        if record["text"] == "held listener":
            entered.set()
            assert release.wait(15)

    monkeypatch.setattr(_process, "ProcessHandle", Process)
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    manager.add_log_listener(listen)
    job = manager.add(Job("deadline", [sys.executable, "-c", "pass"], pty=False,
                          output_drain_timeout=0.05), flow=Flow("held", parsers=[Parser("hold", parse)]))
    attempt = job._attempt
    original = manager._save_attempt

    def save(job, attempt, **kwargs):
        original(job, attempt, **kwargs)
        if attempt.finalized and kwargs.get("flush"):
            saved.set()

    monkeypatch.setattr(manager, "_save_attempt", save)
    try:
        manager.start()
        assert entered.wait(10)
        assert saved.wait(6), "terminal save must not wait for the held Parser"
        assert not release.is_set()
        assert manager._drain(6)
        before = job.to_snapshot_dict(include_details=True, include_logs=True)
        assert before["finalized"] and before["result"] == Result.ERROR.value
        assert before["flow"]["finished"] and before["flow"]["complete"] is False
        assert attempt.output_error["source"] == "output_reader"
        assert any("discard" in d["message"] for d in before["diagnostics"])
        archived = manager.archive.attempt(job.id, attempt.id)
        assert archived["finalized"] and archived["result"] == Result.ERROR.value
        assert archived["errors"][-1] == attempt.output_error
        assert archived["flow"]["complete"] is False
        release.set()
        assert attempt.reader_done.wait(10)
        assert job.to_snapshot_dict(include_details=True, include_logs=True) == before
        assert manager.archive.flow_page(job.id, attempt.id, kind="record")["rows"] == []
        assert not {"tail", "late management log"}.intersection(job.logs())
    finally:
        release.set()
        manager.close()


@pytest.mark.parametrize("decode_errors", ["replace", "strict"])
def test_late_decoder_read_cannot_mutate_finalized_attempt(tmp_path, decode_errors):
    entered, release = threading.Event(), threading.Event()

    class LateRead:
        def read(self):
            if not entered.is_set():
                entered.set()
                assert release.wait(10)
                return b"\xff\n"
            return b""

    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    job = manager.add(Job("late-read", [sys.executable, "-c", "pass"], pty=False,
                          decode_errors=decode_errors))
    attempt = job._attempt
    attempt.process = LateRead()
    reader = threading.Thread(target=job._read, args=(attempt,), daemon=True)
    reader.start()
    try:
        assert entered.wait(10)
        attempt.returncode = 0
        job._finish(attempt)
        manager._save_attempt(job, attempt, flush=True)
        before = job.to_snapshot_dict(include_details=True, include_logs=True)
        release.set()
        assert attempt.reader_done.wait(10)
        assert job.to_snapshot_dict(include_details=True, include_logs=True) == before
        assert manager.archive.attempt(job.id, attempt.id)["output_decode_count"] == 0
    finally:
        release.set()
        reader.join(10)
        manager.close()


def test_busy_snapshot_uses_retained_history_and_one_log_watermark(tmp_path):
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    job = manager.add(Job("observer", [sys.executable, "-c", "pass"], pty=False))
    server = WaveServer(manager)
    release, locked = threading.Event(), threading.Event()

    def hold():
        with job._events_lock:
            locked.set()
            assert release.wait(10)

    try:
        evicted = job.attempt_id
        for _ in range(2):
            job._attempt.returncode = 0
            job._finish(job._attempt)
            manager.rerun(job)
        assert len(server._state(job.id)["detail"]["history"]) == 2
        assert manager.set_max_history(1) == 1
        job.log("new line")
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(hold)
            try:
                assert locked.wait(10)
                missing = server._state(job.id, attempt_id=evicted)
                assert missing["detail"] is None and missing["history_error"]
                assert evicted not in [h["attempt_id"] for h in missing["jobs"][0]["history"]]
                state = server._state(job.id, log_after=0, log_attempt=job.attempt_id)
                row, detail = state["jobs"][0], state["detail"]
                chunk = detail["log_chunk"]
                assert row["attempt_id"] == detail["attempt_id"] == chunk["attempt_id"]
                assert row["total_lines"] == detail["total_lines"] == chunk["next_line"] == 1
                assert row["log_count"] == detail["log_count"] == chunk["retained_count"] == 1
            finally:
                release.set()
            worker.result(timeout=10)
    finally:
        release.set()
        manager.close()


def test_completed_control_audit_is_flushed_with_abandoned_input(tmp_path, monkeypatch):
    from rpkbin.jobcore import _process

    written, entered, acknowledged, exited, release = (threading.Event() for _ in range(5))

    class Process:
        pid = 123

        def __init__(self, *args, **kwargs):
            self.first = True

        def start(self):
            pass

        def poll(self):
            return 0 if exited.is_set() else None

        def write(self, text):
            written.set()

        def read(self):
            if not self.first:
                return b""
            self.first = False
            assert written.wait(10)
            return b"held\n"

        def cleanup(self):
            pass

        def close(self):
            pass

    def parse(line, ctx):
        entered.set()
        assert release.wait(15)

    def on_event(event, ctx):
        if event.name == "started":
            assert ctx.actions.send("go")
            acknowledged.set()

    monkeypatch.setattr(_process, "ProcessHandle", Process)
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    job = manager.add(Job(
        "audit", [sys.executable, "-c", "pass"], pty=False, output_drain_timeout=0.05,
        ), flow=Flow("audit", parsers=[Parser("hold", parse)],
                  automations=[Automation("send", on_event=on_event)]))
    send = job.send

    def wait_for_input(*args, **kwargs):
        receipt = send(*args, **kwargs)
        assert entered.wait(10)
        return receipt

    monkeypatch.setattr(job, "send", wait_for_input)
    try:
        manager.start()
        assert entered.wait(10)
        assert acknowledged.wait(6), "an existing request can record its receipt while the input is held"
        exited.set()
        assert manager._drain(6)
        assert not release.is_set()
        rows = manager.archive.automation_history(job.id, job.attempt_id)["rows"]
        assert len(rows) == 1 and rows[0]["data"]["receipt"]["status"] == "accepted"
    finally:
        exited.set()
        release.set()
        manager.close()
