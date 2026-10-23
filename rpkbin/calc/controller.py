"""Qt view coordination; the IPython kernel owns every numeric value."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .core.config import CalcConfig, DEFAULT_CONFIG_PATH, KERNEL_CONFIG_FIELDS
from .core.startup import prepare_startup
from .kernel_client import KernelTransport
from .protocol import inspector_from_dict, matrix_from_dict, rows_from_dict


class CalcController(QObject):
    workspace_changed = Signal(list)
    inspector_updated = Signal(object)
    matrix_updated = Signal(object)
    matrix_page_received = Signal(object, int)
    matrix_tab_requested = Signal(str)
    matrix_renamed = Signal(str, str)
    config_changed = Signal(object)
    format_error_occurred = Signal(str)
    interaction_enabled = Signal(bool)
    clipboard_ready = Signal(str)
    execute_requested = Signal(str)
    console_executed = Signal(object)
    undo_state_changed = Signal(object)
    preview_ready = Signal(object)
    statistics_ready = Signal(str, object)
    edit_finished = Signal(object, str)
    operation_finished = Signal(object, str)
    startup_state_changed = Signal(object)
    import_state_changed = Signal(str)

    def __init__(self, config_path: Path | str | None = None, parent=None):
        super().__init__(parent)
        self._config_path = config_path
        self.config = CalcConfig.load(config_path)
        self._startup_notice = ""
        try:
            self.startup_store, self._startup_notice = prepare_startup(
                self.config, config_path or DEFAULT_CONFIG_PATH)
        except (OSError, ValueError, UnicodeError) as exc:
            # Keep legacy code on disk if migration failed; a usable console
            # still reports the setup error instead of losing the user script.
            self.startup_store = None
            self._startup_notice = f"Startup file could not be prepared: {exc}"
            self.config.startup_file = str(Path(config_path or DEFAULT_CONFIG_PATH).resolve().with_name("startup.py"))
        self.startup_state = {}
        self.import_error = ""
        self._kernel_config = self._kernel_preferences()
        self.selected_var = None
        self.selected_coord = None
        self.open_matrix_coords = {}
        self.rows = []
        self.undo_state = {"can_undo": False, "can_redo": False}
        self.revision = -1
        self._view_id = 0
        self._request_id = 0
        self._pending = None
        self._dirty = True
        self._follow_user = False
        self._out_of_sync = False
        self._mutations = deque()
        self._pages = {}
        self.transport = KernelTransport(self.config, self)
        self.transport.reply_received.connect(self._on_reply)
        self.transport.user_finished.connect(self._on_execution_finished)
        self.transport.busy_changed.connect(self._on_busy)
        self.transport.ready_changed.connect(self._on_ready)
        self.transport.failed.connect(self.format_error_occurred)

    @property
    def is_available(self):
        return self.transport.ready and not self.transport.busy and not self._out_of_sync

    def start(self):
        self.transport.start()
        if self._startup_notice:
            QTimer.singleShot(0, lambda: self.format_error_occurred.emit(self._startup_notice))

    def _on_ready(self, ready):
        self.interaction_enabled.emit(False)
        if ready:
            self._schedule()

    def _on_busy(self, busy):
        if busy:
            self.interaction_enabled.emit(False)
        else:
            self._schedule()

    def _schedule(self):
        QTimer.singleShot(0, self._pump)

    def refresh_all(self):
        self._dirty = True
        self._schedule()

    def _view_request(self):
        return {"view_id": self._view_id, "selected_var": self.selected_var,
                "selected_coord": self.selected_coord,
                "open_matrices": dict(self.open_matrix_coords)}

    def _pump(self):
        if not self.is_available or self._pending is not None:
            return
        if self._mutations:
            request = dict(self._mutations.popleft(), **self._view_request())
        elif self._dirty:
            request = dict(self._view_request(), op="snapshot", follow_user=self._follow_user)
            self._dirty = False
            self._follow_user = False
        elif self._pages:
            _, request = self._pages.popitem()
        else:
            self.interaction_enabled.emit(True)
            return
        self._request_id += 1
        request["request_id"] = self._request_id
        self._pending = request
        try:
            self.transport.request(request)
        except RuntimeError as exc:
            self._pending = None
            self.format_error_occurred.emit(str(exc))

    def _on_reply(self, request, payload):
        if request.get("op") != "bootstrap":
            if self._pending is None or request.get("request_id") != self._pending.get("request_id"):
                return
            self._pending = None
        if payload.get("transport_error"):
            self._out_of_sync = True
            self.interaction_enabled.emit(False)
            self.format_error_occurred.emit(payload["error"] + "; restart the kernel to recover")
            return
        # Setup diagnostics remain visible, but do not turn successful numeric
        # edits or renames into failed operation acknowledgements.
        error = payload.get("error") or ""
        notice = error or payload.get("startup_error", "") or payload.get("import_error", "")
        if "import_error" in payload:
            self.import_error = payload["import_error"]
            self.import_state_changed.emit(self.import_error)
        if "startup_status" in payload:
            self.startup_state = {**payload["startup_status"], "error": payload.get("startup_error", "")}
            self.startup_state_changed.emit(self.startup_state)
        self.format_error_occurred.emit(notice)
        if request.get("op") == "mutate" and request.get("action") == "edit":
            self.edit_finished.emit(request["args"], error)
        if request.get("op") == "mutate":
            self.operation_finished.emit(request, error)
        if "undo" in payload:
            self.undo_state = payload["undo"]
            self.undo_state_changed.emit(self.undo_state)
        if "preview" in payload and request.get("view_id") == self._view_id:
            self.preview_ready.emit(payload["preview"])
        if "statistics" in payload:
            self.statistics_ready.emit(request["name"], payload["statistics"])
        if "clipboard" in payload:
            self.clipboard_ready.emit(payload["clipboard"])
        revision = payload.get("revision", self.revision)
        if "page" in payload and payload["page"] is not None and revision == self.revision:
            self.matrix_page_received.emit(matrix_from_dict(payload["page"]), revision)
        if request.get("op") == "mutate" and not error and request.get("action") == "rename":
            old, new = request["args"]
            new = new.strip()
            if old in self.open_matrix_coords:
                self.open_matrix_coords[new] = self.open_matrix_coords.pop(old)
                self.matrix_renamed.emit(old, new)
        if "workspace" in payload and revision >= self.revision:
            self.revision = revision
            self.rows = rows_from_dict(payload["workspace"])
            if payload.get("view_id", 0) == self._view_id:
                self.selected_var = payload["selected_var"]
                position = payload["selected_coord"]
                self.selected_coord = tuple(position) if position is not None else None
                self.inspector_updated.emit(inspector_from_dict(payload["inspector"]))
                for matrix in payload.get("matrices", []):
                    snapshot = matrix_from_dict(matrix)
                    if snapshot.name in self.open_matrix_coords:
                        self.matrix_updated.emit(snapshot)
            else:
                self._dirty = True
            existing = {row.name for row in self.rows if row.is_array}
            for name in list(self.open_matrix_coords):
                if name not in existing:
                    self.open_matrix_coords.pop(name, None)
            self.workspace_changed.emit(self.rows)
        elif error and request.get("op") == "mutate":
            self._dirty = True
        self._schedule()

    def _on_execution_finished(self, message):
        self._view_id += 1
        self._follow_user = True
        self._dirty = True
        self._pages.clear()
        self.console_executed.emit(message)
        self._schedule()

    def execute_command(self, code_str):
        if self.is_available:
            self.execute_requested.emit(code_str)
        else:
            self.format_error_occurred.emit("Wait for the kernel to become ready")

    def select_variable(self, name, coord=None):
        if name not in {row.name for row in self.rows}:
            return
        self.selected_var = name
        self.selected_coord = coord if coord is not None else self.open_matrix_coords.get(name)
        if coord is not None and name in self.open_matrix_coords:
            self.open_matrix_coords[name] = coord
        self._view_id += 1
        self.interaction_enabled.emit(False)
        self.refresh_all()

    def open_matrix_editor(self, name):
        if not any(row.name == name and row.is_array for row in self.rows):
            return
        self.open_matrix_coords.setdefault(name, (0, 0))
        self.matrix_tab_requested.emit(name)
        self.select_variable(name, self.open_matrix_coords[name])

    def close_matrix_editor(self, name):
        self.open_matrix_coords.pop(name, None)
        self._pages = {key: value for key, value in self._pages.items() if key[0] != name}

    def request_matrix_page(self, name, row, col):
        if name not in self.open_matrix_coords:
            return
        key = (name, row, col)
        self._pages[key] = {"op": "page", "name": name, "row": row, "col": col,
                            "view_id": self._view_id}
        self._schedule()

    def _mutate(self, action, *args, **kwargs):
        if not self.is_available or len(self._mutations) >= 32:
            self.format_error_occurred.emit("Kernel is busy; wait before editing")
            return False
        self.interaction_enabled.emit(False)
        self._mutations.append({"op": "mutate", "action": action,
                                 "args": args, "kwargs": kwargs})
        self._schedule()
        return True

    def change_variable_format(self, name, new_fmt_str):
        return self._mutate("reformat", name, new_fmt_str, pin_mode=self.config.inspector_pin_mode)

    def convert_to_fxp(self, name, new_fmt_str):
        """Explicit numeric conversion, independent of the last FXP pin selection."""
        return self._mutate("reformat", name, new_fmt_str, pin_mode="real")

    def edit_target_field(self, name, coord, field, text_val):
        return self._mutate("edit", name, coord, field, text_val)

    def toggle_target_bit(self, bit_index):
        if self.selected_var:
            self._mutate("toggle_bit", self.selected_var, self.selected_coord, bit_index)

    def invert_target_bits(self):
        if self.selected_var:
            self._mutate("invert_bits", self.selected_var, self.selected_coord)

    def clear_target_bits(self):
        if self.selected_var:
            self._mutate("zero_bits", self.selected_var, self.selected_coord)

    def rename_variable(self, old_name, new_name):
        return self._mutate("rename", old_name, new_name)

    def duplicate_variable(self, name):
        return self._mutate("duplicate", name)

    def delete_variable(self, name):
        return self._mutate("delete", name)

    def convert_to_float(self, name):
        return self._mutate("to_float", name)

    def clear_workspace(self):
        return self._mutate("clear")

    def reload_startup(self):
        return self._mutate("reload_startup")

    def undo_edit(self):
        return self._mutate("undo")

    def redo_edit(self):
        return self._mutate("redo")

    def set_array_slice(self, name, indices):
        return self._mutate("set_slice", name, list(indices))

    def paste_matrix(self, name, row, col, text, mode="real"):
        return self._mutate("paste", name, row, col, text, mode)

    def set_bit_field(self, high, low, value):
        if self.selected_var:
            return self._mutate("bit_field", self.selected_var, self.selected_coord, high, low, value)
        return False

    def request_preview(self, name, fmt):
        return self._queue_read({"op": "preview", "name": name, "format": fmt,
            "pin": self.config.inspector_pin_mode, "coord": self.selected_coord})

    def request_statistics(self, name, rectangle):
        return self._queue_read({"op": "statistics", "name": name, "rectangle": list(rectangle)})

    def _queue_read(self, request):
        if not self.is_available or len(self._mutations) >= 32:
            self.format_error_occurred.emit("Kernel is busy; wait before requesting data")
            return False
        self._mutations.append(request)
        self._schedule()
        return True

    def copy_matrix(self, name, mode, rectangle=None):
        if self.is_available:
            return self._queue_read({"op": "copy", "name": name, "mode": mode, "rectangle": rectangle})

    def _save_config(self):
        try:
            self.config.save(self._config_path)
        except OSError as exc:
            self.format_error_occurred.emit(f"Preferences could not be saved: {exc}")
        self.config_changed.emit(self.config)

    def set_pin_mode(self, mode):
        if mode in ("real", "hex"):
            self.config.inspector_pin_mode = mode
            self._save_config()

    def set_inspector_view_mode(self, mode):
        if mode in ("list", "grid"):
            self.config.inspector_view_mode = mode
            self._save_config()

    def toggle_bit_order(self):
        self.config.bit_order = "lsb_first" if self.config.bit_order == "msb_first" else "msb_first"
        self._save_config()
        self.refresh_all()

    def set_visible_columns(self, columns):
        self.config.visible_columns = list(columns)
        self.update_config(self.config)

    def update_config(self, config):
        self.config = config
        self.transport.config = config
        self._save_config()
        kernel_config = self._kernel_preferences()
        if kernel_config != self._kernel_config:
            self._kernel_config = kernel_config
            self._mutations.append({"op": "config", "config": asdict(config)})
            self._schedule()

    def _kernel_preferences(self):
        values = asdict(self.config)
        return {name: values[name] for name in KERNEL_CONFIG_FIELDS}

    def restart_kernel(self):
        self._pending = None
        self._mutations.clear()
        self._pages.clear()
        self._view_id += 1
        self._dirty = True
        self._out_of_sync = False
        self.rows = []
        self.undo_state = {"can_undo": False, "can_redo": False}
        self.undo_state_changed.emit(self.undo_state)
        self.selected_var = self.selected_coord = None
        self.open_matrix_coords.clear()
        self.workspace_changed.emit([])
        self.inspector_updated.emit(None)
        self.transport.restart()

    def shutdown(self):
        self._mutations.clear()
        self._pages.clear()
        self.transport.shutdown()
