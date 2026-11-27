from dataclasses import replace
import importlib
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

from corpus.park import park_source
from rpkbin import __version__
from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.cli import _main
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.errors import SourceError, TargetError, UnsupportedError
from rpkbin.mcucc.expand import MInstr
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.ir import Block, Function, Imm, Instr, VReg, ir_eval
from rpkbin.mcucc.lang import run_reference, u8, u16
from rpkbin.mcucc.opt import _algebra, _value_numbering
from rpkbin.mcucc.peephole import _peephole
from rpkbin.mcucc.target import Form, I, M, R, T
from test_compile import source_for
from test_formcheck import toy


TOYS = Path(__file__).parent / "toys"


def test_mcucc_01_per_function_timeout_fails_differential_and_cli(tmp_path, capsys):
    target, sim, _ = toy("t1", 8)
    config = CompileConfig({"sram": (0x60, 0x6F)}, False)
    oks = "\n".join(f"def ok{i}() -> u8:\n    return {i}" for i in range(10))
    hang = "def hang() -> u8:\n    x: u8 = 1\n    while x != 0:\n        pass\n    return x\n"
    source = "from rpkbin.mcucc.lang import u8\n" + oks + "\n" + hang
    report = run_differential(
        source, target, sim, config, stubs={}, seeds=(0,), inputs_per_function=10, window=256
    )
    assert any(f[0] == "hang" for f in report.failures), report

    src_path = tmp_path / "hang.py"
    src_path.write_text("from rpkbin.mcucc.lang import u8\n" + hang, encoding="utf-8")
    target_path = tmp_path / "t1.py"
    target_path.write_text((TOYS / "t1.py").read_text(encoding="utf-8"), encoding="utf-8")
    code = _main([
        "verify", str(src_path), "--target", str(target_path),
        "--spill-pool", "sram:0x60-0x6f", "--window", "256", "--inputs", "2",
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert "hang: verified" not in out
    assert "hang: failed" in out


def test_mcucc_02_stack_first_falls_back_to_second_when_first_is_none():
    target, _, _ = toy("t2", 16)
    source = park_source(1, 3, True)
    smallest = compile_source(source, target, CompileConfig({}, False, park_depth=8, park_policy="smallest"))
    stack_first = compile_source(source, target, CompileConfig({}, False, park_depth=8, park_policy="stack_first"))
    assert stack_first.functions["pressure"].bytes == smallest.functions["pressure"].bytes
    assert stack_first.functions["pressure"]._parks == 1


def test_mcucc_03_peephole_rule6_handles_immediate_store():
    target, _, _ = toy("t1", 8)
    st_imm = Form("st_imm", "st", 8, (M("m", "sram"), I("k", 8)), {"a": "m", "b": "k"}, 3, "st {m}, #{k}")
    target_with_st_imm = replace(target, forms=(*target.forms, st_imm))
    source = "from rpkbin.mcucc.lang import mem, u8\ndef f() -> u8:\n    mem.sram[16] = 5\n    return mem.sram[16]\n"
    result = compile_source(source, target_with_st_imm, CompileConfig({"sram": (0x60, 0x6F)}, False))
    assert result.functions["f"].verified is True
    assert "ldi r0, #5" in result.asm

    wide_const = replace(
        target_with_st_imm,
        forms=tuple(replace(f, bytes=4) if f.op == "const" else f for f in target_with_st_imm.forms),
    )
    kept = compile_source(source, wide_const, CompileConfig({"sram": (0x60, 0x6F)}, False))
    assert kept.functions["f"].verified is True
    assert "ld r0, [0x10]" in kept.asm


def test_mcucc_04_target_rejects_unbound_non_target_operands():
    target = toy("t1", 8)[0]
    forms = tuple(
        Form(
            "add_rr", "add", 8, (R("d", "g"), R("s", "g"), I("extra", 8)),
            {"dst": "d", "a": "d", "b": "s"}, 1, "add {d}, {s}, {extra}",
        )
        if f.id == "add_rr" else f
        for f in target.forms
    )
    with pytest.raises(TargetError, match="bind"):
        replace(target, forms=forms)


def test_mcucc_05_assemble_preserves_unsupported_and_reports_target_error_for_bad_cells():
    target = toy("t1", 8)[0]
    short_call = replace(
        target,
        forms=tuple(replace(f, operands=(T("t", abs_bits=8),)) if f.op == "call" else f for f in target.forms),
    )
    source = (
        "from rpkbin.mcucc.lang import u8\n"
        "def ext(a: u8) -> u8:\n    return a + 1\n"
        "def f(a: u8) -> u8:\n    return ext(a)\n"
    )
    res = compile_source(source, short_call, CompileConfig({"sram": (0x60, 0x6F)}, False))
    with pytest.raises(UnsupportedError, match="outside 8 bits"):
        assemble(res, short_call, origin=0x200)

    bad_ret = replace(
        target,
        forms=tuple(replace(f, encode=lambda v: (256,)) if f.id == "ret" else f for f in target.forms),
    )
    with pytest.raises(TargetError, match="encode length/cells"):
        compile_source(source_for("add2"), bad_ret, CompileConfig({"sram": (0x60, 0x6F)}, False))

    clean_res = compile_source(source_for("add2"), target, CompileConfig({"sram": (0x60, 0x6F)}, False))
    bad_fn = replace(
        clean_res.functions["add2"],
        mir=tuple(
            replace(m, form=next(f for f in bad_ret.forms if f.id == "ret")) if m.op == "ret" else m
            for m in clean_res.functions["add2"].mir
        ),
    )
    with pytest.raises(TargetError, match="encode length/cells"):
        assemble(replace(clean_res, functions={"add2": bad_fn}), bad_ret, origin=0)


def test_mcucc_06_run_differential_rejects_stub_program_overflow():
    target, sim, _ = toy("t1", 8)
    source = (
        "from rpkbin.mcucc.lang import u8, extern\n"
        "@extern(args=('r0',), ret='r0', clobbers=())\n"
        "def ext(a: u8) -> u8:\n    return a\n"
        "def f(a: u8) -> u8:\n    return ext(a)\n"
    )
    with pytest.raises(UnsupportedError, match="program address space"):
        run_differential(
            source, target, sim, CompileConfig({"sram": (0x60, 0x6F)}, False),
            stubs={"ext": (0,) * (1 << 12)}, seeds=(0,), inputs_per_function=1, window=256,
        )


def test_mcucc_07_uint_equality_sim_return_and_missing_reference_file(tmp_path):
    none_val = None
    assert (u8(1) == 300) is False
    assert (u8(1) != 300) is True
    assert (u8(1) == none_val) is False
    assert (u8(1) == u16(1)) is False
    assert (u8(1) != u16(1)) is True
    assert len({u8(1), u16(1)}) == 2
    assert u8(1) == 1

    target, sim, _ = toy("t1", 8)
    source = "from rpkbin.mcucc.lang import u8\ndef f(a: u8) -> u8:\n    return a\n"

    class BadSim:
        def __init__(self, inner):
            self._inner = inner
            self._ran = False

        def load_cells(self, space, cells):
            self._inner.load_cells(space, cells)

        def poke(self, name, value):
            self._inner.poke(name, value)

        def run(self, **kwargs):
            self._ran = True
            return self._inner.run(**kwargs)

        def read(self, name):
            if self._ran and name == "r0":
                return 256
            return self._inner.read(name)

    bad_binding = replace(sim, make_sim=lambda: BadSim(sim.make_sim()))
    report = run_differential(
        source, target, bad_binding, CompileConfig({"sram": (0x60, 0x6F)}, False),
        stubs={}, seeds=(0,), inputs_per_function=1, window=256,
    )
    assert len(report.failures) >= 1
    assert "return 256 !=" in report.failures[0][3]

    missing = tmp_path / "missing.py"
    with pytest.raises(SourceError, match="cannot read source"):
        run_reference(missing, "f", (), {}, addr_bits={})
    with pytest.raises(SourceError, match="cannot read source"):
        run_reference(str(missing), "f", (), {}, addr_bits={})

    bad_utf8 = tmp_path / "bad.py"
    bad_utf8.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(SourceError, match="cannot read source"):
        run_reference(bad_utf8, "f", (), {}, addr_bits={})


def test_mcucc_08_abi_ret_mismatch_and_readonly_asm_outs():
    target = toy("t1", 8)[0]
    bad_void_extern = (
        "from rpkbin.mcucc.lang import u8, extern\n"
        "@extern(args=('r0',), ret='GARBAGE', clobbers=())\n"
        "def ext(a: u8):\n    pass\n"
    )
    with pytest.raises(SourceError):
        _parse(bad_void_extern, target, "bad.py")
    bad_nonvoid_extern = (
        "from rpkbin.mcucc.lang import u8, extern\n"
        "@extern(args=('r0',), ret=None, clobbers=())\n"
        "def ext(a: u8) -> u8:\n    return a\n"
    )
    with pytest.raises(SourceError):
        _parse(bad_nonvoid_extern, target, "bad.py")
    bad_void_fn = "from rpkbin.mcucc.lang import u8, fn\n@fn(ret='r0')\ndef f(a: u8):\n    pass\n"
    with pytest.raises(SourceError):
        _parse(bad_void_fn, target, "bad.py")
    bad_nonvoid_fn = "from rpkbin.mcucc.lang import u8, fn\n@fn(ret=None)\ndef f(a: u8) -> u8:\n    return a\n"
    with pytest.raises(SourceError):
        _parse(bad_nonvoid_fn, target, "bad.py")

    bad_asm = (
        "from rpkbin.mcucc.lang import u8, asm\n"
        "def f() -> u8:\n"
        "    for i in range(3):\n"
        "        asm('ldi {0}, #2', ins=((i, 'r0'),), outs=((i, 'r0'),), nbytes=2, ref=lambda v: 2)\n"
        "    return 0\n"
    )
    with pytest.raises(SourceError):
        _parse(bad_asm, target, "bad.py")
    with pytest.raises(SourceError):
        run_reference(bad_asm, "f", (), {}, addr_bits={})


def test_mcucc_10_cli_guard_version_output_and_lf(tmp_path, capsys):
    importlib.import_module("rpkbin.mcucc.__main__")
    from rpkbin.mcucc.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert f"rpk-mcucc {__version__}" in capsys.readouterr().out

    proc = subprocess.run([sys.executable, "-m", "rpkbin.mcucc", "-V"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert __version__ in proc.stdout

    src = tmp_path / "src.py"
    src.write_text(source_for("add2"), encoding="utf-8")
    target_path = TOYS / "t1.py"
    out_file = tmp_path / "out.asm"
    assert main([
        "compile", str(src), "--target", str(target_path),
        "--spill-pool", "sram:0x60-0x6f", "--output", str(out_file),
    ]) == 0
    assert b"\r\n" not in out_file.read_bytes()

    assert main([
        "compile", str(src), "--target", str(target_path),
        "--spill-pool", "sram:0x60-0x6f", "-o", "",
    ]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "error:" in captured.err


def test_mcucc_11_shifted_parse_unsupported_peephole_cmp_and_opt_self_update():
    target, sim, _ = toy("t2", 16)
    source = (
        "from rpkbin.mcucc.lang import u16, fn\n"
        "@fn(args=(('sram', 0x10),), ret='x0')\n"
        "def bad(a: u16) -> u16:\n    return a\n"
    )
    report = run_differential(
        source, target, sim, CompileConfig({"sram": (0x60, 0x6F)}, False),
        stubs={}, seeds=(0,), inputs_per_function=1, window={"sram": (0x300, 0x4FF)},
    )
    assert len(report.unsupported) == 1 and report.unsupported[0][0] == "bad"

    t1 = toy("t1", 8)[0]
    cmp_ii = Form(
        "cmp_ii", "cmp", 8, (I("a", 8), I("b", 8)), {"a": "a", "b": "b"}, 2,
        "cmp #{a}, #{b}", flags_write={"Z": "zero"},
    )
    t1_cmp = replace(t1, forms=(*t1.forms, cmp_ii))
    forms = {f.id: f for f in t1_cmp.forms}
    abi = {"args": (), "ret": "r0", "preserves": ()}
    end_lbl = MInstr(None, {}, _label="end", _target=t1_cmp, _block=1)
    mir = (
        MInstr(forms["sub_ri"], {"d": "r0", "k": 1}, _target=t1_cmp, _abi=abi, _block=0),
        MInstr(cmp_ii, {"a": 1, "b": 0}, _target=t1_cmp, _abi=abi, _block=0),
        MInstr(forms["jnz"], {"t": "end"}, _target=t1_cmp, _abi=abi, _block=0),
        MInstr(forms["ret"], {}, _target=t1_cmp, _abi=abi, _block=0),
        end_lbl,
        MInstr(forms["ret"], {}, _target=t1_cmp, _abi=abi, _block=1),
    )
    assert _peephole(mir, t1_cmp, "f")

    v0 = VReg(0, 8)
    fn_alg = Function("f", [v0], 8, [Block(0, [
        Instr("entry", None, (v0,)),
        Instr("add", v0, (v0, Imm(1, 8))),
        Instr("add", v0, (v0, Imm(2, 8))),
    ], Instr("ret", None, (v0,)))], {"args": ("r0",), "ret": "r0", "preserves": (), "clobbers": ()})
    expected = ir_eval(fn_alg, (10,), {}, {"f": fn_alg})[0]
    _algebra(fn_alg)
    assert ir_eval(fn_alg, (10,), {}, {"f": fn_alg})[0] == expected

    v1, v2 = VReg(0, 8), VReg(1, 8)
    fn_vn = Function("f", [v1], 8, [Block(0, [
        Instr("entry", None, (v1,)),
        Instr("add", v1, (v1, Imm(1, 8))),
        Instr("add", v2, (v1, Imm(1, 8))),
    ], Instr("ret", None, (v2,)))], {"args": ("r0",), "ret": "r0", "preserves": (), "clobbers": ()})
    expected_vn = ir_eval(fn_vn, (10,), {}, {"f": fn_vn})[0]
    _value_numbering(fn_vn)
    assert ir_eval(fn_vn, (10,), {}, {"f": fn_vn})[0] == expected_vn


def test_mcucc_12_and_13_ergonomics_and_py311_subset():
    target, sim, _ = toy("t1", 8)
    report = run_differential(
        source_for("add2"), target, sim, CompileConfig({"sram": (0x60, 0x6F)}, False),
        seeds=(0,), inputs_per_function=1, window=256,
    )
    assert report.checked > 0 and not report.failures

    raw_pool = {"sram": (0x60, 0x6F)}
    cfg = CompileConfig(raw_pool, False)
    raw_pool["sram"] = (0, 1)
    assert dict(cfg.spill_pool) == {"sram": (0x60, 0x6F)}
    with pytest.raises(TypeError):
        cfg.spill_pool["other"] = (0, 1)
    assert CompileConfig(MappingProxyType({"sram": (0x60, 0x6F)}), False) == cfg

    import rpkbin.mcucc as pkg
    import rpkbin.mcucc.lang as lang_mod

    assert set(pkg.__all__) == {"CompileConfig", "CompileResult", "FunctionResult", "compile_file", "compile_source"}
    assert set(lang_mod.__all__) == {"u8", "u16", "Ptr", "mem", "fn", "extern", "asm", "run_reference"}

    py312_only = "from rpkbin.mcucc.lang import u8\ndef f[T](a: u8) -> u8:\n    return a\n"
    with pytest.raises(SourceError):
        _parse(py312_only, target, "py312.py")
