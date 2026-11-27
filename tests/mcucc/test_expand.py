from dataclasses import replace

import pytest

from rpkbin.mcucc.expand import _Context, _parallel, expand, expand_branch
from rpkbin.mcucc.ir import Imm, Instr, VReg
from rpkbin.mcucc.target import Form, M, MemLoc, R, RegLoc, SlotLoc, units
from test_formcheck import toy


def context(name="t1", width=8):
    target = toy(name, width)[0]
    return _Context(target, {"sram": (0x20, 0x2f) if name == "t0" else (0x60, 0x6f)})


def instruction(op="add", width=8):
    return Instr(op, VReg(2, width), (VReg(0, width), VReg(1, width)))


def test_tied_destination_and_coalescing():
    ctx = context()
    ins = instruction()
    result = expand(ctx, ins, {ins.dst: RegLoc("r0"), ins.args[0]: RegLoc("r0"), ins.args[1]: RegLoc("r1")}, set())
    assert result.cost == (1, 1) and result.mir[0].form.id == "add_rr"
    result = expand(ctx, Instr("mov", ins.dst, (ins.args[0],)), {ins.dst: RegLoc("r0"), ins.args[0]: RegLoc("r0")}, set())
    assert result.cost == (0, 0)


def test_preferred_pre_move_and_noncommutative_post_move():
    ctx = context()
    ins = instruction()
    locations = {ins.dst: RegLoc("r2"), ins.args[0]: RegLoc("r0"), ins.args[1]: RegLoc("r1")}
    result = expand(ctx, ins, locations, units(ctx.target, RegLoc("r0")))
    assert [m.form.id for m in result.mir] == ["mov", "add_rr"]
    ins.op = "sub"
    result = expand(ctx, ins, {ins.dst: RegLoc("r0"), ins.args[0]: RegLoc("r1"), ins.args[1]: RegLoc("r0")}, set())
    assert [m.form.id for m in result.mir] == ["sub_rr", "mov"]


def test_asymmetric_scratch_and_post_move():
    ctx = context("t2")
    ins = instruction()
    result = expand(ctx, ins, {ins.dst: RegLoc("r4"), ins.args[0]: RegLoc("r4"), ins.args[1]: RegLoc("r5")}, set())
    assert [m.form.id for m in result.mir] == ["mov", "add_rr", "mov"]
    assert result.cost == (3, 3)


def test_busy_and_move_path_clobbers():
    ctx = context()
    ins = instruction()
    locations = {ins.dst: SlotLoc("vreg", 2, 0, 1), ins.args[0]: SlotLoc("vreg", 0, 0, 1), ins.args[1]: SlotLoc("vreg", 1, 0, 1)}
    assert expand(ctx, ins, locations, {"r0", "r1", "r2", "r3"}) is None
    forms = tuple(replace(f, clobbers=("r2",)) if f.op in ("add", "mov") else f for f in ctx.target.forms)
    ctx = _Context(replace(ctx.target, forms=forms), ctx.pool)
    locations = {ins.dst: RegLoc("r0"), ins.args[0]: RegLoc("r0"), ins.args[1]: RegLoc("r1")}
    assert expand(ctx, ins, locations, {"r2"}) is None
    assert expand(ctx, Instr("mov", ins.dst, (ins.args[1],)), locations, {"r2"}) is None


def test_flags_survival_applies_to_every_move():
    ctx = context()
    forms = tuple(replace(f, flags_write={"Z": "zero"}) if f.op == "mov" else f for f in ctx.target.forms)
    ctx = _Context(replace(ctx.target, forms=forms), ctx.pool)
    d, a = VReg(1, 8), VReg(0, 8)
    locations = {d: RegLoc("r1"), a: RegLoc("r0")}
    assert expand(ctx, Instr("mov", d, (a,)), locations, set(), True) is None
    assert expand(ctx, Instr("mov", d, (a,)), locations, set(), False).cost == (1, 1)


def test_repeat_shift_and_zero_cost_parts():
    ctx = context("t0")
    d, a = VReg(1, 8), VReg(0, 8)
    result = expand(ctx, Instr("shl", d, (a, Imm(3, 8))), {d: RegLoc("w"), a: RegLoc("w")}, set())
    assert [m.form.id for m in result.mir] == ["shl"] * 3
    ctx = context("t2")
    d, a = VReg(1, 8), VReg(0, 16)
    assert expand(ctx, Instr("trunc", d, (a,)), {d: RegLoc("r0"), a: RegLoc("x0")}, {"r0", "r1"}).cost == (0, 0)
    wide, narrow = VReg(2, 16), VReg(3, 8)
    assert expand(ctx, Instr("zext", wide, (narrow,)), {wide: RegLoc("x0"), narrow: RegLoc("r0")}, {"r0"}).cost == (2, 1)


def test_pack_cycle_and_parallel_slot_part_order():
    ctx = context("t2")
    d, h, lo = VReg(2, 16), VReg(0, 8), VReg(1, 8)
    result = expand(ctx, Instr("pack", d, (h, lo)), {d: RegLoc("x0"), h: RegLoc("r0"), lo: RegLoc("r1")}, set())
    assert result.cost == (3, 3)
    assert [m.values for m in result.mir] == [{"d": "r2", "s": "r1"}, {"d": "r1", "s": "r0"}, {"d": "r0", "s": "r2"}]
    destination = SlotLoc("vreg", 2, 0, 2)
    result = expand(ctx, Instr("pack", d, (h, h)), {d: destination, h: RegLoc("r0")}, set())
    assert [m.values["m"].first for m in result.mir] == [0, 1]


def test_slot_identity_breaks_commutative_tie():
    ctx = context("t0")
    ins = instruction()
    result = expand(ctx, ins, {ins.dst: RegLoc("w"), ins.args[0]: SlotLoc("vreg", 1, 0, 1), ins.args[1]: SlotLoc("vreg", 2, 0, 1)}, set())
    assert result.cost == (4, 2)
    assert [m.values["m"].key for m in result.mir] == [1, 2]


def test_slot_range_is_checked_for_each_memory_operand():
    ctx = context("t2", 16)
    target = replace(ctx.target, forms=tuple(replace(f, operands=tuple(replace(o, bits=8) if isinstance(o, M) else o for o in f.operands)) for f in ctx.target.forms))
    ctx = _Context(target, {"sram": (0xfe, 0x101)})
    d, a = VReg(1, 8), VReg(0, 8)
    ins = Instr("mov", d, (a,))
    assert expand(ctx, ins, {d: RegLoc("r0"), a: SlotLoc("vreg", 0, 0, 1)}, set()) is None
    assert expand(ctx, ins, {d: RegLoc("r0"), a: MemLoc("sram", 0xfe)}, set()).cost == (3, 1)


def test_three_operand_form_avoids_pre_move():
    ctx = context()
    form = Form("three", "add", 8, (R("d", "g"), R("a", "g"), R("b", "g")), {"dst": "d", "a": "a", "b": "b"}, 1, "three {d}, {a}, {b}")
    ctx = _Context(replace(ctx.target, forms=(form, *ctx.target.forms)), ctx.pool)
    ins = instruction()
    result = expand(ctx, ins, {ins.dst: RegLoc("r2"), ins.args[0]: RegLoc("r0"), ins.args[1]: RegLoc("r1")}, {"r0"})
    assert result.cost == (1, 1) and result.mir[0].form.id == "three"


def test_parallel_cycle_fallback_is_local_and_deterministic():
    ctx = context("t2")
    d, h, lo = VReg(2, 16), VReg(0, 8), VReg(1, 8)
    ins = Instr("pack", d, (h, lo))
    locations = {d: RegLoc("x0"), h: RegLoc("r0"), lo: RegLoc("r1")}
    busy = {"r2", "r3", "r4", "r5"}
    first = expand(ctx, ins, locations, busy)
    second = expand(ctx, ins, locations, busy)
    assert first.cost == (5, 3) and first.mir == second.mir
    assert first.mir[0].values["m"] == SlotLoc("ptemp", 0, 0, 1)
    assert _parallel(ctx, [(RegLoc("x1"), RegLoc("x0"), 16), (RegLoc("x0"), RegLoc("x1"), 16)], set()) is not None


def test_indirect_store_may_overwrite_any_busy_memory_cell():
    ctx = context("t2")
    p, value = VReg(0, 8), VReg(1, 8)
    ins = Instr("store", None, (p, value), {"space": "sram"})
    assert expand(ctx, ins, {p: RegLoc("r0"), value: RegLoc("r1")}, {("sram", 0x30)}) is None


@pytest.mark.parametrize("name,expected", [("t1", "jc"), ("t2", "jnc")])
def test_branch_semantic_polarity_and_adjacency(name, expected):
    ctx = context(name)
    a, b = VReg(0, 8), VReg(1, 8)
    result = expand_branch(ctx, "ltu", a, b, {a: RegLoc("r0"), b: RegLoc("r1")}, set(), 2)
    assert [m.form.op for m in result.mir] == ["cmp", "br"]
    assert result.mir[-1].form.id == expected


def test_immediate_left_compare_is_mirrored():
    ctx = context()
    b = VReg(0, 8)
    result = expand_branch(ctx, "gtu", Imm(3, 8), b, {b: RegLoc("r0")}, set(), 2)
    assert [m.form.id for m in result.mir] == ["cmp_ri", "jc"]
