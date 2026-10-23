"""Fixed-quota scheduler shared by Python, headless and the browser."""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .job import Job, _BoundJob, _CALLBACK, _integer
from .model import JobStatus, freeze

_DEFINING = contextvars.ContextVar("wave_defining", default=False)


@contextmanager
def definition_scope():
    token = _DEFINING.set(True)
    try:
        yield
    finally:
        _DEFINING.reset(token)


class JobManager:
    """Register first, validate atomically, then explicitly dispatch.

    Runtime capacity changes do not preempt active jobs. Failed command attempts
    release resources before retries rejoin the queue at their priority's tail.
    """

    def __init__(
        self,
        *,
        max_workers=4,
        resources=None,
        log_dir=None,
        columns=None,
        max_history=20,
    ):
        self._cond = threading.Condition(threading.RLock())
        # One serial monitor dispatch per manager also makes callbacks which
        # control another Job safe without acquiring two independent Job locks.
        self._events_lock = threading.RLock()
        self.max_workers = _integer(max_workers, "max_workers", minimum=1)
        self.resources = self._capacities({} if resources is None else resources)
        self.log_dir = Path(log_dir) if log_dir is not None else None
        if columns is not None and (
            not isinstance(columns, (list, tuple))
            or not all(isinstance(c, str) for c in columns)
        ):
            raise TypeError("columns must be a sequence of names")
        self.columns = None if columns is None else list(dict.fromkeys(columns))
        self.max_history = _integer(max_history, "max_history", minimum=1)
        self.jobs = []
        self.paused, self.started, self.ready = True, False, False
        self._closed = False
        self._closing = False
        self._order, self._counter = {}, 0
        self._active_workers = 0
        self._used = {}
        self._threads = set()
        self._scheduler = None
        self._listeners = []

    @staticmethod
    def _capacities(resources):
        if not isinstance(resources, dict):
            raise TypeError("resources must be a dictionary")
        out = {}
        for name, value in resources.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("resource name must be nonempty")
            out[name] = _integer(value, f"capacity {name}", minimum=0)
        return out

    def _validate_job(self, job):
        if job._manager_ref is not None and job._manager_ref is not self:
            raise ValueError("Job already belongs to a different manager")
        for name, needed in job.resources.items():
            if name not in self.resources:
                raise ValueError(f"{job.name}: unknown resource {name}")
            if needed > self.resources[name]:
                raise ValueError(
                    f"{job.name}: needs {needed} {name}, capacity is {self.resources[name]}"
                )
        job._freeze()

    def add(self, job):
        if not isinstance(job, Job):
            raise TypeError("add expects a Job")
        with self._cond:
            if self._closed or self._closing:
                raise RuntimeError("manager is closing or closed")
            if job in self.jobs:
                raise ValueError("Job is already registered")
            if job._manager_ref is not None and job._manager_ref is not self:
                raise ValueError("Job already belongs to a different manager")
            if self.started:
                self._validate_job(job)
            self.jobs.append(job)
            job._manager_ref = self
            job._events_lock = self._events_lock
            self._enqueue(job)
            self.ready = self.started
            self._cond.notify_all()
        return job

    def add_many(self, jobs):
        return [self.add(job) for job in jobs]

    def get_job(self, job_id_or_name):
        if isinstance(job_id_or_name, _BoundJob):
            job_id_or_name = job_id_or_name._job
        if isinstance(job_id_or_name, Job):
            return job_id_or_name if job_id_or_name in self.jobs else None
        with self._cond:
            found = [job for job in self.jobs if job.id == job_id_or_name]
            if found:
                return found[0]
            found = [job for job in self.jobs if job.name == job_id_or_name]
            if len(found) > 1:
                raise ValueError("ambiguous job name; use job.id")
            return found[0] if found else None

    def validate(self):
        with self._cond:
            if self._closed or self._closing:
                raise RuntimeError("manager is closing or closed")
            if not self.jobs:
                raise ValueError("work file contains no jobs")
            # No worker is started during validation, including when a later job is invalid.
            for job in self.jobs:
                if not job._frozen:
                    self._validate_job(job)
            if self.columns is not None and not set(self.columns).issubset(
                self._available_columns()
            ):
                raise ValueError("unknown display column")
            self.ready = True

    def start(self):
        if _DEFINING.get():
            raise ValueError(
                "work files define jobs; remove start()/resume() and let Wave start dispatch"
            )
        self.validate()
        with self._cond:
            self.started, self.paused = True, False
            if self._scheduler is None:
                self._scheduler = threading.Thread(
                    target=self._schedule, daemon=True, name="Wave-scheduler"
                )
                self._scheduler.start()
            self._cond.notify_all()

    def pause(self):
        with self._cond:
            self.paused = True
            self._cond.notify_all()

    def resume(self):
        self.start()

    def _enqueue(self, job):
        self._counter += 1
        self._order[job.id] = self._counter

    def _pending_reason(self, job):
        if not self.started:
            return "Ready to start" if self.ready else "Not started"
        if self.paused:
            return "Paused (dispatch is paused)"
        if job._attempt.reserved:
            return "Dispatching"
        if self._active_workers >= self.max_workers:
            return f"Waiting for worker slot ({self._active_workers}/{self.max_workers} busy)"
        for name, needed in job.resources.items():
            capacity = self.resources.get(name, 0)
            used = self._used.get(name, 0)
            if used + needed > capacity:
                return f"Waiting for {name} ({used}/{capacity} in use, need {needed})"
        return "Ready to start"

    def explain_pending(self, job):
        with self._cond:
            job = self.get_job(job)
            if job is None:
                raise ValueError("unknown Job")
            return (
                self._pending_reason(job)
                if job.status is JobStatus.PENDING
                else job.status.value
            )

    def _schedule(self):
        while True:
            launch = []
            with self._cond:
                if self._closed:
                    return
                if not self.paused and not self._closing:
                    pending = sorted(
                        (
                            j
                            for j in self.jobs
                            if j.status is JobStatus.PENDING and not j._attempt.reserved
                        ),
                        key=lambda j: (-j.priority, self._order[j.id]),
                    )
                    for job in pending:
                        if self._pending_reason(job) != "Ready to start":
                            continue
                        a = job._attempt
                        a.reserved = True
                        self._active_workers += 1
                        for name, amount in job.resources.items():
                            self._used[name] = self._used.get(name, 0) + amount
                        thread = threading.Thread(
                            target=self._worker,
                            args=(job, a),
                            daemon=True,
                            name=f"Wave-worker-{a.id}",
                        )
                        self._threads.add(thread)
                        launch.append(thread)
                if not launch:
                    self._cond.wait(timeout=0.1)
            for thread in launch:
                thread.start()

    def _archive(self, job, attempt):
        job._history.append(attempt)
        while len(job._history) > self.max_history:
            job._history.popleft()
            job._history_truncated += 1

    def _worker(self, job, a):
        try:
            job._execute(a)
        finally:
            with job._events_lock:
                with self._cond:
                    self._active_workers -= 1
                    for name, amount in job.resources.items():
                        self._used[name] = max(0, self._used.get(name, 0) - amount)
                    a.reserved = False
                    retry = (
                        not self._closed
                        and not self._closing
                        and not a.stop_requested
                        and a.returncode not in (None, 0)
                        and a.retry_index < job.max_retries
                    )
                    a.retry_scheduled = retry
                    if retry:
                        self._archive(job, a)
                        job._attempt = job._new_attempt(
                            a.submission_id, a.retry_index + 1
                        )
                        self._enqueue(job)
                    self._cond.notify_all()
            # Observers receive the completed attempt, even if the next one has already started.
            job._dispatch(
                a,
                [
                    (job.events.status_changed, (a.previous_status, a.status), None),
                    (
                        job.events.attempt_finished,
                        (freeze(job._snapshot_attempt(a)),),
                        None,
                    ),
                ],
                final=True,
            )
            with self._cond:
                self._threads.discard(threading.current_thread())
                self._cond.notify_all()

    def _cancelled(self, job, a):
        with self._cond:
            self._cond.notify_all()
        job._dispatch(
            a,
            [
                (job.events.status_changed, (JobStatus.PENDING, a.status), None),
                (
                    job.events.attempt_finished,
                    (freeze(job._snapshot_attempt(a)),),
                    None,
                ),
            ],
            final=True,
        )

    def add_log_listener(self, callback):
        if not callable(callback):
            raise TypeError("listener must be callable")
        with self._cond:
            self._listeners.append(callback)

    def _publish(self, record):
        # Presentation observers are not monitoring rules and cannot change a verdict.
        for callback in tuple(self._listeners):
            try:
                callback(dict(record))
            except Exception:
                pass

    def _drain(self, timeout=None):
        if _CALLBACK.get() is not None:
            raise RuntimeError("cannot block the runner from a callback")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while (
                any(not j._attempt.finalized for j in self.jobs)
                or self._active_workers
                or self._threads
            ):
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(min(0.1, remaining))
                else:
                    self._cond.wait(0.1)
        return True

    def stop_all(self, *, force=False, source="user", reason=None):
        self.pause()
        with self._cond:
            jobs = list(self.jobs)
        count = 0
        # Cancel pending jobs first so freed slots can never dispatch more work during shutdown.
        for job in sorted(jobs, key=lambda j: j.status is not JobStatus.PENDING):
            if job.stop(force=force, source=source, reason=reason):
                count += 1
        return count

    def control(
        self, job_id, action, *, expected_attempt, expected_submission, text=None
    ):
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("unknown Job")
        with job._events_lock:
            a = job._guard(expected_attempt, expected_submission)
            if action == "stop":
                return job.stop(expected_attempt=a.id)
            if action == "force_stop":
                return job.stop(force=True, expected_attempt=a.id)
            if action == "send":
                return job.send(text, expected_attempt=a.id)
            if action == "interrupt":
                return job.send_ctrl_c(expected_attempt=a.id)
            raise ValueError(f"unknown action: {action}")

    def set_priority(
        self, job_id, priority, *, expected_attempt=None, expected_submission=None
    ):
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("unknown Job")
        with job._events_lock:
            a = job._guard(expected_attempt, expected_submission)
            with self._cond:
                if a.status is not JobStatus.PENDING or a.reserved:
                    raise ValueError("priority can change only while queued")
                job._priority = _integer(priority, "priority")
                job._attempt.priority = job._priority
                self._cond.notify_all()

    def rerun(self, job_id, *, expected_attempt=None, expected_submission=None):
        bound = job_id if isinstance(job_id, _BoundJob) else None
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("unknown Job")
        with job._events_lock:
            a = job._guard(
                expected_attempt or (bound._attempt.id if bound else None),
                expected_submission,
            )
            if not a.finalized or a.reserved:
                raise ValueError("rerun requires a fully finished attempt")
            with self._cond:
                if self._closed or self._closing:
                    raise RuntimeError("manager is closing or closed")
                self._archive(job, a)
                job._attempt = job._new_attempt(uuid.uuid4().hex, 0)
                self._enqueue(job)
                self._cond.notify_all()
        return job

    def set_max_log_lines(
        self,
        max_log_lines,
        job_id=None,
        *,
        expected_attempt=None,
        expected_submission=None,
    ):
        lines = _integer(max_log_lines, "max_log_lines", minimum=1)
        if job_id is not None:
            job = self.get_job(job_id)
            if job is None:
                raise ValueError("unknown Job")
            return job.set_max_log_lines(
                lines,
                expected_attempt=expected_attempt,
                expected_submission=expected_submission,
            )
        if expected_attempt is not None or expected_submission is not None:
            raise ValueError("attempt/submission identity requires a specific Job")
        with self._cond:
            jobs = list(self.jobs)
        return sum(job.set_max_log_lines(lines) for job in jobs)

    def set_max_history(self, max_history):
        limit = _integer(max_history, "max_history", minimum=1)
        evicted = 0
        with self._events_lock:
            with self._cond:
                self.max_history = limit
                for job in self.jobs:
                    while len(job._history) > limit:
                        job._history.popleft()
                        job._history_truncated += 1
                        evicted += 1
                self._cond.notify_all()
        return evicted

    def update_settings(
        self, *, max_workers=None, resources=None, columns=None, max_history=None
    ):
        workers = (
            None
            if max_workers is None
            else _integer(max_workers, "max_workers", minimum=1)
        )
        capacities = None if resources is None else self._capacities(resources)
        history_limit = (
            None
            if max_history is None
            else _integer(max_history, "max_history", minimum=1)
        )
        if columns is not None and (
            not isinstance(columns, (list, tuple))
            or not all(isinstance(c, str) for c in columns)
        ):
            raise TypeError("columns must be a sequence of names")
        evicted_history = 0
        with self._events_lock:
            with self._cond:
                workers = self.max_workers if workers is None else workers
                capacities = self.resources if capacities is None else capacities
                required = {name for j in self.jobs for name in j.resources}
                if not required.issubset(capacities):
                    raise ValueError(
                        "cannot remove a resource referenced by a Job; set capacity to 0 to pause it"
                    )
                if columns is not None and not set(columns).issubset(
                    self._available_columns()
                ):
                    raise ValueError("unknown display column")
                self.max_workers, self.resources = workers, dict(capacities)
                if columns is not None:
                    self.columns = list(dict.fromkeys(columns))
                if history_limit is not None:
                    self.max_history = history_limit
                    for job in self.jobs:
                        while len(job._history) > history_limit:
                            job._history.popleft()
                            job._history_truncated += 1
                            evicted_history += 1
                self._cond.notify_all()
        return {"evicted_history": evicted_history}

    def _available_columns(self):
        return list(
            dict.fromkeys(track.name for job in self.jobs for track in job._tracks)
        )

    def snapshot(self, *, selected_job_id=None, attempt_id=None, include_logs=True):
        with self._cond:
            jobs = list(self.jobs)
            result = dict(
                paused=self.paused,
                started=self.started,
                ready=self.ready,
                max_workers=self.max_workers,
                active_workers=self._active_workers,
                resources=dict(self.resources),
                resources_in_use=dict(self._used),
                max_history=self.max_history,
                configured_columns=None if self.columns is None else list(self.columns),
                available_columns=self._available_columns(),
            )
            result["columns"] = (
                result["available_columns"]
                if self.columns is None
                else list(self.columns)
            )
            reasons = {
                j.id: self._pending_reason(j)
                for j in jobs
                if j.status is JobStatus.PENDING
            }
        snapshots = []
        detail = None
        for job in jobs:
            snapshot = job.to_snapshot_dict(
                include_logs=include_logs
                and job.id == selected_job_id
                and not attempt_id
            )
            if job.id in reasons:
                snapshot["pending_reason"] = reasons[job.id]
            snapshots.append(snapshot)
            if job.id == selected_job_id:
                detail = job.to_snapshot_dict(
                    include_logs=include_logs,
                    attempt_id=attempt_id,
                    include_monitor_definitions=True,
                )
                if detail["attempt_id"] == snapshot["attempt_id"]:
                    detail["pending_reason"] = snapshot["pending_reason"]
        result.update(jobs=snapshots, detail=detail)
        return result

    def close(self):
        if _CALLBACK.get() is not None:
            raise RuntimeError(
                "cannot close the runner inside a callback; request stop instead"
            )
        with self._cond:
            if self._closed:
                return
            self._closing = True
            self.paused = True
        self.stop_all(source="runner", reason="core shutdown")
        if not self._drain(timeout=max([j.stop_timeout for j in self.jobs] + [0]) + 5):
            self.stop_all(force=True, source="runner", reason="shutdown deadline")
            self._drain(timeout=5)
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        if self._scheduler and self._scheduler is not threading.current_thread():
            self._scheduler.join(timeout=2)
