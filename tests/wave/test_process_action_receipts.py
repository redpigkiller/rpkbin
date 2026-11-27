"""Unified control receipts and control-channel secret redaction (S3)."""

from rpkbin.wave import WaveWorkspace

import hashlib
import json
import sys
import time
import uuid
from dataclasses import asdict

import pytest

from rpkbin.flow import Automation, Flow
from rpkbin.jobcore import Job, JobManager
from rpkbin.jobcore._process import ProcessHandle


@pytest.fixture
def managers():
    instances = []
    def make(*, archive_dir=None, **kwargs):
        manager = WaveWorkspace(JobManager(**kwargs), archive_dir=archive_dir)
        instances.append(manager)
        return manager
    yield make
    for manager in instances:
        manager.close()


def command(code="pass", **kwargs):
    return Job("control", [sys.executable, "-u", "-c", code], pty=False, **kwargs)


def ready(job):
    deadline = time.monotonic() + 5
    while "READY" not in job.logs():
        assert time.monotonic() < deadline
        time.sleep(.01)


def assert_secret_absent(manager, job, receipt, secret, log_file):
    manager._save_attempt(job, job._attempt, flush=True)
    saved = manager.archive.attempt(job.id, job.attempt_id)
    actions = manager.archive.flow_page(job.id, job.attempt_id, stage="automation")["rows"]
    serialized = json.dumps([job.logs(), job.to_snapshot_dict(include_details=True), asdict(receipt), saved, actions], ensure_ascii=False)
    assert secret not in serialized
    assert secret not in log_file.read_text(encoding="utf-8")
    manager.archive.flush()
    for path in manager.archive.path.parent.iterdir():
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


@pytest.mark.parametrize("automation,keyword", [(False, False), (True, False), (True, True)])
def test_secret_input_reaches_child_without_control_channel_persistence(managers, tmp_path, automation, keyword):
    secret = "private-" + uuid.uuid4().hex
    digest = hashlib.sha256(secret.encode()).hexdigest()
    code = ("import hashlib,sys; print('READY',flush=True); value=sys.stdin.readline().rstrip('\\n'); "
            f"assert hashlib.sha256(value.encode()).hexdigest()=={digest!r}; print('DELIVERED',flush=True)")
    receipts = []
    def send_secret(event, ctx):
        if event.name == "started":
            receipts.append(ctx.actions.send(text=secret, secret=True) if keyword else ctx.actions.send(secret, secret=True))
    flow = Flow("privacy", automations=[Automation("send_secret", on_event=send_secret)]) if automation else None
    manager = managers(archive_dir=tmp_path / "archive")
    log_file = tmp_path / "input.log"
    job = manager.add(command(code, log_file=log_file), flow=flow)
    manager.start()
    ready(job)
    if not automation:
        receipts.append(job.send(secret, secret=True))
    assert manager._drain(5)
    assert job.returncode == 0 and "DELIVERED" in job.logs()
    assert len(receipts) == 1 and receipts[0].accepted
    assert receipts[0].reason == "Input sent: ***"
    assert "Input sent: ***" in job.logs()
    assert_secret_absent(manager, job, receipts[0], secret, log_file)
    if automation:
        rows = manager.archive.automation_history(job.id, job.attempt_id)["rows"]
        assert len(rows) == 1
        assert rows[0]["data"]["reason"] == "Input sent: ***"
        assert rows[0]["data"]["receipt"] == {"status": "accepted", "reason": "Input sent: ***"}


@pytest.mark.parametrize("automation", [False, True])
def test_secret_write_exception_is_redacted_everywhere(managers, tmp_path, monkeypatch, automation):
    secret = "private-" + uuid.uuid4().hex
    receipts = []
    def send_secret(event, ctx):
        if event.name == "started":
            receipts.append(ctx.actions.send(secret, secret=True))
    def broken(handle, text):
        raise OSError("could not write " + repr(text))
    manager = managers(archive_dir=tmp_path / "archive")
    log_file = tmp_path / "input.log"
    flow = Flow("privacy", automations=[Automation("send_secret", on_event=send_secret)]) if automation else None
    job = manager.add(command("import sys; print('READY',flush=True); sys.stdin.readline()", log_file=log_file), flow=flow)
    with monkeypatch.context() as patch:
        patch.setattr(ProcessHandle, "write", broken)
        manager.start()
        ready(job)
        if automation:
            assert job._attempt.sink.flow_session.wait_automation()
        else:
            receipts.append(job.send(secret, secret=True))
    assert len(receipts) == 1 and receipts[0].status == "failed"
    assert "write failed" in receipts[0].reason.lower()
    assert job.send("exit")
    assert manager._drain(5)
    assert_secret_absent(manager, job, receipts[0], secret, log_file)


