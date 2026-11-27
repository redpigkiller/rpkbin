"""Assembler preprocessing, source maps, and replay tests."""

import json
import os
import subprocess
import sys
from io import StringIO

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.cli import main
from rpkbin.isasim.repl import run_repl

from .test_assembler import cli_source as cli_source
from .test_assembler import machine


@pytest.mark.parametrize("value", [True, False, "1", 1.0])
def test_defines_only_accept_integer_values(value):
    with pytest.raises(isa.AssemblyError, match="VALUE"):
        isa.assemble(machine(), "END", defines={"VALUE": value})


def test_defines_supply_constants_and_preserve_json_integers(tmp_path):
    defines = {"LIMIT": 4, "OFFSET": -2}
    image = isa.assemble(machine(), ".cell LIMIT + OFFSET", defines=defines)
    assert image.cells == {0: 2}
    assert image.constants == defines
    path = tmp_path / "defined.isa.json"
    image.save(path)
    assert json.loads(path.read_bytes())["options"]["defines"] == defines
    with pytest.raises(isa.AssemblyError, match="duplicate"):
        isa.assemble(machine(), "LIMIT EQU 1\nEND", defines=defines)


def test_cli_define_integer_literal_and_reject_expression(cli_source, capsys):
    model, source = cli_source
    source.write_text(".cell X + 4, ENABLED", encoding="utf-8")
    base = ["assemble", str(model), str(source)]
    assert main([*base, "-D", "X=-0b10", "-D", "ENABLED"]) == 0
    path = source.with_suffix(".isa.json")
    assert json.loads(path.read_bytes())["cells"] == [[0, 2], [1, 1]]
    with pytest.raises(SystemExit) as error:
        main([*base, "-D", "X=1<<4"])
    assert error.value.code == 2
    assert "1<<4" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("expr", "truth"),
    [
        ("(2 and 3) == 3", False),
        ("(2 and 3) == 1", True),
        ("(0 or -4) == 1", True),
        ("(0 and 3) == 0", True),
        ("!1 + 1 == 1", True),
        ("not 1 == 2", False),
        ("1 < 2 < 1", False),
        ("3 < 2 < 1", True),
        ("1 != 2 and 2 <= 2 and 3 >= 3 and 4 > 3", True),
        ("0 or 2 + 3 * 4 == 14 and ~0 == -1", True),
    ],
)
def test_condition_operator_results_and_precedence(expr, truth):
    source = f".if {expr}\n.cell 1\n.else\n.cell 2\n.endif"
    assert isa.assemble(machine(), source).cells == {0: 1 if truth else 2}


@pytest.mark.parametrize("expr", ["0 and 1 // 0", "1 or 1 // 0"])
def test_condition_does_not_short_circuit(expr):
    with pytest.raises(isa.AssemblyError, match="division by zero"):
        isa.assemble(machine(), f".if {expr}\nEND\n.endif")


def test_condition_visibility_and_inactive_branch(tmp_path):
    source = (
        "A = B\nB EQU 1\n.if A\n.ifdef A\n.cell A\n.endif\n"
        '.elseif 1 // 0\n.include "missing.inc"\n.endif\n'
        ".ifndef C\n.cell 2\n.endif"
    )
    assert isa.assemble(machine(), source, include_paths=[tmp_path]).cells == {
        0: 1,
        1: 2,
    }
    assert isa.assemble(
        machine(),
        '.if 0\n.include "missing.inc"\n.if 1 // 0\n.endif\n.endif\nEND',
        include_paths=[tmp_path],
    ).cells == {0: 240}
    with pytest.raises(isa.AssemblyError, match="B"):
        isa.assemble(machine(), "A = B\n.if A\nEND\n.endif\nB=1")
    with pytest.raises(isa.AssemblyError, match="Start"):
        isa.assemble(machine(), "Start: END\n.if Start\nEND\n.endif")
    with pytest.raises(isa.AssemblyError):
        isa.assemble(machine(), ".cell 1 == 1")


def test_include_search_normalization_and_source_maps(tmp_path):
    local = tmp_path / "local"
    search = tmp_path / "search"
    local.mkdir()
    search.mkdir()
    (search / "piece;v.inc").write_text(".cell 9", encoding="utf-8")
    (local / "piece;v.inc").write_bytes(b"\xef\xbb\xbfADD 1\r\n")
    image = isa.assemble(
        machine(),
        '.include "piece;v.inc" ; comment\n.include "piece;v.inc"\nEND\nGO 0',
        filename=str(local / "main.asm"),
        include_paths=[search],
    )
    assert image.cells == {0: 17, 1: 17, 2: 240, 3: 48}
    assert image.sources["piece;v.inc"] == "ADD 1\n"
    copied = image.sources
    copied.clear()
    assert "piece;v.inc" in image.sources
    sim = isa.Simulator(machine())
    sim.load_assembly(image)
    assert sim.source_line(1) == ("piece;v.inc", 1)
    assert sim.source_context(1, context=0) == [(1, "ADD 1", True)]
    with pytest.raises(isa.TargetError, match=r"0.*1"):
        sim.disassemble("piece;v.inc:1")
    point = sim.add_breakpoint("piece;v.inc:1", temporary=True)
    assert len(sim.list_breakpoints()) == 1
    assert "0" in sim.list_breakpoints()[0] and "1" in sim.list_breakpoints()[0]
    sim.poke("cursor", 3)
    stop = sim.run(max_steps=5)
    assert (stop.stop_reason, stop.detail["id"], stop.pc) == ("breakpoint", point, 0)
    assert sim.list_breakpoints() == []
    output = StringIO()
    run_repl(sim, StringIO('until "piece;v.inc:1"\nquit\n'), output)
    assert "Stopped: breakpoint" in output.getvalue()
    point = sim.add_breakpoint("piece;v.inc:1")
    sim.delete(point)
    assert sim.list_breakpoints() == []


def test_string_main_search_order_and_absolute_source_keys(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory, cell in ((first, 3), (second, 4)):
        directory.mkdir()
        (directory / "x.inc").write_text(f".cell {cell}", encoding="utf-8")
    image = isa.assemble(machine(), '.include "x.inc"', include_paths=[first, second])
    assert image.cells == {0: 3}
    assert list(image.sources) == ["<string>", (first / "x.inc").as_posix()]
    with pytest.raises(isa.AssemblyError, match="missing.inc.*first.*second"):
        isa.assemble(machine(), '.include "missing.inc"', include_paths=[first, second])


def test_include_depth_cycles_and_cross_file_conditional(tmp_path):
    main_path = str(tmp_path / "main.asm")
    for depth in range(1, 17):
        text = f'.include "{depth + 1}.inc"' if depth < 16 else "END"
        (tmp_path / f"{depth}.inc").write_text(text, encoding="utf-8")
    assert isa.assemble(machine(), '.include "1.inc"', filename=main_path).cells == {
        0: 240
    }
    (tmp_path / "16.inc").write_text('.include "17.inc"', encoding="utf-8")
    (tmp_path / "17.inc").write_text("END", encoding="utf-8")
    with pytest.raises(isa.AssemblyError, match="depth"):
        isa.assemble(machine(), '.include "1.inc"', filename=main_path)
    (tmp_path / "2.inc").write_text('.include "./1.inc"', encoding="utf-8")
    with pytest.raises(isa.AssemblyError, match="cycle"):
        isa.assemble(machine(), '.include "1.inc"', filename=main_path)
    (tmp_path / "1.inc").write_text(".endif", encoding="utf-8")
    with pytest.raises(isa.AssemblyError, match="1.inc:1"):
        isa.assemble(
            machine(), '.if 1\n.include "1.inc"\nEND\n.endif', filename=main_path
        )


@pytest.mark.parametrize(
    ("source", "options", "version"),
    [
        ("END", {}, 1),
        ("END", {"include_paths": ("unused",)}, 1),
        ("END", {"defines": {}}, 1),
        ("END", {"defines": {"UNUSED": 1}}, 2),
        ("END", {"endian": "little"}, 2),
        ("END", {"case_sensitive_names": False}, 2),
        ('.if 0\n.include "missing.inc"\n.endif\nEND', {}, 2),
    ],
)
def test_bundle_format_matrix(tmp_path, source, options, version):
    path = tmp_path / "bundle.json"
    isa.assemble(machine(), source, **options).save(path)
    assert json.loads(path.read_bytes())["format"] == f"isasim-assembly-{version}"


def _includes_bundle(tmp_path, text="ADD 1"):
    for name in ("first", "second"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "x.inc").write_text(text, encoding="utf-8")
    source = '.include "first/x.inc"\n.include "second/x.inc"\nEND'
    image = isa.assemble(machine(), source, filename=str(tmp_path / "main.asm"))
    path = tmp_path / "main.isa.json"
    image.save(path)
    for name in ("first", "second"):
        (tmp_path / name / "x.inc").unlink()
    return path


def test_include_replay_and_source_key_ambiguity_after_removing_inputs(tmp_path):
    path = _includes_bundle(tmp_path)
    image = isa.Assembly.load(machine(), path)
    sim = isa.Simulator(machine())
    sim.load_assembly(image)
    assert image.cells == {0: 17, 1: 17, 2: 240}
    assert sim.source_line(0) == ("first/x.inc", 1)
    assert sim.source_line(1) == ("second/x.inc", 1)
    with pytest.raises(isa.TargetError, match="first/x.inc.*second/x.inc"):
        sim.add_breakpoint("x.inc:1")
    assert sim.source_context("second/x.inc:1", context=0) == [(1, "ADD 1", True)]


@pytest.mark.parametrize(
    "change", ["extra", "missing", "reordered", "unknown", "rebound"]
)
def test_bundle_validates_consumption_and_effect_of_include_bindings(tmp_path, change):
    path = _includes_bundle(tmp_path)
    payload = json.loads(path.read_bytes())
    includes = payload["includes"]
    if change == "extra":
        includes.append(includes[0])
    elif change == "missing":
        includes.pop()
    elif change == "reordered":
        includes.reverse()
    elif change == "unknown":
        includes[0]["to"] = "unknown.inc"
    else:
        includes[0]["to"], includes[1]["to"] = includes[1]["to"], includes[0]["to"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(isa.LoadError):
        isa.Assembly.load(machine(), path)


def test_bundle_allows_rebinding_with_identical_products(tmp_path):
    path = _includes_bundle(tmp_path, text="; identical comment")
    payload = json.loads(path.read_bytes())
    first, second = payload["includes"]
    first["to"], second["to"] = second["to"], first["to"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert isa.Assembly.load(machine(), path).cells == {0: 240}


def test_unknown_bundle_format_has_specific_diagnostic(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text('{"format":"future"}', encoding="utf-8")
    with pytest.raises(
        isa.LoadError, match="future.*isasim-assembly-1.*isasim-assembly-2"
    ):
        isa.Assembly.load(machine(), path)


def test_cli_run_and_repl_use_the_same_source_options(cli_source, capsys, monkeypatch):
    model, source = cli_source
    source.write_text(
        '.include "start.inc"\n.if ENABLED\nEND\n.endif', encoding="utf-8"
    )
    directory = source.parent / "inc"
    directory.mkdir()
    (directory / "start.inc").write_text("Begin: ADD value", encoding="utf-8")
    options = [
        str(model),
        str(source),
        "-I",
        str(directory),
        "-D",
        "Value=2",
        "-D",
        "ENABLED",
        "--case-insensitive",
    ]
    assert main(["run", *options, "--max-steps", "4"]) == 0
    assert "stop_reason=halted" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", StringIO("source\nquit\n"))
    assert main(["repl", *options]) == 0
    assert "Begin: ADD value" in capsys.readouterr().out


def test_preprocessing_and_bundle_bytes_do_not_depend_on_hash_seed(tmp_path):
    (tmp_path / "x.inc").write_text(".cell COUNT", encoding="utf-8")
    program = (
        "from pathlib import Path\n"
        "from rpkbin.isasim import assemble\n"
        "from tests.isasim.test_assembler import machine\n"
        "import sys\n"
        "image = assemble(machine(), '.if 1\\n.include \"x.inc\"\\n.endif\\nEND', "
        "include_paths=[Path(sys.argv[1])], defines={'COUNT': 2, 'NEXT': 1})\n"
        "image.save(sys.argv[2])\n"
    )
    contents = []
    for seed in ("19", "701"):
        path = tmp_path / f"{seed}.json"
        subprocess.run(
            [sys.executable, "-B", "-c", program, str(tmp_path), str(path)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        contents.append(path.read_bytes())
    assert contents[0] == contents[1]
