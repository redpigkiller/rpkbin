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
from rpkbin.wave.parser import RegexParser, StatefulParser
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

    def test_on_data_change_factory(self):
        when = Hook.on_data_change("state")
        assert when.type == "on_data_change"
        assert when.key == "state"

    def test_elapsed_exceeds_factory(self):
        when = Hook.elapsed_exceeds(5.0)
        assert when.type == "elapsed_exceeds"
        assert when.seconds == 5.0

    @pytest.mark.parametrize("seconds", [0, -1, True])
    def test_elapsed_exceeds_rejects_invalid_seconds(self, seconds):
        with pytest.raises(ValueError, match="must be > 0"):
            Hook.elapsed_exceeds(seconds)

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
    def test_rejects_unknown_policy(self):
        with pytest.raises(ValueError, match="unknown policy"):
            Hook(Hook.on_start(), lambda job, ctx: None, policy="typo")

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
        assert "RuntimeError: boom" in job.events[0]["message"]
        assert "Traceback:" in job.events[0]["message"]

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

    def test_every_n_throttle_key_counts_independently(self):
        seen = []
        hook = Hook(
            when=Hook.log_matches(r"x"),
            action=lambda j, ctx: seen.append(ctx["name"]),
            policy="every_n",
            n=2,
            throttle_key=lambda ctx: ctx["name"],
        )
        for name in ["a", "b", "a", "a", "b", "b"]:
            hook._fire(None, {"name": name})
        assert seen == ["a", "b"]

    def test_throttle_key_requires_every_n_policy(self):
        with pytest.raises(ValueError, match="throttle_key"):
            Hook(
                when=Hook.log_matches(r"x"),
                action=lambda j, ctx: None,
                throttle_key=lambda ctx: ctx["name"],
            )


# ---------------------------------------------------------------------------
# WaveJobMixin — parser tests
# ---------------------------------------------------------------------------

class TestAddParser:
    def test_regex_parser_extracts_named_groups(self):
        parser = RegexParser(
            r"SUITE=(?P<suite>\w+)\s+TESTS=(?P<tests>\d+)",
            transform=lambda d: {"suite": d["suite"], "total": d["tests"]},
        )

        assert parser("SUITE=math TESTS=150") == {"suite": "math", "total": "150"}
        assert parser("unrelated") == {}
        assert parser.clone() is parser

    def test_stateful_parser_updates_memory_after_to_data_succeeds(self):
        parser = StatefulParser(
            r"FAILURES=(?P<fails>\d+)",
            on_match=lambda m, mem: {
                "max_fails": max(mem.get("max_fails", 0), int(m["fails"]))
            },
            to_data=lambda m, mem: {"peak_failures": str(mem["max_fails"])},
        )

        assert parser("FAILURES=2") == {"peak_failures": "2"}
        assert parser("FAILURES=5") == {"peak_failures": "5"}
        assert parser("FAILURES=3") == {"peak_failures": "5"}
        assert parser.clone()("FAILURES=1") == {"peak_failures": "1"}

    def test_stateful_parser_keeps_memory_when_to_data_raises(self):
        fail = [False]

        def to_data(match_data, memory):
            if fail[0]:
                raise ValueError("bad")
            return {"count": str(memory["count"])}

        parser = StatefulParser(
            r"(?P<warning>WARNING):",
            on_match=lambda m, mem: {"count": mem.get("count", 0) + 1},
            to_data=to_data,
        )

        assert parser("WARNING: first") == {"count": "1"}
        fail[0] = True
        with pytest.raises(ValueError):
            parser("WARNING: second")
        fail[0] = False
        assert parser("WARNING: third") == {"count": "2"}

    def test_stateful_parser_rolls_back_in_place_memory_mutation(self):
        fail = [False]

        def on_match(match_data, memory):
            memory["count"] = memory.get("count", 0) + 1
            return None

        def to_data(match_data, memory):
            if fail[0]:
                raise ValueError("bad")
            return {"count": str(memory["count"])}

        parser = StatefulParser(
            r"(?P<warning>WARNING):",
            on_match=on_match,
            to_data=to_data,
        )

        assert parser("WARNING: first") == {"count": "1"}
        fail[0] = True
        with pytest.raises(ValueError):
            parser("WARNING: second")
        fail[0] = False
        assert parser("WARNING: third") == {"count": "2"}

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
        assert "ValueError: bad" in job.events[0]["message"]
        assert "Line: 'anything'" in job.events[0]["message"]
        assert "Traceback:" in job.events[0]["message"]

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

class TestDataChangeHook:
    def test_fires_on_first_value(self):
        seen = []
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(
            when=Hook.on_data_change("state"),
            action=lambda j, ctx: seen.append(ctx),
            policy="always",
        ))
        job.update_parsed_data({"state": "IDLE"})
        assert seen == [{"key": "state", "old": None, "new": "IDLE"}]

    def test_fires_only_when_value_changes(self):
        seen = []
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(
            when=Hook.on_data_change("state"),
            action=lambda j, ctx: seen.append((ctx["old"], ctx["new"])),
            policy="always",
        ))
        job.update_parsed_data({"state": "IDLE"})
        job.update_parsed_data({"state": "IDLE"})
        job.update_parsed_data({"state": "TRAINING"})
        assert seen == [(None, "IDLE"), ("IDLE", "TRAINING")]

    def test_fires_via_parser_path(self):
        seen = []
        job = WaveFuncJob("test", lambda: None)
        job.add_parser(lambda line: {"state": line.split("=", 1)[1]} if line.startswith("STATE=") else {})
        job.add_hook(Hook(
            when=Hook.on_data_change("state"),
            action=lambda j, ctx: seen.append(ctx["new"]),
            policy="always",
        ))
        job._handle_log(job, "STATE=IDLE")
        job._handle_log(job, "STATE=IDLE")
        job._handle_log(job, "STATE=TRAINING")
        assert seen == ["IDLE", "TRAINING"]

    def test_ignores_other_keys(self):
        seen = []
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(
            when=Hook.on_data_change("state"),
            action=lambda j, ctx: seen.append(ctx),
            policy="always",
        ))
        job.update_parsed_data({"other": "x"})
        assert seen == []


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


class TestIncrementalLogSnapshot:
    def test_log_snapshot_since_returns_only_new_lines(self):
        job = WaveFuncJob("loggy", lambda: None, max_log_lines=3)
        job._emit_line("a")
        job._emit_line("b")

        total, lines = job.log_snapshot_since(0)

        assert total == 2
        assert lines == ["a", "b"]

        job._emit_line("c")
        job._emit_line("d")

        total, lines = job.log_snapshot_since(2)

        assert total == 4
        assert lines == ["c", "d"]

    def test_log_snapshot_since_clamps_to_retained_buffer(self):
        job = WaveFuncJob("loggy", lambda: None, max_log_lines=3)
        for line in ["a", "b", "c", "d"]:
            job._emit_line(line)

        total, lines = job.log_snapshot_since(0)

        assert total == 4
        assert lines == ["b", "c", "d"]

        total, lines = job.log_snapshot_since(4)

        assert total == 4
        assert lines == []


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

    def test_cancel_hook_does_not_fire_if_job_finished_first(self, monkeypatch):
        from rpkbin.job_manager.func_job import FuncJob

        seen = []
        job = WaveFuncJob("test", lambda: None)
        job.add_hook(Hook(Hook.on_cancel(), lambda j, ctx: seen.append(ctx)))

        def finish_instead(self):
            with self._lock:
                self._status = DONE

        monkeypatch.setattr(FuncJob, "cancel", finish_instead)
        job.cancel()

        assert job.status == DONE
        assert seen == []


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


# ---------------------------------------------------------------------------
# Hook.copy() tests
# ---------------------------------------------------------------------------

class TestHookCopy:
    def test_copy_returns_fresh_instance(self):
        original = Hook(when=Hook.log_matches(r"X"), action=lambda j, ctx: None, policy="always", n=1)
        copied = original.copy()
        assert copied is not original
        assert copied.when is original.when
        assert copied.action is original.action
        assert copied.policy == original.policy
        assert copied.n == original.n

    def test_copy_resets_exhausted_state(self):
        count = [0]
        original = Hook(when=Hook.log_matches(r"X"), action=lambda j, ctx: count.__setitem__(0, count[0] + 1), policy="once")
        original._fire(None, {})
        assert count[0] == 1
        # Original is exhausted
        original._fire(None, {})
        assert count[0] == 1

        copied = original.copy()
        copied._fire(None, {})
        assert count[0] == 2  # fresh copy fires again

    def test_copy_resets_fire_count(self):
        count = [0]
        original = Hook(
            when=Hook.log_matches(r"X"),
            action=lambda j, ctx: count.__setitem__(0, count[0] + 1),
            policy="every_n",
            n=3,
        )
        # Fire 3 times: action triggers on 3rd
        for _ in range(3):
            original._fire(None, {})
        assert count[0] == 1

        # Copy should have fire_count reset to 0
        copied = original.copy()
        # Fire copy 3 times: action triggers on 3rd
        for _ in range(3):
            copied._fire(None, {})
        assert count[0] == 2  # triggered once more via the copy

    def test_copy_preserves_throttle_key(self):
        throttle_key = lambda ctx: ctx["name"]
        original = Hook(
            when=Hook.log_matches(r"X"),
            action=lambda j, ctx: None,
            policy="every_n",
            n=2,
            throttle_key=throttle_key,
        )
        copied = original.copy()
        assert copied.throttle_key is throttle_key
        assert copied._fire_counts_by_key == {}

    def test_copy_does_not_affect_original(self):
        count = [0]
        original = Hook(when=Hook.log_matches(r"X"), action=lambda j, ctx: count.__setitem__(0, count[0] + 1), policy="once")
        copied = original.copy()
        copied._fire(None, {})
        assert count[0] == 1
        assert copied._exhausted is True
        assert original._exhausted is False  # original untouched


# ---------------------------------------------------------------------------
# Rerun tests (_clone_for_rerun)
# ---------------------------------------------------------------------------

class TestRerun:
    def test_clone_cmd_job_copies_static_params(self):
        job = WaveCmdJob("sim", "make -j4", cwd="/proj", env={"FOO": "bar"},
                         priority=5, max_retries=2, tags={"rtl", "sim"})
        clone = job._clone_for_rerun(1)
        assert clone.name == "sim#rerun1"
        assert clone.cmd == "make -j4"
        assert clone.cwd == "/proj"
        assert clone.env == {"FOO": "bar"}
        assert clone.env is not job.env  # deep copy
        assert clone.priority == 5
        assert clone.max_retries == 2
        assert clone.tags == frozenset({"rtl", "sim"})

    def test_clone_copies_parsers(self):
        def my_parser(line):
            if "RESULT=" in line:
                return {"result": line.split("=")[1]}
            return {}

        job = WaveCmdJob("test", "echo RESULT=42")
        job.add_parser(my_parser)
        clone = job._clone_for_rerun(1)

        # Simulate log line on clone
        clone._handle_log(clone, "RESULT=42")
        assert clone.parsed_data.get("result") == "42"
        # Original is unaffected
        assert job.parsed_data.get("result") is None

    def test_clone_hooks_have_fresh_state(self):
        """policy='once' hook fires again on cloned job."""
        fired_on = []
        hook = Hook(
            when=Hook.on_done(),
            action=lambda j, ctx: fired_on.append(j.name),
            policy="once",
        )
        job = WaveFuncJob("original", lambda: "ok")
        job.add_hook(hook)
        _run(job)
        assert "original" in fired_on

        clone = job._clone_for_rerun(1)
        _run(clone)
        assert "original#rerun1" in fired_on  # fires again on clone

    def test_clone_preserves_stop_policy(self):
        job = WaveCmdJob("test", "echo hi")
        job.set_stop_policy(graceful_input="quit\n", graceful_signal=signal.SIGTERM, graceful_timeout=10.0)
        clone = job._clone_for_rerun(1)
        assert clone.peek_stop_policy() == {
            "graceful_key": None,
            "graceful_input": "quit\n",
            "graceful_signal": signal.SIGTERM,
            "graceful_timeout": 10.0,
        }

    def test_clone_generates_unique_id(self):
        job = WaveCmdJob("test", "echo hi")
        clone = job._clone_for_rerun(1)
        assert clone.id != job.id

    def test_rerun_name_format(self):
        job = WaveCmdJob("compile", "make")
        c1 = job._clone_for_rerun(1)
        c2 = job._clone_for_rerun(2)
        assert c1.name == "compile#rerun1"
        assert c2.name == "compile#rerun2"

    def test_clone_func_job(self):
        results = []
        def my_func(x, y):
            results.append(x + y)
            return x + y

        job = WaveFuncJob("add", my_func, (3, 4))
        clone = job._clone_for_rerun(1)
        assert clone.name == "add#rerun1"
        assert clone.func is my_func
        assert clone.args == (3, 4)
        _run(clone)
        assert results == [7]

    def test_clone_func_job_copies_kwargs_dict(self):
        kwargs = {"value": 1}
        job = WaveFuncJob("kw", lambda **kw: kw, kwargs=kwargs)
        clone = job._clone_for_rerun(1)

        assert clone.kwargs == {"value": 1}
        assert clone.kwargs is not job.kwargs
        clone.kwargs["value"] = 2
        assert job.kwargs == {"value": 1}

    def test_rerun_via_session_add(self):
        fired = threading.Event()
        job = WaveCmdJob("test", "echo hello")
        job.add_hook(Hook(when=Hook.on_done(), action=lambda j, ctx: fired.set()))
        sess = Session()
        sess.add(job)
        sess._start()
        sess._stop()
        assert job.status == DONE
        assert fired.is_set()

        # Rerun
        fired.clear()
        clone = job._clone_for_rerun(1)
        sess2 = Session()
        sess2.add(clone)
        sess2._start()
        sess2._stop()
        assert clone.status == DONE
        assert fired.is_set()  # hook fired again on clone

    def test_rerun_of_rerun_increments_correctly(self):
        job = WaveCmdJob("sim", "echo hi")
        c1 = job._clone_for_rerun(1)
        assert c1.name == "sim#rerun1"
        # Rerunning the rerun should use the base name, not nest suffixes.
        c2 = c1._clone_for_rerun(2)
        assert c2.name == "sim#rerun2"

    def test_rerun_command_uses_next_base_name_without_duplicates(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        job = WaveCmdJob("sim", "echo hi")
        sess.add(job)

        _handle_cmd(["rerun", "sim"], sess)
        _handle_cmd(["rerun", "sim#rerun1"], sess)
        _handle_cmd(["rerun", "sim#rerun1"], sess)

        assert [j.name for j in sess.jobs()] == [
            "sim",
            "sim#rerun1",
            "sim#rerun2",
            "sim#rerun3",
        ]
        out = capsys.readouterr().out
        assert "sim#rerun3" in out


class TestStopPolicy:
    def test_set_stop_policy_snapshot(self):
        job = WaveFuncJob("test", lambda: None)
        job.set_stop_policy(
            graceful_input="exit\n",
            graceful_signal=signal.SIGINT,
            graceful_timeout=7.0,
        )
        assert job.peek_stop_policy() == {
            "graceful_key": None,
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

    def test_perf_disabled_returns_disabled_snapshot_and_empty_summary(self):
        sess = Session()
        job = WaveFuncJob("ok", lambda: None)
        job.add_parser(lambda line: {"state": line})
        job.add_hook(Hook(when=Hook.on_done(), action=lambda j, ctx: None))
        job._handle_log(job, "READY")
        assert sess.perf_snapshot() == {"enabled": False}
        assert sess.perf_summary() == ""

    def test_perf_enabled_accumulates_parser_and_hook_timing(self, monkeypatch):
        sess = Session()
        sess.set_perf_enabled(True)
        job = WaveFuncJob("ok", lambda: None)
        sess.add(job)
        job._wave_perf_enabled = True
        job.add_parser(lambda line: {"state": line})
        job.add_hook(Hook(when=Hook.log_matches(r"READY"), action=lambda j, ctx: None, policy="always"))

        times = iter([10.0, 10.25, 20.0, 20.5])
        monkeypatch.setattr("rpkbin.wave.job.time.perf_counter", lambda: next(times))
        monkeypatch.setattr("rpkbin.wave.hook.time.perf_counter", lambda: next(times))

        job._handle_log(job, "READY")
        snapshot = sess.perf_snapshot()

        assert snapshot["enabled"] is True
        assert snapshot["totals"]["parser_calls"] == 1
        assert snapshot["totals"]["hook_calls"] == 1
        assert snapshot["totals"]["parser_elapsed_s"] == pytest.approx(0.25)
        assert snapshot["totals"]["hook_elapsed_s"] == pytest.approx(0.5)

    def test_session_job_action_runs_with_context(self):
        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        calls = []

        sess.define_job_action(
            "mark",
            lambda target, ctx: calls.append((target, ctx["source"], ctx["args"])),
        )

        sess.run_job_action(job, "mark", "a", "b", source="cli")

        assert calls == [(job, "cli", ("a", "b"))]

    def test_job_action_override_takes_precedence(self):
        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        calls = []
        sess.define_job_action("mark", lambda target, ctx: calls.append("session"))
        job.add_action("mark", lambda target, ctx: calls.append("job"))

        sess.run_job_action(job, "mark", source="cli")

        assert calls == ["job"]

    def test_session_action_runs_in_separate_namespace(self):
        sess = Session()
        calls = []
        sess.define_session_action(
            "summary",
            lambda current_session, ctx: calls.append((current_session, ctx["args"])),
        )

        sess.run_session_action("summary", "now", source="cli")

        assert calls == [(sess, ("now",))]

    def test_hook_action_run_action_requires_explicit_allow(self, caplog):
        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        calls = []
        sess.define_job_action("mark", lambda target, ctx: calls.append(ctx["source"]))
        sess._inject_notify(job)

        with caplog.at_level(logging.WARNING):
            Hook.action_run_action("mark")(job, {})

        assert calls == []
        assert "not allowed from hooks" in caplog.text

    def test_hook_action_run_action_allowed(self):
        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        calls = []
        sess.define_job_action(
            "mark",
            lambda target, ctx: calls.append((target, ctx["source"])),
            allow_from_hook=True,
        )
        sess._inject_notify(job)

        Hook.action_run_action("mark")(job, {})

        assert calls == [(job, "hook")]

    def test_hook_session_action_guarded_by_default(self, caplog):
        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        calls = []
        sess.define_session_action("danger", lambda current_session, ctx: calls.append("ran"))
        sess._inject_notify(job)

        with caplog.at_level(logging.WARNING):
            Hook.action_run_session_action("danger")(job, {})

        assert calls == []
        assert "not allowed from hooks" in caplog.text

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

    def test_user_cancelled_job_fails_session(self):
        sess = Session()
        job = WaveFuncJob("cancelme", lambda: None)
        job.cancel()
        sess.add(job)
        sess._start()
        sess._stop()

        summary = sess.summary()
        assert summary["outcome"] == "failed"
        assert summary["exit_code"] == 1
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

    def test_non_interactive_terminal_uses_headless_mode(self, monkeypatch):
        from rpkbin.wave import runner

        class _Stream:
            def isatty(self):
                return False

        called = []
        monkeypatch.setattr(runner, "_load_wave_file", lambda path: None)
        monkeypatch.setattr(runner, "_run_headless", lambda: called.append("headless"))
        monkeypatch.setattr(runner, "_run_tui", lambda: called.append("tui"))
        monkeypatch.setattr(runner.sys, "stdin", _Stream())
        monkeypatch.setattr(runner.sys, "stdout", _Stream())

        assert runner.run("unused") == 0
        assert called == ["headless"]

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
        log_dir = tmp_path / "logs"
        self._write_wave(wave, f"""
from rpkbin.wave import session, FuncJob

session.configure(max_workers=1, resources={{"gpu": 1}}, log_dir={str(log_dir)!r}, timeout=30)
session.add(FuncJob("j", lambda: None, resources={{"gpu": 1}}))
""")
        from rpkbin.wave.runner import run
        from rpkbin.wave.session import session

        # workers=4 should override the wave file's configure(max_workers=1)
        assert run(wave, no_tui=True, workers=4) == 0
        assert session._config == {
            "max_workers": 4,
            "resources": {"gpu": 1},
            "log_dir": str(log_dir),
            "timeout": 30,
        }

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

    def test_perf_summary_printed_in_headless_run(self, tmp_path, capsys):
        wave = tmp_path / "perf.wave.py"
        self._write_wave(wave, """
from rpkbin.wave import session, CmdJob
session.add(CmdJob("echo", "echo perf_ok"))
""")
        from rpkbin.wave.runner import run

        assert run(wave, no_tui=True, perf=True) == 0
        out = capsys.readouterr().out
        assert "[Wave][perf] summary" in out
        assert "job echo:" in out


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
        assert "--perf" in result.output

    def test_run_perf_flag(self, monkeypatch):
        from click.testing import CliRunner
        from rpkbin.wave.cli import main
        called = {}

        def fake_run(path, **kwargs):
            called["path"] = path
            called.update(kwargs)
            return 0

        monkeypatch.setattr("rpkbin.wave.cli.run", fake_run)
        result = CliRunner().invoke(main, ["run", __file__, "--no-tui", "--perf"])

        assert result.exit_code == 0
        assert called["no_tui"] is True
        assert called["perf"] is True

    def test_run_tui_profile_flag_forwarding(self, monkeypatch):
        """Verify that --tui-profile is forwarded correctly to the runner."""
        from click.testing import CliRunner
        from rpkbin.wave.cli import main
        called = {}

        def fake_run(path, **kwargs):
            called["path"] = path
            called.update(kwargs)
            return 0

        monkeypatch.setattr("rpkbin.wave.cli.run", fake_run)
        result = CliRunner().invoke(main, ["run", __file__, "--no-tui", "--tui-profile", "lite"])

        assert result.exit_code == 0
        assert called["tui_profile"] == "lite"

    def test_init_template_rendering_pure_unit(self):
        """Verify the content logic of _render_wave_template without writing files."""
        from rpkbin.wave.cli import _render_wave_template

        # minimal
        minimal = _render_wave_template("min_test", "minimal")
        assert "min_test" in minimal
        assert "CmdJob" in minimal
        assert "add_parser" not in minimal
        assert "PtyJob" not in minimal
        assert "configure_tui" not in minimal

        # full
        full = _render_wave_template("full_test", "full")
        assert "from __future__ import annotations" in full
        assert "full_test" in full
        assert "add_parser" in full
        assert "Hook.on_done" in full
        assert "configure_tui" in full

        # pty
        pty = _render_wave_template("pty_test", "pty")
        assert "PtyJob(" in pty

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

    def test_help_shows_run_init_export_docs_not_docs(self):
        """rpk-wave --help must list run/init/export-docs; must NOT list docs."""
        from click.testing import CliRunner
        from rpkbin.wave.cli import main

        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "init" in result.output
        assert "export-docs" in result.output
        assert "docs" not in result.output.replace("export-docs", "")

    def test_init_help_shows_profile_choices_and_force(self):
        """rpk-wave init --help must show --profile, each profile name, and --force."""
        from click.testing import CliRunner
        from rpkbin.wave.cli import main

        result = CliRunner().invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "--profile" in result.output
        assert "minimal" in result.output
        assert "parser" in result.output
        assert "full" in result.output
        assert "pty" in result.output
        assert "--force" in result.output

    def test_export_docs_help(self):
        """rpk-wave export-docs --help must show --force."""
        from click.testing import CliRunner
        from rpkbin.wave.cli import main

        result = CliRunner().invoke(main, ["export-docs", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output

    def test_export_docs_copies_files(self, tmp_path):
        """export-docs copies docs into <dest>/wave/ and reports main entry files."""
        from click.testing import CliRunner
        from rpkbin.wave.cli import main, _find_wave_docs_src

        if _find_wave_docs_src() is None:
            pytest.skip("wave docs directory not found in this checkout")

        dest = tmp_path / "exported"
        result = CliRunner().invoke(main, ["export-docs", str(dest)])
        assert result.exit_code == 0, result.output
        assert "Docs exported to" in result.output
        # wave/ subdir must exist with at least one .md file
        wave_dir = dest / "wave"
        assert wave_dir.is_dir()
        md_files = list(wave_dir.glob("*.md"))
        assert md_files, "No .md files found in exported wave/ directory"
        # Main entry files reported
        assert "Main entry" in result.output

    def test_export_docs_refuses_nonempty_dest_without_force(self, tmp_path):
        """export-docs fails with non-zero exit when dest/wave/ already has content."""
        from click.testing import CliRunner
        from rpkbin.wave.cli import main, _find_wave_docs_src

        if _find_wave_docs_src() is None:
            pytest.skip("wave docs directory not found in this checkout")

        dest = tmp_path / "occupied"
        wave_dest = dest / "wave"
        wave_dest.mkdir(parents=True)
        (wave_dest / "existing.md").write_text("already here", encoding="utf-8")

        result = CliRunner().invoke(main, ["export-docs", str(dest)])
        assert result.exit_code != 0
        # Click merges stderr into output by default; the error message appears there
        assert "already has content" in result.output

    def test_export_docs_force_overwrites(self, tmp_path):
        """export-docs --force succeeds even when dest/wave/ already has content."""
        from click.testing import CliRunner
        from rpkbin.wave.cli import main, _find_wave_docs_src

        if _find_wave_docs_src() is None:
            pytest.skip("wave docs directory not found in this checkout")

        dest = tmp_path / "occupied2"
        wave_dest = dest / "wave"
        wave_dest.mkdir(parents=True)
        (wave_dest / "stale.md").write_text("stale", encoding="utf-8")

        result = CliRunner().invoke(main, ["export-docs", str(dest), "--force"])
        assert result.exit_code == 0, result.output
        assert "Docs exported to" in result.output


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
        assert [item.text for item in completer.get_completions(_Doc("re"), None)] == ["resume", "rerun"]

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

    def test_action_command_runs_registered_job_action(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        calls = []
        sess.define_job_action(
            "mark",
            lambda target, ctx: calls.append((target, ctx["args"], ctx["source"])),
        )

        _handle_cmd(["action", "job", "mark", "x"], sess)

        assert calls == [(job, ("x",), "cli")]
        assert "Action 'mark' ran" in capsys.readouterr().out

    def test_session_action_command_runs_registered_action(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        calls = []
        sess.define_session_action(
            "summarize",
            lambda current_session, ctx: calls.append((current_session, ctx["args"])),
        )

        _handle_cmd(["session_action", "summarize", "now"], sess)

        assert calls == [(sess, ("now",))]
        assert "Session action 'summarize' ran" in capsys.readouterr().out

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

    def test_show_reports_exit_code_error_and_system_event_counts(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        job = WaveFuncJob("bad", lambda: None)
        with job._lock:
            job._status = FAILED
            job._error = "Traceback line 1\nRuntimeError: boom"
        job.emit("parser_error", "ValueError: bad", source="system")
        sess.add(job)

        _handle_cmd(["show", "bad"], sess)

        out = capsys.readouterr().out
        assert "exit    = 1" in out
        assert "error   = Traceback line 1 RuntimeError: boom" in out
        assert "events  = parser_error=1" in out

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

    def test_add_job_row_registers_row_key_lookup(self):
        from rpkbin.wave.tui.app import WaveApp

        class _FakeTable:
            def __init__(self):
                self.rows = []

            def add_row(self, *cells, key=None):
                self.rows.append((key, cells))

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        app = WaveApp(sess)
        table = _FakeTable()
        app.query_one = lambda *args, **kwargs: table  # type: ignore[method-assign]
        app._update_subtitle = lambda **kwargs: None  # type: ignore[method-assign]

        app._add_job_row_on_main(job)

        key = app._job_row_key(job)
        assert app._job_for_row_key(key) is job
        assert table.rows[0][0] == key

    def test_default_dashboard_uses_exit_code_column(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        app = WaveApp(sess)
        job = WaveFuncJob("job", lambda: 0)
        with job._lock:
            job._status = DONE
            job._result = 0

        assert app._dashboard_column_labels() == ["#", "Name", "Status", "Elapsed", "Exit Code"]
        assert "[cyan]0[/cyan]" in app._build_row_cells(job)

    def test_info_panel_shows_command_cwd_and_error(self):
        from rpkbin.wave.tui.view_models import build_info_text

        job = WaveCmdJob("broken", "python missing.py", cwd="work")
        with job._lock:
            job._status = FAILED
            job._error = "file not found"

        info = build_info_text(job)

        assert "python missing.py" in info
        assert "Working dir:" in info
        assert "work" in info
        assert "file not found" in info

    def test_failed_func_job_without_integer_result_shows_exit_code_one(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        app = WaveApp(sess)
        job = WaveFuncJob("job", lambda: None)
        with job._lock:
            job._status = FAILED
            job._result = None

        assert "[red]1[/red]" in app._build_row_cells(job)

    def test_configured_dashboard_can_include_parsed_data_columns(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        sess.configure_tui(dashboard_columns=[
            "name",
            {"label": "Final", "data": "FINAL_RESULT"},
            "exit_code",
        ])
        job = WaveFuncJob("job", lambda: None)
        job.update_parsed_data({"FINAL_RESULT": "PASS"})
        with job._lock:
            job._status = FAILED
            job._result = 17

        app = WaveApp(sess)

        assert app._dashboard_column_labels() == ["Name", "Final", "Exit Code"]
        assert app._build_row_cells(job) == ("job", "PASS", "[red]17[/red]")

    def test_parsed_data_dashboard_snapshot_failure_keeps_row_rendering(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        sess.configure_tui(dashboard_columns=[
            "name",
            {"label": "Final", "data": "FINAL_RESULT"},
            {"label": "Seed", "data": "SEED"},
        ])
        job = WaveFuncJob("job", lambda: None)
        job.peek_data = lambda: (_ for _ in ()).throw(RuntimeError("snapshot failed"))  # type: ignore[method-assign]
        app = WaveApp(sess)

        assert app._build_row_cells(job) == ("job", "[red]ERR[/red]", "[red]ERR[/red]")

    def test_configure_tui_rejects_unknown_dashboard_column(self):
        sess = Session()

        with pytest.raises(ValueError, match="Unknown dashboard column"):
            sess.configure_tui(dashboard_columns=["name", "result"])

    def test_configure_tui_none_resets_to_default_columns(self):
        """configure_tui(dashboard_columns=None) clears custom columns back to built-in default."""
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        # Set custom columns first
        sess.configure_tui(dashboard_columns=[
            "name",
            {"label": "Result", "data": "result"},
        ])
        # Verify custom columns are active
        assert sess.tui_config()["dashboard_columns"] is not None

        # Reset via None
        sess.configure_tui(dashboard_columns=None)

        # dashboard_columns must be None in tui_config (TUI falls back to built-in default)
        assert sess.tui_config()["dashboard_columns"] is None

        # WaveApp must also use built-in default columns after reset
        app = WaveApp(sess)
        labels = app._dashboard_column_labels()
        assert "Exit Code" in labels  # built-in default includes exit_code
        assert "Result" not in labels  # custom column no longer present

    def test_detail_header_includes_exit_code_for_failed_func_job(self):
        from rpkbin.wave.tui.app import WaveApp

        class _Header:
            def __init__(self):
                self.value = ""

            def update(self, value):
                self.value = value

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        with job._lock:
            job._status = FAILED
        app = WaveApp(sess)
        header = _Header()
        app.query_one = lambda *args, **kwargs: header  # type: ignore[method-assign]
        app._detail_job = job

        app._update_detail_header(job)

        assert "exit=[/dim][red]1[/red]" in header.value
        assert header.disabled is True
        assert header.placeholder == "Input unavailable for this job"

    def test_running_cmd_job_enables_job_input(self):
        from rpkbin.wave.tui.app import WaveApp

        class _Widget:
            def update(self, value):
                self.value = value

        sess = Session()
        job = WaveCmdJob("interactive-command", "python -i")
        sess.add(job)
        with job._lock:
            job._status = RUNNING
        app = WaveApp(sess)
        widget = _Widget()
        app.query_one = lambda *args, **kwargs: widget  # type: ignore[method-assign]
        app._detail_job = job

        app._update_detail_header(job)

        assert widget.disabled is False
        assert widget.placeholder == "[interactive-c…] > "

    def test_data_panel_shows_empty_state_until_data_arrives(self):
        from rpkbin.wave.tui.app import WaveApp

        class _FakeTable:
            def __init__(self):
                self.rows = []
                self.cleared = 0

            def add_row(self, *cells, key=None):
                self.rows.append((key, cells))

            def clear(self):
                self.cleared += 1
                self.rows.clear()

            def update_cell(self, row_key, col_key, value, update_width=False):
                self.rows.append(("update", (row_key, col_key, value, update_width)))

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        app = WaveApp(sess)
        app._data_col_keys = ("key", "value")
        table = _FakeTable()
        app.query_one = lambda *args, **kwargs: table  # type: ignore[method-assign]

        app._refresh_data_panel(job)
        assert table.rows == [("__wave_empty_data__", ("[dim](no parsed data)[/dim]", ""))]

        job.update_parsed_data({"FINAL_RESULT": "PASS"})
        app._refresh_data_panel(job)

        assert table.cleared == 1
        assert table.rows == [("FINAL_RESULT", ("FINAL_RESULT", "PASS"))]

    def test_system_error_events_render_red(self):
        from rpkbin.wave.tui.app import _format_system_event_line

        line = _format_system_event_line({
            "time": "12:34:56",
            "tag": "parser_error",
            "message": "ValueError: bad",
        })

        assert "[bold red]parser_error[/bold red]" in line
        assert "[red]ValueError: bad[/red]" in line

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

    def test_send_line_accepts_unquoted_multi_word_text(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        job = WaveFuncJob("io", lambda: None)
        calls = []
        job.send_input = lambda text: calls.append(text)
        sess.add(job)

        _handle_cmd(["send-line", "io", "hello", "world"], sess)

        assert calls == ["hello world\n"]
        assert "send-line sent" in capsys.readouterr().out

    def test_send_line_appends_missing_newline(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        job = WaveFuncJob("io", lambda: None)
        calls = []
        job.send_input = lambda text: calls.append(text)
        sess.add(job)

        _handle_cmd(["send-line", "io", "hello", "world"], sess)

        assert calls == ["hello world\n"]
        assert "send-line sent" in capsys.readouterr().out


class TestFuncJobCancelWarning:
    def test_cancel_logs_warning_for_funcjob(self, caplog):
        from rpkbin.job_manager import FuncJob

        caplog.set_level(logging.WARNING)
        job = FuncJob("plain", lambda: None)
        with job._lock:
            job._status = RUNNING
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
    def test_job_updates_are_coalesced_before_touching_textual(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        job1 = WaveFuncJob("job1", lambda: None)
        job2 = WaveFuncJob("job2", lambda: None)
        app = WaveApp(sess)

        scheduled = []
        app.call_from_thread = lambda cb: scheduled.append(cb)  # type: ignore[method-assign]

        app._on_job_updated(job1)
        app._on_job_updated(job2)
        app._on_job_updated(job1)

        assert scheduled == [app._schedule_dirty_flush_on_main]
        assert set(app._dirty_jobs) == {app._job_row_key(job1), app._job_row_key(job2)}

    def test_dirty_flush_refreshes_rows_and_subtitle_once(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        job1 = WaveFuncJob("job1", lambda: None)
        job2 = WaveFuncJob("job2", lambda: None)
        app = WaveApp(sess)

        refreshed = []
        subtitles = []
        app._refresh_job_row = lambda job, **kwargs: refreshed.append((job, kwargs))  # type: ignore[method-assign]
        app._update_subtitle = lambda **kwargs: subtitles.append(kwargs)  # type: ignore[method-assign]

        with app._dirty_lock:
            app._dirty_jobs = {
                app._job_row_key(job1): job1,
                app._job_row_key(job2): job2,
            }
            app._dirty_flush_pending = True

        app._flush_dirty_job_rows()

        assert [item[0] for item in refreshed] == [job1, job2]
        assert all(item[1] == {"update_subtitle": False} for item in refreshed)
        assert subtitles == [{}]
        assert app._dirty_jobs == {}
        assert app._dirty_flush_pending is False

    def test_dirty_flush_records_perf_counter(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        sess.set_perf_enabled(True)
        job = WaveFuncJob("job1", lambda: None)
        app = WaveApp(sess)

        app._refresh_job_row = lambda job, **kwargs: None  # type: ignore[method-assign]
        app._update_subtitle = lambda **kwargs: None  # type: ignore[method-assign]

        with app._dirty_lock:
            app._dirty_jobs = {app._job_row_key(job): job}
            app._dirty_flush_pending = True

        app._flush_dirty_job_rows()

        assert sess.perf_snapshot()["tui"]["dirty_flush_count"] == 1
        assert sess.perf_snapshot()["tui"]["dirty_flush_jobs"] == 1

    def test_tick_skips_detail_panels_when_detail_tab_hidden(self):
        from rpkbin.wave.tui.app import WaveApp

        class _Tabs:
            active = "tab-dashboard"

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        app = WaveApp(sess)
        refreshed = []

        app._detail_job = job
        app._refresh_job_row = lambda selected, **kwargs: None  # type: ignore[method-assign]
        app._refresh_right_panels = lambda selected, **kwargs: refreshed.append(selected)  # type: ignore[method-assign]
        app._refresh_dashboard_preview = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app._update_subtitle = lambda **kwargs: None  # type: ignore[method-assign]
        app.query_one = lambda *args, **kwargs: _Tabs()  # type: ignore[method-assign]

        app._tick()

        assert refreshed == []

    def test_tick_and_log_sync_record_perf_counters(self, monkeypatch):
        from rpkbin.wave.tui.app import WaveApp

        class _Tabs:
            active = "tab-dashboard"

        class _RichLog:
            scroll_y = 0
            max_scroll_y = 0
            auto_scroll = False

            def write(self, text):
                self.last = text

        sess = Session()
        sess.set_perf_enabled(True)
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        app = WaveApp(sess)
        rich_log = _RichLog()

        job._total_log_lines = 2
        job.log_snapshot_since = lambda last_total: (2, ["a", "b"])  # type: ignore[method-assign]
        app._refresh_job_row = lambda selected, **kwargs: None  # type: ignore[method-assign]
        app._refresh_right_panels = lambda selected, **kwargs: None  # type: ignore[method-assign]
        app._refresh_dashboard_preview = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app._update_subtitle = lambda **kwargs: None  # type: ignore[method-assign]
        app.query_one = lambda *args, **kwargs: _Tabs() if args[0] == "#main-tabs" else rich_log  # type: ignore[method-assign]

        times = iter([1.0, 1.4])
        monkeypatch.setattr("rpkbin.wave.tui.app.time.perf_counter", lambda: next(times))

        app._sync_job_log(job, type("L", (), {
            "set_auto_scroll_for_append": lambda self: None,
            "write_lines": lambda self, lines, **kwargs: None,
            "write": lambda self, text: None,
        })(), 0)
        app._tick()

        snapshot = sess.perf_snapshot()
        assert snapshot["tui"]["log_append_lines"] == 2
        assert snapshot["tui"]["tick_count"] == 1
        assert snapshot["tui"]["tick_elapsed_s"] == pytest.approx(0.4)

    def test_log_adapter_scrolls_to_end_when_already_at_bottom(self):
        from rpkbin.wave.tui.refresh import _LogViewAdapter

        class _Widget:
            scroll_y = 8
            max_scroll_y = 10

            def __init__(self):
                self.lines = []
                self.auto_scroll = False

            def write(self, text):
                self.lines.append(text)

        widget = _Widget()
        log = _LogViewAdapter(widget)

        log.write_lines(["new"])

        # scroll_y (8) >= max_scroll_y (10) - 2 -> auto-scrolls
        assert widget.auto_scroll is True

    def test_log_adapter_does_not_follow_when_user_scrolled_up(self):
        from rpkbin.wave.tui.refresh import _LogViewAdapter

        class _Widget:
            scroll_y = 1
            max_scroll_y = 10

            def __init__(self):
                self.lines = []
                self.auto_scroll = True

            def write(self, text):
                self.lines.append(text)

        widget = _Widget()
        log = _LogViewAdapter(widget)

        log.write_lines(["new"])

        # scroll_y (1) < max_scroll_y (10) - 2 -> does not scroll
        assert widget.auto_scroll is False

    def test_switching_to_detail_tab_refreshes_detail_panels_once(self):
        from rpkbin.wave.tui.app import WaveApp

        class _Tabs:
            active = "tab-detail"

        class _Focusable:
            def focus(self):
                pass

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        app = WaveApp(sess)
        refreshed = []

        def _query(selector, *args, **kwargs):
            if selector == "#main-tabs":
                return _Tabs()
            return _Focusable()

        app._detail_job = job
        app._refresh_right_panels = lambda selected, **kwargs: refreshed.append((selected, kwargs))  # type: ignore[method-assign]
        app.query_one = _query  # type: ignore[method-assign]

        app.on_tabbed_content_tab_activated(object())

        assert refreshed == [(job, {"force": True})]

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
        app._refresh_right_panels = lambda selected, **kwargs: None
        app.query_one = lambda *args, **kwargs: _FakeWidget()

        app._open_detail_for(job)

        assert app._detail_job is job
        assert synced == [job]

    def test_dashboard_preview_reset_loads_tail_only(self):
        from rpkbin.wave.tui.app import WaveApp, _DASHBOARD_PREVIEW_TAIL_LINES

        class _FakeLog:
            def __init__(self):
                self.cleared = 0
                self.writes = []
                self.appended = []

            def clear(self):
                self.cleared += 1

            def write(self, text):
                self.writes.append(text)

            def write_lines(self, lines, **kwargs):
                self.appended.append(list(lines))

            def set_auto_scroll_for_append(self):
                pass

        class _FakeJob:
            name = "job"
            id = "abcd1234-0000-0000-0000-000000000000"
            _total_log_lines = 1500

            def log_snapshot_since(self, sync_count):
                self.last_sync_count = sync_count
                return self._total_log_lines, [f"line {idx}" for idx in range(sync_count, self._total_log_lines)]

        app = WaveApp(Session())
        fake_log = _FakeLog()
        job = _FakeJob()
        app._plain_log = lambda selector: fake_log  # type: ignore[method-assign]

        app._refresh_dashboard_preview(job, reset=True)

        assert fake_log.cleared == 1
        assert fake_log.writes == [f"{job.name}  [{job.id[:8]}]"]
        assert job.last_sync_count == 1500 - _DASHBOARD_PREVIEW_TAIL_LINES
        assert fake_log.appended == [[f"line {idx}" for idx in range(500, 1500)]]
        assert app._dashboard_preview_log_count == 1500

    def test_show_command_displays_graceful_key(self, capsys):
        from rpkbin.wave.runner import _handle_cmd

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        job.set_stop_policy(graceful_key="ctrl-c", graceful_timeout=3.0)
        sess.add(job)

        _handle_cmd(["show", "job"], sess)

        out = capsys.readouterr().out
        assert "stop    = key='ctrl-c', input=None, signal=None, timeout=3.0" in out

    def test_open_detail_initial_log_sync_starts_from_tail(self):
        from rpkbin.wave.tui.app import WaveApp, _DETAIL_INITIAL_TAIL_LINES

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
        calls = []
        app._update_detail_header = lambda selected: None  # type: ignore[method-assign]
        app._sync_dashboard_selection = lambda selected: None  # type: ignore[method-assign]
        app._tail_sync_start = lambda selected, limit: calls.append((selected, limit)) or 321  # type: ignore[method-assign]
        app._refresh_right_panels = lambda selected, **kwargs: None  # type: ignore[method-assign]
        app.query_one = lambda *args, **kwargs: _FakeWidget()  # type: ignore[method-assign]

        app._open_detail_for(job)

        assert calls == [
            (job, _DETAIL_INITIAL_TAIL_LINES),
        ]
        assert app._log_total_sync_count == 321

    def test_tick_skips_dashboard_preview_when_dashboard_hidden(self):
        from rpkbin.wave.tui.app import WaveApp

        class _Tabs:
            active = "tab-detail"

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        app = WaveApp(sess)
        previews = []

        app._detail_job = None
        app._refresh_job_row = lambda selected, **kwargs: None  # type: ignore[method-assign]
        app._refresh_dashboard_preview = lambda *args, **kwargs: previews.append((args, kwargs))  # type: ignore[method-assign]
        app._update_subtitle = lambda **kwargs: None  # type: ignore[method-assign]
        app.query_one = lambda *args, **kwargs: _Tabs()  # type: ignore[method-assign]

        app._tick()

        assert previews == []

    def test_visible_log_refresh_syncs_detail_log_without_panel_refresh(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        sess.add(job)
        app = WaveApp(sess)
        app._detail_job = job
        app._log_total_sync_count = 7
        calls = []

        app._is_dashboard_tab_active = lambda: False  # type: ignore[method-assign]
        app._is_detail_tab_active = lambda: True  # type: ignore[method-assign]
        app._active_detail_panel_id = lambda: "detail-info"  # type: ignore[method-assign]
        app._plain_log = lambda selector: selector  # type: ignore[method-assign]
        app._sync_job_log = lambda selected, log_view, sync_count, **kwargs: calls.append((selected, log_view, sync_count)) or 11  # type: ignore[method-assign]
        app._refresh_active_detail_panel = lambda selected, **kwargs: calls.append(("panel", selected, kwargs))  # type: ignore[method-assign]

        app._refresh_visible_logs()

        assert calls == [(job, "#log-view", 7)]
        assert app._log_total_sync_count == 11

    def test_refresh_right_panels_only_updates_active_subtab(self):
        from rpkbin.wave.tui.app import WaveApp

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        app = WaveApp(sess)
        calls = []

        app._update_detail_header = lambda selected: calls.append(("header", selected))  # type: ignore[method-assign]
        app._plain_log = lambda selector: object()  # type: ignore[method-assign]
        app._sync_job_log = lambda selected, log_view, sync_count, **kwargs: calls.append(("log", selected, sync_count)) or sync_count  # type: ignore[method-assign]
        app._active_detail_panel_id = lambda: "detail-data"  # type: ignore[method-assign]
        app._refresh_data_panel = lambda selected: calls.append(("data", selected))  # type: ignore[method-assign]
        app._refresh_events_panels = lambda selected: calls.append(("events", selected))  # type: ignore[method-assign]
        app._refresh_terminal_panel = lambda selected, **kwargs: calls.append(("terminal", selected, kwargs))  # type: ignore[method-assign]

        app._refresh_right_panels(job)

        assert calls == [
            ("header", job),
            ("log", job, 0),
            ("data", job),
        ]

    def test_inactive_event_panel_keeps_counter_until_activated(self):
        from rpkbin.wave.tui.app import WaveApp

        class _Tabs:
            def __init__(self, active):
                self.active = active
                self.id = "detail-tabs"

        class _FakeRichLog:
            def __init__(self):
                self.scroll_y = 0
                self.max_scroll_y = 0
                self.auto_scroll = True
                self.messages = []

            def write(self, text):
                self.messages.append(text)

            def scroll_end(self, *, animate=False):
                pass

        class _FakeStatic:
            def update(self, _text):
                pass

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        job.emit("user-note", "hello")
        app = WaveApp(sess)
        app._detail_job = job

        detail_tabs = _Tabs("detail-info")
        events_log = _FakeRichLog()
        system_log = _FakeRichLog()
        info_panel = _FakeStatic()

        def _query(selector, *args, **kwargs):
            if selector == "#detail-tabs":
                return detail_tabs
            if selector == "#info-panel":
                return info_panel
            if selector == "#events-log":
                return events_log
            if selector == "#system-job-log":
                return system_log
            raise AssertionError(selector)

        app.query_one = _query  # type: ignore[method-assign]

        app._refresh_active_detail_panel(job)
        assert app._events_sync_count == 0
        assert events_log.messages == []
        assert system_log.messages == []

        detail_tabs.active = "detail-events"
        app._refresh_active_detail_panel(job, force=True)
        assert app._events_sync_count == 1
        assert len(events_log.messages) == 1

    def test_detail_subtab_activation_forces_immediate_refresh(self):
        from rpkbin.wave.tui.app import WaveApp

        class _Event:
            def __init__(self):
                self.control = type("Control", (), {"id": "detail-tabs"})()

        sess = Session()
        job = WaveFuncJob("job", lambda: None)
        app = WaveApp(sess)
        app._detail_job = job
        app._focus_active_panel = lambda: None  # type: ignore[method-assign]
        refreshed = []
        app._refresh_active_detail_panel = lambda selected, **kwargs: refreshed.append((selected, kwargs))  # type: ignore[method-assign]

        app.on_tabbed_content_tab_activated(_Event())

        assert refreshed == [(job, {"force": True})]

    def test_log_view_adapter_uses_widget_specific_append_api(self):
        from rpkbin.wave.tui.app import _LogViewAdapter
        from textual.widgets import Log, RichLog

        log_widget = Log()
        rich_widget = RichLog()
        log_calls = []
        rich_calls = []
        log_widget.write_lines = lambda lines: log_calls.append(list(lines))  # type: ignore[method-assign]
        rich_widget.write = lambda text: rich_calls.append(text)  # type: ignore[method-assign]

        _LogViewAdapter(log_widget).write_lines(["a", "b"])
        _LogViewAdapter(rich_widget).write_lines(["a", "b"])

        assert log_calls == [["a", "b"]]
        assert rich_calls == ["a\nb"]

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
                self.control = type("C", (), {"id": "cmd-input"})()

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
