# `rpkbin.app.isasim_gui` Visual Web Debugger

[繁體中文](../../../zh/app/isasim_gui/README.md) · [isasim Quickstart](../../isasim/README.md) · [Documentation Index](../../README.md)

`rpkbin.app.isasim_gui` serves a loopback-only browser debugger on top of [`rpkbin.isasim`](../../isasim/README.md), with forward and reverse stepping, conditional breakpoints, inline register/memory poke, and a `DebugSession` Python API.

## Installation and Launch

The GUI uses only the Python standard library on top of `rpkbin.isasim`. Install the package in editable mode from the repository root:

```bash
python -m pip install -e .
```

Start the server with `rpk-isasim-gui` or `python -m rpkbin.app.isasim_gui` (pass `--script` to run a `setup(sim)` function after loading):

```bash
rpk-isasim-gui path/to/mcu.py path/to/program.asm --port 8000
rpk-isasim-gui path/to/mcu.py path/to/program.asm --script path/to/setup_stim.py --port 8000 --open
```

### Run the Included Demo

From the repository root, launch the debugger on [demo_loop.asm](../../../../playground/isasim_demo/demo_loop.asm):

```bash
python -m rpkbin.app.isasim_gui playground/isasim_demo/demo_mcu.py playground/isasim_demo/demo_loop.asm
```

Because `demo_mcu.py` declares `kind="call"`, `kind="return"`, and `role="flag"`, `--call-mnemonics` and `--return-mnemonics` are not required. Open `http://127.0.0.1:8000` and click **Run** to reach `HLT` (`r0` and `data[0x11]` both equal `30` / `0x1e`). Set a breakpoint on `CALL accumulate_step` to step over the subroutine with **Step Over (F10)**.

## Syntax

```bash
rpk-isasim-gui [-h] [-V] [--script SCRIPT] [--host {127.0.0.1,localhost}] [-p PORT] [--open] [--no-browser] [--max-history MAX_HISTORY] [--call-mnemonics CALL_MNEMONICS] [--return-mnemonics RETURN_MNEMONICS] mcu program
```

```python
from rpkbin.app.isasim_gui import DebugSession, create_server, relocate_breakpoints

session = DebugSession(mcu_path, asm_path, script_path=None, max_history=200, *, call_mnemonics=(), return_mnemonics=())
server = create_server(session, *, host="127.0.0.1", port=8000)
relocated = relocate_breakpoints(old_lines, new_lines, breakpoints)
```

## Description

- `rpk-isasim-gui` builds a `DebugSession` for an MCU `.py` file and `.asm` / `.isa.json` program, binds a loopback HTTP server via `create_server()`, and optionally opens the default browser with `--open`.
- `DebugSession` wraps `Simulator` with snapshot ring-buffer history (`Step Back`), source-row breakpoint tracking, subroutine depth stepping (`Step Over` / `Step Out`), register/memory diff tracking, and memory view filtering.
- `create_server()` returns a `DebuggerHTTPServer` (`ThreadingHTTPServer` subclass) bound strictly to `127.0.0.1` with per-session CSRF token (`X-Isasim-Token`) and same-origin enforcement.
- `relocate_breakpoints()` maps 1-based line breakpoints across reloaded source text using a 5-line stripped context window.

## UI Overview and Core Features

### 1. Top Toolbar and Execution Controls

| Button | Shortcut | Behavior |
| :--- | :---: | :--- |
| **Run** | `F5` | Continue execution until a breakpoint, `halt`, watchpoint, fault, or step limit. |
| **Step Over** | `F10` | Step over a call instruction (`kind="call"` or `--call-mnemonics`) to the next instruction, stopping earlier on breakpoints/watchpoints; otherwise acts like Step In. |
| **Step In** | `F11` | Execute one step. |
| **Step Out** | `Shift+F11` | Run until returning from the current subroutine (`kind="return"` or `--return-mnemonics`). |
| **Step Back** | `B` | Restore the previous snapshot from the history ring buffer (`PC`, registers, memory, and trace). |
| **Reset** | `-` | Reset simulator state (re-running `--script` if set) while keeping breakpoints and watchpoints. |
| **Reload** | `-` | Reload `mcu.py` and `.asm` from disk and relocate existing breakpoints via `relocate_breakpoints()`. |
| **Clear BP** | `-` | Remove all active breakpoints. |
| **Watch** | `-` | Toggle the right-pane **Watchpoints** view (shows a badge when active count `> 0`). |

### 2. Status Pill and Zeroable Cycles / Steps Stopwatch

The top-right status pill displays the stop state alongside cumulative **Cycles** and **Steps**. Click **`↺`** inside the pill to set the current counts as the `0` baseline (`Step Back` clamps at `0` if stepping before the baseline; `Reset` and `Reload` clear the offset).

Rendered status pill labels and underlying `stop_reason` codes:

| Dot Color | Pill Label | Underlying `stop_reason` |
| :--- | :--- | :--- |
| Green | `READY`, `STEP`, `STEP BACK`, `SET PC`, `POKE` | `ready`, `step_done`, `step_back`, `set_pc`, `poke` |
| Amber (warning) | `BREAKPOINT`, `WATCHPOINT`, `STEP LIMIT`, `CYCLE LIMIT`, `INTERRUPTED` | `breakpoint`, `watchpoint`, `step_limit` / `max_steps`, `max_cycles`, `interrupted` |
| Red (halt/error) | `HALTED`, `DEBUG ERROR` | `halted`, `debug_error` |
| Red + Error Banner | `FAULT`, `UNINIT READ`, `UNDEF OPCODE`, `OUT OF RANGE`, `READONLY WRITE`, `PYTHON ERROR`, `STUCK` | `fault`, `uninitialized_read`, `undefined_opcode`, `out_of_range`, `readonly_write`, `python_error`, `stuck` |

### 3. Left Pane: ASM Source

- **Breakpoints and Conditions (`if`)**: Click the gutter circle to toggle a breakpoint, or click the filter icon to attach a condition in the restricted integer/boolean language documented in the [isasim API Reference](../../isasim/reference.md). Bare shorthand names (`r1 == 3`, `ram[0x04] > 10`) and explicit forms (`r["reg"].field`) are supported; condition strings are preserved verbatim and validated on install.
- **Direct `PC` Jump (`Set PC`)**: The active PC row shows `▶`. Hovering over another instruction row reveals `▷`; double-click `▷` to move `PC` to that instruction.
- **Multi-Word Code Alignment**: The `Code` column left-aligns multi-word encodings (`8009 2ab7`) and widens automatically.

### 4. Right Pane: Registers, Constants, Aliases, Memory, Mem Aliases, Trace, and Watchpoints

- **Top-Right (`Registers` | `Constants` | `Aliases`)**:
  - `Registers`: Renders a wrapping `FLAGS` bar when the MCU declares `role="flag"` registers (click 1-bit pills to toggle, or double-click cells to poke decimal/`0x`/`0b` values). Supports row drag-reordering, comma-separated filtering, register visibility checklist, and column drag-reorder/resize.
  - `Constants`: Lists assembly constants (`Name`, `Hex`, `Unsigned`, `Signed`) with comma-separated filtering.
  - `Aliases`: Lists MCU register aliases (`mcu.alias`) with inline double-click poke.
- **Bottom-Right (`Memory` | `Mem Aliases` | `Trace`)**:
  - `Memory`: Filters by single addresses (`0x04`), closed ranges (`0x10..0x17` or `16-23`), and length slices (`0x20:4`). `Hide Uninitialized` is enabled by default; unreadable device cells display `--`. Supports inline double-click poke.
  - `Mem Aliases`: Tracks `mcu.mem_alias` and `setup(sim)` `sim.mem_alias` entries. Click **`+ Select...`** to pick aliases (`*`/`?` wildcards by default, plus **`Aa`** Match Case, **`ab`** Whole Word, and **`.*`** Regex toggles, persisted per program in `localStorage`), edit values via double-click poke, or untrack rows.
  - `Trace`: Lists up to 30 recent events newest-first. Clicking a row scrolls the ASM pane to that source line without changing `PC`.
- **Watchpoints (`Watch` button)**: Add `Write`, `Read`, or `Read/Write` watchpoints on registers, aliases, `mem_alias` names, `space[addr]`, or `space[lo:hi]` with optional conditions.
- **Persistent Splitters**: Vertical and horizontal divider positions persist in `localStorage` (`isasim_right_width`, `isasim_reg_height`).

### 5. UI and Font Settings (`⚙`)

Adjust `Toolbar` (`Full` / `Compact`), `Source Font` (`10px`-`20px`), `Tables Font` (`10px`-`18px`), `Freeze Cols` (`None` / `PC` / `Code`), `Reg Hover Tip` (`On` / `Off`), or click `Reset Defaults` (`--right-width: 480px`, `--reg-height: 48%`).

## CLI Reference

### Options

| Flag | Env Var | Default | Description |
| :--- | :--- | :--- | :--- |
| `-h`, `--help` | `-` | `-` | Show help message and exit. |
| `-V`, `--version` | `-` | `-` | Print `rpk-isasim-gui 0.3.0` and exit. |
| `mcu` | `-` | required | Path to Python MCU definition file (`.py`). |
| `program` | `-` | required | Path to assembly source (`.asm`) or bundle (`.isa.json`). |
| `--script SCRIPT` | `-` | `None` | Optional Python setup script defining `setup(sim)`. |
| `--host {127.0.0.1,localhost}` | `-` | `127.0.0.1` | Local GUI bind address (`localhost` binds `127.0.0.1`). |
| `-p PORT`, `--port PORT` | `-` | `8000` | TCP port (`0..65535`; `0` picks an ephemeral port). |
| `--open` | `-` | `False` | Open the GUI URL automatically in the default browser. |
| `--no-browser` | `-` | `True` | Do not open a browser automatically (cannot combine with `--open`). |
| `--max-history MAX_HISTORY` | `-` | `200` | Maximum back-step snapshots (`0` disables history). |
| `--call-mnemonics LIST` | `-` | `()` | Call mnemonics when the MCU does not declare `kind="call"`. |
| `--return-mnemonics LIST` | `-` | `()` | Return mnemonics when the MCU does not declare `kind="return"`. |

**Mnemonic separator note**: `--call-mnemonics` and `--return-mnemonics` split on commas or whitespace (`re.split(r"[,\s]+", value.strip())`), so quote values containing spaces in a shell (for example `--call-mnemonics "CALL, JSR"` or `--call-mnemonics CALL,JSR`). Matching is case-insensitive and the two lists must be disjoint.

### Exit Codes

- `0`: Server stopped cleanly or via `KeyboardInterrupt`.
- `2`: Invalid CLI arguments or startup error (`IsaSimError`, `OSError`, `SyntaxError`, `ImportError`, `ValueError`).

## Python API

`rpkbin.app.isasim_gui` exports `DebugSession`, `create_server`, and `relocate_breakpoints` (`__all__`).

### `DebugSession`

#### Creation

```python
session = DebugSession(
    mcu_path: str | Path,
    asm_path: str | Path,
    script_path: str | Path | None = None,
    max_history: int = 200,
    *,
    call_mnemonics: Iterable[str] = (),
    return_mnemonics: Iterable[str] = (),
)
```

`repr(session)` returns `DebugSession('<mcu_path>', '<asm_path>', max_history=<n>)`.

#### Properties

| Property | Type | Meaning |
| :--- | :--- | :--- |
| `mcu` | `MCU` | Loaded `MCU` model for the active program. |
| `sim` | `Simulator` | Underlying `Simulator` instance. |
| `mcu_path` | `Path` | Resolved path to the MCU Python file. |
| `asm_path` | `Path` | Resolved path to the `.asm` or `.isa.json` file. |
| `script_path` | `Path \| None` | Resolved path to the optional setup script. |
| `max_history` | `int` | Snapshot ring-buffer limit (`0` disables `step_back`). |

#### Object Functions

| Method | Purpose |
| :--- | :--- |
| `step_in() -> dict[str, Any]` | Execute one step and return updated UI state dict. |
| `step_over(max_steps=10000) -> dict[str, Any]` | Step over current call instruction (`kind_of(pc)`). |
| `step_out(max_steps=10000) -> dict[str, Any]` | Step out of current subroutine. |
| `step_back() -> dict[str, Any]` | Restore previous history snapshot (`step_back`). |
| `continue_run(max_steps=50000) -> dict[str, Any]` | Run via `sim.run(max_steps=max_steps)`. |
| `run_to_cursor(file, line, max_steps=50000) -> dict[str, Any]` | Run with temporary breakpoint at `(file, line)`. |
| `set_pc(file, line) -> dict[str, Any]` | Move `PC` directly to the instruction at `(file, line)`. |
| `poke(target, value) -> dict[str, Any]` | Validate range and poke register, field, cell, `irq.line`, or `mem_alias`. |
| `toggle_breakpoint(file, line, condition=None) -> dict[str, Any]` | Add, update, or remove a line breakpoint (`condition` allows bare shorthand). |
| `clear_breakpoints() -> dict[str, Any]` | Remove all breakpoints. |
| `add_watchpoint(target, kind="w", condition=None) -> dict[str, Any]` | Install a watchpoint and return updated state. |
| `remove_watchpoint(watchpoint_id) -> dict[str, Any]` | Remove watchpoint by integer or decimal string ID. |
| `clear_watchpoints() -> dict[str, Any]` | Remove all watchpoints. |
| `reset() -> dict[str, Any]` | Reset simulation state (re-running `--script` if provided) while preserving breakpoints and watchpoints. |
| `reload() -> dict[str, Any]` | Reload `mcu_path` and `asm_path` from disk and relocate breakpoints. |
| `get_state(memory_space=None, memory_filter=None, hide_uninit=None) -> dict[str, Any]` | Update persisted memory view filters and return full state dict. |
| `view_state(memory_space=None, memory_filter=None, hide_uninit=None) -> dict[str, Any]` | Return state dict for temporary memory view parameters without mutating session defaults. |
| `set_memory_view(memory_space=None, memory_filter=None, hide_uninit=None) -> None` | Update memory view parameters without building state JSON. |
| `kind_of(pc) -> str \| None` | Return `"call"`, `"return"`, or `None` using MCU instruction kinds or fallback mnemonics. |

### `create_server`

#### Syntax

```python
server = create_server(session: DebugSession, *, host: str = "127.0.0.1", port: int = 8000) -> DebuggerHTTPServer
```

#### Description

Creates a `DebuggerHTTPServer` (`ThreadingHTTPServer` subclass) serving the static UI (`/`), `GET /api/state`, and `POST` action endpoints. Enforces loopback client IP (`127.0.0.1`), loopback `Host`, same-origin `Origin`, and `X-Isasim-Token` (`server.token`) on mutating requests. Raises `ValueError` if `host != "127.0.0.1"` or `port` is not an integer in `0..65535`. Passing `port=0` allocates an ephemeral port readable from `server.server_port`.

### `relocate_breakpoints`

#### Syntax

```python
relocated = relocate_breakpoints(old_lines: list[str], new_lines: list[str], breakpoints: dict[int, str | None]) -> dict[int, str | None]
```

#### Description

Relocates 1-based line breakpoints (`{line: condition_or_none}`) after editing source lines. Each breakpoint builds a 5-line stripped context tuple (`line - 2 .. line + 2`, padded with `"<BOF>"` and `"<EOF>"`). If the same line still matches in `new_lines`, it keeps its line number; otherwise it relocates only when there is a unique matching window in `new_lines`.

### Example: `DebugSession`, `relocate_breakpoints`, and `create_server`

Run from the repository root:

```python
from rpkbin.app.isasim_gui import DebugSession, create_server, relocate_breakpoints

session = DebugSession(
    "playground/isasim_demo/demo_mcu.py",
    "playground/isasim_demo/demo_loop.asm",
)
st1 = session.step_in()
st2 = session.continue_run()
print("states:", st1["status"]["stop_reason"], st2["status"]["stop_reason"], st2["status"]["pc_hex"], session.sim.read("r0"))

old_src = ["start:", "LDI r0, 0", "LDI r1, 5", "loop:", "CALL sub", "ST r0, [0x10]", "HLT"]
new_src = ["; banner", "start:", "LDI r0, 0", "LDI r1, 5", "loop:", "CALL sub", "ST r0, [0x10]", "HLT"]
print("relocated:", relocate_breakpoints(old_src, new_src, {5: "r1 == 3"}))

server = create_server(session, port=0)
try:
    print("server_bound:", server.server_port > 0, len(server.token) > 0)
finally:
    server.server_close()
```

Output:
```text
states: step_done halted 0x0c 30
relocated: {6: 'r1 == 3'}
server_bound: True True
```

## Tips and Limitations

- **Trusted files**: `mcu.py` and `--script` execute Python code locally; only open trusted definitions and setup scripts.
- **Snapshot requirement for `Step Back`**: Devices without `get_state` / `set_state` disable `Step Back` (`can_step_back == False`) and display a status notice without preventing forward stepping.
- **Loopback security**: `create_server` binds only to `127.0.0.1`; remote browser access requires an SSH tunnel.

## See Also

- [isasim Quickstart](../../isasim/README.md)
- [isasim API Reference](../../isasim/reference.md)
- [Documentation Index](../../README.md)
