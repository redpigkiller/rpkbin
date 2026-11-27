"""Breakpoint preflight shares S0 matching while leaving execution untouched."""

from pathlib import Path

import pytest

from rpkbin import isasim as isa

from .test_assembler import machine


def _loop_sim():
    mcu = machine()
    mcu.irq_line("wake")

    @mcu.interrupt_check
    def check(st):
        st.reg["value"] += 1
        return ("entry", 1)

    sim = isa.Simulator(mcu)
    sim.load_assembly(isa.assemble(mcu, "GO 0"))
    sim.poke("value", 3)
    sim.poke("irq.wake", 1)
    sim.at_cycle(0).set("value", 4)
    return sim


def _state(sim, path):
    sim.snapshot().save(path)
    return (
        path.read_bytes(),
        sim.recent_events(100),
        sim.list_breakpoints(),
        sim.cycle,
        sim.steps_total,
    )


def test_preflight_is_read_only_and_run_consumes_the_temporary_point(tmp_path):
    def configure(sim):
        return sim.add_breakpoint(
            0,
            condition=(
                "pc == 0 and i.wake == 1 and m.prog[0] == 0x30 "
                "and r.value == (3 if cycle == 0 else 5)"
            ),
            temporary=True,
        )

    sim = _loop_sim()
    control = _loop_sim()
    first = configure(sim)
    assert first == configure(control)
    path = tmp_path / "state.json"
    before = _state(sim, path)
    assert sim.next_breakpoint() == first
    assert sim.next_breakpoint() == first
    assert _state(sim, path) == before

    result = sim.run(max_steps=3)
    assert result == control.run(max_steps=3)
    assert (result.stop_reason, result.detail, result.cycle) == (
        "breakpoint",
        {"id": first, "pc": 0},
        1,
    )
    assert sim.read("value") == 5
    assert sim.list_breakpoints() == []
    assert sim.next_breakpoint() is None
    assert sim.add_breakpoint(0) == control.add_breakpoint(0) == first + 1
    assert _state(sim, path) == _state(control, path)


def test_step_still_ignores_breakpoints_after_preflight(tmp_path):
    sim, control = _loop_sim(), _loop_sim()
    point = sim.add_breakpoint(0)
    control.add_breakpoint(0)
    assert sim.next_breakpoint() == point
    assert sim.step() == control.step()
    path = tmp_path / "state.json"
    assert _state(sim, path) == _state(control, path)
    assert sim.next_breakpoint() == point


def test_preflight_skips_unmatched_addresses_watches_and_false_conditions():
    sim = isa.Simulator(machine())
    sim.add_breakpoint(1, condition="1 // 0")
    sim.add_watchpoint("value", kind="rw", condition="1 // 0")
    sim.add_breakpoint(0, condition="0")
    assert sim.next_breakpoint() is None
    first = sim.add_breakpoint(0)
    sim.add_breakpoint(0, condition="1 // 0")
    assert sim.next_breakpoint() == first


def test_preflight_raises_the_same_first_condition_error_as_s0():
    sim = isa.Simulator(machine())
    sim.load_assembly(isa.assemble(machine(), "GO 0"))
    sim.add_breakpoint(0, condition="1 // 0")
    sim.add_breakpoint(0)
    with pytest.raises(isa.DebugError, match="1 // 0") as preflight:
        sim.next_breakpoint()
    with pytest.raises(isa.DebugError) as actual:
        sim.run(max_steps=2)
    assert str(preflight.value) == str(actual.value)


def test_source_group_checks_all_registered_addresses_after_code_changes():
    mcu = machine()
    bundle = Path(__file__).with_name("fixtures") / "v11-assembly-2.json"
    sim = isa.Simulator(mcu)
    sim.load_assembly(isa.Assembly.load(mcu, bundle))
    point = sim.add_breakpoint("part.inc:1", condition="r.value == 2")
    sim.poke("prog[0]", 0x38)  # GO 8
    with pytest.raises(isa.TargetError, match="stale"):
        sim.add_breakpoint("part.inc:1")

    sim.poke("value", 2)
    for addr in (1, 8):
        sim.poke("cursor", addr)
        assert sim.next_breakpoint() == point

    sim.poke("cursor", 0)
    assert sim.next_breakpoint() is None
    result = sim.run(max_steps=2)
    assert (result.stop_reason, result.detail) == ("breakpoint", {"id": point, "pc": 8})
