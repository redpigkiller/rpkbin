"""Tests for JobManager priority scheduling modes (A1)."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from rpkbin.jobcore import Job, JobManager, JobStatus
from rpkbin.jobcore.manager import _scheduling_mode


@pytest.fixture
def managers():
    created = []

    def make(**kwargs):
        manager = JobManager(**kwargs)
        created.append(manager)
        return manager

    yield make
    for manager in created:
        manager.close()


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def hold_job(name, *, resources=None, priority=0):
    code = (
        "import sys; "
        f"print('STARTED:{name}', flush=True); "
        "sys.stdin.readline()"
    )
    return Job(
        name,
        [sys.executable, "-u", "-c", code],
        pty=False,
        resources=resources,
        priority=priority,
    )


def track_starts(manager):
    events = {}

    def on_log(record):
        text = record.get("text", "")
        if record.get("source") == "stdout" and text.startswith("STARTED:"):
            name = text.split(":", 1)[1]
            events.setdefault(name, threading.Event()).set()

    manager.add_log_listener(on_log)
    return events


def started_event(events, name):
    return events.setdefault(name, threading.Event())


def snapshot_rows(manager, *, selected_job_id=None):
    snap = manager.snapshot(selected_job_id=selected_job_id)
    return snap, {row["name"]: row for row in snap["jobs"]}


def test_scheduling_mode_validation_and_snapshot(managers):
    for mode in ("backfill", "strict", "tier_strict"):
        assert _scheduling_mode(mode) == mode
        mgr = managers(scheduling_mode=mode)
        assert mgr.scheduling_mode == mode
        assert mgr.snapshot()["scheduling_mode"] == mode

    default_mgr = managers()
    assert default_mgr.scheduling_mode == "backfill"
    assert default_mgr.snapshot()["scheduling_mode"] == "backfill"

    with pytest.raises(TypeError):
        _scheduling_mode(None)
    with pytest.raises(TypeError):
        JobManager(scheduling_mode=None)
    default_mgr.update_settings(scheduling_mode=None)
    assert default_mgr.scheduling_mode == "backfill"

    for bad_type in (123, True, ["strict"], b"strict"):
        with pytest.raises(TypeError):
            _scheduling_mode(bad_type)
        with pytest.raises(TypeError):
            JobManager(scheduling_mode=bad_type)
        with pytest.raises(TypeError):
            default_mgr.update_settings(scheduling_mode=bad_type)
        assert default_mgr.scheduling_mode == "backfill"

    for bad_value in ("", "fifo", "STRICT", "tier-strict", " backfill"):
        with pytest.raises(ValueError):
            _scheduling_mode(bad_value)
        with pytest.raises(ValueError):
            JobManager(scheduling_mode=bad_value)
        with pytest.raises(ValueError):
            default_mgr.update_settings(scheduling_mode=bad_value)
        assert default_mgr.scheduling_mode == "backfill"


def test_update_settings_preserves_and_updates_scheduling_mode_atomically(managers):
    mgr = managers(resources={"slot": 1}, scheduling_mode="strict")
    mgr.add(hold_job("a", resources={"slot": 1}))

    mgr.update_settings(max_workers=2)
    assert mgr.scheduling_mode == "strict"
    assert mgr.max_workers == 2
    assert mgr.snapshot()["scheduling_mode"] == "strict"

    with pytest.raises(ValueError, match="cannot remove a resource"):
        mgr.update_settings(resources={}, scheduling_mode="backfill")
    assert mgr.resources == {"slot": 1}
    assert mgr.scheduling_mode == "strict"

    mgr.update_settings(scheduling_mode="tier_strict")
    assert mgr.scheduling_mode == "tier_strict"
    assert mgr.snapshot()["scheduling_mode"] == "tier_strict"


def test_backfill_mode_runs_lower_priority_compatible_jobs(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2, "vcs_license": 1},
        scheduling_mode="backfill",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=40))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=30))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=20))
    d = mgr.add(hold_job("d", resources={"vcs_license": 1, "grid": 1}, priority=10))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert started_event(events, "c").wait(5)
    assert not started_event(events, "b").is_set()
    assert not started_event(events, "d").is_set()

    snap, rows = snapshot_rows(mgr)
    assert snap["scheduling_mode"] == "backfill"
    assert rows["a"]["status"] == "RUNNING"
    assert rows["c"]["status"] == "RUNNING"
    assert rows["b"]["status"] == "PENDING"
    assert rows["b"]["pending_code"] == "WAIT_RESOURCE"
    assert rows["b"]["pending_reason"] == "Waiting for matlab_license (1/1 in use, need 1)"
    assert rows["d"]["status"] == "PENDING"
    assert rows["d"]["pending_code"] == "WAIT_RESOURCE"
    assert rows["d"]["pending_reason"] == "Waiting for grid (2/2 in use, need 1)"

    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert not started_event(events, "d").is_set()

    assert b.send("quit")
    assert started_event(events, "d").wait(5)

    assert c.send("quit")
    assert d.send("quit")
    assert mgr._drain(5)
    assert all(j.status is JobStatus.DONE for j in (a, b, c, d))


def test_strict_mode_blocks_lower_jobs_and_explains_blocker(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2, "vcs_license": 1},
        scheduling_mode="strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=40))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=30))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=20))
    d = mgr.add(hold_job("d", resources={"vcs_license": 1, "grid": 1}, priority=10))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert not started_event(events, "b").is_set()
    assert not started_event(events, "c").is_set()
    assert not started_event(events, "d").is_set()

    expected_b = "Waiting for matlab_license (1/1 in use, need 1)"
    expected_blocked = f"Waiting behind higher-priority job b ({expected_b})"

    snap, rows = snapshot_rows(mgr, selected_job_id=c.id)
    assert snap["scheduling_mode"] == "strict"
    assert rows["a"]["status"] == "RUNNING"
    assert rows["b"]["status"] == "PENDING"
    assert rows["b"]["pending_code"] == "WAIT_RESOURCE"
    assert rows["b"]["pending_reason"] == expected_b
    assert rows["c"]["status"] == "PENDING"
    assert rows["c"]["pending_code"] == "WAIT_RESOURCE"
    assert rows["c"]["pending_reason"] == expected_blocked
    assert snap["detail"]["pending_code"] == "WAIT_RESOURCE"
    assert snap["detail"]["pending_reason"] == expected_blocked
    assert rows["d"]["status"] == "PENDING"
    assert rows["d"]["pending_code"] == "WAIT_RESOURCE"
    assert rows["d"]["pending_reason"] == expected_blocked
    assert mgr.explain_pending(b) == expected_b
    assert mgr.explain_pending(c) == expected_blocked
    assert mgr.explain_pending(d) == expected_blocked

    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert started_event(events, "c").wait(5)
    assert not started_event(events, "d").is_set()

    assert b.send("quit")
    assert started_event(events, "d").wait(5)

    assert c.send("quit")
    assert d.send("quit")
    assert mgr._drain(5)
    assert all(j.status is JobStatus.DONE for j in (a, b, c, d))


def test_strict_mode_blocks_same_priority_later_job(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2, "vcs_license": 1},
        scheduling_mode="strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=30))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=20))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=20))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert not started_event(events, "b").is_set()
    assert not started_event(events, "c").is_set()
    assert mgr.explain_pending(c) == (
        "Waiting behind higher-priority job b "
        "(Waiting for matlab_license (1/1 in use, need 1))"
    )

    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert started_event(events, "c").wait(5)
    assert b.send("quit")
    assert c.send("quit")
    assert mgr._drain(5)


def test_tier_strict_allows_same_tier_and_blocks_lower_tiers(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2, "vcs_license": 1},
        scheduling_mode="tier_strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=30))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=20))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=20))
    d = mgr.add(hold_job("d", resources={"vcs_license": 1, "grid": 1}, priority=10))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert started_event(events, "c").wait(5)
    assert not started_event(events, "b").is_set()
    assert not started_event(events, "d").is_set()

    # Finish c while a is still running so grid drops to 1/2 (d has all its own resources).
    assert c.send("quit")
    wait_for(lambda: c.status is JobStatus.DONE)
    assert not started_event(events, "d").is_set()

    expected_blocked = (
        "Waiting behind higher-priority job b "
        "(Waiting for matlab_license (1/1 in use, need 1))"
    )
    snap, rows = snapshot_rows(mgr, selected_job_id=d.id)
    assert snap["scheduling_mode"] == "tier_strict"
    assert rows["d"]["pending_code"] == "WAIT_RESOURCE"
    assert rows["d"]["pending_reason"] == expected_blocked
    assert snap["detail"]["pending_reason"] == expected_blocked
    assert mgr.explain_pending(d) == expected_blocked

    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert started_event(events, "d").wait(5)

    assert b.send("quit")
    assert d.send("quit")
    assert mgr._drain(5)
    assert all(j.status is JobStatus.DONE for j in (a, b, c, d))


def test_tier_strict_blocks_when_c_is_lower_priority_than_b(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2, "vcs_license": 1},
        scheduling_mode="tier_strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=40))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=30))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=20))
    d = mgr.add(hold_job("d", resources={"vcs_license": 1, "grid": 1}, priority=10))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert not started_event(events, "b").is_set()
    assert not started_event(events, "c").is_set()
    assert not started_event(events, "d").is_set()

    expected_blocked = (
        "Waiting behind higher-priority job b "
        "(Waiting for matlab_license (1/1 in use, need 1))"
    )
    assert mgr.explain_pending(c) == expected_blocked
    assert mgr.explain_pending(d) == expected_blocked

    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert started_event(events, "c").wait(5)
    assert not started_event(events, "d").is_set()

    assert b.send("quit")
    assert started_event(events, "d").wait(5)
    assert c.send("quit")
    assert d.send("quit")
    assert mgr._drain(5)


def test_tier_strict_breaks_on_worker_saturation_after_same_tier_dispatch(managers):
    mgr = managers(
        max_workers=2,
        resources={"matlab_license": 1, "grid": 3},
        scheduling_mode="tier_strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1}, priority=30))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1}, priority=20))
    c1 = mgr.add(hold_job("c1", resources={"grid": 1}, priority=20))
    c2 = mgr.add(hold_job("c2", resources={"grid": 1}, priority=20))
    d = mgr.add(hold_job("d", resources={}, priority=10))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert started_event(events, "c1").wait(5)
    assert not started_event(events, "b").is_set()
    assert not started_event(events, "c2").is_set()
    assert not started_event(events, "d").is_set()

    _, rows = snapshot_rows(mgr)
    assert rows["b"]["pending_code"] == "WAIT_WORKER"
    assert rows["c2"]["pending_code"] == "WAIT_WORKER"
    assert rows["d"]["pending_code"] == "WAIT_WORKER"

    assert c1.send("quit")
    assert started_event(events, "c2").wait(5)
    assert not started_event(events, "d").is_set()

    assert c2.send("quit")
    wait_for(lambda: c2.status is JobStatus.DONE)
    assert not started_event(events, "d").is_set()
    assert mgr.explain_pending(d) == (
        "Waiting behind higher-priority job b "
        "(Waiting for matlab_license (1/1 in use, need 1))"
    )

    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert started_event(events, "d").wait(5)
    assert b.send("quit")
    assert d.send("quit")
    assert mgr._drain(5)


def test_live_switch_from_strict_to_backfill_immediately_dispatches_c(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2, "vcs_license": 1},
        scheduling_mode="strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=30))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=20))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=10))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert not started_event(events, "b").is_set()
    assert not started_event(events, "c").is_set()

    mgr.update_settings(scheduling_mode="backfill")
    assert mgr.snapshot()["scheduling_mode"] == "backfill"
    assert started_event(events, "c").wait(5)
    assert a.status is JobStatus.RUNNING
    assert not started_event(events, "b").is_set()

    assert c.send("quit")
    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert b.send("quit")
    assert mgr._drain(5)


def test_live_switch_from_strict_to_tier_strict_dispatches_same_tier_only(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2, "vcs_license": 1},
        scheduling_mode="strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=30))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=20))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=20))
    d = mgr.add(hold_job("d", resources={"vcs_license": 1, "grid": 1}, priority=10))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert not started_event(events, "c").is_set()
    assert not started_event(events, "d").is_set()

    mgr.update_settings(scheduling_mode="tier_strict")
    assert started_event(events, "c").wait(5)
    assert not started_event(events, "b").is_set()
    assert not started_event(events, "d").is_set()

    assert c.send("quit")
    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert started_event(events, "d").wait(5)
    assert b.send("quit")
    assert d.send("quit")
    assert mgr._drain(5)


@pytest.mark.parametrize("mode", ("backfill", "strict", "tier_strict"))
def test_snapshot_and_explain_pending_agree_across_modes(managers, monkeypatch, mode):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 3, "vcs_license": 1},
        scheduling_mode=mode,
    )
    b = mgr.add(hold_job("b", resources={"matlab_license": 1}, priority=20))
    r = mgr.add(hold_job("r", resources={"grid": 1}, priority=25))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=20))
    d = mgr.add(hold_job("d", resources={"vcs_license": 1}, priority=10))
    e = mgr.add(hold_job("e", resources={"matlab_license": 1}, priority=5))

    with monkeypatch.context() as state:
        state.setattr(mgr, "started", True)
        state.setattr(mgr, "ready", True)
        state.setattr(mgr, "paused", False)
        state.setattr(mgr, "_active_workers", 1)
        state.setattr(mgr, "_used", {"matlab_license": 1})
        state.setattr(r._attempt, "reserved", True)

        snap = mgr.snapshot()
        by_id = {row["id"]: row for row in snap["jobs"]}
        for job in (b, r, c, d, e):
            code, text = mgr._pending_reason(job)
            assert by_id[job.id]["pending_code"] == code
            assert by_id[job.id]["pending_reason"] == text
            assert mgr.explain_pending(job) == text


def test_strict_mode_paused_and_not_started_do_not_report_blocker(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2},
        scheduling_mode="strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1}, priority=30))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1}, priority=20))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=10))

    assert mgr.explain_pending(c) == "Not started"
    mgr.validate()
    assert mgr.explain_pending(c) == "Ready to start"

    mgr.start()
    assert started_event(events, "a").wait(5)
    mgr.pause()
    _, rows = snapshot_rows(mgr)
    assert rows["b"]["pending_code"] == "PAUSED"
    assert rows["c"]["pending_code"] == "PAUSED"
    assert mgr.explain_pending(c) == "Paused (dispatch is paused)"

    mgr.resume()
    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert started_event(events, "c").wait(5)
    assert b.send("quit")
    assert c.send("quit")
    assert mgr._drain(5)


def test_set_priority_promotes_blocked_job_in_strict_mode(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2},
        scheduling_mode="strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=30))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=20))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=10))

    mgr.start()
    assert started_event(events, "a").wait(5)
    assert not started_event(events, "c").is_set()

    assert mgr.set_priority(c, 25).accepted
    assert started_event(events, "c").wait(5)
    assert not started_event(events, "b").is_set()

    assert c.send("quit")
    assert a.send("quit")
    assert started_event(events, "b").wait(5)
    assert b.send("quit")
    assert mgr._drain(5)


def test_backfill_snapshot_does_not_scan_blockers(managers, monkeypatch):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2},
        scheduling_mode="backfill",
    )
    mgr.add(hold_job("b", resources={"matlab_license": 1}, priority=20))
    mgr.add(hold_job("c", resources={"grid": 1}, priority=10))

    def fail_blocker(*, before):
        raise AssertionError("_find_blocker should not be called in backfill mode")

    monkeypatch.setattr(mgr, "_find_blocker", fail_blocker)
    with monkeypatch.context() as state:
        state.setattr(mgr, "started", True)
        state.setattr(mgr, "ready", True)
        state.setattr(mgr, "paused", False)
        state.setattr(mgr, "_used", {"matlab_license": 1})
        snap = mgr.snapshot()
        rows = {row["name"]: row for row in snap["jobs"]}
        assert rows["b"]["pending_code"] == "WAIT_RESOURCE"
        assert rows["c"]["pending_code"] == "READY"


@pytest.mark.parametrize("mode", ["strict", "tier_strict"])
def test_schedule_400_jobs_uses_single_linear_scan_without_find_blocker(
    managers, monkeypatch, mode
):
    mgr = managers(
        max_workers=4,
        resources={"license": 1, "grid": 4},
        scheduling_mode=mode,
    )
    # High-priority blocker needs unavailable license; 399 lower jobs only need grid.
    blocker = mgr.add(hold_job("blocker", resources={"license": 1}, priority=500))
    same_tier = mgr.add(hold_job("same_tier", resources={"grid": 1}, priority=500))
    tail = [
        mgr.add(hold_job(f"j_{i}", resources={"grid": 1}, priority=400 - i))
        for i in range(398)
    ]

    def fail_find_blocker(*, before):
        raise AssertionError("_schedule must not call _find_blocker")

    monkeypatch.setattr(mgr, "_find_blocker", fail_find_blocker)
    local_calls = 0
    orig_local = mgr._local_pending_reason

    def count_local(job):
        nonlocal local_calls
        local_calls += 1
        return orig_local(job)

    monkeypatch.setattr(mgr, "_local_pending_reason", count_local)
    dispatched = []

    def fake_execute(job, a):
        dispatched.append(job.name)
        a.returncode = 0
        job._finish(a)

    for job in mgr.jobs:
        monkeypatch.setattr(job, "_execute", lambda a, _j=job: fake_execute(_j, a))

    with mgr._cond:
        mgr.ready = True
        mgr.started = True
        mgr.paused = False
        # Simulate license currently held by an active worker.
        mgr._active_workers = 1
        mgr._used = {"license": 1, "grid": 0}

    # Run one scheduling pass by closing right after the first wait.
    orig_wait = mgr._cond.wait

    def stop_after_one_pass(timeout=None):
        mgr._closed = True

    monkeypatch.setattr(mgr._cond, "wait", stop_after_one_pass)
    mgr._schedule()
    for thread in list(mgr._threads):
        thread.join(timeout=5)
    with mgr._cond:
        mgr._closed = False
        mgr._active_workers = 0
        mgr._used.clear()
    monkeypatch.setattr(mgr._cond, "wait", orig_wait)

    if mode == "strict":
        assert dispatched == []
        # Strict mode stops scanning immediately upon hitting the first blocker.
        assert local_calls == 1
    else:
        # tier_strict dispatches same_tier on pass 1 (2 checks) and stops at lower tier on pass 2 (+1 check).
        assert dispatched == ["same_tier"]
        assert same_tier.status is JobStatus.DONE
        assert local_calls == 3
        assert all(j.status is JobStatus.PENDING for j in tail)
    assert blocker.status is JobStatus.PENDING
