"""Bounded GUI edit journal and explicit array operations, owned by the kernel."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
import math
import sys

import apytypes as apy
import numpy as np

from .format_engine import FormatEngine
from .fxp_model import _exact_real, extract_spec, reformat_value, update_scalar_from_field
from .representations import native_integer_bits as native_integer_bits

MAX_EDIT_CELLS = 100_000
MAX_PREVIEW_CELLS = 4096


def _fingerprint(value):
    if isinstance(value, apy.APyFloat):
        return (type(value), value.exp_bits, value.man_bits, value.bias, value.to_bits())
    if isinstance(value, apy.APyFixed):
        return (type(value), value.bits, value.int_bits, int(value.to_bits()),
                getattr(value, "is_unsigned", False), getattr(value, "hw_wl", 0),
                getattr(value, "target_real", None), getattr(value, "is_saturated", False))
    if isinstance(value, np.generic):
        return (value.dtype.str, value.tobytes())
    if type(value) in (int, bool, float, complex, str, type(None)):
        return (type(value), repr(value))
    raise ValueError("Undo supports numeric/string scalars and individual array cells")


def _copy_value(value):
    if isinstance(value, apy.APyFloat):
        return apy.APyFloat.from_bits(value.to_bits(), exp_bits=value.exp_bits,
                                     man_bits=value.man_bits, bias=value.bias)
    # APyTypes' deepcopy protocol returns its base C++ class, losing subclass
    # provenance. The calculator's copy() explicitly preserves that metadata.
    return value.copy() if isinstance(value, apy.APyFixed) else copy.deepcopy(value)


@dataclass
class _Target:
    name: str
    coord: tuple | None
    index: tuple | None
    owner: object
    value: object
    signature: object
    structure: object


class EditJournal:
    """100 entries / 8 MiB of scalar data; no copies of complete large arrays.

    A REPL execution or structural GUI change invalidates the journal. Guards
    also refuse replay after direct rebinding, dtype/shape change, or cell writes.
    """

    MAX_ENTRIES = 100
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, session):
        self.session = session
        self.undo_stack = []
        self.redo_stack = []

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()

    def state(self):
        return {"can_undo": bool(self.undo_stack), "can_redo": bool(self.redo_stack),
                "undo_count": len(self.undo_stack), "redo_count": len(self.redo_stack)}

    def capture(self, name, coord):
        obj = self.session.user_ns[name]
        if coord is None:
            if sys.getsizeof(obj) > self.MAX_BYTES:
                raise ValueError("Edit exceeds the 8 MiB Undo limit")
            _fingerprint(obj)
            value, index = obj, None
        else:
            coord = tuple(coord)
            metadata = self.session.get_matrix_snapshot(name, include_cells=False)
            if metadata is None or not metadata.editable:
                raise ValueError("This array is read-only")
            index = self.session.cell_index(name, coord)
            if isinstance(obj, list):
                parent = obj
                for component in index[:-1]:
                    parent = parent[component]
                if not isinstance(parent, list):
                    raise ValueError("This nested array cell is read-only")
            value, _ = self.session._extract_array_cell(obj, *coord, name=name)
        structure = ((tuple(obj.shape), str(getattr(obj, "dtype", "FXP")),
                     tuple(getattr(obj, "strides", ())),
                     extract_spec(obj) if isinstance(obj, apy.APyFixedArray) else None)
                     if coord is not None and hasattr(obj, "shape") else None)
        return _Target(name, coord, index, obj, _copy_value(value), _fingerprint(value), structure)

    def _matches(self, saved):
        if self.session.user_ns.get(saved.name) is not saved.owner:
            return False
        try:
            current = self.capture(saved.name, saved.coord)
            return (current.index == saved.index and current.signature == saved.signature
                    and current.structure == saved.structure)
        except (ValueError, IndexError, TypeError):
            return False

    def _restore(self, saved):
        if saved.coord is None:
            # Reuse the original immutable binding so earlier journal guards
            # continue to match when walking back through multiple scalar edits.
            self.session.user_ns[saved.name] = saved.owner
        elif isinstance(saved.owner, apy.APyFixedArray):
            self.session._mutate_array_cell(saved.name, saved.owner, saved.coord,
                                            lambda _: _copy_value(saved.value))
        else:
            self.session._write_plain_cell(saved.owner, saved.coord, saved.value, index=saved.index)

    def apply(self, targets, operation):
        if not targets or len(targets) > MAX_EDIT_CELLS:
            raise ValueError("An edit must contain 1..100,000 cells")
        before = [self.capture(name, coord) for name, coord in targets]
        # A scalar bound to a huge integer/string is still subject to the cap.
        def footprint(items):
            return sum(sys.getsizeof(item.value) + len(repr(item.signature)) + 256 for item in items)
        size = footprint(before)
        if size > self.MAX_BYTES:
            raise ValueError("Edit exceeds the 8 MiB Undo limit; use the REPL for this operation")
        try:
            operation()
            after = [self.capture(name, coord) for name, coord in targets]
            size += footprint(after)
            if size > self.MAX_BYTES:
                raise ValueError("Edit exceeds the 8 MiB Undo limit; use the REPL for this operation")
        except Exception:
            for saved in reversed(before):
                self._restore(saved)
            raise
        self.redo_stack.clear()
        self.undo_stack.append((before, after, size))
        while (len(self.undo_stack) > self.MAX_ENTRIES or
               sum(entry[2] for entry in self.undo_stack) > self.MAX_BYTES):
            self.undo_stack.pop(0)

    def replay(self, *, redo=False):
        source, destination = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not source:
            raise ValueError("Nothing to redo" if redo else "Nothing to undo")
        before, after, size = source[-1]
        expected, restore = (before, after) if redo else (after, before)
        if not all(self._matches(saved) for saved in expected):
            self.clear()
            raise ValueError("Undo history cleared: the variable, slice or cell changed outside this edit")
        for saved in restore:
            self._restore(saved)
        source.pop()
        destination.append((before, after, size))


def paste_rectangle(session, journal, name, row, col, text, mode="real"):
    """Validate all TSV cells before writing; rollback unexpected write failures."""
    if len(text) > 8 * 1024 * 1024:
        raise ValueError("Paste is limited to 8 MiB")
    lines = text.rstrip("\r\n").splitlines()
    grid = [line.split("\t") for line in lines]
    if not grid or not grid[0] or any(len(line) != len(grid[0]) for line in grid):
        raise ValueError("Paste requires a nonempty rectangular TSV selection")
    if len(grid) * len(grid[0]) > MAX_EDIT_CELLS:
        raise ValueError("Paste is limited to 100,000 cells")
    obj = session.user_ns[name]
    metadata = session.get_matrix_snapshot(name, include_cells=False)
    if metadata is None or not metadata.editable:
        raise ValueError("This array is read-only")
    prepared = []
    for dr, line in enumerate(grid):
        for dc, text_value in enumerate(line):
            coord = (row + dr, col + dc)
            index = session.cell_index(name, coord)
            if isinstance(obj, apy.APyFixedArray):
                current, _ = session._extract_array_cell(obj, *coord, name=name)
                value = update_scalar_from_field(current, mode, text_value,
                    quant=session.config.default_quant, overflow=session.config.default_overflow)
            else:
                if mode != "real":
                    raise TypeError("Raw representations require an FXP array")
                value = ast.literal_eval(text_value.strip())
                if isinstance(obj, np.ndarray):
                    value = session._coerce_native_scalar(value, obj.dtype)
            prepared.append((coord, index, value))

    def commit():
        for coord, index, value in prepared:
            if isinstance(obj, apy.APyFixedArray):
                session._mutate_array_cell(name, obj, coord, lambda _, value=value: value)
            else:
                session._write_plain_cell(obj, coord, value, index=index)
    journal.apply([(name, coord) for coord, _, _ in prepared], commit)


def selection_grid(session, name, rectangle, mode="real"):
    row, col, height, width = rectangle
    if (any(type(n) is not int or n < 0 for n in rectangle) or height == 0 or width == 0
            or height * width > MAX_EDIT_CELLS):
        raise ValueError("Select a rectangle containing 1..100,000 cells")
    session.cell_index(name, (row, col))
    session.cell_index(name, (row + height - 1, col + width - 1))
    snapshot = session.get_matrix_snapshot(name, window=tuple(rectangle))
    if mode not in ("real", "hex", "s2", "uint"):
        raise ValueError("Unknown matrix representation")
    if mode != "real" and not snapshot.is_fxp:
        raise TypeError("Raw representations require an FXP array")
    return getattr(snapshot, "cells_" + mode)


def selection_statistics(session, name, rectangle):
    row, col, height, width = rectangle
    selection_grid(session, name, rectangle)  # Shared bounds and work limit.
    obj = session.user_ns[name]
    if not isinstance(obj, apy.APyFixedArray):
        array = np.asarray(obj)
        if array.dtype.kind not in "biufc":
            raise TypeError("Statistics require a numeric array")
    values = []
    for r in range(row, row + height):
        for c in range(col, col + width):
            value, _ = session._extract_array_cell(obj, r, c, name=name)
            values.append(complex(value) if isinstance(value, (complex, np.complexfloating)) else float(value))
    data = np.asarray(values)
    finite = data[np.isfinite(data)]
    result = {"count": len(values), "finite_count": int(finite.size),
              "note": "float64/complex128 summary of finite cells; FXP and large integers are approximate"}
    if finite.size:
        for key, value in (("sum", np.sum(finite)), ("mean", np.mean(finite)),
                           ("min_abs", np.min(np.abs(finite))), ("max_abs", np.max(np.abs(finite)))):
            result[key] = str(value)
        if not np.iscomplexobj(finite):
            result.update(min=str(np.min(finite)), max=str(np.max(finite)))
    return result


def conversion_preview(session, name, fmt, pin="real", coord=None):
    obj = session.user_ns[name]
    spec = FormatEngine.parse(fmt)
    array = isinstance(obj, (apy.APyFixedArray, np.ndarray, list, tuple))
    shape = (obj.shape if isinstance(obj, (apy.APyFixedArray, np.ndarray)) else np.asarray(obj).shape) if array else ()
    size = math.prod(shape) if array else 1
    if size == 0:
        raise ValueError("Empty arrays have no values to preview")
    full = size <= MAX_PREVIEW_CELLS
    if array and full:
        # Preview all original dimensions, independent of the selected view.
        original = list(np.asarray(obj.to_bits(), dtype=object).flat) if isinstance(obj, apy.APyFixedArray) else list(np.asarray(obj, dtype=object).flat)
        if isinstance(obj, apy.APyFixedArray):
            from .fxp_model import make_fxp_from_raw_uint
            old_spec = extract_spec(obj)
            original = [make_fxp_from_raw_uint(raw, old_spec) for raw in original]
    elif array:
        original = [session._extract_array_cell(obj, *(coord or (0, 0)), name=name)[0]]
    else:
        original = [obj]
    changed = saturated = truncated = 0
    example = None
    for value in original:
        updated = reformat_value(value, spec, pin_mode=pin,
            quant=session.config.default_quant, overflow=session.config.default_overflow)
        saturated += bool(getattr(updated, "is_saturated", False))
        changed += _exact_real(value) != _exact_real(updated)
        if pin == "hex" and isinstance(value, apy.APyFixed):
            old_spec = extract_spec(value)
            truncated += old_spec.wl > spec.wl and (int(value.to_bits()) & ((1 << old_spec.wl) - 1)) >> spec.wl != 0
        if example is None:
            example = {"before": str(value), "after": str(updated)}
    return {"name": name, "format": fmt, "pin": pin, "total_cells": size,
            "checked_cells": len(original), "complete": full,
            "scope": "whole variable" if full else "selected cell sample; Apply changes the WHOLE variable",
            "changed_values": changed, "saturated_cells": saturated, "discarded_high_bits_cells": truncated,
            "range": f"{spec.min_real} .. {spec.max_real}", "lsb": f"2**(-{spec.fwl})", "example": example}
