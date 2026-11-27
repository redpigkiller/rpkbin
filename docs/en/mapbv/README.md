# `rpkbin.mapbv`

[繁體中文](../../zh/mapbv/README.md) · [Documentation Index](../README.md) · [Usage Scenarios](scenarios.md) · [API Reference](reference.md)

`rpkbin.mapbv` models unsigned hardware registers and bitfields in pure Python, keeping slices, parent registers, concatenated buses, and logic expressions synchronized.

## Syntax

```python
reg = mbv.var(name, width, value=0)
c = mbv.const(value, width)
bus = mbv.concat(name, *parts)
field = reg[high:low]
bit = reg[bit_index]
text = f"{reg:hex}"
bits = f"{reg:bin}"
```

## Description

- `reg = mbv.var(name, width, value=0)` creates a mutable, named unsigned register of `width` bits initialized to `value`.
- `c = mbv.const(value, width)` creates an immutable constant node for fixed IDs or reserved bits.
- `bus = mbv.concat(name, *parts)` builds a live MSB-to-LSB bus view over existing variables, constants, slices, or buses; writes split back to each underlying writable part.
- `field = reg[high:low]` and `bit = reg[bit_index]` create inclusive closed-interval views (`high >= low`); assigning to `.value` or `reg[high:low] = val` updates the parent register immediately.
- `f"{reg:hex}"` and `f"{reg:bin}"` format values zero-padded to the node's bit width (`ceil(width / 4)` hex digits or `width` binary digits).

## Examples

### 1. Minimal Example: Variables, Constants, and Formatting

Create a 16-bit register variable and a 4-bit constant, then format them with `to_hex()`, `to_bin()`, and f-string specifiers.

```python
import rpkbin.mapbv as mbv

reg = mbv.var("REG0", 16, 0x1234)
mask = mbv.const(0xF, 4)

print(f"Variable: {reg.name}, width: {reg.width} bits, hex: {reg.to_hex()}")
print(f"Constant: {mask.name}, width: {mask.width} bits, hex: {mask:hex}")
print(f"Binary:   {reg:bin}")
```

Output:
```text
Variable: REG0, width: 16 bits, hex: 0x1234
Constant: Constant, width: 4 bits, hex: 0xF
Binary:   0b0001001000110100
```

### 2. Progressive Example: Slicing and Live Synchronization

Slice bitfields using inclusive Verilog-style `[high:low]` bounds and verify bidirectional updates between the parent register and its slices.

```python
import rpkbin.mapbv as mbv

reg = mbv.var("REG0", 16, 0x1234)
high_byte = reg[15:8]
bit3 = reg[3]

print(f"high_byte width: {len(high_byte)} bits, value: {high_byte:hex}")
print(f"bit3 width: {len(bit3)} bits, value: {bit3.value}")

reg[15:8] = 0xAB
print(f"After slice index write: reg={reg:hex}, high_byte={high_byte:hex}")

high_byte.value = 0x55
print(f"After slice .value write: reg={reg:hex}")
```

Output:
```text
high_byte width: 8 bits, value: 0x12
bit3 width: 1 bits, value: 0
After slice index write: reg=0xAB34, high_byte=0xAB
After slice .value write: reg=0x5534
```

## Tips and Limitations

- **Identity vs. value equality (`==`)**: `reg == other` compares object identity (`self is other`) so mutable nodes stay hashable in dicts and sets. Comparing a `MapBV` or `MapBVExpr` directly with an integer (`reg == 0x5534`) raises `TypeError`; compare with `reg.value == 0x5534` or `reg.value_eq(0x5534)`.
- **Unsigned bounds and closed intervals**: Negative values, values `>= 2**width`, slice steps (`reg[7:0:2]`), and reversed bounds (`reg[0:7]`) are rejected.

```python
import rpkbin.mapbv as mbv

reg = mbv.var("REG0", 16, 0x5534)
assert reg.value == 0x5534
assert reg.value_eq(0x5534)

for bad in (lambda: reg == 0x5534, lambda: reg[3:0].__setattr__("value", 0x10)):
    try:
        bad()
    except (TypeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}")
```

Output:
```text
TypeError: MapBV == int compares identity and is almost certainly a bug; use `bv.value == n` or `bv.value_eq(n)`
ValueError: Value 0x10 out of bounds for 4-bit MapBV (max 0xF)
```

- **Compatibility**: Pure Python `>= 3.11`, offline with no external I/O. Widths, values, and indices accept `__index__` integers (including NumPy integer scalars); `bool` is rejected (`TypeError`) for widths and slice indices. Verified on Windows with Python 3.13.

## See Also

- [Usage Scenarios](scenarios.md)
- [API Reference](reference.md)
- [Documentation Index](../README.md)
