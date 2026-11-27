"""Attempt ordering at the process receiver boundary."""

import sys
import threading

from rpkbin.jobcore import Job, JobManager
from rpkbin.jobcore.events import ClockTick, Finished, Line, Started, StatusChanged


def test_receiver_order_normal_exit_and_observer(monkeypatch):
    seen = []
    ready = threading.Event()
    manager = JobManager()
    job = manager.add(Job("probe", [sys.executable, "-u", "-c",
                                  "print('ready'); input(); print('done')"], pty=False))
    attempt = job._attempt
    sink = attempt.sink
    emit, seal = sink.emit, sink.seal

    def observe(event):
        emit(event)
        if isinstance(event, Line) and event.source == "stdout":
            seen.append(event.text)
            if event.text == "ready":
                ready.set()
        elif isinstance(event, ClockTick):
            seen.append("tick")
        elif isinstance(event, (StatusChanged, Started, Finished)):
            seen.append(type(event).__name__)

    def sealing():
        assert not attempt.finalized
        seal()
        seen.append("seal")

    monkeypatch.setattr(sink, "emit", observe)
    monkeypatch.setattr(sink, "seal", sealing)
    try:
        manager.start()
        assert ready.wait(5)
        assert job.send("quit")
        assert manager._drain(5)
        ordered = [event for event in seen if event != "tick"]
        assert ordered == ["StatusChanged", "Started", "ready", "done", "Finished", "seal"]
        assert job.to_snapshot_dict(include_logs=True)["finalized"]
        assert job.logs()[0] == "ready" and job.logs()[-1] == "done"
    finally:
        manager.close()


def test_pending_cancel_delivers_finished_before_seal(monkeypatch):
    manager = JobManager()
    job = manager.add(Job("pending", [sys.executable, "-c", "pass"], pty=False))
    attempt = job._attempt
    sink = attempt.sink
    emit, seal = sink.emit, sink.seal
    seen = []

    def observe(event):
        seen.append(event)
        emit(event)

    def sealing():
        assert not attempt.finalized
        seen.append("seal")
        seal()

    monkeypatch.setattr(sink, "emit", observe)
    monkeypatch.setattr(sink, "seal", sealing)
    try:
        assert job.stop()
        assert manager._drain(5)
        assert seen == [Finished(False, "cancelled"), "seal"]
        assert job.to_snapshot_dict()["finalized"]
        assert job.logs() == []
    finally:
        manager.close()
