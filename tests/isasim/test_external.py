import pytest

from rpkbin.isasim import DeviceError, Simulator, StimulusError, TargetError
from rpkbin.isasim.events import _format

from .fixtures import A1, B2
from .toys.toy_a import make_mcu as a
from .toys.toy_b import make_mcu as b


def machine(program, factory=a, **kwargs):
    sim = Simulator(factory(**kwargs))
    sim.load_cells("prog", dict(enumerate(program)))
    return sim


@pytest.mark.parametrize("delay,value,stamp", [(3, 9, 4), (4, 0, 5)])
def test_delayed_write(delay, value, stamp):
    sim = machine([0x1005, 0x5001, 0x1C00, 0x2E00, 0xFFFF])
    events = []
    sim.subscribe(events.append)
    sim.on_write("data[1]", nth=1).delay(delay).set("r2", 9)
    assert sim.run(max_steps=100).stop_reason == "halted"
    assert sim.read("r3") == value and sim.read("r2") == 9
    assert f"@ c={stamp} EXT set r2 0x0000->0x0009" in [
        _format(e, sim._mcu) for e in events
    ]


def test_at_cycle_and_read():
    sim = machine([0x1001, 0x1402, 0x2100, 0xFFFF])
    sim.at_cycle(2).set("r1", 10)
    events = []
    sim.subscribe(events.append)
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.cycle, sim.read("r0")) == ("halted", 4, 11)
    assert _format(events[2], sim._mcu) == "@ c=2 EXT set r1 0x0002->0x000a"
    sim = machine([0x1009, 0x5007, 0x4407, 0x4807, 0xFFFF])
    sim.on_read("data[7]", nth=2).set("r3", 33)
    r = sim.run(max_steps=100)
    assert (r.cycle, sim.read("r1"), sim.read("r2"), sim.read("r3")) == (8, 9, 9, 33)
    assert any(
        _format(e, sim._mcu) == "@ c=7 EXT set r3 0x0000->0x0021"
        for e in sim.recent_events(20)
    )


class Clock:
    def __init__(self):
        self.calls = []

    def read(self, addr, now):
        return now

    def write(self, addr, value, now):
        pass

    def tick(self, cycles, now):
        self.calls.append((cycles, now))

    def get_state(self):
        return {}

    def set_state(self, state):
        pass


def test_device_and_irq():
    sim = machine([0x4020, 0x4420, 0xFFFF])
    clock = Clock()
    sim.attach(clock, space="data", lo=32, hi=36)
    r = sim.run(max_steps=100)
    assert (r.cycle, sim.read("r0"), sim.read("r1")) == (5, 0, 2)
    assert clock.calls == [(2, 2), (2, 4), (1, 5)]
    sim = Simulator(a(with_irq=True))
    sim.load_cells("prog", {0: 0x7000, 0x40: 0xFFFF})
    sim.at_cycle(4).irq("irq0", 1)
    r = sim.run(max_steps=100)
    assert (
        r.stop_reason,
        r.steps,
        r.instructions,
        r.cycle,
        r.pc,
        sim.read("r3"),
        sim.read("irq.irq0"),
    ) == ("halted", 4, 3, 8, 0x41, 0, 0)
    assert [_format(e, sim._mcu) for e in sim.recent_events(20)] == [
        "#0 c=0 pc=0x00 [0x7000] JMP 0 | - | +2",
        "#1 c=2 pc=0x00 [0x7000] JMP 0 | - | +2",
        "@ c=4 EXT irq irq0 0->1",
        "#2 c=4 pc=0x00 INTERRUPT IRQ0 | irq.irq0:1->0 r3:0x0000->0x0000 | +3",
        "#3 c=7 pc=0x40 [0xffff] HLT | - | +1",
        "= c=8 STOP halted: hlt",
    ]


@pytest.mark.parametrize(
    "mode,reason,count",
    [
        ("seq", "halted", 7),
        ("stuck", "stuck", 50),
        ("pending", "max_steps", 200),
        ("off", "max_steps", 200),
    ],
)
def test_b2(mode, reason, count):
    sim = machine(B2, b)
    if mode == "seq":
        sim.read_seq("inp[0]", [0, 0, 1])
    if mode == "pending":
        sim.at_cycle(1000000).set("inp[0]", 1)
    r = sim.run(
        max_steps=100000 if mode == "stuck" else 200,
        stuck_window=0 if mode == "off" else 50,
    )
    assert (r.stop_reason, r.steps) == (reason, count)
    if mode == "seq":
        assert (r.instructions, r.cycle, r.pc, sim.read("outp[0]")) == (7, 12, 6, 1)
        sim.reset()
        assert sim.run(max_steps=100).cycle == 12
    if mode == "stuck":
        assert (r.cycles, r.cycle, r.pc) == (100, 100, 0)
        assert r.stuck_report.pcs == [0, 2] and r.stuck_report.reads == [("inp", 0, 0)]


@pytest.mark.parametrize("times,expected", [(1, [3]), (None, [3, 7, 11])])
def test_at_pc(times, expected):
    sim = machine(A1)
    sim.at_pc(3, times=times).set("r3", 7)
    events = []
    sim.subscribe(events.append)
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.cycle, sim.read("r3")) == ("halted", 17, 7)
    assert [e.cycle for e in events if e.kind == "external"] == expected
    for index, e in enumerate(events):
        if e.kind == "external":
            assert events[index + 1].pc == 3


def test_fetch_edge():
    sim = Simulator(b())
    sim.load_cells("prog", {63: 0})
    sim.poke("pc", 63)
    assert sim.step().pc == 0
    assert (
        _format(sim.recent_events(2)[0], sim._mcu)
        == "#0 c=0 pc=0x3f [0x00] NOP | - | +1"
    )
    sim = Simulator(b())
    sim.load_cells("prog", {63: 0x41})
    sim.poke("pc", 63)
    r = sim.step()
    assert (r.stop_reason, r.steps) == ("out_of_range", 0)
    assert r.detail == {"space": "prog", "addr": 64, "access": "fetch"}


def test_call_and_interrupt_none():
    sim = Simulator(a())

    def f(s):
        s.poke("r1", 5)
        s.poke("r2", 6)

    sim.at_cycle(0).call(f)
    sim.step()
    events = sim.recent_events(20)
    assert [_format(e, sim._mcu) for e in events[:3]] == [
        "@ c=0 EXT call f",
        "@ c=0 EXT poke r1 0x0000->0x0005",
        "@ c=0 EXT poke r2 0x0000->0x0006",
    ]
    assert events[0].writes == ()
    w = events[1].writes[0]
    assert (w.kind, w.name, w.old, w.new, w.bits) == ("reg", "r1", 0, 5, 16)
    m = a(with_irq=True)

    def check(st):
        st.reg["r2"] = 9
        st.next_pc = 5
        st.add_cycles(7)

    m._interrupt = check
    sim = Simulator(m)
    sim.load_cells("prog", {0: 0x1001, 1: 0xFFFF})
    r = sim.step(2)
    assert (sim.read("r2"), sim.read("r0"), r.cycle, r.pc) == (0, 1, 2, 2)
    assert not any(w.name == "r2" for e in sim.recent_events(20) for w in e.writes)


def test_registration_validation():
    sim = Simulator(b())
    for target, values, error in [
        ("inp[0]", [2], StimulusError),
        ("nosuch[0]", [0], TargetError),
        ("r0", [0], TargetError),
        ("prog[0]", [0], StimulusError),
        ("inp[4]", [0], TargetError),
    ]:
        with pytest.raises(error):
            sim.read_seq(target, values)
    with pytest.raises(StimulusError):
        Simulator(a()).read_seq("r0", [0])
    trigger = sim.at_cycle(0)
    trigger.set("outp[0]", 1)
    with pytest.raises(StimulusError):
        trigger.set("outp[1]", 1)
    device = Clock()
    sim.attach(device, space="inp", lo=0, hi=1)
    with pytest.raises(DeviceError):
        sim.attach(device, space="inp", lo=0, hi=2)
    with pytest.raises(DeviceError):
        sim.attach(device, space="prog", lo=0, hi=1)
    with pytest.raises(StimulusError):
        sim.read_seq("inp[0]", [0])


def test_s1a_failure_keeps_external_and_pauses_remaining():
    sim = machine([0xFFFF])
    first = ValueError("first")
    events = []

    def bad(event):
        if event.kind == "external" and "r1" in event.text:
            raise first

    sim.subscribe(bad)
    sim.subscribe(events.append)
    sim.at_cycle(0).set("r1", 5)
    sim.at_cycle(0).set("r2", 6)
    r = sim.step()
    assert (r.stop_reason, r.steps, r.cycle, r.detail["where"]) == (
        "python_error",
        0,
        0,
        "subscriber",
    )
    assert r.exception is first and (sim.read("r1"), sim.read("r2")) == (5, 0)
    assert [e.kind for e in events] == ["external", "stop"]
    assert sim.step().stop_reason == "halted" and sim.read("r2") == 6


def test_late_failures_preserve_commit_and_first_exception():
    sim = machine([0xFFFF])
    first = ValueError("tick")
    second = RuntimeError("subscriber")
    events = []

    class Broken(Clock):
        def tick(self, cycles, now):
            raise first

    sim.attach(Broken(), space="data", lo=0, hi=1)

    def subscriber(event):
        if event.kind == "instr":
            raise second

    sim.subscribe(subscriber)
    sim.subscribe(events.append)
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.steps, r.cycle, r.pc, r.detail["where"]) == (
        "python_error",
        1,
        1,
        1,
        "device",
    )
    assert r.exception is first and r.detail["suppressed"] == [repr(second)]
    assert [e.kind for e in events] == ["instr", "stop"]
