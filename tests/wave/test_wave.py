"""
test_wave.py — Unit tests for rpkbin.wave (bottom layer: job, hook, session).

Coverage targets
----------------
WaveJobMixin
  - add_parser: callback injected once; results merged into parsed_data
  - add_parser: parser exceptions don't crash the job
  - add_hook(log_matches): fires on matching log line, respects policy=once/always
  - add_hook(data_equals): fires when key reaches target value
  - add_hook(elapsed_exceeds): fires after timeout (via timer thread)
  - add_hook(on_done): fires on job success
  - add_hook(on_fail): fires on job failure
  - emit(): appends to events, fields correct
  - update_parsed_data(): merges data, fires data_equals hooks
  - Injection guard: each on_log/on_done/on_fail callback registered exactly once

Session
  - configure() before start() is respected
  - add() buffers jobs before start, dispatches after start
  - reset() returns session to a clean state
  - timer thread calls _check_timer_hooks() on running Wave jobs
"""

from __future__ import annotations

import re
import shutil
import signal
import asyncio
import logging
import threading
import time
from pathlib import Path
from itertools import count

import pytest

from rpkbin.job_manager import JobManager, PENDING, DONE, FAILED, CANCELLED, RUNNING
from rpkbin.wave.hook import Hook, HookWhen
from rpkbin.wave.job import WaveCmdJob, WaveFuncJob, WaveJobMixin
from rpkbin.wave.session import Session

_TMP_COUNTER = count()


@pytest.fixture(autouse=True)
def _reset_global_wave_singleton():
    """Avoid cross-test state leakage through the module-level session singleton."""
    from rpkbin.wave.session import session as global_session
    global_session.reset()
    yield
    global_session.reset()


# The custom tmp_path fixture has been removed. Tests will now use pytest's built-in tmp_path.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(job, *, max_workers: int = 1, resources: dict | None = None):
    """Run a single job with a fresh JobManager and wait for completion."""
    with JobManager(max_workers=max_workers, resources=resources) as mgr:
        mgr.add(job)
        mgr.wait()


# ---------------------------------------------------------------------------
# Hook unit tests (no scheduler involved)
# ---------------------------------------------------------------------------

class TestHookWhen:
    def test_log_matches_factory(self):
        when = Hook.log_matches(r"PASS")
        assert when.type == "log_matches"
        assert when.pattern is not None
        assert when.pattern.search("PASS completed") is not None

    def test_data_equals_factory(self):
        when = Hook.data_equals("result", "pass")
        assert when.type == "data_equals"
        assert when.key == "result"
        assert when.value == "pass"

    def test_elapsed_exceeds_factory(self):
        when = Hook.elapsed_exceeds(5.0)
        assert when.type == "elapsed_exceeds"
        assert when.seconds == 5.0

    def test_on_done_factory(self):
        when = Hook.on_done()
        assert when.type == "on_done"

    def test_on_fail_factory(self):
        when = Hook.on_fail()
        assert when.type == "on_fail"

    def test_on_retry_factory(self):
        when = Hook.on_retry()
        assert when.type == "on_retry"

    def test_on_cancel_factory(self):
        when = Hook.on_cancel()
        assert when.type == "on_cancel"


class TestHookFire:
    def test_once_policy_fires_once(self):
        count = [0]
        hook = Hook(when=Hook.log_matches(r"x"), action=lambda j, ctx: count.__setitem__(0, count[0] + 1), policy="once")
        hook._fire(None, {})
        hook._fire(None, {})
        hook._fire(None, {})
        assert count[0] == 1

    def test_always_policy_fires_every_time(self):
        count = [0]
        hook = Hook(when=Hook.log_matches(r"x"), action=lambda j, ctx: count.__setitem__(0, count[0] + 1), policy="always")
        hook._fire(None, {})
        hook._fire(None, {})
        hook._fire(None, {})
        assert count[0] == 3

    def test_action_exception_is_silenced(self):
        def bad_action(j, ctx):
            raise RuntimeError("boom")
        hook = Hook(when=Hook.on_done(), action=bad_action)
        job = WaveFuncJob("test", lambda: None)
        hook._fire(job, {})  # must NOT raise
        assert len(job.events) == 1
        assert job.events[0]["tag"] == "hook_error"
        assert job.events[0]["source"] == "system"

    def test_concurrent_once_fires_exactly_once(self):
        """Two threads race to fire the same once-hook."""
        count = [0]
        lock = threading.Lock()

        def action(j, ctx):
            with lock:
                count[0] += 1

        hook = Hook(when=Hook.log_matches(r"x"), action=action, policy="once")
        threads = [threading.Thread(target=hook._fire, args=(None, {})) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert count[0] == 1


# ---------------------------------------------------------------------------
# WaveJobMixin — parser tests
# ---------------------------------------------------------------------------

class TestAddParser:
    def test_parser_result_merged_into_parsed_data(self):
        def my_parser(line):
            if "STATE=ACTIVE" in line:
                return {"state": "ACTIVE"}
            return {}

        job = WaveFuncJob("test", lambda: None)
        job.add_parser(my_parser)
        job._handle_log(job, "STATE=ACTIVE chip1")
        assert job.parsed_data["state"] == "ACTIVE"

    def test_multiple_parsers_merge(self):
        def p1(line):
            return {"a": "1"} if "A" in line else {}

        def p2(line):
            return {"b": "2"} if "B" in line else {}

        job = WaveFuncJob("test", lambda: None)
        job.add_parser(p1)
        job.add_parser(p2)
        job._handle_log(job, "A B")
        assert job.parsed_data == {"a": "1", "b": "2"}

    def test_parser_exception_does_not_crash(self):
        def bad_parser(line):
            raise ValueError("bad")

        job = WaveFuncJob("test", lambda: None)
        job.add_parser(bad_parser)
        # Must not raise
        job._handle_log(job, "anything")
        assert len(job.events) == 1
        assert job.events[0]["tag"] == "parser_error"
        assert job.events[0]["source"] == "system"

    def test_on_log_injected_exactly_once(self):
        job = WaveFuncJob("test", lambda: None)
        job.add_parser(lambda line: {})
        job.add_parser(lambda line: {})
        job.add_parser(lambda line: {})
        # Bound method equality works in Python 3: same instance + same function
        assert job._on_log_cbs.count(job._handle_log) == 1


# ---------------------------------------------------------------------------
# WaveJobMixin — hook tests (log_matches)
# ---------------------------------------------------------------------------

class TestLogMatchesHook:
    def test_hook_fires_on_match(self):
        fired = [False]
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.log_matches(r"FAIL"), action=lambda j, ctx: fired.__setitem__(0, True)))
        job._handle_log(job, "simulation FAIL detected")
        assert fired[0]

    def test_hook_does_not_fire_on_no_match(self):
        fired = [False]
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.log_matches(r"FAIL"), action=lambda j, ctx: fired.__setitem__(0, True)))
        job._handle_log(job, "all good")
        assert not fired[0]

    def test_hook_provides_match_in_ctx(self):
        ctx_received = [None]
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(
            when=Hook.log_matches(r"CHIP(\d)=(\w+)"),
            action=lambda j, ctx: ctx_received.__setitem__(0, ctx),
        ))
        job._handle_log(job, "STATE: CHIP1=ACTIVE")
        assert ctx_received[0] is not None
        assert ctx_received[0]["match"].group(1) == "1"
        assert ctx_received[0]["match"].group(2) == "ACTIVE"
        assert "CHIP1=ACTIVE" in ctx_received[0]["line"]

    def test_log_matches_on_log_injected_once(self):
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.log_matches(r"A"), action=lambda j, ctx: None))
        job.add_hook(Hook(when=Hook.log_matches(r"B"), action=lambda j, ctx: None))
        assert job._on_log_cbs.count(job._handle_log) == 1

    def test_once_policy_with_log_matches(self):
        count = [0]
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.log_matches(r"HIT"), action=lambda j, ctx: count.__setitem__(0, count[0] + 1), policy="once"))
        job._handle_log(job, "HIT")
        job._handle_log(job, "HIT again")
        assert count[0] == 1

    def test_always_policy_with_log_matches(self):
        count = [0]
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.log_matches(r"HIT"), action=lambda j, ctx: count.__setitem__(0, count[0] + 1), policy="always"))
        job._handle_log(job, "HIT")
        job._handle_log(job, "HIT")
        assert count[0] == 2


# ---------------------------------------------------------------------------
# WaveJobMixin — hook tests (data_equals)
# ---------------------------------------------------------------------------

class TestDataEqualsHook:
    def test_fires_when_value_matches(self):
        fired = [False]
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.data_equals("result", "pass"), action=lambda j, ctx: fired.__setitem__(0, True)))
        job.update_parsed_data({"result": "pass"})
        assert fired[0]

    def test_does_not_fire_when_value_differs(self):
        fired = [False]
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.data_equals("result", "pass"), action=lambda j, ctx: fired.__setitem__(0, True)))
        job.update_parsed_data({"result": "fail"})
        assert not fired[0]

    def test_fires_via_parser_path(self):
        """data_equals hook should fire when data is updated through a parser."""
        fired = [False]
        job = WaveFuncJob("test", lambda: None)
        job.add_parser(lambda line: {"status": "done"} if "DONE" in line else {})
        job.add_hook(Hook(when=Hook.data_equals("status", "done"), action=lambda j, ctx: fired.__setitem__(0, True)))
        job._handle_log(job, "DONE")
        assert fired[0]

    def test_ctx_contains_key_and_value(self):
        ctx_received = [None]
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(
            when=Hook.data_equals("chip", "ACTIVE"),
            action=lambda j, ctx: ctx_received.__setitem__(0, ctx),
        ))
        job.update_parsed_data({"chip": "ACTIVE"})
        assert ctx_received[0] == {"key": "chip", "value": "ACTIVE"}


# ---------------------------------------------------------------------------
# WaveJobMixin — emit() and events
# ---------------------------------------------------------------------------

class TestEmit:
    def test_emit_appends_event(self):
        job = WaveFuncJob("test", lambda: None)
        job.emit("alert", "something happened")
        assert len(job.events) == 1
        ev = job.events[0]
        assert ev["tag"] == "alert"
        assert ev["message"] == "something happened"
        assert ev["source"] == "user"
        assert "time" in ev

    def test_emit_multiple(self):
        job = WaveFuncJob("test", lambda: None)
        job.emit("a", "first")
        job.emit("b", "second")
        assert len(job.events) == 2
        assert job.events[0]["tag"] == "a"
        assert job.events[1]["tag"] == "b"

    def test_tui_notify_called_on_emit(self):
        notified = [None]
        job = WaveFuncJob("test", lambda: None)
        job._tui_notify = lambda j: notified.__setitem__(0, j)
        job.emit("test", "hi")
        assert notified[0] is job


# ---------------------------------------------------------------------------
# WaveJobMixin — update_parsed_data()
# ---------------------------------------------------------------------------

class TestUpdateParsedData:
    def test_merges_into_existing(self):
        job = WaveFuncJob("test", lambda: None)
        job.update_parsed_data({"a": "1"})
        job.update_parsed_data({"b": "2"})
        assert job.parsed_data == {"a": "1", "b": "2"}

    def test_later_value_overwrites_earlier(self):
        job = WaveFuncJob("test", lambda: None)
        job.update_parsed_data({"chip": "idle"})
        job.update_parsed_data({"chip": "active"})
        assert job.parsed_data["chip"] == "active"

    def test_empty_update_is_noop(self):
        job = WaveFuncJob("test", lambda: None)
        job.update_parsed_data({})
        assert job.parsed_data == {}

    def test_tui_notify_called(self):
        notified = [0]
        job = WaveFuncJob("test", lambda: None)
        job._tui_notify = lambda j: notified.__setitem__(0, notified[0] + 1)
        job.update_parsed_data({"k": "v"})
        assert notified[0] == 1

    def test_data_equals_uses_same_snapshot_as_update(self):
        seen = []
        proceed = threading.Event()
        captured = threading.Event()
        job = WaveFuncJob("test", lambda: None)

        def action(j, ctx):
            seen.append(ctx["value"])

        original = job._check_data_equals_hooks

        def delayed(hooks, data):
            captured.set()
            proceed.wait(timeout=1.0)
            original(hooks, data)

        job._check_data_equals_hooks = delayed  # type: ignore[method-assign]
        job.add_hook(Hook(when=Hook.data_equals("chip", "ACTIVE"), action=action))

        thread = threading.Thread(target=job.update_parsed_data, args=({"chip": "ACTIVE"},))
        thread.start()
        assert captured.wait(timeout=1.0)

        job.update_parsed_data({"chip": "IDLE"})
        proceed.set()
        thread.join(timeout=1.0)

        assert seen == ["ACTIVE"]

    def test_parser_and_log_hook_share_single_on_log_callback(self):
        job = WaveFuncJob("test", lambda: None)
        job.add_parser(lambda line: {"state": "seen"} if "HIT" in line else {})
        job.add_hook(Hook(when=Hook.log_matches(r"HIT"), action=lambda j, ctx: None))
        assert job._on_log_cbs.count(job._handle_log) == 1


# ---------------------------------------------------------------------------
# Integration tests (WaveJob + real scheduler)
# ---------------------------------------------------------------------------

class TestOnDoneHook:
    def test_fires_on_successful_job(self):
        fired = threading.Event()
        job = WaveFuncJob("ok", lambda: "result")
        job.add_hook(Hook(when=Hook.on_done(), action=lambda j, ctx: fired.set()))
        _run(job)
        assert fired.wait(timeout=1.0)

    def test_does_not_fire_on_failed_job(self):
        fired = threading.Event()
        def bad():
            raise RuntimeError("oops")
        job = WaveFuncJob("bad", bad)
        job.add_hook(Hook(when=Hook.on_done(), action=lambda j, ctx: fired.set()))
        _run(job)
        assert not fired.wait(timeout=0.2)

    def test_on_done_injected_once(self):
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.on_done(), action=lambda j, ctx: None))
        job.add_hook(Hook(when=Hook.on_done(), action=lambda j, ctx: None))
        assert job._on_done_cbs.count(job._handle_on_done) == 1


class TestOnFailHook:
    def test_fires_on_failed_job(self):
        fired = threading.Event()
        err_received = [None]

        def bad():
            raise RuntimeError("simulated failure")

        def action(j, ctx):
            fired.set()
            err_received[0] = ctx.get("error")

        job = WaveFuncJob("bad", bad)
        job.add_hook(Hook(when=Hook.on_fail(), action=action))
        _run(job)
        assert fired.wait(timeout=1.0)
        assert err_received[0] is not None

    def test_does_not_fire_on_done_job(self):
        fired = threading.Event()
        job = WaveFuncJob("ok", lambda: "ok")
        job.add_hook(Hook(when=Hook.on_fail(), action=lambda j, ctx: fired.set()))
        _run(job)
        assert not fired.wait(timeout=0.2)

    def test_on_fail_injected_once(self):
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.on_fail(), action=lambda j, ctx: None))
        job.add_hook(Hook(when=Hook.on_fail(), action=lambda j, ctx: None))
        assert job._on_fail_cbs.count(job._handle_on_fail) == 1


class TestOnRetryHook:
    def test_fires_before_retry_attempt(self):
        attempts = []
        retried = threading.Event()

        def flaky():
            attempts.append("run")
            if len(attempts) == 1:
                raise RuntimeError("boom")
            retried.set()

        seen = []
        job = WaveFuncJob("flaky", flaky, max_retries=1)
        job.add_hook(Hook(when=Hook.on_retry(), action=lambda j, ctx: seen.append(ctx["attempt"])))

        _run(job)

        assert seen == [1]
        assert retried.is_set()
        assert job.status == DONE


class TestOnCancelHook:
    def test_cancel_hook_receives_skipped_false_for_normal_cancel(self):
        seen = []
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.on_cancel(), action=lambda j, ctx: seen.append(ctx["skipped"])))

        job.cancel()

        assert seen == [False]

    def test_cancel_hook_receives_skipped_true_for_skip(self):
        seen = []
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(when=Hook.on_cancel(), action=lambda j, ctx: seen.append(ctx["skipped"])))

        job.skip()

        assert seen == [True]


class TestElapsedExceedsHook:
    def test_fires_after_timeout(self):
        fired = threading.Event()
        ready = threading.Event()

        def slow():
            ready.set()
            time.sleep(3)

        job = WaveFuncJob("slow", slow)
        job.add_hook(Hook(
            when=Hook.elapsed_exceeds(1.0),
            action=lambda j, ctx: fired.set(),
        ))

        # Use a real Session so the timer thread runs
        sess = Session()
        sess.add(job)
        sess._start(tui_notify=None)

        assert ready.wait(timeout=2.0)
        assert fired.wait(timeout=3.0)

        job.cancel()  # stop the slow job
        sess._stop()

        assert fired.is_set()

    def test_does_not_fire_before_timeout(self):
        fired = [False]

        def fast():
            return "done"

        job = WaveFuncJob("fast", fast)
        job.add_hook(Hook(
            when=Hook.elapsed_exceeds(10.0),
            action=lambda j, ctx: fired.__setitem__(0, True),
        ))

        sess = Session()
        sess.add(job)
        sess._start(tui_notify=None)
        sess._stop()

        assert not fired[0]


class TestWaveCmdJob:
    def test_parser_runs_on_real_output(self):
        job = WaveCmdJob("test", "echo RESULT=42")

        def my_parser(line):
            if m := re.search(r"RESULT=(\d+)", line):
                return {"result": m.group(1)}
            return {}

        job.add_parser(my_parser)
        _run(job)
        assert job.parsed_data.get("result") == "42"

    def test_log_matches_hook_fires_on_real_output(self):
        fired = [False]
        job = WaveCmdJob("test", "echo CHECKPOINT reached")
        job.add_hook(Hook(
            when=Hook.log_matches(r"CHECKPOINT"),
            action=lambda j, ctx: fired.__setitem__(0, True),
        ))
        _run(job)
        assert fired[0]

    def test_on_done_hook_fires_on_success(self):
        fired = threading.Event()
        job = WaveCmdJob("test", "echo done")
        job.add_hook(Hook(when=Hook.on_done(), action=lambda j, ctx: fired.set()))
        _run(job)
        assert fired.wait(timeout=1.0)

    def test_on_fail_hook_fires_on_nonzero_exit(self):
        fired = threading.Event()
        job = WaveCmdJob("test", "exit 1")
        job.add_hook(Hook(when=Hook.on_fail(), action=lambda j, ctx: fired.set()))
        _run(job)
        assert fired.wait(timeout=1.0)

    def test_skip_after_done_does_not_mark_skipped(self):
        job = WaveCmdJob("test", "echo done")
        _run(job)
        assert job.status == DONE
        job.skip()
        assert job.status == DONE
        assert not job.is_skipped


class TestStopPolicy:
    def test_set_stop_policy_snapshot(self):
        job = WaveFuncJob("test", lambda: None)
        job.set_stop_policy(
            graceful_input="exit\n",
            graceful_signal=signal.SIGINT,
            graceful_timeout=7.0,
        )
        assert job.peek_stop_policy() == {
            "graceful_input": "exit\n",
            "graceful_signal": signal.SIGINT,
            "graceful_timeout": 7.0,
        }

    def test_request_stop_prefers_graceful_steps(self, monkeypatch):
        calls = []
        job = WaveFuncJob("test", lambda: None)
        job.set_stop_policy(
            graceful_input="exit\n",
            graceful_signal=signal.SIGINT,
            graceful_timeout=1.0,
        )
        monkeypatch.setattr(job, "send_input", lambda text: calls.append(("input", text)), raising=False)
        monkeypatch.setattr(job, "send_signal", lambda sig: calls.append(("signal", sig)), raising=False)
        monkeypatch.setattr(job, "_schedule_force_cancel", lambda timeout: calls.append(("fallback", timeout)))
        with job._lock:
            job._status = RUNNING

        result = job.request_stop()

        assert result == "graceful"
        assert calls == [
            ("input", "exit\n"),
            ("signal", signal.SIGINT),
            ("fallback", 1.0),
        ]

    def test_request_stop_graceful_only_without_policy_is_unsupported(self):
        job = WaveFuncJob("test", lambda: None)
        with job._lock:
            job._status = RUNNING
        assert job.request_stop(graceful_only=True) == "unsupported"

    def test_request_stop_on_pending_cancels_immediately(self):
        job = WaveFuncJob("test", lambda: None)
        assert job.request_stop() == "cancelled_pending"
        assert job.status == CANCELLED


# ---------------------------------------------------------------------------
# Session tests
# ---------------------------------------------------------------------------

class TestSession:
    def test_configure_sets_max_workers(self):
        sess = Session()
        sess.configure(max_workers=8)
        assert sess._config["max_workers"] == 8

    def test_configure_after_start_warns_and_is_ignored(self):
        sess = Session()
        finished = threading.Event()
        job = WaveFuncJob("j", lambda: finished.wait(timeout=2.0))
        sess.add(job)
        sess._start()
        try:
            with pytest.warns(UserWarning, match="already started"):
                sess.configure(max_workers=99)
            # Manager was created before the warning; its config is unmodified.
            assert sess._manager._max_workers != 99
        finally:
            finished.set()
            sess._stop()

    def test_configure_accepts_session_timeout(self):
        sess = Session()
        sess.configure(timeout=3.5)
        assert sess._config["timeout"] == 3.5

    def test_configure_rejects_nonpositive_session_timeout(self):
        sess = Session()
        with pytest.raises(ValueError, match="timeout must be > 0"):
            sess.configure(timeout=0)

    def test_add_timeout_warns_for_plain_job(self):
        from rpkbin.job_manager import FuncJob as PlainFuncJob

        sess = Session()
        plain = PlainFuncJob("plain", lambda: None)
        with pytest.warns(UserWarning, match="ignored for non-Wave job"):
            sess.add(plain, timeout=1.0)


    def test_add_before_start_buffers_job(self):
        sess = Session()
        job = WaveFuncJob("j", lambda: None)
        sess.add(job)
        assert job in sess._pending
        assert sess._manager is None

    def test_add_after_start_dispatches_immediately(self):
        sess = Session()
        job1 = WaveFuncJob("j1", lambda: None)
        sess.add(job1)
        sess._start()

        job2 = WaveFuncJob("j2", lambda: "ok")
        sess.add(job2)
        sess._stop()

        assert job1.status == DONE
        assert job2.status == DONE

    def test_wait_positional_timeout_remains_backward_compatible(self):
        sess = Session()
        gate = threading.Event()
        job = WaveFuncJob("j", lambda: gate.wait(timeout=2.0))
        sess.add(job)
        sess._start()
        try:
            assert sess.wait(0.05) is False
        finally:
            gate.set()
            sess._stop()

    def test_wait_can_target_specific_job(self):
        sess = Session()
        gate = threading.Event()
        slow = WaveFuncJob("slow", lambda: gate.wait(timeout=2.0))
        fast = WaveFuncJob("fast", lambda: None)
        sess.add(slow)
        sess.add(fast)
        sess._start()
        try:
            assert sess.wait(timeout=1.0, job=fast) is True
            assert slow.status in (PENDING, RUNNING)
        finally:
            gate.set()
            sess._stop()

    def test_stop_waits_for_on_done_callback_submissions(self, monkeypatch):
        sess = Session()
        follow_ran = threading.Event()

        original_dispatch = JobManager._dispatch_callbacks

        def delayed_dispatch(manager, job):
            time.sleep(0.05)
            return original_dispatch(manager, job)

        monkeypatch.setattr(JobManager, "_dispatch_callbacks", delayed_dispatch)

        def follow():
            follow_ran.set()

        def after(job):
            sess.add(WaveFuncJob("follow", follow))

        first = WaveFuncJob("first", lambda: None)
        first.on_done(after)
        sess.add(first)

        sess._start()
        sess._stop()

        assert follow_ran.is_set()
        assert any(job.name == "follow" and job.status == DONE for job in sess.jobs())

    def test_jobs_returns_pending_before_start(self):
        sess = Session()
        job = WaveFuncJob("j", lambda: None)
        sess.add(job)
        assert job in sess.jobs()

    def test_jobs_delegates_to_manager_after_start(self):
        sess = Session()
        job = WaveFuncJob("j", lambda: None)
        sess.add(job)
        sess._start()
        assert job in sess.jobs()
        sess._stop()

    def test_tui_notify_injected_into_wave_jobs(self):
        notified = []
        sess = Session()

        def notify(j):
            notified.append(j)

        job = WaveFuncJob("j", lambda: None)
        sess.add(job)
        sess._start(tui_notify=notify)

        # Manually trigger tui_notify by calling emit
        job.emit("test", "hello")

        sess._stop()
        assert any(j is job for j in notified)

    def test_tui_notify_not_injected_into_plain_jobs(self):
        """Plain scheduler jobs (not WaveJobMixin) should not break."""
        from rpkbin.job_manager import FuncJob as PlainFuncJob
        notified = []
        sess = Session()
        plain = PlainFuncJob("plain", lambda: None)
        sess.add(plain)
        sess._start(tui_notify=lambda j: notified.append(j))
        sess._stop()
        assert plain.status == DONE

    def test_reset_clears_state(self):
        sess = Session()
        sess.configure(max_workers=8, log_dir="/tmp/wave")
        sess.add(WaveFuncJob("j", lambda: None))
        sess.reset()
        assert sess._manager is None
        assert sess._pending == []
        assert sess._config["max_workers"] == 4  # default
        assert sess._config["log_dir"] is None

    def test_reset_allows_reuse(self):
        sess = Session()

        # First run
        j1 = WaveFuncJob("j1", lambda: "first")
        sess.add(j1)
        sess._start()
        sess._stop()
        assert j1.status == DONE

        # Second run after reset
        sess.reset()
        j2 = WaveFuncJob("j2", lambda: "second")
        sess.add(j2)
        sess._start()
        sess._stop()
        assert j2.status == DONE

    def test_session_emit_records_batch_events(self):
        sess = Session()
        sess.emit("note", "batch prepared")
        events = sess.peek_events()
        assert len(events) == 1
        assert events[0]["tag"] == "note"
        assert events[0]["message"] == "batch prepared"
        assert events[0]["source"] == "user"

    def test_pause_and_resume_record_session_events(self):
        sess = Session()
        gate = threading.Event()
        sess.add(WaveFuncJob("j", lambda: gate.wait(timeout=2.0)))
        sess._start()
        try:
            sess.pause()
            sess.resume()
            tags = [event["tag"] for event in sess.peek_events()]
            assert "session.pause" in tags
            assert "session.resume" in tags
        finally:
            gate.set()
            sess._stop()

    def test_cancel_group_works_before_start(self):
        sess = Session()
        tagged = WaveFuncJob("tagged", lambda: None, tags={"sim"})
        other = WaveFuncJob("other", lambda: None, tags={"other"})
        sess.add(tagged)
        sess.add(other)

        assert sess.cancel_group("sim") == 1
        assert tagged.status == CANCELLED
        assert other.status == PENDING

    def test_cancel_group_cancels_matching_jobs_after_start(self):
        sess = Session()
        gate = threading.Event()
        tagged = WaveFuncJob("tagged", lambda: gate.wait(timeout=2.0), tags={"sim"})
        other = WaveFuncJob("other", lambda: gate.wait(timeout=2.0), tags={"other"})
        sess.add(tagged)
        sess.add(other)
        sess._start()
        try:
            assert sess.cancel_group("sim") == 1
            assert tagged.status == CANCELLED
            assert other.status in (PENDING, RUNNING)
        finally:
            gate.set()
            sess._stop()

    def test_session_summary_reports_successful_batch(self):
        sess = Session()
        sess.add(WaveFuncJob("ok", lambda: None))
        sess._start()
        sess._stop()
        summary = sess.summary()
        assert summary["outcome"] == "done"
        assert summary["exit_code"] == 0
        assert summary["done"] == 1
        assert summary["failed"] == 0
        assert summary["skipped"] == 0
        assert "ok" in summary["done_names"]
        assert summary["duration_s"] is not None

    def test_session_summary_excludes_skipped_from_failure(self):
        sess = Session()
        job = WaveFuncJob("skipme", lambda: None)
        job.skip()
        sess.add(job)
        sess._start()
        sess._stop()
        summary = sess.summary()
        assert summary["outcome"] == "done"
        assert summary["exit_code"] == 0
        assert summary["skipped"] == 1
        assert summary["cancelled"] == 0
        assert "skipme" in summary["skipped_names"]

    def test_failed_excludes_skipped_by_default(self):
        sess = Session()
        job = WaveFuncJob("skipme", lambda: None)
        job.skip()
        sess.add(job)
        sess._start()
        sess._stop()
        assert sess.failed() == []
        assert [j.name for j in sess.failed(include_skipped=True)] == ["skipme"]

    def test_user_cancelled_job_does_not_fail_session(self):
        sess = Session()
        job = WaveFuncJob("cancelme", lambda: None)
        job.cancel()
        sess.add(job)
        sess._start()
        sess._stop()

        summary = sess.summary()
        assert summary["outcome"] == "done"
        assert summary["exit_code"] == 0
        assert summary["failed"] == 0
        assert summary["cancelled"] == 1
        assert sess.failed() == []

    def test_session_on_finish_and_on_done_fire_once(self):
        sess = Session()
        calls = []
        sess.on_finish(lambda s: calls.append(("finish", s.summary()["outcome"])))
        sess.on_done(lambda s: calls.append(("done", s.summary()["exit_code"])))
        sess.add(WaveFuncJob("ok", lambda: None))
        sess._start()
        sess._stop()
        sess._stop()
        assert calls == [("finish", "done"), ("done", 0)]

    def test_session_on_fail_fires_for_failed_batch(self):
        sess = Session()
        calls = []
        sess.on_fail(lambda s: calls.append(s.summary()["failed_names"]))
        sess.add(WaveFuncJob("boom", lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
        sess._start()
        sess._stop()
        assert calls == [["boom"]]

    def test_session_finalize_emits_finish_event(self):
        sess = Session()
        sess.add(WaveFuncJob("ok", lambda: None))
        sess._start()
        sess._stop()
        finish_events = [event for event in sess.peek_events() if event["tag"] == "session.finish"]
        assert len(finish_events) == 1
        assert finish_events[0]["source"] == "system"

    def test_session_timeout_cancels_active_jobs_and_records_event(self):
        sess = Session()
        gate = threading.Event()
        sess.configure(timeout=0.2)
        sess.add(WaveFuncJob("slow", lambda: gate.wait(timeout=2.0)))
        sess._start()
        try:
            deadline = time.time() + 2.0
            while time.time() < deadline and not any(e["tag"] == "session.timeout" for e in sess.peek_events()):
                time.sleep(0.05)
        finally:
            gate.set()
            sess._stop()

        assert any(e["tag"] == "session.timeout" for e in sess.peek_events())
        assert sess.summary()["exit_code"] == 1

    def test_add_after_session_finished_raises(self):
        sess = Session()
        sess.add(WaveFuncJob("ok", lambda: None))
        sess._start()
        sess._stop()

        with pytest.raises(RuntimeError, match="already finished"):
            sess.add(WaveFuncJob("late", lambda: None))


# ---------------------------------------------------------------------------
# Runner integration tests
# ---------------------------------------------------------------------------

class TestRunner:
    """Tests for wave.runner.run() using temporary wave files."""

    def _write_wave(self, path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    def test_simple_funcjob_no_tui(self, tmp_path):
        """runner.run() with a FuncJob completes successfully."""
        wave = tmp_path / "simple.wave.py"
        results_file = tmp_path / "result.txt"
        self._write_wave(wave, f"""
from rpkbin.wave import session, FuncJob

def work():
    with open(r"{results_file}", "w") as f:
        f.write("done")
    return "ok"

session.add(FuncJob("work", work))
""")
        from rpkbin.wave.runner import run
        assert run(wave, no_tui=True) == 0
        assert results_file.read_text() == "done"

    def test_simple_cmdjob_no_tui(self, tmp_path):
        """runner.run() with a CmdJob that echoes output."""
        wave = tmp_path / "cmd.wave.py"
        out_file = tmp_path / "out.txt"
        self._write_wave(wave, f"""
from rpkbin.wave import session, CmdJob

job = CmdJob("echo", "echo wave_ok")
session.add(job)
""")
        from rpkbin.wave.runner import run
        assert run(wave, no_tui=True) == 0

    def test_workers_override(self, tmp_path):
        """--workers flag overrides configure() in wave file."""
        wave = tmp_path / "workers.wave.py"
        self._write_wave(wave, """
from rpkbin.wave import session, FuncJob

session.configure(max_workers=1)  # wave file sets 1
session.add(FuncJob("j", lambda: None))
""")
        from rpkbin.wave.runner import run

        # workers=4 should override the wave file's configure(max_workers=1)
        assert run(wave, no_tui=True, workers=4) == 0
        # The manager that was used should have had max_workers=4
        # (session is reset between runs, so we check via configure override logic)

    def test_parsers_and_hooks_run_to_completion(self, tmp_path):
        """Parsers and hooks wired in a wave file actually fire."""
        wave = tmp_path / "parsers.wave.py"
        events_file = tmp_path / "events.txt"
        self._write_wave(wave, f"""
from rpkbin.wave import session, CmdJob, Hook
import re

job = CmdJob("sim", "echo RESULT=99")

def my_parser(line):
    if m := re.search(r"RESULT=(\\d+)", line):
        return {{"result": m.group(1)}}
    return {{}}

job.add_parser(my_parser)
job.add_hook(Hook(
    when=Hook.data_equals("result", "99"),
    action=lambda j, ctx: j.emit("found", "result=99"),
))
job.add_hook(Hook(
    when=Hook.on_done(),
    action=lambda j, ctx: open(r"{events_file}", "w").write(
        ",".join(e["tag"] for e in j.events)
    ),
))
session.add(job)
""")
        from rpkbin.wave.runner import run
        assert run(wave, no_tui=True) == 0
        # After run, the on_done hook should have written the events file
        assert events_file.exists(), "on_done hook did not fire"
        tags = events_file.read_text().split(",")
        assert "found" in tags

    def test_dynamic_add_from_on_done(self, tmp_path):
        """Jobs added dynamically from an on_done callback are run and finish."""
        wave = tmp_path / "dynamic.wave.py"
        flag_file = tmp_path / "flag.txt"
        self._write_wave(wave, f"""
from rpkbin.wave import session, FuncJob

def after(job):
    def follow():
        with open(r"{flag_file}", "w") as f:
            f.write("follow_ran")
    session.add(FuncJob("follow", follow))

first = FuncJob("first", lambda: None)
first.on_done(after)
session.add(first)
""")
        from rpkbin.wave.runner import run
        assert run(wave, no_tui=True) == 0
        assert flag_file.exists(), "dynamically-added follow job did not run"
        assert flag_file.read_text() == "follow_ran"

    def test_file_not_found_raises(self, tmp_path):
        """runner.run() raises FileNotFoundError for missing wave files."""
        from rpkbin.wave.runner import run
        with pytest.raises(FileNotFoundError):
            run(tmp_path / "nonexistent.wave.py", no_tui=True)

    def test_reset_between_runs(self, tmp_path):
        """runner.run() calls session.reset() so consecutive runs are clean."""
        wave = tmp_path / "reset.wave.py"
        self._write_wave(wave, """
from rpkbin.wave import session, FuncJob
session.add(FuncJob("j", lambda: None))
""")
        from rpkbin.wave.runner import run
        assert run(wave, no_tui=True) == 0
        assert run(wave, no_tui=True) == 0  # second run must not raise or skip jobs

    def test_failed_job_returns_nonzero(self, tmp_path):
        wave = tmp_path / "fail.wave.py"
        self._write_wave(wave, """
from rpkbin.wave import session, FuncJob

def fail():
    raise RuntimeError("boom")

session.add(FuncJob("fail", fail))
""")
        from rpkbin.wave.runner import run
        assert run(wave, no_tui=True) == 1

    def test_skipped_job_does_not_return_nonzero(self, tmp_path):
        wave = tmp_path / "skip.wave.py"
        self._write_wave(wave, """
from rpkbin.wave import session, FuncJob

job = FuncJob("skipme", lambda: None)
job.skip()
session.add(job)
""")
        from rpkbin.wave.runner import run
        assert run(wave, no_tui=True) == 0


# ---------------------------------------------------------------------------
# CLI integration tests (Click CliRunner)
# ---------------------------------------------------------------------------

class TestCLI:
    """Tests for the Click CLI using CliRunner (no subprocess)."""

    def test_help(self):
        from click.testing import CliRunner
        from rpkbin.wave.cli import main
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output

    def test_run_help(self):
        from click.testing import CliRunner
        from rpkbin.wave.cli import main
        result = CliRunner().invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--no-tui" in result.output
        assert "--workers" in result.output

    def test_run_no_tui(self, tmp_path):
        from click.testing import CliRunner
        from rpkbin.wave.cli import main

        wave = tmp_path / "cli.wave.py"
        wave.write_text("""
from rpkbin.wave import session, FuncJob
session.add(FuncJob("j", lambda: "ok"))
""", encoding="utf-8")

        result = CliRunner().invoke(main, ["run", str(wave), "--no-tui"])
        assert result.exit_code == 0, result.output

    def test_run_workers_flag(self, tmp_path):
        from click.testing import CliRunner
        from rpkbin.wave.cli import main

        wave = tmp_path / "workers.wave.py"
        wave.write_text("""
from rpkbin.wave import session, FuncJob
session.add(FuncJob("j", lambda: None))
""", encoding="utf-8")

        result = CliRunner().invoke(main, ["run", str(wave), "--no-tui", "--workers", "2"])
        assert result.exit_code == 0, result.output

    def test_run_returns_nonzero_for_failed_job(self, tmp_path):
        from click.testing import CliRunner
        from rpkbin.wave.cli import main

        wave = tmp_path / "fail_cli.wave.py"
        wave.write_text("""
from rpkbin.wave import session, FuncJob

def fail():
    raise RuntimeError("boom")

session.add(FuncJob("fail", fail))
""", encoding="utf-8")

        result = CliRunner().invoke(main, ["run", str(wave), "--no-tui"])
        assert result.exit_code == 1

    def test_run_missing_file(self, tmp_path):
        from click.testing import CliRunner
        from rpkbin.wave.cli import main

        result = CliRunner().invoke(main, ["run", str(tmp_path / "no.wave.py"), "--no-tui"])
        # Click's exists=True check triggers before our code
        assert result.exit_code != 0


class TestHeadlessCommandParsing:
    def test_completer_supports_prompt_toolkit_async_api(self):
        from rpkbin.wave.runner import _WaveCompleter

        class _Doc:
            text_before_cursor = "st"

        sess = Session()
        completer = _WaveCompleter(sess)

        async def _collect():
            items = []
            async for item in completer.get_completions_async(_Doc(), None):
                items.append(item.text)
            return items

        assert "status" in asyncio.run(_collect())

    def test_completer_includes_pause_resume(self):
        from rpkbin.wave.runner import _WaveCompleter

        class _Doc:
            def __init__(self, text):
                self.text_before_cursor = text

        sess = Session()
        completer = _WaveCompleter(sess)

        assert [item.text for item in completer.get_completions(_Doc("pa"), None)] == ["pause"]
        assert [item.text for item in completer.get_completions(_Doc("re"), None)] == ["resume"]

    def test_pause_resume_commands_dispatch(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        class _Sess:
            def __init__(self):
                self.calls = []

            def pause(self):
                self.calls.append("pause")

            def resume(self):
                self.calls.append("resume")

        sess = _Sess()

        _handle_cmd(["pause"], sess)
        _handle_cmd(["resume"], sess)
        out = capsys.readouterr().out

        assert sess.calls == ["pause", "resume"]
        assert "Job dispatch paused" in out
        assert "Job dispatch resumed" in out

    def test_requires_quotes_for_names_with_spaces(self, capsys):
        from rpkbin.wave.runner import _handle_cmd
        sess = Session()
        _handle_cmd(["logs", "my", "job"], sess)
        out = capsys.readouterr().out
        assert "must be quoted" in out

    def test_quoted_name_is_accepted(self, capsys):
        from rpkbin.wave.runner import _handle_cmd
        sess = Session()
        job = WaveFuncJob("my job", lambda: None)
        sess.add(job)
        _handle_cmd(["logs", "my job"], sess)
        out = capsys.readouterr().out
        assert "No log output" in out

    def test_duplicate_names_are_ambiguous_for_commands(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        job1 = WaveFuncJob("dup", lambda: None)
        job2 = WaveFuncJob("dup", lambda: None)
        sess.add(job1)
        sess.add(job2)

        _handle_cmd(["show", "dup"], sess)

        out = capsys.readouterr().out
        assert "ambiguous" in out
        assert str(job1.id) in out
        assert str(job2.id) in out

    def test_job_id_prefix_disambiguates_duplicate_names(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        job1 = WaveFuncJob("dup", lambda: None)
        job2 = WaveFuncJob("dup", lambda: None)
        sess.add(job1)
        sess.add(job2)

        _handle_cmd(["show", str(job2.id)[:8]], sess)

        out = capsys.readouterr().out
        assert f"id      = {job2.id}" in out
        assert "ambiguous" not in out

    def test_tui_uses_job_id_row_keys_for_duplicate_names(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        job1 = WaveFuncJob("dup", lambda: None)
        job2 = WaveFuncJob("dup", lambda: None)
        sess.add(job1)
        sess.add(job2)
        app = WaveApp(sess)

        assert app._job_row_key(job1) == str(job1.id)
        assert app._job_row_key(job2) == str(job2.id)
        assert app._job_row_key(job1) != app._job_row_key(job2)
        assert app._command_identifier_for_job(job1) == str(job1.id)

    def test_stop_uses_graceful_policy(self, capsys):
        from rpkbin.wave.runner import _handle_cmd
        sess = Session()
        job = WaveFuncJob("grace", lambda: None)
        job.set_stop_policy(graceful_input="exit\n", graceful_timeout=1.0)
        with job._lock:
            job._status = RUNNING
        job.send_input = lambda text: None
        sess.add(job)
        sess.jobs = lambda: [job]
        _handle_cmd(["stop", "grace"], sess)
        out = capsys.readouterr().out
        assert "Graceful stop requested" in out

    def test_stop_graceful_reports_missing_policy(self, capsys):
        from rpkbin.wave.runner import _handle_cmd
        sess = Session()
        job = WaveFuncJob("nograce", lambda: None)
        with job._lock:
            job._status = RUNNING
        sess.add(job)
        sess.jobs = lambda: [job]
        _handle_cmd(["stop", "-g", "nograce"], sess)
        out = capsys.readouterr().out
        assert "no graceful stop policy" in out


class TestFuncJobCancelWarning:
    def test_cancel_logs_warning_for_funcjob(self, caplog):
        from rpkbin.job_manager import FuncJob

        caplog.set_level(logging.WARNING)
        job = FuncJob("plain", lambda: None)
        job.cancel()
        assert "cannot be force-stopped" in caplog.text

    def test_watch_rejects_non_watchable_command(self, capsys):
        from rpkbin.wave.runner import _handle_cmd
        sess = Session()
        _handle_cmd(["watch", "stop", "job"], sess)
        out = capsys.readouterr().out
        assert "watch currently supports only" in out

    def test_exit_refuses_when_jobs_are_active(self, capsys):
        from rpkbin.wave.runner import _handle_cmd
        sess = Session()
        job = WaveFuncJob("running", lambda: None)
        with job._lock:
            job._status = RUNNING
        sess.jobs = lambda: [job]
        result = _handle_cmd(["exit"], sess)
        out = capsys.readouterr().out
        assert result == "continue"
        assert "Jobs are still active" in out

    def test_exit_invalid_flag_shows_usage(self, capsys):
        from rpkbin.wave.runner import _handle_cmd
        sess = Session()
        sess.jobs = lambda: []
        result = _handle_cmd(["exit", "--detach"], sess)
        out = capsys.readouterr().out
        assert result == "continue"
        assert "Usage: exit [--stop|--force]" in out

    def test_repl_announces_completion_and_waits_for_exit(self, monkeypatch, capsys):
        from rpkbin.wave.runner import _run_repl

        sess = Session()
        finished = WaveFuncJob("done", lambda: None)
        with finished._lock:
            finished._status = DONE

        sess.jobs = lambda: [finished]
        sess.done = lambda: [finished]
        sess.failed = lambda include_skipped=False: []
        sess.skipped = lambda: []

        called = {"count": 0}

        def _fake_reader():
            called["count"] += 1
            return "exit"

        monkeypatch.setattr("rpkbin.wave.runner._make_repl_reader", lambda _sess: _fake_reader)
        _run_repl(sess)
        out = capsys.readouterr().out
        assert called["count"] == 1
        assert "All jobs are complete" in out
        assert "Type 'exit' to leave the REPL." in out


class TestWaveTuiNavigation:
    def test_dangerous_quick_actions_are_not_bound(self):
        from rpkbin.wave.tui.app import WaveApp, _HELP_TEXT, _WAVE_COMMANDS

        keys = {binding.key for binding in WaveApp.BINDINGS}
        actions = {binding.action for binding in WaveApp.BINDINGS}

        assert not {"s", "k", "x"} & keys
        assert {
            "left_square_bracket",
            "right_square_bracket",
            "left_curly_bracket",
            "right_curly_bracket",
        } <= keys
        assert not {"quick_stop", "quick_skip", "quick_kill"} & actions
        assert "s / k / x" not in _HELP_TEXT
        assert "Prefill  stop / skip / kill" not in _HELP_TEXT
        assert {"pause", "resume"} <= set(_WAVE_COMMANDS)
        assert "pause" in _HELP_TEXT
        assert "resume" in _HELP_TEXT

    def test_detail_navigation_wraps_between_all_jobs(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        jobs = [WaveFuncJob(f"job{i}", lambda: None) for i in range(3)]
        for job in jobs:
            sess.add(job)

        app = WaveApp(sess)
        app._is_detail_navigation_context = lambda: True
        app._open_detail_for = lambda job: setattr(app, "_detail_job", job)

        app._detail_job = jobs[0]
        app._navigate_detail_job(-1)
        assert app._detail_job is jobs[2]

        app._navigate_detail_job(1)
        assert app._detail_job is jobs[0]

    def test_detail_navigation_wraps_between_running_jobs(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        jobs = [WaveFuncJob(f"job{i}", lambda: None) for i in range(4)]
        for idx, job in enumerate(jobs):
            with job._lock:
                job._status = RUNNING if idx in (1, 3) else DONE
            sess.add(job)

        app = WaveApp(sess)
        app._is_detail_navigation_context = lambda: True
        app._open_detail_for = lambda job: setattr(app, "_detail_job", job)

        app._detail_job = jobs[1]
        app._navigate_detail_job(1, status="running")
        assert app._detail_job is jobs[3]

        app._navigate_detail_job(1, status="running")
        assert app._detail_job is jobs[1]

        app._navigate_detail_job(-1, status="running")
        assert app._detail_job is jobs[3]

    def test_running_navigation_noops_when_no_running_job(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        jobs = [WaveFuncJob(f"job{i}", lambda: None) for i in range(2)]
        for job in jobs:
            with job._lock:
                job._status = DONE
            sess.add(job)

        notices = []
        app = WaveApp(sess)
        app._is_detail_navigation_context = lambda: True
        app.notify = lambda message, **kwargs: notices.append(message)
        app._detail_job = jobs[0]

        app._navigate_detail_job(1, status="running")

        assert app._detail_job is jobs[0]
        assert notices == ["No running jobs."]

    def test_open_detail_syncs_dashboard_selection(self):
        from rpkbin.wave.tui.app import WaveApp

        class _FakeWidget:
            active = None

            def clear(self):
                pass

            def update(self, _value):
                pass

            def write(self, _value):
                pass

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)

        app = WaveApp(sess)
        synced = []
        app._update_detail_header = lambda selected: None
        app._sync_dashboard_selection = lambda selected: synced.append(selected)
        app._refresh_right_panels = lambda selected: None
        app.query_one = lambda *args, **kwargs: _FakeWidget()

        app._open_detail_for(job)

        assert app._detail_job is job
        assert synced == [job]

    def test_detail_navigation_bindings_work_in_textual(self):
        from rpkbin.wave.tui.app import WaveApp

        async def _smoke():
            sess = Session()
            jobs = [WaveFuncJob(f"job{i}", lambda: None) for i in range(3)]
            for idx, job in enumerate(jobs):
                with job._lock:
                    job._status = RUNNING if idx in (1, 2) else DONE
                sess.add(job)

            app = WaveApp(sess)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._open_detail_for(jobs[0])
                await pilot.pause()

                await pilot.press("]")
                await pilot.pause()
                assert app._detail_job is jobs[1]

                await pilot.press("[")
                await pilot.pause()
                assert app._detail_job is jobs[0]

                await pilot.press("}")
                await pilot.pause()
                assert app._detail_job is jobs[1]

                await pilot.press("{")
                await pilot.pause()
                assert app._detail_job is jobs[2]

        asyncio.run(_smoke())

    def test_detail_navigation_keys_still_type_in_command_bar(self):
        from rpkbin.wave.tui.app import WaveApp

        async def _smoke():
            sess = Session()
            job = WaveFuncJob("job", lambda: None)
            sess.add(job)

            app = WaveApp(sess)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._open_detail_for(job)
                await pilot.pause()
                app.query_one("#cmd-input").focus()

                await pilot.press("[", "]", "{", "}")
                await pilot.pause()

                assert app.query_one("#cmd-input").value == "[]{}"

        asyncio.run(_smoke())

    def test_data_events_commands_route_to_detail_tabs(self):
        from rpkbin.wave.tui.app import WaveApp

        class _Event:
            def __init__(self, value):
                self.value = value

        async def _smoke():
            sess = Session()
            job = WaveFuncJob("job", lambda: None)
            job.update_parsed_data({"answer": "42"})
            job.emit("note", "hello")
            sess.add(job)

            app = WaveApp(sess)
            async with app.run_test() as pilot:
                await pilot.pause()

                app.on_input_submitted(_Event("data job"))
                await pilot.pause()
                assert app._detail_job is job
                assert app.query_one("#main-tabs").active == "tab-detail"
                assert app.query_one("#detail-tabs").active == "detail-data"

                app.on_input_submitted(_Event("events job"))
                await pilot.pause()
                assert app._detail_job is job
                assert app.query_one("#main-tabs").active == "tab-detail"
                assert app.query_one("#detail-tabs").active == "detail-events"

        asyncio.run(_smoke())
