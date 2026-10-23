# MapBV Quickstart: Creating, Reading, and Slicing Bitfields

[繁體中文](../../zh/mapbv/README.md) · [Documentation Index](../README.md) · [Usage Scenarios](scenarios.md) · [API Reference](reference.md)

MapBV (MapBitVector) is a lightweight Python bitfield library designed for digital IC design, hardware register modeling, and testbench development.

It solves a fundamental problem in hardware modeling: **how to slice and manipulate register fields with guaranteed live, bidirectional synchronization between slices, parent registers, and concatenated buses**.

---

## 5-Minute Quickstart

### 1. Variables and Constants

Create named register variables using `var()`, and immutable constants using `const()`:

```python
import rpkbin.mapbv as mbv

# Create a 16-bit register variable named REG0 with initial value 0x1234
reg = mbv.var("REG0", 16, 0x1234)

# Create an immutable 4-bit constant mask 0xF
mask = mbv.const(0xF, 4)

print(f"Variable: {reg.name}, width: {reg.width} bits, value: 0x{reg.value:04X}")
print(f"Constant: {mask.name}, width: {mask.width} bits, value: 0x{mask.value:X}")
```

### 2. Format Hexadecimal and Binary Outputs

MapBV provides conversion methods and native Python f-string format specifiers:

```python
# Conversion methods
print(reg.to_hex())  # Output: 0x1234
print(reg.to_bin())  # Output: 0b0001001000110100

# Native f-string format specifiers
print(f"{reg:hex}")  # Output: 0x1234
print(f"{reg:bin}")  # Output: 0b0001001000110100
```

### 3. Slicing Fields and Single Bits

MapBV follows hardware description conventions (Verilog / SystemVerilog) using **closed-interval** indexing `[high:low]` (both high and low bits inclusive, requiring $high \ge low$):

```python
# Slice high byte [15:8]
high_byte = reg[15:8]
print(f"high_byte width: {len(high_byte)} bits, value: {high_byte:hex}")  # 8 bits, 0x12

# Slice single bit [3]
bit3 = reg[3]
print(f"bit3 width: {len(bit3)} bits, value: {bit3.value}")  # 1 bit, 0
```

### 4. Live Bidirectional Synchronization

Slices are **live, bidirectional views** linked to their parent register:
- Modifying a slice immediately writes through to the parent register.
- Modifying the parent register is instantly reflected when reading the slice.

```python
# Write through slice index
reg[15:8] = 0xAB

# Parent register is updated immediately
print(f"Updated parent register: {reg:hex}")  # Output: 0xAB34
print(f"Read through slice view:   {high_byte:hex}")  # Output: 0xAB

# Assign directly to the slice object's .value property
high_byte.value = 0x55
print(f"Updated again:            {reg:hex}")  # Output: 0x5534
```

---

## Important Caveats & Common Pitfalls

> [!WARNING]
> ### 1. Value Equality vs. Object Identity (`==`)
> In Python, `reg == other` tests **object identity (`self is other`)**.
> Comparing directly with an integer (`reg == 0x5534`) raises `TypeError` to catch common logic bugs.
>
> **Correct value comparison**:
> ```python
> assert reg.value == 0x5534
> # or
> assert reg.value_eq(0x5534)
> ```

> [!CAUTION]
> ### 2. Bit Width and Negative Value Guardrails
> MapBV represents unsigned hardware signals.
> - Negative values raise `ValueError`.
> - Values exceeding bit width capacity raise `ValueError`.
> ```python
> reg[3:0] = 0x10  # ValueError: 0x10 out of bounds for 4-bit slice
> reg[3:0] = -1    # ValueError: negative values not supported
> ```

---

## Next Steps

- **[Practical Usage Scenarios](scenarios.md)**: Explore bus concatenation (`concat`), read-only constant protection, combinational logic trees, and symbolic `eval()`.
- **[API Reference](reference.md)**: In-depth reference for methods, operators, and exceptions.
