"""Memory policy regions and scoped Device access to simulator state."""

from io import StringIO

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.repl import run_repl

from .test_assembler import machine


def _board(*, regions=None, access="rw", policy="stop"):
    mcu = machine()
    mcu.space(
        "data",
        cell_bits=8,
        size=6,
        access=access,
        reset_state="initialized",
        init=0,
        regions=regions,
    )
    mcu._policies["on_write_to_readonly"]["action"] = policy
    mcu.reg("status", bits=4, reset=0, fields={"Low": (1, 0)})
    mcu.alias("LowAlias", "status.Low")

    @mcu.instr("1000 iiii", syntax="PUT {i}", cycles=1)
    def put(st, i):
        st.mem["data"][i] = 7

    return mcu


def _sim(mcu, source):
    sim = isa.Simulator(mcu)
    sim.load_assembly(isa.assemble(mcu, source))
    return sim


def test_interrupt_writes_follow_readonly_region_policy():
    mcu = _board(regions=[(0, 2, "ro")])

    @mcu.interrupt_check
    def check(st):
        st.reg["status"] = 3
        st.mem["data"][0] = 7
        return "test_irq", 1

    sim = _sim(mcu, "END")
    sim.load_cells("data", {0: 4})
    result = sim.step()
    assert result.stop_reason == "readonly_write"
    assert result.detail == {"space": "data", "addr": 0}
    assert sim.read("data[0]") == 4
    assert sim.read("status") == 0
    assert (sim.cycle, sim.steps_total) == (0, 0)


def test_region_overrides_space_access_with_half_open_bounds():
    sim = _sim(_board(regions=[(2, 4, "rw")], access="ro"), "PUT 2\nPUT 3\nPUT 4")
    assert sim.step(2).stop_reason == "step_done"
    assert sim.step().stop_reason == "readonly_write"
    assert [sim.read(f"data[{i}]") for i in range(2, 5)] == [7, 7, 0]


def test_region_ignore_policy_loader_and_external_writes(tmp_path):
    mcu = _board(regions=[(0, 2, "ro")], policy="ignore")
    sim = _sim(mcu, "PUT 0\nEND")
    sim.load_cells("data", {0: 3})
    path = tmp_path / "data.bin"
    path.write_bytes(b"\x05")
    sim.load_bin(path, base=1, space="data")
    sim.poke("data[0]", 4)
    assert sim.step().stop_reason == "step_done"
    assert [sim.read(f"data[{i}]") for i in range(2)] == [4, 5]
    assert any(event.kind == "warning" for event in sim.recent_events(10))
    output = StringIO()
    run_repl(sim, StringIO("info\nquit\n"), output)
    assert "regions=" in output.getvalue()


@pytest.mark.parametrize(
    "regions",
    [
        [(0, 3, "ro"), (2, 4, "rw")],
        [(-1, 1, "ro")],
        [(0, 7, "ro")],
        [(2, 2, "ro")],
        [(2, 1, "ro")],
        [(0, 1, "unsupported")],
    ],
)
def test_invalid_regions_are_rejected(regions):
    with pytest.raises(isa.MCUDefinitionError, match="region"):
        isa.Simulator(_board(regions=regions))


def test_region_configuration_participates_in_hash_and_signature(tmp_path):
    plain = _board()
    empty = _board(regions=[])
    explicit = _board(regions=[(4, 6, "rw"), (0, 2, "rw")])
    image = isa.assemble(plain, "END")
    sim = isa.Simulator(empty)
    sim.load_assembly(image)
    sim.restore(isa.Simulator(plain).snapshot())
    other = isa.Simulator(explicit)
    with pytest.raises(isa.LoadError, match="ISA"):
        other.load_assembly(image)
    with pytest.raises(isa.SnapshotError, match="regions"):
        other.restore(sim.snapshot())
    with pytest.raises(isa.SnapshotError, match="regions"):
        sim.restore(other.snapshot())
    path = tmp_path / "snapshot.json"
    other.snapshot().save(path)
    other.restore(isa.Snapshot.load(path))


class _Modern(isa.Device):
    def __init__(self):
        self.saved = None
        self.saved_reg = None
        self.values = []

    def read(self, addr, now, ctx, /):
        self.saved = ctx
        self.saved_reg = ctx.reg
        ctx.reg["LowAlias"] = 2
        ctx.add_cycles(3)
        return ctx.reg["status"]

    def write(self, addr, value, now, ctx):
        self.values.append(value)
        ctx.reg["LowAlias"] = 3
        ctx.add_cycles(2)


def test_context_events_cycles_device_region_exemption_and_expiry():
    mcu = _board(regions=[(0, 2, "ro")])

    @mcu.instr("1001 0000", syntax="GET", cycles=2)
    def get(st):
        st.reg["value"] = st.mem["data"][0]

    sim = _sim(mcu, "GET\nPUT 0\nEND")
    device = _Modern()
    sim.attach(device, space="data", lo=0, hi=1)
    with pytest.raises(isa.StateAccessError):
        sim.read("data[0]")
    with pytest.raises(isa.StimulusError):
        sim.poke("data[0]", 4)
    assert sim.step().cycles == 5
    assert sim.read("value") == 2
    event = next(event for event in sim.recent_events(5) if event.kind == "instr")
    assert [(w.name, w.new) for w in event.writes] == [("status.Low", 2), ("value", 2)]
    with pytest.raises(isa.StateAccessError, match="expired|inactive"):
        device.saved.add_cycles(1)
    with pytest.raises(isa.StateAccessError, match="expired|inactive"):
        _ = device.saved_reg["LowAlias"]
    assert sim.step().cycles == 3
    assert device.values == [7] and sim.read("LowAlias") == 3


def test_context_changes_roll_back_but_device_effect_is_retained():
    mcu = _board()

    @mcu.instr("1001 0000", syntax="FAIL", cycles=1)
    def fail(st):
        st.mem["data"][0] = 5
        st.fault("test")

    device = _Modern()
    sim = _sim(mcu, "FAIL")
    sim.attach(device, space="data", lo=0, hi=1)
    assert sim.step().stop_reason == "fault"
    assert sim.read("status") == 0
    assert (sim.cycle, sim.steps_total) == (0, 0)
    assert device.values == [5]


def test_device_cannot_write_pc_with_context():
    class Device(isa.Device):
        def write(self, addr, value, now, ctx):
            ctx.reg["cursor"] = 3

    sim = _sim(_board(), "PUT 0")
    sim.attach(Device(), space="data", lo=0, hi=1)
    with pytest.raises(isa.StateAccessError, match="cursor"):
        sim.run(max_steps=1, raise_exceptions=True)


def test_context_is_readonly_during_interrupt_check():
    class Device(isa.Device):
        def read(self, addr, now, ctx):
            with pytest.raises(isa.StateAccessError, match="interrupt"):
                ctx.reg["LowAlias"] = 3
            with pytest.raises(isa.StateAccessError, match="interrupt"):
                ctx.add_cycles(2)
            return ctx.reg["LowAlias"]

    mcu = _board()

    @mcu.interrupt_check
    def check(st):
        st.reg["value"] = st.mem["data"][0]
        return "test_irq", 1

    sim = _sim(mcu, "END")
    sim.attach(Device(), space="data", lo=0, hi=1)
    sim.poke("LowAlias", 2)
    assert sim.step().cycles == 1
    assert sim.read("value") == 2 and sim.read("LowAlias") == 2


@pytest.mark.parametrize(
    "signature", ["varargs", "optional", "keyword_only", "uninspectable"]
)
def test_old_device_signatures_keep_their_argument_counts(signature):
    class Device(isa.Device):
        def write(self, addr, value, now, *args):
            assert not args

    def variadic(self, addr, now, *args):
        assert not args
        return 1

    def optional(self, addr, now, other=None):
        assert other is None
        return 2

    def keyword_only(self, addr, now, *, ctx=None):
        assert ctx is None
        return 3

    def uninspectable(self, addr, now):
        return 4

    uninspectable.__signature__ = "invalid"
    readers = [variadic, optional, keyword_only, uninspectable]
    index = ["varargs", "optional", "keyword_only", "uninspectable"].index(signature)
    Device.read = readers[index]
    mcu = _board()

    @mcu.instr("1001 0000", syntax="GET", cycles=1)
    def get(st):
        st.reg["value"] = st.mem["data"][0]

    sim = _sim(mcu, "GET\nPUT 0")
    sim.attach(Device(), space="data", lo=0, hi=1)
    assert sim.step(2).stop_reason == "step_done"
    assert sim.read("value") == index + 1
