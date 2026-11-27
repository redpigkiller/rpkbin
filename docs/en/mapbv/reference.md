# `rpkbin.mapbv` API Reference

[繁體中文](../../zh/mapbv/reference.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [Usage Scenarios](scenarios.md)

Technical specification for `rpkbin.mapbv`: factory functions, `MapBV`, `MapBVExpr`, formatting, symbolic evaluation, operators, and exceptions.

```python
from rpkbin.mapbv import MapBV, MapBVExpr, const, var, concat
```

`__all__` exports those five names. Requires Python 3.11 or newer.

## Factory Functions

### `var`

#### Syntax
```python
reg = var(name, width, value=0)
```

#### Description
Creates a mutable named register variable (`kind == "VAR"`).

#### Input Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | required | Non-empty valid Python identifier (`name.isidentifier()`), such as `"REG0"` or `"sig_out"`. |
| `width` | `int` | required | Positive bit width (`> 0`). Accepts `__index__` integers (including NumPy scalars); rejects `bool` with `TypeError`. |
| `value` | `int` | `0` | Initial unsigned integer in `0 <= value < 2**width`. |

#### Output
Returns a `MapBV` instance with `kind == "VAR"`, `high == width - 1`, `low == 0`, `is_const == False`, and `is_linked == False`.

### `const`

#### Syntax
```python
c = const(value, width)
```

#### Description
Creates an immutable constant node (`kind == "CONST"`, `name == "Constant"`).

#### Input Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `value` | `int` | required | Non-negative integer in `0 <= value < 2**width`. |
| `width` | `int` | required | Positive bit width (`> 0`). Rejects `bool` with `TypeError`. |

#### Output
Returns a `MapBV` instance with `kind == "CONST"` and `is_const == True`.

### `concat`

#### Syntax
```python
bus = concat(name, *parts)
```

#### Description
Concatenates one or more existing `MapBV` nodes in MSB-to-LSB order into a live bus view. Reading concatenates all parts; writing distributes bits LSB-first back to each writable part and ignores writes to constant bits (issuing `UserWarning` only when written bits differ from the constant).

#### Input Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | required | Valid Python identifier for the bus. |
| `*parts` | `MapBV` | required | One or more `MapBV` nodes (`var`, `const`, `slice`, or another `concat` bus). Must not share overlapping writable bits; multiple constants are allowed. |

#### Output
Returns a `MapBV` instance with `kind == "VAR"`, `is_linked == True`, and `width == sum(p.width for p in parts)`.

## `MapBV`

Represents a hardware bitvector node: a variable, constant, slice, or concatenated bus.

### Creation
Prefer `var()`, `const()`, and `concat()`. Internally, `MapBV(parent, high, low, value=0)` takes `parent: str | MapBV | None` (`low == 0` when `parent` is `str | None`).

### Properties
| Property | Type | Meaning |
| :--- | :--- | :--- |
| `name` | `str` | Identifier for `"VAR"`, `"Constant"` for `"CONST"`, or `f"{parent.name}[{high}:{low}]"` for `"SLICE"`. |
| `width` | `int` | Bit width (`high - low + 1`, equal to `len(bv)`). |
| `high` | `int` | Inclusive highest bit index (`width - 1` for standalone nodes). |
| `low` | `int` | Inclusive lowest bit index (`0` for standalone nodes). |
| `kind` | `str` | `"VAR"`, `"CONST"`, or `"SLICE"`. |
| `is_const` | `bool` | `True` only when `kind == "CONST"`. Note: a slice of a constant has `kind == "SLICE"` (`is_const == False`), though writing to it still raises `TypeError`. |
| `is_linked` | `bool` | `True` when created with `concat()`. |
| `value` | `int` | Readable and writable unsigned integer property. Writing to a constant or constant slice raises `TypeError`. |

### Object Functions
| Method / Operator | Purpose |
| :--- | :--- |
| `bv[high:low]`, `bv[bit]` | Return a live `"SLICE"` view (`width = high - low + 1`, or `1` for `[bit]`). |
| `bv[high:low] = val`, `bv[bit] = val` | Write `val` through to the underlying register or bus. |
| `bv.value_eq(other)` | Compare numeric `.value` against a `MapBV`, `MapBVExpr`, or `__index__` integer (returns `False` for unsupported non-integer types such as `None`, `str`, or `float`). |
| `bv.to_hex()` | Return uppercase hex string zero-padded to `(width + 3) // 4` hex digits (e.g., 4-bit `0xF` gives `"0xF"`; 8-bit `0xA` gives `"0x0A"`). |
| `bv.to_bin()` | Return binary string zero-padded to `width` bits (e.g., 4-bit `0x3` gives `"0b0011"`). |
| `format(bv, spec)` (`__format__`) | `"hex"` calls `to_hex()`; `"bin"` calls `to_bin()`; any other spec (including `""`, `"x"`, `"08b"`, `"d"`) delegates to `format(bv.value, spec)`. |
| `bv.eval(ctx)` | Evaluate symbolically with `ctx: dict[str, int]`, matching leaf `var()` names only. |
| `str(bv)` | Return multi-line structural layout: header `name[high:low] (0x...)` plus `\n  (no link)` or aligned `[high:low] 0x...  <- source_name` lines for `concat()` buses. |
| `repr(bv)` | Return debug summary, e.g., `<MapBV VAR R[15:0] value=0x1234>` or `<MapBV VAR linked BUS[11:0] value=0x125>`. |
| `bv1 == bv2`, `hash(bv)` | Object identity (`self is other`) and `id(self)`. Comparing with `numbers.Integral` (`int`, `bool`, NumPy integer) raises `TypeError`; comparing with `None` or `"x"` returns `False`. |
| `len(bv)`, `int(bv)`, `bool(bv)` | Return `bv.width`, `bv.value`, and `bv.value != 0`. |

#### Slicing Rules
- Bounds are closed intervals (`[high:low]` includes both `high` and `low`) with `parent.low <= low <= high <= parent.high`.
- Nested slices keep root coordinates: for `r = var("R", 16)`, `r[15:8][11:8]` is valid (slice name `"R[15:8][11:8]"`), whereas `r[15:8][3:0]` raises `IndexError` because `0 < 8`.
- Omitted bounds (`bv[:4]`, `bv[4:]`, `bv[:]`) and steps (`bv[7:0:2]`) raise `ValueError`. Reversed bounds (`high < low`) and out-of-range indices raise `IndexError`. `bool` indices/bounds raise `TypeError`.

#### Symbolic Evaluation (`eval(ctx)`) Behavior
- `CONST` always returns its fixed constant value.
- `SLICE` delegates recursively to `parent.eval(ctx)` and masks/shifts the result. A slice of a `concat()` bus (`bus[7:4].eval(ctx)`) therefore also resolves through the underlying leaf `var()` nodes.
- Linked `concat()` buses delegate recursively to each child's `.eval(ctx)` and reassemble bits MSB-first.
- Only leaf `var()` nodes check `self.name in ctx`. Any key matching an intermediate `concat()` bus name or slice name (such as `{"BUS": 0xAB}` or `{"R[7:0]": 0xF}`) is ignored; evaluation always resolves through leaf `var()` names.
- `ctx` must support membership (`name in ctx`) and key lookup (`ctx[name]`); passing `None` or a non-container raises `TypeError`. Matched `ctx` entries are converted via `operator.index` and validated to fit in `0 <= v < 2**width` (`ValueError` otherwise). Unmatched leaf `var()` nodes fall back to their current `.value`.

## `MapBVExpr`

Lazy combinational logic expression tree produced by bitwise operators on `MapBV` or `MapBVExpr` nodes.

### Creation
Built by applying `&`, `|`, `^`, `~`, `<<`, or `>>`:

| Operator | Semantics | Output Width |
| :--- | :--- | :--- |
| `a & b` | Bitwise AND | `max(width_a, width_b)` (or `width_a` with integer `b`) |
| `a \| b` | Bitwise OR | `max(width_a, width_b)` (or `width_a` with integer `b`) |
| `a ^ b` | Bitwise XOR | `max(width_a, width_b)` (or `width_a` with integer `b`) |
| `~a` | Bitwise NOT | `width_a` |
| `a << n` | Left shift by non-negative `int` `n` (truncated to `width_a`) | `width_a` |
| `a >> n` | Right shift by non-negative `int` `n` (zero-filled from MSB) | `width_a` |

- `&`, `|`, and `^` accept a `MapBV`, `MapBVExpr`, or non-negative `__index__` integer fitting in `bv.width` bits (`0 <= n` and `n.bit_length() <= bv.width`). Both `bv & 0x0F` and reflected `0x0F & bv` (including NumPy scalars via `__array_ufunc__ = None`) work.
- `<<` and `>>` require a non-negative `__index__` shift count on the right; `n << bv` is not supported and raises `TypeError`.

### Properties
| Property | Type | Meaning |
| :--- | :--- | :--- |
| `value` | `int` | Read-only. Dynamically evaluates the expression tree from the current `.value` of all operands. Assigning raises `AttributeError`. |
| `width` | `int` | Output bit width of the expression. |

### Object Functions
| Method / Operator | Purpose |
| :--- | :--- |
| `expr.eval(ctx)` | Recursively evaluates the expression tree with `ctx: dict[str, int]`, matching leaf `var()` names only (ignoring `concat()` bus names and slice names). |
| `expr.value_eq(other)` | Compare numeric `.value` against a `MapBV`, `MapBVExpr`, or `__index__` integer. |
| `expr.to_hex()`, `expr.to_bin()` | Format current evaluated `.value` zero-padded to `expr.width`. |
| `format(expr, spec)` (`__format__`) | Supports `:hex`, `:bin`, and integer format specs via `format(expr.value, spec)`. |
| `repr(expr)`, `str(expr)` | Return `<MapBVExpr <tree> width=N value=0x...>` (`MapBVExpr` inherits `object.__str__`, so `str(expr)` calls `repr(expr)`). |
| `expr1 == expr2`, `hash(expr)` | Object identity (`self is other`) and `id(self)`. Comparing with an integer raises `TypeError`. |
| `len(expr)`, `int(expr)`, `bool(expr)` | Return `expr.width`, `expr.value`, and `expr.value != 0`. |

## Examples

### 1. Minimal Example: `str(bv)`, `__format__`, and `repr`

Inspect formatted strings, structural `str(bv)` layouts, and `repr()` outputs for registers, buses, and expressions.

```python
import rpkbin.mapbv as mbv

hi = mbv.var("HI", 4, 0xA)
lo = mbv.var("LO", 4, 0x3)
bus = mbv.concat("BUS", hi, mbv.const(0, 2), lo[1:0])
expr = (hi << 1) ^ 0xF

print(f"format: hex={bus:hex} bin={bus:bin} x={bus:x} d={bus:d}")
print(repr(bus))
print(repr(expr))
print(str(bus))
```

Output:
```text
format: hex=0xA3 bin=0b10100011 x=a3 d=163
<MapBV VAR linked BUS[7:0] value=0xA3>
<MapBVExpr ((HI << 1) ^ 0xF) width=4 value=0xB>
BUS[7:0] (0xA3)
  [7:4] 0xA  <- HI
  [3:2] 0x0  <- Constant
  [1:0] 0x3  <- LO[1:0]
```

### 2. Progressive Example: `MapBVExpr.value` and Leaf `var()` Matching in `eval(ctx)`

Verify live `.value` updates on `MapBVExpr` and confirm that `eval(ctx)` matches leaf `var()` names (`"HI"`, `"LO"`) while ignoring intermediate `concat()` bus names (`"BUS"`).

```python
import rpkbin.mapbv as mbv

hi = mbv.var("HI", 4, 0x2)
lo = mbv.var("LO", 4, 0x5)
bus = mbv.concat("BUS", hi, lo)
expr = bus ^ 0x0F

print("initial expr.value:", hex(expr.value))

hi.value = 0xC
print("after HI=0xC, expr.value:", hex(expr.value))

# "BUS" is ignored because eval() resolves through leaf var() nodes "HI" and "LO".
print("eval ignoring BUS:", hex(expr.eval({"BUS": 0x00, "HI": 0xA})))
print("eval with leaf vars:", hex(expr.eval({"HI": 0x1, "LO": 0x2})))
print("real bus.value unchanged:", bus.to_hex())
```

Output:
```text
initial expr.value: 0x2a
after HI=0xC, expr.value: 0xca
eval ignoring BUS: 0xaa
eval with leaf vars: 0x1d
real bus.value unchanged: 0xC5
```

## Exceptions and Warnings

| Exception / Warning | Triggers |
| :--- | :--- |
| `TypeError` | `bv == integer` or `expr == integer` (any `numbers.Integral` other than `_BVBase`, including `bool` and NumPy integers); writing `.value` or slice-assigning to a `CONST` or slice of a `CONST`; passing `bool` as `width`, slice bound, or bit index; passing a non-`str` `name` to `var()` or `concat()` (e.g., `var(123, 8)`) or non-`str`/`MapBV`/`None` `parent` to `MapBV()`; passing a non-indexable `value` (`var("A", 8, "x")`, `bv.value = "x"`), slice key (`bv["0"]`), operand (`bv & "x"`, `bv \| 1.5`, `1 << bv`), or `ctx` entry in `eval()`; passing a non-container `ctx` (such as `None`) to `eval()`; passing a non-`MapBV` (such as `int` or `MapBVExpr`) to `concat()`. |
| `ValueError` | `width <= 0` in `var()` or `const()`; calling `MapBV(parent, high, low)` with `low != 0` on a standalone (`str \| None`) node or with `high - low + 1 <= 0`; invalid identifier in `var()` or `concat()`; negative integer or value `>= 2**width` on creation, assignment, or in `eval(ctx)`; negative integer or integer with `bit_length() > bv.width` in `&`, `\|`, `^`; negative shift count in `<<`, `>>`; slice with `step` (`bv[7:0:2]`) or omitted bound (`bv[:4]`, `bv[4:]`, `bv[:]`); calling `concat(name)` with zero parts; overlapping writable target bits in `concat()`. |
| `IndexError` | Reversed slice (`high < low`); slice or bit index outside `[parent.low, parent.high]` (including nested slices using local instead of root bit indices). |
| `AttributeError` | Assigning to read-only properties such as `MapBVExpr.value` or `MapBV` structural properties (`width`, `name`, `kind`, `high`, `low`, `is_const`, `is_linked`). |
| `UserWarning` | Writing to a `concat()` bus when bits aligned to a `CONST` part (or constant slice) differ from that constant's value; constant bits remain unchanged. |

## Tips and Limitations

- `MapBV` nodes are mutable and live-linked, so `==` and `hash()` are identity-based. Never use a `MapBV` value directly inside `==` comparisons against integers; use `.value` or `.value_eq()`.
- Bitwise shifts keep the operand's width: `(var("X", 4, 5) << 4)` evaluates to `0` (`width=4`), not `0x50`. Widen or `concat()` before shifting if you need wider outputs.

## See Also

- [Quickstart](README.md)
- [Usage Scenarios](scenarios.md)
- [Documentation Index](../README.md)
