"""_util.py - Shared helpers for Wave display and CLI logic.

These are "interpretation" helpers used by both the TUI and the headless
runner. They do not carry job state and should not import from ``job.py`` or
``session.py`` to avoid circular dependencies.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Dashboard column spec - single source of truth
# ---------------------------------------------------------------------------

DASHBOARD_BUILTIN_LABELS: dict[str, str] = {
    "name": "Name",
    "id": "ID",
    "status": "Status",
    "elapsed": "Elapsed",
    "progress": "Progress",
    "retries": "Retries",
    "exit_code": "Exit Code",
    "tags": "Tags",
}

TUI_BUILTIN_DASHBOARD_COLUMNS: frozenset[str] = frozenset(DASHBOARD_BUILTIN_LABELS)


# ---------------------------------------------------------------------------
# Exit code interpretation
# ---------------------------------------------------------------------------

def job_exit_code(job) -> int | None:
    """Derive an integer exit code from a job's result and status.

    - If ``job.result`` is an ``int`` (and not a ``bool``), use it directly.
    - If the job status is ``"failed"`` but there is no integer result,
      return ``1`` as a conventional non-zero exit code.
    - Otherwise return ``None`` (no exit code available yet).
    """
    result = getattr(job, "result", None)
    if isinstance(result, int) and not isinstance(result, bool):
        return result
    if getattr(job, "status", None) == "failed":
        return 1
    return None
