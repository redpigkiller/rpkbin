"""External-process transport; no scheduling or monitoring policy lives here.

Every process owns a POSIX process group or a Windows Job Object. Call
``cleanup()`` after the main process exits, then drain ``read()`` to EOF and
``close()``. This also releases inherited output handles held by descendants.
"""

from __future__ import annotations

import errno
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Sequence

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.SetNamedPipeHandleState.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _kernel32.SetNamedPipeHandleState.restype = wintypes.BOOL

    def _win_check(ok: object) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())


def _nonblocking_input(fd: int) -> None:
    if os.name == "nt":
        # Equivalent to os.set_blocking on Windows pipes (Python >= 3.12),
        # using the same native operation for the supported Python 3.11 too.
        import msvcrt

        mode = wintypes.DWORD(1)  # PIPE_NOWAIT
        _win_check(
            _kernel32.SetNamedPipeHandleState(
                msvcrt.get_osfhandle(fd), ctypes.byref(mode), None, None
            )
        )
    else:
        os.set_blocking(fd, False)


if os.name == "nt":

    class _WindowsJob:
        def __init__(self) -> None:
            self.handle = _kernel32.CreateJobObjectW(None, None)
            _win_check(self.handle)
            limits = _ExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            try:
                _win_check(
                    _kernel32.SetInformationJobObject(
                        self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                    )
                )
            except BaseException:
                self.close()
                raise

        def assign(self, proc: subprocess.Popen[bytes]) -> None:
            _win_check(
                _kernel32.AssignProcessToJobObject(self.handle, int(proc._handle))
            )

        def kill(self) -> None:
            _win_check(_kernel32.TerminateJobObject(self.handle, 1))

        def close(self) -> None:
            if self.handle:
                _win_check(_kernel32.CloseHandle(self.handle))
                self.handle = None


class ProcessHandle:
    """One-shot external command with merged binary stdout/stderr.

    ``str`` commands use the platform shell; argv commands execute directly.
    ``write`` never appends a newline. Windows uses pipes and CTRL_BREAK for
    interrupt/terminate; it cannot provide a POSIX terminal or SIGTERM.
    """

    _WRITE_TIMEOUT = 1.0

    def __init__(
        self,
        cmd: str | Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        pty: bool = False,
        pty_rows: int | None = None,
        pty_cols: int | None = None,
    ) -> None:
        if isinstance(cmd, str):
            if not cmd.strip():
                raise ValueError("cmd must not be empty")
            self.cmd: str | list[str] = cmd
        else:
            self.cmd = list(cmd)
            if not self.cmd or not all(isinstance(part, str) for part in self.cmd):
                raise ValueError("cmd must be a nonempty sequence of strings")
            if not self.cmd[0]:
                raise ValueError("cmd executable must not be empty")
        if pty and os.name != "posix":
            raise ValueError("PTY is supported only on POSIX; use pty=False on Windows")
        if not pty and (pty_rows is not None or pty_cols is not None):
            raise ValueError("pty_rows/pty_cols require pty=True")
        for value in (pty_rows, pty_cols):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 65535
            ):
                raise ValueError("PTY dimensions must be integers between 1 and 65535")
        self.cwd = cwd
        self.env = dict(env) if env is not None else None
        self.pty = pty
        self.pty_rows = pty_rows or 24
        self.pty_cols = pty_cols or 80
        self._proc: subprocess.Popen[bytes] | None = None
        self._master: int | None = None
        self._windows_job: _WindowsJob | None = None
        self._write_lock = threading.Lock()
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def start(self) -> None:
        if self._proc is not None or self._closed:
            raise RuntimeError("ProcessHandle can only be started once")
        env = os.environ.copy()
        if self.env is not None:
            env.update(self.env)
        options: dict = dict(cwd=self.cwd, env=env, bufsize=0)
        if os.name == "nt":
            self._start_windows(options)
        elif self.pty:
            self._start_pty(options)
        else:
            self._proc = subprocess.Popen(
                self.cmd,
                shell=isinstance(self.cmd, str),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                **options,
            )
            assert self._proc.stdin is not None
            _nonblocking_input(self._proc.stdin.fileno())

    def _start_windows(self, options: dict) -> None:
        # Assign a gated launcher before it can create any descendants. Popen
        # alone cannot atomically assign a Job Object to an already-running child.
        containment = _WindowsJob()
        args = [
            sys.executable,
            "-u",
            str(Path(__file__).resolve()),
            "--wave-launch",
            "shell" if isinstance(self.cmd, str) else "argv",
        ]
        args.extend([self.cmd] if isinstance(self.cmd, str) else self.cmd)
        proc = None
        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                **options,
            )
            containment.assign(proc)
            assert proc.stdin is not None
            # Change wait mode before releasing the launcher: changing it after
            # the gate can race command startup and fail with ERROR_PIPE_BUSY.
            _nonblocking_input(proc.stdin.fileno())
            if proc.stdin.write(b"\0") != 1:
                raise BrokenPipeError("Could not release the process launcher")
            self._proc = proc
            self._windows_job = containment
        except BaseException:
            if proc is not None:
                proc.kill()
                proc.wait()
                for stream in (proc.stdin, proc.stdout):
                    if stream is not None:
                        stream.close()
            containment.close()
            raise

    def _start_pty(self, options: dict) -> None:
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()
        try:
            fcntl.ioctl(
                slave,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", self.pty_rows, self.pty_cols, 0, 0),
            )
            _nonblocking_input(master)
            args = [
                sys.executable,
                str(Path(__file__).with_name("_pty_exec.py")),
                "shell" if isinstance(self.cmd, str) else "argv",
            ]
            args.extend([self.cmd] if isinstance(self.cmd, str) else self.cmd)
            self._proc = subprocess.Popen(
                args,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                **options,
            )
            self._master = master
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self._closed:
            raise RuntimeError("ProcessHandle is closed")
        if self._proc is None:
            raise RuntimeError("ProcessHandle has not started")
        return self._proc

    def poll(self) -> int | None:
        return self._proc.poll() if self._proc is not None else None

    def read(self) -> bytes:
        if self._closed:
            return b""
        proc = self._require_process()
        try:
            if self._master is not None:
                import select

                while not self._closed:
                    try:
                        return os.read(self._master, 4096)
                    except BlockingIOError:
                        select.select([self._master], [], [], 0.1)
                return b""
            assert proc.stdout is not None
            return proc.stdout.read(4096)
        except OSError as exc:
            if self.pty and exc.errno == errno.EIO:
                return b""
            if self._closed and exc.errno in (errno.EBADF, errno.EINVAL):
                return b""
            raise

    def write(self, text: str) -> None:
        """Write exactly this text, with a one-second transport deadline.

        On timeout, a prefix may already be in the command's input pipe. There
        is no background writer and the unsent suffix is discarded, so callers
        must not automatically resend the original text.
        """
        data = memoryview(text.encode("utf-8"))
        total = len(data)
        deadline = time.monotonic() + self._WRITE_TIMEOUT
        if not self._write_lock.acquire(timeout=self._WRITE_TIMEOUT):
            raise TimeoutError(
                "Process input is busy; no bytes written by this request"
            )
        try:
            proc = self._require_process()
            if proc.poll() is not None:
                raise BrokenPipeError("Process has already exited")
            fd = self._master
            if fd is None:
                assert proc.stdin is not None
                fd = proc.stdin.fileno()
            while data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Process input timed out after writing {total-len(data)}/{total} bytes; "
                        "input may be partial, do not automatically retry"
                    )
                if self._closed:
                    raise BrokenPipeError("Process input was closed")
                try:
                    written = os.write(fd, data)
                except BlockingIOError:
                    written = 0
                except OSError as exc:
                    # PIPE_NOWAIT reports ERROR_NO_DATA for a full pipe; older
                    # Windows CRT/Python versions expose it as ENOSPC instead.
                    # This fd is an input pipe, not a filesystem write.
                    if os.name == "nt" and (
                        getattr(exc, "winerror", None) == 232 or exc.errno == errno.ENOSPC
                    ):
                        written = 0
                    else:
                        raise
                if written:
                    data = data[written:]
                    continue
                if proc.poll() is not None:
                    raise BrokenPipeError("Process exited before all input was written")
                if os.name == "posix":
                    import select

                    select.select([], [fd], [], min(remaining, 0.05))
                else:
                    time.sleep(min(remaining, 0.01))
        finally:
            self._write_lock.release()

    def send_signal(self, sig: int) -> None:
        proc = self._require_process()
        if os.name == "posix":
            os.killpg(proc.pid, sig)
        else:
            # Windows only has a few native signal operations. Never emulate a
            # signal by writing a control character into a pipe.
            if sig != signal.CTRL_BREAK_EVENT:
                raise ValueError(
                    "Windows transport supports CTRL_BREAK_EVENT; use kill for the tree"
                )
            if proc.poll() is not None:
                raise ProcessLookupError("Process has already exited")
            os.kill(proc.pid, sig)

    def interrupt(self) -> None:
        proc = self._require_process()
        if self._master is not None:
            self.write("\x03")
        elif os.name == "nt":
            self.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid, signal.SIGINT)

    def terminate(self) -> None:
        if os.name == "nt":
            self.interrupt()
        else:
            self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        proc = self._require_process()
        if self._windows_job is not None:
            self._windows_job.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def cleanup(self) -> None:
        """Kill remaining descendants after main exit, without closing output.

        The caller must poll independently of blocking reads: descendants can
        hold output open after the main process has exited. This method makes
        those reads reach EOF while retaining bytes already in the pipe/PTY.
        """
        if not self._closed and self._proc is not None and self.poll() is not None:
            self.kill()

    def close(self) -> None:
        """Close transport resources; safe to repeat after draining output.

        The owning Job must stop/reap the main process before closing. Closing
        the Windows Job Object also forcibly terminates any remaining children.
        """
        if self._closed:
            return
        self._closed = True
        # A concurrent writer must finish before its descriptor can be reused
        # by another process handle. Writes are bounded and observe _closed.
        with self._write_lock:
            if self._master is not None:
                os.close(self._master)
                self._master = None
            if self._proc is not None:
                for stream in (self._proc.stdin, self._proc.stdout):
                    if stream is not None:
                        stream.close()
            if self._windows_job is not None:
                self._windows_job.close()
                self._windows_job = None


def _windows_launch() -> int:
    """Wait for containment, then run in the launcher's console process group."""
    if os.read(sys.stdin.fileno(), 1) != b"\0":
        return 125
    # CTRL_BREAK must reach the command, while its supervising launcher keeps
    # waiting and returns the command's exit code rather than exiting early.
    signal.signal(signal.SIGBREAK, lambda *_: None)
    mode, *args = sys.argv[2:]
    command = args[0] if mode == "shell" else args
    try:
        return subprocess.call(command, shell=mode == "shell")
    except OSError as exc:
        print(f"Could not start command: {exc}", file=sys.stderr, flush=True)
        return 127


if __name__ == "__main__" and os.name == "nt" and sys.argv[1:2] == ["--wave-launch"]:
    raise SystemExit(_windows_launch())
