# `rpkbin.isasim`

[繁體中文](../../zh/isasim/README.md) · [Documentation Index](../README.md) · [Scenarios](scenarios.md) · [Assembler](assembler.md) · [API Reference](reference.md) · [Architecture](architecture.md) · [Visual Web Debugger (`isasim_gui`)](../app/isasim_gui/README.md)

`rpkbin.isasim` simulates retargetable instruction sets defined by a declarative Python `MCU` model, pairing a two-pass assembler with transactional execution, breakpoints, watchpoints, devices, and snapshots.

## Installation

Run this from the repository root to install the current source in editable mode:

```bash
python -m pip install -e .
```

The package uses only the Python standard library and requires Python 3.11 or newer. Installing in editable mode provides the `rpk-isasim` console command alongside `python -m rpkbin.isasim`.

## Syntax

```text
mcu = MCU(name)
image = assemble(mcu, source, *, filename="<string>", include_paths=(), defines=None, endian=None, case_sensitive_names=True)
with Simulator(mcu, *, trace_reads=False, history=1000) as sim:
    sim.load_assembly(image)
    result = sim.run(max_steps=100)
snap = sim.snapshot()
sim.restore(snap)
```

```bash
rpk-isasim assemble MCU.py SOURCE.asm [-o IMAGE] [--listing FILE]
rpk-isasim run MCU.py PROGRAM --max-steps N
rpk-isasim repl MCU.py [PROGRAM]
```

## Description

- `MCU(name)` declares memory spaces, registers, fields, register aliases, memory aliases (`mem_alias`), IRQ lines, instruction patterns, and stop policies.
- `assemble(mcu, source, ...)` preprocesses includes and conditionals and encodes the program into an `Assembly` bundle without running instruction callbacks.
- `Simulator(mcu, ...)` owns all mutable runtime state, passes a checked `State` into callbacks, exposes read-only `Simulator.state`, and closes any active trace file on exiting a `with` block.
- `sim.snapshot()` and `sim.restore(snap)` capture and restore simulator, device, and stimulus runtime state after verifying structural compatibility.
- `rpk-isasim` runs the assembler, headless batch execution, or the interactive REPL from the shell.

## Examples

### 1. Minimal Example: Assemble and Run a Tiny Program

Define a 5-bit program cell and a 5-bit `value` register, assemble four lines, and run inside a `Simulator` context manager. `PLUS` adds modulo 32 (13, 24, then 2 after 34 wraps to five bits), and `END` halts.

```python
from rpkbin.isasim import MCU, Simulator, assemble, helpers

mcu = MCU("demo")
mcu.space("prog", cell_bits=5, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.reg("cursor", bits=5, reset=0, is_pc=True)
mcu.reg("value", bits=5, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0 iiii", syntax="PLUS {i}", cycles=2)
def plus(st, i):
    st.reg["value"] = helpers.add_with_carry(st.reg["value"], i, 0, 5).result

@mcu.instr("10000", syntax="END", cycles=1)
def end(st):
    st.halt("done")

source = """start:
PLUS 13
PLUS 11
PLUS 10
END
"""
image = assemble(mcu, source, filename="demo.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(image)
    result = sim.run(max_steps=100)
    print(result.stop_reason, result.steps, result.cycles, result.pc)
    print(sim.read("value"))
```
```text
halted 4 7 4
2
```

### 2. Progressive Example: Source Breakpoint, Snapshot, and Replay

Set a source-line breakpoint at `:3` (before the second `PLUS`), capture a `Snapshot`, run to completion, then restore the snapshot and verify identical replay. Inspect `repr()` summaries across `MCU`, `Assembly`, `Simulator`, and `Snapshot`.

```python
from rpkbin.isasim import MCU, Simulator, assemble, helpers

mcu = MCU("demo")
mcu.space("prog", cell_bits=5, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.reg("cursor", bits=5, reset=0, is_pc=True)
mcu.reg("value", bits=5, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0 iiii", syntax="PLUS {i}", cycles=2)
def plus(st, i):
    st.reg["value"] = helpers.add_with_carry(st.reg["value"], i, 0, 5).result

@mcu.instr("10000", syntax="END", cycles=1)
def end(st):
    st.halt("done")

source = """start:
PLUS 13
PLUS 11
PLUS 10
END
"""
image = assemble(mcu, source, filename="demo.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(image)
    sim.add_breakpoint(":3")
    paused = sim.run(max_steps=100)
    print(paused.stop_reason, paused.steps, paused.cycle, paused.pc, sim.read("value"))

    snap = sim.snapshot()
    first = sim.run(max_steps=100)
    sim.restore(snap)
    second = sim.run(max_steps=100)
    assert (first.stop_reason, first.cycles, first.pc) == (second.stop_reason, second.cycles, second.pc)
    print(second.stop_reason, second.steps, second.cycles, sim.read("value"))
    print(repr(mcu))
    print(repr(image))
    print(repr(sim))
    print(repr(snap))
```
```text
breakpoint 1 2 1 13
halted 3 5 2
MCU('demo', spaces=1, registers=2, instructions=2)
Assembly('demo.asm', cells=4, symbols=1)
Simulator('demo', pc=0x04, cycle=7, steps=4)
Snapshot(mcu='demo', cycle=2, steps=1)
```

## Documentation Map

| Guide | Covers |
| :--- | :--- |
| [Scenarios](scenarios.md) | Self-contained examples for tracing, source debugging, watchpoints, `mem_alias`, devices, and snapshots |
| [Assembler](assembler.md) | Operand declarations, expressions, `.include`/`.if` preprocessing, `.isa.json` bundles, and CLI assembly |
| [API Reference](reference.md) | Exact signatures for `MCU`, `Simulator`, `Device`, `Snapshot`, `helpers`, CLI flags, and all 25 REPL commands |
| [Architecture](architecture.md) | File responsibilities, state ownership, transaction rollback, and memory-alias invariants |
| [Visual Web Debugger (`isasim_gui`)](../app/isasim_gui/README.md) | Browser-based stepping, conditional breakpoints, inline poke, and `DebugSession` Python API |

## Tips and Limitations

- **State boundary**: Instruction callbacks receive a writable `State` during step execution. `Simulator.state` is a read-only view; external updates use `poke(target, value)`.
- **Breakpoints and `reset()`**: `run()` skips a breakpoint once when resuming from its address, whereas `step()` ignores breakpoints. `reset()` preserves loaded program images and `mem_alias` registrations while clearing registers, memory initialization outside loaded images, and stimulus counters.
- **Model scope**: Each instruction declaration has a fixed cell length. `isasim` executes the patterns and semantics supplied by your MCU model and does not infer hardware behavior from an MCU name.

## See Also

- [Scenarios](scenarios.md)
- [Assembler](assembler.md)
- [API Reference](reference.md)
- [Architecture](architecture.md)
- [Visual Web Debugger (`isasim_gui`)](../app/isasim_gui/README.md)
- [Documentation Index](../README.md)
