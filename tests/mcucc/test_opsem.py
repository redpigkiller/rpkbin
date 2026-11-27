import pytest

from rpkbin.mcucc.opsem import condition_holds, evaluate, flag_value


@pytest.mark.parametrize("width,op,a,b,expected", [
    (8, "mov", 17, None, 17), (16, "mov", 4660, None, 4660),
    (8, "const", 255, None, 255), (16, "const", 65535, None, 65535),
    (8, "add", 255, 1, 0), (16, "add", 65535, 1, 0),
    (8, "sub", 0, 1, 255), (16, "sub", 0, 1, 65535),
    (8, "and", 165, 15, 5), (16, "and", 4660, 255, 52),
    (8, "or", 160, 5, 165), (16, "or", 4608, 52, 4660),
    (8, "xor", 165, 255, 90), (16, "xor", 4660, 65535, 60875),
    (8, "not", 0, None, 255), (16, "not", 0, None, 65535),
    (8, "neg", 1, None, 255), (16, "neg", 1, None, 65535),
    (8, "shl", 128, 1, 0), (16, "shl", 32768, 1, 0),
    (8, "shr", 129, 1, 64), (16, "shr", 32769, 1, 16384),
    (8, "cmp", 0, 1, 255), (16, "cmp", 0, 1, 65535),
])
def test_hand_values(width, op, a, b, expected):
    assert evaluate(op, width, a, b) == expected


@pytest.mark.parametrize("width,a,b", [(8, 255, 1), (16, 65535, 1)])
def test_flags(width, a, b):
    assert flag_value("zero", "add", width, a, b, 0) == 1
    assert flag_value("carry", "add", width, a, b, 0) == 1
    assert flag_value("borrow", "sub", width, 0, 1, a) == 1
    assert flag_value("nborrow", "cmp", width, 1, 0, 1) == 1
    assert flag_value("shift_out", "shr", width, 3, 1, 1) == 1
    assert flag_value("shift_out", "shl", width, (a + 1) // 2, 1, 0) == 1
    assert flag_value("undef", "add", width, a, b, 0) is None


@pytest.mark.parametrize("cond,a,b,result", [
    ("eq", 2, 2, True), ("ne", 2, 2, False), ("ltu", 1, 2, True),
    ("geu", 1, 2, False), ("gtu", 2, 1, True), ("leu", 2, 2, True),
])
def test_conditions(cond, a, b, result):
    assert condition_holds(cond, a, b, 8) is result
