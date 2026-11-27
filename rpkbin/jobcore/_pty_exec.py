"""Acquire a controlling terminal inside a fresh session, then replace self.

Kept separate so the parent never uses preexec_fn in a threaded application.
Run as ``python -I _pty_exec.py ENV_FD STATUS_FD MODE ARGV...``: the target
environment is JSON on ENV_FD. STATUS_FD closes on a successful exec (EOF);
if exec fails, the OSError is reported there as JSON ``[errno, message, file]``.
"""

import errno
import json
import os
import signal
import sys


def main() -> None:
    import fcntl
    import termios

    env_fd, status_fd = int(sys.argv[1]), int(sys.argv[2])
    os.set_inheritable(status_fd, False)  # close-on-exec: EOF means success
    args: list[str] = []
    try:
        with os.fdopen(env_fd, "rb") as stream:
            env = json.loads(stream.read())
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.tcsetpgrp(0, os.getpgrp())
        # Python ignores SIGPIPE; external commands should receive its default.
        if hasattr(signal, "SIGPIPE"):
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        mode, *args = sys.argv[3:]
        if mode == "shell":
            args = ["/bin/sh", "-c", args[0]]
        os.execvpe(args[0], args, env)
    except Exception as exc:
        code = exc.errno if isinstance(exc, OSError) and exc.errno is not None else errno.EIO
        strerror = (
            exc.strerror
            if isinstance(exc, OSError) and exc.strerror
            else f"{type(exc).__name__}: {exc}"
        )
        filename = (
            exc.filename
            if isinstance(exc, OSError) and exc.filename
            else (args[0] if args else None)
        )
        try:
            report = json.dumps([code, strerror, filename])
            os.write(status_fd, report.encode())
        finally:
            os.close(status_fd)
        os._exit(127)


if __name__ == "__main__":
    main()
