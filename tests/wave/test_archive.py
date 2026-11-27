"""Raw Log history and explicit Flow publications outlive live caches."""

from rpkbin.wave import WaveWorkspace

import json
import sqlite3
import subprocess
import sys

import pytest

from rpkbin.flow import Analyzer, Automation, Flow, Parser
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import RunArchive
from rpkbin.wave._archive_format import CHUNK_CHARS, PAGE_LIMIT


def make(tmp_path, *, code="print('COMPLETE')", flow=None, **kwargs):
    manager = WaveWorkspace(JobManager(**kwargs), archive_dir=tmp_path)
    job = manager.add(Job("example", [sys.executable, "-u", "-c", code], pty=False,
                          max_log_lines=2), flow=flow)
    return manager, job


def walk(store, job, attempt, **kwargs):
    cursor = None
    while True:
        page = store.page(job, attempt, cursor=cursor, **kwargs)
        yield from page["rows"]
        if not page["has_more"]:
            break
        cursor = page["last"]


def walk_flow(store, job, attempt, **kwargs):
    after = None
    while True:
        page = store.flow_page(job, attempt, after=after, **kwargs)
        yield from page["rows"]
        if not page["has_more"]:
            break
        assert page["next_seq"] != after
        after = page["next_seq"]


def test_repeated_values_and_multiple_parser_sources_are_complete(tmp_path):
    value = Parser("value", lambda line, ctx: {"value": int(line.split()[0].split("=")[1])})
    other = Parser("other", lambda line, ctx: {"word": line.split()[1].split("=")[1]})
    accept = Analyzer("accept", on_finish=lambda end, ctx: ctx.summary.result("PASS", "complete", final=True))
    manager, job = make(tmp_path, code="for x in [0,0,2,2,1]: print(f'VALUE={x} OTHER=ready')",
                        flow=Flow("values", parsers=[value, other], analyzers=[accept]))
    try:
        manager.start()
        assert manager._drain(8)
        rows = list(walk_flow(manager.archive, job.id, job.attempt_id, stage="parser", kind="record", limit=3))
        assert [r["data"]["value"] for r in rows if r["producer"] == "value"] == [0, 0, 2, 2, 1]
        assert [r["data"]["word"] for r in rows if r["producer"] == "other"] == ["ready"] * 5
        assert len(rows) == 10
        logs = list(walk(manager.archive, job.id, job.attempt_id, limit=2))
        assert [r["seq"] for r in logs] == [1, 2, 3, 4, 5]
        assert [r["text"] for r in logs] == [f"VALUE={x} OTHER=ready" for x in [0, 0, 2, 2, 1]]
        assert len(job.logs()) == 2
        assert job.to_snapshot_dict()["result"] == "PASS"
    finally:
        manager.close()
    reopened = RunArchive(manager.archive.path)
    assert reopened.health["complete"]
    assert reopened.attempt(job.id, job.attempt_id)["result"] == "PASS"
    assert len(list(walk_flow(reopened, job.id, job.attempt_id, stage="parser"))) == 10


def test_history_survives_attempt_and_log_cache_eviction(tmp_path):
    flow = Flow("words", parsers=[Parser("word", lambda line, ctx: {"word": line})])
    manager, job = make(tmp_path, max_history=1, flow=flow)
    ids = []
    try:
        manager.start()
        for run in range(4):
            if run:
                manager.rerun(job)
            assert manager._drain(8)
            ids.append(job.attempt_id)
        assert len(job.history) == 1
        with pytest.raises(ValueError, match="no longer retained"):
            job.to_snapshot_dict(attempt_id=ids[0])
        for attempt in ids:
            assert manager.archive.attempt(job.id, attempt)["finalized"]
            assert manager.archive.flow_page(job.id, attempt)["rows"][0]["data"] == {"word": "COMPLETE"}
            assert manager.archive.page(job.id, attempt)["rows"][0]["text"] == "COMPLETE"
        receipt = manager.control(job.id, "stop", expected_attempt=ids[0], expected_submission=job.submission_id)
        assert receipt.status == "rejected" and "stale" in receipt.reason
        assert len(manager.archive.attempts(job.id)["rows"]) == 4
    finally:
        manager.close()


def test_parser_failure_keeps_raw_log_and_independent_parser(tmp_path):
    broken = Parser("bad", lambda line, ctx: {"value": int(line.split()[0].split("=")[1])})
    good = Parser("other", lambda line, ctx: {"other": int(line.split()[1].split("=")[1])})
    manager, job = make(tmp_path, code="print('VALUE=x OTHER=1');print('VALUE=2 OTHER=2')",
                        flow=Flow("parse", parsers=[broken, good]))
    try:
        manager.start()
        assert manager._drain(8)
        assert job.to_snapshot_dict()["result"] == "ERROR"
        rows = list(walk_flow(manager.archive, job.id, job.attempt_id, kind="record"))
        assert [r["data"]["other"] for r in rows] == [1, 2]
        assert all(r["producer"] == "other" for r in rows)
        logs = list(walk(manager.archive, job.id, job.attempt_id))
        assert [r["text"] for r in logs] == ["VALUE=x OTHER=1", "VALUE=2 OTHER=2"]
        assert all(r["source"] == "stdout" for r in logs)
        errors = manager.archive.flow_page(job.id, job.attempt_id, kind="diagnostic")["rows"]
        assert len(errors) == 1 and errors[0]["producer"] == "bad"
    finally:
        manager.close()


def test_raw_cursor_identity_time_filters_and_reverse_pages(tmp_path):
    manager, job = make(tmp_path)
    try:
        for n in range(20):
            job._line(job._attempt, f"X={n // 3}")
        store, attempt = manager.archive, job.attempt_id
        page = store.page(job.id, attempt, limit=4)
        reverse = store.page(job.id, attempt, limit=4, direction="backward")
        assert [r["seq"] for r in reverse["rows"]] == [17, 18, 19, 20]
        previous = store.page(job.id, attempt, limit=4, direction="backward", cursor=reverse["first"])
        assert [r["seq"] for r in previous["rows"]] == [13, 14, 15, 16]
        for arguments in ({"start": 1}, {"end": 1}, {"around": 4}):
            with pytest.raises(ValueError, match="Cursor|around"):
                store.page(job.id, attempt, cursor=page["last"], **arguments)
        for cursor in ("%%%", "a", "W10", "WzEsMiwzXQ"):
            with pytest.raises(ValueError):
                store.page(job.id, attempt, cursor=cursor)
        with pytest.raises(ValueError):
            store.page("wrong-job", attempt)
        for invalid in (0, PAGE_LIMIT + 1, True, "10"):
            with pytest.raises(ValueError):
                store.page(job.id, attempt, limit=invalid)
        assert len(list(walk(store, job.id, attempt, limit=3))) == 20
    finally:
        manager.close()


def test_exact_json_null_bigints_and_large_outputs_have_complete_chunks(tmp_path):
    flow = Flow("json", parsers=[Parser("json", lambda line, ctx: {"value": json.loads(line[2:])})])
    manager, job = make(tmp_path, flow=flow)
    values = [None, False, 0, 1.25, 2**120 + 1, [None, {"x": 2**81}], {"unknown": True}, "中😀" * 90000]
    try:
        for value in values:
            job._line(job._attempt, "V=" + json.dumps(value, ensure_ascii=False))
        store, attempt = manager.archive, job.attempt_id
        rows = list(walk_flow(store, job.id, attempt, kind="record"))
        assert [r["data"]["value"] for r in rows[:-1]] == values[:-1]
        assert type(rows[1]["data"]["value"]) is bool
        assert type(rows[2]["data"]["value"]) is int
        assert rows[-1]["truncated"] and rows[-1]["data"] == {}
        chunks, offset = [], 0
        while True:
            chunk = store.flow_text(job.id, attempt, rows[-1]["seq"], offset=offset)
            assert len(chunk["text"]) <= CHUNK_CHARS
            chunks.append(chunk["text"])
            offset = chunk["next_offset"]
            if not chunk["has_more"]:
                break
        assert json.loads("".join(chunks)) == {"value": values[-1]}
        chunks, offset = [], 0
        while True:
            chunk = store.read_text(job.id, attempt, 8, offset=offset)
            chunks.append(chunk["text"])
            offset = chunk["next_offset"]
            if not chunk["has_more"]:
                break
        assert "".join(chunks) == "V=" + json.dumps(values[-1], ensure_ascii=False)
    finally:
        manager.close()


def test_log_nul_and_unicode_byte_chunk_boundaries_are_lossless(tmp_path):
    manager, job = make(tmp_path)
    text = "A\x00中😀\r" * 200
    try:
        job.log(text)
        store, attempt = manager.archive, job.attempt_id
        chunks, offset = [], 0
        while True:
            part = store.read_text(job.id, attempt, 1, offset=offset, limit=7)
            chunks.append(part["text"])
            offset = part["next_offset"]
            if not part["has_more"]:
                break
        assert "".join(chunks) == text
        preview = store.page(job.id, attempt)["rows"][0]
        assert text.startswith(preview["text"]) and preview["truncated"]
    finally:
        manager.close()


@pytest.mark.parametrize("flow_enabled", [False, True])
def test_failed_raw_archive_is_health_failure_not_acceptance_failure(tmp_path, flow_enabled):
    flow = Flow("acceptance", analyzers=[Analyzer(
        "accept", on_finish=lambda end, ctx: ctx.summary.result("PASS", "complete", final=True),
    )]) if flow_enabled else None
    manager, job = make(tmp_path, flow=flow)
    try:
        manager.archive._writer.execute("CREATE TRIGGER reject_logs BEFORE INSERT ON records BEGIN SELECT RAISE(FAIL, 'disk unavailable'); END")
        manager.archive._writer.commit()
        manager.start()
        assert manager._drain(8)
        snapshot = job.to_snapshot_dict(include_details=True)
        assert snapshot["result"] == ("PASS" if flow_enabled else "N/A")
        if flow_enabled:
            assert snapshot["flow"]["publication_error"]
            assert snapshot["flow"]["error_count"] == 0
        health = manager.snapshot()["archive"]
        assert health["error"] and not health["complete"]
    finally:
        manager.close()
    assert not RunArchive(manager.archive.path).health["complete"]


def test_creation_failure_and_late_enable_are_explicit(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("file")
    manager = WaveWorkspace(JobManager(), archive_dir=blocker)
    try:
        assert manager.archive is None
        assert manager.archive_health["error"]
    finally:
        manager.close()
    manager = WaveWorkspace(JobManager())
    manager.add(Job("short", [sys.executable, "-c", "pass"], pty=False))
    try:
        manager.start()
        with pytest.raises(ValueError, match="before starting"):
            manager.enable_archive(tmp_path)
    finally:
        manager.close()


@pytest.mark.parametrize("version", [1, 2, 3, 999])
def test_unsupported_schema_missing_and_corrupt_archive(tmp_path, version):
    with pytest.raises(sqlite3.OperationalError):
        RunArchive(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()
    corrupt = tmp_path / "corrupt"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises((ValueError, sqlite3.Error)):
        RunArchive(corrupt)
    manager, _ = make(tmp_path)
    manager.close()
    with sqlite3.connect(manager.archive.path) as conn:
        conn.execute("UPDATE metadata SET value=? WHERE key='schema_version'", (json.dumps(version),))
    with pytest.raises(ValueError, match="schema"):
        RunArchive(manager.archive.path)


def test_process_crash_recovers_committed_raw_batch_only(tmp_path):
    path = tmp_path / "crash.sqlite3"
    code = """
import os,sys
from rpkbin.wave import RunArchive
a=RunArchive(sys.argv[1],create=True,run_id='crash')
a.save_attempt(dict(attempt_id='a',id='j',attempt_number=1))
for seq in range(1,258):
 a.append('a',dict(line_no=seq,time=1.0,text=str(seq),source='stdout'),0)
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(path)], check=True, timeout=10)
    store = RunArchive(path)
    assert not store.health["complete"]
    assert [int(r["text"]) for r in walk(store, "j", "a")] == list(range(1, 257))


def test_concurrent_core_jobs_preserve_flow_attempt_boundaries(tmp_path):
    flow = Flow("counter", parsers=[Parser("counter", lambda line, ctx: {"x": int(line[2:])})])
    manager = WaveWorkspace(JobManager(max_workers=8), archive_dir=tmp_path)
    jobs = []
    for index in range(8):
        jobs.append(manager.add(Job(str(index), [sys.executable, "-u", "-c",
            f"for n in range(700): print('X='+str({index}*10000+n))"], pty=False, max_log_lines=3), flow=flow))
    try:
        manager.start()
        assert manager._drain(20)
        for index, job in enumerate(jobs):
            values = [r["data"]["x"] for r in walk_flow(manager.archive, job.id, job.attempt_id, kind="record")]
            assert values == list(range(index * 10000, index * 10000 + 700))
            assert len(list(walk(manager.archive, job.id, job.attempt_id))) == 700
    finally:
        manager.close()


def test_retry_and_finished_automation_keep_original_attempt_evidence(tmp_path):
    marker = tmp_path / "attempt-count"
    code = ("from pathlib import Path;import sys;"
            f"p=Path({str(marker)!r});n=int(p.read_text()) if p.exists() else 0;"
            "p.write_text(str(n+1));print('N='+str(n));sys.exit(1 if n==0 else 0)")
    def finish(end, ctx):
        ctx.summary.result("FAIL" if end.execution["returncode"] else "N/A", "process exit", final=True)
    def notification(event, ctx):
        if event.name == "finished":
            ctx.debug("callback for " + event.data["attempt_id"])
            raise RuntimeError("notification only")
    flow = Flow("retry", parsers=[Parser("count", lambda line, ctx: {"n": int(line[2:])})],
                analyzers=[Analyzer("exit", on_finish=finish)],
                automations=[Automation("notification", on_event=notification)])
    manager, job = make(tmp_path / "archive", code=code, flow=flow, max_history=1)
    job.max_retries = 1
    try:
        manager.start()
        assert manager._drain(8)
        attempts = manager.archive.attempts(job.id)["rows"]
        assert len(attempts) == 2
        assert [a["result"] for a in attempts] == ["N/A", "FAIL"]
        for index, metadata in enumerate(reversed(attempts)):
            attempt = metadata["attempt_id"]
            snapshot = manager.archive.attempt(job.id, attempt)
            rows = manager.archive.flow_page(job.id, attempt, stage="parser")["rows"]
            assert [row["data"]["n"] for row in rows] == [index]
            notes = manager.archive.flow_page(job.id, attempt, stage="automation", kind="diagnostic")["rows"]
            assert any(row["data"]["message"] == "callback for " + attempt for row in notes)
            assert snapshot["error_count"] == 0
            assert snapshot["flow"]["automation_error_count"] == 1
            assert "notification only" in snapshot["flow"]["automations"][0]["error"]
    finally:
        manager.close()


def test_raw_elapsed_time_window_boundaries_are_inclusive(tmp_path):
    store = RunArchive(tmp_path / "time.sqlite3", create=True, run_id="time")
    store.save_attempt(dict(id="j", attempt_id="a", attempt_number=1))
    try:
        for seq, elapsed in enumerate((0., 1., 1., 2.), 1):
            store.append("a", dict(line_no=seq, time=100., source="stdout", text=str(seq)), elapsed)
        rows = store.page("j", "a", start=1., end=1.)["rows"]
        assert [r["seq"] for r in rows] == [2, 3]
        assert all(r["elapsed"] == 1. for r in rows)
        assert [r["seq"] for r in store.page("j", "a", around=2)["rows"]] == [1, 2, 3, 4]
    finally:
        store.close()


def test_raw_cursors_are_bounded_and_attempt_picker_is_compact(tmp_path):
    manager, job = make(tmp_path)
    try:
        for value in range(3):
            job._line(job._attempt, "X=" + str(value))
        page = manager.archive.page(job.id, job.attempt_id, limit=1)
        assert len(page["last"]) < 200
        next_page = manager.archive.page(job.id, job.attempt_id, limit=1, cursor=page["last"])
        assert next_page["rows"][0]["text"] == "X=1"
        metadata = manager.archive.attempts()["rows"][0]
        assert "data" not in metadata and metadata["id"] == job.id
    finally:
        manager.close()


def test_latest_flow_heads_come_from_committed_records_not_old_metadata(tmp_path):
    store = RunArchive(tmp_path / "snapshot.sqlite3", create=True, run_id="snapshot")
    try:
        store.save_attempt(dict(id="j", attempt_id="a", attempt_number=1, data={"x": 0}))
        store.append("a", dict(line_no=1, time=1., text="X=1", source="stdout"), 1.)
        assert store.append_flow("a", dict(seq=1, stage="parser", producer="x", kind="record", data={"x": 1}, evidence={"line_no": 1}))
        assert store.attempt("j", "a")["data"] == {"x": 0}
        assert store.flow_latest("j", "a", stage="parser")["rows"][0]["data"] == {"x": 1}
    finally:
        store.close()


def test_parser_diagnostic_does_not_publish_partial_input_group_to_disk_readers(tmp_path):
    bad = Parser("bad", lambda line, ctx: {"x": int(line.split()[0][2:])})
    good = Parser("good", lambda line, ctx: {"y": int(line.split()[1][2:])})
    manager, job = make(tmp_path, flow=Flow("atomic", parsers=[bad, good]))
    manager.archive.flush()
    manager.archive._last_commit = -1
    observed = []
    original = job._attempt.sink.flow_session._publish
    def inspect(publication):
        original(publication)
        if publication["kind"] == "diagnostic":
            reader = RunArchive(manager.archive.path)
            observed.append((reader.page(job.id, job.attempt_id)["rows"],
                             reader.flow_page(job.id, job.attempt_id)["rows"]))
    job._attempt.sink.flow_session._publish = inspect
    try:
        job._line(job._attempt, "X=invalid Y=7")
        assert observed == [([], [])]
        assert [r["text"] for r in manager.archive.page(job.id, job.attempt_id)["rows"]] == ["X=invalid Y=7"]
        assert manager.archive.flow_page(job.id, job.attempt_id, kind="record")["rows"][0]["data"] == {"y": 7}
    finally:
        manager.close()


def test_aborted_input_group_is_not_committed_or_labelled_complete(tmp_path):
    store = RunArchive(tmp_path / "aborted.sqlite3", create=True)
    store.save_attempt(dict(id="j", attempt_id="a", attempt_number=1), flush=True)
    store._last_commit = -1
    try:
        with pytest.raises(RuntimeError, match="interrupted input"):
            with store.record_batch():
                store.append("a", dict(line_no=2, time=1., source="wave", text="later"), 0.)
                raise RuntimeError("interrupted input")
        assert store.health["error"] and not store.health["complete"]
        assert store.page("j", "a")["rows"] == []
    finally:
        store.close()
    assert not RunArchive(store.path).health["complete"]


def test_json1_check_and_posix_nfs_mount_warning(tmp_path, monkeypatch):
    from rpkbin.wave import archive as archive_mod

    monkeypatch.setattr(archive_mod, "_json1_available", False)
    with pytest.raises(RuntimeError, match="JSON1.*json_extract"):
        RunArchive(tmp_path / "no_json1.sqlite3", create=True)
    monkeypatch.setattr(archive_mod, "_json1_available", None)

    mounts = (
        "proc /proc proc rw 0 0\n"
        "/dev/sda1 / ext4 rw 0 0\n"
        "filer:/vol/home /home/user nfs4 rw 0 0\n"
        "tmpfs /home/user/local tmpfs rw 0 0\n"
    )
    monkeypatch.setattr(archive_mod.os, "name", "posix")
    monkeypatch.setattr(archive_mod.os.path, "realpath", lambda p: str(p))
    assert archive_mod._network_filesystem("/home/user/proj/.archive", mounts) == "nfs4"
    assert archive_mod._network_filesystem("/home/user/local/archive", mounts) is None
    assert archive_mod._network_filesystem("/tmp/rpk-wave", mounts) is None

    monkeypatch.setattr(archive_mod, "_network_warned", False)
    monkeypatch.setattr(archive_mod, "_network_filesystem", lambda d: "nfs4")
    with pytest.warns(RuntimeWarning, match="RPK_WAVE_ARCHIVE_DIR=/tmp/"):
        archive_mod._warn_network_filesystem(tmp_path)
    # Warn only once per process.
    import warnings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        archive_mod._warn_network_filesystem(tmp_path)
    assert not caught


def test_b17_wal_writer_uses_synchronous_normal(tmp_path):
    store = RunArchive(tmp_path / "wal_normal.sqlite3", create=True)
    try:
        journal_mode = store._writer.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = store._writer.execute("PRAGMA synchronous").fetchone()[0]
        assert journal_mode.lower() == "wal"
        assert synchronous == 1  # 1 == NORMAL under WAL mode
    finally:
        store.close()
