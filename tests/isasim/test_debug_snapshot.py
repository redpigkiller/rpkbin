import json

import pytest

from rpkbin.isasim import DebugError, Simulator, SnapshotError

from .fixtures import A1
from .toys.toy_a import make_mcu as a
from .toys.toy_b import make_mcu as b


def machine(program=A1, factory=a):
    sim = Simulator(factory())
    sim.load_cells("prog", dict(enumerate(program)))
    return sim


@pytest.mark.parametrize(
    "condition,steps,cycles,pc", [(None, 12, 14, 6), ("r.r0 == 2", 6, 7, 3)]
)
def test_breakpoint(condition, steps, cycles, pc):
    sim = machine()
    sim.add_breakpoint(pc, condition=condition)
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.steps, r.cycle, r.pc, r.detail["pc"]) == (
        "breakpoint",
        steps,
        cycles,
        pc,
        pc,
    )
    if condition is None:
        r = sim.run(max_steps=100)
        assert (r.stop_reason, r.steps) == ("halted", 2)
    with pytest.raises(DebugError):
        sim.add_breakpoint("nosuch")


@pytest.mark.parametrize(
    "target,kind,condition,steps,cycle,pc,old,new",
    [
        ("data[5]", "w", None, 13, 16, 6, 0, 6),
        ("r2", "w", "r.r2 == 6", 10, 12, 3, 5, 6),
    ],
)
def test_watch(target, kind, condition, steps, cycle, pc, old, new):
    sim = machine()
    sim.add_watchpoint(target, kind=kind, condition=condition)
    r = sim.run(max_steps=100)
    assert (
        r.stop_reason,
        r.steps,
        r.cycle,
        r.detail["target"],
        r.detail["access"],
        r.detail["pc"],
        r.detail["old"],
        r.detail["new"],
    ) == ("watchpoint", steps, cycle, target, kind, pc, old, new)


@pytest.mark.parametrize(
    "kind,steps,access,cycle", [("r", 3, "r", 5), ("rw", 2, "w", 3)]
)
def test_read_watch(kind, steps, access, cycle):
    sim = machine([0x1004, 0x5002, 0x4402, 0xFFFF])
    sim.add_watchpoint("data[2]", kind=kind)
    r = sim.run(max_steps=100)
    assert (r.stop_reason, r.steps, r.cycle, r.detail["access"]) == (
        "watchpoint",
        steps,
        cycle,
        access,
    )
    if kind == "r":
        assert (r.detail["old"], r.detail["new"]) == (None, 4)


def test_evaluate():
    sim = machine()
    sim.run(max_steps=100)
    for expr, value in [
        ("r.r2", 6),
        ("r.flags.Z", 1),
        ("m.data[5]", 6),
        ("cycle", 17),
        ("pc", 8),
        ("to_signed(r.r0 - 1, 16)", -1),
    ]:
        assert sim.evaluate(expr) == value
    for expr in ["foo", "r.nope", "'x'"]:
        with pytest.raises(DebugError):
            sim.evaluate(expr)


def test_snapshot_errors_and_signature(tmp_path):
    sim = machine()
    path = tmp_path / "snap.json"
    snap = sim.snapshot()
    snap.save(str(path))
    data = json.loads(path.read_text())
    assert list(data) == ["mcu_name", "signature", "state"]
    assert data["signature"]["spaces"] == [
        ["prog", 16, 256, True],
        ["data", 16, 64, False],
    ]
    assert data["signature"]["registers"][0] == ["pc", 8, True, []]
    m = a()
    m._spaces["data"]["size"] = 32
    with pytest.raises(SnapshotError):
        Simulator(m).restore(snap)
    with pytest.raises(SnapshotError):
        Simulator(b()).restore(snap)

    class Bare:
        def read(self, addr, now):
            return 0

        def write(self, addr, value, now):
            pass

    sim.attach(Bare(), space="data", lo=0, hi=1)
    with pytest.raises(SnapshotError, match="Bare"):
        sim.snapshot()


def test_snapshot_stimulus_configuration_not_runtime():
    from .fixtures import B2

    sim = machine(B2, b)
    sim.read_seq("inp[0]", [0, 0, 1])
    sim.step(2)
    snap = sim.snapshot()
    assert sim.run(max_steps=100).cycle == 12
    sim.restore(snap)
    assert sim.run(max_steps=100).cycle == 12
    sim.clear_stimuli()
    sim.read_seq("inp[0]", [1])
    with pytest.raises(SnapshotError, match="stimuli"):
        sim.restore(snap)


def test_reset_preserves_breakpoints_and_displays():
    sim = machine()
    sim.add_breakpoint(6)
    display = sim.add_display("r.r2")
    sim.run(max_steps=100)
    sim.reset()
    assert sim.run(max_steps=100).stop_reason == "breakpoint"
    sim.remove_display(display)


def test_debug_proxy_underscore_parity():
    from rpkbin.isasim import MCU, Simulator
    from rpkbin.isasim.debug import DebugError

    mcu = MCU("proxy_parity")
    mcu.space("prog", cell_bits=16, size=8, access="ro", reset_state="uninitialized", init=0, is_program=True)
    mcu.space("_ram", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc_reg", bits=8, reset=0, is_pc=True)
    mcu.reg("_scratch", bits=8, reset=0)
    mcu.reg("_sim", bits=8, reset=0)
    mcu.irq_line("_wake")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 0000 0000 0000", syntax="SET", cycles=1)
    def do_set(st):
        st.reg["_scratch"] = 17
        st.reg["_sim"] = 23
        st.mem["_ram"][1] = 42
        st.irq["_wake"] = 1

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def do_hlt(st):
        st.halt("ok")

    sim = Simulator(mcu)
    sim.load_cells("prog", {0: 0x1000, 1: 0xFFFF})
    sim.step()

    assert sim.evaluate("r._scratch == r['_scratch'] == 17") is True
    assert sim.evaluate("r._sim == r['_sim'] == 23") is True
    assert sim.evaluate("m._ram[1] == m['_ram'][1] == 42") is True
    assert sim.evaluate("i._wake == i['_wake'] == 1") is True

    with pytest.raises(DebugError):
        sim.evaluate("r['_missing'] == 0")
    with pytest.raises(DebugError):
        sim.evaluate("r._missing == 0")
    with pytest.raises(DebugError):
        sim.evaluate("m['_missing'][0] == 0")
    with pytest.raises(DebugError):
        sim.evaluate("i['_missing'] == 0")
