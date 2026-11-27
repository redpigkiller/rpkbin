"""Phase 4 regression tests for ISASIM-01..ISASIM-18."""

from __future__ import annotations

import json
import sys
from io import StringIO

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.cli import main as cli_main
from rpkbin.isasim.repl import run_repl

from .toys.toy_a import make_mcu as toy_a


def _minimal_mcu(name: str = "phase4", *, cell_bits: int = 16, size: int = 8) -> isa.MCU:
    mcu = isa.MCU(name)
    mcu.space(
        "prog",
        cell_bits=cell_bits,
        size=size,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    mcu.reg("pc", bits=4, reset=0, is_pc=True)
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("1" * cell_bits, syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("done")

    return mcu


def test_isasim_01_snapshot_save_preserves_existing_file_on_error(tmp_path):
    class UnserializableDevice(isa.Device):
        def read(self, addr: int, now: int) -> int:
            return 0

        def write(self, addr: int, value: int, now: int) -> None:
            pass

        def get_state(self) -> dict[str, object]:
            return {"bad": {1, 2}}

        def set_state(self, state: dict[str, object]) -> None:
            pass

    mcu = toy_a()
    sim = isa.Simulator(mcu)
    sim.attach(UnserializableDevice(), space="data", lo=0, hi=1)
    snap = sim.snapshot()

    target = tmp_path / "checkpoint.json"
    original = '{"valid":"checkpoint"}'
    target.write_text(original, encoding="utf-8")

    with pytest.raises(isa.SnapshotError):
        snap.save(target)

    assert target.read_text(encoding="utf-8") == original


def test_isasim_02_snapshot_roundtrip_wide_integers(tmp_path):
    class WideDevice(isa.Device):
        def __init__(self) -> None:
            self.state_val: dict[str, object] = {
                "big": 1 << 16000,
                "neg_big": -(1 << 15999),
                "tag": "0x10",
            }

        def read(self, addr: int, now: int) -> int:
            return 0

        def write(self, addr: int, value: int, now: int) -> None:
            pass

        def get_state(self) -> dict[str, object]:
            return dict(self.state_val)

        def set_state(self, state: dict[str, object]) -> None:
            self.state_val = dict(state)

    mcu = isa.MCU("wide_snap")
    mcu.space(
        "prog",
        cell_bits=16,
        size=4,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    mcu.space(
        "wide_ram",
        cell_bits=16000,
        size=2,
        access="rw",
        reset_state="initialized",
        init=0,
    )
    mcu.reg("pc", bits=2, reset=0, is_pc=True)
    mcu.reg("huge", bits=16000, reset=0)
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("done")

    sim = isa.Simulator(mcu)
    dev = WideDevice()
    sim.attach(dev, space="wide_ram", lo=1, hi=2)
    wide_reg = (1 << 15999) | 0x12345
    wide_cell = (1 << 15998) | 0xBEEF
    sim.poke("huge", wide_reg)
    sim.poke("wide_ram[0]", wide_cell)
    sim.at_cycle(10**645).set("huge", wide_reg - 1)

    path = tmp_path / "wide_snap.json"
    prev_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        sim.snapshot().save(path)
        loaded = isa.Snapshot.load(path)
    finally:
        sys.set_int_max_str_digits(prev_limit)

    raw_text = path.read_text(encoding="utf-8")
    assert '"0x' in raw_text
    assert json.loads(raw_text)["mcu_name"] == "wide_snap"

    restored = isa.Simulator(mcu)
    restored_dev = WideDevice()
    restored_dev.state_val = {"big": 0, "neg_big": 0, "tag": "other"}
    restored.attach(restored_dev, space="wide_ram", lo=1, hi=2)
    restored.at_cycle(10**645).set("huge", wide_reg - 1)
    restored.restore(loaded)

    assert restored.read("huge") == wide_reg
    assert restored.read("wide_ram[0]") == wide_cell
    assert restored_dev.state_val["big"] == 1 << 16000
    assert restored_dev.state_val["neg_big"] == -(1 << 15999)
    assert restored_dev.state_val["tag"] == "0x10"
    assert isinstance(restored_dev.state_val["tag"], str)


def test_isasim_03_watchpoint_on_field_alias_masks_whole_reg_write():
    mcu = isa.MCU("field_alias_watch")
    mcu.space(
        "prog",
        cell_bits=8,
        size=4,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    mcu.reg("pc", bits=2, reset=0, is_pc=True)
    mcu.reg("word", bits=8, reset=0xAB, fields={"hi": (7, 4), "lo": (3, 0)})
    mcu.alias("LowAlias", "word.lo")
    mcu.alias("WholeAlias", "word")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0000 0001", syntax="WRITE_WHOLE", cycles=1)
    def write_whole(st):
        st.reg["word"] = 0xCD

    @mcu.instr("0000 0010", syntax="READ_WHOLE", cycles=1)
    def read_whole(st):
        _ = st.reg["word"]

    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 1, 1: 2})
    sim.add_watchpoint("LowAlias", kind="w")
    res_w = sim.step()
    assert res_w.stop_reason == "watchpoint"
    assert res_w.detail["old"] == 0xB
    assert res_w.detail["new"] == 0xD

    sim2 = isa.Simulator(mcu)
    sim2.load_cells("prog", {0: 2})
    sim2.add_watchpoint("LowAlias", kind="r")
    res_r = sim2.step()
    assert res_r.stop_reason == "watchpoint"
    assert res_r.detail["old"] is None
    assert res_r.detail["new"] == 0xB

    sim3 = isa.Simulator(mcu)
    sim3.load_cells("prog", {0: 1})
    sim3.add_watchpoint("WholeAlias", kind="w")
    res_whole = sim3.step()
    assert res_whole.stop_reason == "watchpoint"
    assert res_whole.detail["old"] == 0xAB
    assert res_whole.detail["new"] == 0xCD


def test_isasim_04_template_requires_whitespace_between_word_and_operand():
    mcu = isa.MCU("mnemonic_boundary")
    mcu.space(
        "prog",
        cell_bits=8,
        size=8,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    mcu.reg("pc", bits=3, reset=0, is_pc=True)
    mcu.reg("x", bits=8, reset=0)
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 iiii", syntax="ADD {i}", cycles=1)
    def add_imm(st, i):
        st.reg["x"] += i

    @mcu.instr("0010 iiii", syntax="ADDI {i}", cycles=1)
    def addi_imm(st, i):
        st.reg["x"] += i

    @mcu.instr("0011 aaaa", syntax="LD [{a}]", cycles=1)
    def load_mem(st, a):
        st.reg["x"] = a

    @mcu.instr("0100 dd ss", syntax="CP r{d}, r{s}", cycles=1)
    def copy_reg(st, d, s):
        st.reg["x"] = d + s

    image = isa.assemble(mcu, "ADD 2\nADDI 3\nLD [5]\nLD[6]\nCP r1,r2\n")
    assert image.cells == {0: 0x12, 1: 0x23, 2: 0x35, 3: 0x36, 4: 0x46}

    with pytest.raises(isa.AssemblyError):
        isa.assemble(mcu, "ADDX")
    with pytest.raises(isa.AssemblyError):
        isa.assemble(mcu, "ADD(1)")


def test_isasim_05_select_instruction_catches_expression_error_in_range_template():
    mcu = isa.MCU("range_fallback")
    mcu.space(
        "prog",
        cell_bits=8,
        size=8,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    mcu.reg("pc", bits=3, reset=0, is_pc=True)
    mcu.reg("x", bits=8, reset=0)
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 iiii", syntax="MOV {i}", cycles=1, operands={"i": isa.Operand(range=(0, 15))})
    def mov_imm(st, i):
        st.reg["x"] = i

    @mcu.instr("0010 aaaa", syntax="MOV [{a}]", cycles=1)
    def mov_mem(st, a):
        st.reg["x"] = a

    image = isa.assemble(mcu, "MOV 4\nMOV [5]")
    assert image.cells == {0: 0x14, 1: 0x25}

    with pytest.raises(isa.AssemblyError, match="division by zero"):
        isa.assemble(mcu, "MOV 1 // 0")


def test_isasim_06_reentrancy_guard_blocks_nested_drive_reset_restore_and_load():
    mcu = _minimal_mcu("reentrant")
    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 0xFFFF})
    snap = sim.snapshot()

    def nested_step(s: isa.Simulator) -> None:
        s.step()

    sim.at_cycle(0).call(nested_step)
    result = sim.step()
    assert result.stop_reason == "python_error"
    assert result.steps == 0
    assert result.pc == 0
    assert isinstance(result.exception, isa.UsageError)
    assert "already running" in str(result.exception)
    assert sim._running is False
    assert sim._phase is None

    for name, fn in (
        ("reset", lambda s: s.reset()),
        ("restore", lambda s: s.restore(snap)),
        ("load_cells", lambda s: s.load_cells("prog", {0: 0xFFFF})),
        ("load_assembly", lambda s: s.load_assembly(isa.assemble(mcu, "HLT"))),
    ):
        sim2 = isa.Simulator(mcu)
        sim2.load_cells("prog", {0: 0xFFFF})
        sim2.at_cycle(0).call(fn)
        res = sim2.step()
        assert res.stop_reason == "python_error", name
        assert isinstance(res.exception, isa.UsageError), name
        assert "already running" in str(res.exception), name
        assert sim2._running is False


def test_isasim_07_source_addresses_accept_backslash_include_paths(tmp_path):
    mcu = _minimal_mcu("win_inc")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "part.inc").write_text("HLT\n", encoding="utf-8")
    main_file = tmp_path / "main.asm"
    main_file.write_text('.include "sub/part.inc"\n', encoding="utf-8")

    image = isa.assemble(mcu, main_file.read_text(encoding="utf-8"), filename=str(main_file))
    sim = isa.Simulator(mcu)
    sim.load_assembly(image)

    bp_id = sim.add_breakpoint(r"sub\part.inc:1")
    assert bp_id == 1
    assert sim.next_breakpoint() == bp_id

    with pytest.raises(isa.TargetError, match="unknown source file"):
        sim.add_breakpoint(r"sub\missing.inc:1")


def test_isasim_08_run_repl_unsubscribes_on_exit():
    sim = isa.Simulator(toy_a())
    assert len(sim._subscribers) == 0

    out1 = StringIO()
    run_repl(sim, StringIO("quit\n"), out1)
    assert len(sim._subscribers) == 0

    out2 = StringIO()
    run_repl(sim, StringIO(""), out2)  # EOF
    assert len(sim._subscribers) == 0


def test_isasim_09_restore_rolls_back_devices_atomically():
    class GoodDevice(isa.Device):
        def __init__(self, val: int) -> None:
            self.val = val

        def read(self, addr: int, now: int) -> int:
            return self.val

        def write(self, addr: int, value: int, now: int) -> None:
            self.val = value

        def get_state(self) -> dict[str, object]:
            return {"val": self.val}

        def set_state(self, state: dict[str, object]) -> None:
            self.val = int(state["val"])  # type: ignore[arg-type]

    class FailOnRestoreDevice(GoodDevice):
        def __init__(self, val: int, fail_with: Exception | None = None) -> None:
            super().__init__(val)
            self.fail_with = fail_with

        def set_state(self, state: dict[str, object]) -> None:
            if self.fail_with is not None:
                raise self.fail_with
            super().set_state(state)

    mcu = toy_a()
    sim = isa.Simulator(mcu)
    d1 = GoodDevice(1)
    d2 = FailOnRestoreDevice(2)
    sim.attach(d1, space="data", lo=0, hi=1)
    sim.attach(d2, space="data", lo=1, hi=2)
    sim.poke("r0", 11)
    snap = sim.snapshot()

    d1.val = 77
    d2.val = 88
    sim.poke("r0", 99)

    for exc in (NotImplementedError("nope"), ValueError("bad"), KeyError("k"), TypeError("t")):
        d2.fail_with = exc
        with pytest.raises(isa.SnapshotError):
            sim.restore(snap)
        assert d1.val == 77
        assert d2.val == 88
        assert sim.read("r0") == 99

    class UnimplementedSetState(GoodDevice):
        set_state = isa.Device.set_state  # type: ignore[assignment]

    sim_bad = isa.Simulator(mcu)
    bd1 = GoodDevice(1)
    bd2 = UnimplementedSetState(2)
    sim_bad.attach(bd1, space="data", lo=0, hi=1)
    sim_bad.attach(bd2, space="data", lo=1, hi=2)
    bd1.val = 55
    with pytest.raises(isa.SnapshotError):
        sim_bad.restore(snap)
    assert bd1.val == 55


def test_isasim_10_cli_script_syntax_error_exits_2_cleanly(tmp_path, capsys):
    mcu_path = tmp_path / "mcu.py"
    mcu_path.write_text(
        "from rpkbin.isasim import MCU\n"
        "mcu = MCU('cli_err')\n"
        "mcu.space('prog', cell_bits=8, size=4, access='ro', "
        "reset_state='initialized', init=0, is_program=True)\n"
        "mcu.reg('pc', bits=2, reset=0, is_pc=True)\n"
        "mcu.on_undefined_opcode('stop')\n"
        "mcu.on_out_of_range('stop')\n"
        "mcu.on_write_to_readonly('stop')\n"
        "mcu.on_uninitialized_read('stop')\n"
        "@mcu.instr('00000000', syntax='HLT', cycles=1)\n"
        "def hlt(st): st.halt('done')\n",
        encoding="utf-8",
    )
    prog_path = tmp_path / "prog.asm"
    prog_path.write_text("HLT\n", encoding="utf-8")
    bad_script = tmp_path / "bad_setup.py"
    bad_script.write_text("def setup(sim:\n", encoding="utf-8")

    rc = cli_main(
        ["run", str(mcu_path), str(prog_path), "--max-steps", "1", "--script", str(bad_script)]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.startswith("error: ")


def _write_cli_mcu(tmp_path, name="diag.py", body=None):
    path = tmp_path / name
    if body is None:
        body = (
            "from rpkbin.isasim import MCU\n"
            "mcu = MCU('diag')\n"
            "mcu.space('prog', cell_bits=8, size=8, access='ro', "
            "reset_state='initialized', init=0, is_program=True)\n"
            "mcu.space('inp', cell_bits=8, size=4, access='rw', "
            "reset_state='initialized', init=0)\n"
            "mcu.reg('pc', bits=3, reset=0, is_pc=True)\n"
            "mcu.on_undefined_opcode('stop')\n"
            "mcu.on_out_of_range('stop')\n"
            "mcu.on_write_to_readonly('stop')\n"
            "mcu.on_uninitialized_read('stop')\n"
            "@mcu.instr('00000001', syntax='BOOM', cycles=1)\n"
            "def boom(st): raise ValueError('kaboom!')\n"
            "@mcu.instr('00000010', syntax='ABORT', cycles=1)\n"
            "def abort(st): st.fault('my_fault', 'bad state')\n"
            "@mcu.instr('00000011', syntax='SPIN', cycles=1)\n"
            "def spin(st):\n"
            "    _ = st.mem['inp'][0]\n"
            "    st.next_pc = st.pc\n"
            "@mcu.instr('11111111', syntax='HLT', cycles=1)\n"
            "def hlt(st): st.halt('done')\n"
        )
    path.write_text(body, encoding="utf-8")
    return path


def test_isasim_11_cli_non_halted_diagnostics(tmp_path, capsys):
    mcu_path = _write_cli_mcu(tmp_path)

    for src_text, extra, expected_err in (
        ("BOOM\n", [], ("stop: python_error", "kaboom!")),
        ("ABORT\n", [], ("stop: fault", "my_fault")),
        ("SPIN\n", ["--stuck-window", "3"], ("stop: stuck", "stuck_pcs=", "inp[0x0]=0")),
    ):
        asm_path = tmp_path / "prog.asm"
        asm_path.write_text(src_text, encoding="utf-8")
        rc = cli_main(["run", str(mcu_path), str(asm_path), "--max-steps", "10", *extra])
        captured = capsys.readouterr()
        assert rc == 1
        assert "RESULT stop_reason=" in captured.out
        for fragment in expected_err:
            assert fragment in captured.err

    # Normal max_steps stop without detail keeps stderr clean.
    spin_path = tmp_path / "spin.asm"
    spin_path.write_text("SPIN\n", encoding="utf-8")
    rc = cli_main(["run", str(mcu_path), str(spin_path), "--max-steps", "2", "--stuck-window", "0"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "RESULT stop_reason=max_steps" in captured.out
    assert captured.err == ""


def test_isasim_12_cli_integer_errors_bases_and_help(tmp_path, capsys):
    mcu_path = _write_cli_mcu(tmp_path)
    asm_path = tmp_path / "prog.asm"
    asm_path.write_text("HLT\n", encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        cli_main(["run", str(mcu_path), str(asm_path), "--max-steps", "abc"])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "_integer" not in err
    assert "invalid integer value" in err

    for radix_val in ("0b10", "0o2", "0x2"):
        assert cli_main(["run", str(mcu_path), str(asm_path), "--max-steps", radix_val]) == 0
        assert "RESULT stop_reason=halted" in capsys.readouterr().out

    bin_path = tmp_path / "prog.bin"
    bin_path.write_bytes(b"\xff")
    assert (
        cli_main(
            [
                "run",
                str(mcu_path),
                str(bin_path),
                "--format",
                "bin",
                "--base",
                "0b0",
                "--max-steps",
                "0o2",
            ]
        )
        == 0
    )
    capsys.readouterr()

    for args in (["--help"], ["assemble", "--help"], ["run", "--help"], ["repl", "--help"]):
        with pytest.raises(SystemExit) as help_exit:
            cli_main(args)
        assert help_exit.value.code == 0
        help_out = capsys.readouterr().out
        assert len(help_out.splitlines()) > 3


def test_isasim_13_repl_mem_clamping_and_stuck_report():
    mcu = isa.MCU("repl_mem_stuck")
    mcu.space("prog", cell_bits=8, size=4, access="ro", reset_state="initialized", init=0, is_program=True)
    mcu.space("data", cell_bits=8, size=10, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=2, reset=0, is_pc=True)
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("00000001", syntax="SPIN", cycles=1)
    def spin(st):
        _ = st.mem["data"][2]
        st.next_pc = st.pc

    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 1})
    sim.poke("data[9]", 42)

    out = StringIO()
    run_repl(sim, StringIO("mem data 0\nmem data 10\nmem missing 0\nrun\nquit\n"), out)
    text = out.getvalue()
    assert "data[0x0]: 0 0 0 0 0 0 0 0\n" in text
    assert "data[0x8]: 0 42\n" in text
    assert "error: data[10]: outside size=10\n" in text
    assert "error: space='missing': unknown space\n" in text
    assert "Stopped: stuck\n" in text
    assert "stuck: pcs=[0x0] reads=[data[0x2]=0]\n" in text


def test_isasim_14_rejects_bool_in_int_validators_and_keeps_subclasses(tmp_path):
    class DeclaredInt(int):
        pass

    for fn in (
        lambda: isa.helpers.to_unsigned(True, 8),
        lambda: isa.helpers.to_unsigned(1, True),
        lambda: isa.helpers.to_signed(False, 8),
        lambda: isa.helpers.to_signed(1, True),
        lambda: isa.helpers.sign_extend(True, 4, 8),
        lambda: isa.helpers.sign_extend(1, True, 8),
        lambda: isa.helpers.sign_extend(1, 4, True),
        lambda: isa.helpers.bit(True, 0),
        lambda: isa.helpers.bit(1, False),
        lambda: isa.helpers.set_bit(True, 0, 1),
        lambda: isa.helpers.set_bit(0, True, 1),
        lambda: isa.helpers.set_bit(0, 0, True),
        lambda: isa.helpers.parity(True),
        lambda: isa.helpers.rotate_left(True, 1, 4),
        lambda: isa.helpers.rotate_left(1, True, 4),
        lambda: isa.helpers.rotate_right(1, False, 4),
        lambda: isa.helpers.add_with_carry(True, 1, 0, 8),
        lambda: isa.helpers.add_with_carry(1, 1, True, 8),
        lambda: isa.helpers.sub_with_borrow(1, True, 0, 8),
        lambda: isa.helpers.sub_with_borrow(1, 1, False, 8),
        lambda: isa.helpers.carry_out_of_bit(True, 1, 0, 3),
        lambda: isa.helpers.carry_out_of_bit(1, 1, True, 3),
        lambda: isa.helpers.carry_out_of_bit(1, 1, 0, False),
    ):
        with pytest.raises(isa.UsageError):
            fn()

    assert isa.helpers.to_unsigned(DeclaredInt(3), DeclaredInt(4)) == 3

    m = isa.MCU("bool_check")
    with pytest.raises(isa.MCUDefinitionError):
        m.space("s", cell_bits=True, size=1, access="rw", reset_state="initialized", init=0)
    with pytest.raises(isa.MCUDefinitionError):
        m.space("s", cell_bits=8, size=True, access="rw", reset_state="initialized", init=0)
    with pytest.raises(isa.MCUDefinitionError):
        m.space("s", cell_bits=8, size=1, access="rw", reset_state="initialized", init=False)
    with pytest.raises(isa.MCUDefinitionError):
        m.reg("r", bits=True, reset=0)
    with pytest.raises(isa.MCUDefinitionError):
        m.reg("r", bits=4, reset=True)
    with pytest.raises(isa.MCUDefinitionError):
        m.reg("r", bits=4, reset=0, fields={"A": True})
    with pytest.raises(isa.MCUDefinitionError):
        m.reg("r", bits=4, reset=0, fields={"A": (True, False)})
    with pytest.raises(isa.MCUDefinitionError):
        m.on_out_of_range("ignore", read_value=True)
    with pytest.raises(isa.MCUDefinitionError):
        m.instr("0000", syntax="X", cycles=True)

    mcu = toy_a(with_irq=True)
    with pytest.raises(isa.UsageError):
        isa.Simulator(mcu, history=True)

    sim = isa.Simulator(mcu)
    with pytest.raises(isa.UsageError):
        sim.step(True)
    with pytest.raises(isa.UsageError):
        sim.run(max_steps=True)
    with pytest.raises(isa.UsageError):
        sim.run(max_steps=1, stuck_window=False)
    with pytest.raises(isa.UsageError):
        sim.run(max_steps=1, max_cycles=True)
    with pytest.raises(isa.UsageError):
        sim.disassemble(0, count=True)
    with pytest.raises(isa.UsageError):
        sim.recent_events(True)
    with pytest.raises(isa.LoadError):
        sim.load_cells("prog", {True: 0})
    with pytest.raises(isa.LoadError):
        sim.load_cells("prog", {0: True})

    bin_file = tmp_path / "b.bin"
    bin_file.write_bytes(b"\x00\x00")
    with pytest.raises(isa.LoadError):
        sim.load_bin(bin_file, base=False, order="big")

    with pytest.raises(isa.DeviceError):
        sim.attach(isa.Device(), space="data", lo=False, hi=1)
    with pytest.raises(isa.DeviceError):
        sim.attach(isa.Device(), space="data", lo=0, hi=True)
    with pytest.raises(isa.StimulusError):
        sim.read_seq("data[0]", [True])
    with pytest.raises(isa.StimulusError):
        sim.at_cycle(False)
    with pytest.raises(isa.DebugError):
        sim.at_pc(True)
    with pytest.raises(isa.StimulusError):
        sim.at_pc(0, times=True)
    with pytest.raises(isa.StimulusError):
        sim.on_read("data[0]", nth=True)
    with pytest.raises(isa.StimulusError):
        sim.on_write("data[0]", nth=True)
    with pytest.raises(isa.StimulusError):
        sim.at_cycle(0).delay(True)
    with pytest.raises(isa.StimulusError):
        sim.at_cycle(0).set("r0", True)
    with pytest.raises(isa.StimulusError):
        sim.at_cycle(0).irq("irq0", True)
    with pytest.raises(isa.StimulusError):
        sim.poke("r0", True)

    bp_id = sim.add_breakpoint(0)
    disp_id = sim.add_display("r.r0")
    assert bp_id == 1 and disp_id == 1
    with pytest.raises(isa.DebugError):
        sim.delete(True)
    with pytest.raises(isa.DebugError):
        sim.remove_display(True)
    assert len(sim.list_breakpoints()) == 1

    # State accessors reject bool during semantic execution.
    probe_mcu = _minimal_mcu("state_bool")
    probe_mcu.reg("v", bits=8, reset=0)
    probe_mcu.irq_line("q")

    @probe_mcu.instr("0000000000000001", syntax="BOOLWRITE", cycles=1)
    def bool_write(st):
        st.reg["v"] = True

    sim_st = isa.Simulator(probe_mcu)
    sim_st.load_cells("prog", {0: 1})
    res_st = sim_st.step()
    assert res_st.stop_reason == "python_error"
    assert isinstance(res_st.exception, isa.StateAccessError)


def test_isasim_15_pathlike_and_enable_trace_default_fmt(tmp_path):
    mcu_file = _write_cli_mcu(tmp_path, name="path_mcu.py")
    mcu = isa.load_mcu(mcu_file)
    image = isa.assemble(mcu, "HLT\n")

    bundle_path = tmp_path / "out.isa.json"
    image.save(bundle_path)
    loaded_image = isa.Assembly.load(mcu, bundle_path)
    assert loaded_image.cells == image.cells

    hex_path = tmp_path / "prog.hex"
    hex_path.write_text(":01000000FF00\n:00000001FF\n", encoding="utf-8")
    bin_path = tmp_path / "prog.bin"
    bin_path.write_bytes(b"\xff")
    sym_path = tmp_path / "sym.json"
    sym_path.write_text(json.dumps({"symbols": {"start": 0}}), encoding="utf-8")
    trace_path = tmp_path / "trace.log"
    snap_path = tmp_path / "snap.json"

    with isa.Simulator(mcu) as sim:
        sim.enable_trace(trace_path)  # defaults to fmt="text"
        sim.load_hex(hex_path)
        sim.load_bin(bin_path, base=0)
        sim.load_symbols(sym_path)
        sim.step()
        snap = sim.snapshot()
        snap.save(snap_path)
        loaded_snap = isa.Snapshot.load(snap_path)
        sim.restore(loaded_snap)

    assert "= c=1 STOP halted: done" in trace_path.read_text(encoding="utf-8")


def test_isasim_16_repr_and_context_manager(tmp_path):
    mcu = _minimal_mcu("repr_mcu")
    image = isa.assemble(mcu, "HLT\n")
    sim = isa.Simulator(mcu)
    snap = sim.snapshot()

    assert "MCU('repr_mcu'" in repr(mcu)
    assert "Simulator('repr_mcu'" in repr(sim)
    assert "Assembly('<string>'" in repr(image)
    assert "Snapshot(mcu='repr_mcu'" in repr(snap)

    trace_ok = tmp_path / "ctx_ok.log"
    with isa.Simulator(mcu) as s1:
        s1.load_assembly(image)
        s1.enable_trace(trace_ok)
        assert s1._trace is not None
        s1.step()
    assert s1._trace is None
    assert "HLT" in trace_ok.read_text(encoding="utf-8")

    trace_err = tmp_path / "ctx_err.log"
    with pytest.raises(RuntimeError, match="boom"):
        with isa.Simulator(mcu) as s2:
            s2.enable_trace(trace_err)
            raise RuntimeError("boom")
    assert s2._trace is None


def test_isasim_17_cli_version(capsys):
    from rpkbin import __version__

    with pytest.raises(SystemExit) as raised:
        cli_main(["--version"])
    assert raised.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_isasim_18_repl_help_aliases_info_regs_and_list_breakpoints():
    mcu = isa.MCU("repl_extra")
    mcu.space("prog", cell_bits=8, size=4, access="ro", reset_state="initialized", init=0, is_program=True)
    mcu.space("ram", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=2, reset=0, is_pc=True)
    mcu.reg("x", bits=8, reset=5)
    mcu.irq_line("wake")
    mcu.mem_alias("word0", "ram[0:2]", endian="little", bit_order="msb_first")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("11111111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("done")

    sim = isa.Simulator(mcu)
    sim.poke("word0", 0x1234)
    sim.poke("irq.wake", 1)

    sim.add_breakpoint(0)
    sim.add_breakpoint(1, condition="r.x == 5", temporary=True)
    sim.add_watchpoint("ram[0]", kind="rw")
    sim.add_watchpoint("x", kind="w", condition="r.x > 0")

    listed = sim.list_breakpoints()
    assert listed == [
        "1: break 0",
        "2: break 1 temporary if r.x == 5",
        "3: watch ram[0] rw",
        "4: watch x w if r.x > 0",
    ]
    assert all(not line.endswith(" ") for line in listed)

    out = StringIO()
    run_repl(sim, StringIO("help\nhelp r\nhelp s\nhelp q\ninfo\nregs\nquit\n"), out)
    text = out.getvalue()
    assert "aliases:" in text and "r->run" in text
    assert "run [max_steps]\n" in text
    assert "step [n]\n" in text
    assert "quit\n" in text
    assert "mem_alias word0 -> ram[0:2]" in text
    assert "irq.wake=1\n" in text
    assert "word0 -> ram[0:2] = 0x1234\n" in text
