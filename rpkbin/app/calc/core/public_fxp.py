"""Public value and raw-bit constructors."""

from decimal import Decimal, InvalidOperation
from operator import index

import apytypes as apy
import numpy as np

from .config import CalcConfig
from .format_engine import FormatEngine, FxpSpec
from .fxp_model import RpkFixedArray, _exact_real, make_fxp_from_raw_uint
from .scalar_fxp import quantize_scalar, reinterp_raw_bits

_active_config = None


def bind_session_config(provider):
    global _active_config
    _active_config = provider


def _current_config():
    return CalcConfig.load() if _active_config is None else _active_config()


def _resolve_format(fmt, config, bits, wl, fwl, int_bits, frac_bits, signed):
    if bits is not None:
        if wl is not None:
            raise ValueError("Use either bits or wl")
        wl = bits
    if fmt is not None:
        if any(value is not None for value in (wl, fwl, int_bits, frac_bits)):
            raise ValueError("Use either fmt or explicit bit widths")
        return FormatEngine.parse_input(fmt, config)
    if fwl is not None and frac_bits is not None:
        raise ValueError("Use either fwl or frac_bits")
    fraction = fwl if fwl is not None else frac_bits
    if int_bits is not None:
        if wl is None and fraction is not None:
            wl = int_bits + fraction
        elif fraction is None and wl is not None:
            fraction = wl - int_bits
        else:
            raise ValueError("Specify two of wl, int_bits and fractional width")
    if wl is None or fraction is None:
        raise ValueError("Specify an FXP format or explicit bit widths")
    return FxpSpec(signed=signed, wl=index(wl), fwl=index(fraction))


def _array_source(source, *, raw):
    if isinstance(source, apy.APyFixedArray):
        shape = source.shape
        if not np.prod(shape, dtype=object):
            return np.empty(shape, dtype=object)
        bits = source.reshape((1,)).to_bits() if shape == () else source.to_bits()
        values = np.asarray(bits, dtype=object).reshape(shape)
        if raw:
            return values
        return np.array([apy.APyFixed(int(value), bits=source.bits, int_bits=source.int_bits)
                         for value in values.flat], dtype=object).reshape(shape)
    if isinstance(source, (list, tuple, np.ndarray)):
        return np.asarray(source, dtype=object)
    return None


def _make_array(raw, spec):
    array = RpkFixedArray.__new__(RpkFixedArray)
    # Initialize via APyTypes' serialized representation, which preserves
    # rank zero and empty trailing dimensions as well as the subclass.
    array.__setstate__((spec.apy_bits, spec.apy_int_bits, list(raw.shape),
                        raw.reshape(-1).tolist()))
    array.is_unsigned = not spec.signed
    array.hw_wl = spec.wl
    return array


def _numeric(value):
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("fxp requires a numeric value") from exc
    return _exact_real(value)


def fxp(x, fmt=None, *, bits=None, wl=None, fwl=None, int_bits=None, frac_bits=None, signed=True,
        quant=None, overflow=None):
    """Quantize a number or array to an explicit hardware format."""
    config = _current_config()
    spec = _resolve_format(fmt, config, bits, wl, fwl, int_bits, frac_bits, signed)
    quant = config.default_quant if quant is None else quant
    overflow = config.default_overflow if overflow is None else overflow
    values = _array_source(x, raw=False)
    if values is None:
        result = quantize_scalar(_numeric(x), spec, quant=quant, overflow=overflow)
        return make_fxp_from_raw_uint(result.raw_uint, spec, target_real=result.exact_input,
                                      is_saturated=result.overflow_kind == "saturated")
    results = [quantize_scalar(_numeric(value), spec, quant=quant, overflow=overflow)
               for value in values.flat]
    raw = np.array([item.raw_uint for item in results], dtype=object).reshape(values.shape)
    array = _make_array(raw, spec)
    array.target_values = np.array([item.exact_input for item in results], dtype=object).reshape(values.shape)
    array.saturated_mask = np.array([item.overflow_kind == "saturated" for item in results],
                                    dtype=bool).reshape(values.shape)
    array.quantized_bits = raw
    return array


def _raw_value(value):
    if isinstance(value, apy.APyFixed):
        return value.to_bits()
    if isinstance(value, (bool, np.bool_)):
        raise TypeError("fxpi requires an integer bit pattern")
    return index(value)


def fxpi(x, fmt=None, *, bits=None, wl=None, fwl=None, int_bits=None, frac_bits=None, signed=True):
    """Interpret the low bits of an integer or array in an explicit hardware format."""
    spec = _resolve_format(fmt, _current_config(), bits, wl, fwl, int_bits, frac_bits, signed)
    values = _array_source(x, raw=True)
    if values is None:
        return make_fxp_from_raw_uint(reinterp_raw_bits(_raw_value(x), spec), spec)
    raw = np.array([reinterp_raw_bits(_raw_value(value), spec) for value in values.flat],
                   dtype=object).reshape(values.shape)
    return _make_array(raw, spec)
