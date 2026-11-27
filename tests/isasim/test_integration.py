"""Repository entry points preserve the simulator's existing CLI contract."""

import importlib
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


def test_installed_entry_point_declared():
    root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["scripts"]["rpk-isasim"] == "rpkbin.isasim.cli:main"
    assert metadata["project"]["requires-python"] == ">=3.11"


def test_public_cli_help(capsys):
    cli = importlib.import_module("rpkbin.isasim.cli")
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])
    assert raised.value.code == 0
    assert "usage: rpk-isasim" in capsys.readouterr().out


def test_console_and_module_share_results(tmp_path, capsys):
    path = tmp_path / "description.py"
    path.write_text(
        "from rpkbin.isasim import MCU\n"
        "mcu = MCU('entry')\n"
        "mcu.space('prog', cell_bits=8, size=4, access='ro', "
        "reset_state='initialized', init=0, is_program=True)\n"
        "mcu.reg('cursor', bits=3, reset=0, is_pc=True)\n"
        "mcu.on_undefined_opcode('stop')\n"
        "mcu.on_out_of_range('stop')\n"
        "mcu.on_write_to_readonly('stop')\n"
        "mcu.on_uninitialized_read('stop')\n"
        "@mcu.instr('00000000', syntax='END', cycles=1)\n"
        "def finish(st): st.halt('done')\n",
        encoding="utf-8",
    )
    args = ["run", str(path), "--max-steps", "1"]
    cli = importlib.import_module("rpkbin.isasim.cli")
    assert cli.main(args) == 0
    console = capsys.readouterr().out
    module = subprocess.run(
        [sys.executable, "-m", "rpkbin.isasim", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    assert console == module.stdout
    assert "RESULT stop_reason=halted steps=1 instructions=1 cycles=1 pc=0x1" in console


def test_legacy_module_help_still_identifies_module(capsys):
    cli = importlib.import_module("rpkbin.isasim.cli")
    with pytest.raises(SystemExit) as raised:
        cli._main(["--help"])
    assert raised.value.code == 0
    assert "usage: python -m rpkbin.isasim" in capsys.readouterr().out
