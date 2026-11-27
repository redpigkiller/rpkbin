import pytest

from rpkbin.mcucc.errors import SourceError
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.lang import run_reference, u8, u16

from test_formcheck import toy


HEADER = "from rpkbin.mcucc.lang import u8, u16, Ptr, mem, fn, extern, asm\n"


def test_function_names_must_be_assembly_identifiers():
    source = HEADER + "def 函式(a: u8) -> u8:\n    return a\n"
    with pytest.raises(SourceError):
        _parse(source, toy("t1", 8)[0], "names.py")
    with pytest.raises(SourceError):
        run_reference(source, "函式", (1,), {"sram": [0] * 256}, addr_bits={"sram": 8})


@pytest.fixture(scope="module")
def target():
    return toy("t2", 8)[0]


@pytest.mark.parametrize("source", [
    "def f(a: u8, b: u8) -> u8:\n    return a + b\n",
    "def f(a: u8) -> u8:\n    s: u8 = 0\n    while a != 0:\n        s = s + a\n        a -= 1\n    return s\n",
    "def f(n: u8) -> u8:\n    s: u8 = 0\n    for i in range(n & 7):\n        if i == 2:\n            continue\n        s += i\n    return s\n",
    "def f(a: u8, b: u8) -> u8:\n    if a < b or (a == 0 and b != 0):\n        return b\n    return a\n",
    "def f(a: u16) -> u8:\n    return u8(a >> 8)\n",
    "def f(a: u8, b: u8) -> u16:\n    return (u16(a) << 8) | u16(b)\n",
    "P = Ptr('sram')\ndef f(p: P) -> u8:\n    mem.sram[p] = u8(3)\n    return mem.sram[p]\n",
    "@extern(args=('r0',), ret='r0', clobbers=())\ndef helper(a: u8) -> u8:\n    return a ^ 0x5a\ndef f(a: u8) -> u8:\n    return helper(a)\n",
    "LIMIT = (3 * 4) // 2\ndef f(a: u8) -> u8:\n    return a + LIMIT\n",
    "def f(a: u8, b: u8) -> u8:\n    return a if a > b else b\n",
])
def test_accepted_language(source, target):
    assert _parse(HEADER + source, target, "accepted.py").functions


@pytest.mark.parametrize("source", [
    "import os\n", "from math import sqrt\n", "x = 'text'\n",
    "def f(a) -> u8:\n    return a\n", "def f(a: u8 = 1) -> u8:\n    return a\n",
    "def f(a: u8) -> u8:\n    b = 1\n    return a\n",
    "def f(a: u8) -> u8:\n    b = a * 2\n    return b\n",
    "def f(a: u8) -> u8:\n    return a << a\n",
    "def f(a: u8) -> u8:\n    return a + 256\n",
    "def f(a: u8) -> u8:\n    return a + -1\n",
    "def f(a: u8) -> u8:\n    return b\n",
    "def f(a: u8) -> u8:\n    if a != 0:\n        b = a\n    return b\n",
    "def f(a: u8) -> u8:\n    ok = a < 2\n    return a\n",
    "def f(a: u8) -> u8:\n    if 0 < a < 3:\n        return a\n    return a\n",
    "def f(a: u8) -> u8:\n    while a:\n        pass\n    else:\n        pass\n    return a\n",
    "def f(a: u8) -> u8:\n    for i in range(3):\n        i = a\n    return a\n",
    "def f(a: u8) -> u8:\n    for i in range(3):\n        pass\n    return i\n",
    "def f(a: u8) -> u8:\n    return mem.sram[a]\n",
    "def f(a: u8) -> u8:\n    assert a\n    return a\n",
    "def f(a: u8) -> u8:\n    return f(a)\n",
])
def test_rejected_language(source, target):
    with pytest.raises(SourceError, match=r"rejected.py:\d+:"):
        _parse(HEADER + source, target, "rejected.py")


@pytest.mark.parametrize("cls,maximum", [(u8, 255), (u16, 65535)])
def test_wrapped_unsigned_values(cls, maximum):
    assert int(cls(maximum) + 1) == 0
    assert int(1 + cls(maximum)) == 0
    assert int(cls(0) - 1) == maximum
    assert int(~cls(0)) == maximum
    assert int(-cls(1)) == maximum
    with pytest.raises(ValueError):
        cls(maximum + 1)
    with pytest.raises(ValueError):
        cls(1) + maximum + 1 + (maximum + 1)
    with pytest.raises(TypeError):
        cls(1) + (u16(1) if cls is u8 else u8(1))


def test_reference_loop_and_memory_isolation():
    source = HEADER + "def f(n: u8) -> u8:\n    s: u8 = 0\n    for i in range(n & 7):\n        if i == 2:\n            continue\n        s += i\n    mem.sram[48] = s\n    return s\n"
    memory = {"sram": [0] * 256}
    result, changed = run_reference(source, "f", (5,), memory, addr_bits={"sram": 8})
    assert int(result) == 8 and changed["sram"][48] == 8
    assert memory["sram"][48] == 0
    result, changed = run_reference(source, "f", (1,), memory, addr_bits={"sram": 8})
    assert int(result) == 0 and changed["sram"][48] == 0


def test_pointer_namespace_created_before_execution():
    source = HEADER + "P = Ptr('sram')\ndef f(a: P) -> P:\n    return a + P(1)\n"
    assert int(run_reference(source, "f", (255,), {"sram": [0] * 256}, addr_bits={"sram": 8})[0]) == 0
    assert int(run_reference(source, "f", (255,), {"sram": [0] * 65536}, addr_bits={"sram": 16})[0]) == 256


@pytest.mark.parametrize("body,result", [
    ("return 255 + 1", 0),
    ("return 1 - 2", 255),
    ("mem.sram[48] = 3\n    return mem.sram[48]", 3),
    ("return u8(u16(4660))", 52),
    ("return ~0", 255),
    ("return (255 + 1) + u8(1)", 1),
])
def test_reference_contextual_constants(body, result):
    source = HEADER + "def f() -> u8:\n    " + body + "\n"
    assert int(run_reference(source, "f", (), {"sram": [0] * 256}, addr_bits={"sram": 8})[0]) == result


def test_reference_internal_call_converts_parameter_values():
    source = HEADER + "def helper(a: u8) -> u8:\n    return a + 1\ndef f() -> u8:\n    return helper(255)\n"
    assert int(run_reference(source, "f", (), {}, addr_bits={})[0]) == 0


def test_q1_target_dependent_checks_stay_in_compiler():
    source = HEADER + "def r0(a: u8) -> u8:\n    return a\n"
    narrow = toy("t0", 8)[0]
    symmetric = toy("t1", 8)[0]
    assert _parse(source, narrow, "names.py").functions
    with pytest.raises(SourceError, match="conflicts with register"):
        _parse(source, symmetric, "names.py")
    assert int(run_reference(source, "r0", (1,), {"sram": [0] * 256}, addr_bits={"sram": 8})[0]) == 1


def test_q1_runtime_does_not_validate_register_declarations(target):
    source = HEADER + "@fn(args=('unknown',), ret='unknown')\ndef f(a: u8) -> u8:\n    return a\n"
    with pytest.raises(SourceError, match="ABI location"):
        _parse(source, target, "names.py")
    assert int(run_reference(source, "f", (7,), {}, addr_bits={})[0]) == 7


@pytest.mark.parametrize("body", [
    "del a", "try:\n        pass\n    except:\n        pass",
    "with a:\n        pass", "class Thing:\n        pass", "return (lambda: a)()",
    "def nested():\n        pass", "global a", "nonlocal a", "import os",
    "return [a for i in range(2)]", "return f'{a}'", "return (a := 1)",
    "yield a", "return 1.5", "return None", "return b'bad'", "return 'bad'",
    "return a // 2", "return a / 2", "return a % 2", "return a ** 2",
    "return a << 8", "return u8(256)", "return u16(a)",
    "a: u8 = u8(1)", "for i in range(0, 4, 2):\n        pass",
    "for i in range(3):\n        pass\n    else:\n        pass",
    "return (a if a else u16(1))",
])
def test_source_only_errors_are_also_checked_at_runtime(body, target):
    source = HEADER + "def f(a: u8) -> u8:\n    " + body + "\n    return a\n"
    with pytest.raises(SourceError):
        _parse(source, target, "invalid.py")
    with pytest.raises(SourceError):
        run_reference(source, "f", (1,), {"sram": [0] * 256}, addr_bits={"sram": 8})


def test_all_corpus_functions_typecheck_and_execute(target):
    from pathlib import Path
    source = (Path(__file__).parent / "corpus" / "programs.py").read_text(encoding="utf-8")
    typed = _parse(source, target, "programs.py")
    assert len(typed.functions) == 22
    for name, function in typed.functions.items():
        args = tuple(128 if ty.space else 3 for _, ty in function.params)
        result, memory = run_reference(source, name, args, {"sram": [1] * 256}, addr_bits={"sram": 8})
        assert result is not None and 0 <= int(result) < 1 << function.ret.width


def test_reference_timeout_and_missing_pointer_width():
    source = HEADER + "def f(a: u8) -> u8:\n    while a != 0:\n        pass\n    return a\n"
    with pytest.raises(TimeoutError):
        run_reference(source, "f", (1,), {}, addr_bits={})
    source = HEADER + "P = Ptr('missing')\ndef f(a: P) -> P:\n    return a\n"
    with pytest.raises(SourceError, match="Ptr"):
        run_reference(source, "f", (1,), {}, addr_bits={})
