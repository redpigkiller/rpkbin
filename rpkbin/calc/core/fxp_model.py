"""Core Fixed-Point (FXP) model wrapping APyTypes with unsigned support, Pin-mode casting, and bit manipulation."""

from __future__ import annotations

import ast
import math
import copy
import operator
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any

import apytypes as apy
import numpy as np

from .config import CalcConfig
from .format_engine import FormatEngine, FxpSpec


QUANT_MAP: dict[str, apy.QuantizationMode] = {
    "RND": apy.QuantizationMode.TIES_POS,
    "RND_EVEN": apy.QuantizationMode.TIES_EVEN,
    "RND_ZERO": apy.QuantizationMode.TIES_ZERO,
    "RND_AWAY": apy.QuantizationMode.TIES_AWAY,
    "TRN": apy.QuantizationMode.TO_NEG,
    "TRN_ZERO": apy.QuantizationMode.TO_ZERO,
    "TRN_MAG": apy.QuantizationMode.TRN_MAG,
}

OVERFLOW_MAP: dict[str, apy.OverflowMode] = {
    "SAT": apy.OverflowMode.SAT,
    "WRAP": apy.OverflowMode.WRAP,
}


class RpkFixed(apy.APyFixed):
    """GUI metadata on APyFixed; all arithmetic is inherited unchanged from APyTypes."""

    is_unsigned: bool = False
    hw_wl: int = 0
    target_real: Fraction | None = None
    is_saturated: bool = False

    def copy(self):
        out = RpkFixed(self.to_bits(), bits=self.bits, int_bits=self.int_bits)
        out.__dict__.update(copy.deepcopy(self.__dict__))
        return out


class RpkFixedArray(apy.APyFixedArray):
    """GUI metadata on APyFixedArray; arithmetic and result types are native APyTypes."""

    is_unsigned: bool = False
    hw_wl: int = 0
    target_values: np.ndarray | None = None
    saturated_mask: np.ndarray | None = None
    quantized_bits: np.ndarray | None = None
    # GUI edits on arrays without constructor provenance use sparse records.
    cell_sources: dict[tuple[int, ...], tuple[int, Fraction | None, bool]] | None = None

    def copy(self):
        out = RpkFixedArray(self.to_bits(), bits=self.bits, int_bits=self.int_bits)
        out.__dict__.update(copy.deepcopy(self.__dict__))
        return out


@dataclass(frozen=True)
class BitCellInfo:
    """Metadata and value for a single bit in the Inspector Bit Strip."""

    bit_index: int  # Hardware bit index: wl-1 down to 0
    label: str  # "S" for signed MSB, else str(bit_index)
    value: int  # 0 or 1
    role: str  # "sign" | "int" | "frac" | "exponent"
    weight_power: int | None  # FXP power of 2; None for floating-point fields.
    has_radix_after: bool  # True if the binary point '.' sits right to the right of this bit (in MSB->LSB)


@dataclass(frozen=True)
class InspectorSnapshot:
    """Immutable snapshot of a scalar or single matrix element for the UI Inspector."""

    var_name: str
    coord: tuple[int, int] | None
    display_title: str
    is_array_element: bool
    is_fxp: bool
    spec: FxpSpec
    format_str: str
    real_val: float
    real_str: str
    hex_str: str
    s2_int: int
    s2_str: str
    uint_val: int
    uint_str: str
    c1_int: int
    c1_str: str
    bin_str: str
    bits_msb_to_lsb: list[BitCellInfo]
    quant_err: float | None  # Approximation; None when untracked or outside binary64 range.
    is_saturated: bool
    status_kind: str  # "saturated" | "quant_err" | "exact" | "untracked"
    status_text: str
    lsb_str: str
    range_str: str
    whole_variable_fxp: bool = True  # A container may hold an FXP cell without being an FXP array.


@dataclass(frozen=True)
class PlainValueSnapshot:
    """A Python value, without an invented fixed-point bit representation."""

    var_name: str
    coord: tuple[int, int] | None
    display_title: str
    value_str: str
    type_name: str
    editable: bool
    native_bits: dict | None = None  # Numeric representation DTO; never an FXP format.
    can_convert_to_fxp: bool = False  # Whole-variable capability, not the selected cell's type.


def extract_spec(obj: Any) -> FxpSpec:
    """Read an existing FXP format; native numbers have no implicit FXP width."""
    if isinstance(obj, (apy.APyFixed, apy.APyFixedArray)):
        is_unsigned = bool(getattr(obj, "is_unsigned", False))
        hw_wl = getattr(obj, "hw_wl", 0) or (obj.bits - 1 if is_unsigned else obj.bits)
        return FxpSpec(signed=not is_unsigned, wl=max(1, int(hw_wl)), fwl=int(obj.frac_bits))
    raise TypeError("FXP format and raw-bit controls require an APyFixed value; use APyFixed.from_float() or Convert to FXP")


def raw_uint_from_apy_scalar(val: apy.APyFixed, spec: FxpSpec) -> int:
    """Return the hardware wl-bit unsigned integer pattern [0, 2^wl - 1]."""
    mask = (1 << spec.wl) - 1
    return int(val.to_bits()) & mask


def s2_from_raw_uint(uint_val: int, wl: int) -> int:
    """Interpret a wl-bit unsigned integer as 2's complement signed integer."""
    mask = (1 << wl) - 1
    u = uint_val & mask
    sign_bit = 1 << (wl - 1)
    return (u - (1 << wl)) if (u & sign_bit) else u


def c1_from_raw_uint(uint_val: int, wl: int) -> int:
    """Interpret a wl-bit unsigned integer as 1's complement signed integer."""
    mask = (1 << wl) - 1
    u = uint_val & mask
    sign_bit = 1 << (wl - 1)
    if u & sign_bit:
        return -(mask ^ u)
    return u


def format_hex(uint_val: int, wl: int) -> str:
    """Format a wl-bit unsigned pattern as 0x... padded to ceil(wl/4) hex digits."""
    hex_digits = max(1, (wl + 3) // 4)
    mask = (1 << wl) - 1
    return f"0x{(uint_val & mask):0{hex_digits}X}"


def format_real_number(val: float) -> str:
    """Format a floating-point / real number cleanly without trailing float noise."""
    if not math.isfinite(val):
        return str(val)
    if val == 0.0:
        return "0"
    if float(val).is_integer() and abs(val) < 1e15:
        return str(int(val))
    return f"{val:.10g}"


def _quantize_real_to_raw_int(
    real_val: Any,
    spec: FxpSpec,
    quant: str = "RND",
    overflow: str = "SAT",
) -> tuple[int, bool]:
    """Quantize a real number into a hardware wl-bit unsigned pattern and return (uint_val, is_saturated)."""
    if quant.upper() not in QUANT_MAP or overflow.upper() not in OVERFLOW_MAP:
        raise ValueError(f"Unsupported quantization/overflow mode: {quant}/{overflow}")
    if isinstance(real_val, apy.APyFixed):
        source = real_val
    else:
        if isinstance(real_val, (int, np.integer)):
            numerator, denominator = int(real_val), 1
        else:
            value = float(real_val)
            if not math.isfinite(value):
                raise ValueError(f"Cannot quantize non-finite value: {value}")
            numerator, denominator = value.as_integer_ratio()
        frac_bits = denominator.bit_length() - 1
        bits = max(2, numerator.bit_length() + 1)
        source = apy.APyFixed(numerator & ((1 << bits) - 1),
                             bits=bits, int_bits=bits - frac_bits)

    # Let APyTypes perform rounding. The intermediate has spare integer bits,
    # so overflow detection happens before applying the hardware signed/unsigned
    # range. No binary64 round-trip is involved for ints or existing FXP values.
    rounded = source.cast(int_bits=max(source.int_bits, spec.apy_int_bits) + 2,
                          frac_bits=spec.fwl, quantization=QUANT_MAP[quant.upper()])
    q_int = s2_from_raw_uint(int(rounded.to_bits()), rounded.bits)

    min_i = spec.min_raw_int
    max_i = spec.max_raw_int
    saturated = False

    if q_int > max_i or q_int < min_i:
        if overflow.upper() == "SAT":
            saturated = True
            q_int = max(min_i, min(max_i, q_int))
        else:
            # WRAP to [min_i, max_i]
            mask = (1 << spec.wl) - 1
            u = q_int & mask
            q_int = s2_from_raw_uint(u, spec.wl) if spec.signed else u

    mask = (1 << spec.wl) - 1
    uint_val = q_int & mask
    return uint_val, saturated


def _exact_real(value: Any) -> Fraction:
    """Capture the value supplied to a conversion without a binary64 round-trip."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, apy.APyFixed):
        integer = s2_from_raw_uint(int(value.to_bits()), value.bits)
        return integer * Fraction(2) ** (-value.frac_bits)
    if isinstance(value, (int, np.integer)):
        return Fraction(int(value))
    return Fraction.from_float(float(value))


def _error_text(error: Fraction) -> str:
    # Decimal formatting also preserves nonzero errors below binary64's range.
    with localcontext() as context:
        context.prec = 4
        return f"{Decimal(error.numerator) / Decimal(error.denominator):+.3e}"


def make_fxp_from_raw_uint(
    uint_val: int,
    spec: FxpSpec,
    *,
    target_real: Any = None,
    is_saturated: bool = False,
) -> RpkFixed:
    """Construct an RpkFixed directly from its wl-bit unsigned bit pattern."""
    mask = (1 << spec.wl) - 1
    u = int(uint_val) & mask
    # For signed APyFixed(bits=wl), pass the wl-bit pattern `u` directly.
    # For unsigned RpkFixed(bits=wl+1), MSB (bit wl) is 0, so passing `u` gives exact positive value!
    obj = RpkFixed(u, bits=spec.apy_bits, int_bits=spec.apy_int_bits)
    obj.is_unsigned = not spec.signed
    obj.hw_wl = spec.wl
    obj.target_real = _exact_real(target_real) if target_real is not None else None
    obj.is_saturated = is_saturated
    return obj


def _validate_raw_uint(value: int, spec: FxpSpec) -> int:
    """Reject accidental truncation at user-facing raw-bit input boundaries."""
    maximum = (1 << spec.wl) - 1
    if not 0 <= value <= maximum:
        raise ValueError(
            f"Raw bit value {value} is outside WL={spec.wl} range [0, {maximum}]. "
            f"To truncate intentionally, apply value & ((1 << {spec.wl}) - 1) in the REPL."
        )
    return value


def _parse_binary_text(text: str) -> int:
    """Allow displayed spaces/radix points while rejecting non-binary digits."""
    clean = text.strip().replace("_", "").replace(" ", "").replace(".", "")
    negative = clean.startswith("-")
    if negative:
        clean = clean[1:]
    if clean.lower().startswith("0b"):
        clean = clean[2:]
    if not clean or any(digit not in "01" for digit in clean):
        raise ValueError("Binary input must contain only 0 and 1 (spaces, '_' and radix '.' are allowed)")
    return -int(clean, 2) if negative else int(clean, 2)


def _parse_bit_string(s: str, spec: FxpSpec) -> int:
    """Parse a raw Hex/Bin literal without silently discarding high bits."""
    t = s.strip().replace("_", "")
    unsigned_text = t.lstrip("+-").lower()
    if unsigned_text.startswith("0x"):
        return _validate_raw_uint(int(t, 16), spec)
    if unsigned_text.startswith("0b"):
        return _validate_raw_uint(_parse_binary_text(t), spec)
    raise ValueError(f"Unsupported bit string literal: {s!r}")


def make_fxp(
    value_or_fmt: Any = 0.0,
    fmt: str | FxpSpec | None = None,
    *,
    quant: str = "RND",
    overflow: str = "SAT",
) -> RpkFixed | RpkFixedArray:
    """Internal GUI conversion with explicit quantization/overflow provenance.

    REPL users construct native APyTypes objects directly. This conversion
    helper is used by GUI value edits and format changes, not auto-imported.
    """
    if fmt is None:
        if isinstance(value_or_fmt, (str, FxpSpec)):
            spec = FormatEngine.parse(value_or_fmt)
            return make_fxp_from_raw_uint(0, spec, target_real=0.0, is_saturated=False)
        raise ValueError("Specify a GUI FXP format, e.g. Q4.12")
    spec = FormatEngine.parse(fmt)

    # Raw hex / binary string
    if isinstance(value_or_fmt, str):
        t = value_or_fmt.strip()
        if t.lstrip("+-").lower().startswith(("0x", "0b")):
            u = _parse_bit_string(t, spec)
            return make_fxp_from_raw_uint(u, spec)
        # Numeric string
        real_v = float(t)
        u, sat = _quantize_real_to_raw_int(real_v, spec, quant=quant, overflow=overflow)
        return make_fxp_from_raw_uint(u, spec, target_real=real_v, is_saturated=sat)

    # Scalar numeric or APyFixed
    if isinstance(value_or_fmt, (int, float, np.number, apy.APyFixed)):
        u, sat = _quantize_real_to_raw_int(value_or_fmt, spec, quant=quant, overflow=overflow)
        return make_fxp_from_raw_uint(u, spec, target_real=value_or_fmt, is_saturated=sat)

    # Array / Matrix (list, tuple, ndarray, APyFixedArray)
    if isinstance(value_or_fmt, apy.APyFixedArray):
        np_arr = np.empty(value_or_fmt.shape, dtype=object)
        for index in np.ndindex(value_or_fmt.shape):
            np_arr[index] = value_or_fmt[index]
    else:
        np_arr = np.asarray(value_or_fmt, dtype=object)

    if np_arr.ndim == 0:
        u, sat = _quantize_real_to_raw_int(np_arr.item(), spec, quant=quant, overflow=overflow)
        return make_fxp_from_raw_uint(u, spec, target_real=np_arr.item(), is_saturated=sat)

    quantized = [_quantize_real_to_raw_int(x, spec, quant=quant, overflow=overflow)
                 for x in np_arr.flat]
    flat_uints = [item[0] for item in quantized]
    uint_grid = np.array(flat_uints, dtype=object).reshape(np_arr.shape).tolist()
    arr = RpkFixedArray(uint_grid, bits=spec.apy_bits, int_bits=spec.apy_int_bits)
    arr.is_unsigned = not spec.signed
    arr.hw_wl = spec.wl
    arr.target_values = np_arr.copy()
    arr.saturated_mask = np.array([item[1] for item in quantized]).reshape(np_arr.shape)
    arr.quantized_bits = np.array(flat_uints, dtype=object).reshape(np_arr.shape)
    return arr


def make_fxpi(
    raw_int: Any,
    fmt: str | FxpSpec,
    *,
    overflow: str = "SAT",
) -> RpkFixed | RpkFixedArray:
    """Internal conversion from unscaled integers using the GUI overflow policy."""
    spec = FormatEngine.parse(fmt)
    if overflow.upper() not in OVERFLOW_MAP:
        raise ValueError(f"Unsupported overflow mode: {overflow}")
    min_i = spec.min_raw_int
    max_i = spec.max_raw_int
    mask = (1 << spec.wl) - 1

    def _conv_one(v: Any) -> tuple[int, bool]:
        if isinstance(v, (bool, np.bool_)):
            raise TypeError("Unscaled input requires raw integers, not bool")
        try:
            iv = operator.index(v)
        except TypeError as exc:
            raise TypeError("Unscaled input requires raw integers; use a Real value edit for quantization") from exc
        sat = False
        if iv < min_i or iv > max_i:
            if overflow.upper() == "SAT":
                sat = True
                iv = max(min_i, min(max_i, iv))
            else:
                iv = iv & mask
        return iv & mask, sat

    if np.isscalar(raw_int):
        u, sat = _conv_one(raw_int)
        return make_fxp_from_raw_uint(u, spec, target_real=operator.index(raw_int) * Fraction(2) ** (-spec.fwl),
                                      is_saturated=sat)

    # Object conversion preserves mixed Python integers, including >64-bit values.
    np_arr = np.asarray(raw_int, dtype=object)
    if np_arr.ndim == 0:
        u, sat = _conv_one(np_arr.item())
        return make_fxp_from_raw_uint(u, spec, target_real=operator.index(np_arr.item()) * Fraction(2) ** (-spec.fwl),
                                      is_saturated=sat)

    quantized = [_conv_one(x) for x in np_arr.flat]
    flat_uints = [item[0] for item in quantized]
    uint_grid = np.array(flat_uints, dtype=object).reshape(np_arr.shape).tolist()
    arr = RpkFixedArray(uint_grid, bits=spec.apy_bits, int_bits=spec.apy_int_bits)
    arr.is_unsigned = not spec.signed
    arr.hw_wl = spec.wl
    scale = Fraction(2) ** (-spec.fwl)
    arr.target_values = np.array([operator.index(x) * scale for x in np_arr.flat], dtype=object).reshape(np_arr.shape)
    arr.saturated_mask = np.array([item[1] for item in quantized]).reshape(np_arr.shape)
    arr.quantized_bits = np.array(flat_uints, dtype=object).reshape(np_arr.shape)
    return arr


def reformat_value(
    obj: Any,
    new_spec: FxpSpec | str,
    *,
    pin_mode: str = "real",
    quant: str = "RND",
    overflow: str = "SAT",
) -> RpkFixed | RpkFixedArray:
    """Cast numerically with Real pin, or reinterpret hardware bits with Hex pin.

    Hex pin keeps the low bits when shrinking and zero-extends when widening,
    even for signed sources. Signedness and FWL changes reinterpret that pattern.
    Neither mode mutates the source scalar or array.
    """
    spec = FormatEngine.parse(new_spec)
    pin = pin_mode.lower()
    if pin not in ("real", "hex"):
        raise ValueError("Pin mode must be real or hex")
    if pin == "hex" and not isinstance(obj, (apy.APyFixed, apy.APyFixedArray)):
        raise TypeError("Hex pin requires an existing FXP format; use APyFixed for raw bit-pattern initialization")

    # Scalar case
    if not isinstance(obj, (np.ndarray, apy.APyFixedArray, list, tuple)):
        if pin == "hex":
            old_spec = extract_spec(obj)
            u = raw_uint_from_apy_scalar(obj, old_spec)
            return make_fxp_from_raw_uint(u, spec)
        else:
            # Pin Real Value
            u, sat = _quantize_real_to_raw_int(
                obj, spec, quant=quant, overflow=overflow
            )
            return make_fxp_from_raw_uint(
                u, spec, target_real=obj, is_saturated=sat
            )

    # Array case
    if isinstance(obj, apy.APyFixedArray):
        if pin == "hex":
            old_spec = extract_spec(obj)
            old_mask = (1 << old_spec.wl) - 1
            new_mask = (1 << spec.wl) - 1
            raw_bits = np.asarray(obj.to_bits(), dtype=object)
            flat_uints = [(int(b) & old_mask) & new_mask for b in raw_bits.flat]
            uint_grid = (
                np.array(flat_uints, dtype=object).reshape(obj.shape).tolist()
            )
            arr = RpkFixedArray(
                uint_grid, bits=spec.apy_bits, int_bits=spec.apy_int_bits
            )
            arr.is_unsigned = not spec.signed
            arr.hw_wl = spec.wl
            return arr
        else:
            return make_fxp(
                obj, spec, quant=quant, overflow=overflow
            )
    else:
        return make_fxp(obj, spec, quant=quant, overflow=overflow)


def update_scalar_from_field(
    current_obj: Any,
    field: str,
    text_val: str,
    *,
    quant: str = "RND",
    overflow: str = "SAT",
) -> RpkFixed:
    """Update a scalar from an edited Inspector field ('real', 'hex', 's2', 'uint', 'bin')."""
    spec = extract_spec(current_obj)
    clean = text_val.strip().replace("_", "")
    if not clean:
        raise ValueError("Value must not be empty")

    f_key = field.lower()
    if f_key == "real":
        real_v = _evaluate_numeric_expression(text_val)
        u, sat = _quantize_real_to_raw_int(
            real_v, spec, quant=quant, overflow=overflow
        )
        return make_fxp_from_raw_uint(
            u, spec, target_real=real_v, is_saturated=sat
        )

    if f_key == "hex":
        u = _validate_raw_uint(int(clean, 16), spec)
        return make_fxp_from_raw_uint(u, spec)

    if f_key == "uint":
        u = _validate_raw_uint(int(clean, 0), spec)
        return make_fxp_from_raw_uint(u, spec)

    if f_key == "s2":
        iv = int(clean, 0)
        return make_fxpi(iv, spec, overflow=overflow)  # type: ignore[return-value]

    if f_key == "bin":
        u = _validate_raw_uint(_parse_binary_text(clean), spec)
        return make_fxp_from_raw_uint(u, spec)

    raise ValueError(f"Unknown field: {field}")


def _evaluate_numeric_expression(text: str) -> int | float:
    """Evaluate bounded numeric input without executing clipboard Python code.

    This editor convenience is deliberately smaller than the unrestricted REPL.
    Only numeric literals, pi/e, arithmetic and integer bit operators are valid.
    """
    advice = " Use the REPL for larger expressions or general Python."

    def reject(reason: str):
        raise ValueError(reason + advice)

    if len(text) > 4096:
        reject("Numeric input is limited to 4096 characters.")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        if "leading zeros" in exc.msg:
            reject("Leading zeros are not allowed in decimal integers; use 3 instead of 03.")
        reject("Invalid numeric expression.")
    except (ValueError, RecursionError):
        reject("Invalid numeric expression.")
    if sum(1 for _ in ast.walk(tree)) > 512:
        reject("Numeric input is limited to 512 AST nodes.")

    def checked(value):
        if type(value) is int:
            if value.bit_length() > 16384:
                reject("Integer results are limited to 16384 bits.")
        elif type(value) is float:
            if not math.isfinite(value):
                reject("Numeric results must be finite.")
        else:
            reject("Only real int/float values are allowed.")
        return value

    def integers(*values):
        if any(type(value) is not int for value in values):
            reject("Bit operators require integers, excluding bool.")

    arithmetic = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
    bitwise = {ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor}

    def evaluate(node):
        if isinstance(node, ast.Constant):
            return checked(node.value)
        if isinstance(node, ast.Name) and node.id in ("pi", "e"):
            return math.pi if node.id == "pi" else math.e
        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return checked(-value)
            if isinstance(node.op, ast.Invert):
                integers(value)
                return checked(~value)
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            operation = type(node.op)
            if operation in arithmetic:
                return checked(arithmetic[operation](left, right))
            if operation in bitwise:
                integers(left, right)
                return checked(bitwise[operation](left, right))
            if operation in (ast.LShift, ast.RShift):
                integers(left, right)
                if not 0 <= right <= 4096:
                    reject("Shift counts must be in [0, 4096].")
                if operation is ast.LShift and left and left.bit_length() + right > 16384:
                    reject("Integer results are limited to 16384 bits.")
                return checked(left << right if operation is ast.LShift else left >> right)
            if operation is ast.Pow:
                if abs(right) > 4096:
                    reject("Power exponents must have absolute value <= 4096.")
                # Reject guaranteed oversized integer powers before allocating
                # them. The remaining result is at most 4096 bits over budget.
                if (type(left) is int and type(right) is int and right > 0 and abs(left) > 1
                        and (left.bit_length() - 1) * right >= 16384):
                    reject("Integer results are limited to 16384 bits.")
                return checked(left ** right)
        reject("Only numeric literals, pi/e, arithmetic and integer bit operators are allowed.")

    try:
        return evaluate(tree.body)
    except ZeroDivisionError as exc:
        raise ValueError("Division by zero in numeric expression." + advice) from exc
    except OverflowError as exc:
        raise ValueError("Numeric overflow in expression." + advice) from exc
    except RecursionError as exc:
        raise ValueError("Numeric expression is nested too deeply." + advice) from exc


def _scalar_bit_field(current_obj: Any, high: int, low: int) -> tuple[FxpSpec, int, int]:
    if not isinstance(current_obj, apy.APyFixed):
        raise TypeError("Bit fields require an FXP scalar or an extracted FXP array element")
    spec = extract_spec(current_obj)
    indices = []
    for index in (high, low):
        if isinstance(index, (bool, np.bool_)):
            raise TypeError("Bit field indices must be integers, not bool")
        try:
            indices.append(operator.index(index))
        except TypeError as exc:
            raise TypeError("Bit field indices must be integers") from exc
    high, low = indices
    if not 0 <= low <= high < spec.wl:
        raise ValueError(f"Bit field must satisfy 0 <= low <= high < WL={spec.wl}; got [{high}:{low}]")
    return spec, high, low


def read_scalar_bit_field(current_obj: Any, high: int, low: int) -> int:
    """Read the inclusive hardware bit range [high:low] as an unsigned integer."""
    spec, high, low = _scalar_bit_field(current_obj, high, low)
    return (raw_uint_from_apy_scalar(current_obj, spec) >> low) & ((1 << (high - low + 1)) - 1)


def update_scalar_bit_field(current_obj: Any, high: int, low: int, value: int) -> RpkFixed:
    """Replace inclusive bits [high:low], preserving every other hardware bit."""
    spec, high, low = _scalar_bit_field(current_obj, high, low)
    if isinstance(value, (bool, np.bool_)):
        raise TypeError("Bit field value must be an integer, not bool")
    try:
        field_value = operator.index(value)
    except TypeError as exc:
        raise TypeError("Bit field value must be an integer") from exc
    width = high - low + 1
    field_mask = (1 << width) - 1
    if not 0 <= field_value <= field_mask:
        raise ValueError(f"Bit field [{high}:{low}] value must be in [0, {field_mask}], got {field_value}")
    raw = raw_uint_from_apy_scalar(current_obj, spec)
    updated = (raw & ~(field_mask << low)) | (field_value << low)
    return make_fxp_from_raw_uint(updated, spec)


def toggle_scalar_bit(current_obj: Any, bit_index: int) -> RpkFixed:
    """Flip a single bit (0 <= bit_index < wl) and return the updated RpkFixed."""
    spec = extract_spec(current_obj)
    if not (0 <= bit_index < spec.wl):
        raise ValueError(f"Bit index {bit_index} out of range [0, {spec.wl - 1}]")
    if isinstance(current_obj, apy.APyFixed):
        u = raw_uint_from_apy_scalar(current_obj, spec)
    else:
        u, _ = _quantize_real_to_raw_int(current_obj, spec)
    new_u = u ^ (1 << bit_index)
    return make_fxp_from_raw_uint(new_u, spec)


def invert_scalar_bits(current_obj: Any) -> RpkFixed:
    """Bitwise NOT (~x) across all wl bits."""
    spec = extract_spec(current_obj)
    mask = (1 << spec.wl) - 1
    if isinstance(current_obj, apy.APyFixed):
        u = raw_uint_from_apy_scalar(current_obj, spec)
    else:
        u, _ = _quantize_real_to_raw_int(current_obj, spec)
    return make_fxp_from_raw_uint((~u) & mask, spec)


def clear_scalar_bits(current_obj: Any) -> RpkFixed:
    """Reset all bits to 0."""
    spec = extract_spec(current_obj)
    return make_fxp_from_raw_uint(0, spec)


def build_inspector_snapshot(
    var_name: str,
    scalar_obj: Any,
    config: CalcConfig,
    *,
    coord: tuple[int, int] | None = None,
    parent_spec: FxpSpec | None = None,
) -> InspectorSnapshot:
    """Build an immutable InspectorSnapshot for a scalar or a selected array element."""
    if not isinstance(scalar_obj, apy.APyFixed):
        raise TypeError("FXP Inspector snapshots require an FXP value")
    spec = parent_spec if parent_spec is not None else extract_spec(scalar_obj)
    if parent_spec is not None and not parent_spec.signed:
        # Element extracted from an unsigned RpkFixedArray has bits=wl+1.
        uint_val = int(scalar_obj.to_bits()) & ((1 << spec.wl) - 1)
    else:
        uint_val = raw_uint_from_apy_scalar(scalar_obj, spec)
    real_val = float(scalar_obj)
    target_real = getattr(scalar_obj, "target_real", None)
    is_saturated = bool(getattr(scalar_obj, "is_saturated", False))

    s2_int = s2_from_raw_uint(uint_val, spec.wl)
    c1_int = c1_from_raw_uint(uint_val, spec.wl)
    hex_str = format_hex(uint_val, spec.wl)
    bin_str = f"{uint_val:0{spec.wl}b}"

    # Build bit cells from MSB (wl-1) down to LSB (0)
    bits_msb_to_lsb: list[BitCellInfo] = []
    for bit_idx in range(spec.wl - 1, -1, -1):
        bit_val = (uint_val >> bit_idx) & 1
        weight_pwr = bit_idx - spec.fwl
        if spec.signed and bit_idx == spec.wl - 1:
            role = "sign"
            label = "S"
        elif weight_pwr >= 0:
            role = "int"
            label = str(bit_idx)
        else:
            role = "frac"
            label = str(bit_idx)
        # Show binary radix point '.' right after bit_idx == spec.fwl (if 0 < spec.fwl < spec.wl)
        has_radix_after = (0 < spec.fwl < spec.wl) and (bit_idx == spec.fwl)
        bits_msb_to_lsb.append(
            BitCellInfo(
                bit_index=bit_idx,
                label=label,
                value=bit_val,
                role=role,
                weight_power=weight_pwr,
                has_radix_after=has_radix_after,
            )
        )

    exact_error = _exact_real(scalar_obj) - _exact_real(target_real) if target_real is not None else None
    try:
        quant_err = float(exact_error) if exact_error is not None else None
    except OverflowError:
        quant_err = None
    if quant_err is not None and (not math.isfinite(quant_err) or (quant_err == 0 and exact_error != 0)):
        quant_err = None
    fmt_str = FormatEngine.format(spec, config)

    if is_saturated:
        status_kind = "saturated"
        status_text = (
            f"SATURATED [Range: {format_real_number(spec.min_real)} .. "
            f"{format_real_number(spec.max_real)}]"
        )
    elif exact_error is None:
        status_kind = "untracked"
        status_text = "No conversion reference (error unknown)"
    elif exact_error != 0:
        status_kind = "quant_err"
        status_text = f"Quant Err: {_error_text(exact_error)} (last conversion)"
    else:
        status_kind = "exact"
        status_text = f"Exact conversion (LSB = 2^-{spec.fwl} = {spec.lsb:.6g})"

    display_title = (
        f"{var_name}[{coord[0]}, {coord[1]}]" if coord is not None else var_name
    )

    return InspectorSnapshot(
        var_name=var_name,
        coord=coord,
        display_title=display_title,
        is_array_element=coord is not None,
        is_fxp=True,
        spec=spec,
        format_str=fmt_str,
        real_val=real_val,
        real_str=str(scalar_obj),
        hex_str=hex_str,
        s2_int=s2_int,
        s2_str=str(s2_int),
        uint_val=uint_val,
        uint_str=str(uint_val),
        c1_int=c1_int,
        c1_str=str(c1_int),
        bin_str=bin_str,
        bits_msb_to_lsb=bits_msb_to_lsb,
        quant_err=quant_err,
        is_saturated=is_saturated,
        status_kind=status_kind,
        status_text=status_text,
        lsb_str=f"2^-{spec.fwl} ({spec.lsb:.4g})",
        range_str=f"[{format_real_number(spec.min_real)}, {format_real_number(spec.max_real)}]",
    )
