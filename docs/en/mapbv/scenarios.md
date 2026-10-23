# MapBV Practical Usage Scenarios

[繁體中文](../../zh/mapbv/scenarios.md) · [Documentation Index](../README.md) · [API Reference](reference.md)

This document presents 6 practical scenarios demonstrating hardware register modeling, field slicing, bus concatenation, and combinational logic evaluation using MapBV. All examples are complete and runnable.

---

## Table of Contents

1. [Scenario 1: Register Field Slicing and Live Synchronization](#scenario-1-register-field-slicing-and-live-synchronization)
2. [Scenario 2: Hardware Bus Concatenation (`concat`) and Bit Dispatching](#scenario-2-hardware-bus-concatenation-concat-and-bit-dispatching)
3. [Scenario 3: Reserved Bits and Constant Enforcement](#scenario-3-reserved-bits-and-constant-enforcement)
4. [Scenario 4: Combinational Logic Expressions and Deferred Evaluation](#scenario-4-combinational-logic-expressions-and-deferred-evaluation)
5. [Scenario 5: Symbolic Simulation with Context Dictionaries (`eval`)](#scenario-5-symbolic-simulation-with-context-dictionaries-eval)
6. [Scenario 6: Common Pitfalls and Boundary Checks](#scenario-6-common-pitfalls-and-boundary-checks)

---

## Scenario 1: Register Field Slicing and Live Synchronization

### Goal
Model a 32-bit control register `CTRL_REG` with functional fields:
- `[31]`: Enable bit (`enable`, 1 bit)
- `[23:16]`: Burst length (`burst_len`, 8 bits)
- `[7:0]`: Priority level (`priority`, 8 bits)

Writing to any field slice must immediately update the parent register, and updating the parent register must update all field views simultaneously.

### Complete Runnable Code (`scenario_slice.py`)

```python
import rpkbin.mapbv as mbv

# 1. Create a 32-bit register initialized to 0
ctrl_reg = mbv.var("CTRL_REG", 32, 0x0000_0000)

# 2. Slice named fields (closed-interval [high:low])
enable    = ctrl_reg[31]
burst_len = ctrl_reg[23:16]
priority  = ctrl_reg[7:0]

# 3. Field writes: configure individual fields
enable.value = 1
burst_len.value = 0x10
priority.value = 0x05

print(f"CTRL_REG total value: {ctrl_reg:hex}")
assert ctrl_reg.value == 0x8010_0005

# 4. Whole-register write: simulate bus write to the entire register
ctrl_reg.value = 0x00FF_0000

print(f"enable field:    {enable.value}")      # 0
print(f"burst_len field: {burst_len:hex}")    # 0xFF
print(f"priority field:  {priority:hex}")     # 0x00

assert enable.value == 0
assert burst_len.value == 0xFF
assert priority.value == 0
print("Scenario 1 verified successfully!")
```

---

## Scenario 2: Hardware Bus Concatenation (`concat`) and Bit Dispatching

### Goal
Pack multiple registers into a wider memory bus (e.g., SRAM data bus). Writes to the concatenated bus must automatically split and dispatch bits back to their underlying registers.

### Complete Runnable Code (`scenario_concat.py`)

```python
import rpkbin.mapbv as mbv

# Source registers
reg_a = mbv.var("REG_A", 4, 0x1)  # 4-bit
reg_b = mbv.var("REG_B", 4, 0x2)  # 4-bit
reg_c = mbv.var("REG_C", 8, 0x34) # 8-bit

# Concatenate in MSB -> LSB order: [15:12]=REG_A, [11:8]=REG_B, [7:0]=REG_C
sram_bus = mbv.concat("SRAM_BUS", reg_a, reg_b, reg_c)

print(f"Concatenated Bus: {sram_bus.name}, width: {sram_bus.width} bits")
print(f"Initial value:    {sram_bus:hex}")  # 0x1234
assert sram_bus.value == 0x1234

# Print structural layout
print("\n--- Structural Mapping ---")
print(str(sram_bus))

# Write to sram_bus and observe automatic bit dispatching
sram_bus.value = 0xABCD

print(f"\nAfter writing 0xABCD:")
print(f"REG_A: {reg_a:hex}")  # Upper 4 bits: 0xA
print(f"REG_B: {reg_b:hex}")  # Middle 4 bits: 0xB
print(f"REG_C: {reg_c:hex}")  # Lower 8 bits: 0xCD

assert reg_a.value == 0xA
assert reg_b.value == 0xB
assert reg_c.value == 0xCD
print("Scenario 2 verified successfully!")
```

---

## Scenario 3: Reserved Bits and Constant Enforcement

### Goal
Enforce hardware reserved bits and read-only version registers:
1. Direct assignment to a constant raises `TypeError`.
2. Bus writes through `concat` ignore constant bit writes and emit `UserWarning` if written bits differ.

### Complete Runnable Code (`scenario_const.py`)

```python
import warnings
import rpkbin.mapbv as mbv

field_data = mbv.var("DATA", 8, 0x12)
reserved   = mbv.const(0x0, 4)        # 4-bit reserved zero
field_id   = mbv.const(0xF, 4)        # 4-bit read-only chip ID

# 1. Direct write raises TypeError
try:
    reserved.value = 0x1
    assert False, "Constant write should fail"
except TypeError as exc:
    print(f"Direct write correctly blocked: {exc}")

# 2. Concatenate: [15:8]=DATA, [7:4]=RESERVED, [3:0]=ID
packed_reg = mbv.concat("PACKED", field_data, reserved, field_id)
assert packed_reg.value == 0x120F

# 3. Write through bus where constant bits match (no warning)
packed_reg.value = 0xAA0F
assert field_data.value == 0xAA
assert packed_reg.value == 0xAA0F

# 4. Write through bus with mismatched constant bits (warning emitted, constant unchanged)
with warnings.catch_warnings(record=True) as recorded:
    warnings.simplefilter("always")
    packed_reg.value = 0x55FF  # Writing F to reserved field 0

    assert packed_reg.value == 0x550F
    assert len(recorded) >= 1
    print(f"Warning intercepted: {recorded[0].message}")

print("Scenario 3 verified successfully!")
```

---

## Scenario 4: Combinational Logic Expressions and Deferred Evaluation

### Goal
Build logic gate expression trees (`&`, `|`, `^`, `~`, `<<`, `>>`) that evaluate dynamically whenever inputs change.

### Complete Runnable Code (`scenario_expr.py`)

```python
import rpkbin.mapbv as mbv

sig_a = mbv.var("A", 8, 0b1010_0000)
sig_b = mbv.var("B", 8, 0b0000_1111)

# Build expression tree nodes
expr_or  = sig_a | sig_b
expr_and = sig_a & 0xF0

print(f"expr_or type:    {type(expr_or).__name__}")  # MapBVExpr
print(f"expr_or initial: {expr_or:bin}")             # 0b10101111
assert expr_or.value == 0b1010_1111
assert expr_and.value == 0xA0

# Mutate source variable sig_a
sig_a.value = 0b1111_0000

# Expressions reflect updated source values dynamically
print(f"expr_or updated: {expr_or:bin}")             # 0b11111111
assert expr_or.value == 0b1111_1111
assert expr_and.value == 0xF0
print("Scenario 4 verified successfully!")
```

---

## Scenario 5: Symbolic Simulation with Context Dictionaries (`eval`)

### Goal
Evaluate logic expressions under what-if scenarios using context dictionaries without mutating actual register values.

### Complete Runnable Code (`scenario_eval.py`)

```python
import rpkbin.mapbv as mbv

reg_x = mbv.var("X", 8, 10)
reg_y = mbv.var("Y", 8, 20)

pipeline = (reg_x << 2) | reg_y
assert pipeline.value == 60

# Substitute hypothetical values via eval(ctx)
result_1 = pipeline.eval({"X": 1, "Y": 2})   # (1 << 2) | 2 = 6
result_2 = pipeline.eval({"X": 0, "Y": 255}) # (0 << 2) | 255 = 255

assert result_1 == 6
assert result_2 == 255

# Actual register values remain untouched
assert reg_x.value == 10
assert reg_y.value == 20
print("Scenario 5 verified successfully!")
```

---

## Scenario 6: Common Pitfalls and Boundary Checks

### 1. `==` Compares Identity, Not Value
```python
import rpkbin.mapbv as mbv

reg = mbv.var("R", 8, 0x55)

try:
    if reg == 0x55:
        pass
except TypeError as exc:
    print(f"Equality bug prevented: {exc}")

assert reg.value == 0x55
assert reg.value_eq(0x55)
```

### 2. Closed-Interval Slice Rules
```python
# Note: This snippet depends on 'reg' declared above (reg = mbv.var("R", 8, 0x55))
# Reversed slices are invalid (high must be >= low)
try:
    bad_slice = reg[0:7]
except IndexError as exc:
    print(f"Reversed slice caught: {exc}")

# Slices with steps are unsupported
try:
    bad_step = reg[7:0:2]
except ValueError as exc:
    print(f"Slice step caught: {exc}")
```

### 3. Overlapping Writable Bits Forbidden in `concat`
When concatenating bus slices, MapBV prevents ambiguous write targets and multiple driver conflicts by forbidding overlapping bit slices:
```python
# Note: If running this snippet independently, import rpkbin.mapbv as mbv
target = mbv.var("TARGET", 8)
try:
    bad_bus = mbv.concat("BAD_BUS", target[3:0], target[3:0])
except ValueError as exc:
    print(f"Overlapping write target caught: {exc}")
```
