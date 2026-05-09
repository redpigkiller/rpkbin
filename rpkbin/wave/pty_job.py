"""
pty_job.py — PtyCmdJob: PTY-based interactive command job for Wave.

PtyCmdJob runs a shell command inside a pseudo-terminal so the child
process sees ``isatty() == True``.  This makes interactive programs
(Python REPL, bash, ``read``-based scripts) behave normally.

Key differences from WaveCmdJob
-------------------------------
- Child's stdin/stdout/stderr are connected to a PTY slave fd via
  ``pty.fork()``, which establishes a proper controlling terminal.
- Parent reads/writes via the PTY master fd.
- ``send_key("ctrl-c")`` writes the control byte to the PTY (terminal
  key), which is distinct from ``send_signal(SIGINT)`` (OS signal).
  Because the slave PTY is the child's controlling terminal, the kernel
  line discipline translates the ``\\x03`` byte into a SIGINT to the
  child's foreground process group — just like a real terminal.
- Default stop policy uses ``graceful_key="ctrl-c"`` instead of SIGINT.
- Output goes through ANSI-stripped cleaning for both log display and
  parser input.  A future version may preserve ANSI for a dedicated
  terminal rendering path.

Linux / macOS only.  On Windows the object can be constructed (for
import and display purposes), but ``_execute()`` will fail with a clear
``PtyCmdJob requires POSIX PTY support`` error message.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import TYPE_CHECKING, Callable

from rpkbin.job_manager.job import Job, RUNNING
from rpkbin.wave.job import WaveJobMixin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Control key mapping
# ---------------------------------------------------------------------------

_CONTROL_KEY_MAP: dict[str, bytes] = {
    "ctrl-c":  b"\x03",
    "ctrl-d":  b"\x04",
    "ctrl-z":  b"\x1a",
    "ctrl-\\": b"\x1c",
    "enter":   b"\r",
    "tab":     b"\t",
}

# ANSI escape sequence regex (CSI sequences, OSC, simple escapes)
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b"           # ESC character
    r"(?:"
    r"\[[0-9;?]*[A-Za-z]"   # CSI sequences: ESC [ ... letter
    r"|"
    r"\][^\x07]*\x07"       # OSC sequences: ESC ] ... BEL
    r"|"
    r"\][^\x1b]*\x1b\\"     # OSC sequences: ESC ] ... ST
    r"|"
    r"[()][AB012]"           # Character set selection
    r"|"
    r"[>=<]"                 # Keypad modes
    r"|"
    r"[78HM]"               # Cursor save/restore, etc.
    r")"
)

# Control characters that are unsafe for display / parsing (except \t, \n)
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_for_log(raw: str) -> str:
    """Normalize raw PTY output for human-readable log display.

    - Strips ANSI escape sequences for clean log display.
    - Strips ``\\r`` (carriage return) to avoid cursor-overwrite confusion.
    - Replaces remaining unsafe control characters with ``?``.
    """
    text = _ANSI_ESCAPE_RE.sub("", raw)
    text = text.replace("\r", "")
    text = _UNSAFE_CONTROL_RE.sub("?", text)
    return text


def _clean_for_parser(raw: str) -> str:
    """Strip ANSI escapes and control chars for parser consumption.

    Parsers receive a plain-text line so regex patterns work reliably
    without being confused by terminal escape sequences.
    """
    text = _ANSI_ESCAPE_RE.sub("", raw)
    text = text.replace("\r", "")
    text = _UNSAFE_CONTROL_RE.sub("", text)
    return text


# ---------------------------------------------------------------------------
# PtyCmdJob
# ---------------------------------------------------------------------------

class PtyCmdJob(WaveJobMixin, Job):
    """A job that runs a shell command inside a pseudo-terminal (PTY).

    The child process sees ``isatty() == True`` for stdin, stdout, and
    stderr, making it suitable for interactive programs.

    Uses ``pty.fork()`` to establish a proper controlling terminal so
    that terminal control keys (e.g. Ctrl-C → SIGINT) work through the
    kernel's line discipline, exactly like a real terminal.

    API is intentionally close to ``WaveCmdJob``:

    - ``name``, ``cmd``, ``cwd``, ``env``, ``priority``, ``max_retries``,
      ``resources``, ``max_log_lines``, ``flush_tokens``, ``tags``
    - Additionally: ``rows`` and ``cols`` for initial terminal size.

    Linux / macOS only.  On Windows, construction succeeds but
    ``_execute()`` marks the job as failed with a clear error.

    Example::

        job = PtyCmdJob("repl", "python3 -i", rows=24, cols=80)
        job.send_input("print('hello')\\n")
        job.send_key("ctrl-c")
    """

    # Marker for TUI and API consumers
    supports_pty: bool = True

    def __init__(
        self,
        name: str,
        cmd: str,
        *,
        job_id: str | uuid.UUID | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        priority: int = 0,
        max_retries: int = 0,
        resources: dict[str, int] | None = None,
        max_log_lines: int = 10_000,
        flush_tokens: tuple[str, ...] | None = None,
        tags: frozenset[str] | set[str] | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> None:
        super().__init__(
            name,
            job_id=job_id,
            priority=priority,
            max_retries=max_retries,
            resources=resources,
            max_log_lines=max_log_lines,
            tags=tags,
        )
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.flush_tokens = flush_tokens
        self._pty_rows = rows
        self._pty_cols = cols

        self._pid: int | None = None
        self._master_fd: int | None = None

        self._wave_init()
        # Default stop policy: send Ctrl-C via PTY (natural terminal behavior)
        self.set_stop_policy(graceful_key="ctrl-c")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute(self, *, log_file=None, **kwargs) -> None:
        """Run ``self.cmd`` inside a PTY and stream output."""
        # Fire on_start hooks via WaveJobMixin MRO
        self._handle_on_start()

        if os.name == "nt":
            with self._lock:
                self._error = (
                    "PtyCmdJob requires POSIX PTY support (Linux/macOS). "
                    "Windows does not support Unix pseudo-terminals."
                )
                self._result = 1
            return

        # Import POSIX-only modules here so Windows doesn't fail at import time
        import fcntl
        import pty
        import select
        import struct
        import termios

        # -- Prepare everything BEFORE fork (minimize child path) --
        resolved_cwd = self.cwd or None
        env = {**os.environ, **(self.env or {})}
        env.setdefault("TERM", "xterm-256color")
        argv = ["/bin/sh", "-c", self.cmd]
        winsize = struct.pack("HHHH", self._pty_rows, self._pty_cols, 0, 0)

        with self._lock:
            if self.is_cancelled:
                return

        # Use pty.fork() so the child gets a proper controlling terminal.
        # pty.fork() internally does: openpty(), fork(), and in the child:
        # setsid(), TIOCSCTTY on the slave, dup2 to 0/1/2.
        # This ensures the kernel line discipline translates \x03 → SIGINT.
        pid, master_fd = pty.fork()

        if pid == 0:
            # ---- Child process ----
            # pty.fork() already did setsid() + TIOCSCTTY + dup2(slave, 0/1/2).
            # Keep this path MINIMAL: no logging, no locks, no Python callbacks.
            try:
                if resolved_cwd is not None:
                    os.chdir(resolved_cwd)
            except OSError:
                os._exit(127)
            try:
                os.execvpe("/bin/sh", argv, env)
            except OSError:
                pass
            os._exit(127)

        # ---- Parent process ----
        # Set terminal size on the master fd (parent side).
        # This propagates to the slave via the PTY layer.
        try:
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            logger.warning("Failed to set PTY window size for job %r.", self.name)

        with self._lock:
            if self.is_cancelled:
                os.close(master_fd)
                # Clean up the child we already forked
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    os.waitpid(pid, 0)
                except OSError:
                    pass
                return
            self._pid = pid
            self._master_fd = master_fd

        # Read loop: read from master fd, split into lines
        buffer = ""
        try:
            while True:
                # Use select to wait for data or detect EOF
                try:
                    readable, _, _ = select.select([master_fd], [], [], 0.1)
                except (ValueError, OSError):
                    break  # fd closed

                if not readable:
                    # Check if child is still alive (non-blocking)
                    try:
                        wpid, wstatus = os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        break  # already reaped
                    if wpid != 0:
                        # Child exited; do one final read attempt
                        try:
                            chunk = os.read(master_fd, 4096)
                            if chunk:
                                buffer += chunk.decode("utf-8", errors="replace")
                        except OSError:
                            pass
                        self._decode_wait_status(wstatus)
                        break
                    continue

                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    # EIO is normal when the PTY slave side closes
                    break

                if not chunk:
                    break

                raw_text = chunk.decode("utf-8", errors="replace")
                buffer += raw_text

                # Process complete lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    log_line = _clean_for_log(line)
                    self._emit_pty_line(log_line, line, log_file=log_file)

                # Check flush_tokens on partial buffer
                if buffer and self.flush_tokens:
                    if buffer.endswith(self.flush_tokens):
                        log_line = _clean_for_log(buffer)
                        self._emit_pty_line(log_line, buffer, log_file=log_file)
                        buffer = ""

        finally:
            # Flush any remaining buffer
            if buffer.strip():
                log_line = _clean_for_log(buffer)
                self._emit_pty_line(log_line, buffer, log_file=log_file)

            # Reap child if not already reaped in the read loop
            with self._lock:
                already_reaped = self._result is not None

            if not already_reaped:
                try:
                    _, wstatus = os.waitpid(pid, 0)
                    self._decode_wait_status(wstatus)
                except ChildProcessError:
                    # Already reaped (e.g. by non-blocking check above)
                    pass

            # Close master fd
            try:
                os.close(master_fd)
            except OSError:
                pass

            with self._lock:
                self._master_fd = None
                self._pid = None
                # If result was never set (edge case), mark as failed
                if self._result is None:
                    self._result = -1
                if self._result != 0 and self._status == RUNNING:
                    lines = self.tail(5)
                    self._error = (
                        "\n".join(lines) if lines
                        else f"Exit code {self._result}"
                    )

    def _decode_wait_status(self, wstatus: int) -> None:
        """Decode os.waitpid status into a returncode matching Popen convention.

        - Normal exit:     ``returncode = os.WEXITSTATUS(wstatus)``
        - Killed by signal: ``returncode = -os.WTERMSIG(wstatus)``
        """
        with self._lock:
            if os.WIFEXITED(wstatus):
                self._result = os.WEXITSTATUS(wstatus)
            elif os.WIFSIGNALED(wstatus):
                self._result = -os.WTERMSIG(wstatus)
            else:
                self._result = -1

    def _emit_pty_line(
        self,
        log_line: str,
        raw_line: str,
        *,
        log_file=None,
    ) -> None:
        """Emit a line to the log buffer, then dispatch cleaned version to parsers.

        The log buffer receives ``log_line`` (human-readable, ANSI-stripped).
        Parsers/hooks receive an ANSI-stripped version via the on_log callback
        path, which is handled by overriding ``_emit_line`` indirectly.

        If *log_file* is provided, the line is also written to the external
        log file (aligns with CmdJob behavior for JobManager log_dir).
        """
        # Store the cleaned-for-parser version so _handle_log can use it
        parser_line = _clean_for_parser(raw_line)

        # Emit the log-friendly version to the output buffer
        # (this is what `logs <job>` and the TUI log view display)
        with self._lock:
            self._output_buffer.append(log_line)
            self._total_log_lines += 1
            cbs = list(self._on_log_cbs)
            watchers = list(self._watchers)

        # Write to external log file (aligns with CmdJob behavior)
        if log_file is not None:
            try:
                log_file.write(log_line + "\n")
                log_file.flush()
            except OSError:
                pass

        # Dispatch callbacks with the parser-cleaned line
        for cb in cbs:
            try:
                t0 = time.perf_counter()
                cb(self, parser_line)
                dt = time.perf_counter() - t0
                if dt > 0.1:
                    logger.warning(
                        "Slow on_log callback detected for job %r (took %.2fs).",
                        self.name, dt,
                    )
            except Exception:
                logger.exception("on_log callback %r raised (ignored).", cb)

        for pattern, wcb in watchers:
            try:
                m = pattern.search(parser_line)
                if m:
                    wcb(self, m)
            except Exception:
                logger.exception(
                    "watch callback for pattern %r raised (ignored).",
                    pattern.pattern,
                )

    # ------------------------------------------------------------------
    # Input / Control API
    # ------------------------------------------------------------------

    def send_input(self, text: str) -> None:
        """Write *text* to the PTY master fd.

        The text is delivered to the child process as if the user typed
        it at the terminal.  Include ``\\n`` for newline / Enter.
        """
        with self._lock:
            master_fd = self._master_fd
            status = self._status

        if status != RUNNING or master_fd is None:
            raise RuntimeError(
                f"Job {self.name!r}: PTY not available or not running."
            )

        try:
            os.write(master_fd, text.encode("utf-8"))
        except OSError:
            pass  # PTY likely closed because process exited

    def send_key(self, key: str) -> None:
        """Send a terminal control key to the PTY.

        Supported keys: ``ctrl-c``, ``ctrl-d``, ``ctrl-z``,
        ``ctrl-\\\\``, ``enter``, ``tab``.

        This writes the corresponding control byte to the PTY master fd,
        which the child's terminal driver interprets as a key press.
        Because the slave PTY is the child's controlling terminal
        (established by ``pty.fork()``), the kernel line discipline
        translates ``\\x03`` into SIGINT for the foreground process group.

        This is **not** an OS signal — use ``send_signal()`` for that.

        Raises
        ------
        ValueError
            If *key* is not a recognized control key name.
        RuntimeError
            If the job is not running or the PTY is not available.
        """
        key_lower = key.lower().strip()
        byte_val = _CONTROL_KEY_MAP.get(key_lower)
        if byte_val is None:
            supported = ", ".join(sorted(_CONTROL_KEY_MAP.keys()))
            raise ValueError(
                f"Unknown control key {key!r}. "
                f"Supported keys: {supported}"
            )

        with self._lock:
            master_fd = self._master_fd
            status = self._status

        if status != RUNNING or master_fd is None:
            raise RuntimeError(
                f"Job {self.name!r}: PTY not available or not running."
            )

        try:
            os.write(master_fd, byte_val)
        except OSError:
            pass  # PTY likely closed because process exited

    def send_signal(self, signum: int) -> None:
        """Send an OS signal to the running process.

        This delivers a real OS signal to the process group, which is
        distinct from ``send_key("ctrl-c")`` (a terminal control key).
        """
        with self._lock:
            pid = self._pid
            status = self._status

        if status != RUNNING or pid is None:
            raise RuntimeError(
                f"Job {self.name!r} is not running. Cannot send signal."
            )

        if sys.platform == "win32":
            try:
                os.kill(pid, signum)
            except OSError:
                pass
        else:
            try:
                os.killpg(pid, signum)
            except (ProcessLookupError, OSError):
                # Fallback to direct kill if process group is gone
                try:
                    os.kill(pid, signum)
                except (ProcessLookupError, OSError):
                    pass

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    def kill(self) -> None:
        """Forcefully terminate the running process tree.

        Sends SIGTERM, waits briefly, then SIGKILL if still alive.
        Does NOT call waitpid — the _execute() worker thread is the
        sole owner of child reaping to avoid race conditions.
        """
        with self._lock:
            pid = self._pid
            if pid is None:
                return

        # Check if child is already dead (non-blocking, no reap)
        try:
            os.kill(pid, 0)  # signal 0 = existence check
        except ProcessLookupError:
            return  # Already terminated
        except OSError:
            return

        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                return

        # Give the process a moment to exit, then escalate to SIGKILL
        def _escalate():
            time.sleep(2)
            try:
                os.kill(pid, 0)  # still alive?
            except (ProcessLookupError, OSError):
                return
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

        threading.Thread(
            target=_escalate,
            daemon=True,
            name=f"PtyKillEscalate-{self.name}",
        ).start()

    # ------------------------------------------------------------------
    # Rerun
    # ------------------------------------------------------------------

    def _clone_for_rerun(self, rerun_number: int) -> "PtyCmdJob":
        """Create a new PtyCmdJob with the same configuration for rerun.

        Clones static parameters, PTY config (rows/cols), parsers
        (stateless callables — direct reuse), hooks (via ``hook.copy()``
        for fresh firing state), and the stop policy.
        """
        base_name = self._rerun_base_name(self.name)
        new = PtyCmdJob(
            f"{base_name}#rerun{rerun_number}",
            self.cmd,
            cwd=self.cwd,
            env=dict(self.env) if self.env else None,
            priority=self.priority,
            max_retries=self.max_retries,
            resources=dict(self.resources) if self.resources else None,
            max_log_lines=self._output_buffer.maxlen,
            flush_tokens=self.flush_tokens,
            tags=self.tags,
            rows=self._pty_rows,
            cols=self._pty_cols,
        )
        with self._wave_lock:
            parsers = list(self._wave_parsers)
            hooks = list(self._wave_hooks)
        for fn in parsers:
            new.add_parser(fn)
        for hook in hooks:
            new.add_hook(hook.copy())
        policy = self.peek_stop_policy()
        new.set_stop_policy(
            graceful_key=policy.get("graceful_key"),
            graceful_input=policy.get("graceful_input"),
            graceful_signal=policy.get("graceful_signal"),
            graceful_timeout=policy.get("graceful_timeout", 5.0),
        )
        return new
