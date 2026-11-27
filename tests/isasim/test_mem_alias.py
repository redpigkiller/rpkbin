"""Memory alias declarations, byte/bit ordering, expressions, stimuli, and watchpoints."""

from dataclasses import FrozenInstanceError
from io import StringIO

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.repl import run_repl


def _make_mcu(*, cell_bits=8, size=16, reset_state="initialized", init=0):
    mcu = isa.MCU("mem_alias_test")
    mcu.space(
        "prog",
        cell_bits=16,
        size=32,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    mcu.space(
        "data",
        cell_bits=cell_bits,
        size=size,
        access="rw",
        reset_state=reset_state,
        init=init,
    )
    mcu.reg("pc", bits=5, reset=0, is_pc=True)
    mcu.reg("word", bits=16, reset=0, fields={"hi": (15, 8), "lo": (7, 0)})
    mcu.alias("word_alias", "word")
    mcu.irq_line("wake")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("done")

    return mcu


def test_declaration_validation_and_required_orders():
    assert "MemAliasInfo" in isa.__all__
    mcu = _make_mcu(cell_bits=8, size=16)
    mcu.space(
        "bits1",
        cell_bits=1,
        size=4,
        access="rw",
        reset_state="initialized",
        init=0,
    )

    # Single-bit targets accept None for both endian and bit_order.
    mcu.mem_alias("bit_single", "data[1][5]")
    mcu.mem_alias("cell_1bit", "bits1[0]")
    assert mcu.mem_aliases["bit_single"] == isa.MemAliasInfo(
        name="bit_single",
        target="data[1][5]",
        space="data",
        lo=1,
        hi=2,
        bit_hi=5,
        bit_lo=5,
        endian=None,
        bit_order=None,
        bits=1,
    )
    assert mcu.mem_aliases["cell_1bit"].endian is None
    assert mcu.mem_aliases["cell_1bit"].bit_order is None
    assert mcu.mem_aliases["cell_1bit"].bits == 1

    # Single multi-bit cell allows endian=None (or explicit), requires explicit bit_order.
    with pytest.raises(isa.MCUDefinitionError, match="bit_order"):
        mcu.mem_alias("byte0", "data[0]")
    mcu.mem_alias("byte0", "data[0]", bit_order="msb_first")
    assert mcu.mem_aliases["byte0"].endian is None
    assert mcu.mem_aliases["byte0"].bit_order == "msb_first"
    assert mcu.mem_aliases["byte0"].bits == 8

    # Single-cell slice syntax data[2:3] has cell_count == 1 -> endian=None allowed.
    mcu.mem_alias("slice1", "data[2:3]", bit_order="lsb_first")
    assert mcu.mem_aliases["slice1"].lo == 2
    assert mcu.mem_aliases["slice1"].hi == 3
    assert mcu.mem_aliases["slice1"].endian is None
    assert mcu.mem_aliases["slice1"].bits == 8

    # Multi-cell range requires explicit endian AND explicit bit_order.
    with pytest.raises(isa.MCUDefinitionError, match="endian"):
        mcu.mem_alias("pair_no_endian", "data[0:2]", bit_order="msb_first")
    with pytest.raises(isa.MCUDefinitionError, match="bit_order"):
        mcu.mem_alias("pair_no_order", "data[0:2]", endian="big")
    mcu.mem_alias("pair_le", "data[0x2:0x4]", endian="little", bit_order="msb_first")
    assert mcu.mem_aliases["pair_le"] == isa.MemAliasInfo(
        name="pair_le",
        target="data[0x2:0x4]",
        space="data",
        lo=2,
        hi=4,
        bit_hi=None,
        bit_lo=None,
        endian="little",
        bit_order="msb_first",
        bits=16,
    )

    # Multi-bit slice within single cell requires explicit bit_order, allows endian=None.
    with pytest.raises(isa.MCUDefinitionError, match="bit_order"):
        mcu.mem_alias("nibble_bad", "data[0][7:4]")
    mcu.mem_alias("nibble_hi", "data[0][0x7:0x4]", bit_order="msb_first")
    assert mcu.mem_aliases["nibble_hi"].bits == 4
    assert mcu.mem_aliases["nibble_hi"].endian is None

    # int attribute names like 'real' are valid for mem_alias (m is not an int subclass).
    mcu.mem_alias("real", "data[5]", bit_order="msb_first")

    # Immutability & describe isolation.
    with pytest.raises(FrozenInstanceError):
        mcu.mem_aliases["byte0"].bits = 16
    with pytest.raises(TypeError):
        mcu.mem_aliases["extra"] = mcu.mem_aliases["byte0"]
    with pytest.raises(isa.MCUDefinitionError, match="validat"):
        mcu.describe()

    sim = isa.Simulator(mcu)
    desc = mcu.describe()
    assert desc.aliases == (("word_alias", "word"),)
    assert dict(mcu.aliases) == {"word_alias": "word"}
    assert set(sim.mem_aliases) == set(mcu.mem_aliases)
    assert sim.evaluate("m.real") == 0


@pytest.mark.parametrize(
    ("name", "target", "endian", "bit_order"),
    [
        ("_priv", "data[0]", None, "msb_first"),
        ("irq", "data[0]", None, "msb_first"),
        ("1bad", "data[0]", None, "msb_first"),
        ("", "data[0]", None, "msb_first"),
        ("data", "data[0]", None, "msb_first"),
        ("word", "data[0]", None, "msb_first"),
        ("word_alias", "data[0]", None, "msb_first"),
        ("wake", "data[0]", None, "msb_first"),
        ("dup", "data[0]", None, "msb_first"),
        ("x", "missing[0]", None, "msb_first"),
        ("x", "data[16]", None, "msb_first"),
        ("x", "data[-1]", None, "msb_first"),
        ("x", "data[0][8]", None, None),
        ("x", "data[0][8:7]", None, "msb_first"),
        ("x", "data[0][3:5]", None, "msb_first"),
        ("x", "data[4:4]", "big", "msb_first"),
        ("x", "data[5:3]", "big", "msb_first"),
        ("x", "data[0:17]", "big", "msb_first"),
        ("x", "data[0:2][3:0]", "big", "msb_first"),
        ("x", "data", None, "msb_first"),
        ("x", 123, None, "msb_first"),
        ("x", "data[0]", "middle", "msb_first"),
        ("x", "data[0]", True, "msb_first"),
        ("x", "data[0]", None, "lsb"),
        ("x", "data[0]", None, False),
        ("x", "data[0:2]", None, "msb_first"),
        ("x", "data[0:2]", "big", None),
        ("x", "data[0][3:1]", None, None),
    ],
)
def test_invalid_mem_alias_rejected_on_mcu_and_simulator(name, target, endian, bit_order):
    mcu = _make_mcu(cell_bits=8, size=16)
    mcu.mem_alias("dup", "data[1]", bit_order="msb_first")
    with pytest.raises(isa.MCUDefinitionError):
        mcu.mem_alias(name, target, endian=endian, bit_order=bit_order)

    sim = isa.Simulator(mcu)
    with pytest.raises(isa.TargetError):
        sim.mem_alias(name, target, endian=endian, bit_order=bit_order)


def test_name_collision_when_other_declarations_follow_mem_alias():
    mcu = _make_mcu()
    mcu.mem_alias("taken", "data[0]", bit_order="msb_first")
    for fn in (
        lambda: mcu.space("taken", cell_bits=8, size=2, access="rw", reset_state="initialized", init=0),
        lambda: mcu.reg("taken", bits=8, reset=0),
        lambda: mcu.irq_line("taken"),
        lambda: mcu.alias("taken", "word"),
    ):
        with pytest.raises(isa.MCUDefinitionError, match="globally unique"):
            fn()


def test_read_poke_endian_and_bit_order_roundtrips():
    mcu = _make_mcu(cell_bits=8, size=16)
    sim = isa.Simulator(mcu)
    sim.mem_alias("be16", "data[0:2]", endian="big", bit_order="msb_first")
    sim.mem_alias("le16", "data[2:4]", endian="little", bit_order="msb_first")
    sim.mem_alias("be16_rev", "data[4:6]", endian="big", bit_order="lsb_first")
    sim.mem_alias("le16_rev", "data[6:8]", endian="little", bit_order="lsb_first")
    sim.mem_alias("byte_rev", "data[8]", bit_order="lsb_first")
    sim.mem_alias("nib_msb", "data[9][5:2]", bit_order="msb_first")
    sim.mem_alias("nib_lsb", "data[9][5:2]", bit_order="lsb_first")
    sim.mem_alias("bit7", "data[9][7]")

    events = []
    sim.subscribe(events.append)

    sim.poke("be16", 0x1234)
    assert (sim.read("data[0]"), sim.read("data[1]")) == (0x12, 0x34)
    assert sim.read("be16") == sim.evaluate("m.be16") == 0x1234

    sim.poke("le16", 0xABCD)
    assert (sim.read("data[2]"), sim.read("data[3]")) == (0xCD, 0xAB)
    assert sim.read("le16") == sim.evaluate("m.le16") == 0xABCD

    # 0x8001 reversed is 0x8001; 0x8002 reversed in 16 bits is 0x4001.
    sim.poke("be16_rev", 0x8002)
    assert (sim.read("data[4]"), sim.read("data[5]")) == (0x40, 0x01)
    assert sim.read("be16_rev") == 0x8002

    sim.poke("le16_rev", 0x8002)
    assert (sim.read("data[6]"), sim.read("data[7]")) == (0x01, 0x40)
    assert sim.read("le16_rev") == 0x8002

    # 0b10110000 reversed in 8 bits -> 0b00001101 (0x0D).
    sim.poke("byte_rev", 0b10110000)
    assert sim.read("data[8]") == 0b00001101
    assert sim.read("byte_rev") == 0b10110000

    # Preserves outer bits when poking slices and single bits.
    sim.poke("data[9]", 0b11000011)
    sim.poke("nib_msb", 0b1010)
    assert sim.read("data[9]") == 0b11101011
    assert sim.read("nib_msb") == 0b1010
    assert sim.read("nib_lsb") == 0b0101
    sim.poke("nib_lsb", 0b1100)  # reverses to 0b0011 in bits [5:2]
    assert sim.read("data[9]") == 0b11001111
    sim.poke("bit7", 0)
    assert sim.read("data[9]") == 0b01001111
    assert sim.read("bit7") == 0

    # Value masking on out-of-width poke (matches _mem_write/_reg_write).
    sim.poke("nib_msb", -1)
    assert sim.read("nib_msb") == 0b1111
    sim.poke("byte_rev", 0x101)
    assert sim.read("byte_rev") == 0x01

    # Check WriteRecord details for multi-cell poke.
    be_event = events[0]
    assert be_event.kind == "external"
    assert be_event.text == "poke be16 0x0000->0x1234"
    assert [(w.name, w.addr, w.old, w.new, w.bits) for w in be_event.writes] == [
        ("data", 0, 0, 0x12, 8),
        ("data", 1, 0, 0x34, 8),
    ]


def test_non_power_of_two_cell_bits():
    mcu = _make_mcu(cell_bits=5, size=8)
    mcu.mem_alias("pair5", "data[0:2]", endian="big", bit_order="msb_first")
    mcu.mem_alias("slice5", "data[2][4:1]", bit_order="lsb_first")
    sim = isa.Simulator(mcu)
    sim.poke("pair5", (0x1F << 5) | 0x0A)
    assert (sim.read("data[0]"), sim.read("data[1]")) == (0x1F, 0x0A)
    assert sim.read("pair5") == (0x1F << 5) | 0x0A
    sim.poke("data[2]", 0b10001)
    sim.poke("slice5", 0b1100)  # reversed 4-bit -> 0b0011 in bits 4..1
    assert sim.read("data[2]") == 0b00111
    assert sim.read("slice5") == 0b1100


def test_uninitialized_peek_and_partial_poke_behavior():
    mcu = _make_mcu(cell_bits=8, size=8, reset_state="uninitialized", init=0xA5)
    mcu.mem_alias("pair", "data[0:2]", endian="big", bit_order="msb_first")
    mcu.mem_alias("low_nib", "data[2][3:0]", bit_order="msb_first")
    sim = isa.Simulator(mcu)

    # Peeking uninitialized cells must not stop/warn or mark them initialized.
    assert sim.read("pair") == 0xA5A5
    assert sim.evaluate("m.pair") == 0xA5A5
    assert sim.read("low_nib") == 0x5
    assert sim.initialized_ranges("data") == ()

    # Poking a bit-slice merges with init peek and initializes only that cell.
    sim.poke("low_nib", 0x9)
    assert sim.read("data[2]") == 0xA9
    assert sim.initialized_ranges("data") == ((2, 3),)


def test_invalid_poke_values_and_device_cells():
    mcu = _make_mcu(cell_bits=8, size=8)
    mcu.mem_alias("pair", "data[0:2]", endian="big", bit_order="msb_first")
    mcu.mem_alias("bit0", "data[4][0]")
    sim = isa.Simulator(mcu)

    for bad in (True, False, "1", 1.0, None):
        with pytest.raises(isa.StimulusError):
            sim.poke("pair", bad)
        with pytest.raises(isa.StimulusError):
            sim.at_cycle(0).set("pair", bad)

    dev = isa.Device()
    with pytest.raises(isa.DeviceError, match="multi-cell mem_alias"):
        sim.attach(dev, space="data", lo=1, hi=2)

    mcu.mem_alias("io_cell", "data[5]", bit_order="msb_first")
    sim = isa.Simulator(mcu)
    sim.attach(dev, space="data", lo=5, hi=6)
    with pytest.raises(isa.TargetError, match="multi-cell mem_alias"):
        sim.mem_alias("overlap_pair", "data[4:6]", endian="little", bit_order="msb_first")

    with pytest.raises(isa.StimulusError, match="device"):
        sim.poke("io_cell", 0x12)
    with pytest.raises(isa.StimulusError, match="device"):
        sim.at_cycle(0).set("io_cell", 0x12)
    with pytest.raises(isa.StateAccessError, match="device"):
        sim.read("io_cell")
    with pytest.raises(isa.DebugError):
        sim.evaluate("m.io_cell")

    # Non-device slice remains accessible.
    sim.poke("bit0", 1)
    assert sim.read("bit0") == 1


def test_boundary_stimulus_set_on_mem_alias():
    mcu = _make_mcu(cell_bits=8, size=8)

    @mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
    def nop(st):
        pass

    mcu.mem_alias("pair", "data[0:2]", endian="little", bit_order="msb_first")
    mcu.mem_alias("flag_bit", "data[2][2]")
    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 0x1000, 1: 0xFFFF})
    sim.at_cycle(0).set("pair", 0x3456)
    sim.at_cycle(1).set("flag_bit", 1)
    res = sim.run(max_steps=10)
    assert res.stop_reason == "halted"
    assert (sim.read("data[0]"), sim.read("data[1]"), sim.read("data[2]")) == (
        0x56,
        0x34,
        0x04,
    )


def test_watchpoints_for_single_bit_slice_and_multi_cell():
    mcu = _make_mcu(cell_bits=8, size=16)

    @mcu.instr("0001 aaaa vvvvvvvv", syntax="ST {a}, {v}", cycles=1)
    def st_cell(st, a, v):
        st.mem["data"][a] = v

    @mcu.instr("0010 aaaa vvvvvvvv", syntax="ST2 {a}, {v}", cycles=1)
    def st_two(st, a, v):
        # Writes two consecutive cells in big-endian or same value depending on args.
        st.mem["data"][a] = (v >> 4) & 0xF
        st.mem["data"][a + 1] = v & 0xF

    @mcu.instr("0011 aaaa 00000000", syntax="LD {a}", cycles=1)
    def ld_cell(st, a):
        _ = st.mem["data"][a]

    @mcu.instr("0100 aaaa vvvvvvvv", syntax="RW {a}, {v}", cycles=1)
    def rw_cell(st, a, v):
        _ = st.mem["data"][a]
        st.mem["data"][a] = v

    mcu.mem_alias("slice_lsb", "data[0][5:2]", bit_order="lsb_first")
    mcu.mem_alias("pair_le", "data[2:4]", endian="little", bit_order="msb_first")
    mcu.mem_alias("byte_lsb", "data[5]", bit_order="lsb_first")

    # 1) Bit-slice watchpoint ignores writes outside its slice.
    sim = isa.Simulator(mcu)
    sim.poke("data[0]", 0b00_1010_00)  # bits[5:2]=1010 -> lsb_first = 0101 (5)
    sim.load_cells(
        "prog",
        {
            0: 0x1000 | 0b11_1010_11,  # bits[5:2] unchanged -> must NOT trigger
            1: 0x1000 | 0b11_1100_11,  # bits[5:2]=1100 -> lsb_first = 0011 (3) -> triggers
            2: 0xFFFF,
        },
    )
    sim.add_watchpoint("slice_lsb", kind="w")
    res = sim.run(max_steps=10)
    assert (res.stop_reason, res.steps, res.pc) == ("watchpoint", 2, 2)
    assert res.detail == {
        "id": 1,
        "target": "slice_lsb",
        "access": "w",
        "pc": 1,
        "old": 5,
        "new": 3,
    }

    # 2) Single-cell lsb_first watchpoint skips unchanged write and transforms changed value.
    sim = isa.Simulator(mcu)
    sim.poke("data[5]", 0x0F)  # lsb_first = 0xF0
    sim.load_cells("prog", {0: 0x1000 | (5 << 8) | 0x0F, 1: 0x1000 | (5 << 8) | 0x80, 2: 0xFFFF})
    sim.add_watchpoint("byte_lsb", kind="w")
    res = sim.run(max_steps=10)
    assert (res.stop_reason, res.steps) == ("watchpoint", 2)
    assert (res.detail["old"], res.detail["new"]) == (0xF0, 0x01)

    # 3) Multi-cell watchpoint computes alias-level old/new and skips unchanged writes.
    sim = isa.Simulator(mcu)
    sim.poke("pair_le", 0x0102)  # data[2]=0x02, data[3]=0x01
    sim.load_cells(
        "prog",
        {
            0: 0x1000 | (2 << 8) | 0x02,  # write same value to data[2] -> skip
            1: 0x2000 | (2 << 8) | 0xAB,  # data[2]=0x0A, data[3]=0x0B -> pair_le=0x0B0A
            2: 0xFFFF,
        },
    )
    sim.add_watchpoint("pair_le", kind="w", condition="m.pair_le == 0x0B0A")
    res = sim.run(max_steps=10)
    assert (res.stop_reason, res.steps, res.pc) == ("watchpoint", 2, 2)
    assert res.detail == {
        "id": 1,
        "target": "pair_le",
        "access": "w",
        "pc": 1,
        "old": 0x0102,
        "new": 0x0B0A,
    }

    # 4) Multi-cell read and rw ordering (read precedes later write in same step).
    sim = isa.Simulator(mcu)
    sim.poke("pair_le", 0x1234)
    sim.load_cells("prog", {0: 0x4000 | (2 << 8) | 0x99, 1: 0xFFFF})
    sim.add_watchpoint("pair_le", kind="rw")
    res = sim.step()
    assert res.stop_reason == "watchpoint"
    assert (res.detail["access"], res.detail["old"], res.detail["new"]) == ("r", None, 0x1234)
    assert sim.read("pair_le") == 0x1299

    # 5) Bit-slice read watchpoint extracts slice with bit_order.
    sim = isa.Simulator(mcu)
    sim.poke("data[0]", 0b00_1010_00)
    sim.load_cells("prog", {0: 0x3000, 1: 0xFFFF})
    sim.add_watchpoint("slice_lsb", kind="r")
    res = sim.step()
    assert res.stop_reason == "watchpoint"
    assert res.detail == {
        "id": 1,
        "target": "slice_lsb",
        "access": "r",
        "pc": 0,
        "old": None,
        "new": 5,
    }

    # 6) Multi-cell mem_alias cannot overlap Device; single-cell device mem_alias watchpoint stays safe.
    class Dev(isa.Device):
        def read(self, addr, now):
            return 0x77

        def write(self, addr, value, now):
            pass

        def get_state(self):
            return {}

        def set_state(self, state):
            pass

    mcu.mem_alias("dev_cell", "data[6]", bit_order="msb_first")
    sim = isa.Simulator(mcu)
    with pytest.raises(isa.DeviceError, match="multi-cell mem_alias"):
        sim.attach(Dev(), space="data", lo=3, hi=4)
    # Defense-in-depth: even if device list is inconsistent, add_watchpoint rejects multi-cell overlap.
    sim_bad = isa.Simulator(mcu)
    sim_bad._devices.append(
        {"obj": Dev(), "space": "data", "lo": 3, "hi": 4, "context": {"read": False, "write": False}}
    )
    with pytest.raises(isa.DebugError, match="multi-cell mem_alias"):
        sim_bad.add_watchpoint("pair_le", kind="w")
    sim.attach(Dev(), space="data", lo=6, hi=7)
    sim.load_cells("prog", {0: 0x3000 | (6 << 8), 1: 0x1000 | (6 << 8) | 0x88, 2: 0xFFFF})
    sim.add_watchpoint("dev_cell", kind="rw")
    res = sim.step()
    assert res.stop_reason == "watchpoint"
    assert (res.detail["access"], res.detail["old"], res.detail["new"]) == ("r", None, 0x77)
    res = sim.step()
    assert res.stop_reason == "watchpoint"
    assert (res.detail["access"], res.detail["old"], res.detail["new"]) == ("w", None, 0x88)

    # 7) REPL watch and print work with mem_alias.
    sim = isa.Simulator(mcu)
    sim.poke("pair_le", 0x0304)
    sim.load_cells("prog", {0: 0x1000 | (3 << 8) | 0x09, 1: 0xFFFF})
    out = StringIO()
    run_repl(sim, StringIO("watch pair_le w\nrun\nprint m.pair_le\nquit\n"), out)
    text = out.getvalue()
    assert "Stopped: watchpoint" in text
    assert f"{0x0904:d} (0x904)" in text


def test_reset_clear_mem_aliases_and_snapshot_isolation():
    mcu = _make_mcu(cell_bits=8, size=8)
    mcu.mem_alias("static_alias", "data[0]", bit_order="msb_first")
    sim = isa.Simulator(mcu)
    sim.mem_alias("runtime_alias", "data[1:3]", endian="big", bit_order="msb_first")

    sim.poke("static_alias", 0x11)
    sim.poke("runtime_alias", 0x2233)
    snap = sim.snapshot()

    # reset() resets memory values but preserves both static and runtime mem_aliases.
    sim.reset()
    assert set(sim.mem_aliases) == {"static_alias", "runtime_alias"}
    assert sim.read("static_alias") == 0
    assert sim.read("runtime_alias") == 0

    # restore() restores memory contents cleanly and still preserves mem_aliases.
    sim.restore(snap)
    assert sim.read("static_alias") == 0x11
    assert sim.read("runtime_alias") == 0x2233

    # clear_mem_aliases() clears runtime registrations while keeping MCU declarations.
    sim.clear_mem_aliases()
    assert list(sim.mem_aliases) == ["static_alias"]
    assert sim.read("static_alias") == 0x11
    with pytest.raises(isa.TargetError):
        sim.read("runtime_alias")
    # Re-registering the cleared name succeeds.
    sim.mem_alias("runtime_alias", "data[3:5]", endian="little", bit_order="msb_first")
    assert sim.mem_aliases["runtime_alias"].lo == 3

    # Snapshot compatibility requires identical mem_aliases.
    with pytest.raises(isa.SnapshotError):
        sim.restore(snap)

    sim_moved = isa.Simulator(mcu)
    sim_moved.mem_alias("runtime_alias", "data[3:5]", endian="little", bit_order="msb_first")
    snap_moved = sim_moved.snapshot()
    sim_moved.clear_mem_aliases()
    with pytest.raises(isa.SnapshotError):
        sim_moved.restore(snap_moved)


def test_dynamic_mem_alias_watchpoint_and_stimulus_follow_name_and_guard_dangling():
    mcu = _make_mcu(cell_bits=8, size=16)

    @mcu.instr("0001 aaaa vvvvvvvv", syntax="ST {a}, {v}", cycles=1)
    def store(st, a, v):
        st.mem["data"][a] = v

    @mcu.instr("0010 0000 0000 0000", syntax="NOP", cycles=1)
    def nop(st):
        pass

    sim = isa.Simulator(mcu)
    sim.mem_alias("output", "data[1]", bit_order="msb_first")
    sim.add_watchpoint("output", kind="w")
    sim.at_cycle(1).set("output", 0xAA)
    sim.load_cells(
        "prog",
        {
            0: 0x1000 | (1 << 8) | 0x11,
            1: 0x1000 | (2 << 8) | 0x22,
            2: 0x2000,
            3: 0xFFFF,
        },
    )

    # Move output from data[1] to data[2] before running.
    sim.clear_mem_aliases()
    sim.mem_alias("output", "data[2]", bit_order="msb_first")

    # Step 0 writes data[1] -> watchpoint on moved "output" does NOT fire.
    res0 = sim.step()
    assert res0.stop_reason == "step_done"
    assert sim.read("data[1]") == 0x11

    # Step 1 (cycle 1): stimulus writes 0xAA to data[2] (new output), then ST writes 0x22 -> watchpoint fires!
    res1 = sim.step()
    assert res1.stop_reason == "watchpoint"
    assert res1.detail["target"] == "output"
    assert res1.detail["old"] == 0xAA
    assert res1.detail["new"] == 0x22
    assert sim.read("data[2]") == 0x22

    # Clearing mem_aliases without re-adding "output" rejects step/run BEFORE mutating state.
    sim.clear_mem_aliases()
    pc_before = sim.state.pc
    cyc_before = sim.cycle
    data3_before = sim.read("data[3]")
    with pytest.raises(isa.DebugError, match="unknown memory alias"):
        sim.step()
    assert sim.state.pc == pc_before
    assert sim.cycle == cyc_before
    assert sim.read("data[3]") == data3_before

    # Clearing watchpoints leaves dangling set_mem_alias stimulus -> rejected at S0 too.
    sim.delete("all")
    with pytest.raises(isa.StimulusError, match="unknown memory alias"):
        sim.run(max_steps=5)
    assert sim.state.pc == pc_before
    assert sim.cycle == cyc_before


def test_large_multi_cell_watchpoint_skips_cell_reconstruction_without_access():
    mcu = isa.MCU("large_watch")
    mcu.space("prog", cell_bits=16, size=16, access="ro", reset_state="initialized", init=0, is_program=True)
    mcu.space("data", cell_bits=8, size=4097, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=4, reset=0, is_pc=True)
    for pol in ("on_undefined_opcode", "on_out_of_range", "on_write_to_readonly", "on_uninitialized_read"):
        getattr(mcu, pol)("stop")

    @mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
    def nop(st):
        st.mem["data"][4096] = 1

    @mcu.instr("0010 0000 0000 0000", syntax="WR", cycles=1)
    def wr(st):
        st.mem["data"][100] = 0x5A

    sim = isa.Simulator(mcu)
    sim.mem_alias("big", "data[0:4096]", endian="little", bit_order="msb_first")
    sim.add_watchpoint("big", kind="w")
    sim.load_cells("prog", {0: 0x1000, 1: 0x1000, 2: 0x2000, 3: 0xFFFF})

    calls = 0
    orig = sim._mem_alias_step_cells

    def counted_step_cells(info):
        nonlocal calls
        calls += 1
        return orig(info)

    sim._mem_alias_step_cells = counted_step_cells
    assert sim.step().stop_reason == "step_done"
    assert sim.step().stop_reason == "step_done"
    assert calls == 0

    res = sim.step()
    assert res.stop_reason == "watchpoint"
    assert calls == 1


def test_external_set_avoids_redundant_mem_alias_read():
    mcu = _make_mcu(cell_bits=8, size=8)

    @mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
    def nop(st):
        pass

    mcu.mem_alias("pair", "data[0:2]", endian="little", bit_order="msb_first")
    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 0x1000, 1: 0xFFFF})
    events = []
    sim.subscribe(events.append)

    read_calls = 0
    orig_read = sim._mem_alias_read

    def spy_read(info):
        nonlocal read_calls
        read_calls += 1
        return orig_read(info)

    sim._mem_alias_read = spy_read
    sim.poke("pair", 0x1234)
    sim.at_cycle(0).set("pair", 0x5678)
    sim.step()
    assert read_calls == 0
    ext_texts = [ev.text for ev in events if ev.kind == "external"]
    assert ext_texts == ["poke pair 0x0000->0x1234", "set pair 0x1234->0x5678"]


def test_multi_cell_write_watchpoint_ignores_read_seq_values():
    mcu = _make_mcu(cell_bits=8, size=8, reset_state="initialized", init=0)

    @mcu.instr("0001 vvvvvvvv 0000", syntax="RDWR {v}", cycles=1)
    def rdwr(st, v):
        _ = st.mem["data"][1]
        st.mem["data"][0] = v

    @mcu.instr("0010 0000 0000 0000", syntax="RD1", cycles=1)
    def rd1(st):
        _ = st.mem["data"][1]

    mcu.mem_alias("pair", "data[0:2]", endian="little", bit_order="msb_first")

    # 1) Write watchpoint: read_seq on data[1] must not invent a write change or corrupt old value.
    sim = isa.Simulator(mcu)
    sim.read_seq("data[1]", [0x77, 0x88])
    sim.add_watchpoint("pair", kind="w")
    sim.load_cells("prog", {0: 0x1000, 1: 0x1000 | (0x12 << 4), 2: 0xFFFF})

    res0 = sim.step()
    assert res0.stop_reason == "step_done"

    res1 = sim.step()
    assert res1.stop_reason == "watchpoint"
    assert res1.detail["access"] == "w"
    assert res1.detail["old"] == 0
    assert res1.detail["new"] == 0x12

    # 2) Read watchpoint still observes the read_seq value returned for data[1].
    sim_r = isa.Simulator(mcu)
    sim_r.read_seq("data[1]", [0x77])
    sim_r.add_watchpoint("pair", kind="r")
    sim_r.load_cells("prog", {0: 0x2000, 1: 0xFFFF})
    res_r = sim_r.step()
    assert res_r.stop_reason == "watchpoint"
    assert res_r.detail["access"] == "r"
    assert res_r.detail["new"] == 0x7700


def test_ultra_wide_bitslice_and_reg_field_poke_and_event_format(tmp_path):
    import json
    from rpkbin.isasim.events import WriteRecord, _format

    mcu = isa.MCU("wide16k")
    mcu.space("prog", cell_bits=16, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
    mcu.space("small_ram", cell_bits=16, size=4, access="rw", reset_state="initialized", init=0)
    mcu.space("wide_ram", cell_bits=16000, size=2, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=4, reset=0, is_pc=True)
    mcu.reg("huge", bits=16000, reset=0, fields={"H": (15999, 0)})
    mcu.mem_alias("wide_slice", "wide_ram[0][15999:0]", bit_order="msb_first")
    for pol in ("on_undefined_opcode", "on_out_of_range", "on_write_to_readonly", "on_uninitialized_read"):
        getattr(mcu, pol)("stop")

    @mcu.instr("0001 0000 0000 0000", syntax="WRH", cycles=1)
    def wrh(st):
        st.reg["huge.H"] = 1 << 15999

    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 0x1000, 1: 0xFFFF})
    events = []
    sim.subscribe(events.append)

    wide_val = 1 << 15999
    expected_hex = f"0x{wide_val:04000x}"
    sim.poke("wide_slice", wide_val)
    assert sim.read("wide_slice") == wide_val
    sim.poke("huge.H", wide_val)
    assert sim.read("huge.H") == wide_val

    ext_texts = [ev.text for ev in events if ev.kind == "external"]
    assert ext_texts == [
        f"poke wide_slice 0->{expected_hex}",
        f"poke huge.H 0->{expected_hex}",
    ]

    # Instruction writing a 16,000-bit register field formats without integer string digit limits.
    sim.reset()
    res = sim.step()
    assert res.stop_reason == "step_done"
    instr_ev = next(ev for ev in reversed(events) if ev.kind == "instr")
    text_line = _format(instr_ev, mcu, fmt="text")
    assert f"huge.H:0->{expected_hex}" in text_line

    # Direct WriteRecord formatting via _format also handles >4300 decimal digits.
    fake_ev = sim._event(
        "instr",
        pc=0,
        cells=(0x1000,),
        text="NOP",
        writes=(WriteRecord("reg", "huge.H", None, 0, wide_val, 16000, True),),
        consumed=1,
    )
    assert f"huge.H:0->{expected_hex}" in _format(fake_ev, mcu, fmt="text")

    # Scheduled stimulus on 16,000-bit bit-slice formats without integer string digit limits.
    sim.reset()
    sim.at_cycle(0).set("wide_slice", wide_val)
    sim.step()
    sched_ext = [ev.text for ev in events if ev.kind == "external"]
    assert f"set wide_slice 0->{expected_hex}" in sched_ext

    # JSONL trace via enable_trace() preserves int types for small values and encodes ultra-wide ints losslessly.
    trace_path = tmp_path / "wide_trace.jsonl"
    sim.reset()
    sim.enable_trace(str(trace_path), fmt="jsonl")
    try:
        sim.poke("small_ram[0]", 42)
        sim.poke("wide_slice", wide_val)
        sim.step()
    finally:
        sim.disable_trace()

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    small_write = records[0]["writes"][0]
    assert type(small_write["old"]) is int and small_write["old"] == 0
    assert type(small_write["new"]) is int and small_write["new"] == 42

    wide_poke_write = records[1]["writes"][0]
    assert type(wide_poke_write["old"]) is int and wide_poke_write["old"] == 0
    assert wide_poke_write["new"] == expected_hex
    assert int(wide_poke_write["new"], 16) == wide_val

    wide_instr = next(rec for rec in records if rec["kind"] == "instr")
    assert wide_instr["writes"][0]["new"] == expected_hex
    assert int(wide_instr["writes"][0]["new"], 16) == wide_val

    # JSONL encoding threshold (abs >= 10**640) is independent of sys.set_int_max_str_digits().
    import sys

    thousand_digit_val = 10**999
    boundary_ev = sim._event(
        "instr",
        pc=0,
        cells=(0x1000,),
        text="NOP",
        writes=(WriteRecord("reg", "huge.H", None, 0, wide_val, 16000, True),),
        consumed=1,
        detail={
            "below": 10**640 - 1,
            "at": 10**640,
            "neg": -(10**640),
            "k1": thousand_digit_val,
        },
    )
    prev_limit = sys.get_int_max_str_digits()
    try:
        outs = []
        for limit in (640, 4300, 5000, 0):
            sys.set_int_max_str_digits(limit)
            outs.append(_format(boundary_ev, mcu, fmt="jsonl"))
        assert len(set(outs)) == 1
        decoded = json.loads(outs[0])
        assert decoded["writes"][0]["new"] == expected_hex
        assert type(decoded["detail"]["below"]) is int
        assert decoded["detail"]["below"] == 10**640 - 1
        assert decoded["detail"]["at"] == f"0x{10**640:x}"
        assert decoded["detail"]["neg"] == f"-0x{10**640:x}"
        assert decoded["detail"]["k1"] == f"0x{thousand_digit_val:x}"
        assert int(decoded["detail"]["at"], 16) == 10**640
        assert int(decoded["detail"]["neg"], 16) == -(10**640)
        assert int(decoded["detail"]["k1"], 16) == thousand_digit_val
    finally:
        sys.set_int_max_str_digits(prev_limit)
