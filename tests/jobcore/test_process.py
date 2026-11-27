"""Transport tests use real subprocesses, never simulation-specific commands."""

from __future__ import annotations

import contextlib
import errno
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rpkbin.jobcore._process import ProcessHandle


class ProcessTests(unittest.TestCase):
    def make_process(self, code: str, **kwargs) -> ProcessHandle:
        handle = ProcessHandle([sys.executable, "-u", "-c", code], **kwargs)
        self.addCleanup(self.dispose, handle)
        return handle

    @staticmethod
    def dispose(handle: ProcessHandle) -> None:
        if handle.pid is not None and not handle._closed:
            handle.kill()
            deadline = time.monotonic() + 5
            while handle.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
        handle.close()

    def collect(self, handle: ProcessHandle) -> tuple[threading.Thread, bytearray]:
        output = bytearray()

        def read_all() -> None:
            while chunk := handle.read():
                output.extend(chunk)

        reader = threading.Thread(target=read_all, daemon=True)
        reader.start()
        return reader, output

    def until(self, predicate, timeout: float = 8) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate(), "Timed out waiting for subprocess")

    def finish(self, handle: ProcessHandle, reader: threading.Thread) -> int:
        self.until(lambda: handle.poll() is not None)
        handle.cleanup()
        reader.join(5)
        self.assertFalse(reader.is_alive(), "Output failed to reach EOF after cleanup")
        return handle.poll()

    def test_constructor_does_not_spawn(self):
        handle = self.make_process("raise SystemExit(0)")
        self.assertIsNone(handle.pid)
        self.assertIsNone(handle.poll())
        with self.assertRaisesRegex(RuntimeError, "not started"):
            handle.write("test")

    def test_exact_text_roundtrip_and_merged_output(self):
        handle = self.make_process(
            "import os,sys; print('READY',flush=True); "
            "data=sys.stdin.buffer.read(5); os.write(1,b'OUT:'+data); os.write(2,b'ERR')"
        )
        handle.start()
        reader, output = self.collect(handle)
        self.until(lambda: b"READY" in output)
        handle.write("abcde")  # No implicit newline: child reads precisely five bytes.
        self.assertEqual(self.finish(handle, reader), 0)
        self.assertIn(b"OUT:abcdeERR", output)

    def test_nonzero_exit_and_output_survives_cleanup(self):
        handle = self.make_process("print('final evidence'); raise SystemExit(7)")
        handle.start()
        self.until(lambda: handle.poll() is not None)
        handle.cleanup()
        reader, output = self.collect(handle)
        reader.join(5)
        self.assertFalse(reader.is_alive())
        self.assertEqual(handle.poll(), 7)
        self.assertIn(b"final evidence", output)

    @unittest.skipUnless(os.name == "nt", "Windows CRT pipe error mapping")
    def test_windows_full_pipe_enospc_can_resume_without_losing_input(self):
        handle = self.make_process("import os; os.write(1,os.read(0,5))")
        handle.start()
        reader, output = self.collect(handle)
        real_write = os.write
        calls = 0

        def write_with_full_pipe(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.ENOSPC, "Windows nonblocking pipe full")
            return real_write(fd, data)

        with patch("rpkbin.jobcore._process.os.write", side_effect=write_with_full_pipe):
            handle.write("EXACT")
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(self.finish(handle, reader), 0)
        self.assertEqual(output, b"EXACT")

    def test_env_merges_and_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            handle = self.make_process(
                "import os; print(os.environ['WAVE_TEST_VALUE']); "
                "print(os.getcwd()); print(bool(os.environ.get('PATH')))",
                cwd=directory,
                env={"WAVE_TEST_VALUE": "transport-test", "PYTHONIOENCODING": "utf-8"},
            )
            handle.start()
            reader, output = self.collect(handle)
            self.assertEqual(self.finish(handle, reader), 0)
            decoded = output.decode()
            self.assertIn("transport-test", decoded)
            self.assertIn(directory, decoded)
            self.assertIn("True", decoded)
            handle.close()

    def test_shell_string(self):
        handle = ProcessHandle("echo WAVE_SHELL")
        self.addCleanup(self.dispose, handle)
        handle.start()
        reader, output = self.collect(handle)
        self.assertEqual(self.finish(handle, reader), 0)
        self.assertIn(b"WAVE_SHELL", output)

    def test_force_kills_live_tree(self):
        child = "import time; time.sleep(60)"
        handle = self.make_process(
            f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); "
            "print('TREE_READY',flush=True); time.sleep(60)"
        )
        handle.start()
        reader, output = self.collect(handle)
        self.until(lambda: b"TREE_READY" in output)
        handle.kill()
        self.assertNotEqual(self.finish(handle, reader), 0)

    def test_nonreading_stdin_times_out_without_late_writer(self):
        handle = self.make_process(
            "import time; print('NOT_READING',flush=True); time.sleep(60)"
        )
        handle.start()
        reader, output = self.collect(handle)
        self.until(lambda: b"NOT_READING" in output)
        before = time.monotonic()
        with self.assertRaisesRegex(
            TimeoutError, "partial.*do not automatically retry"
        ) as caught:
            handle.write("x" * (8 * 1024 * 1024))
        # An empty pipe accepts a bounded prefix even when the full request is
        # larger than its buffer (including the Python 3.11 Windows CRT).
        self.assertRegex(str(caught.exception), r"writing [1-9][0-9]*/8388608 bytes")
        self.assertLess(time.monotonic() - before, 3)
        # Timeout leaves no pending writer. Cleanup is immediately reachable,
        # and a different handle can receive its exact input without contamination.
        handle.kill()
        self.assertNotEqual(self.finish(handle, reader), 0)
        handle.close()
        other = self.make_process("import os; os.write(1,os.read(0,4))")
        other.start()
        other_reader, other_output = self.collect(other)
        other.write("NEXT")
        self.assertEqual(self.finish(other, other_reader), 0)
        self.assertTrue(other_output.endswith(b"NEXT"), repr(other_output))

    def test_timed_out_suffix_is_not_sent_when_same_process_starts_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory).resolve() / "start-reading"
            handle = self.make_process(
                "import os,time,pathlib; print('READY',flush=True)\n"
                f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n"
                "data=os.read(0,8*1024*1024); print('DRAINED',len(data),flush=True); "
                "print('SECOND',os.read(0,4).decode(),flush=True)"
            )
            handle.start()
            reader, output = self.collect(handle)
            self.until(lambda: b"READY" in output)
            with self.assertRaises(TimeoutError):
                handle.write("x" * (8 * 1024 * 1024))
            release.touch()
            self.until(lambda: b"DRAINED" in output)
            handle.write("NEXT")
            self.assertEqual(self.finish(handle, reader), 0)
            self.assertIn(b"SECOND NEXT", output)
            handle.close()

    @unittest.skipUnless(
        os.name == "nt", "Windows pipe wait mode must be set before gate release"
    )
    def test_windows_repeated_gated_startup(self):
        for _ in range(10):
            handle = self.make_process("print('STARTED',flush=True)")
            handle.start()
            reader, output = self.collect(handle)
            self.assertEqual(self.finish(handle, reader), 0)
            self.assertIn(b"STARTED", output)
            handle.close()

    @unittest.skipUnless(os.name == "posix", "Requires POSIX nonblocking PTY")
    def test_posix_nonreading_pty_input_times_out(self):
        handle = self.make_process(
            "import time,tty; tty.setraw(0); print('NOT_READING',flush=True); time.sleep(60)",
            pty=True,
        )
        handle.start()
        reader, output = self.collect(handle)
        self.until(lambda: b"NOT_READING" in output)
        with self.assertRaisesRegex(TimeoutError, "partial"):
            handle.write("x" * (8 * 1024 * 1024))
        handle.kill()
        self.assertNotEqual(self.finish(handle, reader), 0)

    def test_cleanup_removes_descendants_after_parent_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "escaped-child.txt"
            child = f"import time,pathlib; time.sleep(1.5); pathlib.Path({str(marker)!r}).write_text('escaped')"
            handle = self.make_process(
                f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}]); "
                "print('parent completed',flush=True)"
            )
            handle.start()
            reader, output = self.collect(handle)
            self.assertEqual(self.finish(handle, reader), 0)
            self.assertIn(b"parent completed", output)
            time.sleep(1.6)
            self.assertFalse(
                marker.exists(), "Descendant survived cleanup after main exit"
            )
            handle.close()

    def test_interrupt_delivers_real_signal(self):
        sig_name = "SIGBREAK" if os.name == "nt" else "SIGINT"
        handle = self.make_process(
            f"import signal,time,sys; signal.signal(signal.{sig_name}, "
            "lambda *args: (print('SIGNAL_RECEIVED',flush=True),sys.exit(0))); "
            "print('SIGNAL_READY',flush=True)\nwhile True: time.sleep(0.01)"
        )
        handle.start()
        reader, output = self.collect(handle)
        self.until(lambda: b"SIGNAL_READY" in output)
        handle.interrupt()
        self.assertEqual(self.finish(handle, reader), 0)
        self.assertIn(b"SIGNAL_RECEIVED", output)

    def test_invalid_dimensions(self):
        with self.assertRaisesRegex(ValueError, "require pty"):
            ProcessHandle([sys.executable], pty_rows=20)

    @unittest.skipUnless(os.name == "nt", "Windows rejects unavailable POSIX PTYs")
    def test_windows_rejects_pty(self):
        with self.assertRaisesRegex(ValueError, "only on POSIX"):
            ProcessHandle([sys.executable], pty=True)

    @unittest.skipUnless(os.name == "nt", "Windows console control event delivery")
    def test_windows_interrupt_without_user_console(self):
        # A GUI may launch the core without an attached console. Repeat the
        # actual signal handler check inside such a process, rather than merely
        # asserting that GenerateConsoleCtrlEvent returned success.
        code = (
            "import sys,unittest; "
            f"sys.path.insert(0,{str(Path(__file__).parent.resolve())!r}); "
            "unittest.main(module='test_process', "
            "argv=['test_process','ProcessTests.test_interrupt_delivers_real_signal'])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, repr(result.stdout))

    @unittest.skipUnless(os.name == "posix", "Requires POSIX controlling terminal")
    def test_posix_pty_controlling_terminal_dimensions_and_interrupt(self):
        handle = self.make_process(
            "import os,signal,sys,time; "
            "print('TTY',os.isatty(0),os.tcgetpgrp(0)==os.getpgrp(),os.get_terminal_size()); "
            "signal.signal(signal.SIGINT,lambda *args: (print('INTERRUPTED',flush=True),sys.exit(0))); "
            "print('READY',flush=True); time.sleep(60)",
            pty=True,
            pty_rows=37,
            pty_cols=101,
        )
        handle.start()
        reader, output = self.collect(handle)
        self.until(lambda: b"READY" in output)
        handle.interrupt()
        self.assertEqual(self.finish(handle, reader), 0)
        self.assertIn(b"TTY True True", output)
        self.assertIn(b"columns=101, lines=37", output)
        self.assertIn(b"INTERRUPTED", output)

    def test_pty_read_and_write_use_poll_for_high_file_descriptors(self):
        from types import SimpleNamespace

        handle = ProcessHandle([sys.executable, "-c", "pass"])
        handle._proc = SimpleNamespace(pid=4321, poll=lambda: None, stdin=None, stdout=None)
        handle._master = 1500
        handle.pty = True
        registrations = []

        class FakePoller:
            def register(self, fd, eventmask):
                registrations.append((fd, eventmask))

            def poll(self, timeout):
                return [(1500, 1)]

        fake_select = SimpleNamespace(
            POLLIN=1,
            POLLOUT=4,
            poll=FakePoller,
            select=lambda *args: (_ for _ in ()).throw(
                ValueError("filedescriptor out of range in select()")
            ),
        )
        read_attempts = 0

        def fake_read(fd, size):
            nonlocal read_attempts
            self.assertEqual(fd, 1500)
            read_attempts += 1
            if read_attempts == 1:
                raise BlockingIOError
            return b"high-fd-data"

        write_attempts = 0

        def fake_write(fd, data):
            nonlocal write_attempts
            self.assertEqual(fd, 1500)
            write_attempts += 1
            if write_attempts == 1:
                raise BlockingIOError
            return len(data)

        with (
            patch.dict(sys.modules, {"select": fake_select}),
            patch("rpkbin.jobcore._process.os.name", "posix"),
            patch("rpkbin.jobcore._process.os.read", side_effect=fake_read),
            patch("rpkbin.jobcore._process.os.write", side_effect=fake_write),
        ):
            self.assertEqual(handle.read(), b"high-fd-data")
            handle.write("secret-ok")
        self.assertEqual(registrations, [(1500, 1), (1500, 4)])
        handle._master = None

    def test_pty_secret_write_temporarily_mutes_and_restores_echo(self):
        from types import SimpleNamespace

        handle = ProcessHandle([sys.executable, "-c", "pass"])
        handle._proc = SimpleNamespace(pid=4321, poll=lambda: None, stdin=None, stdout=None)
        handle._master = 42
        handle.pty = True

        class TermiosError(OSError):
            pass

        attrs = [0, 0, 0, 0x08 | 0x01, 0, 0, []]
        observed_during_write = []
        fail_restore = False

        def tcgetattr(fd):
            self.assertEqual(fd, 42)
            return list(attrs)

        def tcsetattr(fd, when, new_attrs):
            self.assertEqual(fd, 42)
            self.assertEqual(when, 0)
            if fail_restore and (new_attrs[3] & 0x08):
                raise TermiosError("slave already closed")
            attrs[:] = list(new_attrs)

        fake_termios = SimpleNamespace(
            ECHO=0x08,
            ISIG=0x01,
            TCSANOW=0,
            error=TermiosError,
            tcgetattr=tcgetattr,
            tcsetattr=tcsetattr,
        )

        def fake_write(fd, data):
            self.assertEqual(fd, 42)
            observed_during_write.append(attrs[3] & 0x08)
            return len(data)

        with (
            patch.dict(sys.modules, {"termios": fake_termios}),
            patch("rpkbin.jobcore._process.os.write", side_effect=fake_write),
        ):
            handle.write("hunter2", secret=True)
            self.assertEqual(observed_during_write, [0])
            self.assertEqual(attrs[3] & 0x08, 0x08)

            fail_restore = True
            handle.write("hunter2", secret=True)
            self.assertEqual(observed_during_write, [0, 0])
        handle._master = None

    def test_pty_interrupt_prefers_ctrl_c_when_isig_enabled_and_falls_back_to_killpg(self):
        from types import SimpleNamespace

        handle = ProcessHandle([sys.executable, "-c", "pass"])
        handle._proc = SimpleNamespace(pid=9876, poll=lambda: None, stdin=None, stdout=None)
        handle._master = 55
        handle.pty = True
        lflag = 0x01
        written = []
        killed = []

        fake_termios = SimpleNamespace(
            ISIG=0x01,
            error=OSError,
            tcgetattr=lambda fd: [0, 0, 0, lflag, 0, 0, []],
        )

        with (
            patch.dict(sys.modules, {"termios": fake_termios}),
            patch(
                "rpkbin.jobcore._process.os.write",
                side_effect=lambda fd, data: (written.append((fd, bytes(data))), len(data))[1],
            ),
            patch(
                "rpkbin.jobcore._process.os.killpg",
                side_effect=lambda pid, sig: killed.append((pid, sig)),
                create=True,
            ),
        ):
            handle.interrupt()
            self.assertEqual(written, [(55, b"\x03")])
            self.assertEqual(killed, [])

            lflag = 0
            handle.interrupt()
            self.assertEqual(len(written), 1)
            self.assertEqual(killed, [(9876, os.sys.modules["signal"].SIGINT)])

            lflag = 0x01
            with patch("rpkbin.jobcore._process.os.write", side_effect=OSError(errno.EIO, "pty full")):
                handle.interrupt()
            self.assertEqual(len(killed), 2)
        handle._master = None

    def test_start_pty_uses_isolated_launcher_clean_env_and_propagates_exec_oserror(self):
        from types import SimpleNamespace
        from rpkbin.jobcore import _pty_exec

        handle = ProcessHandle(
            ["missing-pty-binary", "--flag"],
            env={
                "PYTHONHOME": "/poison/home",
                "PYTHONPATH": "/poison/path",
                "LD_LIBRARY_PATH": "/poison/ld",
                "JOB_SECRET_ENV": "expected-in-target",
            },
        )
        handle.pty = True
        m_r, m_w = os.pipe()
        s_r, s_w = os.pipe()
        os.close(m_w)
        os.close(s_w)
        fake_pty = SimpleNamespace(openpty=lambda: (m_r, s_r))
        fake_fcntl = SimpleNamespace(ioctl=lambda *args: 0)
        fake_termios = SimpleNamespace(TIOCSWINSZ=1, TIOCSCTTY=2)
        captured_popen = {}
        exec_calls = []

        def fake_popen(args, **kwargs):
            captured_popen["args"] = list(args)
            captured_popen["kwargs"] = dict(kwargs)
            env_fd, status_fd = int(args[3]), int(args[4])
            dup_env = os.dup(env_fd)
            dup_status = os.dup(status_fd)

            def fake_execvpe(file, argv, env):
                exec_calls.append((file, list(argv), dict(env)))
                raise FileNotFoundError(errno.ENOENT, "No such file or directory", file)

            def child_runner():
                with (
                    patch.object(sys, "argv", [args[2], str(dup_env), str(dup_status), *args[5:]]),
                    patch.dict(sys.modules, {"fcntl": fake_fcntl, "termios": fake_termios}),
                    patch("os.tcsetpgrp", lambda *_: None, create=True),
                    patch("os.getpgrp", lambda: 111, create=True),
                    patch("rpkbin.jobcore._pty_exec.signal.signal", lambda *_: None),
                    patch("os.execvpe", side_effect=fake_execvpe),
                    patch("os._exit", side_effect=SystemExit),
                ):
                    with self.assertRaises(SystemExit):
                        _pty_exec.main()

            child = threading.Thread(target=child_runner, daemon=True)
            child.start()
            return SimpleNamespace(
                pid=555,
                poll=lambda: None if child.is_alive() else 127,
                wait=lambda: (child.join(5), 127)[1],
                kill=lambda: None,
            )

        with (
            patch.dict(sys.modules, {"pty": fake_pty, "fcntl": fake_fcntl, "termios": fake_termios}),
            patch("rpkbin.jobcore._process._nonblocking_input", lambda fd: None),
            patch("rpkbin.jobcore._process.subprocess.Popen", side_effect=fake_popen),
        ):
            with self.assertRaises(FileNotFoundError) as caught:
                handle._start_pty({"cwd": None, "env": {**os.environ, **handle.env}, "bufsize": 0})

        self.assertEqual(caught.exception.filename, "missing-pty-binary")
        self.assertEqual(captured_popen["args"][:2], [sys.executable, "-I"])
        launcher_env = captured_popen["kwargs"]["env"]
        self.assertNotEqual(launcher_env.get("PYTHONHOME"), "/poison/home")
        self.assertNotEqual(launcher_env.get("PYTHONPATH"), "/poison/path")
        self.assertNotEqual(launcher_env.get("LD_LIBRARY_PATH"), "/poison/ld")
        self.assertNotIn("JOB_SECRET_ENV", launcher_env)
        self.assertEqual(len(exec_calls), 1)
        self.assertEqual(exec_calls[0][0], "missing-pty-binary")
        self.assertEqual(exec_calls[0][2]["JOB_SECRET_ENV"], "expected-in-target")
        self.assertEqual(exec_calls[0][2]["PYTHONHOME"], "/poison/home")

    def test_concurrent_close_during_pty_read_never_passes_none_fd(self):
        from types import SimpleNamespace

        handle = ProcessHandle([sys.executable, "-c", "pass"])
        handle._proc = SimpleNamespace(pid=4321, poll=lambda: 0, stdin=None, stdout=None)
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        handle._master = r_fd
        handle.pty = True
        seen_fds = []
        entered_poll = threading.Event()
        closed_done = threading.Event()

        def fake_read(fd, size):
            seen_fds.append(fd)
            raise BlockingIOError

        def fake_poll_fd(fd, *, write, timeout):
            seen_fds.append(fd)
            entered_poll.set()
            closed_done.wait(2)

        with (
            patch("rpkbin.jobcore._process.os.read", side_effect=fake_read),
            patch("rpkbin.jobcore._process._poll_fd", side_effect=fake_poll_fd),
        ):
            result = []
            errors = []

            def reader():
                try:
                    result.append(handle.read())
                except BaseException as exc:
                    errors.append(exc)

            t = threading.Thread(target=reader)
            t.start()
            self.assertTrue(entered_poll.wait(2))
            handle.close()
            closed_done.set()
            t.join(2)

        self.assertEqual(errors, [])
        self.assertEqual(result, [b""])
        self.assertTrue(all(isinstance(fd, int) for fd in seen_fds), repr(seen_fds))

    def test_launcher_isolates_python_env_from_poisoned_job_pythonpath(self):
        from rpkbin.jobcore import Job, JobManager, JobStatus, ProcessVerdict

        with tempfile.TemporaryDirectory() as directory:
            poison = Path(directory) / "subprocess.py"
            poison.write_text("raise RuntimeError('poisoned')\n", encoding="utf-8")
            manager = JobManager()
            try:
                job = manager.add(
                    Job(
                        "poison",
                        [sys.executable, "-c", "print('OK')"],
                        env={"PYTHONPATH": directory},
                        pty=False,
                    )
                )
                manager.start()
                self.assertTrue(manager._drain(8))
                self.assertIs(job.status, JobStatus.DONE)
                self.assertEqual(job.returncode, 0)
                self.assertIn("OK", job.logs())
                self.assertIs(job.result.verdict, ProcessVerdict.NA)
            finally:
                manager.close()

    def test_pty_read_holds_master_lock_and_reuses_poller_across_retries(self):
        from types import SimpleNamespace

        handle = ProcessHandle([sys.executable, "-c", "pass"])
        handle._proc = SimpleNamespace(pid=4321, poll=lambda: None, stdin=None, stdout=None)
        handle._master = 1500
        handle.pty = True
        registrations = []
        poller_count = 0

        class FakePoller:
            def __init__(self):
                nonlocal poller_count
                poller_count += 1

            def register(self, fd, eventmask):
                registrations.append((fd, eventmask))

            def poll(self, timeout):
                return [(1500, 1)]

        fake_select = SimpleNamespace(POLLIN=1, POLLOUT=4, poll=FakePoller)
        attempts = 0

        def fake_read(fd, size):
            nonlocal attempts
            self.assertTrue(handle._master_lock.locked())
            attempts += 1
            if attempts <= 2:
                raise BlockingIOError
            return b"ok"

        with (
            patch.dict(sys.modules, {"select": fake_select}),
            patch("rpkbin.jobcore._process.os.read", side_effect=fake_read),
        ):
            self.assertEqual(handle.read(), b"ok")
            attempts = 1
            self.assertEqual(handle.read(), b"ok")
        self.assertEqual(poller_count, 1)
        self.assertEqual(registrations, [(1500, 1)])
        handle._master = None

    def test_start_pty_reports_non_exec_setup_errors_via_status_fd(self):
        from types import SimpleNamespace
        from rpkbin.jobcore import _pty_exec

        handle = ProcessHandle([sys.executable, "-c", "pass"])
        handle.pty = True
        m_r, m_w = os.pipe()
        s_r, s_w = os.pipe()
        os.close(m_w)
        os.close(s_w)
        fake_pty = SimpleNamespace(openpty=lambda: (m_r, s_r))

        def failing_ioctl(fd, req, arg):
            if req == 2:  # TIOCSCTTY
                raise PermissionError(errno.EPERM, "Operation not permitted")
            return 0

        fake_fcntl = SimpleNamespace(ioctl=failing_ioctl)
        fake_termios = SimpleNamespace(TIOCSWINSZ=1, TIOCSCTTY=2)

        def fake_popen(args, **kwargs):
            env_fd, status_fd = int(args[3]), int(args[4])
            dup_env = os.dup(env_fd)
            dup_status = os.dup(status_fd)

            def child_runner():
                with (
                    patch.object(sys, "argv", [args[2], str(dup_env), str(dup_status), *args[5:]]),
                    patch.dict(sys.modules, {"fcntl": fake_fcntl, "termios": fake_termios}),
                    patch("os._exit", side_effect=SystemExit),
                ):
                    with self.assertRaises(SystemExit):
                        _pty_exec.main()

            child = threading.Thread(target=child_runner, daemon=True)
            child.start()
            return SimpleNamespace(
                pid=555,
                poll=lambda: None if child.is_alive() else 127,
                wait=lambda: (child.join(5), 127)[1],
                kill=lambda: None,
            )

        with (
            patch.dict(sys.modules, {"pty": fake_pty, "fcntl": fake_fcntl, "termios": fake_termios}),
            patch("rpkbin.jobcore._process._nonblocking_input", lambda fd: None),
            patch("rpkbin.jobcore._process.subprocess.Popen", side_effect=fake_popen),
        ):
            with self.assertRaises(PermissionError):
                handle._start_pty({"cwd": None, "env": dict(os.environ), "bufsize": 0})

    def test_process_handle_accepts_pathlike_cmd(self):
        single = ProcessHandle(Path(sys.executable))
        self.assertEqual(single.cmd, [sys.executable])
        handle = ProcessHandle([Path(sys.executable), "-u", "-c", "print('PATH_OK')"])
        self.addCleanup(self.dispose, handle)
        handle.start()
        reader, output = self.collect(handle)
        self.assertEqual(self.finish(handle, reader), 0)
        self.assertIn(b"PATH_OK", output)

    def test_wait_fd_close_race_does_not_raise_typeerror(self):
        handle = ProcessHandle([sys.executable, "-c", "print('OK')"])
        self.addCleanup(self.dispose, handle)
        r1, w1 = os.pipe()
        r2, w2 = os.pipe()

        def safe_close(fd):
            with contextlib.suppress(OSError):
                os.close(fd)

        for fd in (r1, w1, r2, w2):
            self.addCleanup(safe_close, fd)
        handle._master = r1
        handle._wake_r = r2

        class FakePoller:
            def __init__(self, on_poll):
                self._fds = []
                self._on_poll = on_poll

            def register(self, fd, events):
                self._fds.append((fd, events))

            def poll(self, timeout_ms):
                self._on_poll()
                return []

        fake_select = SimpleNamespace(
            POLLIN=1,
            POLLOUT=4,
            poll=lambda: FakePoller(handle.close),
        )
        with patch.dict(sys.modules, {"select": fake_select}):
            handle._wait_fd(r1, write=False, timeout=0.05, wake_fd=r2)
            self.assertTrue(handle._closed)
            handle._closed = False
            handle._wait_fd(w1, write=True, timeout=0.05)
            self.assertTrue(handle._closed)


if __name__ == "__main__":
    unittest.main()
