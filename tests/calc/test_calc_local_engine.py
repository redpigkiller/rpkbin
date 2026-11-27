from fractions import Fraction

import pytest

from rpkbin.app.calc.core.config import CalcConfig


def test_precedence_and_transactional_error():
    from rpkbin.app.calc.local_engine import SimpleCalcEngine

    calc = SimpleCalcEngine(CalcConfig())
    assert calc.evaluate("2+3*4").quantized_value == 14
    assert calc.evaluate("2*(3+4)").quantized_value == 14
    with pytest.raises(ValueError, match="zero"):
        calc.evaluate("3/0")
    assert calc.raw_uint == 14


def test_exact_parallel_projection_and_raw_input():
    from rpkbin.app.calc.local_engine import SimpleCalcEngine

    calc = SimpleCalcEngine(CalcConfig(default_quant="RND_EVEN"))
    calc.set_primary("Q4.4")
    calc.paste("0xFF")
    assert calc.display("DEC") == "-1"
    assert calc.real == Fraction(-1, 16)
    calc.set_primary("Q8.0")
    calc.evaluate("0.50000000000000000000001")
    assert calc.project("Q4.4").quantized_value == Fraction(1, 2)
    assert calc.store("M").exact_fraction > Fraction(1, 2)


def test_recall_exact_and_raw_and_edit_comparison():
    from rpkbin.app.calc.local_engine import SimpleCalcEngine

    calc = SimpleCalcEngine(CalcConfig())
    calc.set_primary("Q4.4")
    calc.evaluate("0.1")
    saved = calc.store("M")
    calc.set_primary("Q4.8")
    calc.recall(saved)
    assert calc.real == Fraction(26, 256)
    calc.recall(saved, raw=True)
    assert calc.real == Fraction(2, 256)
    calc.edit_projection("Q4.4", Fraction(1, 10))
    assert calc.exact == Fraction(1, 8)


def test_signed_unsigned_shifts_and_clipboard_roundtrip():
    from rpkbin.app.calc.local_engine import SimpleCalcEngine

    calc = SimpleCalcEngine(CalcConfig())
    calc.set_primary("Q8.0")
    assert calc.evaluate("0xFF >> 1").quantized_value == -1
    with pytest.raises(ValueError, match="Shift count"):
        calc.evaluate("0xFF >> -1")
    assert calc.raw_uint == 0xFF
    calc.set_primary("UQ8.0")
    assert calc.evaluate("0xFF >> 1").quantized_value == 127
    for radix in ("HEX", "OCT", "BIN"):
        calc.paste(calc.display(radix))
        assert calc.raw_uint == 127
    assert calc.paste("0b0000 0011").raw_uint == 3
    assert calc.evaluate("1e-1 + 2e-1").exact_input == Fraction(3, 10)


def test_custom_template_case_insensitive_sign_prefix():
    from rpkbin.app.calc.core.format_engine import FormatEngine, FxpSpec

    cfg = CalcConfig(
        format_preset="custom",
        custom_signed_tpl="{s_or_u}{wl}.{fwl}f",
        custom_unsigned_tpl="{s_or_u}{wl}.{fwl}f",
        custom_int_tpl="{S_or_U}{wl}i",
    )
    assert FormatEngine.parse_input("s12.2f", cfg) == FxpSpec(True, 12, 2)
    assert FormatEngine.parse_input("S12.2f", cfg) == FxpSpec(True, 12, 2)
    assert FormatEngine.parse_input("u12.2f", cfg) == FxpSpec(False, 12, 2)
    assert FormatEngine.parse_input("U12.2f", cfg) == FxpSpec(False, 12, 2)
    assert FormatEngine.parse_input("s16i", cfg) == FxpSpec(True, 16, 0)
    assert FormatEngine.parse_input("S16i", cfg) == FxpSpec(True, 16, 0)
    assert FormatEngine.parse_input("u16i", cfg) == FxpSpec(False, 16, 0)
    assert FormatEngine.parse_input("U16i", cfg) == FxpSpec(False, 16, 0)


def test_windows_style_base_display_grouping():
    from rpkbin.app.calc.local_engine import SimpleCalcEngine

    calc = SimpleCalcEngine(CalcConfig())
    calc.paste("0")
    assert calc.display("HEX") == "0x0"
    assert calc.display("DEC") == "0"
    assert calc.display("OCT") == "0o0"
    assert calc.display("BIN") == "0b 0000"

    calc.paste("21")
    assert calc.display("HEX") == "0x15"
    assert calc.display("DEC") == "21"
    assert calc.display("OCT") == "0o25"
    assert calc.display("BIN") == "0b 0001 0101"

    calc.paste("0x10015")
    assert calc.display("HEX") == "0x1 0015"
    assert calc.display("OCT") == "0o200 025"
    assert calc.display("BIN") == "0b 0001 0000 0000 0001 0101"
    assert calc.parse_operand("0x1 0015", "HEX") == 0x10015
    assert calc.parse_operand("0o200 025", "OCT") == 0o200025
    assert calc.parse_operand("0b 0001 0101", "BIN") == 21

    # Negative / 8-bit FXP still formats compactly without forced 32-bit padding
    calc.set_primary("Q4.4")
    calc.paste("0xFF")
    assert calc.display("HEX") == "0xFF"
    assert calc.display("DEC") == "-1"
    assert calc.display("OCT") == "0o377"
    assert calc.display("BIN") == "0b 1111 1111"
    assert calc.parse_operand("0xFF", "HEX") == Fraction(-1, 16)
    assert calc.parse_operand("0b 1111 1111", "BIN") == Fraction(-1, 16)


def test_bitwise_nand_nor_and_all_four_shift_modes():
    from rpkbin.app.calc.local_engine import SimpleCalcEngine

    calc = SimpleCalcEngine(CalcConfig())
    calc.set_primary("UQ8.0")
    assert calc.evaluate("0x0F NAND 0xFF", "HEX").raw_uint == 0xF0
    assert calc.evaluate("0x0F NOR 0x30", "HEX").raw_uint == 0xC0

    calc.set_primary("Q8.0")
    calc.set_shift_mode("arithmetic")
    assert calc.evaluate("0x80 >> 1", "HEX").raw_uint == 0xC0

    calc.set_shift_mode("logical")
    assert calc.evaluate("0x80 >> 1", "HEX").raw_uint == 0x40

    calc.set_shift_mode("rotate")
    assert calc.evaluate("0x81 >> 1", "HEX").raw_uint == 0xC0
    assert calc.evaluate("0x81 << 1", "HEX").raw_uint == 0x03

    calc.set_shift_mode("rotate_carry")
    calc.carry_bit = 0
    assert calc.evaluate("0x81 << 1", "HEX").raw_uint == 0x02
    assert calc.carry_bit == 1
    assert calc.evaluate("0x02 >> 1", "HEX").raw_uint == 0x81
    assert calc.carry_bit == 0
