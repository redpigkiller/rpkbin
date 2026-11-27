"""Flow archive pages and HTTP endpoints preserve scope and exact payloads."""

from rpkbin.wave import WaveWorkspace

from contextlib import contextmanager
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from rpkbin.flow import Flow, Parser
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import _archive_format as archive_module
from rpkbin.wave.web import WaveServer


@contextmanager
def example(tmp_path, code):
    manager = WaveWorkspace(JobManager(max_history=1), archive_dir=tmp_path / "archives")
    flow = Flow("api-flow", parsers=(Parser(
        "item", lambda line, ctx: {"value": int(line[5:])} if line.startswith("ITEM=") else None
    ),))
    job = manager.add(Job("example", [sys.executable, "-u", "-c", code], pty=False), flow=flow)
    manager.start()
    assert manager._drain(8)
    server = WaveServer(manager)
    server.start()
    try:
        yield server, manager, job
    finally:
        manager.close()
        server.stop()


def request_raw(server, **query):
    query.setdefault("workspace_id", server._workspace_id)
    url = urllib.parse.urljoin(server.url, "/api/archive?" + urllib.parse.urlencode(query))
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(server.url).query)["token"][0]
    req = urllib.request.Request(url, headers={"X-Wave-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def request(server, **query):
    status, body = request_raw(server, **query)
    return status, json.loads(body)


def test_archive_flow_filters_and_keyset_continue_after_sparse_matches(tmp_path):
    code = "; ".join(f"print('ITEM={number}')" for number in range(1, 7))
    with example(tmp_path, code) as (_, manager, job):
        first = manager.archive.flow_page(
            job.id, job.attempt_id, producer="item",
            conditions=[{"field": "value", "op": "gte", "value": 4}], limit=1,
        )
        assert [row["data"]["value"] for row in first["rows"]] == [4]
        assert first["has_more"]
        assert first["next_seq"] >= first["rows"][-1]["seq"]
        second = manager.archive.flow_page(
            job.id, job.attempt_id, producer="item", after=first["next_seq"],
            conditions=[{"field": "value", "op": "gte", "value": 4}], limit=10,
        )
        assert [row["data"]["value"] for row in second["rows"]] == [5, 6]
        assert not second["has_more"]


def test_sparse_filter_returns_reusable_cursors_for_empty_scan_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_module, "READER_SCAN_LIMIT", 2)
    code = "; ".join(f"print('ITEM={number}')" for number in range(1, 7))
    with example(tmp_path, code) as (_, manager, job):
        options = dict(
            job_id=job.id,
            attempt_id=job.attempt_id,
            producer="item",
            conditions=[{"field": "value", "op": "gte", "value": 5}],
            limit=1,
        )
        first = manager.archive.flow_page(**options)
        assert first["rows"] == []
        assert first["has_more"] and first["next_seq"] == 2

        second = manager.archive.flow_page(**options, after=first["next_seq"])
        assert second["rows"] == []
        assert second["has_more"] and second["next_seq"] == 4

        third = manager.archive.flow_page(**options, after=second["next_seq"])
        assert [row["data"]["value"] for row in third["rows"]] == [5]
        assert third["has_more"] and third["next_seq"] == 5

        fourth = manager.archive.flow_page(**options, after=third["next_seq"])
        assert [row["data"]["value"] for row in fourth["rows"]] == [6]
        assert not fourth["has_more"]


def test_flow_text_reassembles_exact_json_with_bounded_chunks(tmp_path):
    large = 9007199254740993
    code = f"print('ITEM={large}')"
    with example(tmp_path, code) as (_, manager, job):
        row = manager.archive.flow_page(job.id, job.attempt_id, producer="item")["rows"][0]
        chunks = []
        offset = 0
        while True:
            part = manager.archive.flow_text(job.id, job.attempt_id, row["seq"],
                                             offset=offset, limit=17)
            assert len(part["text"]) <= 17
            chunks.append(part["text"])
            offset = part["next_offset"]
            if not part["has_more"]:
                break
        exact = "".join(chunks)
        assert json.loads(exact)["value"] == large
        assert "9007199254740993" in exact


def test_http_flow_views_keep_large_integer_and_enforce_workspace_scope(tmp_path):
    large = 9007199254740993
    with example(tmp_path, f"print('ITEM={large}')") as (server, manager, job):
        base = dict(view="flow", job_id=job.id, attempt_id=job.attempt_id)
        status, page = request(server, **base, producer="item")
        assert status == 200
        value = page["rows"][0]["data"]["value"]
        assert int(value) == large
        assert page["rows"][0]["evidence"]["line_no"] >= 1

        raw_status, raw = request_raw(server, **base, producer="item", workspace_id="another-workspace")
        assert raw_status == 400
        assert b"9007199254740993" not in raw

        raw_status, raw = request_raw(server, **base, producer="item")
        assert raw_status == 200
        assert b"9007199254740993" in raw
        text_status, text = request(server, **dict(base, view="flow_text", seq=1, offset=0))
        assert text_status == 200
        assert json.loads(text["text"])["value"] == large
