"""The optional per-attempt text Log keeps one live append stream."""

from rpkbin.wave import WaveWorkspace

import sys
from pathlib import Path

from rpkbin.jobcore import Job, JobManager


def test_stream_is_reused_visible_live_and_closed_at_attempt_boundary(tmp_path, monkeypatch):
    log = tmp_path / "output.log"
    actual_open = Path.open
    streams = []

    def track(path, mode="r", *args, **kwargs):
        stream = actual_open(path, mode, *args, **kwargs)
        if path == log and mode == "a":
            streams.append(stream)
            assert stream.line_buffering
        return stream

    monkeypatch.setattr(Path, "open", track)
    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path / "archive")
    try:
        job = manager.add(Job("log", [sys.executable, "-c", "pass"], log_file=log))
        first = job.attempt_id
        job.log("one")
        job.log("two")
        assert len(streams) == 1 and not streams[0].closed
        assert log.read_text(encoding="utf-8").splitlines() == [
            f"[{first}][wave] one", f"[{first}][wave] two",
        ]
        assert job.stop()
        assert streams[0].closed

        assert manager.rerun(job.id)
        second = job.attempt_id
        job.log("three")
        assert second != first and len(streams) == 2
        assert log.read_text(encoding="utf-8").splitlines()[-1] == f"[{second}][wave] three"
    finally:
        manager.close()
    assert all(stream.closed for stream in streams)


def test_write_error_closes_stream_and_next_record_reopens(tmp_path, monkeypatch):
    log = tmp_path / "output.log"
    actual_open = Path.open
    streams = []

    def track(path, mode="r", *args, **kwargs):
        stream = actual_open(path, mode, *args, **kwargs)
        if path == log and mode == "a":
            streams.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", track)
    with_manager = WaveWorkspace(JobManager(), archive_dir=tmp_path / "archive")
    try:
        job = with_manager.add(Job("log", [sys.executable, "-c", "pass"], log_file=log))
        job.log("before")
        job.log("\ud800")
        assert len(streams) == 1 and streams[0].closed
        job.log("after")
        assert len(streams) == 2
        assert log.read_text(encoding="utf-8").splitlines()[-1].endswith("[wave] after")
        snap = job.to_snapshot_dict()
        assert snap["log_file_error"]["count"] == 1
        assert snap["log_file_error"]["path"] == str(log)
        assert snap["error_count"] == 0
        assert [d["source"] for d in snap["diagnostics"]] == ["log_file"]
    finally:
        with_manager.close()
    assert streams[-1].closed

