"""Cross-cutting acceptance of the documented State and failure boundaries."""

import io

import pytest

from rpkbin.isasim import (
    DebugError,
    Device,
    MCUDefinitionError,
    Simulator,
    SnapshotError,
)
from rpkbin.isasim.repl import run_repl

from .toys.toy_a import make_mcu


def one(fn):
    m = make_mcu()
    m.instr("0000 0000 0000 0001", syntax="PROBE", cycles=1)(fn)
    sim = Simulator(m)
    sim.load_cells("prog", {0: 1})
    return sim


def test_word_order_and_truncation():
    def probe(st):
        st.mem["data"].write_word(0, 2, -1, "big")
        st.reg["r0"] = st.mem["data"].read_word(0, 2, "little")
        st.mem["data"].write_word(2, 2, 0x12345678, "little")
        st.reg["r1"] = st.mem["data"].read_word(2, 2, "big")

    sim = one(probe)
    r = sim.step()
    assert r.stop_reason == "step_done"
    assert [sim.read(f"data[{i}]") for i in range(4)] == [
        0xFFFF,
        0xFFFF,
        0x5678,
        0x1234,
    ]
    assert (sim.read("r0"), sim.read("r1")) == (0xFFFF, 0x1234)


def test_fault_restores_initialized_and_repeated_writes():
    def probe(st):
        st.mem["data"][0] = 1
        st.mem["data"][0] = 2
        st.reg["r0"] = 3
        st.fault("abort", "probe")

    sim = one(probe)
    r = sim.step()
    assert (r.stop_reason, r.steps, sim.read("data[0]"), sim.read("r0")) == (
        "fault",
        0,
        0,
        0,
    )
    assert sim._initialized["data"][0] is False
    assert [e.kind for e in sim.recent_events(10)] == ["stop"]


def test_read_seq_peek_and_device_readonly_override():
    sim = Simulator(make_mcu(uninit="warn"))
    sim.read_seq("data[0]", [5, 6])
    assert sim.read("data[0]") == sim.state.mem["data"][0] == 0
    sim.load_cells("prog", {0: 0x4000, 1: 0x4400})
    sim.step(2)
    assert (sim.read("r0"), sim.read("r1")) == (5, 6)
    m = make_mcu()
    m.space(
        "port", cell_bits=3, size=1, access="ro", reset_state="uninitialized", init=0
    )

    @m.instr("0000 0000 0000 0001", syntax="PROBE", cycles=1)
    def probe(st):
        st.mem["port"][0] = 15

    class Sink(Device):
        def __init__(self):
            self.values = []

        def read(self, addr, now):
            return 0

        def write(self, addr, value, now):
            self.values.append(value)

    sim = Simulator(m)
    sim.load_cells("prog", {0: 1})
    sink = Sink()
    sim.attach(sink, space="port", lo=0, hi=1)
    assert sim.step().stop_reason == "step_done" and sink.values == [7]


def test_exception_identity_and_outside_subscriber():
    error = ValueError("semantic")

    def probe(st):
        st.reg["r0"] = 1
        raise error

    sim = one(probe)
    events = []
    sim.subscribe(events.append)
    with pytest.raises(ValueError) as raised:
        sim.run(max_steps=10, raise_exceptions=True)
    assert (
        raised.value is error
        and sim.read("r0") == 0
        and events[-1].code == "python_error"
    )
    other = RuntimeError("outside")
    seen = []

    def bad(event):
        raise other

    sim = Simulator(make_mcu())
    sim.subscribe(bad)
    sim.subscribe(seen.append)
    with pytest.raises(RuntimeError) as raised:
        sim.poke("r1", 5)
    assert raised.value is other and sim.read("r1") == 5 and len(seen) == 1


def test_interrupt_during_semantic_rolls_back():
    def probe(st):
        st.reg["r0"] = 1
        raise KeyboardInterrupt

    sim = one(probe)
    r = sim.run(max_steps=10)
    assert (r.stop_reason, r.steps, r.cycle, sim.read("r0")) == ("interrupted", 0, 0, 0)


def test_two_instances_and_watch_field_names():
    m = make_mcu()
    left = Simulator(m)
    right = Simulator(m)
    left.poke("r0", 5)
    assert right.read("r0") == 0

    def probe(st):
        st.reg["flags.Z"] = 1
        st.reg["flags.Z"] = 0

    sim = one(probe)
    sim.add_watchpoint("flags", kind="w")
    assert sim.step().stop_reason == "watchpoint"
    sim = one(probe)
    sim.add_watchpoint("flags.Z", kind="w")
    r = sim.step()
    assert (r.stop_reason, r.detail["old"], r.detail["new"]) == ("watchpoint", 0, 1)


def test_stop_delivery_reraises_chronologically_first():
    first = ValueError("semantic")
    second = RuntimeError("stop")

    def probe(st):
        raise first

    sim = one(probe)
    seen = []

    def bad(event):
        if event.kind == "stop":
            raise second

    sim.subscribe(bad)
    sim.subscribe(seen.append)
    with pytest.raises(ValueError) as raised:
        sim.step()
    assert raised.value is first and seen[-1].kind == "stop"


def test_expression_errors_are_debug_errors():
    sim = Simulator(make_mcu())
    sim.attach(Device(), space="data", lo=0, hi=1)
    for expr in ["m.data[0]", "to_signed(1, 0)"]:
        with pytest.raises(DebugError):
            sim.evaluate(expr)


def test_snapshot_rejects_unimplemented_set_state():
    class Half(Device):
        def get_state(self):
            return {}

    sim = Simulator(make_mcu())
    sim.attach(Half(), space="data", lo=0, hi=1)
    with pytest.raises(SnapshotError, match="Half"):
        sim.snapshot()


def test_help_reports_syntax():
    out = io.StringIO()
    run_repl(Simulator(make_mcu()), io.StringIO("help run\nhelp watch\nquit\n"), out)
    assert "run [max_steps]" in out.getvalue()
    assert "watch <target>" in out.getvalue()


def test_semantic_state_parameter_must_accept_positional_state():
    m = make_mcu()

    @m.instr("0000 0000 0000 0001", syntax="PROBE", cycles=1)
    def probe(*, st):
        pass

    with pytest.raises(MCUDefinitionError):
        Simulator(m)


def test_description_supports_normal_python_class_decorators(tmp_path):
    from rpkbin.isasim import load_mcu

    source = """from __future__ import annotations
from dataclasses import dataclass
from rpkbin.isasim import MCU
@dataclass(frozen=True)
class Pair:
    value: int
mcu = MCU("class_helper")
mcu.space("prog", cell_bits=3, size=1, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.reg("position", bits=1, reset=0, is_pc=True)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")
@mcu.instr("000", syntax="HLT", cycles=1)
def stop(st):
    st.halt(str(Pair(3).value))
"""
    path = tmp_path / "description.py"
    path.write_text(source, encoding="utf-8")
    result = Simulator(load_mcu(str(path))).step()
    assert (result.stop_reason, result.detail) == ("halted", {"reason": "3"})
