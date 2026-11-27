"""HTTP API tests for Stage U3 queue editing, scheduling mode, and reload."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

import pytest

from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import WaveWorkspace
from rpkbin.wave.web import WaveServer


@pytest.fixture(autouse=True)
def isolated_archive_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RPK_WAVE_ARCHIVE_DIR", str(tmp_path / "archives"))


def request(base, path, body=None):
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(base).query).get("token", [""])[0]
    req = urllib.request.Request(
        urllib.parse.urljoin(base, path),
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Wave-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@contextmanager
def server_for(workspace, *, source_path=None):
    workspace.validate()
    server = WaveServer(workspace, source_path=source_path)
    server.start()
    try:
        yield server, server.url.rstrip("/")
    finally:
        server.workspace.close()
        server.stop()


def target(job_dict):
    return {
        "job_id": job_dict["id"],
        "attempt_id": job_dict["attempt_id"],
        "submission_id": job_dict["submission_id"],
    }


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "Timed out waiting for expected state"
        time.sleep(0.02)


def api(base, route, payload=None):
    code, state = request(base, "/api/state")
    assert code == 200
    body = {"workspace_id": state["workspace_id"]}
    if payload is not None:
        body.update(payload)
    return request(base, route, body)


def add_py(ws, name, code="print('ok')", **kwargs):
    return ws.add(Job(name, [sys.executable, "-u", "-c", code], pty=False, **kwargs))


def test_scheduling_mode_settings_and_hold_api():
    mgr = JobManager(
        max_workers=2,
        resources={"matlab_license": 1, "grid": 2, "vcs_license": 1},
    )
    ws = WaveWorkspace(mgr)
    a = add_py(
        ws,
        "a",
        "import time; time.sleep(0.2)",
        resources={"matlab_license": 1, "grid": 1},
        priority=10,
    )
    b = add_py(ws, "b", "print('b')", resources={"matlab_license": 1, "grid": 1}, priority=9)
    c = add_py(ws, "c", "print('c')", resources={"grid": 1}, priority=8)
    d = add_py(ws, "d", "print('d')", resources={"vcs_license": 1, "grid": 1}, priority=7)

    with server_for(ws) as (_, base):
        code, bad = api(base, "/api/settings/save", {"scheduling_mode": "invalid_mode"})
        assert code == 400
        assert bad["field"] == "scheduling_mode"

        code, ok = api(base, "/api/settings/save", {"scheduling_mode": "strict"})
        assert code == 200 and ok["ok"] is True

        code, state = request(base, "/api/state")
        assert state["scheduling_mode"] == "strict"

        # Hold job b while still in Preview
        by_name = {j["name"]: j for j in state["jobs"]}
        code, res = api(base, "/api/jobs/hold", {"targets": [target(by_name["b"])], "held": True})
        assert code == 200 and res["affected"] == 1
        code, state = request(base, "/api/state")
        assert {j["name"]: j["held"] for j in state["jobs"]}["b"] is True

        # Start batch: a runs, b is held so strict mode does NOT block c behind held b!
        code, _ = api(base, "/api/manager/action", {"action": "start"})
        assert code == 200
        wait_for(lambda: c.status.is_finished and a.status.is_finished and d.status.is_finished)
        assert b.status.value == "PENDING"
        assert b._attempt.held is True

        # Unhold b -> finishes cleanly
        code, state = request(base, "/api/state")
        by_name = {j["name"]: j for j in state["jobs"]}
        code, res = api(base, "/api/jobs/hold", {"targets": [target(by_name["b"])], "held": False})
        assert code == 200 and res["affected"] == 1
        wait_for(lambda: b.status.is_finished)


def test_reorder_and_remove_preview_cleans_unstarted_archive():
    ws = WaveWorkspace(JobManager(max_workers=1))
    j_a = add_py(ws, "a", "print('a')", priority=10)
    j_c = add_py(ws, "c", "print('c')", priority=10)
    j_k = add_py(ws, "k", "print('k')", priority=10)

    with server_for(ws) as (_, base):
        code, state = request(base, "/api/state")
        by_name = {j["name"]: j for j in state["jobs"]}
        a_attempt = by_name["a"]["attempt_id"]

        # Reorder to k > c > a
        code, res = api(
            base,
            "/api/jobs/reorder",
            {"targets": [target(by_name["k"]), target(by_name["c"]), target(by_name["a"])]},
        )
        assert code == 200 and res["affected"] == 3
        assert j_k.priority > j_c.priority > j_a.priority

        # Remove job a in Preview: unstarted archive row should be removed cleanly
        code, state = request(base, "/api/state")
        by_name = {j["name"]: j for j in state["jobs"]}
        code, rm = api(base, "/api/jobs/remove", {"targets": [target(by_name["a"])]})
        assert code == 200 and rm["affected"] == 1
        assert [j.name for j in ws.jobs] == ["c", "k"] or {j.name for j in ws.jobs} == {"c", "k"}
        row = ws.archive._writer.execute(
            "SELECT 1 FROM attempts WHERE id=?", (a_attempt,)
        ).fetchone()
        assert row is None

        # Running state blocks remove
        code, _ = api(base, "/api/manager/action", {"action": "start"})
        assert code == 200
        wait_for(lambda: j_k.status.is_finished and j_c.status.is_finished)


def test_requeue_stopped_while_paused_and_remove_after_stop():
    ws = WaveWorkspace(JobManager(max_workers=1))
    j1 = add_py(ws, "one", "import time; time.sleep(0.05); print('one')")
    add_py(ws, "two", "import time; time.sleep(0.05); print('two')")

    with server_for(ws) as (_, base):
        code, _ = api(base, "/api/manager/action", {"action": "start"})
        assert code == 200
        wait_for(
            lambda: (s := request(base, "/api/state")[1])["active_workers"] == 0
            and all(j["can_rerun"] for j in s["jobs"])
        )

        code, state = request(base, "/api/state")
        by_name = {j["name"]: j for j in state["jobs"]}
        j2_done_attempt = by_name["two"]["attempt_id"]

        # Requeue job 'one' with desired priority order: pauses dispatch first so 'one' stays PENDING
        code, rq = api(
            base,
            "/api/jobs/requeue",
            {"targets": [target(by_name["one"])], "order": [target(by_name["one"])]},
        )
        assert code == 200, rq
        assert rq["affected"] == 1 and rq["paused"] is True
        assert j1.status.value == "PENDING"
        assert j1.attempt_number == 2

        # Remove stopped job 'two' after run started: removed from active roster, archive retains finished attempt!
        code, state = request(base, "/api/state")
        by_name = {j["name"]: j for j in state["jobs"]}
        code, rm = api(base, "/api/jobs/remove", {"targets": [target(by_name["two"])]})
        assert code == 200 and rm["affected"] == 1
        assert [j.name for j in ws.jobs] == ["one"]
        assert (
            ws.archive._writer.execute(
                "SELECT 1 FROM attempts WHERE id=?", (j2_done_attempt,)
            ).fetchone()
            is not None
        )

        # Resume -> 'one' runs attempt 2
        code, _ = api(base, "/api/manager/action", {"action": "resume"})
        assert code == 200
        wait_for(lambda: j1.status.is_finished and j1.attempt_number == 2)


def test_work_reload_reloads_helper_module_and_rejects_bad_edit(tmp_path):
    helper = tmp_path / "u3_test_helper.py"
    work = tmp_path / "work.py"
    helper.write_text('JOB_NAMES = ["alpha", "beta"]\n', encoding="utf-8")
    work.write_text(
        "\n".join(
            [
                "import sys",
                "from rpkbin.jobcore import Job, JobManager",
                "from u3_test_helper import JOB_NAMES",
                "mgr = JobManager(max_workers=2)",
                "for n in JOB_NAMES:",
                "    mgr.add(Job(n, [sys.executable, '-c', 'print(1)']))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    from rpkbin.wave.cli import load_work_file

    ws = load_work_file(work)
    with server_for(ws, source_path=work) as (server, base):
        assert [j.name for j in server.workspace.jobs] == ["alpha", "beta"]

        # Edit helper file only (work.py unchanged!) and hit Reload
        time.sleep(0.02)
        helper.write_text('JOB_NAMES = ["gamma", "delta", "epsilon"]\n', encoding="utf-8")
        code, res = api(base, "/api/work/reload", {})
        assert code == 200 and res["ok"] is True
        assert [j.name for j in server.workspace.jobs] == ["gamma", "delta", "epsilon"]

        # Syntax error on subsequent reload preserves previous valid workspace
        prev_id = server._workspace_id
        time.sleep(0.02)
        helper.write_text("JOB_NAMES = [\n", encoding="utf-8")
        code, err = api(base, "/api/work/reload", {})
        assert code == 400
        assert "SyntaxError" in err["error"]
        assert server._workspace_id == prev_id
        assert [j.name for j in server.workspace.jobs] == ["gamma", "delta", "epsilon"]


def test_logs_cli_default_open_browser_false_and_open_flag(tmp_path, monkeypatch):
    from rpkbin.wave import cli, web

    log = tmp_path / "sample.log"
    log.write_text("hello\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(web, "launch", lambda _ws, **kw: seen.append(kw["open_browser"]))
    assert cli.main(["logs", str(log), "--once", "--archive-dir", str(tmp_path / "arch1")]) == 0
    assert cli.main(["logs", str(log), "--once", "--open", "--archive-dir", str(tmp_path / "arch2")]) == 0
    assert seen == [False, True]


def test_restore_job_api_and_state_exposes_platform_signals():
    ws = WaveWorkspace(JobManager(max_workers=1))
    j_a = add_py(ws, "a", "print('a')", priority=20)
    j_b = add_py(ws, "b", "print('b')", priority=10)

    with server_for(ws) as (_, base):
        code, state = request(base, "/api/state")
        assert code == 200
        assert state["removed_jobs"] == []
        assert state["platform"] == os.name
        assert isinstance(state["supported_signals"], list)
        if os.name == "posix":
            assert "SIGINT" in state["supported_signals"]
            assert "SIGTERM" in state["supported_signals"]
        else:
            assert state["supported_signals"] == []

        by_name = {j["name"]: j for j in state["jobs"]}
        a_attempt = by_name["a"]["attempt_id"]
        code, rm = api(base, "/api/jobs/remove", {"targets": [target(by_name["a"])]})
        assert code == 200 and rm["affected"] == 1

        code, state = request(base, "/api/state")
        assert [j["name"] for j in state["jobs"]] == ["b"]
        assert [j["name"] for j in state["removed_jobs"]] == ["a"]
        assert (
            ws.archive._writer.execute(
                "SELECT 1 FROM attempts WHERE id=?", (a_attempt,)
            ).fetchone()
            is None
        )

        # Invalid/duplicate targets fail validation with HTTP 400.
        code, _ = api(base, "/api/jobs/restore", {"targets": []})
        assert code == 400
        code, _ = api(
            base,
            "/api/jobs/restore",
            {"targets": [target(by_name["a"]), target(by_name["a"])]},
        )
        assert code == 400

        # Restore in Preview brings 'a' back and re-saves unstarted archive row.
        code, rs = api(base, "/api/jobs/restore", {"targets": [target(by_name["a"])]})
        assert code == 200 and rs["affected"] == 1 and rs["action"] == "restore"
        code, state = request(base, "/api/state")
        assert [j["name"] for j in state["jobs"]] == ["a", "b"]
        assert state["removed_jobs"] == []
        assert (
            ws.archive._writer.execute(
                "SELECT 1 FROM attempts WHERE id=?", (a_attempt,)
            ).fetchone()
            is not None
        )

        # Start, finish, pause, remove finished job, then restore it cleanly.
        code, _ = api(base, "/api/manager/action", {"action": "start"})
        assert code == 200
        wait_for(lambda: j_a.status.is_finished and j_b.status.is_finished)
        code, _ = api(base, "/api/manager/action", {"action": "pause"})
        assert code == 200
        code, state = request(base, "/api/state")
        by_name = {j["name"]: j for j in state["jobs"]}
        code, rm2 = api(base, "/api/jobs/remove", {"targets": [target(by_name["b"])]})
        assert code == 200 and rm2["affected"] == 1
        code, rs2 = api(base, "/api/jobs/restore", {"targets": [target(by_name["b"])]})
        assert code == 200 and rs2["affected"] == 1
        assert [j.name for j in ws.jobs] == ["a", "b"]


def test_job_action_send_no_newline_and_signal_validation():
    ws = WaveWorkspace(JobManager(max_workers=1))
    code_str = (
        "import sys; "
        "print('READY', flush=True); "
        "line = sys.stdin.readline().rstrip('\\r\\n'); "
        "print('GOT:' + line, flush=True)"
    )
    job = add_py(ws, "reader", code_str)

    with server_for(ws) as (_, base):
        code, _ = api(base, "/api/manager/action", {"action": "start"})
        assert code == 200
        q_detail = f"/api/state?job_id={urllib.parse.quote(job.id)}"
        wait_for(
            lambda: any(
                "READY" in r.get("text", "")
                for r in (request(base, q_detail)[1].get("detail") or {}).get("log_records", [])
            )
            and job.status.value == "RUNNING"
        )
        _, state = request(base, q_detail)
        t = target(state["jobs"][0])

        # Validation failures on send / signal
        code, _ = api(base, "/api/job/action", {"action": "send", "targets": [t], "text": "x", "newline": "yes"})
        assert code == 400
        code, _ = api(base, "/api/job/action", {"action": "signal", "targets": [t], "signal": "BAD_SIGNAL"})
        assert code == 400

        if os.name != "posix":
            code, _ = api(base, "/api/job/action", {"action": "signal", "targets": [t], "signal": "SIGUSR1"})
            assert code == 400

        # Send without newline, then complete the line with newline=True
        code, r1 = api(base, "/api/job/action", {"action": "send", "targets": [t], "text": "hello", "newline": False})
        assert code == 200 and r1["affected"] == 1
        code, r2 = api(base, "/api/job/action", {"action": "send", "targets": [t], "text": "", "newline": True})
        assert code == 200 and r2["affected"] == 1
        wait_for(lambda: job.status.is_finished)
        _, state = request(base, q_detail)
        assert any("GOT:hello" in r.get("text", "") for r in state["detail"]["log_records"])


def test_job_action_pause_dispatch_pauses_atomically_with_stop():
    ws = WaveWorkspace(JobManager(max_workers=2))
    run_j = add_py(ws, "run", "import time; print('RUN', flush=True); time.sleep(5)", priority=20)
    held_j = add_py(ws, "held", "print('HELD')", priority=10)

    with server_for(ws) as (_, base):
        code, st = request(base, "/api/state")
        assert code == 200
        by_name = {j["name"]: j for j in st["jobs"]}
        code, _ = api(base, "/api/jobs/hold", {"targets": [target(by_name["held"])], "held": True})
        assert code == 200

        code, _ = api(base, "/api/manager/action", {"action": "start"})
        assert code == 200
        wait_for(lambda: run_j.status.value == "RUNNING")

        _, st = request(base, "/api/state")
        by_name = {j["name"]: j for j in st["jobs"]}
        # Invalid pause_dispatch type or unsupported action returns HTTP 400
        code, _ = api(base, "/api/job/action", {"action": "stop", "targets": [target(by_name["run"])], "pause_dispatch": "yes"})
        assert code == 400
        code, _ = api(base, "/api/job/action", {"action": "interrupt", "targets": [target(by_name["run"])], "pause_dispatch": True})
        assert code == 400

        # Stop running job with pause_dispatch=True while leaving held job untouched
        code, res = api(base, "/api/job/action", {"action": "stop", "targets": [target(by_name["run"])], "pause_dispatch": True})
        assert code == 200 and res["affected"] == 1
        wait_for(lambda: run_j.status.is_finished)
        _, st = request(base, "/api/state")
        assert st["paused"] is True
        assert held_j.status.value == "PENDING" and held_j._attempt.held is True
