"""Disk history queries never redirect an old attempt to a current control target."""

from rpkbin.wave import WaveWorkspace

import json
import sys
import urllib.parse
import urllib.request
from contextlib import contextmanager

import pytest

from rpkbin.jobcore import Job, JobManager
from rpkbin.wave.web import WaveServer

from test_wave import request


@contextmanager
def example(tmp_path, *, source=None, directory=None):
    manager = WaveWorkspace(JobManager(max_history=1), archive_dir=directory or tmp_path / "archives")
    job = manager.add(Job("example", [sys.executable, "-c", "print('X=1')"], pty=False))
    server = WaveServer(manager, source_path=source)
    server.start()
    try:
        yield server, manager, job
    finally:
        manager.close()
        server.stop()


def archive(server, **query):
    query.setdefault("workspace_id", server._workspace_id)
    return request(server.url.rstrip("/"), "/api/archive?" + urllib.parse.urlencode(query))


def test_chart_http_reads_committed_rows_while_writer_has_uncommitted_batch(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from rpkbin.flow import Flow, Parser, ViewSpec

    parser = Parser("values", lambda line, ctx: {"x": int(line), "y": int(line) * 2})
    view = ViewSpec.xy_line("values", source=parser, axis={"field": "x"},
                            series={"mode": "wide", "items": [{"id": "y", "field": "y"}]})
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path / "archive")
    job = manager.add(Job("charts", [sys.executable, "-c", "pass"], pty=False), flow=Flow("flow", parsers=[parser], views=[view]))
    server = WaveServer(manager)
    server.start()
    try:
        store = manager.archive
        job._line(job._attempt, "1")
        store.flush()
        scope = dict(job_id=job.id, attempt_id=job.attempt_id)
        monkeypatch.setattr(manager, "_save_current_attempts",
                            lambda: pytest.fail("Chart read attempted to save the workspace"))
        release, staged = Event(), Event()

        def write():
            with store.record_batch():
                job._line(job._attempt, "2")
                store.flush()
                staged.set()
                assert release.wait(5)

        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(write)
            try:
                assert staged.wait(5)
                status, meta = archive(server, **scope, view="chart_meta")
                assert status == 200 and meta["as_of_seq"] == 1
                status, page = archive(server, **scope, view="chart_window", view_id="values",
                                       series='["y"]', lo=0, hi=9, as_of_seq=1)
                assert status == 200
                assert [(point["value"], point["log_seq"]) for point in page["points"]] == [(2, 1)]
            finally:
                release.set()
            pending.result()
        status, meta = archive(server, **scope, view="chart_meta")
        assert status == 200 and meta["as_of_seq"] == 2
        status, page = archive(server, **scope, view="chart_window", view_id="values",
                               series='["y"]', lo=0, hi=9, as_of_seq=1)
        assert status == 200 and [point["log_seq"] for point in page["points"]] == [1]
    finally:
        monkeypatch.undo()
        manager.close()
        server.stop()


def test_api_log_pages_and_evicted_history(tmp_path):
    with example(tmp_path) as (server, manager, job):
        job._line(job._attempt, "X=9007199254740993")
        job._line(job._attempt, "X=9007199254740993")
        job._line(job._attempt, "X=9007199254740994")
        first = job.attempt_id
        status, page = archive(server, job_id=job.id, attempt_id=first, limit=2)
        assert status == 200
        assert page["rows"][0]["text"] == "X=9007199254740993"
        status, next_page = archive(server, job_id=job.id, attempt_id=first, limit=2, cursor=page["last"])
        assert status == 200 and [r["seq"] for r in next_page["rows"]] == [3]
        assert not next_page["has_more"]
        manager.start()
        assert manager._drain(8)
        for _ in range(2):
            manager.rerun(job)
            assert manager._drain(8)
        assert len(job.history) == 1
        status, saved = archive(server, view="attempt", job_id=job.id, attempt_id=first)
        assert status == 200 and saved["attempt"]["attempt_id"] == first
        status, logs = archive(server, view="logs", job_id=job.id, attempt_id=first)
        assert status == 200 and len(logs["rows"]) == 4
        status, attempts = archive(server, view="attempts", job_id=job.id, limit=2)
        assert status == 200 and attempts["has_more"]
        status, earlier = archive(server, view="attempts", job_id=job.id, limit=2, before=attempts["before"])
        assert status == 200 and earlier["rows"][0]["attempt_id"] == first


def test_archive_workspace_run_attempt_and_cursor_validation(tmp_path):
    with example(tmp_path) as (server, manager, job):
        job._line(job._attempt, "X=1")
        good = dict(job_id=job.id, attempt_id=job.attempt_id)
        assert archive(server, **good, workspace_id="old")[0] == 400
        assert archive(server, **good, run_id="../../bad")[0] == 400
        assert archive(server, **good, attempt_id_missing="irrelevant")[0] == 200
        for override in (
            dict(job_id="missing"), dict(attempt_id="missing"), dict(limit=101),
            dict(cursor="%%%"), dict(direction="sideways"),
            dict(start="nan"), dict(start=2, end=1),
        ):
            assert archive(server, **dict(good, **override))[0] == 400
        # Same origin/Host enforcement also covers this endpoint.
        status, _ = request(server.url.rstrip("/"), "/api/archive", headers={"Origin": "https://other.example"})
        assert status == 403


def test_large_text_is_fully_retrievable_with_bounded_chunks(tmp_path):
    with example(tmp_path) as (server, manager, job):
        text = "中😀\x00" * 50000
        job.log(text)
        query = dict(job_id=job.id, attempt_id=job.attempt_id)
        status, page = archive(server, view="logs", **query)
        assert status == 200 and page["rows"][0]["truncated"]
        assert len(json.dumps(page)) < 256 * 1024
        offset, result = 0, []
        while True:
            status, chunk = archive(server, view="text", seq=1, offset=offset, **query)
            assert status == 200
            result.append(chunk["text"])
            offset = chunk["next_offset"]
            if not chunk["has_more"]:
                break
        assert "".join(result) == text


def test_run_reopen_catalog_and_workfile_scoping(tmp_path):
    source = tmp_path / "work.py"
    with example(tmp_path, source=source) as (server, manager, job):
        job._line(job._attempt, "X=37")
        identity = dict(run_id=manager.run_id, job_id=job.id, attempt_id=job.attempt_id)
    with example(tmp_path, source=source) as (server, _, _):
        status, runs = archive(server, view="runs")
        assert status == 200
        assert identity["run_id"] in {row["run_id"] for row in runs["rows"]}
        status, page = archive(server, **identity)
        assert status == 200 and page["rows"][0]["text"] == "X=37"
        assert page["health"]["complete"]
    with example(tmp_path, source=tmp_path / "another.py") as (server, _, _):
        assert archive(server, **identity)[0] == 400
        status, runs = archive(server, view="runs")
        assert status == 200
        assert identity["run_id"] not in {row["run_id"] for row in runs["rows"]}


def test_archive_only_server_never_executes_source_work_file(tmp_path):
    source = tmp_path / "unsafe_to_reexecute.py"
    marker = tmp_path / "executed"
    source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('should not load')")
    with example(tmp_path, source=source) as (_, manager, job):
        job._line(job._attempt, "X=42")
        path, job_id, attempt_id = manager.archive.path, job.id, job.attempt_id
    server = WaveServer(archive_path=path)
    server.start()
    try:
        assert not marker.exists()
        assert server.workspace.jobs == [] and server.workspace.archive is None
        with urllib.request.urlopen(server.url, timeout=5) as response:
            html = response.read()
        assert b'<body class="archive-only">' in html
        status, state = request(server.url.rstrip('/'), '/api/state')
        assert status == 200 and state['opened_run_id'] == manager.run_id
        assert state['archive']['complete']
        status, data = archive(server, job_id=job_id, attempt_id=attempt_id)
        assert status == 200 and data['rows'][0]['text'] == 'X=42'
    finally:
        server.workspace.close()
        server.stop()


def test_attempt_picker_and_detail_preview_do_not_load_large_values_into_browser(tmp_path):
    with example(tmp_path) as (server, manager, job):
        text = 'large-value-' * 100000
        job._line(job._attempt, 'L=' + text)
        query = dict(job_id=job.id, attempt_id=job.attempt_id)
        status, picker = archive(server, view='attempts')
        assert status == 200 and 'data' not in picker['rows'][0]
        assert len(json.dumps(picker)) < 10000
        status, detail = archive(server, view='attempt', **query)
        assert status == 200 and not detail['attempt'].get('log_records')
        assert len(json.dumps(detail)) < 10000
        assert archive(server, view='text', seq=1, **query)[1]['text'].startswith('L=large-value-')



def test_retired_observation_and_statistics_views_are_not_available(tmp_path):
    with example(tmp_path) as (server, _, job):
        for view in ("observations", "statistics"):
            assert archive(server, view=view, job_id=job.id, attempt_id=job.attempt_id)[0] == 400


def test_chart_file_rc_and_vcd_http_roundtrip(tmp_path):
    from rpkbin.flow import Flow, Parser, ViewSpec

    parser = Parser("rtl", lambda line, ctx: {"tick": 10, "sig": "clk", "path": ["top", "clk"], "val": 1})
    rtl = ViewSpec.waveform(
        "rtl",
        source=parser,
        axis={"field": "tick", "unit": "ns"},
        series={"mode": "long", "id_field": "sig", "path_field": "path", "value_field": "val", "value_type": "bit"},
    )
    manager = WaveWorkspace(JobManager(max_history=1), archive_dir=tmp_path / "archives")
    job = manager.add(Job("sim", [sys.executable, "-c", "pass"], pty=False), flow=Flow("rtl_flow", parsers=[parser], views=[rtl]))
    server = WaveServer(manager)
    server.start()
    try:
        job._line(job._attempt, "clk=1")
        manager.archive.flush()
        base = server.url.rstrip("/")
        rc_base = str(tmp_path / "signals")
        code, saved = request(base, "/api/chart/file", {
            "workspace_id": server._workspace_id,
            "action": "save_rc",
            "path": rc_base,
            "content": "Magic 271485\naddSignal /top/clk\n",
        })
        assert code == 200
        assert saved["path"].endswith(".wave.rc")
        (tmp_path / "ignored.txt").write_text("x", encoding="utf-8")
        browse_q = urllib.parse.urlencode({"mode": "rc", "path": str(tmp_path)})
        b_code, listing = request(base, f"/api/files?{browse_q}")
        assert b_code == 200
        names = {e["name"] for e in listing["entries"]}
        assert "signals.wave.rc" in names
        assert "ignored.txt" not in names
        r_code, loaded = request(base, "/api/chart/file", {
            "workspace_id": server._workspace_id,
            "action": "read_rc",
            "path": saved["path"],
        })
        assert r_code == 200 and "addSignal /top/clk" in loaded["content"]
        vcd_path = str(tmp_path / "dump.vcd")
        v_code, exported = request(base, "/api/chart/file", {
            "workspace_id": server._workspace_id,
            "action": "export_vcd",
            "path": vcd_path,
            "job_id": job.id,
            "attempt_id": job.attempt_id,
            "view_id": "rtl",
            "axis": "time",
        })
        assert v_code == 200
        vcd_body = (tmp_path / "dump.vcd").read_text(encoding="utf-8")
        assert "$timescale 1ns $end" in vcd_body and "$scope module top $end" in vcd_body
    finally:
        manager.close()
        server.stop()
