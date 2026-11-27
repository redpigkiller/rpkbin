"""Nonblocking Qt transport and lifecycle for one owned, local IPython kernel."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import warnings

from PySide6.QtCore import QObject, QTimer, Signal

os.environ["QT_API"] = "pyside6"
from qtconsole.manager import QtKernelManager

from .protocol import CONTROL_MIME, PROTOCOL_VERSION, build_id, validate_envelope


class KernelTransport(QObject):
    client_changed = Signal(object)
    ready_changed = Signal(bool)
    busy_changed = Signal(bool)
    user_finished = Signal(object)
    reply_received = Signal(object, object)
    failed = Signal(str)
    status_changed = Signal(str)
    clear_console_requested = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.build = build_id()
        self.manager = None
        self.client = None
        self.ready = False
        self.busy = False
        self._requests = {}
        self._timed_out_ids: OrderedDict[str, None] = OrderedDict()
        self._temporary = None
        self._log = None
        self._bootstrap_id = None
        self._started = 0.0
        self._busy_started: float | None = None
        self._busy_total = 0.0
        self._closing = False
        self._last_health_check = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._check_health)

    def _idle_clock(self, now: float | None = None) -> float:
        if now is None:
            now = time.monotonic()
        active_busy = (now - self._busy_started) if self._busy_started is not None else 0.0
        return now - (self._busy_total + active_busy)

    def start(self):
        if self.manager is not None:
            return
        self._closing = False
        self._started = time.monotonic()
        self._busy_started = None
        self._busy_total = 0.0
        self._timed_out_ids.clear()
        self.status_changed.emit("Starting kernel…")
        try:
            self._temporary = tempfile.TemporaryDirectory(prefix="rpk-calc-")
            directory = Path(self._temporary.name)
            self._log = (directory / "kernel.log").open("w+b")
            self.manager = QtKernelManager(connection_file=str(directory / "kernel.json"),
                                           autorestart=False, ip="127.0.0.1")
            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                executable = executable.with_name("python.exe")
            self.manager.kernel_spec.argv = [str(executable), "-m", "ipykernel_launcher",
                                             "-f", "{connection_file}"]
            env = dict(os.environ)
            env["IPYTHONDIR"] = str(directory / "ipython")
            package_root = str(Path(__file__).resolve().parents[3])
            existing_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{package_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else package_root
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            self.manager.start_kernel(env=env, stdout=self._log, stderr=self._log, **options)
            self.client = self.manager.client()
            self.client.start_channels()
            client = self.client
            client.shell_channel.message_received.connect(
                lambda message: self._handle_reply(message) if self.client is client else None)
            client.iopub_channel.message_received.connect(
                lambda message: self._handle_publication(message) if self.client is client else None)
            self.client_changed.emit(client)
            expression = ("__import__('rpkbin.app.calc.kernel_runtime', fromlist=['install'])"
                          f".install({asdict(self.config)!r})")
            self._bootstrap_id = client.execute("", silent=True, store_history=False,
                user_expressions={"calc": expression})
            self._requests[self._bootstrap_id] = ({"op": "bootstrap"}, self._idle_clock())
            self._timer.start()
        except Exception as exc:
            self._fail(f"Kernel startup failed: {exc}")

    def request(self, request: dict) -> str:
        if not self.ready or self.client is None:
            raise RuntimeError("Kernel is not ready")
        request = dict(request, protocol=PROTOCOL_VERSION, build=self.build)
        message_id = self.client.execute("", silent=True, store_history=False,
            user_expressions={"calc": f"_rpk_calc_dispatch({request!r})"})
        self._requests[message_id] = (request, self._idle_clock())
        return message_id

    def _handle_reply(self, message):
        message_id = message.get("parent_header", {}).get("msg_id")
        if message.get("msg_type") != "execute_reply":
            return
        pending = self._requests.pop(message_id, None)
        if pending is None:
            if message_id in self._timed_out_ids:
                self._timed_out_ids.pop(message_id, None)
                return
            self._set_busy(False)
            self.user_finished.emit(message)
            return
        request, _ = pending
        content = message["content"]
        try:
            result = content.get("user_expressions", {}).get("calc", {})
            if content.get("status") != "ok" or result.get("status") != "ok":
                error = result if result else content
                raise ValueError(f"{error.get('ename', 'KernelError')}: {error.get('evalue', content.get('status'))}")
            payload = result["data"]["application/json"]
            validate_envelope(payload, self.build)
        except (KeyError, TypeError, ValueError) as exc:
            self.reply_received.emit(request, {"error": str(exc), "transport_error": True})
            if message_id == self._bootstrap_id:
                self._fail(f"Kernel initialization failed: {exc}")
            return
        if message_id == self._bootstrap_id:
            self.ready = True
            self._bootstrap_id = None
            self.ready_changed.emit(True)
            self.status_changed.emit("Ready")
        self.reply_received.emit(request, payload)

    def _handle_publication(self, message):
        # The owned frontend marks busy synchronously when submitting execution.
        # IOPub and shell are independent channels: execute_input may arrive AFTER
        # execute_reply. Treating that late notification as busy deadlocks the UI.
        if message.get("msg_type") == "display_data":
            control = message.get("content", {}).get("data", {}).get(CONTROL_MIME)
            if control:
                try:
                    validate_envelope(control, self.build)
                    if control.get("action") == "clear_console":
                        self.clear_console_requested.emit()
                except ValueError as exc:
                    self.failed.emit(str(exc))

    def note_user_execution(self):
        self._set_busy(True)

    def _set_busy(self, busy):
        if busy != self.busy:
            now = time.monotonic()
            if busy:
                self._busy_started = now
            elif self._busy_started is not None:
                self._busy_total += max(0.0, now - self._busy_started)
                self._busy_started = None
            self.busy = busy
            self.busy_changed.emit(busy)
            self.status_changed.emit("Running…" if busy else "Ready" if self.ready else "Kernel stopped")

    def interrupt(self):
        if self.manager is not None and self.manager.has_kernel:
            try:
                self.manager.interrupt_kernel()
                self.status_changed.emit("Interrupt requested…")
            except Exception as exc:
                self.failed.emit(f"Cannot interrupt kernel: {exc}")

    def _check_health(self):
        if self._closing or self.manager is None:
            return
        now = time.monotonic()
        if now - self._last_health_check > 1:
            self._last_health_check = now
            if not self.manager.is_alive():
                self._fail("Kernel exited. Restart the kernel to continue.")
                return
        if not self.ready and now - self._started > 25:
            self._fail("Kernel startup timed out. Restart the kernel to retry.")
        elif self.ready and not self.busy:
            idle_now = self._idle_clock(now)
            for message_id, (request, sent) in list(self._requests.items()):
                if idle_now - sent > 15:
                    self._requests.pop(message_id, None)
                    if message_id is not None:
                        self._timed_out_ids[message_id] = None
                        while len(self._timed_out_ids) > 256:
                            self._timed_out_ids.popitem(last=False)
                    self.reply_received.emit(request, {"error": "Kernel request timed out", "transport_error": True})

    def _cleanup_resources(self):
        self._timer.stop()
        client, manager = self.client, self.manager
        self.client = None
        self._bootstrap_id = None
        self._requests.clear()
        self._timed_out_ids.clear()
        try:
            if client is not None:
                client.stop_channels()
        finally:
            try:
                if manager is not None and getattr(manager, "has_kernel", False):
                    manager.shutdown_kernel(now=True)
            finally:
                self.manager = None
                if self._log is not None:
                    self._log.close()
                    self._log = None
                self._cleanup_temporary()

    def _fail(self, error):
        self._closing = True
        self.ready = False
        self._set_busy(False)
        self._busy_started = None
        self._busy_total = 0.0
        try:
            self._cleanup_resources()
        except Exception as exc:
            warnings.warn(f"Calculator kernel cleanup failed: {exc}", RuntimeWarning, stacklevel=2)
        self.ready_changed.emit(False)
        self.status_changed.emit(error)
        self.failed.emit(error)

    def shutdown(self):
        self._closing = True
        self.ready = False
        self.ready_changed.emit(False)
        try:
            self._cleanup_resources()
        finally:
            self.busy = False
            self._busy_started = None
            self._busy_total = 0.0

    def _cleanup_temporary(self):
        temporary, self._temporary = self._temporary, None
        if temporary is None:
            return
        # Windows may briefly retain SQLite/antivirus handles after process exit.
        # Keep shutdown bounded, and never turn temporary-file cleanup into a crash.
        for attempt in range(4):
            try:
                temporary.cleanup()
                return
            except PermissionError as exc:
                if attempt == 3:
                    warnings.warn(f"Calculator temporary files could not be removed: {exc}",
                                  RuntimeWarning, stacklevel=2)
                else:
                    time.sleep(0.05 * (attempt + 1))

    def restart(self):
        self.shutdown()
        self.start()
