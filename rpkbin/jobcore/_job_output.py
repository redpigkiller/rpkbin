"""Process execution, output reading and decoding."""

from __future__ import annotations

import codecs
import io
import threading
import time

from ._events import (
    ClockTick,
    Line,
    Started,
)
from .model import JobStatus

from .output import DECODE_REPLACEMENTS, REPLACE_HANDLER, strip_ansi

_OUTPUT_IDLE_TIMEOUT = 3.0
# An unterminated line is flushed in segments of this size, bounding memory.
_MAX_LINE_CHARS = 4 * 1024 * 1024
# Prompt detection only needs the end of the pending line.
_PROMPT_TAIL_CHARS = 4096

class _JobOutput:
    def _line(self, a, text):
        a.sink.emit(Line(strip_ansi(text)))

    def _reader_error(self, a, source, exc):
        with self._events_lock.input_callback(a), self._events_lock:
            if not a.finalized and a.reader_cutoff is None:
                self._error(a, source, exc, output_error=True)

    def _read(self, a):
        raw_decoder = codecs.getincrementaldecoder(self.encoding)(
            errors=REPLACE_HANDLER if self.decode_errors == "replace" else "strict"
        )
        newlines = io.IncrementalNewlineDecoder(None, translate=True)
        buffer = ""
        warning = None

        def consume(text, *, prompts=True):
            nonlocal buffer
            buffer += text
            if "\n" in buffer:
                # Split once: repeated split("\n", 1) copies the rest per line.
                *lines, buffer = buffer.split("\n")
                for line in lines:
                    if a.finalized or a.reader_cutoff is not None:
                        return
                    self._output_line(a, line)
            while len(buffer) > _MAX_LINE_CHARS:
                if a.finalized or a.reader_cutoff is not None:
                    return
                segment, buffer = buffer[:_MAX_LINE_CHARS], buffer[_MAX_LINE_CHARS:]
                self._output_line(a, segment)
            if (
                prompts
                and buffer
                and any(
                    strip_ansi(buffer[-_PROMPT_TAIL_CHARS:]).rstrip().endswith(p.rstrip())
                    for p in self.prompts
                )
            ):
                self._output_line(a, buffer)
                buffer = ""

        count_token = DECODE_REPLACEMENTS.set(0)
        try:
            while True:
                chunk = a.process.read()
                if a.finalized or a.reader_cutoff is not None:
                    return
                final = not chunk
                state = raw_decoder.getstate()
                try:
                    text = raw_decoder.decode(chunk, final=final)
                except UnicodeDecodeError as exc:
                    raw_decoder.setstate(state)
                    # Preserve complete, valid lines before the damaged bytes,
                    # including bytes buffered from the preceding read.
                    prefix = max(0, exc.start - len(state[0]))
                    consume(
                        newlines.decode(raw_decoder.decode(chunk[:prefix])),
                        prompts=False,
                    )
                    # A pending CR is already a valid line ending; only its LF
                    # pairing is undecided. Flush it without finalizing the raw
                    # decoder, which may still hold the malformed byte prefix.
                    consume(newlines.decode("", final=True), prompts=False)
                    newlines.reset()
                    self._reader_error(a, "output_decode", exc)
                    raw_decoder.errors = "replace"
                    text = raw_decoder.decode(chunk[prefix:], final=final)
                count = DECODE_REPLACEMENTS.get()
                if count != a.output_decode_count:
                    with self._events_lock:
                        if a.finalized or a.reader_cutoff is not None:
                            return
                        a.output_decode_count = count
                        if warning is None:
                            warning = dict(source="output_decode", level="warning", time=time.time())
                            a.diagnostics.append(warning)
                        warning.update(count=count, message=f"Replaced undecodable output ({count} replacements)")
                consume(newlines.decode(text, final=final))
                if a.finalized or a.reader_cutoff is not None:
                    return
                if final:
                    break
            if buffer:
                self._output_line(a, buffer)
        except Exception as exc:
            if a.reader_cutoff is None:
                self._reader_error(a, "output_reader", exc)
        finally:
            DECODE_REPLACEMENTS.reset(count_token)
            a.reader_done.set()

    def _output_line(self, a, text):
        cleaned = strip_ansi(text)
        if not self.prompts and a.status is not JobStatus.WAITING_INPUT:
            # The Sink checks the attempt gate when committing a reader line.
            a.sink.emit(Line(cleaned, monitor=a.output_error is None))
            return
        with self._events_lock:
            if a.finalized or a.reader_cutoff is not None:
                return
            monitor = a.output_error is None
            if monitor:
                # Publish prompt state before the input's callbacks.
                prompt = any(cleaned.rstrip().endswith(p.rstrip()) for p in self.prompts)
                if prompt:
                    self._set_status(a, JobStatus.WAITING_INPUT)
                elif a.status is JobStatus.WAITING_INPUT and cleaned.strip():
                    self._set_status(a, JobStatus.RUNNING)
        a.sink.emit(Line(cleaned, monitor=monitor))

    def _wait_for_output(self, a):
        started = time.monotonic()
        hard_deadline = started + self.output_drain_timeout
        while not a.reader_done.is_set():
            now = time.monotonic()
            idle_deadline = max(started, a.reader_progress_at) + _OUTPUT_IDLE_TIMEOUT
            if now >= hard_deadline:
                reason = f"hard limit of {self.output_drain_timeout:g}s reached"
            elif now >= idle_deadline:
                reason = f"no line progress for {_OUTPUT_IDLE_TIMEOUT:g}s"
            else:
                a.reader_done.wait(min(hard_deadline, idle_deadline) - now)
                continue
            with self._events_lock:
                if not a.reader_done.is_set():
                    a.sink.abandon_input(f"output did not drain within cleanup deadline ({reason})")
            return

    def _execute(self, a):
        from ._process import ProcessHandle

        reader, launched = None, False
        try:
            with self._events_lock:
                if a.stop_requested:
                    self._finish(a, cancelled=True)
                    return
                a.process = ProcessHandle(
                    self.cmd,
                    cwd=self.cwd,
                    env=dict(self.env),
                    encoding=self.encoding,
                    pty=self.pty,
                    pty_rows=self.pty_rows,
                    pty_cols=self.pty_cols,
                )
                a.process.start()
                launched = True
                a.started, a.started_mono = time.time(), time.monotonic()
                a.pid = a.process.pid
                a.pending_reason = ""
                self._set_status(a, JobStatus.RUNNING)
                a.sink.emit(Started())
            reader = threading.Thread(
                target=self._read, args=(a,), daemon=True, name=f"Wave-output-{a.id}"
            )
            reader.start()
            while a.process.poll() is None:
                # Keep polling for process exit while an event is committing.
                if not self._events_lock.acquire(timeout=0.025):
                    continue
                try:
                    if a.process.poll() is not None:
                        break
                    if (
                        self.timeout is not None
                        and a.elapsed >= self.timeout
                        and not a.timed_out
                    ):
                        a.timed_out = True
                        self.stop(
                            source="timeout",
                            reason=f"timeout after {self.timeout:g}s",
                            expected_attempt=a.id,
                        )
                    a.sink.emit(ClockTick(a.elapsed))
                finally:
                    self._events_lock.release()
                time.sleep(0.025)
            with self._events_lock:
                a.returncode = a.process.poll()
            a.process.cleanup()
            self._wait_for_output(a)
            if a.stop_thread:
                a.stop_thread.join(timeout=self.stop_timeout + 2)
        except Exception as exc:
            with self._events_lock:
                if not launched:
                    a.execution_failed = True
                    a.returncode = -1
                    a.termination.append(
                        dict(
                            source="execution",
                            reason=f"{type(exc).__name__}: {exc}",
                            requested=None,
                            actual=None,
                            time=time.time(),
                        )
                    )
                    a.sink.emit(Line("Execution failed: " + str(exc), "wave", monitor=False))
                else:
                    a.sink.abandon_input(f"Process execution or cleanup failed: {type(exc).__name__}: {exc}")
                if a.process:
                    try:
                        a.process.kill()
                    except Exception:
                        pass
        finally:
            if a.process:
                try:
                    a.process.close()
                except Exception as exc:
                    with self._events_lock:
                        a.sink.abandon_input(f"Process close failed: {type(exc).__name__}: {exc}")
            if reader and not a.reader_done.is_set() and a.reader_cutoff is None:
                with self._events_lock:
                    if not a.reader_done.is_set():
                        a.sink.abandon_input("Reader still active after process cleanup")
            if a.stop_thread and a.stop_thread.is_alive():
                a.stop_thread.join(timeout=self.stop_timeout + 2)
            self._finish(a)
            # Never wait while holding _events_lock: callbacks may call Job
            # controls, which need that same lock. Keep shutdown bounded.
            if a.reader_cutoff is None:
                a.sink.drain(2)
