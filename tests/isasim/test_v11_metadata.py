"""Instruction overrides and display metadata."""

from io import StringIO

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.repl import run_repl

from .test_assembler import machine


def _with_overrides(*, extra=None, general_syntax="GENERAL {i}"):
    mcu = machine()

    @mcu.instr(
        "1010 0000",
        syntax="SPECIAL",
        cycles=3,
        overrides=("middle",),
        kind="call",
    )
    def special(st):
        st.reg["value"] = 7

    @mcu.instr(
        "1010 00ii",
        syntax="MIDDLE {i}",
        cycles=2,
        overrides="general",
        kind="return",
    )
    def middle(st, i):
        st.reg["value"] = i + 1

    @mcu.instr("1010 iiii", syntax=general_syntax, cycles=1)
    def general(st, i):
        st.reg["value"] = i

    if extra is not None:
        mcu.instr("1010 0iii", syntax="EXTRA {i}", cycles=1, **extra)(general)
    return mcu


def test_overrides_use_specific_semantics_regardless_of_assembly_template():
    mcu = _with_overrides()
    image = isa.assemble(mcu, "GENERAL 0\nGENERAL 2\nGENERAL 9")
    sim = isa.Simulator(mcu)
    sim.load_assembly(image)
    assert sim.disassemble(0, 3) == [
        (0, "SPECIAL", 1),
        (1, "MIDDLE 2", 1),
        (2, "GENERAL 9", 1),
    ]
    assert [sim.instruction_kind(i) for i in range(3)] == ["call", "return", None]
    for value, cycle in [(7, 3), (3, 5), (9, 6)]:
        sim.step()
        assert (sim.read("value"), sim.cycle) == (value, cycle)


def test_overrides_do_not_disambiguate_source():
    mcu = _with_overrides(general_syntax="MIDDLE {i}")
    with pytest.raises(isa.AssemblyError, match="found 2"):
        isa.assemble(mcu, "MIDDLE 2")


@pytest.mark.parametrize(
    ("pattern", "overrides", "message"),
    [
        ("1000 iiii", (), "overrides"),
        ("1000 iiii", ("add", "add"), "overrides"),
        ("1000 iiii", "other", "overrides"),
        ("1000 iiii", "unknown", "overrides"),
        ("0101 iiii", "mix", "length"),
        ("0001 iiii", "add", "subset|superset"),
        ("1000 iiii", "add", "subset|superset"),
    ],
)
def test_invalid_overrides_are_rejected(pattern, overrides, message):
    mcu = machine()

    @mcu.instr(pattern, syntax="OTHER {i}", cycles=1, overrides=overrides)
    def other(st, i):
        st.reg["value"] = i

    with pytest.raises(isa.MCUDefinitionError, match=message):
        isa.Simulator(mcu)


def test_override_closure_does_not_allow_uncovered_overlap():
    mcu = _with_overrides(extra={"name": "extra", "overrides": "general"})
    with pytest.raises(isa.MCUDefinitionError, match="overlapping instructions"):
        isa.Simulator(mcu)


def test_instruction_kind_uses_peek_and_excludes_source_locations():
    mcu = machine()
    mcu._spaces["prog"]["init"] = 0x80
    mcu.reg("status", bits=1, reset=0, role="flag")

    @mcu.instr("1000 0000", syntax="ENTER", cycles=1, kind="call")
    def enter(st):
        st.halt("done")

    sim = isa.Simulator(mcu)
    assert sim.instruction_kind(0) == "call"
    assert sim.instruction_kind(-1) is None
    assert sim.instruction_kind(64) is None
    assert sim.disassemble(64) == []
    sim.load_assembly(isa.assemble(mcu, "Start: ENTER\n.cell 0", filename="main.asm"))
    assert sim.instruction_kind("Start") == "call"
    assert sim.instruction_kind(1) is None
    with pytest.raises(isa.DebugError):
        sim.instruction_kind("main.asm:1")
    output = StringIO()
    run_repl(sim, StringIO("info\nquit\n"), output)
    assert "status bits=1 role=flag" in output.getvalue()


def test_invalid_instruction_kind():
    mcu = machine()
    with pytest.raises(isa.MCUDefinitionError, match="kind"):
        mcu.instr("1000 0000", syntax="ENTER", cycles=1, kind="jump")(lambda st: None)
