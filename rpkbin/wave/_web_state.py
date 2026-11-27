"""Capture the workspace snapshot and project it for the browser."""

from __future__ import annotations

import os
import signal
from typing import Any

from .columns import _available_columns, _configured_columns

_POSIX_SIGNAL_NAMES = (
    "SIGINT",
    "SIGTERM",
    "SIGTSTP",
    "SIGCONT",
    "SIGUSR1",
    "SIGUSR2",
    "SIGHUP",
    "SIGQUIT",
)
_ALLOWED_SIGNALS: dict[str, int] = (
    {
        name: int(getattr(signal, name))
        for name in _POSIX_SIGNAL_NAMES
        if getattr(signal, name, None) is not None
    }
    if os.name == "posix"
    else {}
)


def _browser_data(value):
    """Keep Python integers exact when publication data reaches JavaScript Number."""
    if isinstance(value, int) and abs(value) > (1 << 53) - 1:
        return str(value)
    if isinstance(value, dict):
        return {key: _browser_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_browser_data(item) for item in value]
    return value


class _WaveState:
    def _state(
        self, job_id=None, attempt_id=None, *, log_after=None, log_attempt=None
    ) -> dict[str, Any]:
        with self._workspace_lock:
            manager = self.workspace
            opened_archive = self._opened_archive
            workspace = dict(workspace_id=self._workspace_id, source_path=self.source_path,
                             launch_cwd=str(self.launch_cwd))
        jobs, configured = (), None

        def capture_preferences(roster):
            nonlocal jobs, configured
            jobs, configured = roster, _configured_columns(manager)

        result = manager._snapshot(selected_job_id=job_id, attempt_id=attempt_id, include_logs=False,
                                   log_cursor=(log_after, log_attempt), capture_preferences=capture_preferences)
        available = _available_columns(manager, jobs, result["jobs"])
        archive_catalog = opened_archive.path.parent if opened_archive else manager.archive_catalog_dir
        result.update(
            archive_dir=str(archive_catalog),
            archive_dir_exists=archive_catalog.is_dir(),
            configured_columns=None if configured is None else list(configured),
            available_columns=available,
            columns=[name for name in available if "." in name] if configured is None else list(configured),
            platform=os.name,
            supported_signals=list(_ALLOWED_SIGNALS.keys()),
        )
        for job in (
            result["jobs"]
            + result.get("removed_jobs", [])
            + ([result["detail"]] if result["detail"] else [])
        ):
            for field in ("data", "flow", "flow_summary"):
                if field in job:
                    job[field] = _browser_data(job[field])
        result.update(workspace)
        if opened_archive:
            result.update(archive=opened_archive.health, opened_run_id=opened_archive.run_id)
        return result
