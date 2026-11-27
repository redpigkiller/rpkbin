"""Workspace-level file ingestion, discovery and shutdown."""

import sqlite3
from threading import Event
import time

import pytest

from rpkbin.flow import Automation, Flow, FlowSession
from rpkbin.jobcore import JobManager
from rpkbin.wave import RunArchive, WaveWorkspace
from rpkbin.wave.sources import FileSource, ReanalysisSource, TailSource


def _rows(store, job, **filters):
    return [row["text"] for row in store.reader(job.id, job.attempt_id, **filters)["rows"]]


def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not (value := predicate()):
        assert time.monotonic() < deadline, "condition did not become true"
        time.sleep(.01)
    return value


def test_sources_share_archive_and_bypass_worker_limit(tmp_path):
    workspace = WaveWorkspace(JobManager(max_workers=1), archive_dir=tmp_path)
    arrived = {f"tail-{i}": Event() for i in range(3)}
    release = Event()

    def hold(record):
        if (event := arrived.get(record["text"])) is not None:
            event.set()
            release.wait(5)

    workspace.add_log_listener(hold)
    jobs = []
    try:
        for name in arrived:
            path = tmp_path / f"{name}.log"
            path.write_bytes(f"{name}\n".encode())
            jobs.append(workspace.add_source(TailSource(path, poll_interval=.01)))
        with pytest.raises(TypeError, match="Flow"):
            workspace.add_source(FileSource(path), flow=object())
        workspace.start()
        assert all(event.wait(3) for event in arrived.values())
        offline = tmp_path / "offline.log"
        offline.write_bytes(b"offline record\n")
        file_job = workspace.add_source(FileSource(offline), name="offline")
        _wait_for(lambda: file_job.to_snapshot_dict()["finalized"])
        assert not workspace._drain(.02)
        snapshot = workspace.snapshot(selected_job_id=jobs[0].id)
        assert snapshot["active_workers"] == 0
        detail = snapshot["detail"]
        assert detail.get("flow") is None
        assert detail["pid"] is None and detail["origin"] == "external"
        assert detail["origin_label"] == "External (tail)"
        assert not any(detail["capabilities"].values())
        guard = dict(expected_attempt=jobs[0].attempt_id,
                     expected_submission=jobs[0].submission_id)
        for action in ("stop", "send", "interrupt", "priority", "rerun"):
            assert workspace.control(jobs[0].id, action, text="data", **guard).status == "rejected"
        workspace.stop_sources(jobs[0].id)
        release.set()
        _wait_for(lambda: jobs[0].to_snapshot_dict()["finalized"])
        assert not workspace._drain(.02)
        workspace.stop_sources()
        assert workspace._drain(5)
    finally:
        release.set()
        workspace.close()
    archive = RunArchive(workspace.archive.path)
    assert archive.health["closed_cleanly"]
    saved = {row["id"]: row for row in archive.attempts()["rows"]}
    for job, name in zip(jobs, arrived):
        assert _rows(archive, job) == [name]
        row = saved[job.id]
        assert row["verified"] and row["pid"] is None
        assert row["origin"] == "external" and row["origin_label"] == "External (tail)"
        assert row["completion_reason"] == "user_stopped"
    assert _rows(archive, file_job, query="record") == ["offline record"]
    assert saved[file_job.id]["origin_label"] == "External (file)"
    assert saved[file_job.id]["completion_reason"] == "eof"


@pytest.mark.parametrize("phase", ["append", "savepoint"])
def test_no_flow_source_archive_failure(tmp_path, monkeypatch, phase):
    path = tmp_path / "input.log"
    path.write_bytes(b"one\ntwo\n")
    append = RunArchive._append_source_batch

    def fail(store, attempt_id, raw, publications):
        if raw:
            store._fail(sqlite3.OperationalError("injected source write failure"))
            return False
        return append(store, attempt_id, raw, publications)

    connect = sqlite3.connect

    class FailedSavepoint(sqlite3.Connection):
        def execute(self, statement, *args):
            if statement == "SAVEPOINT file_input":
                raise sqlite3.OperationalError("injected source write failure")
            return super().execute(statement, *args)

    if phase == "append":
        monkeypatch.setattr(RunArchive, "_append_source_batch", fail)
    else:
        monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs:
                            connect(*args, factory=FailedSavepoint, **kwargs))
    result = FileSource(path).run(archive_dir=tmp_path)
    assert not result.complete and result.reason == "archive_error"
    assert "injected source write failure" in result.archive_error
    assert result.snapshot["finalized"] and result.snapshot["total_lines"] == 2
    archive = RunArchive(result.archive_path)
    assert not archive.health["closed_cleanly"]
    assert not archive.attempts()["rows"][0]["verified"]


def test_discovery_filters_deduplicates_and_names(tmp_path):
    left, right = (tmp_path / name for name in ("left", "right"))
    nested = left / "nested"
    nested.mkdir(parents=True)
    right.mkdir()
    paths = [left / "same.log", right / "same.log", nested / "more.log",
             left / "trace.txt", left / "other.SQLITE3", left / "db.sqlite3-wal",
             left / "db.sqlite3-shm"]
    for path in paths:
        path.write_bytes(b"record\n")
    (left / "directory.log").mkdir()

    workspace = WaveWorkspace(archive_dir=left)
    try:
        plain = workspace.add_paths([paths[0], left, left, left / "."], once=True)
        assert [job.name for job in plain] == ["same.log"]
        jobs = workspace.add_paths([left, right, nested, paths[0]], once=True,
                                   recursive=True, globs=("*",))
        assert {job._source.path for job in jobs} == set(paths[1:4])
        assert {job.name for job in jobs} == {"right/same.log", "nested/more.log", "trace.txt"}
        workspace.start()
        assert workspace._drain(5)
        (left / "later.log").touch()
    finally:
        workspace.close()
    assert len(workspace.jobs) == 4


@pytest.mark.parametrize("existing", [False, True])
def test_live_discovery_reads_new_file_from_beginning(tmp_path, existing):
    watched = tmp_path / "watched"
    watched.mkdir()
    if existing:
        (watched / "old.log").write_bytes(b"old line\n")
    workspace = WaveWorkspace(archive_dir=watched)
    try:
        initial = workspace.add_paths([watched], start="end", poll_interval=.01,
                                      commit_interval=.02)
        assert len(initial) == int(existing)
        workspace.validate()
        workspace.start()
        assert workspace.started and not workspace.paused
        assert not workspace._drain(.03)
        (watched / "ignored.txt").touch()
        staged = tmp_path / "staged"
        staged.write_bytes(b"first line\nsecond line\n")
        staged.rename(watched / "new.log")
        job = _wait_for(lambda: workspace.get_job("new.log"))
        # The roster can expose the job before the Archive publishes its attempt.
        _wait_for(lambda: workspace.archive.attempts(job.id)["rows"])
        _wait_for(lambda: _rows(workspace.archive, job) == ["first line", "second line"])
        assert len(workspace.jobs) == int(existing) + 1
        workspace.stop_sources(job.id)
        _wait_for(lambda: job.to_snapshot_dict()["finalized"])
        assert not workspace._drain(.02)
    finally:
        workspace.close()
    assert workspace._drain(1)
    assert workspace.archive.health["closed_cleanly"]


def test_replay_and_file_share_archive(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_bytes(b"one\ntwo\n")
    original = FileSource(path).run(archive_dir=tmp_path)
    assert original.complete
    called = Event()
    replay_archive = RunArchive.replay

    def observe(store, attempt_id):
        called.set()
        return replay_archive(store, attempt_id)

    monkeypatch.setattr(RunArchive, "replay", observe)
    replay = ReanalysisSource(original.archive_path, original.snapshot["attempt_id"])
    workspace = WaveWorkspace(archive_dir=tmp_path)
    try:
        jobs = [workspace.add_source(replay), workspace.add_source(FileSource(path))]
        assert not called.is_set()
        workspace.start()
        assert workspace._drain(5) and called.is_set()
    finally:
        workspace.close()
    assert workspace.archive.health["closed_cleanly"]
    for job in jobs:
        assert _rows(workspace.archive, job) == ["one", "two"]


def test_recursive_discovery_and_tail_writes_share_archive(tmp_path):
    root = tmp_path / "watched"
    nested = root / "nested"
    nested.mkdir(parents=True)
    existing = root / "existing.trace"
    existing.touch()
    workspace = WaveWorkspace(JobManager(max_workers=1), archive_dir=root)
    paths = [nested / f"log-{i:02}.trace" for i in range(12)]
    payload = b"".join(f"line {i}\n".encode() for i in range(130))
    try:
        workspace.add_paths([root, nested], recursive=True, globs=("*.trace",),
                            poll_interval=.01, commit_interval=.02)
        workspace.start()
        for path in paths:
            staged = tmp_path / "staged"
            staged.write_bytes(payload)
            staged.rename(path)
            with existing.open("ab") as stream:
                stream.write(payload)
        _wait_for(lambda: len(workspace.jobs) == len(paths) + 1)
        _wait_for(lambda: all(row["total_lines"] == (len(paths) * 130 if row["name"] == existing.name else 130)
                              for row in workspace.snapshot()["jobs"]))
    finally:
        workspace.close()
    assert workspace.archive.health["closed_cleanly"]
    rows = workspace.archive.attempts()["rows"]
    assert all(row["verified"] and row["finalized"] for row in rows)
    assert sum(row["total_lines"] for row in rows) == 2 * len(paths) * 130
    for path in paths:
        assert _rows(workspace.archive, workspace.get_job(f"nested/{path.name}"), query="line 129") == ["line 129"]


def test_stop_during_final_automation_seals_stopped_attempt(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_bytes(b"log\n")
    finishing, release = Event(), Event()

    def hold_finish(event, ctx):
        if event.name == "finished":
            finishing.set()
            release.wait(3)

    wait_automation = FlowSession.wait_automation

    def settle_before_reader(session, timeout):
        assert wait_automation(session, timeout)
        # Settle automation before the source reader resumes finalization.
        session._on_automation_idle()
        return True

    monkeypatch.setattr(FlowSession, "wait_automation", settle_before_reader)
    workspace = WaveWorkspace(archive_dir=tmp_path)
    try:
        job = workspace.add_source(TailSource(path, final_offset=4),
                                   flow=Flow("tail", automations=[Automation("hold", on_event=hold_finish)]))
        workspace.start()
        assert finishing.wait(3)
        workspace.stop_sources(job.id)
        release.set()
        assert workspace._drain(5)
    finally:
        release.set()
        workspace.close()
    saved = workspace.archive.attempt(job.id, job.attempt_id)
    assert saved["completion_reason"] == "user_stopped"
    assert saved["finalized"] and saved["verified"]
    assert not saved["flow"]["complete"]


@pytest.mark.parametrize("once", [False, True])
def test_no_watcher_for_file_list_or_once(tmp_path, once):
    path = tmp_path / "first.log"
    path.write_bytes(b"log\n")
    workspace = WaveWorkspace(archive_dir=tmp_path)
    try:
        options = {} if once else dict(final_offset=4, poll_interval=.01)
        workspace.add_paths([tmp_path if once else path], once=once, **options)
        workspace.start()
        assert workspace._drain(3)
        (tmp_path / "second.log").touch()
        assert workspace._drain(.1)
        assert [job.name for job in workspace.jobs] == ["first.log"]
    finally:
        workspace.close()
    assert workspace.archive.health["closed_cleanly"]
