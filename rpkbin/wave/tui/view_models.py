"""View-model builders for the Wave Textual TUI."""

from __future__ import annotations

import logging

from rich.markup import escape as _escape_markup

from rpkbin.wave._util import (
    DASHBOARD_BUILTIN_LABELS as _DASHBOARD_BUILTIN_LABELS,
    job_exit_code as _job_exit_code,
)
from rpkbin.wave.tui.formatting import _STATUS_COLOR, _fmt_elapsed, _job_elapsed_s


_DEFAULT_DASHBOARD_COLUMNS: tuple[dict[str, str], ...] = (
    {"type": "builtin", "key": "no"},
    {"type": "builtin", "key": "name"},
    {"type": "builtin", "key": "status"},
    {"type": "builtin", "key": "elapsed"},
    {"type": "builtin", "key": "exit_code"},
)
_DATA_SNAPSHOT_FAILED = object()


def resolve_dashboard_columns(session, logger: logging.Logger) -> tuple[dict[str, str], ...]:
    try:
        config = getattr(session, "tui_config", lambda: {})()
        columns = config.get("dashboard_columns")
    except Exception:
        logger.warning("Failed to read Wave TUI config; using default dashboard columns.", exc_info=True)
        return _DEFAULT_DASHBOARD_COLUMNS

    if columns is None:
        return _DEFAULT_DASHBOARD_COLUMNS
    if not isinstance(columns, (list, tuple)) or not columns:
        logger.warning("Invalid dashboard column config %r; using defaults.", columns)
        return _DEFAULT_DASHBOARD_COLUMNS

    resolved: list[dict[str, str]] = []
    for idx, column in enumerate(columns):
        if not isinstance(column, dict):
            logger.warning("Invalid dashboard column at index %s: %r; using defaults.", idx, column)
            return _DEFAULT_DASHBOARD_COLUMNS
        kind = column.get("type")
        if kind == "builtin" and column.get("key") in _DASHBOARD_BUILTIN_LABELS:
            resolved.append({"type": "builtin", "key": str(column["key"])})
        elif kind == "parsed_data" and column.get("label") and column.get("data"):
            resolved.append({
                "type": "parsed_data",
                "label": str(column["label"]),
                "data": str(column["data"]),
            })
        else:
            logger.warning("Invalid dashboard column at index %s: %r; using defaults.", idx, column)
            return _DEFAULT_DASHBOARD_COLUMNS
    return tuple(resolved)


def dashboard_column_labels(columns: tuple[dict[str, str], ...]) -> list[str]:
    labels: list[str] = []
    for column in columns:
        if column["type"] == "builtin":
            labels.append(_DASHBOARD_BUILTIN_LABELS[column["key"]])
        else:
            labels.append(_escape_markup(column["label"]))
    return labels


def build_row_cells(
    job,
    columns: tuple[dict[str, str], ...],
    logger: logging.Logger,
    *,
    row_number: int | None = None,
) -> tuple[str, ...]:
    """Return one dashboard row cell per configured column."""
    has_data_cols = any(column["type"] == "parsed_data" for column in columns)
    data_snapshot = None
    if has_data_cols:
        try:
            data_snapshot = getattr(job, "peek_data", lambda: {})()
        except Exception:
            logger.warning(
                "Failed to read parsed_data snapshot for job %r.",
                getattr(job, "name", job),
                exc_info=True,
            )
            data_snapshot = _DATA_SNAPSHOT_FAILED
    return tuple(
        dashboard_cell(job, column, logger, data=data_snapshot)
        if column["type"] != "builtin" or column["key"] != "no"
        else "" if row_number is None else str(row_number)
        for column in columns
    )


def dashboard_cell(
    job,
    column: dict[str, str],
    logger: logging.Logger,
    *,
    data: dict | object | None = None,
) -> str:
    if column["type"] == "parsed_data":
        if data is _DATA_SNAPSHOT_FAILED:
            return "[red]ERR[/red]"
        try:
            data_snapshot = data if data is not None else getattr(job, "peek_data", lambda: {})()
            return _escape_markup(str(data_snapshot.get(column["data"], "")))
        except Exception:
            logger.warning(
                "Failed to read parsed_data column %r for job %r.",
                column.get("data"),
                getattr(job, "name", job),
                exc_info=True,
            )
            return "[red]ERR[/red]"
    return builtin_dashboard_cell(job, column["key"], logger)


def builtin_dashboard_cell(job, key: str, logger: logging.Logger) -> str:
    if key == "no":
        return ""

    if key == "name":
        return _escape_markup(str(job.name))

    status = getattr(job, "status", "pending")
    if key == "status":
        color = _STATUS_COLOR.get(status, "white")
        return f"[{color}]{status.upper()}[/{color}]"

    if key == "elapsed":
        return _fmt_elapsed(_job_elapsed_s(job))

    if key == "progress":
        prog = getattr(job, "progress", None)
        if isinstance(prog, (int, float)):
            filled = max(0, min(10, round(prog / 10)))
            bar = "#" * filled + "-" * (10 - filled)
            return f"{bar} {prog:.0f}%"
        return ""

    if key == "retries":
        retry_count = getattr(job, "retry_count", 0)
        max_retries = getattr(job, "max_retries", 0)
        return f"{retry_count}/{max_retries}" if max_retries > 0 else ""

    if key == "exit_code":
        exit_code = _job_exit_code(job)
        if exit_code is not None:
            color = "cyan" if exit_code == 0 else "red"
            return f"[{color}]{exit_code}[/{color}]"
        return ""

    if key == "tags":
        tags = getattr(job, "tags", None) or set()
        return ",".join(sorted(_escape_markup(str(tag)) for tag in tags))

    if key == "id":
        return _escape_markup(str(getattr(job, "id", ""))[:8])

    logger.warning("Unknown dashboard builtin column %r; rendering blank.", key)
    return ""


def command_identifier_for_job(job, jobs: list) -> str:
    """Prefer job name for unique jobs; use id when duplicate names exist."""
    name = str(job.name)
    same_name = [candidate for candidate in jobs if str(candidate.name) == name]
    if len(same_name) > 1:
        return str(getattr(job, "id", job.name))
    return name


def build_info_text(job) -> str:
    """Build Rich markup text for the Job Detail INFO panel."""
    lines: list[str] = []
    lines.append(f"[bold white]{_escape_markup(str(job.name))}[/bold white]")
    job_id = getattr(job, "id", None)
    if job_id:
        lines.append(f"[dim]ID: {_escape_markup(str(job_id))}[/dim]")

    command = getattr(job, "cmd", None)
    if command:
        lines.append(f"\n[cyan]Command:[/cyan]  {_escape_markup(str(command))}")

    cwd = getattr(job, "cwd", None)
    if cwd:
        lines.append(f"[cyan]Working dir:[/cyan]  {_escape_markup(str(cwd))}")

    error = getattr(job, "error", None)
    if error:
        lines.append(f"\n[bold red]Error[/bold red]\n[red]{_escape_markup(str(error))}[/red]")

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
        for key, value in resources.items():
            lines.append(f"  [cyan]{_escape_markup(str(key))}[/cyan] = {_escape_markup(str(value))}")

    tags = getattr(job, "tags", None) or set()
    if tags:
        tag_str = "  ".join(
            f"[dim]#{_escape_markup(str(tag))}[/dim]"
            for tag in sorted(str(tag) for tag in tags)
        )
        lines.append(f"\n[bold]Tags[/bold]   {tag_str}")

    return "\n".join(lines)
