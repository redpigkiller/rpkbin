# `rpkbin.isasim` Reference

Retargetable instruction-set simulator (`MCU`, `Simulator`) with a two-pass assembler (`assemble` / `Assembly`), breakpoints/watchpoints, memory aliases (`mem_alias`), memory-mapped `Device` support, traces, and deterministic `Snapshot` checkpoints.

## Public API (`rpkbin.isasim`)

- `MCU(name: str)` — Declarative machine model:
  - `.space(name, *, cell_bits, size, access, reset_state, init, is_program=False, role=None, regions=None)`
  - `.reg(name, *, bits, reset, is_pc=False, fields=None, role=None)`
  - `.alias(name, target)`, `.mem_alias(name, target, *, endian=None, bit_order=None)`
  - `.irq_line(name)`, `.interrupt_check(fn)`
  - `.instr(pattern, *, syntax, cycles, name=None, operands=None, overrides=None, kind=None)`
  - Policies: `.on_undefined_opcode(action, ...)`, `.on_out_of_range(action, ...)`, `.on_write_to_readonly(action)`, `.on_uninitialized_read(action)`
  - `.describe() -> MCUDescription`, properties `.aliases`, `.mem_aliases`.
- `load_mcu(path: str | os.PathLike[str]) -> MCU` — Loads an `MCU` from a `.py` file.
- `assemble(mcu, source, *, filename='<string>', include_paths=(), defines=None, endian=None, case_sensitive_names=True) -> Assembly`
- `Simulator(mcu, *, trace_reads=False, history=1000)` — Supports `with Simulator(mcu) as sim:` (auto-closes trace on exit):
  - Loading: `load_assembly(image)`, `load_cells(space, cells)`, `load_hex(...)`, `load_bin(..., base=...)`, `load_symbols(...)`
  - Execution: `step(n=1) -> RunResult`, `run(*, max_steps, max_cycles=None, stuck_window=1000, raise_exceptions=False, stuck_ignore=()) -> RunResult`, `reset()`, `snapshot() -> Snapshot`, `restore(snap)`
  - State & Aliases: `read(target)`, `poke(target, value)`, `mem_alias(...)`, `clear_mem_aliases()`, `initialized_ranges(space)`
  - Debug & Stimuli: `add_breakpoint(...)`, `add_watchpoint(target, *, kind)`, `delete(which)`, `at_cycle(n)`, `at_pc(where, *, times=1)`, `on_read(...)`, `on_write(...)`, `read_seq(...)`, `attach(device, *, space, lo, hi)`
  - Inspection & Traces: `disassemble(...)`, `source_context(...)`, `recent_events(n)`, `enable_trace(path, *, fmt='text')`, `disable_trace()`, `evaluate(expr)`.
- `rpkbin.isasim.helpers` (`AddResult`, `SubResult`, `add_with_carry`, `sub_with_borrow`, `bit`, `set_bit`, `sign_extend`, `rotate_left`, `rotate_right`, `to_signed`, `to_unsigned`, `parity`, `carry_out_of_bit`).

## CLI (`rpk-isasim` / `python -m rpkbin.isasim`)

```bash
rpk-isasim assemble mcu.py prog.asm -o prog.isa.json -I inc --endian little
rpk-isasim run mcu.py prog.asm --max-steps 500
rpk-isasim repl mcu.py prog.asm
```

Also supports `-V` / `--version`. Default `assemble` output writes `<source_parent>/<source_stem>.isa.json`.

## Gotchas

- Always pass `max_steps=` cleanly in `sim.run(max_steps=...)` (infinite loops trigger `stop_reason='stuck'` after `stuck_window` repeating states).
- Calling `step()`/`run()`/`restore()` re-entrantly inside a running instruction/trigger callback raises `UsageError`.
- `Snapshot.save()` encodes wide registers atomically in memory before opening the file so failed serializations cannot truncate existing checkpoints.

## Recipes

### 1. Minimal `MCU` + Two-Pass Assembly + Context-Managed `Simulator`

```python
from rpkbin.isasim import MCU, Simulator, assemble

mcu = MCU("tiny")
mcu.space("prog", cell_bits=8, size=16, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("acc", bits=8, reset=0)
for pol in ("on_undefined_opcode", "on_out_of_range", "on_write_to_readonly", "on_uninitialized_read"):
    getattr(mcu, pol)("stop")

@mcu.instr("0001 iiii", syntax="LOADI {i}", cycles=1)
def loadi(st, i): st.reg["acc"] = i

@mcu.instr("11110000", syntax="HALT", cycles=1)
def halt(st): st.halt("done")

image = assemble(mcu, "LOADI 9\nHALT\n", filename="tiny.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(image)
    res = sim.run(max_steps=20)
    assert res.stop_reason == "halted" and sim.read("acc") == 9
```

### 2. Memory Alias + Checkpoint Snapshot Replay

```python
from rpkbin.isasim import MCU, Simulator

mcu = MCU("mem_demo")
mcu.space("prog", cell_bits=8, size=4, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=2, reset=0, is_pc=True)
mcu.mem_alias("word16", "ram[0x00:0x02]", endian="little", bit_order="msb_first")
for pol in ("on_undefined_opcode", "on_out_of_range", "on_write_to_readonly", "on_uninitialized_read"):
    getattr(mcu, pol)("stop")

@mcu.instr("00000000", syntax="HALT", cycles=1)
def h(st): st.halt("ok")

with Simulator(mcu) as sim:
    sim.load_cells("prog", {0: 0})
    sim.poke("ram[0]", 0x34); sim.poke("ram[1]", 0x12)
    snap = sim.snapshot()
    assert sim.read("word16") == 0x1234
    sim.poke("ram[0]", 0x00)
    sim.restore(snap)
    assert sim.read("word16") == 0x1234
```
