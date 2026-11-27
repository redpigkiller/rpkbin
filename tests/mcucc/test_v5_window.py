from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from rpkbin.mcucc import CompileConfig
from rpkbin.mcucc.cli import _main, _window
from rpkbin.mcucc.diff import _input_sets, run_differential
from rpkbin.mcucc.errors import ConfigError
from test_formcheck import toy


CONFIG = CompileConfig({"sram": (0x60, 0x6F)}, False)
TOY = Path(__file__).with_name("toys") / "t2.py"
LOAD = """\
from rpkbin.mcucc.lang import u8, Ptr, mem, fn
P = Ptr("sram")
@fn()
def read(p: P) -> u8:
    return mem.sram[p]
"""


def test_window_pointer_samples_and_legacy_sequence():
    function = SimpleNamespace(params=(("p", SimpleNamespace(width=16, space="sram")),))
    legacy = _input_sets(function, CONFIG, random.Random(71), 1)
    shifted = _input_sets(function, CONFIG, random.Random(71), 1,
                          {"sram": (0x300, 0x5FF)})
    assert legacy[:4] == [(0x80,), (0xBF,), (0xD2,), (0x82,)]
    assert shifted[:2] == [(0x300,), (0x33F,)]
    assert all(0x300 <= p < 0x480 for p, in shifted)
    assert shifted == _input_sets(function, CONFIG, random.Random(71), 1,
                                  {"sram": (0x300, 0x5FF)})


@pytest.mark.parametrize(("window", "detail"), [
    ({"sram": (0x300, 0x4FE)}, "511"),
    ({"sram": (0x301, 0x300)}, "sram"),
    ({"sram": (0, 0x1FF)}, "spill pool"),
    ({}, "sram"),
    ({"rom": (0, 0x1FF)}, "rom"),
    ({"sram": (0xFF00, 0x100FF)}, "sram"),
])
def test_window_api_rejects_invalid_inputs(window, detail):
    target, sim, _ = toy("t2", 16)
    with pytest.raises(ConfigError, match=detail):
        run_differential(LOAD, target, sim, CONFIG, stubs={}, seeds=(71,),
                         inputs_per_function=1, window=window)


@pytest.mark.parametrize(("declarations", "detail"), [
    ([], "--window is required"),
    (["256", "sram:0-511"], "mix"),
    (["sram:0x300-0x200"], "sram"),
    (["sram:0-510"], "511"),
])
def test_window_cli_errors(tmp_path, capsys, declarations, detail):
    source = tmp_path / "test.py"
    source.write_text(LOAD, encoding="utf-8")
    argv = ["verify", str(source), "--target", str(TOY), "--spill-pool", "sram:96-111"]
    for declaration in declarations:
        argv.extend(("--window", declaration))
    assert _main(argv) == 2
    assert detail in capsys.readouterr().err


def test_window_reference_out_of_range_is_a_sample_failure():
    target, sim, _ = toy("t2", 16)
    source = LOAD.replace("sram[p]", "sram[p + P(512)]")
    report = run_differential(source, target, sim, CONFIG, stubs={}, seeds=(71,),
                              inputs_per_function=1, window={"sram": (0x300, 0x4FF)})
    assert report.checked == len(report.failures) > 0
    assert "1280" in report.failures[0][3]


def test_window_cli_multiple_intervals():
    assert _window(["first:768-1279", "second:0x600-0x7FF"]) == {
        "first": (0x300, 0x4FF), "second": (0x600, 0x7FF),
    }
    assert _window(["256"]) == 256


def test_shifted_load_and_unlisted_direct_reads():
    target, sim, _ = toy("t2", 16)
    options = dict(stubs={}, seeds=(71,), inputs_per_function=1)
    shifted = run_differential(LOAD, target, sim, CONFIG, **options,
                               window={"sram": (0x300, 0x4FF)})
    assert shifted.checked > 0 and not shifted.failures and not shifted.unsupported
    direct = LOAD.replace("read(p: P)", "read()").replace("sram[p]", "sram[0x700]")
    zero = run_differential(direct, target, sim, CONFIG, **options, window={})
    assert zero.checked == 1 and not zero.failures and not zero.unsupported
