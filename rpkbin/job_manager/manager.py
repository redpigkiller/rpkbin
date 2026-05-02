"""
manager.py — Thread-pool based job manager.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Any

from .job import Job, PENDING, RUNNING, DONE, FAILED, CANCELLED

logger = logging.getLogger(__name__)
SLOW_ASYNC_CALLBACK_WARNING_S = 1.0

class JobManager:
    """Manages concurrent job execution.
    
    Args:
        max_workers:   Number of worker threads (default 4).
        resources:     Capacity limits. Can be static ints or dynamic Callables returning ints.
        log_dir:       Directory to store standard job logs. None to disable files.
        max_history:   How many terminal jobs to keep in memory before GC.
        poll_interval: Seconds between scheduler loops if no jobs ready (default 0.5s).
    """

    def __init__(
        self,
        max_workers: int = 4,
        resources: dict[str, int | Callable[[], int]] | None = None,
        log_dir: str | Path | None = None,
        max_history: int = 1000,
        poll_interval: float = 0.5,
    ) -> None:
        self._max_workers = max(1, max_workers)
        self._resources: dict[str, int | Callable[[], int]] = resources or {}
        
        self._log_dir = Path(log_dir) if log_dir else None
        self._max_history = max_history
        self._poll_interval = poll_interval

        # Internal State tracking
        self._jobs: list[Job] = []
        self._used_resources: dict[str, int] = {}
        
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        
        # Runtime state and event bus
        self._active_workers: int = 0
        self._stop_event = threading.Event()
        self._paused = False
        self._bg_thread: threading.Thread | None = None
        self._worker_threads: set[threading.Thread] = set()
        self._callback_context = threading.local()

        self._event_bus = ThreadPoolExecutor(max_workers=2, thread_name_prefix="JobEventBus")
        self._event_bus_shutdown = False
        self._inflight_callbacks: int = 0
        self._jobs_finishing: int = 0
        
        # Callbacks
        self._on_queue_drained_cbs: list[Callable[['JobManager'], None]] = []

    def update_config(
        self, 
        max_workers: int | None = None, 
        resources: dict[str, int | Callable[[], int]] | None = None
    ) -> None:
        """Dynamically update manager configuration."""
        with self._lock:
            if max_workers is not None:
                self._max_workers = max(1, max_workers)
            if resources is not None:
                self._resources = resources
            self._cond.notify_all()

    def stats(self) -> dict[str, Any]:
        """Return a snapshot of current system load and resources for telemetry."""
        with self._lock:
            capacities = {}
            for res_name, limit_val in self._resources.items():
                try:
                    capacities[res_name] = limit_val() if callable(limit_val) else limit_val
                except Exception:
                    capacities[res_name] = 0

            return {
                "workers": {"used": self._active_workers, "total": self._max_workers},
                "resources": {
                    k: {"used": self._used_resources.get(k, 0), "total": v}
                    for k, v in capacities.items()
                },
                "jobs": {
                    "pending": len([j for j in self._jobs if j.status == PENDING]),
                    "running": len([j for j in self._jobs if j.status == RUNNING])
                }
            }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def __enter__(self) -> JobManager:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.cancel_all()
            self.stop()
        else:
            self.wait()
            self.stop()

    def start(self) -> None:
        """Start the background scheduling loop."""
        if self._bg_thread and self._bg_thread.is_alive():
            return
            
        if self._event_bus_shutdown:
            self._event_bus = ThreadPoolExecutor(max_workers=2, thread_name_prefix="JobEventBus")
            self._event_bus_shutdown = False
            
        self._stop_event.clear()
        with self._lock:
            self._worker_threads = {t for t in self._worker_threads if t.is_alive()}
        self._bg_thread = threading.Thread(
            target=self._execute_loop, daemon=True, name="JobManagerLoop"
        )
        self._bg_thread.start()

    def stop(self) -> None:
        """Stop the scheduler from dispatching new jobs."""
        self._stop_event.set()
        with self._cond:
            self._cond.notify_all()
        if self._bg_thread:
            self._bg_thread.join()
        with self._lock:
            workers = list(self._worker_threads)
        for t in workers:
            t.join()
        if not self._event_bus_shutdown:
            self._event_bus.shutdown(wait=True)
            self._event_bus_shutdown = True

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._cond.notify_all()

    def wait(self, target_id: str | uuid.UUID | None = None, timeout: float | None = None) -> bool:
        """Wait for a target job or for the manager to become quiescent.

        When ``target_id`` is provided, this waits only for that specific job to
        reach a terminal state (``done``, ``failed``, or ``cancelled``).

        When ``target_id`` is omitted, this waits for the whole manager to become
        idle: all known jobs must be terminal, worker-side finishing logic must be
        done, and async callbacks submitted by the manager must have drained. This
        stronger "quiescent" wait avoids races where callbacks enqueue follow-up
        work after the last visible job has already finished.
        """
        if getattr(self._callback_context, "inside_manager_callback", False):
            raise RuntimeError(
                "JobManager.wait() cannot be called from a manager callback "
                "(on_done/on_fail/on_retry/on_queue_drained), because it can deadlock. "
                "Schedule follow-up work without waiting for manager quiescence."
            )
        if not self._bg_thread or not self._bg_thread.is_alive():
            raise RuntimeError(
                "JobManager is not running. Call start() before wait(), "
                "or use 'with JobManager(...) as manager:'."
            )
            
        if isinstance(target_id, str):
            target_id = uuid.UUID(target_id)
        deadline = time.monotonic() + timeout if timeout is not None else None

        def _is_done() -> bool:
            if target_id is not None:
                j = self.get(target_id)
                if not j:
                    return True
                return j.status in (DONE, FAILED, CANCELLED)
            jobs_done = all(j.status in (DONE, FAILED, CANCELLED) for j in self._jobs)
            # Also wait for async callbacks; they may enqueue follow-up jobs.
            return jobs_done and self._inflight_callbacks == 0 and self._jobs_finishing == 0

        try:
            with self._cond:
                while not _is_done():
                    if self._stop_event.is_set():
                        break
                    t_rem = 0.1
                    if deadline is not None:
                        t_rem = deadline - time.monotonic()
                        if t_rem <= 0:
                            return False
                        t_rem = min(t_rem, 0.1)
                    self._cond.wait(timeout=t_rem)
                return True
        except KeyboardInterrupt:
            self.cancel_all()
            self.stop()
            raise

    # ------------------------------------------------------------------
    # Job Management
    # ------------------------------------------------------------------
    def add(self, job: Job) -> None:
        """Enqueue a job for execution."""
        # Static Resource Validation (Fail Fast if impossible)
        for res_name, req_val in job.resources.items():
            limit_val = self._resources.get(res_name)
            if limit_val is not None:
                # If static int, we can check capability
                if isinstance(limit_val, int) and isinstance(req_val, int) and req_val > limit_val:
                    raise ValueError(
                        f"Impossible Resource Request: Job requires {req_val} '{res_name}' "
                        f"but JobManager only supports up to {limit_val}."
                    )
                # If dynamic Callable, we trust it or let it fail at runtime
            else:
                logger.warning(
                    "Job %r requests resource %r which is not declared in the manager. "
                    "The job will never be scheduled unless the resource is added "
                    "via update_config().",
                    job.name, res_name,
                )

        with self._lock:
            if self._stop_event.is_set() and (self._bg_thread is None or not self._bg_thread.is_alive()):
                raise RuntimeError(
                    "JobManager has already been stopped and cannot accept new jobs."
                )
            if getattr(job, '_on_state_change_cb', None) is not None:
                raise ValueError(f"Job with ID {job.id} is already managed by a JobManager.")
            if any(j.id == job.id for j in self._jobs):
                raise ValueError(f"Job with ID {job.id} is already in the manager.")
            
            # SRE wake-up hook injection
            def _wake_up():
                with self._cond:
                    self._cond.notify_all()
            job._manager_set_wakeup_cb(_wake_up)

            self._jobs.append(job)
            self._cleanup_history()
            self._cond.notify_all()

    def get(self, target_id: str | uuid.UUID) -> Job | None:
        if isinstance(target_id, str):
            target_id = uuid.UUID(target_id)
        with self._lock:
            for j in self._jobs:
                if j.id == target_id:
                    return j
        return None

    def cancel_all(self) -> None:
        with self._lock:
            for j in self._jobs:
                if j.status in (PENDING, RUNNING):
                    j.cancel()

    # ------------------------------------------------------------------
    # Manager Events
    # ------------------------------------------------------------------
    def on_queue_drained(self, cb: Callable[['JobManager'], None]) -> None:
        """Register a callback to fire whenever the manager has no pending or running jobs."""
        with self._lock:
            self._on_queue_drained_cbs.append(cb)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------
    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    def running(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs if j.status == RUNNING]

    def pending(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs if j.status == PENDING]

    def finished(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs if j.status in (DONE, FAILED, CANCELLED)]

    def jobs_by_tag(self, tag: str) -> list[Job]:
        """Return all known jobs that have *tag* in their ``tags`` set."""
        with self._lock:
            return [j for j in self._jobs if tag in j.tags]

    def cancel_by_tag(self, tag: str) -> int:
        """Cancel all non-terminal jobs that have *tag* in their ``tags`` set.

        Returns the number of jobs cancelled.  Cancellation is performed
        outside the manager lock so that each job's own ``cancel()`` can
        run without nested-lock concerns.
        """
        with self._lock:
            targets = [
                j for j in self._jobs
                if tag in j.tags and j.status in (PENDING, RUNNING)
            ]
        for j in targets:
            j.cancel()
        return len(targets)

    # ------------------------------------------------------------------
    # Inner Execution Loop
    # ------------------------------------------------------------------
    def _execute_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._cond:
                ready_job = self._get_ready_job()
                if ready_job is None:
                    self._cond.wait(timeout=self._poll_interval)
                    continue

                # Dispatch job
                self._acquire_resources(ready_job)
                self._active_workers += 1
                
                if not ready_job._manager_try_start():
                    self._release_resources(ready_job)
                    self._active_workers -= 1
                    continue

                worker = threading.Thread(
                    target=self._run_job_wrapper_safe,
                    args=(ready_job,),
                    daemon=True,
                    name=f"JobWorker-{ready_job.name}",
                )
                self._worker_threads.add(worker)
                worker.start()

    def _run_job_wrapper_safe(self, job: Job) -> None:
        try:
            self._run_job_wrapper(job)
        except Exception as e:
            # Last-resort safeguard: ensure the job reaches a terminal state.
            with job._lock:
                if job._status in (PENDING, RUNNING):
                    job._status = FAILED
                    if not job._error:
                        job._error = str(e)
            try:
                job._manager_set_end_time()
            except Exception:
                logger.exception("Failed to set end time for job %r after worker error", job.name)
            with self._cond:
                self._cond.notify_all()
            logger.exception("Unexpected worker error while running job %r", job.name)
        finally:
            with self._lock:
                self._worker_threads.discard(threading.current_thread())

    def _get_ready_job(self) -> Job | None:
        if self._paused:
            return None
            
        if self._active_workers >= self._max_workers:
            return None

        # Gather dynamic capacities
        capacities = {}
        for res_name, limit_val in self._resources.items():
            try:
                if callable(limit_val):
                    capacities[res_name] = limit_val()
                else:
                    capacities[res_name] = limit_val
            except Exception as e:
                logger.error("Error computing dynamic resource %r: %s", res_name, e)
                capacities[res_name] = 0

        # Find highest priority pending job that fits
        pending_obs = sorted(
            [j for j in self._jobs if j.status == PENDING],
            key=lambda j: j.priority,
            reverse=True
        )

        for j in pending_obs:
            can_fit = True
            for res_name, req_val in j.resources.items():
                limit = capacities.get(res_name, 0)
                used = self._used_resources.get(res_name, 0)
                if used + req_val > limit:
                    can_fit = False
                    break
            if can_fit:
                return j

        return None

    def _acquire_resources(self, job: Job) -> None:
        # Called from _execute_loop which already holds self._cond (== self._lock).
        for res_name, req_val in job.resources.items():
            self._used_resources[res_name] = self._used_resources.get(res_name, 0) + req_val

    def _release_resources(self, job: Job) -> None:
        with self._lock:
            for res_name, req_val in job.resources.items():
                self._used_resources[res_name] = max(0, self._used_resources.get(res_name, 0) - req_val)
            self._cond.notify_all()

    def _cleanup_history(self) -> None:
        if len(self._jobs) <= self._max_history:
            return
        
        # Pop oldest finished
        finished_list = [j for j in self._jobs if j.status in (DONE, FAILED, CANCELLED)]
        diff = len(self._jobs) - self._max_history
        if diff > 0 and finished_list:
            to_remove = finished_list[:diff]
            for j in to_remove:
                # Remove callback hook to avoid leaks
                j._manager_set_wakeup_cb(None)
                self._jobs.remove(j)

    def _run_job_wrapper(self, job: Job) -> None:
        """Worker thread entrypoint for running constraints, retry logic, and cleanup."""
        with self._cond:
            self._jobs_finishing += 1

        try:
            attempt = job._retry_count
            log_file = None
            should_retry = False
            is_drained = False

            try:
                if self._log_dir:
                    self._log_dir.mkdir(parents=True, exist_ok=True)
                    path = self._log_dir / f"{job.name}_{job.id.hex[:8]}.log"
                    log_file = open(path, "a", encoding="utf-8")

                # Execute
                try:
                    job._execute(log_file=log_file)
                except Exception as e:
                    with job._lock:
                        if job._status == RUNNING:
                            job._status = FAILED
                            job._error = str(e)

                with job._lock:
                    if job._status == RUNNING:
                        if job._error is not None:
                            job._status = FAILED
                        else:
                            job._status = DONE

                    # Capture state for atomic cleanup outside the lock
                    status = job._status
                    is_cancelled = job.is_cancelled

                # Retry evaluation
                if status == FAILED and attempt < job.max_retries and not is_cancelled:
                    job._manager_reset_for_retry()
                    should_retry = True
                else:
                    # Final cleanup for terminal jobs
                    job._manager_set_end_time()
            finally:
                if log_file:
                    try:
                        log_file.close()
                    except Exception:
                        logger.exception("Failed to close log file for job %r", job.name)

                try:
                    self._release_resources(job)
                except Exception:
                    logger.exception("Failed to release resources for job %r", job.name)

                with self._lock:
                    self._active_workers = max(0, self._active_workers - 1)
                    if not should_retry and self._active_workers == 0 and not any(j.status in (PENDING, RUNNING) for j in self._jobs):
                        is_drained = True
                    self._cond.notify_all()

            if should_retry:
                # Dispatch retry callbacks before dropping out of the worker thread
                with job._lock:
                    attempt_num = job._retry_count
                    retry_cbs = list(job._on_retry_cbs)
                for cb in retry_cbs:
                    self._submit_callback(cb, job, attempt_num)
                return # Exit wrapper, let _execute_loop pick it up again

            # Dispatch final callbacks
            try:
                self._dispatch_callbacks(job)
            except Exception:
                logger.exception("Failed to dispatch callbacks for job %r", job.name)

            if is_drained:
                cbs = list(self._on_queue_drained_cbs)
                for cb in cbs:
                    try:
                        self._submit_callback(cb, self)
                    except Exception:
                        logger.exception("Failed to submit queue-drained callback")
        finally:
            with self._cond:
                self._jobs_finishing = max(0, self._jobs_finishing - 1)
                self._cond.notify_all()

    def _dispatch_callbacks(self, job: Job) -> None:
        with job._lock:
            status = job._status
            done_cbs = list(job._on_done_cbs)
            fail_cbs = list(job._on_fail_cbs)
            err = job._error or "Unknown failure"

        if status == DONE:
            for cb in done_cbs:
                self._submit_callback(cb, job)
        elif status == FAILED:
            for cb in fail_cbs:
                self._submit_callback(cb, job, err)

    def _submit_callback(self, cb: Callable[..., Any], *args: Any) -> None:
        """Submit a callback to the event bus and track in-flight count."""
        with self._cond:
            self._inflight_callbacks += 1

        def _wrapped() -> None:
            try:
                self._callback_context.inside_manager_callback = True
                t0 = time.perf_counter()
                cb(*args)
                dt = time.perf_counter() - t0
                if dt > SLOW_ASYNC_CALLBACK_WARNING_S:
                    logger.warning(
                        "Slow async callback detected (took %.2fs): %r. "
                        "Consider moving heavy work out of job/session callbacks.",
                        dt,
                        cb,
                    )
            except Exception:
                logger.exception("Async callback raised (ignored): %r", cb)
            finally:
                self._callback_context.inside_manager_callback = False
                with self._cond:
                    self._inflight_callbacks = max(0, self._inflight_callbacks - 1)
                    self._cond.notify_all()

        try:
            self._event_bus.submit(_wrapped)
        except Exception:
            # Roll back counter if submission itself fails.
            with self._cond:
                self._inflight_callbacks = max(0, self._inflight_callbacks - 1)
                self._cond.notify_all()
            raise
