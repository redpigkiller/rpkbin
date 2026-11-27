"""Regressions from the Stage 1 Workspace and Archive review."""

from contextlib import closing
import sqlite3
import subprocess
import sys
from threading import Event, Thread, current_thread
import time

import pytest

from rpkbin.flow import Flow, Parser
from rpkbin.wave import FileSource, RunArchive, TailSource, WaveWorkspace
from rpkbin.wave import _source_discovery
from rpkbin.wave.cli import main
from rpkbin.wave.web import WaveServer
from test_archive_api import archive
from test_workspace_sources import _wait_for


def _flow():
    return Flow("capture", parsers=[Parser("line", lambda line, ctx: {"line": line})])


@pytest.mark.parametrize("with_flow", [False, True])
@pytest.mark.parametrize("encoding,newline", [("utf-8", "\n"), ("utf-16", "\r\n")])
def test_live_http_and_independent_archive_share_a_committed_prefix(tmp_path, with_flow, encoding, newline):
    path = tmp_path / "input.log"
    path.write_bytes(newline.join(("one", "held", "last", "partial")).encode(encoding))
    workspace = WaveWorkspace(archive_dir=tmp_path)
    job = workspace.add_source(TailSource(path, commit_interval=60, encoding=encoding),
                               flow=_flow() if with_flow else None)
    entered, release = Event(), Event()

    def hold(record):
        if record["text"] == "held":
            entered.set()
            release.wait(8)

    workspace.add_log_listener(hold)
    server = WaveServer(workspace)
    server.start()
    try:
        workspace.start()
        assert entered.wait(5)
        target = dict(job_id=job.id, attempt_id=job.attempt_id)
        expected = ["one"] if with_flow else ["one", "held"]
        status, detail = archive(server, view="attempt", **target)
        assert status == 200
        status, raw = archive(server, view="reader", **target)
        assert status == 200
        with closing(RunArchive(workspace.archive.path)) as store, store.replay(job.attempt_id) as (saved, lines):
            assert list(lines) == [row["text"] for row in raw["rows"]] == expected
            for row in (saved, detail["attempt"]):
                assert row["total_lines"] == len(expected)
                assert row["source_descriptor"]["end_offset"] == len((newline.join(expected) + newline).encode(encoding))
            if with_flow:
                assert saved["data"]["line.line"] == expected[-1]
                assert [row["data"]["line"] for row in store.flow_page(**target, kind="record")["rows"]] == expected
    finally:
        release.set()
        server.stop()
        workspace.close()


@pytest.mark.parametrize("with_flow", [False, True])
def test_reopen_after_abnormal_exit_preserves_http_checkpoint(tmp_path, with_flow):
    code = r"""
import os
from pathlib import Path
import sys
from threading import Event
from rpkbin.wave import WaveWorkspace, TailSource
from rpkbin.wave.web import WaveServer
from rpkbin.flow import Flow, Parser
sys.path.insert(0, str(Path('tests/wave').resolve()))
from test_archive_api import archive

directory = Path(sys.argv[1])
path = directory / 'held.log'
path.write_bytes(b'one\nheld\nlast\n')
workspace = WaveWorkspace(archive_dir=directory)
flow = Flow('flow', parsers=[Parser('capture', lambda line, ctx: {'line': line})]) if sys.argv[2] == 'True' else None
job = workspace.add_source(TailSource(path, commit_interval=60), flow=flow)
entered = Event()
def hold(record):
    if record['text'] == 'held':
        entered.set()
        Event().wait()
workspace.add_log_listener(hold)
server = WaveServer(workspace)
server.start()
workspace.start()
assert entered.wait(5)
status, response = archive(server, view='attempt', job_id=job.id, attempt_id=job.attempt_id)
assert status == 200, response
os._exit(17)
"""
    process = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path), str(with_flow)],
                             capture_output=True, timeout=15)
    assert process.returncode == 17, process.stderr
    path, = tmp_path.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        row, = store.attempts()["rows"]
        with store.replay(row["attempt_id"]) as (saved, lines):
            expected = ["one"] if with_flow else ["one", "held"]
            assert list(lines) == expected
            assert not saved["verified"]
            assert saved["total_lines"] == len(expected)
            assert saved["source_descriptor"]["end_offset"] == sum(len(line) + 1 for line in expected)


@pytest.mark.parametrize("with_flow", [False, True])
def test_failed_live_flush_keeps_previous_committed_snapshot(tmp_path, monkeypatch, with_flow):
    path = tmp_path / "input.log"
    path.write_bytes(b"lost\nheld\n")
    workspace = WaveWorkspace(archive_dir=tmp_path)
    job = workspace.add_source(TailSource(path, commit_interval=60), flow=_flow() if with_flow else None)
    entered, release = Event(), Event()

    def hold(record):
        if record["text"] == "held":
            entered.set()
            release.wait(8)

    def fail(store, attempt_id, records, publications, interrupted):
        # Simulate failure after staging rows, before the outer commit.
        write(store, attempt_id, records, publications, interrupted)
        return False

    workspace.add_log_listener(hold)
    server = WaveServer(workspace)
    server.start()
    try:
        workspace.start()
        assert entered.wait(5)
        write = RunArchive._write_file_batch
        monkeypatch.setattr(RunArchive, "_write_file_batch", fail)
        status, detail = archive(server, view="attempt", job_id=job.id, attempt_id=job.attempt_id)
        assert status == 200 and detail["health"]["error"]
        status, _ = archive(server, view="reader", job_id=job.id, attempt_id=job.attempt_id)
        assert status == 200
        with closing(RunArchive(workspace.archive.path)) as store, store.replay(job.attempt_id) as (saved, lines):
            assert saved["total_lines"] == saved["source_descriptor"]["end_offset"] == 0
            assert not list(lines)
    finally:
        release.set()
        server.stop()
        workspace.close()


def test_watcher_recovers_when_directory_is_replaced_by_file(tmp_path, monkeypatch):
    watched = tmp_path / "watched"
    watched.mkdir()
    scanning, resume, replaced = Event(), Event(), Event()
    scan_dir = type(watched).iterdir

    def observe(path):
        if path == watched and current_thread().name == "Wave-source-discovery":
            scanning.set()
            assert resume.wait(5)
        try:
            yield from scan_dir(path)
        except NotADirectoryError:
            if path == watched:
                replaced.set()
            raise

    monkeypatch.setattr(type(watched), "iterdir", observe)
    workspace = WaveWorkspace(archive_dir=tmp_path)
    try:
        workspace.add_paths([watched], poll_interval=.01)
        workspace.start()
        assert scanning.wait(5)
        watched.rename(tmp_path / "moved")
        watched.touch()
        resume.set()
        assert replaced.wait(5)
        watched.unlink()
        watched.mkdir()
        (watched / "new.log").write_bytes(b"new line\n")
        job = _wait_for(lambda: workspace.get_job("new.log"))
        _wait_for(lambda: job.to_snapshot_dict()["total_lines"] == 1)
    finally:
        resume.set()
        workspace.close()
    assert workspace.archive.health["closed_cleanly"]


def test_watcher_fatal_error_wakes_drain_with_a_live_reader(tmp_path, monkeypatch):
    source = tmp_path / "base.log"
    source.write_bytes(b"base\n")
    fail_scan = Event()
    scan = _source_discovery._scan

    def fail(*args):
        if fail_scan.is_set():
            raise RuntimeError("fatal scan")
        yield from scan(*args)

    monkeypatch.setattr(_source_discovery, "_scan", fail)
    workspace = WaveWorkspace(archive_dir=tmp_path)
    try:
        job, = workspace.add_paths([tmp_path], poll_interval=.01)
        workspace.start()
        _wait_for(lambda: job.to_snapshot_dict()["total_lines"] == 1)
        before = time.monotonic()
        fail_scan.set()
        with pytest.raises(RuntimeError, match="fatal scan"):
            workspace._drain(2)
        assert time.monotonic() - before < 1
        assert "fatal scan" in workspace.snapshot()["watcher_error"]
        assert workspace._source_tasks[job.id].thread.is_alive()
    finally:
        workspace.close()
    assert not any(task.thread.is_alive() for task in (*workspace._source_tasks.values(), *workspace._watchers))


def test_transient_entry_error_does_not_skip_matching_siblings(tmp_path, monkeypatch):
    locked = tmp_path / "locked.log"
    other = tmp_path / "other.log"
    is_file = type(tmp_path).is_file
    unlock = Event()

    def check(path):
        if path == locked and not unlock.is_set():
            raise OSError("temporary stat failure")
        return is_file(path)

    monkeypatch.setattr(type(tmp_path), "is_file", check)
    workspace = WaveWorkspace(archive_dir=tmp_path)
    try:
        workspace.add_paths([tmp_path], poll_interval=.01)
        workspace.start()
        locked.touch()
        other.touch()
        _wait_for(lambda: workspace.get_job(other.name))
        assert not workspace.get_job(locked.name)
        unlock.set()
        _wait_for(lambda: workspace.get_job(locked.name))
    finally:
        workspace.close()
    assert workspace.archive.health["closed_cleanly"]


def test_whitespace_glob_is_rejected(tmp_path, capsys):
    (tmp_path / "input.log").touch()
    assert main(["logs", str(tmp_path), "--headless", "--once", "--glob", "*.log", "--glob", " \t "]) == 2
    assert "patterns" in capsys.readouterr().err


@pytest.mark.parametrize("dynamic", [False, True])
def test_reader_launch_failure_finalizes_and_closes(tmp_path, monkeypatch, dynamic):
    workspace = WaveWorkspace(archive_dir=tmp_path)
    first = workspace.add_source(TailSource(tmp_path / "first.log", poll_interval=.01))
    if dynamic:
        workspace.start()
    else:
        second = workspace.add_source(TailSource(tmp_path / "second.log"))
    start = Thread.start

    def fail(thread):
        if thread.name.startswith("Wave-source-") and thread.name != f"Wave-source-{first.id}":
            raise RuntimeError("can't start new thread")
        return start(thread)

    monkeypatch.setattr(Thread, "start", fail)
    try:
        with pytest.raises(RuntimeError, match="can't start new thread"):
            if dynamic:
                workspace.add_source(TailSource(tmp_path / "second.log"))
            else:
                workspace.start()
    finally:
        workspace.close()
    if dynamic:
        _, second = workspace.jobs
    assert workspace.manager._closed and not workspace.manager._scheduler.is_alive()
    assert not any(task.thread.is_alive() for task in workspace._source_tasks.values())
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        workspace.archive._writer.execute("SELECT 1")
    saved = workspace.archive.attempt(second.id, second.attempt_id)
    assert saved["finalized"] and saved["error_count"] == 1
    assert saved["completion_reason"] == saved["errors"][0]["source"] == "source_reader"


def test_first_discovered_source_is_finalized_if_scheduler_cannot_start(tmp_path, monkeypatch):
    watched = tmp_path / "watched"
    watched.mkdir()
    workspace = WaveWorkspace(archive_dir=tmp_path / "archives")
    workspace.add_paths([watched], poll_interval=.01)
    workspace.start()
    start = Thread.start

    def fail_scheduler(thread):
        if thread.name == "Wave-scheduler":
            raise RuntimeError("can't start new thread")
        start(thread)

    monkeypatch.setattr(Thread, "start", fail_scheduler)
    try:
        (watched / "first.log").write_bytes(b"first\n")
        with pytest.raises(RuntimeError, match="can't start new thread"):
            workspace._drain(5)
        job, = workspace.jobs
    finally:
        before = time.monotonic()
        workspace.close()
        assert time.monotonic() - before < 2
    assert workspace.manager._closed and not workspace.manager.started
    assert workspace.manager._scheduler is None
    assert not any(task.thread.is_alive() for task in workspace._watchers)
    assert job.status.value == "FAILED" and not job._attempt.reserved
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        workspace.archive._writer.execute("SELECT 1")
    saved = workspace.archive.attempt(job.id, job.attempt_id)
    assert saved["finalized"] and saved["error_count"] == 1
    assert saved["completion_reason"] == saved["errors"][0]["source"] == "source_reader"
    assert "can't start new thread" in saved["errors"][0]["message"]


@pytest.mark.parametrize("registered_task", [True, False])
def test_closing_preview_never_launches_reader_or_watcher(tmp_path, monkeypatch, registered_task):
    (tmp_path / "preview.log").touch()
    workspace = WaveWorkspace(archive_dir=tmp_path)
    job, = workspace.add_paths([tmp_path])
    if not registered_task:
        workspace._source_tasks.pop(job.id)

    def unexpected_start(thread):
        pytest.fail("Preview close created a thread")

    monkeypatch.setattr(Thread, "start", unexpected_start)
    workspace.close()
    saved = workspace.archive.attempt(job.id, job.attempt_id)
    assert saved["status"] == "CANCELLED" and saved["finalized"] and saved["verified"]


@pytest.mark.parametrize("terminator", [b"", b"-"])
@pytest.mark.parametrize("decode_errors", ["strict", "replace"])
@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
def test_utf7_file_source_archives_an_eof_decoded_newline(tmp_path, terminator, decode_errors, chunk_size):
    data = b"head\nhello+AAo" + terminator
    path = tmp_path / "input.log"
    path.write_bytes(data)
    result = FileSource(path, encoding="utf-7", decode_errors=decode_errors, chunk_size=chunk_size).run(
        archive_dir=tmp_path)
    assert result.complete and result.reason == "eof", result.snapshot["errors"]
    with closing(RunArchive(result.archive_path)) as store, store.replay(result.snapshot["attempt_id"]) as (saved, lines):
        assert list(lines) == ["head", "hello"]
        assert saved["total_lines"] == 2
        assert saved["source_descriptor"]["end_offset"] == len(data)


@pytest.mark.parametrize("terminator", [b"", b"-"])
@pytest.mark.parametrize("decode_errors", ["strict", "replace"])
def test_utf7_logs_cli_archives_an_eof_decoded_newline(tmp_path, terminator, decode_errors, capsys):
    data = b"head\nhello+AAo" + terminator
    path = tmp_path / "input.log"
    path.write_bytes(data)
    out = tmp_path / "out"
    assert main(["logs", str(path), "--once", "--headless", "--encoding", "utf-7",
                 "--decode-errors", decode_errors, "--archive-dir", str(out)]) == 0
    assert not capsys.readouterr().err
    archived, = out.glob("*.sqlite3")
    with closing(RunArchive(archived)) as store:
        row, = store.attempts()["rows"]
        with store.replay(row["attempt_id"]) as (saved, lines):
            assert list(lines) == ["head", "hello"]
            assert saved["total_lines"] == 2 and saved["verified"]
            assert saved["source_descriptor"]["end_offset"] == len(data)


def test_close_still_saves_and_closes_writer_if_core_cleanup_raises(tmp_path, monkeypatch):
    workspace = WaveWorkspace(archive_dir=tmp_path)
    job = workspace.add_source(TailSource(tmp_path / "pending.log"))
    close = workspace.manager.close
    save = workspace._save_current_attempts
    saved = Event()

    def fail_close():
        close()
        raise RuntimeError("core close failure")

    def fail_save():
        save()
        saved.set()
        raise sqlite3.OperationalError("save failure")

    monkeypatch.setattr(workspace.manager, "close", fail_close)
    monkeypatch.setattr(workspace, "_save_current_attempts", fail_save)
    with pytest.raises(sqlite3.OperationalError, match="save failure"):
        workspace.close()
    assert workspace.manager._closed and saved.is_set()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        workspace.archive._writer.execute("SELECT 1")
    assert workspace.archive.attempt(job.id, job.attempt_id)["finalized"]
