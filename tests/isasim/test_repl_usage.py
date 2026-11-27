"""Usage failures share help syntax and leave the stream usable."""

from io import StringIO

import pytest

from rpkbin.isasim import Simulator
from rpkbin.isasim.repl import run_repl

from .toys.toy_a import make_mcu


def _session(lines):
    output = StringIO()
    run_repl(Simulator(make_mcu()), StringIO(lines), output)
    return output.getvalue()


@pytest.mark.parametrize(
    "line",
    [
        "break",
        "break 1 2",
        "watch",
        "delete",
        "mem",
        "poke 1",
        "irq 1",
        "stim",
        "snapshot",
        "snapshot save",
        "trace",
        "load",
        "until",
        "help nosuch",
        "break 1 if",
        "break 1 missing keyword",
        "watch r0 bad",
        "watch r0 if",
        "print",
        "display",
        "quit extra",
        "run 1 2",
        "step 1 2",
        "tbreak",
        "info nosuch",
        "regs extra",
        "undisplay",
        "disasm 1 2 3",
        "reset extra",
        "where extra",
        "stim nosuch",
        "snapshot nosuch",
        "trace nosuch",
        "load nosuch",
        "trace on x bad",
        "load hex x bad",
        "load bin x 0 bad",
        "help help extra",
    ],
)
def test_usage_matches_help_and_continues(line):
    command = line.split()[0]
    help_output = _session(f"help {command}\nquit\n")
    syntax = next(
        s
        for s in help_output.splitlines()
        if s.startswith(command + " ") or s == command
    )
    output = _session(f"{line}\nprint 7\nquit\n")
    assert f"error: usage: {syntax}\n" in output
    assert "7 (0x7)" in output


@pytest.mark.parametrize(
    "line,reason",
    [
        ("print nosuch", "nosuch"),
        ("break nosuchlabel", "nosuchlabel"),
        ("step x", "expected decimal/hex integer"),
        ("poke r_unknown 1", "r_unknown"),
        ('load hex "', "No closing quotation"),
        ("snapshot restore missing", "snapshot='missing': unknown"),
    ],
)
def test_value_and_simulator_errors_keep_reason(line, reason):
    output = _session(f"{line}\nprint 7\nquit\n")
    assert "error: " in output and reason in output
    assert "usage:" not in output
    assert "7 (0x7)" in output


def test_unknown_command_keeps_reason():
    output = _session("nosuch\nprint 7\nquit\n")
    assert "error: unknown command: nosuch" in output
    assert "usage:" not in output
    assert "7 (0x7)" in output
