"""Public type contracts, independent of implementation module layout."""

import inspect
import os
import types
import typing
from collections.abc import Callable, Sequence
from typing import TextIO

import pytest

from rpkbin.isasim import (
    Assembly,
    MCU,
    Device,
    Event,
    RunResult,
    Simulator,
    Snapshot,
    load_mcu,
)
from rpkbin.isasim.repl import run_repl
from rpkbin.isasim.state import IrqAccess, MemAccess, RegAccess, SpaceAccess, State
from rpkbin.isasim.stimulus import Trigger

_CONTRACTS = [
    (Simulator.__init__, {"mcu": MCU, "trace_reads": bool, "history": int}, None),
    (
        Simulator.load_hex,
        {"path": str | os.PathLike[str], "space": str | None, "order": str | None},
        None,
    ),
    (
        Simulator.load_bin,
        {
            "path": str | os.PathLike[str],
            "base": int,
            "space": str | None,
            "order": str | None,
        },
        None,
    ),
    (Simulator.load_cells, {"space": str, "cells": dict[int, int]}, None),
    (Simulator.load_symbols, {"path": str | os.PathLike[str]}, None),
    (Simulator.label_at, {"addr": int}, tuple[str, int] | None),
    (Simulator.source_line, {"addr": int}, tuple[str, int] | None),
    (Simulator.reset, {}, None),
    (Simulator.step, {"n": int}, RunResult),
    (
        Simulator.run,
        {
            "max_steps": int,
            "max_cycles": int | None,
            "stuck_window": int,
            "raise_exceptions": bool,
        },
        RunResult,
    ),
    (Simulator.read, {"target": str}, int),
    (Simulator.poke, {"target": str, "value": int}, None),
    (Simulator.attach, {"device": object, "space": str, "lo": int, "hi": int}, None),
    (Simulator.read_seq, {"target": str, "values": Sequence[int]}, None),
    (Simulator.at_cycle, {"n": int}, Trigger),
    (Simulator.at_pc, {"where": int | str, "times": int | None}, Trigger),
    (Simulator.on_read, {"target": str, "nth": int}, Trigger),
    (Simulator.on_write, {"target": str, "nth": int}, Trigger),
    (Simulator.list_stimuli, {}, list[str]),
    (Simulator.clear_stimuli, {}, None),
    (
        Simulator.add_breakpoint,
        {"where": int | str, "condition": str | None, "temporary": bool},
        int,
    ),
    (
        Simulator.add_watchpoint,
        {"target": str, "kind": str, "condition": str | None},
        int,
    ),
    (Simulator.delete, {"which": int | str}, None),
    (Simulator.list_breakpoints, {}, list[str]),
    (Simulator.add_display, {"expr": str}, int),
    (Simulator.remove_display, {"display_id": int}, None),
    (Simulator.evaluate, {"expr": str}, int),
    (
        Simulator.disassemble,
        {"where": int | str, "count": int},
        list[tuple[int, str, int]],
    ),
    (Simulator.snapshot, {}, Snapshot),
    (Simulator.restore, {"snap": Snapshot}, None),
    (Simulator.subscribe, {"fn": Callable[[Event], None]}, None),
    (Simulator.enable_trace, {"path": str | os.PathLike[str], "fmt": str}, None),
    (Simulator.disable_trace, {}, None),
    (Simulator.recent_events, {"n": int}, list[Event]),
    (State.add_cycles, {"n": int}, None),
    (State.halt, {"reason": str}, None),
    (State.fault, {"code": str, "message": str}, None),
    (SpaceAccess.__getitem__, {"addr": int}, int),
    (SpaceAccess.__setitem__, {"addr": int, "value": int}, None),
    (SpaceAccess.read_word, {"addr": int, "n": int, "order": str}, int),
    (SpaceAccess.write_word, {"addr": int, "n": int, "value": int, "order": str}, None),
    (Trigger.delay, {"cycles": int}, Trigger),
    (Trigger.set, {"target": str, "value": int}, None),
    (Trigger.irq, {"line": str, "level": int}, None),
    (Trigger.call, {"fn": Callable[[Simulator], None]}, None),
    (Device.read, {"addr": int, "now": int}, int),
    (Device.write, {"addr": int, "value": int, "now": int}, None),
    (Device.tick, {"cycles": int, "now": int}, None),
    (Device.reset, {}, None),
    (Device.get_state, {}, dict[str, object]),
    (Device.set_state, {"state": dict[str, object]}, None),
    (Snapshot.save, {"path": str | os.PathLike[str]}, None),
    (Snapshot.load, {"path": str | os.PathLike[str]}, Snapshot),
    (Assembly.save, {"path": str | os.PathLike[str]}, None),
    (Assembly.load, {"mcu": MCU, "path": str | os.PathLike[str]}, Assembly),
    (load_mcu, {"path": str | os.PathLike[str]}, MCU),
    (run_repl, {"sim": Simulator, "stdin": TextIO, "stdout": TextIO}, None),
]


def _normalize(annotation, namespace):
    if isinstance(annotation, str):
        return _normalize(eval(annotation, namespace), namespace)
    if isinstance(annotation, typing.ForwardRef):
        return _normalize(annotation.__forward_arg__, namespace)
    if isinstance(annotation, list):
        return tuple(_normalize(item, namespace) for item in annotation)
    if annotation is None:
        return type(None)
    origin = typing.get_origin(annotation)
    if origin in (types.UnionType, typing.Union):
        return "union", tuple(
            sorted(
                (_normalize(item, namespace) for item in typing.get_args(annotation)),
                key=repr,
            )
        )
    if origin is not None:
        return origin, tuple(
            _normalize(item, namespace) for item in typing.get_args(annotation)
        )
    return annotation


@pytest.mark.parametrize(
    "fn,parameters,returns",
    _CONTRACTS,
    ids=[fn.__qualname__ for fn, _, _ in _CONTRACTS],
)
def test_public_api_annotations(fn, parameters, returns):
    actual = inspect.get_annotations(fn, eval_str=False)
    expected = dict(parameters, **{"return": returns})
    namespace = dict(globals(), **fn.__globals__)
    for name, annotation in expected.items():
        assert name in actual, f"{fn.__qualname__}: missing annotation {name}"
        assert _normalize(actual[name], namespace) == _normalize(
            annotation, namespace
        ), f"{fn.__qualname__}: annotation {name}"


@pytest.mark.parametrize(
    "owner,expected",
    [
        (
            State,
            {
                "reg": RegAccess,
                "mem": MemAccess,
                "irq": IrqAccess,
                "pc": int,
                "next_pc": int,
                "cycle": int,
            },
        ),
        (
            Simulator,
            {
                "state": State,
                "cycle": int,
                "steps_total": int,
                "instructions_total": int,
            },
        ),
        (Device, {"time_dependent": bool}),
    ],
)
def test_public_state_attribute_annotations(owner, expected):
    actual = inspect.get_annotations(owner, eval_str=False)
    namespace = globals()
    for name, annotation in expected.items():
        assert name in actual, f"{owner.__name__}: missing annotation {name}"
        assert _normalize(actual[name], namespace) == annotation
