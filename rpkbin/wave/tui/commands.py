"""Command bar helpers for the Wave Textual TUI."""

from __future__ import annotations

import shlex

from textual.binding import Binding
from textual.events import Key
from textual.widgets import Input

from rpkbin.wave.tui.view_models import command_identifier_for_job


_HELP_TEXT = """\
[bold green]Key Scopes[/bold green]
  [cyan]Global[/cyan]       F1–F4, :, Ctrl+C  — always active
  [cyan]Job Detail[/cyan]   [ ] { } 1–4 i F9 F10  — active when Job Detail is open
               and focus is not in any input bar
  [cyan]Input[/cyan]        Enter Esc ↑ ↓ Tab  — active when an input bar is focused

[bold green]Navigation[/bold green]
  [cyan]F1 / F2 / F3 / F4[/cyan]  Switch tabs (Dashboard / Job Detail / Session Log / Help)
  [cyan]:[/cyan]                Focus Command Bar  [dim](global)[/dim]
  [cyan]Esc[/cyan]              Leave input bar, return focus to content area  [dim](input)[/dim]
  [cyan]↑ / ↓[/cyan]            Dashboard: move job cursor  [dim](job list focused)[/dim]
                   Command Bar: browse history  [dim](command bar focused)[/dim]
  [cyan]Enter[/cyan]            Dashboard: open Job Detail for selected job  [dim](job list focused)[/dim]
                   Input bars: submit  [dim](input focused)[/dim]
  [cyan][ / ][/cyan]            Previous / next job in Job Detail  [dim](job detail)[/dim]
  [cyan]{ / }[/cyan]            Previous / next running job in Job Detail  [dim](job detail)[/dim]
  [cyan]1 / 2 / 3 / 4[/cyan]   Switch right panel: INFO / DATA / EVENTS / ERRORS  [dim](job detail)[/dim]
  [cyan]Tab[/cyan]              Command Bar: autocomplete  [dim](command bar focused)[/dim]

[bold green]Terminal Shortcuts[/bold green]  [dim](job detail, PTY jobs only)[/dim]
  [cyan]i[/cyan]    Focus Job Input Bar — send text to current job's stdin
  [cyan]F9[/cyan]   Send Ctrl-C (\\x03) to the current PTY job
  [cyan]F10[/cyan]  Send Ctrl-D (\\x04) to the current PTY job
  [dim]For OS signals, use: send-signal . SIGTERM[/dim]

[bold green]Inspect[/bold green]
  [cyan]help[/cyan]                    Show this reference
  [cyan]status[/cyan]                  List all jobs
  [cyan]show   <job>[/cyan]            Compact summary for one job
  [cyan]logs   <job> [n][/cyan]        Last n log lines (default 50)
  [cyan]data   <job>[/cyan]            Parsed data for a job
  [cyan]events <job>[/cyan]            User event history for a job

[bold green]Control Jobs[/bold green]
  [cyan]pause[/cyan]                   Pause dispatch of pending jobs
  [cyan]resume[/cyan]                  Resume dispatch
  [cyan]stop   <job>[/cyan]            Graceful stop, fallback to force stop
  [cyan]stop   -g <job>[/cyan]         Graceful only
  [cyan]stop   --all[/cyan]            Graceful stop all active jobs
  [cyan]stop   --group <tag>[/cyan]    Graceful stop all active jobs with a tag
  [cyan]cancel <job>[/cyan]            Force-cancel immediately
  [cyan]cancel --all[/cyan]            Force-cancel all active jobs
  [cyan]cancel --group <tag>[/cyan]    Force-cancel all active jobs with a tag
  [cyan]skip   <job>[/cyan]            Skip a pending job
  [cyan]rerun  <job>[/cyan]            Rerun a job (creates a new instance)
  [cyan]action <job> <name>[/cyan]     Run a user-defined job action
  [cyan]session_action <name>[/cyan]   Run a user-defined session action

[bold green]Interactive I/O[/bold green]
  [cyan]i[/cyan]                        Focus Job Input Bar  [dim](job detail)[/dim]
  [cyan]send-line   <job> <text>[/cyan]  Send text + newline to a job's stdin
  [cyan]send-key    <job> <key>[/cyan]   Send terminal control key to a PTY job
                               [dim]ctrl-c  ctrl-d  ctrl-z  enter  tab[/dim]
  [cyan]send-signal <job> <sig>[/cyan]   Send an OS signal to a running job
                               [dim]SIGINT  SIGTERM  SIGKILL  SIGUSR1  SIGUSR2[/dim]

[bold green]Exit[/bold green]
  [cyan]exit[/cyan]            Leave when no active jobs remain
  [cyan]exit --stop[/cyan]     Stop active jobs gracefully, then leave
  [cyan]exit --force[/cyan]    Force-kill active jobs, then leave
  [cyan]Ctrl+C[/cyan]          Quit  [dim](warns if jobs active; double-tap within 3s to force quit)[/dim]

[dim]<job> accepts a unique name, job id, or unique id prefix.
In Job Detail, [bold cyan].[/bold cyan] refers to the currently open job.
Names with spaces must be quoted, e.g. logs "my job".[/dim]
"""


_WAVE_COMMANDS: list[str] = [
    "help", "status", "show", "logs", "data", "events",
    "pause", "resume", "stop", "skip", "cancel", "rerun",
    "action", "session_action", "send-line", "send-key", "send-signal", "watch", "exit",
]
_WAVE_JOB_COMMANDS: set[str] = {
    "show", "logs", "data", "events", "stop", "skip", "cancel", "rerun",
    "action", "send-line", "send-key", "send-signal",
}
_DOT_JOB_CMDS: frozenset[str] = frozenset(_WAVE_JOB_COMMANDS)
_KEY_COMPLETIONS: list[str] = ["ctrl-c", "ctrl-d", "ctrl-z", "ctrl-\\", "enter", "tab"]
_SIGNAL_COMPLETIONS: list[str] = ["SIGINT", "SIGTERM", "SIGKILL", "SIGUSR1", "SIGUSR2"]


def _find_common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    m1, m2 = min(strings), max(strings)
    for i, char in enumerate(m1):
        if char != m2[i]:
            return m1[:i]
    return m1


def _expand_dot_in_parts(
    parts: list[str],
    detail_job_id: str | None,
) -> tuple[list[str] | None, str | None]:
    """Expand '.' to the current JOB DETAIL job id in *parts*."""
    if not parts:
        return parts, None

    verb = parts[0].lower()
    if verb not in _DOT_JOB_CMDS:
        return parts, None

    if verb in ("stop", "cancel") and len(parts) >= 2 and parts[1].startswith("-"):
        if parts[1] in ("--all", "--group"):
            return parts, None
        job_idx = 2
    else:
        job_idx = 1

    if job_idx >= len(parts):
        return parts, None

    if parts[job_idx] != ".":
        return parts, None

    if detail_job_id is None:
        return None, (
            "[Wave] '.' means the current JOB DETAIL job; "
            "open a job detail first."
        )

    new_parts = list(parts)
    new_parts[job_idx] = detail_job_id
    return new_parts, None


class CommandInput(Input):
    """An Input widget that supports command history and autocomplete."""

    BINDINGS = [
        Binding("tab", "autocomplete", "Autocomplete", show=False),
        Binding("shift+tab", "autocomplete_backward", "Autocomplete Back", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:  # noqa: D107
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index: int = -1
        self._draft: str = ""
        self._cycle_matches: list[str] = []
        self._cycle_index: int = -1
        self._cycle_before: str = ""

    def record(self, line: str) -> None:
        """Append *line* to history, deduplicating consecutive duplicates."""
        if line and (not self._history or self._history[-1] != line):
            self._history.append(line)
        self._history_index = -1
        self._draft = ""

    def on_key(self, event: Key) -> None:
        if event.key == "tab":
            event.prevent_default()
        if event.key not in ("tab", "shift+tab"):
            self._cycle_matches = []
            self._cycle_index = -1
        if not self._history:
            return
        if event.key == "up":
            event.stop()
            if self._history_index == -1:
                self._draft = self.value
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            self.value = self._history[self._history_index]
            self.cursor_position = len(self.value)
        elif event.key == "down":
            event.stop()
            if self._history_index == -1:
                return
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                self.value = self._history[self._history_index]
            else:
                self._history_index = -1
                self.value = self._draft
            self.cursor_position = len(self.value)


    def action_autocomplete_backward(self) -> None:
        """Cycle autocompletion backwards."""
        if self._cycle_matches:
            self._cycle_index = (self._cycle_index - 1) % len(self._cycle_matches)
            match = self._cycle_matches[self._cycle_index]
            self.value = self._cycle_before + match + " "
            self.cursor_position = len(self.value)

    def action_autocomplete(self) -> None:
        """Autocompletion for commands and job names."""
        if self._cycle_matches:
            self._cycle_index = (self._cycle_index + 1) % len(self._cycle_matches)
            match = self._cycle_matches[self._cycle_index]
            self.value = self._cycle_before + match + " "
            self.cursor_position = len(self.value)
            return

        val = self.value
        if not val:
            return

        try:
            parts = shlex.split(val, posix=True)
        except ValueError:
            parts = val.split()

        if val.endswith(" "):
            parts.append("")

        if len(parts) <= 1:
            prefix = parts[0] if parts else ""
            matches = [cmd for cmd in _WAVE_COMMANDS if cmd.startswith(prefix)]
            self._apply_completion(prefix, matches)
            return

        if len(parts) == 2:
            self._complete_second_arg(parts)
            return

        if len(parts) == 3:
            self._complete_third_arg(parts)

    def _apply_completion(
        self,
        prefix: str,
        matches: list[str],
        *,
        before: str = "",
        suffix: str = " ",
        quote_spaces: bool = False,
    ) -> None:
        if quote_spaces:
            matches = [f'"{m}"' if " " in m else m for m in matches]
        if len(matches) == 1:
            self.value = before + matches[0] + suffix
            self.cursor_position = len(self.value)
        elif len(matches) > 1:
            common = _find_common_prefix(matches)
            if common and common != prefix:
                self.value = before + common
                self.cursor_position = len(self.value)
            else:
                self._cycle_matches = matches
                self._cycle_index = 0
                self._cycle_before = before
                self.value = before + matches[0] + suffix
                self.cursor_position = len(self.value)

    def _complete_second_arg(self, parts: list[str]) -> None:
        verb = parts[0].lower()
        prefix = parts[1]
        if verb == "session_action" and hasattr(self.app, "_session"):
            matches = [
                action for action in self.app._session.session_action_names()
                if action.startswith(prefix)
            ]
            self._apply_completion(prefix, matches, before=f"{verb} ")
        elif verb in _WAVE_JOB_COMMANDS and hasattr(self.app, "_session"):
            jobs = self.app._session.jobs()
            candidates = [command_identifier_for_job(job, jobs) for job in jobs]
            if (
                hasattr(self.app, "_detail_job")
                and self.app._detail_job is not None
                and ".".startswith(prefix)
            ):
                candidates.insert(0, ".")
            matches = [name for name in candidates if name.startswith(prefix)]
            self._apply_completion(prefix, matches, before=f"{verb} ", quote_spaces=True)

    def _complete_third_arg(self, parts: list[str]) -> None:
        verb = parts[0].lower()
        current = parts[2]
        job_name = parts[1]
        quoted_job = f'"{job_name}"' if " " in job_name else job_name
        before = f"{verb} {quoted_job} "
        if verb == "send-key":
            matches = [key for key in _KEY_COMPLETIONS if key.startswith(current)]
            self._apply_completion(current, matches, before=before)
        elif verb == "send-signal":
            matches = [sig for sig in _SIGNAL_COMPLETIONS if sig.startswith(current)]
            self._apply_completion(current, matches, before=before)
        elif verb == "action" and hasattr(self.app, "_session"):
            matches = [
                action for action in self.app._session.job_action_names()
                if action.startswith(current)
            ]
            self._apply_completion(current, matches, before=before)
