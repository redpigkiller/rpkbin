"""Regressions for checkpoint validation and public API contracts."""

import json
from unittest.mock import patch

import pytest

from rpkbin.isasim import Simulator, Snapshot, SnapshotError, StateAccessError

from .toys.toy_a import make_mcu as toy_a
from .toys.toy_b import make_mcu as toy_b


def _configured():
    sim = Simulator(toy_a())
    sim.load_cells("prog", {0: 0xFFFF})
    sim.at_cycle(0).set("r1", 7)
    return sim


def _dump(sim):
    registers = [sim.read(name) for name in ("pc", "r0", "r1", "r2", "r3", "flags")]
    cells = [
        sim.read(f"{name}[{addr}]")
        for name, size in (("prog", 256), ("data", 64))
        for addr in range(size)
    ]
    return registers, cells, sim.list_stimuli(), sim.cycle, sim.steps_total


@pytest.mark.parametrize("corruption", ["inject", "null"])
def test_snapshot_runtime_rejects_corruption_atomically(tmp_path, corruption):
    sim = _configured()
    control = _configured()
    path = tmp_path / "state.json"
    sim.snapshot().save(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "inject":
        payload["state"]["stimuli"][0]["action"] = ["set", "r2", 99]
    else:
        payload["state"]["stimuli"][0] = None
        payload["state"]["registers"]["r0"] = 42
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = _dump(sim)
    with pytest.raises(SnapshotError):
        sim.restore(Snapshot.load(str(path)))
    assert _dump(sim) == before
    assert sim.recent_events(100) == []
    assert sim.step() == control.step()
    assert _dump(sim) == _dump(control)
    assert sim.recent_events(100) == control.recent_events(100)


@pytest.mark.parametrize(
    "runtime",
    [
        [],
        {},
        {"seen": 0, "fired": 0, "pending": None},
        {"seen": -1, "fired": 0, "pending": []},
        {"seen": False, "fired": 0, "pending": []},
        {"seen": 0, "fired": "0", "pending": []},
        {"seen": 0, "fired": 0, "pending": [-1]},
        {"seen": 0, "fired": 0, "pending": [True]},
    ],
)
def test_snapshot_runtime_validates_structure_before_device(tmp_path, runtime):
    sim = _configured()

    class DeviceProbe:
        def __init__(self):
            self.restored = []

        def read(self, addr, now):
            return 0

        def write(self, addr, value, now):
            pass

        def get_state(self):
            return {}

        def set_state(self, state):
            self.restored.append(state)

    device = DeviceProbe()
    sim.attach(device, space="data", lo=0, hi=1)
    path = tmp_path / "state.json"
    sim.snapshot().save(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"]["stimuli"][0] = runtime
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SnapshotError):
        sim.restore(Snapshot.load(str(path)))
    assert device.restored == []
    assert sim.read("r0") == 0
    assert len(sim.list_stimuli()) == 1


@pytest.mark.parametrize("position", [-1, True, None, "1"])
def test_snapshot_sequence_runtime_rejects_invalid_position(tmp_path, position):
    sim = Simulator(toy_a())
    sim.read_seq("data[0]", [0, 1])
    path = tmp_path / "state.json"
    sim.snapshot().save(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"]["stimuli"][0]["position"] = position
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SnapshotError):
        sim.restore(Snapshot.load(str(path)))


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("addr", [True, False, 1.0, "1", None])
def test_word_rejects_original_invalid_address(operation, addr):
    mcu = toy_a()

    @mcu.instr("0000 0000 0000 0001", syntax="PROBE", cycles=1)
    def probe(st):
        if operation == "read":
            st.mem["data"].read_word(addr, 1, "big")
        else:
            st.mem["data"].write_word(addr, 1, 9, "big")

    sim = Simulator(mcu)
    sim.load_cells("prog", {0: 1})
    sim.poke("data[1]", 7)
    result = sim.step()
    assert result.stop_reason == "python_error"
    assert result.detail["where"] == "semantic"
    assert "StateAccessError" in result.detail["error"]
    assert result.steps == 0
    assert (sim.read("data[0]"), sim.read("data[1]")) == (0, 7)


@pytest.mark.parametrize(
    "name,value",
    [("pc", 2), ("cycle", 2), ("reg", {}), ("mem", {}), ("irq", {}), ("next_pc", 2)],
)
def test_state_view_rejects_public_assignment(name, value):
    sim = Simulator(toy_a())
    before = (sim.state.reg, sim.state.mem, sim.state.irq)
    with pytest.raises(StateAccessError, match="use Simulator.poke"):
        setattr(sim.state, name, value)
    assert (sim.state.reg, sim.state.mem, sim.state.irq) == before
    assert sim.state.reg["r0"] == sim.state.pc == sim.state.cycle == 0


def test_state_view_rejects_rebinding_inside_subscriber():
    sim = _configured()
    accessor = sim.state.reg

    def subscriber(event):
        if event.kind == "instr":
            sim.state.reg = {}

    sim.subscribe(subscriber)
    result = sim.step()
    assert result.stop_reason == "python_error"
    assert result.detail["where"] == "subscriber"
    assert isinstance(result.exception, StateAccessError)
    assert "use Simulator.poke" in str(result.exception)
    assert sim.state.reg is accessor
    assert sim.state.reg["r0"] == 0


@pytest.mark.parametrize(
    "factory,program", [(toy_b, [0x00, 0x01]), (toy_a, [0x1001, 0xFFFF])]
)
def test_keyboard_interrupt_at_s0_keeps_previous_commit(factory, program):
    sim = Simulator(factory())
    sim.load_cells("prog", dict(enumerate(program)))
    with patch.object(sim, "_breakpoint", side_effect=KeyboardInterrupt):
        result = sim.run(max_steps=2)
    assert (result.stop_reason, result.steps, result.instructions) == (
        "interrupted",
        1,
        1,
    )
    assert (result.pc, result.cycles, result.cycle) == (1, 1, 1)
    assert result.detail == {}
    events = sim.recent_events(10)
    assert [event.kind for event in events] == ["instr", "stop"]
    assert events[-1].code == "interrupted"
    if factory is toy_a:
        assert sim.read("r0") == 1


@pytest.mark.parametrize("factory,expected", [(toy_b, [(0, "NOP", 1)]), (toy_a, [])])
def test_disassemble_uses_uninitialized_storage_without_mutation(factory, expected):
    sim = Simulator(factory())
    assert sim.disassemble(0) == expected
    assert sim.read("prog[0]") == 0
    assert sim.recent_events(10) == []
    result = sim.step()
    assert (result.stop_reason, result.steps, result.cycle, result.pc) == (
        "uninitialized_read",
        0,
        0,
        0,
    )


def test_disassemble_uses_uninitialized_operand_and_stops_at_space_end():
    sim = Simulator(toy_b())
    sim.load_cells("prog", {0: 0x41})
    assert sim.disassemble(0) == [(0, "JIN 1, 0", 2)]
    assert sim.disassemble(63, count=2) == [(63, "NOP", 1)]
    assert sim.recent_events(10) == []
