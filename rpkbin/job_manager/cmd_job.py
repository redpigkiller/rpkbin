"""
cmd_job.py — Concrete Job subclass for local shell command execution.
"""

from __future__ import annotations

import os
import sys
import uuid
import signal
import subprocess

from .job import Job, RUNNING

class CmdJob(Job):
    """A job that runs a shell command on the local machine.

    Example:
        job = CmdJob("compile", "make -j4", cwd="/proj/rtl")
    """

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
    ) -> None:
        super().__init__(name, job_id=job_id, priority=priority, max_retries=max_retries, resources=resources, max_log_lines=max_log_lines, tags=tags)
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.flush_tokens = flush_tokens
        self._proc: subprocess.Popen | None = None

    def _execute(self, log_file=None) -> None:
        """Run `self.cmd` via subprocess and stream output."""
        env = {**os.environ, **self.env} if self.env else None
        
        # SRE Robustness: Process Group to avoid zombies and enable signals
        kwargs = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        with self._lock:
            if self.is_cancelled:
                return
            proc = subprocess.Popen(
                self.cmd,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.cwd,
                env=env,
                text=True,
                bufsize=1,   # line-buffered
                **kwargs
            )
            self._proc = proc

        buffer = []
        # Non-blocking OS check loop avoiding Busy Wait
        while True:
            if proc.stdout is None:
                break
            char = proc.stdout.read(1)
            
            if not char:  # EOF — process finished or killed
                if buffer:
                    line = "".join(buffer)
                    self._emit_line(line)
                    if log_file:
                        log_file.write(line)
                        log_file.flush()
                break

            buffer.append(char)
            if char == "\n":
                line = "".join(buffer)
                self._emit_line(line.rstrip())
                if log_file:
                    log_file.write(line)
                    log_file.flush()
                buffer.clear()
            else:
                if self.flush_tokens:
                    partial = "".join(buffer)
                    if partial.endswith(self.flush_tokens):
                        self._emit_line(partial)
                        if log_file:
                            log_file.write(partial)
                            log_file.flush()
                        buffer.clear()

        proc.wait()
        
        with self._lock:
            self._result = proc.returncode
            if proc.returncode != 0 and self._status == RUNNING:
                lines = self.tail(5)
                self._error = "\n".join(lines) if lines else f"Exit code {proc.returncode}"

    def kill(self) -> None:
        """Forcefully terminate the running process tree."""
        with self._lock:
            proc = self._proc
            if proc is None:
                return

        if proc.poll() is not None:
            return  # Already terminated

        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                proc.kill() # fallback

    def send_input(self, text: str) -> None:
        """Write *text* to the running process's stdin."""
        with self._lock:
            proc = self._proc
            status = self._status
        
        if status != RUNNING or proc is None or proc.stdin is None:
            raise RuntimeError(f"Job {self.name!r}: stdin not available or not running.")
            
        try:
            proc.stdin.write(text)
            proc.stdin.flush()
        except OSError:
            pass # Pipe likely broken because process exited

    def send_signal(self, signum: int) -> None:
        """Send an OS signal (e.g., signal.SIGINT) to the running process."""
        with self._lock:
            proc = self._proc
            status = self._status
        
        if status != RUNNING or proc is None:
            raise RuntimeError(f"Job {self.name!r} is not running. Cannot send signal.")
            
        if sys.platform == "win32":
            # On Windows, os.kill heavily relies on CTRL_BREAK_EVENT or CTRL_C_EVENT
            # if we want the subprocess to catch it instead of instantly dying.
            if signum == signal.SIGINT:
                real_sig = signal.CTRL_BREAK_EVENT # Safest substitute on Windows
            else:
                real_sig = signum
                
            try:
                os.kill(proc.pid, real_sig)
            except OSError:
                pass
        else:
            # Linux/Unix standard signal to process group
            try:
                os.killpg(os.getpgid(proc.pid), signum)
            except (ProcessLookupError, OSError):
                pass

