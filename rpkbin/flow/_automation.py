"""Bounded serial delivery of live Automation callbacks for one Flow session.

User Python executes without the collector's locks. Backlog overflow fails the
affected Automation explicitly instead of dropping its input or blocking Log IO.
"""

from collections import deque
from copy import deepcopy
import json
import threading
import time


MAX_PENDING = 256
MAX_PENDING_BYTES = 4 * 1024 * 1024
COMPLETION_TIMEOUT = 2.0


class AutomationRunner:
    def __init__(self, session):
        self.session = session
        self._condition = threading.Condition()
        self._pending = deque()
        self._bytes = 0
        self._running = None
        self._thread = None
        self._finishing = False
        self.closed = False
        self._timer = None

    def submit(self, step, kind, value, evidence, source, *, execution=None):
        delivery = (value, evidence, source)
        if execution is not None:
            delivery += (execution,)
        # Do not duplicate a huge Report merely to discover it exceeds the queue.
        size = 0
        for part in json.JSONEncoder(ensure_ascii=True).iterencode(delivery):
            size += len(part)
            if size > MAX_PENDING_BYTES:
                break
        overflow = False
        with self._condition:
            if self.closed or self._finishing or step.error:
                return
            if len(self._pending) >= MAX_PENDING or self._bytes + size > MAX_PENDING_BYTES:
                overflow = True
            else:
                self._pending.append((step, kind, *deepcopy(delivery), size))
                self._bytes += size
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, daemon=True, name="Wave-automation")
                    self._thread.start()
                self._condition.notify_all()
        if overflow:
            self.session._automation_fail(step, RuntimeError("Automation backlog exceeded 256 deliveries or 4 MiB; disabled for this Run"), evidence)

    def _run(self):
        while True:
            with self._condition:
                while not self._pending and not self._finishing and not self.closed:
                    self._condition.wait()
                if self.closed or not self._pending and self._finishing:
                    if self._timer is not None:
                        self._timer.cancel()
                    self._condition.notify_all()
                    return
                item = self._pending.popleft()
                self._bytes -= item[-1]
                self._running = item[0]
            try:
                if not self._running.error:
                    self.session._run_automation(*item[:-1])
            finally:
                with self._condition:
                    self._running = None
                callback = self.session._on_automation_idle
                if callback is not None:
                    try:
                        callback()
                    except Exception as exc:
                        self.session._automation_fail(item[0], exc, item[3])
                with self._condition:
                    self._condition.notify_all()

    def finish(self):
        with self._condition:
            if self._finishing:
                return
            self._finishing = True
            if self._pending or self._running is not None:
                self._timer = threading.Timer(COMPLETION_TIMEOUT, self._expire)
                self._timer.daemon = True
                self._timer.start()
            self._condition.notify_all()

    def _expire(self):
        if self.pending:
            self.session.close_automation()

    @property
    def pending(self):
        with self._condition:
            return not self.closed and (bool(self._pending) or self._running is not None)

    def wait(self, timeout):
        if self._thread is threading.current_thread():
            raise RuntimeError("An Automation cannot wait for its own callback")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._pending or self._running is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self):
        with self._condition:
            self.closed = True
            if self._timer is not None:
                self._timer.cancel()
            affected = {item[0].spec.name: item[0] for item in self._pending}
            if self._running is not None:
                affected[self._running.spec.name] = self._running
            self._pending.clear()
            self._bytes = 0
            self._condition.notify_all()
        for step in affected.values():
            self.session._automation_fail(step, RuntimeError("Automation completion deadline exceeded; further operations rejected"), None)
