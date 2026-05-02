"""
StageTracker — A workflow-oriented logger for sequential, multi-stage processes.

Classes:
    StageTracker      — The main tracking context logger.
    Issue             — Dataclass for accumulating errors and warnings.
    StageFailedError  — Exception raised when a stage fails checkpointing.
    UsageError        — Exception raised for invalid API usage.
"""

from __future__ import annotations

import os
import sys
import time
import logging
import threading
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Literal, Any
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.table import Table

    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Console: Any = None
    RichHandler: Any = None
    Panel: Any = None
    Table: Any = None


# ------------------------------------------------
# Utilities
# ------------------------------------------------


def _detect_plain_fallback() -> bool:
    if os.environ.get("NO_COLOR"):
        return True
    if os.environ.get("TERM") in ("dumb", "unknown"):
        return True
    if not sys.stdout.isatty():
        return True
    return False


# ------------------------------------------------
# Data Model
# ------------------------------------------------


class TrackerMode(Enum):
    FLAT = "flat"
    CONTEXT = "context"


class ErrorLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Issue:
    level: ErrorLevel
    message: str
    stage: str


class StageFailedError(Exception):
    def __init__(self, stage: str, issues: list[Issue], message: Optional[str] = None):
        self.stage = stage
        self.issues = issues
        self.error_count = sum(
            1 for i in issues if i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)
        )
        if message is None:
            message = f"Stage '{stage}' failed with {self.error_count} error(s)"
        super().__init__(message)


class UsageError(Exception):
    pass


class StageFormatter(logging.Formatter):
    def format(self, record):
        record.stage = getattr(record, "stage", "System")
        return super().format(record)


# ------------------------------------------------
# StageTracker
# ------------------------------------------------


class StageTracker:
    """A workflow-oriented logger for sequential, multi-stage processes."""

    def __init__(
        self,
        name: str = "StageTracker",
        mode: Literal["flat", "context"] = "flat",
        plain: bool | None = None,
        track_time: bool = True,
    ):
        """
        Initialize a new StageTracker.

        Args:
            name: The base name for this tracker (used in logging).
            mode: Operating mode. 'flat' uses begin_stage(), 'context' uses stage() context manager.
            plain: If True, disable Rich formatting. If None, auto-detects based on terminal capabilities.
            track_time: If True, record execution duration for each stage.
        """
        if mode not in ("flat", "context"):
            raise ValueError("mode must be 'flat' or 'context'")

        self._mode = TrackerMode(mode)

        self.logger = logging.getLogger(f"{name}.{id(self)}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        if plain is None:
            self._use_plain = _detect_plain_fallback() or not HAS_RICH
        else:
            self._use_plain = plain or not HAS_RICH

        self._console = None if self._use_plain else Console()

        self._local = threading.local()

        self._issues_lock = threading.Lock()
        self._issues: dict[str, list[Issue]] = {}

        self._track_time = track_time
        self._stage_time: dict[str, float] = {}
        self._stage_start: dict[str, float] = {}

        self._stage_order: dict[str, list[str]] = {}

        self.console_enabled = True
        self.file_enabled = True

        self._entered = False

        self.add_console_handler()

    def __repr__(self) -> str:
        return f"<StageTracker mode={self._mode.value} stages={self._stage_order}>"

    def _check_entered(self) -> None:
        """Ensure StageTracker is used within a context manager."""
        if not self._entered:
            raise UsageError("StageTracker must be used within a 'with' statement (e.g., `with StageTracker() as t:`)")

    # ------------------------------------------------
    # thread-local stage
    # ------------------------------------------------

    @property
    def current_stage(self) -> Optional[str]:
        return getattr(self._local, "stage", None)

    @current_stage.setter
    def current_stage(self, value: Optional[str]):
        self._local.stage = value

    # ------------------------------------------------
    # stage lifecycle
    # ------------------------------------------------

    def _check_unique_stage(self, name: str):
        with self._issues_lock:
            if name in self._issues:
                raise UsageError(f"Stage '{name}' already exists")

    def _check_stage_health(self, stage: str):
        issues = self._issues.get(stage, [])
        errors = [
            i for i in issues if i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)
        ]
        if errors:
            raise StageFailedError(stage, errors)

    def _finalize_stage(self):
        stage = self.current_stage
        if stage:
            self._check_stage_health(stage)

            if self._track_time and stage in self._stage_start:
                with self._issues_lock:
                    self._stage_time[stage] = (
                        time.perf_counter() - self._stage_start[stage]
                    )

            self.current_stage = None

    def _make_stage_name(self, name: str) -> str:
        """Create a thread-aware stage name."""
        thread = threading.current_thread()
        # Clean name for main thread without suffix
        if thread is threading.main_thread():
            return name
        return f"{name}#{thread.name}"

    # flat mode
    def begin_stage(self, name: str) -> None:
        """
        Begin a new stage in flat mode.

        This method automatically finalizes the previous stage. If the previous stage
        had any accumulated errors (ERROR or CRITICAL), this method will raise
        a StageFailedError immediately before starting the new stage.

        Args:
            name: The name of the new stage.

        Raises:
            UsageError: If NOT in 'flat' mode or if the stage name already exists.
            StageFailedError: If the PREVIOUS stage had errors.
        """
        self._check_entered()
        name = self._make_stage_name(name)

        if self._mode != TrackerMode.FLAT:
            raise UsageError("begin_stage() only valid in flat mode")

        self._check_unique_stage(name)
        self._finalize_stage()

        self.current_stage = name
        with self._issues_lock:
            self._issues[name] = []
            thread_name = threading.current_thread().name
            self._stage_order.setdefault(thread_name, []).append(name)

            if self._track_time:
                self._stage_start[name] = time.perf_counter()

        self._log_system(f"Stage: {name}", "DEBUG")

    # context mode
    @contextmanager
    def stage(self, name: str):
        """
        Context manager for entering a stage. Context mode only.

        Stage health is checked automatically upon exiting the context. If any errors
        occurred within the stage, StageFailedError is raised.

        Args:
            name: The name of the stage.

        Raises:
            UsageError: If NOT in 'context' mode, or if nested stages are attempted.
            StageFailedError: If the stage concludes with errors.
        """
        self._check_entered()
        name = self._make_stage_name(name)

        if self._mode != TrackerMode.CONTEXT:
            raise UsageError("stage() only valid in context mode")

        if self.current_stage is not None:
            raise UsageError("Nested stages are not supported")

        self._check_unique_stage(name)

        with self._issues_lock:
            self._issues[name] = []
            thread_name = threading.current_thread().name
            self._stage_order.setdefault(thread_name, []).append(name)

        self.current_stage = name
        if self._track_time:
            self._stage_start[name] = time.perf_counter()

        self._log_system(f"Stage: {name}", "DEBUG")

        try:
            yield
        except BaseException as e:
            raise e
        else:
            self._check_stage_health(self.current_stage)
        finally:
            stage = self.current_stage
            if self._track_time and stage in self._stage_start:
                with self._issues_lock:
                    self._stage_time[stage] = (
                        time.perf_counter() - self._stage_start[stage]
                    )
            self.current_stage = None

    # ------------------------------------------------
    # checkpoint
    # ------------------------------------------------

    def checkpoint(self) -> None:
        """
        Proactively check the health of the current stage.

        Raises:
            StageFailedError: If the current stage has accumulated any errors.
        """
        self._check_entered()
        stage = self.current_stage
        if stage:
            self._check_stage_health(stage)

    # ------------------------------------------------
    # logging
    # ------------------------------------------------

    def _log(self, level: ErrorLevel, msg: str, track: bool, **kwargs):
        self._check_entered()
        stage = self.current_stage or "System"

        extra = dict(kwargs.pop("extra", {}))
        extra["stage"] = stage
        kwargs["extra"] = extra

        if track:
            issue = Issue(level, msg, stage)
            with self._issues_lock:
                self._issues.setdefault(stage, []).append(issue)

        getattr(self.logger, level.value)(msg, **kwargs)

    def debug(self, msg, track=False, **kw):
        self._log(ErrorLevel.DEBUG, msg, track, **kw)

    def info(self, msg, track=False, **kw):
        self._log(ErrorLevel.INFO, msg, track, **kw)

    def warning(self, msg, track=True, **kw):
        self._log(ErrorLevel.WARNING, msg, track, **kw)

    def error(self, msg, **kw):
        self._log(ErrorLevel.ERROR, msg, True, **kw)

    def fatal(self, msg, **kw):
        """
        Log a critical error and raise StageFailedError immediately.

        Args:
            msg: The error message.
            **kw: Additional arguments passed to the logger.

        Raises:
            StageFailedError: Always raised after recording the fatal issue.
        """
        self._log(ErrorLevel.CRITICAL, msg, True, **kw)

        stage = self.current_stage or "System"
        issues = self._issues.get(stage, [])

        # Removed clearing current_stage and time calculation.
        # Raise error directly, let __exit__ or _finalize_stage handle cleanup.
        raise StageFailedError(stage, issues, f"Fatal error in stage '{stage}': {msg}")

    def _log_system(self, msg, level="DEBUG"):
        getattr(self.logger, level.lower())(
            msg,
            extra={"stage": self.current_stage or "System"},
        )

    # ------------------------------------------------
    # handlers
    # ------------------------------------------------

    def add_console_handler(self, level="INFO"):
        plain_handlers = [
            h for h in self.logger.handlers if type(h) is logging.StreamHandler
        ]
        rich_handlers = [
            h for h in self.logger.handlers if HAS_RICH and isinstance(h, RichHandler)
        ]
        if plain_handlers or rich_handlers:
            return

        if not self._use_plain:
            handler = RichHandler(
                console=self._console,
                show_time=True,
                show_path=False,
                rich_tracebacks=True,
            )
        else:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(
                StageFormatter(
                    "%(asctime)s [%(levelname)s] %(stage)s: %(message)s",
                    "%H:%M:%S",
                )
            )

        handler.setLevel(level)
        self.logger.addHandler(handler)

    def add_file_handler(
        self,
        path,
        level="DEBUG",
        max_bytes=0,
        backup_count=0,
    ):
        if max_bytes > 0:
            handler = RotatingFileHandler(
                path,
                mode="a",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        else:
            handler = logging.FileHandler(path, encoding="utf-8")

        handler.setFormatter(
            StageFormatter("%(asctime)s [%(levelname)s] %(stage)s: %(message)s")
        )

        handler.setLevel(level)
        self.logger.addHandler(handler)

    # ------------------------------------------------
    # issues API
    # ------------------------------------------------

    def get_issues(self, stage: str | None = None, level: ErrorLevel | str | list | None = None) -> list[Issue]:
        """Get accumulated issues, optionally filtered by stage or level."""
        self._check_entered()
        with self._issues_lock:
            if stage:
                issues = list(self._issues.get(stage, []))
            else:
                issues = [
                    i for stage_issues in self._issues.values() for i in stage_issues
                ]

        if level:
            levels = level if isinstance(level, list) else [level]
            levels = [
                lvl if isinstance(lvl, ErrorLevel) else ErrorLevel(lvl.lower())
                for lvl in levels
            ]
            issues = [i for i in issues if i.level in levels]

        return issues
    
    def clear_issues(self):
        """
        Clear all tracking data, history, and reset internal state.

        Warning: This resets everything except the log handlers and entry status.
        Cannot be called while a stage is currently active.
        """
        self._check_entered()
        if self.current_stage is not None:
            raise UsageError("Cannot clear issues while a stage is active.")
            
        with self._issues_lock:
            self._issues.clear()
            self._stage_order.clear()
            self._stage_time.clear()
            self._stage_start.clear()

    # ------------------------------------------------
    # summary
    # ------------------------------------------------

    def summary(self, title: str = "EXECUTION SUMMARY", raise_errors: bool = True) -> bool:
        """
        Generate and print an execution summary report.

        In 'flat' mode, if there is an active stage, it will be finalized first.

        Args:
            title: Title of the summary panel/table.
            raise_errors: If True and the workflow had errors, raises StageFailedError
                         after printing the report.

        Returns:
            bool: True if the workflow completed without any errors.

        Raises:
            StageFailedError: If raise_errors is True and errors exist.
        """

        deferred_exc = None
        if self._mode == TrackerMode.FLAT and self.current_stage:
            try:
                self._finalize_stage()
            except StageFailedError as e:
                # Record the final stage error, do not swallow it as Warning
                self._log_system(f"Stage '{e.stage}' finalized with {e.error_count} error(s)", "ERROR")
                deferred_exc = e

        issues = self.get_issues()
        errors = [
            i for i in issues
            if i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)
        ]

        if not self._use_plain and self._console:
            self._console.print(Panel(f"[bold cyan]{title}[/]", expand=False))
            
            # Support grouped output by thread
            self._console.print("[bold]Execution Paths by Thread:[/]")
            for t_name, order in self._stage_order.items():
                self._console.print(f"  [cyan]{t_name}[/]: [dim]{' → '.join(order)}[/]")
            self._console.print("")

            if issues:
                table = Table(show_header=True, header_style="bold magenta", expand=False)
                table.add_column("Stage", style="cyan")
                table.add_column("Level", justify="center")
                table.add_column("Message")
                if self._track_time:
                    table.add_column("Time", justify="right", style="dim")

                for i in issues:
                    # Color based on severity level
                    lvl_color = {
                        ErrorLevel.CRITICAL: "bold white on red",
                        ErrorLevel.ERROR: "bold red",
                        ErrorLevel.WARNING: "bold yellow",
                        ErrorLevel.INFO: "green",
                        ErrorLevel.DEBUG: "dim",
                    }.get(i.level, "white")

                    row_data = [
                        i.stage,
                        f"[{lvl_color}]{i.level.value.upper()}[/]",
                        i.message
                    ]
                    
                    if self._track_time:
                        time_str = f"{self._stage_time[i.stage]:.2f}s" if i.stage in self._stage_time else ""
                        row_data.append(time_str)

                    table.add_row(*row_data)
                self._console.print(table)
            else:
                self._console.print("[dim italic]No recorded issues.[/]")

            self._console.print("")
            if errors:
                self._console.print(f"❌ [bold red]FAILED ({len(errors)} critical/errors found)[/]")
            else:
                self._console.print("✅ [bold green]SUCCESS[/]")

        else:
            # Plain text fallback optimization
            print("=" * 60)
            print(f"{title:^60}")
            print("=" * 60)
            
            print("Paths by Thread:")
            for t_name, order in self._stage_order.items():
                print(f"  {t_name}: {' → '.join(order)}")
            print("-" * 60)

            if issues:
                for i in issues:
                    time_str = f"({self._stage_time[i.stage]:.2f}s)" if self._track_time and i.stage in self._stage_time else ""
                    print(f"[{i.level.value.upper():^8}] {i.stage:15} | {i.message} {time_str}")
            else:
                print("No recorded issues.")

            print("-" * 60)
            if errors:
                print(f"FAILED: {len(errors)} critical/errors found.")
            else:
                print("SUCCESS")
            print("=" * 60)

        # After printing report, if there's an error in flat mode's final stage, raise it here
        if deferred_exc and raise_errors:
            raise deferred_exc

        return len(errors) == 0

    # ------------------------------------------------
    # context manager support
    # ------------------------------------------------

    def __enter__(self):
        self._entered = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # Re-record time for current stage in flat mode (context mode's finally already handles this)
            if self._mode == TrackerMode.FLAT:
                stage = self.current_stage
                if stage and self._track_time and stage in self._stage_start:
                    with self._issues_lock:
                        self._stage_time[stage] = (
                            time.perf_counter() - self._stage_start[stage]
                        )
                self.current_stage = None

            self.summary(
                title=f"EXECUTION FAILED ({exc_type.__name__})", raise_errors=False
            )
        else:
            self.summary(title="EXECUTION SUMMARY", raise_errors=True)

        return False
