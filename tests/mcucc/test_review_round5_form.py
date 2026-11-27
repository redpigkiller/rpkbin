"""Malformed form contracts on the existing toy simulator."""

from dataclasses import replace
from itertools import product
from unittest.mock import patch

import pytest

from rpkbin.mcucc.formcheck import check_forms
from test_formcheck import toy


SEEDS = (0,)


@pytest.fixture(scope="module")
def t1():
    return toy("t1", 8)


@pytest.fixture(scope="module")
def t2():
    return toy("t2", 8)


def focus(target, *names):
    # A Target always requires a jump and a return form.
    keep = {*names, "jmp", "ret"}
    return replace(target, forms=tuple(f for f in target.forms if f.id in keep), long_branch="none")


def form(target, name):
    return next(f for f in target.forms if f.id == name)


def with_forms(target, **changes):
    return replace(target, forms=tuple(replace(f, **changes[f.id]) if f.id in changes else f
                                       for f in target.forms))


def sim_with_effects(target, effects):
    """Keep the toy encoding and run an extra effect inside the selected step."""
    import machine

    original = machine._handler

    def handler(form, fixed, variable, names):
        execute = original(form, fixed, variable, names)
        if form.id not in effects:
            return execute
        effect = effects[form.id]
        if variable is None:
            def fixed_step(state):
                execute(state)
                effect(state, fixed)
            return fixed_step

        def variable_step(state, k):
            execute(state, k)
            effect(state, {**fixed, variable[0]: k})
        return variable_step

    with patch.object(machine, "_handler", handler):
        return machine.verified(target, ())[:2]


def load(binding, code, **registers):
    sim = binding.make_sim()
    sim.load_cells(binding.program_space, code)
    for name, value in registers.items():
        sim.poke((binding.names or {}).get(name, name), value)
    return sim


def reject(target, binding, check, *names):
    failures = check_forms(target, binding, SEEDS)
    for name in names:
        assert any(f.form_id == name and f.check == check for f in failures), failures


def test_c1_instruction_advances_pc_by_wrong_length(t1):
    target, binding, _ = t1
    assert check_forms(focus(target, "mov"), binding, SEEDS) == []

    def advance_pc(state, values):
        state.next_pc += 1

    bad, sim = sim_with_effects(target, {"mov": advance_pc})
    reject(focus(bad, "mov"), sim, "C1", "mov")


def test_c2_wrong_result_only_when_source_and_destination_overlap(t1):
    target, binding, _ = t1
    assert check_forms(focus(target, "add_rr"), binding, SEEDS) == []

    def corrupt_overlap(state, values):
        if values["d"] == values["s"]:
            state.reg[values["d"]] ^= 1

    bad, sim = sim_with_effects(target, {"add_rr": corrupt_overlap})
    reject(focus(bad, "add_rr"), sim, "C2", "add_rr")


def test_c3_form_omits_an_actual_register_clobber(t1):
    target = t1[0]

    def change_r3(state, values):
        state.reg["r3"] ^= 1

    device, sim = sim_with_effects(target, {"st": change_r3})
    bad = focus(device, "st")
    control = with_forms(bad, st={"clobbers": ("r3",)})
    assert check_forms(control, sim, SEEDS) == []
    reject(bad, sim, "C3", "st")


def test_c4_const_zero_flag_is_wrong_only_for_zero_immediate(t1):
    target = with_forms(t1[0], ldi={"flags_write": {"Z": "zero"}})
    control, sim = sim_with_effects(target, {})
    assert check_forms(focus(control, "ldi"), sim, SEEDS) == []

    def drop_zero(state, values):
        if values["k"] == 0:
            state.reg["status.Z"] = 0

    bad, sim = sim_with_effects(target, {"ldi": drop_zero})
    reject(focus(bad, "ldi"), sim, "C4", "ldi")


def test_c5_wrong_store_value_only_at_maximum_address(t1):
    target, binding, _ = t1
    assert check_forms(focus(target, "st"), binding, SEEDS) == []

    def corrupt_max_address(state, values):
        if values["m"] == 255:
            state.mem["sram"][255] ^= 1

    bad, sim = sim_with_effects(target, {"st": corrupt_max_address})
    reject(focus(bad, "st"), sim, "C5", "st")


def test_c6_not_taken_branch_jumps_only_at_positive_boundary(t1):
    target, binding, _ = t1
    assert check_forms(focus(target, "jz"), binding, SEEDS) == []

    def force_branch(state, values):
        if values["t"] == 127 and state.reg["status.Z"] == 0:
            state.next_pc += 127

    bad, sim = sim_with_effects(target, {"jz": force_branch})
    for device, simulator, expected_pc in ((target, binding, 2), (bad, sim, 129)):
        branch = form(device, "jz")
        witness = load(simulator, dict(enumerate(branch.encode({"t": 127}))), Z=0)
        witness.step()
        assert witness.read(simulator.pc) == expected_pc
    reject(focus(bad, "jz"), sim, "C6", "jz")


def test_c7_branch_fails_only_when_paired_with_compare(t1):
    target, binding, _ = t1
    control = focus(target, "cmp_rr", "jz")
    assert check_forms(control, binding, SEEDS) == []
    compare, branch = form(control, "cmp_rr"), form(control, "jz")
    regs = target.classes[0].members
    compare_cells = {compare.encode({"d": a, "s": b})[0] for a, b in product(regs, repeat=2)}
    assert compare.bytes == 1

    def corrupt_paired_branch(state, values):
        if state.pc >= 1 and state.mem["rom"][state.pc - 1] in compare_cells:
            if state.reg["status.Z"] == 1:
                state.next_pc = state.pc + branch.bytes

    bad, sim = sim_with_effects(target, {"jz": corrupt_paired_branch})
    assert check_forms(focus(bad, "jz"), sim, SEEDS) == []
    reject(focus(bad, "cmp_rr", "jz"), sim, "C7", "cmp_rr")


def test_c8_pop_returns_wrong_saved_value_only_into_x1(t2):
    target, binding, _ = t2
    assert check_forms(focus(target, "push", "pop"), binding, SEEDS) == []

    def corrupt_pop(state, values):
        if values["d"] == "x1":
            state.reg["x1"] ^= 1

    bad, sim = sim_with_effects(target, {"pop": corrupt_pop})
    reject(focus(bad, "push", "pop"), sim, "C8", "pop")


@pytest.mark.parametrize("missing", [("call",), ("ret",), ("call", "ret")],
                         ids=["call-only", "ret-only", "both-cancel"])
def test_c9_checks_call_and_ret_clobbers_separately(t1, missing):
    def flip_r3(state, values):
        state.reg["r3"] ^= 1

    target, sim = sim_with_effects(t1[0], {"call": flip_r3, "ret": flip_r3})
    target = focus(target, "call", "ret")
    control = with_forms(target, call={"clobbers": ("r3",)}, ret={"clobbers": ("r3",)})
    assert check_forms(control, sim, SEEDS) == []
    call, ret = form(control, "call"), form(control, "ret")
    program = dict(enumerate((*call.encode({"t": 16}), *sim.halt_cells)))
    program[16] = ret.encode({})[0]
    witness = load(sim, program, r3=9)
    for pc, r3 in ((16, 8), (call.bytes, 9)):
        witness.step()
        assert (witness.read(sim.pc), witness.read("r3")) == (pc, r3)
    bad = with_forms(control, **{name: {"clobbers": ()} for name in missing})
    reject(bad, sim, "C9", *missing)


def test_c9_wrong_call_target_only_for_one_hot_bit_8(t1):
    target, binding, _ = t1
    control = focus(target, "call", "ret")
    assert check_forms(control, binding, SEEDS) == []
    call, ret = form(control, "call"), form(control, "ret")

    def encode(values):
        return call.encode({"t": 16 if values["t"] == 256 else values["t"]})

    bad = with_forms(control, call={"encode": encode})
    for candidate, expected_pc in ((call, 256), (form(bad, "call"), 16)):
        program = dict(enumerate((*candidate.encode({"t": 256}), *binding.halt_cells)))
        program[256] = ret.encode({})[0]
        witness = load(binding, program)
        witness.step()
        assert witness.read(binding.pc) == expected_pc
    failures = check_forms(bad, binding, (0, 7, 71))
    assert {failure.seed for failure in failures
            if failure.form_id == "call" and failure.check == "C9"} == {0, 7, 71}


@pytest.mark.parametrize("defect", ["cell-range", "length"])
def test_c10_invalid_encoding_only_at_negative_branch_boundary(t1, defect):
    target, binding, _ = t1
    control = focus(target, "jmp")
    assert check_forms(control, binding, SEEDS) == []
    jump = form(control, "jmp")

    def encode(values):
        cells = jump.encode(values)
        if values["t"] != -128:
            return cells
        return (256, cells[1]) if defect == "cell-range" else cells[:1]

    bad = with_forms(control, jmp={"encode": encode})
    reject(bad, binding, "C10", "jmp")
