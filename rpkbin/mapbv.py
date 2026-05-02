"""
MapBitVector (MapBV) — A lightweight BitVector library for IC design & verification.

Classes:
    MapBV     — The main BitVector node (named variable, constant, or slice).
    MapBVExpr — A logic expression node produced by &, |, ^, ~, <<, >> operators.

Factory functions (preferred user-facing API):
    const(value, width)        — Create a constant MapBV.
    var(name, width[, value])  — Create a named variable MapBV.
    concat(name, *parts)       — Create a linked MapBV from parts (MSB → LSB).
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod


# ---------------------------------------------------------------------------
# _BVBase  — shared logic for MapBV, MapBVExpr
# ---------------------------------------------------------------------------

class _BVBase(ABC):
    """Mixin providing &, |, ^, ~, <<, >> operators."""
    __slots__ = ()

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

    def __eq__(self, other: object) -> bool:
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
        if spec in ("x", "X", "hex"):
            return self.to_hex()
        if spec in ("b", "bin"):
            return self.to_bin()
        return format(self.value, spec)

    # -- formatting ---------------------------------------------------------

    def __and__(self, other: MapBV | MapBVExpr | int) -> "MapBVExpr":
        if isinstance(other, int) and other >= (1 << self.width):
            max_val = (1 << self.width) - 1
            raise ValueError(
                f"Operand 0x{other:X} exceeds MapBV width {self.width} (max 0x{max_val:X})"
            )
        w = self.width if isinstance(other, int) else max(self.width, other.width)
        return MapBVExpr("&", [self, other], w)

    def __rand__(self, other: int) -> "MapBVExpr":
        return MapBVExpr("&", [other, self], self.width)

    def __or__(self, other: MapBV | MapBVExpr | int) -> "MapBVExpr":
        w = self.width if isinstance(other, int) else max(self.width, other.width)
        return MapBVExpr("|", [self, other], w)

    def __ror__(self, other: int) -> "MapBVExpr":
        return MapBVExpr("|", [other, self], self.width)

    def __xor__(self, other: MapBV | MapBVExpr | int) -> "MapBVExpr":
        w = self.width if isinstance(other, int) else max(self.width, other.width)
        return MapBVExpr("^", [self, other], w)

    def __rxor__(self, other: int) -> "MapBVExpr":
        return MapBVExpr("^", [other, self], self.width)

    def __invert__(self) -> "MapBVExpr":
        return MapBVExpr("~", [self], self.width)

    def __lshift__(self, n: int) -> "MapBVExpr":
        if not isinstance(n, int):
            return NotImplemented
        return MapBVExpr("<<", [self, n], self.width)

    def __rshift__(self, n: int) -> "MapBVExpr":
        if not isinstance(n, int):
            return NotImplemented
        return MapBVExpr(">>", [self, n], self.width)
    

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
            return (self._parent.value >> self._low) & self._mask
        
        if self._link_bv_list:
            result = 0
            for child in self._link_bv_list:
                result = (result << child.width) | child.value
            return result
        
        return self._raw_value

    @value.setter
    def value(self, val: int) -> None:
        """Write value.  If linked, split and push to children.

        Writing to a constant raises a warning and is ignored.
        """
        if self._parent is not None:
            val &= self._mask
            clear = ~(self._mask << self._low) & ((1 << self._parent.width) - 1)
            self._parent.value = (self._parent.value & clear) | (val << self._low)
        
        elif self._kind == "CONST":
            warnings.warn(
                f"Attempted to write 0x{val:X} to constant MapBV "
                f"(width={self._width}). Write ignored.",
                UserWarning,
                stacklevel=2,
            )
        
        else:
            val &= self._mask
            if self._link_bv_list:
                offset = 0
                for child in reversed(self._link_bv_list):
                    child_mask = (1 << child.width) - 1
                    child.value = (val >> offset) & child_mask
                    offset += child.width
            else:
                self._raw_value = val

    # -- linking ------------------------------------------------------------

    def _collect_linked(self, visited: set) -> None:
        for child in self._link_bv_list:
            visited.add(id(child))
            child._collect_linked(visited)  # pylint: disable=protected-access

    def link(self, *parts: MapBV, _force: bool = False) -> None:
        """Define this MapBV as a concatenation of *parts* (MSB → LSB order).

        The total width of all parts must equal ``self.width``.
        Re-linking a MapBV that is already linked emits a warning.

        Only valid on VAR MapBVs. Calling this on a SLICE raises TypeError;
        create an explicit VAR to represent the sub-region instead.
        """
        if self._kind == "SLICE":
            raise TypeError(
                "Cannot call link() on a SLICE MapBV. "
                "Create an explicit var() to represent the sub-region, "
                "then link() that instead."
            )

        for p in parts:
            reachable = set()
            p._collect_linked(reachable)    # pylint: disable=protected-access
            if id(self) in reachable or id(p) == id(self):
                raise ValueError(
                    f"Circular link detected: '{self._name}' cannot link to '{p.name}'"
                )
        
        total = sum(p.width for p in parts)
        if total != self._width:
            raise ValueError(
                f"Link width mismatch: parts total {total} bits, "
                f"but {self._name} is {self._width} bits"
            )
        if self._link_bv_list and _force is False:
            warnings.warn(
                f"MapBV '{self._name}' is already linked. "
                f"Overwriting existing link structure.",
                UserWarning,
                stacklevel=2,
            )
        self._link_bv_list = list(parts)


    def detach(self) -> None:
        """Detach the link structure, snapshotting the current value.

        After detaching, the MapBV holds its last computed composite value
        as a standalone raw value and is no longer connected to any parts.
        Does nothing if the MapBV is not linked.
        """
        if not self._link_bv_list:
            return
        self._raw_value = self.value
        self._link_bv_list = []

    # -- slicing ------------------------------------------------------------

    def __getitem__(self, key: int | slice) -> MapBV:
        """``bv[high:low]`` or ``bv[bit]`` → child MapBV (inclusive both ends)."""
        if isinstance(key, int):
            high = low = key
        elif isinstance(key, slice):
            high, low = key.start, key.stop
        else:
            raise TypeError("MapBV indexing requires int or slice, e.g. bv[7:0] or bv[3]")
        
        if high is None or low is None:
            raise ValueError("Both high and low must be specified in slice: bv[high:low]")
        return MapBV(self, high, low)

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
            mask = (1 << self._width) - 1
            return (parent_val >> self._low) & mask
    
        if self._link_bv_list:
            result = 0
            for child in self._link_bv_list:
                result = (result << child.width) | child.eval(ctx)
            return result

        # Try name key; fall back to current value
        if self._name in ctx:
            return ctx[self._name] & self._mask

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
        header = f"{self.name}[{self.high}:{self.low}] ({self.to_hex()})"
        if not self._link_bv_list:
            return header + "\n  (no link)"

        # Build (prefix, source_name) pairs first to compute alignment width
        items = [
            (f"  [{child.high}:{child.low}] {child.to_hex()}", child.name)
            for child in self._link_bv_list
        ]
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

    Mirrors the old ``MapBV(0xFF, 8)`` interface. Value is automatically
    masked to *width* bits.

    Usage::

        import rpkbin.mapbv as mbv
        padding = mbv.const(0, 2)       # 2-bit zero constant
        mask    = mbv.const(0xFF, 4)    # value auto-masked → 0xF

    Args:
        value: Integer value (masked to *width* bits automatically).
        width: Bit width (must be > 0).

    Returns:
        A ``MapBV`` with ``kind == "CONST"``.
    """
    masked = value & ((1 << width) - 1)
    return MapBV(None, width - 1, 0, value=masked)


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
    return MapBV(name, width - 1, 0, value)


def concat(name: str, *parts: MapBV) -> MapBV:
    """Create a new linked MapBV by concatenating *parts* (MSB → LSB).

    Automatically computes the total width from parts.
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
    total = sum(p.width for p in parts)
    new_bv = MapBV(name, total - 1, 0)
    new_bv.link(*parts)
    return new_bv
