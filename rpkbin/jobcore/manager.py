"""Fixed-quota scheduler shared by Python, headless and the browser."""

from __future__ import annotations

import contextvars
import os
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from ._job_attempt import _JobEvents
from ._job_config import _integer
from ._job_snapshot import _detach
from .job import Job
from ._receiver import in_receiver_callback
from .model import ControlReceipt, JobStatus

_DEFINING = contextvars.ContextVar("jobcore_defining", default=False)

_READY = "READY"
_NOT_STARTED = "NOT_STARTED"
_READY_TO_START_PENDING_BEGIN = "READY_TO_START_PENDING_BEGIN"
_PAUSED = "PAUSED"
_HELD = "HELD"
_DISPATCHING = "DISPATCHING"
_WAIT_WORKER = "WAIT_WORKER"
_WAIT_RESOURCE = "WAIT_RESOURCE"
_SCHEDULING_MODES = ("backfill", "strict", "tier_strict")
_SAFE_MAX_INT = (1 << 53) - 1


def _ordered_priorities(existing):
    """Return strictly decreasing safe-integer priorities preserving existing spacing."""
    if not existing:
        return []
    pool = sorted(existing, reverse=True)
    for index in range(len(pool) - 2, -1, -1):
        if pool[index] <= pool[index + 1]:
            pool[index] = pool[index + 1] + 1
    if max(abs(value) for value in pool) > _SAFE_MAX_INT:
        count = len(pool)
        return [count - index for index in range(count)]
    return pool


def _scheduling_mode(value):
    """Validate and normalize a JobManager priority scheduling mode."""
    if not isinstance(value, str):
        raise TypeError("scheduling_mode must be 'backfill', 'strict', or 'tier_strict'")
    if value not in _SCHEDULING_MODES:
        raise ValueError("scheduling_mode must be 'backfill', 'strict', or 'tier_strict'")
    return str(value)


@contextmanager
def definition_scope():
    token = _DEFINING.set(True)
    try:
        yield
    finally:
        _DEFINING.reset(token)


_UNSET = object()

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
        max_history=20,
        scheduling_mode="backfill",
    ):
        self._cond = threading.Condition(threading.RLock())
        self._start_lock = threading.RLock()
        self.max_workers = _integer(max_workers, "max_workers", minimum=1)
        self.resources = self._capacities({} if resources is None else resources)
        if log_dir is not None:
            raw_log_dir = os.fspath(log_dir)
            if not isinstance(raw_log_dir, str):
                raise TypeError("log_dir must be text or PathLike")
            if not raw_log_dir.strip():
                raise ValueError("log_dir must not be empty")
            self.log_dir = Path(raw_log_dir).absolute()
        else:
            self.log_dir = None
        self.max_history = _integer(max_history, "max_history", minimum=1)
        self.scheduling_mode = _scheduling_mode(scheduling_mode)
        self.jobs = []
        self._removed = []
        self.paused, self.started, self.ready = True, False, False
        self._closed = False
        self._closing = False
        self._order, self._counter = {}, 0
        self._positions, self._position_counter = {}, 0
        self._active_workers = 0
        self._schedule_blocker = _UNSET
        self._used = {}
        self._threads = set()
        self._scheduler = None
        self._listeners = []
        self._receiver_factory = None

    # Snapshot the roster under _cond, then visit Jobs in ID order, one lock
    # at a time. Never wait for a Job or receiver lock with _cond held.
    @staticmethod
    def _require_no_job_event():
        if _JobEvents.held() is not None:
            raise RuntimeError("Cannot visit multiple Jobs while handling a Job event")

    def _ordered_jobs(self):
        with self._cond:
            return sorted(self.jobs, key=lambda job: job.id)

    def set_receiver_factory(self, factory):
        """Install a receiver factory for pending and future Jobs before dispatch."""
        if not callable(factory):
            raise TypeError("receiver factory must be callable")
        self._require_no_job_event()
        with self._start_lock:
            with self._cond:
                if self.started or self._closed or self._closing or self._receiver_factory is not None:
                    raise ValueError("Receiver factory must be installed once before dispatch")
                jobs = tuple(self.jobs)
            # Prepare before installing so a factory failure cannot leave half
            # the roster attached. Start and add are excluded by _start_lock.
            receivers = []
            for job in jobs:
                with job._events_lock:
                    attempt = job._attempt
                    receiver = None if attempt.finalized else factory(job, attempt)
                    receivers.append((job, attempt, receiver))
            for job, attempt, receiver in receivers:
                with job._events_lock, job._lock:
                    if not attempt.finalized:
                        attempt.sink = receiver
                    job._receiver_factory = factory
            with self._cond:
                self._receiver_factory = factory

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

    def add(self, job, *, receiver_factory=None):
        if not isinstance(job, Job):
            raise TypeError("add expects a Job")
        self._require_no_job_event()
        with self._start_lock, job._events_lock:
            with self._cond:
                if self._closed or self._closing:
                    raise RuntimeError("manager is closing or closed")
                if job in self.jobs:
                    raise ValueError("Job is already registered")
                if job._manager_ref is not None and job._manager_ref is not self:
                    raise ValueError("Job already belongs to a different manager")
                if self.started:
                    self._validate_job(job)
            if self.started and not job._frozen:
                job._freeze()
            factory = self._receiver_factory if receiver_factory is None else receiver_factory
            if factory is not None:
                if not callable(factory):
                    raise TypeError("receiver factory must be callable")
                if not job._attempt.finalized:
                    receiver = factory(job, job._attempt)
                    with job._lock:
                        job._attempt.sink = receiver
                job._receiver_factory = factory
            with self._cond:
                if self._closed or self._closing:
                    raise RuntimeError("manager is closing or closed")
                self.jobs.append(job)
                self._position_counter += 1
                self._positions[job.id] = self._position_counter
                job._manager_ref = self
                self._enqueue(job)
                self.ready = self.started
                self._cond.notify_all()
            job._attempt.sink.publish_state()
        return job

    def add_many(self, jobs):
        return [self.add(job) for job in jobs]

    def get_job(self, job_id_or_name):
        with self._cond:
            if isinstance(job_id_or_name, Job):
                return job_id_or_name if job_id_or_name in self.jobs else None
            found = [job for job in self.jobs if job.id == job_id_or_name]
            if found:
                return found[0]
            found = [job for job in self.jobs if job.name == job_id_or_name]
            if len(found) > 1:
                raise ValueError("ambiguous job name; use job.id")
            return found[0] if found else None

    def validate(self):
        while True:
            with self._cond:
                if self._closed or self._closing:
                    raise RuntimeError("manager is closing or closed")
                if not self.jobs:
                    raise ValueError("work file contains no jobs")
                jobs = tuple(self.jobs)
            # No worker is started during validation, including when a later job is invalid.
            for job in jobs:
                if not job._frozen:
                    with job._events_lock:
                        with self._cond:
                            self._validate_job(job)
                        job._freeze()
            with self._cond:
                if self._closed or self._closing:
                    raise RuntimeError("manager is closing or closed")
                if tuple(self.jobs) != jobs:
                    continue
                self.ready = True
                return

    def start(self):
        if _DEFINING.get():
            raise ValueError(
                "work files define jobs; remove start()/resume() and let Wave start dispatch"
            )
        if not self.started:
            self._require_no_job_event()
        with self._start_lock:
            while True:
                with self._cond:
                    if self._closed or self._closing:
                        raise RuntimeError("manager is closing or closed")
                    if self.started:
                        self.paused = False
                        self._cond.notify_all()
                        return
                self.validate()
                jobs = self._ordered_jobs()
                for job in jobs:
                    with job._events_lock:
                        job._attempt.sink.publish_state(flush=True)
                with self._cond:
                    if self._closed or self._closing:
                        raise RuntimeError("manager is closing or closed")
                    if sorted(self.jobs, key=lambda job: job.id) != jobs or any(not job._frozen for job in jobs):
                        continue
                    scheduler = threading.Thread(
                        target=self._schedule, daemon=True, name="Wave-scheduler"
                    )
                    # The scheduler cannot dispatch until we release _cond.
                    scheduler.start()
                    self.started, self.paused = True, False
                    self._scheduler = scheduler
                    self._cond.notify_all()
                    return

    def pause(self):
        with self._cond:
            self.paused = True
            self._cond.notify_all()

    def resume(self):
        self.start()

    def _enqueue(self, job):
        self._counter += 1
        self._order[job.id] = self._counter

    def _queue_key(self, job):
        return (-job.priority, self._order[job.id])

    def _pending_queue(self):
        return sorted(
            (
                j
                for j in self.jobs
                if j.status is JobStatus.PENDING and not j._attempt.reserved and not j._attempt.held
            ),
            key=self._queue_key,
        )

    def _local_pending_reason(self, job):
        if job._attempt.held:
            return _HELD, "Held (excluded from queue)"
        if not self.started:
            return (
                (_READY_TO_START_PENDING_BEGIN, "Ready to start")
                if self.ready else (_NOT_STARTED, "Not started")
            )
        if self.paused:
            return _PAUSED, "Paused (dispatch is paused)"
        if job._attempt.reserved:
            return _DISPATCHING, "Dispatching"
        if self._active_workers >= self.max_workers:
            return _WAIT_WORKER, f"Waiting for worker slot ({self._active_workers}/{self.max_workers} busy)"
        for name, needed in job.resources.items():
            capacity = self.resources.get(name, 0)
            used = self._used.get(name, 0)
            if used + needed > capacity:
                return _WAIT_RESOURCE, f"Waiting for {name} ({used}/{capacity} in use, need {needed})"
        return _READY, "Ready to start"

    @staticmethod
    def _blocked_reason(blocker, blocker_code, blocker_text):
        code = _WAIT_RESOURCE if blocker_code == _WAIT_RESOURCE else _WAIT_WORKER
        return code, f"Waiting behind higher-priority job {blocker.name} ({blocker_text})"

    def _resolve_pending_reason(self, job, local_reason, blocker):
        code, text = local_reason
        if code != _READY or self.scheduling_mode == "backfill" or blocker is None:
            return code, text
        b_job, b_code, b_text = blocker
        if self.scheduling_mode == "strict" or b_job.priority > job.priority:
            return self._blocked_reason(b_job, b_code, b_text)
        return code, text

    def _find_blocker(self, *, before):
        before_key = self._queue_key(before)
        strict = self.scheduling_mode == "strict"
        earlier = sorted(
            (
                j
                for j in self.jobs
                if j is not before
                and j.status is JobStatus.PENDING
                and not j._attempt.reserved
                and not j._attempt.held
                and (
                    self._queue_key(j) < before_key
                    if strict
                    else j.priority > before.priority
                )
            ),
            key=self._queue_key,
        )
        for candidate in earlier:
            code, text = self._local_pending_reason(candidate)
            if code != _READY:
                return candidate, code, text
        return None

    def _pending_reason(self, job):
        local = self._local_pending_reason(job)
        if local[0] != _READY or self.scheduling_mode == "backfill":
            return local
        blocker = (
            self._find_blocker(before=job)
            if self._schedule_blocker is _UNSET
            else self._schedule_blocker
        )
        return self._resolve_pending_reason(job, local, blocker)

    def _snapshot_pending_reasons(self, jobs):
        pending = [j for j in jobs if j.status is JobStatus.PENDING]
        if not self.started or self.paused or self.scheduling_mode == "backfill":
            return {j.id: (j.attempt_id, self._pending_reason(j)) for j in pending}
        reasons = {}
        first_blocker = None
        for job in sorted(pending, key=self._queue_key):
            local = self._local_pending_reason(job)
            if job._attempt.reserved or job._attempt.held:
                reasons[job.id] = (job.attempt_id, local)
                continue
            reasons[job.id] = (
                job.attempt_id,
                self._resolve_pending_reason(job, local, first_blocker),
            )
            if first_blocker is None and local[0] != _READY:
                first_blocker = (job, local[0], local[1])
        return reasons

    def explain_pending(self, job):
        with self._cond:
            job = self.get_job(job)
            if job is None:
                raise ValueError("unknown Job")
            return (
                self._pending_reason(job)[1]
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
                    pending = self._pending_queue()
                    blocked_priority = None
                    # One sorted pass; same blocker rule as _snapshot_pending_reasons.
                    first_blocker = None
                    try:
                        for job in pending:
                            if blocked_priority is not None and job.priority < blocked_priority:
                                break
                            self._schedule_blocker = first_blocker
                            reason_code, reason_text = self._pending_reason(job)
                            if reason_code != _READY:
                                if first_blocker is None:
                                    first_blocker = (job, reason_code, reason_text)
                                if self.scheduling_mode == "strict":
                                    break
                                if self.scheduling_mode == "tier_strict":
                                    if reason_code == _WAIT_WORKER:
                                        break
                                    if blocked_priority is None:
                                        blocked_priority = job.priority
                                continue
                            # The scheduler never waits for a Job under _cond.
                            # A busy higher-priority Job is reconsidered next pass.
                            if not job._events_lock.acquire(blocking=False):
                                break
                            try:
                                if job._attempt.finalized or job._attempt.held:
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
                            finally:
                                job._events_lock.release()
                    finally:
                        self._schedule_blocker = _UNSET
                if not launch:
                    self._cond.wait(timeout=0.1)
            for thread in launch:
                thread.start()

    def _remember_attempt(self, job, attempt):
        job._history.append(attempt)
        while len(job._history) > self.max_history:
            job._history.popleft()
            job._history_truncated += 1

    def _worker(self, job, a):
        try:
            job._execute(a)
        finally:
            try:
                with job._events_lock:
                    with self._cond:
                        retry = (
                            not self._closed
                            and not self._closing
                            and not a.stop_requested
                            and not a.interrupt_requested
                            and a.returncode not in (None, 0)
                            and a.retry_index < job.max_retries
                        )
                    replacement = None
                    try:
                        if retry:
                            replacement = job._new_attempt(a.submission_id, a.retry_index + 1)
                    finally:
                        with self._cond:
                            self._active_workers -= 1
                            for name, amount in job.resources.items():
                                self._used[name] = max(0, self._used.get(name, 0) - amount)
                            a.reserved = False
                            a.retry_scheduled = replacement is not None and not self._closed and not self._closing
                            if a.retry_scheduled:
                                self._remember_attempt(job, a)
                                job._attempt = replacement
                                self._enqueue(job)
                            self._cond.notify_all()
                        job._seal_attempt(a)
                        job._publish_observer_roster()
                        a.sink.publish_state(flush=True)
                        if a.retry_scheduled:
                            replacement.sink.publish_state()
            finally:
                with self._cond:
                    self._threads.discard(threading.current_thread())
                    self._cond.notify_all()

    def _cancelled(self, job, a):
        with self._cond:
            self._cond.notify_all()
        a.sink.publish_state(flush=True)

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
        if in_receiver_callback() or _JobEvents.held() is not None:
            raise RuntimeError("cannot block the runner from a callback")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while (
                any(not j._attempt.finalized for j in self.jobs)
                or any(j._attempt.sink.pending for j in self.jobs)
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

    def stop_all(self, *, force=False, source="user", reason=None, include_held=True):
        self._require_no_job_event()
        self.pause()
        jobs = self._ordered_jobs()
        count = 0
        # Cancel pending jobs first. Within each group visit Jobs in ID order.
        for job in sorted(jobs, key=lambda j: j.status is not JobStatus.PENDING):
            if not include_held and job.status is JobStatus.PENDING and job._attempt.held:
                continue
            if job.stop(force=force, source=source, reason=reason):
                count += 1
        return count

    def _control_job(self, job_id):
        try:
            job = self.get_job(job_id)
        except ValueError as exc:
            return None, ControlReceipt("rejected", str(exc))
        if job is None:
            return None, ControlReceipt("rejected", "Unknown Job")
        if job._control_lock_conflict():
            return None, ControlReceipt("rejected", "Cannot control another Job from a Job event")
        return job, None

    def control(
        self, job_id, action, *, expected_attempt, expected_submission, text=None,
        secret=False, newline=True, sig=None,
    ):
        job, receipt = self._control_job(job_id)
        if receipt is not None:
            return receipt
        with job._events_lock:
            a, receipt = job._control_target(expected_attempt, expected_submission)
            if receipt is not None:
                return receipt
            attempt_id = a.id
            if action == "stop":
                return job.stop(expected_attempt=attempt_id)
            if action == "force_stop":
                return job.kill(expected_attempt=attempt_id)
            if action == "interrupt":
                return job.send_ctrl_c(expected_attempt=attempt_id)
            if action == "signal":
                if sig is None:
                    return ControlReceipt("rejected", "Signal is required")
                return job.send_signal(sig, expected_attempt=attempt_id)
            if action != "send":
                return ControlReceipt("rejected", "Unknown control action")
        return job.send(text, newline=newline, secret=secret, expected_attempt=attempt_id)

    def set_priority(
        self, job_id, priority, *, expected_attempt=None, expected_submission=None
    ):
        priority = _integer(priority, "priority")
        job, receipt = self._control_job(job_id)
        if receipt is not None:
            return receipt
        with job._events_lock:
            a, receipt = job._control_target(expected_attempt, expected_submission)
            if receipt is not None:
                return receipt
            if not a.capabilities.has_process:
                return ControlReceipt("rejected", "Attempt does not support priority changes")
            with self._cond:
                if self._closed or self._closing:
                    return ControlReceipt("rejected", "Manager is closing or closed")
                if a.status is not JobStatus.PENDING or a.reserved or a.finalized:
                    return ControlReceipt("rejected", "Priority can change only while queued")
                job._priority = a.priority = priority
                self._cond.notify_all()
            a.sink.publish_state()
        return ControlReceipt("accepted", "Priority updated")

    def set_held(
        self, job_id, held, *, expected_attempt=None, expected_submission=None
    ):
        if type(held) is not bool:
            raise TypeError("held must be bool")
        job, receipt = self._control_job(job_id)
        if receipt is not None:
            return receipt
        with job._events_lock:
            a, receipt = job._control_target(expected_attempt, expected_submission)
            if receipt is not None:
                return receipt
            if not a.capabilities.has_process:
                return ControlReceipt("rejected", "Attempt does not support queue hold")
            with self._cond:
                if self._closed or self._closing:
                    return ControlReceipt("rejected", "Manager is closing or closed")
                if a.status is not JobStatus.PENDING or a.reserved or a.finalized:
                    return ControlReceipt("rejected", "Hold can change only while queued")
                a.held = held
                self._cond.notify_all()
            a.sink.publish_state()
        return ControlReceipt(
            "accepted",
            "Job held (excluded from queue)" if held else "Job included in queue",
        )

    def reorder_queue(self, targets):
        """Atomically reassign strictly decreasing priorities across queued targets."""
        self._require_no_job_event()
        if not isinstance(targets, (list, tuple)) or not targets:
            raise ValueError("targets must be a nonempty sequence")
        seen = set()
        normalized = []
        for entry in targets:
            if isinstance(entry, tuple) and len(entry) == 3:
                target, attempt, submission = entry
            elif isinstance(entry, (str, Job)):
                target, attempt, submission = entry, None, None
            else:
                raise ValueError("Invalid queue reorder target")
            if not isinstance(target, (str, Job)):
                raise ValueError("Invalid queue reorder target")
            jid = target.id if isinstance(target, Job) else target
            if jid in seen:
                raise ValueError("Duplicate job in queue reorder")
            seen.add(jid)
            normalized.append((jid, target, attempt, submission))
        with self._cond:
            if self._closed or self._closing:
                return [
                    (jid, ControlReceipt("rejected", "Manager is closing or closed"), None)
                    for jid, _, _, _ in normalized
                ]
            seen_jobs = set()
            resolved = []
            for jid, target, attempt, submission in normalized:
                job, receipt = self._control_job(target)
                if job is not None:
                    if job.id in seen_jobs:
                        raise ValueError("Duplicate job in queue reorder")
                    seen_jobs.add(job.id)
                resolved.append((jid, attempt, submission, job, receipt))
        candidates = sorted(
            (job for _, _, _, job, receipt in resolved if job is not None and receipt is None),
            key=lambda job: job.id,
        )
        with ExitStack() as stack:
            _JobEvents._local.batch = True
            try:
                for job in candidates:
                    stack.enter_context(job._events_lock)
            finally:
                _JobEvents._local.batch = False
            rows = []
            eligible = []
            with self._cond:
                closing = self._closed or self._closing
                for jid, attempt, submission, job, receipt in resolved:
                    a = None
                    if receipt is None and closing:
                        receipt = ControlReceipt("rejected", "Manager is closing or closed")
                    if receipt is None and job not in self.jobs:
                        receipt = ControlReceipt("rejected", "Unknown Job")
                    if receipt is None:
                        a, receipt = job._control_target(attempt, submission)
                    if receipt is None:
                        if not a.capabilities.has_process:
                            receipt = ControlReceipt(
                                "rejected", "Attempt does not support priority changes"
                            )
                        elif a.status is not JobStatus.PENDING or a.reserved or a.finalized:
                            receipt = ControlReceipt(
                                "rejected", "Priority can change only while queued"
                            )
                        else:
                            eligible.append((job, a))
                    rows.append((jid, job, a, receipt))
                priorities = _ordered_priorities([job._priority for job, _ in eligible])
                assigned = {job.id: value for (job, _), value in zip(eligible, priorities)}
                for (job, a), value in zip(eligible, priorities):
                    job._priority = a.priority = value
                if eligible:
                    self._cond.notify_all()
            results = []
            for jid, job, a, receipt in rows:
                if receipt is not None:
                    results.append((jid, receipt, None))
                    continue
                a.sink.publish_state()
                results.append(
                    (jid, ControlReceipt("accepted", "Priority updated"), assigned[job.id])
                )
            return results

    def remove_job(
        self, job_id, *, expected_attempt=None, expected_submission=None
    ):
        """Remove a job in Preview or after all jobs have stopped cleanly."""
        self._require_no_job_event()
        with self._start_lock:
            job, receipt = self._control_job(job_id)
            if receipt is not None:
                return receipt
            with job._events_lock:
                a, receipt = job._control_target(expected_attempt, expected_submission)
                if receipt is not None:
                    return receipt
                if not a.capabilities.has_process:
                    return ControlReceipt("rejected", "Attempt cannot be removed from the queue")
                with self._cond:
                    if self._closed or self._closing:
                        return ControlReceipt("rejected", "Manager is closing or closed")
                    if job not in self.jobs:
                        return ControlReceipt("rejected", "Unknown Job")
                    if not self.started:
                        if a.status is not JobStatus.PENDING or a.reserved:
                            return ControlReceipt("rejected", "Job is not queued")
                    elif (
                        not self.paused
                        or self._active_workers > 0
                        or self._threads
                        or any(j._attempt.reserved or j.status.is_active for j in self.jobs)
                    ):
                        return ControlReceipt(
                            "rejected",
                            "Remove jobs only in Preview or after all jobs have stopped",
                        )
                    self.jobs.remove(job)
                    order_rank = self._order.pop(job.id, self._counter)
                    self._removed = [
                        entry for entry in self._removed if entry[0].id != job.id
                    ]
                    self._removed.append((job, order_rank))
                    job._manager_ref = None
                    if not self.started:
                        self.ready = bool(self.jobs)
                    self._cond.notify_all()
            return ControlReceipt("accepted", "Job removed from queue")

    @property
    def removed_jobs(self):
        with self._cond:
            return tuple(job for job, _ in self._removed)

    def restore_job(
        self, job_id, *, expected_attempt=None, expected_submission=None
    ):
        """Restore a previously removed job in Preview or after all jobs have stopped."""
        self._require_no_job_event()
        with self._start_lock:
            with self._cond:
                if self._closed or self._closing:
                    return ControlReceipt("rejected", "Manager is closing or closed")
                if isinstance(job_id, Job):
                    matches = [(j, rank) for j, rank in self._removed if j is job_id]
                elif isinstance(job_id, str):
                    matches = [(j, rank) for j, rank in self._removed if j.id == job_id]
                    if not matches:
                        matches = [(j, rank) for j, rank in self._removed if j.name == job_id]
                        if len(matches) > 1:
                            return ControlReceipt("rejected", "ambiguous job name; use job.id")
                else:
                    return ControlReceipt("rejected", "Unknown removed Job")
                if not matches:
                    return ControlReceipt("rejected", "Unknown removed Job")
                job, _ = matches[0]
            if job._control_lock_conflict():
                return ControlReceipt(
                    "rejected", "Cannot control another Job from a Job event"
                )
            with job._events_lock:
                with self._cond:
                    if self._closed or self._closing:
                        return ControlReceipt("rejected", "Manager is closing or closed")
                    idx = next(
                        (i for i, (j, _) in enumerate(self._removed) if j is job), None
                    )
                    if idx is None:
                        return ControlReceipt("rejected", "Unknown removed Job")
                    if (
                        job._manager_ref is not None and job._manager_ref is not self
                    ) or job in self.jobs:
                        return ControlReceipt(
                            "rejected", "Job already belongs to a queue"
                        )
                    if (
                        expected_attempt is not None
                        and job.attempt_id != expected_attempt
                    ) or (
                        expected_submission is not None
                        and job.submission_id != expected_submission
                    ):
                        return ControlReceipt(
                            "rejected", "Target attempt no longer matches"
                        )
                    if not self.started:
                        pass
                    elif (
                        not self.paused
                        or self._active_workers > 0
                        or self._threads
                        or any(
                            j._attempt.reserved or j.status.is_active for j in self.jobs
                        )
                    ):
                        return ControlReceipt(
                            "rejected",
                            "Restore jobs only in Preview or after all jobs have stopped",
                        )
                    try:
                        self._validate_job(job)
                    except ValueError as exc:
                        return ControlReceipt("rejected", str(exc))
                    started_now = self.started
                if started_now and not job._frozen:
                    job._freeze()
                with self._cond:
                    if self._closed or self._closing:
                        return ControlReceipt("rejected", "Manager is closing or closed")
                    idx = next(
                        (i for i, (j, _) in enumerate(self._removed) if j is job), None
                    )
                    if idx is None:
                        return ControlReceipt("rejected", "Unknown removed Job")
                    _, order_rank = self._removed.pop(idx)
                    job._manager_ref = self
                    self._order[job.id] = order_rank
                    self.jobs.append(job)
                    self.jobs.sort(key=lambda j: self._positions.get(j.id, 0))
                    if not self.started:
                        self.ready = True
                    self._cond.notify_all()
            return ControlReceipt("accepted", "Job restored to queue")

    def rerun(self, job_id, *, expected_attempt=None, expected_submission=None):
        job, receipt = self._control_job(job_id)
        if receipt is not None:
            return receipt
        with job._events_lock:
            a, receipt = job._control_target(expected_attempt, expected_submission)
            if receipt is not None:
                return receipt
            if not a.capabilities.can_rerun:
                return ControlReceipt("rejected", "Attempt does not support rerun")
            if not a.can_rerun():
                return ControlReceipt("rejected", "Rerun requires a fully finished attempt")
            with self._cond:
                if self._closed or self._closing:
                    return ControlReceipt("rejected", "Manager is closing or closed")
            replacement = job._new_attempt(uuid.uuid4().hex, 0)
            with self._cond:
                if self._closed or self._closing:
                    return ControlReceipt("rejected", "Manager is closing or closed")
                self._remember_attempt(job, a)
                job._attempt = replacement
                job._publish_observer_roster()
                self._enqueue(job)
                self._cond.notify_all()
            a.sink.publish_state(flush=True)
            replacement.sink.publish_state()
        return ControlReceipt("accepted", "Rerun queued")

    def set_max_log_lines(
        self,
        max_log_lines,
        job_id=None,
        *,
        expected_attempt=None,
        expected_submission=None,
    ):
        return self._apply_settings(
            log_lines=max_log_lines,
            log_job_id=job_id,
            expected_attempt=expected_attempt,
            expected_submission=expected_submission,
        )["evicted_lines"]

    def _trim_histories(self):
        evicted = 0
        for job in self._ordered_jobs():
            with job._events_lock, self._cond:
                excess = max(0, len(job._history) - self.max_history)
                for _ in range(excess):
                    job._history.popleft()
                if excess:
                    job._history_truncated += excess
                    job._publish_observer_roster()
                    evicted += excess
        return evicted

    def set_max_history(self, max_history):
        return self._apply_settings(
            max_history=max_history, history_only=True
        )["evicted_history"]

    def update_settings(
        self, *, max_workers=None, resources=None, max_history=None, scheduling_mode=None
    ):
        return self._apply_settings(
            max_workers=max_workers,
            resources=resources,
            max_history=max_history,
            scheduling_mode=scheduling_mode,
        )

    def _apply_settings(
        self, *, max_workers=None, resources=None, max_history=None,
        scheduling_mode=None, history_only=False, log_lines=_UNSET, log_job_id=None,
        expected_attempt=None, expected_submission=None, apply_preferences=None,
    ):
        # Targeted Log changes take only that Job's event lock. Bulk changes
        # capture the roster under the manager lock and then visit Jobs individually.
        if log_lines is not _UNSET:
            lines = _integer(log_lines, "max_log_lines", minimum=1)
            if log_job_id is not None:
                job = self.get_job(log_job_id)
                if job is None:
                    raise ValueError("unknown Job")
                evicted = job.set_max_log_lines(
                    lines, expected_attempt=expected_attempt, expected_submission=expected_submission,
                )
            else:
                if expected_attempt is not None or expected_submission is not None:
                    raise ValueError("attempt/submission identity requires a specific Job")
                self._require_no_job_event()
                with self._cond:
                    jobs = list(self.jobs)
                evicted = sum(job.set_max_log_lines(lines) for job in jobs)
            return {"evicted_lines": evicted}

        if history_only:
            self._require_no_job_event()
        workers = (
            None
            if max_workers is None
            else _integer(max_workers, "max_workers", minimum=1)
        )
        capacities = None if resources is None else self._capacities(resources)
        mode = None if scheduling_mode is None else _scheduling_mode(scheduling_mode)
        history_limit = (
            None
            if max_history is None and not history_only
            else _integer(max_history, "max_history", minimum=1)
        )
        if history_limit is not None and not history_only:
            self._require_no_job_event()
        with self._cond:
            if not history_only:
                workers = self.max_workers if workers is None else workers
                capacities = self.resources if capacities is None else capacities
                mode = self.scheduling_mode if mode is None else mode
                required = {name for j in self.jobs for name in j.resources}
                if not required.issubset(capacities):
                    raise ValueError(
                        "cannot remove a resource referenced by a Job; set capacity to 0 to pause it"
                    )
                if apply_preferences is not None:
                    # Publish workspace preferences with the matching core settings.
                    apply_preferences()
                self.max_workers, self.resources, self.scheduling_mode = (
                    workers,
                    dict(capacities),
                    mode,
                )
            if history_limit is not None:
                self.max_history = history_limit
            self._cond.notify_all()
        return {"evicted_history": self._trim_histories() if history_limit is not None else 0}

    def snapshot(self, *, selected_job_id=None, attempt_id=None, include_logs=True):
        return self._snapshot(selected_job_id=selected_job_id, attempt_id=attempt_id,
                              include_logs=include_logs)

    def _snapshot(self, *, selected_job_id, attempt_id, include_logs, log_cursor=None,
                  capture_preferences=None):
        with self._cond:
            jobs = list(self.jobs)
            removed = [job for job, _ in self._removed]
            if capture_preferences is not None:
                # Capture workspace preferences against the same roster and settings.
                capture_preferences(jobs)
            result = dict(
                paused=self.paused,
                started=self.started,
                ready=self.ready,
                max_workers=self.max_workers,
                active_workers=self._active_workers,
                resources=dict(self.resources),
                resources_in_use=dict(self._used),
                max_history=self.max_history,
                scheduling_mode=self.scheduling_mode,
            )
            reasons = self._snapshot_pending_reasons(jobs)
        snapshots = []
        detail = None
        observer = log_cursor is not None or _JobEvents.held() is not None
        for job in jobs:
            selected = job.id == selected_job_id
            if not observer:
                row = job.to_snapshot_dict(include_logs=include_logs and selected and not attempt_id)
                if selected:
                    detail = job.to_snapshot_dict(include_logs=include_logs, attempt_id=attempt_id,
                                                  include_details=True)
            else:
                row, selected_detail = job._capture_observer(
                    selected=selected, attempt_id=attempt_id, include_logs=include_logs, log_cursor=log_cursor
                )
                if selected:
                    detail = selected_detail
                    if detail is None:
                        if log_cursor is None:
                            raise ValueError("attempt history is no longer retained")
                        result["history_error"] = "Requested attempt is no longer retained"
            for view in (row, detail if selected else None):
                reason = reasons.get(job.id)
                # Ignore reasons for a different attempt or a job that has started.
                if view is not None and reason is not None and view["attempt_id"] == reason[0] and view["status"] == JobStatus.PENDING.value:
                    view["pending_code"], view["pending_reason"] = reason[1]
            snapshots.append(row)
        removed_snapshots = []
        for job in removed:
            if not observer:
                snap = job.to_snapshot_dict(include_logs=False, include_details=False)
            else:
                snap, _ = job._capture_observer(
                    selected=False, attempt_id=None, include_logs=False, log_cursor=None
                )
            removed_snapshots.append(snap)
        result.update(jobs=snapshots, removed_jobs=removed_snapshots, detail=detail)
        return _detach(result) if observer else result

    def close(self):
        if in_receiver_callback():
            raise RuntimeError(
                "cannot close the runner inside a callback; request stop instead"
            )
        self._require_no_job_event()
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
            removed = [job for job, _ in self._removed]
            self._cond.notify_all()
        for job in [*self._ordered_jobs(), *sorted(removed, key=lambda job: job.id)]:
            job._attempt.sink.close()
        scheduler = self._scheduler
        if (scheduler is not None and scheduler.ident is not None
                and scheduler is not threading.current_thread()):
            scheduler.join(timeout=2)

    def __enter__(self):
        with self._cond:
            if self._closed or self._closing:
                raise RuntimeError("manager is closing or closed")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __repr__(self):
        with self._cond:
            return (
                f"JobManager(jobs={len(self.jobs)}, active={self._active_workers}/{self.max_workers}, "
                f"started={self.started}, paused={self.paused}, closed={self._closed})"
            )
