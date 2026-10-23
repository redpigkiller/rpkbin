"""Behavioral regressions for shared scheduling, monitoring and attempt safety."""

import sys
import time
from dataclasses import FrozenInstanceError

import pytest

from rpkbin.job_manager import (
    Event,
    Expect,
    Job,
    JobManager,
    JobStatus,
    Result,
)


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


@pytest.mark.parametrize(
    "code,expectation,verdict",
    [
        ("print('COMPLETE')", Expect.PRESENT, Result.PASS),
        ("print('other')", Expect.PRESENT, Result.FAIL),
        ("print('COMPLETE')", Expect.ABSENT, Result.FAIL),
        ("print('other')", Expect.ABSENT, Result.PASS),
    ],
)
def test_check_contract(managers, code, expectation, verdict):
    manager = managers()
    job = manager.add(command(code))
    job.check("completion", match="COMPLETE", expect=expectation)
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is verdict
    assert job.status is JobStatus.DONE


def test_normal_exit_without_checks_is_not_pass(managers):
    manager = managers()
    job = manager.add(command("print('ordinary output')"))
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.NA


def test_all_checks_and_execution_must_succeed(managers):
    manager = managers()
    job = manager.add(command("print('FIRST'); raise SystemExit(4)"))
    job.check("first", match="FIRST")
    job.check("second", match="SECOND")
    manager.start()
    assert manager._drain(5)
    assert [c["state"] for c in job.result.checks] == ["OK", "FAIL"]
    assert job.result.verdict is Result.FAIL
    assert job.returncode == 4


def test_all_same_line_evidence_precedes_stop(managers):
    manager = managers()
    job = manager.add(command("import time; print('BAD', flush=True); time.sleep(10)"))
    first = job.check("first", match="BAD", expect=Expect.ABSENT)
    job.on(first.events.failed, stop=True)
    job.check("second", match="BAD", expect=Expect.ABSENT)
    job.check("never_seen", match="COMPLETE")
    manager.start()
    assert manager._drain(6)
    assert [c["state"] for c in job.result.checks] == ["FAIL", "FAIL", "INCOMPLETE"]
    assert job.result.verdict is Result.FAIL
    assert job.result.termination[0]["source"] == "monitor"


def test_broken_match_is_disabled_others_continue(managers):
    manager = managers()
    job = manager.add(command("print('one'); print('two'); print('COMPLETE')"))
    calls = []

    def broken(line):
        calls.append(line)
        raise RuntimeError("monitor unavailable")

    job.check("broken", match=broken)
    job.check("completion", match="COMPLETE")
    manager.start()
    assert manager._drain(5)
    assert len(calls) == 1
    assert [c["state"] for c in job.result.checks] == ["ERROR", "OK"]
    assert job.result.verdict is Result.ERROR
    assert job.returncode == 0


def test_track_error_keeps_previous_value_and_no_string_fallback(managers):
    manager = managers()
    job = manager.add(command("print('value=3'); print('value=bad'); print('value=4')"))
    track = job.track("value", r"value=(\w+)", cast=int)
    changes = []
    job.on(
        track.events.changed,
        callback=lambda j, old, new: changes.append((old, new)),
        once=False,
    )
    manager.start()
    assert manager._drain(5)
    assert track.value == 3
    assert changes == [(None, 3)]
    assert job.result.verdict is Result.ERROR


def test_error_subscription_can_request_stop(managers):
    manager = managers()
    job = manager.add(command("import time; print('x', flush=True); time.sleep(10)"))
    job.check("broken", match=lambda line: 1 / 0)
    job.on(job.events.error, stop=True)
    manager.start()
    assert manager._drain(6)
    assert job.result.verdict is Result.ERROR
    assert job.status is JobStatus.CANCELLED


def test_fail_and_error_both_remain_visible(managers):
    manager = managers()
    job = manager.add(command("print('BAD')"))
    job.check("forbidden", match="BAD", expect=Expect.ABSENT)
    job.check("broken", match=lambda line: 1 / 0)
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.FAIL
    assert len(job.result.errors) == 1


def test_warning_counts_hits_not_once_notifications(managers):
    manager = managers()
    job = manager.add(command("print('WARN'); print('WARN'); print('WARN')"))
    job.on(match="WARN", warn=True, name="review", once=True)
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.NA
    assert job.result.warnings[0]["count"] == 3
    assert sum("Warning [review]" in line for line in job.logs()) == 1


def test_management_log_does_not_reenter_monitor(managers):
    manager = managers()
    job = manager.add(command("print('hello')"))
    job.on(callback=lambda j, line: j.log("BAD"), once=False)
    job.check("not_management", match="BAD", expect=Expect.ABSENT)
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.PASS


def test_completion_callback_failure_does_not_rewrite_result(managers):
    manager = managers()
    job = manager.add(command("print('COMPLETE')"))
    job.check("completion", match="COMPLETE")

    def notification(j, attempt):
        assert j.result.verdict is Result.PASS
        raise RuntimeError("notification unavailable")

    job.on(job.events.attempt_finished, callback=notification)
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.PASS
    assert not job.result.errors
    assert job.to_snapshot_dict()["diagnostics"]


def test_tick_has_explicit_time_source(managers):
    manager = managers()
    job = manager.add(command("import time; time.sleep(10)"))
    deadline = job.check(
        "deadline",
        on=Event.TICK,
        every=0.03,
        match=lambda j, elapsed: elapsed > 0.06,
        expect=Expect.ABSENT,
    )
    job.on(deadline.events.failed, stop=True)
    manager.start()
    assert manager._drain(6)
    assert job.result.verdict is Result.FAIL


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
    assert job.result.verdict is Result.FAIL


def test_text_send_and_prompt_does_not_get_stuck(managers):
    manager = managers()
    job = manager.add(
        command(
            "import sys; print('cmd> ', end='', flush=True); print('GOT:' + sys.stdin.readline().strip())",
            prompts=("cmd> ",),
        )
    )
    job.on(match=r"cmd> ", callback=lambda j, line: j.send("__CTRL_C__"))
    job.check("echo", match="GOT:__CTRL_C__")
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.PASS
    assert job.status is JobStatus.DONE


@pytest.mark.parametrize("ending", ["", "\n"])
def test_prompt_status_is_ready_before_callback_and_send_clears_it(managers, ending):
    manager = managers()
    job = manager.add(
        command(
            f"import sys; sys.stdout.write({'cmd> ' + ending!r}); sys.stdout.flush(); "
            "print('REPLY=' + input(), flush=True)",
            prompts=("cmd> ",),
        )
    )
    observed = []

    def respond(current, line):
        observed.append(current.status)
        assert current.send("hello")
        observed.append(current.status)

    job.on(match=r"^cmd>", callback=respond)
    job.check("reply", match=r"^REPLY=hello$")
    manager.start()
    assert manager._drain(5)
    assert observed == [JobStatus.WAITING_INPUT, JobStatus.RUNNING]
    assert job.result.verdict is Result.PASS


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
    assert first.result.verdict is Result.NA
    assert first.history[0]["result"] == "FAIL"
    assert first.history[0]["retry_scheduled"]
    assert first.attempt_number == 2


def test_old_control_cannot_target_new_attempt_and_report_is_frozen(managers):
    manager = managers()
    job = manager.add(
        command("import sys; print('ready', flush=True); sys.stdin.readline()")
    )
    bound = []
    job.on(match="ready", callback=lambda j, line: bound.append(j))
    manager.start()
    wait_for(lambda: len(bound) == 1)
    original_id, original_submission = job.attempt_id, job.submission_id
    assert job.send("quit")
    assert manager._drain(5)
    original_report = job.result
    manager.rerun(job)
    wait_for(lambda: len(bound) == 2)
    with pytest.raises(ValueError, match="stale"):
        bound[0].send("wrong target")
    with pytest.raises(ValueError, match="stale"):
        manager.control(
            job.id,
            "stop",
            expected_attempt=original_id,
            expected_submission=original_submission,
        )
    assert job.send("quit")
    assert manager._drain(5)
    assert bound[0].result is original_report
    with pytest.raises((FrozenInstanceError, AttributeError)):
        original_report.verdict = Result.PASS
    assert len(job.history) == 1


def test_bounded_logs_keep_failure_evidence(managers):
    manager = managers()
    job = manager.add(
        command("print('BAD'); [print('noise') for _ in range(30)]", max_log_lines=5)
    )
    job.check("no_bad", match="BAD", expect=Expect.ABSENT)
    manager.start()
    assert manager._drain(5)
    snapshot = job.to_snapshot_dict(include_logs=True)
    assert len(snapshot["logs"]) == 5
    assert snapshot["log_records"][0]["line_no"] > 1
    assert snapshot["checks"][0]["evidence"]["line"] == "BAD"
    assert job.result.verdict is Result.FAIL


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


def test_monitor_definitions_freeze_and_enum_typoes_rejected(managers):
    with pytest.raises(TypeError):
        command("pass", stop_policy="gracefil")
    job = command("print('value=1')")
    with pytest.raises(TypeError):
        job.check("bad", match="x", expect="present")
    manager = managers()
    manager.add(job)
    manager.validate()
    with pytest.raises(AttributeError):
        job.cmd = "something else"
    with pytest.raises(RuntimeError):
        job.on(match="x", stop=True)
    with pytest.raises(TypeError):
        job.resources["slot"] = 1


def test_eof_check_event_runs_before_final_result(managers):
    manager = managers()
    job = manager.add(command("print('ordinary output')"))
    missing = job.check("completion", match="COMPLETE")
    job.on(
        missing.events.failed,
        callback=lambda j, check: j.log("Missing: " + check["name"]),
    )
    manager.start()
    assert manager._drain(5)
    assert "Missing: completion" in job.logs()
    assert job.result.verdict is Result.FAIL


def test_failed_track_does_not_manufacture_missing_check_fail(managers):
    manager = managers()
    job = manager.add(command("print('value=bad')"))
    value = job.track("value", r"value=(\w+)", cast=int)
    job.check("observed", on=value.events.changed, match=lambda j, old, new: True)
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.ERROR
    assert job.result.checks[0]["state"] == "ERROR"
    assert len(job.result.errors) == 1


def test_final_notification_warning_does_not_change_frozen_warnings(managers):
    manager = managers()
    job = manager.add(command("print('COMPLETE')"))
    job.check("completion", match="COMPLETE")
    job.on(job.events.attempt_finished, warn=True, name="finished_notification")
    manager.start()
    assert manager._drain(5)
    assert not job.result.warnings
    assert job.to_snapshot_dict()["warning_count"] == 0
    assert any("Notification [finished_notification]" in text for text in job.logs())


def test_bound_track_getters_keep_original_retry_attempt(managers, tmp_path):
    counter = tmp_path / "track_counter"
    code = (
        f"from pathlib import Path; p=Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; "
        "p.write_text(str(n)); print('value='+str(n)); raise SystemExit(1 if n==1 else 0)"
    )
    manager = managers(max_workers=1)
    job = manager.add(command(code, max_retries=1))
    value = job.track("value", r"value=(\d+)", cast=int)
    observed = job.check(
        "observed", on=value.events.changed, match=lambda j, old, new: new > 0
    )
    records = []
    job.on(
        job.events.attempt_finished,
        callback=lambda j, attempt: records.append(
            (j.attempt_number, value.value, observed.state)
        ),
        once=False,
    )
    manager.start()
    assert manager._drain(8)
    assert records == [(1, 1, "OK"), (2, 2, "OK")]


def test_close_prevents_new_jobs_from_completion_callbacks(managers):
    manager = managers()
    job = manager.add(
        command(
            "import sys; print('ready', flush=True); sys.stdin.readline()",
            stop_cmd="quit",
        )
    )
    rejected = []

    def notification(j, attempt):
        try:
            manager.add(command("print('must not run')", name="late"))
        except RuntimeError:
            rejected.append(True)

    job.on(job.events.attempt_finished, callback=notification)
    manager.start()
    wait_for(lambda: any(line == "ready" for line in job.logs()))
    manager.close()
    assert rejected == [True]
    assert len(manager.jobs) == 1


def test_final_status_event_preserves_stopping_source(managers):
    manager = managers()
    job = manager.add(
        command(
            "import sys; print('ready', flush=True); sys.stdin.readline()",
            stop_cmd="quit",
        )
    )
    transitions = []
    job.on(
        job.events.status_changed,
        callback=lambda j, old, new: transitions.append((old, new)),
        once=False,
    )
    manager.start()
    wait_for(lambda: "ready" in job.logs())
    job.stop()
    assert manager._drain(5)
    assert transitions[-1] == (JobStatus.STOPPING, JobStatus.CANCELLED)
    assert job.result.termination[0]["actual"] == "GRACEFUL"


def test_one_job_cannot_be_registered_with_two_managers(managers):
    first, second = managers(), managers()
    job = first.add(command("pass"))
    with pytest.raises(ValueError, match="different manager"):
        second.add(job)


def test_frozen_identity_and_track_are_not_editable(managers):
    manager = managers()
    job = manager.add(command("print('v=1')"))
    value = job.track("v", r"v=(\d+)", cast=int)
    manager.validate()
    with pytest.raises(AttributeError):
        job.id = "different"
    with pytest.raises(AttributeError):
        value.pattern = "different"
    with pytest.raises(AttributeError):
        value.events.changed = Event.LOG


def test_snapshot_is_detached_and_history_keeps_original_priority(managers):
    manager = managers()
    job = manager.add(command("print('value=1')", priority=3))
    job.track("nested", r"value=(\d+)", cast=lambda text: {"items": [int(text)]})
    manager.start()
    assert manager._drain(5)
    original = job.attempt_id
    snapshot = job.to_snapshot_dict()
    snapshot["data"]["nested"]["items"].append(99)
    assert job.data["nested"]["items"] == (1,)
    manager.pause()
    manager.rerun(job)
    manager.set_priority(job.id, 9)
    assert job.to_snapshot_dict()["priority"] == 9
    assert job.to_snapshot_dict(attempt_id=original)["priority"] == 3


def test_runtime_log_retention_shrink_enlarge_and_history_isolation(managers):
    manager = managers()
    job = manager.add(
        command(
            "import sys\n"
            "for i in range(1, 6):\n"
            "    print(f'LINE={i}', flush=True)\n"
            "print('READY', flush=True)\n"
            "sys.stdin.readline()\n"
            "for i in range(6, 9):\n"
            "    print(f'LINE={i}', flush=True)\n"
        )
    )
    other = manager.add(command("print('OTHER=1')"))
    assert job.max_log_lines == 10000
    tracked = job.track("last_line", r"LINE=(\d+)", cast=int)
    chk = job.check("first_line_seen", match="LINE=1", expect=Expect.PRESENT)
    manager.validate()

    with pytest.raises(AttributeError):
        job.max_log_lines = 5
    for bad in (0, -3, True, "10"):
        with pytest.raises(ValueError):
            job.set_max_log_lines(bad)

    manager.start()
    wait_for(lambda: "READY" in job.logs())
    before = job.to_snapshot_dict(include_logs=True)
    first_evidence = before["checks"][0]["evidence"]
    assert first_evidence["line"] == "LINE=1"

    evicted = job.set_max_log_lines(3, expected_attempt=job.attempt_id)
    assert evicted == before["log_count"] - 3
    assert job.max_log_lines == 3
    snap_mid = job.to_snapshot_dict(include_logs=True)
    assert snap_mid["log_records"] == before["log_records"][-3:]
    assert snap_mid["total_lines"] == before["total_lines"]
    assert snap_mid["log_count"] == 3
    assert chk.state == "OK"
    assert snap_mid["checks"][0]["evidence"] == first_evidence

    # Enlarging does not restore already evicted lines, but allows more lines to be kept
    assert job.set_max_log_lines(5) == 0
    assert job.to_snapshot_dict(include_logs=True)["log_records"] == snap_mid["log_records"]

    job.send("go")
    assert manager._drain(5)

    first_attempt_id = job.attempt_id
    snap_done = job.to_snapshot_dict(include_logs=True)
    assert [r["text"] for r in snap_done["log_records"][-3:]] == [
        "LINE=6", "LINE=7", "LINE=8"
    ]
    assert [r["line_no"] for r in snap_done["log_records"]] == list(
        range(snap_done["total_lines"] - 4, snap_done["total_lines"] + 1)
    )
    assert snap_done["total_lines"] > before["total_lines"]
    assert snap_done["log_count"] == 5
    assert tracked.value == 8
    assert snap_done["result"] == "PASS"
    assert snap_done["checks"][0]["evidence"] == first_evidence

    # Shrinking after completion for future attempts must not retroactively truncate completed history
    manager.rerun(job)
    wait_for(lambda: "READY" in job.logs())
    job.send("go")
    assert manager._drain(5)
    job.set_max_log_lines(2)
    hist_snap = job.to_snapshot_dict(include_logs=True, attempt_id=first_attempt_id)
    assert hist_snap["log_records"] == snap_done["log_records"]
    assert hist_snap["attempt_max_log_lines"] == 5

    # Batch manager.set_max_log_lines applies to all jobs
    manager.set_max_log_lines(4)
    assert job.max_log_lines == 4 and other.max_log_lines == 4


def test_runtime_history_retention_evicts_oldest_without_recomputing_result(managers):
    manager = managers()
    assert manager.max_history == 20
    job = manager.add(command("print('OK')"))
    job.check("ok", match="OK", expect=Expect.PRESENT)
    manager.start()
    assert manager._drain(5)
    a1 = job.attempt_id

    for _ in range(3):
        manager.rerun(job)
        assert manager._drain(5)

    assert len(job.history) == 3
    a2, a3 = job.history[1]["attempt_id"], job.history[2]["attempt_id"]

    for bad in (0, -1, False, "2"):
        with pytest.raises(ValueError):
            manager.set_max_history(bad)

    res = manager.update_settings(max_history=2)
    assert res["evicted_history"] == 1
    assert manager.max_history == 2
    assert [h["attempt_id"] for h in job.history] == [a2, a3]
    assert job.to_snapshot_dict()["history_truncated"] == 1
    with pytest.raises(ValueError, match="no longer retained"):
        job.to_snapshot_dict(attempt_id=a1)
    assert job.to_snapshot_dict(attempt_id=a2)["result"] == "PASS"
    assert job.to_snapshot_dict()["result"] == "PASS"
