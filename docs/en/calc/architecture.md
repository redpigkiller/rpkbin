# rpk-calc Architecture and Data Boundaries

[Quickstart and Reference](README.md) · [Documentation Index](../README.md) · [繁體中文](../../zh/calc/architecture.md)

`rpk-calc` separates execution boundaries by mode: Simple mode runs in-process inside `SimpleCalcEngine` without Python/APyTypes or an IPython subprocess, Python Studio runs Qt presentation against a dedicated local IPython kernel that owns all workspace objects, and `rpk-calc --eval` runs a headless `WorkspaceSession` directly without Qt or a kernel subprocess.

---

## Execution Modes and Boundaries

```text
rpk-calc (cli.py)
 ├── -e / --eval ──> WorkspaceSession (headless; CalcConfig + sibling startup.py; no Qt, no kernel)
 └── GUI (ui/app.py: CalculatorWindow)
      ├── Simple page ──> SimpleCalcWidget -> SimpleCalcEngine (local_engine.py, in-process)
      │                                        (Fraction / Decimal + scalar_fxp; no kernel, no apytypes)
      └── Python page ──> CalcMainWindow -> CalcController (controller.py)
                                              │ 127.0.0.1 Jupyter channels (JSON DTOs only)
                                              ▼
                                         KernelTransport (kernel_client.py)
                                              │
                                              ▼
                                         CalcKernelRuntime (kernel_runtime.py) -> WorkspaceSession
```

| File | Responsibility |
|---|---|
| `cli.py` | Argument parsing (`-V`/`--version`, `-c`/`--config`, `-e`/`--eval`, `--simple`, `--python`/`--studio`, `--mode`), headless `--eval` dispatch, and optional-dependency error checks. |
| `local_engine.py` | `SimpleCalcEngine` state, expression parser/evaluator, 4 shift modes, `NAND`/`NOR`, `DEC` unscaled integer semantics, and `display_fraction`. |
| `ui/app.py` | `CalculatorWindow` shell switching between `SimpleCalcWidget` and lazy-created `CalcMainWindow`, syncing shared `CalcConfig` and window geometry. |
| `controller.py` | `CalcController` serializing Studio GUI requests, tracking `view_id` and `revision`, handling busy/out-of-sync state, and propagating `CalcConfig`. |
| `kernel_client.py` | `KernelTransport` lifecycle for one owned IPython subprocess over `127.0.0.1` Jupyter channels with idle-aware request timeouts and cleanup. |
| `kernel_runtime.py` | `CalcKernelRuntime` executing console code inside the kernel, owning `WorkspaceSession` and `EditJournal`, and serving JSON snapshots/pages. |
| `protocol.py` | JSON-only DTO conversion, `PROTOCOL_VERSION = 1`, 16-hex `build_id()`, `PAGE_ROWS = 32`, `PAGE_COLS = 16`, `MAX_COPY_CELLS = 100_000`, and `CONTROL_MIME`. |
| `core/session.py` | `WorkspaceSession` namespace, `whos`/`clc`/`clear`, `WorkspaceRowSnapshot`/`MatrixSnapshot`, and sparse array provenance sidecars. |
| `core/config.py` | `CalcConfig` persistence (`~/.rpkbin/calc_settings.json` or `--config`), `KERNEL_CONFIG_FIELDS`, and `ALL_WORKSPACE_COLUMNS`. |
| `core/public_fxp.py` | Public `fxp` and `fxpi` constructors (`RpkFixed` / `RpkFixedArray`) bound to the active session config provider. |
| `core/editing.py` | `EditJournal` (100 entries / 8 MiB), atomic TSV `paste_rectangle`, `conversion_preview`, and `selection_statistics`. |
| `core/representations.py` | Read-only `native_bits` descriptors and `APyFloat` bit-field metadata (including the 4,096-bit `int` display cap). |
| `core/startup.py`, `core/auto_imports.py` | `StartupFile` revision-checked reads/writes and migration, plus validated absolute custom import loading. |

---

## `SimpleCalcEngine` (`local_engine.py`) Architecture Boundary

`SimpleCalcEngine` provides the standalone fixed-point and programmer calculator backend for Simple mode:

- **In-process execution and zero optional runtime dependencies**: `SimpleCalcEngine` runs synchronously in the Qt process using Python's `fractions.Fraction`, `decimal.Decimal`, `core/format_engine.py`, and `core/scalar_fxp.py`. It does not spawn an IPython kernel and does not import `apytypes` or `numpy`.
- **Owned local state**: Each `SimpleCalcEngine(config: CalcConfig)` instance owns:
  - `primary_spec: FxpSpec | None` (with `.spec` falling back to `INT32 = FxpSpec(wl=32, fwl=0, signed=True)` when `None`) and `compare_specs: list[FxpSpec]`.
  - Current scalar state: `exact: Fraction`, `raw_uint: int`, `status_kind: str` (`"exact"`, `"quantized"`, `"saturated"`, or `"wrapped"`).
  - Shift state: `shift_mode: str` (one of `SHIFT_MODES`: `"arithmetic"`, `"logical"`, `"rotate"`, `"rotate_carry"`) and `carry_bit: int` (`0` or `1`).
  - Local variable table `variables: dict[str, VariableSnapshot | None]` pre-seeded with keys `"M"`, `"A"`, `"B"`, `"C"`, `"D"`, `"E"`, `"F"` (plus custom identifier slots added in `SimpleCalcWidget`), and `history: list[tuple[str, VariableSnapshot]]` capped at the last 100 entries.
- **Mode isolation and shared configuration**:
  - `SimpleCalcEngine.variables` and `SimpleCalcEngine.history` never enter `WorkspaceSession.user_ns`, `EditJournal`, or `CommandWindowWidget` history.
  - Switching between Simple and Python modes in `CalculatorWindow` disables/hides the inactive page on `ActivePageStack`. Python Studio (`CalcMainWindow` and `CalcController`) is instantiated lazily on the first switch to `"python"` and stays alive when returning to `"simple"`.
  - Both modes share `CalcConfig`. Local changes in Simple mode (primary FXP format, format history, quantization/overflow drawer, bit order) emit `local_state_changed`, which updates `CalculatorWindow.config` and calls `CalcController.update_config(cfg)` if Studio exists, or saves directly to disk otherwise.
- **Bounded exact evaluation and invariants**:
  - String inputs are capped at `_MAX_INPUT = 512` characters; parenthesis nesting is capped at `_MAX_DEPTH = 64`, while unary operators (`+`, `-`, `~`) parse iteratively to prevent `RecursionError`.
  - Word length `wl` is bounded to `[1, 256]` (the UI FXP drawer spinboxes use `1..128` for `WL` and `-128..128` for `FWL`), `abs(fwl) <= 256` in `parse_format()`, and shift counts are integers in `[0, 256]` evaluated with `unscaled=True` so shift counts are not scaled by `2^-fwl`.
  - `evaluate()` is transactional: it snapshots `carry_bit` before parsing and restores `carry_bit` on any exception without mutating `exact`, `raw_uint`, or `status_kind`.
  - `set_primary()` re-quantizes from `self.exact` rather than `self.real`, preserving exact `REAL` precision across primary format changes.
  - `parse_operand()` rejects `+` or `-` immediately after `0x`, `0o`, or `0b` prefixes, enforces `0 <= value < 2^wl` for non-`DEC` radix literals, and scales `DEC` integer literals by `2^-fwl`.
  - `display_fraction()` prints exact terminating decimals when `denominator = 2^a * 5^b` within `_MAX_EXACT_BITS = 3322` and `_MAX_EXACT_POWER = 1000` (avoiding Python's 4,300-digit `int` string limit), and falls back to 16-significant-digit `Decimal` notation otherwise.
- **Scope boundary**: `SimpleCalcEngine` handles scalar fixed-point and integer bit operations only. Arrays, NumPy/SciPy, `APyFloat`, `RpkFixed`/`RpkFixedArray`, `EditJournal`, and `startup.py` belong exclusively to `WorkspaceSession` (Python Studio and `--eval`).

---

## Python Studio Responsibilities and Invariants

- `kernel_client.py` starts and manages one IPython subprocess owned by the application over Jupyter channels bound to `127.0.0.1`, prepending `Path(__file__).resolve().parents[3]` to `PYTHONPATH` when present. Replies are processed asynchronously without blocking the Qt event loop. `_idle_clock()` excludes time spent busy running user cells from the 15-second internal request timeout, `_timed_out_ids` discards late replies from timed-out internal requests without clearing an active user run, and `_cleanup_resources()` tears down channels, kernel, log handles, and the temporary runtime directory on both shutdown and startup/health failures (25-second startup timeout). The kernel never restarts automatically; closing the window stops it.
- `kernel_runtime.py` executes user code inside the kernel, owns `WorkspaceSession` and `EditJournal`, and handles inspection, paging, copy, preview, statistics, and mutation requests. The GUI receives formatted snapshots rather than live Python objects or full arrays.
- `controller.py` serializes GUI requests (up to 32 queued operations), tracks `view_id` and workspace `revision`, and coordinates busy state (`interaction_enabled`). `restart_kernel()` resets `self.revision = -1` so the restarted kernel's `revision = 0` replies are accepted immediately. Failed or empty matrix page replies at matching `revision` emit `matrix_page_failed` so pending tiles do not stay stuck. `update_config()` forwards `KERNEL_CONFIG_FIELDS` (`format_preset`, `custom_signed_tpl`, `custom_unsigned_tpl`, `custom_int_tpl`, `default_quant`, `default_overflow`, `auto_import_groups`, `custom_import_groups`, `startup_file`, `visible_columns`) via `op: "config"`, skipping redundant queuing before `transport.ready` or while `_out_of_sync`. `inspector_pin_mode` is excluded from `KERNEL_CONFIG_FIELDS` because `CalcController` passes `pin_mode` explicitly per `preview`/`reformat` request.
- `CalcMainWindow` uses Qt Fusion style so checkbox indicators remain visible with the application QSS on Windows 11. Workspace scalar Value/Format activation routes to the Inspector's Value or shared conversion controls, while array Value opens the matrix editor. **Hints** (or **Help → Command hints**) opens a non-modal three-tab reference (**FXP**, **Python / NumPy**, **Workspace**) with **FXP** selected by default.
- `SettingsDialog` collects preferences and shows a read-only startup preview. Appearance and `keep_english_terms` update Qt only; numeric defaults update the live session. Applying changed built-in function packs or custom imports reloads configured imports in the existing kernel without executing `startup.py`, clearing the namespace, or restarting the kernel. Startup code runs at kernel start/restart or on an explicit **Reload and run** request.
- The Settings dialog places its Cancel/Apply footer below a stretchable tab area so the footer stays aligned as pages scroll or grow.
- Collapsing Inspector hides its body while keeping the title/arrow header visible; opening an array expands Inspector. Closing the window saves geometry, splitter sizes, and Inspector collapsed state.
- `protocol.py` and `MatrixTableModel` define JSON DTOs and bounded matrix pages (`PAGE_ROWS = 32`, `PAGE_COLS = 16`, up to `MAX_CACHED_PAGES = 8` cached pages in `MatrixTableModel`). Shape and slice metadata queries do not read the whole array. Workspace Min/Max scans values only while that column is visible; FXP array saturation summaries check at most 4,096 cells (`MAX_SATURATION_CHECK_CELLS`). Copying an entire matrix is capped at 100,000 cells (`MAX_COPY_CELLS`).
- The native **Convert to FXP…** dialog produces a kernel conversion request with `expected_revision`; FXP inline format controls and **Convert to float…** share the same conversion path.

---

## FXP Conversion Provenance and Status

APyTypes constructors create native values without calculator conversion-reference metadata, so they display `No conversion reference (error unknown)` even when their bits represent the input exactly. Python `float` uses binary64 (`0.1` is already a binary approximation of decimal 0.1). A GUI Real edit, `fxp(...)`, or Real-pinned reformat records a comparison source; raw-bit edits and Hex-pin reinterpretation do not.

For native `APyFixedArray` GUI edits, `WorkspaceSession` stores sparse provenance in a bounded sidecar (`_native_sources`) keyed to the original owner object without replacing the array or breaking aliases. Reads check owner identity, shape, FXP spec, and current raw bits; any mismatch yields unknown status. The sidecar retains at most 32 owners and 100,000 cell records using LRU eviction (dropping diagnostics only, never numeric values). Reset clears the sidecar, refresh drops vanished owners, `duplicate` copies valid records, and Undo/Redo restores source metadata.

Settings `default_quant` and `default_overflow` (defaults `"RND"` and `"SAT"`) apply to GUI real-value edits, native-to-FXP conversions, Real-pinned FXP format changes, and `fxp(...)` defaults. They do not override native APyTypes constructors (`APyFixed.from_float(...)` defaults to nearest ties away and `WRAP`) or console arithmetic.

`RpkFixed` and `RpkFixedArray` preserve GUI metadata and `copy()` behavior, while arithmetic operators inherit directly from APyTypes without calculator operand widening or result wrapping. Results follow native APyTypes/NumPy dispatch and carry no GUI conversion provenance.

---

## Workspace Array Saturation Markers

The kernel computes `WorkspaceRowSnapshot.is_saturated: bool | None` for Qt:

- `True`: at least one cell's current bits still match a tracked saturated GUI edit.
- `False`: every cell has a matching verified source and none saturated.
- `None`: provenance is incomplete, bits no longer match their recorded source, or the array has more than 4,096 cells (`MAX_SATURATION_CHECK_CELLS`).

Qt prefixes the Workspace Value cell of an FXP array with red `[SAT]` for `True`, neutral `[SAT?]` (with tooltip explaining incomplete provenance) for `None`, and no marker for `False`. The marker is display text only and never modifies `value_str` or copied values. A single-cell edit updates only that cell's saturation metadata without scanning the array, and per-cell Inspector checks still work regardless of array size.

---

## Native Values and the FXP Boundary

Python `int` keeps arbitrary precision, and console assignments such as `a = 2` follow Python rebinding semantics. Inspector represents native values with `PlainValueSnapshot` without inventing an FXP word length or pin semantics. Inspector can convert native real scalars/arrays to FXP and FXP values to Python `float` or NumPy `float64` (preserving array shape); `APyFloat` and unsupported types convert in the console.

The kernel produces read-only `native_bits` descriptors in `core/representations.py`:
- NumPy fixed-width integer scalars report dtype, width, signedness, hex, and binary without FXP bit editing.
- Python `int` Hex/Binary uses signed-magnitude notation; above 4,096 magnitude bits (`MAX_INTEGER_DISPLAY_BITS`), Inspector shows an explicitly marked bounded preview with copying of truncated text disabled.
- Python `float` and NumPy `float16`/`float32`/`float64` expose read-only sign, exponent, fraction, and special-value classifications from storage bits (`longdouble`, `complex`, and `APyFloatArray` layouts are not guessed).

GUI format input uses `FormatEngine.parse_input()` against `Q`, `MATLAB`, and active `custom` templates. Only simple placeholders that uniquely recover signedness, `wl`, and `fwl` are reversible; conflicts are rejected, and `CalcController` normalizes resolved specs to canonical `Qm.n` / `UQm.n` before sending requests.

The FXP Inspector Real-value field uses a bounded AST evaluator accepting numeric literals, `pi`/`e`, arithmetic, and integer bitwise operators while rejecting calls, attributes, containers, and general statements. Limits are 4,096 characters, 512 AST nodes, 16,384-bit integer results, exponent/shift magnitudes up to 4,096, and finite real results. The Python REPL is not restricted by this field parser.

---

## Numeric Representation DTO and `APyFloat` Edits

`PlainValueSnapshot.native_bits` is a JSON DTO produced by the kernel session for Qt. A scalar `APyFloat` descriptor looks like:

```json
{"kind":"float","dtype":"APyFloat","width":8,"hex":"0x1E","binary":"00011110","exp_bits":5,"man_bits":2,"bias":7,"classification":"Normal","editable":true}
```

`native_bits["editable"]` controls bit-toggle buttons separately from `PlainValueSnapshot.editable`. Only scalar `APyFloat` supports bit toggling (`to_bits` / `from_bits` preserve format, bias, and NaN payloads without casting through Python `float`). Native floats and selected array elements remain read-only. `EditJournal` fingerprints use raw bits and full format (distinguishing `+0.0` and `-0.0`).

---

## GUI Editing, Matrix Operations, and Command History

- **`EditJournal`** (`core/editing.py`): Owned by `CalcKernelRuntime`; tracks GUI numeric/bit edits to scalars, individual cells, and rectangular pastes up to 100 entries or 8 MiB without copying large arrays. Before Undo/Redo, it verifies variable binding, cell/slice coordinates, shape, dtype, strides, FXP format, and current value fingerprint. Any REPL execution, array structural operation, whole-array reformat, or slice change clears the journal.
- **TSV paste**: `paste_rectangle()` parses and validates the entire rectangle, bounds, dtype/range, and editability up to 100,000 cells (`MAX_EDIT_CELLS`) and 8 MiB before applying writes, rolling back on unexpected errors.
- **Matrix snapshots and statistics**: `MatrixSnapshot` carries `full_shape` and `slice_indices`; trailing axes beyond 2D default to `0`. `selection_statistics()` summarizes finite values over up to 100,000 cells via `float64`/`complex128` conversion (`count`, `finite_count`, `sum`, `mean`, `min_abs`, `max_abs`, plus `min`/`max` for real data).
- **Command history**: `CommandWindowWidget` buffers submitted console text in Qt (separate from IPython history), retaining up to 1,000 commands or 1 MiB (single commands over 1 MiB are omitted). Exporting writes UTF-8 `.py` files noting that IPython magics may require IPython.

---

## Display and DTO Scope

`Workspace` `numeric_display` (`"auto"`, `"scientific"`, `"fixed"`) and `numeric_precision` (`1` to `17` decimal places) format only finite real display strings in Workspace Value and Matrix `DisplayRole`; Inspector, Matrix `EditRole`, copied text, and raw bits stay exact. Fixed strings estimated over 256 characters fall back to scientific notation. **View → Reset layout** resets window size, splitters, and Inspector collapse state only.

Currently one command window owns one IPython kernel; adding multiple command windows later would require explicit decisions on kernel/namespace sharing, stream routing, and interrupt ownership.

---

## Kernel Protocol Envelope

Control requests use Jupyter `user_expressions` returning `application/json` (never pickle); `clc` publishes `CONTROL_MIME` (`"application/vnd.rpk.calc.control+json"`) over IOPub `display_data` with `action: "clear_console"`.

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

`validate_envelope()` checks `type(payload.get("protocol")) is int` and `protocol == 1`, plus matching `build` computed from `protocol.py`, `kernel_runtime.py`, and the 11 `core/*.py` files. Mismatches are rejected and require a kernel restart.

---

## Settings Transfer and Startup File Ownership

`SettingsDialog` exposes four pages: Appearance, FXP format, Auto-import, and Preferences. `core/settings_transfer.py` exports and validates portable JSON envelopes:

```json
{"kind":"rpk-calc-preferences","version":1,"preferences":{"language":"zh_TW","theme":"light"}}
```

Runtime `startup_file`, window geometry, splitter/collapse state, workspace variables, and command history are excluded from exports. Imports require explicit opt-in checkboxes for **Replace startup.py after Apply (does not run code)** (with SHA-256 revision check and `startup.before-import-HASH.py` backup) and **Include custom imports (runs after Apply)**.

`StartupFile` (`core/startup.py`) owns `startup.py` beside the active settings JSON file (for both GUI and `rpk-calc --eval`, using `--config` if supplied or `~/.rpkbin/calc_settings.json` otherwise). Legacy `startup_script` JSON values migrate once into `startup.py` (preserving an existing differing file by saving `startup.legacy-HASH.py`). Load order is: built-in packs, custom imports (validated by `parse_imports()` to allow only explicit absolute imports and comments without `_`-prefixed bindings), then `startup.py`. Each custom import statement is attempted independently so one failure does not drop valid imports.

---

## See Also

- [rpk-calc Quickstart and Reference](README.md)
- [Documentation Index](../README.md)
- [rpk-calc 架構與資料邊界 (繁體中文)](../../zh/calc/architecture.md)
