"""Devices and deterministic boundary stimuli."""

from __future__ import annotations

import inspect as _inspect
from collections.abc import Callable as _Callable
from collections.abc import Sequence as _Sequence
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .errors import (
    DebugError,
    DeviceError,
    StimulusError,
    _Stop,
    TargetError,
    StateAccessError,
    _is_int,
)
from .events import StuckReport, _dec_or_hex, _hex

if _TYPE_CHECKING:
    from .sim import Simulator


def _accepts_device_context(method, position):
    try:
        parameters = _inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = [
        param
        for param in parameters
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) > position and positional[position].name == "ctx"


class _DeviceRegisters:
    __slots__ = ("_context",)

    def __init__(self, context):
        self._context = context

    def __getitem__(self, name):
        return self._context._active_state().reg[name]

    def __setitem__(self, name, value):
        self._context._active_state(write=True).reg[name] = value


class _DeviceContext:
    __slots__ = ("_state", "_writable", "_registers")

    def __init__(self, state, writable):
        self._state = state
        self._writable = writable
        self._registers = _DeviceRegisters(self)

    def _active_state(self, *, write=False):
        if self._state is None:
            raise StateAccessError("Device ctx expired at the end of its callback")
        if write and not self._writable:
            raise StateAccessError(
                "Device ctx writes and add_cycles are unavailable during interrupt_check or outside instruction execution"
            )
        return self._state

    @property
    def reg(self):
        self._active_state()
        return self._registers

    def add_cycles(self, n: int) -> None:
        self._active_state(write=True).add_cycles(n)


class Device:
    time_dependent: bool = False

    def read(self, addr: int, now: int) -> int:
        raise NotImplementedError

    def write(self, addr: int, value: int, now: int) -> None:
        raise NotImplementedError

    def tick(self, cycles: int, now: int) -> None:
        pass

    def reset(self) -> None:
        pass

    def get_state(self) -> dict[str, object]:
        raise NotImplementedError

    def set_state(self, state: dict[str, object]) -> None:
        raise NotImplementedError


class Trigger:
    def __init__(self, sim, entry):
        self._sim = sim
        self._entry = entry
        self._used = False

    def delay(self, cycles: int) -> Trigger:
        if not _is_int(cycles) or cycles < 0:
            raise StimulusError(f"cycles={cycles!r}: expected >= 0")
        self._entry["delay"] = cycles
        return self

    def _action(self, action):
        if self._used:
            raise StimulusError(
                f"trigger={self._entry['kind']}: action already registered"
            )
        self._used = True
        self._entry["action"] = action
        self._sim._stimuli.append(self._entry)

    def set(self, target: str, value: int) -> None:
        if isinstance(target, str) and target in self._sim._mem_aliases:
            self._sim._validate_mem_alias_write(
                target, value, error_cls=StimulusError
            )
            self._action(("set_mem_alias", target, value))
            return
        kind, name, addr = self._sim._target(target)
        if kind not in ("reg", "mem"):
            raise StimulusError(f"target={target!r}: expected register/field/cell")
        if kind == "mem" and self._sim._device(name, addr) is not None:
            raise StimulusError(f"target={target!r}: device cell")
        if not _is_int(value):
            raise StimulusError(f"{target}={value!r}: expected int")
        self._action(("set", target, value))

    def irq(self, line: str, level: int) -> None:
        self._sim._target(f"irq.{line}")
        if not _is_int(level) or level not in (0, 1):
            raise StimulusError(f"level={level!r}: expected 0/1")
        self._action(("irq", line, level))

    def call(self, fn: _Callable[[Simulator], None]) -> None:
        if not callable(fn):
            raise StimulusError(f"fn={fn!r}: expected callable")
        self._action(("call", fn))


class _External:
    def _external_init(self):
        self._devices = []
        self._sequences = {}
        self._stimuli = []
        self._symbols = {}
        self._lines = []
        self._step_reads = []
        self._device_written = False
        self._stimulus_applied = False

    def _device_entry(self, space, addr):
        for entry in self._devices:
            if entry["space"] == space and entry["lo"] <= addr < entry["hi"]:
                return entry
        return None

    def _device(self, space, addr):
        entry = self._device_entry(space, addr)
        return None if entry is None else entry["obj"]

    def _device_call(self, entry, name, *args):
        method = getattr(entry["obj"], name)
        if not entry["context"][name]:
            return method(*args)
        context = _DeviceContext(self._active, self._instruction_active)
        try:
            return method(*args, context)
        finally:
            context._state = None

    def attach(self, device: object, *, space: str, lo: int, hi: int) -> None:
        meta = self._mcu._spaces.get(space)
        if (
            meta is None
            or meta["is_program"]
            or not _is_int(lo)
            or not _is_int(hi)
            or not 0 <= lo < hi <= meta["size"]
        ):
            raise DeviceError(
                f"space={space!r}, lo={lo!r}, hi={hi!r}: invalid device range"
            )
        if not callable(getattr(device, "read", None)) or not callable(
            getattr(device, "write", None)
        ):
            raise DeviceError(f"device={type(device).__name__}: read/write required")
        if any(
            e["space"] == space and lo < e["hi"] and e["lo"] < hi for e in self._devices
        ):
            raise DeviceError(f"{space}[{lo}:{hi}]: overlapping device")
        if any(s == space and lo <= addr < hi for s, addr in self._sequences):
            raise DeviceError(f"{space}[{lo}:{hi}]: overlaps read_seq")
        if any(
            info.hi - info.lo > 1
            and info.space == space
            and lo < info.hi
            and info.lo < hi
            for info in self._mem_aliases.values()
        ):
            raise DeviceError(f"{space}[{lo}:{hi}]: overlaps multi-cell mem_alias")
        context = {
            "read": _accepts_device_context(device.read, 2),
            "write": _accepts_device_context(device.write, 3),
        }
        self._devices.append(
            {"obj": device, "space": space, "lo": lo, "hi": hi, "context": context}
        )

    def _single_cell(self, target):
        kind, name, addr = self._target(target)
        if kind != "mem":
            raise StimulusError(f"target={target!r}: expected single space cell")
        return name, addr

    def read_seq(self, target: str, values: _Sequence[int]) -> None:
        name, addr = self._single_cell(target)
        meta = self._mcu._spaces[name]
        if (
            meta["is_program"]
            or self._device(name, addr) is not None
            or (name, addr) in self._sequences
        ):
            raise StimulusError(f"target={target!r}: unavailable for read_seq")
        values = list(values)
        if not values or any(
            not _is_int(v) or not 0 <= v < (1 << meta["cell_bits"])
            for v in values
        ):
            raise StimulusError(f"{target} values={values!r}: outside cell width")
        entry = {
            "kind": "read_seq",
            "target": f"{name}[{addr}]",
            "values": values,
            "position": 0,
        }
        self._sequences[name, addr] = entry
        self._stimuli.append(entry)

    def _symbol_address(self, name):
        if name in self._symbols:
            return self._symbols[name]
        if self._assembly is not None and not self._assembly._case_sensitive_names:
            key = name.casefold()
            for symbol, address in self._symbols.items():
                if symbol.casefold() == key:
                    return address
        return None

    def _where(self, where, *, source_locations=True):
        if isinstance(where, str):
            symbol = self._symbol_address(where)
            if symbol is not None:
                return symbol
            if source_locations:
                source = self._source_address(where)
                if source is not None:
                    return source
        if _is_int(where):
            return where
        raise DebugError(f"where={where!r}: unknown label/address")

    def _trigger(self, kind, **parameters):
        return Trigger(
            self,
            dict(
                kind=kind,
                delay=0,
                action=None,
                seen=0,
                fired=0,
                pending=[],
                **parameters,
            ),
        )

    def at_cycle(self, n: int) -> Trigger:
        if not _is_int(n) or n < 0:
            raise StimulusError(f"n={n!r}: expected >= 0")
        return self._trigger("at_cycle", n=n)

    def at_pc(self, where: int | str, *, times: int | None = 1) -> Trigger:
        addr = self._where(where)
        if times is not None and (not _is_int(times) or times < 1):
            raise StimulusError(f"times={times!r}: expected >= 1 or None")
        return self._trigger("at_pc", where=addr, times=times)

    def on_read(self, target: str, *, nth: int) -> Trigger:
        return self._on("on_read", target, nth)

    def on_write(self, target: str, *, nth: int) -> Trigger:
        return self._on("on_write", target, nth)

    def _on(self, kind, target, nth):
        name, addr = self._single_cell(target)
        if not _is_int(nth) or nth < 1:
            raise StimulusError(f"nth={nth!r}: expected >= 1")
        return self._trigger(kind, target=f"{name}[{addr}]", nth=nth)

    def _notify_cell(self, kind, space, addr):
        target = f"{space}[{addr}]"
        for entry in self._stimuli:
            if entry["kind"] == kind and entry["target"] == target:
                entry["seen"] += 1
                if entry["seen"] == entry["nth"]:
                    entry["fired"] += 1
                    entry["pending"].append(self._cycle + entry["delay"])

    def _external_set(self, target, value, verb, *, require_mem_alias=False):
        if require_mem_alias or (
            isinstance(target, str) and target in self._mem_aliases
        ):
            info = self._validate_mem_alias_write(
                target, value, error_cls=StimulusError
            )
            records = self._mem_alias_write(info, value)
            old_val = self._pack_mem_alias_cells(
                info, [record.old for record in records]
            )
            new_val = self._pack_mem_alias_cells(
                info, [record.new for record in records]
            )
            if info.bit_hi is not None:
                old = _dec_or_hex(old_val, info.bits)
                new = _dec_or_hex(new_val, info.bits)
            else:
                old, new = _hex(old_val, info.bits), _hex(new_val, info.bits)
            text = f"{verb} {target} {old}->{new}"
            self._emit(self._event("external", text=text, writes=records))
            return
        kind, name, addr = self._target(target)
        if not _is_int(value):
            raise StimulusError(f"{target}={value!r}: expected int")
        if kind == "reg":
            record = self._reg_write(name, value, True)
        elif kind == "irq":
            record = self._irq_write(name, value, True)
        else:
            if self._device(name, addr) is not None:
                raise StimulusError(f"target={target!r}: device cell")
            record = self._mem_write(name, addr, value, True)
        if record.is_field or kind == "irq":
            old = _dec_or_hex(record.old, record.bits)
            new = _dec_or_hex(record.new, record.bits)
        else:
            old, new = _hex(record.old, record.bits), _hex(record.new, record.bits)
        text = (
            f"irq {name} {old}->{new}"
            if verb == "irq"
            else f"{verb} {target} {old}->{new}"
        )
        self._emit(self._event("external", text=text, writes=(record,)))

    def _boundary(self):
        pc = self.state.pc
        for entry in self._stimuli:
            if (
                entry["kind"] == "at_pc"
                and entry["where"] == pc
                and (entry["times"] is None or entry["fired"] < entry["times"])
            ):
                entry["fired"] += 1
                entry["pending"].append(self._cycle + entry["delay"])
            elif (
                entry["kind"] == "at_cycle"
                and not entry["fired"]
                and self._cycle >= entry["n"]
            ):
                entry["fired"] = 1
                entry["pending"].append(entry["n"] + entry["delay"])
        for entry in self._stimuli:
            if entry["kind"] == "read_seq":
                continue
            while entry["pending"] and entry["pending"][0] <= self._cycle:
                entry["pending"].pop(0)
                self._stimulus_applied = True
                action = entry["action"]
                if action[0] == "call":
                    self._emit(
                        self._event("external", text=f"call {action[1].__name__}")
                    )
                    self._call("stimulus", action[1], self)
                elif action[0] == "set":
                    self._external_set(action[1], action[2], "set")
                elif action[0] == "set_mem_alias":
                    self._external_set(
                        action[1], action[2], "set", require_mem_alias=True
                    )
                else:
                    self._external_set(f"irq.{action[1]}", action[2], "irq")

    def _reset_external(self):
        for entry in self._devices:
            reset = getattr(entry["obj"], "reset", None)
            if callable(reset):
                reset()
        for entry in self._stimuli:
            if entry["kind"] == "read_seq":
                entry["position"] = 0
            else:
                entry["seen"] = entry["fired"] = 0
                entry["pending"] = []

    def list_stimuli(self) -> list[str]:
        return [str(self._stimulus_signature(entry)) for entry in self._stimuli]

    def clear_stimuli(self) -> None:
        self._sequences.clear()
        self._stimuli.clear()

    def _stimulus_signature(self, entry):
        kind = entry["kind"]
        if kind == "read_seq":
            return [kind, entry["target"], list(entry["values"])]
        if kind == "at_cycle":
            result = [kind, entry["n"], entry["delay"]]
        elif kind == "at_pc":
            result = [kind, entry["where"], entry["times"], entry["delay"]]
        else:
            result = [kind, entry["target"], entry["nth"], entry["delay"]]
        action = entry["action"]
        if action[0] == "call":
            action_sig = ["call", action[1].__name__]
        elif action[0] == "set_mem_alias":
            action_sig = ["set", action[1], action[2]]
        else:
            action_sig = list(action)
        return result + action_sig

    def _tick(self, consumed):
        for entry in self._devices:
            tick = getattr(entry["obj"], "tick", None)
            if callable(tick):
                try:
                    self._call("device", tick, consumed, self._cycle)
                except _Stop:
                    break

    def _check_interrupt(self):
        check = self._mcu._interrupt
        if check is None:
            return None
        self._next_pc = self.state.pc
        result = self._call("interrupt_check", check, self._active)
        if result is None:
            self._rollback()
            self._writes = []
            self._reads = []
            self._warnings = []
            self._step_reads = []
            self._extra = 0
            self._halted = None
            self._next_pc = self.state.pc
            self._observations = []
            return None
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], str)
            or not _is_int(result[1])
            or result[1] < 0
        ):
            self._record_error(
                "interrupt_check", ValueError(f"interrupt_check returned {result!r}")
            )
            raise _Stop("python_error")
        return result

    def _stuck_masks(self, ignored):
        masks = {}
        for target in ignored:
            kind, name, _ = self._target(target)
            if kind != "reg":
                raise TargetError(f"target={target!r}: expected a register or field")
            parts, meta = self._reg_parts(name)
            if len(parts) == 2:
                high, low = meta["fields"][parts[1]]
            else:
                high, low = meta["bits"] - 1, 0
            mask = ((1 << (high - low + 1)) - 1) << low
            masks[parts[0]] = masks.get(parts[0], 0) | mask
        return masks

    def _stuck_write_changed(self, write, masks):
        if write.kind != "reg" or not masks:
            return write.old != write.new
        name, _, field = write.name.partition(".")
        mask = masks.get(name, 0)
        if field and mask:
            _, low = self._mcu._registers[name]["fields"][field]
            mask >>= low
        return bool((write.old ^ write.new) & ~mask)

    def _stuck_check(self, window, pc, interrupt, masks):
        changed = (
            interrupt
            or self._stimulus_applied
            or self._device_written
            or any(self._stuck_write_changed(write, masks) for write in self._writes)
        )
        for space, addr, value, dynamic in self._step_reads:
            if dynamic:
                key = (space, addr)
                if key not in self._last_values or self._last_values[key] != value:
                    changed = True
                self._last_values[key] = value
        pending = any(
            e["kind"] == "at_cycle" and not e["fired"] or e.get("pending")
            for e in self._stimuli
        )
        time_dependent = any(
            getattr(e["obj"], "time_dependent", False) for e in self._devices
        )
        if changed or pending or time_dependent:
            self._quiet = []
            return None
        self._quiet.append((pc, [(s, a, v) for s, a, v, _ in self._step_reads]))
        self._quiet = self._quiet[-window:]
        if len(self._quiet) < window:
            return None
        pcs = [item[0] for item in self._quiet]
        if len(pcs) == window and len(dict.fromkeys(pcs)) < len(pcs):
            reads = list(
                dict.fromkeys(item for _, items in self._quiet for item in items)
            )
            return StuckReport(sorted(dict.fromkeys(pcs)), reads)
        return None
