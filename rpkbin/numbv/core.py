"""
NumBV v1 — Bit-true DSP fixed-point simulation core.

A single ``NumBV`` class handles both scalar and array operations.
Backed by pure numpy (int64 ndarray); no external fixed-point dependency.
Optionally accelerated via JAX (XLA) with a single ``set_backend()`` call.

Two-layer API
-------------
Operator layer (convenience) — results quantized back to **left-operand format**::

    import rpkbin.numbv as nbv

    fmt = nbv.Format(16, 12)
    a   = nbv.array([0.5, 1.0], fmt=fmt)
    b   = nbv.array([0.25, 0.25], fmt=fmt)
    y   = a + b        # → fmt  (auto-quantize back)
    p   = a * b        # → fmt  (auto-quantize back)

Function layer (pipeline-explicit) — caller controls output format::

    prod = nbv.mul(a, b)                     # → full-precision Format(32, 24)
    acc  = nbv.sum(prod, acc_fmt=acc_fmt)    # → acc_fmt
    out  = acc.quantize(out_fmt)             # → out_fmt

Backend selection
-----------------
Choose the process-global backend before creating objects; changing it afterward
raises ``RuntimeError``::

    nbv.set_backend("numpy")  # default, always available
    nbv.set_backend("jax")    # XLA acceleration (needs: pip install 'rpkbin[jax]')

Rounding modes
--------------
``"trunc"``           — truncate (floor), hardware >> default
``"round"``           — round half-up
``"round_half_even"`` — convergent rounding (Xilinx DSP48 default)
``"ceil"``            — round toward +∞
``"round_to_zero"``   — round toward zero (C integer truncation)
"""

from __future__ import annotations

import json
import math
import warnings
from typing import Literal, Any, Iterator

import numpy as np
from numpy.typing import NDArray, ArrayLike

from rpkbin.numbv._backend import get_xp, get_backend, mark_instantiated
# Re-export so users can call  nbv.set_backend("jax")
from rpkbin.numbv._backend import set_backend  # noqa: F401

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

RoundingMode = Literal["trunc", "round", "round_half_even", "ceil", "round_to_zero"]
OverflowMode = Literal["saturate", "wrap"]

_VALID_ROUNDING: frozenset[str] = frozenset(
    {"trunc", "round", "round_half_even", "ceil", "round_to_zero"}
)
_VALID_OVERFLOW: frozenset[str] = frozenset({"saturate", "wrap"})


# ===========================================================================
# Block 1 — Format
# ===========================================================================


class Format:
    """Immutable fixed-point format descriptor.

    Parameters
    ----------
    width   : Total bit-width (> 0, ≤ 63).
    frac    : Fractional bits (≥ 0; may exceed *width* — see spec §4.1).
    signed  : Two's-complement signed when True.
    rounding: Rounding mode applied during quantization.
    overflow: Overflow handling policy.

    Examples
    --------
    >>> fmt = Format(16, 12)            # S16.12
    >>> fmt = Format(16, 12, signed=False)
    >>> fmt2 = fmt.replace(width=32, frac=22)
    """

    __slots__ = (
        "width", "frac", "signed", "rounding", "overflow",
        # derived (cached)
        "_int_bits", "_scale", "_min_raw", "_max_raw",
        "_mask", "_wrap_offset",
    )

    def __init__(
        self,
        width: int,
        frac: int,
        signed: bool = True,
        rounding: RoundingMode = "trunc",
        overflow: OverflowMode = "saturate",
    ) -> None:
        if not isinstance(width, int) or width <= 0:
            raise ValueError(f"width must be a positive int, got {width!r}")
        if not isinstance(frac, int) or frac < 0:
            raise ValueError(f"frac must be a non-negative int, got {frac!r}")
        if width > 63:
            raise ValueError(
                f"width must be ≤ 63 (int64 backend limit), got {width}"
            )
        if rounding not in _VALID_ROUNDING:
            raise ValueError(
                f"rounding must be one of {sorted(_VALID_ROUNDING)}, got {rounding!r}"
            )
        if overflow not in _VALID_OVERFLOW:
            raise ValueError(
                f"overflow must be one of {sorted(_VALID_OVERFLOW)}, got {overflow!r}"
            )

        object.__setattr__(self, "width",    width)
        object.__setattr__(self, "frac",     frac)
        object.__setattr__(self, "signed",   bool(signed))
        object.__setattr__(self, "rounding", rounding)
        object.__setattr__(self, "overflow", overflow)

        # Derived attributes (cached for speed)
        signed_bit = 1 if signed else 0
        int_bits   = width - frac - signed_bit  # may be negative
        scale      = 1 << frac                  # 2**frac — exact integer

        if int_bits < 0:
            warnings.warn(
                f"Format has negative integer bits (width={width}, frac={frac}). "
                "This means the max absolute value is < 1.0. Is this intentional?",
                UserWarning,
                stacklevel=2,
            )

        if signed:
            min_raw = -(1 << (width - 1))
            max_raw =  (1 << (width - 1)) - 1
        else:
            min_raw = 0
            max_raw = (1 << width) - 1

        mask        = (1 << width) - 1
        wrap_offset = 1 << width

        object.__setattr__(self, "_int_bits",    int_bits)
        object.__setattr__(self, "_scale",       scale)
        object.__setattr__(self, "_min_raw",     min_raw)
        object.__setattr__(self, "_max_raw",     max_raw)
        object.__setattr__(self, "_mask",        mask)
        object.__setattr__(self, "_wrap_offset", wrap_offset)

    # Prevent accidental mutation (immutable-ish)
    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Format is immutable; use .replace() to create a modified copy.")

    # ---- derived properties -----------------------------------------------

    @property
    def int_bits(self) -> int:
        """Integer bits (= width - frac - signed_bit). May be negative."""
        return self._int_bits  # type: ignore[return-value]

    @property
    def scale(self) -> int:
        """Scale factor: 2**frac (exact integer)."""
        return self._scale  # type: ignore[return-value]

    @property
    def min_raw(self) -> int:
        """Minimum representable raw integer."""
        return self._min_raw  # type: ignore[return-value]

    @property
    def max_raw(self) -> int:
        """Maximum representable raw integer."""
        return self._max_raw  # type: ignore[return-value]

    @property
    def min_val(self) -> float:
        """Minimum representable real value."""
        return self._min_raw / self._scale  # type: ignore[operator]

    @property
    def max_val(self) -> float:
        """Maximum representable real value."""
        return self._max_raw / self._scale  # type: ignore[operator]

    @property
    def precision(self) -> float:
        """Smallest representable step: 1 / scale."""
        return 1.0 / self._scale  # type: ignore[operator]

    # ---- internal helpers (used by NumBV internals) -----------------------

    @property
    def _lo(self) -> int:
        return self._min_raw  # type: ignore[return-value]

    @property
    def _hi(self) -> int:
        return self._max_raw  # type: ignore[return-value]

    # ---- public methods ---------------------------------------------------

    def replace(self, **changes: Any) -> "Format":
        """Return a new Format with the given fields replaced.

    Examples
    --------
    >>> fmt = Format(16, 12)
    >>> fmt2 = fmt.replace(width=32, frac=22)
    >>> fmt3 = fmt.replace(rounding="round_half_even")
        """
        fields = {
            "width":    self.width,
            "frac":     self.frac,
            "signed":   self.signed,
            "rounding": self.rounding,
            "overflow": self.overflow,
        }
        unknown = set(changes) - set(fields)
        if unknown:
            raise TypeError(f"Unknown Format fields: {unknown}")
        fields.update(changes)
        return Format(**fields)

    # ---- dunder -----------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Format):
            return NotImplemented
        return (
            self.width    == other.width
            and self.frac    == other.frac
            and self.signed  == other.signed
            and self.rounding == other.rounding
            and self.overflow == other.overflow
        )

    def __hash__(self) -> int:
        return hash((self.width, self.frac, self.signed, self.rounding, self.overflow))

    def __repr__(self) -> str:
        s = "s" if self.signed else "u"
        return (
            f"Format({self.width}, {self.frac}, signed={self.signed}, "
            f"rounding={self.rounding!r}, overflow={self.overflow!r})"
        )


# ===========================================================================
# Block 2 — Pure internal helpers (backend-agnostic via get_xp())
# ===========================================================================


def _apply_overflow(
    raw: NDArray[np.int64],
    lo: int,
    hi: int,
    mask: int,
    wrap_offset: int,
    is_wrap: bool,
    is_signed: bool,
) -> NDArray[np.int64]:
    """Apply overflow policy to int64 raw values. Returns int64 ndarray."""
    xp = get_xp()
    if is_wrap:
        raw = raw & mask  # mask is Python int — works for numpy and JAX
        if is_signed:
            sign_offset = (hi + 1)
            raw = xp.where(raw > hi, raw - sign_offset - sign_offset, raw)
    else:
        raw = xp.clip(raw, lo, hi)
    return raw.astype("int64")


def _apply_rounding_shift(
    raw: NDArray[np.int64],
    shift_n: int,
    mode: RoundingMode,
) -> NDArray[np.int64]:
    """Right-shift *raw* by *shift_n* bits, applying *mode* rounding.

    This is the core rounding kernel, called for:
    - mul operator (to bring product frac back to self.frac)
    - quantize() when shifting raw representations
    """
    xp = get_xp()
    if shift_n <= 0:
        if shift_n < 0:
            # Left shift — used for frac alignment upcast
            return (raw << (-shift_n)).astype("int64")
        return raw.astype("int64")

    # All shift amounts are Python ints — safe for both numpy and JAX
    half          = 1 << (shift_n - 1)          # Python int
    remainder_mask = (1 << shift_n) - 1          # Python int
    remainder = raw & remainder_mask
    base = raw >> shift_n  # arithmetic right-shift for signed int64

    if mode == "trunc":
        return base.astype("int64")

    elif mode == "round":  # half-up
        return ((raw + half) >> shift_n).astype("int64")

    elif mode == "round_half_even":
        is_tie   = remainder == half
        is_odd   = (base & 1).astype(bool)
        round_up = (remainder > half) | (is_tie & is_odd)
        return xp.where(round_up, base + 1, base).astype("int64")

    elif mode == "ceil":
        has_remainder = (raw & remainder_mask) != 0
        return xp.where(has_remainder, base + 1, base).astype("int64")

    elif mode == "round_to_zero":
        # positive: floor; negative: ceil
        has_remainder = remainder != 0
        is_negative   = raw < 0
        return xp.where(
            is_negative & has_remainder,
            base + 1,
            base,
        ).astype("int64")

    else:
        raise ValueError(f"Unknown rounding mode: {mode!r}")


def _float_to_raw(
    values: NDArray[np.float64],
    fmt: Format,
    *,
    overflow: bool = True,
) -> NDArray[np.int64]:
    """Convert float64 array to int64 raw using fmt's rounding + overflow.

    Strategy:
      scaled = values * scale        (float64)
      apply floor to get integer
      apply rounding adjustment
      apply overflow

    .. note::
        For ``frac > 48``, float64's 53-bit mantissa may introduce rounding
        errors before quantization.  Use :func:`from_bits` for bit-exact
        initialization at very high fractional precision.
    """
    xp = get_xp()
    values = xp.asarray(values, dtype="float64")
    is_tracer = False
    if get_backend() == "jax":
        import jax  # noqa: PLC0415
        is_tracer = isinstance(values, jax.core.Tracer)
    if not is_tracer and bool(xp.any(xp.isnan(values))):
        raise ValueError("Cannot quantize NaN.")
    scaled = values * float(fmt.scale)
    if (
        not is_tracer
        and overflow
        and fmt.overflow == "wrap"
        and bool(xp.any(xp.isinf(scaled)))
    ):
        raise ValueError("Cannot quantize infinity with overflow='wrap'.")

    mode = fmt.rounding
    if mode == "trunc":
        raw = xp.floor(scaled)
    elif mode == "round":
        raw = xp.floor(scaled + 0.5)
    elif mode == "round_half_even":
        # Use the backend's built-in banker's rounding on float, then convert
        raw = xp.round(scaled)
    elif mode == "ceil":
        raw = xp.ceil(scaled)
    elif mode == "round_to_zero":
        raw = xp.fix(scaled)  # fix() truncates toward zero
    else:
        raise ValueError(f"Unknown rounding mode: {mode!r}")

    if overflow:
        if fmt.overflow == "wrap":
            raw = xp.mod(raw, fmt._wrap_offset).astype("int64")
            return _apply_overflow(
                raw,
                fmt._lo, fmt._hi, fmt._mask, fmt._wrap_offset,
                True,
                fmt.signed,
            )
    hi_float = float(fmt._hi)
    safe_hi = math.nextafter(hi_float, -math.inf) if hi_float > fmt._hi else hi_float
    saturated = xp.clip(raw, fmt._lo, safe_hi).astype("int64")
    return xp.where(raw > safe_hi, fmt._hi, saturated).astype("int64")


def _format_bits(bits: NDArray[np.int64], prefix: str, width: int, spec: str) -> "str | list[Any]":
    """Return scalar or shape-preserving nested Python strings for bit patterns."""
    def format_value(value: Any) -> "str | list[Any]":
        if isinstance(value, list):
            return [format_value(item) for item in value]
        return f"{prefix}{int(value):0{width}{spec}}"

    return format_value(np.asarray(bits, dtype=np.int64).tolist())


def _requantize_raw(
    raw: NDArray[np.int64],
    *,
    src_frac: int,
    dst_fmt: Format,
) -> NDArray[np.int64]:
    """Convert raw values between formats without a float64 round-trip."""
    frac_diff = dst_fmt.frac - src_frac
    if frac_diff >= 0:
        shifted = (raw << frac_diff).astype("int64")  # Python int shift amount
    else:
        shifted = _apply_rounding_shift(raw, -frac_diff, dst_fmt.rounding)

    return _apply_overflow(
        shifted,
        dst_fmt._lo, dst_fmt._hi, dst_fmt._mask, dst_fmt._wrap_offset,
        dst_fmt.overflow == "wrap",
        dst_fmt.signed,
    )



# ===========================================================================
# Block 3 — NumBV class (skeleton, properties, reshape)
# ===========================================================================


class NumBV:
    """Fixed-point value — scalar or array.

    Do **not** instantiate directly. Use the module-level factory functions::

        import rpkbin.numbv as nbv

        fmt = nbv.Format(16, 12)
        a   = nbv.scalar(1.5, fmt=fmt)
        b   = nbv.array([0.5, 1.0], fmt=fmt)
        c   = nbv.zeros(fmt=fmt, shape=4)
        d   = nbv.from_bits(0xFFFF, fmt=fmt)
    """

    __slots__ = ("_raw", "_fmt")

    # Unhashable: has mutable _raw and custom __eq__.
    # Without this, Python 3 silently sets __hash__ = None when __eq__ is
    # defined, but we make it explicit for clarity.
    __hash__ = None  # type: ignore[assignment]

    # Block numpy ufuncs (np.add, np.multiply, ...) from silently casting
    # NumBV to float via __array__.  Users must use the nbv.* function API.
    __array_ufunc__ = None

    # ---- internal constructor (bypasses quantization) ----------------------

    @classmethod
    def _from_raw(cls, raw: "NDArray[np.int64] | np.int64 | int", fmt: Format) -> "NumBV":
        """Wrap a pre-validated int64 value into a NumBV without re-quantizing.

        Always stores a genuine ndarray in the current backend's format.
        ``np.asarray``-compatible for numpy; ``jnp.asarray``-compatible for JAX.
        """
        mark_instantiated()
        obj = object.__new__(cls)
        xp = get_xp()
        raw_arr = xp.asarray(raw, dtype="int64")
        object.__setattr__(obj, "_raw", raw_arr)
        object.__setattr__(obj, "_fmt", fmt)
        return obj

    def __init__(self, raw: NDArray[np.int64], fmt: Format) -> None:
        raise TypeError(
            "Do not instantiate NumBV directly. "
            "Use nbv.scalar(), nbv.array(), nbv.zeros(), nbv.full(), or nbv.from_bits()."
        )

    # ---- core properties --------------------------------------------------

    @property
    def fmt(self) -> Format:
        """The Format descriptor for this value."""
        return self._fmt

    @property
    def raw(self) -> NDArray[np.int64]:
        """Signed raw integer view (int64 ndarray)."""
        return self._raw

    @property
    def bits(self) -> NDArray[np.int64]:
        """Unsigned bit pattern view (mask applied)."""
        return (self._raw & self._fmt._mask).astype("int64")

    @property
    def val(self) -> NDArray[np.float64]:
        """Real-number representation (float64 ndarray)."""
        return self._raw.astype("float64") / float(self._fmt.scale)

    @property
    def shape(self) -> tuple[int, ...]:
        """Numpy-style shape: () for scalar, (n,) for 1-d array."""
        return self._raw.shape

    @property
    def ndim(self) -> int:
        """Number of dimensions."""
        return int(self._raw.ndim)

    @property
    def size(self) -> int:
        """Total number of elements."""
        return int(self._raw.size)

    @property
    def is_scalar(self) -> bool:
        """True if this is a scalar (0-d) NumBV."""
        return self._raw.ndim == 0

    @property
    def hex(self) -> "str | list[Any]":
        """Hex string, or nested ``list[str]`` matching an array's shape."""
        return _format_bits(self.bits, "0x", (self._fmt.width + 3) // 4, "X")

    @property
    def bin(self) -> "str | list[Any]":
        """Binary string, or nested ``list[str]`` matching an array's shape."""
        return _format_bits(self.bits, "0b", self._fmt.width, "b")

    # ---- reshape / copy ---------------------------------------------------

    def copy(self) -> "NumBV":
        """Return an independent copy."""
        return NumBV._from_raw(self._raw.copy(), self._fmt)

    def reshape(self, *shape: int) -> "NumBV":
        """Return a view with a new shape."""
        return NumBV._from_raw(self._raw.reshape(*shape), self._fmt)

    def transpose(self, *axes: int) -> "NumBV":
        """Permute dimensions."""
        if self._raw.ndim < 2:
            return self.copy()
        return NumBV._from_raw(self._raw.transpose(*axes), self._fmt)

    def flatten(self) -> "NumBV":
        """Return a contiguous flattened copy."""
        return NumBV._from_raw(self._raw.flatten(), self._fmt)

    # ---- indexing ---------------------------------------------------------

    def __getitem__(self, key: "int | slice") -> "NumBV":
        """Element access: ``arr[i]`` → scalar NumBV, ``arr[i:j]`` → array NumBV."""
        if self.is_scalar:
            raise TypeError(
                "NumBV scalar does not support indexing. Use .val for the float value."
            )
        return NumBV._from_raw(self._raw[key], self._fmt)

    def __setitem__(self, key: "int | slice", value: "NumBV | int | float") -> None:
        """Element assignment. Quantizes *value* to self's format."""
        if self.is_scalar:
            raise TypeError("Cannot index-assign to a scalar NumBV.")
        aligned = self._align_other(value)
        if get_backend() == "jax":
            # JAX arrays are immutable; use .at[].set() and replace _raw.
            object.__setattr__(self, "_raw", self._raw.at[key].set(aligned))
        else:
            self._raw[key] = aligned

    def __len__(self) -> int:
        if self.is_scalar:
            raise TypeError("len() of scalar NumBV")
        return len(self._raw)

    def __iter__(self) -> Iterator["NumBV"]:
        """Iterate over elements of an array NumBV, yielding scalar NumBVs.

        Enables ``for x in arr`` and ``list(arr)``.

        Raises
        ------
        TypeError
            If called on a scalar (0-d) NumBV.

        Example::

            for coeff in h:
                acc = mac(acc, x_i, coeff, acc_fmt=acc_fmt)
        """
        if self.is_scalar:
            raise TypeError(
                "Cannot iterate over a scalar NumBV. "
                "Use .val for the float value, or call nbv.array() to create an array."
            )
        for i in range(len(self._raw)):
            yield NumBV._from_raw(self._raw[i], self._fmt)

    # ---- numpy interop (fallback) -----------------------------------------

    def __array__(self, dtype=None, copy=None) -> NDArray:
        """Allow numpy to consume NumBV as a float array (read-only view).

        The ``copy`` parameter is accepted for NumPy 2.x compatibility
        (passed when ``__array_ufunc__ = None`` is set).

        Notes
        -----
        We must wrap the result in ``np.asarray()`` because dividing a 0-d
        ndarray by a float returns a numpy scalar, not an ndarray.
        """
        raw_f = np.asarray(self._raw, dtype=np.float64)
        v = np.asarray(raw_f / float(self._fmt.scale))  # always ndarray
        if dtype is not None:
            v = v.astype(dtype)
        if copy:
            return v.copy()
        return v

    # ---- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize this NumBV to a plain Python dict.

        The dict can be round-tripped via :func:`from_dict`.

        Returns
        -------
        dict
            Keys: ``"raw"`` (Python int or list of ints),
            ``"fmt"`` (dict of Format fields).
        """
        raw_val = self._raw
        # Convert to numpy first (handles JAX arrays transparently)
        raw_np = np.asarray(raw_val, dtype=np.int64)
        return {
            "raw": raw_np.tolist(),
            "fmt": {
                "width":    self._fmt.width,
                "frac":     self._fmt.frac,
                "signed":   self._fmt.signed,
                "rounding": self._fmt.rounding,
                "overflow": self._fmt.overflow,
            },
        }

    def to_json(self) -> str:
        """Serialize this NumBV to a JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> "NumBV":
        """Restore a NumBV from a dict previously returned by :meth:`to_dict`."""
        fmt = Format(
            d["fmt"]["width"],
            d["fmt"]["frac"],
            signed=d["fmt"]["signed"],
            rounding=d["fmt"]["rounding"],
            overflow=d["fmt"]["overflow"],
        )
        raw = d["raw"]
        return from_bits(raw, fmt=fmt)

    @classmethod
    def from_json(cls, s: str) -> "NumBV":
        """Restore a NumBV from a JSON string previously returned by :meth:`to_json`."""
        return cls.from_dict(json.loads(s))


    def report(self) -> str:
        """Return a human-readable debug summary string."""
        fmt = self._fmt
        lines = [
            f"Q-Format  : Q{fmt.int_bits}.{fmt.frac} ({'Signed' if fmt.signed else 'Unsigned'})",
            f"Range     : [{fmt.min_val}, {fmt.max_val}]",
            f"Precision : {fmt.precision}",
            f"Overflow  : {fmt.overflow}",
            f"Rounding  : {fmt.rounding}",
        ]
        if self.is_scalar:
            lines.insert(0, f"Value     : {self.val}")
            lines.insert(1, f"Bits      : {self.hex} ({self.bin})")
        else:
            lines.insert(0, f"Shape     : {self.shape}")
            lines.insert(1, f"Values    : {self.val}")
        return "\n".join(lines)

    # ---- dunder helpers ---------------------------------------------------

    def __float__(self) -> float:
        if not self.is_scalar:
            raise TypeError("Cannot convert array NumBV to float. Use .val instead.")
        return float(self.val)

    def __int__(self) -> int:
        if not self.is_scalar:
            raise TypeError("Cannot convert array NumBV to int. Use .raw instead.")
        return int(self._raw)

    def __bool__(self) -> bool:
        if not self.is_scalar:
            raise TypeError("Cannot convert array NumBV to bool.")
        return bool(self._raw != 0)

    def __repr__(self) -> str:
        s = "s" if self._fmt.signed else "u"
        if self.is_scalar:
            return f"NumBV(w={self._fmt.width}, f={self._fmt.frac}, {s}, val={float(self.val):.6g})"
        return f"NumBV(w={self._fmt.width}, f={self._fmt.frac}, {s}, shape={self.shape})"

    def __format__(self, spec: str) -> str:
        if self.is_scalar:
            if spec in ("x", "X", "hex"):
                return str(self.hex)
            if spec in ("b", "bin"):
                return str(self.bin)
        return format(float(self.val) if self.is_scalar else self.val, spec)


# ===========================================================================
# Block 4 — Format conversion (quantize, reinterpret, clip, with_bits)
# ===========================================================================


    def quantize(self, fmt: Format) -> "NumBV":
        """Convert to a new format, preserving real-number value.

        Applies *fmt*'s rounding and overflow policy.
        This is the canonical way to change format in a pipeline.
        """
        raw = _requantize_raw(self._raw, src_frac=self._fmt.frac, dst_fmt=fmt)
        return NumBV._from_raw(raw, fmt)

    def reinterpret(
        self,
        width: "int | None" = None,
        *,
        frac:  "int | None" = None,
        signed: "bool | None" = None,
    ) -> "NumBV":
        """Reinterpret bit pattern with a different format.

        Bits are preserved (subject to width change rules), not values.
        No rounding or overflow is applied.

        Width change rules
        ------------------
        Narrowing (new < old) : take low *new_width* bits (high bits discarded).
        Widening + source signed   : sign-extend from source MSB.
        Widening + source unsigned : zero-extend (high bits = 0).

        When both *width* and *signed* are changed, the extension is performed
        first using the **source** format's signedness, then the new *signed*
        interpretation is applied.
        """
        new_w = width  if width  is not None else self._fmt.width
        new_f = frac   if frac   is not None else self._fmt.frac
        new_s = signed if signed is not None else self._fmt.signed

        if new_w <= 0:
            raise ValueError(f"width must be > 0, got {new_w}")
        if new_w > 63:
            raise ValueError(f"width must be ≤ 63, got {new_w}")
        if new_f < 0:
            raise ValueError(f"frac must be ≥ 0, got {new_f}")

        bits = self.bits  # unsigned, int64 ndarray
        xp   = get_xp()
        old_w = self._fmt.width

        if new_w < old_w:
            # Narrowing: take low new_w bits (Python int mask)
            bits = (bits & ((1 << new_w) - 1)).astype("int64")

        elif new_w > old_w:
            # Widening: extend based on SOURCE signedness
            if self._fmt.signed:
                # Sign-extend: check MSB of source (Python int shift amounts)
                sign_bit = (bits >> (old_w - 1)) & 1
                ext_len  = new_w - old_w
                ext_mask = ((1 << ext_len) - 1) << old_w  # Python int
                bits = bits | xp.where(
                    sign_bit.astype(bool),
                    ext_mask,
                    0,
                )
                bits = bits.astype("int64")
            # else: unsigned → zero-extend, high bits already 0 in int64

        # Reinterpret bits under new_s
        new_fmt = Format(new_w, new_f, new_s, self._fmt.rounding, self._fmt.overflow)
        raw = _bits_to_raw(bits, new_fmt)
        return NumBV._from_raw(raw, new_fmt)

    def clip(self, lo: float, hi: float) -> "NumBV":
        """Clamp real-number value to [lo, hi]. Format is unchanged.

        *lo* and *hi* are specified in the real-number domain.
        Internally converts to raw bounds then uses clip.
        """
        if lo > hi:
            raise ValueError(f"clip(): lo ({lo}) must be <= hi ({hi})")
        xp     = get_xp()
        fmt    = self._fmt
        lo_raw = _float_to_raw(xp.asarray(lo, dtype="float64"), fmt, overflow=False)
        hi_raw = _float_to_raw(xp.asarray(hi, dtype="float64"), fmt, overflow=False)
        clipped = xp.clip(self._raw, lo_raw, hi_raw).astype("int64")
        return NumBV._from_raw(clipped, fmt)

    def with_bits(self, new_bits: "int | NDArray[np.int64]") -> "NumBV":
        """Return a new NumBV with the given bit pattern, keeping self's format.

        This is the one-liner for bitwise operations::

            y = x.with_bits(x.bits & 0xFF00)   # AND mask
            y = x.with_bits(x.bits | other.bits)  # OR

        Equivalent to ``from_bits(new_bits, fmt=self.fmt)``.
        No quantization or overflow policy is applied.
        """
        return from_bits(new_bits, fmt=self._fmt)


def _bits_to_raw(
    bits: NDArray[np.int64],
    fmt: Format,
) -> NDArray[np.int64]:
    """Interpret unsigned bit pattern as signed two's complement if fmt.signed."""
    xp   = get_xp()
    bits = (bits & fmt._mask).astype("int64")  # Python int mask
    if fmt.signed:
        sign_offset = fmt._hi + 1
        bits = xp.where(
            bits > fmt._hi,
            bits - sign_offset - sign_offset,
            bits,
        ).astype("int64")
    return bits


# ===========================================================================
# Block 5 — Operator layer
# ===========================================================================


def _check_signedness(a: NumBV, b: NumBV, op: str) -> None:
    """Runtime signedness guard. Raises TypeError if mismatched."""
    if a._fmt.signed != b._fmt.signed:
        a_s = "signed" if a._fmt.signed else "unsigned"
        b_s = "signed" if b._fmt.signed else "unsigned"
        raise TypeError(
            f"Cannot {op} {a_s} and {b_s} NumBV. "
            "Use .reinterpret(signed=...) or .quantize(fmt) to align signedness first."
        )


def _align_other_raw(
    self_fmt: Format,
    other: "NumBV | int | float",
) -> NDArray[np.int64]:
    """Return *other* as a raw int64 aligned to *self_fmt*'s frac.

    - NumBV with same frac  : return raw directly.
    - NumBV with diff frac  : shift to align.
    - int / float           : quantize to self_fmt.
    """
    if isinstance(other, NumBV):
        if other._fmt.frac == self_fmt.frac:
            return other._raw
        # Align frac via shift (Python int shift amount — works for both backends)
        frac_diff = self_fmt.frac - other._fmt.frac
        if frac_diff > 0:
            return (other._raw << frac_diff).astype("int64")
        else:
            return _apply_rounding_shift(
                other._raw, -frac_diff, self_fmt.rounding
            )
    # scalar constant: quantize via the active backend
    return _float_to_raw(
        get_xp().asarray(other, dtype="float64"), self_fmt
    )


# Attach _align_other as a method (avoids class-level definition ordering issues)
def _nbv_align_other(self: NumBV, other: "NumBV | int | float") -> NDArray[np.int64]:
    if isinstance(other, NumBV):
        _check_signedness(self, other, "add/sub/mul")
    return _align_other_raw(self._fmt, other)


NumBV._align_other = _nbv_align_other  # type: ignore[attr-defined]


def _add_sub_op(
    a: NumBV,
    b: "NumBV | int | float",
    subtract: bool,
) -> NumBV:
    """Core add/sub — result quantized back to a.fmt."""
    b_raw = a._align_other(b)  # type: ignore[attr-defined]
    if subtract:
        intermediate = a._raw - b_raw
    else:
        intermediate = a._raw + b_raw
    result = _apply_overflow(
        intermediate,
        a._fmt._lo, a._fmt._hi, a._fmt._mask, a._fmt._wrap_offset,
        a._fmt.overflow == "wrap",
        a._fmt.signed,
    )
    return NumBV._from_raw(result, a._fmt)


def _mul_op(a: NumBV, b: "NumBV | int | float") -> NumBV:
    """Mul operator — result quantized back to a.fmt.

    Internally:
      1. Align b to a.frac (so both raws have frac = a.frac).
      2. product frac = a.frac + a.frac = 2 * a.frac.
      3. Right-shift by a.frac to bring frac back to a.frac.
      4. Apply a.fmt rounding + overflow.
    """
    if isinstance(b, NumBV):
        if a._fmt.width + b._fmt.width > 63:
            raise OverflowError(
                f"Multiplication of Q{a._fmt.width} × Q{b._fmt.width} would require "
                f"{a._fmt.width + b._fmt.width} bits — exceeds int64 limit (63). "
                "Use narrower formats."
            )
    b_raw    = a._align_other(b)  # type: ignore[attr-defined]  — frac aligned to a.frac
    product  = a._raw * b_raw     # frac = 2 * a.frac
    shifted  = _apply_rounding_shift(product, a._fmt.frac, a._fmt.rounding)
    result   = _apply_overflow(
        shifted,
        a._fmt._lo, a._fmt._hi, a._fmt._mask, a._fmt._wrap_offset,
        a._fmt.overflow == "wrap",
        a._fmt.signed,
    )
    return NumBV._from_raw(result, a._fmt)


# Inject arithmetic operators into NumBV
def _make_add(subtract: bool):
    def op(self, other):
        return _add_sub_op(self, other, subtract)
    return op

def _make_radd(subtract: bool):
    def rop(self, other):
        # other + self (or other - self)
        if subtract:
            # other - self: create a temporary with same fmt
            tmp_raw = _align_other_raw(self._fmt, other) - self._raw
        else:
            tmp_raw = _align_other_raw(self._fmt, other) + self._raw
        result = _apply_overflow(
            tmp_raw,
            self._fmt._lo, self._fmt._hi, self._fmt._mask, self._fmt._wrap_offset,
            self._fmt.overflow == "wrap", self._fmt.signed,
        )
        return NumBV._from_raw(result, self._fmt)
    return rop


def _mul_dunder(self: NumBV, other: "NumBV | int | float") -> NumBV:
    return _mul_op(self, other)

NumBV.__add__  = _make_add(False)       # type: ignore[assignment]
NumBV.__sub__  = _make_add(True)        # type: ignore[assignment]
NumBV.__radd__ = _make_radd(False)      # type: ignore[assignment]
NumBV.__rsub__ = _make_radd(True)       # type: ignore[assignment]
NumBV.__mul__  = _mul_dunder            # type: ignore[assignment]
NumBV.__rmul__ = _mul_dunder            # type: ignore[assignment]

# In-place operators
# Use object.__setattr__ to replace _raw entirely — this works for both
# 0-d (scalar) and 1-d (array) NumBV, avoiding item-assignment errors on
# numpy scalars that masquerade as 0-d arrays.
def _make_iadd(subtract: bool):
    def iop(self, other):
        result = _add_sub_op(self, other, subtract)
        object.__setattr__(self, "_raw", result._raw)
        return self
    return iop

def _make_imul():
    def imul(self, other):
        result = _mul_op(self, other)
        object.__setattr__(self, "_raw", result._raw)
        return self
    return imul

NumBV.__iadd__ = _make_iadd(False)  # type: ignore[assignment]
NumBV.__isub__ = _make_iadd(True)   # type: ignore[assignment]
NumBV.__imul__ = _make_imul()       # type: ignore[assignment]

# Unary
def _neg(self: NumBV) -> NumBV:
    r = _apply_overflow(
        -self._raw,
        self._fmt._lo, self._fmt._hi, self._fmt._mask, self._fmt._wrap_offset,
        self._fmt.overflow == "wrap", self._fmt.signed,
    )
    return NumBV._from_raw(r, self._fmt)

def _abs_(self: NumBV) -> NumBV:
    xp = get_xp()
    r = _apply_overflow(
        xp.abs(self._raw),
        self._fmt._lo, self._fmt._hi, self._fmt._mask, self._fmt._wrap_offset,
        self._fmt.overflow == "wrap", self._fmt.signed,
    )
    return NumBV._from_raw(r, self._fmt)

NumBV.__neg__ = _neg   # type: ignore[assignment]
NumBV.__abs__ = _abs_  # type: ignore[assignment]


# ===========================================================================
# Block 6 — Comparison operators
# ===========================================================================


def _cmp_align(
    a: NumBV,
    b: "NumBV | int | float",
) -> "tuple[NDArray[np.int64], NDArray[np.int64]]":
    """Align a and b to same frac for comparison, checking signedness."""
    if isinstance(b, NumBV):
        _check_signedness(a, b, "compare")
        max_frac = max(a._fmt.frac, b._fmt.frac)
        a_aligned = (a._raw << (max_frac - a._fmt.frac)) if max_frac > a._fmt.frac else a._raw
        b_aligned = (b._raw << (max_frac - b._fmt.frac)) if max_frac > b._fmt.frac else b._raw
        return a_aligned.astype("int64"), b_aligned.astype("int64")
    raise TypeError


def _scalar_or_arr(result: NDArray) -> "bool | NDArray":
    if result.ndim == 0:
        return bool(result)
    return result


def _cmp_op(a: NumBV, b: "NumBV | int | float", op_name: str) -> "bool | NDArray":
    """Apply a comparison using the current backend's function."""
    xp = get_xp()
    fn = getattr(xp, op_name)
    if isinstance(b, NumBV):
        return _scalar_or_arr(fn(*_cmp_align(a, b)))
    if isinstance(b, (int, np.integer)):
        return _cmp_raw(a, int(b) * a._fmt.scale, op_name)
    if isinstance(b, (float, np.floating)):
        value = float(b)
        if math.isnan(value):
            return _scalar_or_arr(xp.full(a.shape, op_name == "not_equal", dtype=bool))
        if math.isinf(value):
            result = (
                op_name in ("less", "less_equal", "not_equal")
                if value > 0
                else op_name in ("greater", "greater_equal", "not_equal")
            )
            return _scalar_or_arr(xp.full(a.shape, result, dtype=bool))
        numerator, denominator = value.as_integer_ratio()
        raw, remainder = divmod(numerator * a._fmt.scale, denominator)
        if op_name == "equal":
            return _scalar_or_arr(xp.full(a.shape, False, dtype=bool)) if remainder else _cmp_raw(a, raw, op_name)
        if op_name == "not_equal":
            return _scalar_or_arr(xp.full(a.shape, True, dtype=bool)) if remainder else _cmp_raw(a, raw, op_name)
        if op_name == "less":
            return _cmp_raw(a, raw, "less" if remainder == 0 else "less_equal")
        if op_name == "less_equal":
            return _cmp_raw(a, raw, "less_equal")
        if op_name == "greater":
            return _cmp_raw(a, raw, "greater")
        return _cmp_raw(a, raw, "greater_equal" if remainder == 0 else "greater")
    raise TypeError


def _cmp_raw(a: NumBV, raw: int, op_name: str) -> "bool | NDArray":
    """Compare raw values without narrowing a Python integer to int64."""
    xp = get_xp()
    if raw < a._fmt.min_raw:
        result = op_name in ("greater", "greater_equal", "not_equal")
    elif raw > a._fmt.max_raw:
        result = op_name in ("less", "less_equal", "not_equal")
    else:
        return _scalar_or_arr(getattr(xp, op_name)(a._raw, raw))
    return _scalar_or_arr(xp.full(a.shape, result, dtype=bool))


def _is_cmp_operand(value: object) -> bool:
    return isinstance(value, (NumBV, int, np.integer, float, np.floating))


def _eq_dunder(self: NumBV, other: object) -> "bool | NDArray":
    if not _is_cmp_operand(other):
        return NotImplemented
    return _cmp_op(self, other, "equal")  # type: ignore[arg-type]


def _ne_dunder(self: NumBV, other: object) -> "bool | NDArray":
    if not _is_cmp_operand(other):
        return NotImplemented
    return _cmp_op(self, other, "not_equal")  # type: ignore[arg-type]


def _lt_dunder(self: NumBV, other: object) -> "bool | NDArray":
    if not _is_cmp_operand(other):
        return NotImplemented
    return _cmp_op(self, other, "less")  # type: ignore[arg-type]


def _le_dunder(self: NumBV, other: object) -> "bool | NDArray":
    if not _is_cmp_operand(other):
        return NotImplemented
    return _cmp_op(self, other, "less_equal")  # type: ignore[arg-type]


def _gt_dunder(self: NumBV, other: object) -> "bool | NDArray":
    if not _is_cmp_operand(other):
        return NotImplemented
    return _cmp_op(self, other, "greater")  # type: ignore[arg-type]


def _ge_dunder(self: NumBV, other: object) -> "bool | NDArray":
    if not _is_cmp_operand(other):
        return NotImplemented
    return _cmp_op(self, other, "greater_equal")  # type: ignore[arg-type]


NumBV.__eq__ = _eq_dunder   # type: ignore[assignment]
NumBV.__ne__ = _ne_dunder   # type: ignore[assignment]
NumBV.__lt__ = _lt_dunder   # type: ignore[assignment]
NumBV.__le__ = _le_dunder   # type: ignore[assignment]
NumBV.__gt__ = _gt_dunder   # type: ignore[assignment]
NumBV.__ge__ = _ge_dunder   # type: ignore[assignment]


# ===========================================================================
# Block 7 — Factory functions
# ===========================================================================


def scalar(value: "int | float", *, fmt: Format) -> NumBV:
    """Create a scalar NumBV from a real value.

    .. note::
        For ``frac > 48``, float64 precision may affect initialization.
        Use :func:`from_bits` for bit-exact construction at very high
        fractional precision.

    Examples
    --------
    >>> a = scalar(1.5, fmt=Format(16, 12))
    """
    xp  = get_xp()
    raw = _float_to_raw(xp.asarray(value, dtype="float64"), fmt)
    if raw.ndim != 0:
        raise ValueError("scalar() requires a scalar input; use array() for array inputs.")
    return NumBV._from_raw(raw, fmt)


def array(data: "ArrayLike", *, fmt: Format) -> NumBV:
    """Create an array NumBV from a list or ndarray of real values.

    .. note::
        For ``frac > 48``, float64 precision may affect initialization.
        Use :func:`from_bits` for bit-exact construction at very high
        fractional precision.

    Examples
    --------
    >>> a = array([0.5, 1.0, 1.5], fmt=Format(16, 12))
    >>> b = array(np.linspace(0, 1, 512), fmt=Format(16, 12))
    """
    xp  = get_xp()
    raw = _float_to_raw(xp.asarray(data, dtype="float64"), fmt)
    if raw.ndim == 0:
        raise ValueError("array() requires an array input; use scalar() for scalar inputs.")
    return NumBV._from_raw(raw, fmt)


def zeros(*, fmt: Format, shape: "int | tuple | None" = None) -> NumBV:
    """Create a NumBV filled with zeros.

    Examples
    --------
    >>> fmt = Format(16, 12)
    >>> a = zeros(fmt=fmt)            # scalar
    >>> b = zeros(fmt=fmt, shape=64)  # array
    """
    xp = get_xp()
    if shape is None:
        raw = xp.zeros((), dtype="int64")
    else:
        raw = xp.zeros(shape, dtype="int64")
    return NumBV._from_raw(raw, fmt)


def ones(*, fmt: Format, shape: "int | tuple | None" = None) -> NumBV:
    """Create a NumBV filled with quantized 1.0.

    Examples
    --------
    >>> a = ones(fmt=Format(16, 12))
    """
    return full(1.0, fmt=fmt, shape=shape)


def full(
    fill_value: "int | float",
    *,
    fmt: Format,
    shape: "int | tuple | None" = None,
) -> NumBV:
    """Create a NumBV filled with *fill_value*.

    Examples
    --------
    >>> a = full(0.5, fmt=Format(16, 12), shape=256)
    """
    xp       = get_xp()
    fill_raw = _float_to_raw(xp.asarray(fill_value, dtype="float64"), fmt)
    if shape is None:
        raw = fill_raw.reshape(())
    else:
        raw = xp.full(shape, int(fill_raw), dtype="int64")
    return NumBV._from_raw(raw, fmt)


def from_bits(
    bits: "int | ArrayLike",
    *,
    fmt: Format,
    strict: bool = False,
) -> NumBV:
    """Create a NumBV from raw unsigned bit pattern(s).

    No quantization or rounding is applied; bits are reinterpreted.

    Parameters
    ----------
    bits : int or array_like
        The raw (unsigned) bits to import.
    fmt : Format
        The format to interpret these bits as.
    strict : bool, default False
        If True, raises ValueError if the inputs exceed the format's capacity.
        If False, silently masks off high bits.

    Examples
    --------
    >>> a = from_bits(0xFF00, fmt=Format(16, 8, signed=True))
    >>> # raw = -256, val = -1.0
    """
    xp = get_xp()

    if strict:
        if isinstance(bits, int):
            if bits < 0 or bits >= (1 << fmt.width):
                raise ValueError(
                    f"from_bits(strict=True): input bit pattern {hex(bits)} "
                    f"exceeds format width {fmt.width}. Use strict=False to silently mask."
                )
        else:
            arr_test = np.asarray(bits, dtype=np.int64)
            if np.any(arr_test < 0) or np.any(arr_test >= (1 << fmt.width)):
                raise ValueError(
                    f"from_bits(strict=True): array contains bit patterns "
                    f"exceeding format width {fmt.width}. Use strict=False to silently mask."
                )

    if isinstance(bits, (int, np.integer)):
        arr = xp.array(int(bits), dtype="int64").reshape(())
    else:
        arr = xp.asarray(bits, dtype="int64")
        
    raw = _bits_to_raw(arr, fmt)
    return NumBV._from_raw(raw, fmt)


# ===========================================================================
# Block 8 — Function-level API (pipeline-explicit)
# ===========================================================================


def add(a: NumBV, b: NumBV, *, out_fmt: Format) -> NumBV:
    """Add *a* and *b*, quantizing result to *out_fmt*.

    *out_fmt* is required. For convenience (auto-quantize to a.fmt), use
    the operator: ``a + b``.
    """
    _check_signedness(a, b, "add")
    max_frac = max(a._fmt.frac, b._fmt.frac)
    a_raw = (a._raw << (max_frac - a._fmt.frac)).astype("int64") if max_frac > a._fmt.frac else a._raw
    b_raw = (b._raw << (max_frac - b._fmt.frac)).astype("int64") if max_frac > b._fmt.frac else b._raw
    intermediate_raw = a_raw + b_raw
    result_raw = _requantize_raw(intermediate_raw, src_frac=max_frac, dst_fmt=out_fmt)
    return NumBV._from_raw(result_raw, out_fmt)


def sub(a: NumBV, b: NumBV, *, out_fmt: Format) -> NumBV:
    """Subtract *b* from *a*, quantizing result to *out_fmt*.

    *out_fmt* is required. For convenience, use ``a - b``.
    """
    _check_signedness(a, b, "sub")
    max_frac = max(a._fmt.frac, b._fmt.frac)
    a_raw = (a._raw << (max_frac - a._fmt.frac)).astype("int64") if max_frac > a._fmt.frac else a._raw
    b_raw = (b._raw << (max_frac - b._fmt.frac)).astype("int64") if max_frac > b._fmt.frac else b._raw
    intermediate_raw = a_raw - b_raw
    result_raw = _requantize_raw(intermediate_raw, src_frac=max_frac, dst_fmt=out_fmt)
    return NumBV._from_raw(result_raw, out_fmt)


def mul(
    a: NumBV,
    b: NumBV,
    *,
    out_fmt: "Format | None" = None,
) -> NumBV:
    """Multiply *a* and *b*.

    If *out_fmt* is omitted, returns a full-precision result:
      - frac  = a.frac + b.frac
      - width = a.width + b.width
      - signed = signed if either input is signed, else unsigned

    If *out_fmt* is provided, full-precision product is first computed then
    quantized to *out_fmt*.

    Examples
    --------
    >>> fmt = Format(16, 12)
    >>> a, b = scalar(0.5, fmt=fmt), scalar(0.25, fmt=fmt)
    >>> p = mul(a, b)                   # full-precision
    >>> y = mul(a, b, out_fmt=fmt)      # full-precision → quantize
    """
    total_w = a._fmt.width + b._fmt.width
    if total_w > 63:
        raise OverflowError(
            f"mul(): {a._fmt.width} + {b._fmt.width} = {total_w} bits > 63 (int64 limit)."
        )
    is_signed = a._fmt.signed or b._fmt.signed
    fp_frac   = a._fmt.frac + b._fmt.frac
    fp_width  = total_w

    product_raw = a._raw * b._raw  # int64 × int64 = int64 (assuming fits)

    if out_fmt is None:
        fp_fmt = Format(fp_width, fp_frac, is_signed)
        # product_raw is already in raw form for fp_fmt — no overflow expected
        # (since widths are designed not to overflow)
        return NumBV._from_raw(product_raw.astype("int64"), fp_fmt)
    else:
        result_raw = _requantize_raw(product_raw.astype("int64"), src_frac=fp_frac, dst_fmt=out_fmt)
        return NumBV._from_raw(result_raw, out_fmt)


def neg(a: NumBV, *, out_fmt: "Format | None" = None) -> NumBV:
    """Negate *a*.

    If *out_fmt* is omitted, result format = *a.fmt* (same as ``-a``).
    """
    if out_fmt is None:
        return -a
    neg_raw = (-a._raw).astype("int64")
    result_raw = _requantize_raw(neg_raw, src_frac=a._fmt.frac, dst_fmt=out_fmt)
    return NumBV._from_raw(result_raw, out_fmt)


# ===========================================================================
# Block 9 — Reduction / DSP helpers
# ===========================================================================


def sum(x: NumBV, *, acc_fmt: Format) -> NumBV:  # noqa: A001
    """Sequential left-to-right sum with per-step quantization to *acc_fmt*.

    Each addition result is immediately quantized to *acc_fmt* before the
    next step. This exactly models a fixed-size accumulator register.

    For array inputs, elements are consumed in flattened C-order, matching
    ``numpy.reshape(-1)``.

    Examples
    --------
    >>> products = array([0.25, 0.5], fmt=Format(16, 12))
    >>> total = sum(products, acc_fmt=Format(32, 22))
    """
    if x.is_scalar:
        return x.quantize(acc_fmt)

    xp = get_xp()
    flat_raw = x._raw.reshape(-1)
    acc_raw = xp.zeros((), dtype="int64")
    for i in range(flat_raw.size):
        elem_raw = xp.asarray(flat_raw[i], dtype="int64").reshape(())
        elem_q = _requantize_raw(elem_raw, src_frac=x._fmt.frac, dst_fmt=acc_fmt)
        acc_raw = _apply_overflow(
            acc_raw + elem_q,
            acc_fmt._lo,
            acc_fmt._hi,
            acc_fmt._mask,
            acc_fmt._wrap_offset,
            acc_fmt.overflow == "wrap",
            acc_fmt.signed,
        )
    return NumBV._from_raw(acc_raw, acc_fmt)


def dot(
    a: NumBV,
    b: NumBV,
    *,
    acc_fmt: Format,
    out_fmt: "Format | None" = None,
) -> NumBV:
    """Dot product with explicit accumulator format.

    Procedure for each element pair (aᵢ, bᵢ):
      1. Full-precision multiply: pᵢ = mul(aᵢ, bᵢ).
      2. Quantize pᵢ to *acc_fmt*.
      3. Accumulate (quantize after each add).

    If *out_fmt* is provided, final result is quantized to *out_fmt*.

    Examples
    --------
    >>> fmt = Format(16, 12)
    >>> x, h = array([0.5], fmt=fmt), array([0.25], fmt=fmt)
    >>> y = dot(x, h, acc_fmt=Format(32, 22), out_fmt=fmt)
    """
    if a.size != b.size:
        raise ValueError(f"dot(): length mismatch: {a.size} vs {b.size}")

    xp = get_xp()
    a_flat = a._raw.reshape(-1)
    b_flat = b._raw.reshape(-1)
    prod_frac = a._fmt.frac + b._fmt.frac

    acc_raw = xp.zeros((), dtype="int64")
    for i in range(a_flat.size):
        ai_raw = xp.asarray(a_flat[i], dtype="int64").reshape(())
        bi_raw = xp.asarray(b_flat[i], dtype="int64").reshape(())
        prod_raw = (ai_raw * bi_raw).astype("int64")
        prod_q = _requantize_raw(prod_raw, src_frac=prod_frac, dst_fmt=acc_fmt)
        acc_raw = _apply_overflow(
            acc_raw + prod_q,
            acc_fmt._lo,
            acc_fmt._hi,
            acc_fmt._mask,
            acc_fmt._wrap_offset,
            acc_fmt.overflow == "wrap",
            acc_fmt.signed,
        )

    acc = NumBV._from_raw(acc_raw, acc_fmt)
    if out_fmt is not None:
        return acc.quantize(out_fmt)
    return acc


def mac(
    acc: NumBV,
    a: NumBV,
    b: NumBV,
    *,
    acc_fmt: Format,
) -> NumBV:
    """Multiply-Accumulate: ``acc + quantize(a * b, acc_fmt)``, result in *acc_fmt*.

    Equivalent to::

        add(acc, mul(a, b, out_fmt=acc_fmt), out_fmt=acc_fmt)

    Examples
    --------
    >>> fmt, acc_fmt = Format(16, 12), Format(32, 22)
    >>> acc = zeros(fmt=acc_fmt)
    >>> acc = mac(acc, scalar(0.5, fmt=fmt), scalar(0.25, fmt=fmt), acc_fmt=acc_fmt)
    """
    product = mul(a, b, out_fmt=acc_fmt)
    return add(acc, product, out_fmt=acc_fmt)


# ===========================================================================
# Block 10 — Bit operations (get_bits, set_bits)
# ===========================================================================


def _nbv_get_bits(
    self: NumBV,
    high: int,
    low: int,
    *,
    fmt: "Format | None" = None,
) -> "int | NDArray[np.int64] | NumBV":
    """Extract bits [high:low] (inclusive, 0-based from LSB).

    Returns
    -------
    int or ndarray
        If *fmt* is None.
    NumBV
        If *fmt* is provided (equivalent to ``from_bits(result, fmt=fmt)``).
    """
    if low < 0:
        raise ValueError(f"low bit {low} must be >= 0")
    if high < low:
        raise ValueError(f"high ({high}) must be ≥ low ({low}) (Hardware convention: [MSB:LSB])")
    if high >= self._fmt.width:
        raise ValueError(f"high bit {high} ≥ width {self._fmt.width}")
    w      = high - low + 1
    mask   = (1 << w) - 1              # Python int
    result = (self.bits >> low) & mask  # Python int shift and mask on array

    if fmt is not None:
        return from_bits(result, fmt=fmt)
    if self.is_scalar:
        return int(result)
    return result.astype("int64")


def _nbv_set_bits(
    self: NumBV,
    high: int,
    low: int,
    value: "int | NDArray[np.int64]",
) -> None:
    """Write *value* into bits [high:low] (inclusive). In-place."""
    if low < 0:
        raise ValueError(f"low bit {low} must be >= 0")
    if high < low:
        raise ValueError(f"high ({high}) must be ≥ low ({low}) (Hardware convention: [MSB:LSB])")
    if high >= self._fmt.width:
        raise ValueError(f"high bit {high} ≥ width {self._fmt.width}")
    xp         = get_xp()
    w          = high - low + 1
    field_mask = (1 << w) - 1                                # Python int
    clear_mask = self._fmt._mask & ~(field_mask << low)      # Python int
    val_arr    = xp.asarray(value, dtype="int64")
    new_bits   = (self.bits & clear_mask) | ((val_arr & field_mask) << low)
    new_raw    = _bits_to_raw(new_bits, self._fmt)
    if get_backend() == "jax":
        object.__setattr__(self, "_raw", new_raw)
    else:
        self._raw[...] = new_raw


NumBV.get_bits = _nbv_get_bits  # type: ignore[attr-defined]
NumBV.set_bits = _nbv_set_bits  # type: ignore[attr-defined]


# ===========================================================================
# Block 11 — Format inference helpers
# ===========================================================================


def infer_add_format(
    fmt_a: Format,
    fmt_b: Format,
    *,
    rounding: RoundingMode = "trunc",
    overflow: OverflowMode = "saturate",
) -> Format:
    """Return the smallest Format that can hold the sum of *fmt_a* and *fmt_b*
    without precision loss.

    Formula
    -------
    frac      = max(a.frac, b.frac)
    int_bits  = max(a.int_bits, b.int_bits) + 1   (+1 for carry)
    signed    = a.signed (both must match)
    width     = int_bits + frac + signed_bit
    """
    if fmt_a.signed != fmt_b.signed:
        raise TypeError(
            "infer_add_format: both formats must have the same signedness. "
            f"Got {fmt_a.signed} and {fmt_b.signed}."
        )
    signed     = fmt_a.signed
    signed_bit = 1 if signed else 0
    frac       = max(fmt_a.frac, fmt_b.frac)
    int_bits   = max(fmt_a.int_bits, fmt_b.int_bits) + 1
    width      = int_bits + frac + signed_bit
    if width > 63:
        raise OverflowError(
            f"infer_add_format: result would require width={width} > 63."
        )
    return Format(width, frac, signed, rounding, overflow)


def infer_mul_format(
    fmt_a: Format,
    fmt_b: Format,
    *,
    rounding: RoundingMode = "trunc",
    overflow: OverflowMode = "saturate",
) -> Format:
    """Return the Format for the full-precision product of *fmt_a* × *fmt_b*.

    Formula
    -------
    frac   = a.frac + b.frac
    width  = a.width + b.width
    signed = True if either is signed, else False
    """
    frac   = fmt_a.frac  + fmt_b.frac
    width  = fmt_a.width + fmt_b.width
    signed = fmt_a.signed or fmt_b.signed
    if width > 63:
        raise OverflowError(
            f"infer_mul_format: result would require width={width} > 63."
        )
    return Format(width, frac, signed, rounding, overflow)
