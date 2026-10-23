"""Acquire a controlling terminal inside a fresh session, then replace self.

Kept separate so the parent never uses preexec_fn in a threaded application.
Arguments are ordinary argv, not a serialized job definition.
"""

import fcntl
import os
import signal
import sys
import termios


def main() -> None:
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())
    # Python ignores SIGPIPE; external commands should receive its default.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    mode, *args = sys.argv[1:]
    if mode == "shell":
        args = ["/bin/sh", "-c", args[0]]
    os.execvpe(args[0], args, os.environ)


if __name__ == "__main__":
    main()
