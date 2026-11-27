"""HTTP priority edits use explicit executions and honest per-job receipts."""

from __future__ import annotations

from rpkbin.wave import WaveWorkspace

import json
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from contextlib import contextmanager

import pytest

from rpkbin.jobcore import Job, JobManager
from rpkbin.wave.web import WaveServer


def request(base, path, body=None):
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(base).query).get("token", [""])[0]
    req = urllib.request.Request(
        urllib.parse.urljoin(base, path),
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Wave-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@contextmanager
def server_for(manager):
    manager.validate()
    server = WaveServer(manager)
    server.start()
    try:
        yield server.url.rstrip("/")
    finally:
        manager.close()
        server.stop()


def post(base, payload):
    code, state = request(base, "/api/state")
    assert code == 200
    return request(
        base, "/api/job/priority", {"workspace_id": state["workspace_id"], **payload}
    )


def target(job):
    return {
        "job_id": job.id,
        "attempt_id": job.attempt_id,
        "submission_id": job.submission_id,
    }


def add_job(manager, name, code="pass", **kwargs):
    return manager.add(Job(name, [sys.executable, "-u", "-c", code], pty=False, **kwargs))


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while not predicate():
        assert time.monotonic() < deadline, "Job did not reach the expected state"
        time.sleep(.01)


def test_priority_batch_edits_preview_jobs_without_starting_them():
    manager = WaveWorkspace(JobManager())
    first, second = (add_job(manager, name) for name in ("first", "second"))
    captured = [target(first), target(second)]
    with server_for(manager) as base:
        code, response = post(base, {"targets": captured, "priority": 13})
        assert code == 200, response
        assert response["action"] == "priority"
        assert response["priority"] == 13 and response["affected"] == 2
        assert [row["job_id"] for row in response["results"]] == [first.id, second.id]
        for row, original in zip(response["results"], captured):
            assert row["ok"] and row["priority"] == 13 and row["message"]
            assert all(row[key] == value for key, value in original.items())
        assert first.priority == second.priority == 13
        assert not manager.started and first.pid is second.pid is None


@pytest.mark.parametrize("priority", [True, False, None, 1.5, "10", 2**53, -(2**53)])
def test_invalid_priority_rejects_batch_before_mutating_any_job(priority):
    manager = WaveWorkspace(JobManager())
    first = add_job(manager, "first", priority=2)
    second = add_job(manager, "second", priority=3)
    with server_for(manager) as base:
        code, _ = post(base, {"targets": [target(first), target(second)], "priority": priority})
        assert code == 400
        assert (first.priority, second.priority) == (2, 3)


@pytest.mark.parametrize("priority", [-(2**53 - 1), 0, 2**53 - 1])
def test_safe_integer_priority_boundaries_are_not_clamped(priority):
    manager = WaveWorkspace(JobManager())
    job = add_job(manager, "boundary")
    with server_for(manager) as base:
        code, response = post(base, {"targets": [target(job)], "priority": priority})
        assert code == 200, response
        assert response["affected"] == 1 and job.priority == priority


@pytest.mark.parametrize("invalid", [None, {}, "selected", {"job_id": "missing"}])
def test_malformed_later_target_rejects_before_editing_valid_first_target(invalid):
    manager = WaveWorkspace(JobManager())
    job = add_job(manager, "unchanged", priority=4)
    with server_for(manager) as base:
        code, _ = post(base, {"targets": [target(job), invalid], "priority": 99})
        assert code == 400 and job.priority == 4


@pytest.mark.parametrize("targets", [None, [], {}, "selected"])
def test_missing_or_empty_explicit_targets_never_fall_back_to_a_job(targets):
    manager = WaveWorkspace(JobManager())
    job = add_job(manager, "unchanged", priority=4)
    with server_for(manager) as base:
        code, _ = post(base, {"targets": targets, "priority": 99})
        assert code == 400 and job.priority == 4


def test_duplicate_job_targets_are_rejected_before_editing():
    manager = WaveWorkspace(JobManager())
    job = add_job(manager, "unchanged", priority=4)
    captured = target(job)
    with server_for(manager) as base:
        code, _ = post(base, {"targets": [captured, {**captured, "attempt_id": "other"}], "priority": 99})
        assert code == 400 and job.priority == 4


def test_oversized_target_batch_is_rejected_before_editing_valid_first_job():
    manager = WaveWorkspace(JobManager())
    job = add_job(manager, "unchanged", priority=4)
    captured = [target(job)] + [
        {"job_id": f"missing-{index}", "attempt_id": "attempt", "submission_id": "submission"}
        for index in range(10000)
    ]
    with server_for(manager) as base:
        code, _ = post(base, {"targets": captured, "priority": 99})
        assert code == 400 and job.priority == 4


def test_unknown_and_cancelled_targets_do_not_prevent_valid_pending_edits():
    manager = WaveWorkspace(JobManager())
    cancelled = add_job(manager, "cancelled", priority=2)
    pending = add_job(manager, "pending", priority=3)
    assert cancelled.stop()
    unknown = {"job_id": "missing", "attempt_id": "attempt", "submission_id": "submission"}
    with server_for(manager) as base:
        code, response = post(base, {"targets": [target(cancelled), unknown, target(pending)], "priority": 9})
        assert code == 200, response
        assert response["affected"] == 1
        assert [row["ok"] for row in response["results"]] == [False, False, True]
        assert all(row["message"] for row in response["results"])
        assert (cancelled.priority, pending.priority) == (2, 9)


def test_stale_attempt_cannot_change_priority_of_a_rerun():
    manager = WaveWorkspace(JobManager())
    rerun = add_job(manager, "rerun", priority=2)
    pending = add_job(manager, "pending", priority=3)
    original = target(rerun)
    assert rerun.stop()
    manager.rerun(rerun)
    assert rerun.attempt_id != original["attempt_id"]
    with server_for(manager) as base:
        code, response = post(base, {"targets": [original, target(pending)], "priority": 8})
        assert code == 200, response
        assert response["affected"] == 1
        rejected, accepted = response["results"]
        assert not rejected["ok"] and "stale" in rejected["message"].lower()
        assert rejected["attempt_id"] == original["attempt_id"]
        assert accepted["ok"] and (rerun.priority, pending.priority) == (2, 8)


def test_reserved_pending_job_is_rejected_even_before_running_state_is_visible():
    manager = WaveWorkspace(JobManager())
    reserved = add_job(manager, "reserved", priority=1)
    pending = add_job(manager, "pending", priority=2)
    with server_for(manager) as base:
        # Reproduce the scheduler's reservation boundary without a live worker.
        # Restore it before close so this synthetic reservation cannot trap cleanup.
        with manager._cond:
            reserved._attempt.reserved = True
        try:
            code, response = post(base, {"targets": [target(reserved), target(pending)], "priority": 7})
            assert code == 200, response
            assert [row["ok"] for row in response["results"]] == [False, True]
            assert (reserved.priority, pending.priority) == (1, 7)
        finally:
            with manager._cond:
                reserved._attempt.reserved = False


def test_running_job_is_not_preempted_by_priority_batch():
    manager = WaveWorkspace(JobManager(max_workers=1))
    running = add_job(manager, "running", "print('ready', flush=True); input()", priority=5)
    pending = add_job(manager, "pending", priority=3)
    with server_for(manager) as base:
        manager.start()
        wait_for(lambda: "ready" in running.logs())
        try:
            code, response = post(base, {"targets": [target(running), target(pending)], "priority": 20})
            assert code == 200, response
            assert [row["ok"] for row in response["results"]] == [False, True]
            assert (running.priority, pending.priority) == (5, 20)
            assert running.status.value == "RUNNING" and running.returncode is None
        finally:
            running.send("done")
        assert manager._drain(5)


def test_priority_edits_preserve_fifo_ties_and_affect_real_dispatch_order():
    manager = WaveWorkspace(JobManager(max_workers=1))
    first, second, third = (
        add_job(manager, name, "print('RUN', flush=True)")
        for name in ("first", "second", "third")
    )
    launches = []
    manager.add_log_listener(lambda record: launches.append(record["job_name"]) if record["text"] == "RUN" else None)
    with server_for(manager) as base:
        # Send in reverse order: priority edits must not change equal-priority FIFO.
        code, response = post(base, {"targets": [target(third), target(second)], "priority": 5})
        assert code == 200, response
        manager.start()
        assert manager._drain(5)
        assert launches == [second.name, third.name, first.name]


def test_scheduler_http_rejections_use_manager_receipts():
    manager = WaveWorkspace(JobManager())
    pending = add_job(manager, "pending")
    ended = add_job(manager, "ended", priority=3)
    assert ended.stop()
    with server_for(manager) as base:
        original = target(pending)
        rerun = manager.rerun(pending)
        priority = manager.set_priority(ended, 17)
        assert rerun.status == priority.status == "rejected"

        code, state = request(base, "/api/state")
        assert code == 200
        code, response = request(base, "/api/job/action", {
            "workspace_id": state["workspace_id"], "action": "rerun", "targets": [original],
        })
        assert code == 200 and response["affected"] == 0
        row, = response["results"]
        assert (row["status"], row["reason"]) == (rerun.status, rerun.reason)
        assert "new_attempt_id" not in row and target(pending) == original

        code, response = post(base, {"targets": [target(ended)], "priority": 17})
        assert code == 200 and response["affected"] == 0
        row, = response["results"]
        assert (row["status"], row["reason"]) == (priority.status, priority.reason)
        assert "priority" not in row and ended.priority == 3
