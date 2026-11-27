"""End-to-end Flow coverage across child output, attempts, and archive storage."""

from rpkbin.wave import WaveWorkspace

import sys
import sqlite3
import time

import pytest

from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, Parser
from rpkbin.jobcore import Job, JobManager, Report as JobReport
from rpkbin.wave import RunArchive
from rpkbin.wave.columns import configure_columns
from rpkbin.wave.web import WaveServer


def command(code, *, name="flow-worker"):
    return Job(name, [sys.executable, "-u", "-c", code], pty=False)


@pytest.fixture
def manager(tmp_path):
    value = WaveWorkspace(JobManager(max_history=1), archive_dir=tmp_path / "archives")
    yield value
    value.close()


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def flow_detail(job):
    return job.to_snapshot_dict(include_details=True)["flow"]


def test_real_child_keeps_equal_publications_and_latest_value(manager):
    large = 9007199254740993
    flow = Flow(
        "samples",
        parsers=(Parser("sample", lambda line, ctx: {"value": int(line[6:])}
                        if line.startswith("VALUE=") else None),),
    )
    job = manager.add(command(f"print('VALUE={large}'); print('VALUE={large}')"), flow=flow)

    manager.start()
    assert manager._drain(5)

    page = manager.archive.flow_page(job.id, job.attempt_id, stage="parser", producer="sample",
                                     kind="record", limit=10)
    assert [row["data"] for row in page["rows"]] == [{"value": large}, {"value": large}]
    detail = flow_detail(job)
    assert detail["parsers"][0]["count"] == 2
    assert detail["parsers"][0]["latest"] == {"value": large}
    assert job.data["sample.value"] == large
    assert job.data["value"] == large


def test_finish_result_is_not_pass_until_child_completes(manager):
    def finish(end, ctx):
        ctx.summary.result("PASS", "finished", final=True)

    flow = Flow(
        "finish-check",
        analyzers=(Analyzer("completion", on_finish=finish),),
    )
    job = manager.add(command("import time; print('READY', flush=True); time.sleep(.35)"), flow=flow)

    manager.start()
    wait_for(lambda: "READY" in job.logs())
    assert flow_detail(job)["result"] is None
    assert manager._drain(5)
    assert flow_detail(job)["result"] == "PASS"
    assert job.result.verdict.value == "PASS"


def test_analyzer_failure_is_visible_before_process_exit(manager):
    def analyze(row, ctx):
        if row["text"] == "BAD":
            ctx.summary.result("FAIL", "bad input", final=True)

    source = Parser("line", lambda text, ctx: {"text": text})
    flow = Flow(
        "early-failure",
        parsers=(source,),
        analyzers=(Analyzer("guard", records=[Binding(source, analyze)]),),
    )
    job = manager.add(command("import time; print('BAD', flush=True); time.sleep(.4)"), flow=flow)

    manager.start()
    wait_for(lambda: any(line == "BAD" for line in job.logs()))
    detail = flow_detail(job)
    assert detail["result"] == "FAIL"
    assert detail["analyzers"][0]["reason"] == "bad input"
    assert job.status.value == "RUNNING"
    assert manager._drain(5)


def test_rerun_gets_fresh_parser_state_and_distinct_archive_scope(manager):
    def parse(line, ctx):
        ctx.state["count"] = ctx.state.get("count", 0) + 1
        return {"count": ctx.state["count"]} if line == "SAMPLE" else None

    flow = Flow("state-reset", parsers=(Parser("counter", parse),))
    job = manager.add(command("print('SAMPLE'); print('SAMPLE')"), flow=flow)
    manager.start()
    assert manager._drain(5)
    first_attempt = job.attempt_id

    manager.rerun(job.id)
    assert manager._drain(5)
    second_attempt = job.attempt_id
    assert second_attempt != first_attempt

    first = manager.archive.flow_page(job.id, first_attempt, producer="counter", limit=10)
    second = manager.archive.flow_page(job.id, second_attempt, producer="counter", limit=10)
    assert [row["data"]["count"] for row in first["rows"]] == [1, 2]
    assert [row["data"]["count"] for row in second["rows"]] == [1, 2]


def test_multiple_equal_records_from_one_line_have_independent_archive_identity(manager):
    flow = Flow("batch", parsers=[Parser("pair", lambda line, ctx: [{"value": 1}, {"value": 1}])])
    job = manager.add(command("print('sample')"), flow=flow)
    manager.start()
    assert manager._drain(5)
    rows = manager.archive.flow_page(job.id, job.attempt_id, producer="pair")["rows"]
    assert [row["seq"] for row in rows] == [1, 2]
    assert [row["evidence"]["line_no"] for row in rows] == [1, 1]


def test_dynamic_columns_and_colliding_aliases_are_explicit(tmp_path):
    flow = Flow("collisions", parsers=[Parser("left", lambda line, ctx: {"value": 1}),
                                      Parser("right", lambda line, ctx: {"value": 2})])
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    try:
        job = manager.add(command("print('sample')"), flow=flow)
        configure_columns(manager, ["left.value", "right.value"])
        server = WaveServer(manager)
        manager.start()
        assert manager._drain(5)
        assert job.data["left.value"] == 1 and job.data["right.value"] == 2
        assert "value" not in job.data
        configure_columns(manager, ["future_dynamic_field"])
        assert server._state()["columns"] == ["future_dynamic_field"]
    finally:
        manager.close()


def test_job_exposes_only_flow_monitoring():
    manager = WaveWorkspace(JobManager())
    try:
        job = manager.add(command("print('ok')"), flow=Flow("pure"))
        assert not any(hasattr(job, name) for name in ('track', 'check', 'on', 'events', 'context'))
        manager.start()
        assert manager._drain(5)
        assert job.result.verdict.value == 'N/A'
    finally:
        manager.close()


def test_previous_archive_schema_is_rejected(tmp_path):
    path = tmp_path / "old.sqlite3"
    archive = RunArchive(path, create=True)
    archive.close()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE metadata SET value='1' WHERE key='schema_version'")
    with pytest.raises(ValueError, match="schema"):
        RunArchive(path)


def test_job_reason_prioritizes_failed_result_over_warning(manager):
    def warn(row, ctx):
        ctx.summary.issue("saturation", level="warning", message="temporary saturation")

    def fail(row, ctx):
        ctx.summary.result("FAIL", "did not converge", final=True)

    source = Parser("sample", lambda line, ctx: {"value": 1})
    flow = Flow("failure-reason", parsers=[source], analyzers=[Analyzer("messages", records=[Binding(source, warn)]), Analyzer("verification", records=[Binding(source, fail)])])
    job = manager.add(command("print('sample')"), flow=flow)
    manager.start()
    assert manager._drain(5)
    summary = job.to_snapshot_dict()["flow_summary"]
    assert summary["reason"] == "did not converge"
    assert summary["producer"] == "verification"
    assert summary["warning_count"] == 1
    assert summary["evidence"]["line_no"] == 1


def test_analysis_report_is_distinct_from_job_report():
    assert JobReport is not AnalysisReport
