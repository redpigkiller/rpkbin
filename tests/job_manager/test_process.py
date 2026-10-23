"""Transport tests use real subprocesses, never simulation-specific commands."""

from __future__ import annotations

import os
import errno
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from rpkbin.job_manager._process import ProcessHandle


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

        with patch("rpkbin.job_manager._process.os.write", side_effect=write_with_full_pipe):
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


if __name__ == "__main__":
    unittest.main()
