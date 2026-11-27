"""Attempt-local successful interrupts suppress retries; stop/timeout stay unchanged (S2)."""

import os
import signal
import sys
import time

import pytest

from rpkbin.jobcore import Job, JobManager
from rpkbin.jobcore._process import ProcessHandle


SIGNAL_NAME = "SIGBREAK" if os.name == "nt" else "SIGINT"
SIGNAL = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
SIGNAL_CODE = (
    "import signal,sys,time; "
    f"signal.signal(signal.{SIGNAL_NAME}, lambda *args: sys.exit(23)); "
    "print('READY', flush=True)\nwhile True: time.sleep(0.01)"
)


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


def command(code=SIGNAL_CODE, **kwargs):
    return Job("interrupt", [sys.executable, "-u", "-c", code], pty=False, max_retries=1, **kwargs)


def wait_ready(job):
    deadline = time.monotonic() + 5
    while "READY" not in job.logs():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def interrupt(target, action):
    return getattr(target, action)(SIGNAL) if action == "send_signal" else getattr(target, action)()


@pytest.mark.parametrize("action", ["send_ctrl_c", "send_signal"])
def test_failed_interrupt_does_not_suppress_crash_retry(managers, monkeypatch, tmp_path, action):
    counter = tmp_path / "attempts"
    code = (
        "from pathlib import Path; import sys; "
        f"p=Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; "
        "p.write_text(str(n)); print('READY', flush=True); "
        "sys.stdin.readline() if n == 1 else None; sys.exit(23 if n == 1 else 0)"
    )
    manager = managers()
    job = manager.add(command(code))
    manager.start()
    wait_ready(job)

    def failed(*args):
        raise OSError("signal delivery failed")

    transport_action = "interrupt" if action == "send_ctrl_c" else "send_signal"
    with monkeypatch.context() as transport:
        transport.setattr(ProcessHandle, transport_action, failed)
        assert interrupt(job, action).status == "failed"
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    assert job.to_snapshot_dict()["interrupt_source"] is None
    assert job.send("exit")
    assert manager._drain(5)
    assert job.attempt_number == 2 and job.returncode == 0
    assert counter.read_text() == "2"
    assert job.history[0]["returncode"] == 23
    assert job.history[0]["interrupt_requested"] is False
    assert job.history[0]["retry_scheduled"] is True


@pytest.mark.parametrize("action", ["send_ctrl_c", "send_signal"])
def test_rejected_interrupt_does_not_mark_attempt(managers, action):
    manager = managers()
    job = manager.add(command("pass"))
    assert interrupt(job, action).status == "rejected"
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    manager.start()
    assert manager._drain(5)
    assert interrupt(job, action).status == "rejected"
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    assert job.to_snapshot_dict()["interrupt_source"] is None


def test_rerun_has_fresh_interrupt_state_and_stale_control_cannot_mark_it(managers):
    manager = managers()
    job = manager.add(command())
    manager.start()
    wait_ready(job)
    original = job.attempt_id
    assert job.send_ctrl_c()
    assert manager._drain(5)
    manager.pause()
    manager.rerun(job)
    assert job.history[0]["interrupt_requested"] is True
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    assert job.to_snapshot_dict()["interrupt_source"] is None
    receipt = job.send_ctrl_c(expected_attempt=original)
    assert receipt.status == "rejected" and "stale" in receipt.reason
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    assert job.stop()


def test_timeout_does_not_retry(managers):
    manager = managers()
    job = manager.add(command(timeout=0.5, stop_ctrl_c=True, stop_timeout=1))
    manager.start()
    assert manager._drain(6)
    assert job.returncode == 23
    assert job._attempt.timed_out and job._attempt.stop_requested
    assert job.attempt_number == 1 and not job.history
    assert job.to_snapshot_dict()["retry_scheduled"] is False
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    assert job.result.termination[0]["source"] == "timeout"


def test_user_stop_does_not_retry_or_become_an_interrupt_request(managers):
    manager = managers()
    job = manager.add(command(stop_ctrl_c=True, stop_timeout=1))
    manager.start()
    wait_ready(job)
    assert job.stop()
    assert manager._drain(5)
    assert job.returncode == 23
    assert job.attempt_number == 1 and not job.history
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    assert job.to_snapshot_dict()["retry_scheduled"] is False


def test_process_crash_still_retries(managers):
    manager = managers()
    job = manager.add(command("raise SystemExit(23)"))
    manager.start()
    assert manager._drain(5)
    assert job.returncode == 23 and job.attempt_number == 2
    assert len(job.history) == 1
    assert job.history[0]["retry_scheduled"] is True
    assert job.history[0]["interrupt_requested"] is False
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    assert job.to_snapshot_dict()["retry_scheduled"] is False


def test_non_interrupting_signals_keep_retries_sigtstp_wakes_on_stop_and_force_skips_timeout_log(
    managers, monkeypatch, tmp_path
):
    from rpkbin.jobcore import _job_control

    FAKE_TSTP = 9001
    FAKE_CONT = 9002
    FAKE_USR1 = 9003
    monkeypatch.setattr(_job_control, "_SIGTSTP", FAKE_TSTP)
    monkeypatch.setattr(_job_control, "_SIGCONT", FAKE_CONT)
    monkeypatch.setattr(
        _job_control,
        "_NON_INTERRUPTING_SIGNALS",
        frozenset((FAKE_TSTP, FAKE_CONT, FAKE_USR1)),
    )

    # 1. Non-interrupting signal (SIGUSR1/SIGTSTP/SIGCONT) does not suppress max_retries.
    counter = tmp_path / "retry_after_usr1"
    code = (
        "from pathlib import Path; import sys; "
        f"p=Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; "
        "p.write_text(str(n)); print('READY', flush=True); "
        "sys.stdin.readline() if n == 1 else None; sys.exit(23 if n == 1 else 0)"
    )
    manager = managers()
    job = manager.add(command(code))
    sent = []
    monkeypatch.setattr(ProcessHandle, "send_signal", lambda self, s: sent.append(s))
    manager.start()
    wait_ready(job)
    assert job.send_signal(FAKE_USR1).accepted
    assert sent == [FAKE_USR1]
    assert job.to_snapshot_dict()["interrupt_requested"] is False
    assert job.send("go").accepted
    assert manager._drain(5)
    assert job.attempt_number == 2 and job.returncode == 0

    # 2. SIGTSTP marks suspended, SIGCONT clears it, stop() wakes with SIGCONT first,
    #    and manual kill() suppresses duplicate "Stop timeout reached" escalation.
    manager2 = managers()
    job2 = manager2.add(
        Job(
            "suspendable",
            [
                sys.executable,
                "-u",
                "-c",
                "import sys; print('READY', flush=True); sys.stdin.readline()",
            ],
            pty=False,
            stop_timeout=0.15,
        )
    )
    events = []
    orig_term = ProcessHandle.terminate
    orig_kill = ProcessHandle.kill
    monkeypatch.setattr(
        ProcessHandle,
        "send_signal",
        lambda self, s: events.append(("signal", s)),
    )
    monkeypatch.setattr(
        ProcessHandle,
        "terminate",
        lambda self: (events.append(("terminate", None)), orig_term(self))[-1],
    )
    monkeypatch.setattr(
        ProcessHandle,
        "kill",
        lambda self: (events.append(("kill", None)), orig_kill(self))[-1],
    )

    manager2.start()
    wait_ready(job2)
    assert job2.send_signal(FAKE_TSTP).accepted
    assert job2.to_snapshot_dict()["suspended"] is True
    assert job2.to_snapshot_dict()["interrupt_requested"] is False
    assert job2.send_signal(FAKE_CONT).accepted
    assert job2.to_snapshot_dict()["suspended"] is False

    # Re-suspend then call stop(), immediately followed by manual force_stop (kill).
    assert job2.send_signal(FAKE_TSTP).accepted
    assert job2.stop().accepted
    assert job2.kill().accepted
    assert manager2._drain(5)
    assert job2.to_snapshot_dict()["suspended"] is False
    assert ("signal", FAKE_CONT) in events
    kill_count = sum(1 for kind, _ in events if kind == "kill")
    # Manual kill() + ProcessHandle.cleanup() kill may run; timeout thread must NOT escalate.
    assert kill_count >= 1
    assert "Stop timeout reached" not in job2.logs()
