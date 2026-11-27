"""Tests for JobManager queue hold, atomic reorder, and lifecycle-gated removal."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from rpkbin.jobcore import Job, JobManager, JobStatus


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


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_hold_excludes_job_and_unblocks_downstream_in_strict_mode(managers):
    mgr = managers(
        max_workers=4,
        resources={"matlab_license": 1, "grid": 2},
        scheduling_mode="strict",
    )
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", resources={"matlab_license": 1, "grid": 1}, priority=40))
    b = mgr.add(hold_job("b", resources={"matlab_license": 1, "grid": 1}, priority=30))
    c = mgr.add(hold_job("c", resources={"grid": 1}, priority=20))

    assert mgr.set_held(b, True).accepted
    assert mgr.explain_pending(b) == "Held (excluded from queue)"
    snap = mgr.snapshot()
    by_id = {row["id"]: row for row in snap["jobs"]}
    assert by_id[b.id]["held"] is True
    assert by_id[b.id]["pending_code"] == "HELD"

    mgr.start()
    assert started_event(events, "a").wait(5)
    # Holding b means b is excluded from queue and must not block c in strict mode.
    assert started_event(events, "c").wait(5)
    assert not started_event(events, "b").is_set()

    # Cannot hold an already-running job.
    assert not mgr.set_held(a, True).accepted
    with pytest.raises(TypeError):
        mgr.set_held(b, "yes")

    # Unholding b allows it to queue and start once a releases matlab_license.
    assert mgr.set_held(b, False).accepted
    assert a.send("quit")
    assert c.send("quit")
    assert started_event(events, "b").wait(5)
    assert b.send("quit")
    assert mgr._drain(5)


def test_atomic_reorder_assigns_monotone_priorities_and_skips_active(managers):
    mgr = managers(max_workers=1)
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", priority=0))
    c = mgr.add(hold_job("c", priority=0))
    k = mgr.add(hold_job("k", priority=0))
    job_l = mgr.add(hold_job("l", priority=0))

    # Preview reorder when all started at 0: k > a > c > l
    results = mgr.reorder_queue([
        (k.id, k.attempt_id, k.submission_id),
        (a.id, a.attempt_id, a.submission_id),
        (c.id, c.attempt_id, c.submission_id),
        (job_l.id, job_l.attempt_id, job_l.submission_id),
    ])
    assert all(r.accepted for _, r, _ in results)
    assert [k.priority, a.priority, c.priority, job_l.priority] == [3, 2, 1, 0]

    # Repeated reorder keeps priorities bounded instead of inflating endlessly.
    mgr.reorder_queue([job_l.id, k.id, a.id, c.id])
    assert [job_l.priority, k.priority, a.priority, c.priority] == [3, 2, 1, 0]

    mgr.start()
    assert started_event(events, "l").wait(5)
    # While l is running, reorder remaining pending jobs [c, k, a] alongside stale l.
    res = mgr.reorder_queue([c.id, job_l.id, k.id, a.id])
    by_jid = {jid: r for jid, r, _ in res}
    assert by_jid[c.id].accepted and by_jid[k.id].accepted and by_jid[a.id].accepted
    assert not by_jid[job_l.id].accepted
    assert c.priority > k.priority > a.priority

    for job, name in ((job_l, "c"), (c, "k"), (k, "a")):
        assert job.send("quit")
        assert started_event(events, name).wait(5)
    assert a.send("quit")
    assert mgr._drain(5)


def test_reorder_rejects_duplicates_and_preserves_distinct_tiers(managers):
    mgr = managers()
    a = mgr.add(hold_job("a", priority=40))
    b = mgr.add(hold_job("b", priority=30))
    c = mgr.add(hold_job("c", priority=20))
    with pytest.raises(ValueError, match="Duplicate"):
        mgr.reorder_queue([a.id, a.id])
    mgr.reorder_queue([c.id, a.id, b.id])
    assert [c.priority, a.priority, b.priority] == [40, 30, 20]


def test_remove_job_allowed_in_preview_and_stopped_idle_but_blocked_while_running(managers):
    mgr = managers(max_workers=1)
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", priority=30))
    b = mgr.add(hold_job("b", priority=20))
    c = mgr.add(hold_job("c", priority=10))

    # Preview: remove b cleanly.
    assert mgr.remove_job(b.id, expected_attempt=b.attempt_id, expected_submission=b.submission_id).accepted
    assert [j.id for j in mgr.jobs] == [a.id, c.id]

    mgr.start()
    assert started_event(events, "a").wait(5)
    # Running (even after pause while worker active): removal is rejected.
    assert not mgr.remove_job(c.id).accepted
    mgr.pause()
    assert not mgr.remove_job(c.id).accepted

    # Stop all + drain -> idle stopped: removal is unlocked cleanly.
    mgr.stop_all()
    assert mgr._drain(5)
    wait_for(lambda: mgr._active_workers == 0 and not mgr._threads)
    assert mgr.remove_job(c.id, expected_attempt=c.attempt_id, expected_submission=c.submission_id).accepted
    assert [j.id for j in mgr.jobs] == [a.id]


def test_remove_last_preview_job_disables_ready(managers):
    mgr = managers()
    only = mgr.add(hold_job("only"))
    mgr.validate()
    assert mgr.ready is True
    assert mgr.remove_job(only.id).accepted
    assert mgr.jobs == []
    assert mgr.ready is False


def test_set_priority_and_reorder_queue_publish_state_under_event_lock(managers, monkeypatch):
    from rpkbin.jobcore._job_attempt import _JobEvents

    mgr = managers()
    a = mgr.add(hold_job("a", priority=10))
    b = mgr.add(hold_job("b", priority=20))
    published = []

    for job in (a, b):
        def record_publish(*, _job=job, flush=False, snapshot=None):
            assert _JobEvents.held() is not None
            assert _job._events_lock._is_owned()
            published.append((_job.name, _job.priority, _job._attempt.priority))

        monkeypatch.setattr(job._attempt.sink, "publish_state", record_publish)

    assert mgr.set_priority(a.id, 50).accepted
    assert published == [("a", 50, 50)]
    published.clear()

    res = mgr.reorder_queue([b.id, a.id])
    assert all(r.accepted for _, r, _ in res)
    assert published == [("b", 50, 50), ("a", 20, 20)]


def test_concurrent_reorder_queue_and_stop_atomicity_without_deadlock(managers):
    from concurrent.futures import ThreadPoolExecutor

    mgr = managers(max_workers=1)
    jobs = [mgr.add(hold_job(f"j{i}", priority=i * 10)) for i in range(6)]
    barrier = threading.Barrier(4, timeout=5)
    errors = []

    def reorder_forward():
        try:
            barrier.wait()
            for _ in range(40):
                mgr.reorder_queue([j.id for j in jobs])
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def reorder_reverse():
        try:
            barrier.wait()
            for _ in range(40):
                mgr.reorder_queue([j.id for j in reversed(jobs)])
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def stop_some():
        try:
            barrier.wait()
            for job in jobs[::2]:
                time.sleep(0.001)
                job.stop()
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def observe_priorities():
        try:
            barrier.wait()
            for _ in range(40):
                with mgr._cond:
                    pending = [j for j in jobs if j.status is JobStatus.PENDING]
                    priorities = [j.priority for j in pending]
                    # Eligible pending jobs always hold distinct priorities.
                    assert len(priorities) == len(set(priorities))
                    for j in jobs:
                        assert j.priority == j._attempt.priority
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(reorder_forward),
            pool.submit(reorder_reverse),
            pool.submit(stop_some),
            pool.submit(observe_priorities),
        ]
        for fut in futures:
            fut.result(timeout=10)

    assert not errors
    for job in jobs[::2]:
        assert job.status is JobStatus.CANCELLED
        assert job._attempt.finalized
    remaining = [j for j in jobs[1::2]]
    res = mgr.reorder_queue([j.id for j in jobs])
    by_id = {jid: receipt for jid, receipt, _ in res}
    for job in jobs[::2]:
        assert not by_id[job.id].accepted
    for job in remaining:
        assert by_id[job.id].accepted


def test_restore_job_in_preview_and_stopped_idle_preserves_order_and_blocks_while_running(
    managers,
):
    mgr = managers(max_workers=1, resources={"grid": 2})
    events = track_starts(mgr)
    a = mgr.add(hold_job("a", priority=30))
    b = mgr.add(hold_job("b", priority=20))
    c = mgr.add(hold_job("c", priority=10))

    # Preview: remove b -> lands in removed_jobs & snapshot["removed_jobs"]; restore puts it back in order.
    assert mgr.remove_job(
        b.id, expected_attempt=b.attempt_id, expected_submission=b.submission_id
    ).accepted
    assert [j.id for j in mgr.jobs] == [a.id, c.id]
    assert [j.id for j in mgr.removed_jobs] == [b.id]
    assert [j["id"] for j in mgr.snapshot()["removed_jobs"]] == [b.id]

    assert not mgr.restore_job(b.id, expected_attempt="stale-attempt").accepted
    assert not mgr.restore_job("nonexistent").accepted
    assert mgr.restore_job(
        b.id, expected_attempt=b.attempt_id, expected_submission=b.submission_id
    ).accepted
    assert [j.id for j in mgr.jobs] == [a.id, b.id, c.id]
    assert mgr.removed_jobs == ()
    assert mgr.snapshot()["removed_jobs"] == []

    # Removing and restoring the last remaining preview job toggles ready flag.
    m_single = managers()
    only = m_single.add(hold_job("only"))
    m_single.validate()
    assert m_single.ready is True
    assert m_single.remove_job(only.id).accepted
    assert m_single.ready is False
    assert m_single.restore_job(only.id).accepted
    assert m_single.ready is True

    # Restoring a job whose required resource exceeds capacity is rejected.
    m_res = managers(resources={"grid": 1})
    need_grid = m_res.add(hold_job("need-grid", resources={"grid": 1}))
    assert m_res.remove_job(need_grid.id).accepted
    m_res.update_settings(resources={"grid": 0})
    assert not m_res.restore_job(need_grid.id).accepted

    # Remove b in preview, then start a -> restore_job is blocked while worker active (even when paused).
    assert mgr.remove_job(b.id).accepted
    mgr.start()
    assert started_event(events, "a").wait(5)
    assert not mgr.restore_job(b.id).accepted
    mgr.pause()
    assert not mgr.restore_job(b.id).accepted

    # Stop all + drain -> idle stopped: restore and remove/restore of finished job both work.
    mgr.stop_all()
    assert mgr._drain(5)
    wait_for(lambda: mgr._active_workers == 0 and not mgr._threads)
    assert mgr.restore_job(b.id).accepted
    assert mgr.remove_job(a.id).accepted
    assert mgr.restore_job(a.id).accepted
    assert [j.id for j in mgr.jobs] == [a.id, b.id, c.id]


def test_stop_all_include_held_false_keeps_held_pending_jobs(managers):
    mgr = managers(max_workers=2)
    events = track_starts(mgr)
    running = mgr.add(hold_job("running", priority=30))
    queued = mgr.add(hold_job("queued", priority=20))
    held = mgr.add(hold_job("held", priority=10))
    assert mgr.set_held(held.id, True).accepted
    assert mgr.set_held(queued.id, True).accepted

    mgr.start()
    assert started_event(events, "running").wait(5)
    # Include queued after start with max_workers paused/busy or pause first so queued stays PENDING.
    mgr.pause()
    assert mgr.set_held(queued.id, False).accepted

    stopped = mgr.stop_all(include_held=False)
    assert stopped == 2
    wait_for(lambda: running.status is JobStatus.CANCELLED and queued.status is JobStatus.CANCELLED)
    wait_for(lambda: mgr._active_workers == 0 and not mgr._threads)
    assert mgr.paused is True
    assert running.status is JobStatus.CANCELLED
    assert queued.status is JobStatus.CANCELLED
    assert held.status is JobStatus.PENDING and held._attempt.held is True

    # Default include_held=True still cancels held jobs cleanly.
    assert mgr.stop_all() == 1
    assert mgr._drain(5)
    assert held.status is JobStatus.CANCELLED


def test_restore_job_after_rerun_preserves_original_registration_order(managers):
    mgr = managers(max_workers=1)
    a = mgr.add(Job("a", [sys.executable, "-c", "pass"], pty=False))
    b = mgr.add(Job("b", [sys.executable, "-c", "pass"], pty=False))
    c = mgr.add(Job("c", [sys.executable, "-c", "pass"], pty=False))
    mgr.start()
    assert mgr._drain(5)
    mgr.pause()
    assert mgr.rerun(a.id).accepted
    assert mgr.remove_job(a.id).accepted
    assert mgr.restore_job(a.id).accepted
    assert [j.id for j in mgr.jobs] == [a.id, b.id, c.id]


def test_reorder_queue_and_restore_job_accept_job_objects_and_unique_names(managers):
    mgr = managers()
    a = mgr.add(hold_job("alpha", priority=10))
    b = mgr.add(hold_job("beta", priority=20))
    dup1 = mgr.add(hold_job("dup", priority=5))
    dup2 = mgr.add(hold_job("dup", priority=1))

    res = mgr.reorder_queue([b, (a, a.attempt_id, a.submission_id), dup1, dup2])
    assert all(r.accepted for _, r, _ in res)
    assert [jid for jid, _, _ in res] == [b.id, a.id, dup1.id, dup2.id]
    assert b.priority > a.priority > dup1.priority > dup2.priority

    assert mgr.remove_job(b).accepted
    assert mgr.restore_job(b).accepted
    assert mgr.remove_job("alpha").accepted
    assert mgr.restore_job("alpha").accepted

    assert mgr.remove_job(dup1).accepted
    assert mgr.remove_job(dup2).accepted
    ambiguous = mgr.restore_job("dup")
    assert ambiguous.status == "rejected" and "ambiguous" in ambiguous.reason.lower()
    assert mgr.restore_job(dup1).accepted
    assert mgr.restore_job(dup2.id).accepted
    assert [j.id for j in mgr.jobs] == [a.id, b.id, dup1.id, dup2.id]
