"""Receiver registration and lifecycle, with no Wave or Flow dependency."""

import sys
import threading

import pytest

from rpkbin.jobcore import Job, JobManager
from rpkbin.jobcore.events import AttemptReceiver, ClockTick, Finished, Line, Started, StatusChanged


class Recorder(AttemptReceiver):
    def __init__(self, job, attempt, trace):
        super().__init__(job, attempt)
        self.trace = trace
        self.ready = threading.Event()
        self.ticked = threading.Event()

    def emit(self, event):
        super().emit(event)
        self.trace.append((self._attempt.id, event))
        if isinstance(event, Line) and event.text == "ready":
            self.ready.set()
        if isinstance(event, ClockTick):
            self.ticked.set()

    def drain(self, timeout):
        self.trace.append((self._attempt.id, "drain"))
        return super().drain(timeout)

    def seal(self):
        assert not self._attempt.finalized
        self.trace.append((self._attempt.id, "seal"))
        super().seal()

    def publish_state(self, *, flush=False, snapshot=None):
        if flush and self._attempt.finalized:
            self.trace.append((self._attempt.id, "terminal"))


def test_factory_binds_initial_attempt_and_rerun():
    trace = []
    manager = JobManager()
    job = Job("test", [sys.executable, "-u", "-c", "print('ready'); input(); print('done')"], pty=False)
    initial = job.attempt_id
    manager.add(job, receiver_factory=lambda job, attempt: Recorder(job, attempt, trace))
    try:
        for _ in range(2):
            receiver = job._attempt.sink
            manager.start()
            assert receiver.ready.wait(5) and receiver.ticked.wait(5)
            assert job.send("quit")
            assert manager._drain(5)
            events = [event for attempt, event in trace if attempt == job.attempt_id]
            assert isinstance(events[0], StatusChanged)
            assert isinstance(events[1], Started)
            assert any(isinstance(event, ClockTick) for event in events)
            assert [event.text for event in events if isinstance(event, Line) and event.source == "stdout"] == ["ready", "done"]
            assert events[-4:] == [Finished(True, "process_exit"), "drain", "seal", "terminal"]
            snapshot = manager.snapshot(selected_job_id=job.id)
            assert snapshot["detail"]["finalized"]
            assert snapshot["detail"]["total_lines"] == len(job.logs())
            if job.attempt_id == initial:
                assert manager.rerun(job)
                assert job.attempt_id != initial
    finally:
        manager.close()


def test_pending_attempt_can_be_adopted_once():
    manager = JobManager()
    job = manager.add(Job("queued", [sys.executable, "-c", "pass"], pty=False))
    trace = []
    manager.set_receiver_factory(lambda job, attempt: Recorder(job, attempt, trace))
    with pytest.raises(ValueError):
        manager.set_receiver_factory(Recorder)
    try:
        assert job.stop()
        assert manager._drain(5)
        assert (job.attempt_id, Finished(False, "cancelled")) in trace
        assert (job.attempt_id, "seal") in trace
        assert job.to_snapshot_dict()["finalized"]
    finally:
        manager.close()


def test_cutoff_abandons_before_finish_and_rejects_late_output(monkeypatch):
    from rpkbin.jobcore import _process
    from rpkbin.jobcore.events import SourceError

    release = threading.Event()
    reading = threading.Event()

    class PendingOutput:
        pid = 123

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def poll(self):
            return 0 if reading.is_set() else None

        def read(self):
            reading.set()
            release.wait(5)
            return b"late\n"

        def cleanup(self):
            pass

        def close(self):
            pass

    class CutoffRecorder(Recorder):
        def abandon_input(self, reason):
            self.trace.append((self._attempt.id, "abandon"))
            super().abandon_input(reason)

    monkeypatch.setattr(_process, "ProcessHandle", PendingOutput)
    trace = []
    manager = JobManager()
    job = manager.add(Job("cutoff", [sys.executable, "-c", "pass"], pty=False, output_drain_timeout=0.05),
                      receiver_factory=lambda job, attempt: CutoffRecorder(job, attempt, trace))
    try:
        manager.start()
        assert reading.wait(5)
        assert manager._drain(5) and not release.is_set()
        events = [event for _, event in trace]
        assert events[:3] == [StatusChanged("PENDING", "RUNNING"), Started(), "abandon"]
        assert isinstance(events[3], SourceError)
        assert events[4:] == [Finished(False, "source_error"), "seal", "terminal"]
        before = manager.snapshot(selected_job_id=job.id)
        release.set()
        assert job._attempt.reader_done.wait(5)
        assert manager.snapshot(selected_job_id=job.id) == before
        assert not any("late" == line for line in job.logs())
    finally:
        release.set()
        manager.close()


def test_factory_failure_keeps_pending_roster_reusable():
    manager = JobManager()
    jobs = [manager.add(Job(name, [sys.executable, "-c", "pass"])) for name in ("one", "two")]
    originals = [job._attempt.sink for job in jobs]

    def factory(job, attempt):
        if job is jobs[1]:
            raise RuntimeError("factory error")
        return Recorder(job, attempt, [])

    try:
        with pytest.raises(RuntimeError, match="factory error"):
            manager.set_receiver_factory(factory)
        assert [job._attempt.sink for job in jobs] == originals
        manager.set_receiver_factory(lambda job, attempt: Recorder(job, attempt, []))
        assert all(isinstance(job._attempt.sink, Recorder) for job in jobs)
    finally:
        manager.close()
