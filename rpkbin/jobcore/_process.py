"""External-process transport; no scheduling or monitoring policy lives here.

Every process owns a POSIX process group or a Windows Job Object. Call
``cleanup()`` after the main process exits, then drain ``read()`` to EOF and
``close()``. This also releases inherited output handles held by descendants.
"""

from __future__ import annotations

import codecs
import contextlib
import errno
import json
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


def _poll_fd(fd: int, *, write: bool, timeout: float) -> None:
    """Wait for readiness; unlike select(), poll() accepts descriptors >= 1024."""
    import select

    poller = select.poll()
    poller.register(fd, select.POLLOUT if write else select.POLLIN)
    poller.poll(timeout * 1000)


_ORIGINAL_POLL_FD = _poll_fd


def _close_fds(*fds: int) -> None:
    for fd in fds:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)


@contextlib.contextmanager
def _echo_muted(fd: int):
    """Clear terminal ECHO for a secret; its echo is generated during write."""
    import termios

    saved = termios.tcgetattr(fd)
    if not saved[3] & termios.ECHO:
        yield
        return
    muted = list(saved)
    muted[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, muted)
    try:
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
        except termios.error:  # Slave side already closed.
            pass


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
        encoding: str = "utf-8",
        pty: bool = False,
        pty_rows: int | None = None,
        pty_cols: int | None = None,
    ) -> None:
        if isinstance(cmd, str):
            if not cmd.strip():
                raise ValueError("cmd must not be empty")
            self.cmd: str | list[str] = cmd
        elif isinstance(cmd, os.PathLike):
            part = os.fspath(cmd)
            if not isinstance(part, str) or not part.strip():
                raise ValueError("cmd executable must not be empty")
            self.cmd = [part]
        elif isinstance(cmd, (bytes, bytearray)):
            raise ValueError("cmd must be a nonempty sequence of strings")
        else:
            parts: list[str] = []
            for part in cmd:
                if isinstance(part, os.PathLike):
                    part = os.fspath(part)
                if not isinstance(part, str):
                    raise ValueError("cmd must be a nonempty sequence of strings")
                parts.append(part)
            self.cmd = parts
            if not self.cmd:
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
        "".encode(encoding)
        self.encoding = codecs.lookup(encoding).name
        self._encoder = codecs.getincrementalencoder(self.encoding)(errors="strict")
        self.pty = pty
        self.pty_rows = pty_rows or 24
        self.pty_cols = pty_cols or 80
        self._proc: subprocess.Popen[bytes] | None = None
        self._master: int | None = None
        self._windows_job: _WindowsJob | None = None
        self._write_lock = threading.Lock()
        # Guards _master so a concurrent close() never leaves a reader with
        # None or a descriptor number already reused elsewhere.
        self._master_lock = threading.Lock()
        self._read_poller = None
        self._write_poller = None
        self._wake_r: int | None = None
        self._wake_w: int | None = None
        self._active_readers = 0
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
            "-I",
            "-u",
            str(Path(__file__).resolve()),
            "--wave-launch",
            "shell" if isinstance(self.cmd, str) else "argv",
        ]
        args.extend([self.cmd] if isinstance(self.cmd, str) else self.cmd)
        proc = None
        stdin_r, stdin_w = os.pipe()
        try:
            # Set non-blocking mode on the write end before spawning so
            # SetNamedPipeHandleState cannot race with an outstanding child read.
            _nonblocking_input(stdin_w)
            launcher_options = dict(options, env=None)
            proc = subprocess.Popen(
                args,
                stdin=stdin_r,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                **launcher_options,
            )
            _close_fds(stdin_r)
            stdin_r = -1
            proc.stdin = os.fdopen(stdin_w, "wb", buffering=0)
            stdin_w = -1
            containment.assign(proc)
            payload = (
                json.dumps(self.env).encode("utf-8") if self.env is not None else b""
            )
            frame = struct.pack("<I", len(payload)) + payload + b"\0"
            view = memoryview(frame)
            deadline = time.monotonic() + 5.0
            while view:
                try:
                    written = proc.stdin.write(view)
                except BlockingIOError:
                    written = 0
                except OSError as exc:
                    if getattr(exc, "winerror", None) != 232 and exc.errno != errno.ENOSPC:
                        raise
                    written = 0
                if written:
                    view = view[written:]
                    continue
                if proc.poll() is not None or time.monotonic() >= deadline:
                    raise BrokenPipeError("Could not pass environment to process launcher")
                time.sleep(0.005)
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
        finally:
            _close_fds(stdin_r, stdin_w)

    def _start_pty(self, options: dict) -> None:
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()
        env_r = env_w = status_r = status_w = wake_r = wake_w = -1
        proc = None
        try:
            fcntl.ioctl(
                slave,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", self.pty_rows, self.pty_cols, 0, 0),
            )
            _nonblocking_input(master)
            wake_r, wake_w = os.pipe()
            _nonblocking_input(wake_r)
            # The target environment travels over env_r; status_w is closed by
            # the kernel when the launcher's exec succeeds (EOF), otherwise the
            # launcher reports the exec OSError there.
            env_r, env_w = os.pipe()
            status_r, status_w = os.pipe()
            args = [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("_pty_exec.py")),
                str(env_r),
                str(status_w),
                "shell" if isinstance(self.cmd, str) else "argv",
            ]
            args.extend([self.cmd] if isinstance(self.cmd, str) else self.cmd)
            proc = subprocess.Popen(
                args,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                pass_fds=(env_r, status_w),
                cwd=options["cwd"],
                env=os.environ.copy(),  # Launcher env; never the job's.
                bufsize=0,
            )
            _close_fds(env_r, status_w)
            env_r = status_w = -1
            payload = json.dumps(options["env"]).encode()
            stream, env_w = os.fdopen(env_w, "wb"), -1
            with contextlib.suppress(BrokenPipeError), stream:
                stream.write(payload)
            with os.fdopen(status_r, "rb") as status:
                status_r = -1
                report = status.read()
            if report:
                code, strerror, filename = json.loads(report)
                proc.wait()
                proc = None
                raise OSError(code, strerror, filename)
            self._proc, proc = proc, None
            with self._master_lock:
                self._master, master = master, -1
                self._wake_r, wake_r = wake_r, -1
                self._wake_w, wake_w = wake_w, -1
        except BaseException:
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            raise
        finally:
            _close_fds(master, slave, env_r, env_w, status_r, status_w, wake_r, wake_w)

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self._closed:
            raise RuntimeError("ProcessHandle is closed")
        if self._proc is None:
            raise RuntimeError("ProcessHandle has not started")
        return self._proc

    def poll(self) -> int | None:
        return self._proc.poll() if self._proc is not None else None

    def _wait_fd(
        self, fd: int, *, write: bool, timeout: float, wake_fd: int | None = None
    ) -> None:
        if _poll_fd is not _ORIGINAL_POLL_FD:
            _poll_fd(fd, write=write, timeout=timeout)
            return
        import select

        if write:
            with self._master_lock:
                if self._closed:
                    return
                if self._write_poller is None or self._write_poller[0] != fd:
                    poller = select.poll()
                    poller.register(fd, select.POLLOUT)
                    self._write_poller = (fd, poller)
                else:
                    poller = self._write_poller[1]
            poller.poll(timeout * 1000)
            return
        with self._master_lock:
            if self._closed or self._master is None:
                return
            if (
                self._read_poller is None
                or self._read_poller[0] != fd
                or self._read_poller[1] != wake_fd
            ):
                poller = select.poll()
                poller.register(fd, select.POLLIN)
                if wake_fd is not None:
                    poller.register(wake_fd, select.POLLIN)
                self._read_poller = (fd, wake_fd, poller)
            else:
                poller = self._read_poller[2]
        poller.poll(timeout * 1000)

    def read(self) -> bytes:
        if self._closed:
            return b""
        proc = self._require_process()
        fd = None
        try:
            with self._master_lock:
                fd = self._master
            if fd is not None or self.pty:
                while not self._closed:
                    with self._master_lock:
                        if self._closed or self._master is None:
                            return b""
                        fd = self._master
                        try:
                            return os.read(fd, 4096)
                        except BlockingIOError:
                            self._active_readers += 1
                            wake_fd = self._wake_r
                    try:
                        self._wait_fd(fd, write=False, timeout=0.1, wake_fd=wake_fd)
                    finally:
                        with self._master_lock:
                            self._active_readers -= 1
                            if (
                                self._closed
                                and self._active_readers == 0
                                and self._wake_r is not None
                            ):
                                wake_to_close, self._wake_r = self._wake_r, None
                                _close_fds(wake_to_close)
                return b""
            assert proc.stdout is not None
            return proc.stdout.read(4096)
        except OSError as exc:
            if (self.pty or fd is not None) and exc.errno == errno.EIO:
                return b""
            if self._closed and exc.errno in (errno.EBADF, errno.EINVAL):
                return b""
            raise

    def write(self, text: str, *, secret: bool = False) -> None:
        """Write exactly this text, with a one-second transport deadline.

        On timeout, a prefix may already be in the command's input pipe. There
        is no background writer and the unsent suffix is discarded, so callers
        must not automatically resend the original text. ``secret`` suppresses
        the PTY's echo of this input.
        """
        deadline = time.monotonic() + self._WRITE_TIMEOUT
        if not self._write_lock.acquire(timeout=self._WRITE_TIMEOUT):
            raise TimeoutError(
                "Process input is busy; no bytes written by this request"
            )
        try:
            proc = self._require_process()
            if proc.poll() is not None:
                raise BrokenPipeError("Process has already exited")
            data = memoryview(self._encoder.encode(text))
            # close() needs _write_lock, so this descriptor stays valid here.
            with self._master_lock:
                master = fd = self._master
            if fd is None:
                if self.pty:
                    raise BrokenPipeError("Process input was closed")
                assert proc.stdin is not None
                fd = proc.stdin.fileno()
            muted = _echo_muted(fd) if secret and master is not None else contextlib.nullcontext()
            with muted:
                self._write_all(proc, fd, data, deadline)
        finally:
            self._write_lock.release()

    def _write_all(self, proc, fd: int, data: memoryview, deadline: float) -> None:
        total = len(data)
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Process input timed out after writing {total - len(data)}/{total} bytes; "
                    "input may be partial, do not automatically retry"
                )
            if self._closed:
                raise BrokenPipeError("Process input was closed")
            try:
                # The Windows CRT can reject a request larger than the
                # available pipe buffer without writing any prefix.
                chunk = data[:4096] if os.name == "nt" else data
                written = os.write(fd, chunk)
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
                self._wait_fd(fd, write=True, timeout=min(remaining, 0.05))
            else:
                time.sleep(min(remaining, 0.01))

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

    def _pty_isig(self) -> bool | None:
        """Whether the PTY translates ^C into SIGINT; None without a PTY."""
        with self._master_lock:
            fd = self._master
        if fd is None:
            return None
        import termios

        try:
            return bool(termios.tcgetattr(fd)[3] & termios.ISIG)
        except (termios.error, OSError):
            return False

    def interrupt(self) -> None:
        proc = self._require_process()
        isig = self._pty_isig()
        if isig is not None:
            if isig:
                try:
                    return self.write("\x03")
                except OSError:
                    if proc.poll() is not None:
                        raise
            # ^C is not translated (raw terminal) or could not be written.
            os.killpg(proc.pid, signal.SIGINT)
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
            with self._master_lock:
                master, self._master = self._master, None
                wake_w, self._wake_w = self._wake_w, None
                if wake_w is not None:
                    with contextlib.suppress(OSError):
                        os.write(wake_w, b"\0")
                    _close_fds(wake_w)
                if self._active_readers == 0 and self._wake_r is not None:
                    wake_r, self._wake_r = self._wake_r, None
                    _close_fds(wake_r)
                self._read_poller = None
                self._write_poller = None
                if master is not None:
                    os.close(master)
            if self._proc is not None:
                for stream in (self._proc.stdin, self._proc.stdout):
                    if stream is not None:
                        stream.close()
            if self._windows_job is not None:
                self._windows_job.close()
                self._windows_job = None


def _read_exact(fd: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            raise EOFError("Unexpected EOF from process launcher")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _windows_launch() -> int:
    """Wait for containment, then run in the launcher's console process group."""
    # CTRL_BREAK must reach the command, while its supervising launcher keeps
    # waiting and returns the command's exit code rather than exiting early.
    signal.signal(signal.SIGBREAK, lambda *_: None)
    stdin_fd = sys.stdin.fileno()
    try:
        (env_len,) = struct.unpack("<I", _read_exact(stdin_fd, 4))
        if env_len:
            env = os.environ.copy()
            env.update(json.loads(_read_exact(stdin_fd, env_len)))
        else:
            env = None
        if _read_exact(stdin_fd, 1) != b"\0":
            return 125
    except (EOFError, OSError, ValueError, struct.error):
        return 125
    mode, *args = sys.argv[2:]
    command = args[0] if mode == "shell" else args
    try:
        return subprocess.call(command, shell=mode == "shell", env=env)
    except OSError as exc:
        print(f"Could not start command: {exc}", file=sys.stderr, flush=True)
        return 127


if __name__ == "__main__" and os.name == "nt" and sys.argv[1:2] == ["--wave-launch"]:
    raise SystemExit(_windows_launch())
