"""Automation with an actual process and archive."""

from rpkbin.wave import WaveWorkspace

import sys
import threading
import time

import pytest

from rpkbin.flow import ActionOutcome, AnalysisReport, Analyzer, Automation, Binding, Flow, Parser
from rpkbin.jobcore import Job, JobManager


def definition(callback=None, *, event=None, enabled=True):
    samples = Parser("samples", lambda line, ctx: {"x": int(line)} if line.isdigit() else None)
    limits = Analyzer("limits", records=[Binding(samples, lambda row, ctx: AnalysisReport("upper", result="HIGH" if row["x"] > 10 else "OK", values=dict(row)))])
    return Flow("control", parsers=[samples], analyzers=[limits],
                automations=[Automation("react", reports=[Binding(limits, callback)] if callback else [], on_event=event, enabled=enabled)])


def test_real_child_control_archive_and_rerun_identity(tmp_path):
    old_controls, native = [], []

    def react(report, ctx):
        old_controls.append(ctx.actions)
        if report.result == "HIGH":
            outcome = ctx.actions.stop(reason="explicit decision")
            assert isinstance(outcome, ActionOutcome) and outcome.accepted

    def event(value, ctx):
        native.append((value.name, value.data["attempt_id"]))

    mgr = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    try:
        job = mgr.add(Job("worker", [sys.executable, "-u", "-c", "import time; print(12); time.sleep(3)"],
                          pty=False), flow=definition(react, event=event))
        mgr.start()
        assert mgr._drain(8)
        first = job.attempt_id
        history = mgr.archive.automation_history(job.id, first)
        assert len(history["rows"]) == 1
        action = history["rows"][0]
        assert action["data"]["status"] == "accepted"
        assert action["data"]["kwargs"]["source"] == "automation:react"
        assert job.result.termination[0]["source"] == "automation:react"
        assert action["evidence"]["line_no"] == 1
        assert [e[0] for e in native if e[0] in ("started", "finished")] == ["started", "finished"]
        assert any(e[0] == "status_changed" for e in native)
        mgr.rerun(job.id)
        assert mgr._drain(8)
        assert job.attempt_id != first
        assert len(mgr.archive.automation_history(job.id, job.attempt_id)["rows"]) == 1
        with pytest.raises(RuntimeError, match="during its callback"):
            old_controls[0].stop()
    finally:
        mgr.close()



def test_real_slow_callback_keeps_collecting_raw_output(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def slow(report, ctx):
        if not ctx.state.get("blocked"):
            ctx.state["blocked"] = True
            entered.set()
            release.wait(4)

    mgr = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    try:
        job = mgr.add(Job("collector", [sys.executable, "-u", "-c", "import time; print(12); time.sleep(.1); print('RAW_END'); time.sleep(.3)"],
                          pty=False), flow=definition(slow))
        mgr.start()
        assert entered.wait(2)
        deadline = time.monotonic() + 2
        while "RAW_END" not in job.logs():
            assert time.monotonic() < deadline
            time.sleep(.01)
        release.set()
        assert mgr._drain(5)
    finally:
        release.set()
        mgr.close()



def test_runner_rejects_blocking_operations_from_composed_callbacks():
    rejected = []
    manager = WaveWorkspace(JobManager())

    def check(stage):
        with pytest.raises(RuntimeError, match="cannot close the runner inside a callback"):
            manager.close()
        with pytest.raises(RuntimeError, match="cannot block the runner from a callback"):
            manager._drain(0)
        rejected.append(stage)

    def parse(line, ctx):
        check("parser")
        return {"line": line}

    def analyze(record, ctx):
        check("analyzer")

    def react(event, ctx):
        if event.name == "finished":
            check("automation")

    parser = Parser("lines", parse)
    flow = Flow("guard", parsers=[parser],
                analyzers=[Analyzer("check", records=[Binding(parser, analyze)])],
                automations=[Automation("observe", on_event=react)])
    try:
        manager.add(Job("probe", [sys.executable, "-u", "-c", "print('probe')"], pty=False), flow=flow)
        manager.start()
        assert manager._drain(5)
        assert rejected == ["parser", "analyzer", "automation"]
    finally:
        manager.close()
