"""HTTP contract for bounded, read-only saved-attempt reader queries."""

from __future__ import annotations

from rpkbin.wave import WaveWorkspace

import sys
import threading
import urllib.parse
import urllib.request

import pytest

from rpkbin.jobcore import Job, JobManager
from rpkbin.wave.archive import RunArchive
from test_wave import finish, post, request, running_server, state, target


def reader(base, workspace_id, job_id, attempt_id, **options):
    query = {
        "view": "reader",
        "workspace_id": workspace_id,
        "job_id": job_id,
        "attempt_id": attempt_id,
        **options,
    }
    return request(base, "/api/archive?" + urllib.parse.urlencode(query))


def make_manager(tmp_path):
    manager = WaveWorkspace(JobManager(max_history=3), archive_dir=tmp_path / "archive")
    job = manager.add(Job("reader", [sys.executable, "-u", "-c", "pass"], pty=False))
    return manager, job


def test_reader_literal_is_casefolded_and_searches_complete_log_text(tmp_path):
    manager, job = make_manager(tmp_path)
    with running_server(manager) as (server, base):
        workspace = state(base)["workspace_id"]
        long_line = "prefix-" + "x" * 5000 + "-NeedleTail"
        job.log("MiXeD needle")
        job.log(long_line)
        status, result = reader(base, workspace, job.id, job.attempt_id, query="mixed NEEDLE")
        assert status == 200
        assert [row["text"] for row in result["rows"]] == ["MiXeD needle"]
        status, result = reader(base, workspace, job.id, job.attempt_id, query="NeedleTail")
        assert status == 200 and len(result["rows"]) == 1
        assert result["rows"][0]["truncated"]
        status, result = reader(base, workspace, job.id, job.attempt_id,
                                query="mixed", regex="true")
        assert status == 200 and len(result["rows"]) == 1  # regex defaults to case-insensitive
        status, result = reader(base, workspace, job.id, job.attempt_id,
                                query="mixed", regex="true", case_sensitive="true")
        assert status == 200 and result["rows"] == []
        status, result = reader(base, workspace, job.id, job.attempt_id,
                                query="MiXeD", regex="true", case_sensitive="true")
        assert status == 200 and len(result["rows"]) == 1


def test_reader_api_case_sensitive_whole_word_and_logs_view(tmp_path):
    manager, job = make_manager(tmp_path)
    with running_server(manager) as (server, base):
        workspace = state(base)["workspace_id"]
        job.log("warn WARNING Warn")
        job.log("warning_only")
        job.log("Warn exact token")

        for view in ("reader", "logs"):
            # Case-insensitive substring (default)
            status, res = reader(base, workspace, job.id, job.attempt_id, view=view, query="warn")
            assert status == 200 and [r["seq"] for r in res["rows"]] == [1, 2, 3]

            # Case-sensitive substring
            status, res = reader(base, workspace, job.id, job.attempt_id,
                                 view=view, query="Warn", case_sensitive="true")
            assert status == 200 and [r["seq"] for r in res["rows"]] == [1, 3]

            # Case-insensitive whole word
            status, res = reader(base, workspace, job.id, job.attempt_id,
                                 view=view, query="warn", whole_word="true")
            assert status == 200 and [r["seq"] for r in res["rows"]] == [1, 3]

            # Case-sensitive whole word
            status, res = reader(base, workspace, job.id, job.attempt_id,
                                 view=view, query="warn", case_sensitive="true", whole_word="true")
            assert status == 200 and [r["seq"] for r in res["rows"]] == [1]

            # Regex + whole word + case-sensitive
            status, res = reader(base, workspace, job.id, job.attempt_id,
                                 view=view, query="Warn|exact", regex="true",
                                 case_sensitive="true", whole_word="true")
            assert status == 200 and [r["seq"] for r in res["rows"]] == [1, 3]


def test_reader_cursor_is_bound_to_workspace_run_attempt_and_filters(tmp_path):
    manager, job = make_manager(tmp_path)
    with running_server(manager) as (server, base):
        workspace = state(base)["workspace_id"]
        for i in range(3):
            job.log(f"row {i}")
        status, page = reader(base, workspace, job.id, job.attempt_id, limit=1)
        assert status == 200 and page["has_more"]
        assert reader(base, "wrong-workspace", job.id, job.attempt_id)[0] == 400
        assert reader(base, workspace, job.id, job.attempt_id,
                      run_id="../invalid")[0] == 400
        assert reader(base, workspace, job.id, "not-an-attempt")[0] == 400
        assert reader(base, workspace, job.id, job.attempt_id,
                      cursor=page["next"], query="different filter")[0] == 400
        assert reader(base, workspace, job.id, job.attempt_id,
                      cursor=page["next"], case_sensitive="true")[0] == 400
        assert reader(base, workspace, job.id, job.attempt_id,
                      cursor=page["next"], whole_word="true")[0] == 400


@pytest.mark.parametrize("options", [
    {"kind": "observations"},
    {"fields": '["value"]'},
    {"value": "9007199254740993"},
    {"changed": "true"},
    {"regex": "yes"},
    {"case_sensitive": "yes"},
    {"whole_word": "1"},
    {"regex": "true", "query": "("},
    {"regex": "true", "whole_word": "true", "query": "a)(b"},
    {"start": "nan"},
    {"start": "2", "end": "1"},
    {"limit": "0"},
    {"direction": "sideways"},
])
def test_reader_rejects_malformed_filters(options, tmp_path):
    manager, job = make_manager(tmp_path)
    with running_server(manager) as (_, base):
        workspace = state(base)["workspace_id"]
        assert reader(base, workspace, job.id, job.attempt_id, **options)[0] == 400


def test_saved_attempt_reader_never_targets_current_job_controls(tmp_path):
    manager, job = make_manager(tmp_path)
    with running_server(manager) as (_, base):
        workspace = state(base)["workspace_id"]
        first = job.attempt_id
        job.log("saved evidence")
        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        finish(manager)
        current = state(base, job.id)["detail"]
        assert post(base, "/api/job/action", {
            "action": "rerun", "targets": [target(current)]
        })[1]["affected"] == 1
        finish(manager)
        current = state(base, job.id)["detail"]
        status, old = reader(base, workspace, job.id, first)
        assert status == 200 and old["rows"][0]["text"] == "saved evidence"
        result = post(base, "/api/job/action", {
            "action": "stop", "targets": [{
                "job_id": job.id, "attempt_id": first,
                "submission_id": current["submission_id"],
            }],
        })[1]
        assert result["affected"] == 0
        assert job.attempt_id == current["attempt_id"]


def test_analysis_javascript_is_served_from_local_server(tmp_path):
    manager, _ = make_manager(tmp_path)
    with running_server(manager) as (_, base):
        with urllib.request.urlopen(urllib.parse.urljoin(base, "/analysis.js"), timeout=8) as response:
            assert response.status == 200
            assert "analysisQuery" in response.read().decode("utf-8")


def test_slow_reader_does_not_hold_up_live_state(monkeypatch, tmp_path):
    manager, job = make_manager(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original_reader = RunArchive.reader

    def paused_reader(self, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_reader(self, *args, **kwargs)

    monkeypatch.setattr(RunArchive, "reader", paused_reader)
    with running_server(manager) as (_, base):
        workspace = state(base)["workspace_id"]
        result = []

        def read_archive():
            result.append(reader(base, workspace, job.id, job.attempt_id))

        worker = threading.Thread(target=read_archive)
        worker.start()
        try:
            assert entered.wait(5)
            # The reader is held inside SQLite-facing work. A live state query
            # must still be able to acquire the server action lock and complete.
            status, snapshot = request(base, "/api/state")
            assert status == 200 and snapshot["workspace_id"] == workspace
        finally:
            release.set()
            worker.join(timeout=8)
        assert not worker.is_alive()
        assert result and result[0][0] == 200



@pytest.mark.parametrize("view", ["reader", "logs", "text"])
@pytest.mark.parametrize("retired", ["kind", "fields", "field", "value", "changed"])
def test_raw_log_views_reject_retired_filters(view, retired, tmp_path):
    manager, job = make_manager(tmp_path)
    job.log("raw output")
    with running_server(manager) as (_, base):
        workspace = state(base)["workspace_id"]
        code, payload = reader(base, workspace, job.id, job.attempt_id,
                               view=view, seq=1, **{retired: "value"})
        assert code == 400
        assert "retired filters" in payload["error"]
