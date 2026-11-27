"""Wave entry point: load a Python work file, preview it, or run a batch."""

from __future__ import annotations

import argparse
import math
import sys
import threading
import traceback
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Sequence

from rpkbin import __version__
from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, Parser
from rpkbin.jobcore import Job, JobManager
from ._loader import run_definition_script
from .archive import RunArchive
from .sources import ReanalysisSource, _load_flow_file, _stop_on_interrupt
from rpkbin.jobcore.manager import definition_scope

from .workspace import WaveWorkspace, safe_print, terminate_as_interrupt


def load_work_file(target: str | Path, *, reload_modules: bool = False) -> WaveWorkspace:
    """Execute a trusted Python definition without dispatching Wave jobs.

    The file runs with normal Python privileges; definition_scope only prevents
    Wave start/resume. Its directory does not become the process working directory.
    """
    path = Path(target).expanduser().absolute()
    if not path.exists():
        raise FileNotFoundError(f"Work file not found: {path}")
    if not path.is_file() or path.suffix.lower() != ".py":
        raise ValueError("A work file must be a Python (.py) file")
    try:
        with definition_scope():
            namespace = run_definition_script(path, reload_modules=reload_modules)
    except SystemExit as exc:
        raise ValueError(
            f"Work file called sys.exit({exc.code!r}); define jobs without running them"
        ) from exc
    except Exception as exc:
        if isinstance(exc, SyntaxError):
            filename, line, message = exc.filename or str(path), exc.lineno, exc.msg
        else:
            frames = traceback.extract_tb(exc.__traceback__)
            frame = next(
                (frame for frame in reversed(frames) if frame.filename == str(path)),
                frames[-1],
            )
            filename, line, message = frame.filename, frame.lineno, str(exc)
        location = f"{filename}:{line}" if line else filename
        raise ValueError(f"{location}: {type(exc).__name__}: {message}") from exc
    workspaces = {id(value): value for value in namespace.values()
                  if isinstance(value, WaveWorkspace)}
    owned = {id(workspace.manager) for workspace in workspaces.values()}
    bare = {id(value): value for value in namespace.values()
            if isinstance(value, JobManager) and id(value) not in owned}
    created = [*workspaces.values(), *bare.values()]
    try:
        if len(workspaces) + len(bare) != 1:
            raise ValueError(
                "Work file must expose one unambiguous WaveWorkspace or JobManager"
            )
        workspace = next(iter(workspaces.values())) if workspaces else WaveWorkspace(next(iter(bare.values())))
        created = [workspace]
        if not workspace.jobs:
            raise ValueError("Work file contains no jobs")
        workspace.validate()
    except BaseException:
        # The caller never receives these objects; release their archives and threads.
        for value in created:
            try:
                value.close()
            except Exception:
                pass
        raise
    return workspace


def build_demo_workspace() -> WaveWorkspace:
    """Build three finite, generic example commands; do not start them."""
    manager = WaveWorkspace(JobManager(max_workers=2, resources={"slot": 2}), columns=["progress"])

    def parse_progress(line, ctx):
        if line.startswith("progress="):
            return {"progress": int(line.split("=", 1)[1])}

    def parse_completion(line, ctx):
        if line == "COMPLETE":
            return {"completed": True}

    def finish(end, ctx):
        if not end.complete:
            return
        ctx.summary.result("PASS" if ctx.state.get("completed") else "FAIL",
                   "Completion marker received" if ctx.state.get("completed") else "Completion marker missing",
                   final=True, evidence=ctx.state.get("completion_evidence"))
        return AnalysisReport("completion", result="PASS" if ctx.state.get("completed") else "MISSING",
                              reason="Completion marker received" if ctx.state.get("completed") else "Completion marker missing",
                              evidence=ctx.state.get("completion_evidence"))

    def analyze_completion(values, ctx):
        ctx.state["completed"] = True
        ctx.state["completion_evidence"] = ctx.source["evidence"]

    for index in range(3):
        def analyze_progress(values, ctx, midpoint=index == 1):
            ctx.state["samples"] = ctx.state.get("samples", 0) + 1
            reports = [AnalysisReport("samples", values={"sample_count": ctx.state["samples"]})]
            if midpoint and values["progress"] == 50:
                ctx.summary.issue("midpoint", level="warning", message="Review midpoint")
                ctx.summary.event("midpoint", level="warning", message="Review midpoint")
                reports.append(AnalysisReport("midpoint", result="REVIEW", reason="Review midpoint"))
            return reports

        progress = Parser("progress", parse_progress, outputs={"progress": {"label": "Progress", "unit": "%"}})
        completion = Parser("completion", parse_completion)
        flow = Flow("progress_monitor", parsers=(progress, completion), analyzers=(Analyzer(
            "completion_check", records=(Binding(progress, analyze_progress), Binding(completion, analyze_completion)),
            on_finish=finish),))
        code = (
            "import time\n"
            "for value in (0, 50, 100):\n"
            "    print(f'progress={value}', flush=True)\n"
            "    time.sleep(0.3)\n"
            "print('COMPLETE', flush=True)\n"
        )
        job = Job(
            f"worker_{index + 1}",
            [sys.executable, "-u", "-c", code],
            resources={"slot": 1},
        )
        manager.add(job, flow=flow)
    return manager


def _summary(manager: WaveWorkspace, jobs: list[dict], *, source_mode: bool) -> None:
    safe_print("\n[Wave] Final summary", flush=True)
    archive = manager.archive_health
    disposition = archive.get("disposition")
    if not source_mode and archive.get("error"):
        message = archive['error']['message'].encode("ascii", errors="backslashreplace").decode("ascii")
        safe_print(f"[Wave] History save failed: {message}", flush=True)
    elif not source_mode and archive.get("enabled"):
        if disposition in ("discarded_ephemeral", "discarded_pass", "discarded_unstarted"):
            reason = "no-save" if disposition == "discarded_ephemeral" else "keep-on-fail"
            safe_print(f"[Wave] History discarded ({reason})", flush=True)
        else:
            safe_print(f"[Wave] History saved: run {archive['run_id']}", flush=True)
    safe_print(f"{'JOB':<26} {'STATE':<15} {'RESULT':<12} {'WARNINGS':<9} EXIT", flush=True)
    for job in jobs:
        state = job.get("status", job.get("state", "?"))
        returncode = job.get("returncode")
        safe_print(
            f"{job['name']:<26} {state:<15} {job.get('result') or '—':<12} "
            f"{job.get('warning_count', 0):<9} {returncode if returncode is not None else '—'}",
            flush=True,
        )
        if source_mode:
            label = job.get("origin_label")
            reason = job.get("completion_reason")
            if label or reason:
                safe_print("  " + " ".join(part for part in (label, f"({reason})" if reason else None) if part),
                      flush=True)
        if job.get("flow_name"):
            flow = manager.get_job(job["id"]).to_snapshot_dict(include_details=True)["flow"]
            for analyzer in flow["analyzers"]:
                safe_print(f"  analysis {analyzer['name']}: {analyzer['result'] or 'no verdict'}; {analyzer['reason']}", flush=True)
                for issue in analyzer["issues"]:
                    safe_print(f"  {issue['level']} {issue['name']}: {issue['message']}", flush=True)
            for diagnostic in flow["diagnostics"]:
                safe_print(f"  diagnostic {diagnostic['producer']}: {diagnostic['data'].get('message', '')}", flush=True)
        log_error = job.get("log_file_error")
        if log_error:
            safe_print(
                f"  log save failed: {log_error['path']}: {log_error['message']} "
                f"({log_error['count']} record(s) not saved)",
                flush=True,
            )
        if job.get("attempt_number", 1) > 1:
            safe_print(
                f"  latest attempt: #{job['attempt_number']}; earlier history retained: {len(job.get('history', []))}",
                flush=True,
            )
        for error in job.get("errors", []):
            safe_print(f"  error: {error.get('message', error)}", flush=True)
        for reason in job.get("termination", []):
            safe_print(
                f"  stopped: {reason.get('source', '?')}: {reason.get('reason', '')}",
                flush=True,
            )


def run_headless(manager: WaveWorkspace, *, timeout: float | None = None,
                 source_mode: bool = False) -> int:
    """Run a validated batch, stream output, drain cleanup, and return its outcome."""
    if not manager.jobs and not manager._watchers:
        raise ValueError("Headless execution requires at least one job")
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be a finite positive number")
    output_lock = threading.Lock()

    def stream(record: dict) -> None:
        origin = "child" if record["source"] == "stdout" else "Wave"
        line = f"[{origin} {record['job_name']} #{record['attempt_number']}] {record['text']}"
        with output_lock:
            safe_print(line, flush=True)

    interrupted = False
    timed_out = False
    dispatch_complete = False
    try:
        manager.validate()
        if not manager.started:
            manager.enable_archive()
        if source_mode and not manager.archive:
            raise OSError(f"Cannot initialize archive: {manager.archive_health['error']}")
        manager.add_log_listener(stream)
        safe_print(
            f"[Wave] Starting {len(manager.jobs)} job(s); workers={manager.max_workers}",
            flush=True,
        )
        # Finish starting reader threads before handling a console Ctrl+C.
        startup_interrupt = threading.Event() if source_mode else None
        with _stop_on_interrupt(startup_interrupt) if startup_interrupt is not None else nullcontext():
            manager.start()
        dispatch_complete = True
        if startup_interrupt is not None and startup_interrupt.is_set():
            raise KeyboardInterrupt
        if not manager._drain(timeout):
            timed_out = True
            safe_print(
                "[Wave] Batch timeout; stopping remaining jobs and waiting for cleanup",
                flush=True,
            )
            manager.stop_sources()
            manager.stop_all(source="timeout", reason="Headless batch timeout")
            manager._drain()
    except KeyboardInterrupt:
        interrupted = True
        safe_print("\n[Wave] Interrupted; stopping jobs and waiting for cleanup", flush=True)
        try:
            manager.stop_sources()
            manager.stop_all(source="user", reason="Terminal Ctrl+C")
            if dispatch_complete:
                manager._drain()
        except KeyboardInterrupt:
            safe_print(
                "\n[Wave] Second interrupt; forcing remaining jobs to stop", flush=True
            )
            manager.stop_sources()
            manager.stop_all(force=True, source="user", reason="Second terminal Ctrl+C")
            if dispatch_complete:
                manager._drain()
    finally:
        manager.close()
    jobs = manager.snapshot()["jobs"]
    _summary(manager, jobs, source_mode=source_mode)
    if source_mode:
        return _source_outcome(manager, jobs, interrupted=interrupted, timed_out=timed_out)
    if interrupted:
        return 130
    failed = any(job["result"] not in ("PASS", "N/A") for job in jobs)
    return 1 if failed or timed_out else 0


def _source_errors(manager, job):
    detail = manager.get_job(job["id"]).to_snapshot_dict(include_details=True)
    reason = job["completion_reason"]
    messages = [error["message"] for error in detail["errors"]]
    messages.extend(item["message"] for item in detail["diagnostics"] if item["source"] == reason)
    flow = detail.get("flow") or {}
    for stage in ("parsers", "analyzers", "automations"):
        messages.extend(f"{step['name']}: {step['error']}" for step in flow.get(stage, ()) if step.get("error"))
    if flow.get("aggregation_error"):
        messages.append(flow["aggregation_error"])
    for message in dict.fromkeys(messages or [f"{reason} (result {job['result']})"]):
        safe_print(f"[Wave] {job['name']} ({reason}): {message[:2048]}", file=sys.stderr, flush=True)


def _archive_failure(workspace):
    health = workspace.archive_health
    error = health.get("error")
    if error or not health.get("closed_cleanly"):
        message = error["message"] if error else "Archive did not close cleanly"
        path = health.get("path") or (error or {}).get("path", "unknown path")
        safe_print(f"[Wave] Archive save failed ({path}): {message}", file=sys.stderr, flush=True)
        return True
    return False


def _source_outcome(manager, jobs, *, interrupted, timed_out):
    if _archive_failure(manager):
        return 130 if interrupted else 2
    disposition = manager.archive_health.get("disposition")
    if disposition in ("discarded_ephemeral", "discarded_pass", "discarded_unstarted"):
        reason = "no-save" if disposition == "discarded_ephemeral" else "keep-on-fail"
        safe_print(f"[Wave] Archive discarded ({reason})", flush=True)
    else:
        safe_print(f"[Wave] Archive: {manager.archive.path}", flush=True)
    errors = [job for job in jobs if job["result"] == "ERROR" or job["error_count"] > 0
              or job["completion_reason"] in ("file_io", "line_too_long", "output_decode",
                                              "invalid_final_offset", "unverified_source", "source_reader")]
    for job in errors:
        _source_errors(manager, job)
    if interrupted or (not timed_out and any(job["completion_reason"] == "user_stopped" for job in jobs)):
        return 130
    if errors:
        return 2
    if timed_out or any(job["status"] != "DONE" or job["result"] == "INCOMPLETE"
                        or job["completion_reason"] in ("idle_timeout", "file_changed", "discontinuity",
                                                        "source_incomplete") for job in jobs):
        return 1
    return 3 if any(job["result"] == "FAIL" for job in jobs) else 0


_TAIL_OPTIONS = ("start", "poll_interval", "commit_interval", "rotation_grace", "idle_timeout",
                 "completion_file", "end_marker", "final_offset")


def _add_archive_args(parser: argparse.ArgumentParser, *, include_archive_dir: bool = True) -> None:
    archive_group = parser.add_argument_group("archive and scratch options")
    if include_archive_dir:
        archive_group.add_argument(
            "--archive-dir",
            help="Directory for saved SQLite archives (default: local user state dir, or RPK_WAVE_ARCHIVE_DIR)",
        )
    archive_group.add_argument(
        "--archive-mode",
        choices=("persist", "keep-on-fail", "ephemeral"),
        help="Archive retention mode: persist (default), keep-on-fail, or ephemeral",
    )
    archive_group.add_argument(
        "--no-save",
        action="store_true",
        help="Do not keep the SQLite archive after Wave exits (alias for --archive-mode ephemeral)",
    )
    archive_group.add_argument(
        "--temp-dir",
        help="Scratch directory used during ephemeral / keep-on-fail runs or NFS staging (default: system temp dir, or RPK_WAVE_TEMP_DIR)",
    )
    archive_group.add_argument(
        "--retention-days",
        type=float,
        help="Auto-delete closed archives in archive_dir older than N days",
    )
    archive_group.add_argument(
        "--pass-retention-days",
        type=float,
        help="Auto-delete closed PASS archives older than N days (overrides --retention-days)",
    )
    archive_group.add_argument(
        "--fail-retention-days",
        type=float,
        help="Auto-delete closed FAIL/incomplete archives older than N days (overrides --retention-days)",
    )


def _cli_archive_policy(parser: argparse.ArgumentParser, args) -> dict:
    if args.no_save and args.archive_mode not in (None, "ephemeral"):
        parser.error("--no-save cannot be combined with --archive-mode persist or keep-on-fail")
    for flag, attr in (
        ("--retention-days", "retention_days"),
        ("--pass-retention-days", "pass_retention_days"),
        ("--fail-retention-days", "fail_retention_days"),
    ):
        val = getattr(args, attr, None)
        if val is not None and (not math.isfinite(val) or val < 0):
            parser.error(f"{flag} must be a non-negative finite number")
    policy = {}
    if getattr(args, "archive_dir", None) is not None:
        if not str(args.archive_dir).strip():
            parser.error("--archive-dir must not be empty")
        policy["archive_dir"] = args.archive_dir
    if args.no_save:
        policy["archive_mode"] = "ephemeral"
    elif args.archive_mode is not None:
        policy["archive_mode"] = args.archive_mode
    if getattr(args, "temp_dir", None) is not None:
        if not str(args.temp_dir).strip():
            parser.error("--temp-dir must not be empty")
        policy["temp_dir"] = args.temp_dir
    for attr in ("retention_days", "pass_retention_days", "fail_retention_days"):
        val = getattr(args, attr, None)
        if val is not None:
            policy[attr] = val
    return policy


def _source_parser(command):
    parser = argparse.ArgumentParser(
        prog=f"rpk-wave {command}",
        description="Replay saved attempts into a new archive." if command == "reanalyze"
        else "Open log files and directories. Follow updates unless --once is selected.",
        epilog="Opens a GUI preview by default. Use --headless to start immediately in the terminal.",
    )
    parser.add_argument("--flow", required=command == "reanalyze", type=Path,
                        help="Trusted Python file exposing a Flow as 'flow'")
    parser.add_argument("--archive-dir", type=str,
                        help="Directory for the new SQLite archive")
    _add_archive_args(parser, include_archive_dir=False)
    parser.add_argument("--headless", action="store_true", help="Run, stream output and exit")
    parser.add_argument("--timeout", type=float, help="Headless batch deadline in seconds")
    parser.add_argument("--host", default="127.0.0.1", help="Local GUI bind address (default: 127.0.0.1)")
    parser.add_argument("-p", "--port", type=int, default=0, help="GUI port; 0 chooses a free port")
    parser.add_argument("--open", action="store_true", help="Open the GUI automatically in a web browser")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically (default)")
    if command == "reanalyze":
        parser.add_argument("archive", type=Path, help="Input SQLite archive")
        parser.add_argument("--attempt", "--run", dest="attempt",
                            help="Saved attempt ID; defaults to the latest finalized attempt per job")
        return parser

    parser.add_argument("targets", metavar="TARGET", nargs="+", type=Path, help="Log file or directory")
    parser.add_argument("--once", action="store_true", help="Read existing files once and stop")
    parser.add_argument("--glob", action="append", metavar="PATTERN", help="Directory filter (default: *.log)")
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan directory subtrees")
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--decode-errors", choices=("replace", "strict"), default="replace")
    live = parser.add_argument_group("live log options")
    # Suppress absent options so FileSource receives no tail-only arguments.
    absent = argparse.SUPPRESS
    live.add_argument("--start", choices=("beginning", "end"), default=absent,
                      help="Read existing files from this position (default: beginning)")
    live.add_argument("--poll-interval", type=float, default=absent, help="Poll every N seconds (default: 0.1)")
    live.add_argument("--commit-interval", type=float, default=absent,
                      help="Commit archive updates every N seconds (default: 1.0)")
    live.add_argument("--rotation-grace", type=float, default=absent,
                      help="Retain rotated files for N seconds (default: 3.0)")
    live.add_argument("--idle-timeout", type=float, default=absent)
    completion = live.add_mutually_exclusive_group()
    completion.add_argument("--completion-file", type=Path, default=absent)
    completion.add_argument("--end-marker", default=absent)
    completion.add_argument("--final-offset", type=int, default=absent)
    return parser


def _latest_attempt_ids(store):
    latest = {}
    before = None
    while True:
        page = store.attempts(before=before)
        for row in page["rows"]:
            previous = latest.get(row["id"])
            if row["finalized"] and (previous is None or row["attempt_number"] > previous["attempt_number"]):
                latest[row["id"]] = row
        if not page["has_more"]:
            return [row["attempt_id"] for row in latest.values()]
        before = page["before"]


def _source_cli(command: str, argv: Sequence[str]) -> int:
    parser = _source_parser(command)
    args = parser.parse_args(argv)
    if args.open and args.no_browser:
        parser.error("--open and --no-browser cannot be combined")
    if args.timeout is not None and (not args.headless or not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error("--timeout requires --headless and a finite positive number")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.host not in ("127.0.0.1", "localhost"):
        parser.error("Wave GUI binds only to loopback (127.0.0.1 or localhost)")
    options = {key: vars(args)[key] for key in _TAIL_OPTIONS if key in vars(args)}
    if command == "logs" and args.once and options:
        flags = ", ".join("--" + key.replace("_", "-") for key in options)
        parser.error(f"--once cannot be combined with {flags}")
    policy = _cli_archive_policy(parser, args)

    try:
        flow = _load_flow_file(args.flow) if args.flow is not None else None
        workspace = WaveWorkspace()
        cleanup_here = True
        try:
            if command == "logs":
                targets = [path.expanduser().resolve() for path in args.targets]
                if ("completion_file" in options or "final_offset" in options) and (
                    len(targets) != 1 or targets[0].is_dir()
                ):
                    parser.error("--completion-file and --final-offset require one explicit log file")
                workspace.add_paths(targets, once=args.once, globs=args.glob or ("*.log",),
                                    recursive=args.recursive, flow=flow, encoding=args.encoding,
                                    decode_errors=args.decode_errors, **options)
                if args.once and not workspace.jobs:
                    raise ValueError("No matching log files for --once")
                source_path = targets[0] if len(targets) == 1 and targets[0].exists() else None
            else:
                source_path = args.archive.expanduser().resolve()
                if args.attempt is None:
                    with closing(RunArchive(source_path)) as store:
                        attempts = _latest_attempt_ids(store)
                    if not attempts:
                        raise ValueError("No finalized attempts to reanalyze in this archive")
                else:
                    attempts = [args.attempt]
                for attempt_id in attempts:
                    replay = ReanalysisSource(source_path, attempt_id, flow_path=args.flow)
                    workspace.add_source(replay, flow=flow, name=replay.attempt_name)
            workspace.validate()
            if policy:
                workspace.configure_archive(**policy, override=True)
            workspace.enable_archive(source_path=source_path)
            if not workspace.archive or workspace.archive_health.get("error"):
                _archive_failure(workspace)
                return 2

            if args.headless:
                cleanup_here = False  # run_headless closes before reporting the archive outcome.
                return run_headless(workspace, timeout=args.timeout, source_mode=True)
            from .web import launch

            launch(workspace, host=args.host, port=args.port,
                   open_browser=bool(args.open) and not args.no_browser,
                   block=True, source_path=source_path)
        finally:
            if cleanup_here:
                workspace.close()
        return 2 if _archive_failure(workspace) else 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        safe_print(f"[Wave] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    # SIGTERM/SIGHUP (service stop, closed ssh session) shut down like Ctrl+C.
    with terminate_as_interrupt():
        return _main(argv)


def _main(argv: Sequence[str] | None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("logs", "reanalyze"):
        return _source_cli(argv[0], argv[1:])
    parser = argparse.ArgumentParser(
        prog="rpk-wave",
        description="Wave: Python-defined jobs, local GUI or headless batch execution.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="rpk-wave logs TARGET [TARGET ...] [--once] [--flow FLOW.py]\n"
               "rpk-wave reanalyze ARCHIVE --flow FLOW.py [--attempt ATTEMPT_ID]\n"
               "Both open a GUI preview by default; use --headless for terminal execution.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Trusted Python (.py) work file; GUI loads it for preview",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Local GUI bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "-p", "--port", type=int, default=0, help="GUI port; 0 chooses a free port"
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the GUI automatically in a web browser",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically (default)",
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run the batch, stream output and exit"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Headless batch deadline in seconds; cleanup may take longer",
    )
    parser.add_argument(
        "-j",
        "--max-workers",
        type=int,
        help="Override concurrency before any job starts",
    )
    parser.add_argument(
        "--demo", action="store_true", help="Load finite generic demonstration jobs"
    )
    parser.add_argument("--archive", help="Open a saved Wave SQLite run read-only, without executing its Python work file")
    _add_archive_args(parser, include_archive_dir=True)
    args = parser.parse_args(argv)
    policy = _cli_archive_policy(parser, args)
    if args.open and args.no_browser:
        parser.error("--open and --no-browser cannot be combined")
    if args.archive and (args.target or args.demo or args.headless or args.max_workers is not None):
        parser.error("--archive cannot be combined with a work file, --demo, --headless or --max-workers")
    if args.archive and policy:
        parser.error("--archive cannot be combined with archive write options")
    if args.demo and args.target:
        parser.error("--demo and a work file cannot be combined")
    if args.headless and not (args.target or args.demo):
        parser.error("--headless requires a Python work file or --demo")
    if args.timeout is not None and (
        not args.headless or not math.isfinite(args.timeout) or args.timeout <= 0
    ):
        parser.error("--timeout requires --headless and a finite positive number")
    if args.max_workers is not None and args.max_workers < 1:
        parser.error("--max-workers must be at least 1")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.host not in ("127.0.0.1", "localhost"):
        parser.error("Wave GUI binds only to loopback (127.0.0.1 or localhost)")
    manager = None
    try:
        manager = (
            build_demo_workspace()
            if args.demo
            else load_work_file(args.target)
            if args.target
            else WaveWorkspace()
        )
        if args.max_workers is not None:
            manager.update_settings(max_workers=args.max_workers)
        if not args.archive:
            if policy:
                manager.configure_archive(**policy, override=True)
            manager.enable_archive(source_path=args.target)
        if args.headless:
            return run_headless(manager, timeout=args.timeout)
        # Import lazily: loading a definition and headless runs do not need a GUI.
        from .web import launch

        launch(
            manager,
            host=args.host,
            port=args.port,
            open_browser=bool(args.open) and not args.no_browser,
            block=True,
            source_path=args.target,
            archive_path=args.archive,
        )
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        safe_print(f"[Wave] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        if manager is not None:
            manager.close()


if __name__ == "__main__":
    raise SystemExit(main())
