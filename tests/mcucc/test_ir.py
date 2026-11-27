from copy import deepcopy
from pathlib import Path

import pytest

from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.errors import SourceError
from rpkbin.mcucc.ir import Imm, ir_eval
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.lower import _lower
from rpkbin.mcucc.opt import _optimize
from test_formcheck import toy
from test_lang import HEADER


def lowered(source):
    return _lower(_parse(source, toy("t2", 8)[0], "input.py"))


def instructions(function):
    return [instruction for block in function.blocks for instruction in block.instrs]


@pytest.mark.parametrize("expression", ("a + 0", "a - 0", "a | 0", "a ^ 0", "a << 0", "a >> 0", "a & 255", "a & 0", "a ^ a", "a - a"))
def test_algebra_preserves_values(expression):
    source = HEADER + "def f(a: u8) -> u8:\n    return " + expression + "\n"
    module = lowered(source)
    before = deepcopy(module["f"])
    _optimize(module["f"])
    assert not any(i.op in ("add", "sub", "or", "xor", "shl", "shr", "and") for i in instructions(module["f"]))
    for value in (0, 1, 127, 128, 255):
        assert ir_eval(before, (value,), {}, module) == ir_eval(module["f"], (value,), {}, module)


def test_fold_and_pack_patterns():
    module = lowered(HEADER + "def f(a: u8) -> u8:\n    return a + 3 + 4\ndef w(a: u8, b: u8) -> u16:\n    return (u16(a) << 8) | u16(b)\n")
    _optimize(module["f"])
    adds = [i for i in instructions(module["f"]) if i.op == "add"]
    assert len(adds) == 1 and adds[0].args[1] == Imm(7, 8)
    _optimize(module["w"])
    assert [i.op for i in instructions(module["w"]) if i.op != "entry"] == ["pack"]
    assert ir_eval(module["w"], (0x12, 0x34), {}, module)[0] == 0x1234


@pytest.mark.parametrize("source,args,expected", [
    ("def f(a: u8, b: u8) -> u8:\n    x = a\n    y = x\n    x = b\n    return y\n", (7, 9), 7),
    ("def f(a: u8) -> u8:\n    x = a\n    if a != 0:\n        x = x + 1\n    return x\n", (3,), 4),
    ("def f(a: u8) -> u8:\n    x = a\n    while a != 0:\n        x = x + 1\n        a = a - 1\n    return x\n", (3,), 6),
])
def test_copy_propagation_with_mutable_variables(source, args, expected):
    module = lowered(HEADER + source)
    assert ir_eval(module["f"], args, {}, module)[0] == expected
    _optimize(module["f"])
    assert ir_eval(module["f"], args, {}, module)[0] == expected


@pytest.mark.parametrize("expression", ("(a+b) ^ (a+b)", "(a&b) ^ (a&b)", "(a|b) ^ (a|b)"))
def test_value_numbering_and_dead_code(expression):
    module = lowered(HEADER + "def f(a: u8, b: u8) -> u8:\n    unused = a + 9\n    return " + expression + "\n")
    _optimize(module["f"])
    assert not any(i.op in ("add", "and", "or", "xor") for i in instructions(module["f"]))
    assert ir_eval(module["f"], (17, 23), {}, module)[0] == 0


def test_memory_value_numbering_stops_at_store():
    module = lowered(HEADER + "def f() -> u8:\n    a = mem.sram[48]\n    mem.sram[48] = u8(3)\n    b = mem.sram[48]\n    return a + b\n")
    _optimize(module["f"])
    assert ir_eval(module["f"], (), {"sram": [7] * 256}, module)[0] == 10


@pytest.mark.parametrize("condition,expected", (("u8(0)", 3), ("u8(1)", 2), ("3 < 2", 3)))
def test_constant_cfg_simplification(condition, expected):
    module = lowered(HEADER + "def f() -> u8:\n    if " + condition + ":\n        return 2\n    return 3\n")
    original_ids = {b.id for b in module["f"].blocks}
    _optimize(module["f"])
    assert len(module["f"].blocks) == 1
    assert {b.id for b in module["f"].blocks} <= original_ids
    assert ir_eval(module["f"], (), {}, module)[0] == expected


def test_for_stop_is_evaluated_once_and_continue_increments():
    source = HEADER + "def f(n: u8) -> u8:\n    s: u8 = 0\n    for i in range(n):\n        n = 0\n        if i == 1:\n            continue\n        s = s + 1\n    return s\n"
    module = lowered(source)
    for _ in range(2):
        assert ir_eval(module["f"], (4,), {}, module)[0] == 3
        _optimize(module["f"])


def test_corpus_ir_matches_reference_before_and_after_optimization():
    source = (Path(__file__).parent / "corpus" / "programs.py").read_text(encoding="utf-8")
    module = lowered(source)
    for name, function in module.items():
        args = tuple(128 if name in ("mem_sum", "load") and index == 0 else 3 for index in range(len(function.params)))
        memory = {"sram": [1] * 256}
        expected = run_reference(source, name, args, memory, addr_bits={"sram": 8})
        actual = ir_eval(function, args, memory, module)
        assert (int(expected[0]), expected[1]) == actual, name
        _optimize(function)
        assert ir_eval(function, args, memory, module) == actual, name


@pytest.mark.parametrize("seed", range(36))
def test_generated_ir_matches_reference(seed):
    from gen_programs import generate
    source = generate(seed)
    module = lowered(source)
    function = module["generated"]
    for number in (0, 7, 255):
        args = tuple((number + i) & 255 for i in range(len(function.params)))
        memory = {"sram": [3] * 256}
        expected, changed = run_reference(source, "generated", args, memory, addr_bits={"sram": 8})
        assert ir_eval(function, args, memory, module) == (int(expected), changed)
        _optimize(function)
        assert ir_eval(function, args, memory, module) == (int(expected), changed)


def test_ir_calls_are_kept_even_when_result_is_dead():
    source = HEADER + "@extern(args=('r0',), ret='r0', clobbers=())\ndef helper(a: u8) -> u8:\n    mem.sram[48] = a\n    return a + 1\ndef f(a: u8) -> u8:\n    helper(a)\n    return a\n"
    module = lowered(source)
    _optimize(module["f"])
    assert any(i.op == "call" for i in instructions(module["f"]))
    result, memory = ir_eval(module["f"], (17,), {"sram": [0] * 256}, module)
    assert result == 17 and memory["sram"][48] == 17


def test_unreachable_cfg_is_still_well_typed_and_removed():
    module = lowered(HEADER + "def f(a: u8) -> u8:\n    return a\n    if a == 0:\n        pass\n")
    _optimize(module["f"])
    assert len(module["f"].blocks) == 1
    assert ir_eval(module["f"], (9,), {}, module)[0] == 9


def test_for_counter_cannot_change_an_existing_variable_type():
    source = HEADER + "def f(a: u8) -> u8:\n    i: u8 = 0\n    for i in range(u16(3)):\n        pass\n    return a\n"
    with pytest.raises(SourceError):
        lowered(source)
