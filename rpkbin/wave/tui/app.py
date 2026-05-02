"""app.py - WaveApp: Textual TUI for monitoring and controlling Wave sessions.

Layout
------
Three top-level tabs:
  DASHBOARD   - live DataTable of all jobs (Name/Status/Elapsed/Progress/Result/Tags)
  JOB DETAIL  - log view (left 60%) + Data/Events/System tabs (right 40%)
  SYSTEM LOG  - session-level events

Bottom command bar reuses runner._parse_repl_line + runner._handle_cmd verbatim.
stdout from those helpers is redirected into the SYSTEM LOG tab via
contextlib.redirect_stdout so the terminal stays clean.

Threading
---------
All callbacks from worker threads (_on_job_updated, _on_job_added) are
forwarded to the Textual main thread via self.call_from_thread().

Detail panel refresh strategy
------------------------------
_on_job_updated  → updates Dashboard row cells only (status, progress, etc.)
_tick (1s)       → drives log / DATA / EVENTS panel sync for the selected job
                   and refreshes elapsed for all running jobs

This separation prevents the detail panel from being flooded with full buffer
copies on every parser callback, which was the main source of UI lag.
"""

from __future__ import annotations

import contextlib
import io
import logging
import time
from typing import TYPE_CHECKING

from rich.markup import escape as _escape_markup

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Key
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rpkbin.wave.session import Session


# ---------------------------------------------------------------------------
# CommandInput - Input subclass with Up/Down command history
# ---------------------------------------------------------------------------

class CommandInput(Input):
    """An Input widget that supports Up/Down arrow key command history.

    History navigation is handled entirely within this widget and does not
    interfere with any other focusable widget (e.g. DataTable) because
    key events reaching this handler only fire when this widget is focused.
    """

    def __init__(self, *args, **kwargs) -> None:  # noqa: D107
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index: int = -1   # -1 = not browsing history
        self._draft: str = ""           # saved draft while browsing

    def record(self, line: str) -> None:
        """Append *line* to history (dedup consecutive duplicates)."""
        if line and (not self._history or self._history[-1] != line):
            self._history.append(line)
        self._history_index = -1
        self._draft = ""

    def on_key(self, event: Key) -> None:  # noqa: D102
        if not self._history:
            return
        if event.key == "up":
            event.stop()   # prevent propagation to TabbedContent / DataTable
            if self._history_index == -1:
                self._draft = self.value          # save current input as draft
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
        elif event.key == "tab":
            event.stop()  # Prevent Textual from changing focus
            self._handle_autocomplete()

    def _handle_autocomplete(self) -> None:
        """Autocompletion for commands and job names."""
        import shlex
        val = self.value
        if not val:
            return

        try:
            parts = shlex.split(val, posix=True)
        except ValueError:
            parts = val.split()

        if val.endswith(" "):
            parts.append("")
        
        def find_common_prefix(strings: list[str]) -> str:
            if not strings:
                return ""
            m1, m2 = min(strings), max(strings)
            for i, c in enumerate(m1):
                if c != m2[i]:
                    return m1[:i]
            return m1

        if len(parts) <= 1:
            prefix = parts[0] if parts else ""
            matches = [c for c in _WAVE_COMMANDS if c.startswith(prefix)]
            if len(matches) == 1:
                self.value = matches[0] + " "
                self.cursor_position = len(self.value)
            elif len(matches) > 1:
                cp = find_common_prefix(matches)
                if cp and cp != prefix:
                    self.value = cp
                    self.cursor_position = len(self.value)
        elif len(parts) == 2:
            verb = parts[0].lower()
            if verb in _WAVE_JOB_COMMANDS and hasattr(self.app, "_session"):
                prefix = parts[1]
                job_names = [j.name for j in self.app._session.jobs()]
                matches = [n for n in job_names if n.startswith(prefix)]
                if len(matches) == 1:
                    name = matches[0]
                    quoted = f'"{name}"' if " " in name else name
                    self.value = f"{verb} {quoted} "
                    self.cursor_position = len(self.value)
                elif len(matches) > 1:
                    cp = find_common_prefix(matches)
                    if cp and cp != prefix:
                        quoted = f'"{cp}"' if " " in cp else cp
                        self.value = f"{verb} {quoted}"
                        self.cursor_position = len(self.value)


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
[bold green]TUI Navigation[/bold green]
  [cyan]F1 / F2 / F3 / F4[/cyan]  Switch tabs (Dashboard / Job Detail / System Log / Help)
  [cyan]:[/cyan]                Focus Command Bar (Vim style)
  [cyan]Esc[/cyan]              Exit Command Bar, return focus to active panel
  [cyan]UP / DOWN[/cyan]            Navigate jobs in Dashboard; scroll in log / info views
  [cyan]Enter[/cyan]            Open selected job in Job Detail
  [cyan][ ][/cyan]              Previous / next job in Job Detail
  [cyan]{ }[/cyan]              Previous / next running job in Job Detail
  [cyan]UP / DOWN[/cyan] (in CMD) Browse command history
  [cyan]Tab[/cyan]   (in CMD) Auto-complete commands and job names
  [cyan]Shift + Drag[/cyan]     Select text (Right-click or Ctrl+Shift+C to copy, NEVER Ctrl+C)
  [cyan]Ctrl+C[/cyan]           Quit (Safe: warns if jobs are active. Double-tap to force quit)

[bold green]Command Bar (wave>)[/bold green]
  [cyan]help[/cyan]                    Show this reference
  [cyan]status[/cyan]                  List all jobs
  [cyan]show   <job>[/cyan]            Compact summary for one job
  [cyan]logs   <job> [n][/cyan]        Last [i]n[/i] log lines (default 50)
  [cyan]data   <job>[/cyan]            Parsed data output for a job
  [cyan]events <job>[/cyan]            User event history for a job
  [cyan]pause[/cyan]                   Pause dispatch of pending jobs
  [cyan]resume[/cyan]                  Resume dispatch of pending jobs
  [cyan]stop   <job>[/cyan]            Graceful stop, fallback to force stop
  [cyan]stop   -g <job>[/cyan]         Graceful only (no force fallback)
  [cyan]stop   --all[/cyan]            Graceful stop all active jobs
  [cyan]stop   --group <tag>[/cyan]    Graceful stop all active jobs with a tag
  [cyan]cancel <job>[/cyan]            Force-cancel a job immediately
  [cyan]cancel --all[/cyan]            Force-cancel all active jobs
  [cyan]cancel --group <tag>[/cyan]    Force-cancel all active jobs with a tag
  [cyan]skip   <job>[/cyan]            Skip a pending job
  [cyan]input  <job> <text>[/cyan]     Send stdin text (\\\\n \\\\r \\\\t supported)
  [cyan]signal <job> <sig>[/cyan]      Send OS signal (e.g. SIGINT)
  [cyan]exit[/cyan]                    Leave TUI when no active jobs remain
  [cyan]exit   --stop[/cyan]           Stop active jobs gracefully, then leave
  [cyan]exit   --force[/cyan]          Force-kill active jobs, then leave

[dim]Note: <job> may be a unique name or job id. Names with spaces must be quoted.[/dim]
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_COLOR: dict[str, str] = {
    "running":   "green",
    "done":      "cyan",
    "pending":   "yellow",
    "failed":    "red",
    "cancelled": "grey50",
}

# Operational commands produce a brief confirmation message; the user stays on
# the current tab and sees a Toast.  All other commands (display-oriented like
# 'logs', 'status', or unknown verbs whose error we want the user to read)
# fall through to System Log so the output is clearly visible.
_OPERATION_CMDS: frozenset[str] = frozenset({"pause", "resume", "stop", "skip", "cancel", "input", "signal"})

_WAVE_COMMANDS: list[str] = ["help", "status", "show", "logs", "data", "events", "pause", "resume", "stop", "skip", "cancel", "input", "signal", "watch", "exit"]
_WAVE_JOB_COMMANDS: set[str] = {"show", "logs", "data", "events", "stop", "skip", "cancel", "input", "signal"}


def _fmt_elapsed(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _job_elapsed_s(job) -> float | None:
    started = getattr(job, "start_time", None)
    if started is None:
        return None
    finished = getattr(job, "end_time", None)
    end = finished if finished is not None else time.monotonic()
    return max(0.0, end - started)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
Screen { layout: vertical; }

* { transition: none; }

TabbedContent { height: 1fr; }

#dashboard-container { height: 1fr; }

#dashboard-left {
    width: 55%;
    border-right: solid $primary-darken-2;
}

#dashboard-preview { width: 45%; }

#dashboard-log { height: 1fr; }

#detail-root { height: 1fr; }

#detail-header {
    height: 1;
    padding: 0 1;
    background: $surface;
}

#detail-container { height: 1fr; }

#left-panel {
    width: 60%;
    border-right: solid $primary-darken-2;
}

#right-panel { width: 40%; }

#log-view { height: 1fr; }

#data-table  { height: 1fr; }
#events-log  { height: 1fr; }
#system-job-log { height: 1fr; }
#session-log { height: 1fr; }

#info-panel {
    height: 1fr;
    padding: 1 2;
}

#cmd-input {
    dock: bottom;
    border-top: solid $primary-darken-2;
}

Footer { height: 1; }
"""


# ---------------------------------------------------------------------------
# WaveApp
# ---------------------------------------------------------------------------

class WaveApp(App):
    """Textual TUI for a Wave session.

    Usage (called by runner._run_tui, not directly by user code)::

        app = WaveApp(session)
        session._start(
            tui_notify=app._on_job_updated,
            tui_job_added=app._on_job_added,
        )
        app.run()
    """

    CSS = _CSS
    TITLE = "WAVE"

    BINDINGS = [
        Binding("ctrl+c", "request_quit", "Quit"),
        Binding("f1", "goto_tab('tab-dashboard')", "Dashboard", show=True),
        Binding("f2", "goto_tab('tab-detail')", "Job Detail", show=True),
        Binding("f3", "goto_tab('tab-system')", "System Log", show=True),
        Binding("f4", "goto_tab('tab-help')", "Help", show=True),
        Binding(":", "focus_cmd", "Command", show=False),
        Binding("escape", "unfocus_cmd", "Exit Command", show=False),
        Binding("left_square_bracket", "previous_detail_job", "Previous Job", show=False),
        Binding("right_square_bracket", "next_detail_job", "Next Job", show=False),
        Binding("left_curly_bracket", "previous_running_detail_job", "Previous Running Job", show=False),
        Binding("right_curly_bracket", "next_running_detail_job", "Next Running Job", show=False),
    ]

    def __init__(self, session: "Session") -> None:  # noqa: D107
        super().__init__()
        self._session = session

        # Which job is currently shown in JOB DETAIL
        self._detail_job = None

        # Stable row identities for #jobs-table. Use job ids, not names, so
        # duplicate human-readable names never collapse into one TUI row.
        self._row_keys: set[str] = set()
        # Row cache to prevent needless DataTable cell updates
        self._row_cache: dict[str, tuple] = {}
        # data-table row keys and cache for the currently shown job
        self._data_row_keys: set[str] = set()
        self._data_cache: dict[str, str] = {}

        # Column key tuples returned by DataTable.add_columns()
        self._jobs_col_keys: tuple = ()
        self._data_col_keys: tuple = ()

        # Incremental sync counters (avoid re-painting unchanged content).
        # All three are declared here to ensure they are always defined before
        # any callback path can read them.
        self._log_total_sync_count: int = 0
        self._session_event_count: int = 0
        self._events_sync_count: int = 0

        # Double-tap quit tracking
        self._last_ctrl_c: float = 0.0

        # Job row key currently highlighted in the Dashboard table (for quick-stop).
        self._highlighted_job_key: str | None = None

        # Dashboard log preview tracks the highlighted row independently of
        # JOB DETAIL so F1 can be used for quick scan-and-read workflows.
        self._dashboard_preview_job_key: str | None = None
        self._dashboard_preview_log_count: int = 0

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:  # noqa: D102
        yield Header(show_clock=True)

        with TabbedContent(id="main-tabs"):
            # -- Tab 1: DASHBOARD --------------------------------------
            with TabPane("DASHBOARD", id="tab-dashboard"):
                with Horizontal(id="dashboard-container"):
                    with Vertical(id="dashboard-left"):
                        yield DataTable(id="jobs-table", cursor_type="row")
                    with Vertical(id="dashboard-preview"):
                        yield RichLog(id="dashboard-log", markup=False, highlight=False, max_lines=2000)

            # -- Tab 2: JOB DETAIL -------------------------------------
            with TabPane("JOB DETAIL", id="tab-detail"):
                with Vertical(id="detail-root"):
                    yield Static("", id="detail-header", markup=True)
                    with Horizontal(id="detail-container"):
                        with Vertical(id="left-panel"):
                            yield RichLog(id="log-view", markup=False, highlight=False, max_lines=5000)

                        with Vertical(id="right-panel"):
                            with TabbedContent(id="detail-tabs"):
                                with TabPane("INFO", id="detail-info"):
                                    yield Static("", id="info-panel", markup=True)
                                with TabPane("DATA", id="detail-data"):
                                    yield DataTable(id="data-table", cursor_type="row")
                                with TabPane("EVENTS", id="detail-events"):
                                    yield RichLog(id="events-log", markup=True, max_lines=2000)
                                with TabPane("SYSTEM", id="detail-system"):
                                    yield RichLog(id="system-job-log", markup=True, max_lines=2000)

            # -- Tab 3: SYSTEM LOG (session-level events + command output)
            with TabPane("SYSTEM LOG", id="tab-system"):
                yield RichLog(id="session-log", markup=True, max_lines=5000)

            # -- Tab 4: HELP -------------------------------------------
            with TabPane("HELP", id="tab-help"):
                help_widget = Static(_HELP_TEXT, markup=True, id="help-text")
                help_widget.can_focus = True
                yield help_widget

        yield CommandInput(placeholder="Press ':' to type commands (UP / DOWN for history)", id="cmd-input")
        yield Footer()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def on_mount(self) -> None:  # noqa: D102
        # DASHBOARD table columns
        t = self.query_one("#jobs-table", DataTable)
        self._jobs_col_keys = t.add_columns(
            "Name", "ID", "Status", "Elapsed", "Progress", "Retries", "Result", "Tags"
        )

        # DATA table columns (rows are added lazily per-job)
        dt = self.query_one("#data-table", DataTable)
        self._data_col_keys = dt.add_columns("Key", "Value")

        # Populate rows for jobs already registered before _start()
        for job in self._session.jobs():
            self._add_job_row_on_main(job)

        # Initial hint for JOB DETAIL if they switch without selecting
        self._update_detail_header(None)
        self.query_one("#log-view", RichLog).write(
            "No job selected.\n"
            "Press F1 to go to the Dashboard, "
            "then highlight a job and press Enter to open it here.\n"
            "Inside Job Detail, use [ ] for jobs and { } for running jobs."
        )

        # Periodic timer: elapsed refresh + detail panel sync + session-event drain
        self.set_interval(1.0, self._tick)

        self._update_subtitle()
        self.query_one("#jobs-table", DataTable).focus()

        self.query_one("#dashboard-log", RichLog).write(
            "No job selected.\n"
            "Highlight a job in the Dashboard to preview its log here."
        )

    # ------------------------------------------------------------------
    # Worker-thread callbacks (called by Session via tui_notify /
    # tui_job_added - must not touch widgets directly)
    # ------------------------------------------------------------------

    def _on_job_updated(self, job) -> None:
        """Called from a worker thread when a job's observable state changes.

        Only updates the Dashboard row cells (status, progress, etc.).
        Log / DATA / EVENTS panel sync is intentionally deferred to _tick
        (once per second) to avoid flooding the main thread with full buffer
        copies on every parser callback.
        """
        try:
            self.call_from_thread(self._refresh_job_row, job)
        except RuntimeError:
            logger.debug("Dropped TUI job update while the event loop was closing.", exc_info=True)
        except Exception:
            logger.exception("Unexpected failure scheduling TUI job update for %r.", getattr(job, "name", job))

    def _on_job_added(self, job) -> None:
        """Called from a worker thread when a new job is dynamically added."""
        try:
            self.call_from_thread(self._add_job_row_on_main, job)
        except RuntimeError:
            logger.debug("Dropped TUI job-add update while the event loop was closing.", exc_info=True)
        except Exception:
            logger.exception("Unexpected failure scheduling TUI job-add update for %r.", getattr(job, "name", job))

    # ------------------------------------------------------------------
    # DASHBOARD helpers (main-thread only)
    # ------------------------------------------------------------------

    def _build_row_cells(self, job) -> tuple[str, ...]:
        """Return one cell value per column: Name Status Elapsed Progress Retries Result Tags."""
        name = _escape_markup(str(job.name))
        status = getattr(job, "status", "pending")
        color = _STATUS_COLOR.get(status, "white")
        status_cell = f"[{color}]{status.upper()}[/{color}]"

        elapsed_cell = _fmt_elapsed(_job_elapsed_s(job))

        prog = getattr(job, "progress", None)
        if isinstance(prog, (int, float)):
            filled = max(0, min(10, round(prog / 10)))
            bar = "#" * filled + "-" * (10 - filled)
            prog_cell = f"{bar} {prog:.0f}%"
        else:
            prog_cell = ""

        retry_count = getattr(job, "retry_count", 0)
        max_retries = getattr(job, "max_retries", 0)
        # Hide retry counter entirely when retries are not configured (no info value).
        retries_cell = f"{retry_count}/{max_retries}" if max_retries > 0 else ""

        # Result column: outcome indicator + error snippet for failed jobs.
        # is_skipped is a Wave-layer concept (cancelled with skip flag); plain
        # scheduler jobs that are cancelled simply show nothing here.
        if status == "done":
            result_cell = "[cyan]PASS[/cyan]"
        elif status == "failed":
            error = getattr(job, "error", None)
            if error:
                snippet = (error[:27] + "...") if len(error) > 30 else error
                result_cell = f"[red]FAIL[/red] [dim]{_escape_markup(snippet)}[/dim]"
            else:
                result_cell = "[red]FAIL[/red]"
        elif status == "cancelled" and getattr(job, "is_skipped", False):
            result_cell = "[grey50]SKIP[/grey50]"
        else:
            result_cell = ""

        tags = getattr(job, "tags", None) or set()
        tags_cell = ",".join(sorted(_escape_markup(str(t)) for t in tags))

        id_cell = _escape_markup(str(getattr(job, "id", ""))[:8])

        return name, id_cell, status_cell, elapsed_cell, prog_cell, retries_cell, result_cell, tags_cell

    def _job_row_key(self, job) -> str:
        """Return the stable DataTable row key for *job*."""
        return str(getattr(job, "id", job.name))

    def _job_for_row_key(self, row_key: str):
        """Resolve a DataTable row key back to its job."""
        for job in self._session.jobs():
            if self._job_row_key(job) == row_key:
                return job
        return None

    def _command_identifier_for_job(self, job) -> str:
        """Prefer name for unique jobs; use id when duplicate names exist."""
        name = str(job.name)
        same_name = [j for j in self._session.jobs() if str(j.name) == name]
        if len(same_name) > 1:
            return self._job_row_key(job)
        return name

    def _build_info_text(self, job) -> str:
        """Build Rich markup text for the INFO metadata panel in Job Detail.

        Displays static job metadata (name, id, priority, resources, tags,
        retry config).  Only non-default / non-empty fields are shown to
        keep the panel uncluttered.
        """
        lines: list[str] = []
        lines.append(f"[bold white]{_escape_markup(str(job.name))}[/bold white]")
        job_id = getattr(job, "id", None)
        if job_id:
            lines.append(f"[dim]ID: {_escape_markup(str(job_id))}[/dim]")

        priority = getattr(job, "priority", 0)
        if priority:
            lines.append(f"\n[cyan]Priority:[/cyan]  {priority}")

        max_retries = getattr(job, "max_retries", 0)
        if max_retries > 0:
            retry_count = getattr(job, "retry_count", 0)
            lines.append(f"[cyan]Retries:[/cyan]   {retry_count} / {max_retries}")

        resources = getattr(job, "resources", None) or {}
        if resources:
            lines.append("\n[bold]Resources[/bold]")
            for k, v in resources.items():
                lines.append(f"  [cyan]{_escape_markup(str(k))}[/cyan] = {_escape_markup(str(v))}")

        tags = getattr(job, "tags", None) or set()
        if tags:
            tag_str = "  ".join(f"[dim]#{_escape_markup(str(t))}[/dim]" for t in sorted(str(t) for t in tags))
            lines.append(f"\n[bold]Tags[/bold]   {tag_str}")

        return "\n".join(lines)

    def _add_job_row_on_main(self, job, from_tick: bool = False) -> None:
        """Add a fresh row for *job* to the DASHBOARD table (main thread)."""
        key = self._job_row_key(job)
        if key in self._row_keys:
            return  # already present
        self._row_keys.add(key)

        cells = self._build_row_cells(job)
        self._row_cache[key] = cells

        t = self.query_one("#jobs-table", DataTable)
        t.add_row(*cells, key=key)

        if not from_tick:
            self._update_subtitle()

    def _refresh_job_row(self, job, from_tick: bool = False) -> None:
        """Update a job's DASHBOARD row cells.

        Detail panel sync (log / DATA / EVENTS) is only performed when
        called from _tick (from_tick=True) to avoid high-frequency full
        buffer copies triggered by parser or emit callbacks.
        """
        key = self._job_row_key(job)
        if key not in self._row_keys:
            self._add_job_row_on_main(job, from_tick=from_tick)
            return

        cells = self._build_row_cells(job)
        if self._row_cache.get(key) != cells:
            self._row_cache[key] = cells
            t = self.query_one("#jobs-table", DataTable)
            for col_key, value in zip(self._jobs_col_keys, cells):
                try:
                    t.update_cell(key, col_key, value, update_width=False)
                except KeyError:
                    logger.debug(
                        "Row or column removed before TUI cell update for job %r (row=%s, col=%s).",
                        getattr(job, "name", job), key, col_key,
                    )
                except Exception:
                    logger.warning(
                        "Failed to update TUI cell for job %r (row=%s, col=%s).",
                        getattr(job, "name", job),
                        key,
                        col_key,
                        exc_info=True,
                    )

        # Detail panel sync only from the periodic tick, not from every
        # event callback.  This bounds the cost to one refresh per second
        # regardless of how frequently parsers or hooks fire.
        if from_tick and self._detail_job is not None and self._job_row_key(self._detail_job) == key:
            try:
                self._refresh_right_panels(job)
            except NoMatches:
                logger.debug("Skipped detail refresh while TUI widgets were unavailable.", exc_info=True)

        if not from_tick:
            self._update_subtitle()

    # ------------------------------------------------------------------
    # Periodic tick (main thread via set_interval)
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Every second: refresh elapsed + detail panels + drain session events."""
        try:
            for job in self._session.jobs():
                self._refresh_job_row(job, from_tick=True)

            self._refresh_dashboard_preview()
            self._update_subtitle()

            # Drain new session-level events into SYSTEM LOG tab
            events = self._session.peek_events()
            new_events = events[self._session_event_count:]
            if new_events:
                log = self.query_one("#session-log", RichLog)
                buf = []
                for ev in new_events:
                    src = ev.get("source", "?")
                    color = "cyan" if src == "system" else "white"
                    buf.append(
                        f"[grey50]{_escape_markup(str(ev.get('time', '?')))}[/grey50] "
                        f"[{color}]\\[{_escape_markup(str(src))}][/{color}] "
                        f"[bold]{_escape_markup(str(ev.get('tag', '')))}[/bold] - {_escape_markup(str(ev.get('message', '')))}"
                    )
                log.auto_scroll = (log.scroll_y >= max(0, log.max_scroll_y - 2))
                log.write("\n".join(buf))
                self._session_event_count = len(events)
        except NoMatches:
            logger.debug("Skipped tick refresh while TUI widgets were unavailable.", exc_info=True)

    def _refresh_dashboard_preview(self, job=None, *, reset: bool = False) -> None:
        """Append log output for the Dashboard-highlighted job."""
        if job is None:
            if self._highlighted_job_key is None:
                return
            job = self._job_for_row_key(self._highlighted_job_key)
        if job is None:
            return

        key = self._job_row_key(job)
        log_view = self.query_one("#dashboard-log", RichLog)

        if reset or self._dashboard_preview_job_key != key:
            self._dashboard_preview_job_key = key
            self._dashboard_preview_log_count = 0
            log_view.clear()
            log_view.write(f"{job.name}  [{key[:8]}]")

        self._dashboard_preview_log_count = self._sync_job_log(
            job,
            log_view,
            self._dashboard_preview_log_count,
            empty_message="No log output yet." if reset else None,
        )

    def _sync_job_log(
        self,
        job,
        log_view: RichLog,
        sync_count: int,
        *,
        empty_message: str | None = None,
    ) -> int:
        """Append newly emitted job log lines to *log_view* and return sync count."""
        total_lines = getattr(job, "_total_log_lines", 0)
        if total_lines <= sync_count:
            if empty_message is not None and total_lines == 0:
                log_view.write(empty_message)
            return sync_count

        new_lines = job.tail(total_lines - sync_count)
        if new_lines:
            log_view.auto_scroll = (log_view.scroll_y >= max(0, log_view.max_scroll_y - 2))
            log_view.write("\n".join(new_lines))
        return total_lines

    def _update_subtitle(self) -> None:
        jobs = self._session.jobs()
        n_run  = sum(1 for j in jobs if getattr(j, "status", None) == "running")
        n_done = sum(1 for j in jobs if getattr(j, "status", None) == "done")
        n_pend = sum(1 for j in jobs if getattr(j, "status", None) == "pending")
        n_fail = sum(1 for j in jobs if getattr(j, "status", None) in ("failed", "cancelled"))
        s = self._session.stats()
        mw = s.get("workers", {}).get("total", "?") if s else "?"
        self.sub_title = (
            f"> {n_run}/{mw} running   OK {n_done} done   "
            f"WAIT {n_pend} pending   FAIL {n_fail} failed"
        )

    # ------------------------------------------------------------------
    # JOB DETAIL helpers
    # ------------------------------------------------------------------

    def _open_detail_for(self, job) -> None:
        """Switch to JOB DETAIL tab and load *job*."""
        self._detail_job = job
        self._update_detail_header(job)
        if job is not None:
            self._sync_dashboard_selection(job)
        # Reset all incremental sync counters for the new job
        self._log_total_sync_count = 0
        self._events_sync_count = 0
        self._data_row_keys = set()
        self._data_cache = {}

        log_view = self.query_one("#log-view", RichLog)
        log_view.clear()
        if job is None:
            log_view.write("No job selected. Go to Dashboard (F1) and press Enter on a job to view details.")

        self.query_one("#events-log", RichLog).clear()
        self.query_one("#system-job-log", RichLog).clear()
        self.query_one("#data-table", DataTable).clear()
        self.query_one("#info-panel", Static).update("")

        if job is not None:
            self._refresh_right_panels(job)
        self.query_one("#main-tabs", TabbedContent).active = "tab-detail"

    def _detail_job_index(self) -> int | None:
        """Return the selected detail job's index in the current session order."""
        if self._detail_job is None:
            return None
        for idx, job in enumerate(self._session.jobs()):
            if self._job_row_key(job) == self._job_row_key(self._detail_job):
                return idx
        return None

    def _navigate_detail_job(self, delta: int, *, status: str | None = None) -> None:
        """Move JOB DETAIL to the next matching job, wrapping around."""
        if not self._is_detail_navigation_context():
            return

        jobs = self._session.jobs()
        if not jobs:
            return

        current_idx = self._detail_job_index()
        if current_idx is None:
            current_idx = 0 if delta >= 0 else len(jobs) - 1

        if status is None:
            self._open_detail_for(jobs[(current_idx + delta) % len(jobs)])
            return

        matches = [
            idx for idx, job in enumerate(jobs)
            if getattr(job, "status", None) == status
        ]
        if not matches:
            self.notify(f"No {status} jobs.", timeout=2)
            return

        step = 1 if delta >= 0 else -1
        probe = current_idx
        for _ in range(len(jobs)):
            probe = (probe + step) % len(jobs)
            if probe in matches:
                self._open_detail_for(jobs[probe])
                return

    def _is_detail_navigation_context(self) -> bool:
        """Return True only when detail navigation keys should be active."""
        if self.query_one("#main-tabs", TabbedContent).active != "tab-detail":
            return False
        focused = self.focused
        return getattr(focused, "id", None) != "cmd-input"

    def _update_detail_header(self, job) -> None:
        """Refresh the one-line JOB DETAIL location/status header."""
        header = self.query_one("#detail-header", Static)
        if job is None:
            header.update("[dim]No job selected. F1 -> choose a job -> Enter.  Jobs: [ ]  Running: { }[/dim]")
            return

        jobs = self._session.jobs()
        idx = self._detail_job_index()
        position = f"{idx + 1}/{len(jobs)}" if idx is not None and jobs else "?/?"
        status = getattr(job, "status", "pending")
        color = _STATUS_COLOR.get(status, "white")
        elapsed = _fmt_elapsed(_job_elapsed_s(job)) or "--:--:--"
        prog = getattr(job, "progress", None)
        progress = f"{prog:.0f}%" if isinstance(prog, (int, float)) else "--"
        name = _escape_markup(str(getattr(job, "name", job)))

        header.update(
            f"[dim]Jobs: [ ][/dim]  [bold]{name}[/bold]  "
            f"[dim]{position}[/dim]  [{color}]{status.upper()}[/{color}]  "
            f"[dim]{elapsed}[/dim]  [cyan]{progress}[/cyan]  [dim]Running: {{ }}[/dim]"
        )

    def _sync_dashboard_selection(self, job) -> None:
        """Keep Dashboard highlight and preview aligned with the detail job."""
        key = self._job_row_key(job)
        self._highlighted_job_key = key

        table = self.query_one("#jobs-table", DataTable)
        try:
            table.move_cursor(row=table.get_row_index(key), animate=False)
        except KeyError:
            logger.debug("Dashboard row %s not found; cursor not synced.", key)
        except Exception:
            logger.debug("Could not sync Dashboard cursor for job row %s.", key, exc_info=True)

        self._refresh_dashboard_preview(job, reset=True)

    def _refresh_right_panels(self, job) -> None:
        """Push the latest state for *job* into the three right-side panels."""
        self._update_detail_header(job)

        # -- INFO tab (static job metadata) ----------------------------
        self.query_one("#info-panel", Static).update(self._build_info_text(job))

        # -- Log panel (append-only for the selected job) --------------
        log_view = self.query_one("#log-view", RichLog)
        self._log_total_sync_count = self._sync_job_log(
            job,
            log_view,
            self._log_total_sync_count,
        )

        # -- DATA tab (upsert by key) -----------------------------------
        data = getattr(job, "peek_data", lambda: {})()
        dt = self.query_one("#data-table", DataTable)

        for k, v in data.items():
            sk = _escape_markup(str(k))
            sv = _escape_markup(str(v))
            if self._data_cache.get(sk) == sv:
                continue
            self._data_cache[sk] = sv

            if sk in self._data_row_keys:
                try:
                    dt.update_cell(sk, self._data_col_keys[1], sv, update_width=False)
                except KeyError:
                    logger.debug(
                        "Data cell row removed before update for job %r (row=%s).",
                        getattr(job, "name", job), sk,
                    )
                except Exception:
                    logger.warning(
                        "Failed to update TUI data cell for job %r (row=%s).",
                        getattr(job, "name", job),
                        sk,
                        exc_info=True,
                    )
            else:
                dt.add_row(sk, sv, key=sk)
                self._data_row_keys.add(sk)

        # -- EVENTS & SYSTEM tabs (incremental chronologic rendering) --
        all_events = getattr(job, "peek_events", lambda: [])()
        sync_count = self._events_sync_count

        if len(all_events) > sync_count:
            new_events = all_events[sync_count:]
            ev_log = self.query_one("#events-log", RichLog)
            sys_log = self.query_one("#system-job-log", RichLog)

            ev_msgs = []
            sys_msgs = []
            for ev in new_events:
                source = ev.get("source")
                tag = _escape_markup(str(ev.get("tag", "")))
                msg = _escape_markup(str(ev.get("message", "")))
                time_str = _escape_markup(str(ev.get("time", "?")))

                if source == "user":
                    ev_msgs.append(f"\\[{time_str}] [bold yellow]{tag}[/bold yellow] - {msg}")
                else:
                    sys_msgs.append(f"[grey50]{time_str}[/grey50] [bold]{tag}[/bold] - {msg}")

            if ev_msgs:
                ev_log.auto_scroll = (ev_log.scroll_y >= max(0, ev_log.max_scroll_y - 2))
                ev_log.write("\n".join(ev_msgs))
            if sys_msgs:
                sys_log.auto_scroll = (sys_log.scroll_y >= max(0, sys_log.max_scroll_y - 2))
                sys_log.write("\n".join(sys_msgs))

            self._events_sync_count = len(all_events)

    def _focus_active_panel(self) -> None:
        """Route keyboard focus to the primary widget of the active tab."""
        active = self.query_one("#main-tabs", TabbedContent).active
        if active == "tab-dashboard":
            self.query_one("#jobs-table").focus()
        elif active == "tab-detail":
            self.query_one("#log-view").focus()
        elif active == "tab-system":
            self.query_one("#session-log").focus()
        elif active == "tab-help":
            self.query_one("#help-text").focus()

    # ------------------------------------------------------------------
    # Textual event handlers
    # ------------------------------------------------------------------

    def on_tabbed_content_tab_activated(self, event) -> None:
        """When a tab is selected (via mouse or F-keys), route focus to its main widget."""
        self._focus_active_panel()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Track which Dashboard row the cursor is on for preview and commands."""
        if getattr(event.control, "id", None) != "jobs-table":
            return
        self._highlighted_job_key = event.row_key.value if event.row_key else None
        if self._highlighted_job_key is not None:
            self._refresh_dashboard_preview(reset=True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a DASHBOARD row → open JOB DETAIL for that job."""
        if getattr(event.control, "id", None) != "jobs-table":
            return
        row_key_val = event.row_key.value  # the key passed to add_row()
        job = self._job_for_row_key(row_key_val)
        if job is not None:
            self._open_detail_for(job)
            return

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Dispatch the REPL command from the bottom command bar."""
        # Import here to avoid circular imports at module level.
        from rpkbin.wave.runner import _handle_cmd, _parse_repl_line  # noqa: PLC0415

        line = event.value.strip()
        cmd_input = self.query_one("#cmd-input", CommandInput)
        cmd_input.clear()
        if not line:
            return
        cmd_input.record(line)

        parts = _parse_repl_line(line)
        if parts is None:
            self.notify(
                "Command parse error - check syntax (e.g. unmatched quotes).",
                severity="error",
            )
            return

        verb = parts[0].lower()

        # -- TUI-specific routing: commands that have dedicated views ----
        if verb == "help":
            self.query_one("#main-tabs", TabbedContent).active = "tab-help"
            return

        if verb == "watch":
            self.notify("'watch' is not needed in TUI mode - the Dashboard auto-refreshes.", severity="warning")
            return

        if verb in ("logs", "show", "data", "events") and len(parts) >= 2:
            from rpkbin.wave.runner import _find_job  # noqa: PLC0415

            job = _find_job(parts[1], self._session, quiet=True)
            if job is not None:
                self._open_detail_for(job)
                if verb == "data":
                    self.query_one("#detail-tabs", TabbedContent).active = "detail-data"
                elif verb == "events":
                    self.query_one("#detail-tabs", TabbedContent).active = "detail-events"
                elif verb == "show":
                    self.query_one("#detail-tabs", TabbedContent).active = "detail-info"
                return
            # Job not found — fall through to _handle_cmd for error message

        # -- Default path: run command, capture output to SYSTEM LOG ----
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            action = _handle_cmd(parts, self._session)

        output = buf.getvalue()
        if output:
            session_log = self.query_one("#session-log", RichLog)
            session_log.write(f"[dim]$ {_escape_markup(line)}[/dim]")
            for out_line in output.splitlines():
                session_log.write(_escape_markup(out_line))
            # Known operational commands (stop, skip, input, signal) produce a
            # short confirmation message - show a Toast so the user stays put.
            # Everything else (display commands, unknown verbs) jumps to System
            # Log so the user can actually read the output or error message.
            if verb in _OPERATION_CMDS:
                first_line = output.strip().splitlines()[0] if output.strip() else "Done."
                if first_line.startswith("[Wave] "):
                    first_line = first_line[7:]
                self.notify(first_line, timeout=3)
            else:
                self.query_one("#main-tabs", TabbedContent).active = "tab-system"
                self.query_one("#session-log").focus()

        if action == "exit":
            self.exit()

    # ------------------------------------------------------------------
    # Bound actions
    # ------------------------------------------------------------------

    def action_request_quit(self) -> None:
        """Quit safely: warn if jobs are still active, allow double-tap force quit."""
        from rpkbin.wave.runner import _active_jobs  # noqa: PLC0415

        active = _active_jobs(self._session)
        if not active:
            self.exit()
            return

        now = time.time()
        if self._last_ctrl_c > now - 3.0:
            # Double-tap confirmed: force shutdown
            sys_log = self.query_one("#session-log", RichLog)
            sys_log.write("[bold red]Force quit triggered. Canceling active jobs...[/bold red]")
            for job in active:
                if hasattr(job, "cancel"):
                    job.cancel()
            self.exit()
        else:
            self._last_ctrl_c = now
            log = self.query_one("#session-log", RichLog)
            log.write(
                "[bold yellow]Jobs are still active![/bold yellow] "
                "Press [bold cyan]Ctrl+C[/bold cyan] again within 3 seconds to force quit, "
                "or wait for completion."
            )
            self.notify(
                "Jobs still active!  Press Ctrl+C again within 3 s to force quit.",
                severity="warning",
                timeout=4,
            )

    def action_goto_tab(self, tab_id: str) -> None:
        """Switch the top-level TabbedContent to *tab_id*."""
        self.query_one("#main-tabs", TabbedContent).active = tab_id

    def action_focus_cmd(self) -> None:
        """Focus the REPL command bar (Vim style ':')."""
        self.query_one("#cmd-input", CommandInput).focus()

    def action_unfocus_cmd(self) -> None:
        """Leave command bar and focus the active panel's main widget."""
        self._focus_active_panel()

    def action_previous_detail_job(self) -> None:
        """Open the previous job in JOB DETAIL."""
        self._navigate_detail_job(-1)

    def action_next_detail_job(self) -> None:
        """Open the next job in JOB DETAIL."""
        self._navigate_detail_job(1)

    def action_previous_running_detail_job(self) -> None:
        """Open the previous running job in JOB DETAIL."""
        self._navigate_detail_job(-1, status="running")

    def action_next_running_detail_job(self) -> None:
        """Open the next running job in JOB DETAIL."""
        self._navigate_detail_job(1, status="running")
