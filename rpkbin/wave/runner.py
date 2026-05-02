"""
runner.py - Load a wave file and orchestrate the Session.

Execution model
---------------
1. ``session.reset()``        - clear any stale state from previous runs
2. ``_load_wave_file(path)``  - execute the wave file; it calls session.configure()
                                and session.add() to register jobs
3. Apply CLI overrides        - e.g. max_workers from --workers flag
4. ``session._start(...)``    - create JobManager, inject callbacks, start loop
5. Run TUI (or wait headless)
6. ``session._stop()``        - drain + stop
"""

from __future__ import annotations

import importlib.util
import shlex
import signal
import sys
import threading
import time
from pathlib import Path

from rpkbin.wave.session import session

_WAVE_IMPORT_LOCK = threading.RLock()
_REPL_POLL_INTERVAL = 1.0

_HELP_TEXT = r"""Wave headless commands
  help                          - show this message
  status                        - print status of all jobs
  show <job>                    - print a compact summary for one job
  logs <job> [n]                - print the last n log lines (default 50)
  data <job>                    - print parsed_data for a job
  events <job>                  - print event history for a job
  pause                         - pause dispatch of pending jobs
  resume                        - resume dispatch of pending jobs
  stop <job>                    - graceful stop, then force after timeout
  stop -g <job>                 - graceful stop only; never force kill
  stop --all                    - graceful stop all active jobs
  stop --group <tag>            - graceful stop all active jobs with a tag
  cancel <job>                  - force-cancel a job immediately
  cancel --all                  - force-cancel all active jobs
  cancel --group <tag>          - force-cancel all active jobs with a tag
  skip <job>                    - skip a pending job (marks as skipped)
  input <job> <text>            - send stdin text to a running job (\n, \r, \t supported)
  signal <job> <sig>            - send an OS signal (e.g. SIGINT) to a running job
  watch status                  - refresh status until Ctrl+C
  watch logs <name> [n]         - refresh log tail until Ctrl+C
  exit                          - leave REPL when no jobs are active
  exit --stop                   - stop running/pending jobs, then leave REPL
  exit --force                  - force-kill running jobs, then leave REPL

<job> may be a unique name, full job id, or unique id prefix.
Names containing spaces must be quoted, e.g. logs "job with spaces"
"""


class _WaveCompleter:
    """Minimal prompt_toolkit completer for Wave REPL."""

    def __init__(self, sess) -> None:
        self._sess = sess

    def get_completions(self, document, complete_event):  # pragma: no cover - UI glue
        try:
            from prompt_toolkit.completion import Completion
        except Exception:
            return

        text = document.text_before_cursor
        try:
            parts = shlex.split(text, posix=True)
        except ValueError:
            parts = text.split()

        if text.endswith(" "):
            parts.append("")

        commands = [
            "help", "status", "show", "logs", "data", "events",
            "pause", "resume", "stop", "skip", "cancel", "input", "signal", "watch", "exit",
        ]

        if len(parts) <= 1:
            word = parts[0] if parts else ""
            for cmd in commands:
                if cmd.startswith(word):
                    yield Completion(cmd, start_position=-len(word))
            return

        first = parts[0]
        current = parts[-1]
        job_cmds = {"show", "logs", "data", "events", "stop", "skip", "cancel", "input", "signal"}

        if first == "watch" and len(parts) == 2:
            for cmd in ("status", "logs"):
                if cmd.startswith(current):
                    yield Completion(cmd, start_position=-len(current))
            return

        if first == "stop" and len(parts) == 2 and current.startswith("-"):
            for flag in ("-g", "--graceful", "-f", "--force", "--all", "--group"):
                if flag.startswith(current):
                    yield Completion(flag, start_position=-len(current))
            return

        if first == "cancel" and len(parts) == 2 and current.startswith("-"):
            for flag in ("--all", "--group"):
                if flag.startswith(current):
                    yield Completion(flag, start_position=-len(current))
            return

        if first == "exit" and len(parts) == 2 and current.startswith("-"):
            for flag in ("--stop", "--force"):
                if flag.startswith(current):
                    yield Completion(flag, start_position=-len(current))
            return

        expect_job = first in job_cmds or (first == "watch" and len(parts) >= 3 and parts[1] == "logs")
        if expect_job:
            for name in sorted(job.name for job in self._sess.jobs()):
                if name.startswith(current):
                    yield Completion(name, start_position=-len(current))

    async def get_completions_async(self, document, complete_event):  # pragma: no cover - UI glue
        for completion in self.get_completions(document, complete_event):
            yield completion


def run(
    wave_file: str | Path,
    *,
    no_tui: bool = False,
    workers: int | None = None,
) -> int:
    """Load *wave_file* and run the batch."""
    session.reset()
    _load_wave_file(wave_file)

    if workers is not None:
        session.configure(max_workers=workers)

    if no_tui:
        _run_headless()
    else:
        _run_tui()

    return _session_exit_code()


def _run_headless() -> None:
    """Run without TUI.

    In an interactive terminal, a REPL is opened for command-based control.
    In CI / pipe environments, the session simply runs to completion.
    """
    session._start(tui_notify=None)
    try:
        if sys.stdin.isatty():
            _run_repl(session)
        else:
            session.wait()
    except KeyboardInterrupt:
        if session._manager is not None:
            session._manager.cancel_all()
    finally:
        # _stop() performs the final wait/cleanup even if the non-interactive
        # path already called session.wait() above.
        session._stop()


def _run_tui() -> None:
    """Run with TUI."""
    from rpkbin.wave.tui.app import WaveApp

    app = WaveApp(session)
    session._start(
        tui_notify=app._on_job_updated,
        tui_job_added=app._on_job_added,
    )
    try:
        app.run()
    finally:
        session._stop()


def _run_repl(sess) -> None:
    """Run the interactive headless REPL until the user exits it."""
    print("[Wave] Interactive mode active. Type 'help' for commands.")
    read_line = _make_repl_reader(sess)
    completion_announced = False

    while True:
        if not _active_jobs(sess):
            if not completion_announced:
                _print_completion_summary(sess)
                print("[Wave] Type 'exit' to leave the REPL.")
                completion_announced = True

        try:
            line = read_line()
        except EOFError:
            print("[Wave] EOF received. Leaving REPL.")
            return
        except KeyboardInterrupt:
            print("^C")
            continue

        if line is None:
            return
        line = line.strip()
        if not line:
            continue

        parts = _parse_repl_line(line)
        if parts is None:
            continue

        action = _handle_cmd(parts, sess)
        if action == "exit":
            return
        if _active_jobs(sess):
            completion_announced = False


def _make_repl_reader(sess):
    """Create a line reader backed by prompt_toolkit when available."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
    except Exception:
        return lambda: input("wave> ")

    prompt_session = PromptSession(
        history=InMemoryHistory(),
        completer=_WaveCompleter(sess),
    )
    return lambda: prompt_session.prompt("wave> ")


def _parse_repl_line(line: str) -> list[str] | None:
    try:
        return shlex.split(line, posix=True)
    except ValueError as exc:
        print(f"[Wave] Parse error: {exc}")
        return None


def _parse_job_name(parts: list[str], start_idx: int, verb: str) -> str | None:
    if len(parts) <= start_idx:
        print(f"Usage: {verb}")
        return None
    if len(parts) > start_idx + 1:
        print(f"[Wave] Job names with spaces must be quoted, e.g. {verb.split()[0]} \"my job\".")
        return None
    return parts[start_idx]


def _handle_cmd(parts: list[str], sess) -> str:
    """Dispatch a single user command.

    Returns ``continue`` or ``exit``.
    """
    verb = parts[0].lower()

    try:
        if verb == "help":
            print(_HELP_TEXT)

        elif verb == "status":
            _cmd_status(sess)

        elif verb == "show":
            name = _parse_job_name(parts, 1, "show <job_name>")
            if name is not None:
                _cmd_show(name, sess)

        elif verb == "logs":
            _cmd_logs_parts(parts, sess)

        elif verb == "data":
            name = _parse_job_name(parts, 1, "data <job_name>")
            if name is not None:
                _cmd_data(name, sess)

        elif verb == "events":
            name = _parse_job_name(parts, 1, "events <job_name>")
            if name is not None:
                _cmd_events(name, sess)

        elif verb == "pause":
            _cmd_pause(parts, sess)

        elif verb == "resume":
            _cmd_resume(parts, sess)

        elif verb == "stop":
            _cmd_stop_parts(parts, sess)

        elif verb == "skip":
            name = _parse_job_name(parts, 1, "skip <job_name>")
            if name is not None:
                _cmd_skip(name, sess)

        elif verb == "cancel":
            _cmd_cancel(parts, sess)

        elif verb == "input":
            _cmd_input(parts, sess)

        elif verb == "signal":
            _cmd_signal(parts, sess)

        elif verb == "watch":
            _cmd_watch(parts, sess)

        elif verb == "exit":
            return _cmd_exit(parts, sess)

        else:
            print(f"Unknown command: {verb!r}. Type 'help' for commands.")

    except Exception as exc:
        print(f"[Wave] Command error: {exc}")

    return "continue"


def _job_id(job) -> str:
    return str(getattr(job, "id", ""))


def _resolve_job(identifier: str, sess) -> tuple[object | None, str | None]:
    """Resolve a user-facing job identifier.

    A unique job name is accepted for the common case.  Duplicate names are
    deliberately rejected so commands never act on an arbitrary first match;
    users can disambiguate with the full job id or a unique id prefix.
    """
    jobs = sess.jobs()
    by_name = [job for job in jobs if str(job.name) == identifier]
    if len(by_name) == 1:
        return by_name[0], None
    if len(by_name) > 1:
        lines = [f"[Wave] Job name {identifier!r} is ambiguous:"]
        for job in by_name:
            jid = _job_id(job)
            status = getattr(job, "status", "?")
            lines.append(f"  {jid}  {status}")
        lines.append("Use a job id prefix to disambiguate.")
        return None, "\n".join(lines)

    by_id = [job for job in jobs if _job_id(job) == identifier]
    if len(by_id) == 1:
        return by_id[0], None

    if len(identifier) >= 8:
        by_prefix = [job for job in jobs if _job_id(job).startswith(identifier)]
        if len(by_prefix) == 1:
            return by_prefix[0], None
        if len(by_prefix) > 1:
            ids = ", ".join(_job_id(job) for job in by_prefix)
            return None, f"[Wave] Job id prefix {identifier!r} is ambiguous. Matches: {ids}"

    return None, f"[Wave] No job named or identified by {identifier!r}."


def _find_job(identifier: str, sess, *, quiet: bool = False):
    """Return the job matching *identifier*, or None after reporting why."""
    job, error = _resolve_job(identifier, sess)
    if error and not quiet:
        print(error)
    return job


def _cmd_status(sess) -> None:
    jobs = sess.jobs()
    if not jobs:
        print("[Wave] No jobs registered.")
        return
    print(f"{'ID':<8} {'NAME':<30} {'STATUS':<12} {'STATE':<16} {'SKIPPED'}")
    print("-" * 77)
    for job in jobs:
        status = job.status
        state = getattr(job, "state", None) or ""
        skipped = "yes" if getattr(job, "is_skipped", False) else ""
        display_name = (job.name[:27] + "...") if len(job.name) > 30 else job.name
        print(f"{_job_id(job)[:8]:<8} {display_name:<30} {status:<12} {state:<16} {skipped}")


def _cmd_show(name: str, sess) -> None:
    job = _find_job(name, sess)
    if job is None:
        return
    print(f"id      = {_job_id(job)}")
    print(f"name    = {job.name}")
    print(f"status  = {job.status}")
    print(f"state   = {getattr(job, 'state', None) or ''}")
    print(f"skipped = {getattr(job, 'is_skipped', False)}")
    if hasattr(job, "peek_stop_policy"):
        policy = job.peek_stop_policy()
        print(f"stop    = input={policy.get('graceful_input')!r}, signal={policy.get('graceful_signal')!r}, timeout={policy.get('graceful_timeout')!r}")


def _cmd_logs_parts(parts: list[str], sess) -> None:
    if len(parts) < 2:
        print("Usage: logs <job_name> [n]")
        return
    name = parts[1]
    n = 50
    if len(parts) >= 3:
        try:
            n = int(parts[2])
        except ValueError:
            print('[Wave] Job names with spaces must be quoted, e.g. logs "my job".')
            return
    if len(parts) > 3:
        print("Usage: logs <job_name> [n]")
        return
    _cmd_logs(name, sess, n=n)


def _cmd_logs(name: str, sess, *, n: int = 50) -> None:
    job = _find_job(name, sess)
    if job is None:
        return
    lines = job.tail(n)
    if not lines:
        print(f"[Wave] No log output for {name!r} yet.")
    else:
        print("\n".join(lines))


def _cmd_data(name: str, sess) -> None:
    job = _find_job(name, sess)
    if job is None:
        return
    data = getattr(job, "peek_data", lambda: {})()
    if not data:
        print(f"[Wave] No parsed data for {name!r} yet.")
    else:
        for k, v in data.items():
            print(f"  {k} = {v}")


def _cmd_events(name: str, sess) -> None:
    job = _find_job(name, sess)
    if job is None:
        return
    events = getattr(job, "peek_events", lambda: [])()
    if not events:
        print(f"[Wave] No events for {name!r} yet.")
    else:
        for ev in events:
            print(f"  [{ev.get('time', '?')}] {ev.get('tag', '')} - {ev.get('message', '')}")


def _cmd_pause(parts: list[str], sess) -> None:
    if len(parts) != 1:
        print("Usage: pause")
        return
    sess.pause()
    print("[Wave] Job dispatch paused.")


def _cmd_resume(parts: list[str], sess) -> None:
    if len(parts) != 1:
        print("Usage: resume")
        return
    sess.resume()
    print("[Wave] Job dispatch resumed.")


def _cmd_stop_parts(parts: list[str], sess) -> None:
    force = False
    graceful_only = False
    stop_all = False
    stop_group = False
    idx = 1

    while idx < len(parts) and parts[idx].startswith("-"):
        flag = parts[idx]
        if flag in ("-g", "--graceful"):
            graceful_only = True
        elif flag in ("-f", "--force"):
            force = True
        elif flag == "--all":
            stop_all = True
        elif flag == "--group":
            stop_group = True
        else:
            print("Usage: stop [-g] <job> | stop --all | stop --group <tag>")
            return
        idx += 1

    if graceful_only and force:
        print("[Wave] stop cannot use both graceful and force flags together.")
        return

    if stop_all:
        if idx < len(parts):
            print("[Wave] --all does not take a job name.")
            return
        _cmd_stop_all(sess, force=force, graceful_only=graceful_only)
        return

    if stop_group:
        if idx >= len(parts):
            print("Usage: stop --group <tag>")
            return
        tag = parts[idx]
        _cmd_stop_group(tag, sess, force=force, graceful_only=graceful_only)
        return

    name = _parse_job_name(parts, idx, "stop [-g] <job>")
    if name is None:
        return
    _cmd_stop(name, sess, force=force, graceful_only=graceful_only)


def _cmd_stop(name: str, sess, *, force: bool = False, graceful_only: bool = False) -> None:
    job = _find_job(name, sess)
    if job is None:
        return
    if hasattr(job, "request_stop"):
        result = job.request_stop(force=force, graceful_only=graceful_only)
        if result == "already_finished":
            print(f"[Wave] Job {name!r} is already {job.status!r}; nothing to stop.")
        elif result == "cancelled_pending":
            print(f"[Wave] Pending job {name!r} cancelled.")
        elif result == "graceful":
            print(f"[Wave] Graceful stop requested for {name!r}.")
        elif result == "unsupported":
            print(f"[Wave] {name!r} has no graceful stop policy configured.")
        else:
            print(f"[Wave] Force stop requested for {name!r}.")
    else:
        if graceful_only:
            print(f"[Wave] {name!r} does not support graceful stop.")
            return
        job.cancel()
        print(f"[Wave] Force stop requested for {name!r}.")


def _cmd_stop_all(sess, *, force: bool = False, graceful_only: bool = False) -> None:
    """Graceful stop (or force-kill) all active jobs."""
    active = _active_jobs(sess)
    if not active:
        print("[Wave] No active jobs to stop.")
        return
    count = 0
    for job in active:
        if hasattr(job, "request_stop"):
            job.request_stop(force=force, graceful_only=graceful_only)
            count += 1
        else:
            if graceful_only:
                continue  # plain scheduler jobs have no graceful path
            job.cancel()
            count += 1
    print(f"[Wave] Stop requested for {count} active job(s).")


def _cmd_stop_group(
    tag: str, sess, *, force: bool = False, graceful_only: bool = False
) -> None:
    """Graceful stop (or force-kill) all active jobs carrying *tag*."""
    jobs = sess.jobs()
    targets = [
        j for j in jobs
        if tag in getattr(j, "tags", frozenset())
        and getattr(j, "status", None) in ("pending", "running")
    ]
    if not targets:
        print(f"[Wave] No active jobs with tag {tag!r}.")
        return
    count = 0
    for job in targets:
        if hasattr(job, "request_stop"):
            job.request_stop(force=force, graceful_only=graceful_only)
            count += 1
        else:
            if graceful_only:
                continue
            job.cancel()
            count += 1
    print(f"[Wave] Stop requested for {count} job(s) with tag {tag!r}.")


def _cmd_cancel(parts: list[str], sess) -> None:
    """Force-cancel a single job or all active jobs."""
    if len(parts) == 1:
        print("Usage: cancel <job> | cancel --all | cancel --group <tag>")
        return

    if parts[1] == "--all":
        if len(parts) > 2:
            print("[Wave] --all does not take a job name.")
            return
        active = _active_jobs(sess)
        if not active:
            print("[Wave] No active jobs to cancel.")
            return
        for job in active:
            job.cancel()
        print(f"[Wave] Force-cancelled {len(active)} active job(s).")
        return

    if parts[1] == "--group":
        if len(parts) != 3:
            print("Usage: cancel --group <tag>")
            return
        tag = parts[2]
        count = sess.cancel_group(tag)
        if count == 0:
            print(f"[Wave] No active jobs with tag {tag!r}.")
        else:
            print(f"[Wave] Force-cancelled {count} job(s) with tag {tag!r}.")
        return

    name = _parse_job_name(parts, 1, "cancel <job>")
    if name is None:
        return
    job = _find_job(name, sess)
    if job is None:
        return
    status = getattr(job, "status", None)
    if status in ("done", "failed", "cancelled"):
        print(f"[Wave] Job {name!r} is already {status!r}; nothing to cancel.")
        return
    job.cancel()
    print(f"[Wave] Job {name!r} force-cancelled.")


def _cmd_skip(name: str, sess) -> None:
    job = _find_job(name, sess)
    if job is None:
        return
    if hasattr(job, "skip"):
        if getattr(job, "status", None) != "pending":
            print(f"[Wave] Job {name!r} is {job.status!r}; only pending jobs can be skipped.")
            return
        job.skip()
        print(f"[Wave] {name!r} marked as skipped.")
    else:
        print(f"[Wave] {name!r} is not a Wave job; use 'cancel' to force-cancel it.")


def _cmd_input(parts: list[str], sess) -> None:
    if len(parts) != 3:
        print("Usage: input <job_name> <text>")
        return
    job = _find_job(parts[1], sess)
    if job is None:
        return
    if not hasattr(job, "send_input"):
        print(f"[Wave] {parts[1]!r} does not accept stdin input.")
        return
    text = parts[2].replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    try:
        job.send_input(text)
    except RuntimeError as exc:
        print(f"[Wave] {exc}")
        return
    print(f"[Wave] Input sent to {parts[1]!r}.")


def _resolve_signal(value: str) -> int | None:
    normalized = value.upper()
    if normalized.isdigit():
        return int(normalized)
    if not normalized.startswith("SIG"):
        normalized = f"SIG{normalized}"
    sig_value = getattr(signal, normalized, None)
    if isinstance(sig_value, int):
        return sig_value
    return None


def _cmd_signal(parts: list[str], sess) -> None:
    if len(parts) != 3:
        print("Usage: signal <job_name> <signal>")
        return
    job = _find_job(parts[1], sess)
    if job is None:
        return
    if not hasattr(job, "send_signal"):
        print(f"[Wave] {parts[1]!r} does not support OS signals.")
        return
    sig_value = _resolve_signal(parts[2])
    if sig_value is None:
        print(f"[Wave] Unknown signal {parts[2]!r}.")
        return
    try:
        job.send_signal(sig_value)
    except RuntimeError as exc:
        print(f"[Wave] {exc}")
        return
    print(f"[Wave] Signal {parts[2]!r} sent to {parts[1]!r}.")


def _cmd_watch(parts: list[str], sess) -> None:
    if len(parts) < 2:
        print("Usage: watch <command>")
        return
    watched = parts[1:]
    if watched[0] not in {"status", "logs"}:
        print("[Wave] watch currently supports only: status, logs")
        return
    print("[Wave] Watching. Press Ctrl+C to return to the REPL.")
    try:
        while True:
            print()
            _handle_cmd(watched, sess)
            time.sleep(_REPL_POLL_INTERVAL)
    except KeyboardInterrupt:
        print("[Wave] Watch stopped.")


def _active_jobs(sess) -> list:
    return [job for job in sess.jobs() if job.status in {"pending", "running"}]


def _print_completion_summary(sess) -> None:
    done = len(sess.done())
    failed = len(sess.failed(include_skipped=False))
    skipped = len(sess.skipped())
    print(f"[Wave] All jobs are complete. done={done} failed={failed} skipped={skipped}")


def _cmd_exit(parts: list[str], sess) -> str:
    active = _active_jobs(sess)
    if len(parts) == 1:
        if active:
            print("[Wave] Jobs are still active. Use 'exit --stop' or 'exit --force'.")
            return "continue"
        return "exit"

    if len(parts) != 2:
        print("Usage: exit [--stop|--force]")
        return "continue"

    mode = parts[1]
    if mode == "--stop":
        for job in active:
            if hasattr(job, "request_stop"):
                job.request_stop()
            else:
                job.cancel()
        return "exit"

    if mode == "--force":
        for job in active:
            job.cancel()
        return "exit"

    print("Usage: exit [--stop|--force]")
    return "continue"


def _load_wave_file(path: str | Path) -> None:
    """Import *path* as a module, executing its top-level code."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Wave file not found: {path}")

    spec = importlib.util.spec_from_file_location("_wave_user_file", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load wave file: {path}")
    loader = spec.loader

    module = importlib.util.module_from_spec(spec)
    wave_dir = str(path.parent)
    with _WAVE_IMPORT_LOCK:
        inserted_at: int | None = None
        if wave_dir not in sys.path:
            sys.path.insert(0, wave_dir)
            inserted_at = 0
        try:
            loader.exec_module(module)
        finally:
            if inserted_at is not None:
                if len(sys.path) > inserted_at and sys.path[inserted_at] == wave_dir:
                    sys.path.pop(inserted_at)
                else:
                    for i, entry in enumerate(sys.path):
                        if entry == wave_dir:
                            sys.path.pop(i)
                            break


def _session_exit_code() -> int:
    """Map the finalized session summary to a shell-style exit code."""
    return int(session.summary().get("exit_code", 1))
