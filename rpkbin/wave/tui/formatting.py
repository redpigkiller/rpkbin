"""Formatting helpers for the Wave Textual TUI."""

from __future__ import annotations

import time

from rich.markup import escape as _escape_markup


_STATUS_COLOR: dict[str, str] = {
    "running": "green",
    "done": "cyan",
    "pending": "yellow",
    "failed": "red",
    "cancelled": "grey50",
}


def _fmt_elapsed(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _job_elapsed_s(job) -> float | None:
    started = getattr(job, "start_time", None)
    if started is None:
        return None
    finished = getattr(job, "end_time", None)
    end = finished if finished is not None else time.monotonic()
    return max(0.0, end - started)


def _format_system_event_line(event: dict) -> str:
    tag_raw = str(event.get("tag", ""))
    tag = _escape_markup(tag_raw)
    msg = _escape_markup(str(event.get("message", "")))
    time_str = _escape_markup(str(event.get("time", "?")))
    if tag_raw in {"parser_error", "hook_error"}:
        return f"[grey50]{time_str}[/grey50] [bold red]{tag}[/bold red] - [red]{msg}[/red]"
    return f"[grey50]{time_str}[/grey50] [bold]{tag}[/bold] - {msg}"
