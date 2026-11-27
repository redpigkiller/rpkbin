"""Register bit overlap, IRQ contract matching and observed-value snapshots."""

from io import StringIO

import pytest

from rpkbin.isasim import MCU, Simulator
from rpkbin.isasim.repl import run_repl


def _machine(probe, *, initial=0x00FF, check=None):
    mcu = MCU("overlap")
    mcu.space(
        "prog",
        cell_bits=8,
        size=4,
        access="ro",
        reset_state="uninitialized",
        init=0,
        is_program=True,
    )
    mcu.space(
        "cells", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0
    )
    mcu.reg("cursor", bits=3, reset=0, is_pc=True)
    mcu.reg("P", bits=16, reset=initial, fields={"hi": (15, 8), "lo": (7, 0)})
    mcu.irq_line("line")
    mcu.irq_line("other")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")
    mcu.instr("00000000", syntax="PROBE", cycles=1)(probe)

    @mcu.instr("00000001", syntax="END", cycles=1)
    def end(st):
        st.halt("done")

    if check is not None:
        mcu.interrupt_check(check)
    sim = Simulator(mcu)
    sim.load_cells("prog", {0: 0, 1: 1})
    return sim


def _write(target, value):
    def probe(st):
        st.reg[target] = value

    return probe


def _read(target):
    def probe(st):
        _ = st.reg[target]

    return probe


def _nothing(st):
    pass


def _hit(sim, target, access, old, new):
    result = sim.step()
    assert (result.stop_reason, result.steps, result.cycle, result.pc) == (
        "watchpoint",
        1,
        1,
        1,
    )
    assert result.detail == {
        "id": 1,
        "target": target,
        "access": access,
        "pc": 0,
        "old": old,
        "new": new,
    }


def test_W1_whole_write_hits_low_field():
    sim = _machine(_write("P", 0x0100))
    sim.add_watchpoint("P.lo", kind="w")
    _hit(sim, "P.lo", "w", 0xFF, 0x00)


def test_W2_field_write_hits_whole():
    sim = _machine(_write("P.hi", 0xAB))
    sim.add_watchpoint("P", kind="w")
    _hit(sim, "P", "w", 0x00FF, 0xABFF)


def test_W3_high_write_does_not_hit_low():
    sim = _machine(_write("P.hi", 0xAB))
    sim.add_watchpoint("P.lo", kind="w")
    assert sim.step().stop_reason == "step_done"


def test_W4_low_write_does_not_hit_high():
    sim = _machine(_write("P.lo", 0x12))
    sim.add_watchpoint("P.hi", kind="w")
    assert sim.step().stop_reason == "step_done"


def test_W5_whole_read_hits_low_field():
    sim = _machine(_read("P"), initial=0x1234)
    sim.add_watchpoint("P.lo", kind="r")
    _hit(sim, "P.lo", "r", None, 0x34)


def test_W6_field_read_hits_whole():
    sim = _machine(_read("P.hi"), initial=0xABCD)
    sim.add_watchpoint("P", kind="r")
    _hit(sim, "P", "r", None, 0xABCD)


def test_W7_low_read_write_does_not_hit_high():
    def probe(st):
        st.reg["P.lo"] = st.reg["P.lo"]

    sim = _machine(probe)
    sim.add_watchpoint("P.hi", kind="rw")
    assert sim.step().stop_reason == "step_done"


def test_W8_first_registered_watch_wins():
    sim = _machine(_write("P", 0x0100))
    sim.add_watchpoint("P", kind="w")
    sim.add_watchpoint("P.lo", kind="w")
    _hit(sim, "P", "w", 0x00FF, 0x0100)


def test_W9_first_write_snapshot_wins():
    def probe(st):
        st.reg["P.hi"] = 0x11
        st.reg["P.lo"] = 0x22

    sim = _machine(probe)
    sim.add_watchpoint("P", kind="w")
    _hit(sim, "P", "w", 0x00FF, 0x11FF)
    assert sim.read("P") == 0x1122


def test_W10_condition_uses_completed_state():
    sim = _machine(_write("P", 0x0100))
    sim.add_watchpoint("P.lo", kind="w", condition="r.P.lo == 0")
    _hit(sim, "P.lo", "w", 0xFF, 0x00)


def test_W11_poke_does_not_hit():
    sim = _machine(_nothing)
    sim.add_watchpoint("P.lo", kind="w")
    sim.poke("P", 0x0100)
    assert sim.step().stop_reason == "step_done"
    assert sim.read("P") == 0x0100


def test_W12_discarded_interrupt_write_does_not_hit():
    def check(st):
        st.reg["P"] = 0x0100

    sim = _machine(_nothing, check=check)
    sim.add_watchpoint("P.lo", kind="w")
    assert sim.step().stop_reason == "step_done"
    assert sim.read("P") == 0x00FF


def test_W13_repl_field_watch_hits_whole_write():
    sim = _machine(_write("P", 0x0100))
    output = StringIO()
    run_repl(sim, StringIO("watch P.lo w\nrun\nquit\n"), output)
    assert "Stopped: watchpoint" in output.getvalue()


@pytest.mark.parametrize("target,expected", [("P", 0xABCD), ("P.lo", 0xCD)])
def test_read_snapshot_precedes_later_write(target, expected):
    def probe(st):
        _ = st.reg["P.hi"] if target == "P" else st.reg["P"]
        st.reg["P"] = 0

    sim = _machine(probe, initial=0xABCD)
    sim.add_watchpoint(target, kind="rw")
    _hit(sim, target, "r", None, expected)
    assert sim.read("P") == 0


def test_field_registered_first_wins_over_whole():
    sim = _machine(_write("P", 0x0100))
    sim.add_watchpoint("P.lo", kind="w")
    sim.add_watchpoint("P", kind="w")
    _hit(sim, "P.lo", "w", 0xFF, 0)


def test_entering_interrupt_access_is_observed():
    def check(st):
        st.reg["P.hi"] = 0xAB
        return "entry", 2

    sim = _machine(_nothing, check=check)
    sim.add_watchpoint("P", kind="w")
    result = sim.step()
    assert (result.stop_reason, result.steps, result.cycle) == ("watchpoint", 1, 2)
    assert (result.detail["old"], result.detail["new"]) == (0x00FF, 0xABFF)


def test_stimulus_and_faulted_access_do_not_hit():
    def probe(st):
        st.reg["P"] = 0xABCD
        st.fault("abort")

    sim = _machine(probe)
    sim.add_watchpoint("P.lo", kind="w")
    sim.at_cycle(0).set("P", 0x0100)
    result = sim.step()
    assert (result.stop_reason, result.steps) == ("fault", 0)
    assert sim.read("P") == 0x0100


@pytest.mark.parametrize("target", ["cells[1]", "cells[0:2]", "irq.line"])
def test_cell_range_and_irq_targets_match(target):
    def probe(st):
        if target.startswith("cells"):
            st.mem["cells"][1] = 3
        else:
            st.irq["line"] = 1

    sim = _machine(probe)
    sim.add_watchpoint(target, kind="w")
    _hit(sim, target, "w", 0, 3 if target.startswith("cells") else 1)


def test_irq_w_matches_and_keeps_event_name():
    def probe(st):
        st.irq["line"] = 1

    sim = _machine(probe)
    sim.add_watchpoint("irq.line", kind="w")
    _hit(sim, "irq.line", "w", 0, 1)
    instr = next(e for e in sim.recent_events(10) if e.kind == "instr")
    assert [(w.name, w.old, w.new) for w in instr.writes] == [("irq.line", 0, 1)]


def test_irq_r_matches():
    def probe(st):
        _ = st.irq["line"]

    sim = _machine(probe)
    sim.poke("irq.line", 1)
    sim.add_watchpoint("irq.line", kind="r")
    _hit(sim, "irq.line", "r", None, 1)


@pytest.mark.parametrize("access", ["r", "w"])
def test_irq_rw_matches_both_accesses(access):
    def probe(st):
        if access == "r":
            _ = st.irq["line"]
        else:
            st.irq["line"] = 1

    sim = _machine(probe)
    sim.add_watchpoint("irq.line", kind="rw")
    _hit(
        sim, "irq.line", access, None if access == "r" else 0, 0 if access == "r" else 1
    )


def test_irq_other_line_does_not_hit():
    def probe(st):
        st.irq["other"] = 1

    sim = _machine(probe)
    sim.add_watchpoint("irq.line", kind="w")
    assert sim.step().stop_reason == "step_done"


@pytest.mark.parametrize("source", ["poke", "stimulus"])
def test_irq_external_writes_do_not_hit(source):
    sim = _machine(_nothing)
    sim.add_watchpoint("irq.line", kind="w")
    if source == "poke":
        sim.poke("irq.line", 1)
    else:
        sim.at_cycle(0).irq("line", 1)
    assert sim.step().stop_reason == "step_done"
    assert sim.read("irq.line") == 1


@pytest.mark.parametrize("enter", [False, True])
def test_irq_interrupt_observations_respect_commit(enter):
    def check(st):
        st.irq["line"] = 1
        return ("entry", 2) if enter else None

    sim = _machine(_nothing, check=check)
    sim.add_watchpoint("irq.line", kind="w")
    result = sim.step()
    if enter:
        assert (result.stop_reason, result.steps, result.cycle) == ("watchpoint", 1, 2)
        assert result.detail == {
            "id": 1,
            "target": "irq.line",
            "access": "w",
            "pc": 0,
            "old": 0,
            "new": 1,
        }
    else:
        assert (result.stop_reason, sim.read("irq.line")) == ("step_done", 0)


def test_irq_repl_watch_hits():
    def probe(st):
        st.irq["line"] = 1

    sim = _machine(probe)
    output = StringIO()
    run_repl(sim, StringIO("watch irq.line w\nrun\nquit\n"), output)
    assert "Stopped: watchpoint" in output.getvalue()


@pytest.mark.parametrize("first", ["P.lo", "irq.line"])
def test_irq_and_register_registration_order_is_shared(first):
    def probe(st):
        st.reg["P"] = 0x0100
        st.irq["line"] = 1

    sim = _machine(probe)
    sim.add_watchpoint(first, kind="w")
    sim.add_watchpoint("irq.line" if first == "P.lo" else "P.lo", kind="w")
    _hit(sim, first, "w", 0xFF if first == "P.lo" else 0, 0 if first == "P.lo" else 1)


def test_irq_read_snapshot_precedes_later_write():
    def probe(st):
        _ = st.irq["line"]
        st.irq["line"] = 1

    sim = _machine(probe)
    sim.add_watchpoint("irq.line", kind="rw")
    _hit(sim, "irq.line", "r", None, 0)
    assert sim.read("irq.line") == 1
