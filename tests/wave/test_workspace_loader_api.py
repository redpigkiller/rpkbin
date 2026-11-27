"""Workspace loader validation, directory views and atomic replacement."""

import sqlite3
from pathlib import Path
from threading import Event
from urllib.parse import urlencode

import pytest

from rpkbin.wave import FileSource, ReanalysisSource, RunArchive, TailSource, WaveWorkspace
from rpkbin.wave.web import WaveServer
from test_wave import post, request, running_server, state


def _write_work(path):
    path.write_text(
        "import sys\nfrom rpkbin.jobcore import JobManager, Job\n"
        "manager=JobManager()\nmanager.add(Job('loaded', [sys.executable, '-c', 'pass'], pty=False))\n",
        encoding="utf-8",
    )


def test_files_modes_and_sorting(tmp_path):
    (tmp_path / "z-folder").mkdir()
    (tmp_path / "A-folder").mkdir()
    for name in ("b.log", "A.PY", "c.txt", "run.sqlite3", "other.SQLITE3-wal", "other.sqlite3-shm"):
        (tmp_path / name).touch()
    with running_server() as (server, base):
        server.launch_cwd = tmp_path
        for params, names in [
            ({}, ["A-folder", "z-folder", "A.PY"]),
            ({"mode": "work"}, ["A-folder", "z-folder", "A.PY"]),
            ({"mode": "logs"}, ["A-folder", "z-folder", "A.PY", "b.log", "c.txt"]),
            ({"mode": "archive"}, ["A-folder", "z-folder", "run.sqlite3"]),
        ]:
            code, listing = request(base, "/api/files?" + urlencode(params))
            assert code == 200, listing
            assert listing["path"] == str(tmp_path)
            assert [row["name"] for row in listing["entries"]] == names
            for row in listing["entries"]:
                assert isinstance(row["mtime"], float)
                assert row["size"] is None if row["directory"] else isinstance(row["size"], int)
            assert {s["id"] for s in listing["shortcuts"]} >= {"cwd", "archives", "home"}
        for query in ("mode=other", "mode=", "mode=work&mode=logs"):
            code, body = request(base, "/api/files?" + query)
            assert code == 400 and "mode" in body["error"]
            assert "field" not in body
        code, body = request(base, "/api/files?path=missing")
        assert code == 400 and body == {"error": "Choose a directory", "field": "browse_path"}


@pytest.mark.parametrize("once", [False, True])
def test_load_directory_with_flow_and_relative_paths(tmp_path, once):
    folder = tmp_path / "logs"
    (folder / "nested").mkdir(parents=True)
    (folder / "nested" / "match.trace").write_bytes(b"initial\n")
    (folder / "ignored.log").write_bytes(b"ignore\n")
    (tmp_path / "flow.py").write_text("from rpkbin.flow import Flow\nflow = Flow('my flow')\n", encoding="utf-8")
    with running_server() as (server, base):
        server.launch_cwd = tmp_path
        previous = server.workspace
        old_id = state(base)["workspace_id"]
        code, result = post(base, "/api/logs/load", dict(targets=" logs ", once=once, globs="*.trace, *.out",
                                                         recursive=True, start="end", flow_path="flow.py"))
        assert code == 200, result
        assert ("Analyze" if once else "Watch") in result["message"]
        preview = state(base)
        row, = preview["jobs"]
        assert row["name"] == "nested/match.trace"
        assert row["status"] == "PENDING" and row["flow_name"] == "my flow"
        assert row["source_kind"] == ("file" if once else "tail")
        assert preview["ready"] and not preview["started"]
        assert preview["source_path"] == str(folder)
        assert preview["sources"]["configured_watchers"] == int(not once)
        assert server.workspace.archive.metadata["source_path"] == str(folder)
        job, = server.workspace.jobs
        if not once:
            assert job._source.start == "end"
        assert previous.manager._closed
        assert request(base, "/api/logs/load", {"workspace_id": old_id, "targets": "logs"})[0] == 400


def test_load_multi_file_from_readonly_archive(tmp_path):
    a, b = tmp_path / "a.log", tmp_path / "b.txt"
    a.write_bytes(b"a\n")
    b.write_bytes(b"b\n")
    result = FileSource(a).run(archive_dir=tmp_path)
    server = WaveServer(archive_path=result.archive_path)
    base = server.start()
    try:
        assert state(base)["opened_run_id"] == RunArchive(result.archive_path).run_id
        previous = server.workspace
        code, body = post(base, "/api/logs/load", {"targets": [str(a), str(b)], "once": True})
        assert code == 200, body
        preview = state(base)
        assert "opened_run_id" not in preview and preview["source_path"] is None
        assert {row["name"] for row in preview["jobs"]} == {"a.log", "b.txt"}
        assert server.workspace.archive.metadata["source_path"] is None
        assert previous.manager._closed
        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        assert server.workspace._drain(5)
    finally:
        server.stop()
        server.workspace.close()


def test_empty_live_watcher_blocks_both_load_routes_until_stopped(tmp_path):
    folder = tmp_path / "watched"
    folder.mkdir()
    work = tmp_path / "work.py"
    _write_work(work)
    with running_server() as (server, base):
        code, body = post(base, "/api/logs/load", {"targets": str(folder), "once": True})
        assert code == 400 and "matching" in body["error"]
        assert body["field"] == "targets"
        code, body = post(base, "/api/logs/load", {"targets": [str(folder)]})
        assert code == 200 and "Start Watching" in body["message"]
        previous = server.workspace
        code, body = post(base, "/api/manager/action", {"action": "start"})
        assert code == 200 and body["message"] == "Watching started"
        observed = state(base)
        assert not observed["jobs"] and observed["sources"]["active_watchers"] == 1
        for route, payload in [("/api/logs/load", {"targets": str(folder)}), ("/api/work/load", {"path": str(work)})]:
            code, body = post(base, route, payload)
            assert code == 400 and "watch" in body["error"].lower()
            assert "field" not in body
            assert server.workspace is previous
        assert post(base, "/api/sources/stop", {})[0] == 200
        assert previous._drain(5)
        assert post(base, "/api/work/load", {"path": str(work)})[0] == 200
        assert all(not task.thread.is_alive() for task in previous._watchers)
        assert previous.manager._closed and previous.archive.health["closed_cleanly"]


def test_live_job_blocks_replacement_and_bad_options_preserve_preview(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    path = tmp_path / "input.log"
    path.touch()
    flow = tmp_path / "broken.py"
    flow.write_text("flow = (\n", encoding="utf-8")
    workspace = WaveWorkspace(archive_dir=tmp_path)
    workspace.add_source(TailSource(path, poll_interval=.02))
    workspace.validate()
    with running_server(workspace) as (server, base):
        before = state(base)
        invalid = (dict(targets=[]), dict(targets=[None]), dict(targets=" "), dict(once=1), dict(recursive="yes"),
                   dict(globs=["*.log", " "]), dict(globs="*.log, "), dict(start="middle"),
                   dict(flow_path=123), dict(flow_path=str(flow)))
        for extra in invalid:
            code, body = post(base, "/api/logs/load", {"targets": str(folder), **extra})
            assert code == 400, body
            assert server.workspace is workspace and not workspace.manager._closed
            assert state(base)["workspace_id"] == before["workspace_id"]
        assert "SyntaxError" in body["error"]
        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        assert post(base, "/api/logs/load", {"targets": str(folder)})[0] == 400


def test_failed_replacement_archive_closes_candidate_only(tmp_path, monkeypatch):
    path = tmp_path / "file.log"
    path.touch()
    with running_server() as (server, base):
        previous = server.workspace
        initial_id = state(base)["workspace_id"]
        rejected = []

        def fail_archive(workspace, *, source_path=None):
            rejected.append(workspace)
            workspace.archive_error = dict(path=str(Path(source_path)), message="archive unavailable")

        monkeypatch.setattr(WaveWorkspace, "enable_archive", fail_archive)
        code, body = post(base, "/api/logs/load", {"targets": str(path), "once": True})
        assert code == 400 and "archive unavailable" in body["error"]
        candidate, = rejected
        assert candidate.manager._closed and all(job._attempt.finalized for job in candidate.jobs)
        assert server.workspace is previous and not previous.manager._closed
        assert state(base)["workspace_id"] == initial_id


def test_live_watch_started_by_python_during_validation_blocks_replacement(tmp_path, monkeypatch):
    folder = tmp_path / "watched"
    folder.mkdir()
    path = tmp_path / "input.log"
    path.touch()
    previous = WaveWorkspace(archive_dir=tmp_path)
    previous.add_paths([folder])
    previous.validate()
    with running_server(previous) as (server, base):
        enable_archive = WaveWorkspace.enable_archive

        def enable_and_start(workspace, *, source_path=None):
            enable_archive(workspace, source_path=source_path)
            previous.start()

        monkeypatch.setattr(WaveWorkspace, "enable_archive", enable_and_start)
        code, body = post(base, "/api/logs/load", {"targets": str(path), "once": True})
        assert code == 400 and "watch" in body["error"].lower()
        assert server.workspace is previous
        assert state(base)["sources"]["active_watchers"] == 1


@pytest.mark.parametrize(("route", "action"), [("/api/work/load", "Start"), ("/api/logs/load", "Analyze")])
@pytest.mark.parametrize("failure", ["raises", "sqlite"])
def test_valid_preview_reports_cleanup_warning(tmp_path, monkeypatch, route, action, failure):
    path = tmp_path / "input.log"
    path.touch()
    work = tmp_path / "work.py"
    _write_work(work)
    payload = {"path": str(work)} if route == "/api/work/load" else {"targets": str(path), "once": True}
    with running_server() as (server, base):
        previous = server.workspace
        close = previous.archive.close

        def close_with_error(*, clean=True):
            close(clean=clean)
            raise OSError("archive close error")

        if failure == "raises":
            monkeypatch.setattr(previous.archive, "close", close_with_error)
            expected = "archive close error"
        else:
            previous.archive._writer.set_authorizer(
                lambda operation, argument, *_: sqlite3.SQLITE_DENY
                if operation == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT"
                else sqlite3.SQLITE_OK
            )
            expected = "not authorized"
        code, body = post(base, route, payload)
        assert code == 200 and body["ok"] is True
        assert body["message"] == f"Validated. Review the preview, then select {action}."
        assert body["warning"]["kind"] == "previous_workspace_cleanup"
        assert expected in body["warning"]["message"]
        assert previous.manager._closed
        assert server.workspace is not previous
        assert state(base)["ready"]


def test_failed_close_before_replacement_leaves_previous_workspace_open(tmp_path, monkeypatch):
    work = tmp_path / "work.py"
    _write_work(work)
    with running_server() as (server, base):
        previous = server.workspace
        previous_id = state(base)["workspace_id"]

        def fail_before_close():
            raise OSError("cleanup did not complete")

        with monkeypatch.context() as patch:
            patch.setattr(previous, "close", fail_before_close)
            code, body = post(base, "/api/work/load", {"path": str(work)})
        assert code == 400 and body == {"error": "cleanup did not complete"}
        assert server.workspace is previous
        assert not previous.manager._closed
        assert state(base)["workspace_id"] == previous_id


@pytest.mark.parametrize(("route", "payload", "field", "message"), [
    ("/api/work/load", {}, "path", "Python work file path is required"),
    ("/api/work/load", {"path": "missing.py"}, "path", "Work file not found"),
    ("/api/logs/load", {"targets": []}, "targets", "Provide one or more"),
    ("/api/logs/load", {"targets": "missing.log", "once": True}, "targets", "Source is not a file"),
    ("/api/logs/load", {"targets": "logs", "globs": "*.log, "}, "globs", "nonempty patterns"),
    ("/api/logs/load", {"targets": "logs", "globs": "."}, "globs", "pattern"),
    ("/api/logs/load", {"targets": "logs", "flow_path": 1}, "flow_path", "must be a string"),
    ("/api/logs/load", {"targets": "logs", "flow_path": "missing.py"}, "flow_path", "Could not load Flow"),
    ("/api/settings/save", {"max_workers": False}, "max_workers", "max_workers must be an integer"),
    ("/api/settings/save", {"max_history": 0}, "max_history", "max_history must be an integer"),
    ("/api/settings/save", {"max_history": None}, "max_history", "Settings values cannot be null"),
    ("/api/settings/save", {"resources": {"test.slot": -1}}, "resources.test.slot", "capacity test.slot must be an integer"),
    ("/api/job/retention", {"max_log_lines": 0}, "max_log_lines", "max_log_lines must be an integer"),
])
def test_invalid_form_values_return_correctable_field(tmp_path, route, payload, field, message):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.log").touch()
    with running_server() as (server, base):
        server.launch_cwd = tmp_path
        previous = state(base)
        code, body = post(base, route, payload)
        assert code == 400
        assert message in body["error"]
        assert body["field"] == field
        current = state(base)
        for key in ("workspace_id", "max_workers", "max_history", "resources"):
            assert current[key] == previous[key]


def test_malformed_settings_stay_form_level():
    with running_server() as (_, base):
        code, body = post(base, "/api/settings/save", {"resources": {" ": 0}})
        assert code == 400 and body == {"error": "resource name must be nonempty"}


@pytest.mark.parametrize(("mode", "message"), [
    ("process", "Dispatch started"),
    ("tail", "Watching started"),
    ("file", "Analysis started"),
    ("reanalysis", "Reanalysis started"),
])
def test_start_acknowledges_workspace_mode(tmp_path, mode, message):
    path = tmp_path / "sample.log"
    path.write_text("line\n", encoding="utf-8")
    workspace = WaveWorkspace(archive_dir=tmp_path)
    if mode == "tail":
        workspace.add_source(TailSource(path))
    elif mode == "file":
        workspace.add_source(FileSource(path))
    elif mode == "reanalysis":
        original = FileSource(path).run(archive_dir=tmp_path)
        workspace.add_source(ReanalysisSource(original.archive_path, original.snapshot["attempt_id"]))
    if mode != "process":
        workspace.validate()
    with running_server(workspace) as (_, base):
        if mode == "process":
            work = tmp_path / "work.py"
            _write_work(work)
            code, body = post(base, "/api/work/load", {"path": str(work)})
            assert code == 200 and "warning" not in body
        code, body = post(base, "/api/manager/action", {"action": "start"})
        assert code == 200 and body["message"] == message
        assert state(base)["started"]


@pytest.mark.parametrize("single_reader", [False, True])
def test_stop_static_analysis_acknowledges_reading(tmp_path, monkeypatch, single_reader):
    path = tmp_path / "sample.log"
    path.write_text("line\n", encoding="utf-8")
    source = FileSource(path)
    release = Event()
    read = source._read

    def blocking_read(reader, stop):
        release.wait()
        return read(reader, stop)

    monkeypatch.setattr(source, "_read", blocking_read)
    workspace = WaveWorkspace(archive_dir=tmp_path)
    job = workspace.add_source(source)
    workspace.validate()
    with running_server(workspace) as (_, base):
        try:
            assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
            payload = {"job_id": job.id} if single_reader else {}
            code, body = post(base, "/api/sources/stop", payload)
            assert code == 200 and body["message"].startswith("Stopping reading")
        finally:
            release.set()
        assert workspace._drain(5)
        assert state(base, job.id)["detail"]["completion_reason"] == "user_stopped"


def test_archive_catalog_all_and_open_route(tmp_path):
    arch_dir = tmp_path / "archives"
    log_a = tmp_path / "a.log"
    log_b = tmp_path / "b.log"
    log_a.write_text("alpha line\n", encoding="utf-8")
    log_b.write_text("beta line\n", encoding="utf-8")
    res_a = FileSource(log_a).run(archive_dir=arch_dir)
    res_b = FileSource(log_b).run(archive_dir=arch_dir)

    work = tmp_path / "work.py"
    _write_work(work)
    ws = WaveWorkspace(archive_dir=arch_dir)
    with running_server(ws) as (server, base):
        server.launch_cwd = tmp_path
        wid = state(base)["workspace_id"]
        code, catalog = request(
            base,
            "/api/archive?" + urlencode({"view": "runs", "all": "true", "workspace_id": wid}),
        )
        assert code == 200, catalog
        found_ids = {row["run_id"] for row in catalog["rows"]}
        assert {RunArchive(res_a.archive_path).run_id, RunArchive(res_b.archive_path).run_id}.issubset(found_ids)
        row_a = next(row for row in catalog["rows"] if row["run_id"] == RunArchive(res_a.archive_path).run_id)
        assert row_a["filename"] == res_a.archive_path.name
        assert row_a["path"] == str(res_a.archive_path)
        assert isinstance(row_a["size"], int) and row_a["size"] > 0
        assert row_a["run_outcome"]

        # Shortcut for Work File Dir appears when work file lives in a separate folder from launch_cwd.
        sub_dir = tmp_path / "nested_work"
        sub_dir.mkdir()
        sub_work = sub_dir / "nested.py"
        _write_work(sub_work)
        assert post(base, "/api/work/load", {"path": str(sub_work)})[0] == 200
        code, listing = request(base, "/api/files?mode=logs")
        assert code == 200
        assert {s["id"]: s["path"] for s in listing["shortcuts"]}["work_dir"] == str(sub_dir.resolve())

        # Active running workspace archive cannot be deleted; closed unused archive can be deleted.
        snap_before = state(base)
        active_id = snap_before["archive"]["run_id"]
        wid = snap_before["workspace_id"]
        act_code, act_body = post(base, "/api/archive/delete", {"workspace_id": wid, "run_id": active_id})
        assert act_code == 400 and "Cannot delete" in act_body["error"]

        # Reject invalid archive paths without disturbing current workspace.
        bad_code, bad_body = post(base, "/api/archive/open", {"path": str(tmp_path / "missing.sqlite3")})
        assert bad_code == 400 and bad_body["field"] == "path"
        assert "opened_run_id" not in state(base)

        # Open saved archive when idle -> transitions into read-only archive mode.
        ok_code, ok_body = post(base, "/api/archive/open", {"path": str(res_a.archive_path)})
        assert ok_code == 200, ok_body
        snap = state(base)
        run_a_id = RunArchive(res_a.archive_path).run_id
        run_b_id = RunArchive(res_b.archive_path).run_id
        assert snap["opened_run_id"] == run_a_id
        assert snap["archive_dir"] == str(arch_dir.resolve())
        assert snap["archive_dir_exists"] is True

        # Cannot delete currently opened archive, but can delete closed sibling archive res_b.
        op_code, op_body = post(base, "/api/archive/delete", {"workspace_id": snap["workspace_id"], "run_id": run_a_id})
        assert op_code == 400 and "Cannot delete" in op_body["error"]
        del_code, del_body = post(base, "/api/archive/delete", {"workspace_id": snap["workspace_id"], "run_id": run_b_id})
        assert del_code == 200 and del_body["deleted_run_id"] == run_b_id
        assert not res_b.archive_path.exists()

        # Load work file while in archive, start a blocking watcher, and verify busy rejection on archive open.
        folder = tmp_path / "watched_busy"
        folder.mkdir()
        assert post(base, "/api/logs/load", {"targets": [str(folder)], "once": False})[0] == 200
        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        busy_code, busy_body = post(base, "/api/archive/open", {"path": str(res_b.archive_path)})
        assert busy_code == 400 and "Stop" in busy_body["error"]
        assert "opened_run_id" not in state(base)
        assert post(base, "/api/sources/stop", {})[0] == 200
