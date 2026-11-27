"""Composition of a process manager, analysis definitions and persistent history."""

from contextlib import contextmanager
from pathlib import Path
import os
import signal
import sqlite3
import sys
from threading import Event, Lock, Thread, current_thread, main_thread
import time
import uuid

from rpkbin.flow import Flow
from rpkbin.jobcore import Job, JobManager, JobStatus
from rpkbin.jobcore._receiver import in_receiver_callback
from rpkbin.jobcore.manager import _DEFINING
from .archive import (
    ArchivePolicy,
    RunArchive,
    _ensure_private_dir,
    _env_archive_mode,
    _env_retention_days,
    _network_filesystem,
    archive_filename,
    default_archive_dir,
    move_archive_file,
    prune_archives,
    remove_archive_files,
    resolve_directory_option,
)
from .sources import FileSource, _FileAttempt, _SourceStop

from ._attempt import AttemptSink
from .columns import configure_columns

_UNSET = object()


def _terminate_handler(signum, frame):
    raise KeyboardInterrupt


def safe_print(*parts, file=None, flush=True):
    """print() that degrades to backslash escapes on ASCII/LC_ALL=C streams."""
    stream = sys.stdout if file is None else file
    text = " ".join(str(part) for part in parts)
    try:
        print(text, file=stream, flush=flush)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(encoding, errors="backslashreplace").decode(encoding),
              file=stream, flush=flush)


@contextmanager
def terminate_as_interrupt():
    """Treat SIGTERM (and POSIX SIGHUP) like Ctrl+C for the enclosed block.

    Only the main thread may install handlers. A handler is installed only over
    the default disposition, so an embedding host's own handler (or an ignored
    SIGHUP from ``nohup``) is left alone. Previous handlers are always restored.
    """
    installed = []
    try:
        if current_thread() is main_thread():
            names = ("SIGTERM", "SIGHUP") if os.name == "posix" else ("SIGTERM",)
            for name in names:
                number = getattr(signal, name, None)
                try:
                    if number is not None and signal.getsignal(number) is signal.SIG_DFL:
                        installed.append((number, signal.signal(number, _terminate_handler)))
                except (ValueError, OSError):
                    continue
        yield
    finally:
        for number, previous in reversed(installed):
            try:
                signal.signal(number, previous)
            except (ValueError, OSError):
                pass


class _SourceTask:
    def __init__(self, target, name, changed, *, job=None):
        self.stop = _SourceStop()
        self.thread = Thread(target=self._run, args=(target,), name=name, daemon=True)
        self.job = job
        self.changed = changed
        self.result = self.failure = None

    @property
    def started(self):
        return self.thread.ident is not None

    @property
    def active(self):
        return self.started and self.thread.is_alive() and not self.stop.is_set()

    def start(self):
        if self.failure is not None:
            raise self.failure
        if not self.started:
            try:
                self.thread.start()
            except Exception as exc:
                if not self.started:
                    self._fail(exc)
                raise

    def _fail(self, exc):
        self.failure = exc
        try:
            if self.job is not None:
                self.job.finish_reader(exc)
        finally:
            self.changed.set()

    def _run(self, target):
        try:
            self.result = target(self.stop)
        except BaseException as exc:
            self._fail(exc)
        finally:
            self.changed.set()


class WaveWorkspace:
    """Own Flow bindings, display preferences and archive for one JobManager."""

    def __init__(
        self,
        manager=None,
        *,
        columns=None,
        archive_dir=None,
        archive_mode=None,
        temp_dir=None,
        retention_days=None,
        pass_retention_days=None,
        fail_retention_days=None,
        stage_on_network_fs=True,
    ):
        if manager is None:
            manager = JobManager()
        if not isinstance(manager, JobManager):
            raise TypeError("workspace requires a JobManager")
        self.manager = manager
        self.run_id = uuid.uuid4().hex
        self.archive = None
        self.archive_error = None
        self._archive_policy = ArchivePolicy(
            mode=_env_archive_mode() if archive_mode is None else archive_mode,
            archive_dir=archive_dir,
            temp_dir=temp_dir,
            retention_days=(
                _env_retention_days("RPK_WAVE_RETENTION_DAYS")
                if retention_days is None
                else retention_days
            ),
            pass_retention_days=(
                _env_retention_days("RPK_WAVE_PASS_RETENTION_DAYS")
                if pass_retention_days is None
                else pass_retention_days
            ),
            fail_retention_days=(
                _env_retention_days("RPK_WAVE_FAIL_RETENTION_DAYS")
                if fail_retention_days is None
                else fail_retention_days
            ),
            stage_on_network_fs=stage_on_network_fs,
        )
        self._archive_target_dir: Path | None = None
        self._archive_temp_dir: Path | None = None
        self._archive_staged: bool = False
        self._archive_finalized: bool = False
        self._archive_disposition: str | None = None
        self._archive_pruned: int = 0
        self._cli_archive_policy: dict | None = None
        self._flows = {}
        self._removed_flows = {}
        self._wave_columns = None
        self._initial_columns = columns
        # Registration and launches share the manager's start lock.
        self._source_tasks = {}
        self._watchers = []
        self._sources_changed = Event()
        self._discovered_paths = set()
        self._started_empty = False
        self._closing_sources = False
        self._close_lock = Lock()
        manager.set_receiver_factory(self._new_receiver)
        if archive_dir is not None:
            self.enable_archive(archive_dir)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self) -> str:
        state = "closed" if self.manager._closed else ("started" if self.started else "preview")
        return f"WaveWorkspace(run_id={self.run_id[:8]!r}, jobs={len(self.manager.jobs)}, state={state!r})"

    def configure_archive(
        self,
        *,
        archive_dir=_UNSET,
        archive_mode=_UNSET,
        temp_dir=_UNSET,
        retention_days=_UNSET,
        pass_retention_days=_UNSET,
        fail_retention_days=_UNSET,
        stage_on_network_fs=_UNSET,
        override: bool = False,
    ) -> ArchivePolicy:
        """Update archive policy before dispatch (optionally replacing preview archive)."""
        self._require_no_job_event()
        with self.manager._start_lock, self.manager._cond:
            if self.started:
                raise ValueError("Cannot change archive policy after starting workspace")
            updates = {}
            if archive_mode is not _UNSET:
                updates["mode"] = archive_mode
            if archive_dir is not _UNSET:
                updates["archive_dir"] = archive_dir
            if temp_dir is not _UNSET:
                updates["temp_dir"] = temp_dir
            if retention_days is not _UNSET:
                updates["retention_days"] = retention_days
            if pass_retention_days is not _UNSET:
                updates["pass_retention_days"] = pass_retention_days
            if fail_retention_days is not _UNSET:
                updates["fail_retention_days"] = fail_retention_days
            if stage_on_network_fs is not _UNSET:
                updates["stage_on_network_fs"] = stage_on_network_fs
            new_policy = self._archive_policy.with_overrides(**updates)
            self._archive_policy = new_policy
            if override:
                cli_updates = dict(updates)
                if "mode" in cli_updates:
                    cli_updates["archive_mode"] = cli_updates.pop("mode")
                self._cli_archive_policy = {**(self._cli_archive_policy or {}), **cli_updates}
            if override and (self.archive is not None or self.archive_error is not None):
                if self.archive is not None:
                    old_path = self.archive.path
                    self.archive.set_outcome("UNSTARTED")
                    self.archive.close(clean=True)
                    remove_archive_files(old_path)
                    self.archive = None
                self.archive_error = None
                self._archive_target_dir = None
                self._archive_temp_dir = None
                self._archive_staged = False
                self._archive_finalized = False
                self._archive_disposition = None
            return new_policy

    def _new_receiver(self, job, attempt):
        flow = self._flows.get(job.id)
        if isinstance(job, _FileAttempt):
            return job.receiver(self, attempt, flow)
        return AttemptSink(self, job, attempt, flow)

    def flow_for(self, job):
        return self._flows.get(job.id)

    def add(self, job, *, flow=None):
        if not isinstance(job, Job):
            raise TypeError("add expects a Job")
        if flow is not None and not isinstance(flow, Flow):
            raise TypeError("flow must be a Flow")
        manager = self.manager
        manager._require_no_job_event()
        with manager._start_lock:
            self._check_open()
            if job.id in self._flows or job in manager.jobs:
                raise ValueError("Job is already registered")
            self._flows[job.id] = flow
            try:
                manager.add(job)
                if self._started_empty and not manager.started:
                    manager.start()
                return job
            except BaseException:
                if job not in manager.jobs:
                    self._flows.pop(job.id)
                raise

    def add_many(self, jobs, *, flow=None):
        if flow is not None and not isinstance(flow, Flow):
            raise TypeError("flow must be a Flow")
        return [self.add(job, flow=flow) for job in jobs]

    def _check_open(self):
        with self.manager._cond:
            if self._closing_sources or self.manager._closing or self.manager._closed:
                raise RuntimeError("workspace is closing or closed")

    def add_source(self, source, *, flow=None, name=None):
        """Register an external source and start its reader if already running."""
        if not isinstance(source, FileSource):
            raise TypeError("source must be a FileSource")
        self._require_no_job_event()
        with self.manager._start_lock:
            base = (source.path.name or str(source.path)) if name is None else name
            if not isinstance(base, str):
                raise TypeError("source name must be a string")
            used = {os.path.normcase(job.name) for job in self.jobs}
            candidate = base
            serial = 2
            while os.path.normcase(candidate) in used:
                candidate = f"{base} ({serial})"
                serial += 1
            job = _FileAttempt(source, candidate)
            task = None
            try:
                self.add(job, flow=flow)
                task = _SourceTask(job.run_source, f"Wave-source-{job.id}", self._sources_changed, job=job)
                with self.manager._cond:
                    self._source_tasks[job.id] = task
                if self.started:
                    task.start()
            except BaseException as exc:
                # An empty watcher starts the core after manager.add() registers its first job.
                if (job in self.jobs and not job._attempt.finalized
                        and (task is None or not task.started)):
                    job.finish_reader(exc)
                raise
            return job

    def add_paths(self, paths, *, once=False, globs=("*.log",), recursive=False,
                  flow=None, **source_options):
        """Import files and optionally follow directories for new matching files."""
        from ._source_discovery import add_paths

        return add_paths(self, paths, once=once, globs=globs, recursive=recursive,
                         flow=flow, source_options=source_options)

    def _watch_directory(self, poll):
        task = _SourceTask(poll, "Wave-source-discovery", self._sources_changed)
        with self.manager._cond:
            self._watchers.append(task)
        if self.started:
            task.start()

    @property
    def started(self):
        return self.manager.started or self._started_empty

    def validate(self):
        self._require_no_job_event()
        with self.manager._start_lock:
            self._check_open()
            if self.jobs or not self._watchers:
                self.manager.validate()
            else:
                with self.manager._cond:
                    self.manager.ready = True
            if self._initial_columns is not None:
                configure_columns(self, self._initial_columns)
                self._initial_columns = None

    def start(self):
        self._require_no_job_event()
        with self.manager._start_lock:
            self.validate()
            if self.jobs:
                self.manager.start()
            else:
                if _DEFINING.get():
                    raise ValueError("work files define jobs; let Wave start dispatch")
                # The core requires a nonempty roster. Start it as soon as
                # discovery registers the first source.
                with self.manager._cond:
                    self._started_empty = True
                    self.manager.paused = False
            for task in (*self._source_tasks.values(), *self._watchers):
                task.start()

    def resume(self):
        self.start()

    def snapshot(self, *, selected_job_id=None, attempt_id=None, include_logs=True):
        return self._snapshot(selected_job_id=selected_job_id, attempt_id=attempt_id,
                              include_logs=include_logs)

    def _source_roster(self):
        with self.manager._cond:
            return tuple(self._source_tasks.items()), tuple(self._watchers)

    @staticmethod
    def _source_summary(readers, watchers):
        stoppable = [job_id for job_id, task in readers if task.active]
        active_watchers = sum(task.active for task in watchers)
        return dict(configured_watchers=len(watchers), active_watchers=active_watchers,
                    active_readers=len(stoppable), stoppable_jobs=stoppable,
                    watching=bool(active_watchers or stoppable))

    def _snapshot(self, *, capture_preferences=None, **kwargs):
        readers, watchers = (), ()

        def capture(jobs):
            nonlocal readers, watchers
            # The core already holds _cond. Capture references only, then
            # inspect task activity after the core releases its locks.
            readers, watchers = self._source_roster()
            if capture_preferences is not None:
                capture_preferences(jobs)

        result = self.manager._snapshot(capture_preferences=capture, **kwargs)
        result["started"] = result["started"] or self._started_empty
        result["archive"] = self.archive_health
        result["watcher_error"] = self.watcher_error
        result["sources"] = self._source_summary(readers, watchers)
        return result

    @property
    def watcher_error(self):
        for task in tuple(self._watchers):
            if (failure := task.failure) is not None:
                return f"{type(failure).__name__}: {failure}"
        return None

    def stop_sources(self, job_id=None):
        """Stop one reader, or all readers and directory watchers."""
        self._require_no_job_event()
        with self.manager._start_lock:
            if job_id is None:
                for task in (*self._source_tasks.values(), *self._watchers):
                    task.stop.set()
            elif (task := self._source_tasks.get(job_id)) is not None:
                task.stop.set()

    def _join_sources(self, deadline=None, *, fail_fast=False):
        while True:
            self._sources_changed.clear()
            with self.manager._start_lock:
                tasks = (*self._source_tasks.values(), *self._watchers)
                if fail_fast:
                    for task in tasks:
                        if task.failure is not None:
                            raise task.failure
                thread = next((task.thread for task in tasks if task.started and task.thread.is_alive()), None)
            if thread is None:
                return tasks
            # Return to Python periodically so console interrupts can be
            # delivered even when a live reader has no completion deadline.
            remaining = .1 if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                return None
            if fail_fast:
                self._sources_changed.wait(min(.1, remaining))
            else:
                thread.join(min(.1, remaining))

    def _drain(self, timeout=None):
        if in_receiver_callback():
            raise RuntimeError("cannot block the runner from a callback")
        self._require_no_job_event()
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            tasks = self._join_sources(deadline, fail_fast=True)
            if tasks is None:
                return False
            remaining = .1 if deadline is None else max(0, deadline - time.monotonic())
            if self.manager._drain(min(.1, remaining)):
                with self.manager._start_lock:
                    if len(tasks) == len(self._source_tasks) + len(self._watchers):
                        return True
            elif deadline is not None and time.monotonic() >= deadline:
                return False

    def close(self):
        if in_receiver_callback():
            raise RuntimeError("cannot close the runner inside a callback; request stop instead")
        self._require_no_job_event()
        with self._close_lock:
            sources_closed = False
            clean = False
            # A KeyboardInterrupt inside _join_sources() must not leave this unbound.
            tasks = ()
            try:
                try:
                    with self.manager._start_lock:
                        self._closing_sources = True
                        self.stop_sources()
                        queued = [
                            job for job in self.jobs
                            if isinstance(job, _FileAttempt) and not job._attempt.finalized
                            and ((task := self._source_tasks.get(job.id)) is None or not task.started)
                        ]
                    failure = None
                    for job in queued:
                        try:
                            job.finish_reader()
                        except Exception as exc:
                            if failure is None:
                                failure = exc
                    if failure is not None:
                        raise failure
                finally:
                    tasks = self._join_sources()
                sources_closed = True
            finally:
                try:
                    try:
                        self.manager.close()
                    finally:
                        self._save_current_attempts()
                    clean = (sources_closed and self.manager._active_workers == 0
                             and all(task.failure is None for task in tasks))
                finally:
                    if self.archive and not self._archive_finalized:
                        self._archive_finalized = True
                        outcome = self._compute_run_outcome(clean=clean)
                        if clean:
                            self.archive.set_outcome(outcome)
                        self.archive.close(clean=clean)
                        self._finalize_archive_disposition(outcome=outcome)

    def remove_job(self, job_id, *, expected_attempt=None, expected_submission=None):
        """Remove a queued or stopped job, cleaning Flow bindings and unstarted Archive rows."""
        self._require_no_job_event()
        with self.manager._start_lock:
            was_started = self.started
            job = self.manager.get_job(job_id)
            attempt_id = job.attempt_id if job is not None else None
            receipt = self.manager.remove_job(
                job_id,
                expected_attempt=expected_attempt,
                expected_submission=expected_submission,
            )
            if receipt.accepted:
                key = job.id if job is not None else job_id
                self._removed_flows[key] = self._flows.pop(key, None)
                with self.manager._cond:
                    self._source_tasks.pop(key, None)
                if not was_started and self.archive is not None and attempt_id:
                    self.archive.delete_unstarted_attempt(attempt_id)
            return receipt

    def restore_job(self, job_id, *, expected_attempt=None, expected_submission=None):
        """Restore a previously removed job and reattach its Flow binding and Preview Archive row."""
        self._require_no_job_event()
        with self.manager._start_lock:
            receipt = self.manager.restore_job(
                job_id,
                expected_attempt=expected_attempt,
                expected_submission=expected_submission,
            )
            if receipt.accepted:
                job = self.manager.get_job(job_id)
                key = job.id if job is not None else job_id
                if key in self._removed_flows:
                    self._flows[key] = self._removed_flows.pop(key)
                if job is not None and not self.started and self.archive is not None:
                    with job._events_lock:
                        self._save_attempt(job, job._attempt, flush=True)
            return receipt

    # The workspace exposes the core controls used by Wave clients.
    _CORE_OPERATIONS = frozenset((
        "jobs", "removed_jobs", "paused", "ready", "max_workers", "max_history", "scheduling_mode",
        "get_job", "pause", "stop_all", "control", "rerun", "set_priority",
        "set_held", "reorder_queue",
        "set_max_log_lines", "set_max_history", "update_settings", "explain_pending",
        "add_log_listener", "_cond", "_apply_settings", "_require_no_job_event",
    ))

    def __getattr__(self, name):
        if name in self._CORE_OPERATIONS:
            return getattr(self.manager, name)
        raise AttributeError(name)

    @property
    def archive_catalog_dir(self) -> Path:
        if self._archive_target_dir is not None:
            return self._archive_target_dir
        if self.archive is not None:
            return self.archive.path.parent
        return self._archive_policy.resolved_archive_dir()

    def _prune_target_dir(self) -> None:
        target_dir = self._archive_target_dir
        if target_dir is None:
            return
        policy = self._archive_policy
        pass_days = policy.effective_pass_retention_days()
        fail_days = policy.effective_fail_retention_days()
        if pass_days is None and fail_days is None:
            return
        result = prune_archives(
            target_dir,
            pass_retention_days=pass_days,
            fail_retention_days=fail_days,
            exclude_run_id=self.run_id,
        )
        self._archive_pruned += len(result["deleted"])

    def _compute_run_outcome(self, *, clean: bool) -> str:
        if not self.started:
            return "UNSTARTED"
        jobs = self.manager._ordered_jobs()
        if not clean or self.watcher_error is not None:
            any_fail = any(
                j.status is JobStatus.FAILED
                or j.result in ("FAIL", "ERROR")
                or bool(j._attempt.errors)
                for j in jobs
            )
            return "FAIL" if (any_fail or self.watcher_error is not None) else "INCOMPLETE"
        if not jobs:
            return "INCOMPLETE"
        has_fail = False
        has_incomplete = False
        for job in jobs:
            snap = job.to_snapshot_dict(include_details=False)
            status = snap.get("status")
            result = snap.get("result")
            err_count = snap.get("error_count", 0)
            if status == "FAILED" or result in ("FAIL", "ERROR") or err_count:
                has_fail = True
            elif status != "DONE" or result not in ("PASS", "N/A"):
                has_incomplete = True
        if has_fail:
            return "FAIL"
        if has_incomplete:
            return "INCOMPLETE"
        return "PASS"

    def _finalize_archive_disposition(self, *, outcome: str) -> None:
        archive = self.archive
        if archive is None:
            return
        policy = self._archive_policy
        if policy.mode == "ephemeral":
            remove_archive_files(archive.path)
            self._archive_disposition = "discarded_ephemeral"
            return
        if policy.mode == "keep_on_fail" and outcome in ("PASS", "UNSTARTED") and archive.error is None:
            remove_archive_files(archive.path)
            self._archive_disposition = "discarded_pass" if outcome == "PASS" else "discarded_unstarted"
            return
        if self._archive_staged and self._archive_target_dir is not None and archive.path.exists():
            try:
                moved = move_archive_file(archive.path, self._archive_target_dir)
                archive.path = moved
            except OSError as exc:
                archive.error = dict(message=f"{type(exc).__name__}: {exc}", path=str(archive.path))
                self._archive_disposition = "move_failed"
                return
        self._archive_disposition = "saved_on_failure" if policy.mode == "keep_on_fail" else "saved"
        self._prune_target_dir()

    def enable_archive(self, directory=None, *, source_path=None):
        """Enable complete local history before dispatch, without changing verdicts.

        Wave calls this automatically. Direct Python users may opt in here or
        with ``archive_dir=...``. An already enabled archive is never replaced.
        """
        self._require_no_job_event()
        with self.manager._start_lock, self.manager._cond:
            if self.archive is not None or self.archive_error is not None:
                if self.archive and source_path and not self.started and self.archive.metadata["source_path"] is None:
                    self.archive.bind_source(str(Path(source_path).resolve()))
                return
            if self.started or self._closing_sources or self.manager._closing or self.manager._closed:
                raise ValueError("Enable history before starting the manager")
            policy = self._archive_policy
            if directory is not None:
                policy = policy.with_overrides(archive_dir=directory)
                self._archive_policy = policy
                target_dir = resolve_directory_option(directory, default_fn=default_archive_dir)
            else:
                target_dir = policy.resolved_archive_dir()
            temp_dir = policy.resolved_temp_dir()
            if policy.mode in ("ephemeral", "keep_on_fail"):
                staged = True
                active_dir = temp_dir
            elif (
                policy.stage_on_network_fs
                and _network_filesystem(target_dir) is not None
                and _network_filesystem(temp_dir) is None
                and temp_dir != target_dir
            ):
                staged = True
                active_dir = temp_dir
            else:
                staged = False
                active_dir = target_dir
            self._archive_target_dir = target_dir
            self._archive_temp_dir = temp_dir
            self._archive_staged = staged
            self._archive_disposition = None
            filename = archive_filename(self.run_id, source_path=source_path)
            path = active_dir / filename
            try:
                if staged and policy.temp_dir is None and not (os.environ.get("RPK_WAVE_TEMP_DIR") or "").strip():
                    active_dir = _ensure_private_dir(active_dir)
                    self._archive_temp_dir = active_dir
                    path = active_dir / filename
                self.archive = RunArchive(
                    path, create=True, run_id=self.run_id,
                    source_path=str(Path(source_path).resolve()) if source_path else None,
                )
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                self.archive_error = dict(path=str(path.absolute()), message=f"{type(exc).__name__}: {exc}")
                return
        self._save_current_attempts()
        self.archive.flush()
        if self._archive_policy.mode != "ephemeral":
            self._prune_target_dir()

    def _save_current_attempts(self, *, jobs=None, flush=False):
        if self.archive:
            self._require_no_job_event()
            for job in self.manager._ordered_jobs() if jobs is None else jobs:
                with job._events_lock:
                    self._save_attempt(job, job._attempt, flush=flush)

    def _save_attempt(self, job, attempt, *, flush=False, snapshot=None):
        store = self.archive
        if store is None:
            return
        sink = attempt.sink
        if isinstance(sink, AttemptSink) and sink.source_kind != "process":
            with job._events_lock:
                if not sink.prepare_source_snapshot():
                    return
                if snapshot is None:
                    snapshot = job._snapshot_attempt(attempt, include_details=True)
                with store.record_batch(interrupted=sink._interrupt_source):
                    if sink.flush_source():
                        store.save_attempt(snapshot, flush=flush)
        else:
            if snapshot is None:
                snapshot = job._snapshot_attempt(attempt, include_details=True)
            store.save_attempt(snapshot, flush=flush)

    @property
    def archive_health(self):
        policy = self._archive_policy
        target_dir = (
            str(self._archive_target_dir)
            if self._archive_target_dir is not None
            else str(policy.resolved_archive_dir())
        )
        temp_dir = (
            str(self._archive_temp_dir)
            if self._archive_temp_dir is not None
            else str(policy.resolved_temp_dir())
        )
        extra = dict(
            mode=policy.mode,
            target_dir=target_dir,
            temp_dir=temp_dir,
            staged=self._archive_staged,
            disposition=self._archive_disposition,
            pruned_count=self._archive_pruned,
        )
        if self.archive:
            health = self.archive.health
            health.update(extra)
            return health
        return dict(enabled=False, error=self.archive_error, **extra)

