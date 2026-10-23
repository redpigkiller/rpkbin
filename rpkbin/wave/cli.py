"""Wave entry point: load a Python work file, preview it, or run a batch."""

from __future__ import annotations

import argparse
import math
import runpy
import sys
import threading
from pathlib import Path
from typing import Sequence

from rpkbin.job_manager import Event, Expect, Job, JobManager
from rpkbin.job_manager.manager import definition_scope


def load_work_file(target: str | Path) -> JobManager:
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
            namespace = runpy.run_path(str(path), run_name="__wave_definition__")
    except SystemExit as exc:
        raise ValueError(
            f"Work file called sys.exit({exc.code!r}); define jobs without running them"
        ) from exc
    preferred = {
        id(namespace[key]): namespace[key]
        for key in ("mgr", "manager", "job_manager")
        if isinstance(namespace.get(key), JobManager)
    }
    candidates = preferred or {
        id(value): value
        for value in namespace.values()
        if isinstance(value, JobManager)
    }
    if len(candidates) != 1:
        raise ValueError(
            "Work file must expose one unambiguous JobManager as mgr, manager, or job_manager"
        )
    manager = next(iter(candidates.values()))
    if not manager.jobs:
        raise ValueError("Work file contains no jobs")
    manager.validate()
    return manager


def build_demo_manager() -> JobManager:
    """Build three finite, generic example commands; do not start them."""
    manager = JobManager(max_workers=2, resources={"slot": 2}, columns=["progress"])
    for index in range(3):
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
        job.track("progress", r"progress=(\d+)", cast=int)
        job.check("completion", match=r"^COMPLETE$", expect=Expect.PRESENT)
        if index == 1:
            job.on(
                Event.LOG,
                match=r"progress=50",
                warn=True,
                message="Review midpoint",
                name="midpoint",
            )
        manager.add(job)
    return manager


def _summary(manager: JobManager) -> None:
    print("\n[Wave] Final summary", flush=True)
    print(f"{'JOB':<26} {'STATE':<15} {'RESULT':<12} {'WARNINGS':<9} EXIT", flush=True)
    for job in manager.snapshot()["jobs"]:
        state = job.get("status", job.get("state", "?"))
        print(
            f"{job['name']:<26} {state:<15} {job.get('result') or '—':<12} "
            f"{job.get('warning_count', 0):<9} {job.get('returncode', '—')}",
            flush=True,
        )
        checks = job.get("checks", [])
        if checks:
            print(
                "  checks: "
                + "; ".join(
                    f"{item['name']}: {item.get('state') or '—'}" for item in checks
                ),
                flush=True,
            )
        for check in checks:
            if check.get("state") == "OK":
                continue
            state = check.get("state", "—")
            if state == "INCOMPLETE":
                why = "execution ended before this check could be decided"
            elif state == "ERROR":
                why = "monitoring failed; see error evidence"
            else:
                why = (
                    "forbidden condition matched"
                    if check.get("expect") == "absent"
                    else "required condition never matched"
                )
            evidence = check.get("evidence") or {}
            at = (
                f" #{evidence['line_no']}"
                if evidence.get("line_no") is not None
                else ""
            )
            text = f": {evidence['line']}" if evidence.get("line") else ""
            print(f"  check {check['name']}: {state}; {why}{at}{text}", flush=True)
        for warning in job.get("warnings", []):
            print(
                f"  warning: {warning['name']} ({warning['count']} match(es))",
                flush=True,
            )
        if job.get("attempt_number", 1) > 1:
            print(
                f"  latest attempt: #{job['attempt_number']}; earlier history retained: {len(job.get('history', []))}",
                flush=True,
            )
        for error in job.get("errors", []):
            print(f"  error: {error.get('message', error)}", flush=True)
        for reason in job.get("termination", []):
            print(
                f"  stopped: {reason.get('source', '?')}: {reason.get('reason', '')}",
                flush=True,
            )


def run_headless(manager: JobManager, *, timeout: float | None = None) -> int:
    """Run a validated batch, stream output, drain cleanup, and return its outcome."""
    if not manager.jobs:
        raise ValueError("Headless execution requires at least one job")
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be a finite positive number")
    manager.validate()
    output_lock = threading.Lock()

    def stream(record: dict) -> None:
        origin = "child" if record["source"] == "stdout" else "Wave"
        with output_lock:
            print(
                f"[{origin} {record['job_name']} #{record['attempt_number']}] {record['text']}",
                flush=True,
            )

    manager.add_log_listener(stream)
    interrupted = False
    timed_out = False
    print(
        f"[Wave] Starting {len(manager.jobs)} job(s); workers={manager.max_workers}",
        flush=True,
    )
    try:
        manager.start()
        if not manager._drain(timeout):
            timed_out = True
            print(
                "[Wave] Batch timeout; stopping remaining jobs and waiting for cleanup",
                flush=True,
            )
            manager.stop_all(source="timeout", reason="Headless batch timeout")
            manager._drain()
    except KeyboardInterrupt:
        interrupted = True
        print("\n[Wave] Interrupted; stopping jobs and waiting for cleanup", flush=True)
        try:
            manager.stop_all(source="user", reason="Terminal Ctrl+C")
            manager._drain()
        except KeyboardInterrupt:
            print(
                "\n[Wave] Second interrupt; forcing remaining jobs to stop", flush=True
            )
            manager.stop_all(force=True, source="user", reason="Second terminal Ctrl+C")
            manager._drain()
    finally:
        manager.close()
    _summary(manager)
    if interrupted:
        return 130
    failed = any(
        job["result"] not in ("PASS", "N/A") for job in manager.snapshot()["jobs"]
    )
    return 1 if failed or timed_out else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rpk-wave",
        description="Wave: Python-defined jobs, local GUI or headless batch execution.",
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
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically",
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
    args = parser.parse_args(argv)
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
    manager = None
    try:
        manager = (
            build_demo_manager()
            if args.demo
            else load_work_file(args.target) if args.target else JobManager()
        )
        if args.max_workers is not None:
            manager.update_settings(max_workers=args.max_workers)
        if args.headless:
            return run_headless(manager, timeout=args.timeout)
        # Import lazily: loading a definition and headless runs do not need a GUI.
        from .web import launch

        launch(
            manager,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            block=True,
            source_path=args.target,
        )
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"[Wave] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        if manager is not None:
            manager.close()


if __name__ == "__main__":
    raise SystemExit(main())
