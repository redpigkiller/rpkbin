"""HTTP request handling, command routes and archive queries."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
import urllib.parse
import uuid
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from rpkbin.jobcore._job_config import _integer
from rpkbin.jobcore.manager import _scheduling_mode

from .archive import RunArchive, find_archive_by_run_id, remove_archive_files

from ._web_state import _ALLOWED_SIGNALS, _browser_data
from .columns import _store_columns, _validated_columns
from ._source_discovery import _ARCHIVE_SUFFIXES
from .sources import _FileAttempt, _load_flow_file
from .workspace import WaveWorkspace

_MAX_POST_BYTES = 2 * 1024 * 1024
_REJECT_DRAIN_SECONDS = 0.5
_SOCKET_TIMEOUT_SECONDS = 10.0
_CHART_VIEWS = frozenset(("chart_meta", "chart_series", "chart_window", "chart_find"))

_STATIC_DIR = Path(__file__).with_name("static")
_STATIC_ASSETS: dict[str, tuple[Path, str]] = {
    "/": (_STATIC_DIR / "index.html", "text/html; charset=utf-8"),
    "/index.html": (_STATIC_DIR / "index.html", "text/html; charset=utf-8"),
    "/static/index.html": (_STATIC_DIR / "index.html", "text/html; charset=utf-8"),
    "/app.css": (_STATIC_DIR / "app.css", "text/css; charset=utf-8"),
    "/static/app.css": (_STATIC_DIR / "app.css", "text/css; charset=utf-8"),
    "/app.js": (_STATIC_DIR / "app.js", "text/javascript; charset=utf-8"),
    "/static/app.js": (_STATIC_DIR / "app.js", "text/javascript; charset=utf-8"),
}


_LOOPBACK_HOST = re.compile(r"(127\.0\.0\.1|localhost|\[::1\]):([1-9][0-9]{0,4})", re.ASCII)


def _loopback_host(host: str | None) -> bool:
    """Accept only ``loopback-name:port`` with a canonical port in 1..65535."""
    match = _LOOPBACK_HOST.fullmatch(host) if isinstance(host, str) else None
    return match is not None and int(match.group(2)) <= 65535


class _FieldError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@contextmanager
def _field_validation(field):
    try:
        yield
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error) as exc:
        raise _FieldError(field, str(exc)) from exc


def _target(raw: Any) -> tuple[str, str, str]:
    if not isinstance(raw, dict):
        raise ValueError(
            "Each target must include job_id, attempt_id and submission_id"
        )
    values = tuple(raw.get(k) for k in ("job_id", "attempt_id", "submission_id"))
    if not all(isinstance(v, str) and v for v in values):
        raise ValueError(
            "Explicit job, attempt and submission identities are required"
        )
    return values


def _parse_targets(body: dict[str, Any], field: str = "targets") -> list[tuple[str, str, str]]:
    targets = body.get(field)
    if not isinstance(targets, list) or not targets or len(targets) > 10000:
        raise ValueError("Provide between 1 and 10000 explicit targets")
    parsed = [_target(t) for t in targets]
    if len({t[0] for t in parsed}) != len(parsed):
        raise ValueError("Duplicate job target")
    return parsed


def _run_target_action(manager, parsed, fn):
    results = []
    for jid, attempt, submission in parsed:
        job = manager.get_job(jid)
        item = {
            "job_id": jid,
            "attempt_id": attempt,
            "submission_id": submission,
            "name": job.name if job else jid,
            "ok": False,
        }
        try:
            if job is None or job.id != jid:
                raise ValueError("Unknown job ID")
            receipt = fn(job, jid, attempt, submission, item)
            item.update(
                ok=receipt.accepted,
                status=receipt.status,
                reason=receipt.reason,
                message=receipt.reason,
            )
        except (ValueError, TypeError, RuntimeError) as exc:
            item.update(status="rejected", reason=str(exc), message=str(exc))
        results.append(item)
    return results

def _archive_query(self, query):
    """Read-only history, scoped to this workspace and an explicit run."""
    def arg(name, default=None):
        values = query.get(name)
        if values is not None and len(values) != 1:
            raise ValueError(f"Duplicate {name}")
        return values[0] if values is not None else default

    def integer(name, default=None):
        value = arg(name)
        if value is None:
            return default
        if not value.isascii() or not value.isdecimal() or len(value) > 18:
            raise ValueError(f"{name} must be a nonnegative integer")
        return int(value)

    def seconds(name):
        value = arg(name)
        return float(value) if value is not None else None

    def boolean(name, default="false"):
        value = arg(name, default)
        if value not in ("true", "false"):
            raise ValueError(f"{name} must be true or false")
        return value == "true"

    def json_arg(name, default=None):
        raw = arg(name)
        if raw is None:
            return default
        if len(raw) > 16384:
            raise ValueError(f"{name} is too large")
        try:
            return json.loads(raw)
        except RecursionError as exc:
            raise ValueError(f"{name} is nested too deeply") from exc

    catalog_request = None
    with self._action_lock:
        if arg("workspace_id") != self._workspace_id:
            raise ValueError("Workspace changed; refresh before reading history")
        current = self.workspace.archive or self._opened_archive
        view = arg("view", "logs")
        chart = view in _CHART_VIEWS
        historical_path = None
        if view == "runs":
            raw_all = arg("all", "false")
            if raw_all not in ("true", "false", "1", "0"):
                raise ValueError("all must be true or false")
            directory = (
                self._opened_archive.path.parent
                if self._opened_archive is not None
                else self.workspace.archive_catalog_dir
            )
            catalog_request = (
                directory,
                self.source_path,
                arg("cursor"),
                integer("limit", 50),
                raw_all in ("true", "1"),
                self._workspace_id,
            )
        else:
            run_id = arg("run_id", current.run_id if current else self.workspace.run_id)
            if not re.fullmatch(r"[0-9a-f]{32}", run_id):
                raise ValueError("Invalid run identity")
            if current and run_id == current.run_id:
                store = current
                # Stage each live prefix and its snapshot together before this commit.
                if not chart:
                    self.workspace._save_current_attempts()
                    store.flush()
            else:
                directory = (
                    self._opened_archive.path.parent
                    if self._opened_archive is not None
                    else self.workspace.archive_catalog_dir
                )
                historical_path = find_archive_by_run_id(directory, run_id) or (directory / f"{run_id}.sqlite3")
                source_path = self.source_path
                store = None
            workspace_id = self._workspace_id
    if catalog_request is not None:
        directory, source_path, cursor_arg, limit_arg, include_all, workspace_id = catalog_request
        return dict(
            RunArchive.catalog(
                directory,
                source_path=source_path,
                cursor=cursor_arg,
                limit=limit_arg,
                include_all=include_all,
                max_seconds=3.0,
            ),
            workspace_id=workspace_id,
        )
    opened_store = None
    if historical_path is not None:
        if not historical_path.is_file():
            raise LookupError("Saved archive not found")
        opened_store = store = RunArchive(historical_path)
    try:
        if opened_store is not None and store.metadata["source_path"] != source_path:
            raise ValueError("Archive belongs to a different work file")
        if view in {"reader", "logs", "text"}:
            retired = {"kind", "fields", "field", "value", "changed"}.intersection(query)
            if retired:
                raise ValueError("Raw Log views do not accept retired filters: " + ", ".join(sorted(retired)))
        job_id, attempt_id = arg("job_id"), arg("attempt_id")
        if chart:
            if view == "chart_meta":
                result = store.chart_meta(job_id, attempt_id)
            else:
                scope = dict(view_id=arg("view_id"), as_of_seq=integer("as_of_seq"), cursor=arg("cursor"))
                if view == "chart_series":
                    result = store.chart_series(job_id, attempt_id, **scope, prefix=json_arg("prefix"),
                                                query=arg("query", ""), limit=integer("limit", 100),
                                                ids=json_arg("ids"))
                elif view == "chart_window":
                    result = store.chart_window(
                        job_id, attempt_id, **scope, series=json_arg("series"), lo=json_arg("lo"), hi=json_arg("hi"),
                        axis=arg("axis", "time"), mode=arg("mode", "raw"), buckets=integer("buckets", 160),
                        inclusive=boolean("inclusive"),
                    )
                else:
                    result = store.chart_find(
                        job_id, attempt_id, **scope, series_id=arg("series_id"), op=arg("op"),
                        axis=arg("axis", "time"), direction=arg("direction", "forward"),
                        value=json_arg("value"), anchor=json_arg("anchor"), bit_slice=json_arg("bit_slice"),
                    )
        elif view == "attempts":
            result = store.attempts(job_id, before=integer("before"), limit=integer("limit", 100))
        elif view == "attempt":
            attempt, health = store.attempt(job_id, attempt_id, preview=True), store.health
            # The attempt and its heartbeat are published in the same transaction.
            if health["closed_cleanly"] and not attempt["verified"]:
                attempt = store.attempt(job_id, attempt_id, preview=True)
            if attempt["source_kind"] == "tail":
                descriptor = attempt.get("source_descriptor") or {}
                health["writer_heartbeat"] = descriptor.get("writer_heartbeat")
                health["writer_commit_interval"] = descriptor.get("commit_interval")
            result = dict(attempt=attempt, health=health)
        elif view == "reader" or (
            view == "logs"
            and "around" not in query
            and bool({"query", "regex", "case_sensitive", "whole_word", "anchor"}.intersection(query))
        ):
            result = store.reader(job_id, attempt_id,
                                  limit=integer("limit", 100), cursor=arg("cursor"),
                                  direction=arg("direction", "forward"), anchor=integer("anchor"),
                                  query=arg("query", ""), regex=boolean("regex"),
                                  case_sensitive=boolean("case_sensitive"),
                                  whole_word=boolean("whole_word"),
                                  start=seconds("start"), end=seconds("end"))
        elif view == "text":
            result = store.read_text(job_id, attempt_id, integer("seq"),
                                     offset=integer("offset", 0),
                                     limit=integer("limit", 65536))
        elif view == "flow":
            conditions = json_arg("conditions")
            result = store.flow_page(
                job_id, attempt_id, stage=arg("stage"), producer=arg("producer"),
                kind=arg("kind"), after=integer("after"), before=integer("before"),
                report_name=arg("report_name"),
                conditions=conditions,
                limit=integer("limit", 100),
            )
        elif view == "automation_history":
            result = store.automation_history(job_id, attempt_id, producer=arg("producer"),
                                               after=integer("after"), before=integer("before"),
                                               limit=integer("limit", 100))
        elif view == "flow_latest":
            result = store.flow_latest(job_id, attempt_id, stage=arg("stage"),
                                       producer=arg("producer"), report_name=arg("report_name"),
                                       mode=arg("mode", "record"), after=integer("after"),
                                       before=integer("before"), limit=integer("limit", 100))
        elif view == "flow_latest_text":
            result = store.flow_latest_text(job_id, attempt_id, integer("seq"),
                                            mode=arg("mode", "fields"), offset=integer("offset", 0),
                                            limit=integer("limit", 65536))
        elif view == "flow_report_names":
            result = store.flow_report_names(job_id, attempt_id, producer=arg("producer"),
                                             query=arg("query", ""), after=arg("after"),
                                             limit=integer("limit", 100))
        elif view == "flow_text":
            result = store.flow_text(job_id, attempt_id, integer("seq"),
                                     offset=integer("offset", 0), limit=integer("limit", 65536))
        else:
            if view == "logs":
                result = store.page(job_id, attempt_id,
                                    limit=integer("limit", 100), cursor=arg("cursor"),
                                    direction=arg("direction", "forward"), around=integer("around"),
                                    start=seconds("start"), end=seconds("end"))
            else:
                raise ValueError("Unknown history view")
        return _browser_data(dict(result, run_id=run_id, workspace_id=workspace_id))
    finally:
        if opened_store is not None:
            opened_store.close()

def _resolve_path(cwd, raw):
    return (cwd / Path(raw).expanduser()).resolve()


def _browse_files(cwd, query, *, archive_dir=None, source_path=None):
    modes = query.get("mode", ["work"])
    if len(modes) != 1 or modes[0] not in ("work", "logs", "archive", "rc"):
        raise ValueError("mode must be 'work', 'logs', 'archive', or 'rc'")
    mode = modes[0]
    with _field_validation("browse_path"):
        path = _resolve_path(cwd, query.get("path", [""])[0])
        if not path.is_dir():
            raise ValueError("Choose a directory")
        entries = []
        with os.scandir(path) as scan:
            for entry in scan:
                try:
                    directory = entry.is_dir()
                    name = entry.name.lower()
                    if mode == "work":
                        match = name.endswith(".py")
                    elif mode == "archive":
                        match = name.endswith(".sqlite3")
                    elif mode == "rc":
                        match = name.endswith((".rc", ".vcd", ".json"))
                    else:
                        match = not name.endswith(_ARCHIVE_SUFFIXES)
                    if directory or (match and entry.is_file()):
                        stat = entry.stat()
                        entries.append({
                            "name": entry.name,
                            "path": str(path / entry.name),
                            "directory": directory,
                            "size": None if directory else int(stat.st_size),
                            "mtime": float(stat.st_mtime),
                        })
                except OSError:
                    continue
    entries.sort(key=lambda e: (not e["directory"], e["name"].casefold()))
    cwd_resolved = Path(cwd).resolve()
    shortcuts = [
        {"id": "cwd", "label": "Launch Dir", "path": str(cwd_resolved), "exists": True},
    ]
    if source_path and not str(source_path).startswith("<"):
        try:
            src_p = Path(source_path).expanduser().resolve()
            work_dir = src_p if src_p.is_dir() else src_p.parent
            if work_dir.is_dir() and work_dir != cwd_resolved:
                shortcuts.append({"id": "work_dir", "label": "Work File Dir", "path": str(work_dir), "exists": True})
        except (OSError, RuntimeError):
            pass
    if archive_dir is not None:
        try:
            arch = Path(archive_dir).expanduser().resolve()
            shortcuts.append({"id": "archives", "label": "Archives", "path": str(arch), "exists": arch.is_dir()})
        except (OSError, RuntimeError):
            pass
    try:
        home = Path.home().resolve()
        shortcuts.append({"id": "home", "label": "Home", "path": str(home), "exists": home.is_dir()})
    except (OSError, RuntimeError):
        pass
    return {"path": str(path), "parent": str(path.parent), "entries": entries, "shortcuts": shortcuts}


def _require_idle_for_load(workspace):
    manager = workspace.manager
    with manager._start_lock:
        readers, watchers = workspace._source_roster()
        # A stop request hides the control immediately. A live thread still
        # owns its reader until it exits, including while it is sealing.
        if any(task.thread.is_alive() for task in (*watchers, *(task for _, task in readers))):
            raise ValueError("Stop active readers and folder watches and wait for cleanup before replacing this workspace")
        if workspace.started and any(not job.status.is_finished or not job._attempt.finalized
                                     for job in manager._ordered_jobs()):
            raise ValueError("Stop queued and running jobs before replacing this batch")


def _log_load_options(cwd, body):
    targets = body.get("targets")
    if isinstance(targets, str):
        targets = [targets]
    with _field_validation("targets"):
        if not isinstance(targets, list) or any(not isinstance(path, str) for path in targets):
            raise ValueError("Provide one or more log or folder paths in targets")
        paths = [_resolve_path(cwd, path.strip()) for path in targets if path.strip()]
        if not paths:
            raise ValueError("Provide one or more log or folder paths in targets")
    once, recursive = body.get("once", False), body.get("recursive", False)
    if type(once) is not bool or type(recursive) is not bool:
        raise ValueError("once and recursive must be booleans")
    globs = body.get("globs", ["*.log"])
    if isinstance(globs, str):
        globs = globs.split(",")
    if not isinstance(globs, list) or not globs or any(not isinstance(g, str) or not g.strip() for g in globs):
        raise _FieldError("globs", "globs must contain nonempty patterns")
    globs = [g.strip() for g in globs]
    with _field_validation("globs"):
        for pattern in globs:
            # Use the same matcher as directory discovery to validate patterns.
            Path("log").match(pattern)
    start = body.get("start", "beginning")
    if start not in ("beginning", "end"):
        raise ValueError("start must be 'beginning' or 'end'")
    flow_path = body.get("flow_path", "")
    if not isinstance(flow_path, str):
        raise _FieldError("flow_path", "flow_path must be a string")
    flow = None
    if flow_path.strip():
        with _field_validation("flow_path"):
            path = _resolve_path(cwd, flow_path.strip())
        try:
            flow = _load_flow_file(path, reload_modules=True)
        except Exception as exc:
            raise _FieldError("flow_path", f"Could not load Flow {path}: {type(exc).__name__}: {exc}") from exc
    options = dict(once=once, recursive=recursive, globs=globs, flow=flow)
    if not once:
        options["start"] = start
    return paths, options


def _load_workspace(self, route, body):
    manager = self.workspace
    if route == "/api/work/reload" and self._opened_archive is not None:
        raise ValueError("Saved archives cannot be reloaded; use Open to load a work file")
    _require_idle_for_load(manager)
    opened = None
    with ExitStack() as cleanup:
        if route == "/api/archive/open":
            with _field_validation("path"):
                raw = body.get("path")
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError("Archive .sqlite3 file path is required")
                target = _resolve_path(self.launch_cwd, raw.strip())
                opened = RunArchive(target)
            replacement = WaveWorkspace()
            cleanup.callback(replacement.close)
            source_path = opened.metadata["source_path"]
            archive_policy = getattr(self, "_archive_policy", None)
            if archive_policy:
                replacement.configure_archive(**archive_policy, override=True)
            response = {"ok": True, "message": f"Opened read-only archive {target.name}."}
        elif route in ("/api/work/load", "/api/work/reload"):
            # Late import avoids the CLI -> web launch import cycle.
            from .cli import load_work_file

            if route == "/api/work/reload":
                if not self.source_path or Path(self.source_path).suffix.lower() != ".py":
                    raise ValueError("Reload requires an active Python work file workspace")
                target = Path(self.source_path)
                replacement = load_work_file(target, reload_modules=True)
            else:
                with _field_validation("path"):
                    raw = body.get("path")
                    if not isinstance(raw, str) or not raw.strip():
                        raise ValueError("Python work file path is required")
                    target = _resolve_path(self.launch_cwd, raw.strip())
                    replacement = load_work_file(target, reload_modules=True)
            cleanup.callback(replacement.close)
            source_path = str(target)
            action = "Start"
        else:
            targets, options = _log_load_options(self.launch_cwd, body)
            replacement = WaveWorkspace()
            cleanup.callback(replacement.close)
            try:
                replacement.add_paths(targets, **options)
            except OSError as exc:
                raise _FieldError("targets", str(exc)) from exc
            if options["once"] and not replacement.jobs:
                raise _FieldError("targets", "No matching log files for static analysis")
            replacement.validate()
            source_path = str(targets[0]) if len(targets) == 1 and targets[0].exists() else None
            action = "Analyze" if options["once"] else "Watch" if replacement.jobs else "Start Watching"
        if opened is None:
            archive_policy = getattr(self, "_archive_policy", None)
            if archive_policy:
                replacement.configure_archive(**archive_policy, override=True)
            replacement.enable_archive(source_path=source_path)
            health = replacement.archive_health
            if not replacement.archive or health.get("error"):
                detail = (health.get("error") or {}).get("message", "archive unavailable")
                raise ValueError(f"Could not enable Archive: {detail}")
            verb = "Reloaded work file" if route == "/api/work/reload" else "Validated"
            response = {"ok": True, "message": f"{verb}. Review the preview, then select {action}."}
        _require_idle_for_load(manager)
        cleanup_error = None
        try:
            manager.close()
        except Exception as exc:
            if not manager.manager._closed:
                raise
            cleanup_error = str(exc) or type(exc).__name__
        # RunArchive records SQLite write and commit failures without raising.
        archive_error = manager.archive.error if manager.archive else None
        if archive_error:
            cleanup_error = archive_error["message"] if cleanup_error is None else cleanup_error
        if cleanup_error:
            response["warning"] = {
                "kind": "previous_workspace_cleanup",
                "message": f"Previous workspace cleanup reported an error: {cleanup_error}",
            }
        with self._workspace_lock:
            self.workspace = replacement
            self._opened_archive = opened
            self.source_path = source_path
            self._workspace_id = uuid.uuid4().hex
        cleanup.pop_all()
    return response


def _start_message(workspace):
    kinds = {job._source.kind if isinstance(job, _FileAttempt) else "process"
             for job in workspace.jobs}
    if not kinds and workspace._watchers:
        return "Watching started"
    if len(kinds) == 1:
        return {
            "tail": "Watching started",
            "file": "Analysis started",
            "reanalysis": "Reanalysis started",
        }.get(next(iter(kinds)), "Dispatch started")
    return "Dispatch started"


def _validate_settings_fields(workspace, body):
    # The core revalidates and publishes the settings together.
    if "max_workers" in body:
        with _field_validation("max_workers"):
            _integer(body["max_workers"], "max_workers", minimum=1)
    if "resources" in body:
        resources = body["resources"]
        capacities = workspace.manager._capacities
        if not isinstance(resources, dict):
            capacities(resources)
        else:
            for name, capacity in resources.items():
                if not name.strip():
                    capacities({name: capacity})
                with _field_validation(f"resources.{name}"):
                    capacities({name: capacity})
    if "max_history" in body:
        with _field_validation("max_history"):
            _integer(body["max_history"], "max_history", minimum=1)
    if "scheduling_mode" in body:
        with _field_validation("scheduling_mode"):
            _scheduling_mode(body["scheduling_mode"])


def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
    # Serialize load/start/control against this server's workspace replacement.
    with self._action_lock:
        if body.get("workspace_id") != self._workspace_id:
            raise ValueError("Workspace changed; refresh before issuing an action")
        manager = self.workspace
        if path == "/api/sources/stop":
            if self._opened_archive is not None:
                raise ValueError("Saved runs are read-only")
            job_id = body.get("job_id")
            with manager.manager._start_lock:
                if job_id is None:
                    readers, watchers = manager._source_roster()
                    kinds = {task.job._source.kind for _, task in readers if task.active}
                    if any(task.active for task in watchers):
                        kinds.add("tail")
                    if not kinds:
                        return {"ok": True, "message": "No active source readers or folder watches."}
                    manager.stop_sources()
                    message = ("Stopping all source readers and watched folders."
                               if "tail" in kinds else "Stopping reading.")
                    return {"ok": True, "message": message}
                if not isinstance(job_id, str) or not job_id.strip():
                    raise ValueError("job_id must be a nonempty string")
                job = manager.get_job(job_id)
                if not isinstance(job, _FileAttempt) or job.id != job_id:
                    raise ValueError("Unknown external source job ID")
                task = manager._source_tasks.get(job_id)
                if task is None or not task.active:
                    return {"ok": True, "message": f"No active source reader for {job.name}."}
                manager.stop_sources(job_id)
                operation = "watching" if job._source.kind == "tail" else "reading"
                return {"ok": True, "message": f"Stopping {operation} {job.name}."}
        if path in ("/api/work/load", "/api/logs/load", "/api/work/reload", "/api/archive/open"):
            return _load_workspace(self, path, body)
        if path == "/api/archive/delete":
            run_id = body.get("run_id")
            if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
                raise ValueError("Invalid run identity")
            active_ids = {manager.run_id}
            if manager.archive is not None:
                active_ids.add(manager.archive.run_id)
            if self._opened_archive is not None:
                active_ids.add(self._opened_archive.run_id)
            if run_id in active_ids:
                raise ValueError("Cannot delete the currently active or opened archive")
            directory = (
                self._opened_archive.path.parent
                if self._opened_archive is not None
                else manager.archive_catalog_dir
            )
            target = find_archive_by_run_id(directory, run_id)
            if target is None or not target.is_file():
                raise LookupError("Saved archive not found")
            store = RunArchive(target)
            if store.run_id != run_id:
                raise LookupError("Saved archive not found")
            if not store.metadata.get("closed_cleanly"):
                raise ValueError("Cannot delete an unclosed archive")
            del store
            remove_archive_files(target)
            return {
                "ok": True,
                "deleted_run_id": run_id,
                "filename": target.name,
                "message": f"Deleted archive {target.name}.",
            }
        if path == "/api/manager/action":
            action = body.get("action")
            actions = {
                "pause": manager.pause,
                "resume": manager.resume,
            }
            if action == "start":
                with manager.manager._start_lock:
                    message = _start_message(manager)
                    manager.start()
                return {"ok": True, "message": message}
            if action not in actions:
                raise ValueError("Unknown manager action")
            if not manager.started:
                raise ValueError("Preview has not started; use Start")
            actions[action]()
            return {
                "ok": True,
                "message": {
                    "pause": "Dispatch paused; running jobs continue",
                    "resume": "Dispatch resumed",
                }[action],
            }
        if path == "/api/settings/save":
            keys = set(body) - {"workspace_id"}
            if not keys or keys - {
                "max_workers",
                "resources",
                "columns",
                "max_history",
                "scheduling_mode",
            }:
                raise ValueError(
                    "Settings accepts max_workers, resources, columns, max_history and scheduling_mode only"
                )
            missing = next((key for key in body if key in keys and body[key] is None), None)
            if missing is not None:
                message = "Settings values cannot be null"
                if missing in ("max_workers", "max_history", "scheduling_mode"):
                    raise _FieldError(missing, message)
                raise ValueError(message)
            _validate_settings_fields(manager, body)

            def apply_preferences():
                _store_columns(manager, _validated_columns(manager, body["columns"]))

            info = manager._apply_settings(
                **{k: body[k] for k in keys - {"columns"}},
                apply_preferences=apply_preferences if "columns" in keys else None,
            )
            return {
                "ok": True,
                "evicted_history": (
                    info.get("evicted_history", 0) if isinstance(info, dict) else 0
                ),
            }
        if path == "/api/job/retention":
            with _field_validation("max_log_lines"):
                raw_lines = _integer(body.get("max_log_lines"), "max_log_lines", minimum=1)
            targets = body.get("targets")
            if not isinstance(targets, list) or not targets or len(targets) > 10000:
                raise ValueError("Provide between 1 and 10000 explicit targets")
            parsed = [_target(t) for t in targets]
            if len({t[0] for t in parsed}) != len(parsed):
                raise ValueError("Duplicate job target")
            results = []
            for jid, attempt, submission in parsed:
                job = manager.get_job(jid)
                item = {
                    "job_id": jid,
                    "attempt_id": attempt,
                    "submission_id": submission,
                    "name": job.name if job else jid,
                    "ok": False,
                    "evicted_lines": 0,
                }
                try:
                    if job is None or job.id != jid:
                        raise ValueError("Unknown job ID")
                    evicted = manager.set_max_log_lines(
                        raw_lines,
                        jid,
                        expected_attempt=attempt,
                        expected_submission=submission,
                    )
                    item["ok"] = True
                    item["evicted_lines"] = evicted
                    item["max_log_lines"] = job.max_log_lines
                    item["message"] = (
                        f"Accepted ({evicted} older line(s) evicted)"
                        if evicted
                        else "Accepted"
                    )
                except (ValueError, TypeError, RuntimeError) as exc:
                    item["message"] = str(exc)
                results.append(item)
            return {
                "ok": True,
                "action": "max_log_lines",
                "max_log_lines": raw_lines,
                "results": results,
                "affected": sum(item["ok"] for item in results),
                "evicted_lines": sum(
                    item.get("evicted_lines", 0) for item in results
                ),
            }
        if path in ("/api/job/action", "/api/job/priority"):
            action = "priority" if path == "/api/job/priority" else body.get("action")
            if path == "/api/job/priority":
                priority = body.get("priority")
                if type(priority) is not int or abs(priority) > 2**53 - 1:
                    raise ValueError("Priority must be a safe integer")
            elif action not in ("stop", "force_stop", "rerun", "send", "interrupt", "signal"):
                raise ValueError("Unknown job action")
            pause_dispatch = body.get("pause_dispatch", False)
            if type(pause_dispatch) is not bool:
                raise ValueError("pause_dispatch must be a boolean")
            if pause_dispatch and action not in ("stop", "force_stop"):
                raise ValueError("pause_dispatch is only supported with stop or force_stop")
            parsed = _parse_targets(body)
            if pause_dispatch and manager.started:
                manager.pause()
            if action == "send":
                if not isinstance(body.get("text"), str):
                    raise ValueError("send requires text")
                newline = body.get("newline", True)
                if type(newline) is not bool:
                    raise ValueError("newline must be a boolean")
            if action == "signal":
                sig_name = body.get("signal")
                if not isinstance(sig_name, str) or sig_name not in _ALLOWED_SIGNALS:
                    raise ValueError("Unsupported or unavailable signal")
            # Cancel queued targets first before running jobs release their slots.
            if action == "stop":
                parsed.sort(
                    key=lambda t: (
                        manager.get_job(t[0]) is None
                        or manager.get_job(t[0]).status.value != "PENDING"
                    )
                )

            def apply_job(job, jid, attempt, submission, item):
                if action == "priority":
                    receipt = manager.set_priority(
                        jid, priority,
                        expected_attempt=attempt,
                        expected_submission=submission,
                    )
                    if receipt.accepted:
                        item["priority"] = priority
                    return receipt
                if action == "rerun":
                    receipt = manager.rerun(
                        jid,
                        expected_attempt=attempt,
                        expected_submission=submission,
                    )
                    if receipt.accepted:
                        item["new_attempt_id"] = job.attempt_id
                        item["new_attempt_number"] = job.attempt_number
                    return receipt
                return manager.control(
                    jid,
                    action,
                    expected_attempt=attempt,
                    expected_submission=submission,
                    text=body.get("text"),
                    secret=body.get("secret", False),
                    newline=body.get("newline", True),
                    sig=_ALLOWED_SIGNALS.get(body.get("signal")),
                )

            results = _run_target_action(manager, parsed, apply_job)
            return {
                "ok": True,
                "action": action,
                **({"priority": priority} if action == "priority" else {}),
                "results": results,
                "affected": sum(item["ok"] for item in results),
            }
        if path == "/api/jobs/hold":
            if self._opened_archive is not None:
                raise ValueError("Saved runs are read-only")
            held = body.get("held")
            if type(held) is not bool:
                raise ValueError("held must be a boolean")
            parsed = _parse_targets(body)

            def apply_hold(job, jid, attempt, submission, item):
                receipt = manager.set_held(
                    jid, held, expected_attempt=attempt, expected_submission=submission
                )
                if receipt.accepted:
                    item["held"] = held
                return receipt

            results = _run_target_action(manager, parsed, apply_hold)
            return {
                "ok": True,
                "action": "hold" if held else "include",
                "held": held,
                "results": results,
                "affected": sum(item["ok"] for item in results),
            }
        if path == "/api/jobs/reorder":
            if self._opened_archive is not None:
                raise ValueError("Saved runs are read-only")
            parsed = _parse_targets(body)
            target_map = {jid: (attempt, submission) for jid, attempt, submission in parsed}
            results = []
            for jid, receipt, value in manager.reorder_queue(parsed):
                job = manager.get_job(jid)
                attempt, submission = target_map[jid]
                results.append(
                    {
                        "job_id": jid,
                        "attempt_id": attempt,
                        "submission_id": submission,
                        "name": job.name if job else jid,
                        "ok": receipt.accepted,
                        "status": receipt.status,
                        "reason": receipt.reason,
                        "message": receipt.reason,
                        **({"priority": value} if value is not None else {}),
                    }
                )
            return {
                "ok": True,
                "action": "reorder",
                "results": results,
                "affected": sum(item["ok"] for item in results),
            }
        if path == "/api/jobs/remove":
            if self._opened_archive is not None:
                raise ValueError("Saved runs are read-only")
            parsed = _parse_targets(body)
            results = _run_target_action(
                manager,
                parsed,
                lambda _job, jid, attempt, submission, _item: manager.remove_job(
                    jid, expected_attempt=attempt, expected_submission=submission
                ),
            )
            return {
                "ok": True,
                "action": "remove",
                "results": results,
                "affected": sum(item["ok"] for item in results),
            }
        if path == "/api/jobs/restore":
            if self._opened_archive is not None:
                raise ValueError("Saved runs are read-only")
            raw_targets = body.get("targets")
            if not isinstance(raw_targets, list) or not raw_targets or len(raw_targets) > 10000:
                raise ValueError("Provide between 1 and 10000 explicit targets")
            parsed_restore = []
            seen_restore = set()
            for raw in raw_targets:
                if not isinstance(raw, dict) or not isinstance(raw.get("job_id"), str) or not raw["job_id"]:
                    raise ValueError("Each target must include a nonempty job_id")
                jid = raw["job_id"]
                if jid in seen_restore:
                    raise ValueError("Duplicate job target")
                seen_restore.add(jid)
                attempt = raw.get("attempt_id")
                submission = raw.get("submission_id")
                if attempt is not None and (not isinstance(attempt, str) or not attempt):
                    raise ValueError("attempt_id must be a nonempty string")
                if submission is not None and (not isinstance(submission, str) or not submission):
                    raise ValueError("submission_id must be a nonempty string")
                parsed_restore.append((jid, attempt, submission))
            removed_names = {j.id: j.name for j in manager.removed_jobs}
            results = []
            for jid, attempt, submission in parsed_restore:
                item = {
                    "job_id": jid,
                    "attempt_id": attempt,
                    "submission_id": submission,
                    "name": removed_names.get(jid, jid),
                    "ok": False,
                }
                try:
                    receipt = manager.restore_job(
                        jid, expected_attempt=attempt, expected_submission=submission
                    )
                    job = manager.get_job(jid)
                    if job is not None:
                        item["name"] = job.name
                    item.update(
                        ok=receipt.accepted,
                        status=receipt.status,
                        reason=receipt.reason,
                        message=receipt.reason,
                    )
                except (ValueError, TypeError, RuntimeError) as exc:
                    item.update(status="rejected", reason=str(exc), message=str(exc))
                results.append(item)
            return {
                "ok": True,
                "action": "restore",
                "results": results,
                "affected": sum(item["ok"] for item in results),
            }
        if path == "/api/jobs/requeue":
            if self._opened_archive is not None:
                raise ValueError("Saved runs are read-only")
            parsed = _parse_targets(body)
            order = body.get("order")
            held = body.get("held", False)
            if type(held) is not bool:
                raise ValueError("held must be a boolean")
            order_specs = None
            if order is not None:
                order_specs = _parse_targets(body, field="order")
            if not manager.started:
                raise ValueError("Preview jobs are already queued")
            core = manager.manager
            with core._start_lock:
                if not manager.paused:
                    with core._cond:
                        active_work = (
                            core._active_workers > 0
                            or any(j._attempt.reserved or j.status.is_active for j in core.jobs)
                        )
                    if not active_work:
                        manager.pause()
                with core._cond:
                    if core._closed or core._closing:
                        raise ValueError("Manager is closing or closed")
                    deadline = time.monotonic() + 0.5
                    while (
                        core._threads
                        and core._active_workers == 0
                        and not any(j._attempt.reserved or j.status.is_active for j in core.jobs)
                    ):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        core._cond.wait(remaining)
                    if (
                        core._active_workers > 0
                        or core._threads
                        or any(j._attempt.reserved or j.status.is_active for j in core.jobs)
                    ):
                        raise ValueError("Wait for running jobs to stop before re-queuing")
                if not manager.paused:
                    manager.pause()
            requeued_ids: set[str] = set()

            def apply_requeue(job, jid, attempt, submission, item):
                receipt = manager.rerun(
                    jid, expected_attempt=attempt, expected_submission=submission
                )
                if receipt.accepted:
                    requeued_ids.add(jid)
                    item["new_attempt_id"] = job.attempt_id
                    item["new_attempt_number"] = job.attempt_number
                    item["new_submission_id"] = job.submission_id
                    if held:
                        hold_receipt = manager.set_held(
                            jid,
                            True,
                            expected_attempt=job.attempt_id,
                            expected_submission=job.submission_id,
                        )
                        item["held"] = bool(hold_receipt.accepted)
                return receipt

            results = _run_target_action(manager, parsed, apply_requeue)
            reorder_results = []
            if order_specs is not None:
                resolved_order = [
                    (jid, None, None) if jid in requeued_ids else (jid, attempt, submission)
                    for jid, attempt, submission in order_specs
                ]
                for jid, r_receipt, value in manager.reorder_queue(resolved_order):
                    reorder_results.append(
                        {
                            "job_id": jid,
                            "ok": r_receipt.accepted,
                            "status": r_receipt.status,
                            "reason": r_receipt.reason,
                            **({"priority": value} if value is not None else {}),
                        }
                    )
            return {
                "ok": True,
                "action": "requeue",
                "paused": manager.paused,
                "results": results,
                "affected": sum(item["ok"] for item in results),
                **({"reorder_results": reorder_results} if reorder_results else {}),
            }
        if path == "/api/chart/file":
            action = body.get("action")
            if action not in ("save_rc", "read_rc", "export_vcd"):
                raise ValueError("Unsupported chart file action")
            raw_path = body.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise _FieldError("path", "Provide a file path")
            with _field_validation("path"):
                target = _resolve_path(self.launch_cwd, raw_path.strip())
            if action == "read_rc":
                if not target.is_file():
                    raise _FieldError("path", "Configuration file not found")
                if target.stat().st_size > 524288:
                    raise _FieldError("path", "Configuration file exceeds 512 KB limit")
                return {
                    "ok": True,
                    "action": "read_rc",
                    "path": str(target),
                    "content": target.read_text(encoding="utf-8"),
                }
            if action == "save_rc":
                if target.suffix.lower() not in (".rc", ".json"):
                    target = target.with_name(target.name + ".wave.rc")
                content = body.get("content")
                if not isinstance(content, str) or len(content) > 524288:
                    raise ValueError("Invalid waveform configuration payload")
                if not target.parent.is_dir():
                    raise _FieldError("path", "Target folder does not exist")
                target.write_text(content, encoding="utf-8")
                return {"ok": True, "action": "save_rc", "path": str(target)}
            if target.suffix.lower() != ".vcd":
                target = target.with_name(target.name + ".vcd")
            if not target.parent.is_dir():
                raise _FieldError("path", "Target folder does not exist")
            current = manager.archive or self._opened_archive
            run_id = body.get("run_id") or (current.run_id if current else manager.run_id)
            if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
                raise ValueError("Invalid run identity")
            opened_hist = False
            if current and run_id == current.run_id:
                store = current
            else:
                directory = (
                    self._opened_archive.path.parent
                    if self._opened_archive is not None
                    else manager.archive_catalog_dir
                )
                hist = find_archive_by_run_id(directory, run_id) or (directory / f"{run_id}.sqlite3")
                if not hist.is_file():
                    raise LookupError("Saved archive not found")
                store = RunArchive(hist)
                opened_hist = True
            try:
                if opened_hist and store.metadata["source_path"] != self.source_path:
                    raise ValueError("Archive belongs to a different work file")
                job_id, attempt_id, view_id = body.get("job_id"), body.get("attempt_id"), body.get("view_id")
                if not all(isinstance(v, str) and v for v in (job_id, attempt_id, view_id)):
                    raise ValueError("job_id, attempt_id and view_id are required")
                as_of_seq = body.get("as_of_seq")
                if as_of_seq is not None and type(as_of_seq) is not int:
                    raise ValueError("as_of_seq must be an integer")
                vcd_text = store.export_vcd(
                    job_id, attempt_id, view_id=view_id, as_of_seq=as_of_seq,
                    series=body.get("series"), axis=body.get("axis", "time"),
                )
            finally:
                if opened_hist:
                    store.close()
            target.write_text(vcd_text, encoding="utf-8")
            return {
                "ok": True,
                "action": "export_vcd",
                "path": str(target),
                "bytes": len(vcd_text.encode("utf-8")),
            }
        raise LookupError("Not found")


def make_handler(owner):
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            # Bound every socket read/write so a stalled client cannot pin a
            # handler thread (Slowloris) or hang rfile.read() forever.
            self.timeout = _SOCKET_TIMEOUT_SECONDS
            super().setup()

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _headers(self, status: int, content_type: str, size: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()

        def _json(self, payload: Any, status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
            try:
                self._headers(status, "application/json; charset=utf-8", len(raw))
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Navigation and cancelled polling can close the client mid-response.
                # An action may already have completed; do not send a second response.
                return

        def _forbidden(self, message: str) -> None:
            if self.command == "POST" and not self.headers.get("Transfer-Encoding"):
                lengths = self.headers.get_all("Content-Length", [])
                try:
                    remaining = int(lengths[0]) if len(lengths) == 1 else 0
                except ValueError:
                    remaining = 0
                if 0 < remaining <= _MAX_POST_BYTES:
                    # Closing with unread TCP data can reset the connection
                    # before the client receives the denial.
                    deadline = time.monotonic() + _REJECT_DRAIN_SECONDS
                    timeout = self.connection.gettimeout()
                    try:
                        while remaining:
                            wait = deadline - time.monotonic()
                            if wait <= 0:
                                break
                            self.connection.settimeout(wait)
                            chunk = self.rfile.read1(min(remaining, 65536))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                    except OSError:
                        pass
                    finally:
                        self.connection.settimeout(timeout)
            self._json({"error": message}, 403)

        def _local_request(self) -> bool:
            host = self.headers.get("Host")
            # SSH -L forwards remote:<port> to a different local port, so the
            # port is not pinned to owner.port; only loopback names are valid.
            if not _loopback_host(host):
                self._forbidden("Invalid local Host")
                return False
            origin = self.headers.get("Origin")
            if (origin and origin != "http://" + host) or self.headers.get(
                "Sec-Fetch-Site"
            ) == "cross-site":
                self._forbidden("Cross-origin requests are not allowed")
                return False
            return True

        def _authorized(self, query=None) -> bool:
            values = (self.headers.get_all("X-Wave-Token", [])
                      if query is None else query.get("token", []))
            if len(values) == 1 and secrets.compare_digest(
                values[0].encode("utf-8"), owner._token.encode("ascii")
            ):
                return True
            self._forbidden("Invalid or missing Wave token")
            return False

        def do_GET(self) -> None:
            if not self._local_request():
                return
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path.startswith("/api/") and not self._authorized():
                return
            try:
                if parsed.path in _STATIC_ASSETS:
                    asset_path, content_type = _STATIC_ASSETS[parsed.path]
                    is_html = content_type.startswith("text/html")
                    if is_html and not self._authorized(query):
                        return
                    raw = asset_path.read_bytes()
                    if is_html and owner._opened_archive is not None:
                        raw = raw.replace(b"<body>", b'<body class="archive-only">', 1)
                    self._headers(200, content_type, len(raw))
                    self.wfile.write(raw)
                elif parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                elif parsed.path in ("/history.js", "/analysis.js", "/reading.js", "/jobs.js", "/flow.js", "/icons.js", "/charts.js"):
                    raw = Path(__file__).with_name(parsed.path[1:]).read_bytes()
                    self._headers(200, "text/javascript; charset=utf-8", len(raw))
                    self.wfile.write(raw)
                elif parsed.path == "/api/state":
                    raw_after = query.get("log_after", [None])[0]
                    if raw_after is not None and (
                        not raw_after.isascii()
                        or not raw_after.isdecimal()
                        or len(raw_after) > 18
                    ):
                        raise ValueError("log_after must be a nonnegative integer")
                    self._json(
                        owner._state(
                            query.get("job_id", [None])[0],
                            query.get("attempt_id", [None])[0],
                            log_after=int(raw_after)
                            if raw_after is not None
                            else None,
                            log_attempt=query.get("log_attempt", [None])[0],
                        )
                    )
                elif parsed.path == "/api/logs":
                    state = owner._state(
                        query.get("job_id", [None])[0],
                        query.get("attempt_id", [None])[0],
                    )
                    if state["detail"] is None:
                        self._json({"error": "Job or attempt not found"}, 404)
                    else:
                        self._json(
                            {
                                "job_id": state["detail"]["id"],
                                "attempt_id": state["detail"]["attempt_id"],
                                "records": state["detail"]["log_records"],
                            }
                        )
                elif parsed.path == "/api/archive":
                    self._json(_archive_query(owner, query))
                elif parsed.path == "/api/files":
                    with owner._workspace_lock:
                        opened = owner._opened_archive
                        workspace = owner.workspace
                        source_path = owner.source_path
                    try:
                        arch_dir = (
                            opened.path.parent
                            if opened is not None
                            else workspace.archive_catalog_dir
                        )
                    except (OSError, RuntimeError):
                        arch_dir = None
                    self._json(_browse_files(
                        owner.launch_cwd,
                        query,
                        archive_dir=arch_dir,
                        source_path=source_path,
                    ))
                else:
                    self._json({"error": "Not found"}, 404)
            except _FieldError as exc:
                self._json({"error": str(exc), "field": exc.field}, 400)
            except LookupError as exc:
                self._json({"error": str(exc)}, 404)
            except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
                self._json({"error": str(exc)}, 400)
            except RuntimeError as exc:
                self._json({"error": str(exc) or "Internal error"}, 500)

        def do_POST(self) -> None:
            if not self._local_request() or not self._authorized():
                return
            if (
                self.headers.get("Content-Type", "").split(";")[0].strip()
                != "application/json"
            ):
                self._json({"error": "Content-Type must be application/json"}, 415)
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) > 1 or (lengths and not (
                    lengths[0].isascii() and lengths[0].isdecimal()
                )):
                    raise ValueError("Invalid Content-Length")
                size = int(lengths[0]) if lengths else 0
                if not 0 < size <= _MAX_POST_BYTES or self.headers.get(
                    "Transfer-Encoding"
                ):
                    raise ValueError("Invalid request body size")
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("JSON body must be an object")
                response = _post(owner, urllib.parse.urlsplit(self.path).path, body)
                self._json(response)
            except _FieldError as exc:
                self._json({"error": str(exc), "field": exc.field}, 400)
            except LookupError as exc:
                self._json({"error": str(exc)}, 404)
            except (ValueError, TypeError, RuntimeError, OSError, sqlite3.Error) as exc:
                self._json({"error": str(exc)}, 400)

    return Handler
