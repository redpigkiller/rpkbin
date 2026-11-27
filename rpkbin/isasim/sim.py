"""Instruction execution with transactional State access and event delivery."""

from __future__ import annotations

import os
import re
import string as _string
from collections import deque
from collections.abc import Callable as _Callable, Mapping as _Mapping
from contextlib import ExitStack as _ExitStack
from copy import deepcopy
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .debug import _Debug
from .errors import (
    DebugError,
    LoadError,
    StateAccessError,
    StimulusError,
    TargetError,
    UsageError,
    _Stop,
    _is_int,
)
from .events import Event, ReadRecord, RunResult, WriteRecord, _format
from .loader import _Loaders
from .mcu import MemAliasInfo, _parse_mem_alias
from .snapshot import _Snapshots
from .state import State, _int
from .stimulus import _External

if _TYPE_CHECKING:
    from .mcu import MCU, MCUDescription


def _reverse_bits(val: int, bits: int) -> int:
    result = 0
    for _ in range(bits):
        result = (result << 1) | (val & 1)
        val >>= 1
    return result


class Simulator(_External, _Debug, _Snapshots, _Loaders):
    state: State
    cycle: int
    steps_total: int
    instructions_total: int

    def __init__(
        self, mcu: MCU, *, trace_reads: bool = False, history: int = 1000
    ) -> None:
        mcu._validate()
        if not _is_int(history) or history < 1:
            raise UsageError(f"history={history!r}: expected >= 1")
        self._mcu = mcu
        self._trace_reads = trace_reads
        self._history = deque(maxlen=history)
        self._subscribers = []
        self._trace = None
        self._trace_added = False
        self._images = []
        self._assembly = None
        self._phase = None
        self._running = False
        self._failure = None
        self._active = State(self)
        self._view = State(self, True)
        self._journal = []
        self._instruction_active = False
        self._writes = []
        self._reads = []
        self._warnings = []
        self._next_pc = 0
        self._extra = 0
        self._halted = None
        self._external_init()
        self._debug_init()
        self._mem_aliases: dict[str, MemAliasInfo] = dict(mcu._mem_aliases)
        self.reset()

    def __repr__(self) -> str:
        pc_bits = self._mcu._registers[self._mcu._pc]["bits"]
        return (
            f"Simulator({self._mcu._name!r}, pc=0x{self.state.pc:0{(pc_bits + 3) // 4}x}, "
            f"cycle={self._cycle}, steps={self._steps})"
        )

    def __enter__(self) -> Simulator:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disable_trace()

    def describe(self) -> MCUDescription:
        """Return the current validated MCU declaration as a snapshot."""
        return self._mcu.describe()

    @property
    def state(self) -> State:
        return self._view

    @property
    def cycle(self) -> int:
        return self._cycle

    @property
    def steps_total(self) -> int:
        return self._steps

    @property
    def instructions_total(self) -> int:
        return self._instructions

    def _require_idle(self) -> None:
        if self._running:
            raise UsageError("simulator is already running")

    def reset(self) -> None:
        self._require_idle()
        self._registers = {name: r["reset"] for name, r in self._mcu._registers.items()}
        self._memory = {
            name: [s["init"]] * s["size"] for name, s in self._mcu._spaces.items()
        }
        self._initialized = {
            name: [s["reset_state"] == "initialized"] * s["size"]
            for name, s in self._mcu._spaces.items()
        }
        self._irq = dict.fromkeys(self._mcu._irqs, 0)
        for space, cells in self._images:
            for addr, value in cells.items():
                self._memory[space][addr] = value
                self._initialized[space][addr] = True
        self._cycle = self._steps = self._instructions = 0
        self._next_pc = self.state.pc
        self._reset_external()

    def load_cells(self, space: str, cells: dict[int, int]) -> None:
        self._require_idle()
        if space not in self._mcu._spaces:
            raise LoadError(f"space={space!r}: unknown space")
        meta = self._mcu._spaces[space]
        if not isinstance(cells, dict):
            raise LoadError(f"cells={cells!r}: expected dict")
        size = meta["size"]
        limit = 1 << meta["cell_bits"]
        for addr, value in cells.items():
            if (
                not _is_int(addr)
                or not 0 <= addr < size
                or not _is_int(value)
                or not 0 <= value < limit
            ):
                raise LoadError(f"{space}[{addr!r}]={value!r}: invalid address/value")
        if space == self._mcu._program and cells and self._assembly is not None:
            self._assembly = None
            self._symbols = {}
            self._lines = []
        mem = self._memory[space]
        init = self._initialized[space]
        for addr, value in cells.items():
            mem[addr] = value
            init[addr] = True
        self._images.append((space, dict(cells)))

    def _reg_parts(self, name):
        if not isinstance(name, str):
            raise StateAccessError(f"register={name!r}")
        parts = name.split(".")
        alias = self._mcu._aliases.get(parts[0])
        if alias is not None:
            parts = alias.split(".") + parts[1:]
        meta = self._mcu._registers.get(parts[0])
        if (
            meta is None
            or len(parts) > 2
            or (len(parts) == 2 and parts[1] not in meta["fields"])
        ):
            raise StateAccessError(f"register={name!r}: unknown register/field")
        return parts, meta

    def _reg_read(self, name, semantic=False):
        parts, meta = self._reg_parts(name)
        value = self._registers[parts[0]]
        whole = value
        if len(parts) == 2:
            high, low = meta["fields"][parts[1]]
            value = (value >> low) & ((1 << (high - low + 1)) - 1)
        else:
            high, low = meta["bits"] - 1, 0
        if semantic:
            self._observe("reg", parts[0], (low, high), "r", None, whole)
        return value

    def _reg_write(self, name, value, external=False):
        parts, meta = self._reg_parts(name)
        _int(value, "value")
        if meta["is_pc"] and not external:
            raise StateAccessError(f"register={name!r}: use next_pc")
        width = meta["bits"]
        whole = self._registers[parts[0]]
        if len(parts) == 2:
            high, low = meta["fields"][parts[1]]
            width = high - low + 1
            mask = (1 << width) - 1
            old = (whole >> low) & mask
            new = value & mask
            stored = (whole & ~(mask << low)) | (new << low)
        else:
            high, low = width - 1, 0
            old = whole
            new = stored = value & ((1 << width) - 1)
        if not external:
            self._journal.append(("reg", parts[0], None, whole, None))
        self._registers[parts[0]] = stored
        record = WriteRecord(
            "reg", ".".join(parts), None, old, new, width, len(parts) == 2
        )
        if not external:
            self._writes.append(record)
            self._observe("reg", parts[0], (low, high), "w", whole, stored)
        return record

    def _irq_write(self, line, value, external=False):
        if line not in self._irq:
            raise StateAccessError(f"irq={line!r}: unknown line")
        value = _int(value, "value") & 1
        old = self._irq[line]
        if not external:
            self._journal.append(("irq", line, None, old, None))
        self._irq[line] = value
        record = WriteRecord("irq", f"irq.{line}", None, old, value, 1, False)
        if not external:
            self._writes.append(record)
            self._observe(
                record.kind, record.name, record.addr, "w", record.old, record.new
            )
        return record

    def _warning(self, code, text):
        self._warnings.append((code, text))

    def _address(self, space, addr, access, peek=False):
        if not _is_int(addr):
            raise StateAccessError(f"{space}[{addr!r}]: address must be int, not bool")
        meta = self._mcu._spaces[space]
        if 0 <= addr < meta["size"]:
            return True
        if peek:
            raise TargetError(f"{space}[{addr}]: outside size={meta['size']}")
        policy = self._mcu._policies["on_out_of_range"]
        if policy["action"] == "stop":
            raise _Stop(
                "out_of_range", {"space": space, "addr": addr, "access": access}
            )
        self._warning("out_of_range", f"{space}[{addr}] access={access}")
        return False

    def _mem_read(self, space, addr, peek=False, fetch=False):
        if not self._address(space, addr, "fetch" if fetch else "read", peek):
            return self._mcu._policies["on_out_of_range"]["read_value"] & (
                (1 << self._mcu._spaces[space]["cell_bits"]) - 1
            )
        device = self._device_entry(space, addr)
        bits = self._mcu._spaces[space]["cell_bits"]
        dynamic = False
        if device is not None:
            if peek:
                raise StateAccessError(f"{space}[{addr}]: cannot peek device")
            value = self._call(
                "device",
                lambda: (
                    self._device_call(device, "read", addr, self._cycle)
                    & ((1 << bits) - 1)
                ),
            )
            dynamic = True
        else:
            if not peek and not self._initialized[space][addr]:
                action = self._mcu._policies["on_uninitialized_read"]["action"]
                if action == "stop":
                    raise _Stop("uninitialized_read", {"space": space, "addr": addr})
                if action == "warn":
                    self._warning(
                        "uninitialized_read", f"{space}[{addr}] is uninitialized"
                    )
            sequence = None if peek or fetch else self._sequences.get((space, addr))
            if sequence is None:
                value = self._memory[space][addr]
            else:
                value = sequence["values"][
                    min(sequence["position"], len(sequence["values"]) - 1)
                ]
                sequence["position"] += 1
                dynamic = True
        if not peek and not fetch:
            self._step_reads.append((space, addr, value, dynamic))
            if self._trace_reads:
                self._reads.append(ReadRecord(space, addr, value, bits))
            self._notify_cell("on_read", space, addr)
            self._observe("mem", space, addr, "r", None, value)
        return value

    def _mem_write(self, space, addr, value, external=False):
        _int(value, "value")
        if not self._address(space, addr, "write", external):
            return None
        meta = self._mcu._spaces[space]
        value &= (1 << meta["cell_bits"]) - 1
        device = self._device_entry(space, addr)
        if device is not None:
            if external:
                raise StimulusError(f"{space}[{addr}]: device cell")
            self._call(
                "device", self._device_call, device, "write", addr, value, self._cycle
            )
            old = None
            self._device_written = True
        else:
            access = meta["access"]
            if not external:
                for lo, hi, region_access in meta.get("regions", ()):
                    if lo <= addr < hi:
                        access = region_access
                        break
            if not external and access == "ro":
                if self._mcu._policies["on_write_to_readonly"]["action"] == "stop":
                    raise _Stop("readonly_write", {"space": space, "addr": addr})
                self._warning("readonly_write", f"{space}[{addr}] readonly")
                return None
            old = self._memory[space][addr]
            if not external:
                self._journal.append(
                    ("mem", space, addr, old, self._initialized[space][addr])
                )
            self._memory[space][addr] = value
            self._initialized[space][addr] = True
        record = WriteRecord("mem", space, addr, old, value, meta["cell_bits"], False)
        if not external:
            self._writes.append(record)
            self._notify_cell("on_write", space, addr)
            self._observe("mem", space, addr, "w", old, value)
        return record

    def _target(self, target):
        if not isinstance(target, str):
            raise TargetError(f"target={target!r}: expected string")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", target):
            if target.startswith("irq."):
                if target[4:] not in self._irq:
                    raise TargetError(f"target={target!r}: unknown line")
                return "irq", target[4:], None
            try:
                self._reg_parts(target)
            except StateAccessError as error:
                raise TargetError(f"target={target!r}: {error}") from error
            return "reg", target, None
        match = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_]*)\[((?:0x[0-9a-fA-F]+|[0-9]+))\]", target
        )
        if match:
            name, digits = match.groups()
            addr = int(digits, 16 if digits.startswith("0x") else 10)
            if name in self._mcu._spaces and addr < self._mcu._spaces[name]["size"]:
                return "mem", name, addr
        raise TargetError(f"target={target!r}: invalid or outside space")

    def initialized_ranges(self, space: str) -> tuple[tuple[int, int], ...]:
        """Return sorted half-open ranges of initialized, non-device cells."""
        if space not in self._mcu._spaces:
            raise StateAccessError(f"space={space!r}: unknown space")
        initialized = self._initialized[space]
        device_ranges = sorted(
            (entry["lo"], entry["hi"])
            for entry in self._devices
            if entry["space"] == space
        )
        end = len(initialized)
        ranges = []
        scan_from = 0
        for scan_to, next_start in (*device_ranges, (end, end)):
            start = None
            addresses = range(scan_from, scan_to)
            for addr in addresses:
                if initialized[addr]:
                    if start is None:
                        start = addr
                elif start is not None:
                    ranges.append((start, addr))
                    start = None
            if start is not None:
                ranges.append((start, addresses.stop))
            scan_from = next_start
        return tuple(ranges)

    @property
    def mem_aliases(self) -> _Mapping[str, MemAliasInfo]:
        """Return active memory aliases (MCU declarations plus simulator registrations)."""
        return _MappingProxyType(self._mem_aliases)

    def mem_alias(
        self,
        name: str,
        target: str,
        *,
        endian: str | None = None,
        bit_order: str | None = None,
    ) -> None:
        """Register a named memory alias on this simulator."""
        existing = (
            set(self._mcu._spaces)
            | set(self._mcu._registers)
            | set(self._mcu._irqs)
            | set(self._mcu._aliases)
            | set(self._mem_aliases)
        )
        info = _parse_mem_alias(
            name,
            target,
            endian=endian,
            bit_order=bit_order,
            spaces=self._mcu._spaces,
            existing_names=existing,
            error_cls=TargetError,
        )
        self._validate_mem_alias_target(
            info, error_cls=TargetError, target_label=target
        )
        self._mem_aliases[name] = info

    def clear_mem_aliases(self) -> None:
        """Clear simulator-registered memory aliases while keeping MCU declarations."""
        self._mem_aliases.clear()
        self._mem_aliases.update(self._mcu._mem_aliases)

    def _pack_mem_alias_cells(self, info: MemAliasInfo, cells) -> int:
        if info.bit_hi is not None:
            raw = (cells[0] >> info.bit_lo) & ((1 << info.bits) - 1)
        else:
            cell_bits = self._mcu._spaces[info.space]["cell_bits"]
            ordered = reversed(cells) if info.endian == "little" else cells
            raw = 0
            for cell in ordered:
                raw = (raw << cell_bits) | cell
        if info.bit_order == "lsb_first":
            raw = _reverse_bits(raw, info.bits)
        return raw

    def _mem_alias_read(self, info: MemAliasInfo) -> int:
        cells = [
            self._mem_read(info.space, addr, peek=True)
            for addr in range(info.lo, info.hi)
        ]
        return self._pack_mem_alias_cells(info, cells)

    def _mem_alias_has_device(self, info: MemAliasInfo) -> bool:
        return any(
            e["space"] == info.space and info.lo < e["hi"] and e["lo"] < info.hi
            for e in self._devices
        )

    def _validate_mem_alias_target(
        self,
        info_or_name: MemAliasInfo | str,
        *,
        error_cls: type[Exception] = TargetError,
        reject_device: bool = False,
        target_label: str | None = None,
    ) -> MemAliasInfo:
        if isinstance(info_or_name, MemAliasInfo):
            info = info_or_name
            label = info.name if target_label is None else target_label
        else:
            label = str(info_or_name) if target_label is None else target_label
            info = (
                self._mem_aliases.get(info_or_name)
                if isinstance(info_or_name, str)
                else None
            )
            if info is None:
                raise error_cls(f"target={label!r}: unknown memory alias")
        if reject_device and self._mem_alias_has_device(info):
            raise error_cls(f"target={label!r}: device cell")
        if info.hi - info.lo > 1 and self._mem_alias_has_device(info):
            raise error_cls(
                f"target={label!r}: multi-cell mem_alias cannot overlap device"
            )
        return info

    def _validate_mem_alias_write(
        self,
        target: str,
        value: int,
        *,
        error_cls: type[Exception] = StimulusError,
    ) -> MemAliasInfo:
        info = self._validate_mem_alias_target(
            target, error_cls=error_cls, reject_device=True
        )
        if not _is_int(value):
            raise error_cls(f"{target}={value!r}: expected int")
        return info

    def _validate_mem_alias_refs(self) -> None:
        for point in self._debug_points:
            alias_name = point.get("mem_alias_name")
            if point.get("kind") == "watch" and alias_name is not None:
                self._validate_mem_alias_target(alias_name, error_cls=DebugError)
        for entry in self._stimuli:
            action = entry.get("action")
            if action and action[0] == "set_mem_alias":
                self._validate_mem_alias_target(
                    action[1], error_cls=StimulusError, reject_device=True
                )

    def _mem_alias_write(
        self, info: MemAliasInfo, value: int
    ) -> tuple[WriteRecord, ...]:
        raw = value & ((1 << info.bits) - 1)
        if info.bit_order == "lsb_first":
            raw = _reverse_bits(raw, info.bits)
        if info.bit_hi is not None:
            current = self._mem_read(info.space, info.lo, peek=True)
            field_mask = ((1 << info.bits) - 1) << info.bit_lo
            updated = (current & ~field_mask) | ((raw << info.bit_lo) & field_mask)
            return (self._mem_write(info.space, info.lo, updated, external=True),)
        cell_bits = self._mcu._spaces[info.space]["cell_bits"]
        cell_mask = (1 << cell_bits) - 1
        count = info.hi - info.lo
        records = []
        for offset in range(count):
            position = count - offset - 1 if info.endian != "little" else offset
            cell_value = (raw >> (position * cell_bits)) & cell_mask
            records.append(
                self._mem_write(info.space, info.lo + offset, cell_value, external=True)
            )
        return tuple(records)

    def read(self, target: str) -> int:
        if isinstance(target, str) and target in self._mem_aliases:
            return self._mem_alias_read(self._mem_aliases[target])
        kind, name, addr = self._target(target)
        if kind == "reg":
            return self._reg_read(name)
        if kind == "irq":
            return self._irq[name]
        return self._mem_read(name, addr, peek=True)

    def poke(self, target: str, value: int) -> None:
        self._external_set(target, value, "poke")

    def _decode(self, pc, peek=False):
        remaining = self._mcu._instructions
        cells = []
        index = 0
        while remaining:
            if peek:
                addr = pc + index
                if not 0 <= addr < self._mcu._spaces[self._mcu._program]["size"]:
                    return None, tuple(cells), {}
                cell = self._memory[self._mcu._program][addr]
            else:
                cell = self._mem_read(self._mcu._program, pc + index, fetch=True)
            cells.append(cell)
            candidates, complete = [], []
            for ins in remaining:
                mask, expected = ins["masks"][index]
                if cell & mask != expected:
                    continue
                if ins["length"] == index + 1:
                    complete.append(ins)
                else:
                    candidates.append(ins)
            if complete:
                ins = complete[0]
                if len(complete) > 1:
                    names = {candidate["name"] for candidate in complete}
                    ins = next(
                        (
                            candidate
                            for candidate in complete
                            if names - {candidate["name"]} <= candidate["covered"]
                        ),
                        None,
                    )
                    if ins is None:
                        raise RuntimeError(
                            f"no overriding decode candidate at address {pc}"
                        )
                values = {}
                for letter, chunks in ins["extractors"]:
                    value = 0
                    for cell_index, shift, count, field_mask in chunks:
                        value = (value << count) | (
                            (cells[cell_index] >> shift) & field_mask
                        )
                    values[letter] = value
                return ins, tuple(cells), values
            remaining = candidates
            index += 1
        return None, tuple(cells), {}

    def instruction_kind(self, where: int | str) -> str | None:
        """Return the declared display kind for the instruction at a cell address."""
        addr = self._where(where, source_locations=False)
        instruction, _, _ = self._decode(addr, True)
        return None if instruction is None else instruction["kind"]

    def disassemble(
        self, where: int | str, count: int = 1
    ) -> list[tuple[int, str, int]]:
        if not _is_int(count) or count < 1:
            raise UsageError(f"count={count!r}")
        where = self._where(where)
        result = []
        pc = where
        for _ in range(count):
            ins, _cells, values = self._decode(pc, True)
            if ins is None:
                break
            names = {}
            for letter, operand in ins.get("operands", {}).items():
                value = values[letter]
                bits = ins["compiled"].count(letter)
                if operand.signed and value & (1 << (bits - 1)):
                    value -= 1 << bits
                if operand.relative is not None:
                    value += pc + (
                        ins["length"] if operand.relative == "next_pc" else 0
                    )
                values[letter] = value
                if operand.names is not None:
                    for name, named_value in operand.names.items():
                        if named_value == value:
                            names[letter] = name
                            break
            syntax = ins["syntax"]
            if names:
                parts = []
                for literal, field, fmt, conversion in _string.Formatter().parse(
                    syntax
                ):
                    parts.append(literal)
                    if field in names:
                        parts.append(names[field])
                    elif field is not None:
                        conversion = "" if conversion is None else f"!{conversion}"
                        parts.append(f"{{{field}{conversion}:{fmt}}}".format(**values))
                syntax = "".join(parts)
            else:
                syntax = syntax.format(**values)
            result.append((pc, syntax, ins["length"]))
            pc += ins["length"]
        return result

    def _rollback(self, mark=0):
        for kind, name, addr, old, initialized in reversed(self._journal[mark:]):
            if kind == "reg":
                self._registers[name] = old
            elif kind == "irq":
                self._irq[name] = old
            else:
                self._memory[name][addr] = old
                self._initialized[name][addr] = initialized
        del self._journal[mark:]

    def _event(
        self,
        kind,
        *,
        seq=None,
        cycle=None,
        pc=None,
        cells=None,
        name=None,
        text="",
        writes=(),
        reads=(),
        consumed=None,
        code=None,
        detail=None,
    ):
        return Event(
            kind,
            self._steps if seq is None else seq,
            self._cycle if cycle is None else cycle,
            pc,
            cells,
            self._mcu._spaces[self._mcu._program]["cell_bits"]
            if kind == "instr"
            else None,
            self._mcu._registers[self._mcu._pc]["bits"],
            name,
            text,
            tuple(writes),
            tuple(reads),
            consumed,
            code,
            None if detail is None else deepcopy(detail),
        )

    def _record_error(self, where, error):
        if self._failure is None:
            self._failure = (where, error, [])
        else:
            self._failure[2].append(repr(error))

    def _call(self, where, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as error:
            self._record_error(where, error)
            raise _Stop("python_error") from error

    def _emit(self, event):
        self._history.append(event)
        if not self._subscribers:
            return
        errors = []
        for subscriber in tuple(self._subscribers):
            try:
                subscriber(event)
            except Exception as error:
                errors.append(error)
                if self._running:
                    self._record_error("subscriber", error)
        if errors and (not self._running or event.kind == "stop"):
            raise self._failure[1] if self._running else errors[0]
        if errors and self._phase == "A":
            raise _Stop("python_error")

    def subscribe(self, fn: _Callable[[Event], None]) -> None:
        if not callable(fn):
            raise UsageError(f"subscriber={fn!r}: expected callable")
        self._subscribers.append(fn)

    def _trace_event(self, event):
        if self._trace is not None:
            stream, fmt = self._trace
            stream.write(_format(event, self._mcu, fmt) + "\n")
            stream.flush()

    def enable_trace(self, path: str | os.PathLike[str], *, fmt: str = "text") -> None:
        if fmt not in ("text", "jsonl") or self._trace is not None:
            raise UsageError(f"fmt={fmt!r}, path={path!r}: invalid trace request")
        try:
            with _ExitStack() as owner:
                stream = owner.enter_context(
                    open(path, "w", encoding="utf-8", newline="\n")
                )
                # Transfer the open stream to disable_trace's existing lifecycle.
                owner.pop_all()
        except OSError as error:
            raise UsageError(f"path={path!r}: {error}") from error
        self._trace = (stream, fmt)
        if not self._trace_added:
            self.subscribe(self._trace_event)
            self._trace_added = True

    def disable_trace(self) -> None:
        if self._trace is not None:
            self._trace[0].close()
            self._trace = None

    def recent_events(self, n: int) -> list[Event]:
        if not _is_int(n) or n < 0:
            raise UsageError(f"n={n!r}")
        return list(self._history)[-n:] if n else []

    def step(self, n: int = 1) -> RunResult:
        if not _is_int(n) or n < 1:
            raise UsageError(f"n={n!r}: expected >= 1")
        return self._drive(n)

    def run(
        self,
        *,
        max_steps: int,
        max_cycles: int | None = None,
        stuck_window: int = 1000,
        raise_exceptions: bool = False,
        stuck_ignore: tuple[str, ...] = (),
    ) -> RunResult:
        for name, value, minimum in (
            ("max_steps", max_steps, 1),
            ("stuck_window", stuck_window, 0),
        ):
            if not _is_int(value) or value < minimum:
                raise UsageError(f"{name}={value!r}: expected >= {minimum}")
        if max_cycles is not None and (
            not _is_int(max_cycles) or max_cycles < 1
        ):
            raise UsageError(f"max_cycles={max_cycles!r}: expected >= 1")
        return self._drive(
            max_steps,
            run_mode=True,
            max_cycles=max_cycles,
            raise_exceptions=raise_exceptions,
            stuck_window=stuck_window,
            stuck_masks=self._stuck_masks(stuck_ignore),
        )

    def _drive(
        self,
        limit,
        *,
        run_mode=False,
        max_cycles=None,
        raise_exceptions=False,
        stuck_window=0,
        stuck_masks=None,
    ):
        self._require_idle()
        start_steps, start_instructions, start_cycle = (
            self._steps,
            self._instructions,
            self._cycle,
        )
        self._running = True
        self._failure = None
        self._quiet = []
        self._last_values = {}
        reason = "max_steps" if run_mode else "step_done"
        detail = {}
        stuck = None
        try:
            try:
                for iteration in range(limit):
                    self._phase = "S0"
                    self._validate_mem_alias_refs()
                    if run_mode and iteration:
                        hit = self._breakpoint()
                        if hit is not None:
                            reason = "breakpoint"
                            detail = hit
                            break
                    self._journal = []
                    self._writes = []
                    self._reads = []
                    self._warnings = []
                    self._extra = 0
                    self._halted = None
                    self._step_reads = []
                    self._device_written = False
                    self._stimulus_applied = False
                    self._observations = []
                    try:
                        self._phase = "A"
                        self._boundary()
                        self._phase = "B"
                        pc = self.state.pc
                        cycle = self._cycle
                        seq = self._steps
                        interrupt = self._check_interrupt()
                        if interrupt is not None:
                            name, base_cycles = interrupt
                            consumed = base_cycles + self._extra
                            kind = "interrupt"
                            cells = None
                            text = ""
                        else:
                            kind = "instr"
                            ins, cells, values = self._decode(pc)
                            if ins is None:
                                policy = self._mcu._policies["on_undefined_opcode"]
                                if policy["action"] == "stop":
                                    raise _Stop(
                                        "undefined_opcode",
                                        {"pc": pc, "cells": [cells[0]]},
                                    )
                                length = policy["nop_cells"]
                                consumed = policy["nop_cycles"]
                                name = text = "<undefined>"
                                size = self._mcu._spaces[self._mcu._program]["size"]
                                cells = tuple(
                                    self._memory[self._mcu._program][
                                        pc : min(pc + length, size)
                                    ]
                                )
                                self._next_pc = pc + length
                                self._warning("undefined_opcode", f"pc={pc}")
                            else:
                                self._next_pc = pc + ins["length"]
                                self._instruction_active = True
                                try:
                                    self._call(
                                        "semantic", ins["fn"], self._active, **values
                                    )
                                finally:
                                    self._instruction_active = False
                                consumed = ins["cycles"] + self._extra
                                name = ins["name"]
                                text = ins["syntax"].format(**values)
                        self._phase = "C"
                        self._registers[self._mcu._pc] = self._next_pc & (
                            (1 << self._mcu._registers[self._mcu._pc]["bits"]) - 1
                        )
                        self._cycle += consumed
                        self._steps += 1
                        if kind == "instr":
                            self._instructions += 1
                        self._tick(consumed)
                        self._emit(
                            self._event(
                                kind,
                                seq=seq,
                                cycle=cycle,
                                pc=pc,
                                cells=cells,
                                name=name,
                                text=text,
                                writes=self._writes,
                                reads=self._reads,
                                consumed=consumed,
                            )
                        )
                        for code, message in self._warnings:
                            self._emit(
                                self._event(
                                    "warning",
                                    seq=seq,
                                    cycle=cycle,
                                    pc=pc,
                                    code=code,
                                    text=message,
                                )
                            )
                        if self._failure is not None:
                            reason = "python_error"
                            break
                        if self._halted is not None:
                            reason = "halted"
                            detail = {"reason": self._halted}
                            break
                        watch = self._watchpoint(pc)
                        if watch is not None:
                            reason = "watchpoint"
                            detail = watch
                            break
                        if run_mode and stuck_window:
                            stuck = self._stuck_check(
                                stuck_window, pc, interrupt is not None, stuck_masks
                            )
                            if stuck is not None:
                                reason = "stuck"
                                break
                        if run_mode and self._steps - start_steps == limit:
                            reason = "max_steps"
                            break
                        if (
                            run_mode
                            and max_cycles is not None
                            and self._cycle - start_cycle >= max_cycles
                        ):
                            reason = "max_cycles"
                            break
                    except _Stop as stop:
                        if self._phase == "B":
                            self._rollback()
                        reason = stop.code
                        detail = stop.detail
                        break
            except KeyboardInterrupt:
                if self._phase == "B":
                    self._rollback()
                reason = "interrupted"
                detail = {}
            exception = None
            if self._failure is not None:
                where, exception, suppressed = self._failure
                reason = "python_error"
                detail = {"where": where, "error": repr(exception)}
                if suppressed:
                    detail["suppressed"] = list(suppressed)
            text = detail.get("reason", "")
            if reason == "fault":
                text = detail["code"] + (
                    ": " + detail["message"] if detail["message"] else ""
                )
            result = RunResult(
                reason,
                self._steps - start_steps,
                self._instructions - start_instructions,
                self._cycle - start_cycle,
                self._cycle,
                self.state.pc,
                detail,
                exception,
                stuck,
            )
            self._phase = "stop"
            self._emit(
                self._event(
                    "stop", pc=self.state.pc, text=text, code=reason, detail=detail
                )
            )
            if exception is not None and raise_exceptions:
                raise exception.with_traceback(exception.__traceback__)
            return result
        finally:
            self._running = False
            self._phase = None
