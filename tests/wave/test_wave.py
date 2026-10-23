"""HTTP integration: preview, controls, evidence and attempt isolation."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

import pytest

from rpkbin.job_manager import Event, Expect, Job, JobManager
from rpkbin.wave.web import WaveServer

PY = sys.executable


def request(base, path, body=None, *, headers=None):
    req = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@contextmanager
def running_server(manager=None):
    server = WaveServer(manager)
    server.start()
    try:
        yield server, server.url.rstrip("/")
    finally:
        server.manager.close()
        server.stop()


def state(base, job=None, attempt=None):
    query = urllib.parse.urlencode(
        {k: v for k, v in {"job_id": job, "attempt_id": attempt}.items() if v}
    )
    status, data = request(base, "/api/state?" + query)
    assert status == 200, data
    return data


def post(base, path, payload):
    return request(base, path, {"workspace_id": state(base)["workspace_id"], **payload})


def target(snapshot):
    return {
        "job_id": snapshot["id"],
        "attempt_id": snapshot["attempt_id"],
        "submission_id": snapshot["submission_id"],
    }


def finish(manager):
    assert manager._drain(timeout=8), manager.snapshot()


def until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_preview_browse_partial_settings_and_queued_control(tmp_path):
    mgr = JobManager(resources={"slot": 2})
    job = mgr.add(
        Job("queued", [PY, "-c", "print('VALUE=1')"], resources={"slot": 1}, pty=False)
    )
    job.track("value", r"VALUE=(\d+)", cast=int)
    mgr.validate()
    with running_server(mgr) as (_, base):
        initial = state(base, job.id)
        assert not initial["started"]
        assert initial["detail"]["status"] == "PENDING"
        assert initial["detail"]["log_records"] == []
        assert "track value: regex" in initial["detail"]["monitor_definitions"][0]
        assert initial["available_columns"] == ["value"]
        # Independent panels never reset omitted settings or resurrect columns.
        assert post(base, "/api/settings/save", {"max_workers": 7})[0] == 200
        assert state(base)["resources"] == {"slot": 2}
        assert post(base, "/api/settings/save", {"columns": []})[0] == 200
        assert state(base)["columns"] == []
        assert state(base)["available_columns"] == ["value"]
        assert post(base, "/api/settings/save", {"resources": {"slot": 1}})[0] == 200
        assert state(base)["max_workers"] == 7
        for bad in ({"max_workers": True}, {"macros": []}, {"resources": None}):
            assert post(base, "/api/settings/save", bad)[0] == 400
        assert state(base)["resources"] == {"slot": 1}
        assert post(base, "/api/manager/action", {"action": "resume"})[0] == 400
        snapshot = state(base, job.id)["detail"]
        assert (
            post(
                base, "/api/job/priority", {"target": target(snapshot), "priority": 11}
            )[0]
            == 200
        )
        assert job.priority == 11
        code, res = post(
            base, "/api/job/action", {"action": "stop", "targets": [target(snapshot)]}
        )
        assert code == 200 and res["affected"] == 1
        assert state(base, job.id)["detail"]["result"] == "INCOMPLETE"
        (tmp_path / "one.py").write_text("# sample", encoding="utf-8")
        (tmp_path / "ignore.txt").write_text("not Python", encoding="utf-8")
        (tmp_path / "nested").mkdir()
        code, listing = request(
            base, "/api/files?path=" + urllib.parse.quote(str(tmp_path))
        )
        assert code == 200, listing
        assert [e["name"] for e in listing["entries"]] == ["nested", "one.py"]
        assert listing["parent"] == str(tmp_path.parent)


def test_work_load_atomic_preview_and_workspace_guard(tmp_path):
    marker, work, bad = tmp_path / "executed", tmp_path / "work.py", tmp_path / "bad.py"
    command = f"from pathlib import Path; Path({str(marker)!r}).touch()"
    work.write_text(
        "import sys\nfrom rpkbin.job_manager import Job, JobManager\n"
        "mgr=JobManager()\n"
        f"mgr.add(Job('preview',[sys.executable,'-c',{command!r}],pty=False))\n",
        encoding="utf-8",
    )
    bad.write_text("raise ValueError('broken definition')\n", encoding="utf-8")
    with running_server() as (server, base):
        old_workspace = state(base)["workspace_id"]
        code, res = post(base, "/api/work/load", {"path": str(work)})
        assert code == 200, res
        preview = state(base)
        assert preview["ready"] and not preview["started"]
        assert preview["source_path"] == str(work.resolve())
        assert not marker.exists()
        assert (
            request(
                base,
                "/api/manager/action",
                {"workspace_id": old_workspace, "action": "start"},
            )[0]
            == 400
        )
        current = server.manager
        code, res = post(base, "/api/work/load", {"path": str(bad)})
        assert code == 400 and "broken definition" in res["error"]
        assert server.manager is current
        assert state(base)["workspace_id"] == preview["workspace_id"]
        assert post(base, "/api/work/load", {"path": str(work)})[0] == 200
        assert not marker.exists()


def test_local_request_validation_and_removed_endpoints():
    with running_server() as (_, base):
        assert request(base, "/api/state", headers={"Host": "evil.example"})[0] == 403
        assert (
            request(base, "/api/state", headers={"Origin": "https://evil.example"})[0]
            == 403
        )
        assert request(base, "/api/config/export")[0] == 404
        for path in (
            "/api/regex/test",
            "/api/rule/save",
            "/api/config/import",
            "/api/job/add",
        ):
            assert post(base, path, {})[0] == 404
        assert post(base, "/api/manager/action", {"action": "rescan"})[0] == 400
    with pytest.raises(ValueError, match="loopback"):
        WaveServer(host="0.0.0.0")


def test_api_result_evidence_history_and_stale_attempt():
    mgr = JobManager()
    job = mgr.add(
        Job(
            "example",
            [PY, "-u", "-c", "print('VALUE=3'); print('BAD'); print('NOTICE')"],
            pty=False,
        )
    )
    job.track("value", r"VALUE=(\d+)", cast=int)
    job.check("no_bad", match="BAD", expect=Expect.ABSENT)
    job.on(Event.LOG, match="NOTICE", warn=True, name="notice")
    mgr.validate()
    with running_server(mgr) as (_, base):
        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        finish(mgr)
        first = state(base, job.id)["detail"]
        assert first["returncode"] == 0 and first["result"] == "FAIL"
        assert first["warning_count"] == 1
        assert first["checks"][0]["state"] == "FAIL"
        assert first["checks"][0]["evidence"]["line"] == "BAD"
        code, res = post(
            base, "/api/job/action", {"action": "rerun", "targets": [target(first)]}
        )
        assert code == 200 and res["affected"] == 1
        finish(mgr)
        current = state(base, job.id)
        assert current["detail"]["attempt_id"] != first["attempt_id"]
        historical = state(base, job.id, first["attempt_id"])
        assert historical["detail"]["attempt_id"] == first["attempt_id"]
        assert historical["jobs"][0]["attempt_id"] == current["detail"]["attempt_id"]
        assert historical["detail"]["log_records"] == first["log_records"]
        code, res = post(
            base, "/api/job/action", {"action": "rerun", "targets": [target(first)]}
        )
        assert code == 200 and res["affected"] == 0
        assert "stale" in res["results"][0]["message"].lower()
        assert (
            state(base, job.id)["detail"]["attempt_id"]
            == current["detail"]["attempt_id"]
        )
        missing = state(base, job.id, "not-retained")
        assert missing["detail"] is None and missing["history_error"]


def test_literal_control_token_and_http_lifetime():
    mgr = JobManager()
    job = mgr.add(
        Job(
            "echo",
            [
                PY,
                "-u",
                "-c",
                "import sys\nprint('READY',flush=True)\n"
                "text=sys.stdin.readline().rstrip('\\n')\n"
                "print('RECEIVED: '+text,flush=True)\n",
            ],
            pty=False,
        )
    )
    mgr.validate()
    with running_server(mgr) as (server, base):
        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        until(lambda: "READY" in job.logs())
        snapshot = state(base, job.id)["detail"]
        server.stop()
        assert not job.status.is_finished
        server.start()
        base = server.url.rstrip("/")
        code, res = post(
            base,
            "/api/job/action",
            {"action": "send", "targets": [target(snapshot)], "text": "__CTRL_C__"},
        )
        assert code == 200 and res["affected"] == 1
        finish(mgr)
        assert "RECEIVED: __CTRL_C__" in job.logs()
        assert state(base, job.id)["detail"]["result"] == "N/A"


def test_mixed_batch_no_selected_fallback():
    mgr = JobManager()
    mgr.add(Job("cancel_me", [PY, "-c", "pass"], pty=False))
    second = mgr.add(Job("keep_queued", [PY, "-c", "pass"], pty=False))
    mgr.validate()
    with running_server(mgr) as (_, base):
        a, b = state(base)["jobs"]
        assert (
            post(base, "/api/job/action", {"action": "stop", "targets": [target(a)]})[
                1
            ]["affected"]
            == 1
        )
        code, res = post(
            base,
            "/api/job/action",
            {"action": "send", "targets": [target(a), target(b)], "text": "status"},
        )
        assert code == 200 and res["affected"] == 0
        assert len(res["results"]) == 2 and second.status.value == "PENDING"
        assert (
            post(base, "/api/job/action", {"action": "stop", "targets": []})[0] == 400
        )
        assert (
            post(
                base,
                "/api/job/action",
                {"action": "stop", "targets": [{"job_id": second.id}]},
            )[0]
            == 400
        )


def test_retention_endpoints_mixed_batch_and_history_eviction():
    mgr = JobManager()
    j1 = mgr.add(
        Job(
            "j1",
            [PY, "-u", "-c", "for i in range(6): print(f'L{i}')"],
            pty=False,
        )
    )
    mgr.add(Job("j2", [PY, "-u", "-c", "print('OK')"], pty=False))
    j1.check("seen_l0", match="L0", expect=Expect.PRESENT)
    mgr.validate()
    with running_server(mgr) as (_, base):
        initial = state(base, j1.id)
        assert initial["max_history"] == 20
        assert initial["jobs"][0]["max_log_lines"] == 10000
        assert initial["jobs"][1]["max_log_lines"] == 10000

        assert post(base, "/api/manager/action", {"action": "start"})[0] == 200
        finish(mgr)

        s1 = state(base, j1.id)["detail"]
        # A Python launcher may also emit diagnostics; those remain real output.
        assert [r["text"] for r in s1["log_records"] if r["text"].startswith("L")] == [
            f"L{i}" for i in range(6)
        ]
        evidence = s1["checks"][0]["evidence"]
        code, res = post(
            base,
            "/api/job/retention",
            {"targets": [target(s1)], "max_log_lines": 3},
        )
        assert code == 200 and res["affected"] == 1
        assert res["evicted_lines"] == s1["log_count"] - 3
        after_s1 = state(base, j1.id)
        assert after_s1["jobs"][0]["max_log_lines"] == 3
        assert after_s1["jobs"][1]["max_log_lines"] == 10000
        assert after_s1["detail"]["log_records"] == s1["log_records"][-3:]
        assert after_s1["detail"]["total_lines"] == s1["total_lines"]
        assert after_s1["detail"]["checks"][0]["evidence"] == evidence
        assert after_s1["detail"]["result"] == "PASS"

        # Invalid max_log_lines rejected
        for bad in (0, -5, True, "3"):
            assert (
                post(
                    base,
                    "/api/job/retention",
                    {"targets": [target(after_s1["detail"])], "max_log_lines": bad},
                )[0]
                == 400
            )

        # Batch apply to all jobs
        all_targets = [target(j) for j in state(base)["jobs"]]
        code, res = post(
            base,
            "/api/job/retention",
            {"targets": all_targets, "max_log_lines": 4},
        )
        assert code == 200 and res["affected"] == 2
        assert [j["max_log_lines"] for j in state(base)["jobs"]] == [4, 4]

        # Rerun j1 twice to create 2 history attempts, then shrink max_history to 1
        first_attempt = after_s1["detail"]["attempt_id"]
        for _ in range(2):
            cur = state(base, j1.id)["detail"]
            code, r_res = post(
                base, "/api/job/action", {"action": "rerun", "targets": [target(cur)]}
            )
            assert code == 200 and r_res["affected"] == 1
            assert r_res["results"][0]["attempt_id"] == cur["attempt_id"]
            assert r_res["results"][0]["new_attempt_id"] != cur["attempt_id"]
            finish(mgr)

        # Stale target on retention and action returns original attempt_id with ok=False
        code, stale_ret = post(
            base,
            "/api/job/retention",
            {"targets": [target(s1)], "max_log_lines": 10},
        )
        assert code == 200 and stale_ret["affected"] == 0
        assert stale_ret["results"][0]["attempt_id"] == first_attempt

        assert (
            state(base, j1.id, first_attempt)["detail"]["attempt_id"] == first_attempt
        )
        code, save_res = post(base, "/api/settings/save", {"max_history": 1})
        assert code == 200 and save_res["evicted_history"] == 1
        assert state(base)["max_history"] == 1
        evicted_view = state(base, j1.id, first_attempt)
        assert evicted_view["detail"] is None and evicted_view["history_error"]
        assert evicted_view["jobs"][0]["history_truncated"] == 1


def stream_state(base, job_id, after=0, cursor_attempt=None, attempt=None):
    query = {"job_id": job_id, "log_after": after}
    if cursor_attempt:
        query["log_attempt"] = cursor_attempt
    if attempt:
        query["attempt_id"] = attempt
    status, data = request(base, "/api/state?" + urllib.parse.urlencode(query))
    assert status == 200, data
    return data


def test_log_stream_deltas_retention_and_cursor_recovery():
    mgr = JobManager()
    job = mgr.add(Job("log-stream", [PY, "-c", "pass"], max_log_lines=5, pty=False))
    mgr.validate()
    for i in range(5):
        job.log(f"line {i}")
    with running_server(mgr) as (_, base):
        initial = stream_state(base, job.id)
        chunk = initial["detail"]["log_chunk"]
        attempt = chunk["attempt_id"]
        assert chunk["reset"] and chunk["first_line"] == 1
        assert [r["line_no"] for r in chunk["records"]] == [1, 2, 3, 4, 5]
        assert "log_records" not in initial["detail"]
        assert "logs" not in initial["detail"]
        assert all("log_records" not in j and "logs" not in j for j in initial["jobs"])
        assert "monitor_definitions" in initial["detail"]

        job.log("new output")
        delta = stream_state(base, job.id, 5, attempt)["detail"]["log_chunk"]
        assert not delta["reset"] and delta["first_line"] == 2
        assert [r["line_no"] for r in delta["records"]] == [6]
        assert delta["next_line"] == 6 and delta["retained_count"] == 5

        job.set_max_log_lines(2)
        shrunk = stream_state(base, job.id, 6, attempt)["detail"]["log_chunk"]
        assert shrunk["records"] == [] and not shrunk["reset"]
        assert shrunk["first_line"] == 5 and shrunk["retained_count"] == 2
        job.set_max_log_lines(8)
        grown = stream_state(base, job.id, 6, attempt)["detail"]["log_chunk"]
        assert grown["records"] == [] and grown["first_line"] == 5
        assert grown["capacity"] == 8

        # Disconnected/evicted, ahead-of-server, and wrong-attempt cursors reset.
        for after, identity in ((1, attempt), (999, attempt), (6, "old-attempt")):
            reset = stream_state(base, job.id, after, identity)["detail"]["log_chunk"]
            assert reset["reset"]
            assert [r["line_no"] for r in reset["records"]] == [5, 6]

        # Existing Python APIs and non-incremental HTTP readers retain full logs.
        python_snapshot = mgr.snapshot(selected_job_id=job.id)["detail"]
        assert len(python_snapshot["logs"]) == 2
        legacy = state(base, job.id)
        assert legacy["detail"]["log_records"] == python_snapshot["log_records"]
        assert "logs" not in legacy["detail"]
        status, logs = request(base, "/api/logs?job_id=" + job.id)
        assert status == 200 and logs["records"] == python_snapshot["log_records"]
        for bad in ("-1", "true", "1.5", "9" * 19):
            assert (
                request(base, f"/api/state?job_id={job.id}&log_after={bad}")[0] == 400
            )


def test_log_stream_rerun_and_evicted_history_never_fall_back_to_current():
    mgr = JobManager(max_history=1)
    job = mgr.add(Job("rerun-stream", [PY, "-c", "pass"], pty=False))
    mgr.validate()
    job.log("first attempt")
    with running_server(mgr) as (_, base):
        first = stream_state(base, job.id)["detail"]
        for text in ("second attempt", "third attempt"):
            current = state(base, job.id)["detail"]
            assert (
                post(
                    base,
                    "/api/job/action",
                    {"action": "stop", "targets": [target(current)]},
                )[1]["affected"]
                == 1
            )
            assert (
                post(
                    base,
                    "/api/job/action",
                    {"action": "rerun", "targets": [target(current)]},
                )[1]["affected"]
                == 1
            )
            job.log(text)
            fresh = stream_state(base, job.id, 1, first["attempt_id"])["detail"]
            assert fresh["attempt_id"] != first["attempt_id"]
            assert fresh["log_chunk"]["reset"]
            assert [r["text"] for r in fresh["log_chunk"]["records"]] == [text]
            if text == "second attempt":
                historical = stream_state(
                    base, job.id, 1, first["attempt_id"], first["attempt_id"]
                )
                assert historical["detail"]["attempt_id"] == first["attempt_id"]
                assert historical["detail"]["log_chunk"]["records"] == []
        evicted = stream_state(
            base, job.id, 1, first["attempt_id"], first["attempt_id"]
        )
        assert evicted["detail"] is None and evicted["history_error"]


def test_unchanged_log_poll_is_small_after_a_large_retained_window():
    mgr = JobManager()
    job = mgr.add(Job("large-window", [PY, "-c", "pass"], pty=False))
    mgr.validate()
    for i in range(512):
        job.log(f"{i}:" + "x" * 1024)
    with running_server(mgr) as (_, base):
        initial = stream_state(base, job.id)
        chunk = initial["detail"]["log_chunk"]
        unchanged = stream_state(base, job.id, chunk["next_line"], chunk["attempt_id"])
        assert unchanged["detail"]["log_chunk"]["records"] == []
        assert len(json.dumps(unchanged)) < len(json.dumps(initial)) / 20
        assert all(
            "log_records" not in j and "logs" not in j for j in unchanged["jobs"]
        )


def test_rerun_readiness_waits_for_worker_cleanup(monkeypatch):
    finished_execution, release_cleanup = threading.Event(), threading.Event()
    execute = Job._execute

    def pause_before_cleanup(self, attempt):
        execute(self, attempt)
        finished_execution.set()
        release_cleanup.wait(5)

    monkeypatch.setattr(Job, "_execute", pause_before_cleanup)
    mgr = JobManager()
    job = mgr.add(Job("cleanup-window", [PY, "-c", "pass"], pty=False))
    with running_server(mgr) as (_, base):
        try:
            mgr.start()
            assert finished_execution.wait(5)
            during = state(base, job.id)
            assert during["jobs"][0]["status"] == "DONE"
            assert not during["jobs"][0]["can_rerun"]
            assert not during["detail"]["can_rerun"]
            assert (
                post(
                    base,
                    "/api/job/action",
                    {"action": "rerun", "targets": [target(during["detail"])]},
                )[1]["affected"]
                == 0
            )
        finally:
            release_cleanup.set()
        finish(mgr)
        ready = state(base, job.id)
        assert ready["jobs"][0]["can_rerun"]
        assert (
            post(
                base,
                "/api/job/action",
                {"action": "rerun", "targets": [target(ready["detail"])]},
            )[1]["affected"]
            == 1
        )
        finish(mgr)
