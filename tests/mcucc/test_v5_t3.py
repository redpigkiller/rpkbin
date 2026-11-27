from dataclasses import replace
from itertools import product
import json
from pathlib import Path

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.cli import _main
from rpkbin.mcucc.diff import _module, run_differential
from rpkbin.mcucc.errors import AllocationError, InternalError, UnsupportedError
from rpkbin.mcucc.formcheck import check_forms
from rpkbin.mcucc.ir import ir_eval
from rpkbin.mcucc.target import Alias, Cls, Reg
from test_formcheck import toy


CONFIG = CompileConfig({"sram": (0x60, 0x6F)}, False)
TARGET_FILE = Path(__file__).with_name("toys") / "t3.py"
SOURCE = (Path(__file__).with_name("corpus") / "v5.py").read_text(encoding="utf-8")
CHANGED = {"sub_rr", "sub_ri", "subw", "cmp_rr", "cmp_ri", "cmpw"}


def test_t3_declarations_and_availability_matrix(capsys):
    original, _, _ = toy("t2", 16)
    target, _, _ = toy("t3", 16)
    assert target.regs == (*original.regs, Reg("r6", 8), Reg("r7", 8))
    assert target.aliases == (*original.aliases, Alias("x2", ("r7", "r6")))
    assert target.classes == (
        Cls("b", (*original.classes[0].members, "r6", "r7")), original.classes[1],
        Cls("w", (*original.classes[2].members, "x2")),
        Cls("ptr", (*original.classes[3].members, "x2")),
    )
    assert replace(target, regs=original.regs, aliases=original.aliases,
                   classes=original.classes, forms=original.forms) == original
    for base, changed in zip(original.forms, target.forms, strict=True):
        if base.id in CHANGED:
            assert changed.flags_write == {"Z": "zero", "C": "borrow_msb0"}
        else:
            assert changed.flags_write == base.flags_write
        assert replace(changed, flags_write=base.flags_write, encode=base.encode) == base
    assert _main(["lint-target", str(TARGET_FILE)]) == 0
    matrix = capsys.readouterr().out
    assert "cmp\t8\tTrue\t2\t2\n" in matrix
    assert "cmp\t16\tTrue\t1\t1\n" in matrix


def test_t3_one_byte_encoding_capacity():
    target, sim, _ = toy("t3", 16)
    classes = {cls.name: cls.members for cls in target.classes}
    codes = [sim.halt_cells]
    for form in target.forms:
        if form.bytes == 1:
            codes.extend(form.encode(dict(zip((operand.name for operand in form.operands), values)))
                         for values in product(*(classes[operand.cls] for operand in form.operands)))
    assert len(codes) == len(set(codes)) == 164
    assert all(len(code) == 1 and 0 <= code[0] < 256 for code in codes)


def test_t3_check_forms_and_mutated_sim(capsys):
    target, _, _ = toy("t3", 16)
    assert _main(["check-forms", str(TARGET_FILE)]) == 0
    assert "verified: forms" in capsys.readouterr().out
    from machine import verified

    forms = tuple(replace(form, flags_write={**form.flags_write, "C": "nborrow"})
                  if form.id in CHANGED else form for form in target.forms)
    _, wrong, _ = verified(replace(target, forms=forms), ())
    failures = check_forms(target, wrong, (71,))
    assert CHANGED <= {f.form_id for f in failures if f.check == "C4"}


def test_t3_rejects_unrestricted_u16():
    target, _, _ = toy("t3", 16)
    with pytest.raises(UnsupportedError, match="lt16:.*最高位"):
        compile_source(_module(SOURCE, "lt16"), target, CONFIG)


def test_t3_ge8_widened_output_and_differential(monkeypatch):
    from rpkbin.mcucc import verify
    from test_v5_widen import optimized

    target, sim, _ = toy("t3", 16)
    source = _module(SOURCE, "ge8")
    before = optimized(source, target, "ge8")
    result = compile_source(source, target, CONFIG)
    fn = result.functions["ge8"]
    block = next(block for block in fn._ir.blocks if block.term.op == "br")
    extensions = [(index, instr) for index, instr in enumerate(block.instrs)
                  if instr.op == "zext"]
    assert len(extensions) == 2
    assert tuple(instr.dst for _, instr in extensions) == block.term.args
    assert all(instr.args[0].width == 8 and instr.dst.width == 16 for _, instr in extensions)
    for index, _ in extensions:
        # Each zero extension must materialize a zero high cell in emitted code.
        assert any(ins.op == "const" and ins._origin == (block.id, index)
                   and ins.values[ins.form.bind["a"]] == 0 for ins in fn.mir)
    assert any(ins.op == "cmp" and ins.form.width == 16 for ins in fn.mir)
    for a, b in product((0, 0x7F, 0x80, 0xFF), repeat=2):
        assert ir_eval(before, (a, b), {}, {}) == ir_eval(fn._ir, (a, b), {}, {})

    sizes = json.loads(Path(__file__).with_name("sizes.json").read_text(encoding="utf-8"))
    assert fn.bytes == sizes["t3-16"]["ge8"]
    inputs = set()
    visits = []
    original = verify._branch_msb0

    def record_proof(block, width):
        proved = original(block, width)
        visits.append((width, proved))
        return proved

    def sample(phase, machine):
        if phase == "before":
            inputs.add(tuple(machine.read((sim.names or {}).get(reg, reg))
                             for reg in fn._ir.abi["args"]))

    monkeypatch.setattr(verify, "_branch_msb0", record_proof)
    report = run_differential(source, target, sim, CONFIG, stubs={}, seeds=(71,),
                              inputs_per_function=120, window=256, post_check=sample)
    assert report.checked >= 120 and not report.failures and not report.unsupported
    assert visits == [(16, True)]
    for column in zip(*inputs, strict=True):
        assert {0x7F, 0x80, 0xFF} <= set(column)


@pytest.mark.parametrize("forge_proof", (False, True))
def test_t3_ge8_mutation_without_widening(monkeypatch, forge_proof):
    from rpkbin.mcucc import compiler

    def disable_widening(function, target):
        if forge_proof:
            for block in function.blocks:
                if block.term.op == "br":
                    block.term.meta["msb0_ok"] = True

    monkeypatch.setattr(compiler, "_widen_comparisons", disable_widening)
    target, _, _ = toy("t3", 16)
    if forge_proof:
        error, message = InternalError, r"ge8: V10:.*lacks proof"
    else:
        error, message = UnsupportedError, r"ge8:\d+: \(br, 8\) has no ideal expansion"
    with pytest.raises(error, match=message):
        compile_source(_module(SOURCE, "ge8"), target, CONFIG)


def test_t3_level_scan_snapshot(monkeypatch):
    from rpkbin.mcucc import verify

    target, sim, _ = toy("t3", 16)
    source = _module(SOURCE, "level_scan")
    baseline = json.loads((Path(__file__).with_name("corpus") / "t3-level-scan.json")
                          .read_text(encoding="utf-8"))
    try:
        result = compile_source(source, target, CONFIG)
    except AllocationError as exc:
        assert baseline == {"allocation_error": str(exc)}
        return
    assert baseline == {"asm": result.asm, "report": json.loads(json.dumps(result.report()))}
    visits = []
    original = verify._branch_msb0

    def record_proof(block, width):
        proved = original(block, width)
        visits.append(proved)
        return proved

    monkeypatch.setattr(verify, "_branch_msb0", record_proof)
    report = run_differential(source, target, sim, CONFIG, stubs={}, seeds=(71,),
                              inputs_per_function=120, window={"sram": (0x300, 0x6FF)})
    assert report.checked >= 120 and not report.failures and not report.unsupported
    assert visits and all(visits)


def test_t3_lt_zext_without_widening(monkeypatch):
    from rpkbin.mcucc import verify
    from rpkbin.mcucc.widen import _widen_comparisons
    from test_v5_widen import optimized

    target, sim, _ = toy("t3", 16)
    source = _module(SOURCE, "lt_zext")
    ir = optimized(source, target, "lt_zext")
    before = tuple(i for block in ir.blocks for i in block.instrs if i.op == "zext")
    assert len(before) == 2
    _widen_comparisons(ir, target)
    assert tuple(i for block in ir.blocks for i in block.instrs if i.op == "zext") == before
    branch = next(block for block in ir.blocks if block.term.op == "br")
    assert branch.term.meta["msb0_ok"] is True
    assert {i.dst for i in before} == set(branch.term.args)
    sizes = json.loads(Path(__file__).with_name("sizes.json").read_text(encoding="utf-8"))
    assert compile_source(source, target, CONFIG).functions["lt_zext"].bytes == sizes["t3-16"]["lt_zext"]
    visits = []
    original = verify._branch_msb0

    def record_proof(block, width):
        proved = original(block, width)
        visits.append((width, proved))
        return proved

    monkeypatch.setattr(verify, "_branch_msb0", record_proof)
    report = run_differential(source, target, sim, CONFIG, stubs={}, seeds=(71,),
                              inputs_per_function=120, window=256)
    assert report.checked >= 120 and not report.failures and not report.unsupported
    assert visits == [(16, True)]
