import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from rpkbin.isasim import LoadError, Simulator
from rpkbin.isasim.repl import run_repl

from .fixtures import A1, GOLDEN_A
from .toys.toy_a import make_mcu as a
from .toys.toy_b import make_mcu as b

A_HEX = ":100000001003140118002800310060FD5805FFFF9F\n:00000001FF\n"
B_HEX = ":0C000000104106112008300A0100130214\n:00000001FF\n"
B2_HEX = ":0600000040042000100185\n:00000001FF\n"
ROOT = Path(__file__).resolve().parents[2]


def file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_hex_modes(tmp_path):
    path = file(tmp_path, "a.hex", A_HEX)
    sim = Simulator(a())
    sim.load_hex(path, order="big")
    assert (sim.run(max_steps=100).cycle, sim.read("r2")) == (17, 6)
    sim = Simulator(a())
    sim.load_hex(path, order="little")
    r = sim.step()
    assert (r.stop_reason, r.steps) == ("undefined_opcode", 0)
    with pytest.raises(LoadError):
        Simulator(a()).load_hex(path)
    sim = Simulator(b())
    sim.load_hex(file(tmp_path, "b.hex", B_HEX))
    sim.poke("inp[1]", 1)
    assert sim.run(max_steps=100).cycle == 9


@pytest.mark.parametrize(
    "content,part",
    [
        (A_HEX.replace("FFFF9F", "FFFF00"), None),
        (A_HEX.splitlines()[0], None),
        (":0100000010EF\n:00000001FF\n", "0"),
        (":020000001003EB\n:020001000314E6\n:00000001FF\n", None),
        (":00000006FA\n:00000001FF\n", None),
    ],
)
def test_bad_hex(tmp_path, content, part):
    with pytest.raises(LoadError) as error:
        Simulator(a()).load_hex(file(tmp_path, "bad.hex", content), order="big")
    if part is not None:
        assert part in str(error.value)


def test_hex_range_and_binary(tmp_path):
    with pytest.raises(LoadError):
        Simulator(b()).load_hex(
            file(tmp_path, "range.hex", ":0100400000BF\n:00000001FF\n")
        )
    path = tmp_path / "a.bin"
    path.write_bytes(b"".join(value.to_bytes(2, "big") for value in A1))
    sim = Simulator(a())
    sim.load_bin(str(path), base=0, order="big")
    assert sim.run(max_steps=100).cycles == 17
    path.write_bytes(b"\x10")
    with pytest.raises(LoadError):
        Simulator(a()).load_bin(str(path), base=0, order="big")


def test_symbols_label_and_atomic(tmp_path):
    sim = Simulator(a())
    sim.load_cells("prog", dict(enumerate(A1)))
    data = {
        "symbols": {"loop": 3, "done": 6},
        "lines": [{"addr": 6, "file": "a.asm", "line": 7}],
    }
    path = file(tmp_path, "symbols.json", json.dumps(data))
    sim.load_symbols(path)
    assert [sim.label_at(i) for i in (3, 5, 6, 2)] == [
        ("loop", 0),
        ("loop", 2),
        ("done", 0),
        None,
    ]
    assert sim.source_line(6) == ("a.asm", 7) and sim.source_line(5) is None
    sim.add_breakpoint("done")
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.steps, r.cycle, r.pc) == ("breakpoint", 12, 14, 6)
    for bad in [{"extra": 0}, {"symbols": {"bad": 256}}]:
        with pytest.raises(LoadError):
            sim.load_symbols(file(tmp_path, "bad.json", json.dumps(bad)))
    with pytest.raises(LoadError):
        sim.load_cells("data", {0: 1, 1: 0x10000})
    assert sim.read("data[0]") == 0


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "rpkbin.isasim", *map(str, args)],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_cli_run_limits_trace_and_errors(tmp_path):
    toy = ROOT / "tests/isasim/toys/toy_a.py"
    path = file(tmp_path, "a.hex", A_HEX)
    command = [
        "run",
        toy,
        path,
        "--format",
        "hex",
        "--order",
        "big",
        "--max-steps",
        "100",
    ]
    result = cli(*command)
    assert (
        result.returncode == 0
        and "RESULT stop_reason=halted steps=14 instructions=14 cycles=17 pc=0x08"
        in result.stdout
    )
    result = cli(*command[:-1], "5")
    assert (
        result.returncode == 1
        and "RESULT stop_reason=max_steps steps=5 instructions=5 cycles=5 pc=0x05"
        in result.stdout
    )
    trace = tmp_path / "trace.log"
    result = cli(*command, "--trace", trace)
    assert result.returncode == 0 and trace.read_text() == GOLDEN_A
    for args in [
        [
            "run",
            toy,
            tmp_path / "missing.hex",
            "--format",
            "hex",
            "--order",
            "big",
            "--max-steps",
            "100",
        ],
        ["run", toy, path, "--format", "hex", "--max-steps", "100"],
    ]:
        result = cli(*args)
        assert result.returncode == 2 and result.stderr.startswith("error: ")


def test_cli_script(tmp_path):
    path = file(tmp_path, "b2.hex", B2_HEX)
    script = file(
        tmp_path, "s.py", "def setup(sim):\n    sim.read_seq('inp[0]', [0, 0, 1])\n"
    )
    result = cli(
        "run",
        ROOT / "tests/isasim/toys/toy_b.py",
        path,
        "--format",
        "hex",
        "--max-steps",
        100,
        "--script",
        script,
    )
    assert (
        result.returncode == 0
        and "RESULT stop_reason=halted steps=7 instructions=7 cycles=12 pc=0x06"
        in result.stdout
    )


def test_repl(tmp_path):
    sim = Simulator(a())
    sim.load_cells("prog", dict(enumerate(A1)))
    sim.load_symbols(
        file(tmp_path, "symbols.json", json.dumps({"symbols": {"done": 6}}))
    )
    out = io.StringIO()
    run_repl(sim, io.StringIO("break done\nrun\nregs\nstep\nprint r.r2\nquit\n"), out)
    text = out.getvalue()
    assert "Stopped: breakpoint" in text and "r2=0x0006" in text and "r0=0x0000" in text
    assert (
        "#12 c=14 pc=0x06 [0x5805] ST r2, [5] | data[0x05]:0x0000->0x0006 | +2"
        in text.splitlines()
    )
    assert "Stopped: step_done" in text
    assert any(
        line.strip() == "6" or line.strip().startswith("6 ")
        for line in text.splitlines()
    )
    out = io.StringIO()
    run_repl(sim, io.StringIO("bogus\nstep 0\nprint 6\nquit\n"), out)
    assert (
        "error: unknown command: bogus" in out.getvalue() and "error:" in out.getvalue()
    )


def test_repl_expression():
    sim = Simulator(a())
    sim.load_cells("prog", dict(enumerate(A1)))
    out = io.StringIO()
    run_repl(
        sim,
        io.StringIO("print 'x' == 'x'\nbreak 3 if r.r0 == 2\nrun\nprint pc\nquit\n"),
        out,
    )
    lines = [line.strip() for line in out.getvalue().splitlines()]
    assert any(line.startswith("1") for line in lines)
    assert "Stopped: breakpoint" in out.getvalue() and any(
        line == "3" or line.startswith("3 ") for line in lines
    )
