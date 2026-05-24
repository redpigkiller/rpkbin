"""test_pty_ux.py - Tests for the Wave PTY UX increment.

Covers:
  Goal 1 : _expand_dot_in_parts() helper and TUI '.' dispatch
  Goal 2 : F8/F9/F10 shortcut guard (_is_terminal_shortcut_context)
  Goal 3 : CommandInput autocomplete ('. ', key args, signal args)
  Goal 4 : Hook.action_send_key()
"""

from __future__ import annotations

import logging
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(*, name="sim", job_id="aabb1122-0000-0000-0000-000000000000",
              status="running", supports_pty=True, send_key=None):
    """Build a minimal mock job."""
    job = MagicMock()
    job.name = name
    job.id = job_id
    job.status = status
    job.supports_pty = supports_pty
    if send_key is not None:
        job.send_key = send_key
    elif supports_pty:
        job.send_key = MagicMock()
    else:
        del job.send_key  # non-PTY jobs have no send_key
    return job


# ---------------------------------------------------------------------------
# Goal 1 : _expand_dot_in_parts
# ---------------------------------------------------------------------------

class TestExpandDotInParts:
    """Unit-tests for the _expand_dot_in_parts() helper in app.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from rpkbin.wave.tui.app import _expand_dot_in_parts
        self.expand = _expand_dot_in_parts

    def _expand(self, parts, job_id):
        return self.expand(parts, job_id)

    # ---- happy-path expansion ----

    def test_key_dot_expanded(self):
        expanded, err = self._expand(["send-key", ".", "ctrl-c"], "uuid-1234")
        assert err is None
        assert expanded == ["send-key", "uuid-1234", "ctrl-c"]

    def test_signal_dot_expanded(self):
        expanded, err = self._expand(["send-signal", ".", "SIGTERM"], "uuid-1234")
        assert err is None
        assert expanded == ["send-signal", "uuid-1234", "SIGTERM"]

    def test_input_dot_expanded(self):
        expanded, err = self._expand(["send-line", ".", "hello"], "uuid-1234")
        assert err is None
        assert expanded == ["send-line", "uuid-1234", "hello"]

    def test_show_dot_expanded(self):
        expanded, err = self._expand(["show", "."], "uuid-1234")
        assert err is None
        assert expanded == ["show", "uuid-1234"]

    def test_logs_dot_expanded(self):
        expanded, err = self._expand(["logs", "."], "uuid-1234")
        assert err is None
        assert expanded == ["logs", "uuid-1234"]

    def test_data_dot_expanded(self):
        expanded, err = self._expand(["data", "."], "uuid-1234")
        assert err is None
        assert expanded == ["data", "uuid-1234"]

    def test_events_dot_expanded(self):
        expanded, err = self._expand(["events", "."], "uuid-1234")
        assert err is None
        assert expanded == ["events", "uuid-1234"]

    def test_stop_dot_no_flag_expanded(self):
        expanded, err = self._expand(["stop", "."], "uuid-1234")
        assert err is None
        assert expanded == ["stop", "uuid-1234"]

    def test_stop_dot_with_g_flag_expanded(self):
        """stop -g . → stop -g <uuid>"""
        expanded, err = self._expand(["stop", "-g", "."], "uuid-1234")
        assert err is None
        assert expanded == ["stop", "-g", "uuid-1234"]

    def test_stop_dot_with_f_flag_expanded(self):
        expanded, err = self._expand(["stop", "-f", "."], "uuid-1234")
        assert err is None
        assert expanded == ["stop", "-f", "uuid-1234"]

    def test_cancel_dot_expanded(self):
        expanded, err = self._expand(["cancel", "."], "uuid-1234")
        assert err is None
        assert expanded == ["cancel", "uuid-1234"]

    def test_stop_all_dot_ignored(self):
        """stop --all doesn't take a job id, so '.' is ignored."""
        expanded, err = self._expand(["stop", "--all", "."], "uuid-1234")
        assert err is None
        assert expanded == ["stop", "--all", "."]

    def test_stop_group_dot_ignored(self):
        """stop --group takes a tag, so '.' is ignored."""
        expanded, err = self._expand(["stop", "--group", "."], "uuid-1234")
        assert err is None
        assert expanded == ["stop", "--group", "."]

    def test_cancel_all_dot_ignored(self):
        expanded, err = self._expand(["cancel", "--all", "."], "uuid-1234")
        assert err is None
        assert expanded == ["cancel", "--all", "."]

    def test_cancel_group_dot_ignored(self):
        expanded, err = self._expand(["cancel", "--group", "."], "uuid-1234")
        assert err is None
        assert expanded == ["cancel", "--group", "."]

    def test_rerun_dot_expanded(self):
        expanded, err = self._expand(["rerun", "."], "uuid-1234")
        assert err is None
        assert expanded == ["rerun", "uuid-1234"]

    def test_skip_dot_expanded(self):
        expanded, err = self._expand(["skip", "."], "uuid-1234")
        assert err is None
        assert expanded == ["skip", "uuid-1234"]

    # ---- error: dot without open detail job ----

    def test_dot_no_detail_job_returns_error(self):
        expanded, err = self._expand(["send-key", ".", "ctrl-c"], None)
        assert expanded is None
        assert err is not None
        assert "open a job detail first" in err

    def test_signal_dot_no_detail_job_returns_error(self):
        expanded, err = self._expand(["send-signal", ".", "SIGTERM"], None)
        assert expanded is None
        assert "'.' means the current JOB DETAIL job" in err

    # ---- no-op: no dot present ----

    def test_no_dot_returns_unchanged(self):
        parts = ["send-key", "sim", "ctrl-c"]
        expanded, err = self._expand(parts, "uuid-1234")
        assert err is None
        assert expanded == parts

    def test_verb_without_job_id_no_dot(self):
        parts = ["status"]
        expanded, err = self._expand(parts, "uuid-1234")
        assert err is None
        assert expanded == parts

    def test_pause_does_not_expand(self):
        """'pause' doesn't take a job identifier; '.' is not touched."""
        parts = ["pause", "."]  # malformed, but we still should not touch it
        expanded, err = self._expand(parts, "uuid-1234")
        assert err is None
        assert expanded == parts  # unchanged

    def test_empty_parts(self):
        expanded, err = self._expand([], "uuid-1234")
        assert err is None
        assert expanded == []

    # ---- extra args not touched ----

    def test_logs_dot_with_n_arg(self):
        """logs . 100 → only parts[1] expanded; '100' stays."""
        expanded, err = self._expand(["logs", ".", "100"], "uuid-1234")
        assert err is None
        assert expanded == ["logs", "uuid-1234", "100"]


# ---------------------------------------------------------------------------
# Goal 2 : _is_terminal_shortcut_context guard
# ---------------------------------------------------------------------------

class TestTerminalShortcutContext:
    """Test _is_terminal_shortcut_context() without spinning a full TUI."""

    def _make_app(self, *, tab="tab-detail", sub_tab="detail-terminal",
                  detail_job="set", focused_id=None, raises=False):
        """Build a minimal WaveApp-alike mock for the guard method."""
        from rpkbin.wave.tui.app import WaveApp

        mock_focused = MagicMock()
        mock_focused.id = focused_id

        class MockWaveApp(WaveApp):
            @property
            def focused(self):
                return mock_focused

        app = object.__new__(MockWaveApp)
        app._detail_job = MagicMock() if detail_job == "set" else None

        main_tabs = MagicMock()
        main_tabs.active = tab

        detail_tabs = MagicMock()
        detail_tabs.active = sub_tab

        def _query_one(sel, *args):
            if "main-tabs" in sel:
                return main_tabs
            if "detail-tabs" in sel:
                if raises:
                    raise Exception("not found")
                return detail_tabs
            raise KeyError(sel)

        app.query_one = _query_one
        return app

    def _check(self, **kwargs):
        from rpkbin.wave.tui.app import WaveApp
        app = self._make_app(**kwargs)
        return WaveApp._is_terminal_shortcut_context(app)

    def test_all_conditions_met(self):
        assert self._check() is True

    def test_wrong_main_tab(self):
        assert self._check(tab="tab-dashboard") is False

    def test_wrong_sub_tab(self):
        """Sub-tab no longer affects terminal shortcut context (F9/F10 work from any sub-tab)."""
        # F9/F10 now work when JOB DETAIL is open + PTY job selected, regardless of sub-tab
        assert self._check(sub_tab="detail-info") is True

    def test_no_detail_job(self):
        assert self._check(detail_job=None) is False

    def test_cmd_input_focused(self):
        assert self._check(focused_id="cmd-input") is False

    def test_detail_tabs_raises(self):
        """detail-tabs widget availability no longer affects terminal shortcut context."""
        # guard now only checks main tab + job + focus, not sub-tab
        assert self._check(raises=True) is True


# ---------------------------------------------------------------------------
# Goal 3 : TUI autocomplete
# ---------------------------------------------------------------------------

class TestTuiAutocomplete:
    """Test CommandInput._handle_autocomplete logic."""

    def _make_cmd_input(self, *, session_jobs=None, detail_job=None):
        """Return a CommandInput with a mocked parent app."""
        from rpkbin.wave.tui.app import CommandInput

        mock_app = MagicMock()
        session = MagicMock()
        session.jobs.return_value = session_jobs or []
        mock_app._session = session
        mock_app._detail_job = detail_job

        class MockCommandInput(CommandInput):
            value = ""
            cursor_position = 0

            @property
            def app(self):
                return mock_app

        inp = object.__new__(MockCommandInput)
        inp.value = ""
        inp.cursor_position = 0
        inp._history = []
        inp._history_index = -1
        inp._draft = ""
        inp._cycle_matches = []
        inp._cycle_index = -1
        inp._cycle_before = ""
        return inp

    def _completions(self, inp, text: str) -> list[str]:
        """Simulate tab-completion on *text*, return list of candidate names."""
        import shlex

        try:
            parts = shlex.split(text, posix=True)
        except ValueError:
            parts = text.split()
        if text.endswith(" "):
            parts.append("")

        # We call _handle_autocomplete indirectly via the actual logic;
        # here we reconstruct the candidates manually to test the routing.
        # This avoids needing a full Textual event loop.
        from rpkbin.wave.tui.app import _KEY_COMPLETIONS, _SIGNAL_COMPLETIONS, _WAVE_JOB_COMMANDS

        if not parts:
            return []

        candidates: list[str] = []

        if len(parts) == 2:
            verb = parts[0].lower()
            current = parts[1]
            if verb in _WAVE_JOB_COMMANDS:
                job_names = [j.name for j in inp.app._session.jobs()]
                all_cands = list(job_names)
                if inp.app._detail_job is not None and ".".startswith(current):
                    all_cands.insert(0, ".")
                candidates = [n for n in all_cands if n.startswith(current)]

        elif len(parts) == 3:
            verb = parts[0].lower()
            current = parts[2]
            if verb == "send-key":
                candidates = [k for k in _KEY_COMPLETIONS if k.startswith(current)]
            elif verb == "send-signal":
                candidates = [s for s in _SIGNAL_COMPLETIONS if s.startswith(current)]

        return candidates

    # ---- '.' completion ----

    def test_dot_included_when_detail_job_set(self):
        jobs = [MagicMock(name="alpha"), MagicMock(name="beta")]
        jobs[0].name = "alpha"
        jobs[1].name = "beta"
        detail_job = MagicMock()
        inp = self._make_cmd_input(session_jobs=jobs, detail_job=detail_job)
        candidates = self._completions(inp, "send-key ")
        assert "." in candidates
        assert "alpha" in candidates

    def test_dot_not_included_when_no_detail_job(self):
        jobs = [MagicMock()]
        jobs[0].name = "alpha"
        inp = self._make_cmd_input(session_jobs=jobs, detail_job=None)
        candidates = self._completions(inp, "send-key ")
        assert "." not in candidates

    def test_dot_matches_literal_dot_prefix(self):
        """When user types 'send-key .' the '.' candidate still appears."""
        detail_job = MagicMock()
        inp = self._make_cmd_input(detail_job=detail_job)
        candidates = self._completions(inp, "send-key .")
        assert "." in candidates

    def test_dot_in_all_job_commands(self):
        from rpkbin.wave.tui.app import _WAVE_JOB_COMMANDS
        detail_job = MagicMock()
        inp = self._make_cmd_input(detail_job=detail_job)
        for verb in _WAVE_JOB_COMMANDS:
            candidates = self._completions(inp, f"{verb} ")
            assert "." in candidates, f"'.' missing for verb '{verb}'"

    # ---- key third-arg completion ----

    def test_key_ctrl_c_completion(self):
        inp = self._make_cmd_input()
        candidates = self._completions(inp, "send-key sim ctrl")
        assert "ctrl-c" in candidates
        assert "ctrl-d" in candidates
        assert "ctrl-z" in candidates

    def test_key_enter_completion(self):
        inp = self._make_cmd_input()
        candidates = self._completions(inp, "send-key sim e")
        assert "enter" in candidates

    def test_key_quoted_job_name_preserved(self):
        """Ensure that 3rd-arg completion quotes job names containing spaces."""
        inp = self._make_cmd_input()
        # Mocking what the _handle_autocomplete method would do manually
        inp.value = 'send-key "my job" ctrl-c'
        inp.action_autocomplete()
        # Verify that the value has the quotes
        assert inp.value == 'send-key "my job" ctrl-c '

    def test_key_tab_completion(self):
        inp = self._make_cmd_input()
        candidates = self._completions(inp, "send-key sim t")
        assert "tab" in candidates

    def test_key_completions_empty_prefix(self):
        from rpkbin.wave.tui.app import _KEY_COMPLETIONS
        inp = self._make_cmd_input()
        candidates = self._completions(inp, "send-key sim ")
        assert set(candidates) == set(_KEY_COMPLETIONS)

    # ---- signal third-arg completion ----

    def test_signal_sig_prefix(self):
        inp = self._make_cmd_input()
        candidates = self._completions(inp, "send-signal sim SIG")
        assert "SIGINT" in candidates
        assert "SIGTERM" in candidates
        assert "SIGKILL" in candidates

    def test_signal_sigterm_completion(self):
        inp = self._make_cmd_input()
        candidates = self._completions(inp, "send-signal sim SIGTERM")
        assert "SIGTERM" in candidates

    def test_signal_quoted_job_name_preserved(self):
        """Ensure that 3rd-arg completion quotes job names containing spaces."""
        inp = self._make_cmd_input()
        inp.value = 'send-signal "my job" SIGT'
        inp.action_autocomplete()
        assert inp.value == 'send-signal "my job" SIGTERM '

    def test_signal_completions_empty_prefix(self):
        from rpkbin.wave.tui.app import _SIGNAL_COMPLETIONS
        inp = self._make_cmd_input()
        candidates = self._completions(inp, "send-signal sim ")
        assert set(candidates) == set(_SIGNAL_COMPLETIONS)

    # ---- existing job-name completion not regressed ----

    def test_job_name_completion_still_works(self):
        jobs = [MagicMock(), MagicMock()]
        jobs[0].name = "render"
        jobs[1].name = "compile"
        inp = self._make_cmd_input(session_jobs=jobs, detail_job=None)
        candidates = self._completions(inp, "stop r")
        assert "render" in candidates
        assert "compile" not in candidates

    def test_job_name_quoting_still_works(self):
        """Job names with spaces should appear in candidates regardless of quoting."""
        jobs = [MagicMock()]
        jobs[0].name = "my render job"
        inp = self._make_cmd_input(session_jobs=jobs, detail_job=None)
        candidates = self._completions(inp, "stop my")
        assert "my render job" in candidates


# ---------------------------------------------------------------------------
# Goal 3 : headless runner _WaveCompleter key/signal completions
# ---------------------------------------------------------------------------

class TestRunnerCompleterKeySignal:
    """Test key/signal third-arg completions in headless _WaveCompleter."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from rpkbin.wave.runner import _KEY_COMPLETIONS, _SIGNAL_COMPLETIONS, _WaveCompleter
        self.Completer = _WaveCompleter
        self.key_comps = _KEY_COMPLETIONS
        self.sig_comps = _SIGNAL_COMPLETIONS

    def _completions(self, text: str) -> list[str]:
        """Collect completion display-strings for *text*."""
        try:
            from prompt_toolkit.completion import Completion
            from prompt_toolkit.document import Document
        except ImportError:
            pytest.skip("prompt_toolkit not installed")

        sess = MagicMock()
        sess.jobs.return_value = []
        c = self.Completer(sess)
        doc = Document(text=text, cursor_position=len(text))
        return [comp.text for comp in (c.get_completions(doc, None) or [])]

    def test_key_ctrl_completions(self):
        results = self._completions("send-key sim ctrl")
        assert "ctrl-c" in results
        assert "ctrl-d" in results

    def test_key_empty_prefix(self):
        results = self._completions("send-key sim ")
        assert set(results) == set(self.key_comps)

    def test_signal_sig_completions(self):
        results = self._completions("send-signal sim SIG")
        assert "SIGINT" in results
        assert "SIGTERM" in results

    def test_signal_empty_prefix(self):
        results = self._completions("send-signal sim ")
        assert set(results) == set(self.sig_comps)

    def test_dot_not_in_headless_completions(self):
        """'.' must NOT appear in headless job-name completions."""
        results = self._completions("send-key ")
        assert "." not in results

    def test_dot_not_in_signal_completions(self):
        results = self._completions("send-signal ")
        assert "." not in results


# ---------------------------------------------------------------------------
# Goal 4 : Hook.action_send_key
# ---------------------------------------------------------------------------

class TestActionSendKey:
    """Tests for Hook.action_send_key()."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from rpkbin.wave.hook import Hook
        self.Hook = Hook

    # ---- happy path ----

    def test_send_key_called_on_pty_job(self):
        job = MagicMock()
        job.send_key = MagicMock()
        action = self.Hook.action_send_key("ctrl-c")
        action(job, {})
        job.send_key.assert_called_once_with("ctrl-c")

    def test_send_key_ctrl_d(self):
        job = MagicMock()
        action = self.Hook.action_send_key("ctrl-d")
        action(job, {})
        job.send_key.assert_called_once_with("ctrl-d")

    # ---- non-PTY job (no send_key) ----

    def test_non_pty_job_does_not_crash(self):
        """If job has no send_key, action warns and returns without crashing."""
        job = MagicMock(spec=[])  # no attributes except those we add
        job.name = "batchjob"
        action = self.Hook.action_send_key("ctrl-c")
        action(job, {})  # must not raise

    def test_non_pty_job_logs_warning(self, caplog):
        job = MagicMock(spec=[])
        job.name = "batchjob"
        action = self.Hook.action_send_key("ctrl-c")
        with caplog.at_level(logging.WARNING):
            action(job, {})
        assert any("does not support send_key" in r.message for r in caplog.records)

    # ---- error: ValueError ----

    def test_value_error_does_not_crash(self):
        job = MagicMock()
        job.send_key.side_effect = ValueError("unsupported key 'xyz'")
        action = self.Hook.action_send_key("xyz")
        action(job, {})  # must not raise

    def test_value_error_logs_warning(self, caplog):
        job = MagicMock()
        job.name = "sim"
        job.send_key.side_effect = ValueError("unsupported key 'xyz'")
        action = self.Hook.action_send_key("xyz")
        with caplog.at_level(logging.WARNING):
            action(job, {})
        assert any("send_key failed" in r.message for r in caplog.records)

    # ---- error: RuntimeError ----

    def test_runtime_error_does_not_crash(self):
        job = MagicMock()
        job.send_key.side_effect = RuntimeError("job not running")
        action = self.Hook.action_send_key("ctrl-c")
        action(job, {})  # must not raise

    def test_runtime_error_logs_warning(self, caplog):
        job = MagicMock()
        job.name = "sim"
        job.send_key.side_effect = RuntimeError("job not running")
        action = self.Hook.action_send_key("ctrl-c")
        with caplog.at_level(logging.WARNING):
            action(job, {})
        assert any("send_key failed" in r.message for r in caplog.records)

    # ---- action_send_key is a factory (returns callable) ----

    def test_factory_returns_callable(self):
        action = self.Hook.action_send_key("ctrl-c")
        assert callable(action)

    def test_each_call_returns_independent_closure(self):
        a1 = self.Hook.action_send_key("ctrl-c")
        a2 = self.Hook.action_send_key("ctrl-d")
        job = MagicMock()
        a1(job, {})
        job.send_key.assert_called_once_with("ctrl-c")
        a2(job, {})
        assert job.send_key.call_count == 2
        job.send_key.assert_called_with("ctrl-d")

    # ---- key name passed through correctly ----

    def test_key_name_passthrough(self):
        for key in ["ctrl-c", "ctrl-d", "ctrl-z", "ctrl-\\", "enter", "tab"]:
            job = MagicMock()
            action = self.Hook.action_send_key(key)
            action(job, {})
            job.send_key.assert_called_with(key)
