# `rpk-calc`

[繁體中文](../../zh/calc/README.md) · [Documentation Index](../README.md) · [Architecture](architecture.md)

`rpk-calc` is an engineering and fixed-point (FXP) calculator with a standalone Simple mode GUI, a Python Studio IPython workspace, a headless `--eval` CLI, and the `rpkbin.app.calc` Python API (`fxp`, `fxpi`, `launch`, `main`).

> **Compatibility.** Requires Python 3.11 or newer. Install `pip install -e ".[calc]"` for the full Qt/IPython GUI, or `pip install -e ".[fixed]"` for headless `fxp`/`fxpi` without Qt.

---

## CLI Reference

### Syntax

```text
rpk-calc [-h] [-V] [-c CONFIG] [-e EVAL_EXPR | --simple | --python | --mode {simple,python}]
python -m rpkbin.app.calc [-h] [-V] [-c CONFIG] [-e EVAL_EXPR | --simple | --python | --mode {simple,python}]
```

### Description

- `rpk-calc` opens the desktop window using `launch_mode` from the active settings file (default `"simple"`; `"last"` reopens `last_mode`).
- `rpk-calc --simple` and `rpk-calc --mode simple` open the GUI in Simple standalone fixed-point calculator mode.
- `rpk-calc --python`, `rpk-calc --studio`, and `rpk-calc --mode python` open the GUI in Python Studio workspace mode. Switching back to Simple keeps an open Studio session alive, and each mode keeps separate variables.
- `rpk-calc -e EVAL_EXPR` / `rpk-calc --eval EVAL_EXPR` creates a headless `WorkspaceSession` (`seed_demo_vars=False`), evaluates `EVAL_EXPR`, prints the result to stdout, and exits without starting Qt or an IPython kernel. Passing an empty string `""` runs headlessly and exits `0` with no output.
- `-c CONFIG` / `--config CONFIG` selects a settings JSON file (default `~/.rpkbin/calc_settings.json`) and its sibling `startup.py` for both GUI launch and `--eval`.
- `-V` / `--version` prints `rpk-calc <version>` and exits.

### Examples

Print the version and evaluate an expression headlessly:

```bash
rpk-calc --version
rpk-calc --eval "1+2"
rpk-calc --eval "fxp('0.75', 'Q4.4')"
```

Output:

```text
rpk-calc 0.3.0
ans = 3
ans = 0.75  [Q4.4 | Hex: 0x0C | 2's: 12]
```

Evaluate with an isolated settings file or launch Python Studio directly:

```bash
rpk-calc --config path/to/custom_settings.json --eval "fxp('0.5', 'Q4.0')"
rpk-calc --studio
```

### Options

| Flag | Env Var | Default | Description |
|---|---|---|---|
| `-h`, `--help` | (none) | (none) | Show the CLI help message and exit. |
| `-V`, `--version` | (none) | (none) | Print `rpk-calc <version>` from `rpkbin.__version__` and exit. |
| `-c CONFIG`, `--config CONFIG` | (none) | `~/.rpkbin/calc_settings.json` | Path to the settings JSON file (and sibling `startup.py`) for GUI and `--eval`. |
| `-e EVAL_EXPR`, `--eval EVAL_EXPR` | (none) | `None` | Evaluate a Python/FXP expression headlessly and print the result. Mutually exclusive with mode flags. |
| `--simple` | (none) | `launch_mode` (`simple`) | Launch GUI in Simple standalone fixed-point mode. Mutually exclusive with `--eval`, `--python`/`--studio`, and `--mode`. |
| `--python`, `--studio` | (none) | `launch_mode` (`simple`) | Launch GUI in Python Studio workspace mode. Mutually exclusive with `--eval`, `--simple`, and `--mode`. |
| `--mode {simple,python}` | (none) | `launch_mode` (`simple`) | Select GUI launch mode explicitly (`simple` or `python`). Mutually exclusive with `--eval`, `--simple`, and `--python`/`--studio`. |

`rpk-calc` CLI flags do not read environment variables. Persistent preferences live in the JSON settings file.

### Exit Codes and Tips

- `0`: Normal GUI exit or successful `--eval` run.
- `1`: Syntax or runtime error during `--eval` (the traceback or error message is printed to `stderr`).
- `2`: Invalid or conflicting CLI flags (from `argparse`), or missing optional packages (`pip install "rpkbin[calc]"`).
- `--eval` checks for `apytypes` and `matplotlib`; GUI launch additionally checks for `PySide6`, `qtconsole`, `ipykernel`, and `jupyter_client`.
- If the default config directory is unwritable during `--eval`, startup initialization skips writing and evaluation continues.

---

## Python API (`rpkbin.app.calc`)

### Syntax

```python
from rpkbin.app.calc import fxp, fxpi, launch, main

val = fxp(x, fmt=None, *, bits=None, wl=None, fwl=None, int_bits=None, frac_bits=None, signed=True, quant=None, overflow=None)
raw = fxpi(x, fmt=None, *, bits=None, wl=None, fwl=None, int_bits=None, frac_bits=None, signed=True)
code = launch(argv=None, *, mode=None, config_path=None)
code = main(argv=None)
```

### Description

- `val = fxp(x, fmt=None, ...)` quantizes a scalar (`int`, `float`, `Decimal`, `Fraction`, `APyFixed`, or decimal string) or array/sequence (`list`, `tuple`, `np.ndarray`, `APyFixedArray`) into `RpkFixed` or `RpkFixedArray`, recording conversion-reference metadata (`target_real`/`target_values`, `is_saturated`/`saturated_mask`). When `quant` or `overflow` is `None`, it reads `default_quant` (`"RND"`) and `default_overflow` (`"SAT"`) from the active `CalcConfig` (bound to the live session in Studio and `--eval`).
- `raw = fxpi(x, fmt=None, ...)` reinterprets the low `wl` bits (`raw & ((1 << wl) - 1)`) of an integer, `APyFixed`, or array/sequence in the given hardware format, returning `RpkFixed` or `RpkFixedArray` with no conversion-reference metadata (`target_real=None`).
- `code = launch(argv=None, *, mode=None, config_path=None)` opens the Qt desktop shell (`CalculatorWindow`) in `mode` (`None`, `"simple"`, or `"python"`) using `config_path` (or `~/.rpkbin/calc_settings.json`) and returns the Qt exit code.
- `code = main(argv=None)` runs the CLI argument parser and dispatcher (same entry point as `rpk-calc`).

`fxp` and `fxpi` are lazily exported via PEP 562 `__getattr__` and listed in `__dir__` alongside `launch` and `main`.

### Examples

#### 1. Minimal Example: Quantize Values and Reinterpret Raw Bits

Quantize `"0.75"` as signed `Q4.4` and reinterpret `0xFF` as `Q4.4`.

```python
from rpkbin.app.calc import fxp, fxpi

val = fxp("0.75", "Q4.4")
raw = fxpi(0xFF, "Q4.4")
print(float(val), hex(val.to_bits()))
print(float(raw), hex(raw.to_bits()))
```

Output:

```text
0.75 0xc
-0.0625 0xff
```

#### 2. Realistic Example: Precision Strings, Rounding Modes, Saturation, and Bit Masking

Pass exact `Decimal` strings to preserve decimal precision before binary quantization, inspect array saturation masks, and mask wider integers with `fxpi`.

```python
from decimal import Decimal
from rpkbin.app.calc import fxp, fxpi

tie_even = fxp(Decimal("0.5"), "Q8.0", quant="RND_EVEN")
tie_up = fxp(Decimal("0.5"), "Q8.0", quant="RND")
arr = fxp([0.25, 10.0], "Q4.4", overflow="SAT")
masked = fxpi(300, bits=8, frac_bits=4)

print(float(tie_even), float(tie_up))
print(arr.to_numpy().tolist(), arr.saturated_mask.tolist())
print(float(masked), hex(masked.to_bits()))
```

Output:

```text
0.0 1.0
[0.25, 7.9375] [False, True]
2.75 0x2c
```

### Input Arguments

| Name | Type | Default | Description |
|---|---|---|---|
| `x` | scalar or array | required | For `fxp`: `int`, `float`, `Decimal`, `Fraction`, `APyFixed`, decimal `str`, or `list`/`tuple`/`np.ndarray`/`APyFixedArray`. For `fxpi`: `int` (or `__index__`), `APyFixed`, or array/sequence of integers. |
| `fmt` | `str \| FxpSpec \| None` | `None` | Format string (`"Q4.4"`, `"UQ8.8"`, `"sfix16_En12"`, or uniquely reversible custom template) or `FxpSpec`. Omit when supplying explicit width keyword arguments. |
| `argv` | `Sequence[str] \| None` | `None` | Argument list for `launch` / `main` (defaults to `sys.argv` / `sys.argv[1:]`). |

### Name-Value Arguments

| Name | Type | Default | Description |
|---|---|---|---|
| `bits` / `wl` | `int \| None` | `None` | Total hardware word length in bits (`1` to `256`). Mutually exclusive with each other and with `fmt`. |
| `fwl` / `frac_bits` | `int \| None` | `None` | Fractional bit width (can be negative, zero, or `> wl`). Mutually exclusive with each other and with `fmt`. |
| `int_bits` | `int \| None` | `None` | Integer bit width (includes sign bit for signed formats). Pair with exactly one of `wl`/`bits` or `fwl`/`frac_bits` (passing all three raises `ValueError`). |
| `signed` | `bool` | `True` | `True` for signed two's-complement, `False` for unsigned when using explicit width keywords. |
| `quant` | `str \| None` | `None` | (`fxp` only) Rounding mode: `"RND"`, `"RND_EVEN"`, `"TRN"`, or `"TRN_ZERO"`. `None` uses active `CalcConfig.default_quant` (`"RND"`). |
| `overflow` | `str \| None` | `None` | (`fxp` only) Overflow policy: `"SAT"` or `"WRAP"`. `None` uses active `CalcConfig.default_overflow` (`"SAT"`). |
| `mode` | `str \| None` | `None` | (`launch` only) `"simple"`, `"python"`, or `None` to use `CalcConfig.launch_mode`. |
| `config_path` | `Path \| str \| None` | `None` | (`launch` only) Override `~/.rpkbin/calc_settings.json`. |

### Output

- `fxp` returns `RpkFixed` (scalar) or `RpkFixedArray` (array/sequence), carrying `is_unsigned`, `hw_wl`, `target_real` / `target_values`, and `is_saturated` / `saturated_mask`.
- `fxpi` returns `RpkFixed` or `RpkFixedArray` with `is_unsigned` and `hw_wl`, and `target_real = None` (no conversion reference).
- `launch` and `main` return an `int` process exit code.

### Tips and Limitations

- You cannot combine `fmt` with explicit width keywords (`bits`, `wl`, `fwl`, `int_bits`, `frac_bits`).
- `fxp("0x15", "Q4.4")` raises `ValueError` because `fxp` accepts decimal numeric strings only; pass integers to `fxpi(0x15, "Q4.4")` to interpret raw bits.
- `fxpi` rejects `bool` and `np.bool_` with `TypeError` and does not accept `quant` or `overflow`.
- In this tool's Q notation, `Qm.n` includes the sign bit in `m`, so `wl = m + n` (for example, `Q1.15` is 16 bits total). `UQm.n` is unsigned with `wl = m + n`.

---

## Simple Mode

Simple mode runs locally in the Qt process with exact rational (`Fraction`) and `Decimal` math, requiring no IPython kernel. Use the middle control bar to switch views (**Keypad only**, **Bits only**, **Keypad and bits**, **Collapse panels**), reset the primary format to **Int32** (`wl=32, fwl=0, signed=True`), pick or pin formats in the FXP history dropdown, open the **FXP Format & Quantization** drawer (`Signed`/`Unsigned`, `WL`, `FWL`, `Quantization`, `Overflow`), or add parallel **+Cmp** comparison rows (`FXP2`, `FXP3`, ...).

### Radices and `DEC` FXP-Unscaled Semantics

| Row | Format / Display Rule | Input Behavior |
|---|---|---|
| `HEX` | Unsigned raw bits in base 16, grouped every 4 digits (`0x1 0015`, `0xFF`). | Unsigned raw integer in `[0, 2^wl - 1]`. Spaces and `_` are ignored; `+`/`-` after `0x` is rejected. |
| `DEC` | Unscaled signed integer underlying the active FXP format (`raw_uint_to_signed_int`). | Entering a decimal integer scales it by `2^-fwl` (`value * 2^-fwl`). |
| `OCT` | Unsigned raw bits in base 8, grouped every 3 digits (`0o200 025`). | Unsigned raw integer in `[0, 2^wl - 1]`. Spaces and `_` are ignored; `+`/`-` after `0o` is rejected. |
| `BIN` | Unsigned raw bits in base 2, zero-padded to a 4-bit nibble boundary (`0b 1111 1111`; e.g. `5` shows as `0b 0101`). | Unsigned raw integer in `[0, 2^wl - 1]`. Spaces and `_` are ignored; `+`/`-` after `0b` is rejected. |
| `REAL` | Exact unquantized rational input or expression result (`display_fraction`). | Decimal literal or scientific notation (`[eE][+-]?[0-9]{1,3}`) parsed exactly before quantization. |
| `FXP` | Quantized value `raw_int * 2^-fwl` under the primary FXP format (defaults to `Int32`). | Same decimal/scientific input as `REAL`, committed and quantized under active `Quantization` and `Overflow`. |

Because `DEC` is always bound to the unscaled integer behind the active FXP format, both display and input reflect `2^-fwl` scaling. Select `Q4.4` (`wl=8, fwl=4`) and paste `0xFF`: `DEC` shows `-1` and `REAL`/`FXP` show `-0.0625`. When `DEC` is the active row, entering `1` sets raw bits `1` (`0.0625` in `REAL`), and entering `-1` sets `0xFF` (`-0.0625`). Prefix literals (`0x`, `0o`, `0b`) always parse as unsigned raw bits in any radix mode.

### Operators, `NAND`/`NOR`, and Precedence

Expressions support up to 512 characters and 64 levels of parenthesis nesting, ordered from lowest to highest precedence:

1. Bitwise OR / NOR: `|`, `NOR` (case-insensitive unless shadowed by an entry in `operands`; `~(left | right)` masked to `wl`)
2. Bitwise XOR: `^`
3. Bitwise AND / NAND: `&`, `NAND` (case-insensitive unless shadowed by an entry in `operands`; `~(left & right)` masked to `wl`)
4. Bitwise shifts: `<<`, `>>` (the right-hand shift-count subexpression, including parenthesized arithmetic such as `0x01 << (1 + 2)`, evaluates with `unscaled=True` as an integer in `0` to `256` even when `fwl != 0`)
5. Addition and subtraction: `+`, `-`
6. Multiplication, division, and remainder: `*`, `/`, `%`
7. Unary signs and complement: `+`, `-`, `~`
8. Parentheses: `(...)`

All bitwise operators (`&`, `|`, `^`, `NAND`, `NOR`, `~`) and the left operand of shifts require `value * 2^fwl` to be an exact integer. Values with fractional bits finer than `2^-fwl` raise `ValueError`. On the keypad, clicking **NOT** (`~`) complements the current `raw_uint` immediately.

### Four Bit-Shift Modes

Select the active shift mode above the bitwise keypad row before evaluating `<<` or `>>` (shift count `n` in `0..256`; `n == 0` leaves the value and `carry_bit` unchanged):

| Mode | Keypad Label | `<<` (Left Shift) | `>>` (Right Shift) | Carry Bit (`carry_bit`) |
|---|---|---|---|---|
| `arithmetic` (default) | **Arith** | Arithmetic/logical left shift (`signed_val << n`). | Arithmetic right shift (`signed_val >> n`): sign-extends MSB for signed formats, zero-fills for unsigned. | Last shifted-out bit if `n <= wl`; for `n > wl`, `0` on `<<` and sign bit (`1` if negative else `0`) on `>>`. |
| `logical` | **Logic** | Unsigned left shift (`(u << n) & mask`). | Unsigned right shift (`u >> n`): always zero-fills MSB. | Last shifted-out bit if `n <= wl`, else `0`. |
| `rotate` | **Rot** | Circular `wl`-bit left rotation by `k = n % wl`. | Circular `wl`-bit right rotation by `k = n % wl`. | For `k > 0`: `<<` sets `carry_bit = res & 1`; `>>` sets `carry_bit = (res >> (wl - 1)) & 1`. Unchanged when `k == 0`. |
| `rotate_carry` | **Rot·C (0/1)** | Circular `(wl + 1)`-bit left rotation of `[carry_bit \| u]` by `k = n % (wl + 1)`. | Circular `(wl + 1)`-bit right rotation of `[carry_bit \| u]` by `k = n % (wl + 1)`. | Bit `wl` of the rotated `(wl + 1)`-bit ring (unchanged when `k == 0`). |

Pressing **C** or **Esc** resets `carry_bit` to `0`. Failed expressions roll back `carry_bit` and leave the previous value intact.

Example outcomes under `Q8.0` (`wl=8`; `display("HEX")` does not zero-pad to `wl` bits):
- `0x80 >> 1` produces `0xC0` (`-64`) in **Arith**, and `0x40` (`64`) in **Logic**.
- `0x81 << 1` produces `0x3` in **Rot**, and with `carry_bit = 0` produces `0x2` (`carry_bit = 1`) in **Rot·C**, followed by `0x02 >> 1` returning `0x81` (`carry_bit = 0`).

In Python, the internal `SimpleCalcEngine` class (`rpkbin.app.calc.local_engine`) implements these Simple-mode rules directly:

```python
from rpkbin.app.calc.core.config import CalcConfig
from rpkbin.app.calc.local_engine import SimpleCalcEngine

eng = SimpleCalcEngine(CalcConfig())

# 1. DEC FXP-unscaled semantics under Q4.4 (wl=8, fwl=4)
eng.set_primary("Q4.4")
eng.paste("0xFF", "HEX")
print("Q4.4 0xFF ->", eng.display("DEC"), eng.display("REAL"), eng.display("FXP"))
eng.paste("-1", "DEC")
print("Q4.4 DEC -1 ->", eng.display("HEX"), eng.display("REAL"))

# 2. NAND / NOR under Q4.4
eng.evaluate("0xF0 NAND 0x0F", "HEX")
print("NAND ->", eng.display("HEX"))
eng.evaluate("0x00 NOR 0x0F", "HEX")
print("NOR  ->", eng.display("HEX"))

# 3. All 4 shift modes under Q8.0 (wl=8)
eng.set_primary("Q8.0")
for mode in ("arithmetic", "logical"):
    eng.set_shift_mode(mode)
    eng.evaluate("0x80 >> 1", "HEX")
    print(f"{mode:10s} 0x80>>1 -> {eng.display('HEX')} ({eng.display('DEC')}), carry={eng.carry_bit}")

eng.set_shift_mode("rotate")
eng.evaluate("0x81 << 1", "HEX")
print(f"rotate     0x81<<1 -> {eng.display('HEX')}, carry={eng.carry_bit}")

eng.carry_bit = 0
eng.set_shift_mode("rotate_carry")
eng.evaluate("0x81 << 1", "HEX")
print(f"rot_carry  0x81<<1 -> {eng.display('HEX')}, carry={eng.carry_bit}")
eng.evaluate("0x02 >> 1", "HEX")
print(f"rot_carry  0x02>>1 -> {eng.display('HEX')}, carry={eng.carry_bit}")
```

Output:

```text
Q4.4 0xFF -> -1 -0.0625 -0.0625
Q4.4 DEC -1 -> 0xFF -0.0625
NAND -> 0xFF
NOR  -> 0xF0
arithmetic 0x80>>1 -> 0xC0 (-64), carry=0
logical    0x80>>1 -> 0x40 (64), carry=0
rotate     0x81<<1 -> 0x3, carry=1
rot_carry  0x81<<1 -> 0x2, carry=1
rot_carry  0x02>>1 -> 0x81, carry=0
```

### Bit Select, Variables, and History

- **Bit Select**: Toggle individual bits by clicking 4-bit nibble cells, click **~ Invert** or **0 Zero** across the full word, toggle **MSB↔LSB** display order, or read and write an inclusive `[high:low]` unsigned bit field.
- **Variables sidebar**: Includes preset slots `M` (also written by **MS**) and `A` through `F`, plus **+ Add** for custom Python-identifier slots. Use **STO**, **RCL**, **Delete**, or **F2** (rename). Recalling a variable stored from `HEX`, `DEC`, `OCT`, or `BIN` loads its raw bits into the active format; recalling from `REAL` or `FXP` commits its exact rational value.
- **History sidebar**: Stores up to the last 100 evaluated expressions; click any card to recall its snapshot.

---

## Python Studio Mode

Select **Python** or run `rpk-calc --python` (`rpk-calc --studio`) to open Python Studio. Switching back to Simple keeps the Studio kernel alive, and Studio maintains its own workspace namespace separately from Simple mode variables.

### Open a Clean Workspace

From the repository root, install the optional dependencies and launch Studio:

```powershell
pip install -e ".[calc]"
rpk-calc --python
```

The GUI starts at a clean `In[1]` prompt without startup banners or demo variables. Ordinary Python semantics apply: only expressions returning a non-`None` value update `ans`.

- **Keyboard shortcuts**: **Enter** runs a complete input or continues an incomplete block, **Shift+Enter** runs the whole cell, **Ctrl+Enter** inserts a newline, and **Tab** triggers attribute completion.
- **Built-in magics**: `whos` / `who` (accepting optional variable names in Studio; no arguments in `--eval`) list workspace variables, `clc` clears the command display without deleting variables, and `clear` / `clear all` clears user variables while keeping configured auto-imports and cached startup symbols.
- **Header controls**: **Idle** / **Stop** interrupts the kernel (native C/OS calls may delay interruption until returning), and **Hints** (or **Help → Command hints**) opens a non-modal reference with **FXP**, **Python / NumPy**, and **Workspace** tabs.
- **Session history**: Click the **History** icon to search, preview, restore (**Use as input**), or export UTF-8 `.py` files (**Export selected** / **Export all**). History keeps up to 1,000 commands or 1 MiB (single commands over 1 MiB are omitted) and clears on kernel restart.

To plot inline in the command window, run:

```ipython
%matplotlib inline
import numpy as np
import matplotlib.pyplot as plt
t = np.linspace(0, 2 * np.pi, 200)
plt.plot(t, np.sin(t))
plt.show()
```

### Themes, Fonts, Q-Notation, and Number Display

Open **Settings… → Appearance** to adjust language (`English` or `繁體中文`), theme (`dark` or `light`), UI and code fonts, font sizes, and overall scale. With `zh_TW`, **Keep workspace terms in English** keeps Qt labels such as **Command Window**, **Workspace**, **Name**, and **Format** in English without affecting kernel behavior or user code. Pressing **Apply** updates Qt immediately without restarting the kernel.

Open **Settings… → FXP format** to pick **Q-format**, **MATLAB short** (`sfix16_En12`), or **Custom display template**. The **Q-format** preset uses `Qm.n` with `m` integer-side bits (including the sign bit) and `n` fractional bits (`m + n` total bits); `UQm.n` is unsigned. GUI input also reverses custom templates whose simple placeholders uniquely recover signedness, `wl`, and `fwl` (for example `S{iwl}|{fwl}` parses `S6|10` as 16 total bits, 10 fractional bits).

Use **View → Number display (view only)** for Workspace and Matrix display formatting (**Full precision / automatic**, **Scientific notation**, or **Fixed decimals** with 1 to 17 decimal places; fixed strings longer than 256 characters fall back to Scientific). Inspector, edit fields, copied data, and raw bits stay exact. **View → Reset layout** restores main window size, splitters, and expanded Inspector without restarting the kernel.

### Explicit FXP Values with APyTypes

The command window preloads `apy`, `APyFixed`, `APyFixedArray`, `APyFloat`, and `APyFloatArray` as the native APyTypes module and class objects alongside `fxp` and `fxpi`.

```python
from apytypes import APyFixed

a = APyFixed.from_float(2, bits=8, frac_bits=4)
b = APyFixed(7, 4, 2)  # first argument is raw bits (7), int_bits=4, frac_bits=2
print(float(a), hex(a.to_bits()))
print(float(b), b.to_bits())
```

Output:

```text
2.0 0x20
1.75 7
```

Mixed Python numbers follow APyTypes' native operand conversion and dispatch rules. Narrow formats can wrap a Python integer operand unless the second operand carries an explicit format:

```python
from apytypes import APyFixed

narrow = APyFixed(2, bits=3, int_bits=1)  # 0.5, range -1 .. 0.75
print(float(narrow + 1))                # -0.5: Python 1 wraps in narrow format
one = APyFixed.from_float(1, bits=8, frac_bits=4)
print(float(narrow + one))              # 1.5
```

Output:

```text
-0.5
1.5
```

In the GUI:
- **Convert to FXP…** converts a native real scalar or array to FXP; **Convert to float…** converts FXP back to `float` (scalar) or `np.float64` (array, preserving shape).
- **Real / Hex Pin** applies only to FXP-to-FXP format changes (Real preserves numeric value; Hex preserves raw bits). Native-to-FXP and FXP-to-float always perform numeric conversion.
- **Value** card edits affect the selected scalar or array element. For FXP values, the field accepts bounded math expressions (`pi/4`, `2**-3`) and rejects function calls, attributes, and containers.
- **Bit controls** (`Invert`, `Zero`, `Edit bit range` for inclusive `[high:low]`) operate on hardware bit 0 at the LSB end, grouped in 4-bit nibbles.
- **Conversion status**: Native `APyFixed` / `APyFixedArray` constructors have no GUI conversion reference and start as `No conversion reference (error unknown)`, even for `APyFixed.from_float(0.1, bits=64, frac_bits=60)` (since Python `0.1` is already binary64). A GUI Real edit, `fxp(...)`, or Real-pinned reformat records a comparison reference; raw-bit edits and Hex-pin reinterpretation do not.

### Inspect Native Numbers and Edit APyFloat Bits

```python
import numpy as np
from apytypes import APyFloat

n = -255
s = np.float32(1.5)
f = APyFloat.from_float(1.5, exp_bits=5, man_bits=2, bias=7)
assert f.to_bits() == 0x1E
print(n, float(s), hex(f.to_bits()))
```

Output:

```text
-255 1.5 0x1e
```

- **Python `int`**: Read-only Hex/Binary fields show `-0xff` and `-0b11111111`. Above 4,096 magnitude bits, display switches to a bounded preview with truncated copy disabled.
- **Python `float` and NumPy floats**: Expand **Bit representation (read-only)** to inspect sign, exponent, fraction, and classification (zero, infinity, NaN, normal, subnormal) from storage bits without casting precision.
- **Scalar `APyFloat`**: Supports interactive bit toggles (for example flipping sign bit 7 changes `0x1E` / `1.5` to `0x9E` / `-1.5`) with GUI Undo/Redo; numeric and format fields remain read-only, and `APyFloatArray` operations stay in the REPL.

### Inspect and Edit Matrices

```python
import numpy as np
from apytypes import APyFixedArray

A = APyFixedArray.from_float(np.zeros((2, 2)), bits=8, frac_bits=4)
v = np.fft.fft([1, 2, 3, 4])
v[1] = 5 - 6j
print(A.shape, v[1])
```

Output:

```text
(2, 2) (5-6j)
```

In Studio, `fft` is preloaded in the workspace namespace. The virtual matrix editor loads 32 × 16 pages on demand, shows the first two axes with trailing indices defaulting to `0` (enter `0, 1` in the slice field for `A[:, :, 0, 1]`), and jumps using zero-based row/column coordinates.

- **Copy / Paste TSV**: Select a rectangle and use `Ctrl+C` / `Ctrl+V` (or toolbar icons). Pastes validate dtype, range, and shape up to 100,000 cells / 8 MiB before writing atomically. Whole-matrix copy is capped at 100,000 cells.
- **GUI Undo / Redo**: Retains up to 100 edits or 8 MiB for GUI scalar, cell, and paste edits; REPL execution, slice changes, and whole-array reformatting clear the journal.
- **Statistics**: Summarizes finite values over a selected rectangle (up to 100,000 cells) via `float64`/`complex128` conversion.
- **Large array saturation markers**: Arrays with more than 4,096 cells skip whole-array scanning and show `[SAT?]` (unknown provenance) in Workspace:

```python
import numpy as np
from apytypes import APyFixedArray

big = APyFixedArray.from_float(np.zeros(4097), bits=8, frac_bits=4)
print(big.shape)
```

Output:

```text
(4097,)
```

Per-cell Inspector saturation checks still run normally on arrays over 4,096 cells; red `[SAT]` marks smaller arrays with a verified saturated GUI edit.

### Execution Controls, Auto-Import, Startup File, and Preferences

- **Runtime files**: Settings live in `~/.rpkbin/calc_settings.json` (or `--config PATH`). Kernel connection, history, and log files stay in a temporary runtime directory deleted at shutdown.
- **Auto-import**: In **Settings… → Auto-import**, built-in packs (`fxp_core`, `numpy`, `math`) can be enabled or disabled individually. Custom imports accept explicit absolute Python `import` statements and comments (no wildcards, relative imports, arbitrary statements, or `_`-prefixed bindings) for packages already installed in Calc's environment.
- **Startup file (`startup.py`)**: Created beside the active settings file (for both GUI and `--eval`). Loads in order: function packs, custom imports, then `startup.py`. Runs on kernel start/restart or **Reload and run** in Settings; **Apply** reloads changed function packs and custom imports without running `startup.py` or restarting the kernel.
- **Export / Import preferences**: In **Settings… → Preferences**, export and validate versioned JSON (`{"kind":"rpk-calc-preferences","version":1,"preferences":{...}}`). Imports require explicit opt-in checkboxes for **Replace startup.py after Apply (does not run code)** (backed up to `startup.before-import-HASH.py` with revision conflict checking) and **Include custom imports (runs after Apply)**. **Restore defaults** preserves `startup.py`.

---

## See Also

- [Architecture and Data Boundaries](architecture.md)
- [Documentation Index](../README.md)
- [rpk-calc (繁體中文)](../../zh/calc/README.md)
