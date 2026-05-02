"""
func_job.py — Concrete Job subclass for local Python function execution.
"""

from __future__ import annotations

import logging
import uuid
import traceback
from typing import Any, Callable

from .job import Job

logger = logging.getLogger(__name__)

class FuncJob(Job):
    """A job that runs a Python function in a background thread.

    Warning: 
        Because it runs in a thread, `FuncJob` is subject to the Python GIL.
        It is ideal for I/O bound tasks, but CPU bound tasks may block the 
        scheduler or fail to parallelize.
        
        Also note that calling `job.cancel()` on a `FuncJob` will immediately
        set its status to `CANCELLED`, but due to Python's threading limitations, 
        the underlying function execution cannot be forcefully stopped and will 
        continue running in the background until it finishes naturally.

    Example:
        job = FuncJob("parse", my_function, args=(1, 2), kwargs={"foo": "bar"})
    """

    def __init__(
        self,
        name: str,
        func: Callable[..., Any],
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
        *,
        job_id: str | uuid.UUID | None = None,
        priority: int = 0,
        max_retries: int = 0,
        resources: dict[str, int] | None = None,
        max_log_lines: int = 10_000,
        tags: frozenset[str] | set[str] | None = None,
    ) -> None:
        super().__init__(name, job_id=job_id, priority=priority, max_retries=max_retries, resources=resources, max_log_lines=max_log_lines, tags=tags)
        self.func = func
        self.args = args or ()
        self.kwargs = kwargs or {}

    def _execute(self, log_file=None) -> None:
        """Run the user function."""
        with self._lock:
            if self.is_cancelled:
                return

        try:
            res = self.func(*self.args, **self.kwargs)
            with self._lock:
                if not self.is_cancelled:
                    self._result = res
        except Exception as e:
            err_str = traceback.format_exc()
            self._emit_line(err_str)
            if log_file:
                log_file.write(err_str + "\n")
                log_file.flush()
            with self._lock:
                self._error = str(e)

    def cancel(self) -> None:
        """Cancel the job.

        Note that cancelling a ``FuncJob`` only marks the job as cancelled.
        The underlying Python function keeps running until it returns.
        """
        was_active = self.status not in ("done", "failed", "cancelled")
        super().cancel()
        if was_active:
            logger.warning(
                "FuncJob %r was marked cancelled, but its Python function cannot "
                "be force-stopped and may continue running in the background.",
                self.name,
            )

    def kill(self) -> None:
        """
        FuncJob cannot be forcefully killed because Python threads cannot be 
        terminated from the outside. The state is marked CANCELLED and the 
        scheduler will ignore the result when the function eventually returns.
        """
        pass
