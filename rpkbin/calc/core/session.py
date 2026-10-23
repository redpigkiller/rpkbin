"""Workspace session manager: REPL execution, variable tracking, and immutable UI snapshots."""

from __future__ import annotations

import ast
import copy
import io
import keyword
import math
import traceback
import types
from collections import OrderedDict
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

import apytypes as apy
import numpy as np

from .config import CalcConfig
from .format_engine import FormatEngine, FxpSpec
from .fxp_model import (
    InspectorSnapshot,
    PlainValueSnapshot,
    build_inspector_snapshot,
    c1_from_raw_uint,
    clear_scalar_bits,
    extract_spec,
    format_hex,
    format_real_number,
    invert_scalar_bits,
    make_fxp_from_raw_uint,
    raw_uint_from_apy_scalar,
    reformat_value,
    s2_from_raw_uint,
    toggle_scalar_bit,
    update_scalar_from_field,
)
from .math_helpers import apply_startup, build_namespace_symbols
from .representations import MAX_INTEGER_DISPLAY_BITS, format_apy_float, numeric_representation, toggle_float_bit


# Workspace refreshes must not scan arbitrarily large FXP arrays for diagnostics.
MAX_SATURATION_CHECK_CELLS = 4096


@dataclass(frozen=True)
class WorkspaceRowSnapshot:
    """Immutable row representation of a user variable in the Workspace table."""

    name: str
    value_str: str
    format_str: str
    hex_str: str
    s2_str: str
    uint_str: str
    c1_str: str
    bin_str: str
    wl_info_str: str
    lsb_range_str: str
    shape_str: str
    minmax_str: str
    is_array: bool
    is_fxp: bool
    is_saturated: bool | None  # None: whole-array conversion provenance is not verified.

    def get_column(self, col_id: str) -> str:
        mapping = {
            "name": self.name,
            "value": self.value_str,
            "format": self.format_str,
            "hex": self.hex_str,
            "s2": self.s2_str,
            "uint": self.uint_str,
            "c1": self.c1_str,
            "bin": self.bin_str,
            "wl_info": self.wl_info_str,
            "lsb_range": self.lsb_range_str,
            "shape": self.shape_str,
            "minmax": self.minmax_str,
        }
        return mapping.get(col_id, "")


@dataclass(frozen=True)
class MatrixSnapshot:
    """Immutable snapshot of a 1D/2D array for the Variable Editor tab."""

    name: str
    shape: tuple[int, int]
    is_1d: bool
    format_str: str
    is_fxp: bool
    spec: FxpSpec | None
    cells_real: list[list[str]]
    cells_hex: list[list[str]]
    cells_s2: list[list[str]]
    cells_uint: list[list[str]]
    selected_row: int = 0
    selected_col: int = 0
    editable: bool = True
    row_offset: int = 0
    col_offset: int = 0
    slice_note: str = ""
    full_shape: tuple[int, ...] = ()
    slice_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class ExecutionResult:
    """Result of executing a command or multi-line block in the Command Window."""

    output_text: str
    error_text: str
    clear_console: bool = False
    assigned_vars: tuple[str, ...] = ()


class WorkspaceSession:
    """Manages the Python execution namespace, FXP conversions, and UI state snapshots."""

    def __init__(
        self,
        config: CalcConfig | None = None,
        *,
        seed_demo_vars: bool = True,
        namespace: dict | None = None,
    ) -> None:
        self.config: CalcConfig = config or CalcConfig.load()
        if config is None:
            from .config import DEFAULT_CONFIG_PATH
            from .startup import prepare_startup
            prepare_startup(self.config, DEFAULT_CONFIG_PATH)
        self.user_ns: dict[str, Any] = namespace if namespace is not None else {}
        self.hidden_names: set[str] = set()
        self._imported_symbols: dict[str, Any] = {}
        self._startup_symbols = {}
        self.array_slices: dict[str, tuple[int, ...]] = {}
        # Native extension arrays cannot hold Python attributes or weakrefs.
        # Keep bounded GUI provenance here, without replacing native owners.
        self._native_sources = OrderedDict()
        if namespace is None:
            self.reset(seed_demo_vars=seed_demo_vars)
        else:
            self.reload_auto_imports()

    def reset(self, *, seed_demo_vars: bool = False) -> None:
        """Clear workspace variables and reload auto-imported symbols."""
        self.user_ns.clear()
        self.array_slices.clear()
        self._native_sources.clear()
        self.hidden_names.clear()
        self._imported_symbols.clear()
        self.reload_auto_imports(run_startup=not bool(self._startup_symbols))
        if seed_demo_vars:
            self.execute(
                'a = APyFixed.from_float(-0.03, bits=16, frac_bits=12)\n'
                "b = a + 2\n"
                'A = APyFixedArray.from_float([[0.25, -0.125, 0.05], [-0.03, 0.1, -0.2]], bits=16, frac_bits=12)'
            )

    def reload_auto_imports(self, *, run_startup=True) -> None:
        """Re-inject configured auto-import functions without wiping user variables."""
        # Remove old auto-imported symbols first
        for k, previous in self._imported_symbols.items():
            if self.user_ns.get(k) is previous:
                del self.user_ns[k]
            self.hidden_names.discard(k)
        self._imported_symbols.clear()

        base = build_namespace_symbols(self.config, run_startup=False)
        symbols = base if run_startup else {**base, **self._startup_symbols}
        for k, v in symbols.items():
            if not k.startswith("__") and k not in self.user_ns:
                self.user_ns[k] = v
                self.hidden_names.add(k)
                self._imported_symbols[k] = v
        if run_startup:
            before = dict(self.user_ns)
            self.user_ns.pop("_calc_startup_error", None)
            apply_startup(self.config, self.user_ns)
            self._startup_symbols = {key: value for key, value in self.user_ns.items()
                                     if key not in before or value is not before[key]}
            for key, value in self._startup_symbols.items():
                if not key.startswith("__"):
                    self.hidden_names.add(key)
                    self._imported_symbols[key] = value

    def is_user_variable(self, name: str, val: Any) -> bool:
        """Return True if `name` should be displayed in the Workspace table."""
        if name.startswith("_") or (name in self.hidden_names and
                (name not in self._imported_symbols or val is self._imported_symbols[name])):
            return False
        if isinstance(val, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, type)):
            return False
        return True

    def user_variable_names(self) -> list[str]:
        return [
            k for k, v in self.user_ns.items() if self.is_user_variable(k, v)
        ]

    def format_value_for_console(self, val: Any) -> str:
        """Format a return value nicely for the Command Window output."""
        if isinstance(val, apy.APyFixed):
            spec = extract_spec(val)
            fmt_s = FormatEngine.format(spec, self.config)
            u = raw_uint_from_apy_scalar(val, spec)
            hex_s = format_hex(u, spec.wl)
            s2_v = s2_from_raw_uint(u, spec.wl)
            real_s = str(val)
            return f"{real_s}  [{fmt_s} | Hex: {hex_s} | 2's: {s2_v}]"
        if isinstance(val, apy.APyFixedArray):
            spec = extract_spec(val)
            fmt_s = FormatEngine.format(spec, self.config)
            arr_np = np.asarray(val.to_numpy(), dtype=float)
            arr_s = np.array2string(arr_np, precision=6, suppress_small=True, separator=", ")
            return f"{arr_s}\n  [Array shape={val.shape}, Format={fmt_s}]"
        if isinstance(val, np.ndarray):
            return np.array2string(val, precision=6, suppress_small=True, separator=", ")
        if isinstance(val, float):
            return format_real_number(val)
        return repr(val)

    def execute(self, code_str: str) -> ExecutionResult:
        """Execute one or more Python statements in the workspace namespace.

        Supports:
        - `clc`: clear console output
        - `clear`: clear all user variables
        - `whos`: print workspace summary
        Assignments use Python semantics: rebinding a former FXP variable never
        implicitly quantizes the new value. Construct APyTypes values explicitly.
        """
        stripped = code_str.strip()
        if not stripped:
            return ExecutionResult(output_text="", error_text="")

        if stripped == "clc":
            return ExecutionResult(output_text="", error_text="", clear_console=True)
        if stripped in ("clear", "clear all"):
            self.reset(seed_demo_vars=False)
            return ExecutionResult(
                output_text="[Workspace cleared]", error_text=""
            )
        if stripped in ("whos", "who"):
            rows = self.list_variables()
            if not rows:
                return ExecutionResult(output_text="(Workspace is empty)", error_text="")
            lines = [f"{'Name':<12} {'Shape':<10} {'Format':<14} {'Value':<20} {'Hex'}"]
            lines.append("-" * 68)
            for r in rows:
                lines.append(
                    f"{r.name:<12} {r.shape_str:<10} {r.format_str:<14} {r.value_str:<20} {r.hex_str}"
                )
            return ExecutionResult(output_text="\n".join(lines), error_text="")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        out_lines: list[str] = []
        assigned: list[str] = []

        try:
            tree = ast.parse(code_str, mode="exec")
        except SyntaxError:
            return ExecutionResult(
                output_text="",
                error_text=traceback.format_exc(limit=1).strip(),
            )

        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                for stmt in tree.body:
                    # Handle expression statement -> evaluate, store in `ans`, and print
                    if isinstance(stmt, ast.Expr):
                        expr_code = compile(
                            ast.Expression(body=stmt.value), "<command>", "eval"
                        )
                        val = eval(expr_code, self.user_ns)
                        if val is not None:
                            self.user_ns["ans"] = val
                            assigned.append("ans")
                            out_lines.append(f"ans = {self.format_value_for_console(val)}")
                        continue

                    mod = ast.Module(body=[stmt], type_ignores=[])
                    stmt_code = compile(mod, "<command>", "exec")
                    exec(stmt_code, self.user_ns)
                    # Report top-level binding targets, including unpacking and
                    # chained assignment, without changing Python execution.
                    targets = []
                    if isinstance(stmt, ast.Assign):
                        targets = stmt.targets
                    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
                        targets = [stmt.target]
                    for target_name in dict.fromkeys(
                        node.id for target in targets for node in ast.walk(target)
                        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                    ):
                        new_val = self.user_ns[target_name]
                        self.hidden_names.discard(target_name)
                        assigned.append(target_name)
                        out_lines.append(
                            f"{target_name} = {self.format_value_for_console(new_val)}"
                        )
        except Exception:
            printed = stdout_buf.getvalue().rstrip()
            if printed:
                out_lines.insert(0, printed)
            return ExecutionResult(
                output_text="\n".join(out_lines),
                error_text="\n".join(filter(None, (
                    stderr_buf.getvalue().rstrip(), traceback.format_exc(limit=2).strip()
                ))),
                assigned_vars=tuple(assigned),
            )

        printed = stdout_buf.getvalue().rstrip()
        if printed:
            out_lines.insert(0, printed)
        err_printed = stderr_buf.getvalue().rstrip()

        return ExecutionResult(
            output_text="\n".join(out_lines),
            error_text=err_printed,
            assigned_vars=tuple(assigned),
        )

    def list_variables(self) -> list[WorkspaceRowSnapshot]:
        """Return immutable snapshots for all user variables in the Workspace."""
        live = {id(value) for value in self.user_ns.values()}
        for identity in list(self._native_sources):
            if identity not in live:
                del self._native_sources[identity]
        rows: list[WorkspaceRowSnapshot] = []
        for name, val in self.user_ns.items():
            if not self.is_user_variable(name, val):
                continue
            rows.append(self._build_row_snapshot(name, val))
        return rows

    def _native_source_map(self, obj, *, create=False):
        identity = id(obj)
        saved = self._native_sources.get(identity)
        structure = (tuple(obj.shape), extract_spec(obj))
        if saved is not None and (saved[0] is not obj or saved[1] != structure):
            del self._native_sources[identity]
            saved = None
        if saved is None:
            if not create:
                return {}
            saved = (obj, structure, {})
            self._native_sources[identity] = saved
        if create:
            self._native_sources.move_to_end(identity)
        return saved[2]

    def _array_saturation_status(self, val: apy.APyFixedArray) -> bool | None:
        """Verify current conversion records within a fixed cell budget.

        True requires a currently matching saturated source. False requires all
        cells to have matching, nonsaturated conversion records. Cached flags
        alone cannot establish either state after arbitrary REPL writes.
        """
        size = math.prod(val.shape)
        if size == 0:
            return False
        if size > MAX_SATURATION_CHECK_CELLS:
            return None
        baseline = getattr(val, "quantized_bits", None)
        saturated = getattr(val, "saturated_mask", None)
        targets = getattr(val, "target_values", None)
        if (baseline is not None and saturated is not None and targets is not None
                and baseline.shape == saturated.shape == targets.shape == val.shape):
            current = np.asarray(val.to_bits(), dtype=object)
            known = (current == baseline) & np.fromiter(
                (target is not None for target in targets.flat), dtype=bool, count=size
            ).reshape(val.shape)
            if np.any(known & saturated):
                return True
            return False if np.all(known) else None

        sources = getattr(val, "cell_sources", None) or self._native_source_map(val)
        known_count = 0
        for index, (raw, target, saturated) in sources.items():
            if target is not None and int(val[index].to_bits()) == raw:
                if saturated:
                    return True
                known_count += 1
        return False if known_count == size else None

    def _build_row_snapshot(self, name: str, val: Any) -> WorkspaceRowSnapshot:
        # Case 1: FXP Scalar
        if isinstance(val, apy.APyFixed):
            spec = extract_spec(val)
            fmt_str = FormatEngine.format(spec, self.config)
            u = raw_uint_from_apy_scalar(val, spec)
            s2 = s2_from_raw_uint(u, spec.wl)
            c1 = c1_from_raw_uint(u, spec.wl)
            real_s = str(val)
            return WorkspaceRowSnapshot(
                name=name,
                value_str=real_s,
                format_str=fmt_str,
                hex_str=format_hex(u, spec.wl),
                s2_str=str(s2),
                uint_str=str(u),
                c1_str=str(c1),
                bin_str=f"0b{u:0{spec.wl}b}",
                wl_info_str=f"{spec.wl} ({spec.iwl} / {spec.fwl})",
                lsb_range_str=f"2^-{spec.fwl} [{format_real_number(spec.min_real)}..{format_real_number(spec.max_real)}]",
                shape_str="1x1",
                minmax_str=real_s,
                is_array=False,
                is_fxp=True,
                is_saturated=bool(getattr(val, "is_saturated", False)),
            )

        # Case 2: FXP Array / Matrix
        if isinstance(val, apy.APyFixedArray):
            spec = extract_spec(val)
            fmt_str = FormatEngine.format(spec, self.config)
            shape_s = "x".join(str(d) for d in val.shape)
            size = math.prod(val.shape)
            preview = [str(val[tuple(int(x) for x in np.unravel_index(i, val.shape))])
                       for i in range(min(3, size))]
            val_s = f"[{', '.join(preview)}{', ...' if size > 3 else ''}]"
            min_v = max_v = "—"
            if "minmax" in self.config.visible_columns and size:
                values = val.to_numpy()
                min_v = format_real_number(float(np.min(values)))
                max_v = format_real_number(float(np.max(values)))
            has_saturation = self._array_saturation_status(val)
            return WorkspaceRowSnapshot(
                name=name,
                value_str=val_s,
                format_str=f"{fmt_str} [{shape_s}]",
                hex_str=f"[{shape_s} Hex]",
                s2_str=f"[{shape_s} Int]",
                uint_str=f"[{shape_s} UInt]",
                c1_str="—",
                bin_str="—",
                wl_info_str=f"{spec.wl} ({spec.iwl} / {spec.fwl})",
                lsb_range_str=f"2^-{spec.fwl} [{format_real_number(spec.min_real)}..{format_real_number(spec.max_real)}]",
                shape_str=shape_s,
                minmax_str=f"{min_v} .. {max_v}",
                is_array=True,
                is_fxp=True,
                is_saturated=has_saturation,
            )

        # Case 3: NumPy Array or Python List of numbers
        if isinstance(val, (np.ndarray, list, tuple)):
            try:
                np_arr = np.asarray(val)
                shape_s = "x".join(str(d) for d in np_arr.shape) if np_arr.ndim > 0 else "1x1"
                if np_arr.size <= 4 and np.issubdtype(np_arr.dtype, np.number):
                    val_s = np.array2string(np_arr, precision=4, separator=", ").replace("\n", " ")
                elif np.issubdtype(np_arr.dtype, np.number) and np_arr.size > 0:
                    flat = [self.format_python_scalar(x) for x in np_arr.flat[:3]]
                    val_s = f"[{', '.join(flat)}, ...]"
                else:
                    val_s = repr(val)[:32]
                if ("minmax" in self.config.visible_columns and
                        np_arr.dtype.kind in "iuf" and np_arr.size > 0):
                    min_v = self.format_python_scalar(np.min(np_arr))
                    max_v = self.format_python_scalar(np.max(np_arr))
                    mm_s = f"{min_v} .. {max_v}"
                else:
                    mm_s = "—"
                return WorkspaceRowSnapshot(
                    name=name,
                    value_str=val_s,
                    format_str=f"{np_arr.dtype} [{shape_s}]",
                    hex_str="—",
                    s2_str="—",
                    uint_str="—",
                    c1_str="—",
                    bin_str="—",
                    wl_info_str="—",
                    lsb_range_str="—",
                    shape_str=shape_s,
                    minmax_str=mm_s,
                    is_array=True,
                    is_fxp=False,
                    is_saturated=False,
                )
            except Exception:
                pass

        # Native values keep their real Python/NumPy type, without invented FXP bits.
        return WorkspaceRowSnapshot(
            name=name,
            value_str=self.format_python_scalar(val)[:512],
            format_str=str(val.dtype) if isinstance(val, np.generic) else type(val).__name__,
            hex_str="—",
            s2_str="—",
            uint_str="—",
            c1_str="—",
            bin_str="—",
            wl_info_str="—",
            lsb_range_str="—",
            shape_str="1x1",
            minmax_str="—",
            is_array=False,
            is_fxp=False,
            is_saturated=False,
        )

    def get_inspector_snapshot(
        self,
        name: str,
        coord: tuple[int, int] | None = None,
    ) -> InspectorSnapshot | PlainValueSnapshot | None:
        """Build an InspectorSnapshot for a variable or a specific (row, col) cell of an array."""
        if name not in self.user_ns:
            return None
        obj = self.user_ns[name]

        if isinstance(obj, (apy.APyFixedArray, np.ndarray, list, tuple)):
            r, c = coord if coord is not None else (0, 0)
            parent_spec = extract_spec(obj) if isinstance(obj, apy.APyFixedArray) else None
            try:
                scalar_elem, actual_coord = self._extract_array_cell(obj, r, c, name=name)
            except (IndexError, ValueError, TypeError):
                return self._plain_snapshot(name, obj, None, editable=False)
            snapshot = self._inspect_scalar(name, scalar_elem, actual_coord, parent_spec,
                editable=(not isinstance(obj, tuple) and
                          (not isinstance(obj, np.ndarray) or
                           (obj.flags.writeable and obj.dtype.kind in "biufcUS"))))
            index = self.cell_index(name, actual_coord)
            if len(index) != 2:
                position = ', '.join(map(str, index)) if index else '()'
                snapshot = replace(snapshot, display_title=f"{name}[{position}]")
            return snapshot

        return self._inspect_scalar(name, obj, None)

    @staticmethod
    def format_python_scalar(value: Any) -> str:
        if isinstance(value, apy.APyFloat):
            return format_apy_float(value)
        if type(value) is int and value.bit_length() > MAX_INTEGER_DISPLAY_BITS:
            return f"<{'negative ' if value < 0 else ''}int: {value.bit_length()} magnitude bits>"
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float):
            return repr(value)
        return str(value) if isinstance(value, (int, complex)) else repr(value)

    def _plain_snapshot(self, name, value, coord, *, editable=True):
        return PlainValueSnapshot(
            var_name=name, coord=coord,
            display_title=f"{name}[{coord[0]}, {coord[1]}]" if coord is not None else name,
            value_str=self.format_python_scalar(value), type_name=type(value).__name__,
            editable=editable and isinstance(value, (str, bool, int, float, complex, np.number)),
            native_bits=numeric_representation(value, editable_float=coord is None),
        )

    def _inspect_scalar(self, name, value, coord, parent_spec=None, *, editable=True):
        if not editable:
            return self._plain_snapshot(name, value, coord, editable=False)
        if isinstance(value, apy.APyFixed):
            return build_inspector_snapshot(name, value, self.config,
                                            coord=coord, parent_spec=parent_spec)
        return self._plain_snapshot(name, value, coord, editable=editable)

    def _extract_array_cell(
        self, obj: Any, row: int, col: int, *, name: str | None = None
    ) -> tuple[Any, tuple[int, int]]:
        if isinstance(obj, apy.APyFixedArray):
            if not math.prod(obj.shape):
                raise ValueError("Empty arrays have no selected element")
            if obj.ndim == 1:
                r = 0
                c = max(0, min(int(col), obj.shape[0] - 1))
                idx = (c,)
            else:
                r = max(0, min(int(row), obj.shape[0] - 1))
                c = max(0, min(int(col), obj.shape[1] - 1))
                idx = (r, c) + self._slice_for(name, obj.shape)
            spec = extract_spec(obj)
            raw = int(obj[idx].to_bits()) & ((1 << spec.wl) - 1)
            elem = make_fxp_from_raw_uint(raw, spec)
            source = (getattr(obj, "cell_sources", None) or self._native_source_map(obj)).get(idx)
            baseline = getattr(obj, "quantized_bits", None)
            if source is not None:
                if source[0] == raw:
                    elem = make_fxp_from_raw_uint(raw, spec, target_real=source[1], is_saturated=source[2])
            elif baseline is not None and baseline.shape == obj.shape and int(baseline[idx]) == raw:
                elem = make_fxp_from_raw_uint(raw, spec, target_real=obj.target_values[idx],
                                              is_saturated=bool(obj.saturated_mask[idx]))
            return elem, (r, c)

        np_arr = obj if isinstance(obj, np.ndarray) else np.asarray(obj, dtype=object)
        if not np_arr.size:
            raise ValueError("Empty arrays have no selected element")
        if np_arr.ndim == 0:
            return np_arr[()], (0, 0)
        if np_arr.ndim == 1:
            r = 0
            c = max(0, min(int(col), np_arr.shape[0] - 1))
            return np_arr[c], (r, c)
        r = max(0, min(int(row), np_arr.shape[0] - 1))
        c = max(0, min(int(col), np_arr.shape[1] - 1))
        idx = (r, c) + self._slice_for(name, np_arr.shape)
        return np_arr[idx], (r, c)

    def _slice_for(self, name, shape):
        limits = tuple(shape[2:])
        saved = self.array_slices.get(name, ())
        if len(saved) != len(limits) or any(i < 0 or i >= n for i, n in zip(saved, limits)):
            saved = (0,) * len(limits)
        return saved

    def set_array_slice(self, name, indices):
        obj = self.user_ns[name]
        shape = obj.shape if isinstance(obj, (np.ndarray, apy.APyFixedArray)) else np.asarray(obj).shape
        indices = tuple(indices)
        if (len(indices) != max(0, len(shape) - 2) or
                any(type(i) is not int or not 0 <= i < n for i, n in zip(indices, shape[2:]))):
            raise ValueError(f"Enter one valid zero-based index for each trailing dimension: {shape[2:]}")
        self.array_slices[name] = indices

    def cell_index(self, name, coord):
        obj = self.user_ns[name]
        shape = obj.shape if isinstance(obj, (np.ndarray, apy.APyFixedArray)) else np.asarray(obj).shape
        r, c = coord
        if (type(r) is not int or type(c) is not int or r < 0 or c < 0 or
                r >= (shape[0] if len(shape) > 1 else 1) or
                c >= (shape[1] if len(shape) > 1 else shape[0] if shape else 1)):
            raise IndexError("Cell is outside the current array view")
        return () if not shape else (c,) if len(shape) == 1 else (r, c) + self._slice_for(name, shape)

    def get_matrix_snapshot(
        self,
        name: str,
        selected_coord: tuple[int, int] = (0, 0),
        *,
        window: tuple[int, int, int, int] | None = None,
        include_cells: bool = True,
    ) -> MatrixSnapshot | None:
        """Describe an array and optionally format only a rectangular page.

        ``shape`` always describes the full 2D view; cell lists start at the
        returned offsets. Higher-dimensional arrays use the selected trailing
        indices, defaulting to ``[:, :, 0, ...]`` before a slice is selected.
        Metadata-only requests do no element conversion or full-array scans.
        """
        obj = self.user_ns.get(name)
        if not isinstance(obj, (apy.APyFixedArray, np.ndarray, list, tuple)):
            return None
        is_fxp = isinstance(obj, apy.APyFixedArray)
        try:
            array = obj if is_fxp else np.asarray(obj)
        except (ValueError, TypeError):
            return None
        spec = extract_spec(obj) if is_fxp else None
        fmt_str = FormatEngine.format(spec, self.config) if is_fxp else str(array.dtype)
        ndim = array.ndim
        shape = ((1, 1) if ndim == 0 else (1, array.shape[0]) if ndim == 1
                 else (array.shape[0], array.shape[1]))
        rows_n, cols_n = shape
        sel_r = max(0, min(selected_coord[0], rows_n - 1))
        sel_c = max(0, min(selected_coord[1], cols_n - 1))
        row0, col0, height, width = window or (0, 0, rows_n, cols_n)
        row0, col0 = max(0, row0), max(0, col0)
        height, width = max(0, height), max(0, width)
        cells_real, cells_hex, cells_s2, cells_uint = [], [], [], []
        if include_cells:
            for r in range(row0, min(rows_n, row0 + height)):
                real_row, hex_row, s2_row, uint_row = [], [], [], []
                for c in range(col0, min(cols_n, col0 + width)):
                    index = () if ndim == 0 else (c,) if ndim == 1 else (r, c) + self._slice_for(name, array.shape)
                    value = array[index]
                    if is_fxp:
                        raw = int(value.to_bits()) & ((1 << spec.wl) - 1)
                        real_row.append(str(make_fxp_from_raw_uint(raw, spec)))
                        hex_row.append(format_hex(raw, spec.wl))
                        s2_row.append(str(s2_from_raw_uint(raw, spec.wl)))
                        uint_row.append(str(raw))
                    else:
                        real_row.append(self.format_python_scalar(value))
                        hex_row.append("—")
                        s2_row.append("—")
                        uint_row.append("—")
                cells_real.append(real_row)
                cells_hex.append(hex_row)
                cells_s2.append(s2_row)
                cells_uint.append(uint_row)
        editable = is_fxp or (not isinstance(obj, tuple) and array.dtype.kind in "biufcUS"
                              and (not isinstance(obj, np.ndarray) or obj.flags.writeable))
        return MatrixSnapshot(
            name=name, shape=shape, is_1d=ndim == 1, format_str=fmt_str,
            is_fxp=is_fxp, spec=spec, cells_real=cells_real, cells_hex=cells_hex,
            cells_s2=cells_s2, cells_uint=cells_uint, selected_row=sel_r, selected_col=sel_c,
            editable=editable, row_offset=row0, col_offset=col0,
            slice_note="[:, :, " + ", ".join(map(str, self._slice_for(name, array.shape))) + "]" if ndim > 2 else "",
            full_shape=tuple(array.shape), slice_indices=self._slice_for(name, array.shape),
        )

    # ── Mutations triggered by UI ─────────────────────────────────────────

    def reformat_variable(
        self,
        name: str,
        new_fmt_str: str,
        *,
        pin_mode: str | None = None,
    ) -> None:
        """Reformat an entire scalar or array variable while honoring the active Pin mode ('real' or 'hex')."""
        if name not in self.user_ns:
            raise KeyError(f"Variable '{name}' not found")
        spec = FormatEngine.parse(new_fmt_str)
        pin = pin_mode or self.config.inspector_pin_mode
        updated = reformat_value(
            self.user_ns[name],
            spec,
            pin_mode=pin,
            quant=self.config.default_quant,
            overflow=self.config.default_overflow,
        )
        self.user_ns[name] = updated

    def update_target_field(
        self,
        name: str,
        coord: tuple[int, int] | None,
        field: str,
        text_val: str,
    ) -> None:
        """Update a scalar variable or a single matrix cell from 'real', 'hex', 's2', 'uint', or 'bin'."""
        if name not in self.user_ns:
            raise KeyError(f"Variable '{name}' not found")
        obj = self.user_ns[name]

        if isinstance(obj, apy.APyFloat):
            raise TypeError("APyFloat numeric/format edits use the REPL; its bit controls preserve the format")
        if not isinstance(obj, (apy.APyFixed, apy.APyFixedArray)) and field == "real":
            value = ast.literal_eval(text_val.strip())
            if coord is not None and isinstance(obj, (np.ndarray, list)):
                self._write_plain_cell(obj, coord, value, index=self.cell_index(name, coord))
            elif isinstance(obj, np.generic):
                self.user_ns[name] = self._coerce_native_scalar(value, obj.dtype)
            else:
                self.user_ns[name] = value
            return

        if coord is not None and isinstance(obj, (apy.APyFixedArray, np.ndarray, list)):
            self._mutate_array_cell(
                name,
                obj,
                coord,
                lambda cell_fxp: update_scalar_from_field(
                    cell_fxp,
                    field,
                    text_val,
                    quant=self.config.default_quant,
                    overflow=self.config.default_overflow,
                ),
            )
            return

        updated = update_scalar_from_field(
            obj,
            field,
            text_val,
            quant=self.config.default_quant,
            overflow=self.config.default_overflow,
        )
        self.user_ns[name] = updated

    def toggle_target_bit(
        self,
        name: str,
        coord: tuple[int, int] | None,
        bit_index: int,
    ) -> None:
        """Flip bit `bit_index` on a scalar or a selected matrix cell."""
        if name not in self.user_ns:
            return
        obj = self.user_ns[name]
        if isinstance(obj, apy.APyFloat):
            if coord is not None:
                raise TypeError("APyFloat bit editing supports a single scalar variable")
            self.user_ns[name] = toggle_float_bit(obj, bit_index)
            return
        if coord is not None and isinstance(obj, (apy.APyFixedArray, np.ndarray, list)):
            self._mutate_array_cell(
                name, obj, coord, lambda cell_fxp: toggle_scalar_bit(cell_fxp, bit_index)
            )
            return
        updated = toggle_scalar_bit(obj, bit_index)
        self.user_ns[name] = updated

    def invert_target_bits(self, name: str, coord: tuple[int, int] | None) -> None:
        if name not in self.user_ns:
            return
        obj = self.user_ns[name]
        if coord is not None and isinstance(obj, (apy.APyFixedArray, np.ndarray, list)):
            self._mutate_array_cell(name, obj, coord, invert_scalar_bits)
            return
        updated = invert_scalar_bits(obj)
        self.user_ns[name] = updated

    def clear_target_bits(self, name: str, coord: tuple[int, int] | None) -> None:
        if name not in self.user_ns:
            return
        obj = self.user_ns[name]
        if coord is not None and isinstance(obj, (apy.APyFixedArray, np.ndarray, list)):
            self._mutate_array_cell(name, obj, coord, clear_scalar_bits)
            return
        updated = clear_scalar_bits(obj)
        self.user_ns[name] = updated

    @staticmethod
    def _coerce_native_scalar(value: Any, dtype: np.dtype) -> Any:
        converted = np.asarray(value, dtype=dtype)
        if converted.ndim != 0:
            raise ValueError("Enter one scalar value")
        scalar = converted[()]
        if dtype.kind in "biuUS" and scalar != value:
            raise ValueError(f"{value!r} cannot be represented as {dtype} without loss")
        return scalar

    @staticmethod
    def _write_plain_cell(obj: Any, coord: tuple[int, int], value: Any, *, index=None) -> None:
        arr = np.asarray(obj)
        r, c = coord
        if index is None:
            index = () if arr.ndim == 0 else ((c,) if arr.ndim == 1 else
                    (r, c) + (0,) * (arr.ndim - 2))
        if isinstance(obj, np.ndarray):
            if not obj.flags.writeable:
                raise ValueError("This array is read-only")
            if obj.dtype.kind not in "biufcUS":
                raise ValueError("This array dtype is read-only in the editor")
            obj[index] = WorkspaceSession._coerce_native_scalar(value, obj.dtype)
        else:
            parent = obj
            for component in index[:-1]:
                parent = parent[component]
            if not isinstance(parent, list):
                raise ValueError("This nested array cell is read-only")
            parent[index[-1]] = value

    def _mutate_array_cell(
        self,
        name: str,
        obj: Any,
        coord: tuple[int, int],
        mutator_fn,
    ) -> None:
        r, c = coord
        if not isinstance(obj, apy.APyFixedArray):
            raise TypeError("Raw-bit controls require an APyFixedArray; use APyFixedArray.from_float() or Convert to FXP")

        spec = extract_spec(obj)
        self.cell_index(name, coord)  # Edits reject stale/out-of-range coordinates.
        elem, (ar, ac) = self._extract_array_cell(obj, r, c, name=name)
        u_elem = int(elem.to_bits()) & ((1 << spec.wl) - 1)
        wrapped_cell = make_fxp_from_raw_uint(u_elem, spec)
        new_cell = mutator_fn(wrapped_cell)

        if obj.ndim == 1:
            obj[ac] = apy.APyFixed(
                int(new_cell.to_bits()), bits=spec.apy_bits, int_bits=spec.apy_int_bits
            )
        else:
            idx = self.cell_index(name, (ar, ac))
            obj[idx] = apy.APyFixed(
                int(new_cell.to_bits()), bits=spec.apy_bits, int_bits=spec.apy_int_bits
            )
        idx = self.cell_index(name, (ar, ac))
        if not hasattr(obj, "__dict__"):
            sources = self._native_source_map(obj, create=True)
            target = getattr(new_cell, "target_real", None)
            if target is None:
                sources.pop(idx, None)
            else:
                sources[idx] = (raw_uint_from_apy_scalar(new_cell, spec), target,
                                getattr(new_cell, "is_saturated", False))
            # Eviction loses diagnostics only, never numeric data. Bound both
            # retained native owners and total sparse records.
            while (len(self._native_sources) > 32 or
                   sum(len(entry[2]) for entry in self._native_sources.values()) > 100_000):
                self._native_sources.popitem(last=False)
            return
        if obj.quantized_bits is not None:
            obj.quantized_bits[idx] = raw_uint_from_apy_scalar(new_cell, spec)
            obj.target_values[idx] = getattr(new_cell, "target_real", None)
            obj.saturated_mask[idx] = getattr(new_cell, "is_saturated", False)
        else:
            if obj.cell_sources is None:
                obj.cell_sources = {}
            obj.cell_sources[idx] = (raw_uint_from_apy_scalar(new_cell, spec),
                                     getattr(new_cell, "target_real", None), getattr(new_cell, "is_saturated", False))

    def rename_variable(self, old_name: str, new_name: str) -> None:
        new_clean = new_name.strip()
        if not new_clean.isidentifier() or keyword.iskeyword(new_clean):
            raise ValueError(f"'{new_clean}' is not a valid Python variable name")
        if new_clean in self.hidden_names:
            raise ValueError(f"'{new_clean}' is a reserved function name")
        if old_name not in self.user_ns or old_name == new_clean:
            return
        if new_clean in self.user_ns:
            raise ValueError(f"Variable '{new_clean}' already exists")
        self.user_ns[new_clean] = self.user_ns.pop(old_name)
        if old_name in self.array_slices:
            self.array_slices[new_clean] = self.array_slices.pop(old_name)

    def duplicate_variable(self, name: str) -> str:
        if name not in self.user_ns:
            raise KeyError(name)
        idx = 1
        while f"{name}_copy{idx if idx > 1 else ''}" in self.user_ns:
            idx += 1
        new_name = f"{name}_copy{idx if idx > 1 else ''}"
        val = self.user_ns[name]
        try:
            duplicate = val.copy() if isinstance(val, (np.ndarray, apy.APyFixed, apy.APyFixedArray)) else copy.deepcopy(val)
        except Exception as exc:
            raise ValueError(f"Cannot duplicate '{name}': {exc}") from exc
        self.user_ns[new_name] = duplicate
        if isinstance(val, apy.APyFixedArray) and not hasattr(val, "__dict__"):
            sources = self._native_source_map(val)
            if sources:
                # Duplicate metadata follows the independent copied owner.
                for index, source in list(sources.items()):
                    if int(val[index].to_bits()) == source[0]:
                        self._native_source_map(duplicate, create=True)[index] = source
                while (len(self._native_sources) > 32 or
                       sum(len(entry[2]) for entry in self._native_sources.values()) > 100_000):
                    self._native_sources.popitem(last=False)
        if name in self.array_slices:
            self.array_slices[new_name] = self.array_slices[name]
        return new_name

    def delete_variable(self, name: str) -> None:
        self.user_ns.pop(name, None)
        self.array_slices.pop(name, None)

    def convert_variable_to_float(self, name: str) -> None:
        if name not in self.user_ns:
            return
        val = self.user_ns[name]
        if isinstance(val, apy.APyFixed):
            self.user_ns[name] = float(val)
        elif isinstance(val, apy.APyFixedArray):
            self.user_ns[name] = np.asarray(val.to_numpy(), dtype=float)
