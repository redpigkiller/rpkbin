# rpk-calc: a Python engineering calculator with variables

`rpk-calc` is for quick engineering calculations, with persistent session variables, function definitions, and direct numeric and bit inspection. Its IPython command window combines Python, NumPy, and APyTypes; Workspace and Inspector show variables, with a paged matrix editor available when needed.

## Install and open a clean workspace

From the project root, install the GUI and FXP dependencies, then launch the application:

```powershell
pip install -e ".[calc]"
rpk-calc
```

The GUI starts at a clean `In[1]` prompt without application or Python startup banners and without demo variables. When the command window is ready, enter Python code:

```python
import numpy as np
x = np.array([1.0, 2.0, 3.0])
np.mean(x)
```

IPython displays the final result, and you can select `ans` in Workspace. Ordinary assignments follow Python semantics; only an expression that returns a value updates `ans`. Python stdout, stderr, and tracebacks appear directly in the command window.

The command window keeps IPython editing behavior: press **Enter** to run a complete input, or to continue an incomplete statement; **Shift+Enter** runs the whole cell, and **Ctrl+Enter** forces a newline. Press **Tab** for attribute completion. Enter `whos` to list workspace variables, `clc` to clear the screen, or `clear` / `clear all` to clear user variables. To change only the application interface language, open **Settings… → Appearance**, choose **Language** (`English` or `繁體中文`), then **Apply**. Qt labels update immediately; the kernel does not restart, and the namespace, history, unsent input, and selection stay in place. User code, variable names, and data are never translated.

The command-window header shows **Stop** as an icon with a visible label. While idle, it is labeled **Idle** and disabled; during execution it becomes **Stop** and is enabled. An interrupt can still be delayed while Python is inside a native operation. **Hints** opens a non-modal quick reference with short Python, NumPy, and APyTypes constructor examples plus common commands. To clear the command window, enter `clc`; it clears the display without deleting Workspace variables.

The menu bar groups **Exit** under **File**; GUI numeric edit **Undo/Redo** and **Settings…** under **Edit**; number display, layout, and session history under **View**; and **Command hints** under **Help**. Top-bar and command-window controls also expose common actions.

To plot in the command window, run:

```python
%matplotlib inline
import numpy as np
import matplotlib.pyplot as plt
t = np.linspace(0, 2 * np.pi, 200)
plt.plot(t, np.sin(t))
plt.show()
```

The sine-wave PNG appears in the command output; no separate plot window opens.

To find input from the current session, click the **Session history** icon in the command window. Search and preview commands, then choose **Use as input** to restore text to the editor without executing it, or click **Export selected** / **Export all** to explicitly write UTF-8 `.py`. History retains up to 1,000 commands or 1 MiB and drops the oldest entries when over the limit; a single command over 1 MiB is omitted. It is never written automatically and is cleared when the kernel restarts.

## Adjust the theme and fonts

Click the **Settings** icon or choose **Edit → Settings…**. On **Appearance**, choose the language, theme, separate UI and code/value fonts, font sizes, and overall scale. With `zh_TW`, **Keep workspace terms in English** keeps app-owned labels such as **Command Window**, **Workspace**, **Name**, and **Format** in English; it changes Qt labels only, not kernel behavior or user content. Press **Apply** to apply preferences immediately. The namespace, command history and input, open matrix tabs, and saved `startup.py` are preserved; the kernel does not restart or run startup code. The dialog footer keeps **Cancel** and **Apply** below its stretchable tab area. Scrolling the mouse wheel over a combo box or spinner continues scrolling the page; keyboard selection and spinner arrows still work. The main window uses Qt Fusion style, whose checkbox indicator remains visible on Windows 11 with the existing QSS.

To choose how FXP formats are displayed, open **Settings → FXP format** and select **Q**, **MATLAB**, or **Custom display template**. The default **Q** display uses this tool's convention: `Qm.n` has `m` total integer-side bits including the sign bit, and `n` fractional bits, for `m+n` bits overall. Thus signed `Q1.15` is a 16-bit word, while `UQm.n` is unsigned. Other Q-format conventions exist, so use the bit counts in the declaration when exact hardware width matters. The GUI format field accepts Q and MATLAB formats; this setting changes format text only, not a value's bit width or numeric meaning. A custom template may display any text but does not change values or parsing; editable format fields remain canonical Q text.

For ordinary finite real-number text, use **View → Number display (view only)** to choose **Full precision / automatic**, **Scientific notation**, or **Fixed decimals**, then set **Decimal places…** from 1–17 (decimal places, not significant digits). **What do these options mean?** opens a non-modal explanation comparing `1.23456789` using the current decimal-place setting. This changes only Workspace and matrix display; it does not change kernel calculations. Inspector, edit fields, copied data, and raw bits retain their original precision. Fixed output longer than 256 characters falls back to Scientific. **View → Reset layout** restores the main-window size, splitters, and expanded Inspector without restarting the kernel or resetting theme/fonts.

For a single terminal calculation, use:

```powershell
rpk-calc --eval '1+2'
```

This command creates a headless `WorkspaceSession`, prints the result, and exits. It does not start the GUI and does not use the IPython kernel used by the GUI.

## Create an FXP value with APyTypes

The command window preloads `apy`, `APyFixed`, `APyFixedArray`, `APyFloat`, and `APyFloatArray` as the original APyTypes module and class objects. Python integers and NumPy values remain native until you explicitly construct an APyTypes value. For an 8-bit signed fixed-point value with four fractional bits:

```python
a = APyFixed.from_float(2, bits=8, frac_bits=4)
float(a)  # 2.0; raw bits are 0x20
```

The first positional argument to `APyFixed(...)` is a raw bit pattern, while `from_float(...)` converts a numeric value. For example, `APyFixed(7, 4, 2)` has raw bits 7, `int_bits=4`, and `frac_bits=2`, so its value is 1.75. To convert the numeric value 7 instead, use `APyFixed.from_float(7, int_bits=4, frac_bits=2)`.

For arrays, construct values directly with `APyFixedArray.from_float([0.25, -0.125], bits=16, frac_bits=12)`. `apy`, `APyFixed`, `APyFixedArray`, `APyFloat`, and `APyFloatArray` are preloaded objects, so `type(a) is APyFixed` is true; these are the original APyTypes identities, not wrappers. `APyFloat.from_float(0.25, exp_bits=8, man_bits=23)` supports ordinary Python arithmetic and value preview. The GUI Inspector supports APyFixed bit operations and individual bit toggles on a scalar APyFloat; other native numbers provide read-only radix or storage-bit views.

For a tracked FXP value in the GUI, right-click a native variable and choose **Convert to FXP**, or edit an APyFixed value in the Inspector. Settings **RND/SAT** apply to GUI value edits, conversion, and reformatting. They do not replace APyTypes constructor defaults. Native APyTypes constructors use their own quantization and overflow behavior (normally nearest ties away and WRAP), and do not create conversion-reference diagnostics. Inspector **Invert** and **Zero** operate on every bit in the FXP word; **Edit bit range** writes an unsigned value to the inclusive `[high:low]` field and preserves bits outside it. Bit labels use hardware numbering from bit 0 at the least-significant end. A 10-bit word is grouped as `[9 8][7 6 5 4][3 2 1 0]`; changing display direction or wrapping lines does not split groups of four or change the value.

The global Real/Hex pin controls what a format change preserves for existing FXP values only. With Hex pinned, changing a 16-bit value to an 8-bit signed integer format keeps the low 8 bits `0x85`, reinterpreted as real value `-123`. With Real pinned, the calculator attempts to preserve the real value and quantizes it in the new format. Reformatting an FXP array applies to the entire array, not only the selected cell.

The FXP Inspector Real-value field accepts bounded mathematical expressions such as `pi/4`, `2**-3`, and integer bitwise operations. It rejects function calls, attributes, containers, and general Python statements. Use the command window for ordinary Python; this input-field restriction is not a kernel sandbox.

A native `APyFixed` created in Python has no GUI conversion-reference source and starts with `No conversion reference (error unknown)`. Native `APyFixedArray` values also start without GUI provenance, but Inspector edits record sparse comparison data in a session-owned sidecar while preserving the original array object and its aliases. A matching record can show the GUI edit result; raw-bit edits and Hex-pin reinterpretation do not establish a comparison source.

### Interpreting conversion status

For example, create a native APyFixed value:

```python
x = APyFixed.from_float(0.1, bits=64, frac_bits=60)
```

Native APyTypes construction has no conversion-reference source, so this value shows `No conversion reference (error unknown)` even if the stored bits exactly represent the input float. Python's `0.1` is already a binary64 approximation of decimal 0.1. A Real-value edit or Real-pin reformat establishes a new comparison source; raw-bit edits and Hex-pin reinterpretation do not.

## Inspect native numbers and edit APyFloat bits

Create three variables in the command window:

```python
import numpy as np
from apytypes import APyFloat

n = -255
s = np.float32(1.5)
f = APyFloat.from_float(1.5, exp_bits=5, man_bits=2, bias=7)
assert f.to_bits() == 0x1E
```

Select `n` in Workspace. Inspector's read-only Hex/Binary fields show `-0xff` and
`-0b11111111`; use each field's Copy button. Python int has no assumed fixed width or
two's-complement interpretation. Above 4,096 magnitude bits, numeric and radix text
becomes an explicitly marked preview, with copying of truncated text disabled. The
stored value and Python arithmetic are unchanged; use `hex(n)`/`bin(n)` in the REPL
for complete radix text.

Select `s`. Hex shows `0x3FC00000`; bits are labeled as sign/exponent/fraction.
Python float and NumPy float16/32/64 show storage bits in their original format.
These fields and bit buttons are read-only; viewing never casts to another precision.
Positive/negative zero, positive/negative infinity, NaN, normal and subnormal values
are classified explicitly. This Hex is a storage bit pattern, not the hexadecimal
numeric notation returned by `float.hex()`.

Select `f`. Hex shows `0x1E`. Click its sign bit (bit 7): the value becomes `-1.5` and
Hex becomes `0x9E`. Undo and then Redo show `0x1E` and `0x9E`. Then verify in the REPL:

```python
assert type(f) is APyFloat
assert (f.exp_bits, f.man_bits, f.bias, f.to_bits()) == (5, 2, 7, 0x9E)
```

APyFloat bit editing applies to a single scalar variable, preserving its format,
bias and exact special-value bits. Its numeric/format fields are read-only; use the
REPL for numeric/format changes and APyFloatArray operations. Finite APyFloat values
outside Python float's range or precision use exact binary scientific notation.
As before, executing a REPL cell clears GUI Undo/Redo, so complete Undo/Redo before
running the verification cell above.

## Inspect and edit a matrix

Create a zero-filled fixed-point array, then edit its top-left cell in the Inspector. Here `bits=8, frac_bits=4` gives a signed range of -8 to 7.9375 and an LSB of 1/16:

```python
import numpy as np
A = APyFixedArray.from_float(np.zeros((2, 2)), bits=8, frac_bits=4)
```

Double-click `A` in Workspace to open a matrix tab, or select it and click the **Open array editor** icon. Select a cell to inspect that element, or double-click it to edit. FXP matrices show **Array format:** and can display Real, Hex, 2's, or UInt values; changing FXP Format applies to the whole array. A native NumPy array shows **Dtype:** and its read-only dtype, with no inline FXP conversion; use **Convert to FXP** from the Workspace context menu. Writable native NumPy arrays preserve their dtype when edited. Integer cells reject fractional or out-of-range values; read-only arrays and unsupported dtypes cannot be edited. Inspector labels native values **Type:** and shows no FXP pin or FXP bit controls. The FXP format row says **Whole variable**, indicating it applies to the entire variable. Use ordinary Python/NumPy operations in the command window for native bitwise work.

Double-click the first cell, enter `99` in its Real value field, and apply the edit. The value saturates to 7.9375 (`127/16`) and Inspector marks it **SATURATED**. The constructor starts without GUI provenance; the Inspector edit records its comparison and saturation outcome in a session sidecar. The native array object and aliases remain intact. Ordinary complex arrays are shown as Python values without FXP bits:

```python
v = fft([1, 2, 3, 4])
v[1] = 5 - 6j
```

Select the second cell to see `5-6j` in Inspector; it has no FXP Hex/bit fields. Editing the complex value preserves its imaginary component.

The matrix uses a virtual table and reads visible regions on demand. Arrays with more than two dimensions show the first two dimensions; trailing indices default to 0 and can be changed in the slice field (for example, `0, 1` selects `A[:, :, 0, 1]`). The page describes the active slice. Copying a whole matrix is limited to 100,000 cells; export the needed data from Python for larger arrays.

To edit a rectangular block, select it and click the **Copy selected cells as TSV (Ctrl+C)** icon, or press Ctrl+C; move to the destination's top-left cell and click **Paste TSV at the selected cell (Ctrl+V)**, or press Ctrl+V. The whole block is validated for dtype, range, and shape before any write, so a rejected paste does not leave a partial edit. **Undo numeric edit** reverses the entire paste once; **Redo numeric edit** reapplies it. GUI edit history retains up to 100 entries and 8 MiB total. REPL execution, slice changes, whole-array format conversion, and other structural operations clear it. This history covers GUI edits, not arbitrary Python code. To copy the entire matrix, click the **Copy entire matrix as TSV (up to 100,000 cells)** icon.

Enter trailing indices in the slice field for higher-dimensional arrays; for example, `0, 1` selects `A[:, :, 0, 1]`. **Jump to row, column** uses zero-based indices. Select one rectangular region and click the **Show statistics for the selected cells** icon for a summary of finite values; FXP and large integers are converted to float64/complex128, so these results are approximate. After entering an FXP Format, click the **Preview format conversion for the whole array** icon to inspect range, LSB, changed/saturated counts, and an example, then choose **Apply**; pressing Enter applies directly. Moving focus away from the format field does not apply it. Above 4,096 cells, the preview samples only the selected cell, but applying it still changes the whole variable.

### Reading saturation markers on large arrays

```python
import numpy as np
A = APyFixedArray.from_float(np.zeros(4097), bits=8, frac_bits=4)
```

This 4,097-cell array exceeds Workspace's automatic check limit, so its Value cell shows `[SAT?]`. Select the first cell and enter `99` in the Inspector Real value field; it reports `SATURATED`. The whole-array marker remains `[SAT?]`: arrays above 4,096 cells keep a conservative unknown summary even when the selected cell has a verified result. `[SAT?]` means whole-array provenance is incomplete, neither confirmed saturation nor safety. A red `[SAT]` marks an array with at least one currently verified saturated GUI edit; a fully verified nonsaturated array has no marker. These prefixes appear only in the Workspace Value cell and do not alter values or copied data. Large arrays do not disable per-cell Inspector checks.

## Execution controls and limits

- **Stop execution (Ctrl+C)** asks the Python kernel to interrupt. If code is waiting inside a native C function or an operating-system operation, the interrupt may be delayed until that operation returns. The GUI stays responsive during long calculations, while numeric editing is disabled.
- **Restart kernel** asks for confirmation, then clears the workspace and creates a new kernel. A crashed kernel is not silently restarted; its error appears in the status bar so the user can choose to restart. Closing the application stops the kernel it started.
- Settings are saved in `~/.rpkbin/calc_settings.json`. Kernel connection, history, and log files live in a temporary runtime directory that is removed at shutdown; command history is session-local.
- FXP display and numeric defaults update the existing session. Auto-import group changes and `clear` re-inject built-in helpers and cached startup symbols without rerunning `startup.py`. The startup file runs when a kernel starts/restarts or when you choose **Reload and run** in Settings.
- **RND/SAT** defaults apply to GUI real-value edits, **Convert to FXP**, and format changes that preserve numeric values. Native APyTypes constructors use APyTypes' own defaults and do not produce GUI conversion-reference records.
- Select an array in Workspace and click the **Open array editor** icon, or double-click its row; its tooltip describes the double-click action. Click the Inspector title and arrow to collapse or expand its body; the header stays visible. Opening an array or double-clicking a Workspace variable expands the Inspector again.
- On close, the app saves window geometry, splitter sizes, and the Inspector's collapsed state for the next launch. Startup errors include the real `startup.py` path and line in the displayed traceback.

See the [architecture notes](architecture.md) for process boundaries, matrix transport, and build checks.


## Import and export preferences safely

Open **Settings… → Preferences**. The page shows the active settings file path; **Open settings folder** opens its containing folder. **Export preferences…** writes a versioned JSON file and can include the current startup source under `preferences.startup_script`. **Import preferences…** previews changes and shows scripts and custom imports as plain text. To import startup source, explicitly check **Replace startup.py after Apply (does not run code)**. To import custom imports, separately check **Include custom imports (runs after Apply)**. After **Apply**, the source replaces `startup.py` but does not run; opted-in custom imports load in the current kernel. A revision conflict refuses the startup overwrite and asks you to reopen Settings. Before replacement, the app saves `startup.before-import-HASH.py`. **Restore defaults** preserves `startup.py`; **Cancel** discards the dialog draft, but cannot undo saves made in an external editor.

For example, export preferences from one installation and import them on another. Review each source in the preview. Choose **Include custom imports (runs after Apply)** if you want those imports to load in this session; separately choose **Replace startup.py after Apply** if you want to copy the startup source. Applying custom imports does not run `startup.py`; that file runs only at kernel start/restart or when you choose **Reload and run**. The versioned envelope remains `{"kind":"rpk-calc-preferences","version":1,"preferences":{"startup_script":"...","custom_imports":"from math import prod as product"}}`; runtime `startup_file`, geometry, splitter/collapse state, workspace, and history are not exported.

On **Auto-import**, built-in function packs have fixed contents and can only be enabled or disabled. To add an import, enter it under **Custom imports**. This field accepts only explicit absolute Python import statements and comments; wildcard imports, relative imports, non-import code, and imported or aliased names beginning with `_` are rejected. Packages must already be installed in the Python environment used by Calc; this setting never installs packages. For example, add `from math import prod as product`, press **Apply**, then enter `product([2, 3])` in the command window and check for `6`. Imports load in this order: function packs, custom imports, then `startup.py`. The read-only startup preview is a separate feature: **Refresh preview** rereads the saved file, and **Open text editor** opens Notepad on Windows, TextEdit on macOS, or asks you to choose an editor executable on Linux. The first-run file is created beside the active settings file and contains commented examples for SciPy imports, explicit FFT overrides, `db20`, and `fxp`; install optional packages before enabling those examples. On upgrade, legacy JSON `startup_script` source initializes `startup.py`; if a different file already exists, it is kept, the legacy source is saved as `startup.legacy-HASH.py`, and the app reports a notice. Imports and helper definitions stay out of Workspace.

The GUI reads and writes `startup.py`; the kernel reads it, compiles it using its real path, and executes it in the live session namespace. For example, a startup-defined `scale(x): return x * gain` can use `gain = 3` entered later at the prompt, so `scale(2)` returns `6`. **Apply** saves preferences, reloads changed function packs and custom imports in the current kernel, and preserves user variables and cached startup bindings; it does not run `startup.py` or restart the kernel. Startup code runs on kernel startup/restart, or when **Reload and run** is selected. Reload is ordinary Python execution: it does not undo prior side effects, clear old script-created names, or reload imported modules. Calculator-owned helper bindings are refreshed only while they remain calculator-owned; user-rebound names are preserved, while explicit assignments in the script follow normal Python overwrite rules. Changing function packs or custom imports and running `clear` restores cached helpers without rerunning the script. Import statements are attempted independently: a failed statement appears in **Auto-import** status and the application error, while successful imports remain available. A failed statement publishes no partial bindings, though Python module side effects cannot be rolled back. The status displays startup success or a traceback with the file path and line number. `rpk-calc --eval` also uses the default settings folder's `startup.py`.
