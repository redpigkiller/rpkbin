"""Exact compatibility baselines for existing MCU descriptions."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.assembler import _isa_hash

from .test_assembler import machine
from .toys import toy_a, toy_b, toy_c, toy_r

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_SIGNATURE_KEY = ',"signature":'


def _user_example(page, name):
    path = _ROOT / page
    for block in re.findall(
        r"^```python\n(.*?)^```",
        path.read_text(encoding="utf-8"),
        re.MULTILINE | re.DOTALL,
    ):
        if f'mcu = MCU("{name}")' in block:
            declaration = re.split(r"\n(?:source|sim) = ", block, maxsplit=1)[0]
            namespace = {}
            exec(compile(declaration, str(path), "exec"), namespace)
            return namespace["mcu"]
    pytest.fail(f"Missing MCU {name!r} in {page}")


# Frozen declarations for the two examples without an importable factory.
def _odd_cells():
    m = isa.MCU("odd_cells")
    m.space(
        "prog",
        cell_bits=5,
        size=12,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    m.reg("cursor", bits=4, reset=0, is_pc=True)
    for name in (
        "on_undefined_opcode",
        "on_out_of_range",
        "on_write_to_readonly",
        "on_uninitialized_read",
    ):
        getattr(m, name)("stop")

    @m.instr("1-aaa aa-00", syntax="TAKE {a}", cycles=1)
    def take(st, a):
        st.halt(str(a))

    return m


def _example():
    mcu = isa.MCU("example")
    mcu.space(
        "prog",
        cell_bits=16,
        size=64,
        access="ro",
        reset_state="uninitialized",
        init=0,
        is_program=True,
    )
    mcu.space(
        "data", cell_bits=16, size=16, access="rw", reset_state="initialized", init=0
    )
    mcu.reg("pc", bits=6, reset=0, is_pc=True)
    mcu.reg("r0", bits=16, reset=0)
    mcu.reg("flags", bits=1, reset=0, fields={"Z": 0})
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 0000 iiiiiiii", syntax="LOADI {i}", cycles=1)
    def loadi(st, i):
        st.reg["r0"] = i
        st.reg["flags.Z"] = int(st.reg["r0"] == 0)

    @mcu.instr("1111 1111 1111 1111", syntax="STOP", cycles=1)
    def stop(st):
        st.halt("stop")

    return mcu


# Frozen pre-v1.2 demo_mcu declaration (0eab081:playground/isasim_demo/demo_mcu.py).
_FROZEN_DEMO_MCU_V11 = '''"""Demo MCU for testing the isasim Web Debugger (registers + data memory + CALL/RET)."""

from rpkbin.isasim import MCU, Operand, helpers

mcu = MCU("demo_mcu")
mcu.space(
    "prog",
    cell_bits=16,
    size=256,
    access="ro",
    reset_state="uninitialized",
    init=0,
    is_program=True,
)
mcu.space(
    "data",
    cell_bits=16,
    size=64,
    access="rw",
    reset_state="uninitialized",
    init=0,
)
mcu.space(
    "stack",
    cell_bits=8,
    size=16,
    access="rw",
    reset_state="initialized",
    init=0,
)

mcu.reg("pc", bits=8, reset=0, is_pc=True)
mcu.reg("sp", bits=4, reset=0)
for _n in range(4):
    mcu.reg(f"r{_n}", bits=16, reset=0)
mcu.reg("flags", bits=3, reset=0, fields={"Z": 0, "N": 1, "C": 2})
mcu._registers["flags"]["is_flag"] = True

mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")


def _set_flags(st, res, carry):
    st.reg["flags.Z"] = int(res == 0)
    st.reg["flags.N"] = helpers.bit(res, 15)
    st.reg["flags.C"] = carry


@mcu.instr("0001 dd 00 iiiiiiii", syntax="LDI r{d}, {i}", cycles=1)
def ldi(st, d, i):
    st.reg[f"r{d}"] = i


@mcu.instr("1010 dd 00 0000 0000 iiiiiiiiiiiiiiii", syntax="MOVI16 r{d}, {i}", cycles=2)
def movi16(st, d, i):
    st.reg[f"r{d}"] = i


@mcu.instr("0010 dd ss 00000000", syntax="ADD r{d}, r{s}", cycles=1)
def add(st, d, s):
    res = helpers.add_with_carry(st.reg[f"r{d}"], st.reg[f"r{s}"], 0, 16)
    st.reg[f"r{d}"] = res.result
    _set_flags(st, res.result, res.carry)


@mcu.instr("0011 dd ss 00000000", syntax="SUB r{d}, r{s}", cycles=1)
def sub(st, d, s):
    res = helpers.sub_with_borrow(st.reg[f"r{d}"], st.reg[f"r{s}"], 0, 16)
    st.reg[f"r{d}"] = res.result
    _set_flags(st, res.result, res.borrow)


@mcu.instr("0100 dd 00 aaaaaaaa", syntax="LD r{d}, [{a}]", cycles=2)
def ld(st, d, a):
    st.reg[f"r{d}"] = st.mem["data"][a]


@mcu.instr("0101 ss 00 aaaaaaaa", syntax="ST r{s}, [{a}]", cycles=2)
def store(st, s, a):
    st.mem["data"][a] = st.reg[f"r{s}"]


@mcu.instr(
    "0110 0000 aaaaaaaa",
    syntax="JNZ {a}",
    cycles=1,
    operands={"a": Operand(signed=False)},
)
def jnz(st, a):
    if st.reg["flags.Z"] == 0:
        st.next_pc = a
        st.add_cycles(1)


@mcu.instr("0111 0000 aaaaaaaa", syntax="JMP {a}", cycles=2)
def jmp(st, a):
    st.next_pc = a


@mcu.instr("1000 0000 0000 1001 0000 0000 aaaaaaaa", syntax="CALL {a}", cycles=3)
def call(st, a):
    sp = st.reg["sp"]
    st.mem["stack"][sp] = st.next_pc
    st.reg["sp"] = (sp + 1) & 0xF
    st.next_pc = a


@mcu.instr("1001 0000 00000000", syntax="RET", cycles=2)
def ret(st):
    sp = (st.reg["sp"] - 1) & 0xF
    st.reg["sp"] = sp
    st.next_pc = st.mem["stack"][sp]


@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
'''


def _frozen_demo_mcu(tmp_path, *, load_via_api=False):
    path = tmp_path / "demo_mcu.py"
    path.write_bytes(_FROZEN_DEMO_MCU_V11.encode("utf-8"))
    if load_via_api:
        return isa.load_mcu(str(path))
    spec = importlib.util.spec_from_file_location("playground.isasim_demo.demo_mcu", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.mcu


def _cases(tmp_path):
    cases = {
        "toy_a:factory default": toy_a.make_mcu,
        "toy_a:pc_name=ip": lambda: toy_a.make_mcu(pc_name="ip"),
        "toy_a:{'undefined': 'nop'}": lambda: toy_a.make_mcu(undefined="nop"),
        "toy_a:{'out_of_range': 'ignore'}": lambda: toy_a.make_mcu(
            out_of_range="ignore"
        ),
        "toy_a:{'uninit': 'warn'}": lambda: toy_a.make_mcu(uninit="warn"),
        "toy_a:{'uninit': 'allow'}": lambda: toy_a.make_mcu(uninit="allow"),
        "toy_a:{'with_irq': True}": lambda: toy_a.make_mcu(with_irq=True),
        "toy_b:factory default": toy_b.make_mcu,
        "toy_b:pc_name=ip": lambda: toy_b.make_mcu(pc_name="ip"),
        "toy_c:factory default": toy_c.make_mcu,
        "toy_c:pc_name=ip": lambda: toy_c.make_mcu(pc_name="ip"),
        "toy_c:oor=ignore": lambda: toy_c.make_mcu(oor="ignore"),
        "demo_mcu:module direct": lambda: _frozen_demo_mcu(tmp_path),
        "demo_mcu:load_mcu": lambda: _frozen_demo_mcu(tmp_path, load_via_api=True),
        "source_demo:machine()": machine,
        "odd_cells:existing fixture": _odd_cells,
        "docs02:example": _example,
    }
    for toy in (toy_a, toy_b, toy_c):
        name = toy.__name__.rsplit(".", 1)[-1]
        def _load_toy(toy=toy, name=name):
            path = tmp_path / f"{name}.py"
            path.write_bytes(Path(toy.__file__).read_bytes().replace(b"\r\n", b"\n"))
            return isa.load_mcu(str(path))
        cases[f"{name}:load_mcu"] = _load_toy
    for out_of_range in ("stop", "ignore"):
        for ro in ("stop", "ignore"):
            cases[f"toy_r:out_of_range={out_of_range},ro={ro}"] = (
                lambda out_of_range=out_of_range, ro=ro: toy_r.make_toy_r(
                    out_of_range=out_of_range, ro=ro
                )
            )
    for lang in ("en", "zh"):
        for page, name in (
            ("README.md", "demo"),
            ("scenarios.md", "trace_demo"),
            ("scenarios.md", "watch_demo"),
        ):
            path = f"docs/{lang}/isasim/{page}"
            cases[f"{path}:{name}"] = lambda path=path, name=name: _user_example(
                path, name
            )
    return cases


def _baselines():
    rows = []
    for line in (
        (_HERE / "compatibility_baselines.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        expected = json.loads(line)
        signature = line.partition(_SIGNATURE_KEY)[2][:-1]
        rows.append(
            pytest.param(
                expected["case"], expected["isa_hash"], signature, id=expected["case"]
            )
        )
    assert len(rows) == 30
    return rows


@pytest.mark.parametrize("case,expected_hash,expected_signature", _baselines())
def test_existing_hash_and_snapshot_signature(
    case, expected_hash, expected_signature, tmp_path
):
    mcu = _cases(tmp_path)[case]()
    sim = isa.Simulator(mcu)
    assert _isa_hash(mcu) == expected_hash
    path = tmp_path / "snapshot.json"
    sim.snapshot().save(path)
    suffix = path.read_text(encoding="utf-8").partition(_SIGNATURE_KEY)[2]
    _, end = json.JSONDecoder().raw_decode(suffix)
    assert suffix[:end] == expected_signature
