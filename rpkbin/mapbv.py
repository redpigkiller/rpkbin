"""
MapBitVector (MapBV) — A lightweight BitVector library for IC design & verification.

Classes:
    MapBV     — The main BitVector node (named variable, constant, or slice).
    MapBVExpr — A logic expression node produced by &, |, ^, ~, <<, >> operators.

Factory functions (preferred user-facing API):
    const(value, width)        — Create a constant MapBV.
    var(name, width[, value])  — Create a named variable MapBV.
    concat(name, *parts)       — Create a live, writable view of parts (MSB → LSB).
"""

from __future__ import annotations

import numbers
import operator
import warnings
from abc import ABC, abstractmethod


# ---------------------------------------------------------------------------
# _BVBase  — shared logic for MapBV, MapBVExpr
# ---------------------------------------------------------------------------

class _BVBase(ABC):
    """Mixin providing &, |, ^, ~, <<, >> operators."""
    __slots__ = ()

    # Make numpy scalars defer to our reflected operators (np.uint8(3) & bv).
    __array_ufunc__ = None

    @property
    @abstractmethod
    def value(self) -> int:
        ...

    @property
    @abstractmethod
    def width(self) -> int:
        ...

    # -- dunder -------------------------------------------------------------

    def __len__(self) -> int:
        return self.width

    def __int__(self) -> int:
        return self.value

    def __bool__(self) -> bool:
        return self.value != 0

    def __eq__(self, other: object) -> bool:
        """Identity comparison (NOT value comparison); use :meth:`value_eq` for values.

        MapBV is mutable and live-linked, so it is hashed by identity to stay
        safe in sets/dicts.  Comparing with an integer raises ``TypeError``
        because it can only ever be a mistake; use ``bv.value == n``.
        """
        if not isinstance(other, _BVBase) and isinstance(other, numbers.Integral):
            raise TypeError(
                "MapBV == int compares identity and is almost certainly a bug; "
                "use `bv.value == n` or `bv.value_eq(n)`"
            )
        return self is other

    def __hash__(self) -> int:
        return id(self)

    def value_eq(self, other: "MapBV | int") -> bool:
        """Compare values, not identity."""
        if isinstance(other, int):
            return self.value == other
        return self.value == other.value

    # -- operators ---------------------------------------------------------

    def to_hex(self) -> str:
        """Return value as hex string, e.g. ``'0x000F'``."""
        ndigits = (self.width + 3) // 4
        return f"0x{self.value:0{ndigits}X}"

    def to_bin(self) -> str:
        """Return value as binary string, e.g. ``'0b0000000000001111'``."""
        return f"0b{self.value:0{self.width}b}"

    def __format__(self, spec: str) -> str:
        if spec == "hex":
            return self.to_hex()
        if spec == "bin":
            return self.to_bin()
        return format(self.value, spec)

    # -- formatting ---------------------------------------------------------

    def _binary(self, op: str, other, reflected: bool = False):
        if isinstance(other, _BVBase):
            width = max(self.width, other.width)
            operand = other
        else:
            try:
                operand = operator.index(other)
            except TypeError:
                return NotImplemented
            width = self.width
            if operand < 0:
                raise ValueError(f"MapBV does not support negative operand {operand}")
            if operand.bit_length() > width:
                raise ValueError(
                    f"Operand 0x{operand:X} does not fit in {width} bits for '{op}'"
                )
        operands = [operand, self] if reflected else [self, operand]
        return MapBVExpr(op, operands, width)

    def _shift(self, op: str, n):
        try:
            n = operator.index(n)
        except TypeError:
            return NotImplemented
        if n < 0:
            raise ValueError(f"Negative shift count {n}")
        return MapBVExpr(op, [self, n], self.width)

    def __and__(self, other):
        return self._binary("&", other)

    def __rand__(self, other):
        return self._binary("&", other, reflected=True)

    def __or__(self, other):
        return self._binary("|", other)

    def __ror__(self, other):
        return self._binary("|", other, reflected=True)

    def __xor__(self, other):
        return self._binary("^", other)

    def __rxor__(self, other):
        return self._binary("^", other, reflected=True)

    def __invert__(self) -> "MapBVExpr":
        return MapBVExpr("~", [self], self.width)

    def __lshift__(self, n):
        return self._shift("<<", n)

    def __rshift__(self, n):
        return self._shift(">>", n)


# ---------------------------------------------------------------------------
# MapBVExpr  — logic expression node
# ---------------------------------------------------------------------------

class MapBVExpr(_BVBase):
    """Represents a combinational logic expression.

    Produced by ``&``, ``|``, ``^``, ``~``, ``<<``, ``>>`` operators.
    """

    __slots__ = ("_op", "_operands", "_width", "_mask")

    def __init__(self, op: str, operands: list, width: int) -> None:
        self._op = op
        self._operands = operands
        self._width = width
        self._mask = (1 << self._width) - 1

    # -- resolve helper -----------------------------------------------------

    @staticmethod
    def _resolve(operand: MapBV | MapBVExpr | int, ctx: dict[str, int] | None = None) -> int:
        if isinstance(operand, int):
            return operand
        if ctx is not None:
            return operand.eval(ctx)
        return operand.value

    # -- value / eval -------------------------------------------------------

    @property
    def value(self) -> int:
        return self._evaluate(ctx=None)

    @property
    def width(self) -> int:
        return self._width
    
    def eval(self, ctx: dict[str, int]) -> int:
        return self._evaluate(ctx)

    def _evaluate(self, ctx: dict[str, int] | None) -> int:
        a = self._resolve(self._operands[0], ctx)
        if self._op == "~":
            return (~a) & self._mask
        b = self._resolve(self._operands[1], ctx)
        if self._op == "&":
            return (a & b) & self._mask
        if self._op == "|":
            return (a | b) & self._mask
        if self._op == "^":
            return (a ^ b) & self._mask
        if self._op == "<<":
            return (a << b) & self._mask
        if self._op == ">>":
            return (a >> b) & self._mask
        raise ValueError(f"Unknown operator: {self._op}")

# ---------------------------------------------------------------------------
# MapBV  — the main BitVector class
# ---------------------------------------------------------------------------

class MapBV(_BVBase):
    __slots__ = (
        "_name",
        "_parent",
        "_high", "_low", "_width", "_mask",
        "_kind",                                # "CONST" | "VAR" | "SLICE"
        "_raw_value",
        "_link_bv_list",
    )

    def __init__(
        self,
        parent: str | MapBV | None,
        high: int,
        low: int,
        value: int = 0,
    ) -> None:
        value = operator.index(value)
        if parent is None or isinstance(parent, str):
            # New instance
            if low != 0:
                raise ValueError(f"Standalone MapBV must have low=0, got low={low}")
            
            # 1. width
            self._width = high - low + 1
            if self._width <= 0:
                raise ValueError(f"Invalid range [{high}:{low}]: width must be > 0, got {self._width}")
            self._low = low
            self._high = high

            # 2. raw_value
            if not 0 <= value < (1 << self._width):
                max_val = (1 << self._width) - 1
                raise ValueError(f"Value 0x{value:X} out of bounds for {self._width}-bit MapBV (max 0x{max_val:X})")
            self._raw_value = value

            # 3. name
            if parent is None:
                self._name = "Constant"
                self._parent = None
                self._kind = "CONST"

            else:
                if not parent.isidentifier():
                    raise ValueError(f"Invalid name '{parent}': must be a valid Python identifier (e.g., 'REG0', 'sig_out')")
                self._name = parent
                self._parent = None
                self._kind = "VAR"

        elif isinstance(parent, MapBV):
            # 1. width
            self._width = high - low + 1
            if not (parent.low <= low <= high <= parent.high):
                raise IndexError(f"Slice [{high}:{low}] out of bounds for {parent.name}[{parent.high}:{parent.low}]")
            self._low = low
            self._high = high
            
            # 2. raw_value
            if not 0 <= value < (1 << self._width):
                max_val = (1 << self._width) - 1
                raise ValueError(f"Value 0x{value:X} out of bounds for {self._width}-bit MapBV (max 0x{max_val:X})")
            self._raw_value = value
            
            # 3. name
            self._name = f"{parent.name}[{high}:{low}]"
            self._parent = parent
            self._kind = "SLICE"
            
        else:
            raise TypeError(f"parent must be str, MapBV, or None, got {type(parent).__name__}")

        self._mask = (1 << self._width) - 1

        # Linking state
        self._link_bv_list: list[MapBV] = []

    # -- basic properties ---------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def width(self) -> int:
        return self._width

    @property
    def high(self) -> int:
        return self._high

    @property
    def low(self) -> int:
        return self._low
    
    @property
    def kind(self) -> str:
        return self._kind
    
    @property
    def is_const(self) -> bool:
        return self._kind == "CONST"
    
    @property
    def is_linked(self) -> bool:
        return len(self._link_bv_list) != 0

    # -- value access -------------------------------------------------------

    @property
    def value(self) -> int:
        """Read value.  If linked, concatenate children (MSB-first)."""
        if self._parent is not None:
            return (self._parent.value >> (self._low - self._parent._low)) & self._mask
        
        if self._link_bv_list:
            result = 0
            for child in self._link_bv_list:
                result = (result << child.width) | child.value
            return result
        
        return self._raw_value

    @value.setter
    def value(self, val: int) -> None:
        """Write value.  If linked, split and push to children.

        Writing directly to a constant (or a slice of one) raises ``TypeError``.
        Negative values and values wider than the MapBV raise ``ValueError``.

        When a constant is part of a linked bus, bits written to it are ignored
        (like reserved/read-only bits); a ``UserWarning`` is emitted only if the
        written bits differ from the constant.
        """
        if self._kind == "CONST":
            raise TypeError(
                f"Cannot write 0x{val:X} to a constant MapBV (width={self._width})."
            )

        val = operator.index(val)
        if val < 0:
            raise ValueError(f"MapBV does not support negative values, got {val}")
        if val > self._mask:
            raise ValueError(
                f"Value 0x{val:X} out of bounds for {self._width}-bit MapBV "
                f"(max 0x{self._mask:X})"
            )
        if self._parent is not None:
            rel = self._low - self._parent._low
            clear = ~(self._mask << rel) & self._parent._mask
            self._parent.value = (self._parent.value & clear) | (val << rel)
        elif self._link_bv_list:
            offset = 0
            for child in reversed(self._link_bv_list):
                child._bus_write((val >> offset) & child._mask)
                offset += child.width
        else:
            self._raw_value = val

    def _is_const_backed(self) -> bool:
        node = self
        while node._parent is not None:
            node = node._parent
        return node._kind == "CONST"

    def _bus_write(self, val: int) -> None:
        """Write from a parent bus: constant bits are ignored, warn if they differ."""
        if self._is_const_backed():
            if val != self.value:
                warnings.warn(
                    f"Write of 0x{val:X} to constant bits of {self._name} "
                    f"(width={self._width}, value=0x{self.value:X}) ignored.",
                    UserWarning,
                    stacklevel=4,
                )
            return
        self.value = val

    # -- linking ------------------------------------------------------------

    def _write_targets(self) -> list[tuple[int, int] | None]:
        """Return underlying writable bits in LSB-first order."""
        if self._kind == "CONST":
            return [None] * self._width
        if self._parent is not None:
            start = self._low - self._parent.low
            return self._parent._write_targets()[start:start + self._width]
        if self._link_bv_list:
            return [
                target
                for child in reversed(self._link_bv_list)
                for target in child._write_targets()
            ]
        return [(id(self), bit) for bit in range(self._width)]

    def _link(self, *parts: MapBV) -> None:
        """Make this (fresh, unlinked) VAR a live view of *parts* (MSB → LSB).

        Internal: use :func:`concat`.  Parts must already exist, so links can
        never form a cycle.
        """
        for p in parts:
            if not isinstance(p, MapBV):
                raise TypeError(
                    f"concat() parts must be MapBV (var/const/slice/bus), "
                    f"got {type(p).__name__}"
                )
        total = sum(p.width for p in parts)
        if total != self._width:
            raise ValueError(
                f"Link width mismatch: parts total {total} bits, "
                f"but {self._name} is {self._width} bits"
            )
        targets = [target for p in parts for target in p._write_targets() if target is not None]
        if len(targets) != len(set(targets)):
            raise ValueError("Linked parts contain overlapping writable bits")
        self._link_bv_list = list(parts)

    # -- slicing ------------------------------------------------------------

    def __getitem__(self, key: int | slice) -> MapBV:
        """``bv[high:low]`` or ``bv[bit]`` → child MapBV (inclusive both ends)."""
        if isinstance(key, int):
            high = low = key
        elif isinstance(key, slice):
            if key.step is not None:
                raise ValueError("MapBV slices do not support a step: use bv[high:low]")
            high, low = key.start, key.stop
        else:
            raise TypeError("MapBV indexing requires int or slice, e.g. bv[7:0] or bv[3]")
        
        if high is None or low is None:
            raise ValueError("Both high and low must be specified in slice: bv[high:low]")
        if high < low:
            raise IndexError(
                f"Slice [{high}:{low}] is reversed: write bv[high:low] with high >= low"
            )
        return MapBV(self, high, low)

    def __setitem__(self, key: int | slice, val: int) -> None:
        """``bv[high:low] = n`` / ``bv[bit] = n`` writes through to this MapBV."""
        self[key].value = val

    # -- symbolic eval ------------------------------------------------------

    def eval(self, ctx: dict) -> int:
        """Evaluate this MapBV symbolically using the context dict.

        Context keys are plain ``str`` names.
        If the name is not found, falls back to the current ``.value``.
        """
        if self._kind == "CONST":
            return self._raw_value

        if self._parent is not None:
            parent_val = self._parent.eval(ctx)
            return (parent_val >> (self._low - self._parent.low)) & self._mask
    
        if self._link_bv_list:
            result = 0
            for child in self._link_bv_list:
                result = (result << child.width) | child.eval(ctx)
            return result

        # Try name key; fall back to current value
        if self._name in ctx:
            v = operator.index(ctx[self._name])
            if not 0 <= v <= self._mask:
                raise ValueError(
                    f"ctx value {v} for '{self._name}' does not fit in {self._width} bits"
                )
            return v

        return self._raw_value

    # -- human-readable representation -------------------------------------

    def __str__(self) -> str:
        """Return a human-readable layout of this MapBV.

        For a linked MapBV, lists each part with its bit range, current
        hex value, and source name, with the ``<-`` column aligned.

        Example::

            SRAM_00[7:0] (0x52)
              [7:4] 0x05  <- REG0[3:0]
              [3:2] 0x00  <- Constant
              [1:0] 0x02  <- REG1[1:0]
        """
        base = self.name if self._kind == "SLICE" else f"{self.name}[{self.high}:{self.low}]"
        header = f"{base} ({self.to_hex()})"
        if not self._link_bv_list:
            return header + "\n  (no link)"

        # Build (prefix, source_name) pairs first to compute alignment width
        offset = self.width
        items = []
        for child in self._link_bv_list:
            high, low = offset - 1, offset - child.width
            items.append((f"  [{high}:{low}] {child.to_hex()}", child.name))
            offset = low
        col = max(len(prefix) for prefix, _ in items)
        lines = [header] + [f"{prefix:<{col}}  <- {name}" for prefix, name in items]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"MapBV(\"{self._name}\", {self._width})"


# ---------------------------------------------------------------------------
# Factory functions  — preferred user-facing API
# ---------------------------------------------------------------------------

def const(value: int, width: int) -> MapBV:
    """Create a constant (immutable) MapBV.

    Mirrors the old ``MapBV(0xFF, 8)`` interface. A value that does not fit
    in *width* bits raises ``ValueError``.

    Usage::

        import rpkbin.mapbv as mbv
        padding = mbv.const(0, 2)       # 2-bit zero constant
        mask    = mbv.const(0xF, 4)     # 0xFF would raise ValueError

    Args:
        value: Non-negative integer that fits in *width* bits.
        width: Bit width (must be > 0).

    Returns:
        A ``MapBV`` with ``kind == "CONST"``.
    """
    if width <= 0:
        raise ValueError(f"width must be > 0, got {width}")
    value = operator.index(value)
    if value < 0:
        raise ValueError(f"MapBV does not support negative values, got {value}")
    return MapBV(None, width - 1, 0, value=value)


def var(name: str, width: int, value: int = 0) -> MapBV:
    """Create a named variable MapBV.

    Usage::

        import rpkbin.mapbv as mbv
        reg = mbv.var("REG0", 16)           # initial value defaults to 0
        reg = mbv.var("REG0", 16, 0xABCD)  # with initial value

    Args:
        name:  Python identifier string for this register/signal.
        width: Bit width (must be > 0).
        value: Initial integer value (default 0).

    Returns:
        A ``MapBV`` with ``kind == "VAR"``.
    """
    if width <= 0:
        raise ValueError(f"width must be > 0, got {width}")
    return MapBV(name, width - 1, 0, value)


def concat(name: str, *parts: MapBV) -> MapBV:
    """Create a new linked MapBV by concatenating *parts* (MSB → LSB).

    Automatically computes the total width from parts.  Parts are fixed at
    creation; to get a different mapping, create another ``concat``.
    The resulting MapBV value is always derived live from its parts;
    writing to it distributes bits back to each part.

    Usage::

        import rpkbin.mapbv as mbv
        sram = mbv.concat("SRAM", reg0[3:0], padding, reg1[1:0])

    Args:
        name:  Python identifier for the resulting MapBV.
        parts: Source MapBVs in MSB → LSB order.

    Returns:
        A ``MapBV`` with ``kind == "VAR"`` and ``is_linked == True``.
    """
    if not parts:
        raise ValueError("concat() requires at least one part")
    for p in parts:
        if not isinstance(p, MapBV):
            raise TypeError(
                f"concat() parts must be MapBV (var/const/slice/bus), got {type(p).__name__}"
            )
    total = sum(p.width for p in parts)
    new_bv = MapBV(name, total - 1, 0)
    new_bv._link(*parts)
    return new_bv
