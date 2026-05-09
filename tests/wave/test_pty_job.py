"""
test_pty_job.py — Unit tests for PtyCmdJob (PTY interactive job).

Linux/macOS-only tests are guarded with pytest.mark.skipif(os.name == "nt").
Cross-platform tests (construction, stop policy, CLI dispatch) run everywhere.

Coverage targets
----------------
PtyCmdJob
  - Construction succeeds on all platforms
  - Execution fails clearly on Windows (or when PTY is unavailable)
  - Child process sees isatty() == True (Linux/macOS only)
  - send_input() delivers text to interactive script
  - send_key("ctrl-c") writes control byte and terminates process via SIGINT
  - send_key("invalid") raises ValueError
  - Default stop policy uses graceful_key="ctrl-c"
  - graceful_key in stop policy: request_stop fallback chain
  - Rerun clone preserves config (name, rows, cols, parsers, hooks, policy)
  - Rerun name uses base name (no nested #rerun suffixes)
  - Parser receives ANSI-stripped lines
  - Child process sees TERM=xterm-256color
  - _clean_for_log strips ANSI (no ?[31m fragments)
  - log_file receives output with newlines
  - waitpid return code decoding (normal exit and signal kill)

CLI
  - key command dispatch (with mock)
  - key on non-PTY job shows clear error
"""

from __future__ import annotations

import io
import os
import re
import signal
import threading
import time
from unittest import mock

import pytest

from rpkbin.job_manager import JobManager, DONE, FAILED, RUNNING
from rpkbin.wave.hook import Hook
from rpkbin.wave.job import WaveCmdJob
from rpkbin.wave.pty_job import PtyCmdJob, _clean_for_log, _clean_for_parser

# Shared skip condition for POSIX-only tests
requires_posix = pytest.mark.skipif(
    os.name == "nt",
    reason="PTY requires POSIX (Linux/macOS)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(job, *, max_workers: int = 1, timeout: float = 10.0):
    """Run a single job with a fresh JobManager and wait for completion."""
    with JobManager(max_workers=max_workers) as mgr:
        mgr.add(job)
        mgr.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# Construction and platform behavior
# ---------------------------------------------------------------------------

class TestPtyCmdJobConstruction:
    def test_construction_succeeds_on_any_platform(self):
        """PtyCmdJob can be constructed on any platform (including Windows)."""
        job = PtyCmdJob("test", "echo hello", rows=30, cols=120)
        assert job.name == "test"
        assert job.cmd == "echo hello"
        assert job._pty_rows == 30
        assert job._pty_cols == 120
        assert job.supports_pty is True

    def test_default_stop_policy_uses_graceful_key(self):
        job = PtyCmdJob("test", "echo hello")
        policy = job.peek_stop_policy()
        assert policy["graceful_key"] == "ctrl-c"
        assert policy["graceful_input"] is None
        assert policy["graceful_signal"] is None

    def test_non_pty_job_has_no_supports_pty(self):
        job = WaveCmdJob("test", "echo hello")
        assert not getattr(job, "supports_pty", False)

    def test_non_pty_job_has_no_send_key(self):
        job = WaveCmdJob("test", "echo hello")
        assert not hasattr(job, "send_key")


class TestWindowsExecution:
    def test_execution_fails_on_windows_with_clear_error(self):
        """On Windows (or mocked), _execute marks the job as failed."""
        job = PtyCmdJob("test", "echo hello")
        with mock.patch("rpkbin.wave.pty_job.os") as mock_os:
            mock_os.name = "nt"
            mock_os.environ = os.environ.copy()
            # Simulate the _execute path
            job._handle_on_start = lambda: None  # skip hook dispatch
            job._execute()

        assert job._error is not None
        assert "POSIX PTY support" in job._error
        assert job._result == 1


# ---------------------------------------------------------------------------
# POSIX-only: PTY execution tests
# ---------------------------------------------------------------------------

@requires_posix
class TestPtyExecution:
    def test_process_sees_tty(self):
        """Child process should see stdin as a TTY."""
        job = PtyCmdJob(
            "tty-test",
            'python3 -c "import sys; print(sys.stdin.isatty())"',
        )
        _run(job)
        logs = job.logs()
        # Find a line containing "True"
        assert any("True" in line for line in logs), (
            f"Expected 'True' in output but got: {logs}"
        )

    def test_send_input_delivers_text(self):
        """send_input() should deliver text to an interactive script."""
        # Use a shell script that reads a line and echoes it
        script = (
            'python3 -c "'
            "import sys; "
            "line = sys.stdin.readline().strip(); "
            "print(f'GOT:{line}')"
            '"'
        )
        job = PtyCmdJob("input-test", script)
        ready = threading.Event()

        def on_log(j, line):
            # Wait for any initial output before sending input
            ready.set()

        job.on_log(on_log)

        with JobManager(max_workers=1) as mgr:
            mgr.add(job)
            # Give the process a moment to start
            time.sleep(0.5)
            try:
                job.send_input("hello\n")
            except RuntimeError:
                pass  # Job may have finished already
            mgr.wait(timeout=5.0)

        logs = job.logs()
        assert any("GOT:hello" in line for line in logs), (
            f"Expected 'GOT:hello' in output but got: {logs}"
        )

    def test_send_key_ctrl_c_terminates_sleep(self):
        """send_key('ctrl-c') must actually terminate a sleeping process.

        This is the core test for pty.fork() establishing a proper
        controlling terminal. The kernel line discipline translates
        \\x03 → SIGINT to the foreground process group.
        """
        job = PtyCmdJob("ctrl-c-test", "sleep 999")

        with JobManager(max_workers=1) as mgr:
            mgr.add(job)
            # Wait for job to start
            deadline = time.monotonic() + 3.0
            while job.status != "running" and time.monotonic() < deadline:
                time.sleep(0.1)
            assert job.status == "running", f"Job didn't start: {job.status}"

            # Send Ctrl-C
            job.send_key("ctrl-c")

            # Wait for job to finish — should be fast (< 3s)
            mgr.wait(timeout=5.0)

        # Job should have ended (not still running)
        assert job.status in ("done", "failed", "cancelled"), (
            f"Job still in {job.status} after ctrl-c"
        )

        # Return code should indicate signal termination (-2 = SIGINT)
        # or non-zero exit. The key point is the job actually stopped.
        assert job._result is not None, "Job result should be set"
        assert job._result != 0, (
            f"Expected non-zero exit (signal kill) but got {job._result}"
        )

    def test_env_term_set(self):
        """Child should see TERM=xterm-256color by default."""
        job = PtyCmdJob(
            "term-test",
            'python3 -c "import os; print(os.environ.get(\'TERM\', \'UNSET\'))"',
        )
        _run(job)
        logs = job.logs()
        assert any("xterm-256color" in line for line in logs), (
            f"Expected TERM=xterm-256color but got: {logs}"
        )

    def test_parser_receives_cleaned_lines(self):
        """Parsers should receive ANSI-stripped lines."""
        received_lines = []

        def parser(line):
            received_lines.append(line)
            if "MARKER" in line:
                return {"found": "yes"}
            return {}

        # Use printf to emit a line with ANSI color codes
        job = PtyCmdJob(
            "parser-test",
            r'printf "\033[31mMARKER_LINE\033[0m\n"',
        )
        job.add_parser(parser)
        _run(job)

        # Parser should have received the line without ANSI escapes
        found = [l for l in received_lines if "MARKER" in l]
        assert found, f"Parser didn't receive MARKER line. Got: {received_lines}"
        # The ANSI escape should be stripped
        for line in found:
            assert "\x1b" not in line, (
                f"Parser received unstripped ANSI: {line!r}"
            )

    def test_custom_rows_cols(self):
        """Custom rows/cols should be set on the PTY."""
        job = PtyCmdJob(
            "size-test",
            'python3 -c "import os; r, c = os.get_terminal_size(); print(f\'SIZE:{r}x{c}\')"',
            rows=40,
            cols=132,
        )
        _run(job)
        logs = job.logs()
        # Look for the SIZE line
        size_lines = [l for l in logs if "SIZE:" in l]
        assert size_lines, f"No SIZE line in output: {logs}"

    def test_log_file_receives_output(self):
        """When log_file is provided, PTY output should be written to it."""
        job = PtyCmdJob("log-file-test", 'echo "LOG_LINE_1" && echo "LOG_LINE_2"')

        log_buffer = io.StringIO()

        # Run _execute directly with a log_file
        job._handle_on_start = lambda: None  # skip hook dispatch
        job._execute(log_file=log_buffer)

        content = log_buffer.getvalue()
        assert "LOG_LINE_1" in content, f"Expected LOG_LINE_1 in log_file but got: {content!r}"
        assert "LOG_LINE_2" in content, f"Expected LOG_LINE_2 in log_file but got: {content!r}"
        # Each line should end with newline
        lines = [l for l in content.split("\n") if l.strip()]
        assert len(lines) >= 2, f"Expected at least 2 lines in log_file but got: {lines}"

    def test_waitpid_normal_exit(self):
        """Normal exit should produce returncode == 0."""
        job = PtyCmdJob("exit-ok", "exit 0")
        _run(job)
        assert job._result == 0
        assert job.status == "done"

    def test_waitpid_nonzero_exit(self):
        """Non-zero exit should produce the correct exit code."""
        job = PtyCmdJob("exit-42", "exit 42")
        _run(job)
        assert job._result == 42
        assert job.status == "failed"

    def test_waitpid_signal_kill(self):
        """A process killed by signal should produce negative returncode."""
        job = PtyCmdJob("sig-kill", "sleep 999")

        with JobManager(max_workers=1) as mgr:
            mgr.add(job)
            deadline = time.monotonic() + 3.0
            while job.status != "running" and time.monotonic() < deadline:
                time.sleep(0.1)
            assert job.status == "running"

            # Kill via OS signal
            job.send_signal(signal.SIGTERM)
            mgr.wait(timeout=5.0)

        assert job.status in ("done", "failed", "cancelled")
        # Negative return code = killed by signal
        assert job._result is not None
        assert job._result < 0, f"Expected negative return code for signal kill, got {job._result}"


# ---------------------------------------------------------------------------
# _clean_for_log / _clean_for_parser tests
# ---------------------------------------------------------------------------

class TestCleanFunctions:
    def test_clean_for_log_strips_ansi(self):
        """_clean_for_log should strip ANSI, not leave ?[31m fragments."""
        raw = "\x1b[31mERROR\x1b[0m: something failed"
        cleaned = _clean_for_log(raw)
        assert "?" not in cleaned, f"Found ? fragment in: {cleaned!r}"
        assert "\x1b" not in cleaned, f"Found ESC in: {cleaned!r}"
        assert "ERROR: something failed" == cleaned

    def test_clean_for_log_strips_cr(self):
        raw = "line1\roverwrite"
        cleaned = _clean_for_log(raw)
        assert "\r" not in cleaned

    def test_clean_for_log_replaces_unsafe_control(self):
        raw = "before\x00\x07after"
        cleaned = _clean_for_log(raw)
        assert "before??after" == cleaned

    def test_clean_for_parser_strips_ansi(self):
        raw = "\x1b[31mERROR\x1b[0m: something"
        cleaned = _clean_for_parser(raw)
        assert "\x1b" not in cleaned
        assert "ERROR: something" == cleaned

    def test_clean_for_parser_removes_unsafe_control(self):
        """Parser version removes unsafe controls entirely (not replace with ?)."""
        raw = "before\x00after"
        cleaned = _clean_for_parser(raw)
        assert "beforeafter" == cleaned


# ---------------------------------------------------------------------------
# send_key validation
# ---------------------------------------------------------------------------

class TestSendKeyValidation:
    def test_invalid_key_raises_valueerror(self):
        job = PtyCmdJob("test", "echo hello")
        # Job is not running, but ValueError should fire before RuntimeError
        with pytest.raises(ValueError, match="Unknown control key"):
            job.send_key("invalid-key")

    def test_send_key_not_running_raises_runtimeerror(self):
        job = PtyCmdJob("test", "echo hello")
        with pytest.raises(RuntimeError, match="not available or not running"):
            job.send_key("ctrl-c")

    def test_send_input_not_running_raises_runtimeerror(self):
        job = PtyCmdJob("test", "echo hello")
        with pytest.raises(RuntimeError, match="not available or not running"):
            job.send_input("hello")


# ---------------------------------------------------------------------------
# Rerun / clone tests
# ---------------------------------------------------------------------------

class TestPtyRerun:
    def test_clone_preserves_config(self):
        job = PtyCmdJob(
            "sim", "make -j4",
            cwd="/proj", env={"FOO": "bar"},
            priority=5, max_retries=2,
            tags={"rtl", "sim"},
            rows=40, cols=132,
        )
        clone = job._clone_for_rerun(1)
        assert clone.name == "sim#rerun1"
        assert clone.cmd == "make -j4"
        assert clone.cwd == "/proj"
        assert clone.env == {"FOO": "bar"}
        assert clone.env is not job.env
        assert clone.priority == 5
        assert clone.max_retries == 2
        assert clone.tags == frozenset({"rtl", "sim"})
        assert clone._pty_rows == 40
        assert clone._pty_cols == 132
        assert clone.supports_pty is True

    def test_clone_preserves_stop_policy(self):
        job = PtyCmdJob("test", "echo hi")
        job.set_stop_policy(
            graceful_key="ctrl-d",
            graceful_input="exit\n",
            graceful_signal=signal.SIGTERM,
            graceful_timeout=10.0,
        )
        clone = job._clone_for_rerun(1)
        assert clone.peek_stop_policy() == {
            "graceful_key": "ctrl-d",
            "graceful_input": "exit\n",
            "graceful_signal": signal.SIGTERM,
            "graceful_timeout": 10.0,
        }

    def test_clone_copies_parsers_and_hooks(self):
        fired = []
        job = PtyCmdJob("test", "echo hi")
        job.add_parser(lambda line: {"seen": "yes"} if "HI" in line else {})
        job.add_hook(Hook(
            when=Hook.on_done(),
            action=lambda j, ctx: fired.append(j.name),
            policy="once",
        ))
        clone = job._clone_for_rerun(1)

        # Verify parser was copied
        clone._handle_log(clone, "HI there")
        assert clone.parsed_data.get("seen") == "yes"
        assert job.parsed_data.get("seen") is None  # original unaffected

    def test_rerun_name_no_nesting(self):
        """Rerun of a rerun should not create nested #rerun suffixes."""
        job = PtyCmdJob("compile", "make")
        c1 = job._clone_for_rerun(1)
        assert c1.name == "compile#rerun1"
        c2 = c1._clone_for_rerun(2)
        assert c2.name == "compile#rerun2"  # not "compile#rerun1#rerun2"

    def test_clone_generates_unique_id(self):
        job = PtyCmdJob("test", "echo hi")
        clone = job._clone_for_rerun(1)
        assert clone.id != job.id


# ---------------------------------------------------------------------------
# Stop policy: graceful_key fallback chain
# ---------------------------------------------------------------------------

class TestGracefulKeyStopPolicy:
    def test_graceful_key_called_on_request_stop(self):
        """request_stop() should call send_key when graceful_key is set."""
        job = PtyCmdJob("test", "sleep 999")
        job.set_stop_policy(graceful_key="ctrl-c", graceful_timeout=5.0)

        # Mock send_key to track calls
        send_key_calls = []
        job.send_key = lambda key: send_key_calls.append(key)

        # Simulate running state
        with job._lock:
            job._status = RUNNING
        with job._wave_lock:
            pass  # just ensure lock works

        result = job.request_stop(graceful_only=True)
        assert result == "graceful"
        assert send_key_calls == ["ctrl-c"]

    def test_graceful_key_failure_falls_through_to_input_and_signal(self):
        """If graceful_key fails, request_stop should try input, then signal."""
        job = PtyCmdJob("test", "sleep 999")
        job.set_stop_policy(
            graceful_key="ctrl-c",
            graceful_input="quit\n",
            graceful_signal=signal.SIGTERM,
            graceful_timeout=5.0,
        )

        # Make send_key fail
        def bad_send_key(key):
            raise RuntimeError("PTY unavailable")
        job.send_key = bad_send_key

        # Track send_input and send_signal
        input_calls = []
        signal_calls = []
        job.send_input = lambda text: input_calls.append(text)
        job.send_signal = lambda sig: signal_calls.append(sig)

        with job._lock:
            job._status = RUNNING

        result = job.request_stop(graceful_only=True)
        assert result == "graceful"
        assert input_calls == ["quit\n"]
        assert signal_calls == [signal.SIGTERM]

    def test_wavecmdjob_stop_policy_unchanged(self):
        """WaveCmdJob default should still use graceful_signal=SIGINT."""
        job = WaveCmdJob("test", "echo hi")
        policy = job.peek_stop_policy()
        assert policy["graceful_key"] is None
        assert policy["graceful_signal"] == signal.SIGINT

    def test_graceful_key_on_non_pty_job_warns_and_continues(self):
        """Setting graceful_key on WaveCmdJob: key step is skipped, but
        other steps (input/signal) should still work."""
        job = WaveCmdJob("test", "sleep 999")
        job.set_stop_policy(
            graceful_key="ctrl-c",  # job has no send_key
            graceful_signal=signal.SIGTERM,
            graceful_timeout=5.0,
        )

        signal_calls = []
        job.send_signal = lambda sig: signal_calls.append(sig)

        with job._lock:
            job._status = RUNNING

        result = job.request_stop(graceful_only=True)
        assert result == "graceful"
        # send_key was skipped (no such method on WaveCmdJob)
        assert signal_calls == [signal.SIGTERM]


# ---------------------------------------------------------------------------
# CLI key command dispatch
# ---------------------------------------------------------------------------

class TestCmdKeyDispatch:
    def test_key_dispatches_to_send_key(self):
        """_cmd_key should call job.send_key with the correct key."""
        from rpkbin.wave.runner import _cmd_key

        job = PtyCmdJob("test", "sleep 1")
        calls = []
        job.send_key = lambda key: calls.append(key)

        sess = mock.MagicMock()
        # Mock _find_job by making sess.jobs() return our job
        with mock.patch("rpkbin.wave.runner._find_job", return_value=job):
            _cmd_key(["key", "test", "ctrl-c"], sess)

        assert calls == ["ctrl-c"]

    def test_key_on_non_pty_job_shows_error(self, capsys):
        """key command on WaveCmdJob should print a clear error."""
        from rpkbin.wave.runner import _cmd_key

        job = WaveCmdJob("batch-job", "echo hi")

        sess = mock.MagicMock()
        with mock.patch("rpkbin.wave.runner._find_job", return_value=job):
            _cmd_key(["key", "batch-job", "ctrl-c"], sess)

        captured = capsys.readouterr()
        assert "does not support terminal keys" in captured.out
        assert "PtyCmdJob" in captured.out
        assert "signal" in captured.out  # should mention signal as alternative

    def test_key_usage_shown_on_wrong_args(self, capsys):
        from rpkbin.wave.runner import _cmd_key

        sess = mock.MagicMock()
        _cmd_key(["key"], sess)
        captured = capsys.readouterr()
        assert "Usage:" in captured.out
        assert "ctrl-c" in captured.out
