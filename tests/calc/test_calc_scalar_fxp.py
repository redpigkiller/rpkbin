from fractions import Fraction

import pytest

from rpkbin.app.calc.core.format_engine import FormatEngine


def test_exact_rounding_above_half():
    from rpkbin.app.calc.core.scalar_fxp import quantize_scalar

    result = quantize_scalar("0.50000000000000000000001", FormatEngine.parse("Q8.0"),
                             quant="RND_EVEN", overflow="SAT")
    assert result.raw_uint == 1
    assert result.quantized_value == Fraction(1)
    assert result.status_kind == "quantized"


def test_field_write_preserves_neighboring_bits():
    from rpkbin.app.calc.core.scalar_fxp import read_raw_bit_field, update_raw_bit_field

    raw = update_raw_bit_field(0xA5, 8, 5, 2, 3)
    assert raw == 0x8D
    assert read_raw_bit_field(raw, 8, 5, 2) == 3
    with pytest.raises(ValueError):
        update_raw_bit_field(raw, 8, 5, 2, 16)
