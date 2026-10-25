# rpk-calc Architecture and Data Boundaries

The GUI separates interactive Python execution from Qt presentation. The IPython kernel is the sole owner of numeric objects; the Qt process handles the command window, Workspace, Inspector, matrix tables, and user interaction. The existing `core` layer provides `WorkspaceSession` and the inspection/editing logic for FXP and ordinary values. The GUI does not copy the complete Python namespace.

## Responsibilities and invariants

- `kernel_client.py` starts and manages one IPython subprocess owned by the application, communicating over Jupyter channels bound to `127.0.0.1`. Replies are handled without blocking the Qt event loop. The kernel does not restart automatically; closing the application stops this process.
- `kernel_runtime.py` executes user code inside the kernel, owns `WorkspaceSession`, and handles inspection, paging, and edit requests. The GUI receives formatted snapshots, not Python objects or complete arrays.
- `controller.py` serializes GUI requests, tracks `view_id` and workspace `revision`, and coordinates busy state. The GUI stays responsive during execution, while numeric editing is disabled. Workspace scalar Value/Format activation routes to the Inspector's Value or shared conversion controls; array Value continues to open the matrix editor. Help is a compact three-tab reference with FXP selected by default.
- `CalcMainWindow` uses Qt Fusion style, which draws the checkbox indicator normally and works with the existing application QSS; the checked state remains visible on Windows 11.
- `SettingsDialog` collects preferences and presents a read-only startup preview; `CalcController` distinguishes Qt appearance, kernel numeric defaults, built-in function packs, custom imports, and startup-file actions. Built-in pack contents are fixed; their checkboxes only enable or disable each pack. Appearance and `keep_english_terms` update Qt only; numeric defaults update the existing session. Applying changed packs or custom imports reloads the configured imports in the existing kernel without executing `startup.py` or clearing the namespace. User variables and cached startup bindings retain their existing bindings. Startup code runs at kernel startup/restart or on an explicit Reload and run request. Apply does not restart the kernel, preserving console input/history and matrix tabs.
- The Settings dialog places its Cancel/Apply footer below a stretchable tab area. The footer remains aligned while each settings page scrolls or grows.
- The Workspace array button and double-click both open the existing matrix tab. Collapsing Inspector hides its body while the arrow/title header stays available to expand it; opening an array expands Inspector. Closing the app saves window geometry, splitter sizes, and the Inspector collapsed preference.
- `protocol.py` and `MatrixTableModel` define JSON DTOs and bounded matrix pages. A normal matrix page contains at most 32 × 16 values, and the table caches at most 8 pages. Matrix-page shape/slice metadata queries do not read the whole array. The Workspace Min/Max column scans values only while displayed; an FXP array saturation summary checks at most 4,096 cells and reports unknown above that limit without scanning the whole array. Copying an entire matrix is capped at 100,000 cells; export larger data from Python.


The native **Convert to FXP…** dialog produces a conversion request consumed by the kernel; it carries the existing `expected_revision` guard. FXP inline format controls and **Convert to float…** use the same established kernel conversion path. This UI adds no config field or schema. The Inspector continues to honor the saved list/grid layout preference.

## FXP conversion provenance and status

APyTypes constructors create native values without calculator conversion-reference metadata. Such a value therefore shows `No conversion reference (error unknown)`, even when its bits exactly represent the input. Python `float` uses binary64; for example, `0.1` is already an approximation of decimal 0.1. A GUI Real-value edit or Real-pin reformat records a new comparison source. Raw-bit edits and Hex-pin reinterpretation do not. The Inspector's exact conversion status compares a tracked GUI operation's result with that operation's actual input; it says nothing about decimal intent or earlier arithmetic.

Native constructors start without GUI conversion-reference data; APyFixed scalars therefore show `No conversion reference (error unknown)` until a tracked GUI Real edit or Real-pin reformat. For native APyFixedArray GUI edits, the session stores sparse provenance in a sidecar keyed to the original owner; it does not replace the array or break aliases. Reads validate owner, shape, FXP spec, and current raw bits. A mismatch makes the diagnostic unknown. This is bounded diagnostic metadata, not an edit history for arbitrary Python: identical raw bits written back by Python cannot be distinguished from an unchanged value.

APyFixedArray constructors do not seed GUI provenance. GUI edits record changed-cell sources sparsely in the session sidecar, avoiding an array copy. The sidecar retains at most 32 owners and 100,000 cell records; LRU eviction drops diagnostic records only and never changes numeric values. Reset clears it; refresh removes records for owners no longer present in the namespace. Duplicate copies valid records, and Undo/Redo restores their source metadata. Settings `default_quant` / `default_overflow` (RND/SAT by default) apply to GUI real-value edits, native-to-FXP conversion, and Real-pinned FXP format changes; they do not override APyTypes constructor behavior or control ordinary Python/NumPy/APyTypes arithmetic. Native APyTypes constructors use their own quantization and overflow behavior (normally nearest ties away and WRAP). `protocol.py` build ids cover the FXP, session, and protocol sources; an incompatible GUI/kernel build rejects requests and requires a restart.

`RpkFixed` and `RpkFixedArray` retain GUI metadata and `copy()` behavior. Arithmetic operators are inherited from APyTypes: no calculator operand widening, implicit operand coercion, or result wrapping is applied. Result bits, formats and exceptions follow the installed APyTypes version; mixed Python operands can overflow during native conversion. Result types follow native dispatch, including NumPy dispatch where applicable; the calculator does not wrap them or attach GUI conversion provenance to new results. Explicit GUI edits continue to use their selected Pin/quantization/overflow policy.

## Workspace array saturation markers

The kernel produces `WorkspaceRowSnapshot.is_saturated: bool | null` for Qt to consume. `true` means at least one cell's current bits still match a tracked saturated GUI edit; `false` means every cell has a matching current source and none saturated; `null` means provenance is incomplete, bits no longer match their source, or the array has more than 4,096 cells. Arrays over the limit keep a conservative unknown whole-array summary and are not scanned; the selected cell can still report `SATURATED`. For smaller arrays, sparse GUI `cell_sources` are checked. Per-cell Inspector checks remain available regardless of array size.

Qt prefixes only the Workspace Value cell of an FXP array with `[SAT]` or `[SAT?]`; `true` uses red `[SAT]`, while `null` uses neutral `[SAT?]` with a tooltip explaining incomplete provenance. `false` adds no marker. `[SAT?]` means unknown, not saturated or safe. The marker is display text and does not change `value_str` or copied numeric data. A 4,097-cell `APyFixedArray` exceeds the scan limit, so Workspace shows `[SAT?]`. If the user edits one cell to `99` in the Inspector Real field on an 8-bit word with four fractional bits, that cell saturates to 7.9375 and Inspector reports `SATURATED`; the whole-array summary remains conservatively unknown above the 4,096-cell limit.

DTO example: `{"is_saturated":null}`. The kernel computes this field and sends it to Qt over JSON; the existing build-id check rejects incompatible builds and requires a restart. A single-cell array edit updates only that cell's saturation metadata and no longer runs a full-array `np.any` reduction over the saturation mask.

## Native values and the FXP boundary

Python `int` retains arbitrary precision and an assignment such as `a = 2` in the console rebinds `a` to an int. Inspector represents ordinary numbers with `PlainValueSnapshot`; radix and bit views follow the original type without implicit FXP word length or pin semantics; Workspace shows the native type/dtype and does not assume native values are FXP or have an LSB. Inspector conversion controls can convert native real scalars/arrays to FXP and FXP values to Python `float` or NumPy `float64`; array conversions preserve shape. APyFloat and unsupported types use the console for conversion or format changes. Native arrays' matrix Format field remains their dtype. FXP format changes apply to the whole variable/array and use the Inspector's Real/Hex pin; the existing matrix-editor Format/Preview controls use the same conversion flow. Native↔FXP and FXP→float are numeric conversions and ignore that pin.

The kernel produces `native_bits` descriptors in `representations.py`, and Qt renders them by type. For a fixed-width NumPy integer scalar: Inspector may include a read-only `native_bits` summary (dtype, width, signedness, Hex, and binary), without FXP bit editing. Python int HEX/BIN uses signed magnitude notation with a minus sign and no assumed storage width. Python float and NumPy float16/32/64 expose read-only floating-point fields and special-value classifications from their storage bits. Layouts for longdouble, complex and APyFloatArray are not guessed.

The kernel preloads `apy`, `APyFixed`, `APyFixedArray`, `APyFloat`, and `APyFloatArray` as the original APyTypes module and class objects, not wrappers. Use these constructors directly: `APyFixed.from_float(0.25, bits=16, frac_bits=12)` converts a numeric value; `APyFixed(0x400, bits=16, frac_bits=12)` interprets its first argument as raw bits; `APyFixedArray.from_float([0.25, -0.125], bits=16, frac_bits=12)` constructs an array; and `APyFloat.from_float(0.25, exp_bits=8, man_bits=23)` constructs an APyFloat. For `APyFixed(7, 4, 2)`, raw bits are 7, `int_bits=4`, `frac_bits=2`, and the value is 1.75; to encode numeric value 7, call `APyFixed.from_float(7, int_bits=4, frac_bits=2)`. APyTypes constructors use their native defaults (normally nearest ties away and WRAP) and do not apply GUI RND/SAT defaults or create conversion-reference metadata. A scalar APyFloat supports numeric preview, floating-point bit inspection and individual bit toggles; its format and bias remain fixed and numeric/format fields are read-only.

GUI format input is resolved by `FormatEngine.parse_input` against standard notation and the currently enabled custom templates. Only simple placeholders that uniquely recover signedness, WL and FWL are reversible; repeated fields must agree, adjacent numeric captures and advanced format specifications are not reversed. Conflicts, including conflicts with standard notation, are rejected. `CalcController` normalizes the resolved `FxpSpec` to canonical Q text before producing the existing JSON request; the kernel consumes the same standard format contract. No config field or transport schema is added, and native APyTypes constructors stay unchanged. The Q convention here counts the sign in `m` for `Qm.n`, so total width is `m+n`; `UQm.n` is unsigned. Other tools may use a different convention, so constructor bit counts remain the clearest declaration of hardware width.

For a repeated shorthand, define a function in **Settings → Auto-import**, for example `def fxp(value, bits=16, frac_bits=12): return APyFixed.from_float(value, bits=bits, frac_bits=frac_bits)`. This is user-defined startup code, not a preloaded calculator constructor. Inspector conversion controls perform GUI conversions; Workspace only routes the user to that section and does not edit values inline.

Matrix labels show **Array format:** for FXP and **Dtype:** for native arrays; Inspector labels native values **Type:**, and the FXP format row identifies its scope as **Whole variable**.

The kernel DTO reports whole-variable conversion capability independently of the selected array cell. Inspector Value edits affect the selected element; Conversion operations affect the whole variable or array. Conversion requests carry `expected_revision` in the request envelope, which the kernel checks before mutation. The preview DTO returns pin, quantization, overflow, and revision; the UI rejects Apply if the policy changed after preview. Writable NumPy array edits preserve dtype, and integer edits reject fractional or out-of-range values. Native-to-FXP conversion and Real-pinned FXP format changes use GUI quantization/overflow settings; these settings do not alter APyTypes constructors or arithmetic entered in the Python console. The FXP Inspector Real-value field uses a bounded AST evaluator: it accepts numeric literals, `pi`/`e`, arithmetic, and integer bitwise operators, and rejects function calls, attributes, containers, and general Python statements. Input is limited to 4,096 characters and 512 AST nodes; integer results are limited to 16,384 bits, exponent magnitude and shift counts to 4,096, and results must be finite real values. The ordinary Python REPL is not restricted by this field parser; the restriction is not a kernel sandbox. Native bitwise operations use the Python/NumPy REPL; GUI FXP raw-bit controls do not apply to native values.

Inspector HEX/BIN fields for ordinary numbers are read-only. Above 4,096 magnitude bits, Python int uses an explicitly marked bounded preview with copying of truncated text disabled; the stored value is unchanged. Workspace name/type filters change only the visible list; if the selected Inspector target is filtered out, the target and table scroll position remain intact.

## Numeric representation DTO and APyFloat edits

`PlainValueSnapshot.native_bits` is an internal JSON DTO produced by the kernel session
and consumed by the Qt Inspector. A scalar APyFloat descriptor looks like this
(other snapshot fields omitted):

```json
{"kind":"float","dtype":"APyFloat","width":8,"hex":"0x1E","binary":"00011110","exp_bits":5,"man_bits":2,"bias":7,"classification":"Normal","editable":true}
```

The descriptor's `editable` controls bit buttons separately from the numeric input's
`PlainValueSnapshot.editable`. Only a scalar APyFloat permits bit editing; native
floats and selected native array elements keep read-only bits. Qt receives no APyFloat
objects and never reconstructs them via Python float. The kernel uses `to_bits` /
`from_bits` to retain format, bias and NaN payload. Journal fingerprints use raw bits
and full format, including signed zero, rather than floating-point equality.
REPL execution still clears GUI edit history under the existing rules.

The build id includes `representations.py`; mismatched GUI/kernel sources are rejected
by envelope validation and require a restart. This is internal transport metadata;
users do not create configuration files or sidecars for it.

## GUI editing, matrix operations, and command history

`EditJournal` is owned by `CalcKernelRuntime` and tracks only GUI numeric/bit edits to scalars, individual cells, and rectangular pastes. It retains at most 100 entries or 8 MiB. It does not copy large arrays; instead it records each edit target, original owner, location, value fingerprint, and structural signature. Before Undo/Redo, it checks the variable binding, slice/cell, shape, dtype, strides, FXP format, and current value. A REPL cell, array structural operation, whole-array reformat, or slice change clears the journal; a failed guard also clears it and refuses replay. This is not general undo for arbitrary Python. Console Ctrl+Z remains text editing.

Paste TSV parses and validates the complete rectangle, bounds, dtype/range, and editability before writing; the limits are 100,000 cells and 8 MiB. Rejected input is not partially applied, and an unexpected write error rolls back changed cells. A rejected single-cell edit keeps the entered text and shows the reason so the user can correct and retry.

Matrix snapshots carry `full_shape` and `slice_indices`. The GUI shows the first two axes; trailing indices default to 0 and can be changed in the slice field. Row/column jumps are zero-based. Selected-rectangle statistics are limited to 100,000 cells and summarize finite values only. Values are converted to float64/complex128 first, so FXP and large-integer results are approximate. Results include count, finite_count, sum, mean, min_abs, and max_abs; real-valued data also includes min/max. Whole-array conversion and FXP format preview/apply are handled by Inspector conversion controls; the matrix editor remains for per-cell inspection and edits.

History is a Qt-side `CommandWindowWidget` buffer of submitted source, separate from IPython's kernel history. It is cleared whenever a new kernel client is created; it retains at most 1,000 commands/1 MiB and omits a single command larger than 1 MiB. Searching and previewing do not execute commands; Use as input only restores text to the editor. Export explicitly writes a UTF-8 `.py` file, whose header notes that IPython syntax may require IPython to run.

## Display and DTO scope

`native_bits` is read-only NumPy integer inspection data in the Inspector snapshot; it does not create an FXP format. Undo state, matrix `full_shape`/`slice_indices`, format Preview, and selection Statistics are computed in the kernel and sent to Qt as JSON DTOs. For example, Undo state is `{"can_undo":true,"can_redo":false,"undo_count":1,"redo_count":0}`. The FXP display setting offers Q, MATLAB, and Custom display template. Changing the representation does not mutate values. GUI edits use uniquely reversible template text or fall back to unambiguous standard notation; the kernel request always uses canonical Q. The separate Workspace `numeric_display`/`numeric_precision` settings format only finite real number text in Workspace Value and Matrix `DisplayRole`, using Full/automatic, Scientific, or Fixed with 1–17 decimal places; Inspector, Matrix `EditRole`, copied data, and raw bits remain unchanged. Fixed output estimated to exceed 256 characters falls back to scientific notation. View's Reset layout restores only window size, splitters, and expanded Inspector; it does not restart the kernel or change appearance preferences. DTOs do not contain numeric objects or a namespace that Qt can edit independently. The `protocol.py` build id includes `core/editing.py`; an incompatible GUI/kernel build is rejected.

## Deferred work

The app currently has one command window and one application-owned kernel. Before adding multiple command windows, decide whether they share or own separate kernels/namespaces, which window owns each output stream, and how Stop/interrupt behaves. This feature is not implemented.

For arrays with more than two dimensions, the table shows the first two dimensions and defaults trailing indices to 0 (for example, `A[:, :, 0]`); the slice field can set other indices, and the page identifies the active slice. Inspector and matrix edits still operate on the original kernel values; display snapshots are not an independently editable workspace.

## Example kernel envelope

Control operations request an `application/json` result through Jupyter `user_expressions`. The protocol carries JSON-only data and does not use pickle. `clc` separately sends a Jupyter `display_data` message with `application/vnd.rpk.calc.control+json`, containing `protocol`, `build`, and `action: "clear_console"`; this tells the frontend to clear its display and carries no numeric data. A simplified request and response look like this:

```json
{
  "protocol": 1,
  "build": "<16-hex-build-id>",
  "op": "snapshot",
  "request_id": 12,
  "view_id": 4,
  "selected_var": "A",
  "selected_coord": [3, 2],
  "open_matrices": {"A": [3, 2]}
}
```

```json
{
  "protocol": 1,
  "build": "<same-build-id>",
  "view_id": 4,
  "revision": 8,
  "workspace": [{"name": "A", "shape_str": "1000000x4"}],
  "selected_var": "A",
  "selected_coord": [3, 2],
  "inspector": {"kind": "plain", "value": {"...": "formatted snapshot fields"}},
  "matrices": [{"name": "A", "shape": [1000000, 4]}]
}
```

Fields are illustrative; the actual DTOs are defined by `protocol.py` and the `core` snapshot dataclasses. Every request carries a protocol version and a build id computed from protocol and runtime sources. A mismatch is rejected and requires a restart so the GUI and kernel cannot silently use different data structures or semantics. `view_id` lets the UI discard stale selection results; `revision` lets it reject old matrix pages.

Settings are saved in `~/.rpkbin/calc_settings.json`. The connection file, IPython history, and kernel log live in a temporary runtime directory that is removed at shutdown; interactive history is not retained between sessions. `rpk-calc --eval` is a separate path that creates a headless `WorkspaceSession` without starting or connecting to an IPython kernel.


## Settings, language, and preference transfer

`SettingsDialog` owns the four preference pages: Appearance, FXP format, Auto-import, and Preferences. `CalcConfig` is the in-memory/settings-file model; `core/settings_transfer.py` defines the portable subset and validates imports before the dialog merges them into a draft. Settings produces and consumes JSON shaped like:

```json
{"kind":"rpk-calc-preferences","version":1,"preferences":{"language":"zh_TW","theme":"light"}}
```

The exact kind and version are required. Unknown keys, unsupported versions, and invalid types or values are rejected. Runtime `startup_file`, window geometry, splitter/collapse state, workspace variables, and command history are excluded; portable `startup_script` and `custom_imports` remain source text in the envelope. Import previews the changes; startup source is excluded unless the user opts in to **Replace startup.py after Apply (does not run code)**, and custom imports are excluded unless the user separately opts in to **Include custom imports (runs after Apply)**. Apply writes opted-in startup source without running it and loads opted-in custom imports in the current kernel. A revision mismatch rejects the startup write; before replacement the app saves `startup.before-import-HASH.py`. Restore defaults preserves the startup file. Cancel discards the Settings draft but cannot undo changes already saved by an external editor.

Language switching and `keep_english_terms` are Qt presentation settings. The latter defaults to `false`, is excluded from kernel configuration, and keeps selected application labels such as Command Window, Workspace, Name, and Format in English when the UI language is Traditional Chinese. Other explanations still use the selected UI language. Submitted Python, variable names, values, and user startup code remain unchanged. The active widget tree is retranslated in place, without rebuilding the kernel or clearing the namespace, history, pending console input, or selection. Translation coverage is bounded to application-owned interface strings.

## Startup file ownership and execution

The GUI's `StartupFile` creates `startup.py` beside the active settings file; the CLI headless `--eval` path uses the default settings directory. After initialization, `startup.py` is the GUI's source of truth. `CalcConfig.startup_file` carries its resolved path at runtime and is never persisted. The legacy JSON `startup_script` is read during migration and is retained in preference exports as portable source text. A first-run file contains comments only, with examples for SciPy imports, explicitly overriding FFT helpers, `db20`, and `fxp`.

On migration, if `startup.py` is absent, legacy JSON source initializes it. If a different `startup.py` already exists, the app preserves it, writes the legacy source to `startup.legacy-HASH.py`, and reports a notice. The Settings Auto-import page reads and previews the file but does not edit it inline. Open text editor launches Notepad on Windows and TextEdit on macOS; Linux asks for a text-editor executable. Refresh preview rereads external saves, and Cancel cannot undo those saves. The kernel reads the file, compiles it with the actual filename, then executes it in the live session namespace; errors include the path and line number. Startup-defined functions can use variables entered later in the console.

Apply saves preferences and immediately loads changed function packs/custom imports in the current kernel; it preserves user variables and cached startup bindings and never runs `startup.py`. The startup file runs when a kernel starts/restarts or when the user selects **Reload and run**. Custom imports accept only explicit absolute Python import statements and comments. Wildcard imports, relative imports, other code, and names or aliases beginning with `_` are rejected; packages must already be installed, and Calc does not install them. Imports load in the order function packs → custom imports → `startup.py`. Each statement is attempted independently; failures appear in Auto-import status and the application error, while successful statements remain loaded. A failed statement publishes no partial bindings, though module side effects cannot be rolled back. Reload is ordinary Python execution: it does not roll back side effects, guarantee removal of old script-created names, or reload imported modules. Only calculator-owned prior bindings are refreshed when they still retain calculator ownership; user-rebound names are preserved, while assignments made explicitly by the script can overwrite names as normal Python code does. `clear` restores cached helpers without rerunning the script. Imported startup source replaces the file only after explicit consent and Apply, using a revision check to reject a concurrent editor save and a `startup.before-import-HASH.py` backup.
