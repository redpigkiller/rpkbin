"""Independent S12 checks with real files, deterministic polls, and SQLite faults."""

from contextlib import closing
import os
import random
import shutil
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

from rpkbin.flow import Flow, Parser
from rpkbin.wave import FileSource, RunArchive, TailSource
from rpkbin.wave.cli import main


class ScriptedPolls(Event):
    """Advance the producer at chosen EOF polls without sleeps or worker races."""

    def __init__(self, *steps):
        super().__init__()
        self.steps = iter(steps)
        self.count = 0

    def wait(self, timeout=None):
        self.count += 1
        step = next(self.steps, self.set)
        step()
        return self.is_set()


def _append(path, data):
    with path.open("ab") as stream:
        stream.write(data)


def _payload(count, *, seed, first=0):
    rng = random.Random(seed)
    lines = [
        f"{i:05d}:" + "".join(rng.choices("azé中🙂", k=rng.randrange(1, 220)))
        for i in range(first, first + count)
    ]
    data = "".join(line + rng.choice(("\r\n", "\n")) for line in lines)
    return lines, data.encode("utf-8")


def _read_saved(result):
    ids = result.snapshot["id"], result.snapshot["attempt_id"]
    with closing(RunArchive(result.archive_path)) as store:
        saved = store.attempt(*ids)
    # Read exact BLOBs without page/preview limits, including runs over 256 rows.
    with closing(sqlite3.connect(result.archive_path.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT seq,text FROM records WHERE attempt_id=? ORDER BY seq", (ids[1],)
        ).fetchall()
        pubs = db.execute(
            "SELECT count(*) FROM flow_records WHERE attempt_id=? AND kind='record'", (ids[1],)
        ).fetchone()[0]
    assert [seq for seq, _ in rows] == list(range(1, len(rows) + 1))
    assert saved["total_lines"] == len(rows)
    return saved, [data.decode("utf-8") for _, data in rows], pubs


@pytest.mark.parametrize("mtime", ["older", "equal"])
def test_stale_done_is_never_a_current_completion(tmp_path, mtime):
    log, done = tmp_path / "log", tmp_path / "done"
    done.write_bytes(b"")
    log.write_bytes(b"working\n")
    timestamp = 1_700_000_000_000_000_000
    os.utime(log, ns=(timestamp, timestamp))
    stale = timestamp if mtime == "equal" else timestamp - 2_000_000_000
    os.utime(done, ns=(stale, stale))

    result = TailSource(log, completion_file=done, idle_timeout=0).run(
        Flow("verify"), archive_dir=tmp_path)
    saved, lines, _ = _read_saved(result)
    assert lines == ["working"]
    assert (result.complete, result.reason, saved["flow"]["complete"]) == (
        False, "idle_timeout", False)


@pytest.mark.parametrize("delay", ["before_handoff", "after_handoff", "after_grace"])
def test_rename_create_captures_old_fd_writes(tmp_path, monkeypatch, delay):
    from rpkbin.wave import sources

    log, rotated = tmp_path / "log", tmp_path / "log.1"
    log.write_bytes(b"old\n")
    observed = []
    now = [0.0]
    monkeypatch.setattr(sources, "time", SimpleNamespace(monotonic=lambda: now[0], time=sources.time.time))

    def rotate():
        log.rename(rotated)
        log.write_bytes(b"new\nEND\n")
        if delay == "before_handoff":
            late_write()

    def late_write():
        _append(rotated, b"late-old\n")

    def parse(line, _ctx):
        observed.append(line)
        if delay == "after_handoff" and line == "new":
            late_write()

    def finish_grace():
        assert observed[:2] == (["old", "late-old"] if delay == "before_handoff" else ["old", "new"])
        now[0] = 4.0

    def finish_idle():
        if delay == "after_grace":
            late_write()
        now[0] = 10.0

    # In the last case, use an idle deadline so the watch remains alive after
    # releasing the old fd and can observe the test's late write.
    use_marker = delay != "after_grace"
    polls = ScriptedPolls(rotate, lambda: None, finish_grace, finish_idle)
    result = TailSource(log, end_marker="END" if use_marker else None,
                        idle_timeout=None if use_marker else 5, chunk_size=3).run(
        Flow("verify", parsers=[Parser("observe", parse)]),
        archive_dir=tmp_path, stop_event=polls)
    saved, lines, _ = _read_saved(result)
    assert not result.complete and not saved["flow"]["complete"]
    assert any(d["source"] == "discontinuity" for d in saved["diagnostics"])
    assert rotated.read_bytes() == b"old\nlate-old\n"
    if delay == "before_handoff":
        assert result.reason == "discontinuity"
        assert lines == observed == ["old", "late-old", "new", "END"]
    elif delay == "after_handoff":
        assert result.reason == "discontinuity" and polls.count >= 3
        assert lines == observed == ["old", "new", "END", "late-old"]
    else:
        assert lines == observed == ["old", "new", "END"]
        assert result.reason == "idle_timeout"


def test_rotation_with_final_offset_drains_original_handle(tmp_path, monkeypatch):
    from rpkbin.wave import sources

    log, rotated = tmp_path / "log", tmp_path / "log.1"
    log.write_bytes(b"old\n")
    now = [0.0]
    monkeypatch.setattr(sources, "time", SimpleNamespace(monotonic=lambda: now[0], time=sources.time.time))

    def rotate():
        log.rename(rotated)
        log.write_bytes(b"new\n")

    polls = ScriptedPolls(rotate, lambda: _append(rotated, b"late-old\n"),
                          lambda: now.__setitem__(0, 4.0))
    result = TailSource(log, final_offset=8, chunk_size=3).run(
        Flow("verify"), archive_dir=tmp_path, stop_event=polls)
    saved, lines, _ = _read_saved(result)
    assert lines == ["old", "late-old"]
    assert saved["source_descriptor"]["end_offset"] == len(b"old\nlate-old\n")
    assert not result.complete and result.reason == "discontinuity"
    assert any(d["source"] == "discontinuity" for d in saved["diagnostics"])


@pytest.mark.parametrize("regrow", ["visible", "same_fingerprint"])
def test_copytruncate_captures_both_generations(tmp_path, regrow):
    log, rotated = tmp_path / "log", tmp_path / "log.1"
    if regrow == "visible":
        previous, current = b"old\n", b"new\n"
    else:
        # The first line changes but the old offset's last 64 bytes match.
        shared = b"x" * 64 + b"\n"
        previous, current = b"old\n" + shared, b"new\n" + shared
    log.write_bytes(previous)

    def copytruncate():
        shutil.copyfile(log, rotated)
        log.write_bytes(b"")
        if regrow == "same_fingerprint":
            _append(log, current + b"END\n")

    def append_new():
        if regrow == "visible":
            _append(log, current + b"END\n")

    result = TailSource(log, end_marker="END", chunk_size=7).run(
        Flow("verify"), archive_dir=tmp_path,
        stop_event=ScriptedPolls(copytruncate, append_new))
    _, lines, _ = _read_saved(result)
    assert rotated.read_bytes() == previous
    assert (lines, result.complete, result.reason) == (
        (previous + current + b"END\n").decode().splitlines(), False, "discontinuity")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le"])
@pytest.mark.parametrize("separator", ["\r", "\r\n", "\n"], ids=["CR", "CRLF", "LF"])
def test_marker_at_multibyte_boundary(tmp_path, encoding, separator):
    log = tmp_path / "log"
    marker = "終🙂"
    prefix = "é中\r\n🙂\r" + marker + separator
    payload = prefix.encode(encoding)
    log.write_bytes(payload)
    result = TailSource(log, encoding=encoding, end_marker=marker, decode_errors="strict",
                        chunk_size=3, idle_timeout=0).run(Flow("verify"), archive_dir=tmp_path)
    saved, lines, _ = _read_saved(result)
    assert (result.complete, result.reason) == (True, "end_marker")
    assert lines == ["é中", "🙂", marker]
    assert saved["source_descriptor"]["end_offset"] == len(payload)


def test_marker_waits_for_crlf_split_across_multiple_polls(tmp_path):
    log = tmp_path / "log"
    initial = b"END\r"
    log.write_bytes(initial)
    polls = ScriptedPolls(lambda: None, lambda: _append(log, b"\n"))
    result = TailSource(log, end_marker="END", chunk_size=1).run(
        Flow("verify"), archive_dir=tmp_path, stop_event=polls)
    saved, lines, _ = _read_saved(result)
    assert (result.complete, result.reason, lines) == (True, "end_marker", ["END"])
    assert (polls.count, saved["source_descriptor"]["end_offset"]) == (2, 5)


@pytest.mark.parametrize("source", [FileSource, TailSource], ids=["file", "tail"])
def test_stop_event_preserves_delivered_rows_and_flow_across_batches(tmp_path, source):
    log = tmp_path / "log"
    lines, data = _payload(520, seed=4249)
    rest, later = _payload(1600, seed=5017, first=len(lines))
    partial = "界".encode("utf-8")[:1]
    log.write_bytes(data + partial + "界".encode("utf-8")[1:] + later)
    stop, delivered = Event(), []

    def parse(line, _ctx):
        delivered.append(line)
        if len(delivered) == 300:
            stop.set()
        return {"ordinal": len(delivered)}

    result = source(log, chunk_size=len(data) + len(partial)).run(
        Flow("verify", parsers=[Parser("observe", parse)]), archive_dir=tmp_path, stop_event=stop)
    saved, stored, publications = _read_saved(result)
    assert (result.complete, result.reason, saved["flow"]["complete"]) == (
        False, "user_stopped", False)
    assert stored == delivered == lines
    assert publications == len(lines)
    assert saved["source_descriptor"]["end_offset"] == len(data)
    assert not set(stored).intersection(rest)


@pytest.mark.parametrize("once", [False, True])
@pytest.mark.parametrize("phase", [
    "second_flow_insert",
    "close_commit",
])
def test_cli_archive_write_failure_has_no_saved_success(tmp_path, monkeypatch, capsys, phase, once):
    log, definition = tmp_path / "log", tmp_path / "flow.py"
    archive_dir = tmp_path / "archive"
    _, data = _payload(960, seed=8831)
    log.write_bytes(data)
    definition.write_text(
        "from rpkbin.flow import Flow, Parser\n"
        "flow = Flow('verify', parsers=[Parser('line', lambda line, ctx: {'line': line})])\n",
        encoding="utf-8",
    )
    connect = sqlite3.connect
    injected = Event()

    class DiskFull(sqlite3.Connection):
        flow_batches = 0
        closing_marker = False

        def executemany(self, sql, rows):
            if sql.startswith("INSERT INTO flow_records"):
                self.flow_batches += 1
                if phase == "second_flow_insert" and self.flow_batches == 2:
                    injected.set()
                    raise sqlite3.OperationalError("database or disk is full")
            return super().executemany(sql, rows)

        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE metadata SET value='true' WHERE key='closed_cleanly'"):
                self.closing_marker = True
            return super().execute(sql, parameters)

        def commit(self):
            if phase == "close_commit" and self.closing_marker:
                injected.set()
                raise sqlite3.OperationalError("database or disk is full")
            return super().commit()

    args = ["logs", str(log), "--headless", "--flow", str(definition), "--archive-dir", str(archive_dir)]
    if once:
        args += ["--once"]
    else:
        args += ["--final-offset", str(len(data))]
    with monkeypatch.context() as scoped:
        scoped.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=DiskFull, **kw))
        code = main(args)
    output = capsys.readouterr()
    assert injected.is_set()
    assert code == 2
    assert "database or disk is full" in output.err and "Archive:" not in output.out

    archive, = archive_dir.glob("*.sqlite3")
    with closing(RunArchive(archive)) as store:
        assert not store.metadata["closed_cleanly"]
        rows = store.attempts()["rows"]
        assert len(rows) == 1 and rows[0]["verified"] is False
        saved = store.attempt(rows[0]["id"], rows[0]["attempt_id"])
    with closing(connect(archive)) as db:
        raw_count, = db.execute("SELECT count(*) FROM records").fetchone()
        flow_count, = db.execute("SELECT count(*) FROM flow_records WHERE kind='record'").fetchone()
    assert raw_count == flow_count
    assert saved["verified"] is False
    assert (saved["status"], saved["flow"]["complete"]) != ("DONE", True)


@pytest.mark.parametrize("mutation", ["same_size_rewrite", "same_size_mtime_replace"])
def test_offline_large_input_mutation_is_incomplete(tmp_path, mutation):
    log, backup = tmp_path / "log", tmp_path / "log.1"
    lines, data = _payload(1800, seed=3329)
    assert len(data) > 256 * 1024
    log.write_bytes(data)
    delivered = []

    def parse(line, _ctx):
        delivered.append(line)
        if len(delivered) != 420:
            return
        before = log.stat()
        changed = b"X" + data[1:]
        if mutation == "same_size_rewrite":
            with log.open("r+b") as stream:
                stream.write(changed[:1])
            os.utime(log, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
        else:
            log.rename(backup)
            log.write_bytes(changed)
            os.utime(log, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert log.stat().st_size == before.st_size

    result = FileSource(log, chunk_size=8191).run(
        Flow("verify", parsers=[Parser("observe", parse)]), archive_dir=tmp_path)
    saved, stored, _ = _read_saved(result)
    assert len(delivered) >= 420
    assert (result.complete, result.reason, saved["flow"]["complete"]) == (
        False, "file_changed", False)
    assert stored == delivered == lines[:len(delivered)]
    assert len(delivered) < len(lines)

def test_completion_marker_change_can_preserve_mtime_and_size(tmp_path):
    log, done = tmp_path / "log", tmp_path / "done"
    log.write_bytes(b"line\n")
    done.write_bytes(b"old")
    timestamp = log.stat().st_mtime_ns
    os.utime(done, ns=(timestamp, timestamp))

    def publish():
        done.write_bytes(b"new")
        os.utime(done, ns=(timestamp, timestamp))

    polls = ScriptedPolls(publish)
    result = TailSource(log, completion_file=done).run(
        Flow("verify"), archive_dir=tmp_path, stop_event=polls)
    assert polls.count == 1
    assert result.complete and result.reason == "completion_file"
