"""Acceptance tests for the shared AST condition language across Core, REPL, and CLI."""

from io import StringIO
import sys

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.cli import main as cli_main
from rpkbin.isasim.debug import CONDITION_MAX_BITS, CONDITION_MAX_DEPTH
from rpkbin.isasim.helpers import sign_extend
from rpkbin.isasim.repl import run_repl


def _make_condition_mcu(*, wide_bits: int = 16000):
    mcu = isa.MCU("cond_lang")
    mcu.space(
        "prog",
        cell_bits=16,
        size=16,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    mcu.space("data", cell_bits=8, size=16, access="rw", reset_state="initialized", init=0)
    mcu.space("_ram", cell_bits=8, size=8, access="rw", reset_state="initialized", init=0)

    mcu.reg("pc", bits=4, reset=0, is_pc=True)
    mcu.reg("val", bits=16, reset=0, fields={"hi": (15, 8), "lo": (7, 0)})
    mcu.reg("_scratch", bits=16, reset=0, fields={"hi": (15, 8), "lo": (7, 0)})
    mcu.reg("wide", bits=wide_bits, reset=0)
    mcu.alias("val_alias", "val")
    mcu.mem_alias("ram_alias", "_ram[0]", bit_order="msb_first")
    mcu.irq_line("wake")
    mcu.irq_line("_wake")

    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 aaaa vvvvvvvv", syntax="ST {a}, {v}", cycles=1)
    def store_data(st, a, v):
        st.mem["data"][a] = v

    @mcu.instr("0010 vvvv vvvv vvvv", syntax="SETV {v}", cycles=1)
    def set_val(st, v):
        st.reg["val"] = v

    @mcu.instr("0100 0000 0000 0000", syntax="SETWIDE", cycles=1)
    def set_wide(st):
        st.reg["wide"] = (1 << (wide_bits - 1)) | 0x45

    @mcu.instr("0011 0000 0000 0000", syntax="NOP", cycles=1)
    def nop(st):
        pass

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("done")

    return mcu


def test_integer_and_boolean_domain_operators_and_short_circuit():
    mcu = _make_condition_mcu()
    sim = isa.Simulator(mcu)
    sim.poke("val", 0x05)
    sim.poke("data[1]", 4)

    assert sim.evaluate("7 // 2") == 3
    assert sim.evaluate("7 % 4") == 3
    assert sim.evaluate("2 ** 5") == 32
    assert sim.evaluate("2 ** 0") == 1
    assert sim.evaluate("0 ** 0") == 1
    assert sim.evaluate("~0") == -1
    assert sim.evaluate("+(r.val) - 2") == 3
    assert sim.evaluate("r.val << 2") == 20
    assert sim.evaluate("r.val >> 1") == 2
    assert sim.evaluate("(r.val & 3) ^ (r.val | 8)") == (1 ^ 13)

    # `and` / `or` must short-circuit and preserve Python selected-operand return values.
    assert sim.evaluate("0 or 7") == 7
    assert sim.evaluate("5 or 9") == 5
    assert sim.evaluate("0 and 9") == 0
    assert sim.evaluate("3 and 9") == 9
    assert sim.evaluate("0 or r.val") == 5
    assert sim.evaluate("10 if r.val == 5 else 20") == 10
    assert sim.evaluate("10 if r.val != 5 else 20") == 20

    # Membership (`in` / `not in`) with tuple/set RHS at the final comparison step.
    assert sim.evaluate("r.val in (4, 5, 6)") is True
    assert sim.evaluate("r.val in {4, 5, 6}") is True
    assert sim.evaluate("r.val not in (1, 2)") is True
    assert sim.evaluate("r.val in ()") is False
    assert sim.evaluate("0 <= r.val < 10") is True
    assert sim.evaluate("r.val == 5 in (5, 6)") is True
    assert sim.evaluate("r.val == 5 in (6, 7)") is False

    # Arithmetic domain errors map to DebugError at evaluation time.
    for expr in ("1 // 0", "1 % 0", "1 << -1", "1 >> -1", "2 ** -1", "0 ** -1"):
        with pytest.raises(isa.DebugError):
            sim.evaluate(expr)


def test_ternary_helper_selection_and_integer_methods():
    mcu = _make_condition_mcu()
    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 0x3000, 1: 0x3000, 2: 0xFFFF})  # NOP, NOP, HLT
    sim.poke("val", 0x85)

    # Identical-signature branches work dynamically across both outcomes.
    expr = "(bit if cycle == 0 else to_unsigned)(r.val, 7)"
    assert sim.evaluate(expr) == 1  # cycle == 0 -> bit(0x85, 7) == 1
    sim.step()
    assert sim.cycle == 1
    assert sim.evaluate(expr) == 5  # cycle == 1 -> to_unsigned(0x85, 7) == 5

    # Differing-arity branches validate and call ONLY the selected helper branch.
    mixed = "(bit if cycle == 1 else parity)(7, 0)"
    assert sim.evaluate(mixed) == 1  # cycle == 1 -> bit(7, 0)
    sim.step()
    assert sim.cycle == 2
    with pytest.raises(isa.DebugError):
        sim.evaluate(mixed)  # cycle == 2 -> parity(7, 0) fails arity

    # Breakpoint using the ternary helper can be installed cleanly and fires on chosen branch.
    sim.reset()
    bp0 = sim.add_breakpoint(0, condition="(bit if cycle == 0 else parity)(7, 0) == 1")
    assert sim.next_breakpoint() == bp0
    bp_id = sim.add_breakpoint(1, condition="(bit if cycle == 1 else parity)(7, 0) == 1")
    res = sim.run(max_steps=3)
    assert res.stop_reason == "breakpoint"
    assert res.detail["id"] == bp_id

    # Whitelisted read-only integer methods + subscript lookups.
    sim.poke("val", 0x0B)  # 0b1011 -> bit_count=3, bit_length=4
    assert sim.evaluate('r["val"].bit_count()') == 3
    assert sim.evaluate('(r["val_alias"] + 1).bit_length()') == 4
    assert sim.evaluate("to_signed(0xFF, 8)") == -1
    assert sim.evaluate("sign_extend(0x85, 8, 16)") == sign_extend(0x85, 8, 16)
    assert sim.evaluate("parity(0b1011)") == 1


def test_lazy_evaluation_skips_unchosen_device_and_error_branches():
    class SpyDevice(isa.Device):
        def __init__(self):
            self.peek_calls = 0

        def read(self, addr, now, ctx=None):
            return 0x55

        def write(self, addr, value, now, ctx=None):
            pass

    mcu = _make_condition_mcu()
    sim = isa.Simulator(mcu)
    dev = SpyDevice()
    # Device has no peek() -> direct sim.evaluate("m.data[0]") raises DebugError.
    sim.attach(dev, space="data", lo=0, hi=1)

    with pytest.raises(isa.DebugError):
        sim.evaluate("m.data[0]")

    assert sim.evaluate("1 or (1 // 0)") == 1
    assert sim.evaluate("0 and (1 // 0)") == 0
    assert sim.evaluate("1 or m.data[0]") == 1
    assert sim.evaluate("0 and m.data[0]") == 0
    assert sim.evaluate("1 or (1 << 100000)") == 1
    assert sim.evaluate("0 and (2 ** 100000)") == 0
    assert sim.evaluate("pc if cycle == 0 else m.data[0]") == 0
    assert sim.evaluate("(1 // 0) if cycle != 0 else 42") == 42


def test_dynamic_mem_alias_rebinding_and_dangling_behavior():
    mcu = _make_condition_mcu()
    sim = isa.Simulator(mcu)
    sim.load_cells(
        "prog",
        {
            0: 0x1000 | (3 << 8) | 0x99,  # ST 3, 0x99
            1: 0x3000,                    # NOP
            2: 0x1000 | (4 << 8) | 0x77,  # ST 4, 0x77
            3: 0xFFFF,
        },
    )
    sim.poke("data[1]", 0x11)
    sim.poke("data[2]", 0x22)
    sim.mem_alias("dyn", "data[1]", bit_order="msb_first")

    bp_id = sim.add_breakpoint(1, condition="m.dyn == 0x22")
    assert "m.dyn == 0x22" in "\n".join(sim.list_breakpoints())
    wp_id = sim.add_watchpoint("data[4]", kind="w", condition="m.dyn == 0x22")

    # Rebind runtime mem_alias from data[1] to data[2] AFTER installing conditions.
    sim.clear_mem_aliases()
    sim.mem_alias("dyn", "data[2]", bit_order="msb_first")

    assert sim.evaluate("m.dyn") == 0x22
    assert sim.evaluate('m["dyn"]') == 0x22

    # Breakpoint at pc=1 sees m.dyn == data[2] == 0x22.
    r1 = sim.run(max_steps=5)
    assert r1.stop_reason == "breakpoint"
    assert r1.detail["id"] == bp_id

    # Continue to pc=2 (ST 4, 0x77) -> watchpoint condition also reads rebound m.dyn == 0x22.
    r2 = sim.run(max_steps=5)
    assert r2.stop_reason == "watchpoint"
    assert r2.detail["id"] == wp_id

    # Clearing without rebinding leaves m.dyn dangling -> DebugError on evaluation.
    sim.clear_mem_aliases()
    with pytest.raises(isa.DebugError):
        sim.evaluate("m.dyn")


def test_underscore_mcu_targets_allowed_and_private_attrs_rejected():
    mcu = _make_condition_mcu()
    sim = isa.Simulator(mcu)
    sim.poke("_scratch", 0xABCD)
    sim.poke("_ram[0]", 0x42)
    sim.poke("_ram[1]", 0x5E)
    sim.poke("irq._wake", 1)

    assert sim.evaluate("r._scratch == 0xABCD") is True
    assert sim.evaluate('r["_scratch"] == 0xABCD') is True
    assert sim.evaluate("r._scratch.lo == 0xCD") is True
    assert sim.evaluate('r["_scratch"].lo == 0xCD') is True
    assert sim.evaluate("m._ram[1] == 0x5E") is True
    assert sim.evaluate('m["_ram"][1] == 0x5E') is True
    assert sim.evaluate("m.ram_alias == 0x42") is True
    assert sim.evaluate('m["ram_alias"] == 0x42') is True
    assert sim.evaluate("i._wake == 1") is True
    assert sim.evaluate('i["_wake"] == 1') is True

    for expr in ("r._scratch._fields", "(1).__class__", "r.__dict__", "m.__class__"):
        with pytest.raises(isa.DebugError):
            sim.evaluate(expr)
        with pytest.raises(isa.DebugError):
            sim.add_breakpoint(0, condition=expr)


def test_resource_bounds_65536_bits_and_max_depth():
    assert CONDITION_MAX_BITS == 65536
    assert CONDITION_MAX_DEPTH == 256

    mcu = _make_condition_mcu(wide_bits=16000)
    sim = isa.Simulator(mcu)
    huge_val = (1 << 15999) | 0x12345
    huge_hex = hex(huge_val)
    sim.poke("wide", huge_val)

    assert sim.evaluate(f"r.wide == {huge_hex}") is True
    bp_id = sim.add_breakpoint(0, condition=f"r.wide == {huge_hex}")
    assert sim.next_breakpoint() == bp_id

    # Boundary-inclusive 65536-bit operations succeed.
    assert sim.evaluate("(1 << 65535).bit_length()") == 65536
    assert sim.evaluate("to_unsigned(-1, 65536).bit_length()") == 65536
    assert sim.evaluate("to_signed(1 << 65535, 65536)") == -(1 << 65535)
    assert sim.evaluate("sign_extend(0x80, 8, 65536)") == sign_extend(0x80, 8, 65536)
    assert sim.evaluate("2 ** 65535 == (1 << 65535)") is True

    # Over-limit allocations are blocked BEFORE allocation.
    over_limit_exprs = (
        "1 << 65536",
        "-1 << 65536",
        "2 ** 65536",
        "3 ** 41350",
        "(1 << 40000) * (1 << 40000)",
        "to_unsigned(1, 65537)",
        "to_signed(1, 65537)",
        "sign_extend(1, 8, 65537)",
        ("-" * 260) + "1",
    )
    for expr in over_limit_exprs:
        with pytest.raises(isa.DebugError):
            sim.evaluate(expr)


def test_oversized_mem_alias_rejected_before_reading_or_packing(monkeypatch):
    mcu = isa.MCU("oversized_alias")
    mcu.space("prog", cell_bits=16, size=4, access="ro", reset_state="initialized", init=0, is_program=True)
    mcu.space("big_ram", cell_bits=8, size=8200, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=2, reset=0, is_pc=True)
    mcu.mem_alias("ok_8192", "big_ram[0:8192]", endian="big", bit_order="msb_first")
    mcu.mem_alias("over_8193", "big_ram[0:8193]", endian="big", bit_order="msb_first")
    mcu.mem_alias("over_lsb", "big_ram[0:8193]", endian="little", bit_order="lsb_first")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("done")

    sim = isa.Simulator(mcu)
    sim.poke("big_ram[0]", 1)
    assert sim.evaluate("m.ok_8192") == (1 << (8191 * 8))

    # Lazy short-circuit still skips oversized alias when unchosen.
    assert sim.evaluate("1 or m.over_8193") == 1

    # Evaluating an alias > 65,536 bits must fail BEFORE calling _mem_alias_read / _pack_mem_alias_cells / _mem_read.
    calls = []
    orig_read = sim._mem_alias_read
    orig_pack = sim._pack_mem_alias_cells
    orig_mem_read = sim._mem_read

    def spy_read(info):
        calls.append(("read", info.name))
        return orig_read(info)

    def spy_pack(info, cells):
        calls.append(("pack", info.name))
        return orig_pack(info, cells)

    def spy_mem_read(space, addr, **kwargs):
        calls.append(("mem", space, addr))
        return orig_mem_read(space, addr, **kwargs)

    monkeypatch.setattr(sim, "_mem_alias_read", spy_read)
    monkeypatch.setattr(sim, "_pack_mem_alias_cells", spy_pack)
    monkeypatch.setattr(sim, "_mem_read", spy_mem_read)

    for alias_expr in ("m.over_8193 == 0", "m.over_lsb == 0"):
        with pytest.raises(isa.DebugError):
            sim.evaluate(alias_expr)
        assert calls == []

    # Dynamic rebinding works too: runtime alias > 65,536 bits fails before packing.
    sim.mem_alias("dyn_over", "big_ram[0:8193]", endian="big", bit_order="msb_first")
    with pytest.raises(isa.DebugError):
        sim.evaluate("m.dyn_over == 0")
    assert calls == []


@pytest.mark.parametrize(
    "bad_expr",
    [
        "5 / 2",
        "1.5",
        "'x'",
        'f"{r.val}"',
        'f"{pc=}"',
        "(lambda: 1)()",
        "(x := 1)",
        "[x for x in (1, 2)]",
        "(1, 2)",
        "{1, 2}",
        "0 in (1, 2) < 3",
        "r.val is 0",
        "bit == 1",
        "bit(value=0, value=1, n=0)",
        "(bit if cycle == 0 else parity)(value=0, value=1, n=0)",
        "to_unsigned(value=1, bits=8, bits=4)",
        "r.val.bit_count(x=1, x=2)",
        "(1).to_bytes(1, 'big')",
        "unknown_helper(1)",
        "foo",
        "r.no_such_reg",
        "m.no_such_space[0]",
        "i.no_such_irq",
        "m.data",
    ],
)
def test_install_time_rejections_across_core_entrypoints(bad_expr):
    mcu = _make_condition_mcu()
    sim = isa.Simulator(mcu)

    with pytest.raises(isa.DebugError):
        sim.evaluate(bad_expr)
    with pytest.raises(isa.DebugError):
        sim.add_breakpoint(0, condition=bad_expr)
    with pytest.raises(isa.DebugError):
        sim.add_watchpoint("data[0]", kind="w", condition=bad_expr)
    with pytest.raises(isa.DebugError):
        sim.add_display(bad_expr)


def test_repl_and_cli_condition_surface(tmp_path, monkeypatch, capsys):
    mcu = _make_condition_mcu()
    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 0x2005, 1: 0x3000, 2: 0xFFFF})  # SETV 5, NOP, HLT
    sim.poke("_scratch", 9)

    commands = "\n".join(
        [
            "break 1 if (bit if cycle == 1 else to_unsigned)(r.val, 2) == 1",
            "watch data[0] w if 0 or r._scratch",
            "display 0 or r.val",
            "run",
            "print r._scratch",
            'print f"{pc=}"',
            "break 0 if 5 / 2 == 2",
            "quit",
        ]
    )
    out = StringIO()
    run_repl(sim, StringIO(commands), out)
    text = out.getvalue()
    assert "Stopped: breakpoint" in text
    assert "9 (0x9)" in text
    assert "error:" in text

    # Also exercise CLI `repl` end-to-end with an MCU file and program file.
    mcu_path = tmp_path / "cond_mcu.py"
    mcu_path.write_text(
        "\n".join(
            [
                "from rpkbin.isasim import MCU",
                "mcu = MCU('cli_cond')",
                "mcu.space('prog', cell_bits=16, size=8, access='ro', "
                "reset_state='initialized', init=0, is_program=True)",
                "mcu.space('data', cell_bits=8, size=8, access='rw', "
                "reset_state='initialized', init=0)",
                "mcu.reg('pc', bits=3, reset=0, is_pc=True)",
                "mcu.reg('_scratch', bits=8, reset=7)",
                "mcu.on_undefined_opcode('stop')",
                "mcu.on_out_of_range('stop')",
                "mcu.on_write_to_readonly('stop')",
                "mcu.on_uninitialized_read('stop')",
                "@mcu.instr('0000 0000 0000 0000', syntax='NOP', cycles=1)",
                "def nop(st): pass",
                "@mcu.instr('1111 1111 1111 1111', syntax='HLT', cycles=1)",
                "def hlt(st): st.halt('done')",
            ]
        ),
        encoding="utf-8",
    )
    prog_path = tmp_path / "prog.isa"
    prog_path.write_text("NOP\nHLT\n", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "\n".join(
                [
                    "break 1 if r._scratch == 7 and (0 or 1)",
                    "run",
                    'print (lambda: 1)()',
                    "quit",
                ]
            )
        ),
    )
    rc = cli_main(["repl", str(mcu_path), str(prog_path), "--format", "asm"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Stopped: breakpoint" in captured.out
    assert "error:" in captured.out


def test_repl_wide_values_fallback_and_continue():
    mcu = _make_condition_mcu(wide_bits=16000)
    sim = isa.Simulator(mcu)
    sim.load_cells("prog", {0: 0x4000, 1: 0x3000, 2: 0xFFFF})  # SETWIDE, NOP, HLT

    expected_wide = (1 << 15999) | 0x45
    expected_hex = hex(expected_wide)

    # Exercise print, display, watchpoint stop detail, and subsequent REPL command execution on 16,000-bit values.
    commands = "\n".join(
        [
            "watch wide w",
            "display r.wide",
            "print r.wide",
            "run",
            "print r.wide",
            "print -r.wide",
            "regs",
            "print 7",
            "quit",
        ]
    )
    out = StringIO()
    run_repl(sim, StringIO(commands), out)
    text = out.getvalue()
    assert "Stopped: watchpoint" in text
    assert f"1: r.wide = {expected_hex}" in text
    assert f"\n{expected_hex}\n" in text
    assert f"\n-{expected_hex}\n" in text
    assert f"'{expected_hex}'" in text
    assert "\n7 (0x7)\n" in text

    # Also verify runtime ValueError fallback when sys.set_int_max_str_digits is lowered to 640.
    prev_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        sim.reset()
        sim.poke("wide", 1 << 2200)  # ~663 digits (>640)
        out_low = StringIO()
        run_repl(sim, StringIO("print r.wide\nprint 11\nquit\n"), out_low)
        low_text = out_low.getvalue()
        assert f"\n{hex(1 << 2200)}\n" in low_text
        assert "\n11 (0xb)\n" in low_text
    finally:
        sys.set_int_max_str_digits(prev_limit)
