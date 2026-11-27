import pytest

from rpkbin.isasim import Simulator
from rpkbin.isasim.events import _format

from .fixtures import A1, B1, B_GOLDEN, B_NOT
from .toys.toy_a import make_mcu as a
from .toys.toy_b import make_mcu as b
from .toys.toy_r import make_toy_r


def setup(factory, program, *, reads=False, pc_name="pc"):
    sim = Simulator(factory(pc_name=pc_name), trace_reads=reads)
    sim.load_cells("prog", dict(enumerate(program)))
    return sim


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"max_steps": 5}, ("max_steps", 5, 5, 5, 5)),
        ({"max_steps": 100, "max_cycles": 6}, ("max_cycles", 6, 6, 7, 3)),
    ],
)
def test_run_limits(kwargs, expected):
    r = setup(a, A1).run(**kwargs)
    assert (r.stop_reason, r.steps, r.instructions, r.cycles, r.pc) == expected


@pytest.mark.parametrize(
    "taken,cycles,count,outputs,stack",
    [
        (True, 9, 6, [1, 0, 0, 1], [0, 8, 0, 0, 0]),
        (False, 7, 5, [1, 1, 0, 0], [0, 0, 0, 0, 0]),
    ],
)
def test_b1(tmp_path, taken, cycles, count, outputs, stack):
    sim = setup(b, B1)
    path = tmp_path / "trace.log"
    sim.enable_trace(str(path), fmt="text")
    if taken:
        sim.poke("inp[1]", 1)
    r = sim.run(max_steps=100)
    sim.disable_trace()
    assert (r.stop_reason, r.steps, r.instructions, r.cycle, r.pc) == (
        "halted",
        count,
        count,
        cycles,
        9,
    )
    assert [sim.read(f"outp[{i}]") for i in range(4)] == outputs
    assert [sim.read(f"stack[{i}]") for i in range(5)] == stack
    text = path.read_text()
    assert (
        text == B_GOLDEN
        if taken
        else "\n".join(line for line in text.splitlines() if line.startswith("#"))
        + "\n"
        == B_NOT
    )


def test_b1_reads():
    sim = setup(b, B1, reads=True)
    sim.poke("inp[1]", 1)
    events = []
    sim.subscribe(events.append)
    sim.run(max_steps=100)
    line = _format(events[1], sim._mcu)
    assert line == "#1 c=1 pc=0x01 [0x41 0x06] JIN 1, 6 | - | rd inp[0x1]=0x1 | +2"


@pytest.mark.parametrize(
    "program,steps,cycle,code,stack",
    [
        ([0x30, 0], 4, 8, "stack_overflow", [4, 2, 2, 2, 2]),
        ([2], 0, 0, "stack_underflow", [0, 0, 0, 0, 0]),
    ],
)
def test_b_faults(program, steps, cycle, code, stack):
    sim = setup(b, program)
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.steps, r.instructions, r.cycle, r.pc) == (
        "fault",
        steps,
        steps,
        cycle,
        0,
    )
    assert r.detail["code"] == code
    assert [sim.read(f"stack[{i}]") for i in range(5)] == stack


@pytest.mark.parametrize(
    "kind,action,program,reason,steps,x,d",
    [
        ("oor", "stop", [1], "out_of_range", 0, 0, 0),
        ("oor", "ignore", [1, 3], "halted", 2, 5, 7),
        ("ro", "stop", [2], "readonly_write", 0, 0, 0),
        ("ro", "ignore", [2, 3], "halted", 2, 0, 0),
    ],
)
def test_rollback(kind, action, program, reason, steps, x, d):
    sim = Simulator(
        make_toy_r(
            out_of_range=action if kind == "oor" else "stop",
            ro=action if kind == "ro" else "stop",
        )
    )
    sim.load_cells("prog", dict(enumerate(program)))
    events = []
    sim.subscribe(events.append)
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.steps, sim.read("x"), sim.read("d[1]")) == (
        reason,
        steps,
        x,
        d,
    )
    if action == "ignore":
        assert len([e for e in events if e.kind == "warning"]) == 1
    else:
        assert [e.kind for e in events] == ["stop"]
    if kind == "ro":
        assert sim.read("prog[0]") == 2


def test_reset_image_and_poke():
    sim = setup(a, A1)
    sim.run(max_steps=100)
    sim.reset()
    assert [sim.read("r2"), sim.read("pc"), sim.cycle, sim.steps_total] == [0, 0, 0, 0]
    assert sim._initialized["data"][5] is False
    assert sim.run(max_steps=100).cycles == 17
    sim = setup(b, B1)
    sim.poke("inp[1]", 1)
    assert sim.run(max_steps=100).cycles == 9
    sim.reset()
    assert sim.run(max_steps=100).cycles == 7
