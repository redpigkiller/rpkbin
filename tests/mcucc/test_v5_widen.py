from copy import deepcopy
from dataclasses import replace

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import InternalError, UnsupportedError
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.ir import Imm, Instr, VReg, ir_eval
from rpkbin.mcucc.lower import _lower
from rpkbin.mcucc.opt import _optimize
from test_formcheck import toy


CONFIG = CompileConfig({"sram": (0x60, 0x6F)}, False)
COMPARE = """\
from rpkbin.mcucc.lang import u8, u16, fn
@fn()
def f(a: u8, b: u8) -> u8:
    if a < b:
        return 1
    return 0
"""
PROVEN = COMPARE.replace("a < b", "(a & 0x3f) < (b >> 1)")


def conditional(*widths):
    target, _, _ = toy("t2", 16)
    return replace(target, forms=tuple(
        replace(form, flags_write={**form.flags_write, "C": "borrow_msb0"})
        if form.op == "cmp" and form.width in widths else form
        for form in target.forms))


def optimized(source, target, name="f"):
    return _optimize(_lower(_parse(source, target, "<test>"))[name])


@pytest.mark.parametrize("source,mode", [(COMPARE, "widen"), (PROVEN, "prove")])
def test_w_keeps_unsigned_ir_semantics(source, mode):
    from rpkbin.mcucc.widen import _widen_comparisons

    target = conditional(8, 16)
    before = optimized(source, target)
    after = deepcopy(before)
    _widen_comparisons(after, target)
    branches = [b.term for b in after.blocks if b.term.op == "br"]
    assert all(branch.meta["msb0_ok"] is True for branch in branches)
    assert all(branch.args[0].width == (16 if mode == "widen" else 8) for branch in branches)
    if mode == "widen":
        old = max(value.id for value in before._vregs)
        assert [value.id for value in branches[0].args] == [old + 1, old + 2]
    for a in (0, 0x7F, 0x80, 0xFF):
        for b in (0, 0x7F, 0x80, 0xFF):
            assert ir_eval(before, (a, b), {}, {}) == ir_eval(after, (a, b), {}, {})


def test_w_free_and_unsupported():
    from rpkbin.mcucc.widen import _widen_comparisons

    free = conditional()
    original = optimized(COMPARE, free)
    function = deepcopy(original)
    _widen_comparisons(function, free)
    assert function == original
    source = COMPARE.replace("a: u8, b: u8", "a: u16, b: u16")
    with pytest.raises(UnsupportedError, match=r"f:4:.*ltu.*16.*最高位"):
        compile_source(source, conditional(8, 16), CONFIG)


def test_v10_independently_rejects_forged_branch_proof(monkeypatch):
    from rpkbin.mcucc import compiler

    def forge(function, target):
        for block in function.blocks:
            if block.term.op == "br":
                block.term.meta["msb0_ok"] = True

    monkeypatch.setattr(compiler, "_widen_comparisons", forge)
    with pytest.raises(InternalError, match="V10"):
        compile_source(COMPARE, conditional(8, 16), CONFIG)


def test_v10_accepts_real_proof_even_if_msb0_meta_is_removed(monkeypatch):
    from rpkbin.mcucc import verify

    verify_original = verify._verify

    def strip(function, target, config, interfaces):
        for block in function._ir.blocks:
            block.term.meta.pop("msb0_ok", None)
        return verify_original(function, target, config, interfaces)

    monkeypatch.setattr(verify, "_verify", strip)
    assert compile_source(PROVEN, conditional(8, 16), CONFIG).functions["f"].verified


@pytest.mark.parametrize(("op", "args", "wanted"), [
    ("const", (Imm(127, 8),), True),
    ("const", (Imm(128, 8),), False),
    ("zext", (VReg(0, 8),), False),
    ("and", (VReg(0, 8), Imm(127, 8)), True),
    ("and", (VReg(0, 8), Imm(128, 8)), False),
    ("shr", (VReg(0, 8), Imm(1, 8)), True),
    ("shr", (VReg(0, 8), Imm(0, 8)), False),
    ("mov", (VReg(0, 8),), False),
    ("load", (Imm(1, 8),), False),
    ("or", (Imm(1, 8), Imm(2, 8)), False),
])
def test_w_same_block_proof(op, args, wanted):
    from rpkbin.mcucc.widen import _msb0

    dst = VReg(1, 8)
    assert _msb0(dst, 8, [Instr(op, dst, args)], 1) is wanted


def test_proof_depth_and_reaching_definition():
    from rpkbin.mcucc.widen import _msb0
    from rpkbin.mcucc.verify import _branch_msb0
    from rpkbin.mcucc.ir import Block

    values = [VReg(index, 8) for index in range(6)]
    body = [Instr("const", values[0], (Imm(127, 8),))]
    body.extend(Instr("mov", dst, (src,)) for src, dst in zip(values, values[1:]))
    for index, wanted in ((4, True), (5, False)):
        assert _msb0(values[index], 8, body, len(body)) is wanted
        block = Block(0, body, Instr("br", None, (values[index], Imm(0, 8))))
        assert _branch_msb0(block, 8) is wanted
    body.append(Instr("const", values[4], (Imm(128, 8),)))
    assert not _msb0(values[4], 8, body, len(body))
    assert not _branch_msb0(Block(0, body, Instr("br", None, (values[4], Imm(0, 8)))), 8)
