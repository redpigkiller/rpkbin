from dataclasses import replace
from random import Random

import pytest

from rpkbin.mcucc.cli import _main
from rpkbin.mcucc.expand import MInstr
from rpkbin.mcucc.formcheck import _numeric_cases, check_forms
from rpkbin.mcucc.target import I, T
from test_formcheck import toy
from test_review_round5_form import focus, form, sim_with_effects, with_forms
from test_review_round5_static import BRANCH, MEMORY_CALL, compiled, find, reject, with_instruction


@pytest.mark.parametrize("name, operand", [
    ("jz", T("t", rel_bits=3, base="end")),
    ("call", T("t", abs_bits=4)),
])
def test_cr26_narrow_control_form(name, operand):
    target, _, _ = toy("t1", 8)
    target = with_forms(target, **{name: {"operands": (operand,)}})
    target, binding = sim_with_effects(target, {})
    assert check_forms(target, binding, (0,)) == []


def test_v42_distinct_numeric_draws_and_single_value():
    target, binding, _ = toy("t1", 8)
    allowed = (3, 19, 26, 37, 76, 86, 91, 103, 115, 151, 179, 203, 218, 240, 251)
    original = form(target, "ldi")
    limited = replace(original, operands=(original.operands[0], I("k", 8, values=allowed)))
    for seed in range(8):
        draws = _numeric_cases(target, limited, Random(seed))["k"][-4:]
        assert len(set(draws)) == 4 and set(draws) <= set(allowed)
    single = form(target, "shl")
    assert _numeric_cases(target, single, Random(0))["n"] == (1, 1)
    assert check_forms(focus(target, "shl"), binding, (0,)) == []


@pytest.mark.parametrize("bad", [False, True])
def test_c9_overlaps_and_skip_counts(monkeypatch, capsys, bad):
    target, _, _ = toy("t1", 8)
    target = focus(target, "call")
    target = replace(target, spaces=tuple(replace(s, addr_bits=2) if s.kind == "program" else s
                                         for s in target.spaces))
    target = with_forms(target, jmp={"operands": (T("t", abs_bits=2),)},
                        call={"operands": (T("t", abs_bits=3),)}, ret={"bytes": 2})
    calls, returns = [], []
    target, binding = sim_with_effects(target, {
        "call": lambda state, values: calls.append(state.next_pc),
        "ret": lambda state, values: returns.append(state.pc),
    })
    if bad:
        call = form(target, "call")
        target = with_forms(target, call={"encode": lambda values: call.encode(
            {"t": 2 if values["t"] == 3 else values["t"]})})
    monkeypatch.setattr("rpkbin.mcucc.cli.load_target_file", lambda path: (target, binding, None))
    assert _main(["check-forms", "toy.py"]) == int(bad)
    lines = capsys.readouterr().out.splitlines()
    assert lines.count("c9 skipped: out_of_space=4 ret_not_fit=1") == 1
    assert sorted(calls) == ([0, 1, 2, 2] if bad else [0, 1, 2, 3])
    assert sorted(returns) == [0, 1, 2]


@pytest.mark.parametrize("role, after", [
    ("call-save", True), ("call-result", False), ("call-restore", False),
    ("asm-save", True), ("asm-restore", False),
])
def test_v6_roles_must_be_on_correct_side(role, after):
    target, _, _ = toy("t1", 8)
    op = role.split("-")[0]
    body = MEMORY_CALL if op == "call" else 'def f(x:u8):\n    asm("opaque {0}", ins=((x,"r0"),), nbytes=1)\n'
    _, function, interfaces = compiled(target, body)
    index, effect = find(function, op)
    move = MInstr(form(target, "mov"), {"d": "r0", "s": "r0"}, _origin=effect._origin,
                  _block=effect._block, _role=role, _target=target, _abi=function._ir.abi)
    sequence = (effect, move) if after else (move, effect)
    reject(with_instruction(function, index, *sequence), target, interfaces, r"V[56]")


def test_v6_reachable_store_can_still_be_bypassed_by_a_branch():
    target, _, _ = toy("t1", 8)
    _, template, _ = compiled(target, BRANCH)
    _, function, interfaces = compiled(target, "def f(x:u8):\n    mem.sram[48] = x\n")
    index, store = find(function, "st")
    _, compare = find(template, "cmp")
    _, branch = find(template, "br")
    compare = replace(compare, _origin=store._origin, _block=store._block)
    branch = replace(branch, values={"t": ".Ldone"}, _origin=store._origin, _block=store._block)
    done = replace(function.mir[0], _label=".Ldone")
    reject(with_instruction(function, index, compare, branch, store, done),
           target, interfaces, r"V6.*store")
