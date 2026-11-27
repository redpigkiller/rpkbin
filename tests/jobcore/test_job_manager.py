"""Behavioral regressions for shared scheduling, monitoring and attempt safety."""

import sys
import time
from dataclasses import FrozenInstanceError

import pytest


from rpkbin.jobcore import Job, JobManager, JobStatus, ProcessVerdict


def command(code, name="worker", **kwargs):
    return Job(name, [sys.executable, "-u", "-c", code], pty=False, **kwargs)


@pytest.fixture
def managers():
    instances = []

    def make(**kwargs):
        manager = JobManager(**kwargs)
        instances.append(manager)
        return manager

    yield make
    for manager in instances:
        manager.close()


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_normal_exit_without_flow_is_not_pass(managers):
    manager = managers()
    job = manager.add(command("print('ordinary output')"))
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is ProcessVerdict.NA


def test_retired_monitoring_api_is_absent(managers):
    import rpkbin.jobcore as api
    import rpkbin.jobcore.model as model
    import rpkbin.wave as wave

    job = command("pass")
    for name in ("track", "check", "on", "events", "context"):
        assert not hasattr(job, name)
    for name in ("Track", "Check", "CheckState", "Expect", "Event", "EventRef", "Events", "Subscription", "matcher"):
        for module in (api, model, wave):
            assert not hasattr(module, name)
    manager = managers()
    manager.add(job)
    manager.start()
    assert manager._drain(5)
    assert not hasattr(job.result, "checks")
    snapshot = job.to_snapshot_dict(include_logs=True, include_details=True)
    assert "checks" not in snapshot and "monitor_definitions" not in snapshot


@pytest.mark.parametrize("ending", ["", "\n"])
def test_prompt_is_waiting_before_send_and_send_clears_it(managers, ending):
    manager = managers()
    job = manager.add(command(
        f"import sys; sys.stdout.write({'cmd> ' + ending!r}); sys.stdout.flush(); print('REPLY=' + input(), flush=True)",
        prompts=("cmd> ",),
    ))
    manager.start()
    wait_for(lambda: job.status is JobStatus.WAITING_INPUT)
    assert job.send("__CTRL_C__")
    assert job.status is JobStatus.RUNNING
    assert manager._drain(5)
    assert "REPLY=__CTRL_C__" in job.logs()
    assert job.status is JobStatus.DONE


def test_old_control_cannot_target_rerun_and_report_is_frozen(managers):
    from rpkbin.jobcore._job_control import _ProcessControl

    manager = managers()
    job = manager.add(command("import sys; print('ready', flush=True); sys.stdin.readline()"))
    manager.start()
    wait_for(lambda: "ready" in job.logs())
    original = _ProcessControl(job, job._attempt)
    original_attempt = job._attempt
    original_id, original_submission = job.attempt_id, job.submission_id
    assert original.send("quit")
    assert manager._drain(5)
    report = job.result
    manager.rerun(job)
    wait_for(lambda: "ready" in job.logs())
    receipt = original.send("wrong attempt")
    assert receipt.status == "rejected" and "stale" in receipt.reason
    receipt = manager.control(job.id, "stop", expected_attempt=original_id,
                              expected_submission=original_submission)
    assert receipt.status == "rejected" and "stale" in receipt.reason
    assert job.send("quit")
    assert manager._drain(5)
    assert original_attempt.report is report
    with pytest.raises((FrozenInstanceError, AttributeError)):
        report.verdict = "PASS"


def test_definition_and_identity_freeze(managers):
    with pytest.raises(TypeError):
        command("pass", stop_policy="gracefil")
    job = managers().add(command("pass"))
    job._manager_ref.validate()
    for name, value in (("id", "different"), ("cmd", "something else")):
        with pytest.raises(AttributeError):
            setattr(job, name, value)
    with pytest.raises(TypeError):
        job.resources["slot"] = 1


def test_timeout_remains_fail_when_quit_exits_zero(managers):
    manager = managers()
    job = manager.add(
        command(
            "import sys; print('ready', flush=True); sys.stdin.readline()",
            timeout=0.2,
            stop_cmd="quit",
            stop_timeout=1,
        )
    )
    manager.start()
    assert manager._drain(6)
    assert job.returncode == 0
    assert job.result.verdict is ProcessVerdict.FAIL


def test_impossible_resource_rejected_before_any_dispatch(managers):
    manager = managers(resources={"slot": 1})
    good = manager.add(command("print('unexpected')", name="good"))
    manager.add(command("print('bad')", name="bad", resources={"slot": 2}))
    with pytest.raises(ValueError, match="capacity"):
        manager.start()
    assert good.pid is None
    assert not manager.started


def test_delayed_newline_does_not_clear_detected_prompt(managers):
    manager = managers()
    job = manager.add(
        command(
            "import sys,time; sys.stdout.write('cmd>'); sys.stdout.flush(); "
            "time.sleep(.1); sys.stdout.write('\\n'); sys.stdout.flush(); input()",
            prompts=("cmd>",),
        )
    )
    manager.start()
    wait_for(lambda: "" in job.logs())
    assert job.status is JobStatus.WAITING_INPUT
    assert job.send("quit")
    assert manager._drain(5)


def test_retry_releases_slot_and_requeues_fairly(managers, tmp_path):
    counter = tmp_path / "counter"
    code = (
        f"from pathlib import Path; p=Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; "
        "p.write_text(str(n)); print('A'+str(n)); raise SystemExit(1 if n==1 else 0)"
    )
    manager = managers(max_workers=1, resources={"slot": 1})
    first = manager.add(command(code, name="A", resources={"slot": 1}, max_retries=1))
    manager.add(command("print('B')", name="B", resources={"slot": 1}))
    order = []
    manager.add_log_listener(
        lambda record: (
            order.append(record["text"])
            if record["source"] == "stdout" and record["text"] in ("A1", "B", "A2")
            else None
        )
    )
    manager.start()
    assert manager._drain(8)
    assert order == ["A1", "B", "A2"]
    assert first.result.verdict is ProcessVerdict.NA
    assert first.history[0]["result"] == "FAIL"
    assert first.history[0]["retry_scheduled"]
    assert first.attempt_number == 2


def test_runtime_capacity_change_does_not_preempt(managers):
    manager = managers(max_workers=2, resources={"slot": 2})
    jobs = [
        manager.add(
            command("import time; time.sleep(0.3)", name=str(i), resources={"slot": 1})
        )
        for i in range(3)
    ]
    manager.start()
    wait_for(lambda: manager.snapshot()["active_workers"] == 2)
    manager.update_settings(max_workers=1)
    assert manager.snapshot()["active_workers"] == 2
    assert manager._drain(6)
    assert jobs[2]._attempt.started >= max(j._attempt.ended for j in jobs[:2])


def test_one_job_cannot_be_registered_with_two_managers(managers):
    first, second = managers(), managers()
    job = first.add(command("pass"))
    with pytest.raises(ValueError, match="different manager"):
        second.add(job)


def test_observer_snapshot_omits_unselected_details_caches_history_and_isolates_mutations(
    managers, monkeypatch
):
    manager = managers(max_workers=1, resources={"slot": 2})
    a = manager.add(command("raise SystemExit(1)", name="a", resources={"slot": 1}, max_retries=1))
    b = manager.add(command("print('ok')", name="b", resources={"slot": 1}))
    manager.start()
    assert manager._drain(8)
    assert len(a._history) == 1

    detail_calls = []
    for job in (a, b):
        for attempt in (job._attempt, *job._history):
            orig_project = attempt.sink.project_snapshot

            def track_project(snapshot, *, include_details, _orig=orig_project, _aid=attempt.id):
                detail_calls.append((_aid, include_details))
                snapshot["data"] = {"nested": {"items": [1, 2]}}
                if include_details:
                    snapshot["flow"] = {"code": "x" * 1024, "analyzers": [{"issues": []}]}
                return _orig(snapshot, include_details=include_details)

            monkeypatch.setattr(attempt.sink, "project_snapshot", track_project)
            monkeypatch.setattr(attempt.sink, "omit_details", lambda snap: snap.pop("flow", None))

    # 1. Observer snapshot with current attempt selected builds details ONLY for the selected job.
    detail_calls.clear()
    snap = manager._snapshot(
        selected_job_id=a.id,
        attempt_id=None,
        include_logs=False,
        log_cursor=(None, None),
    )
    assert detail_calls == [(a.attempt_id, True), (b.attempt_id, False)]
    rows = {row["id"]: row for row in snap["jobs"]}
    assert "flow" not in rows[a.id]
    assert "flow" not in rows[b.id]
    assert snap["detail"]["flow"]["code"] == "x" * 1024
    assert a._history[0].history_summary is not None

    # 2. Observer snapshot selecting a historical attempt skips details on current row,
    # builds details only on the selected historical attempt, and uses cached history_summary.
    detail_calls.clear()
    hist_id = a._history[0].id
    snap_hist = manager._snapshot(
        selected_job_id=a.id,
        attempt_id=hist_id,
        include_logs=False,
        log_cursor=(None, None),
    )
    assert detail_calls == [(a.attempt_id, False), (hist_id, True), (b.attempt_id, False)]
    assert "flow" not in {row["id"]: row for row in snap_hist["jobs"]}[a.id]
    assert snap_hist["detail"]["attempt_id"] == hist_id
    assert snap_hist["detail"]["flow"]["code"] == "x" * 1024

    # 3. Mutating returned snapshot structures never leaks into subsequent snapshots or live state.
    snap["resources"]["slot"] = 999
    rows[a.id]["resources"]["slot"] = 999
    rows[a.id]["data"]["nested"]["items"].append(99)
    rows[a.id]["history"][0]["capabilities"]["can_rerun"] = False
    snap["detail"]["flow"]["analyzers"].append({"mutated": True})

    fresh = manager._snapshot(
        selected_job_id=a.id,
        attempt_id=None,
        include_logs=False,
        log_cursor=(None, None),
    )
    fresh_rows = {row["id"]: row for row in fresh["jobs"]}
    assert fresh["resources"]["slot"] == 2
    assert fresh_rows[a.id]["resources"]["slot"] == 1
    assert fresh_rows[a.id]["data"]["nested"]["items"] == [1, 2]
    assert fresh_rows[a.id]["history"][0]["capabilities"]["can_rerun"] is True
    assert len(fresh["detail"]["flow"]["analyzers"]) == 1


def test_manager_control_send_does_not_hold_events_lock_during_write(managers, monkeypatch):
    manager = managers()
    job = manager.add(command("import sys; print('ready', flush=True); sys.stdin.readline()"))
    manager.start()
    wait_for(lambda: "ready" in job.logs())
    lock_states = []
    orig_write = job._attempt.process.write

    def check_lock_write(data, **kwargs):
        lock_states.append(job._events_lock._is_owned())
        return orig_write(data, **kwargs)

    monkeypatch.setattr(job._attempt.process, "write", check_lock_write)
    receipt = manager.control(
        job.id,
        "send",
        expected_attempt=job.attempt_id,
        expected_submission=job.submission_id,
        text="quit",
    )
    assert receipt.accepted
    assert lock_states == [False]
    assert manager._drain(5)


def test_logs_tail_validation_and_bounds(managers):
    manager = managers()
    job = manager.add(command("print('a'); print('b'); print('c')"))
    manager.start()
    assert manager._drain(5)
    assert job.logs() == ["a", "b", "c"]
    assert job.logs(tail=0) == []
    assert job.logs(tail=2) == ["b", "c"]
    for bad in (-1, True, False, "1"):
        with pytest.raises(ValueError, match="tail"):
            job.logs(tail=bad)


def test_relative_log_paths_pin_at_construction_and_empty_paths_rejected(tmp_path, monkeypatch):
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    existing_dir = tmp_path / "existing_dir"
    existing_dir.mkdir()

    with pytest.raises(ValueError, match="cwd"):
        Job("empty-cwd", [sys.executable, "-c", "pass"], cwd="")
    with pytest.raises(ValueError, match="log_file"):
        Job("empty-log", [sys.executable, "-c", "pass"], log_file="")
    with pytest.raises(ValueError, match="directory"):
        Job("dir-log", [sys.executable, "-c", "pass"], log_file=existing_dir)
    with pytest.raises(ValueError, match="log_dir"):
        JobManager(log_dir="")

    monkeypatch.chdir(d1)
    manager = JobManager(log_dir="rel_logs")
    try:
        manager.add(Job("relfile", [sys.executable, "-u", "-c", "print('one')"], pty=False, log_file="rel.log"))
        j_dir = manager.add(Job("reldir", [sys.executable, "-u", "-c", "print('two')"], pty=False))
        monkeypatch.chdir(d2)
        manager.start()
        assert manager._drain(5)
        assert "one" in (d1 / "rel.log").read_text(encoding="utf-8")
        assert not (d2 / "rel.log").exists()
        assert "two" in (d1 / "rel_logs" / f"{j_dir.id}.log").read_text(encoding="utf-8")
        assert not (d2 / "rel_logs").exists()
    finally:
        manager.close()


def test_whitespace_only_prompt_is_rejected():
    with pytest.raises(ValueError, match="prompts"):
        Job("ws", [sys.executable, "-c", "pass"], prompts=("   ",))


def test_validate_and_start_recheck_roster_and_get_job_locks_cond(managers):
    mgr = managers()
    first = mgr.add(command("pass", name="first"))
    replacement = command("pass", name="replacement")
    swapped = False
    orig_freeze = first._freeze

    def swap_during_freeze():
        nonlocal swapped
        orig_freeze()
        if not swapped:
            swapped = True
            with mgr._cond:
                mgr.jobs.remove(first)
                mgr.jobs.append(replacement)

    first._freeze = swap_during_freeze
    mgr.validate()
    assert swapped
    assert replacement._frozen
    assert mgr.get_job(replacement) is replacement
    assert mgr.get_job(first) is None


def test_job_accepts_pathlike_cmd(managers):
    from pathlib import Path

    single = Job("single", Path(sys.executable))
    assert single.cmd == (sys.executable,)
    mgr = managers()
    job = mgr.add(Job("seq", [Path(sys.executable), "-u", "-c", "print('JOB_PATH_OK')"], pty=False))
    mgr.start()
    assert mgr._drain(5)
    assert "JOB_PATH_OK" in job.logs()


def test_job_manager_context_manager_and_repr():
    job = command("pass", name="ctx")
    assert "Job(" in repr(job) and "name='ctx'" in repr(job) and "status='PENDING'" in repr(job)
    with JobManager() as mgr:
        mgr.add(job)
        assert "JobManager(" in repr(mgr) and "jobs=1" in repr(mgr)
        mgr.start()
        assert mgr._drain(5)
    assert mgr._closed is True
    with pytest.raises(RuntimeError, match="closed"):
        with mgr:
            pass
