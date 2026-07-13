"""NumBV v1 Test Suite.

Covers all spec §16.5 test requirements:
  1.  Format ??construction, attributes, replace, validation
  2.  Factory ??scalar, array, zeros, ones, full, from_bits
  3.  Attributes ??.fmt .raw .bits .val .hex .bin .shape .ndim .size
  4.  Rounding ??all 5 modes, boundary cases
  5.  quantize ??value preserved, rounding/overflow applied
  6.  reinterpret ??narrowing, widening signed/unsigned, width+signed combo
  7.  clip
  8.  with_bits
  9.  Operators + - * ??quantize back to left-operand format, signedness guard
 10.  neg / abs
 11.  In-place operators
 12.  Comparison ??frac alignment, signedness guard
 13.  get_bits / set_bits
 14.  Function API ??add/sub (out_fmt required), mul (full-prec vs out_fmt)
 15.  Reduction ??sum / dot / mac
 16.  Format inference ??infer_add_format / infer_mul_format
 17.  Acceptance Test ??FIR pipeline end-to-end
"""

import subprocess
import sys

import pytest
import numpy as np

import rpkbin.numbv as nbv
from rpkbin.numbv import (
    Format, NumBV,
    scalar, array, zeros, ones, full, from_bits,
    add, sub, mul, neg, dot, mac,
    infer_add_format, infer_mul_format,
)

# Handy tolerance for float comparisons
TOL = 1e-9


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 1. Format
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestFormat:
    def test_basic_attrs(self):
        fmt = Format(16, 12)
        assert fmt.width   == 16
        assert fmt.frac    == 12
        assert fmt.signed  is True
        assert fmt.rounding == "trunc"
        assert fmt.overflow == "saturate"

    def test_unsigned(self):
        fmt = Format(8, 4, signed=False)
        assert fmt.signed is False
        assert fmt.min_raw == 0
        assert fmt.max_raw == 255

    def test_signed_ranges(self):
        fmt = Format(8, 0)
        assert fmt.min_raw == -128
        assert fmt.max_raw ==  127

    def test_int_bits(self):
        # S16.12 ??int_bits = 16-12-1 = 3
        assert Format(16, 12, signed=True).int_bits == 3
        # U16.12 ??int_bits = 16-12-0 = 4
        assert Format(16, 12, signed=False).int_bits == 4

    def test_int_bits_negative(self):
        # frac > width: int_bits can be negative
        fmt = Format(8, 10, signed=True)
        assert fmt.int_bits == 8 - 10 - 1  # -3

    def test_scale(self):
        assert Format(16, 12).scale == 4096

    def test_min_max_val(self):
        fmt = Format(8, 4, signed=True)
        # min_raw = -128, scale = 16
        assert abs(fmt.min_val - (-128 / 16)) < TOL
        assert abs(fmt.max_val - (127  / 16)) < TOL

    def test_precision(self):
        fmt = Format(16, 12)
        assert abs(fmt.precision - 1 / 4096) < TOL

    def test_replace(self):
        fmt  = Format(16, 12)
        fmt2 = fmt.replace(width=32, frac=22)
        assert fmt2.width == 32
        assert fmt2.frac  == 22
        assert fmt2.signed == fmt.signed  # unchanged

    def test_replace_rounding(self):
        fmt  = Format(16, 12)
        fmt2 = fmt.replace(rounding="round_half_even")
        assert fmt2.rounding == "round_half_even"
        assert fmt2.width == 16  # unchanged

    def test_immutable(self):
        fmt = Format(16, 12)
        with pytest.raises(AttributeError):
            fmt.width = 8  # type: ignore

    def test_eq(self):
        assert Format(16, 12) == Format(16, 12)
        assert Format(16, 12) != Format(16, 11)

    def test_hash(self):
        s = {Format(16, 12), Format(16, 12), Format(8, 4)}
        assert len(s) == 2

    def test_repr(self):
        r = repr(Format(16, 12))
        assert "Format" in r and "16" in r and "12" in r

    # --- validation ---

    def test_invalid_width_zero(self):
        with pytest.raises(ValueError):
            Format(0, 0)

    def test_invalid_width_negative(self):
        with pytest.raises(ValueError):
            Format(-1, 0)

    def test_invalid_frac_negative(self):
        with pytest.raises(ValueError):
            Format(8, -1)

    def test_width_over_63(self):
        with pytest.raises(ValueError):
            Format(64, 0)

    def test_frac_gt_width_is_valid(self):
        # spec allows frac > width
        fmt = Format(8, 10)
        assert fmt.frac == 10

    def test_invalid_rounding(self):
        with pytest.raises(ValueError):
            Format(8, 4, rounding="bad_mode")  # type: ignore

    def test_invalid_overflow(self):
        with pytest.raises(ValueError):
            Format(8, 4, overflow="clamp")  # type: ignore

    def test_replace_unknown_field(self):
        with pytest.raises(TypeError):
            Format(16, 12).replace(unknown_field=99)


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 2. Factory functions
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestFactory:
    fmt = Format(16, 12)

    def test_scalar_val(self):
        a = scalar(1.5, fmt=self.fmt)
        assert a.is_scalar
        assert abs(float(a.val) - 1.5) < 1 / 4096

    def test_array_1d(self):
        a = array([0.5, 1.0, 1.5], fmt=self.fmt)
        assert not a.is_scalar
        assert a.size == 3

    def test_array_preserves_multidim_shape(self):
        a = array([[0.5, 1.0], [1.5, 2.0]], fmt=self.fmt)
        assert a.shape == (2, 2)

    def test_zeros_scalar(self):
        a = zeros(fmt=self.fmt)
        assert a.is_scalar and float(a.val) == 0.0

    def test_zeros_array(self):
        a = zeros(fmt=self.fmt, shape=8)
        assert a.size == 8
        assert np.all(a.val == 0.0)

    def test_ones(self):
        a = ones(fmt=self.fmt)
        assert abs(float(a.val) - 1.0) < 1 / 4096

    def test_full(self):
        a = full(0.25, fmt=self.fmt, shape=4)
        assert np.all(np.abs(a.val - 0.25) < 1 / 4096)

    def test_from_bits_signed(self):
        # 0xFFFF in S16.12 ??raw = -1, val = -1/4096
        a = from_bits(0xFFFF, fmt=Format(16, 12, signed=True))
        assert int(a.raw) == -1

    def test_from_bits_unsigned(self):
        a = from_bits(0xFF, fmt=Format(8, 4, signed=False))
        assert int(a.raw) == 0xFF

    def test_from_bits_mask(self):
        # Extra bits should be masked off
        a = from_bits(0x1FF, fmt=Format(8, 0, signed=False))  # only 8 bits
        assert int(a.raw) == 0xFF

    def test_from_bits_array(self):
        a = from_bits([0x0100, 0x0200], fmt=Format(16, 8, signed=False))
        assert a.size == 2
        assert int(a.raw[0]) == 0x0100

    def test_from_bits_preserves_multidim_shape(self):
        a = from_bits([[0x01, 0x02], [0x03, 0x04]], fmt=Format(8, 0, signed=False))
        assert a.shape == (2, 2)

    def test_from_bits_no_quantize(self):
        # from_bits should not apply overflow ??raw bits are taken as-is
        a = from_bits(0xFFFF, fmt=Format(16, 12, signed=True))
        assert int(a.raw) == -1  # sign-extended, not clipped

    def test_from_bits_signed_63_bit_patterns(self):
        fmt = Format(63, 0, signed=True)
        assert int(from_bits(1 << 62, fmt=fmt).raw) == -(1 << 62)
        assert int(from_bits((1 << 63) - 1, fmt=fmt).raw) == -1

    def test_from_bits_strict_array_raises(self):
        with pytest.raises(ValueError):
            from_bits([0x00, 0x1FF], fmt=Format(8, 0, signed=False), strict=True)

    def test_format_preserved(self):
        a = scalar(1.5, fmt=self.fmt)
        assert a.fmt == self.fmt


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 3. Attributes
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestAttributes:
    def test_raw_scalar(self):
        fmt = Format(16, 8, signed=True)
        a = scalar(1.0, fmt=fmt)
        # 1.0 * 2^8 = 256
        assert int(a.raw) == 256

    def test_bits_positive(self):
        a = scalar(1.0, fmt=Format(16, 8, signed=True))
        assert int(a.bits) == 256

    def test_bits_negative(self):
        a = scalar(-1.0, fmt=Format(16, 8, signed=True))
        # raw = -256, bits = 0xFF00
        assert int(a.bits) == 0xFF00

    def test_val_roundtrip(self):
        fmt = Format(16, 12)
        for v in [0.0, 0.5, -0.5, 1.0, -1.0]:
            assert abs(float(scalar(v, fmt=fmt).val) - v) < 1 / 4096

    def test_hex_scalar(self):
        a = scalar(0.0, fmt=Format(16, 8))
        a_bits = from_bits(0x00C0, fmt=Format(16, 8))
        assert a_bits.hex == "0x00C0"

    def test_bin_scalar(self):
        a = from_bits(0b00000001, fmt=Format(8, 4))
        assert a.bin == "0b00000001"

    def test_hex_bin_preserve_array_shape(self):
        fmt = Format(8, 0, signed=False)
        assert array([1, 2], fmt=fmt).hex == ["0x01", "0x02"]
        assert from_bits([[1, 2], [3, 4]], fmt=fmt).bin == [
            ["0b00000001", "0b00000010"],
            ["0b00000011", "0b00000100"],
        ]
        assert from_bits([[[1]]], fmt=fmt).hex == [[["0x01"]]]

    def test_shape_scalar(self):
        assert scalar(1.0, fmt=Format(16, 12)).shape == ()

    def test_shape_array(self):
        assert array([1.0, 2.0], fmt=Format(16, 12)).shape == (2,)

    def test_ndim(self):
        assert scalar(1.0, fmt=Format(16, 12)).ndim == 0
        assert array([1.0], fmt=Format(16, 12)).ndim == 1

    def test_size(self):
        assert scalar(1.0, fmt=Format(16, 12)).size == 1
        assert array([1.0, 2.0, 3.0], fmt=Format(16, 12)).size == 3


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 4. Rounding modes
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestRounding:
    """Test all 5 rounding modes at boundary values."""

    def _q(self, val: float, rounding: str) -> float:
        """Quantize val to Q4.0 (integer) with given rounding mode."""
        fmt = Format(8, 1, rounding=rounding)  # resolution 0.5 ??test halves
        # Use a frac=1 format so 0.5 steps become the rounding boundary
        # val * 2 should give the 0.5-step boundary
        # Actually, let's use a simpler helper: quantize to nearest integer
        # by using Format(8, 0) and testing values like 0.5, 1.5, -0.5, etc.
        fmt_int = Format(8, 0, rounding=rounding)
        return float(scalar(val, fmt=fmt_int).val)

    def test_trunc_positive(self):
        assert self._q(1.9,  "trunc") == 1.0
        assert self._q(0.5,  "trunc") == 0.0
        assert self._q(2.5,  "trunc") == 2.0

    def test_trunc_negative(self):
        assert self._q(-0.5, "trunc") == -1.0  # floor
        assert self._q(-1.9, "trunc") == -2.0

    def test_round_half_up(self):
        assert self._q(0.5,  "round") == 1.0
        assert self._q(1.5,  "round") == 2.0
        assert self._q(-0.5, "round") == 0.0   # half-up rounds 0 (toward +??
        assert self._q(-1.5, "round") == -1.0

    def test_round_half_even_ties(self):
        # 0.5 ??nearest even ??0
        assert self._q(0.5,  "round_half_even") == 0.0
        # 1.5 ??nearest even ??2
        assert self._q(1.5,  "round_half_even") == 2.0
        # 2.5 ??nearest even ??2
        assert self._q(2.5,  "round_half_even") == 2.0
        # -0.5 ??nearest even ??0
        assert self._q(-0.5, "round_half_even") == 0.0
        # -1.5 ??nearest even ??-2
        assert self._q(-1.5, "round_half_even") == -2.0
        # -2.5 ??nearest even ??-2
        assert self._q(-2.5, "round_half_even") == -2.0

    def test_ceil(self):
        assert self._q(0.1,  "ceil") == 1.0
        assert self._q(1.0,  "ceil") == 1.0   # exact, no rounding
        assert self._q(-0.9, "ceil") == 0.0
        assert self._q(-1.0, "ceil") == -1.0

    def test_round_to_zero(self):
        assert self._q(1.9,  "round_to_zero") == 1.0
        assert self._q(-1.9, "round_to_zero") == -1.0  # toward zero = -1 not -2
        assert self._q(0.5,  "round_to_zero") == 0.0
        assert self._q(-0.5, "round_to_zero") == 0.0

    def test_extreme_float_applies_overflow_policy_before_int64_cast(self):
        assert float(scalar(1e300, fmt=Format(8, 0)).val) == 127
        assert float(scalar(-1e300, fmt=Format(8, 0)).val) == -128
        assert int(scalar(1e300, fmt=Format(8, 0, overflow="wrap")).raw) == 0

    @pytest.mark.parametrize("signed", [True, False])
    def test_saturate_63_bit_extreme_float_stays_in_range(self, signed):
        fmt = Format(63, 0, signed=signed)
        assert int(scalar(1e300, fmt=fmt).raw) == fmt.max_raw
        assert int(scalar(-1e300, fmt=fmt).raw) == fmt.min_raw

    @pytest.mark.parametrize("overflow", ["saturate", "wrap"])
    def test_nan_is_rejected(self, overflow):
        with pytest.raises(ValueError, match="NaN"):
            scalar(float("nan"), fmt=Format(8, 0, overflow=overflow))

    @pytest.mark.parametrize("value", [float("inf"), float("-inf")])
    def test_wrap_infinity_is_rejected(self, value):
        with pytest.raises(ValueError, match="infinity"):
            scalar(value, fmt=Format(8, 0, overflow="wrap"))


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 5. quantize
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestQuantize:
    def test_basic_upcasts_preserve_value(self):
        fmt8  = Format(8,  4)
        fmt16 = Format(16, 8)
        a = scalar(1.5, fmt=fmt8)
        b = a.quantize(fmt16)
        assert abs(float(b.val) - 1.5) < 1 / 256

    def test_narrows_with_saturate(self):
        fmt32 = Format(32, 16, overflow="saturate")
        fmt8  = Format(8,  4,  overflow="saturate")
        a = scalar(200.0, fmt=fmt32)
        b = a.quantize(fmt8)
        # Q8.4 max ??7.9375 (signed); clamp
        assert float(b.val) <= fmt8.max_val + 1e-9

    def test_narrows_with_wrap(self):
        fmt32 = Format(32, 0, overflow="saturate")
        fmt8  = Format(8,  0, signed=True, overflow="wrap")
        a = scalar(130, fmt=fmt32)
        b = a.quantize(fmt8)
        # 130 wraps in S8: 130 - 256 = -126
        assert float(b.val) == -126.0

    def test_applies_target_rounding(self):
        fmt_src = Format(16, 12)
        fmt_dst = Format(16, 0, rounding="round")  # integer output
        a = scalar(0.5, fmt=fmt_src)
        b = a.quantize(fmt_dst)
        assert float(b.val) == 1.0  # round 0.5 up

    def test_fmt_unchanged(self):
        fmt = Format(16, 12)
        a = scalar(1.5, fmt=fmt)
        b = a.quantize(Format(8, 4))
        assert a.fmt == fmt  # a is not mutated

    def test_large_integer_quantize_preserves_bits(self):
        fmt = Format(63, 0, signed=False)
        a = from_bits((1 << 60) + 1, fmt=fmt)
        b = a.quantize(fmt)
        assert int(b.bits) == (1 << 60) + 1


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 6. reinterpret
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestReinterpret:
    def test_change_signed_only(self):
        # 0xFF in S8 = raw -1; reinterpret as unsigned = 255
        a = from_bits(0xFF, fmt=Format(8, 0, signed=True))
        b = a.reinterpret(signed=False)
        assert int(b.raw) == 255

    def test_change_frac_only(self):
        a = from_bits(0x0100, fmt=Format(16, 8))
        # bits don't change
        b = a.reinterpret(frac=4)
        assert int(a.bits) == int(b.bits)
        assert b.fmt.frac == 4

    def test_narrowing_drops_high_bits(self):
        # 0xABCD ??low 8 bits = 0xCD
        a = from_bits(0xABCD, fmt=Format(16, 8, signed=False))
        b = a.reinterpret(width=8)
        assert int(b.bits) == 0xCD
        assert b.fmt.width == 8

    def test_widening_signed_sign_extend_negative(self):
        # S8 raw = -128 = 0x80; widening to 16 signed: 0xFF80
        a = from_bits(0x80, fmt=Format(8, 0, signed=True))   # raw = -128
        b = a.reinterpret(width=16)
        assert int(b.bits) == 0xFF80
        assert int(b.raw) == -128  # sign preserved

    def test_widening_signed_no_extend_positive(self):
        # S8 raw = 0x40 (64); widening to 16: 0x0040
        a = from_bits(0x40, fmt=Format(8, 0, signed=True))
        b = a.reinterpret(width=16)
        assert int(b.bits) == 0x0040

    def test_widening_unsigned_zero_extend(self):
        # U8 = 0xFF; widening to 16: 0x00FF (zero-extend)
        a = from_bits(0xFF, fmt=Format(8, 0, signed=False))
        b = a.reinterpret(width=16)
        assert int(b.bits) == 0x00FF

    def test_width_and_signed_combo(self):
        # S8 raw=-128=0x80 ??widen to 32 (sign-extend) ??bits=0xFFFFFF80,
        # then reinterpret as unsigned
        a = from_bits(0x80, fmt=Format(8, 0, signed=True))
        b = a.reinterpret(width=16, signed=False)
        # Step 1: sign-extend 0x80 to 16 bits ??0xFF80
        # Step 2: signed=False ??raw = 0xFF80 = 65408
        assert int(b.bits) == 0xFF80
        assert b.fmt.signed is False


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 7. clip
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestClip:
    fmt = Format(16, 8)

    def test_clip_above(self):
        a = scalar(10.0, fmt=self.fmt)
        b = a.clip(0.0, 5.0)
        assert abs(float(b.val) - 5.0) < 1 / 256

    def test_clip_below(self):
        a = scalar(-5.0, fmt=self.fmt)
        b = a.clip(-1.0, 1.0)
        assert abs(float(b.val) - (-1.0)) < 1 / 256

    def test_clip_in_range_unchanged(self):
        a = scalar(1.5, fmt=self.fmt)
        b = a.clip(0.0, 2.0)
        assert abs(float(b.val) - 1.5) < 1 / 256

    def test_fmt_preserved(self):
        a = scalar(5.0, fmt=self.fmt)
        b = a.clip(0.0, 2.0)
        assert b.fmt == self.fmt

    def test_clip_array(self):
        a = array([0.0, 5.0, -3.0, 2.0], fmt=self.fmt)
        b = a.clip(-1.0, 1.0)
        assert np.all(b.val <= 1.0 + 1 / 256)
        assert np.all(b.val >= -1.0 - 1 / 256)

    def test_clip_invalid_range_raises(self):
        a = scalar(1.0, fmt=self.fmt)
        with pytest.raises(ValueError):
            a.clip(2.0, 1.0)

    def test_clip_bounds_clamp_without_wrap(self):
        fmt = Format(8, 0, overflow="wrap")
        assert float(scalar(5, fmt=fmt).clip(-1000, 1000).val) == 5
        assert float(scalar(5, fmt=fmt).clip(1000, 2000).val) == 127
        assert float(scalar(5, fmt=fmt).clip(-2000, -1000).val) == -128

    def test_clip_fractional_bounds_and_array(self):
        fmt = Format(8, 1, rounding="round")
        result = array([-3, 0, 3], fmt=fmt).clip(-0.26, 0.26)
        assert np.allclose(result.val, [-0.5, 0, 0.5])

    def test_clip_extreme_bounds_clamp_before_int64_cast(self):
        fmt = Format(8, 0, overflow="wrap")
        assert float(scalar(5, fmt=fmt).clip(-1e300, 1e300).val) == 5


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 8. with_bits
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestWithBits:
    def test_and_mask(self):
        fmt = Format(16, 8, signed=False)
        a = from_bits(0xABCD, fmt=fmt)
        b = a.with_bits(a.bits & 0xFF00)
        assert int(b.bits) == 0xAB00

    def test_or_combine(self):
        fmt = Format(16, 8, signed=False)
        a = from_bits(0xAB00, fmt=fmt)
        b = from_bits(0x00CD, fmt=fmt)
        c = a.with_bits(a.bits | b.bits)
        assert int(c.bits) == 0xABCD

    def test_fmt_preserved(self):
        fmt = Format(16, 8)
        a = from_bits(0x1234, fmt=fmt)
        b = a.with_bits(0x5678)
        assert b.fmt == fmt

    def test_original_unchanged(self):
        fmt = Format(16, 8, signed=False)
        a = from_bits(0xABCD, fmt=fmt)
        _ = a.with_bits(0x0000)
        assert int(a.bits) == 0xABCD  # a not mutated


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 9. Operators + - * (quantize to left-operand format)
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestOperators:
    fmt = Format(16, 12)

    def test_add_same_fmt(self):
        a = scalar(0.5, fmt=self.fmt)
        b = scalar(0.5, fmt=self.fmt)
        c = a + b
        assert abs(float(c.val) - 1.0) < 1 / 4096
        assert c.fmt == self.fmt

    def test_sub_same_fmt(self):
        a = scalar(1.5, fmt=self.fmt)
        b = scalar(0.5, fmt=self.fmt)
        c = a - b
        assert abs(float(c.val) - 1.0) < 1 / 4096

    def test_mul_quantizes_back(self):
        a = scalar(0.5, fmt=self.fmt)
        b = scalar(0.5, fmt=self.fmt)
        c = a * b
        # 0.5 * 0.5 = 0.25 ??fits in Q16.12, should be preserved
        assert abs(float(c.val) - 0.25) < 1 / 4096
        assert c.fmt == self.fmt

    def test_add_float_operand(self):
        a = scalar(1.0, fmt=self.fmt)
        b = a + 0.5
        assert abs(float(b.val) - 1.5) < 1 / 4096

    def test_radd(self):
        a = scalar(1.0, fmt=self.fmt)
        b = 0.5 + a
        assert abs(float(b.val) - 1.5) < 1 / 4096

    def test_rsub(self):
        a = scalar(1.0, fmt=self.fmt)
        b = 2.0 - a
        assert abs(float(b.val) - 1.0) < 1 / 4096

    def test_rmul(self):
        a = scalar(2.0, fmt=self.fmt)
        b = 1.5 * a
        assert abs(float(b.val) - 3.0) < 1 / 4096

    def test_add_different_frac_quantizes_to_left(self):
        # a: Q16.12, b: Q8.4
        # a + b quantizes to a.fmt (Q16.12)
        fmt_a = Format(16, 12)
        fmt_b = Format(8,  4)
        a = scalar(1.0, fmt=fmt_a)
        b = scalar(0.5, fmt=fmt_b)
        c = a + b
        assert c.fmt == fmt_a   # left-operand format

    def test_noncommutativity_different_fmt(self):
        fmt_a = Format(16, 12)
        fmt_b = Format(8,  4)
        a = scalar(1.0, fmt=fmt_a)
        b = scalar(0.5, fmt=fmt_b)
        c1 = a + b  # ??fmt_a
        c2 = b + a  # ??fmt_b
        assert c1.fmt == fmt_a
        assert c2.fmt == fmt_b

    def test_signedness_mismatch_raises(self):
        a = scalar(1.0, fmt=Format(16, 12, signed=True))
        b = scalar(1.0, fmt=Format(16, 12, signed=False))
        with pytest.raises(TypeError):
            _ = a + b
        with pytest.raises(TypeError):
            _ = a * b

    def test_overflow_saturate(self):
        fmt = Format(8, 0, overflow="saturate", signed=True)
        a = scalar(120.0, fmt=fmt)
        b = a + 10.0   # 130 > 127 ??saturate
        assert float(b.val) == 127.0

    def test_overflow_wrap(self):
        fmt = Format(8, 0, overflow="wrap", signed=True)
        a = scalar(120.0, fmt=fmt)
        b = a + 10.0   # 130 wraps to 130 - 256 = -126
        assert float(b.val) == -126.0

    def test_mul_width_overflow_raises(self):
        fmt = Format(32, 16)
        a = scalar(1.0, fmt=fmt)
        with pytest.raises(OverflowError):
            _ = a * a  # 32+32 = 64 > 63


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 10. neg / abs
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestNegAbs:
    fmt = Format(16, 12)

    def test_neg_positive(self):
        a = scalar(1.5, fmt=self.fmt)
        b = -a
        assert abs(float(b.val) + 1.5) < 1 / 4096

    def test_neg_negative(self):
        a = scalar(-1.5, fmt=self.fmt)
        b = -a
        assert abs(float(b.val) - 1.5) < 1 / 4096

    def test_neg_fmt_preserved(self):
        a = scalar(1.0, fmt=self.fmt)
        assert (-a).fmt == self.fmt

    def test_abs_negative(self):
        a = scalar(-1.5, fmt=self.fmt)
        b = abs(a)
        assert abs(float(b.val) - 1.5) < 1 / 4096

    def test_abs_signed_min_saturates(self):
        """abs(MIN_S8) saturates to MAX_S8 (spec §9.5)."""
        fmt = Format(8, 0, signed=True, overflow="saturate")
        a = scalar(-128.0, fmt=fmt)
        b = abs(a)
        assert float(b.val) == 127.0


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 11. In-place operators
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestInPlace:
    fmt = Format(16, 12)

    def test_iadd(self):
        a = scalar(1.0, fmt=self.fmt)
        orig_id = id(a._raw)
        a += 0.5
        assert abs(float(a.val) - 1.5) < 1 / 4096

    def test_isub(self):
        a = scalar(2.0, fmt=self.fmt)
        a -= 0.5
        assert abs(float(a.val) - 1.5) < 1 / 4096

    def test_imul(self):
        a = scalar(2.0, fmt=self.fmt)
        a *= 0.5
        assert abs(float(a.val) - 1.0) < 1 / 4096

    def test_imul_format_preserved(self):
        a = scalar(1.0, fmt=self.fmt)
        a *= 2.0
        assert a.fmt == self.fmt

    def test_iadd_saturates(self):
        fmt = Format(8, 0, signed=True, overflow="saturate")
        a = scalar(120.0, fmt=fmt)
        a += 10.0
        assert float(a.val) == 127.0


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 12. Comparison operators
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestComparison:
    fmt = Format(16, 12)

    def test_eq_same_fmt(self):
        a = scalar(1.5, fmt=self.fmt)
        b = scalar(1.5, fmt=self.fmt)
        assert bool(a == b)

    def test_ne(self):
        a = scalar(1.5, fmt=self.fmt)
        b = scalar(2.0, fmt=self.fmt)
        assert bool(a != b)

    def test_lt(self):
        a = scalar(1.0, fmt=self.fmt)
        b = scalar(2.0, fmt=self.fmt)
        assert bool(a < b)

    def test_le_equal(self):
        a = scalar(1.0, fmt=self.fmt)
        assert bool(a <= a)

    def test_gt(self):
        a = scalar(2.0, fmt=self.fmt)
        b = scalar(1.0, fmt=self.fmt)
        assert bool(a > b)

    def test_ge(self):
        a = scalar(1.5, fmt=self.fmt)
        assert bool(a >= a)

    def test_eq_float(self):
        a = scalar(1.5, fmt=self.fmt)
        assert bool(a == 1.5)

    def test_compare_different_frac_aligned(self):
        fmt_a = Format(16, 12)
        fmt_b = Format(8,  4)
        a = scalar(1.0, fmt=fmt_a)
        b = scalar(1.0, fmt=fmt_b)
        assert bool(a == b)  # frac-aligned comparison

    def test_signedness_mismatch_raises(self):
        a = scalar(1.0, fmt=Format(16, 12, signed=True))
        b = scalar(1.0, fmt=Format(16, 12, signed=False))
        with pytest.raises(TypeError):
            _ = a == b

    def test_array_comparison_returns_ndarray(self):
        a = array([1.0, 2.0, 3.0], fmt=self.fmt)
        b = array([1.0, 1.0, 4.0], fmt=self.fmt)
        result = a == b
        assert isinstance(result, np.ndarray)
        assert list(result) == [True, False, False]


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 13. get_bits / set_bits
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestBitOps:
    def test_get_bits_scalar(self):
        a = from_bits(0xABCD, fmt=Format(16, 8, signed=False))
        assert a.get_bits(15, 8) == 0xAB
        assert a.get_bits(7, 0)  == 0xCD

    def test_get_bits_with_fmt(self):
        a = from_bits(0x00C0, fmt=Format(16, 8, signed=False))
        # Extract bits [7:0] and interpret as Q8.4
        b = a.get_bits(7, 0, fmt=Format(8, 4, signed=False))
        assert isinstance(b, NumBV)
        assert int(b.bits) == 0xC0

    def test_get_bits_array(self):
        a = from_bits([0xAB12, 0xCD34], fmt=Format(16, 8, signed=False))
        high = a.get_bits(15, 8)
        assert list(high) == [0xAB, 0xCD]

    def test_set_bits_inplace(self):
        a = from_bits(0x0000, fmt=Format(16, 8, signed=False))
        a.set_bits(7, 0, 0xCD)
        assert int(a.bits) == 0x00CD

    def test_set_bits_masks_value(self):
        a = from_bits(0x0000, fmt=Format(16, 8, signed=False))
        # Only 4-bit field; 0x1F masked to 0x0F
        a.set_bits(3, 0, 0x1F)
        assert int(a.bits) & 0x0F == 0x0F

    def test_get_bits_high_lt_low_raises(self):
        a = from_bits(0, fmt=Format(16, 8))
        with pytest.raises(ValueError):
            a.get_bits(3, 5)

    def test_get_bits_out_of_range_raises(self):
        a = from_bits(0, fmt=Format(16, 8))
        with pytest.raises(ValueError):
            a.get_bits(16, 0)  # bit 16 >= width 16

    def test_get_bits_negative_low_raises(self):
        a = from_bits(0, fmt=Format(16, 8))
        with pytest.raises(ValueError):
            a.get_bits(3, -1)

    def test_set_bits_negative_low_raises(self):
        a = from_bits(0, fmt=Format(16, 8))
        with pytest.raises(ValueError):
            a.set_bits(3, -1, 1)


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 14. Function-level API
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestFunctionAPI:
    fmt_a  = Format(16, 12)
    fmt_b  = Format(12, 10)
    out_fmt = Format(16, 12)

    def test_add_explicit_fmt(self):
        a = scalar(0.5, fmt=self.fmt_a)
        b = scalar(0.25, fmt=self.fmt_b)
        c = add(a, b, out_fmt=self.out_fmt)
        assert abs(float(c.val) - 0.75) < 1 / 4096
        assert c.fmt == self.out_fmt

    def test_sub_explicit_fmt(self):
        a = scalar(1.0, fmt=self.fmt_a)
        b = scalar(0.25, fmt=self.fmt_b)
        c = sub(a, b, out_fmt=self.out_fmt)
        assert abs(float(c.val) - 0.75) < 1 / 4096

    def test_mul_full_precision(self):
        a = scalar(0.5, fmt=Format(16, 12))
        b = scalar(0.5, fmt=Format(16, 12))
        p = mul(a, b)
        # Full-precision: width=32, frac=24
        assert p.fmt.width == 32
        assert p.fmt.frac  == 24
        assert abs(float(p.val) - 0.25) < 1e-6

    def test_mul_with_out_fmt(self):
        a = scalar(0.5, fmt=Format(16, 12))
        b = scalar(0.5, fmt=Format(16, 12))
        out = Format(16, 12, rounding="round")
        p = mul(a, b, out_fmt=out)
        assert p.fmt == out
        assert abs(float(p.val) - 0.25) < 1 / 4096

    def test_neg_no_fmt(self):
        a = scalar(1.5, fmt=self.fmt_a)
        b = neg(a)
        assert abs(float(b.val) + 1.5) < 1 / 4096
        assert b.fmt == self.fmt_a

    def test_neg_with_out_fmt(self):
        a = scalar(1.5, fmt=self.fmt_a)
        out = Format(8, 4)
        b = neg(a, out_fmt=out)
        assert b.fmt == out

    def test_mul_overflow_raises(self):
        a = scalar(1.0, fmt=Format(32, 16))
        b = scalar(1.0, fmt=Format(32, 16))
        with pytest.raises(OverflowError):
            mul(a, b)  # 32+32 = 64 > 63

    def test_add_large_integer_no_float_precision_loss(self):
        fa = Format(63, 0, signed=False)
        fb = Format(1, 0, signed=False)
        a = from_bits((1 << 60) + 1, fmt=fa)
        b = from_bits(1, fmt=fb)
        out = add(a, b, out_fmt=fa)
        assert int(out.bits) == (1 << 60) + 2


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 15. Reduction ??sum / dot / mac
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestReduction:
    acc_fmt = Format(32, 12, rounding="trunc", overflow="saturate")
    elem_fmt = Format(16, 12)

    def test_sum_array(self):
        a = array([0.25, 0.25, 0.25, 0.25], fmt=self.elem_fmt)
        s = nbv.sum(a, acc_fmt=self.acc_fmt)
        assert abs(float(s.val) - 1.0) < 1 / 4096
        assert s.fmt == self.acc_fmt

    def test_sum_scalar(self):
        a = scalar(1.5, fmt=self.elem_fmt)
        s = nbv.sum(a, acc_fmt=self.acc_fmt)
        assert abs(float(s.val) - 1.5) < 1 / 4096

    def test_sum_multidim_flattens_c_order(self):
        a = array([[0.25, 0.25], [0.25, 0.25]], fmt=self.elem_fmt)
        s = nbv.sum(a, acc_fmt=self.acc_fmt)
        assert abs(float(s.val) - 1.0) < 1 / 4096

    def test_dot_basic(self):
        a = array([1.0, 1.0, 1.0, 1.0], fmt=Format(16, 12))
        h = array([0.25, 0.25, 0.25, 0.25], fmt=Format(12, 10))
        out_fmt = Format(16, 12)
        y = dot(a, h, acc_fmt=self.acc_fmt, out_fmt=out_fmt)
        assert abs(float(y.val) - 1.0) < 1 / 4096
        assert y.fmt == out_fmt

    def test_dot_multidim_flattens_c_order(self):
        a = array([[1.0, 2.0], [3.0, 4.0]], fmt=Format(16, 12))
        b = array([[0.25, 0.25], [0.25, 0.25]], fmt=Format(12, 10))
        y = dot(a, b, acc_fmt=self.acc_fmt)
        assert abs(float(y.val) - 2.5) < 1 / 4096

    def test_dot_length_mismatch(self):
        a = array([1.0, 2.0], fmt=Format(16, 12))
        b = array([1.0],      fmt=Format(16, 12))
        with pytest.raises(ValueError):
            dot(a, b, acc_fmt=self.acc_fmt)

    def test_mac_basic(self):
        acc_fmt = Format(32, 12)
        acc  = zeros(fmt=acc_fmt)
        a = scalar(0.5, fmt=Format(16, 12))
        b = scalar(0.5, fmt=Format(16, 12))
        acc = mac(acc, a, b, acc_fmt=acc_fmt)
        # 0.5 * 0.5 = 0.25 added to acc=0 ??0.25
        assert abs(float(acc.val) - 0.25) < 1 / 4096

    def test_mac_accumulates(self):
        acc_fmt = Format(32, 12)
        acc = zeros(fmt=acc_fmt)
        a = scalar(0.5, fmt=Format(16, 12))
        b = scalar(0.5, fmt=Format(16, 12))
        for _ in range(4):
            acc = mac(acc, a, b, acc_fmt=acc_fmt)
        assert abs(float(acc.val) - 1.0) < 1 / 4096


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 16. Format inference
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestFormatInference:
    def test_infer_add(self):
        fa = Format(16, 12)
        fb = Format(12, 10)
        out = infer_add_format(fa, fb)
        assert out.frac == 12              # max(12, 10)
        assert out.int_bits == max(fa.int_bits, fb.int_bits) + 1

    def test_infer_add_width_clears_overflow(self):
        fa = Format(16, 12)
        fb = Format(16, 12)
        out = infer_add_format(fa, fb)
        # int_bits = 3+1=4, frac=12, signed ??width = 4+12+1 = 17
        assert out.width == 17

    def test_infer_add_signedness_mismatch(self):
        fa = Format(16, 12, signed=True)
        fb = Format(16, 12, signed=False)
        with pytest.raises(TypeError):
            infer_add_format(fa, fb)

    def test_infer_mul(self):
        fa = Format(16, 12)
        fb = Format(12, 10)
        out = infer_mul_format(fa, fb)
        assert out.width == 28
        assert out.frac  == 22
        assert out.signed is True

    def test_infer_mul_both_unsigned(self):
        fa = Format(8, 4, signed=False)
        fb = Format(8, 4, signed=False)
        out = infer_mul_format(fa, fb)
        assert out.signed is False

    def test_infer_mul_one_signed(self):
        fa = Format(8, 4, signed=True)
        fb = Format(8, 4, signed=False)
        out = infer_mul_format(fa, fb)
        assert out.signed is True


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 17. Acceptance Test ??FIR pipeline (spec §22)
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestFIRAcceptance:
    """End-to-end FIR filter simulation using NumBV v1 API.

    Validates:
    - Format and raw/value correspondence
    - full-precision nbv.mul()
    - explicit quantize()
    - sequential nbv.sum()
    - accumulator format
    - array-backed simulation
    - convergent rounding alignment with reference
    """

    def _reference_fir(self, samples, coeffs):
        """Pure Python floating-point FIR reference."""
        n = len(coeffs)
        out = []
        for i in range(len(samples)):
            acc = 0.0
            for j in range(n):
                if i - j >= 0:
                    acc += samples[i - j] * coeffs[j]
            out.append(acc)
        return out

    def test_fir_4tap(self):
        """4-tap FIR with known inputs ??check bit-true result is close to float ref."""
        import math

        x_vals = [1.0, 0.5, -0.5, -1.0, 0.0, 0.5]
        h_vals = [0.25, 0.25, 0.25, 0.25]  # moving average

        x_fmt   = Format(16, 12)
        h_fmt   = Format(12, 10, rounding="round_half_even")
        acc_fmt = Format(32, 22, rounding="round_half_even")
        out_fmt = Format(16, 12, rounding="round_half_even", overflow="saturate")

        x = array(x_vals, fmt=x_fmt)
        h = array(h_vals, fmt=h_fmt)

        # Compute FIR output for last sample (full history available at index 3)
        # y[3] = x[3]*h[0] + x[2]*h[1] + x[1]*h[2] + x[0]*h[3]
        x_window = array([x_vals[3], x_vals[2], x_vals[1], x_vals[0]], fmt=x_fmt)

        y = dot(x_window, h, acc_fmt=acc_fmt, out_fmt=out_fmt)
        ref = x_vals[3]*h_vals[0] + x_vals[2]*h_vals[1] + x_vals[1]*h_vals[2] + x_vals[0]*h_vals[3]

        # Allow 1 LSB tolerance (Q16.12 step = 1/4096)
        assert abs(float(y.val) - ref) < 2 / 4096, (
            f"FIR result {float(y.val)} differs from ref {ref} by {abs(float(y.val) - ref)}"
        )

    def test_fir_convergent_rounding_no_dc_bias(self):
        """Convergent rounding should not accumulate DC bias over many operations."""
        fmt_int = Format(16, 0, rounding="round_half_even")
        # Sum of 100 values that each round to 0 with half-up but alternate with even
        # 0.5, 0.5, 0.5, ... with convergent ??0, 0, 0, ...  (all tie to 0, even)
        # Use 1.5, 1.5, 1.5 ??converges to 2, 2, 2 (ties to even=2)
        a = array([1.5] * 100, fmt=fmt_int)
        acc_fmt = Format(32, 0, rounding="round_half_even")
        s = nbv.sum(a, acc_fmt=acc_fmt)
        # Each 1.5 rounds to 2, so sum = 200
        assert float(s.val) == 200.0


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# 18. Protocol Safety (__hash__, __iter__, __array_ufunc__)
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???

class TestProtocol:
    """v1 patch: hash=None, iter, array_ufunc=None."""

    fmt = Format(16, 12)

    # ---- __hash__ ----------------------------------------------------------

    def test_unhashable(self):
        """NumBV must not be hashable (mutable _raw + custom __eq__)."""
        a = scalar(1.0, fmt=self.fmt)
        with pytest.raises(TypeError):
            hash(a)

    def test_unhashable_cannot_be_set_key(self):
        a = scalar(1.0, fmt=self.fmt)
        with pytest.raises(TypeError):
            {a}  # attempt to add to a set

    # ---- __iter__ ----------------------------------------------------------

    def test_iter_yields_scalars(self):
        """Iterating array NumBV yields scalar NumBVs."""
        a = array([0.25, 0.5, 0.75], fmt=self.fmt)
        result = list(a)
        assert len(result) == 3
        assert all(isinstance(x, NumBV) for x in result)
        assert all(x.is_scalar for x in result)

    def test_iter_values_correct(self):
        vals = [0.25, 0.5, 0.75]
        a = array(vals, fmt=self.fmt)
        for elem, expected in zip(a, vals):
            assert abs(float(elem.val) - expected) < 1 / 4096

    def test_iter_fmt_preserved(self):
        a = array([1.0, 2.0], fmt=self.fmt)
        for elem in a:
            assert elem.fmt == self.fmt

    def test_iter_scalar_raises(self):
        """Iterating a scalar NumBV should raise TypeError."""
        a = scalar(1.0, fmt=self.fmt)
        with pytest.raises(TypeError, match="scalar"):
            list(a)

    def test_iter_in_for_loop(self):
        """for x in arr should work like a normal Python loop."""
        a = array([1.0, 2.0, 3.0], fmt=self.fmt)
        acc = 0.0
        for elem in a:
            acc += float(elem.val)
        assert abs(acc - 6.0) < 1 / 4096

    def test_iter_used_in_mac_loop(self):
        """Realistic use: iterate coefficients in a MAC loop."""
        h_fmt   = Format(12, 10)
        acc_fmt = Format(32, 22)
        x_i = scalar(0.5, fmt=self.fmt)
        h   = array([0.25, 0.25, 0.25, 0.25], fmt=h_fmt)
        acc = zeros(fmt=acc_fmt)
        for coeff in h:
            acc = nbv.mac(acc, x_i, coeff, acc_fmt=acc_fmt)
        # 4 * (0.5 * 0.25) = 0.5
        assert abs(float(acc.val) - 0.5) < 1 / (1 << 22)

    # ---- __array_ufunc__ ---------------------------------------------------

    def test_array_ufunc_blocked(self):
        """np.add(a, b) on NumBV should raise TypeError, not silently cast to float."""
        a = scalar(1.0, fmt=self.fmt)
        b = scalar(0.5, fmt=self.fmt)
        with pytest.raises(TypeError):
            np.add(a, b)

    def test_array_multiply_blocked(self):
        a = scalar(1.0, fmt=self.fmt)
        with pytest.raises(TypeError):
            np.multiply(a, 2.0)

    def test_array_ufunc_none_is_set(self):
        """Verify the class attribute is explicitly None (not inherited default)."""
        from rpkbin.numbv import NumBV as _NumBV
        assert _NumBV.__array_ufunc__ is None

    def test_scalar_asarray_works(self):
        a = scalar(1.0, fmt=self.fmt)
        out = np.asarray(a)
        assert isinstance(out, np.ndarray)
        assert out.shape == ()
        assert float(out) == 1.0

    def test_direct_init_blocked(self):
        with pytest.raises(TypeError):
            NumBV(np.array(1, dtype=np.int64), self.fmt)


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# TestBackend ??backend switching (numpy / JAX)
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???
class TestBackend:
    """Backend choices are isolated because the production backend is process-global."""

    @staticmethod
    def run(backend: str) -> None:
        script = f'''import rpkbin.numbv as nbv
nbv.set_backend({backend!r})
assert nbv.get_backend() == {backend!r}
a = nbv.scalar(1.5, fmt=nbv.Format(16, 12))
assert abs(float(a.val) - 1.5) < 1 / 4096
'''
        subprocess.run([sys.executable, "-c", script], check=True)

    def test_numpy_backend(self):
        self.run("numpy")

    def test_jax_backend(self):
        pytest.importorskip("jax")
        script = '''import jax
import numpy as np
import rpkbin.numbv as nbv
nbv.set_backend("jax")
fmt = nbv.Format(8, 0, overflow="wrap")
assert nbv.scalar(127, fmt=fmt) != 1000
assert np.array_equal(nbv.array([1, 2], fmt=fmt).hex, ["0x01", "0x02"])
quantize = jax.jit(lambda x: nbv.scalar(x, fmt=fmt))
assert int(quantize(5.0).raw) == 5
try:
    nbv.scalar([1], fmt=fmt)
except ValueError:
    pass
else:
    raise AssertionError("scalar accepted an array")
try:
    nbv.array(1, fmt=fmt)
except ValueError:
    pass
else:
    raise AssertionError("array accepted a scalar")
'''
        subprocess.run([sys.executable, "-c", script], check=True)

    def test_same_backend_is_safe_after_creation(self):
        nbv.set_backend(nbv.get_backend())
        scalar(1.0, fmt=Format(16, 12))
        nbv.set_backend(nbv.get_backend())

    def test_switch_after_creation_raises(self):
        scalar(1.0, fmt=Format(16, 12))
        other = "jax" if nbv.get_backend() == "numpy" else "numpy"
        with pytest.raises(RuntimeError, match="before creating"):
            nbv.set_backend(other)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            nbv.set_backend("numba")


# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???# TestSerialization ??to_dict / from_dict / to_json / from_json
# ?��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��??��???
class TestSerialization:
    fmt = Format(16, 12)

    def test_scalar_roundtrip_dict(self):
        a = scalar(1.5, fmt=self.fmt)
        d = a.to_dict()
        b = NumBV.from_dict(d)
        assert abs(float(b.val) - 1.5) < 1 / 4096
        assert b.fmt == self.fmt

    def test_array_roundtrip_dict(self):
        a = array([0.25, 0.5, 0.75], fmt=self.fmt)
        d = a.to_dict()
        b = NumBV.from_dict(d)
        assert np.allclose(b.val, a.val, atol=1 / 4096)
        assert b.fmt == self.fmt

    def test_dict_structure(self):
        a = scalar(1.0, fmt=self.fmt)
        d = a.to_dict()
        assert "raw" in d and "fmt" in d
        assert d["fmt"]["width"] == 16
        assert d["fmt"]["frac"] == 12
        assert d["fmt"]["signed"] is True

    def test_json_roundtrip_scalar(self):
        a = scalar(-1.25, fmt=self.fmt)
        s = a.to_json()
        b = NumBV.from_json(s)
        assert abs(float(b.val) - (-1.25)) < 1 / 4096
        assert b.fmt == self.fmt

    def test_json_roundtrip_array(self):
        a = array([1.0, -0.5, 0.25], fmt=self.fmt)
        b = NumBV.from_json(a.to_json())
        assert np.allclose(b.val, a.val, atol=1 / 4096)

    def test_roundtrip_preserves_rounding_mode(self):
        fmt = Format(16, 12, rounding="round_half_even", overflow="wrap")
        a = scalar(0.5, fmt=fmt)
        b = NumBV.from_dict(a.to_dict())
        assert b.fmt.rounding == "round_half_even"
        assert b.fmt.overflow == "wrap"

    def test_bit_exact_roundtrip(self):
        """Raw bits survive serialisation unchanged."""
        a = from_bits(0xABCD, fmt=Format(16, 8, signed=False))
        b = NumBV.from_dict(a.to_dict())
        assert int(a.bits) == int(b.bits)


class TestComparisonLiterals:
    def test_ints_are_not_quantized_or_wrapped(self):
        value = scalar(127, fmt=Format(8, 0, overflow="wrap"))
        assert value != 1000
        assert value < 1000
        assert value > -1000

    def test_negative_and_fractional_literals_compare_as_values(self):
        value = scalar(-1.5, fmt=Format(8, 1, overflow="wrap"))
        assert value == -1.5
        assert value != -1
        assert value < -1.25

    def test_float_literal_comparison_preserves_large_raw_precision(self):
        value = from_bits((1 << 53) + 1, fmt=Format(63, 0, signed=False))
        assert value != float(1 << 53)
        assert value > float(1 << 53)

    def test_array_comparison_and_unsupported_operands(self):
        value = array([0, 1, 2], fmt=Format(8, 0, overflow="wrap"))
        assert np.array_equal(value == 1000, [False, False, False])
        assert value != object()
        with pytest.raises(TypeError):
            value < object()


class TestFactoryDimensions:
    fmt = Format(8, 0)

    @pytest.mark.parametrize("value", [[1, 2], np.array([1, 2])])
    def test_scalar_rejects_arrays(self, value):
        with pytest.raises(ValueError, match=r"array\(\)"):
            scalar(value, fmt=self.fmt)

    @pytest.mark.parametrize("value", [1, np.array(1)])
    def test_array_rejects_scalars(self, value):
        with pytest.raises(ValueError, match=r"scalar\(\)"):
            array(value, fmt=self.fmt)
