import pytest

from rpkbin.isasim import Simulator, Snapshot
from rpkbin.isasim.events import _format

from .fixtures import A1, B1
from .toys.toy_a import make_mcu as a
from .toys.toy_b import make_mcu as b


def machine(program=A1, factory=a):
    sim = Simulator(factory())
    sim.load_cells("prog", dict(enumerate(program)))
    return sim


@pytest.mark.parametrize("factory,program", [(a, A1), (b, B1)])
def test_conformance_c1_c2_c6(factory, program):
    sim = Simulator(factory())
    events = []
    sim.subscribe(events.append)

    def configure(s):
        s.load_cells("prog", dict(enumerate(program)))
        if factory is b:
            s.poke("inp[1]", 1)

    configure(sim)
    if factory is a:
        targets = ["r0", "r1", "r2", "r3", "flags", "flags.Z", "flags.N", "flags.C"] + [
            f"data[{i}]" for i in range(64)
        ]
    else:
        targets = [
            f"{name}[{i}]"
            for name, size in [("inp", 4), ("outp", 4), ("stack", 5)]
            for i in range(size)
        ]
    before = {target: sim.read(target) for target in targets}
    sim.run(max_steps=100)
    first = list(events)
    after = {target: sim.read(target) for target in targets}
    last = {}
    for event in events:
        for w in event.writes:
            target = f"{w.name}[{w.addr}]" if w.kind == "mem" else w.name
            last[target] = w.new
    assert all(after[target] == value for target, value in last.items())
    for target in targets:
        if before[target] != after[target]:
            assert target in last or any(key.startswith(target + ".") for key in last)
    events.clear()
    sim.reset()
    configure(sim)
    sim.run(max_steps=100)
    assert events == first
    renamed = Simulator(factory(pc_name="ip"))
    renamed_events = []
    renamed.subscribe(renamed_events.append)
    configure(renamed)
    renamed.run(max_steps=100)
    assert [_format(e, sim._mcu) for e in first] == [
        _format(e, renamed._mcu) for e in renamed_events
    ]
    assert sim.state.pc == renamed.state.pc == (8 if factory is a else 9)
    assert [renamed.read(t) for t in targets] == [sim.read(t) for t in targets]


@pytest.mark.parametrize("factory,program,k", [(a, A1, 7), (b, B1, 3)])
def test_conformance_c3_c4_c5(tmp_path, factory, program, k):
    sim = machine(program, factory)
    if factory is b:
        sim.poke("inp[1]", 1)
    sim.step(k)
    snap = sim.snapshot()
    path = tmp_path / "state.json"
    snap.save(str(path))
    events = []
    sim.subscribe(events.append)
    sim.run(max_steps=100)
    first = list(events)
    targets = (
        ["r0", "r1", "r2", "r3", "flags"] + [f"data[{i}]" for i in range(64)]
        if factory is a
        else [
            f"{s}[{i}]"
            for s, n in [("inp", 4), ("outp", 4), ("stack", 5)]
            for i in range(n)
        ]
    )
    dump = [sim.read(t) for t in targets]
    sim.restore(Snapshot.load(str(path)))
    events.clear()
    sim.run(max_steps=100)
    assert events == first and [sim.read(t) for t in targets] == dump
    sim.reset()
    if factory is b:
        sim.poke("inp[1]", 1)
    where = 6 if factory is a else 10
    sim.add_breakpoint(where)
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.pc) == ("breakpoint", where)
    assert sim.run(max_steps=100).stop_reason == "halted"
    sim.delete("all")
    sim.reset()
    if factory is b:
        sim.poke("inp[1]", 1)
    target = "data[5]" if factory is a else "outp[3]"
    sim.add_watchpoint(target, kind="w")
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.detail["target"], r.detail["access"]) == (
        "watchpoint",
        target,
        "w",
    )
    assert sim.read(target) == (6 if factory is a else 1)
