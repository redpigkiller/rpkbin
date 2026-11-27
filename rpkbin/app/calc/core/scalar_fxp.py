"""Exact hardware fixed-point conversions without optional dependencies."""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from operator import index

from .format_engine import FxpSpec


@dataclass(frozen=True)
class BitCellInfo:
    bit_index: int
    label: str
    value: int
    role: str
    weight_power: int | None
    has_radix_after: bool


@dataclass(frozen=True)
class ScalarQuantizeResult:
    raw_uint: int
    exact_input: Fraction
    quantized_value: Fraction
    overflow_kind: str

    @property
    def is_quantized(self) -> bool:
        return self.exact_input != self.quantized_value

    @property
    def status_kind(self) -> str:
        if self.overflow_kind != "none":
            return self.overflow_kind
        return "quantized" if self.is_quantized else "exact"


def reinterp_raw_bits(bits: int, spec: FxpSpec) -> int:
    return index(bits) & ((1 << spec.wl) - 1)


def raw_uint_to_signed_int(raw_uint: int, spec: FxpSpec) -> int:
    raw = reinterp_raw_bits(raw_uint, spec)
    if spec.signed and raw & (1 << (spec.wl - 1)):
        return raw - (1 << spec.wl)
    return raw


def raw_uint_to_fraction(raw_uint: int, spec: FxpSpec) -> Fraction:
    return raw_uint_to_signed_int(raw_uint, spec) * Fraction(2) ** -spec.fwl


def quantize_scalar(value, spec: FxpSpec, *, quant: str, overflow: str) -> ScalarQuantizeResult:
    quant, overflow = quant.upper(), overflow.upper()
    if quant not in {"RND", "RND_EVEN", "TRN", "TRN_ZERO"} or overflow not in {"SAT", "WRAP"}:
        raise ValueError(f"Unsupported quantization/overflow mode: {quant}/{overflow}")
    exact = Fraction(Decimal(value)) if isinstance(value, str) else Fraction(value)
    scaled = exact * Fraction(2) ** spec.fwl
    if quant == "RND":
        rounded = (scaled + Fraction(1, 2)).__floor__()
    elif quant == "RND_EVEN":
        rounded = round(scaled)
    elif quant == "TRN_ZERO":
        rounded = int(scaled)
    else:
        rounded = scaled.__floor__()

    overflow_kind = "none"
    if not spec.min_raw_int <= rounded <= spec.max_raw_int:
        if overflow == "SAT":
            rounded = min(spec.max_raw_int, max(spec.min_raw_int, rounded))
            overflow_kind = "saturated"
        else:
            overflow_kind = "wrapped"
    raw = reinterp_raw_bits(rounded, spec)
    return ScalarQuantizeResult(raw, exact, raw_uint_to_fraction(raw, spec), overflow_kind)


def build_fxp_bit_cells(raw_uint: int, spec: FxpSpec, *, bit_order: str = "msb") -> list[BitCellInfo]:
    indices = range(spec.wl) if bit_order == "lsb" else range(spec.wl - 1, -1, -1)
    cells = []
    for bit in indices:
        sign = spec.signed and bit == spec.wl - 1
        weight = bit - spec.fwl
        role = "sign" if sign else ("int" if weight >= 0 else "frac")
        cells.append(BitCellInfo(bit, "S" if sign else str(bit), (raw_uint >> bit) & 1,
                                 role, weight, 0 < spec.fwl < spec.wl and weight == 0))
    return cells


def _field_bounds(wl: int, high: int, low: int) -> tuple[int, int, int]:
    if isinstance(high, bool) or isinstance(low, bool):
        raise TypeError("Bit field indices must be integers, not bool")
    try:
        high, low = index(high), index(low)
    except TypeError as exc:
        raise TypeError("Bit field indices must be integers") from exc
    if not 0 <= low <= high < wl:
        raise ValueError(f"Bit field must satisfy 0 <= low <= high < WL={wl}; got [{high}:{low}]")
    return high, low, (1 << (high - low + 1)) - 1


def read_raw_bit_field(raw_uint: int, wl: int, high: int, low: int) -> int:
    _, low, mask = _field_bounds(wl, high, low)
    return (raw_uint >> low) & mask


def update_raw_bit_field(raw_uint: int, wl: int, high: int, low: int, value: int) -> int:
    high, low, mask = _field_bounds(wl, high, low)
    if isinstance(value, bool):
        raise TypeError("Bit field value must be an integer, not bool")
    try:
        value = index(value)
    except TypeError as exc:
        raise TypeError("Bit field value must be an integer") from exc
    if not 0 <= value <= mask:
        raise ValueError(f"Bit field [{high}:{low}] value must be in [0, {mask}], got {value}")
    return (raw_uint & ~(mask << low)) | (value << low)
