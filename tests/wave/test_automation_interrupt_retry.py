"""Attempt-local successful interrupts suppress retries; stop/timeout stay unchanged (S2)."""

from rpkbin.wave import WaveWorkspace

import os
import signal
import sys
import time

import pytest

from rpkbin.flow import AnalysisReport, Analyzer, Automation, Binding, Flow, Parser
from rpkbin.jobcore import Job, JobManager


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

    def make(*, archive_dir=None, **kwargs):
        manager = WaveWorkspace(JobManager(**kwargs), archive_dir=archive_dir)
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


@pytest.mark.parametrize("action", ["interrupt", "send_ctrl_c", "send_signal"])
def test_successful_automation_interrupt_does_not_retry(managers, action):
    receipts = []
    parser = Parser("ready", lambda line, ctx: {"ready": True} if line == "READY" else None)
    analyzer = Analyzer("notify", records=[Binding(parser, lambda row, ctx: AnalysisReport("ready"))])

    def react(report, ctx):
        receipts.append(interrupt(ctx.actions, action))

    flow = Flow("interrupt", parsers=[parser], analyzers=[analyzer],
                automations=[Automation("interrupt_once", reports=[Binding(analyzer, react)])])
    manager = managers()
    job = manager.add(command(), flow=flow)
    manager.start()
    assert manager._drain(8)
    assert len(receipts) == 1 and receipts[0].accepted
    assert job.returncode == 23
    assert job.attempt_number == 1 and not job.history
    snapshot = job.to_snapshot_dict()
    assert snapshot["interrupt_requested"] is True
    assert snapshot["interrupt_source"] == "automation:interrupt_once"
    assert snapshot["retry_scheduled"] is False


@pytest.mark.parametrize("action", ["send_ctrl_c", "send_signal"])
def test_successful_user_interrupt_does_not_retry_and_is_archived(managers, tmp_path, action):
    manager = managers(archive_dir=tmp_path)
    job = manager.add(command())
    manager.start()
    wait_ready(job)
    assert interrupt(job, action)
    assert manager._drain(5)
    assert job.returncode == 23
    assert job.attempt_number == 1 and not job.history
    snapshot = job.to_snapshot_dict()
    assert snapshot["interrupt_requested"] is True
    assert snapshot["interrupt_source"] == "user"
    assert snapshot["retry_scheduled"] is False
    saved = manager.archive.attempt(job.id, job.attempt_id)
    assert saved["interrupt_requested"] is True
    assert saved["interrupt_source"] == "user"
    assert len(manager.archive.attempts(job.id)["rows"]) == 1
