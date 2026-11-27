# MapBV Practical Usage Scenarios

[繁體中文](../../zh/mapbv/scenarios.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [API Reference](reference.md)

Six self-contained scenarios covering hardware register slicing, bus concatenation, constant bit enforcement, combinational logic trees, symbolic evaluation, and boundary checks.

## Table of Contents

1. [Scenario 1: Register Field Slicing and Live Synchronization](#scenario-1-register-field-slicing-and-live-synchronization)
2. [Scenario 2: Hardware Bus Concatenation (`concat`) and Bit Dispatching](#scenario-2-hardware-bus-concatenation-concat-and-bit-dispatching)
3. [Scenario 3: Reserved Bits and Constant Enforcement](#scenario-3-reserved-bits-and-constant-enforcement)
4. [Scenario 4: Combinational Logic Expressions and Deferred Evaluation](#scenario-4-combinational-logic-expressions-and-deferred-evaluation)
5. [Scenario 5: Symbolic Simulation with Context Dictionaries (`eval`)](#scenario-5-symbolic-simulation-with-context-dictionaries-eval)
6. [Scenario 6: Common Pitfalls and Boundary Checks](#scenario-6-common-pitfalls-and-boundary-checks)

## Scenario 1: Register Field Slicing and Live Synchronization

### Goal
Model a 32-bit control register `CTRL_REG` with fields `[31]` (`enable`), `[23:16]` (`burst_len`), and `[7:0]` (`priority`). Writing to any field slice updates the parent register immediately, and writing the parent updates all slices.

```python
import rpkbin.mapbv as mbv

ctrl_reg = mbv.var("CTRL_REG", 32, 0x0000_0000)

enable = ctrl_reg[31]
burst_len = ctrl_reg[23:16]
priority = ctrl_reg[7:0]

enable.value = 1
burst_len.value = 0x10
priority.value = 0x05

print(f"CTRL_REG total value: {ctrl_reg:hex}")
assert ctrl_reg.value == 0x8010_0005

ctrl_reg.value = 0x00FF_0000
print(f"enable field:    {enable.value}")
print(f"burst_len field: {burst_len:hex}")
print(f"priority field:  {priority:hex}")

assert enable.value == 0
assert burst_len.value == 0xFF
assert priority.value == 0
print("Scenario 1 verified successfully!")
```

Output:
```text
CTRL_REG total value: 0x80100005
enable field:    0
burst_len field: 0xFF
priority field:  0x00
Scenario 1 verified successfully!
```

## Scenario 2: Hardware Bus Concatenation (`concat`) and Bit Dispatching

### Goal
Pack multiple registers into a wider bus view so writes to the bus split and dispatch back to each underlying register.

```python
import rpkbin.mapbv as mbv

reg_a = mbv.var("REG_A", 4, 0x1)
reg_b = mbv.var("REG_B", 4, 0x2)
reg_c = mbv.var("REG_C", 8, 0x34)

# MSB -> LSB: [15:12]=REG_A, [11:8]=REG_B, [7:0]=REG_C
sram_bus = mbv.concat("SRAM_BUS", reg_a, reg_b, reg_c)

print(f"Concatenated Bus: {sram_bus.name}, width: {sram_bus.width} bits")
print(f"Initial value:    {sram_bus:hex}")
assert sram_bus.value == 0x1234

print("\n--- Structural Mapping ---")
print(str(sram_bus))

sram_bus.value = 0xABCD
print("\nAfter writing 0xABCD:")
print(f"REG_A: {reg_a:hex}")
print(f"REG_B: {reg_b:hex}")
print(f"REG_C: {reg_c:hex}")

assert reg_a.value == 0xA
assert reg_b.value == 0xB
assert reg_c.value == 0xCD
print("Scenario 2 verified successfully!")
```

Output:
```text
Concatenated Bus: SRAM_BUS, width: 16 bits
Initial value:    0x1234

--- Structural Mapping ---
SRAM_BUS[15:0] (0x1234)
  [15:12] 0x1  <- REG_A
  [11:8] 0x2   <- REG_B
  [7:0] 0x34   <- REG_C

After writing 0xABCD:
REG_A: 0xA
REG_B: 0xB
REG_C: 0xCD
Scenario 2 verified successfully!
```

## Scenario 3: Reserved Bits and Constant Enforcement

### Goal
Block direct writes to constants (`TypeError`) while ignoring writes to constant bits inside a `concat()` bus (emitting `UserWarning` only when the written bits differ).

```python
import warnings
import rpkbin.mapbv as mbv

field_data = mbv.var("DATA", 8, 0x12)
reserved = mbv.const(0x0, 4)
field_id = mbv.const(0xF, 4)

try:
    reserved.value = 0x1
    assert False, "Constant write should fail"
except TypeError as exc:
    print(f"Direct write correctly blocked: {exc}")

packed_reg = mbv.concat("PACKED", field_data, reserved, field_id)
assert packed_reg.value == 0x120F

# Matching constant bits: no warning
packed_reg.value = 0xAA0F
assert field_data.value == 0xAA
assert packed_reg.value == 0xAA0F

# Mismatched constant bits: warning emitted, constant unchanged
with warnings.catch_warnings(record=True) as recorded:
    warnings.simplefilter("always")
    packed_reg.value = 0x55FF

    assert packed_reg.value == 0x550F
    assert len(recorded) >= 1
    print(f"Warning intercepted: {recorded[0].message}")

print("Scenario 3 verified successfully!")
```

Output:
```text
Direct write correctly blocked: Cannot write 1 to a constant MapBV (width=4).
Warning intercepted: Write of 0xF to constant bits of Constant (width=4, value=0x0) ignored.
Scenario 3 verified successfully!
```

## Scenario 4: Combinational Logic Expressions and Deferred Evaluation

### Goal
Build logic expression trees (`&`, `|`, `^`, `~`, `<<`, `>>`) whose `.value` always reflects updated input registers.

```python
import rpkbin.mapbv as mbv

sig_a = mbv.var("A", 8, 0b1010_0000)
sig_b = mbv.var("B", 8, 0b0000_1111)

expr_or = sig_a | sig_b
expr_and = sig_a & 0xF0

print(f"expr_or type:    {type(expr_or).__name__}")
print(f"expr_or initial:  {expr_or:bin}")
print(f"expr_and initial: {expr_and:hex}")
assert expr_or.value == 0b1010_1111
assert expr_and.value == 0xA0

sig_a.value = 0b1111_0000

print(f"expr_or updated:  {expr_or:bin}")
print(f"expr_and updated: {expr_and:hex}")
assert expr_or.value == 0b1111_1111
assert expr_and.value == 0xF0
print("Scenario 4 verified successfully!")
```

Output:
```text
expr_or type:    MapBVExpr
expr_or initial:  0b10101111
expr_and initial: 0xA0
expr_or updated:  0b11111111
expr_and updated: 0xF0
Scenario 4 verified successfully!
```

## Scenario 5: Symbolic Simulation with Context Dictionaries (`eval`)

### Goal
Evaluate logic expressions under hypothetical register values via `eval(ctx)` without mutating actual register state.

```python
import rpkbin.mapbv as mbv

reg_x = mbv.var("X", 8, 10)
reg_y = mbv.var("Y", 8, 20)

pipeline = (reg_x << 2) | reg_y
assert pipeline.value == 60

result_1 = pipeline.eval({"X": 1, "Y": 2})
result_2 = pipeline.eval({"X": 0, "Y": 255})

assert result_1 == 6
assert result_2 == 255
assert reg_x.value == 10
assert reg_y.value == 20
print(f"result_1={result_1}, result_2={result_2}")
print("Scenario 5 verified successfully!")
```

Output:
```text
result_1=6, result_2=255
Scenario 5 verified successfully!
```

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

Output:
```text
Equality bug prevented: MapBV == int compares identity and is almost certainly a bug; use `bv.value == n` or `bv.value_eq(n)`
```

### 2. Closed-Interval Slice Rules

```python
import rpkbin.mapbv as mbv

reg = mbv.var("R", 8, 0x55)

try:
    bad_slice = reg[0:7]
except IndexError as exc:
    print(f"Reversed slice caught: {exc}")

try:
    bad_step = reg[7:0:2]
except ValueError as exc:
    print(f"Slice step caught: {exc}")
```

Output:
```text
Reversed slice caught: Slice [0:7] is reversed: write bv[high:low] with high >= low
Slice step caught: MapBV slices do not support a step: use bv[high:low]
```

### 3. Overlapping Writable Bits Forbidden in `concat`

MapBV rejects overlapping writable bit ranges during `concat()` construction to prevent conflicting write drivers.

```python
import rpkbin.mapbv as mbv

target = mbv.var("TARGET", 8)
try:
    bad_bus = mbv.concat("BAD_BUS", target[3:0], target[3:0])
except ValueError as exc:
    print(f"Overlapping write target caught: {exc}")
```

Output:
```text
Overlapping write target caught: Linked parts contain overlapping writable bits
```
