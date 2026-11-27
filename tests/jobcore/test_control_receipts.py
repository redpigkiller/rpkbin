"""Unified control receipts and control-channel secret redaction (S3)."""

import os
import signal
import sys
import time
from dataclasses import asdict

import pytest

from rpkbin.jobcore import ControlReceipt, Job, JobManager, JobStatus
from rpkbin.jobcore._process import ProcessHandle


@pytest.fixture
def managers():
    instances = []
    def make(**kwargs):
        manager = JobManager(**kwargs)
        instances.append(manager)
        return manager
    yield make
    for manager in instances:
        manager.close()


def command(code="pass", **kwargs):
    return Job("control", [sys.executable, "-u", "-c", code], pty=False, **kwargs)


def invoke(job, action, **kwargs):
    if action == "send":
        return job.send("input", **kwargs)
    if action == "send_signal":
        return job.send_signal(signal.SIGTERM, **kwargs)
    return getattr(job, action)(**kwargs)


def ready(job):
    deadline = time.monotonic() + 5
    while "READY" not in job.logs():
        assert time.monotonic() < deadline
        time.sleep(.01)


@pytest.mark.parametrize("status,accepted", [("accepted", True), ("rejected", False), ("failed", False)])
def test_receipt_bool_means_accepted(status, accepted):
    receipt = ControlReceipt(status, reason="specific reason")
    assert receipt.accepted is accepted
    assert bool(receipt) is accepted
    assert asdict(receipt) == {"status": status, "reason": "specific reason"}


@pytest.mark.parametrize("action", ["send", "send_signal", "send_ctrl_c"])
def test_controls_without_process_return_rejected_receipt(managers, action):
    job = managers().add(command())
    receipt = invoke(job, action)
    assert isinstance(receipt, ControlReceipt)
    assert receipt.status == "rejected" and not receipt
    assert "no process" in receipt.reason.lower()


@pytest.mark.parametrize("action", ["send", "send_signal", "send_ctrl_c", "stop", "kill"])
def test_stale_control_is_rejected_without_mutating_attempt(managers, action):
    job = managers().add(command())
    receipt = invoke(job, action, expected_attempt="old-attempt")
    assert receipt.status == "rejected" and "stale attempt" in receipt.reason.lower()
    assert job.status is JobStatus.PENDING
    assert not job._attempt.stop_requested and not job._attempt.interrupt_requested


@pytest.mark.parametrize("action", ["stop", "kill"])
def test_pending_stop_is_accepted_and_finished_stop_is_rejected(managers, action):
    job = managers().add(command())
    receipt = invoke(job, action)
    assert receipt.status == "accepted" and receipt
    assert "cancel" in receipt.reason.lower()
    assert job.status is JobStatus.CANCELLED
    second = invoke(job, action)
    assert second.status == "rejected" and "finished" in second.reason.lower()


@pytest.mark.parametrize("action", ["send", "send_signal", "send_ctrl_c", "stop", "kill"])
def test_exited_process_is_rejected_before_attempt_finalization(managers, monkeypatch, action):
    class Exited:
        def poll(self):
            return 7
    job = managers().add(command())
    with monkeypatch.context() as state:
        state.setattr(job._attempt, "process", Exited())
        receipt = invoke(job, action)
    assert receipt.status == "rejected" and "exited" in receipt.reason.lower()


@pytest.mark.parametrize("action,transport", [("send", "write"), ("send_signal", "send_signal"),
                                              ("send_ctrl_c", "interrupt"), ("kill", "kill")])
def test_transport_failures_return_failed_receipts(managers, monkeypatch, action, transport):
    manager = managers()
    job = manager.add(command("import sys; print('READY',flush=True); sys.stdin.readline()"))
    manager.start()
    ready(job)
    def broken(*args):
        raise OSError("transport unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(ProcessHandle, transport, broken)
        receipt = invoke(job, action)
    assert receipt.status == "failed" and not receipt
    assert "transport unavailable" in receipt.reason
    assert job.send("exit")
    assert manager._drain(5)


def test_manager_control_returns_receipts_for_unknown_stale_and_success(managers):
    manager = managers()
    job = manager.add(command())
    def control(job_id=job.id, action="send", attempt=None, submission=None):
        return manager.control(job_id, action, expected_attempt=attempt or job.attempt_id,
                               expected_submission=submission or job.submission_id, text="input")
    assert control("missing").status == "rejected"
    assert "unknown" in control("missing").reason.lower()
    assert "stale submission" in control(submission="old").reason.lower()
    assert "stale attempt" in control(attempt="old").reason.lower()
    assert "unknown" in control(action="unknown").reason.lower()
    assert "no process" in control().reason.lower()
    assert control(action="stop").accepted


def test_failed_stop_worker_start_returns_receipt_without_unjoinable_thread(managers, monkeypatch):
    import threading
    manager = managers()
    job = manager.add(command("import sys; print('READY',flush=True); sys.stdin.readline()"))
    manager.start()
    ready(job)
    original_start = threading.Thread.start
    def fail_stop_worker(thread):
        if thread.name.startswith("Wave-stop-"):
            raise RuntimeError("stop worker unavailable")
        return original_start(thread)
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", fail_stop_worker)
        receipt = job.stop()
    assert receipt.status == "failed" and "stop worker unavailable" in receipt.reason
    assert job._attempt.stop_started is False
    assert job._attempt.stop_thread is None
    assert job.send("exit")
    assert manager._drain(5)
    assert [e["source"] for e in job.result.errors] == ["stop"]


@pytest.mark.parametrize("action", ["send", "send_signal", "send_ctrl_c", "stop", "kill"])
def test_direct_job_controls_use_attempt_capabilities(monkeypatch, action):
    from rpkbin.jobcore.events import Capabilities

    job = command()
    # A process source with an unsupported control still needs enforcement.
    class Handle:
        def __getattr__(self, name):
            pytest.fail(f"unsupported control touched transport: {name}")

    monkeypatch.setattr(job._attempt, "capabilities", Capabilities(has_process=True))
    monkeypatch.setattr(job._attempt, "process", Handle())
    snapshot = job.to_snapshot_dict()
    assert {name for name, allowed in snapshot["capabilities"].items() if allowed} == {"has_process"}
    reply = invoke(job, action)
    assert isinstance(reply, ControlReceipt) and reply.status == "rejected" and reply.reason
    assert not job._attempt.termination and not job._attempt.interrupt_requested


def test_manager_scheduler_controls_report_stale_and_inapplicable_states(managers):
    manager = managers()
    job = manager.add(command())
    original = job.attempt_id
    assert manager.rerun(job).status == "rejected"
    assert manager.set_priority(job, 9, expected_attempt="stale").status == "rejected"
    assert job.priority == 0
    assert manager.set_priority(job, 9).accepted
    assert job.stop().accepted
    assert manager.set_priority(job, 12).status == "rejected" and job.priority == 9
    assert manager.rerun(job).accepted and job.attempt_id != original
    assert manager.rerun(job, expected_attempt=original).reason.startswith("stale attempt")


@pytest.mark.parametrize("action", ["send_ctrl_c", "send_signal"])
@pytest.mark.parametrize("kind", ["lookup", "oserror_after_exit"])
def test_signal_race_with_exiting_process_is_rejected_without_error(
    managers, monkeypatch, action, kind
):
    from rpkbin.jobcore import ProcessVerdict

    manager = managers()
    job = manager.add(command("import sys; print('READY',flush=True); sys.stdin.readline()"))
    manager.start()
    ready(job)
    proc = job._attempt.process
    exited = False
    real_poll = proc.poll

    def fake_poll():
        return 0 if exited else real_poll()

    def race_on_signal(*args):
        nonlocal exited
        if kind == "lookup":
            raise ProcessLookupError("process just exited")
        exited = True
        raise OSError("signal after exit")

    transport = "interrupt" if action == "send_ctrl_c" else "send_signal"
    with monkeypatch.context() as patch:
        patch.setattr(proc, "poll", fake_poll)
        patch.setattr(proc, transport, race_on_signal)
        receipt = invoke(job, action)
    assert receipt == ControlReceipt("rejected", "Process has already exited")
    assert not job._attempt.interrupt_requested
    assert job.send("done")
    assert manager._drain(5)
    assert job.status is JobStatus.DONE
    assert job.result.verdict is not ProcessVerdict.ERROR
    assert not job.result.errors


def test_send_forwards_secret_flag_and_releases_events_lock_during_write(managers, monkeypatch):
    import threading

    manager = managers()
    job = manager.add(
        command(
            "import sys; print('PROMPT>', end='', flush=True); sys.stdin.readline()",
            prompts=["PROMPT>"],
        )
    )
    manager.start()
    deadline = time.monotonic() + 5
    while job.status is not JobStatus.WAITING_INPUT:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    entered_write = threading.Event()
    release_write = threading.Event()
    lock_free_during_write = []
    secret_args = []
    real_write = ProcessHandle.write

    def slow_write(handle, text, *, secret=False):
        secret_args.append((text, secret))
        lock_free_during_write.append(job._events_lock.held() is None)
        entered_write.set()
        assert release_write.wait(3)
        return real_write(handle, text, secret=secret)

    monkeypatch.setattr(ProcessHandle, "write", slow_write)
    receipt_box = []
    sender = threading.Thread(
        target=lambda: receipt_box.append(job.send("top-secret", secret=True)),
        daemon=True,
    )
    sender.start()
    assert entered_write.wait(3)
    # Another thread must be able to acquire _events_lock while proc.write blocks.
    acquired = job._events_lock.acquire(timeout=0.5)
    assert acquired
    job._events_lock.release()
    release_write.set()
    sender.join(3)
    assert not sender.is_alive()
    assert receipt_box == [ControlReceipt("accepted", "Input sent: ***")]
    assert lock_free_during_write == [True]
    assert secret_args == [("top-secret\n", True)]
    assert manager._drain(5)


@pytest.mark.parametrize("outcome", ["broken_pipe", "ok"])
def test_send_finishing_while_write_is_in_flight_does_not_mutate_finished_attempt(
    managers, outcome
):
    from rpkbin.jobcore import ProcessVerdict

    manager = managers()
    job = manager.add(command("pass", prompts=["PROMPT>"]))
    manager.validate()
    attempt = job._attempt
    attempt.status = JobStatus.WAITING_INPUT
    attempt.reserved = True

    class FinishingProcess:
        def poll(self):
            return None if attempt.ended is None else 0

        def write(self, text):
            # Simulate _finish(a) running on the worker thread while _events_lock
            # is released for proc.write(), before a.finalized is flipped.
            attempt.returncode = 0
            job._finish(attempt)
            assert attempt.ended is not None and not attempt.finalized
            if outcome == "broken_pipe":
                raise BrokenPipeError("Process exited before all input was written")

    attempt.process = FinishingProcess()
    receipt = job.send("late-input")
    attempt.process = None
    if outcome == "broken_pipe":
        assert receipt == ControlReceipt("rejected", "Process has already exited")
    else:
        assert receipt == ControlReceipt("accepted", "Input sent")
    assert attempt.status is JobStatus.DONE
    assert job.result.verdict is not ProcessVerdict.ERROR
    assert not job.result.errors
    assert "Input sent: late-input" not in job.logs()


def test_invalid_or_unsupported_signal_is_rejected_without_error_verdict(managers):
    from rpkbin.jobcore import ProcessVerdict

    manager = managers()
    job = manager.add(command("import sys; print('READY', flush=True); sys.stdin.readline()"))
    manager.start()
    ready(job)

    assert job.send_signal(True) == ControlReceipt("rejected", "Signal must be an integer")
    assert job.send_signal(None) == ControlReceipt("rejected", "Signal must be an integer")
    assert job.send_signal(-1) == ControlReceipt("rejected", "Signal must be an integer")
    if os.name == "nt":
        receipt = job.send_signal(signal.SIGTERM)
        assert receipt.status == "rejected"
        assert "CTRL_BREAK_EVENT" in receipt.reason
    assert not job._attempt.interrupt_requested
    assert job.send("exit")
    assert manager._drain(5)
    assert job.status is JobStatus.DONE
    assert job.result.verdict is ProcessVerdict.NA
    assert not job.result.errors


def test_unencodable_send_is_rejected_without_error_or_secret_leak(managers):
    from rpkbin.jobcore import ProcessVerdict

    manager = managers()
    job = manager.add(
        command("import sys; print('READY', flush=True); sys.stdin.readline()", encoding="ascii")
    )
    manager.start()
    ready(job)
    plain = job.send("café")
    assert plain.status == "rejected"
    secret_val = "secret-café"
    secret_res = job.send(secret_val, secret=True)
    assert secret_res.status == "rejected"
    assert secret_val not in secret_res.reason
    assert "café" not in secret_res.reason
    assert job.send("exit")
    assert manager._drain(5)
    assert job.status is JobStatus.DONE
    assert job.result.verdict is ProcessVerdict.NA
    assert not job.result.errors
