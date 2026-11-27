"""Output fidelity and stop races use independently known bytes and verdicts."""

import sys

import pytest

from rpkbin.jobcore import Job, JobManager, JobStatus, ProcessVerdict
from rpkbin.jobcore._process import ProcessHandle


@pytest.fixture
def managers():
    instances = []

    def make(**kwargs):
        manager = JobManager(**kwargs)
        instances.append(manager)
        return manager

    yield make
    for manager in instances:
        manager.close()


@pytest.mark.parametrize("encoding", ["not-a-real-codec", "base64_codec", None, 3])
def test_encoding_is_validated_before_spawn(encoding):
    for factory in (Job, ProcessHandle):
        args = (
            ("invalid", [sys.executable, "-c", "pass"])
            if factory is Job
            else ([sys.executable, "-c", "pass"],)
        )
        with pytest.raises((LookupError, TypeError)):
            factory(*args, encoding=encoding)


def test_encoding_is_frozen_with_the_definition(managers):
    manager = managers()
    job = manager.add(Job("fixed", [sys.executable, "-c", "pass"], encoding="UTF8"))
    manager.validate()
    assert job.encoding == "utf-8"
    with pytest.raises(AttributeError, match="frozen"):
        job.encoding = "cp950"


def test_encoding_changed_before_validation_is_revalidated(managers):
    manager = managers()
    job = manager.add(Job("changed", [sys.executable, "-c", "pass"]))
    job.encoding = "not-a-real-codec"
    with pytest.raises(LookupError):
        manager.start()
    assert not manager.started and job.pid is None


def test_stop_broken_pipe_from_live_process_is_still_error(managers):
    class LiveProcess:
        exited = False

        def poll(self):
            return 0 if self.exited else None

        def write(self, text):
            raise BrokenPipeError("stdin unavailable while process is alive")

        def kill(self):
            self.exited = True

    manager = managers()
    job = manager.add(
        Job(
            "live_broken_pipe",
            [sys.executable, "-c", "pass"],
            stop_cmd="quit",
            stop_timeout=0.01,
        )
    )
    manager.validate()
    attempt = job._attempt
    attempt.status = JobStatus.RUNNING
    attempt.process = LiveProcess()
    assert job.stop()
    attempt.stop_thread.join(2)
    assert not attempt.stop_thread.is_alive()
    attempt.returncode = 0
    job._finish(attempt)
    attempt.process = None
    assert job.result.verdict is ProcessVerdict.ERROR
    assert job.result.errors[0]["source"] == "stop"


@pytest.mark.parametrize("decode_errors", [None, 3, True, "ignore", "REPLACE"])
def test_decode_policy_is_validated_before_spawn(decode_errors):
    with pytest.raises((TypeError, ValueError), match="decode_errors"):
        Job("invalid", [sys.executable, "-c", "pass"], decode_errors=decode_errors)


def test_decode_policy_is_revalidated_and_frozen(managers):
    manager = managers()
    job = manager.add(Job("policy", [sys.executable, "-c", "pass"]))
    assert job.decode_errors == "replace"
    job.decode_errors = "ignore"
    with pytest.raises(ValueError, match="decode_errors"):
        manager.start()
    assert not manager.started and job.pid is None
    job.decode_errors = "strict"
    manager.validate()
    with pytest.raises(AttributeError, match="frozen"):
        job.decode_errors = "replace"


def test_read_caps_unterminated_line_buffer_and_splits_multiline_chunks(managers, monkeypatch):
    from rpkbin.jobcore import _job_output

    class ChunkProcess:
        def __init__(self, chunks):
            self._chunks = iter([*chunks, b""])

        def read(self):
            return next(self._chunks)

    monkeypatch.setattr(_job_output, "_MAX_LINE_CHARS", 16)
    manager = managers()
    job = manager.add(Job("bounded", [sys.executable, "-c", "pass"]))
    manager.validate()
    attempt = job._attempt
    attempt.process = ChunkProcess(
        [
            b"\x1b[32malpha\x1b[0m\n\x1b[31mbeta\x1b[0m\ngamma\n",
            b"0123456789abcdef0123456789ABCDEFtail",
        ]
    )
    job._read(attempt)
    attempt.process = None
    attempt.returncode = 0
    job._finish(attempt)
    assert job.logs() == [
        "alpha",
        "beta",
        "gamma",
        "0123456789abcdef",
        "0123456789ABCDEF",
        "tail",
    ]


def test_strict_decode_error_without_prompts_marks_subsequent_lines_unmonitored(managers):
    from rpkbin.jobcore.events import AttemptReceiver, Line

    emitted = []

    class RecordingReceiver(AttemptReceiver):
        def emit(self, event):
            if isinstance(event, Line):
                emitted.append((event.text, event.monitor))
            super().emit(event)

    class ChunkProcess:
        def __init__(self, chunks):
            self._chunks = iter([*chunks, b""])

        def read(self):
            return next(self._chunks)

    manager = managers()
    job = manager.add(
        Job(
            "strict",
            [sys.executable, "-c", "pass"],
            encoding="utf-8",
            decode_errors="strict",
            prompts=(),
        ),
        receiver_factory=RecordingReceiver,
    )
    manager.validate()
    attempt = job._attempt
    attempt.process = ChunkProcess([b"ok\n\xff\nafter\n"])
    job._read(attempt)
    attempt.process = None
    attempt.returncode = 0
    job._finish(attempt)
    assert emitted == [("ok", True), ("\ufffd", False), ("after", False)]
