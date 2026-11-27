"""Behavioral regressions for shared scheduling, monitoring and attempt safety."""

from rpkbin.wave import WaveWorkspace

import sys
import time

import pytest

from rpkbin.flow import Analyzer, Automation, Binding, Flow, Parser

from rpkbin.flow import Result
from rpkbin.jobcore import Job, JobManager


def command(code, name="worker", **kwargs):
    return Job(name, [sys.executable, "-u", "-c", code], pty=False, **kwargs)


@pytest.fixture
def managers():
    instances = []

    def make(**kwargs):
        manager = WaveWorkspace(JobManager(**kwargs))
        instances.append(manager)
        return manager

    yield make
    for manager in instances:
        manager.close()


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_final_status_facts_and_finished_result_are_attempt_local(managers):
    events = []
    flow = Flow("events", automations=[Automation("observe", on_event=lambda event, ctx: events.append(event))])
    manager = managers()
    job = manager.add(command("import sys; print('ready', flush=True); sys.stdin.readline()", stop_cmd="quit"), flow=flow)
    manager.start()
    wait_for(lambda: "ready" in job.logs())
    assert job.stop()
    assert manager._drain(5)
    statuses = [e.data for e in events if e.name == "status_changed"]
    assert (statuses[-1]["old"], statuses[-1]["new"]) == ("STOPPING", "CANCELLED")
    assert events[-1].name == "finished"
    assert events[-1].data["result"] == "INCOMPLETE"
    assert all(e.data["attempt_id"] == job.attempt_id for e in events)
    assert job.result.termination[0]["actual"] == "GRACEFUL"


def test_pending_cancel_publishes_transition_before_finished(managers):
    events = []
    manager = managers()
    job = manager.add(command("pass"), flow=Flow("cancel", automations=[Automation("events", on_event=lambda event, ctx: events.append(event))]))
    assert job.stop()
    assert manager._drain(5)
    assert [e.name for e in events] == ["status_changed", "finished"]
    assert events[0].data["old"] == "PENDING" and events[0].data["new"] == "CANCELLED"
    assert job.result.verdict is Result.INCOMPLETE


def test_flow_summary_warnings_are_immutable_and_counted_once(managers):
    records = Parser("lines", lambda line, ctx: {"line": line})
    def warning(row, ctx):
        ctx.summary.issue("review", level="warning", message=row["line"])
    flow = Flow("warnings", parsers=[records], analyzers=[Analyzer("review", records=[Binding(records, warning)])])
    manager = managers()
    job = manager.add(command("print('WARN'); print('WARN')"), flow=flow)
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.NA
    assert len(job.result.warnings) == 1
    assert job.result.warnings[0]["producer"] == "review"
    with pytest.raises(TypeError):
        job.result.warnings[0]["message"] = "changed"
    assert job.to_snapshot_dict()["warning_count"] == 1


def test_management_log_does_not_reenter_flow(managers):
    records = Parser("stdout", lambda line, ctx: {"line": line})
    manager = managers()
    job = manager.add(command(
        "import sys; print('ready', flush=True); sys.stdin.readline()",
        ), flow=Flow("source", parsers=[records]))
    manager.start()
    wait_for(lambda: "ready" in job.logs())
    job.log("management-only")
    assert job.send("quit")
    assert manager._drain(5)
    assert "management-only" in job.logs()
    assert job.data["line"] == "ready"
    assert job.to_snapshot_dict(include_details=True)["flow"]["parsers"][0]["count"] == 1


def test_close_prevents_jobs_added_by_finished_automation(managers):
    rejected = []
    manager = managers()
    def finished(event, ctx):
        if event.name == "finished":
            try:
                manager.add(command("pass", name="late"))
            except RuntimeError:
                rejected.append(True)
    job = manager.add(command("import sys; print('ready', flush=True); sys.stdin.readline()", stop_cmd="quit"), flow=Flow("closing", automations=[Automation("notify", on_event=finished)]))
    manager.start()
    wait_for(lambda: "ready" in job.logs())
    manager.close()
    assert rejected == [True]
    assert len(manager.jobs) == 1


def test_snapshot_is_detached_and_history_keeps_original_priority(managers):
    manager = managers()
    job = command("print('value=1')", priority=3)
    manager.add(job, flow=Flow("nested", parsers=[Parser("values", lambda line, ctx: {"nested": {"items": [int(line.split("=")[1])]}})]))
    manager.start()
    assert manager._drain(5)
    original = job.attempt_id
    snapshot = job.to_snapshot_dict()
    snapshot["data"]["nested"]["items"].append(99)
    assert job.data["nested"]["items"] == (1,)
    manager.pause()
    manager.rerun(job)
    manager.set_priority(job.id, 9)
    assert job.to_snapshot_dict()["priority"] == 9
    assert job.to_snapshot_dict(attempt_id=original)["priority"] == 3


def test_runtime_log_retention_shrink_enlarge_and_history_isolation(managers):
    manager = managers()
    job = command(
            "import sys\n"
            "for i in range(1, 6):\n"
            "    print(f'LINE={i}', flush=True)\n"
            "print('READY', flush=True)\n"
            "sys.stdin.readline()\n"
            "for i in range(6, 9):\n"
            "    print(f'LINE={i}', flush=True)\n"
        )
    other = manager.add(command("print('OTHER=1')"))
    assert job.max_log_lines == 10000
    samples = Parser("lines", lambda line, ctx: {"last_line": int(line.split("=")[1])} if line.startswith("LINE=") else None)
    def observed(row, ctx):
        if row["last_line"] == 1:
            ctx.summary.result("PASS", "first line seen")
    manager.add(job, flow=Flow("retention", parsers=[samples], analyzers=[Analyzer("first", records=[Binding(samples, observed)])]))
    manager.validate()

    with pytest.raises(AttributeError):
        job.max_log_lines = 5
    for bad in (0, -3, True, "10"):
        with pytest.raises(ValueError):
            job.set_max_log_lines(bad)

    manager.start()
    wait_for(lambda: "READY" in job.logs())
    before = job.to_snapshot_dict(include_logs=True)
    first_evidence = before["flow"]["analyzers"][0]["evidence"]
    assert first_evidence["line"] == "LINE=1"

    evicted = job.set_max_log_lines(3, expected_attempt=job.attempt_id)
    assert evicted == before["log_count"] - 3
    assert job.max_log_lines == 3
    snap_mid = job.to_snapshot_dict(include_logs=True)
    assert snap_mid["log_records"] == before["log_records"][-3:]
    assert snap_mid["total_lines"] == before["total_lines"]
    assert snap_mid["log_count"] == 3
    assert snap_mid["flow"]["analyzers"][0]["result"] == "PASS"
    assert snap_mid["flow"]["analyzers"][0]["evidence"] == first_evidence

    # Enlarging does not restore already evicted lines, but allows more lines to be kept
    assert job.set_max_log_lines(5) == 0
    assert job.to_snapshot_dict(include_logs=True)["log_records"] == snap_mid["log_records"]

    job.send("go")
    assert manager._drain(5)

    first_attempt_id = job.attempt_id
    snap_done = job.to_snapshot_dict(include_logs=True)
    stdout_texts = [r["text"] for r in snap_done["log_records"] if r.get("stream") != "wave"]
    assert stdout_texts[-3:] == ["LINE=6", "LINE=7", "LINE=8"]
    assert [r["line_no"] for r in snap_done["log_records"]] == list(
        range(snap_done["total_lines"] - 4, snap_done["total_lines"] + 1)
    )
    assert snap_done["total_lines"] > before["total_lines"]
    assert snap_done["log_count"] == 5
    assert job.data["last_line"] == 8
    assert snap_done["result"] == "PASS"
    assert snap_done["flow"]["analyzers"][0]["evidence"] == first_evidence

    # Shrinking after completion for future attempts must not retroactively truncate completed history
    manager.rerun(job)
    wait_for(lambda: "READY" in job.logs())
    job.send("go")
    assert manager._drain(5)
    job.set_max_log_lines(2)
    hist_snap = job.to_snapshot_dict(include_logs=True, attempt_id=first_attempt_id)
    assert hist_snap["log_records"] == snap_done["log_records"]
    assert hist_snap["attempt_max_log_lines"] == 5

    # Batch manager.set_max_log_lines applies to all jobs
    manager.set_max_log_lines(4)
    assert job.max_log_lines == 4 and other.max_log_lines == 4


def test_runtime_history_retention_evicts_oldest_without_recomputing_result(managers):
    manager = managers()
    assert manager.max_history == 20
    job = command("print('OK')")
    manager.add(job, flow=Flow("ok", analyzers=[Analyzer("accept", on_finish=lambda end, ctx: ctx.summary.result("PASS", "complete", final=True))]))
    manager.start()
    assert manager._drain(5)
    a1 = job.attempt_id

    for _ in range(3):
        manager.rerun(job)
        assert manager._drain(5)

    assert len(job.history) == 3
    a2, a3 = job.history[1]["attempt_id"], job.history[2]["attempt_id"]

    for bad in (0, -1, False, "2"):
        with pytest.raises(ValueError):
            manager.set_max_history(bad)

    res = manager.update_settings(max_history=2)
    assert res["evicted_history"] == 1
    assert manager.max_history == 2
    assert [h["attempt_id"] for h in job.history] == [a2, a3]
    assert job.to_snapshot_dict()["history_truncated"] == 1
    with pytest.raises(ValueError, match="no longer retained"):
        job.to_snapshot_dict(attempt_id=a1)
    assert job.to_snapshot_dict(attempt_id=a2)["result"] == "PASS"
    assert job.to_snapshot_dict()["result"] == "PASS"
