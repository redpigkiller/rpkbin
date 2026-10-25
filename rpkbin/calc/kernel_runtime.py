"""Kernel-side calculator operations; numeric objects never leave this process."""

from __future__ import annotations

import ast
from dataclasses import asdict

from IPython.display import JSON

from .core.config import CalcConfig
from .core.session import WorkspaceSession
from .core.editing import (EditJournal, conversion_preview, paste_rectangle,
                           selection_grid, selection_statistics)
from .core.fxp_model import update_scalar_bit_field
import apytypes as apy
import numpy as np
from .protocol import (
    CONTROL_MIME, MAX_COPY_CELLS, PAGE_COLS, PAGE_ROWS, PROTOCOL_VERSION, build_id,
    inspector_to_dict, validate_envelope,
)


class CalcKernelRuntime:
    def __init__(self, shell, config: dict) -> None:
        self.shell = shell
        self.build = build_id()
        self.revision = 0
        self.last_assigned = None
        framework_names = set(shell.user_ns)
        self.session = WorkspaceSession(CalcConfig(**config), seed_demo_vars=False, namespace=shell.user_ns)
        self.journal = EditJournal(self.session)
        self.session.hidden_names.update(framework_names)
        shell.user_ns["_rpk_calc_dispatch"] = self.dispatch
        shell.events.register("post_run_cell", self._after_cell)
        shell.register_magic_function(self._clc, "line", "clc")
        shell.register_magic_function(self._clear, "line", "clear")
        shell.register_magic_function(self._whos, "line", "whos")
        shell.register_magic_function(self._whos, "line", "who")

    def _after_cell(self, result) -> None:
        if not result.info.store_history:
            return
        self.journal.clear()
        self.revision += 1
        self.last_assigned = None
        if result.result is not None:
            self.session.user_ns["ans"] = result.result
            self.last_assigned = "ans"
            return
        try:
            tree = ast.parse(result.info.raw_cell)
        except SyntaxError:
            return
        for statement in tree.body:
            targets = statement.targets if isinstance(statement, ast.Assign) else (
                [statement.target] if isinstance(statement, (ast.AnnAssign, ast.AugAssign)) else [])
            for target in targets:
                for node in ast.walk(target):
                    if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                            and node.id in self.session.user_ns):
                        self.last_assigned = node.id

    def _clc(self, argument="") -> None:
        self.shell.display_pub.publish(data={CONTROL_MIME: {
            "protocol": PROTOCOL_VERSION, "build": self.build, "action": "clear_console"}})

    def _clear(self, argument="") -> None:
        if argument.strip() not in ("", "all"):
            raise ValueError("Use clear or clear all to clear the calculator workspace")
        self.clear_workspace()

    def _whos(self, argument="") -> None:
        print(self.session.execute("whos").output_text)

    def clear_workspace(self) -> None:
        # IPython owns its history, output cache and framework symbols. Let it
        # reset those instead of clearing its dictionary behind its back.
        self.shell.reset(new_session=False)
        self.journal.clear()
        self.session.array_slices.clear()
        self.session._native_sources.clear()
        self.session.user_ns = self.shell.user_ns
        self.session.hidden_names.clear()
        self.session._imported_symbols.clear()
        self.session.hidden_names.update(self.shell.user_ns)
        self.session.reload_auto_imports(run_startup=False)
        self.shell.user_ns["_rpk_calc_dispatch"] = self.dispatch
        self.last_assigned = None

    def _state(self, request: dict) -> dict:
        rows = self.session.list_variables()
        names = {row.name for row in rows}
        selected = request.get("selected_var")
        coord = request.get("selected_coord")
        if request.get("follow_user") and self.last_assigned in names:
            selected, coord = self.last_assigned, None
        if selected not in names:
            selected, coord = (rows[0].name if rows else None), None
        inspector = None
        error = None
        if selected:
            try:
                inspector = self.session.get_inspector_snapshot(selected, tuple(coord) if coord else None)
            except (ValueError, TypeError, IndexError, OverflowError) as exc:
                error = f"Cannot inspect {selected}: {exc}"
            coord = inspector.coord if inspector is not None else None
        matrices = []
        for name, position in request.get("open_matrices", {}).items():
            snapshot = self.session.get_matrix_snapshot(name, tuple(position), include_cells=False)
            if snapshot is not None:
                matrices.append(asdict(snapshot))
        return {"workspace": [asdict(row) for row in rows],
                "selected_var": selected, "selected_coord": coord,
                "inspector": inspector_to_dict(inspector), "matrices": matrices,
                "error": error, "undo": self.journal.state(),
                "startup_error": self.session.user_ns.get("_calc_startup_error", ""),
                "import_error": self.session.user_ns.get("_calc_import_error", ""),
                "startup_status": self.session.user_ns.get("_calc_startup_status", {})}

    def _mutate(self, request: dict) -> None:
        actions = {
            "reformat": self.session.reformat_variable,
            "edit": self.session.update_target_field,
            "toggle_bit": self.session.toggle_target_bit,
            "invert_bits": self.session.invert_target_bits,
            "zero_bits": self.session.clear_target_bits,
            "rename": self.session.rename_variable,
            "duplicate": self.session.duplicate_variable,
            "delete": self.session.delete_variable,
            "to_float": self.session.convert_variable_to_float,
            "clear": self.clear_workspace,
            "set_slice": self.session.set_array_slice,
        }
        action = request["action"]
        args, kwargs = request.get("args", []), request.get("kwargs", {})
        result = None
        if action == "reload_startup":
            self.session.reload_auto_imports()
            self.journal.clear()
        elif action in ("undo", "redo"):
            self.journal.replay(redo=action == "redo")
        elif action == "paste":
            paste_rectangle(self.session, self.journal, *args, **kwargs)
        elif action == "bit_field":
            name, coord, high, low, value = args
            def apply_bits():
                if coord is None:
                    self.session.user_ns[name] = update_scalar_bit_field(self.session.user_ns[name], high, low, value)
                else:
                    self.session._mutate_array_cell(name, self.session.user_ns[name], tuple(coord),
                        lambda cell: update_scalar_bit_field(cell, high, low, value))
            self.journal.apply([(name, coord)], apply_bits)
        else:
            if action not in actions:
                raise ValueError("Unknown calculator operation")
            numeric_edit = action in ("edit", "toggle_bit", "invert_bits", "zero_bits")
            scalar_format = (action == "reformat" and not isinstance(self.session.user_ns[args[0]],
                (apy.APyFixedArray, np.ndarray, list, tuple)))
            if numeric_edit or scalar_format:
                self.journal.apply([(args[0], args[1] if numeric_edit else None)],
                                   lambda: actions[action](*args, **kwargs))
            else:
                result = actions[action](*args, **kwargs)
                self.journal.clear()
        if action == "duplicate":
            request["selected_var"], request["selected_coord"] = result, None
        elif action == "rename":
            old, new = request["args"]
            if request.get("selected_var") == old:
                request["selected_var"] = new.strip()
            positions = request.get("open_matrices", {})
            if old in positions:
                positions[new.strip()] = positions.pop(old)
        self.revision += 1

    def dispatch(self, request: dict):
        response = {"protocol": PROTOCOL_VERSION, "build": self.build,
                    "view_id": request.get("view_id", 0)}
        try:
            validate_envelope(request, self.build)
            op = request.get("op", "snapshot")
            if op == "mutate":
                expected = request.get("expected_revision")
                if expected is not None and (type(expected) is not int or expected != self.revision):
                    raise ValueError("Workspace changed; review the conversion and try again")
                self._mutate(request)
            elif op == "config":
                config = CalcConfig(**request["config"])
                symbols_changed = any(getattr(self.session.config, name) != getattr(config, name)
                    for name in ("auto_import_groups", "custom_imports"))
                # Helpers retain this object and read numeric defaults at call time.
                self.session.config.__dict__.update(asdict(config))
                if symbols_changed:
                    self.session.reload_auto_imports(run_startup=False)
                self.revision += 1
            elif op == "page":
                row, col = request["row"], request["col"]
                page = self.session.get_matrix_snapshot(request["name"],
                    window=(row, col, PAGE_ROWS, PAGE_COLS))
                response["page"] = asdict(page) if page else None
            elif op == "copy":
                metadata = self.session.get_matrix_snapshot(request["name"], include_cells=False)
                if metadata is None:
                    raise ValueError("Variable is no longer an array")
                rectangle = request.get("rectangle")
                if rectangle is None and metadata.shape[0] * metadata.shape[1] > MAX_COPY_CELLS:
                    raise ValueError("Copy is limited to 100,000 cells; export larger arrays from Python")
                rectangle = rectangle or (0, 0, *metadata.shape)
                grid = selection_grid(self.session, request["name"], rectangle, request.get("mode", "real"))
                response["clipboard"] = "\n".join("\t".join(row) for row in grid)
            elif op == "preview":
                response["preview"] = conversion_preview(self.session, request["name"], request["format"],
                    request.get("pin", "real"), request.get("coord"))
            elif op == "statistics":
                response["statistics"] = selection_statistics(self.session, request["name"], request["rectangle"])
            elif op != "snapshot":
                raise ValueError("Unknown calculator request")
            if op not in ("page", "copy", "preview", "statistics"):
                response.update(self._state(request))
        except Exception as exc:
            response["error"] = f"{type(exc).__name__}: {exc}"
        response["revision"] = self.revision
        return JSON(response)


def install(config: dict):
    from IPython import get_ipython
    runtime = CalcKernelRuntime(get_ipython(), config)
    return runtime.dispatch({"protocol": PROTOCOL_VERSION, "build": runtime.build,
                             "op": "snapshot", "view_id": 0})
