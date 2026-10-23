"""Bounded numeric representations; reading a value never casts or quantizes it."""

from __future__ import annotations

import struct
import sys
import math

import apytypes as apy
import numpy as np


MAX_INTEGER_DISPLAY_BITS = 4096
_FLOAT_FIELDS = {16: (5, 10), 32: (8, 23), 64: (11, 52)}


def native_integer_bits(value):
    """Read NumPy integer storage width; Python integers have no storage contract."""
    if not isinstance(value, np.integer) or value.dtype.kind not in "iu":
        return None
    width = value.dtype.itemsize * 8
    raw = int(value) & ((1 << width) - 1)
    return {
        "dtype": str(value.dtype),
        "width": width,
        "signed": value.dtype.kind == "i",
        "hex": f"0x{raw:0{width // 4}X}",
        "binary": f"{raw:0{width}b}",
    }


def _float_bits(raw, dtype, exp_bits, man_bits, bias, *, editable=False):
    width = 1 + exp_bits + man_bits
    sign = raw >> (width - 1)
    exponent = (raw >> man_bits) & ((1 << exp_bits) - 1)
    fraction = raw & ((1 << man_bits) - 1)
    if exponent == (1 << exp_bits) - 1:
        classification = (
            "NaN"
            if fraction
            else ("Negative infinity" if sign else "Positive infinity")
        )
    elif exponent == 0:
        classification = (
            "Subnormal" if fraction else ("Negative zero" if sign else "Positive zero")
        )
    else:
        classification = "Normal"
    return {
        "kind": "float",
        "dtype": dtype,
        "width": width,
        "hex": f"0x{raw:0{(width + 3) // 4}X}",
        "binary": f"{raw:0{width}b}",
        "exp_bits": exp_bits,
        "man_bits": man_bits,
        "bias": bias,
        "classification": classification,
        "editable": editable,
    }


def numeric_representation(value, *, editable_float=False):
    """Return a JSON-only descriptor for the Inspector, or None if unsupported.

    Python int text uses a minus sign, never an invented two's-complement width.
    NumPy float bytes are read directly to retain dtype and NaN payload bits.
    APyFloat descriptors preserve the object's own format and custom bias.
    """
    integer = native_integer_bits(value)
    if integer is not None:
        return integer
    if type(value) is int:
        size = value.bit_length()
        truncated = size > MAX_INTEGER_DISPLAY_BITS
        if truncated:
            head = abs(value) >> (size - 64)
            sign = "-" if value < 0 else ""
            hexadecimal, binary = f"{sign}0x{head:016X}…", f"{sign}0b{head:064b}…"
        else:
            hexadecimal, binary = hex(value), bin(value)
        return {
            "kind": "python_int",
            "dtype": "int",
            "width": None,
            "hex": hexadecimal,
            "binary": binary,
            "truncated": truncated,
            "magnitude_bits": size,
        }
    if isinstance(value, apy.APyFloat):
        return _float_bits(
            value.to_bits(),
            "APyFloat",
            value.exp_bits,
            value.man_bits,
            value.bias,
            editable=editable_float,
        )
    if type(value) is float:
        if (sys.float_info.mant_dig, sys.float_info.max_exp) != (53, 1024):
            return None
        raw = int.from_bytes(struct.pack(">d", value), "big")
        width, dtype = 64, "float64 (Python float)"
    elif type(value) in (np.float16, np.float32, np.float64):
        width, dtype = value.dtype.itemsize * 8, str(value.dtype)
        raw = int.from_bytes(value.tobytes(), sys.byteorder)
    else:
        # longdouble is platform-dependent; complex and APyFloatArray have no
        # scalar floating-point representation contract in this Inspector.
        return None
    exp_bits, man_bits = _FLOAT_FIELDS[width]
    return _float_bits(raw, dtype, exp_bits, man_bits, (1 << (exp_bits - 1)) - 1)


def toggle_float_bit(value, bit_index):
    """Rebuild an APyFloat from changed bits with identical format and bias."""
    if type(bit_index) is not int or not 0 <= bit_index < value.bits:
        raise ValueError(f"Bit index must be in [0, {value.bits - 1}]")
    return apy.APyFloat.from_bits(
        value.to_bits() ^ (1 << bit_index),
        exp_bits=value.exp_bits,
        man_bits=value.man_bits,
        bias=value.bias,
    )


def format_apy_float(value):
    """Keep finite custom-format values from being displayed as binary64 inf/0."""
    if not value.is_finite:
        return str(value)
    approximation = float(value)
    if math.isfinite(approximation):
        roundtrip = apy.APyFloat.from_float(
            approximation,
            exp_bits=value.exp_bits,
            man_bits=value.man_bits,
            bias=value.bias,
        )
        if roundtrip.to_bits() == value.to_bits():
            return str(value)
    fraction = f".{value.man:0{value.man_bits}b}" if value.man_bits else ""
    return (
        f"{'-' if value.sign else ''}0b{0 if value.exp == 0 else 1}{fraction}"
        f" × 2^{max(1, value.exp) - value.bias}"
    )
