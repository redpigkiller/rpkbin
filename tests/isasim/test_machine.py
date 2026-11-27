import pytest

from rpkbin.isasim import (
    MCU,
    MCUDefinitionError,
    Simulator,
    StateAccessError,
    UsageError,
    helpers,
    load_mcu,
)

from .fixtures import A1, GOLDEN_A, JSON_FIRST
from .toys.toy_a import make_mcu


def small(bits=16, size=256, pc_bits=8):
    m = MCU("small")
    m.space(
        "prog",
        cell_bits=bits,
        size=size,
        access="ro",
        reset_state="uninitialized",
        init=0,
        is_program=True,
    )
    m.reg("pc", bits=pc_bits, reset=0, is_pc=True)
    m.on_undefined_opcode("stop")
    m.on_out_of_range("stop")
    m.on_write_to_readonly("stop")
    m.on_uninitialized_read("stop")
    return m


def test_a1_text_and_state(tmp_path):
    sim = Simulator(make_mcu())
    sim.load_cells("prog", dict(enumerate(A1)))
    path = tmp_path / "trace.log"
    sim.enable_trace(str(path), fmt="text")
    result = sim.step(14)
    sim.disable_trace()
    assert (
        result.stop_reason,
        result.steps,
        result.instructions,
        result.cycles,
        result.cycle,
        result.pc,
    ) == ("halted", 14, 14, 17, 17, 8)
    assert result.detail == {"reason": "hlt"}
    assert [sim.read(f"r{n}") for n in range(4)] == [0, 1, 6, 0]
    assert [
        sim.read(n) for n in ("flags", "flags.Z", "flags.N", "flags.C", "data[5]")
    ] == [1, 1, 0, 0, 6]
    assert path.read_bytes() == GOLDEN_A.encode()


def test_json_first(tmp_path):
    sim = Simulator(make_mcu())
    sim.load_cells("prog", dict(enumerate(A1)))
    path = tmp_path / "trace.jsonl"
    sim.enable_trace(str(path), fmt="jsonl")
    sim.step(14)
    sim.disable_trace()
    assert path.read_text().splitlines()[0] == JSON_FIRST


@pytest.mark.parametrize(
    "pattern,bits,syntax,cells,text,length",
    [
        ("0100 00ii 00aaaaaa", 8, "J {i} {a}", [0x41, 0x06], "J 1 6", 2),
        ("1010 aaaa 0000 aaaa", 16, "S {a}", [0xA50B], "S 91", 1),
        ("1100 0000 aaaaaaaa aaaaaaaa", 8, "T {a}", [0xC0, 0x12, 0x34], "T 4660", 3),
        ("0011 0000 ---- rrrr", 16, "D {r}", [0x3057], "D 7", 1),
        ("0011 0000 ---- rrrr", 16, "D {r}", [0x30A7], "D 7", 1),
        ("0001 dd ss 00000000", 16, "ADD r{d}, r{s}", [0x1600], "ADD r1, r2", 1),
        ("1011 0000 aaaa", 12, "X {a}", [0xB05], "X 5", 1),
        ("101 aaa", 3, "X {a}", [0b101, 0b011], "X 3", 2),
    ],
)
def test_decode(pattern, bits, syntax, cells, text, length):
    m = small(bits)
    letters = list(dict.fromkeys(c for c in pattern if c.isalpha()))
    namespace = {}
    eval(
        compile(
            "def semantic(st" + "".join(", " + c for c in letters) + "): pass",
            "<string>",
            "exec",
        ),
        namespace,
    )
    m.instr(pattern, syntax=syntax, cycles=1)(namespace["semantic"])
    sim = Simulator(m)
    sim.load_cells("prog", dict(enumerate(cells)))
    assert sim.disassemble(0) == [(0, text, length)]


def test_non_power_space():
    m = small(12, 100, 8)

    @m.instr("0000 0000 0001", syntax="HLT", cycles=1)
    def h(st):
        st.halt("h")

    sim = Simulator(m)
    sim.load_cells("prog", {99: 1})
    sim.poke("pc", 99)
    result = sim.step()
    assert (result.stop_reason, result.pc) == ("halted", 100)
    result = sim.step()
    assert (result.stop_reason, result.steps, result.pc) == ("out_of_range", 0, 100)
    assert result.detail == {"space": "prog", "addr": 100, "access": "fetch"}


def test_fetch_last_cell():
    sim = Simulator(make_mcu())
    sim.load_cells("prog", {255: 0xFFFF})
    sim.poke("pc", 255)
    assert sim.step().stop_reason == "halted"
    assert sim.state.pc == 0


@pytest.mark.parametrize(
    "program,options,reason,steps,value,warning",
    [
        ([0], {}, "undefined_opcode", 0, 0, None),
        ([0, 0xFFFF], {"undefined": "nop"}, "halted", 2, 0, "undefined_opcode"),
        ([0x4064, 0xFFFF], {}, "out_of_range", 0, 0, None),
        (
            [0x4064, 0xFFFF],
            {"out_of_range": "ignore"},
            "halted",
            2,
            0xAA,
            "out_of_range",
        ),
        ([0x4003, 0xFFFF], {}, "uninitialized_read", 0, 0, None),
        ([0x4003, 0xFFFF], {"uninit": "warn"}, "halted", 2, 0, "uninitialized_read"),
        ([0x4003, 0xFFFF], {"uninit": "allow"}, "halted", 2, 0, None),
    ],
)
def test_policies(program, options, reason, steps, value, warning):
    sim = Simulator(make_mcu(**options))
    sim.load_cells("prog", dict(enumerate(program)))
    result = sim.step(2)
    assert (result.stop_reason, result.steps, sim.read("r0")) == (reason, steps, value)
    events = sim.recent_events(20)
    assert [e.code for e in events if e.kind == "warning"] == (
        [warning] if warning else []
    )
    if not steps:
        assert (sim.cycle, sim.state.pc, len(events)) == (0, 0, 1)
    if reason == "out_of_range":
        assert result.detail == {"space": "data", "addr": 100, "access": "read"}
    if reason == "uninitialized_read":
        assert result.detail == {"space": "data", "addr": 3}


def test_zero_and_readonly_view():
    sim = Simulator(make_mcu())
    with pytest.raises(UsageError):
        sim.step(0)
    with pytest.raises(StateAccessError):
        sim.state.reg["r0"] = 1


def test_direct_pc_write():
    m = make_mcu()

    @m.instr("0000 0000 0000 0001", syntax="BAD", cycles=1)
    def bad(st):
        st.reg["pc"] = 3

    sim = Simulator(m)
    sim.load_cells("prog", {0: 1})
    r = sim.step()
    assert (r.stop_reason, r.steps, r.detail["where"]) == (
        "python_error",
        0,
        "semantic",
    )
    assert "StateAccessError" in r.detail["error"]
    assert sim.state.pc == 0


@pytest.mark.parametrize(
    "defect,substring",
    [
        ("no_program", "program"),
        ("two_program", "program"),
        ("no_pc", "pc"),
        ("two_pc", "pc"),
        ("duplicate", "x"),
        ("reserved", "irq"),
        ("pattern_length", "0001 dd"),
        ("pattern_char", "$"),
        ("overlap", "second"),
        ("parameter", "d"),
        ("syntax", "z"),
        ("policy", "on_uninitialized_read"),
        ("init", "init"),
        ("reset", "reset"),
        ("fields", "A"),
        ("nop", "nop_cells"),
        ("no_instruction", "instruction"),
        ("field_int", "real"),
        ("field_private", "_x"),
    ],
)
def test_validation(defect, substring):
    with pytest.raises(MCUDefinitionError, match=substring.replace("$", r"\$")):
        m = small()
        if defect == "no_program":
            m._spaces["prog"]["is_program"] = False
        elif defect == "two_program":
            m.space(
                "extra",
                cell_bits=16,
                size=2,
                access="ro",
                reset_state="initialized",
                init=0,
                is_program=True,
            )
        elif defect == "no_pc":
            m._registers["pc"]["is_pc"] = False
        elif defect == "two_pc":
            m.reg("second", bits=8, reset=0, is_pc=True)
        elif defect == "duplicate":
            m.space(
                "x", cell_bits=1, size=1, access="rw", reset_state="initialized", init=0
            )
            m.reg("x", bits=1, reset=0)
        elif defect == "reserved":
            m.reg("irq", bits=1, reset=0)
        elif defect == "init":
            m.space(
                "x", cell_bits=1, size=1, access="rw", reset_state="initialized", init=2
            )
        elif defect == "reset":
            m.reg("x", bits=1, reset=2)
        elif defect.startswith("field"):
            fields = (
                {"A": (3, 0), "B": 2}
                if defect == "fields"
                else ({"real": 0} if defect == "field_int" else {"_x": 0})
            )
            m.reg("x", bits=4, reset=0, fields=fields)
        elif defect == "nop":
            m._policies.pop("on_undefined_opcode")
            m.on_undefined_opcode("nop")
        elif defect == "policy":
            m._policies.pop("on_uninitialized_read")
        if defect != "no_instruction":
            pattern = "0001 dd ss 00000000"
            if defect == "pattern_length":
                pattern = "0001 dd"
            if defect == "pattern_char":
                pattern = "0001 d$ss 00000000"
            syntax = "X {z}" if defect == "syntax" else "X {d} {s}"
            if defect == "parameter":

                def first(st, s):
                    pass
            else:

                def first(st, d, s):
                    pass

            m.instr(pattern, syntax=syntax, cycles=1)(first)
            if defect == "overlap":

                @m.instr("0001 11 ss 00000000", syntax="Y {s}", cycles=1)
                def second(st, s):
                    pass

        Simulator(m)


def test_load_description(tmp_path):
    path = tmp_path / "bad.py"
    path.write_text("x=1")
    with pytest.raises(MCUDefinitionError, match="mcu"):
        load_mcu(str(path))


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("to_signed", (0xFF, 8), -1),
        ("to_signed", (0x7F, 8), 127),
        ("to_signed", (0x80, 8), -128),
        ("to_signed", (0x1FF, 8), -1),
        ("to_unsigned", (-1, 8), 255),
        ("to_unsigned", (-1, 16), 65535),
        ("sign_extend", (0x80, 8, 16), 0xFF80),
        ("sign_extend", (0x7F, 8, 16), 0x007F),
        ("bit", (0b100, 2), 1),
        ("set_bit", (0, 3, 1), 8),
        ("set_bit", (0xF, 0, 0), 14),
        ("parity", (0,), 0),
        ("parity", (7,), 1),
        ("parity", (3,), 0),
        ("rotate_left", (0b1001, 1, 4), 3),
        ("rotate_right", (0b0001, 1, 4), 8),
        ("rotate_left", (1, 5, 4), 2),
        ("add_with_carry", (0xFF, 1, 0, 8), (0, 1, 0)),
        ("add_with_carry", (0x7F, 1, 0, 8), (0x80, 0, 1)),
        ("add_with_carry", (0x80, 0x80, 0, 8), (0, 1, 1)),
        ("add_with_carry", (0xFF, 0xFF, 1, 8), (0xFF, 1, 0)),
        ("sub_with_borrow", (0, 1, 0, 8), (0xFF, 1, 0)),
        ("sub_with_borrow", (0x80, 1, 0, 8), (0x7F, 0, 1)),
        ("sub_with_borrow", (5, 5, 1, 8), (0xFF, 1, 0)),
        ("sub_with_borrow", (5, 3, 0, 8), (2, 0, 0)),
        ("carry_out_of_bit", (0xF, 1, 0, 3), 1),
        ("carry_out_of_bit", (7, 1, 0, 3), 0),
        ("carry_out_of_bit", (0xFF, 0, 1, 7), 1),
    ],
)
def test_helpers(name, args, expected):
    result = getattr(helpers, name)(*args)
    if isinstance(expected, tuple):
        from dataclasses import astuple

        result = astuple(result)
    assert result == expected
