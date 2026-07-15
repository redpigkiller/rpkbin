"""app.py - WaveApp: Textual TUI for monitoring and controlling Wave sessions.

Layout
------
Three top-level tabs:
  DASHBOARD   - live DataTable of all jobs
  JOB DETAIL  - log view (left 60%) + Data/Events/System tabs (right 40%)
  SESSION LOG - session-level events

Bottom command bar reuses runner._parse_repl_line + runner._handle_cmd verbatim.
stdout from those helpers is redirected into the SESSION LOG tab via
contextlib.redirect_stdout so the terminal stays clean.

Threading
---------
All callbacks from worker threads (_on_job_updated, _on_job_added) are
forwarded to the Textual main thread via self.call_from_thread().

Detail panel refresh strategy
------------------------------
_on_job_updated  -> updates Dashboard row cells only (status, progress, etc.)
_tick (1s)       -> drives Dashboard rows, DATA / EVENTS panel sync, session
                   events, and elapsed refreshes
visible log tick -> syncs only the log widgets currently visible to the user

This separation prevents the detail panel from being flooded with full buffer
copies on every parser callback, which was the main source of UI lag.
"""

from __future__ import annotations

import contextlib
import io
import logging
import threading
import time
from typing import TYPE_CHECKING

from rich.markup import escape as _escape_markup

from rpkbin.wave._util import job_exit_code as _job_exit_code
from rpkbin.wave.tui.commands import (
    CommandInput,
    _HELP_TEXT,
    _KEY_COMPLETIONS,
    _SIGNAL_COMPLETIONS,
    _WAVE_COMMANDS,
    _WAVE_JOB_COMMANDS,
    _expand_dot_in_parts,
)
from rpkbin.wave.tui.formatting import (
    _STATUS_COLOR,
    _fmt_elapsed,
    _format_system_event_line,
    _job_elapsed_s,
)
from rpkbin.wave.tui.refresh import (
    _LogViewAdapter,
    sync_job_log as _sync_job_log_model,
    tail_sync_start as _tail_sync_start_model,
)
from rpkbin.wave.tui.view_models import (
    build_info_text as _build_info_text_model,
    build_row_cells as _build_row_cells_model,
    command_identifier_for_job as _command_identifier_for_job_model,
    dashboard_column_labels as _dashboard_column_labels_model,
    resolve_dashboard_columns as _resolve_dashboard_columns_model,
)
from rpkbin.wave.runner import _handle_cmd, _parse_repl_line, _active_jobs, _find_job

from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Log,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rpkbin.wave.session import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Operational commands produce a brief confirmation message; the user stays on
# the current tab and sees a Toast.  All other commands (display-oriented like
# 'logs', 'status', or unknown verbs whose error we want the user to read)
# fall through to Session Log so the output is clearly visible.
_OPERATION_CMDS: frozenset[str] = frozenset({"pause", "resume", "stop", "skip", "cancel", "rerun", "action", "session_action", "send-line", "send-key", "send-signal"})

_DATA_EMPTY_ROW_KEY = "__wave_empty_data__"
_DASHBOARD_PREVIEW_TAIL_LINES = 1000
_DETAIL_INITIAL_TAIL_LINES = 5000
_DIRTY_FLUSH_DELAY_S = 0.05


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

#terminal-container { height: 1fr; }
#terminal-view { height: 1fr; }
#terminal-hint {
    height: auto;
    padding: 0 1;
    background: $surface;
}

#help-text {
    height: auto;
    padding: 1 2;
}

#info-panel {
    height: 1fr;
    padding: 1 2;
}

#job-input-bar {
    dock: bottom;
    border-top: solid $primary-darken-2;
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
        Binding("f3", "goto_tab('tab-system')", "Session Log", show=True),
        Binding("f4", "goto_tab('tab-help')", "Help", show=True),
        # F8 retired: job input bar (i key) replaces the old pre-fill shortcut.
        Binding("f9", "terminal_send_ctrl_c", "Send Ctrl-C", show=False),
        Binding("f10", "terminal_send_ctrl_d", "Send Ctrl-D", show=False),
        Binding("i", "focus_job_input", "Job Input", show=False),
        Binding("1", "detail_panel('detail-info')", "INFO tab", show=False),
        Binding("2", "detail_panel('detail-data')", "DATA tab", show=False),
        Binding("3", "detail_panel('detail-events')", "EVENTS tab", show=False),
        Binding("4", "detail_panel('detail-system')", "ERRORS tab", show=False),
        Binding(":", "focus_cmd", "Command", show=False),
        Binding("escape", "unfocus_input", "Back", show=False),
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
        self._jobs_by_row_key: dict[str, object] = {}
        self._row_numbers: dict[str, int] = {}
        # Row cache to prevent needless DataTable cell updates
        self._row_cache: dict[str, tuple] = {}
        self._row_status_cache: dict[str, str] = {}
        # data-table row keys and cache for the currently shown job
        self._data_row_keys: set[str] = set()
        self._data_cache: dict[str, str] = {}

        # Column key tuples returned by DataTable.add_columns()
        self._jobs_col_keys: tuple = ()
        self._data_col_keys: tuple = ()
        self._dashboard_columns = self._resolve_dashboard_columns()
        self._tui_config = self._read_tui_config()

        # Incremental sync counters (avoid re-painting unchanged content).
        # All three are declared here to ensure they are always defined before
        # any callback path can read them.
        self._log_total_sync_count: int = 0
        self._detail_job_needs_scroll: bool = False  # force scroll on first refresh after job switch
        self._session_event_count: int = 0
        self._events_sync_count: int = 0

        self._quitting: bool = False

        # Double-tap quit tracking
        self._last_ctrl_c: float = 0.0

        # Job row key currently highlighted in the Dashboard table (for quick-stop).
        self._highlighted_job_key: str | None = None

        # Dashboard log preview tracks the highlighted row independently of
        # JOB DETAIL so F1 can be used for quick scan-and-read workflows.
        self._dashboard_preview_job_key: str | None = None
        self._dashboard_preview_log_count: int = 0

        # Worker-thread notifications are coalesced before touching Textual.
        # This bounds call_from_thread pressure when parsers emit frequently.
        self._dirty_lock = threading.Lock()
        self._dirty_jobs: dict[str, object] = {}
        self._dirty_flush_pending: bool = False

        # Worker count is static for a Wave TUI run. Cache it so the subtitle
        # path does not call the heavier Session.stats() snapshot every second.
        self._worker_total_label: str | None = None
        self._perf_summary_written: bool = False

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
                        yield Log(
                            id="dashboard-log",
                            highlight=False,
                            max_lines=self._tui_config["dashboard_log_max_lines"],
                        )

            # -- Tab 2: JOB DETAIL -------------------------------------
            with TabPane("JOB DETAIL", id="tab-detail"):
                with Vertical(id="detail-root"):
                    yield Static("", id="detail-header", markup=True)
                    with Horizontal(id="detail-container"):
                        with Vertical(id="left-panel"):
                            yield Log(
                                id="log-view",
                                highlight=False,
                                max_lines=self._tui_config["detail_log_max_lines"],
                            )
                            # Job Input Bar: docked at bottom of left panel.
                            # Press 'i' to focus, Enter to send, Esc to return.
                            yield Input(
                                placeholder="No job selected",
                                id="job-input-bar",
                                disabled=True,
            )

                        with Vertical(id="right-panel"):
                            with TabbedContent(id="detail-tabs"):
                                with TabPane("INFO", id="detail-info"):
                                    yield Static("", id="info-panel", markup=True)
                                with TabPane("DATA", id="detail-data"):
                                    yield DataTable(id="data-table", cursor_type="row")
                                with TabPane("EVENTS", id="detail-events"):
                                    yield RichLog(
                                        id="events-log",
                                        markup=True,
                                        max_lines=self._tui_config["event_log_max_lines"],
                                    )
                                with TabPane("ERRORS", id="detail-system"):
                                    yield RichLog(
                                        id="system-job-log",
                                        markup=True,
                                        max_lines=self._tui_config["event_log_max_lines"],
                                    )

            # -- Tab 3: SESSION LOG (session-level events + command output)
            with TabPane("SESSION LOG", id="tab-system"):
                yield RichLog(
                    id="session-log",
                    markup=True,
                    max_lines=self._tui_config["session_log_max_lines"],
                )

            # -- Tab 4: HELP -------------------------------------------
            with TabPane("HELP", id="tab-help"):
                with VerticalScroll(id="help-scroll"):
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
        self._jobs_col_keys = t.add_columns(*self._dashboard_column_labels())

        # DATA table columns (rows are added lazily per-job)
        dt = self.query_one("#data-table", DataTable)
        self._data_col_keys = dt.add_columns("Key", "Value")

        # Populate rows for jobs already registered before _start().
        # Defer subtitle calculation until the initial table is complete.
        initial_jobs = self._session.jobs()
        for job in initial_jobs:
            self._add_job_row_on_main(job, update_subtitle=False)

        # Initial hint for JOB DETAIL if they switch without selecting
        self._update_detail_header(None)
        self._plain_log("#log-view").write(
            "No job selected.\n"
            "Press F1 to go to the Dashboard, "
            "then highlight a job and press Enter to open it here.\n"
            "Inside Job Detail, use [ ] for jobs and { } for running jobs."
        )

        # Periodic timer: elapsed refresh + detail panel sync + session-event drain
        self.set_interval(self._tui_config["tick_interval"], self._tick)
        # Visible logs get a faster path so output feels live without making
        # all detail/dashboard bookkeeping run at log-line frequency.
        self.set_interval(self._tui_config["visible_log_interval"], self._refresh_visible_logs)

        self._update_subtitle(jobs=initial_jobs)
        self.query_one("#jobs-table", DataTable).focus()

        self._plain_log("#dashboard-log").write(
            "No job selected.\n"
            "Highlight a job in the Dashboard to preview its log here."
        )

    def _plain_log(self, selector: str) -> _LogViewAdapter:
        """Return a thin adapter for a plain-text log widget."""
        return _LogViewAdapter(self.query_one(selector))

    def _read_tui_config(self) -> dict[str, int | float]:
        """Read numeric TUI config with safe defaults."""
        defaults: dict[str, int | float] = {
            "tick_interval": 1.0,
            "visible_log_interval": 0.2,
            "dashboard_log_max_lines": 2000,
            "detail_log_max_lines": 5000,
            "session_log_max_lines": 5000,
            "event_log_max_lines": 2000,
            "dashboard_preview_tail_lines": _DASHBOARD_PREVIEW_TAIL_LINES,
            "detail_initial_tail_lines": _DETAIL_INITIAL_TAIL_LINES,
        }
        try:
            raw = getattr(self._session, "tui_config", lambda: {})()
        except Exception:
            logger.warning("Failed to read Wave TUI config; using numeric defaults.", exc_info=True)
            return defaults
        config = dict(defaults)
        for key, default in defaults.items():
            value = raw.get(key, default)
            try:
                numeric = float(value) if key.endswith("_interval") else int(value)
            except (TypeError, ValueError):
                logger.warning("Invalid TUI config %s=%r; using default %r.", key, value, default)
                continue
            if numeric <= 0:
                logger.warning("Invalid TUI config %s=%r; using default %r.", key, value, default)
                continue
            config[key] = numeric
        return config

    # ------------------------------------------------------------------
    # Worker-thread callbacks (called by Session via tui_notify /
    # tui_job_added - must not touch widgets directly)
    # ------------------------------------------------------------------

    def _on_job_updated(self, job) -> None:
        """Called from a worker thread when a job's observable state changes.

        Worker threads only mark jobs dirty and schedule one main-thread flush
        per burst. This avoids flooding Textual's asyncio queue when parsers or
        hooks update parsed data at log-line frequency.
        """
        key = self._job_row_key(job)
        should_schedule = False
        with self._dirty_lock:
            self._dirty_jobs[key] = job
            if not self._dirty_flush_pending:
                self._dirty_flush_pending = True
                should_schedule = True
        if not should_schedule:
            return
        try:
            self.call_from_thread(self._schedule_dirty_flush_on_main)
        except RuntimeError:
            with self._dirty_lock:
                self._dirty_flush_pending = False
            logger.debug("Dropped TUI job update while the event loop was closing.", exc_info=True)
        except Exception:
            with self._dirty_lock:
                self._dirty_flush_pending = False
            logger.exception("Unexpected failure scheduling TUI job update for %r.", getattr(job, "name", job))

    def _on_job_added(self, job) -> None:
        """Called from a worker thread when a new job is dynamically added."""
        try:
            self.call_from_thread(self._add_job_row_on_main, job)
        except RuntimeError:
            logger.debug("Dropped TUI job-add update while the event loop was closing.", exc_info=True)
        except Exception:
            logger.exception("Unexpected failure scheduling TUI job-add update for %r.", getattr(job, "name", job))

    def _schedule_dirty_flush_on_main(self) -> None:
        """Debounce dirty job row updates on the Textual main thread."""
        self.set_timer(_DIRTY_FLUSH_DELAY_S, self._flush_dirty_job_rows)

    def _flush_dirty_job_rows(self) -> None:
        """Refresh all coalesced dirty rows on the Textual main thread."""
        with self._dirty_lock:
            dirty_jobs = list(self._dirty_jobs.values())
            self._dirty_jobs.clear()
            self._dirty_flush_pending = False

        if dirty_jobs:
            self._session.perf_record_tui_dirty_flush(len(dirty_jobs))

        for job in dirty_jobs:
            self._refresh_job_row(job, update_subtitle=False)
        if dirty_jobs:
            self._update_subtitle()

        with self._dirty_lock:
            needs_another_flush = bool(self._dirty_jobs) and not self._dirty_flush_pending
            if needs_another_flush:
                self._dirty_flush_pending = True
        if needs_another_flush:
            self._schedule_dirty_flush_on_main()

    # ------------------------------------------------------------------
    # DASHBOARD helpers (main-thread only)
    # ------------------------------------------------------------------

    def _build_row_cells(self, job) -> tuple[str, ...]:
        """Return one cell value per configured dashboard column."""
        return _build_row_cells_model(
            job,
            self._dashboard_columns,
            logger,
            row_number=self._dashboard_row_number(job),
        )

    def _dashboard_row_number(self, job) -> int | None:
        """Return the current 1-based display index for *job*."""
        key = self._job_row_key(job)
        cached = self._row_numbers.get(key)
        if cached is not None:
            return cached
        for idx, candidate in enumerate(self._session.jobs(), start=1):
            if self._job_row_key(candidate) == key:
                return idx
        return None

    def _resolve_dashboard_columns(self) -> tuple[dict[str, str], ...]:
        return _resolve_dashboard_columns_model(self._session, logger)

    def _dashboard_column_labels(self) -> list[str]:
        return _dashboard_column_labels_model(self._dashboard_columns)

    def _job_row_key(self, job) -> str:
        """Return the stable DataTable row key for *job*."""
        return str(getattr(job, "id", job.name))

    def _job_for_row_key(self, row_key: str):
        """Resolve a DataTable row key back to its job."""
        return self._jobs_by_row_key.get(row_key)

    def _command_identifier_for_job(self, job) -> str:
        """Prefer name for unique jobs; use id when duplicate names exist."""
        return _command_identifier_for_job_model(job, self._session.jobs())

    def _build_info_text(self, job) -> str:
        """Build Rich markup text for the INFO metadata panel in Job Detail."""
        return _build_info_text_model(job)

    def _add_job_row_on_main(
        self,
        job,
        *,
        update_subtitle: bool = True,
    ) -> None:
        """Add a fresh row for *job* to the DASHBOARD table (main thread)."""
        key = self._job_row_key(job)
        if key in self._row_keys:
            return  # already present
        self._row_keys.add(key)
        self._jobs_by_row_key[key] = job
        self._row_numbers[key] = len(self._row_numbers) + 1

        cells = self._build_row_cells(job)
        self._row_cache[key] = cells
        self._row_status_cache[key] = getattr(job, "status", "pending")

        t = self.query_one("#jobs-table", DataTable)
        t.add_row(*cells, key=key)

        if update_subtitle:
            self._update_subtitle()

    def _refresh_job_row(
        self,
        job,
        *,
        update_subtitle: bool = True,
    ) -> None:
        """Update a job's DASHBOARD row cells."""
        key = self._job_row_key(job)
        if key not in self._row_keys:
            self._add_job_row_on_main(job, update_subtitle=update_subtitle)
            return

        cells = self._build_row_cells(job)
        self._row_status_cache[key] = getattr(job, "status", "pending")
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

        if update_subtitle:
            self._update_subtitle()

    # ------------------------------------------------------------------
    # Periodic tick (main thread via set_interval)
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Every second: refresh elapsed + detail panels + drain session events."""
        if getattr(self, "_quitting", False) and not _active_jobs(self._session):
            self.exit()
            return
        try:
            perf_enabled = getattr(self._session, "perf_enabled", False)
            tick_t0 = time.perf_counter() if perf_enabled else None
            jobs = self._session.jobs()
            self._refresh_dashboard_rows(jobs)
            self._refresh_detail_if_visible()
            self._refresh_dashboard_preview_if_visible()
            self._update_subtitle(jobs=jobs)
            self._drain_session_events()
            self._write_perf_summary_if_finished(perf_enabled)
        except NoMatches:
            logger.debug("Skipped tick refresh while TUI widgets were unavailable.", exc_info=True)
        finally:
            if tick_t0 is not None:
                self._session.perf_record_tui_tick(time.perf_counter() - tick_t0)

    def _refresh_dashboard_rows(self, jobs: list) -> None:
        """Refresh dashboard rows whose status or elapsed display may have changed."""
        for job in jobs:
            key = self._job_row_key(job)
            status = getattr(job, "status", "pending")
            if status == "running" or self._row_status_cache.get(key) != status:
                self._refresh_job_row(job, update_subtitle=False)

    def _refresh_detail_if_visible(self) -> None:
        """Refresh the selected detail job only when JOB DETAIL is visible."""
        if self._detail_job is not None and self._is_detail_tab_active():
            self._refresh_right_panels(self._detail_job)

    def _refresh_dashboard_preview_if_visible(self) -> None:
        """Refresh the dashboard preview only while the Dashboard tab is visible."""
        if self._is_dashboard_tab_active():
            self._refresh_dashboard_preview()

    def _refresh_visible_logs(self) -> None:
        """Refresh only the log widgets currently visible to the user."""
        try:
            if self._is_dashboard_tab_active():
                self._refresh_dashboard_preview()

            if self._detail_job is None or not self._is_detail_tab_active():
                return

            self._refresh_detail_log(self._detail_job)
        except NoMatches:
            logger.debug("Skipped visible log refresh while TUI widgets were unavailable.", exc_info=True)

    def _drain_session_events(self) -> None:
        """Append new session-level events to the SESSION LOG tab."""
        events = self._session.peek_events()
        new_events = events[self._session_event_count:]
        if not new_events:
            return

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
        # Single decision point: snapshot before write, scroll_end after if needed.
        _at_bottom = log.scroll_y >= max(0, log.max_scroll_y - 5)
        log.write("\n".join(buf))
        if _at_bottom:
            log.scroll_end(animate=False)
        self._session_event_count = len(events)

    def _write_perf_summary_if_finished(self, perf_enabled: bool) -> None:
        """Write the one-shot perf summary after the session leaves running state."""
        if not perf_enabled or self._perf_summary_written:
            return
        if self._session.summary().get("outcome") == "running":
            return
        summary = self._session.perf_summary()
        if not summary:
            return
        log = self.query_one("#session-log", RichLog)
        for line in summary.splitlines():
            log.write(_escape_markup(line))
        self._perf_summary_written = True

    def _refresh_dashboard_preview(self, job=None, *, reset: bool = False) -> None:
        """Append log output for the Dashboard-highlighted job."""
        if job is None:
            if self._highlighted_job_key is None:
                return
            job = self._job_for_row_key(self._highlighted_job_key)
        if job is None:
            return

        key = self._job_row_key(job)
        log_view = self._plain_log("#dashboard-log")

        if reset or self._dashboard_preview_job_key != key:
            self._dashboard_preview_job_key = key
            self._dashboard_preview_log_count = self._tail_sync_start(
                job,
                int(self._tui_config["dashboard_preview_tail_lines"]),
            )
            log_view.clear()
            log_view.write(f"{job.name}  [{key[:8]}]")

        self._dashboard_preview_log_count = self._sync_job_log(
            job,
            log_view,
            self._dashboard_preview_log_count,
            empty_message="No log output yet." if reset else None,
            force_scroll=reset,
        )

    def _sync_job_log(
        self,
        job,
        log_view: _LogViewAdapter,
        sync_count: int,
        *,
        empty_message: str | None = None,
        force_scroll: bool = False,
    ) -> int:
        """Append newly emitted job log lines to *log_view* and return sync count."""
        return _sync_job_log_model(
            job,
            log_view,
            sync_count,
            empty_message=empty_message,
            record_log_append=self._session.perf_record_tui_log_append,
            force_scroll=force_scroll,
        )

    def _tail_sync_start(self, job, limit: int) -> int:
        """Return the sync counter that starts at the last *limit* lines."""
        return _tail_sync_start_model(job, limit)

    def _update_subtitle(self, *, jobs: list | None = None) -> None:
        if self._row_status_cache:
            statuses = self._row_status_cache.values()
            n_run  = sum(1 for status in statuses if status == "running")
            n_done = sum(1 for status in statuses if status == "done")
            n_pend = sum(1 for status in statuses if status == "pending")
            n_fail = sum(1 for status in statuses if status in ("failed", "cancelled"))
        else:
            jobs = self._session.jobs() if jobs is None else jobs
            n_run  = sum(1 for j in jobs if getattr(j, "status", None) == "running")
            n_done = sum(1 for j in jobs if getattr(j, "status", None) == "done")
            n_pend = sum(1 for j in jobs if getattr(j, "status", None) == "pending")
            n_fail = sum(1 for j in jobs if getattr(j, "status", None) in ("failed", "cancelled"))
        if self._worker_total_label is None:
            s = self._session.stats()
            self._worker_total_label = str(s.get("workers", {}).get("total", "?")) if s else "?"
        mw = self._worker_total_label
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
        if job is not None:
            self._sync_dashboard_selection(job)
        # Reset all incremental sync counters for the new job
        self._log_total_sync_count = (
            self._tail_sync_start(job, int(self._tui_config["detail_initial_tail_lines"]))
            if job is not None
            else 0
        )
        self._events_sync_count = 0
        self._data_row_keys = set()
        self._data_cache = {}

        log_view = self._plain_log("#log-view")
        log_view.clear()
        self._detail_job_needs_scroll = True  # scroll to bottom on the first log refresh
        if job is None:
            log_view.write("No job selected. Go to Dashboard (F1) and press Enter on a job to view details.")

        self.query_one("#events-log", RichLog).clear()
        self.query_one("#system-job-log", RichLog).clear()
        self.query_one("#data-table", DataTable).clear()
        job_input = self.query_one("#job-input-bar", Input)
        job_input.clear()
        self._update_detail_header(job)

        if job is not None:
            self._refresh_right_panels(job, force=True)
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
        """Return True only when detail navigation keys should be active.

        Navigation keys ([ ] { } 1-4 i) are suppressed when any input widget
        has focus, to prevent them from interfering with text entry.
        """
        if self.query_one("#main-tabs", TabbedContent).active != "tab-detail":
            return False
        focused = self.focused
        focused_id = getattr(focused, "id", None)
        return focused_id not in ("cmd-input", "job-input-bar")

    def _update_detail_header(self, job) -> None:
        """Refresh the one-line JOB DETAIL location/status header."""
        header = self.query_one("#detail-header", Static)
        job_input = self.query_one("#job-input-bar", Input)
        if job is None:
            header.update("[dim]No job selected. F1 -> choose a job -> Enter.  Jobs: [ ]  Running: { }[/dim]")
            job_input.disabled = True
            job_input.placeholder = "No job selected"
            return

        jobs = self._session.jobs()
        idx = self._detail_job_index()
        position = f"{idx + 1}/{len(jobs)}" if idx is not None and jobs else "?/?"
        status = getattr(job, "status", "pending")
        can_input = status == "running" and hasattr(job, "send_input")
        job_input.disabled = not can_input
        if can_input:
            raw_name = str(getattr(job, "name", ""))
            truncated = (raw_name[:13] + "\u2026") if len(raw_name) > 15 else raw_name
            job_input.placeholder = f"[{truncated}] > "
        elif not hasattr(job, "send_input"):
            job_input.placeholder = "Input unavailable for this job"
        else:
            job_input.placeholder = f"Input unavailable while {status}"
        color = _STATUS_COLOR.get(status, "white")
        elapsed = _fmt_elapsed(_job_elapsed_s(job)) or "--:--:--"
        prog = getattr(job, "progress", None)
        progress = f"{prog:.0f}%" if isinstance(prog, (int, float)) else "--"
        name = _escape_markup(str(getattr(job, "name", job)))
        exit_code = _job_exit_code(job)
        exit_part = ""
        if exit_code is not None:
            exit_color = "cyan" if exit_code == 0 else "red"
            exit_part = f"  [dim]exit=[/dim][{exit_color}]{exit_code}[/{exit_color}]"

        header.update(
            f"[dim]Jobs: [ ][/dim]  [bold]{name}[/bold]  "
            f"[dim]{position}[/dim]  [{color}]{status.upper()}[/{color}]  "
            f"[dim]{elapsed}[/dim]  [cyan]{progress}[/cyan]{exit_part}  [dim]Running: {{ }}[/dim]"
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

    def _active_detail_panel_id(self) -> str:
        """Return the active right-side detail panel id."""
        try:
            return self.query_one("#detail-tabs", TabbedContent).active
        except NoMatches:
            return "detail-info"

    def _refresh_right_panels(self, job, *, force: bool = False) -> None:
        """Refresh the left log and only the active right-side detail panel."""
        self._update_detail_header(job)

        self._refresh_detail_log(job)
        self._refresh_active_detail_panel(job, force=force)

    def _refresh_detail_log(self, job) -> None:
        """Append new log lines for the selected JOB DETAIL job."""
        log_view = self._plain_log("#log-view")
        force = self._detail_job_needs_scroll
        self._detail_job_needs_scroll = False
        self._log_total_sync_count = self._sync_job_log(
            job,
            log_view,
            self._log_total_sync_count,
            force_scroll=force,
        )

    def _refresh_active_detail_panel(self, job, *, force: bool = False) -> None:
        """Refresh only the active right-side JOB DETAIL sub-tab."""
        active_panel = self._active_detail_panel_id()
        if active_panel == "detail-info":
            self.query_one("#info-panel", Static).update(self._build_info_text(job))
            return
        if active_panel == "detail-data":
            self._refresh_data_panel(job)
            return
        if active_panel == "detail-events":
            self._refresh_events_panels(job)
            return
        elif active_panel == "detail-system":
            self._refresh_events_panels(job)

    def _refresh_events_panels(self, job) -> None:
        """Incrementally refresh the EVENTS and ERRORS sub-tabs."""
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
                    sys_msgs.append(_format_system_event_line(ev))

            if ev_msgs:
                _ev_bottom = ev_log.scroll_y >= max(0, ev_log.max_scroll_y - 5)
                ev_log.auto_scroll = _ev_bottom
                ev_log.write("\n".join(ev_msgs))
            if sys_msgs:
                _sys_bottom = sys_log.scroll_y >= max(0, sys_log.max_scroll_y - 5)
                sys_log.write("\n".join(sys_msgs))
                if _sys_bottom:
                    sys_log.scroll_end(animate=False)

            self._events_sync_count = len(all_events)

    def _refresh_data_panel(self, job) -> None:
        """Refresh the DATA sub-tab, including a clear empty state."""
        data = getattr(job, "peek_data", lambda: {})()
        dt = self.query_one("#data-table", DataTable)

        if not data:
            if _DATA_EMPTY_ROW_KEY not in self._data_row_keys:
                dt.add_row("[dim](no parsed data)[/dim]", "", key=_DATA_EMPTY_ROW_KEY)
                self._data_row_keys.add(_DATA_EMPTY_ROW_KEY)
            return

        if _DATA_EMPTY_ROW_KEY in self._data_row_keys:
            dt.clear()
            self._data_row_keys = set()
            self._data_cache = {}

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

    def _focus_log_view_if_available(self) -> None:
        """Focus the detail log view when the Textual widget tree is ready."""
        try:
            self.query_one("#log-view").focus()
        except (NoMatches, ScreenStackError):
            logger.debug("Skipped log-view focus because the widget tree is unavailable.", exc_info=True)

    def _is_main_tab_active(self, tab_id: str) -> bool:
        """Return True when the requested top-level tab is visible."""
        return self.query_one("#main-tabs", TabbedContent).active == tab_id

    def _is_dashboard_tab_active(self) -> bool:
        """Return True when the top-level DASHBOARD tab is visible."""
        return self._is_main_tab_active("tab-dashboard")

    def _is_detail_tab_active(self) -> bool:
        """Return True when the top-level JOB DETAIL tab is visible."""
        return self._is_main_tab_active("tab-detail")

    # ------------------------------------------------------------------
    # Textual event handlers
    # ------------------------------------------------------------------

    def on_key(self, event) -> None:
        """Intercept Tab in Job Detail content area to prevent focus cycling."""
        if event.key == "tab":
            focused_id = getattr(self.focused, "id", None)
            if self._is_detail_navigation_context():
                event.stop()
                event.prevent_default()
                self._focus_log_view_if_available()
            elif self._is_dashboard_tab_active() and focused_id != "cmd-input":
                event.stop()
                event.prevent_default()
                self.query_one("#jobs-table").focus()
            elif focused_id == "job-input-bar":
                event.stop()
                event.prevent_default()

    def on_tabbed_content_tab_activated(self, event) -> None:
        """When a tab is selected (via mouse or F-keys), route focus to its main widget."""
        self._focus_active_panel()
        if self._detail_job is None:
            return
        control_id = getattr(getattr(event, "control", None), "id", None)
        if control_id == "detail-tabs":
            self._refresh_active_detail_panel(self._detail_job, force=True)
            self._focus_log_view_if_available()
        elif self._is_detail_tab_active():
            self._refresh_right_panels(self._detail_job, force=True)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Track which Dashboard row the cursor is on for preview and commands."""
        if getattr(event.control, "id", None) != "jobs-table":
            return
        self._highlighted_job_key = event.row_key.value if event.row_key else None
        if self._highlighted_job_key is not None:
            self._refresh_dashboard_preview(reset=True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a DASHBOARD row -> open JOB DETAIL for that job."""
        if getattr(event.control, "id", None) != "jobs-table":
            return
        row_key_val = event.row_key.value  # the key passed to add_row()
        job = self._job_for_row_key(row_key_val)
        if job is not None:
            self._open_detail_for(job)
            return

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Dispatch the submitted input from either the command bar or the Job Input Bar."""
        # --- Job Input Bar: send text directly to the current job's stdin ---
        if getattr(event.control, "id", None) == "job-input-bar":
            job_input = self.query_one("#job-input-bar", Input)
            text = event.value
            job_input.clear()
            if not text or self._detail_job is None:
                return
            job = self._detail_job
            if not hasattr(job, "send_input"):
                self.notify("This job does not accept stdin input.", severity="warning", timeout=3)
                return
            # Always append newline (like pressing Enter in a real terminal).
            if not text.endswith("\n"):
                text += "\n"
            try:
                job.send_input(text)
            except RuntimeError as exc:
                self.notify(f"Send failed: {exc}", severity="error", timeout=4)
            return

        # --- Command bar (existing path) ---
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

        # --- TUI-only: expand '.' to the current JOB DETAIL job id ---
        detail_job_id = (
            str(self._detail_job.id)
            if self._detail_job is not None and self._is_detail_tab_active() and hasattr(self._detail_job, "id")
            else None
        )
        parts, dot_error = _expand_dot_in_parts(parts, detail_job_id)
        if dot_error is not None:
            # '.' was used but no job is open in JOB DETAIL
            session_log = self.query_one("#session-log", RichLog)
            session_log.write(f"[dim]$ {_escape_markup(line)}[/dim]")
            session_log.write(_escape_markup(dot_error))
            self.notify(dot_error.replace("[Wave] ", ""), severity="error", timeout=4)
            self.query_one("#main-tabs", TabbedContent).active = "tab-system"
            return

        verb = parts[0].lower()

        # -- TUI-specific routing: commands that have dedicated views ----
        if verb == "help":
            self.query_one("#main-tabs", TabbedContent).active = "tab-help"
            return

        if verb == "watch":
            self.notify("'watch' is not needed in TUI mode - the Dashboard auto-refreshes.", severity="warning")
            return

        if verb == "rerun" and len(parts) >= 2:
            # Capture the job list before rerun to find the new job after.
            jobs_before = set(self._job_row_key(j) for j in self._session.jobs())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _handle_cmd(parts, self._session)
            output = buf.getvalue().strip()
            if output:
                session_log = self.query_one("#session-log", RichLog)
                session_log.write(f"[dim]$ {_escape_markup(line)}[/dim]")
                for out_line in output.splitlines():
                    session_log.write(_escape_markup(out_line))
            first_line = output.splitlines()[0] if output else "Done."
            if first_line.startswith("[Wave] "):
                first_line = first_line[7:]
            self.notify(first_line, timeout=3)
            # Open the newly created job in JOB DETAIL.
            for j in reversed(self._session.jobs()):
                if self._job_row_key(j) not in jobs_before:
                    self._open_detail_for(j)
                    break
            return

        if verb in ("logs", "show", "data", "events") and len(parts) >= 2:
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
            # Job not found -> fall through to _handle_cmd for error message

        # -- Default path: run command, capture output to SESSION LOG ---
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            action = _handle_cmd(parts, self._session)

        output = buf.getvalue()
        if output:
            session_log = self.query_one("#session-log", RichLog)
            session_log.write(f"[dim]$ {_escape_markup(line)}[/dim]")
            for out_line in output.splitlines():
                session_log.write(_escape_markup(out_line))
            # Known operational commands (stop, skip, send-line, send-signal) produce a
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
        active = _active_jobs(self._session)
        if not active:
            self.exit()
            return

        now = time.time()
        if self._last_ctrl_c > now - 3.0:
            # Double-tap confirmed: force shutdown — notify immediately so the
            # user knows we are working (avoids the "frozen" appearance while
            # cancel() + _stop() drain worker threads).
            n = len(active)
            self.notify(
                f"Terminating {n} job(s)\u2026 please wait.",
                severity="warning",
                timeout=30,
            )
            sys_log = self.query_one("#session-log", RichLog)
            sys_log.write(f"[bold red]Force quit: cancelling {n} active job(s)\u2026[/bold red]")

            def _cancel_jobs() -> None:
                if hasattr(self._session, "pause"):
                    self._session.pause()
                for job in active:
                    if hasattr(job, "cancel"):
                        job.cancel()

            self.run_worker(_cancel_jobs, thread=True)
            self._quitting = True
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

    def action_focus_job_input(self) -> None:
        """'i': Focus the Job Input Bar to send text to the current job's stdin."""
        if not self._is_detail_tab_active():
            return
        focused = self.focused
        if getattr(focused, "id", None) in ("cmd-input", "job-input-bar"):
            return
        self.query_one("#job-input-bar", Input).focus()

    def action_unfocus_input(self) -> None:
        """Esc: Leave any input widget and return focus to the active panel."""
        self._focus_active_panel()

    def action_detail_panel(self, panel_id: str) -> None:
        """1-4: Switch the right-side detail sub-tab by panel id."""
        if not self._is_detail_navigation_context():
            return
        try:
            self.query_one("#detail-tabs", TabbedContent).active = panel_id
            self._focus_log_view_if_available()
        except NoMatches:
            pass

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

    # ------------------------------------------------------------------
    # TERMINAL sub-tab shortcut actions (F9 / F10)
    # ------------------------------------------------------------------

    def _is_terminal_shortcut_context(self) -> bool:
        """Return True when F9/F10 shortcuts should be active.

        All of these must hold:
        - JOB DETAIL tab is open
        - A PTY job is selected in JOB DETAIL
        - Neither command bar nor job input bar is focused

        Note: the TERMINAL sub-tab no longer needs to be active; F9/F10
        work from anywhere in JOB DETAIL as long as a PTY job is open.
        """
        if not self._is_detail_tab_active():
            return False
        if self._detail_job is None:
            return False
        focused = self.focused
        focused_id = getattr(focused, "id", None)
        return focused_id not in ("cmd-input", "job-input-bar")

    def action_terminal_send_ctrl_c(self) -> None:
        """F9: Send Ctrl-C (\\x03) to the current PTY job."""
        self._terminal_send_key("ctrl-c")

    def action_terminal_send_ctrl_d(self) -> None:
        """F10: Send Ctrl-D (\\x04) to the current PTY job."""
        self._terminal_send_key("ctrl-d")

    def _terminal_send_key(self, key: str) -> None:
        """Send *key* to the current detail job's PTY (used by F9/F10)."""
        if not self._is_terminal_shortcut_context():
            return
        job = self._detail_job
        if job is None:
            return
        name = _escape_markup(str(getattr(job, "name", job)))
        if not getattr(job, "supports_pty", False) or not hasattr(job, "send_key"):
            self.notify("Terminal keys require PtyJob.", severity="warning", timeout=3)
            return
        try:
            job.send_key(key)
            label = {"ctrl-c": "Ctrl-C", "ctrl-d": "Ctrl-D"}.get(key, key)
            self.notify(f"{label} sent to '{name}'.", timeout=2)
        except (ValueError, RuntimeError) as exc:
            self.notify(f"Key send failed: {exc}", severity="error", timeout=4)
