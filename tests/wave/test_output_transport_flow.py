"""Output fidelity and stop races use independently known bytes and verdicts."""

from rpkbin.wave import WaveWorkspace

import sys
import threading
import time

import pytest

from rpkbin.flow import Analyzer, Automation, Binding, Flow, Parser, Result
from rpkbin.jobcore import Job, JobManager, JobStatus
from rpkbin.jobcore._process import ProcessHandle


def parse_stdout(line, ctx):
    if line.startswith("value="):
        return {"line": line, "value": int(line.split("=")[1])}
    return {"line": line}


def analyze_stdout(row, ctx):
    if row["line"] == "FATAL":
        ctx.state["failed"] = True
        ctx.summary.result("FAIL", "fatal output", final=True)
    elif row["line"] in ("COMPLETE", "完成", "�") or row["line"].startswith("GOT:"):
        if not ctx.state.get("failed"):
            ctx.summary.result("PASS", "completion observed")


STDOUT = Parser("stdout", parse_stdout, outputs={"line": "line", "value": "numeric sample"})
ACCEPTANCE = Analyzer("acceptance", records=[Binding(STDOUT, analyze_stdout)])
TRANSPORT_FLOW = Flow("transport", parsers=[STDOUT], analyzers=[ACCEPTANCE])


@pytest.fixture
def managers():
    instances = []

    def make(*, archive_dir=None, **kwargs):
        manager = WaveWorkspace(JobManager(**kwargs), archive_dir=archive_dir)
        instances.append(manager)
        return manager

    yield make
    for manager in instances:
        manager.close()


class Chunks:
    def __init__(self, chunks):
        self.chunks = iter([*chunks, b""])

    def read(self):
        chunk = next(self.chunks)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk


def read_and_finish(manager, job, chunks):
    manager.add(job, flow=TRANSPORT_FLOW)
    manager.validate()
    attempt = job._attempt
    attempt.process = Chunks(chunks)
    job._read(attempt)
    attempt.process = None
    attempt.returncode = 0
    job._finish(attempt)


@pytest.mark.parametrize("encoding", ["utf-8", "cp950"])
def test_explicit_encoding_roundtrips_real_child_stdin(managers, encoding):
    text = "完成"
    code = (
        "import os,sys\n"
        "print('READY',flush=True)\n"
        "value=sys.stdin.readline().strip()\n"
        f"assert value == {text!r}, ascii(value)\n"
        f"os.write(1,('GOT:'+value+'\\r\\n').encode({encoding!r}))\n"
    )
    manager = managers()
    job = Job(
            "echo",
            [sys.executable, "-u", "-c", code],
            encoding=encoding,
            env={"PYTHONIOENCODING": encoding},
            pty=False,
        )
    manager.add(job, flow=TRANSPORT_FLOW)
    manager.start()
    deadline = time.monotonic() + 4
    while "READY" not in job.logs() and time.monotonic() < deadline:
        time.sleep(.005)
    assert job.send(text)
    assert manager._drain(6)
    assert job.result.verdict is Result.PASS
    assert "GOT:" + text in job.logs()
    assert job.to_snapshot_dict()["encoding"] == encoding


@pytest.mark.parametrize("encoding", ["utf-8", "cp950"])
def test_fragmented_multibyte_characters_remain_exact(managers, encoding):
    job = Job("fragmented", [sys.executable, "-c", "pass"], encoding=encoding)
    raw = "完成\r\n".encode(encoding)
    read_and_finish(managers(), job, [bytes([byte]) for byte in raw])
    assert job.logs() == ["完成"]
    assert job.result.verdict is Result.PASS


@pytest.mark.parametrize(
    "chunks,lines",
    [
        ([b"a\r", b"\nb\rc\n", b"d\r", b"e"], ["a", "b", "c", "d", "e"]),
        ([b"x" * 4095 + b"\r", b"\n"], ["x" * 4095]),
        ([b"a\r"], ["a"]),
    ],
)
def test_universal_newlines_do_not_invent_blank_lines(managers, chunks, lines):
    job = Job("newlines", [sys.executable, "-c", "pass"])
    read_and_finish(managers(), job, chunks)
    assert job.logs() == lines
    assert job.result.verdict is Result.NA


@pytest.mark.parametrize("decode_errors", ["replace", "strict"])
def test_malformed_output_monitoring_depends_on_decode_policy(managers, decode_errors):
    job = Job("malformed", [sys.executable, "-c", "pass"], decode_errors=decode_errors)
    read_and_finish(
        managers(),
        job,
        [b"value=3\nCOMPLETE\nbad\xff\nFATAL\nvalue=4\n", b"MORE\n"],
    )
    strict = decode_errors == "strict"
    assert job.result.verdict is (Result.ERROR if strict else Result.FAIL)
    assert job.data["value"] == (3 if strict else 4)
    snapshot = job.to_snapshot_dict(include_details=True)
    assert snapshot["flow"]["parsers"][0]["count"] == (2 if strict else 6)
    assert "bad\ufffd" in job.logs() and "MORE" in job.logs()
    assert [e["source"] for e in job.result.errors] == (["output_decode"] if strict else [])
    assert snapshot["flow"]["complete"] is not strict
    assert (job._attempt.output_error is not None) is strict


def test_valid_replacement_character_is_not_a_decode_error(managers):
    job = Job("literal_replacement", [sys.executable, "-c", "pass"])
    read_and_finish(managers(), job, ["\ufffd\n".encode("utf-8")])
    assert job.result.verdict is Result.PASS
    assert not job.result.errors
    assert job.to_snapshot_dict()["output_decode_count"] == 0
    assert not job.to_snapshot_dict()["diagnostics"]


@pytest.mark.parametrize(
    "chunks",
    [
        [b"FATAL\nCOMPLETE\nbad\xff\n"],
        [b"FATAL\r", b"\xff\n"],
        [b"FATAL\r\xe4\xb8"],
    ],
)
@pytest.mark.parametrize("decode_errors", ["replace", "strict"])
def test_real_failure_before_bad_bytes_is_preserved(managers, chunks, decode_errors):
    job = Job("known_failure", [sys.executable, "-c", "pass"], decode_errors=decode_errors)
    read_and_finish(managers(), job, chunks)
    assert job.result.verdict is (Result.ERROR if decode_errors == "strict" else Result.FAIL)
    assert job.to_snapshot_dict(include_details=True)["flow"]["analyzers"][0]["result"] == "FAIL"
    assert job.to_snapshot_dict(include_details=True)["flow"]["analyzers"][0]["evidence"]["line"] == "FATAL"
    assert len(job.result.errors) == (1 if decode_errors == "strict" else 0)


@pytest.mark.parametrize(
    "encoding,chunks",
    [
        ("utf-8", [b"COMPLETE\nA\xe4", b"\x00\n"]),
        ("utf-8", [b"COMPLETE\nA\xe4\xb8"]),
        ("utf-16", ["COMPLETE\nA".encode("utf-16"), b"\xff"]),
    ],
)
@pytest.mark.parametrize("decode_errors", ["replace", "strict"])
def test_invalid_pending_bytes_and_truncated_eof_do_not_fake_missing_fail(
    managers, encoding, chunks, decode_errors
):
    job = Job("pending_bytes", [sys.executable, "-c", "pass"], encoding=encoding, decode_errors=decode_errors)
    read_and_finish(managers(), job, chunks)
    assert job.result.verdict is (Result.ERROR if decode_errors == "strict" else Result.PASS)
    assert len(job.result.errors) == (1 if decode_errors == "strict" else 0)


@pytest.mark.parametrize("decode_errors", ["replace", "strict"])
def test_valid_partial_prefix_is_not_mistaken_for_a_prompt(managers, decode_errors):
    job = Job("bad_prompt", [sys.executable, "-c", "pass"], prompts=("READY>",), decode_errors=decode_errors)
    read_and_finish(managers(), job, [b"READY>\xff"])
    assert job.to_snapshot_dict(include_details=True)["flow"]["parsers"][0]["count"] == (0 if decode_errors == "strict" else 1)
    assert "READY>\ufffd" in job.logs()
    assert job.result.verdict is (Result.ERROR if decode_errors == "strict" else Result.NA)


def test_reader_io_failure_marks_output_incomplete(managers):
    job = Job("reader_error", [sys.executable, "-c", "pass"])
    read_and_finish(managers(), job, [b"COMPLETE\n", OSError("reader unavailable")])
    assert job.result.verdict is Result.ERROR
    assert job.result.errors[0]["source"] == "output_reader"


def test_output_drain_deadline_is_a_source_error(
    managers, monkeypatch
):
    release = threading.Event()

    class UndrainedProcess:
        pid = 1

        def start(self):
            pass

        def poll(self):
            return 0

        def read(self):
            assert release.wait(3), "test reader was not closed"
            return b""

        def cleanup(self):
            pass

        def close(self):
            release.set()

    original_join = threading.Thread.join

    def deadline_join(thread, timeout=None):
        if thread.name.startswith("Wave-output-") and timeout == 3:
            return
        return original_join(thread, timeout)

    monkeypatch.setattr(
        "rpkbin.jobcore._process.ProcessHandle", lambda *a, **k: UndrainedProcess()
    )
    monkeypatch.setattr(threading.Thread, "join", deadline_join)
    manager = managers()
    job = Job("undrained", [sys.executable, "-c", "pass"], pty=False)
    manager.add(job, flow=TRANSPORT_FLOW)
    manager.start()
    assert manager._drain(5)
    assert job.result.verdict is Result.ERROR
    assert job.result.errors[0]["source"] == "output_reader"


@pytest.mark.parametrize("decode_errors", ["replace", "strict"])
def test_real_child_keeps_draining_after_decode_error_and_ticks_remain_live(managers, decode_errors):
    manager = managers()
    code = "import os,time; os.write(1,b'bad\\xff\\n'+b'data\\n'*20000); time.sleep(.15); print('TAIL',flush=True)"
    job = Job("drain", [sys.executable, "-u", "-c", code], pty=False, max_log_lines=3, decode_errors=decode_errors)
    ticks = []
    timer = Analyzer("timer", on_tick=lambda tick, ctx: ticks.append(tick.elapsed), every=.025)
    manager.add(job, flow=Flow("draining", parsers=[STDOUT], analyzers=[ACCEPTANCE, timer]))
    tail_seen = threading.Event()

    def observe(record):
        if record["text"] == "TAIL":
            tail_seen.set()

    manager.add_log_listener(observe)
    manager.start()
    # The burst must reach the reader. Time terminal drain separately so
    # Windows scheduling during the burst does not decide that assertion.
    assert tail_seen.wait(15)
    assert manager._drain(6)
    assert job.returncode == 0
    assert job.result.verdict is (Result.ERROR if decode_errors == "strict" else Result.NA)
    assert job.logs()[-1] == "TAIL"
    assert ticks
    assert len(job.result.errors) == (1 if decode_errors == "strict" else 0)


def test_decode_error_can_request_stop_through_native_flow_error_event(managers):
    manager = managers()
    code = "import os,time; os.write(1,b'bad\\xff\\n'); time.sleep(10)"
    job = Job(
            "stop_on_decode",
            [sys.executable, "-u", "-c", code],
            pty=False,
            decode_errors="strict",
            stop_timeout=0.05,
        )
    errors = []
    def stop_on_error(event, ctx):
        if event.name == "error":
            errors.append(event)
            return ctx.actions.stop(reason=event.data["message"])
    manager.add(job, flow=Flow("error_stop", parsers=[STDOUT], analyzers=[ACCEPTANCE],
                    automations=[Automation("stop", on_event=stop_on_error)]))
    manager.start()
    assert manager._drain(6)
    assert job.status is JobStatus.CANCELLED
    assert job.result.verdict is Result.ERROR
    assert errors[0].data["source"] == "output_decode"
    assert errors[0].data["output_complete"] is False
    assert errors[0].data["attempt_id"] == job.attempt_id


@pytest.mark.parametrize("decode_errors", ["replace", "strict"])
def test_retry_starts_with_a_fresh_decoder_and_flow_state(managers, tmp_path, decode_errors):
    counter = tmp_path / "attempt"
    code = (
        f"from pathlib import Path; import os; p=Path({str(counter)!r}); first=not p.exists(); p.touch(); "
        "os.write(1,b'bad\\xff\\n' if first else b'COMPLETE\\n'); raise SystemExit(1 if first else 0)"
    )
    manager = managers()
    job = Job("retry", [sys.executable, "-u", "-c", code], pty=False, max_retries=1, decode_errors=decode_errors)
    manager.add(job, flow=TRANSPORT_FLOW)
    manager.start()
    assert manager._drain(6)
    assert job.attempt_number == 2
    assert job.result.verdict is Result.PASS
    assert not job.result.errors
    assert job.history[0]["result"] == ("ERROR" if decode_errors == "strict" else Result.NA.value)
    assert job.to_snapshot_dict()["output_decode_count"] == 0
    previous = job.to_snapshot_dict(attempt_id=job.history[0]["attempt_id"], include_details=True)
    assert previous["flow"]["complete"] is (decode_errors != "strict")
    assert previous["output_decode_count"] == (0 if decode_errors == "strict" else 1)


@pytest.mark.parametrize("cause", ["manual", "timeout", "known_fail"])
def test_stop_command_race_with_real_normal_exit_preserves_result_priority(
    managers, tmp_path, monkeypatch, cause
):
    gate = tmp_path / "finish"
    code = (
        f"import time; from pathlib import Path; gate=Path({str(gate)!r}); print('READY',flush=True)\n"
        + ("print('FATAL',flush=True)\n" if cause == "known_fail" else "")
        + "while not gate.exists(): time.sleep(.005)\n"
    )
    manager = managers()
    job = Job(
            "race",
            [sys.executable, "-u", "-c", code],
            pty=False,
            stop_cmd="quit",
            stop_timeout=2,
            timeout=0.15 if cause == "timeout" else 5,
        )
    manager.add(job, flow=TRANSPORT_FLOW if cause == "known_fail" else None)
    original = ProcessHandle.write
    entered_alive = []

    def delayed_write(handle, text):
        entered_alive.append(handle.poll() is None)
        gate.touch()
        deadline = time.monotonic() + 2
        while handle.poll() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert handle.poll() == 0
        return original(handle, text)

    monkeypatch.setattr(ProcessHandle, "write", delayed_write)
    manager.start()
    deadline = time.monotonic() + 4
    while "READY" not in job.logs() and time.monotonic() < deadline:
        time.sleep(.005)
    assert "READY" in job.logs()
    if cause != "timeout":
        assert job.stop()
    assert manager._drain(5)
    assert entered_alive == [True]
    assert job.returncode == 0
    assert job.result.verdict is (
        Result.INCOMPLETE if cause == "manual" else Result.FAIL
    )
    assert not job.result.errors


def test_incremental_stdin_encoder_does_not_repeat_bom(managers):
    # UTF-16 is used only for stdin; the child's known output is also UTF-16.
    manager = managers()
    code = "import os,sys; data=sys.stdin.buffer.read(8); assert data == b'\\xff\\xfeA\\x00B\\x00C\\x00', repr(data); os.write(1,'COMPLETE\\n'.encode('utf-16'))"
    job = Job("bom", [sys.executable, "-u", "-c", code], pty=False, encoding="utf-16")
    manager.add(job, flow=TRANSPORT_FLOW)
    manager.start()
    deadline = time.monotonic() + 4
    while job.status is JobStatus.PENDING and time.monotonic() < deadline:
        time.sleep(0.005)
    assert job.send("A", newline=False)
    assert job.send("B", newline=False)
    assert job.send("C", newline=False)
    assert manager._drain(5)
    assert job.result.verdict is Result.PASS


def test_default_replacements_are_counted_once_per_attempt_and_archived(managers, tmp_path):
    manager = managers(archive_dir=tmp_path)
    job = Job("warning", [sys.executable, "-c", "pass"])
    read_and_finish(manager, job, [b"\xff", b"\xfe\n", "\ufffd\nCOMPLETE\n".encode(), b"\xe4\xb8"])
    assert job.logs() == ["\ufffd\ufffd", "\ufffd", "COMPLETE", "\ufffd"]
    assert job.result.verdict is Result.PASS
    assert job._attempt.output_error is None
    assert not job.result.errors
    snapshot = job.to_snapshot_dict(include_details=True)
    assert snapshot["decode_errors"] == "replace"
    assert snapshot["output_decode_count"] == 3
    assert snapshot["flow"]["complete"] is True
    warnings = [d for d in snapshot["diagnostics"] if d["source"] == "output_decode"]
    assert len(warnings) == 1
    assert warnings[0]["level"] == "warning" and warnings[0]["count"] == 3
    assert "3" in warnings[0]["message"]
    manager._save_attempt(job, job._attempt, flush=True)
    saved = manager.archive.attempt(job.id, job.attempt_id)
    assert saved["output_decode_count"] == 3
    assert saved["diagnostics"] == snapshot["diagnostics"]
