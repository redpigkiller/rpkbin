# NumBV — Bit-True Fixed-Point Arithmetic

[![English](https://img.shields.io/badge/Language-English-blue.svg)](numbv.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](numbv_zh.md)

`NumBV` (Number BitVector) is a fixed-point simulation core for bit-true DSP pipeline verification.
It uses pure NumPy (`int64`) by default and optionally accelerates via JAX (XLA) with a single
`set_backend()` call — no external fixed-point library required.

It features a **two-layer API** separating everyday convenience from bit-exact pipeline staging,
**five hardware-aligned rounding modes** (including Xilinx DSP48's convergent rounding),
**explicit format tracking** through every arithmetic step, and an **optional JAX backend**
for transparent computation acceleration.

---

## Quick Start

```python
import rpkbin.numbv as nbv
```

### 1. Defining a Format

A `Format` describes the fixed-point representation: bit-width, fractional bits, signedness,
rounding, and overflow policy.

```python
# Q16.12 — 16-bit signed, 12 fractional bits, truncate, saturate
fmt = nbv.Format(16, 12)

# Custom policy
acc_fmt = nbv.Format(32, 22, rounding="round_half_even", overflow="saturate")
```

**Rounding modes:**

| Mode | Literal | Description | Hardware |
|------|---------|-------------|----------|
| Truncate | `"trunc"` | Floor (toward −∞) | Verilog `>>` default |
| Round half-up | `"round"` | Nearest, 0.5 → +∞ | Simple DSP adder |
| **Convergent** | `"round_half_even"` | Nearest, 0.5 → even | **Xilinx DSP48 default** |
| Ceiling | `"ceil"` | Toward +∞ | Special algorithms |
| Round to zero | `"round_to_zero"` | Toward zero | C integer truncation |

### 2. Creating Values

Use factory functions — do not instantiate `NumBV` directly.

```python
fmt = nbv.Format(16, 12)

a = nbv.scalar(1.5,       fmt=fmt)                        # scalar
b = nbv.array([0.5, 1.0], fmt=fmt)                        # 1-d array
c = nbv.zeros(fmt=fmt, shape=256)                          # array of zeros
d = nbv.from_bits(0xFF80, fmt=nbv.Format(16, 8, signed=True))  # raw bits
```

> **Note (float64 precision)**: `scalar()` and `array()` use float64 as an intermediate.
> When `frac > 48`, float64's 53-bit mantissa may introduce quantization error before rounding.
> Use `from_bits()` for bit-exact initialization at very high fractional precision.

### 3. Inspecting Values

```python
a.val    # → float/ndarray: real-number view
a.raw    # → int64 ndarray: signed raw integer
a.bits   # → int64 ndarray: unsigned bit pattern
a.hex    # → '0x00C0'
a.bin    # → '0b0000000011000000'
a.fmt    # → Format(...)
a.report()  # human-readable debug summary
```

### 4. Operator Layer (Convenience)

All arithmetic operators quantize the result back to the **left operand's format**.

```python
fmt = nbv.Format(16, 12)
a = nbv.scalar(0.75, fmt=fmt)
b = nbv.scalar(0.50, fmt=fmt)

y = a + b    # → Q16.12
p = a * b    # → Q16.12 (quantized back — not full-precision!)
z = -a       # → Q16.12
w = abs(a)   # → Q16.12
a += b       # in-place, format preserved
```


> **Important**: Because operators always quantize to the left operand's format, `a + b` can differ from `b + a`
> when `a` and `b` have different formats. For mixed-format work, prefer the function-level API.
>
> For multiplication, the convenience operator also follows the left operand's format policy, so `a * b`
> is intentionally a shortcut, not a full-precision DSP pipeline primitive.

### 5. Function Layer (Pipeline-Explicit)

For bit-true pipeline staging, use the function API to explicitly control the output format.

```python
# add / sub always require out_fmt
y = nbv.add(a, b, out_fmt=nbv.Format(32, 22))

# mul: full-precision by default, or quantize with out_fmt
p = nbv.mul(a, b)                    # → Q32.24 (full precision)
y = nbv.mul(a, b, out_fmt=acc_fmt)   # → acc_fmt (full-prec → quantize)
```

### 6. Format Conversion

```python
# quantize: preserve value, apply target fmt's rounding + overflow
y = x.quantize(nbv.Format(8, 4, rounding="round_half_even"))

# reinterpret: preserve bits, change interpretation (no rounding)
u    = x.reinterpret(signed=False)   # signed → unsigned view
lo   = x.reinterpret(width=8)        # take low 8 bits
wide = x.reinterpret(width=32)       # sign-extend (if source signed)

# clip: clamp real-number value, format unchanged
y = x.clip(-1.0, 1.0)
```

### 7. Bit Operations

```python
# get_bits / set_bits (inclusive, 0-based from LSB)
high_byte = x.get_bits(15, 8)           # → int or ndarray
field     = x.get_bits(7, 0, fmt=fmt8)  # → NumBV

x.set_bits(7, 0, 0xAB)                  # in-place write

# Bitwise one-liner via .bits + .with_bits
y = x.with_bits(x.bits & 0xFF00)        # AND mask, same format
y = x.with_bits(x.bits | other.bits)    # OR combine
```

---

## API Reference

### Format

```python
Format(width, frac, signed=True, rounding="trunc", overflow="saturate")
```

| Attribute | Description |
|-----------|-------------|
| `.width` | Total bit width |
| `.frac` | Fractional bits |
| `.signed` | Two's complement signed |
| `.int_bits` | Integer bits (may be negative) |
| `.scale` | `2 ** frac` |
| `.min_val` / `.max_val` | Real-number range |
| `.precision` | `1 / scale` — smallest representable step |
| `.replace(**changes)` | Return a modified copy |

### Factory Functions

| Function | Description |
|----------|-------------|
| `nbv.scalar(value, *, fmt)` | Create scalar NumBV from real value |
| `nbv.array(data, *, fmt)` | Create array NumBV from list or ndarray |
| `nbv.zeros(*, fmt, shape=None)` | All-zeros NumBV |
| `nbv.ones(*, fmt, shape=None)` | Ones (quantized 1.0) |
| `nbv.full(fill, *, fmt, shape=None)` | Fill NumBV |
| `nbv.from_bits(bits, *, fmt)` | Import raw bit pattern (no quantization) |

### Serialization

| Method | Description |
|--------|-------------|
| `a.to_dict()` | Export to a Python `dict` |
| `a.to_json()` | Export to a JSON string |
| `NumBV.from_dict(d)` | Restore from dict |
| `NumBV.from_json(s)` | Restore from JSON string |

### Backend Control

| Function | Description |
|----------|-------------|
| `nbv.set_backend("numpy")` | Use NumPy (default, always available) |
| `nbv.set_backend("jax")` | Use JAX / XLA acceleration |
| `nbv.get_backend()` | Return current backend name |

### Operators (quantize to left-operand format)

| Operator | Description |
|----------|-------------|
| `a + b`, `a += b` | Addition |
| `a - b`, `a -= b` | Subtraction |
| `a * b`, `a *= b` | Multiplication |
| `-a`, `abs(a)` | Negate / absolute value |
| `==`, `!=`, `<`, `<=`, `>`, `>=` | Comparison (frac-aligned) |

> Mixing `signed` and `unsigned` NumBVs raises `TypeError` at runtime.

### Function-Level API

| Function | Description |
|----------|-------------|
| `nbv.add(a, b, *, out_fmt)` | Add → `out_fmt` (required) |
| `nbv.sub(a, b, *, out_fmt)` | Subtract → `out_fmt` (required) |
| `nbv.mul(a, b, *, out_fmt=None)` | Full-precision multiply, optional quantize |
| `nbv.neg(a, *, out_fmt=None)` | Negate, optional quantize |

### Reduction / DSP Helpers

| Function | Description |
|----------|-------------|
| `nbv.sum(x, *, acc_fmt)` | Sequential accumulation, quantize each step |
| `nbv.dot(a, b, *, acc_fmt, out_fmt=None)` | Dot product with explicit accumulator |
| `nbv.mac(acc, a, b, *, acc_fmt)` | Multiply-accumulate: `acc + mul(a, b)` |

> **Performance Note (`sum` and `dot`)**: To guarantee bit-exactness, rounding and overflow must be applied *immediately* after each addition. Therefore, under the NumPy backend, these functions must use a **pure Python loop**, which can be slow for large arrays. This is the necessary tradeoff for accurate hardware simulation. For high performance, use the **JAX backend** and wrap your operations in `@jax.jit`.
>
> **Iteration Order**: `sum()` and `dot()` consume array inputs in flattened C-order (`reshape(-1)` semantics). This keeps multi-dimensional inputs deterministic without adding per-axis reduction rules.

### Format Inference

```python
safe = nbv.infer_add_format(fmt_a, fmt_b)   # lossless add format
safe = nbv.infer_mul_format(fmt_a, fmt_b)   # full-precision mul format
```

Read-only helpers that compute the minimum `Format` to hold the result without precision loss.
Rounding / overflow policy must be set by the caller.

---

## Backend Selection (NumPy / JAX)

Call `set_backend()` **once, before creating any NumBV objects**. All subsequent operations
use the chosen backend automatically.

```python
import rpkbin.numbv as nbv

nbv.set_backend("numpy")   # default — always available
nbv.set_backend("jax")     # XLA acceleration (requires: pip install jax)

print(nbv.get_backend())   # 'numpy' or 'jax'
```

**JAX backend features:**
- `jax_enable_x64` is enabled automatically (ensures correct int64/float64 behaviour)
- `NumBV` is registered as a JAX PyTree, enabling transparent use with `@jax.jit`
- All bit-true results are **bit-identical** between NumPy and JAX

```python
import jax
nbv.set_backend("jax")

@jax.jit
def run_fir(x, h):
    return nbv.dot(x, h, acc_fmt=acc_fmt)

y = run_fir(x, h)  # XLA compiled; subsequent calls use the cached kernel
```

> JAX CPU backend is fully supported on Windows. GPU support requires the matching
> CUDA driver and a JAX GPU build.

### NumPy vs JAX: When Each One Wins

Use **NumPy** when:
- You run a simulation only once or a few times
- Array sizes are small or medium
- You want the shortest startup latency and easiest debugging

Use **JAX** when:
- The pipeline shape is stable and you will run it many times
- You can wrap the hot path with `@jax.jit`
- Compile cost is acceptable in exchange for much faster steady-state execution

Practical rule of thumb:
- First call with JAX is often slower because it includes XLA compilation
- Repeated calls with the same shapes can be much faster than NumPy
- `sum()` and `dot()` benefit the most only when you reuse the compiled function enough times to amortize compile cost

For a simple local benchmark, run:

```bash
RUN_NUMBV_BENCHMARK=1 pytest tests/test_numbv_benchmark.py -q -s
```

---

## Serialization

```python
# Save
d = a.to_dict()   # → Python dict (raw + fmt)
s = a.to_json()   # → JSON string

# Restore
from rpkbin.numbv import NumBV
b = NumBV.from_dict(d)   # from dict
b = NumBV.from_json(s)   # from JSON string

assert b.fmt == a.fmt        # format fully preserved
assert all(b.bits == a.bits) # bits are identical
```

Serialization is backend-agnostic: the dict/JSON representation uses plain Python
`int`/`float`, so it is portable across platforms and backends.

---

## Bit-True FIR Pipeline Example

```python
import rpkbin.numbv as nbv

x_fmt   = nbv.Format(16, 12)
h_fmt   = nbv.Format(12, 10, rounding="round_half_even")
acc_fmt = nbv.Format(32, 22, rounding="round_half_even")
out_fmt = nbv.Format(16, 12, rounding="round_half_even", overflow="saturate")

x = nbv.array(input_samples, fmt=x_fmt)
h = nbv.array(fir_coeffs,    fmt=h_fmt)

# Full-precision element-wise multiply → accumulate → output
y = nbv.dot(x, h, acc_fmt=acc_fmt, out_fmt=out_fmt)
```

This models exactly how a Xilinx DSP48-based FIR filter behaves:
1. Each multiply captures the full `width_x + width_h` precision.
2. Each partial sum is rounded with convergent rounding before accumulation.
3. The final result is saturated to the output word length.

---

## Common Gotchas

### `a + b ≠ b + a` (different formats)

```python
a = nbv.scalar(1.0, fmt=nbv.Format(16, 12))
b = nbv.scalar(0.5, fmt=nbv.Format(8,  4))

y1 = a + b   # → Q16.12 (precision kept)
y2 = b + a   # → Q8.4  (may truncate or saturate!)
```

Use `nbv.add(a, b, out_fmt=...)` to make the output format explicit.

### `*` truncates (operator layer)

```python
p = a * b           # Q16.12 — truncated back to left operand format
p = nbv.mul(a, b)   # Q32.24 — full precision
```

Always use `nbv.mul()` in pipeline verification.

### `reinterpret()` is not `quantize()`

```python
y = x.quantize(fmt2)       # value preserved, bits change (rounding applied)
z = x.reinterpret(frac=8)  # bits preserved, numerical interpretation changes
```

### NumPy ufuncs are blocked

```python
# ✗ Wrong: np.add(a, b) is intercepted and raises TypeError
import numpy as np
np.add(a, b)          # TypeError: NumBV does not support numpy ufuncs

# ✓ Correct: use the nbv API or operators
nbv.add(a, b, out_fmt=out_fmt)
a + b
```

Bit-true precision depends on explicit format tracking. NumPy ufuncs bypass this mechanism.

### Python literals are quantized to the left operand format

```python
a = nbv.scalar(0.3, fmt=nbv.Format(8, 4))

y1 = a + 0.1   # 0.1 is quantized to a.fmt first
y2 = a * 0.1   # same rule
```

This is convenient for quick experiments, but in bit-true verification it can hide where quantization happened.
If the exact staging matters, convert constants explicitly with `nbv.scalar(..., fmt=...)` or use the function API.

### `clip()` quantizes the bounds too

```python
y = x.clip(-0.3, 0.3)
```

The bounds are specified in real-value space, but internally they are quantized to `x.fmt` before clipping.
That keeps clipping bit-true, but the effective threshold is the nearest representable value in the current format.

### Backend switch timing

`set_backend()` must be called **before** creating any NumBV objects.
Objects created after the switch use the new backend; previously created objects
retain their original `_raw` type. The safest pattern is to call it once at
the program entry point and not change it again.

---

## Writing Your Own Simulation Functions

If you want to build your own DSP operations on top of NumBV, such as an FFT stage, CIC block,
CORDIC step, or custom pipeline primitive, the safest pattern is:

1. Keep intermediate values in the raw integer domain whenever possible.
2. Make every format transition explicit with `quantize()` or function-level API boundaries.
3. Decide where rounding and overflow happen, and encode that in the function itself.
4. Use JAX only when the pipeline shape is stable enough to benefit from `@jax.jit`.

### Recommended pattern

```python
import rpkbin.numbv as nbv

def butterfly(a: nbv.NumBV, b: nbv.NumBV, *, stage_fmt: nbv.Format, out_fmt: nbv.Format):
    # Explicit stage math: no hidden output format
    s = nbv.add(a, b, out_fmt=stage_fmt)
    d = nbv.sub(a, b, out_fmt=stage_fmt)

    # If a later pipeline register truncates again, make that explicit too
    return s.quantize(out_fmt), d.quantize(out_fmt)
```

### NumPy-first workflow

- Prototype the algorithm with explicit stage formats
- Verify the rounding / overflow points against your hardware model
- Add tests that check `.bits` or `.raw`, not only `.val`

### JAX-accelerated workflow

```python
import jax
import rpkbin.numbv as nbv

nbv.set_backend("jax")

@jax.jit
def run_stage(x0, x1, stage_fmt, out_fmt):
    s = nbv.add(x0, x1, out_fmt=stage_fmt)
    d = nbv.sub(x0, x1, out_fmt=stage_fmt)
    return s.quantize(out_fmt), d.quantize(out_fmt)
```

### Practical authoring rules

- Prefer `nbv.add`, `nbv.sub`, `nbv.mul`, `nbv.dot`, and `quantize()` in reusable simulation blocks.
- Treat operators like `+` and `*` as convenience syntax, not as the canonical implementation of a pipeline stage.
- Keep stage format, accumulator format, and output format separate in the function signature.
- If a step is bit-true against hardware, document exactly where truncation, convergent rounding, or saturation occurs.
- For custom reductions or FFT-style staging, write the reduction order explicitly so the bit growth path is reviewable.

---

## TODO / Possible Future Additions

These are not blocking gaps for the current NumBV scope, but they may be worth revisiting later.

- Public bit-true shift / rescale helpers such as `shift_right`, `shift_left`, or `rescale`.
  These look convenient, but they are easy to design poorly: a shift API can accidentally hide where rounding,
  saturation, sign-extension, or register truncation happened. If added in the future, the format transition and
  rounding point should stay fully explicit.
- Axis-aware reductions such as `sum(axis=...)` or `dot(..., axis=...)`.
  The current API already supports deterministic flatten-then-accumulate behaviour. Axis-aware variants would only
  be worth adding if they can preserve the same bit-true clarity without introducing ambiguous reduction order.
- More DSP-oriented helper blocks, such as reusable butterfly / FIR-stage / accumulator-tree patterns.
  These are not core gaps because users can already build them from the existing function-level API, but a few
  carefully designed patterns could reduce repeated boilerplate in larger simulation platforms.
