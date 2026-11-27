# `rpkbin.isasim` Scenarios

[繁體中文](../../zh/isasim/scenarios.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [Assembler](assembler.md) · [API Reference](reference.md) · [Architecture](architecture.md)

Four self-contained scenarios covering deterministic trace capture, multi-file source debugging, watchpoints with `mem_alias`, and memory-mapped `Device` callbacks with `Snapshot` replay.

## Scenario 1: Capture Deterministic Text and JSONL Traces

Define a small 5-bit MCU (`trace_demo`), run its four-cell program once, then reset and stream a JSONL trace file inside a `Simulator` context manager so the trace stream closes automatically on exit.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from rpkbin.isasim import MCU, Simulator, helpers

mcu = MCU("trace_demo")
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

sim = Simulator(mcu)
sim.load_cells("prog", {0: 13, 1: 11, 2: 10, 3: 16})
first = sim.run(max_steps=100)
print("first:", first.stop_reason, sim.read("value"), "recent:", len(sim.recent_events(10)))

sim.reset()
with TemporaryDirectory() as folder:
    trace_path = Path(folder) / "trace.jsonl"
    with sim:
        sim.enable_trace(trace_path, fmt="jsonl")
        second = sim.run(max_steps=100)
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    print("second:", second.stop_reason, second.cycles, "lines:", len(lines))
```
```text
first: halted 2 recent: 5
second: halted 7 lines: 5
```

Each run emits four instruction events (`PLUS`, `PLUS`, `PLUS`, `END`) and one `"stop"` event. Setting `trace_reads=True` on `Simulator` adds semantic data cell reads (`mem[...]`) without recording instruction fetches.

## Scenario 2: Assemble with Includes and Stop at a Source Line

Assemble a program that pulls constants from an included file, load it into `Simulator`, set a `:line` breakpoint on the main file, and inspect `source_context()`.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from rpkbin.isasim import MCU, Simulator, assemble, helpers

mcu = MCU("src_demo")
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

with TemporaryDirectory() as folder:
    root = Path(folder)
    (root / "const.inc").write_text("STEP = 11\n", encoding="utf-8")
    main_asm = root / "main.asm"
    main_asm.write_text('.include "const.inc"\nPLUS 13\nPLUS STEP\nEND\n', encoding="utf-8")

    image = assemble(mcu, main_asm.read_text(encoding="utf-8"), filename=str(main_asm))
    with Simulator(mcu) as sim:
        sim.load_assembly(image)
        sim.add_breakpoint(":3")
        result = sim.run(max_steps=100)
        current_line = next(row for row in sim.source_context() if row[2])
        print(result.stop_reason, result.pc, sim.read("value"))
        print(current_line[0], current_line[1], current_line[2])
```
```text
breakpoint 1 13
3 PLUS STEP True
```

`:3` resolves to line 3 of `main.asm`, leaving `pc=1` (right before executing `PLUS STEP`) after `PLUS 13` updated `value` to `13`.

## Scenario 3: Watchpoints, Register Fields, and `mem_alias`

Register field watchpoints detect writes overlapping any watched bit range (`status.high` covers bits `7..4` even when an instruction writes the full `status` register):

```python
from rpkbin.isasim import MCU, Simulator

mcu = MCU("watch_demo")
mcu.space("prog", cell_bits=8, size=4, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.reg("pc", bits=2, reset=0, is_pc=True)
mcu.reg("status", bits=8, reset=0, fields={"high": (7, 4), "low": (3, 0)})
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00000000", syntax="SET", cycles=1)
def set_status(st):
    st.reg["status"] = 0xA3

sim = Simulator(mcu)
sim.load_cells("prog", {0: 0})
sim.add_watchpoint("status.high", kind="w")
result = sim.run(max_steps=10)
print(result.stop_reason, result.detail["old"], result.detail["new"])
```
```text
watchpoint 0 10
```

You can also declare multi-cell `mem_alias` windows and single-bit flag aliases on the `MCU`, add dynamic simulator aliases at runtime, attach watchpoint conditions, and reset dynamic aliases with `clear_mem_aliases()`:

```python
from rpkbin.isasim import MCU, Simulator

mcu = MCU("alias_watch_demo")
mcu.space("prog", cell_bits=8, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=16, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("status", bits=8, reset=0, fields={"high": (7, 4), "low": (3, 0)})
mcu.mem_alias("total16", "ram[0x00:0x02]", endian="little", bit_order="msb_first")
mcu.mem_alias("ready_bit", "ram[0x02][0]")
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00010000", syntax="SET_STATUS", cycles=1)
def set_status(st):
    st.reg["status"] = 0xA3

@mcu.instr("00100000", syntax="WRITE_PAIR", cycles=2)
def write_pair(st):
    st.mem["ram"][0] = 0x34
    st.mem["ram"][1] = 0x12

@mcu.instr("00110000", syntax="HALT", cycles=1)
def halt_op(st):
    st.halt("done")

with Simulator(mcu) as sim:
    sim.load_cells("prog", {0: 0x10, 1: 0x20, 2: 0x30})
    sim.mem_alias("upper_byte", "ram[0x01][7:0]", bit_order="msb_first")
    sim.poke("ready_bit", 1)

    sim.add_watchpoint("status.high", kind="w")
    first = sim.run(max_steps=10)
    print("wp1:", first.stop_reason, first.detail["target"], first.detail["old"], first.detail["new"])

    sim.add_watchpoint("total16", kind="w", condition="m.total16 == 0x1234")
    second = sim.run(max_steps=10)
    print("wp2:", second.stop_reason, second.detail["target"], hex(second.detail["old"]), hex(second.detail["new"]))
    print("ready_bit:", sim.read("ready_bit"), "upper_byte:", hex(sim.read("upper_byte")))

    sim.clear_mem_aliases()
    print("remaining_aliases:", list(sim.mem_aliases))
```
```text
wp1: watchpoint status.high 0 10
wp2: watchpoint total16 0x0 0x1234
ready_bit: 1 upper_byte: 0x12
remaining_aliases: ['total16', 'ready_bit']
```

`clear_mem_aliases()` removes `upper_byte` registered on `Simulator` while retaining `total16` and `ready_bit` declared on `MCU`.

## Scenario 4: Memory-Mapped `Device` with Context, Stimuli, and `Snapshot` Replay

Attach a `Device` subclass whose `read` and `write` callbacks opt into `ctx` (`ctx.reg` and `ctx.add_cycles`), schedule an `at_pc` stimulus, checkpoint with `snapshot()`, and verify deterministic replay via `restore()`.

```python
from rpkbin.isasim import Device, MCU, Simulator, assemble

class TimerDevice(Device):
    time_dependent = True

    def __init__(self):
        self.count = 0

    def read(self, addr, now, ctx):
        ctx.add_cycles(1)
        return (self.count + ctx.reg["bias"]) & 0xFF

    def write(self, addr, value, now, ctx):
        self.count = value
        ctx.reg["acc"] = value

    def tick(self, cycles, now):
        self.count = (self.count + cycles) & 0xFF

    def reset(self):
        self.count = 0

    def get_state(self):
        return {"count": self.count}

    def set_state(self, state):
        self.count = state["count"]

mcu = MCU("dev_replay")
mcu.space("prog", cell_bits=8, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("mmio", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0, role="inout")
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("acc", bits=8, reset=0)
mcu.reg("bias", bits=8, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00010000", syntax="WRDEV", cycles=1)
def wrdev(st):
    st.mem["mmio"][0] = 10

@mcu.instr("00100000", syntax="RDDEV", cycles=1)
def rddev(st):
    st.reg["acc"] = st.mem["mmio"][0]

@mcu.instr("00110000", syntax="END", cycles=1)
def dend(st):
    st.halt("done")

with Simulator(mcu) as sim:
    sim.attach(TimerDevice(), space="mmio", lo=0, hi=1)
    sim.load_assembly(assemble(mcu, "WRDEV\nRDDEV\nEND\n", filename="dev.asm"))
    sim.at_pc(1).set("bias", 5)

    sim.step()
    snap = sim.snapshot()
    first = sim.run(max_steps=10)
    first_acc = sim.read("acc")

    sim.restore(snap)
    second = sim.run(max_steps=10)
    assert (first.stop_reason, first.cycles, first_acc) == (second.stop_reason, second.cycles, sim.read("acc"))
    print(second.stop_reason, second.cycles, sim.read("acc"), repr(snap))
```
```text
halted 3 16 Snapshot(mcu='dev_replay', cycle=1, steps=1)
```

During `WRDEV`, `write` sets `count = 10`, and post-commit `tick(1)` increments `count` to `11`. Before `RDDEV`, the `at_pc(1)` stimulus sets `bias = 5`, then `read` adds 1 cycle (`ctx.add_cycles(1)`) and returns `11 + 5 = 16`. `Snapshot` captures both simulator registers/stimulus runtime state and `TimerDevice.get_state()`, allowing identical replay.

## See Also

- [Quickstart](README.md)
- [Assembler](assembler.md)
- [API Reference](reference.md)
- [Architecture](architecture.md)
