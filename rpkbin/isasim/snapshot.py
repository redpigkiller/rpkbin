"""Immutable checkpoint values and explicit configuration compatibility."""

from __future__ import annotations

import json as _json
import os
import re as _re
from copy import deepcopy as _deepcopy

from .errors import SnapshotError
from .events import _WIDE_INT_LIMIT, _json_safe_value
from .stimulus import Device as _Device

_WIDE_HEX_RE = _re.compile(r"-?0x[1-9a-f][0-9a-f]*")


def _decode_wide_ints(value):
    if (
        isinstance(value, str)
        and len(value) >= 500
        and _WIDE_HEX_RE.fullmatch(value) is not None
    ):
        parsed = int(value, 16)
        return parsed if abs(parsed) >= _WIDE_INT_LIMIT else value
    if isinstance(value, list):
        return [_decode_wide_ints(item) for item in value]
    if isinstance(value, dict):
        return {key: _decode_wide_ints(val) for key, val in value.items()}
    return value


class Snapshot:
    __slots__ = ("_data",)

    def __init__(self, _data):
        object.__setattr__(self, "_data", _deepcopy(_data))

    def __repr__(self) -> str:
        mcu_name = self._data.get("mcu_name")
        state = self._data.get("state", {})
        return f"Snapshot(mcu={mcu_name!r}, cycle={state.get('cycle')}, steps={state.get('steps')})"

    def __setattr__(self, name, value):
        raise AttributeError("Snapshot is immutable")

    def save(self, path: str | os.PathLike[str]) -> None:
        try:
            payload = _json.dumps(_json_safe_value(self._data), separators=(",", ":"))
            with open(path, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
        except (OSError, TypeError, ValueError) as error:
            raise SnapshotError(f"path={path!r}: {error}") from error

    @staticmethod
    def load(path: str | os.PathLike[str]) -> Snapshot:
        try:
            with open(path, encoding="utf-8-sig") as stream:
                data = _json.load(stream)
        except (OSError, ValueError) as error:
            raise SnapshotError(f"path={path!r}: {error}") from error
        # JSON object order is not required for external checkpoint files.
        if (
            not isinstance(data, dict)
            or list(data.keys()) != ["mcu_name", "signature", "state"]
        ) and (
            not isinstance(data, dict)
            or len(data) != 3
            or any(key not in data for key in ("mcu_name", "signature", "state"))
        ):
            raise SnapshotError(f"path={path!r}: invalid top-level keys")
        if (
            not isinstance(data["mcu_name"], str)
            or not isinstance(data["signature"], dict)
            or not isinstance(data["state"], dict)
        ):
            raise SnapshotError(f"path={path!r}: invalid checkpoint types")
        data["signature"] = _decode_wide_ints(data["signature"])
        data["state"] = _decode_wide_ints(data["state"])
        return Snapshot(data)


class _Snapshots:
    def _stimulus_runtime(self, runtime):
        if not isinstance(runtime, list) or len(runtime) != len(self._stimuli):
            raise ValueError(f"stimuli={runtime!r}: invalid runtime list")
        validated = []
        for index, (entry, item) in enumerate(zip(self._stimuli, runtime)):
            keys = (
                ("position",)
                if entry["kind"] == "read_seq"
                else ("seen", "fired", "pending")
            )
            if (
                not isinstance(item, dict)
                or len(item) != len(keys)
                or any(key not in item for key in keys)
            ):
                raise ValueError(
                    f"stimuli[{index}]={item!r}: expected runtime keys {keys!r}"
                )
            for key in keys:
                value = item[key]
                if key == "pending":
                    valid = isinstance(value, list) and all(
                        type(cycle) is int and cycle >= 0 for cycle in value
                    )
                else:
                    valid = type(value) is int and value >= 0
                if not valid:
                    raise ValueError(
                        f"stimuli[{index}].{key}={value!r}: invalid runtime value"
                    )
            validated.append({key: _deepcopy(item[key]) for key in keys})
        return validated

    def _signature(self):
        signature = {
            "spaces": [
                [name, s["cell_bits"], s["size"], s["is_program"]]
                for name, s in self._mcu._spaces.items()
            ],
            "registers": [
                [
                    name,
                    r["bits"],
                    r["is_pc"],
                    [[key, high, low] for key, (high, low) in r["fields"].items()],
                ]
                for name, r in self._mcu._registers.items()
            ],
            "irq_lines": list(self._mcu._irqs),
            "devices": [
                [e["space"], e["lo"], e["hi"], type(e["obj"]).__name__]
                for e in self._devices
            ],
            "stimuli": [self._stimulus_signature(e) for e in self._stimuli],
        }
        if self._mcu._aliases:
            signature["aliases"] = [list(item) for item in self._mcu._aliases.items()]
        if self._mem_aliases:
            signature["mem_aliases"] = [
                [
                    info.name,
                    info.target,
                    info.space,
                    info.lo,
                    info.hi,
                    info.bit_hi,
                    info.bit_lo,
                    info.endian,
                    info.bit_order,
                    info.bits,
                ]
                for info in self._mem_aliases.values()
            ]
        regions = [
            [name, [list(region) for region in space["regions"]]]
            for name, space in self._mcu._spaces.items()
            if space.get("regions")
        ]
        if regions:
            signature["regions"] = regions
        return signature

    def _device_state(self, device):
        get = getattr(device, "get_state", None)
        put = getattr(device, "set_state", None)
        if (
            not callable(get)
            or not callable(put)
            or getattr(put, "__func__", None) is _Device.set_state
        ):
            raise SnapshotError(
                f"device={type(device).__name__}: get_state/set_state required"
            )
        try:
            return get()
        except NotImplementedError as error:
            raise SnapshotError(
                f"device={type(device).__name__}: get_state/set_state required"
            ) from error

    def snapshot(self) -> Snapshot:
        device_states = [self._device_state(e["obj"]) for e in self._devices]
        runtime = []
        for entry in self._stimuli:
            runtime.append(
                {
                    key: _deepcopy(value)
                    for key, value in entry.items()
                    if key in ("position", "seen", "fired", "pending")
                }
            )
        data = {
            "mcu_name": self._mcu._name,
            "signature": self._signature(),
            "state": {
                "registers": self._registers,
                "memory": self._memory,
                "initialized": self._initialized,
                "irq": self._irq,
                "cycle": self._cycle,
                "steps": self._steps,
                "instructions": self._instructions,
                "stimuli": runtime,
                "devices": device_states,
            },
        }
        return Snapshot(data)

    def restore(self, snap: Snapshot) -> None:
        self._require_idle()
        if not isinstance(snap, Snapshot):
            raise SnapshotError(f"snapshot={snap!r}: expected Snapshot")
        data = _deepcopy(snap._data)
        if data["mcu_name"] != self._mcu._name:
            raise SnapshotError(f"mcu_name={data['mcu_name']!r}: incompatible")
        signature = self._signature()
        actual = data["signature"]
        if actual != signature:
            for key in dict.fromkeys((*signature, *actual)):
                if (
                    key not in actual
                    or key not in signature
                    or actual[key] != signature[key]
                ):
                    raise SnapshotError(
                        f"signature {key}={actual.get(key)!r}: expected {signature.get(key)!r}"
                    )
        state = data["state"]
        try:
            for key in (
                "registers",
                "memory",
                "initialized",
                "irq",
                "cycle",
                "steps",
                "instructions",
                "stimuli",
                "devices",
            ):
                state[key]
            for name, meta in self._mcu._registers.items():
                if type(state["registers"][name]) is not int or not 0 <= state[
                    "registers"
                ][name] < (1 << meta["bits"]):
                    raise ValueError(f"register {name}")
            for name, meta in self._mcu._spaces.items():
                if (
                    len(state["memory"][name]) != meta["size"]
                    or len(state["initialized"][name]) != meta["size"]
                ):
                    raise ValueError(f"space {name}")
                if any(
                    type(v) is not int or not 0 <= v < (1 << meta["cell_bits"])
                    for v in state["memory"][name]
                ):
                    raise ValueError(f"cell {name}")
                if any(type(v) is not bool for v in state["initialized"][name]):
                    raise ValueError(f"initialized {name}")
            if any(
                type(state[key]) is not int or state[key] < 0
                for key in ("cycle", "steps", "instructions")
            ):
                raise ValueError("counters")
            if list(state["irq"]) != list(self._irq) or any(
                v not in (0, 1) for v in state["irq"].values()
            ):
                raise ValueError("irq")
            if len(state["stimuli"]) != len(self._stimuli) or len(
                state["devices"]
            ) != len(self._devices):
                raise ValueError("runtime configuration")
            runtime = self._stimulus_runtime(state["stimuli"])
        except (KeyError, TypeError, ValueError) as error:
            raise SnapshotError(f"state={state!r}: {error}") from error
        prior_states = []
        for entry in self._devices:
            device = entry["obj"]
            put = getattr(device, "set_state", None)
            if (
                not callable(put)
                or getattr(put, "__func__", None) is _Device.set_state
            ):
                raise SnapshotError(
                    f"device={type(device).__name__}: get_state/set_state required"
                )
            try:
                prior = _deepcopy(self._device_state(device))
            except (ValueError, KeyError, TypeError) as error:
                raise SnapshotError(
                    f"device={type(device).__name__}: {error}"
                ) from error
            prior_states.append((device, put, prior))
        applied = []
        for (device, put, prior), device_state in zip(prior_states, state["devices"]):
            try:
                put(_deepcopy(device_state))
            except (NotImplementedError, ValueError, KeyError, TypeError) as error:
                for prev_put, prev_state in reversed(applied):
                    try:
                        prev_put(_deepcopy(prev_state))
                    except Exception:
                        pass
                if isinstance(error, NotImplementedError):
                    raise SnapshotError(
                        f"device={type(device).__name__}: set_state required"
                    ) from error
                raise SnapshotError(
                    f"device={type(device).__name__}: {error}"
                ) from error
            applied.append((put, prior))
        self._registers = state["registers"]
        self._memory = state["memory"]
        self._initialized = state["initialized"]
        self._irq = state["irq"]
        self._cycle = state["cycle"]
        self._steps = state["steps"]
        self._instructions = state["instructions"]
        self._next_pc = self.state.pc
        for entry, item in zip(self._stimuli, runtime):
            if entry["kind"] == "read_seq":
                entry["position"] = item["position"]
            else:
                entry["seen"] = item["seen"]
                entry["fired"] = item["fired"]
                entry["pending"] = item["pending"]
        self._emit(self._event("external", text="restore"))
