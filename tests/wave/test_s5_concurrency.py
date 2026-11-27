"""Regression coverage for S5 output cleanup and lock ordering."""

from rpkbin.wave import WaveWorkspace

from concurrent.futures import ThreadPoolExecutor
import sys
import threading
import time

import pytest

from rpkbin.flow import Flow, Parser, Result
from rpkbin.jobcore import Job, JobManager


def test_output_drain_keeps_monitoring_while_short_lines_make_progress():
    manager = WaveWorkspace(JobManager())
    lines = []

    def parse(line, ctx):
        time.sleep(0.055)
        lines.append(line)

    job = manager.add(Job(
        "short-lines",
        [sys.executable, "-u", "-c", "for i in range(64): print(i, flush=True)"],
        pty=False, ), flow=Flow("progress", parsers=[Parser("slow", parse)]))
    try:
        manager.start()
        assert manager._drain(9)
        assert lines == [str(i) for i in range(64)]
        assert job._attempt.sink.flow_session.snapshot()["complete"] is True
        assert not job.result.errors
    finally:
        manager.close()


def test_output_drain_hard_limit_marks_incomplete_while_callback_is_still_running():
    manager = WaveWorkspace(JobManager())
    entered, release = threading.Event(), threading.Event()
    lines = []

    def parse(line, ctx):
        lines.append(line)
        entered.set()
        assert release.wait(10)

    job = manager.add(Job(
        "hard-limit",
        [sys.executable, "-u", "-c", "print('first', flush=True); print('tail', flush=True)"],
        pty=False, output_drain_timeout=0.2,
        ), flow=Flow("hard-limit", parsers=[Parser("hold", parse)]))
    try:
        manager.start()
        assert entered.wait(6)
        assert manager._drain(6)
        assert not release.is_set()
        assert job._attempt.finalized
        assert "hard limit" in (job._attempt.reader_cutoff or "")
        release.set()
        assert job._attempt.reader_done.wait(10)
        snapshot = job.to_snapshot_dict(include_details=True)
        assert snapshot["flow"]["complete"] is False
        assert any(d["source"] == "output_reader" and "hard limit" in d["message"]
                   for d in snapshot["diagnostics"])
        assert job.result.verdict is Result.ERROR
        assert lines == ["first"]
    finally:
        release.set()
        manager.close()


def test_archive_enabled_resume_does_not_wait_for_live_flow(tmp_path):
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    entered, release, looked_up = (threading.Event() for _ in range(3))

    def parse(line, ctx):
        entered.set()
        assert release.wait(10)
        # Bound this probe so a lock-order regression cannot hang teardown.
        if manager._cond.acquire(timeout=1):
            try:
                if manager.get_job(job.id) is job:
                    looked_up.set()
            finally:
                manager._cond.release()

    job = manager.add(Job(
        "resume",
        [sys.executable, "-u", "-c", "print('working', flush=True)"],
        pty=False, ), flow=Flow("resume", parsers=[Parser("hold", parse)]))
    try:
        manager.start()
        assert entered.wait(6)
        manager.pause()
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                pool.submit(manager.resume).result(timeout=2)
            finally:
                release.set()
        assert looked_up.wait(2)
        assert manager._drain(6)
    finally:
        release.set()
        manager.close()


def test_concurrent_callbacks_can_take_manager_snapshots_without_nested_job_locks(tmp_path):
    manager = WaveWorkspace(JobManager(max_workers=2), archive_dir=tmp_path)
    together = threading.Barrier(2, timeout=4)
    observed = []

    def parse(line, ctx):
        together.wait()
        state = manager.snapshot(selected_job_id=ctx.source["execution"]["job_id"])
        assert state["detail"]["attempt_id"] == ctx.source["execution"]["attempt_id"]
        peer = next(job for job in jobs if job.name != line)
        with pytest.raises(RuntimeError, match="Cannot lock another Job"):
            _ = peer.result
        observed.append((line, len(state["jobs"])))

    jobs = [manager.add(Job(
        name, [sys.executable, "-u", "-c", f"print({name!r}, flush=True)"],
        pty=False, ), flow=Flow(name, parsers=[Parser("observe", parse)])) for name in ("one", "two")]
    try:
        manager.start()
        assert manager._drain(8)
        assert sorted(observed) == [("one", 2), ("two", 2)]
        assert all(not job.result.errors for job in jobs)
    finally:
        # A regression in the lock ordering must fail, not deadlock teardown.
        if manager._drain(0):
            manager.close()


def test_retry_construction_failure_releases_quota_and_saves_attempt(tmp_path, monkeypatch):
    manager = WaveWorkspace(JobManager(resources={"slot": 1}), archive_dir=tmp_path)
    job = manager.add(Job("retry", [sys.executable, "-c", "pass"],
                          resources={"slot": 1}, max_retries=1))
    attempt = job._attempt

    def execute(a):
        a.returncode = 2
        job._finish(a)

    def fail_retry(*_):
        raise SystemExit("retry factory failed")

    monkeypatch.setattr(job, "_execute", execute)
    monkeypatch.setattr(job, "_new_attempt", fail_retry)
    with manager._cond:
        manager.manager._active_workers = 1
        manager.manager._used["slot"] = 1
        manager.manager._threads.add(threading.current_thread())
        attempt.reserved = True
    try:
        with pytest.raises(SystemExit, match="retry factory failed"):
            manager.manager._worker(job, attempt)
        assert manager._drain(0)
        assert not attempt.reserved and manager.manager._used["slot"] == 0
        saved = manager.archive.attempt(job.id, attempt.id)
        assert saved["returncode"] == 2 and saved["finalized"]
    finally:
        manager.close()


def test_archive_setup_rejects_reentry_before_acquiring_start_lock(tmp_path):
    manager = WaveWorkspace(JobManager())
    job = manager.add(Job("setup", [sys.executable, "-c", "pass"]))
    try:
        with job._events_lock, pytest.raises(RuntimeError, match="handling a Job event"):
            manager.enable_archive(tmp_path)
        assert manager.archive is None
    finally:
        manager.close()
