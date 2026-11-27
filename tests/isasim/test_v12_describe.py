"""Read-only descriptions of the current validated MCU declaration."""

from collections import UserString
from dataclasses import FrozenInstanceError, asdict, fields
from io import StringIO

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.repl import run_repl

from .test_assembler import machine
from .toys import toy_a, toy_b, toy_c, toy_r


def _with_metadata():
    mcu = machine()
    mcu.space(
        "mem",
        cell_bits=5,
        size=12,
        access="rw",
        regions=[(8, 10, "ro"), (1, 3, "rw")],
        reset_state="initialized",
        init=0,
        role="inout",
    )
    mcu.reg("status", bits=5, reset=9, fields={"H": (4, 2), "Z": 0}, role="flag")
    mcu.irq_line("wake")
    mcu.irq_line("alarm")
    mcu.alias("whole", "status")
    mcu.alias("bitzero", "status.Z")

    @mcu.instr("1110 0000", syntax="ENTER", cycles=1, kind="call")
    def enter(st):
        pass

    return mcu


@pytest.mark.parametrize(
    "factory",
    [
        lambda: toy_a.make_mcu(with_irq=True),
        toy_b.make_mcu,
        toy_c.make_mcu,
        lambda: toy_r.make_toy_r(out_of_range="stop", ro="stop"),
        _with_metadata,
    ],
    ids=["toy-a", "toy-b", "toy-c", "toy-r", "opt-in"],
)
def test_description_matches_repl_and_fixture_declarations(factory):
    mcu = factory()
    sim = isa.Simulator(mcu)
    desc = mcu.describe()
    assert desc == sim.describe() == mcu.describe()
    assert isinstance(desc, isa.MCUDescription)

    text = StringIO()
    run_repl(sim, stdin=StringIO("info\nquit\n"), stdout=text)
    info = text.getvalue()
    (program,) = (space.name for space in desc.spaces if space.is_program)
    assert f"program={program}\n" in info
    for space, declaration in zip(desc.spaces, mcu._spaces.values(), strict=True):
        assert isinstance(space, isa.SpaceInfo)
        expected = {key: value for key, value in declaration.items() if key != "init"}
        expected.setdefault("regions", ())
        assert asdict(space) == expected
        row = f"{space.name} cell_bits={space.cell_bits} size={space.size} role={space.role}"
        if space.regions:
            row += f" regions={list(space.regions)!r}"
        assert row + "\n" in info

    for reg, declaration in zip(desc.registers, mcu._registers.values(), strict=True):
        assert isinstance(reg, isa.RegisterInfo)
        expected = {
            key: declaration[key] for key in ("name", "bits", "reset", "is_pc", "role")
        }
        expected["fields"] = tuple(
            {"name": name, "hi": hi, "lo": lo}
            for name, (hi, lo) in declaration["fields"].items()
        )
        assert asdict(reg) == expected
        assert f"{reg.name} bits={reg.bits} role={reg.role}\n" in info

    assert desc.aliases == tuple(mcu.aliases.items())
    for name, target in desc.aliases:
        assert f"{name} -> {target}\n" in info
    assert desc.irq_lines == tuple(mcu._irqs)
    assert desc.has_instruction_kinds is (factory is _with_metadata)


def test_description_is_deeply_immutable():
    desc = isa.Simulator(_with_metadata()).describe()
    assert desc.spaces[-1] == isa.SpaceInfo(
        "mem", 5, 12, "rw", ((8, 10, "ro"), (1, 3, "rw")), False, "inout", "initialized"
    )
    assert desc.registers[-1] == isa.RegisterInfo(
        "status",
        5,
        9,
        False,
        "flag",
        (isa.FieldInfo("H", 4, 2), isa.FieldInfo("Z", 0, 0)),
    )
    assert desc.aliases == (("whole", "status"), ("bitzero", "status.Z"))
    assert desc.irq_lines == ("wake", "alarm")

    def check(value):
        if isinstance(value, tuple):
            for child in value:
                check(child)
        elif isinstance(
            value, (isa.MCUDescription, isa.SpaceInfo, isa.RegisterInfo, isa.FieldInfo)
        ):
            for field in fields(value):
                child = getattr(value, field.name)
                with pytest.raises(FrozenInstanceError):
                    setattr(value, field.name, child)
                check(child)
        else:
            assert type(value) in (str, int, bool)

    check(desc)


def test_description_rejects_unvalidated_mcu_without_running_validation():
    mcu = machine()  # Complete declaration, but no validating entry point yet.
    with pytest.raises(isa.MCUDefinitionError, match="source_demo.*validat"):
        mcu.describe()
    assert not hasattr(mcu, "_program")


def test_description_requires_revalidation_of_changed_declaration():
    mcu = _with_metadata()
    sim = isa.Simulator(mcu)
    old = sim.describe()

    mcu.irq_line("extra")
    mcu.alias("third", "value")
    for describe in (mcu.describe, sim.describe):
        with pytest.raises(isa.MCUDefinitionError, match="source_demo.*validat"):
            describe()

    validated = isa.Simulator(mcu).describe()
    assert validated == mcu.describe() == sim.describe()
    assert validated.irq_lines == ("wake", "alarm", "extra")
    assert validated.aliases == (
        ("whole", "status"),
        ("bitzero", "status.Z"),
        ("third", "value"),
    )
    assert old.irq_lines == ("wake", "alarm")
    assert old.aliases == (("whole", "status"), ("bitzero", "status.Z"))


def test_failed_validation_keeps_description_unavailable():
    mcu = machine()
    sim = isa.Simulator(mcu)
    old = sim.describe()
    mcu.reg("second_cursor", bits=8, reset=0, is_pc=True)

    with pytest.raises(isa.MCUDefinitionError, match="pc registers=2"):
        isa.Simulator(mcu)
    for describe in (mcu.describe, sim.describe):
        with pytest.raises(isa.MCUDefinitionError, match="source_demo.*validat"):
            describe()
    assert tuple(reg.name for reg in old.registers) == ("cursor", "value")


def test_description_detaches_scalar_subclasses():
    class DeclaredInt(int):
        def __int__(self):
            raise AssertionError("describe must not call declaration code")

    class DeclaredText(str):
        def __str__(self):
            raise AssertionError("describe must not call declaration code")

    bits = DeclaredInt(8)
    name = DeclaredText("extra")
    policy = DeclaredText("initialized")
    bits.notes = []
    name.notes = []
    policy.notes = []

    mcu = machine()
    mcu.reg(name, bits=bits, reset=0)
    mcu.space("mem", cell_bits=8, size=2, access="rw", reset_state=policy, init=0)
    desc = isa.Simulator(mcu).describe()
    bits.notes.append("changed")
    name.notes.append("changed")
    policy.notes.append("changed")

    reg = desc.registers[-1]
    assert type(reg.bits) is int and reg.bits == 8
    assert type(reg.name) is str and reg.name == "extra"
    reset_state = desc.spaces[-1].reset_state
    assert type(reset_state) is str and reset_state == "initialized"


def test_description_detaches_string_like_metadata():
    role = UserString("output")
    mcu = machine()
    mcu.reg("extra", bits=8, reset=0, role=role)
    desc = isa.Simulator(mcu).describe()
    role.data = "input"
    assert desc.registers[-1].role == "output"
