# MapBV — Register & Bit Mapping

[![English](https://img.shields.io/badge/Language-English-blue.svg)](mapbv.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](mapbv_zh.md)

`MapBV` (Map BitVector) is a lightweight BitVector library built for IC design and verification. It allows you to describe registers, SRAM mappings, and logic operations using intuitive Python objects.

It features **bidirectional value synchronization** (changes to a register immediately reflect in its mapped sub-slices and vice-versa), **symbolic evaluation**, and beautifully formatted layout printing.

---

## Quick Start (User Guide)

To safely instantiate objects in `MapBV`, we strongly encourage always using our three provided factory functions: `var`, `const`, and `concat`.

### 1. Variables and Constants

```python
import rpkbin.mapbv as mbv

# Declare two 16-bit registers (variables), initially set to 0
reg0 = mbv.var("REG0", 16)
reg1 = mbv.var("REG1", 16)

# Declare a 2-bit constant, functionally always 0
padding = mbv.const(0, 2)
```

### 2. Slicing Bits

You can slice mapped variables much like slicing a standard Python list. By hardware convention, bounds are inclusive on both ends, formatted as `[MSB:LSB]`:

```python
reg0.value = 0xABCD

# Reading a slice reflects the parent register live
print(reg0[7:4].to_hex())  # → 0xA

# Writing to a slice modifies the parent register in-place
reg0[7:4].value = 0xF
print(reg0.to_hex())       # → 0xFBCD
```

### 3. Concatenation and Linking

When constructing buses or SRAM words, you can use `concat` to string together multiple sources (from MSB down to LSB). A concatenated MapBV forms a **bidirectional link** with its sources!

```python
# Create an 8-bit word from scratch: {reg0[3:0], padding, reg1[1:0]}
sram = mbv.concat("SRAM_00", reg0[3:0], padding, reg1[1:0])

# Mutating source registers updates SRAM live
reg0[3:0].value = 0x5
reg1[1:0].value = 0x2
print(sram.to_hex())       # → 0x52 (computed as 0x5 << 4 | 0x0 << 2 | 0x2)

# Conversely, writing to SRAM auto-distributes data back to registers
sram.value = 0xF3
print(reg0[3:0].to_hex())  # → 0x0F
print(reg1[1:0].to_hex())  # → 0x03
```
> **Note**: Trying to write `0xFF` to the SRAM would mean writing `0x3` into the inner constant `padding`. `MapBV` handles this by triggering a `UserWarning` and ignoring that particular bit field modification. A subsequent read to the `sram` object would thus return `0xF3`.

### 4. Manual Linking (`link`) and Detaching (`detach`)

While `concat` bundles variables at creation time, you can also declare an empty variable first and use `link` to bind it to other registers later.
If you ever want to "freeze" a mapped variable's state and break its connection to the source registers, you can use `detach`.

```python
# Create an empty 8-bit variable first
sram_b = mbv.var("SRAM_01", 8)

# Manually link it to other variables (total width must exactly match 8 bits)
sram_b.link(reg0[3:0], padding, reg1[1:0])
print(sram_b.to_hex())     # → Immediately reflects the live state of reg0, padding, and reg1

# Detach the link
sram_b.detach()

# After detaching, sram_b becomes an independent variable holding its last computed value
reg0[3:0].value = 0x0
print(sram_b.to_hex())     # → Retains its snapshotted value, unaffected by reg0 changing to 0
```

### 5. Logic Operators & Symbolic What-Ifs

`MapBV` intuitively supports familiar logic operations (`&`, `|`, `^`, `~`, `<<`, `>>`). Integer operands are masked to the expression width. You can also perform hypothesis runs—temporarily replacing certain register values via a context dictionary `eval`—without scrambling the authentic states of your variables.

```python
# Logic operations (returns an expr node)
result_expr = (reg0 & 0x00FF) | reg1
print(result_expr.value)

# What if REG0 were 0xAAAA? What would SRAM read?
simulated = sram.eval({"REG0": 0xAAAA, "REG1": 0x3})
print(hex(simulated))       # → 0xa3
```

---

## API Reference (Detailed Control)

### Object Creation (Factory Functions)

| API Function | Description |
| --- | --- |
| `mbv.var(name, width, value=0)` | Creates a named variable of size `width` bits (`"VAR"`). Optionally accepts an initial `value`. |
| `mbv.const(value, width)` | Creates an immutable constant (`"CONST"`). The `value` is automatically masked to bound within the `width`. |
| `mbv.concat(name, *parts)` | Creates and returns a variable named `name` automatically linked to `parts` (ordered MSB→LSB). Output width handles itself. |

---

### Core `MapBV` Properties

Every instance holds the following readable attributes:

- `.name` (`str`): Identifier name. `"Constant"` for constants, `"NAME[high:low]"` for slices.
- `.width` (`int`): Bit width representation.
- `.high` / `.low` (`int`): Bit index offset for the current object representing its bounds natively (lowest is 0).
- `.value` (`int`): Current numeric value (Readable and Writable). Dynamically calculates references for linked or sliced segments.
- `.kind` (`str`): Returns `"CONST"`, `"VAR"`, or `"SLICE"`.
- `.is_const` (`bool`): Helper boolean indicating if it's a constant.
- `.is_linked` (`bool`): Helper boolean indicating whether this instance possesses child concatenations via `link`.

---

### Mechanics Details

#### 1. Slicing (`SLICE` and `__getitem__`)
- **Syntax**: `bv[high:low]` or single-bit `bv[bit]`.
- Note that slices are **inclusive** on both ends. Thus, `[7:0]` spans exactly 8 bits.
- **Restriction**: Only a `VAR` can call `.link()`; `CONST` and `SLICE` nodes cannot become mapping targets. To construct mappings that target a subset, define a primary `var()` and link it, keeping architectures hierarchical.

#### 2. Linking (`link` and `detach`)
Besides using `concat()` to combine sources out of the gate, you can call parameter linkages manually.
- **`link(*parts)`**:
  - Connects several `MapBV` objects (passed MSB → LSB) onto the caller variable.
  - The caller must possess a `width` equal to the sum widths of all the `parts`. Violating this throws a `ValueError`.
  - Writable bits in `parts` must not overlap; ambiguous bidirectional mappings raise `ValueError`.
  - Circular references and cross-linking preventions are strictly enforced.
  - Doing this redundantly on a variable already holding connections throws a `UserWarning` and destructively overwrites the mapping.
- **`detach()`**:
  - Drops the links to all `parts`.
  - At the very moment of detachment, the engine resolves the currently evaluated `.value` and commits it structurally as an independent `.value`. Source registers no longer affect this node once detached.

#### 3. Output Dumping (`to_hex`, `to_bin`, `__str__`)
- **Hex & Bin**: Utilize `bv.to_hex()` (returns eg. `0x00FF`) or `bv.to_bin()` (`0b00001010`). Standard Python native formats work beautifully too: passing `f"{bv:hex}"` is identical to calling `.to_hex()`.
- **String Prints**: Simple `print(bv)` spits out a heavily formatted mapping tree that resolves underlying hexadecimal values along alongside parameter names for peak readability.

```python
print(sram)
# Structured layout example format:
# SRAM_00[7:0] (0xF3)
#   [7:4] 0x0F  <- REG0[3:0]
#   [3:2] 0x00  <- Constant
#   [1:0] 0x03  <- REG1[1:0]
```

#### 4. The equality `==` Operator vs `value_eq`
- Under `MapBV`, the **`==` evaluates Python Object Identity** (`is`), not the mathematical numeric inside. This exists predominantly so you can safely inject `MapBV` instances directly into `set`s and dictionaries without destructive collisions.
- If evaluating math values side-by-side, explicitly utilize `.value_eq(other)` or just directly interrogate via `.value == other.value`.
