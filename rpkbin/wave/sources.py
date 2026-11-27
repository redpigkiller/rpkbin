"""Polling and offline file sources for the Wave attempt sink."""

import codecs
from copy import copy
import hashlib
from dataclasses import dataclass
from contextlib import closing, contextmanager
import io
import os
import re
import signal
from pathlib import Path
import stat
import sqlite3
from threading import Event, current_thread, main_thread
import time

from rpkbin.flow import Flow

from rpkbin.jobcore.events import Capabilities, ClockTick, Finished, Line, Started
from ._loader import run_definition_script
from ._run import NoControl
from .archive import RunArchive, default_archive_dir
from rpkbin.jobcore._job_config import _integer, _positive
from rpkbin.jobcore.output import DECODE_REPLACEMENTS, REPLACE_HANDLER, strip_ansi
from rpkbin.jobcore import Job, JobStatus
from rpkbin.jobcore.manager import definition_scope

_MAX_LINE = 4 * 1024 * 1024
_END_CODECS = {"utf-8", "ascii", "iso8859-1", "cp1252"}
_FINGERPRINT_BYTES = 4096


@dataclass(frozen=True)
class SourceResult:
    """Terminal source facts and the identifiers needed to reopen its archive."""

    snapshot: dict
    archive_path: Path
    complete: bool
    reason: str
    capabilities: Capabilities
    archive_error: str | None = None


class _FileAttempt(Job):
    """Reuse attempt storage and its Sink without dispatching a process."""

    def __init__(self, source, name):
        self._source = source
        super().__init__(name, (str(source.path),),
                         pty=False, encoding=source.encoding,
                         decode_errors=source.decode_errors, max_log_lines=256)
        # Exclude the pending source from process dispatch without reserving
        # a worker. Only its own reader can seal the attempt.
        self._attempt.reserved = True

    def receiver(self, workspace, attempt, flow):
        source = self._source
        from ._attempt import AttemptSink

        descriptor = dict(
            version=1, path=str(source.path), encoding=self.encoding,
            decode_errors=self.decode_errors, start_offset=0, end_offset=0,
            generation=0, completion_reason=None,
            time_basis="observed" if source.kind == "tail" else "ingested",
        )
        descriptor.update(source._provenance())
        return AttemptSink(
            workspace, self, attempt, flow, control=NoControl(),
            source_kind=source.kind, source_descriptor=descriptor, clock_enabled=source.kind == "tail",
            automation_enabled=source.automation_enabled,
        )

    def _execute(self, attempt):
        raise RuntimeError("File attempts are driven by their Source")

    def finish_reader(self, error=None):
        """Seal a reader that could not run, without starting Flow callbacks."""
        a = self._attempt
        with self._events_lock:
            if a.finalized:
                return
            a.pending_reason = ""
            a.ended, a.ended_mono = time.time(), time.monotonic()
            a.previous_status, a.status = a.status, JobStatus.CANCELLED if error is None else JobStatus.FAILED
            a.sink.source_descriptor["completion_reason"] = "user_stopped" if error is None else "source_reader"
            if a.sink.flow_session is not None:
                a.sink.flow_session._seal(incomplete=True)
            if error is not None:
                self._error(a, "source_reader", error, output_error=True)
            a.reserved = False
            self._seal_attempt(a, persist=True)

    def run_source(self, stop):
        a = self._attempt
        with self._events_lock:
            a.pending_reason = ""
            if stop.is_set():
                self.finish_reader()
                return False, "user_stopped"
            a.started, a.started_mono = time.time(), time.monotonic()
            self._set_status(a, JobStatus.RUNNING)
            a.sink.emit(Started())
        reader = _Reader(self, stop)
        token = DECODE_REPLACEMENTS.set(0)
        try:
            try:
                if reader.stopped():
                    complete, reason = False, "user_stopped"
                else:
                    complete, reason = self._source._read(reader, stop)
            except KeyboardInterrupt:
                complete, reason = False, "user_stopped"
            except OSError as exc:
                reader.error("file_io", exc)
                complete, reason = False, "file_io"
            except _LineTooLong as exc:
                reader.error("line_too_long", exc)
                complete, reason = False, "line_too_long"
            except Exception as exc:
                reader.error("source_reader", exc)
                complete, reason = False, "source_reader"
            if reader.stopped():
                complete, reason = False, "user_stopped"
            reader.note_partial()
            if reason == "user_stopped":
                reader.resolve_cr()
                reader.set_offset(reader.delivered_offset)
            if a.output_error is not None and complete:
                complete, reason = False, a.output_error["source"]
        finally:
            DECODE_REPLACEMENTS.reset(token)

        with self._events_lock:
            if a.sink.has_source_batch:
                a.sink.publish_state()
            if reader.stopped():
                complete, reason = False, "user_stopped"
                reader.set_offset(reader.delivered_offset)
            a.sink.source_descriptor["completion_reason"] = reason
            a.ended, a.ended_mono = time.time(), time.monotonic()
            a.previous_status = a.status
            a.status = JobStatus.DONE if complete else JobStatus.FAILED
            try:
                a.sink.emit(Finished(complete, reason))
            except KeyboardInterrupt:
                complete, reason = False, "user_stopped"
                a.sink.source_descriptor["completion_reason"] = reason
                a.status = JobStatus.FAILED
                a.sink.abandon_input("Stopped while finishing source analysis")
            if a.sink.has_source_batch:
                a.sink.publish_state()
        session = a.sink.flow_session
        if session is not None and not session.wait_automation(2):
            session.close_automation()
        with self._events_lock:
            if reader.stopped() and reason != "user_stopped":
                complete, reason = False, "user_stopped"
                a.sink.source_descriptor["completion_reason"] = reason
                a.status = JobStatus.FAILED
                reader.set_offset(reader.delivered_offset)
                a.sink.abandon_input("Interrupted while saving source analysis")
            a.reserved = False
            self._seal_attempt(a)
            a.sink.publish_state(flush=True)
        return complete, reason


class _SourceStop(Event):
    """Combine Workspace cancellation with the synchronous API's stop event."""

    def __init__(self):
        super().__init__()
        self.external = None

    def is_set(self):
        return super().is_set() or (self.external is not None and self.external.is_set())

    def wait(self, timeout):
        if self.external is None:
            return super().wait(timeout)
        if type(self.external) is Event:
            deadline = time.monotonic() + timeout
            while not self.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                if super().wait(min(remaining, .05)):
                    return True
            return True
        return self.is_set() or self.external.wait(timeout)


class _LineTooLong(ValueError):
    pass


class _Reader:
    def __init__(self, job, stop):
        self.job = job
        self.stop = stop
        self.attempt = job._attempt
        self.descriptor = self.attempt.sink.source_descriptor
        self.marker = getattr(job._source, "end_marker", None)
        self.warning = None
        self.marker_floor = 0
        self.discontinuous = False
        self.offset = 0
        self.delivered_offset = 0
        self.reset_decoder()

    def stopped(self):
        return self.stop.is_set() or self.attempt.sink.source_interrupted

    def reset_decoder(self):
        self.decoder = codecs.getincrementaldecoder(self.job.encoding)(
            errors=REPLACE_HANDLER if self.job.decode_errors == "replace" else "strict"
        )
        self.newlines = io.IncrementalNewlineDecoder(None, translate=True)
        self.buffer = ""
        self.skip_prefix = False
        self.skip_lf = False
        self.cr_offset = None

    def resolve_cr(self):
        if self.cr_offset is None:
            return False
        offset = self.offset
        self.set_offset(self.cr_offset)
        self.cr_offset = None
        if self._consume(self.newlines.decode("", final=True), iter((self.offset,))):
            return True
        self.set_offset(offset)
        return False

    def error(self, source, exc):
        with self.job._events_lock:
            self.job._error(self.attempt, source, exc, output_error=True)

    def diagnostic(self, source, message):
        with self.job._events_lock:
            self.attempt.diagnostics.append(dict(source=source, level="warning", message=message, time=time.time()))

    def note_partial(self):
        if self.buffer or self.decoder.getstate()[0] or self.newlines.getstate()[1] & 1 or self.skip_prefix:
            self.diagnostic("partial_line", "Unterminated data was not submitted to Flow")

    def next_generation(self, *, truncated):
        if truncated or self.buffer or self.decoder.getstate()[0]:
            self.discontinuous = True
            self.diagnostic("discontinuity", "File truncated or rotated with an unfinished line")
        with self.job._events_lock:
            self.descriptor["generation"] += 1
            self.descriptor["end_offset"] = 0
        self.set_offset(0)
        self.delivered_offset = 0
        self.reset_decoder()

    def set_offset(self, offset):
        self.offset = offset

    def line(self, value, end_offset):
        if len(value) > _MAX_LINE:
            raise _LineTooLong("Decoded line exceeds 4 Mi characters")
        marker = self.marker is not None and value == self.marker and end_offset >= self.marker_floor
        sink = self.attempt.sink
        self.delivered_offset = end_offset
        try:
            sink._line(strip_ansi(value), self.job._source.kind, monitor=True,
                       end_offset=end_offset if self.descriptor is sink.source_descriptor else None)
        except KeyboardInterrupt:
            self.attempt.sink.source_interrupted = True
        return marker

    def _consume(self, text, ends):
        parts = text.split("\n")
        if len(parts) == 1:
            self.buffer += text
            if len(self.buffer) > _MAX_LINE:
                raise _LineTooLong("Decoded line exceeds 4 Mi characters")
            return False
        first = self.buffer + parts[0]
        self.buffer = ""
        if self.line(first, next(ends)):
            return True
        for line in parts[1:-1]:
            if self.line(line, next(ends)):
                return True
        self.buffer = parts[-1]
        if len(self.buffer) > _MAX_LINE:
            raise _LineTooLong("Decoded line exceeds 4 Mi characters")
        return False

    def _line_ends(self, data, state, start, previous_cr, *, newline_final, decoder_final=False):
        # UTF-8 and the supported single-byte encodings use ASCII newline bytes.
        # Other codecs need a decoder to recover exact byte boundaries.
        if self.job.encoding in _END_CODECS:
            pairs_previous_cr = previous_cr is not None and data.startswith(b"\n")
            if previous_cr is not None and not pairs_previous_cr and (data or newline_final):
                yield previous_cr
            for match in re.finditer(rb"\r\n|[\r\n]", data):
                if match.group() != b"\r" or match.end() < len(data) or newline_final:
                    yield start + match.end()
            return

        decoder = codecs.getincrementaldecoder(self.job.encoding)(errors="replace")
        decoder.setstate(state)

        def decoded_chunks():
            for index, byte in enumerate(data, start + 1):
                yield index, decoder.decode(bytes((byte,)))
            if decoder_final:
                yield start + len(data), decoder.decode(b"", final=True)

        pending_cr = previous_cr
        for index, chars in decoded_chunks():
            for char in chars:
                if pending_cr is not None:
                    yield index if char == "\n" else pending_cr
                    pending_cr = None
                    if char == "\n":
                        continue
                if char == "\r":
                    pending_cr = index
                elif char == "\n":
                    yield index
        if pending_cr is not None and newline_final:
            yield pending_cr

    def _decode(self, data, *, final=False):
        state = self.decoder.getstate()
        start = self.offset - len(data)
        try:
            decoded = self.decoder.decode(data, final=final)
        except UnicodeDecodeError as exc:
            self.decoder.setstate(state)
            prefix = max(0, exc.start - len(state[0]))
            first = data[:prefix]
            ends = self._line_ends(first, state, start, self.cr_offset, newline_final=True)
            if self._consume(self.newlines.decode(self.decoder.decode(first), final=True), ends):
                return True
            self.newlines.reset()
            self.cr_offset = None
            self.error("output_decode", exc)
            self.decoder.errors = "replace"
            state = self.decoder.getstate()
            start += prefix
            data = data[prefix:]
            decoded = self.decoder.decode(data, final=final)
        count = DECODE_REPLACEMENTS.get()
        if count != self.attempt.output_decode_count:
            with self.job._events_lock:
                self.attempt.output_decode_count = count
                if self.warning is None:
                    self.warning = dict(source="output_decode", level="warning", time=time.time())
                    self.attempt.diagnostics.append(self.warning)
                self.warning.update(count=count, message=f"Replaced undecodable output ({count} replacements)")
        previous_cr = self.cr_offset
        if self.marker is not None and previous_cr is not None and decoded and not decoded.startswith("\n"):
            if self.resolve_cr():
                return True
            previous_cr = None
        ends = self._line_ends(data, state, start, previous_cr, newline_final=final, decoder_final=final)
        marked = self._consume(self.newlines.decode(decoded, final=final), ends)
        if decoded:
            self.cr_offset = self.offset - len(self.decoder.getstate()[0]) if decoded.endswith("\r") else None
        return marked

    def read_chunk(self, data):
        if self.skip_prefix:
            boundaries = [index for byte in (b"\n", b"\r") if (index := data.find(byte)) >= 0]
            if not boundaries:
                self.set_offset(self.offset + len(data))
                return False
            count = min(boundaries) + 1
            self.skip_lf = data[count - 1] == 13
            self.skip_prefix = False
            self.set_offset(self.offset + count)
            data = data[count:]
        if self.skip_lf and data:
            self.skip_lf = False
            if data.startswith(b"\n"):
                data = data[1:]
                self.set_offset(self.offset + 1)
        if not data:
            return False
        self.set_offset(self.offset + len(data))
        if self._decode(data):
            if self.marker is not None:
                self.set_offset(self.delivered_offset)
            return True
        return False

    def finish(self):
        if not self._decode(b"", final=True) and self.buffer:
            self.line(self.buffer, self.offset)
        self.buffer = ""


@contextmanager
def _stop_on_interrupt(stop):
    # Convert console Ctrl+C into a request observed at input boundaries.
    # Preserve a caller's custom handler and allow non-main-thread sources.
    if current_thread() is not main_thread() or signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
        yield
        return
    previous = signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _open_windows(path):
    # CRT open does not share delete access, so a producer cannot rename its
    # Log while we drain the old handle. Use the OS sharing flags explicitly.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)
        raise


def _open_regular(path):
    fd = _open_windows(path) if os.name == "nt" else os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"Source must be a regular file: {path}")
        return os.fdopen(fd, "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def _identity(info):
    return info.st_dev, info.st_ino


def _version(info):
    return _identity(info), info.st_size, info.st_mtime_ns


def _stat_version(path):
    if path is not None:
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        if stat.S_ISREG(info.st_mode):
            return _version(info)
    return None


def _completion_snapshot(path):
    """Read a stable marker, including changes that preserve size and mtime."""
    if path is None:
        return None
    try:
        if not path.is_file():
            return None
        with _open_regular(path) as stream:
            version = _version(os.fstat(stream.fileno()))
            digest = hashlib.file_digest(stream, "sha256").digest()
            if version == _version(os.fstat(stream.fileno())) == _stat_version(path):
                return (*version, digest)
    except FileNotFoundError:
        pass
    return None


@dataclass
class _RetiredFile:
    stream: object
    reader: _Reader
    deadline: float
    end_offset: int | None = None

    def drain(self, chunk_size, now):
        if self.end_offset is None and now >= self.deadline:
            self.end_offset = os.fstat(self.stream.fileno()).st_size
        if self.end_offset is not None:
            chunk_size = min(chunk_size, self.end_offset - self.reader.offset)
            if chunk_size <= 0:
                return False
        self.stream.seek(self.reader.offset)
        data = self.stream.read(chunk_size)
        if data:
            self.reader.read_chunk(data)
        return bool(data)



class FileSource:
    """Analyze a stable file from the beginning through its captured EOF."""

    kind = "file"
    capabilities = NoControl.capabilities
    automation_enabled = False

    def __init__(self, path, *, encoding="utf-8", decode_errors="replace", chunk_size=65536,
                 automation=None):
        self.path = Path(path).expanduser().absolute()
        self.encoding = codecs.lookup(encoding).name
        codecs.getincrementaldecoder(self.encoding)
        if decode_errors not in ("replace", "strict"):
            raise ValueError("decode_errors must be 'replace' or 'strict'")
        self.decode_errors = decode_errors
        if automation is not None:
            if type(automation) is not bool:
                raise ValueError("automation must be bool")
            self.automation_enabled = automation
        self.chunk_size = _integer(chunk_size, "chunk_size", minimum=1)
        if self.chunk_size > 1024 * 1024:
            raise ValueError("chunk_size must be <= 1048576")

    def run(self, flow=None, *, archive_dir=None, stop_event=None):
        """Run synchronously and return the terminal snapshot and archive path."""
        if flow is not None and not isinstance(flow, Flow):
            raise TypeError("flow must be a Flow")
        from .workspace import WaveWorkspace

        workspace = WaveWorkspace(archive_dir=archive_dir if archive_dir is not None else default_archive_dir())
        store = workspace.archive
        try:
            if store is None:
                raise OSError(f"Cannot initialize archive: {workspace.archive_health['error']}")
            job = workspace.add_source(self, flow=flow)
            task = workspace._source_tasks[job.id]
            task.stop.external = stop_event
            with _stop_on_interrupt(task.stop):
                workspace.start()
                workspace._drain()
            complete, reason = task.result
            snapshot = job.to_snapshot_dict(include_details=True)
        finally:
            try:
                workspace.close()
            except (OSError, sqlite3.Error) as exc:
                if store is not None and store.error is None:
                    store.error = {"message": f"{type(exc).__name__}: {exc}", "path": str(store.path)}
        archive_path = store.path
        error = store.health["error"]
        if error is None and not store.health["closed_cleanly"]:
            error = {"message": "Archive did not close cleanly"}
        if error:
            return SourceResult(snapshot, archive_path, False, "archive_error",
                                NoControl.capabilities, error["message"])
        return SourceResult(snapshot, archive_path, complete, reason, NoControl.capabilities)

    def _provenance(self):
        return {}

    def _read(self, reader, stop):
        with _open_regular(self.path) as stream:
            initial = os.fstat(stream.fileno())

            def unchanged():
                try:
                    return _version(initial) == _version(os.fstat(stream.fileno())) == _version(self.path.stat())
                except FileNotFoundError:
                    return False

            while not reader.stopped() and unchanged():
                left = initial.st_size - reader.offset
                if not left:
                    reader.finish()
                    if unchanged():
                        return True, "eof"
                    break
                data = stream.read(min(left, self.chunk_size))
                if not data or not unchanged():
                    break
                reader.read_chunk(data)
            if reader.stopped():
                return False, "user_stopped"
            reader.diagnostic("file_changed", "File identity, size or mtime changed during analysis")
            return False, "file_changed"


class TailSource(FileSource):
    """Poll an append-oriented file, with one optional producer completion rule."""

    kind = "tail"
    automation_enabled = True

    def __init__(self, path, *, start="beginning", poll_interval=.1, idle_timeout=None,
                 completion_file=None, end_marker=None, final_offset=None, rotation_grace=3.0,
                 commit_interval=1.0, **options):
        super().__init__(path, **options)
        if start not in ("beginning", "end"):
            raise ValueError("start must be 'beginning' or 'end'")
        if start == "end" and self.encoding not in _END_CODECS:
            raise ValueError("start='end' requires UTF-8, ASCII, Latin-1 or CP1252")
        if sum(value is not None for value in (completion_file, end_marker, final_offset)) > 1:
            raise ValueError("Choose one completion rule")
        if end_marker is not None and (
            not isinstance(end_marker, str) or not end_marker or "\r" in end_marker or "\n" in end_marker
        ):
            raise ValueError("end_marker must be one nonempty line")
        self.start = start
        self.poll_interval = _positive(poll_interval, "poll_interval")
        self.rotation_grace = _positive(rotation_grace, "rotation_grace", zero=True)
        self.commit_interval = _positive(commit_interval, "commit_interval")
        self.idle_timeout = None if idle_timeout is None else _positive(idle_timeout, "idle_timeout", zero=True)
        self.completion_file = None if completion_file is None else Path(completion_file).expanduser().absolute()
        if self.completion_file == self.path:
            raise ValueError("completion_file must be separate from the source")
        self.end_marker = end_marker
        self.final_offset = None if final_offset is None else _integer(final_offset, "final_offset", minimum=0)

    def _commit_progress(self, reader):
        job, attempt = reader.job, reader.attempt
        sink = attempt.sink
        with job._events_lock:
            sink.source_descriptor["writer_heartbeat"] = time.time()
            sink.source_descriptor["commit_interval"] = self.commit_interval
            sink.publish_state(flush=True)

    def _read(self, reader, stop):
        stream = None
        opened = False
        started = last_data = next_commit = time.monotonic()
        fingerprint = b""
        rotation_deadline = None
        rotation_limit = None
        old_eof = False
        retired = []
        finish_after_rotation = False
        marker_baseline = _completion_snapshot(self.completion_file)
        log_baseline = _stat_version(self.path)
        if log_baseline is not None:
            # A preexisting terminal marker can describe the starting file.
            # A marker inside the baseline may belong to an earlier run.
            reader.marker_floor = log_baseline[1]
        completion_version = None
        completion = self.final_offset
        completed_by = "final_offset"
        try:
            while not reader.stopped():
                now = time.monotonic()
                if now >= next_commit:
                    self._commit_progress(reader)
                    next_commit = time.monotonic() + self.commit_interval
                reader.attempt.sink.emit(ClockTick(now - started))
                for previous in retired[:]:
                    if previous.drain(self.chunk_size, time.monotonic()):
                        last_data = time.monotonic()
                    elif previous.end_offset is not None:
                        previous.reader.resolve_cr()
                        previous.reader.note_partial()
                        previous.stream.close()
                        retired.remove(previous)
                if finish_after_rotation:
                    if not retired:
                        return False, "discontinuity"
                    with reader.job._events_lock:
                        if reader.attempt.sink.has_source_batch:
                            reader.attempt.sink.publish_state()
                    if not any(previous.end_offset is not None for previous in retired):
                        stop.wait(min(self.poll_interval, max(0, next_commit - time.monotonic())))
                    continue
                try:
                    path_info = self.path.stat()
                except FileNotFoundError:
                    path_info = None
                if stream is None and path_info is not None:
                    try:
                        stream = _open_regular(self.path)
                    except FileNotFoundError:
                        continue
                    if not opened:
                        opened = True
                        if self.start == "end":
                            size = os.fstat(stream.fileno()).st_size
                            if size:
                                stream.seek(size - 1)
                                last = stream.read(1)
                                reader.skip_prefix = last not in (b"\r", b"\n")
                                reader.skip_lf = last == b"\r"
                            stream.seek(max(0, size - _FINGERPRINT_BYTES))
                            fingerprint = stream.read(min(size, _FINGERPRINT_BYTES))
                            reader.set_offset(size)
                            reader.delivered_offset = size
                            with reader.job._events_lock:
                                reader.descriptor.update(start_offset=size, end_offset=size)
                            if completion is not None and completion < size:
                                reader.diagnostic("discontinuity", "Final offset is before the selected start")
                                return False, "invalid_final_offset"
                if stream is not None:
                    info = os.fstat(stream.fileno())
                    replaced = path_info is not None and _identity(path_info) != _identity(info)
                    if replaced and rotation_deadline is None:
                        rotation_deadline = time.monotonic() + self.rotation_grace
                        reader.discontinuous = True
                        reader.diagnostic("discontinuity", "Rotation handoff could not be verified")
                        if completion_version is not None:
                            marker_baseline = _completion_snapshot(self.completion_file)
                            completion_version = completion = None
                    if replaced and rotation_limit is None and time.monotonic() >= rotation_deadline:
                        rotation_limit = info.st_size
                    truncated = info.st_size < reader.offset
                    if not truncated and fingerprint:
                        stream.seek(reader.offset - len(fingerprint))
                        truncated = stream.read(len(fingerprint)) != fingerprint
                    if truncated:
                        reader.next_generation(truncated=True)
                        reader.marker_floor = 0
                        if completion is not None:
                            return False, "discontinuity"
                        fingerprint = b""
                    if completion_version is not None and (
                        completion_version != _version(info) or completion_version != _stat_version(self.path)
                    ):
                        reader.diagnostic("discontinuity", "Log changed after its completion file was observed")
                        return False, "discontinuity"
                    stream.seek(reader.offset)
                    count = self.chunk_size
                    if rotation_limit is not None:
                        count = min(count, max(0, rotation_limit - reader.offset))
                    if completion is not None and not replaced:
                        count = min(count, completion - reader.offset)
                        if count == 0 and not retired and rotation_deadline is None:
                            reader.finish()
                            if completion_version is not None and completion_version != _stat_version(self.path):
                                reader.diagnostic("discontinuity", "Log changed while finishing the completed generation")
                                return False, "discontinuity"
                            return not reader.discontinuous, "discontinuity" if reader.discontinuous else completed_by
                    data = stream.read(count)
                    if data:
                        old_eof = False
                        last_data = time.monotonic()
                        if replaced or path_info is None:
                            reader.marker_floor = float("inf")
                        if reader.read_chunk(data):
                            if not retired:
                                return not reader.discontinuous, "discontinuity" if reader.discontinuous else "end_marker"
                            finish_after_rotation = True
                        fingerprint = (fingerprint + data)[-_FINGERPRINT_BYTES:]
                        continue
                    if replaced:
                        # Drain the known old tail before reading the new file.
                        # Keep its decoder and handle through the grace period
                        # to collect later writes from a producer's old fd.
                        if old_eof or rotation_limit is not None:
                            old_reader = reader
                            if self.final_offset is None:
                                old_reader = copy(reader)
                                old_reader.descriptor = dict(reader.descriptor)
                                old_reader.marker = None
                            retired.append(_RetiredFile(stream, old_reader, rotation_deadline, rotation_limit))
                            stream = None
                            fingerprint = b""
                            rotation_deadline = rotation_limit = None
                            old_eof = False
                            if self.final_offset is not None:
                                # A numeric endpoint belongs to the original generation.
                                # Drain its handle, then finish without applying it to the replacement.
                                finish_after_rotation = True
                                continue
                            reader.next_generation(truncated=False)
                            reader.marker_floor = 0
                            continue
                        old_eof = True
                    elif completion is None and not retired:
                        marker_version = _completion_snapshot(self.completion_file)
                        if marker_version is not None and marker_version != marker_baseline:
                            version = _version(os.fstat(stream.fileno()))
                            if (marker_version[2] >= version[2] and version == _stat_version(self.path)
                                    and version[1] >= reader.offset):
                                completion = version[1]
                                completion_version = version
                                completed_by = "completion_file"
                                continue
                with reader.job._events_lock:
                    if reader.attempt.sink.has_source_batch:
                        reader.attempt.sink.publish_state()
                wait = min(self.poll_interval, max(0, next_commit - time.monotonic()))
                if self.idle_timeout is not None and not retired and rotation_deadline is None:
                    remaining = self.idle_timeout - (time.monotonic() - last_data)
                    if remaining <= 0:
                        if self.end_marker is not None and reader.resolve_cr():
                            return not reader.discontinuous, "discontinuity" if reader.discontinuous else "end_marker"
                        return False, "idle_timeout"
                    wait = min(wait, remaining)
                stop.wait(wait)
            return False, "user_stopped"
        finally:
            if stream is not None:
                stream.close()
            for previous in retired:
                previous.stream.close()


def _load_flow_file(path, *, reload_modules: bool = False):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".py":
        raise ValueError("--flow must name a Python (.py) file")
    with definition_scope():
        try:
            namespace = run_definition_script(path, reload_modules=reload_modules)
        except SystemExit as exc:
            raise ValueError("Flow file must define 'flow' without calling sys.exit") from exc
    flow = namespace.get("flow")
    if not isinstance(flow, Flow):
        raise ValueError("Flow file must expose a Flow as 'flow'")
    return flow


class ReanalysisSource(FileSource):
    """Replay a saved attempt's Log with a trusted Flow file into a new archive."""

    kind = "reanalysis"

    def __init__(self, archive, run_id, *, flow_path=None):
        super().__init__(Path(archive).expanduser().resolve())
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be a saved attempt_id")
        self.run_id = run_id
        with closing(RunArchive(self.path)) as store:
            self.attempt_name = store._replay_attempt_name(run_id)
            self._origin = dict(
                archive_path=str(store.path), archive_run_id=store.run_id, source_attempt_id=run_id,
                flow_path=str(Path(flow_path).expanduser().resolve()) if flow_path is not None else None,
            )

    def run(self, flow_path, *, archive_dir=None, stop_event=None):
        flow_path = Path(flow_path).expanduser().resolve()
        flow = _load_flow_file(flow_path)
        replay = copy(self)
        replay._origin = dict(self._origin, flow_path=str(flow_path))
        return FileSource.run(replay, flow, archive_dir=archive_dir, stop_event=stop_event)

    def _provenance(self):
        return dict(self._origin, start_offset=None, end_offset=None)

    def _read(self, reader, stop):
        with closing(RunArchive(self.path)) as store, store.replay(self.run_id) as (snapshot, lines):
            for line in lines:
                if reader.stopped():
                    break
                reader.attempt.sink.emit(Line(line, self.kind))
        if reader.stopped():
            return False, "user_stopped"
        if not snapshot["verified"]:
            reader.diagnostic("unverified_source", "Source archive did not close cleanly")
            return False, "unverified_source"
        if not snapshot.get("finalized") or (snapshot.get("flow") or {}).get("complete") is False:
            return False, "source_incomplete"
        if snapshot["source_kind"] == "process" and snapshot["completion_reason"] not in (None, "process_exit"):
            return False, "source_incomplete"
        if snapshot["source_kind"] != "process" and snapshot["status"] != "DONE":
            return False, "source_incomplete"
        return True, "replayed"
