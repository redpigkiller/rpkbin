"""Regression coverage for S5 output cleanup and lock ordering."""

from concurrent.futures import ThreadPoolExecutor
import sys
import threading

import pytest

from rpkbin.jobcore import Job, JobManager


def test_scheduler_start_failure_leaves_manager_retryable(monkeypatch):
    manager = JobManager()
    job = manager.add(Job("retry", [sys.executable, "-c", "pass"], pty=False))
    start = threading.Thread.start

    def fail_scheduler(thread):
        if thread.name == "Wave-scheduler":
            raise RuntimeError("can't start new thread")
        start(thread)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", fail_scheduler)
            with pytest.raises(RuntimeError, match="can't start new thread"):
                manager.start()
        assert not manager.started and manager.paused and manager._scheduler is None
        assert job.status.value == "PENDING"
        manager.start()
        assert manager._drain(5)
        assert job.status.value == "DONE"
    finally:
        manager.close()


def test_close_does_not_join_an_unstarted_scheduler():
    manager = JobManager()
    manager._scheduler = threading.Thread(name="Wave-scheduler")
    manager.close()
    assert manager._closed


def test_history_settings_and_priority_keep_job_then_condition_order(monkeypatch):
    manager = JobManager()
    blocked = manager.add(Job("blocked", [sys.executable, "-c", "pass"]))
    peer = manager.add(Job("peer", [sys.executable, "-c", "pass"]))
    trimming = threading.Event()
    original = manager._trim_histories

    def trim():
        trimming.set()
        return original()

    monkeypatch.setattr(manager, "_trim_histories", trim)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with blocked._events_lock:
                settings = pool.submit(manager.update_settings, max_history=1)
                assert trimming.wait(2)
                priority = pool.submit(manager.set_priority, peer, 5,
                                       expected_attempt=peer.attempt_id,
                                       expected_submission=peer.submission_id)
                assert priority.result(timeout=2).accepted
            assert settings.result(timeout=2) == {"evicted_history": 0}
        assert peer.priority == 5 and manager.max_history == 1
    finally:
        manager.close()


def test_failed_registration_does_not_freeze_unregistered_job():
    manager = JobManager()
    manager.add(Job("initial", [sys.executable, "-c", "pass"]))
    incompatible = Job("extra", [sys.executable, "-c", "pass"], resources={"gpu": 1})
    try:
        manager.start()
        with pytest.raises(ValueError, match="unknown resource gpu"):
            manager.add(incompatible)
        incompatible.resources = {}
        manager.add(incompatible)
        assert manager._drain(6)
    finally:
        manager.close()


def test_concurrent_observer_snapshot_and_diagnostic_deque_mutations():
    manager = JobManager()
    job = manager.add(Job("stress", [sys.executable, "-c", "pass"], pty=False))
    a = job._attempt
    stop = threading.Event()
    failures = []
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.00001)

    def mutate_errors():
        idx = 0
        try:
            while not stop.is_set():
                with job._events_lock:
                    job._error(a, "stress", RuntimeError(f"err-{idx}"))
                    a.diagnostics.append(
                        {"source": "stress", "level": "warning", "message": f"warn-{idx}", "time": 0.0}
                    )
                    a.termination.append(
                        {
                            "source": "user",
                            "reason": f"stop-{idx}",
                            "requested": "GRACEFUL",
                            "actual": None,
                            "time": 0.0,
                        }
                    )
                    for entry in a.termination:
                        entry["actual"] = "FORCE"
                idx += 1
        except BaseException as exc:  # pragma: no cover
            failures.append(exc)
            stop.set()

    def observe():
        try:
            for _ in range(300):
                if stop.is_set():
                    break
                row, detail = job._capture_observer(
                    selected=True,
                    attempt_id=a.id,
                    include_logs=False,
                    log_cursor=(None, None),
                )
                assert isinstance(row["errors"], list)
                assert isinstance(row["diagnostics"], list)
                assert isinstance(row["termination"], list)
                assert detail is not None
                _ = list(a.errors)
                _ = list(a.diagnostics)
                _ = list(a.termination)
        except BaseException as exc:  # pragma: no cover
            failures.append(exc)
            stop.set()

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            writers = [pool.submit(mutate_errors) for _ in range(3)]
            readers = [pool.submit(observe) for _ in range(3)]
            for reader in readers:
                reader.result(timeout=10)
            stop.set()
            for writer in writers:
                writer.result(timeout=10)
        assert not failures
    finally:
        stop.set()
        sys.setswitchinterval(old_interval)
        manager.close()
