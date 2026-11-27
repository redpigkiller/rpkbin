"""Runtime initialization ranges for ordinary memory cells."""

import pytest

from rpkbin.isasim import Device, Simulator, StateAccessError, TargetError

from .toys.toy_a import make_mcu


def test_reset_states_and_immutable_ranges():
    mcu = make_mcu()
    mcu.space(
        "preset", cell_bits=16, size=6, access="rw", reset_state="initialized", init=0
    )
    sim = Simulator(mcu)
    assert sim.initialized_ranges("data") == ()
    ranges = sim.initialized_ranges("preset")
    assert isinstance(ranges, tuple)
    assert isinstance(ranges[0], tuple)
    assert ranges == ((0, 6),)

    sim.poke("data[0]", 0)
    assert sim.initialized_ranges("data") == ((0, 1),)
    sim.reset()
    assert sim.initialized_ranges("data") == ()
    assert sim.initialized_ranges("preset") == ((0, 6),)


def test_load_cells_and_writes_merge_ranges():
    sim = Simulator(make_mcu())
    sim.load_cells("data", dict.fromkeys((63, 62, 61, 9, 8, 6, 5, 4), 0))
    assert sim.initialized_ranges("data") == ((4, 7), (8, 10), (61, 64))

    sim.poke("data[7]", 0)
    assert sim.initialized_ranges("data") == ((4, 10), (61, 64))

    sim.load_cells("prog", {0: 0x500A})  # ST to data[10]
    assert sim.step().instructions == 1
    assert sim.initialized_ranges("data") == ((4, 11), (61, 64))


def test_binary_loader_initializes_only_loaded_cells(tmp_path):
    image = tmp_path / "data.bin"
    image.write_bytes(bytes.fromhex("00000002"))
    sim = Simulator(make_mcu())
    sim.load_bin(image, base=12, space="data", order="big")
    assert sim.initialized_ranges("data") == ((12, 14),)


def test_device_cells_are_excluded_from_initialized_storage():
    sim = Simulator(make_mcu())
    sim.load_cells("data", dict.fromkeys(range(4, 12), 0))
    sim.attach(Device(), space="data", lo=9, hi=11)
    sim.attach(Device(), space="data", lo=5, hi=9)
    sim.load_cells("data", {8: 1, 10: 2})
    assert sim.initialized_ranges("data") == ((4, 5), (11, 12))


def test_device_boundary_returns_plain_integer_addresses():
    class DeclaredInt(int):
        pass

    sim = Simulator(make_mcu())
    sim.load_cells("data", {0: 0})
    sim.attach(Device(), space="data", lo=DeclaredInt(1), hi=2)
    ranges = sim.initialized_ranges("data")
    assert ranges == ((0, 1),)
    assert type(ranges[0][1]) is int


def test_snapshot_restore_recovers_ranges():
    sim = Simulator(make_mcu())
    sim.load_cells("data", {1: 0, 3: 0})
    saved_ranges = sim.initialized_ranges("data")
    snap = sim.snapshot()

    sim.poke("data[2]", 0)
    sim.poke("data[63]", 0)
    assert sim.initialized_ranges("data") == ((1, 4), (63, 64))
    assert saved_ranges == ((1, 2), (3, 4))
    sim.restore(snap)
    assert sim.initialized_ranges("data") == saved_ranges


@pytest.mark.parametrize("policy", ["stop", "warn"])
def test_query_leaves_snapshot_events_breakpoints_and_read_sequence_intact(
    policy, tmp_path
):
    sim = Simulator(make_mcu(uninit=policy))
    sim.load_cells("prog", {0: 0x4003, 1: 0x4004})  # LD data[3], LD data[4]
    sim.poke("data[3]", 0)
    sim.read_seq("data[3]", [7, 9])
    sim.add_breakpoint(0, temporary=True)
    path = tmp_path / "snapshot.json"

    def checkpoint():
        sim.snapshot().save(path)
        return path.read_bytes(), sim.recent_events(20), sim.list_breakpoints()

    before = checkpoint()
    assert sim.initialized_ranges("data") == ((3, 4),)
    assert sim.initialized_ranges("data") == ((3, 4),)
    assert checkpoint() == before

    assert sim.step().instructions == 1
    assert sim.read("r0") == 7
    if policy == "stop":
        assert sim.step().stop_reason == "uninitialized_read"
    else:
        assert sim.step().instructions == 1
        assert [e.code for e in sim.recent_events(20) if e.kind == "warning"] == [
            "uninitialized_read"
        ]
    assert sim.initialized_ranges("data") == ((3, 4),)


def test_unknown_space_raises_state_access_error():
    sim = Simulator(make_mcu())
    with pytest.raises(StateAccessError, match="missing") as caught:
        sim.initialized_ranges("missing")
    assert not isinstance(caught.value, TargetError)
