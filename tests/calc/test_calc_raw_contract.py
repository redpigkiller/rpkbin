"""Raw FXP boundaries, explicit reformat policy, and hardware bit fields."""

from fractions import Fraction
import math

import numpy as np
import pytest

from rpkbin.calc.core.format_engine import FxpSpec
from rpkbin.calc.core.fxp_model import (
    extract_spec,
    make_fxp,
    make_fxp_from_raw_uint,
    make_fxpi,
    raw_uint_from_apy_scalar,
    read_scalar_bit_field,
    reformat_value,
    update_scalar_bit_field,
    update_scalar_from_field,
)


def raw(value):
    return raw_uint_from_apy_scalar(value, extract_spec(value))


@pytest.mark.parametrize("literal", ["0x100", "0b100000000", "-0x1", "-0b1"])
@pytest.mark.parametrize("signed", [False, True])
def test_raw_literal_rejects_value_outside_hardware_width(literal, signed):
    with pytest.raises(ValueError, match=r"WL=8 range \[0, 255\].*mask|WL=8 range \[0, 255\].*REPL"):
        make_fxp(literal, FxpSpec(signed=signed, wl=8, fwl=4))


@pytest.mark.parametrize("field,text", [
    ("hex", "100"), ("hex", "-0x1"),
    ("uint", "256"), ("uint", "-1"),
    ("bin", "0b1 0000.0000"), ("bin", "-1"),
])
def test_raw_inspector_fields_reject_out_of_range_without_changing_source(field, text):
    source = make_fxp("0xA5", "Q4.4")
    with pytest.raises(ValueError, match=r"WL=8 range \[0, 255\].*REPL"):
        update_scalar_from_field(source, field, text)
    assert raw(source) == 0xA5


@pytest.mark.parametrize("literal", ["0x0000_FF", "0b0000_1111 1111", "0b1111.1111"])
def test_raw_initializer_ignores_leading_zeros_and_display_separators(literal):
    assert raw(make_fxp(literal, "UQ8.0")) == 255


@pytest.mark.parametrize("text", ["0b1012", "1e1", "+1", "0b", "0b01\t01"])
def test_binary_fields_require_binary_digits_after_display_cleanup(text):
    with pytest.raises(ValueError, match="only 0 and 1"):
        update_scalar_from_field(make_fxp("Q8.0"), "bin", text)


@pytest.mark.parametrize("field,text", [("hex", "00ff"), ("uint", "0xFF"), ("bin", "0b0000 1111.1111")])
def test_raw_inspector_fields_accept_full_unsigned_pattern_for_signed_fxp(field, text):
    result = update_scalar_from_field(make_fxp("Q8.0"), field, text)
    assert raw(result) == 255
    assert float(result) == -1
    assert result.target_real is None


def test_unscaled_signed_integer_edit_retains_explicit_saturation_and_wrap():
    source = make_fxp("Q8.0")
    assert float(update_scalar_from_field(source, "s2", "255", overflow="SAT")) == 127
    assert float(update_scalar_from_field(source, "s2", "255", overflow="WRAP")) == -1
    assert float(make_fxpi(-1, "UQ8.0", overflow="SAT")) == 0
    assert float(make_fxpi(-1, "UQ8.0", overflow="WRAP")) == 255


@pytest.mark.parametrize("target, expected_raw, expected_real", [
    (FxpSpec(True, 16, 0), 255, 255),
    (FxpSpec(True, 4, 0), 15, -1),
    (FxpSpec(False, 8, 0), 255, 255),
    (FxpSpec(True, 8, 4), 255, -Fraction(1, 16)),
    (FxpSpec(True, 8, -2), 255, -4),
])
def test_hex_pin_policy_matches_for_scalar_and_array(target, expected_raw, expected_real):
    scalar = make_fxp("0xFF", "Q8.0")
    array = make_fxpi([[-1, 0]], "Q8.0")
    result_scalar = reformat_value(scalar, target, pin_mode="hex")
    result_array = reformat_value(array, target, pin_mode="hex")
    assert extract_spec(result_scalar) == extract_spec(result_array) == target
    assert raw(result_scalar) == int(result_array.to_bits()[0][0]) == expected_raw
    assert float(result_scalar) == float(result_array[0, 0]) == expected_real
    assert raw(scalar) == 255
    assert array.to_bits() == [[255, 0]]
    assert result_scalar.target_real is None
    assert result_array.target_values is None


@pytest.mark.parametrize("quant,overflow,expected", [
    ("RND", "SAT", 7), ("TRN", "SAT", 7),
    ("RND", "WRAP", 8), ("TRN", "WRAP", 7),
])
def test_real_pin_applies_numeric_rounding_and_overflow_for_scalar_and_array(quant, overflow, expected):
    scalar = make_fxp(7.75, "Q6.2")
    array = make_fxp([[7.75]], "Q6.2")
    target = FxpSpec(True, 4, 0)
    converted = reformat_value(scalar, target, pin_mode="real", quant=quant, overflow=overflow)
    converted_array = reformat_value(array, target, pin_mode="real", quant=quant, overflow=overflow)
    assert raw(converted) == converted_array.to_bits()[0][0] == expected
    assert float(scalar) == float(array[0, 0]) == 7.75


@pytest.mark.parametrize("spec", [
    FxpSpec(True, 1, 0), FxpSpec(False, 1, 3),
    FxpSpec(True, 8, -3), FxpSpec(False, 8, 12),
    FxpSpec(True, 256, 300), FxpSpec(False, 256, -5),
])
def test_whole_width_bit_field_uses_hardware_bits_independent_of_sign_and_fwl(spec):
    source = make_fxp_from_raw_uint(0, spec, target_real=0)
    maximum = (1 << spec.wl) - 1
    result = update_scalar_bit_field(source, spec.wl - 1, 0, maximum)
    assert read_scalar_bit_field(result, spec.wl - 1, 0) == maximum
    assert raw(result) == maximum
    assert extract_spec(result) == spec
    assert result.target_real is None
    assert raw(source) == 0
    assert source.target_real == 0


def test_partial_bit_field_preserves_neighbors_and_accepts_numpy_integer():
    source = make_fxp("0xA5", "Q4.4")
    updated = update_scalar_bit_field(source, np.int64(5), np.int64(2), np.uint8(3))
    assert raw(updated) == 0x8D
    assert read_scalar_bit_field(updated, 5, 2) == 3
    assert read_scalar_bit_field(updated, 7, 6) == 2
    assert read_scalar_bit_field(updated, 1, 0) == 1
    assert raw(source) == 0xA5


@pytest.mark.parametrize("high,low", [(-1, 0), (8, 0), (2, 3), (3, -1)])
def test_invalid_bit_field_range_rejected(high, low):
    source = make_fxp("Q8.0")
    for operation in (lambda: read_scalar_bit_field(source, high, low),
                      lambda: update_scalar_bit_field(source, high, low, 0)):
        with pytest.raises(ValueError, match="WL=8"):
            operation()


@pytest.mark.parametrize("high,low", [(True, 0), (3, False), (3.0, 0), (3, "0")])
def test_non_integer_bit_field_indices_rejected(high, low):
    source = make_fxp("Q8.0")
    with pytest.raises(TypeError, match="indices must be integers"):
        read_scalar_bit_field(source, high, low)


@pytest.mark.parametrize("value", [True, np.bool_(False), 1.0, "1"])
def test_non_integer_bit_field_value_rejected(value):
    with pytest.raises(TypeError, match="value must be an integer"):
        update_scalar_bit_field(make_fxp("Q8.0"), 3, 2, value)


@pytest.mark.parametrize("value", [-1, 4])
def test_bit_field_value_never_silently_wraps(value):
    with pytest.raises(ValueError, match=r"\[0, 3\]"):
        update_scalar_bit_field(make_fxp("Q8.0"), 3, 2, value)


@pytest.mark.parametrize("value", [2, 2.0, np.int8(2), [1], np.array([1])])
def test_native_values_have_no_implicit_bit_field_width(value):
    with pytest.raises(TypeError, match="FXP scalar"):
        read_scalar_bit_field(value, 1, 0)


def test_array_bit_fields_require_explicit_scalar_selection():
    array = make_fxpi([[1, -1]], "Q8.0")
    with pytest.raises(TypeError, match="FXP scalar"):
        update_scalar_bit_field(array, 3, 0, 5)
    cell = array[0, 1]
    result = update_scalar_bit_field(cell, 3, 0, 5)
    assert raw(result) == 0xF5
    assert array.to_bits() == [[1, 255]]


@pytest.mark.parametrize("expression,expected", [
    ("pi / 2", math.pi / 2), ("e - 1", math.e - 1),
    ("-(2 + 3) * +4 / 2", -10), ("17 // 3 + 17 % 3", 7),
    ("2 ** -3", 0.125), ("0x1_0 + 0b11", 19),
    ("((0xA5 & 0xF0) | 3) ^ 1", 0xA2),
    ("(1 << 6) >> 2", 16), ("~0 & 0xFF", 255),
])
def test_real_editor_supports_bounded_numeric_and_integer_bit_expressions(expression, expected):
    source = make_fxp(0, "Q32.32")
    result = update_scalar_from_field(source, "real", expression)
    assert float(result) == pytest.approx(expected, abs=2 ** -32)
    assert result.target_real == Fraction(expected)
    assert float(source) == 0


@pytest.mark.parametrize("expression", [
    "().__class__.__base__.__subclasses__()", "(1).__class__", "abs(-1)",
    "[1][0]", "[1]", "{1: 2}", "{1}", "(1, 2)",
    "1j", "True", "False", "'2'", "None", "pi if True else e", "x", "1 < 2",
    "lambda: 1", "(x := 1)", "2 +", "1; 2",
])
def test_real_editor_rejects_general_python_without_mutating_source(expression):
    source = make_fxp(0.25, "Q4.4")
    with pytest.raises(ValueError, match="REPL"):
        update_scalar_from_field(source, "real", expression)
    assert float(source) == 0.25
    assert source.target_real == Fraction(1, 4)


def test_real_editor_call_and_attribute_payloads_have_no_side_effects(tmp_path):
    marker = tmp_path / "clipboard-must-not-execute.txt"
    payload = f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')"
    with pytest.raises(ValueError, match="REPL"):
        update_scalar_from_field(make_fxp("Q8.0"), "real", payload)
    assert not marker.exists()


def test_malicious_fxp_paste_is_rejected_before_any_cell_changes(tmp_path):
    from rpkbin.calc.core import WorkspaceSession
    from rpkbin.calc.core.editing import EditJournal, paste_rectangle

    session = WorkspaceSession(seed_demo_vars=False)
    source = make_fxp([[1, 2]], "Q8.0")
    session.user_ns["a"] = source
    journal = EditJournal(session)
    marker = tmp_path / "paste-must-not-execute.txt"
    payload = f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')"
    with pytest.raises(ValueError, match="REPL"):
        paste_rectangle(session, journal, "a", 0, 0, "9\t" + payload)
    assert source.to_bits() == [[1, 2]]
    assert journal.state()["undo_count"] == 0
    assert not marker.exists()


@pytest.mark.parametrize("expression,reason", [
    (" " * 4096 + "1", "4096 characters"),
    ("1+" * 200 + "1", "512 AST nodes"),
    ("2 ** 4097", "exponents"), ("2 ** -4097", "exponents"),
    ("1 << 4097", "Shift counts"), ("1 >> -1", "Shift counts"),
    ("(1 << 4096) ** 4096", "16384 bits"),
    ("(1 << 4096) * (1 << 4096) * (1 << 4096) * (1 << 4096)", "16384 bits"),
    ("1e999", "finite"), ("1e308 * 2", "finite"),
    ("1e308 ** 2", "overflow"), ("1 / 0", "Division by zero"),
    ("1 // 0", "Division by zero"), ("1 % 0", "Division by zero"),
    ("0 ** -1", "Division by zero"), ("(-1) ** 0.5", "real int/float"),
    ("1.0 << 2", "Bit operators require integers"),
    ("1 & 2.0", "Bit operators require integers"), ("~1.0", "Bit operators require integers"),
])
def test_real_editor_rejects_resource_overruns_and_numeric_errors_clearly(expression, reason):
    with pytest.raises(ValueError, match=reason) as failure:
        update_scalar_from_field(make_fxp("Q8.0"), "real", expression)
    assert "REPL" in str(failure.value)


@pytest.mark.parametrize("expression,expected", [
    ("2 ** 4096", 1 << 4096), ("1 << 4096", 1 << 4096),
    ("1 >> 4096", 0),
    ("(1 << 4096) * (1 << 4096) * (1 << 4096) * (1 << 4095)", 1 << 16383),
], ids=["power-limit", "left-shift-limit", "right-shift-limit", "integer-bit-limit"])
def test_real_editor_accepts_numeric_budget_boundaries(expression, expected):
    result = update_scalar_from_field(make_fxp("Q8.0"), "real", expression)
    assert result.target_real == expected
    assert result.is_saturated == (expected > 127)
