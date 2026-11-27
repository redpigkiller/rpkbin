"""HTTP controls propagate per-target receipt reasons without presentation changes."""

from rpkbin.wave import WaveWorkspace

import sys

from rpkbin.jobcore import Job, JobManager
from test_wave import post, running_server, target


def test_control_batch_reports_concrete_receipt_statuses_and_reasons():
    manager = WaveWorkspace(JobManager())
    finished = manager.add(Job("finished", [sys.executable, "-c", "pass"], pty=False))
    manager.start()
    assert manager._drain(5)
    manager.pause()
    pending = manager.add(Job("pending", [sys.executable, "-c", "pass"], pty=False))
    stale = manager.add(Job("stale", [sys.executable, "-c", "pass"], pty=False))
    targets = [target(pending.to_snapshot_dict()), target(finished.to_snapshot_dict()),
               {"job_id": "missing", "attempt_id": "missing", "submission_id": "missing"},
               {**target(stale.to_snapshot_dict()), "attempt_id": "old"}]
    with running_server(manager) as (_, base):
        status, payload = post(base, "/api/job/action", {"action": "send", "text": "input", "targets": targets})
        assert status == 200 and payload["affected"] == 0
        for row, expected in zip(payload["results"], ("no process", "finished", "unknown", "stale attempt")):
            assert row["status"] == "rejected" and row["ok"] is False
            assert expected in row["reason"].lower()
            assert row["message"] == row["reason"]


def test_http_secret_send_reaches_child_and_redacts_response_and_archive(tmp_path):
    import hashlib
    import json
    import uuid
    from test_wave import until
    secret = "private-" + uuid.uuid4().hex
    digest = hashlib.sha256(secret.encode()).hexdigest()
    code = ("import sys,hashlib; print('READY',flush=True); value=sys.stdin.readline().rstrip('\\n'); "
            f"assert hashlib.sha256(value.encode()).hexdigest()=={digest!r}")
    log_file = tmp_path / "input.log"
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path / "archive")
    job = manager.add(Job("secret", [sys.executable, "-u", "-c", code], pty=False, log_file=log_file))
    with running_server(manager) as (_, base):
        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        until(lambda: "READY" in job.logs())
        status, payload = post(base, "/api/job/action", {
            "action": "send", "text": secret, "secret": True, "targets": [target(job.to_snapshot_dict())]})
        assert status == 200 and payload["affected"] == 1
        row = payload["results"][0]
        assert row["status"] == "accepted" and row["reason"] == row["message"] == "Input sent: ***"
        assert manager._drain(5) and job.returncode == 0
        saved = manager.archive.attempt(job.id, job.attempt_id)
        assert secret not in json.dumps([payload, saved, job.logs()])
        assert secret not in log_file.read_text(encoding="utf-8")
        manager.archive.flush()
        assert all(secret.encode() not in p.read_bytes() for p in manager.archive.path.parent.iterdir() if p.is_file())
