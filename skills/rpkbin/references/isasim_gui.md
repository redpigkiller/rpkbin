# `rpkbin.app.isasim_gui` Reference

Local loopback HTTP visual debugger layered on `rpkbin.isasim`, featuring reverse-step history, conditional breakpoints, inline poke, call-stack inference, and peripheral panels.

## Public API (`rpkbin.app.isasim_gui`)

- `DebugSession(mcu_path: str | Path, asm_path: str | Path, script_path: str | Path | None = None, max_history: int = 200, *, call_mnemonics: Iterable[str] = (), return_mnemonics: Iterable[str] = ())`
  - Methods: `step_in()`, `step_over(max_steps=10000)`, `step_out(max_steps=10000)`, `step_back()`, `continue_run(max_steps=50000)`, `run_to_cursor(file, line, max_steps=50000)`, `toggle_breakpoint(file, line, condition=None)`, `clear_breakpoints()`, `add_watchpoint(target, kind='w', condition=None)`, `remove_watchpoint(watchpoint_id)`, `clear_watchpoints()`, `poke(target, value)`, `reset()`, `reload()`, `set_pc(file, line)`, `set_memory_view(...)`, `get_state(...)`, `view_state(...)`, `kind_of(pc)`.
- `create_server(session: DebugSession, *, host: str = '127.0.0.1', port: int = 8000) -> DebuggerHTTPServer`
- `relocate_breakpoints(old_lines: list[str], new_lines: list[str], breakpoints: dict[int, str | None]) -> dict[int, str | None]`

## CLI (`rpk-isasim-gui` / `python -m rpkbin.app.isasim_gui`)

```bash
rpk-isasim-gui mcu.py prog.asm -p 8000 --open
rpk-isasim-gui mcu.py prog.asm --script setup.py --no-browser
rpk-isasim-gui mcu.py prog.asm --call-mnemonics "CALL,JAL" --return-mnemonics "RET"
rpk-isasim-gui --version
```

Note: `--host` only accepts `127.0.0.1` or `localhost` (loopback security).

## Gotchas

- `max_history=0` disables reverse-step snapshot capture; default `200` supports step-back without mutating `relocate_breakpoints`.
- Stop reasons rendered in status pills cover `HALTED`, `BREAKPOINT`, `WATCHPOINT`, `STEP LIMIT`, `DEBUG ERROR`, `STUCK`, `UNDEFINED_OPCODE`, `OUT_OF_RANGE`, `READONLY_WRITE`, `PYTHON_ERROR`.
- Wait on server lifecycle with `try: ... finally: server.server_close()` when embedding `create_server(...)` in scripts or tests.

## Recipes

### 1. Headless `DebugSession` Stepping & Breakpoint Relocation

```python
import tempfile
from pathlib import Path
from rpkbin.app.isasim_gui import DebugSession, relocate_breakpoints

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    mcu_py = root / "mcu.py"
    asm = root / "prog.asm"
    mcu_py.write_text(
        "from rpkbin.isasim import MCU\n"
        "mcu = MCU('gui_demo')\n"
        "mcu.space('prog', cell_bits=8, size=8, access='ro', reset_state='initialized', init=0, is_program=True)\n"
        "mcu.reg('pc', bits=3, reset=0, is_pc=True)\n"
        "mcu.reg('r0', bits=8, reset=0)\n"
        "for p in ('on_undefined_opcode','on_out_of_range','on_write_to_readonly','on_uninitialized_read'):\n"
        "    getattr(mcu, p)('stop')\n"
        "@mcu.instr('0001 iiii', syntax='SET {i}', cycles=1)\n"
        "def s(st, i): st.reg['r0'] = i\n"
        "@mcu.instr('11110000', syntax='HLT', cycles=1)\n"
        "def h(st): st.halt('ok')\n",
        encoding="utf-8",
    )
    asm.write_text("SET 5\nHLT\n", encoding="utf-8")
    sess = DebugSession(mcu_py, asm)
    snap = sess.step_in()
    assert any(r["name"] == "r0" and r["value"] == 5 for r in snap["registers"])
    snap_back = sess.step_back()
    assert any(r["name"] == "r0" and r["value"] == 0 for r in snap_back["registers"])
    moved = relocate_breakpoints(
        ["SET 1", "SET 2", "SET 5", "HLT", "END"],
        ["SET 0", "SET 1", "SET 2", "SET 5", "HLT", "END"],
        {4: None},
    )
    assert moved == {5: None}
```
