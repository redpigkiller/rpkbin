"""Stable pending codes keep scheduling independent of display text (S1)."""

import sys
import threading

import pytest

from rpkbin.jobcore import Job, JobManager, JobStatus


@pytest.fixture
def manager():
    manager = JobManager(max_workers=1, resources={"license": 2})
    yield manager
    manager.close()


def command(code="pass", **kwargs):
    return Job("pending", [sys.executable, "-u", "-c", code], pty=False, **kwargs)


@pytest.mark.parametrize(
    "started,ready,paused,reserved,busy,used,code,text",
    [
        (False, False, True, False, 0, 0, "NOT_STARTED", "Not started"),
        (False, True, True, False, 0, 0, "READY_TO_START_PENDING_BEGIN", "Ready to start"),
        (True, True, True, False, 0, 0, "PAUSED", "Paused (dispatch is paused)"),
        (True, True, False, True, 0, 0, "DISPATCHING", "Dispatching"),
        (True, True, False, False, 1, 0, "WAIT_WORKER", "Waiting for worker slot (1/1 busy)"),
        (True, True, False, False, 0, 2, "WAIT_RESOURCE", "Waiting for license (2/2 in use, need 1)"),
        (True, True, False, False, 0, 0, "READY", "Ready to start"),
    ],
)
def test_pending_codes_preserve_explanations(
    manager, monkeypatch, started, ready, paused, reserved, busy, used, code, text
):
    job = manager.add(command(resources={"license": 1}))
    with monkeypatch.context() as state:
        for name, value in (("started", started), ("ready", ready), ("paused", paused),
                            ("_active_workers", busy), ("_used", {"license": used})):
            state.setattr(manager, name, value)
        state.setattr(job._attempt, "reserved", reserved)
        snapshot = manager.snapshot(selected_job_id=job.id)
        assert snapshot["jobs"][0]["pending_code"] == code
        assert snapshot["jobs"][0]["pending_reason"] == text
        assert snapshot["detail"]["pending_code"] == code
        assert snapshot["detail"]["pending_reason"] == text
        assert manager.explain_pending(job) == text


def test_pending_text_does_not_decide_dispatch(manager, monkeypatch):
    first = manager.add(command("import sys; print('READY', flush=True); sys.stdin.readline()"))
    second = manager.add(command())
    first_ready = threading.Event()
    manager.add_log_listener(lambda record: first_ready.set() if record["text"] == "READY" else None)
    checked_worker_limit = threading.Event()
    original = manager._pending_reason

    def changed_text(job):
        code, text = original(job)
        if code == "READY":
            return code, "Ready with different display text"
        if code == "WAIT_WORKER":
            checked_worker_limit.set()
            return code, "Ready to start"
        return code, text

    monkeypatch.setattr(manager, "_pending_reason", changed_text)
    manager.start()
    assert checked_worker_limit.wait(5)
    with manager._cond:
        assert manager._active_workers == 1
        assert second.status is JobStatus.PENDING
        assert not second._attempt.reserved
    assert first_ready.wait(5)
    with first._events_lock:
        assert first.send("finish")
    assert manager._drain(5)
    assert first.returncode == second.returncode == 0
    assert manager.snapshot()["jobs"][0]["pending_code"] is None
    assert manager.explain_pending(first) == "DONE"


def test_history_detail_has_no_current_pending_code(manager):
    job = manager.add(command())
    manager.start()
    assert manager._drain(5)
    original = job.attempt_id
    manager.pause()
    manager.rerun(job)
    snapshot = manager.snapshot(selected_job_id=job.id, attempt_id=original)
    assert snapshot["jobs"][0]["pending_code"] == "PAUSED"
    assert snapshot["detail"]["pending_code"] is None


@pytest.mark.parametrize("transition_on_read", [1, 2])
def test_snapshot_drops_pending_metadata_after_status_transition(manager, monkeypatch, transition_on_read):
    job = manager.add(command())
    original_snapshot = job.to_snapshot_dict
    reads = 0

    def after_completion(**kwargs):
        nonlocal reads
        reads += 1
        if reads == transition_on_read:
            manager.start()
            assert manager._drain(5)
        return original_snapshot(**kwargs)

    monkeypatch.setattr(job, "to_snapshot_dict", after_completion)
    snapshot = manager.snapshot(selected_job_id=job.id)
    row = snapshot["jobs"][0]
    if transition_on_read == 1:
        assert row["status"] == "DONE"
        assert row["pending_code"] is None
        assert row["pending_reason"] == ""
    else:
        assert row["status"] == "PENDING"
        assert row["pending_code"] == "NOT_STARTED"
    assert snapshot["detail"]["status"] == "DONE"
    assert snapshot["detail"]["pending_code"] is None
    assert snapshot["detail"]["pending_reason"] == ""


def test_snapshot_drops_pending_metadata_for_replaced_attempt(manager, monkeypatch):
    job = manager.add(command())
    original_id = job.attempt_id
    original_snapshot = job.to_snapshot_dict
    replaced = False

    def after_rerun(**kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            manager.start()
            assert manager._drain(5)
            manager.pause()
            manager.rerun(job)
        return original_snapshot(**kwargs)

    with monkeypatch.context() as transition:
        transition.setattr(job, "to_snapshot_dict", after_rerun)
        snapshot = manager.snapshot(selected_job_id=job.id)
    for row in (snapshot["jobs"][0], snapshot["detail"]):
        assert row["attempt_id"] != original_id
        assert row["status"] == "PENDING"
        assert row["pending_code"] is None
    # The next read computes a reason for the new attempt, rather than reusing the old one.
    assert manager.snapshot()["jobs"][0]["pending_code"] == "PAUSED"
