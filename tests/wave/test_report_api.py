"""Analysis Reports remain scoped and read-only through archive HTTP views."""

from rpkbin.wave import WaveWorkspace

from contextlib import contextmanager
import json
import sys
import urllib.parse
import urllib.request

from rpkbin.flow import AnalysisReport, Analyzer, Automation, Binding, Flow, Parser
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave.web import WaveServer


LARGE = 9007199254740993


@contextmanager
def report_workspace(tmp_path):
    trigger_calls = []

    def analyze(record, ctx):
        value = record["value"]
        if value == LARGE:
            return AnalysisReport(
                "quality", result="review-needed", reason="first sample",
                values={"exact": value, "carry": "from first"},
            )
        quality = AnalysisReport(
            "quality", result="accepted-with-note", reason="second sample",
            values={"exact": value},
        )
        if value == LARGE + 1:
            return [quality, AnalysisReport(
                "summary", result="arbitrary display text",
                values={"samples": 2},
            )]
        return quality

    samples = Parser("sample", lambda line, ctx: {"value": int(line[6:])} if line.startswith("VALUE=") else None)
    evaluate = Analyzer("evaluate", records=[Binding(samples, analyze)])
    flow = Flow(
        "report-api",
        parsers=(samples,),
        analyzers=(evaluate,),
        automations=(Automation(
            "disabled", reports=[Binding(evaluate, lambda publication, ctx: trigger_calls.append(publication))], enabled=False,
        ),),
    )
    manager = WaveWorkspace(JobManager(max_history=1), archive_dir=tmp_path / "archives")
    job_a = manager.add(Job(
        "job-a", [sys.executable, "-u", "-c",
                   f"print('VALUE={LARGE}'); print('VALUE={LARGE + 1}')"],
        pty=False, ), flow=flow)
    job_b = manager.add(Job(
        "job-b", [sys.executable, "-u", "-c", "print('VALUE=9')"],
        pty=False, ), flow=flow)
    manager.start()
    assert manager._drain(8)
    first_attempt = job_a.attempt_id
    manager.rerun(job_a.id)
    assert manager._drain(8)
    second_attempt = job_a.attempt_id
    server = WaveServer(manager)
    server.start()
    try:
        yield server, manager, job_a, job_b, first_attempt, second_attempt, trigger_calls
    finally:
        manager.close()
        server.stop()


def archive(server, **query):
    query.setdefault("workspace_id", server._workspace_id)
    url = urllib.parse.urljoin(server.url, "/api/archive?" + urllib.parse.urlencode(query))
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(server.url).query)["token"][0]
    req = urllib.request.Request(url, headers={"X-Wave-Token": token})
    with urllib.request.urlopen(req, timeout=8) as response:
        return response.status, json.loads(response.read())


def test_http_report_history_latest_projections_and_scope(tmp_path):
    with report_workspace(tmp_path) as (
        server, manager, job_a, job_b, first_attempt, second_attempt, trigger_calls,
    ):
        history_query = dict(
            view="flow", job_id=job_a.id, attempt_id=first_attempt,
            stage="analyzer", producer="evaluate", kind="report",
            report_name="quality",
        )
        status, history = archive(server, **history_query)
        assert status == 200
        reports = [row["data"] for row in history["rows"]]
        assert [row["name"] for row in reports] == ["quality", "quality"]
        assert reports[0]["result"] == "review-needed"
        assert reports[1]["result"] == "accepted-with-note"
        # HTTP snapshots stringify integers outside JavaScript's exact range.
        assert reports[1]["values"]["exact"] == str(LARGE + 1)

        status, names = archive(
            server, view="flow_report_names", job_id=job_a.id,
            attempt_id=first_attempt, producer="evaluate",
        )
        assert status == 200
        assert names["rows"] == ["quality", "summary"]

        latest_query = dict(
            job_id=job_a.id, attempt_id=first_attempt, stage="analyzer",
            producer="evaluate", report_name="quality",
        )
        status, whole = archive(server, view="flow_latest", mode="record", **latest_query)
        assert status == 200
        assert len(whole["rows"]) == 1
        latest = whole["rows"][0]
        assert latest["data"]["result"] == "accepted-with-note"
        assert latest["data"]["values"] == {"exact": str(LARGE + 1)}

        status, per_field = archive(
            server, view="flow_latest", mode="fields", **latest_query,
        )
        assert status == 200
        merged = per_field["rows"][0]
        assert merged["data"]["result"] == "accepted-with-note"
        assert merged["data"]["reason"] == "second sample"
        assert merged["data"]["values"] == {
            "exact": str(LARGE + 1), "carry": "from first",
        }
        assert merged["field_sources"]["exact"] == latest["seq"]
        assert merged["field_sources"]["carry"] < latest["seq"]

        status, names_for_job_b = archive(
            server, view="flow_report_names", job_id=job_b.id,
            attempt_id=job_b.attempt_id, producer="evaluate",
        )
        assert status == 200
        assert names_for_job_b["rows"] == ["quality"]
        status, latest_for_job_b = archive(
            server, view="flow_latest", mode="record", job_id=job_b.id,
            attempt_id=job_b.attempt_id, stage="analyzer", producer="evaluate",
            report_name="quality",
        )
        assert status == 200
        assert latest_for_job_b["rows"][0]["data"]["values"] == {"exact": 9}

        status, first_attempt_latest = archive(
            server, view="flow_latest", mode="record", **latest_query,
        )
        assert status == 200 and first_attempt_latest["rows"][0]["seq"] == latest["seq"]
        status, second_attempt_latest = archive(
            server, view="flow_latest", mode="record", job_id=job_a.id,
            attempt_id=second_attempt, stage="analyzer", producer="evaluate",
            report_name="quality",
        )
        assert status == 200
        assert second_attempt_latest["rows"][0]["data"]["values"] == {
            "exact": str(LARGE + 1),
        }

        text_query = dict(
            view="flow_latest_text", job_id=job_a.id, attempt_id=first_attempt,
            seq=merged["seq"], mode="fields", offset=0, limit=4096,
        )
        status, full = archive(server, **text_query)
        assert status == 200
        projected = json.loads(full["text"])
        assert projected["data"]["values"]["exact"] == LARGE + 1
        assert projected["field_sources"]["carry"] < projected["field_sources"]["exact"]

        # Reading projections must not dispatch callbacks or change either verdict.
        assert trigger_calls == []
        assert job_a.result.verdict.value == "N/A"
        assert job_b.result.verdict.value == "N/A"
