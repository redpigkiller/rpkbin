"""Register aliases and stuck detection with ignored bit masks."""

from io import StringIO

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.cli import main
from rpkbin.isasim.repl import run_repl

from .test_assembler import cli_source as cli_source
from .test_assembler import machine


def _machine(*, aliases=True):
    mcu = machine()
    mcu.reg("word", bits=4, reset=0, fields={"High": (3, 2), "Low": (1, 0)})
    if aliases:
        mcu.alias("Whole", "word")
        mcu.alias("LowAlias", "word.Low")
    return mcu


def test_aliases_read_write_expression_watch_and_repl():
    mcu = _machine()

    @mcu.instr("1000 0000", syntax="PUT", cycles=1)
    def put(st):
        assert st.reg["Whole.High"] == 3
        st.reg["LowAlias"] = 2

    sim = isa.Simulator(mcu)
    sim.load_assembly(isa.assemble(mcu, "PUT\nEND"))
    sim.poke("Whole.High", 3)
    assert sim.read("word") == 12
    assert sim.evaluate("r.Whole.High + r.LowAlias") == 3
    assert sim.add_watchpoint("LowAlias", kind="w", condition="r.Whole.Low == 2") == 1
    assert sim.step().stop_reason == "watchpoint"
    assert sim.read("Whole.Low") == 2 and sim.read("word") == 14
    writes = next(
        event.writes for event in sim.recent_events(10) if event.kind == "instr"
    )
    assert [(w.name, w.old, w.new, w.is_field) for w in writes] == [
        ("word.Low", 0, 2, True)
    ]
    output = StringIO()
    run_repl(sim, StringIO("info\nregs\nprint r.LowAlias\nquit\n"), output)
    assert "Whole" in output.getvalue() and "word.Low" in output.getvalue()
    assert dict(mcu.aliases) == {"Whole": "word", "LowAlias": "word.Low"}
    with pytest.raises(isa.TargetError):
        sim.read("LowAlias.High")


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("_hidden", "word"),
        ("other", "Whole"),
        ("other", "cursor"),
        ("other", "cursor.Field"),
        ("word", "word.Low"),
        ("prog", "word.Low"),
        ("Whole", "word.High"),
        ("other", "word.Unknown"),
        ("other", "Unknown"),
    ],
)
def test_invalid_alias_declarations(name, target):
    with pytest.raises(isa.MCUDefinitionError):
        _machine().alias(name, target)


def test_alias_signature_matches_only_identical_configuration(tmp_path):
    with_aliases = isa.Simulator(_machine())
    plain = isa.Simulator(_machine(aliases=False))
    plain.restore(plain.snapshot())
    with pytest.raises(isa.SnapshotError, match="signature"):
        with_aliases.restore(plain.snapshot())
    with pytest.raises(isa.SnapshotError, match="signature"):
        plain.restore(with_aliases.snapshot())
    path = tmp_path / "snapshot.json"
    with_aliases.poke("LowAlias", 2)
    with_aliases.snapshot().save(path)
    restored = isa.Simulator(_machine())
    restored.restore(isa.Snapshot.load(path))
    assert restored.read("LowAlias") == 2
    bundle = tmp_path / "plain.isa.json"
    isa.assemble(_machine(aliases=False), "END").save(bundle)
    assert isa.Assembly.load(_machine(), bundle).cells == {0: 240}


@pytest.mark.parametrize(
    ("write_kind", "ignored", "reason"),
    [
        ("whole", ("LowAlias",), "stuck"),
        ("field", ("Whole.Low",), "stuck"),
        ("whole", ("word.High",), "max_steps"),
        ("mixed", ("LowAlias",), "max_steps"),
        ("restore", (), "max_steps"),
        ("restore", ("LowAlias",), "stuck"),
    ],
)
def test_stuck_masks_each_write_independently(write_kind, ignored, reason):
    mcu = _machine()

    @mcu.instr("1000 0000", syntax="LOOP", cycles=1)
    def loop(st):
        if write_kind == "field":
            st.reg["LowAlias"] ^= 1
        elif write_kind == "restore":
            st.reg["Whole.Low"] = 1
            st.reg["LowAlias"] = 0
        else:
            st.reg["Whole"] ^= 5 if write_kind == "mixed" else 1
        st.next_pc = 0

    sim = isa.Simulator(mcu)
    sim.load_assembly(isa.assemble(mcu, "LOOP"))
    assert (
        sim.run(max_steps=8, stuck_window=3, stuck_ignore=ignored).stop_reason == reason
    )


def test_stuck_unknown_target_and_cli_option(cli_source, capsys):
    sim = isa.Simulator(_machine())
    with pytest.raises(isa.TargetError, match="unknown register"):
        sim.run(max_steps=3, stuck_ignore=("Missing",))
    model, source = cli_source
    assert (
        main(
            [
                "run",
                str(model),
                str(source),
                "--max-steps",
                "8",
                "--stuck-ignore",
                "value",
            ]
        )
        == 0
    )
    assert "stop_reason=halted" in capsys.readouterr().out
