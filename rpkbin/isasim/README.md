# `rpkbin.isasim`

English documentation: [isasim Quickstart](../../docs/en/isasim/README.md) · 繁體中文文件：[isasim 快速入門](../../docs/zh/isasim/README.md)

`rpkbin.isasim` executes instruction programs against a declarative Python MCU model, with two-pass assembly, breakpoints, watchpoints, stimuli, devices, and snapshots.

## Syntax

```python
mcu = MCU(name)
image = assemble(mcu, source, *, filename="<string>", include_paths=(), defines=None, endian=None, case_sensitive_names=True)
with Simulator(mcu, *, trace_reads=False, history=1000) as sim:
    sim.load_assembly(image)
    result = sim.run(max_steps=100)
```

```bash
rpk-isasim assemble MCU.py SOURCE.asm [-o IMAGE] [--listing FILE]
rpk-isasim run MCU.py PROGRAM --max-steps N
rpk-isasim repl MCU.py [PROGRAM]
```

## Description

- `MCU(name)` declares memory spaces, registers, bitfields, register aliases, memory aliases (`mem_alias`), IRQ lines, instruction patterns, and stop policies.
- `assemble(mcu, source, ...)` translates assembly source and included files into an `Assembly` bundle without executing instruction callbacks.
- `Simulator(mcu, ...)` owns all mutable execution state, validates accesses through `State`, emits `Event` records, and closes any open trace file when used in a `with` block.
- `rpk-isasim` (equivalent to `python -m rpkbin.isasim`) exposes `assemble`, `run`, and `repl` subcommands from the command line.

## Examples

### 1. Minimal Example

Define a 5-bit MCU, assemble a short program, and run it inside a `Simulator` context manager.

```python
from rpkbin.isasim import MCU, Simulator, assemble, helpers

mcu = MCU("demo")
mcu.space("prog", cell_bits=5, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.reg("pc", bits=5, reset=0, is_pc=True)
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

image = assemble(mcu, "PLUS 13\nPLUS 11\nPLUS 10\nEND\n", filename="demo.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(image)
    result = sim.run(max_steps=100)
    print(result.stop_reason, result.steps, result.cycles, sim.read("value"))
```

Output:
```text
halted 4 7 2
```

## Tips and Limitations

- Uses only the Python standard library and requires Python 3.11 or newer.
- Instruction encodings have fixed lengths per declaration; `isasim` executes the semantics you supply and does not infer hardware behavior from the MCU name.

## See Also

- [English Documentation](../../docs/en/isasim/README.md) · [API Reference](../../docs/en/isasim/reference.md) · [Assembler](../../docs/en/isasim/assembler.md) · [Scenarios](../../docs/en/isasim/scenarios.md) · [Architecture](../../docs/en/isasim/architecture.md)
- [繁體中文文件](../../docs/zh/isasim/README.md) · [API 參考](../../docs/zh/isasim/reference.md) · [組譯器](../../docs/zh/isasim/assembler.md) · [使用情境](../../docs/zh/isasim/scenarios.md) · [架構](../../docs/zh/isasim/architecture.md)
- [Visual Web Debugger (`isasim_gui`)](../../docs/en/app/isasim_gui/README.md)
