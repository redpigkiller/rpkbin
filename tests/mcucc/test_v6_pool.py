"""M11 private pool acceptance and negative cases."""
from dataclasses import replace
from pathlib import Path

import pytest

from corpus.pressure import pressure_source
from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc import diff
from rpkbin.mcucc.errors import AllocationError, ConfigError, InternalError, UnsupportedError
from rpkbin.mcucc.target import MemLoc
from rpkbin.mcucc.verify import _verify
from test_formcheck import toy

CONFIG = CompileConfig({"sram": (96, 111)}, False)
SOURCE = (Path(__file__).with_name("corpus") / "v5.py").read_text(encoding="utf-8")
SCAN = diff._module(SOURCE, "level_scan")


def test_private_marker_and_mode_authority():
    target, _, _ = toy("t2", 16)
    result = compile_source(SCAN, target, CONFIG)
    fn = result.functions["level_scan"]
    assert result.report()["functions"]["level_scan"]["pool_contract"] == "private"
    with pytest.raises(InternalError, match="V5/INV1"):
        _verify(fn, target, CONFIG, {fn.name: fn._ir.abi}, pool_private=False)


@pytest.mark.parametrize("access", ["mem.sram[96] = a0", "mem.sram[160] = mem.sram[111]"])
def test_private_constant_access_rejected(access):
    target, _, _ = toy("t2", 16)
    source = pressure_source(5) + f"    {access}\n"
    with pytest.raises(ConfigError, match=r"sram.*(96|111).*(96|0x60).*(111|0x6f)"):
        compile_source(source, target, CONFIG)


@pytest.mark.parametrize("window", [512, {"sram": (0x300, 0x6FF)}])
@pytest.mark.parametrize("access", ["return mem.sram[p]", "mem.sram[p] = 1\n    return 0"])
def test_differential_pointer_into_pool(monkeypatch, window, access):
    target, sim, _ = toy("t2", 16)
    source = f"""from rpkbin.mcucc.lang import Ptr, mem, fn, u8
P = Ptr("sram")
@fn()
def read_cell(p: P) -> u8:
    {access}
"""
    monkeypatch.setattr(diff, "_input_sets", lambda *args: [(96,)])
    result = diff.run_differential(source, target, sim, CONFIG, stubs={}, seeds=(71,),
                                   inputs_per_function=1, window=window)
    assert result.checked == 1 and len(result.failures) == 1
    assert "accesses spill pool at sram:96" in result.failures[0][3]


def test_v8_rejects_indirect_source_store_replaced_with_pool_write():
    target, _, _ = toy("t2", 16)
    fn = compile_source(SCAN, target, CONFIG).functions["level_scan"]
    form = next(f for f in target.forms if f.id == "st")
    mutated = tuple(replace(m, form=form, values={"m": MemLoc("sram", 96), "s": m.values["s"]})
                    if m.op == "st_ind" else m for m in fn.mir)
    changed = replace(fn, mir=mutated, bytes=sum(m.bytes for m in mutated))
    with pytest.raises(InternalError, match="V8"):
        _verify(changed, target, CONFIG, {fn.name: fn._ir.abi}, pool_private=True)


def test_modes_are_per_function():
    target, _, _ = toy("t2", 16)
    pressure = pressure_source(5).replace("def pressure", "def b")
    a = "from rpkbin.mcucc.lang import fn, u8\n@fn()\ndef a(x: u8) -> u8:\n    return x + 1\n"
    c = a.replace("def a", "def c").replace("+ 1", "+ 2")
    combined = compile_source(a + pressure + c, target, CONFIG)
    report = combined.report()["functions"]
    assert "pool_contract" not in report["a"] and "pool_contract" not in report["c"]
    assert report["b"]["pool_contract"] == "private"
    for name, source, immediate in (("a", a, 1), ("c", c, 2)):
        fn = combined.functions[name]
        # v5.2 goldens contain no slots, labels are excluded from the shape.
        shape = [(m.form.id, m.values) for m in fn.mir if m.form is not None]
        assert shape == [("add_ri", {"d": "r0", "k": immediate}), ("ret", {})]
        assert fn.bytes == 3


def test_unsupported_does_not_retry():
    target, _, _ = toy("t0", 8)
    with pytest.raises(UnsupportedError) as failure:
        compile_source(diff._module(SOURCE, "lt16"), target, CONFIG)
    assert str(failure.value) == "lt16: ABI has no argument location for width 16"


def test_second_allocation_error_suffix():
    target, _, _ = toy("t2", 16)
    with pytest.raises(AllocationError, match=r"\[private-pool mode\]$"):
        compile_source(SCAN, target, CompileConfig({}, False))



@pytest.mark.parametrize(("name", "width", "first_private"), [
    ("t2", 8, 6), ("t2", 16, 5), ("t3", 16, 6),
])
@pytest.mark.parametrize("k", range(1, 11))
def test_pressure_corpus(name, width, first_private, k):
    target, sim, _ = toy(name, width)
    source = pressure_source(k)
    row = compile_source(source, target, CONFIG).report()["functions"]["pressure"]
    assert row.get("pool_contract") == ("private" if k >= first_private else None)
    report = diff.run_differential(source, target, sim, CONFIG, stubs={}, seeds=(71,),
                                   inputs_per_function=8, window=256)
    assert report.checked == 8 and not report.failures and not report.unsupported


def test_t2_word_scan_differential():
    target, sim, _ = toy("t2", 16)
    report = diff.run_differential(SCAN, target, sim, CONFIG, stubs={}, seeds=(71,),
                                   inputs_per_function=120, window={"sram": (0x300, 0x6FF)})
    assert report.checked >= 120 and not report.failures and not report.unsupported


def test_direct_reference_access_in_legacy_mode():
    target, sim, _ = toy("t2", 16)
    source = "from rpkbin.mcucc.lang import fn, mem, u8\n@fn()\ndef f() -> u8:\n    return mem.sram[96]\n"
    assert "pool_contract" not in compile_source(source, target, CONFIG).report()["functions"]["f"]
    report = diff.run_differential(source, target, sim, CONFIG, stubs={}, seeds=(71,),
                                   inputs_per_function=1, window=512)
    assert report.checked == 1 and len(report.failures) == 1
    assert "accesses spill pool at sram:96" in report.failures[0][3]

