"""File sources exercise real files and the shared Flow/SQLite sink."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event

import pytest

from rpkbin.flow import Analyzer, Automation, Flow, Parser
from rpkbin.wave import RunArchive
from rpkbin.wave.sources import FileSource, TailSource


def lines_from(result):
    with_archive = RunArchive(result.archive_path)
    try:
        return [row["text"] for row in with_archive.page(
            result.snapshot["id"], result.snapshot["attempt_id"])["rows"]]
    finally:
        with_archive.close()


def test_offline_chunk_boundaries_and_archive(tmp_path):
    path = tmp_path / "input.log"
    path.write_bytes("頭\r\nmiddle\rTAIL".encode())
    seen = []
    flow = Flow("capture", parsers=[Parser("line", lambda line, ctx: seen.append(line) or {"line": line})])
    result = FileSource(path, chunk_size=1).run(flow, archive_dir=tmp_path)
    assert result.complete and result.snapshot["flow"]["complete"]
    assert seen == lines_from(result) == ["頭", "middle", "TAIL"]
    assert result.snapshot["pid"] is None and result.snapshot["returncode"] is None
    assert result.snapshot["can_rerun"] is False
    assert not any(vars(result.capabilities).values())
    archive = RunArchive(result.archive_path)
    try:
        job_id, attempt_id = result.snapshot["id"], result.snapshot["attempt_id"]
        saved = archive.attempt(job_id, attempt_id)
        assert archive.metadata["schema_version"] == 4
        assert saved["source_kind"] == "file"
        assert saved["source_descriptor"] == {
            "version": 1, "path": str(path), "encoding": "utf-8", "decode_errors": "replace",
            "start_offset": 0, "end_offset": path.stat().st_size, "generation": 0,
            "completion_reason": "eof", "time_basis": "ingested",
        }
        assert archive.attempts(job_id)["rows"][0]["source_kind"] == "file"
        assert len(archive.flow_page(job_id, attempt_id, kind="record")["rows"]) == 3
    finally:
        archive.close()


def test_offline_mutation_is_incomplete_and_automation_disabled(tmp_path):
    path = tmp_path / "input.log"
    path.write_text("first\nsecond\n", encoding="utf-8")
    calls = []
    def parse(line, ctx):
        with path.open("ab") as stream:
            stream.write(b"new\n")
    flow = Flow("change", parsers=[Parser("change", parse)], automations=[
        Automation("inactive", on_event=lambda *args: calls.append(args)),
    ])
    result = FileSource(path, chunk_size=6).run(flow, archive_dir=tmp_path)
    assert not result.complete and result.reason == "file_changed"
    assert not calls and lines_from(result) == ["first"]


class Polls:
    """Step past temporary EOF only when the producer has changed the file."""

    def __init__(self):
        self.waiting = Event()
        self.resume = Event()
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waiting.set()
        assert self.resume.wait(10)
        self.resume.clear()
        return self.stopped


@pytest.mark.parametrize("rotate", [False, True])
def test_tail_generation_never_joins_partial_lines(tmp_path, rotate):
    path = tmp_path / "tail.log"
    path.write_bytes(b"old\nuncommitted")
    poll = Polls()
    source = TailSource(path, end_marker="END", chunk_size=2, rotation_grace=0)
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(source.run, Flow("tail"), archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(10) and not result.done()
            if rotate:
                os.replace(path, path.with_suffix(".1"))
            path.write_bytes(b"new\nEND\n")
            poll.resume.set()
            saved = result.result(10)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert lines_from(saved) == ["old", "new", "END"]
    assert saved.snapshot["source_descriptor"]["generation"] == 1
    assert not saved.complete
    assert any(d["source"] == "discontinuity" for d in saved.snapshot["diagnostics"])


def test_tail_waits_at_eof_and_rejects_automation_control(tmp_path):
    path = tmp_path / "tail.log"
    path.write_bytes("頭".encode() + b"\r")
    poll = Polls()
    receipts = []
    def started(event, ctx):
        if event.name == "started":
            receipts.append(ctx.actions.send("text"))
    source = TailSource(path, end_marker="END", chunk_size=1)
    flow = Flow("live", automations=[Automation("try", on_event=started)])
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(source.run, flow, archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(10) and not result.done()
            with path.open("ab") as stream:
                stream.write(b"\nEND\nignored\n")
            poll.resume.set()
            saved = result.result(10)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert saved.complete and saved.reason == "end_marker"
    assert lines_from(saved) == ["頭", "END"]
    assert saved.snapshot["source_descriptor"]["end_offset"] == len("頭\r\nEND\n".encode())
    assert [(r.status, r.reason) for r in receipts] == [("rejected", "no control channel")]


@pytest.mark.parametrize("mode", ["offset", "completion_file", "idle", "stop"])
def test_tail_completion_boundaries(tmp_path, mode):
    path = tmp_path / "tail.log"
    path.write_bytes(b"old\nlast")
    marker = tmp_path / "done"
    kwargs = {}
    stop = Event()
    if mode == "offset":
        kwargs["final_offset"] = 8
    elif mode == "completion_file":
        class PublishCompletion(Event):
            def wait(self, timeout=None):
                marker.touch()
                return False

        stop = PublishCompletion()
        kwargs["completion_file"] = marker
    elif mode == "idle":
        kwargs["idle_timeout"] = 0
    else:
        stop.set()
    result = TailSource(path, **kwargs).run(Flow("tail"), archive_dir=tmp_path, stop_event=stop)
    assert result.complete == (mode in ("offset", "completion_file"))
    assert result.reason == {"offset": "final_offset", "completion_file": "completion_file",
                             "idle": "idle_timeout", "stop": "user_stopped"}[mode]
    assert lines_from(result) == ([] if mode == "stop" else ["old"] if mode == "idle" else ["old", "last"])


def test_tail_start_end_discards_prefix_fragment(tmp_path):
    path = tmp_path / "tail.log"
    path.write_bytes("舊\n殘".encode()[:-1])
    original_size = path.stat().st_size
    poll = Polls()
    source = TailSource(path, start="end", end_marker="END", chunk_size=1)
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(source.run, Flow("tail"), archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(10)
            with path.open("ab") as stream:
                stream.write("殘".encode()[-1:] + b"\nnew\nEND\n")
            poll.resume.set()
            saved = result.result(10)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert saved.complete and lines_from(saved) == ["new", "END"]
    assert saved.snapshot["source_descriptor"]["start_offset"] == original_size


@pytest.mark.parametrize("policy", ["replace", "strict"])
def test_file_decode_policy(tmp_path, policy):
    path = tmp_path / "input.log"
    path.write_bytes(b"before\nbad\xff\n")
    result = FileSource(path, chunk_size=2, decode_errors=policy).run(Flow("file"), archive_dir=tmp_path)
    assert result.complete == (policy == "replace")
    lines = lines_from(result)
    assert lines[0] == "before"
    if policy == "replace":
        assert result.snapshot["output_decode_count"] == 1 and lines[-1] == "bad\ufffd"
    else:
        assert result.reason == "output_decode"


def test_tail_clock_and_replacement_drains_old_handle(tmp_path, monkeypatch):
    from rpkbin.wave import sources

    path = tmp_path / "tail.log"
    path.write_bytes(b"old\n")
    poll = Polls()
    now = [10.0]
    ticks = []
    import time
    from types import SimpleNamespace
    # Isolate the source clock from executor and archive timing.
    monkeypatch.setattr(sources, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time))
    flow = Flow("clock", analyzers=[
        Analyzer("clock", every=1, on_tick=lambda tick, ctx: ticks.append(tick.elapsed)),
    ])
    source = TailSource(path, end_marker="END", chunk_size=2, rotation_grace=0)
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(source.run, flow, archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(10)
            with path.open("ab") as stream:
                stream.write(b"old-tail\n")
            replacement = path.with_suffix(".new")
            replacement.write_bytes(b"new\nEND\n")
            if os.name == "nt":
                os.rename(path, path.with_suffix(".1"))
            os.replace(replacement, path)
            now[0] += 2
            poll.resume.set()
            saved = result.result(10)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert not saved.complete and saved.reason == "discontinuity"
    assert lines_from(saved) == ["old", "old-tail", "new", "END"]
    assert ticks and ticks[0] >= 1
    assert saved.snapshot["source_descriptor"]["generation"] == 1


def test_tail_detects_truncation_that_regrows_past_offset(tmp_path):
    path = tmp_path / "tail.log"
    path.write_bytes(b"old\n")
    poll = Polls()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(TailSource(path, end_marker="END").run, Flow("tail"),
                             archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(10)
            path.write_bytes(b"longer-new-line\nEND\n")
            poll.resume.set()
            saved = future.result(10)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert not saved.complete and saved.reason == "discontinuity"
    assert lines_from(saved) == ["old", "longer-new-line", "END"]


def test_tail_marker_offset_after_lone_cr_with_utf16(tmp_path):
    path = tmp_path / "tail.log"
    import sys
    path.write_bytes("one\r".encode("utf-16"))
    poll = Polls()
    source = TailSource(path, encoding="utf-16", end_marker="END", chunk_size=3)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(source.run, Flow("tail"), archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(10)
            with path.open("ab") as stream:
                stream.write("END\r後".encode("utf-16-le" if sys.byteorder == "little" else "utf-16-be"))
            poll.resume.set()
            result = future.result(10)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert result.complete
    assert lines_from(result) == ["one", "END"]
    assert result.snapshot["source_descriptor"]["end_offset"] == len("one\rEND\r".encode("utf-16"))


def test_legacy_archive_attempt_defaults_to_process(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    archive = RunArchive(path, create=True)
    archive.save_attempt(dict(id="job", attempt_id="attempt", attempt_number=1))
    archive.close()
    store = RunArchive(path)
    try:
        assert store.attempt("job", "attempt")["source_kind"] == "process"
        assert store.attempts()["rows"][0]["source_kind"] == "process"
    finally:
        store.close()


def test_stale_completion_file_waits_for_current_producer(tmp_path):
    path, marker = tmp_path / "log", tmp_path / "done"
    marker.touch()
    os.utime(marker, ns=(1_000_000_000, 1_000_000_000))
    path.write_bytes(b"first\n")
    poll = Polls()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(TailSource(path, completion_file=marker).run,
                             Flow("tail"), archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(3)
            with path.open("ab") as stream:
                stream.write(b"second\n")
            marker.write_text("current", encoding="utf-8")
            poll.resume.set()
            result = future.result(5)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert result.complete and lines_from(result) == ["first", "second"]


def test_tail_waits_for_delayed_old_writer_after_rotation(tmp_path, monkeypatch):
    path, old = tmp_path / "log", tmp_path / "old"
    path.write_bytes(b"first\n")
    poll = Polls()
    from rpkbin.wave import sources
    from types import SimpleNamespace

    now = [0.0]
    monkeypatch.setattr(sources, "time", SimpleNamespace(monotonic=lambda: now[0], time=sources.time.time))
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(TailSource(path, end_marker="END").run,
                             Flow("tail"), archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(3)
            poll.waiting.clear()
            path.rename(old)
            path.write_bytes(b"new\nEND\n")
            poll.resume.set()
            assert poll.waiting.wait(3)
            poll.waiting.clear()
            with old.open("ab") as stream:
                stream.write(b"late-old\n")
            poll.resume.set()
            assert poll.waiting.wait(3)
            now[0] = 4.0
            poll.resume.set()
            result = future.result(5)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert lines_from(result) == ["first", "late-old", "new", "END"]
    assert not result.complete and result.reason == "discontinuity"


@pytest.mark.parametrize("separator", [b"\r", b"\r\n", b"\n"])
def test_tail_terminal_marker_at_eof(tmp_path, separator):
    path = tmp_path / "log"
    payload = b"one\r\ntwo\nEND" + separator
    path.write_bytes(payload)
    result = TailSource(path, end_marker="END", idle_timeout=0, chunk_size=1).run(
        Flow("tail"), archive_dir=tmp_path)
    assert result.complete and result.reason == "end_marker"
    assert lines_from(result) == ["one", "two", "END"]
    assert result.snapshot["source_descriptor"]["end_offset"] == len(payload)


def test_stop_event_commits_the_captured_chunk(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"one\ntwo\npartial")
    stop = Event()

    def parse(line, ctx):
        stop.set()
        return {"line": line}

    result = FileSource(path).run(Flow("file", parsers=[Parser("parse", parse)]),
                                  archive_dir=tmp_path, stop_event=stop)
    assert not result.complete and result.reason == "user_stopped"
    assert lines_from(result) == ["one", "two"]
    assert result.snapshot["source_descriptor"]["end_offset"] == len(b"one\ntwo\n")
    store = RunArchive(result.archive_path)
    try:
        saved = store.attempt(result.snapshot["id"], result.snapshot["attempt_id"])
        assert saved["total_lines"] == 2 and not saved["flow"]["complete"]
    finally:
        store.close()


@pytest.mark.parametrize("stage", ["flow_insert", "commit"])
def test_interrupt_preserves_the_entire_batch(tmp_path, monkeypatch, stage):
    import sqlite3

    original = sqlite3.connect
    injected = Event()

    class InterruptedConnection(sqlite3.Connection):
        def executemany(self, sql, rows):
            if sql.startswith("INSERT INTO flow_records"):
                if stage == "flow_insert" and not injected.is_set():
                    injected.set()
                    raise KeyboardInterrupt
                self.has_flow = True
            return super().executemany(sql, rows)

        def commit(self):
            if stage == "commit" and getattr(self, "has_flow", False) and not injected.is_set():
                injected.set()
                raise KeyboardInterrupt
            super().commit()

    def connect(*args, **kwargs):
        return original(*args, factory=InterruptedConnection, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    path = tmp_path / "log"
    path.write_bytes(b"row\n" * 130)
    result = FileSource(path).run(Flow("file", parsers=[Parser("parse", lambda line, ctx: {"row": line})]),
                                  archive_dir=tmp_path)
    assert injected.is_set()
    assert not result.complete and result.reason == "user_stopped"
    assert result.archive_error is None
    store = RunArchive(result.archive_path)
    try:
        saved = store.attempt(result.snapshot["id"], result.snapshot["attempt_id"])
        assert saved["total_lines"] == 130 and not saved["flow"]["complete"]
        assert saved["source_descriptor"]["end_offset"] == 520
        with store._reader() as conn:
            for table in ("records", "flow_records"):
                assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 130
    finally:
        store.close()


def test_tail_ignores_marker_inside_starting_baseline(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"last run\nEND\ncurrent run\n")
    poll = Polls()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(TailSource(path, end_marker="END").run,
                             Flow("tail"), archive_dir=tmp_path, stop_event=poll)
        try:
            assert poll.waiting.wait(5)
            with path.open("ab") as stream:
                stream.write(b"more\nEND\n")
            poll.resume.set()
            result = future.result(5)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert result.complete
    assert lines_from(result) == ["last run", "END", "current run", "more", "END"]
