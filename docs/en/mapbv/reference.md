# MapBV API & Semantics Reference

[繁體中文](../../zh/mapbv/reference.md) · [Documentation Index](../README.md) · [Usage Scenarios](scenarios.md)

This document provides technical specifications for the `rpkbin.mapbv` module, detailing factory functions, classes, properties, operators, and exceptions.

Import statement:
```python
import rpkbin.mapbv as mbv
```

---

## Factory Functions

### `var(name: str, width: int, value: int = 0) -> MapBV`
Creates a named variable register.
- **`name`** (`str`): Valid Python identifier (e.g., `"REG0"`).
- **`width`** (`int`): Bit width ($> 0$).
- **`value`** (`int`, default 0): Initial integer value in range $[0, 2^{\text{width}} - 1]$.
- **Returns**: A `MapBV` instance with `kind == "VAR"`.

### `const(value: int, width: int) -> MapBV`
Creates an immutable constant.
- **`value`** (`int`): Integer value in range $[0, 2^{\text{width}} - 1]$.
- **`width`** (`int`): Bit width ($> 0$).
- **Returns**: A `MapBV` instance with `kind == "CONST"`.

### `concat(name: str, *parts: MapBV) -> MapBV`
Concatenates multiple `MapBV` instances in **MSB → LSB** order into a live bus view.
- **`name`** (`str`): Identifier for the concatenated bus.
- **`parts`** (`MapBV`): Source `MapBV` objects.
- **Restrictions**: Parts cannot contain overlapping writable bits.
- **Returns**: A `MapBV` instance with `kind == "VAR"` and `is_linked == True`.

---

## Core Class: `MapBV`

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| **`name`** | `str` | Name or formatted path (`parent[high:low]`). |
| **`width`** | `int` | Bit width (`len(bv)`). |
| **`high`** | `int` | Highest bit index (inclusive). |
| **`low`** | `int` | Lowest bit index (inclusive). |
| **`kind`** | `str` | Node classification: `"VAR"`, `"CONST"`, or `"SLICE"`. |
| **`is_const`** | `bool` | True if `kind == "CONST"`. |
| **`is_linked`** | `bool` | True if created via `concat`. |
| **`value`** | `int` | **Readable & Writable**. Evaluates unsigned integer value or writes through to parent/parts. |

### Indexing & Slicing (`__getitem__`, `__setitem__`)

- **Closed Interval**: `bv[high:low]` includes both `high` and `low`. Width is $high - low + 1$.
- **Constraints**:
  - $low \le high$ required; $high < low$ raises `IndexError`.
  - Slices must reside within parent bounds; out-of-bounds raises `IndexError`.
  - Slice step is unsupported; `bv[high:low:step]` raises `ValueError`.
- **Single Bit**: `bv[bit]` is shorthand for `bv[bit:bit]` (1-bit width).
- **Slice Assignment**: `bv[high:low] = val` writes through immediately to underlying registers.

### Dunder Methods & Comparisons

- **`bv1 == bv2`**: Tests object identity (`self is other`). Comparing with an integer raises `TypeError`.
- **`bv.value_eq(other: MapBV | int) -> bool`**: Compares numerical values.
- **`len(bv) -> int`**: Returns bit width.
- **`int(bv) -> int`**: Returns `.value`.
- **`bool(bv) -> bool`**: Returns `bv.value != 0`.
- **`to_hex() -> str`**: Returns formatted uppercase hex string padded to hexadecimal digits `(width + 3) // 4` (e.g., 4-bit is `"0xF"`, 8-bit is `"0xFF"`, not fixed byte-aligned).
- **`to_bin() -> str`**: Returns formatted binary string (`"0b00001111"`).
- **`eval(ctx: dict[str, int]) -> int`**: Evaluates using a name-to-integer dictionary.

---

## Expression Class: `MapBVExpr`

Produced by bitwise operators applied to `MapBV` or `MapBVExpr` instances. Evaluated lazily to always reflect the latest underlying operand values.

### Operators

| Operator | Semantics | Output Bit Width |
| :---: | :--- | :--- |
| **`a & b`** | Bitwise AND | $\max(\text{width}_a, \text{width}_b)$ |
| **`a \| b`** | Bitwise OR | $\max(\text{width}_a, \text{width}_b)$ |
| **`a ^ b`** | Bitwise XOR | $\max(\text{width}_a, \text{width}_b)$ |
| **`~a`** | Bitwise NOT | $\text{width}_a$ |
| **`a << n`** | Left shift by non-negative int $n$ | $\text{width}_a$ (bits shifted beyond width are masked) |
| **`a >> n`** | Right shift by non-negative int $n$ | $\text{width}_a$ (zero-filled from MSB) |

---

## Exception Specifications

| Exception | Triggers |
| :--- | :--- |
| **`TypeError`** | - `bv == integer` comparison.<br>- Assigning to a constant (`kind == "CONST"`).<br>- Passing non-MapBV objects to `concat`. |
| **`ValueError`** | - Bit width $\le 0$.<br>- Assigning negative values.<br>- Assigning values larger than $2^{\text{width}} - 1$.<br>- Slice with step parameter.<br>- Overlapping writable target bits in `concat`. |
| **`IndexError`** | - Slice out of bounds.<br>- Reversed slice ($high < low$). |
| **`UserWarning`** | - Writing mismatched values to constant bits during bus concatenation. |
