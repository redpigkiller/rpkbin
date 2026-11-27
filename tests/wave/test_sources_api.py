"""Live source activity and its independent stop endpoint."""

import sys
from threading import Event

from rpkbin.jobcore import Job
from rpkbin.wave import TailSource, WaveWorkspace
from rpkbin.wave.web import WaveServer
from test_wave import post, request, running_server, state
from test_workspace_sources import _wait_for


def test_source_activity_and_stopping_one_or_all(tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "left.log").write_bytes(b"left\n")
    path = tmp_path / "right.log"
    path.write_bytes(b"right\n")
    workspace = WaveWorkspace(archive_dir=tmp_path)
    jobs = {job.name: job for job in workspace.add_paths([watched, path], poll_interval=.02)}
    left, right = jobs["left.log"], jobs["right.log"]
    entered, release = Event(), Event()

    def hold_left(record):
        if record["job_id"] == left.id:
            entered.set()
            release.wait(6)

    workspace.add_log_listener(hold_left)
    with running_server(workspace) as (_, base):
        preview = state(base)
        assert preview["sources"] == dict(configured_watchers=1, active_watchers=0,
                                          active_readers=0, stoppable_jobs=[], watching=False)
        assert preview["watcher_error"] is None
        assert post(base, "/api/sources/stop", {"job_id": None})[0] == 200
        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        try:
            assert entered.wait(5)
            active = state(base)["sources"]
            assert active == dict(configured_watchers=1, active_watchers=1,
                                  active_readers=2, stoppable_jobs=list(workspace._source_tasks), watching=True)
            assert post(base, "/api/sources/stop", {"job_id": left.id})[0] == 200
            # A stop request takes the reader out of the controllable set before it joins.
            active = state(base)["sources"]
            assert active["stoppable_jobs"] == [right.id]
            assert active["active_readers"] == active["active_watchers"] == 1
            assert not left._attempt.finalized and not right._attempt.finalized
        finally:
            release.set()
        _wait_for(lambda: left._attempt.finalized)
        code, body = post(base, "/api/sources/stop", {"job_id": None})
        assert code == 200 and "folders" in body["message"].lower()
        assert workspace._drain(5)
        assert state(base)["sources"] == preview["sources"]
    assert workspace.archive.health["closed_cleanly"]


def test_source_stop_rejects_bad_ids_stale_workspace_and_saved_runs(tmp_path):
    workspace = WaveWorkspace(archive_dir=tmp_path)
    source = workspace.add_source(TailSource(tmp_path / "input.log"))
    job = workspace.add(Job("process", [sys.executable, "-c", "pass"], pty=False))
    with running_server(workspace) as (_, base):
        for invalid in ("unknown", source.name, job.id, "", " ", False, {}):
            assert post(base, "/api/sources/stop", {"job_id": invalid})[0] == 400
        code, body = request(base, "/api/sources/stop", {"workspace_id": "stale", "job_id": source.id})
        assert code == 400 and "Workspace changed" in body["error"]
        assert not workspace._source_tasks[source.id].stop.is_set()
    server = WaveServer(archive_path=workspace.archive.path)
    base = server.start()
    try:
        code, body = post(base, "/api/sources/stop", {"job_id": None})
        assert code == 400 and "read-only" in body["error"].lower()
    finally:
        server.stop()
        server.workspace.close()


def test_callback_can_snapshot_source_activity_while_start_lock_is_held(tmp_path):
    path = tmp_path / "source.log"
    path.write_bytes(b"line\n")
    workspace = WaveWorkspace(archive_dir=tmp_path)
    job = workspace.add_source(TailSource(path))
    captured, snapshots = Event(), []

    def snapshot_in_reader(record):
        snapshots.append(workspace.snapshot(include_logs=False)["sources"])
        captured.set()

    workspace.add_log_listener(snapshot_in_reader)
    try:
        with workspace.manager._start_lock:
            workspace.start()
            assert captured.wait(5)
        assert snapshots[0]["stoppable_jobs"] == [job.id]
    finally:
        workspace.close()
