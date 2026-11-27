"""Boundary fetch results computed independently of the engine."""

import pytest

from rpkbin.isasim import Simulator

from .ref_toy_c import Ref
from .toys.toy_c import make_mcu


@pytest.mark.parametrize(
    "pc,program,text", [(100, {}, "<undefined>"), (99, {99: 0x400}, "JZ 1")]
)
def test_ignore_fetch_hand_computed(pc, program, text):
    sim = Simulator(make_mcu(oor="ignore"))
    sim.load_cells("prog", program)
    sim.poke("pc", pc)
    events = []
    sim.subscribe(events.append)
    result = sim.step()
    assert (result.stop_reason, result.pc, result.cycle, result.steps) == (
        "step_done",
        102,
        3,
        1,
    )
    assert sim.read("w") == 0
    assert next(e.text for e in events if e.kind == "instr") == text
    expected = {"out_of_range", "undefined_opcode"} if pc == 100 else {"out_of_range"}
    assert {e.code for e in events if e.kind == "warning"} == expected


@pytest.mark.parametrize("pc,program", [(100, {}), (99, {99: 0x400})])
def test_reference_handles_ignore_fetch(pc, program):
    ref = Ref(program, oor="ignore", pc=pc)
    outcome = ref.step()
    assert (outcome.stop, ref.pc, ref.cycle, ref.steps) == ("step_done", 102, 3, 1)
    assert ref.fetch_out_of_range is True
    assert "out_of_range" in outcome.warns
