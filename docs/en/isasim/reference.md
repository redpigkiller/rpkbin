# `rpkbin.isasim` API Reference

[繁體中文](../../zh/isasim/reference.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [Scenarios](scenarios.md) · [Assembler](assembler.md) · [Architecture](architecture.md)

Complete technical specification for `rpkbin.isasim`: `MCU`, `Assembly`, `Simulator`, `Device`, `Snapshot`, `helpers`, CLI flags, REPL commands, and exceptions.

## Public Package API

`from rpkbin.isasim import ...` exports these **31** names (`__all__`):

```python
from rpkbin.isasim import (
    MCU,
    Assembly,
    AssemblyError,
    DebugError,
    Device,
    DeviceError,
    Event,
    FieldInfo,
    IsaSimError,
    LoadError,
    MCUDefinitionError,
    MCUDescription,
    MemAliasInfo,
    Operand,
    ReadRecord,
    RegisterInfo,
    RunResult,
    Simulator,
    Snapshot,
    SnapshotError,
    SourceRecord,
    SpaceInfo,
    StateAccessError,
    StimulusError,
    StuckReport,
    TargetError,
    UsageError,
    WriteRecord,
    assemble,
    helpers,
    load_mcu,
)
```

`rpk-isasim` and `python -m rpkbin.isasim` invoke the same CLI.

## `MCU` and `load_mcu`

Declarative machine description holding spaces, registers, aliases, memory aliases, IRQ lines, instruction patterns, and stop policies.

### Creation

```python
mcu = MCU(name: str)
mcu = load_mcu(path: str | os.PathLike[str])
```

- `MCU(name)` creates an empty machine model (`name` must be a non-empty `str`).
- `load_mcu(path)` executes a Python file defining a module-level `mcu` `MCU` instance, validates it, and hashes the description file bytes (`_description_sha256`). Only load trusted `.py` files.
- `repr(mcu)` returns `MCU('<name>', spaces=<n>, registers=<n>, instructions=<n>)`.

### Properties

| Property | Type | Meaning |
| :--- | :--- | :--- |
| `aliases` | `Mapping[str, str]` | Read-only proxy of declared register/field aliases in declaration order. |
| `mem_aliases` | `Mapping[str, MemAliasInfo]` | Read-only proxy of declared memory aliases in declaration order. |

### Object Functions

| Method | Purpose |
| :--- | :--- |
| `space(name, *, cell_bits, size, access, reset_state, init, is_program=False, role=None, regions=None)` | Declare a memory space (`access`: `"ro"`/`"rw"`; `reset_state`: `"initialized"`/`"uninitialized"`; `role`: `"internal"`/`"input"`/`"output"`/`"inout"`). |
| `reg(name, *, bits, reset, is_pc=False, fields=None, role=None)` | Declare a register (`role`: `"internal"`/`"input"`/`"output"`/`"inout"`/`"flag"`). `fields` maps names to `bit` or `(hi, lo)`. |
| `alias(name, target)` | Bind `name` (not starting with `"_"`) to an existing non-PC register or `reg.field`. |
| `mem_alias(name, target, *, endian=None, bit_order=None)` | Declare a named memory alias for a cell, bit, bit-slice, or half-open cell range. |
| `irq_line(name)` | Declare an external interrupt line. |
| `instr(pattern, *, syntax, cycles, name=None, operands=None, overrides=None, kind=None)` | Decorator registering an instruction encoding (`kind`: `None`, `"call"`, or `"return"`). |
| `interrupt_check(fn)` | Decorator registering the single step-boundary checker `fn(st) -> (event_name, cycles) \| None`. |
| `on_undefined_opcode(action, *, nop_cells=None, nop_cycles=None)` | Policy for undefined opcodes: `"stop"` or `"nop"` (requires `nop_cells >= 1`, `nop_cycles >= 0`). |
| `on_out_of_range(action, *, read_value=None)` | Policy for out-of-range memory accesses: `"stop"` or `"ignore"` (requires integer `read_value`). |
| `on_write_to_readonly(action)` | Policy for semantic writes to read-only cells/regions: `"stop"` or `"ignore"`. |
| `on_uninitialized_read(action)` | Policy for semantic reads of uninitialized cells: `"stop"`, `"warn"`, or `"allow"`. |
| `describe()` | Return an immutable `MCUDescription` snapshot of the validated MCU. |

### Details: Policies, Regions, Aliases, `mem_alias`, and `MCUDescription`

- **Validation**: Every `MCU` requires at least one instruction, all four `on_*` policies, and at most one `interrupt_check`. Exactly one space must set `is_program=True` and one register must set `is_pc=True`. `describe()` raises `MCUDefinitionError` if the current declaration has not passed validation.
- **Regions**: `regions=[(lo, hi, access), ...]` overrides `space` access on non-overlapping half-open cell ranges (`0 <= lo < hi <= size`, `access` in `("ro", "rw")`). Semantic writes via `State` follow effective region policy; loaders, `load_cells()`, `poke()`, and `Device` cells bypass it.
- **Register aliases (`alias`)**: Aliases share one global namespace with spaces, registers, IRQ lines, and `mem_aliases`, and cannot equal `"irq"` or start with `"_"`. A whole-register alias accepts `.field` suffixes; a field alias resolves directly to that field.
- **Memory aliases (`mem_alias`)**:
  - `target` accepts `space[addr]`, `space[addr][bit]`, `space[addr][hi:lo]`, or half-open `space[lo:hi]` (decimal or `0x` hex indices, `0 <= lo < hi <= size`, `0 <= bit_lo <= bit_hi < cell_bits`).
  - `endian` accepts `None`, `"big"`, or `"little"`, and is required (`"big"` or `"little"`) for multi-cell targets (`hi - lo > 1`).
  - `bit_order` accepts `None`, `"msb_first"`, or `"lsb_first"`, and is required when `bits > 1`.
  - `MCU.mem_alias()` raises `MCUDefinitionError` on invalid syntax, bounds, or name collisions, and stores a `MemAliasInfo` in `MCU.mem_aliases`.
- **Overrides**: `overrides` names one or more less specific instructions of the same length whose fixed bits are a strict compatible subset. Overrides are transitive.
- **Inspection Dataclasses** (all frozen, slotted):
  - `MCUDescription(spaces, registers, irq_lines, aliases, has_instruction_kinds)` (`mem_aliases` is exposed via `MCU.mem_aliases` and `Simulator.mem_aliases`, not `MCUDescription`).
  - `SpaceInfo(name, cell_bits, size, access, regions, is_program, role, reset_state)`
  - `RegisterInfo(name, bits, reset, is_pc, role, fields)`
  - `FieldInfo(name, hi, lo)` (single-bit fields have `hi == lo`)
  - `MemAliasInfo(name, target, space, lo, hi, bit_hi, bit_lo, endian, bit_order, bits)`

## Assembler and Source Navigation

### Syntax

```python
op = Operand(signed=False, relative=None, names=None, range=None)
image = assemble(
    mcu,
    source,
    *,
    filename="<string>",
    include_paths=(),
    defines=None,
    endian=None,
    case_sensitive_names=True,
)
image = Assembly(mcu, source, *, filename="<string>", include_paths=(), defines=None, endian=None, case_sensitive_names=True)
image = Assembly.load(mcu, path)
```

### Description

`assemble()` returns an `Assembly` without executing instruction callbacks. Each placeholder letter in `MCU.instr(..., syntax=...)` must appear once with explicit non-whitespace delimiters between adjacent operands (`OP {a}, {b}` or `OP r{a}, r{b}`). See the [Assembler guide](assembler.md) for directives, preprocessing, and bundle validation.

### `Assembly` Properties and Object Functions

| Member | Type / Output | Description |
| :--- | :--- | :--- |
| `cells` | `dict[int, int]` | Independent copy of emitted address-to-cell pairs. |
| `symbols` | `dict[str, int]` | Independent copy of label addresses. |
| `constants` | `dict[str, int]` | Independent copy of resolved constants and `defines`. |
| `sources` | `dict[str, str]` | Independent copy of captured source files keyed by source key. |
| `main_key` | `str` | Read-only key of the main file inside `sources`. |
| `records()` | `tuple[SourceRecord, ...]` | Emitted `SourceRecord(addr, file, line, cells, is_instruction)` items in listing order. |
| `listing()` | `str` | Formatted address, cell hex, `file:line`, and source text. |
| `save(path)` | `None` | Write UTF-8 `.isa.json` bundle (LF newlines). |
| `Assembly.load(mcu, path)` | `Assembly` | Validate hashes, ISA fingerprint, and rebuilt cells from a bundle. |
| `repr(image)` | `str` | `Assembly('<filename>', cells=<n>, symbols=<n>)`. |

## `Simulator`

Transactional simulation engine owning all mutable machine state.

### Creation

```python
sim = Simulator(mcu: MCU, *, trace_reads: bool = False, history: int = 1000)
with Simulator(mcu, *, trace_reads=False, history=1000) as sim:
    ...
```

- `history >= 1` controls how many `Event` entries `recent_events(n)` retains (`UsageError` if `< 1`).
- Context manager (`__enter__` / `__exit__`): `__enter__` returns `sim`; `__exit__` calls `sim.disable_trace()` so open trace files close automatically.
- `repr(sim)` returns `Simulator('<mcu_name>', pc=0x<hex>, cycle=<n>, steps=<n>)` with `pc` zero-padded to `(pc_bits + 3) // 4` hex digits.

### Properties

| Property | Type | Meaning |
| :--- | :--- | :--- |
| `state` | `State` | Read-only view of registers (`reg`), memory (`mem`), IRQ lines (`irq`), `pc`, `next_pc`, and `cycle`. |
| `cycle` | `int` | Cumulative elapsed cycles since creation or `reset()`. |
| `steps_total` | `int` | Total committed steps (instructions plus interrupt entries). |
| `instructions_total` | `int` | Total committed instruction steps. |
| `mem_aliases` | `Mapping[str, MemAliasInfo]` | Read-only proxy of active memory aliases (MCU declarations plus simulator registrations). |

### Object Functions

| Method | Purpose |
| :--- | :--- |
| `describe() -> MCUDescription` | Return validated `MCUDescription` snapshot from the bound MCU. |
| `reset() -> None` | Reset registers, memory, IRQ lines, cycle/step counters, devices, and stimulus positions while keeping loaded images, breakpoints, subscribers, and `mem_aliases`. |
| `load_cells(space, cells) -> None` | Load `{addr: value}` cells into `space` and mark them initialized (clears assembly mapping if loading program space). |
| `load_hex(path, *, space=None, order=None) -> None` | Load Intel HEX into `space` (default program space; `order` `"big"`/`"little"` required when `cell_bits > 8`). |
| `load_bin(path, *, base, space=None, order=None) -> None` | Load raw binary starting at nonnegative cell address `base`. |
| `load_symbols(path) -> None` | Load legacy UTF-8 JSON `{"symbols": ..., "lines": ...}` (clears assembly source association). |
| `load_assembly(image) -> None` | Validate and atomically install an `Assembly` and its source mapping (does not reset registers or cycles). |
| `initialized_ranges(space) -> tuple[tuple[int, int], ...]` | Return ascending merged half-open `[lo, hi)` ranges of initialized, non-device cells (`StateAccessError` on unknown space). |
| `read(target) -> int` | Peek a register, `reg.field`, `space[addr]`, `irq.line`, or `mem_alias` without side effects (`StateAccessError` on device cells). |
| `poke(target, value) -> None` | Mutate a register, `reg.field`, `space[addr]`, `irq.line`, or `mem_alias` from outside and emit an `"external"` event (`StimulusError` on device cells). |
| `mem_alias(name, target, *, endian=None, bit_order=None) -> None` | Register a dynamic memory alias on this simulator (`TargetError` on invalid spec, collision, or multi-cell device overlap). |
| `clear_mem_aliases() -> None` | Remove simulator-registered memory aliases while preserving MCU-declared ones. |
| `step(n=1) -> RunResult` | Execute up to `n >= 1` steps without checking breakpoints. |
| `run(*, max_steps, max_cycles=None, stuck_window=1000, raise_exceptions=False, stuck_ignore=()) -> RunResult` | Run up to `max_steps >= 1` steps with breakpoint, watchpoint, cycle limit, and stuck detection. |
| `disassemble(where, count=1) -> list[tuple[int, str, int]]` | Disassemble up to `count >= 1` instructions from `where` (address, symbol, or `file:line`), returning `(addr, text, length)` tuples. |
| `instruction_kind(where) -> str \| None` | Return declared `kind` (`"call"`, `"return"`, or `None`) for the instruction at `where` (`source_locations=False`). |
| `subscribe(fn) -> None` | Register `fn(event: Event)` callback notified on emitted events. |
| `enable_trace(path, *, fmt="text") -> None` | Open trace file (`fmt`: `"text"` or `"jsonl"`). |
| `disable_trace() -> None` | Close any open trace file. |
| `recent_events(n) -> list[Event]` | Return up to the last `n >= 0` buffered events. |
| `add_breakpoint(where, *, condition=None, temporary=False) -> int` | Register a breakpoint at an address, symbol, or `file:line` / `:line` source location. |
| `add_watchpoint(target, *, kind, condition=None) -> int` | Watch a register, field, `irq.line`, `space[addr]`, `space[lo:hi]`, or `mem_alias` (`kind`: `"r"`, `"w"`, `"rw"`). |
| `delete(which) -> None` | Delete breakpoint/watchpoint by `int` ID or `"all"`. |
| `list_breakpoints() -> list[str]` | Return formatted descriptions of active breakpoints and watchpoints. |
| `next_breakpoint() -> int \| None` | Return first breakpoint ID matching current PC/cycle/state at `S0`, or `None` (does not execute or remove temporary breakpoints). |
| `evaluate(expr) -> int` | Evaluate a read-only debugger condition expression against current state. |
| `add_display(expr) -> int` | Register a persistent REPL display expression and return its ID. |
| `remove_display(display_id) -> None` | Remove a registered display expression. |
| `source_context(where=None, *, context=2) -> list[tuple[int, str, bool]]` | Return `(line_no, text, is_current)` rows around `where` (default current PC); `DebugError` if assembly mapping is missing or stale. |
| `source_line(addr) -> tuple[str, int] \| None` | Return `(file, line)` mapped to `addr`, or `None`. |
| `label_at(addr) -> tuple[str, int] \| None` | Return nearest symbol at or before `addr` as `(name, offset)`, or `None`. |
| `read_seq(target, values) -> None` | Supply successive values to semantic reads of one non-program cell (peeks do not advance; `reset()` resets position). |
| `at_cycle(n) -> Trigger` | Schedule an action once `cycle >= n`. |
| `at_pc(where, *, times=1) -> Trigger` | Schedule an action when PC reaches `where` (`times=None` repeats indefinitely). |
| `on_read(target, *, nth) -> Trigger` | Schedule an action on the `nth >= 1` semantic read of `space[addr]`. |
| `on_write(target, *, nth) -> Trigger` | Schedule an action on the `nth >= 1` semantic write of `space[addr]`. |
| `list_stimuli() -> list[str]` | List formatted signatures of active stimuli and `read_seq` bindings. |
| `clear_stimuli() -> None` | Remove all registered stimuli and `read_seq` sequences. |
| `attach(device, *, space, lo, hi) -> None` | Attach a `Device` (or object with `read`/`write`) to half-open non-program range `space[lo:hi]`. |
| `snapshot() -> Snapshot` | Capture an immutable checkpoint of simulator, device, and stimulus runtime state. |
| `restore(snap) -> None` | Restore state from a structurally compatible `Snapshot`. |

### Details: `State`, Condition Expressions, `mem_alias`, and Trace Events

- **Instruction `State` boundary**: Callbacks receive a writable `State` with `st.reg[name]`, `st.mem[space][addr]`, `st.mem[space].read_word(addr, n, order)` / `write_word(addr, n, value, order)` (`n >= 1`, `order` in `("big", "little")`), `st.irq[line]`, `st.pc`, `st.next_pc`, `st.cycle`, `st.add_cycles(n)`, `st.halt(reason)`, and `st.fault(code, message="")`. Writes are journaled and rolled back if the semantic callback or a `Device` `read`/`write` raises.
- **Run stop reasons**: `RunResult.stop_reason` is one of `"step_done"`, `"halted"`, `"breakpoint"`, `"watchpoint"`, `"max_steps"`, `"max_cycles"`, `"stuck"`, `"fault"`, `"interrupted"`, `"python_error"`, `"undefined_opcode"`, `"out_of_range"`, `"readonly_write"`, or `"uninitialized_read"`.
- **Condition language**: Restricted integer/boolean AST with no Python builtins, `eval()`, floats, or `/`.
  - Targets: `pc`, `cycle`, `r.reg` (with `.field`), `m.space[addr]`, `m.mem_alias`, `i.line`, or subscript forms `r["name"]`, `m["space"][addr]`, `m["mem_alias"]`, `i["line"]`.
  - Operators: unary `+`, `-`, `~`, `not`; binary `+`, `-`, `*`, `//`, `%`, `<<`, `>>`, `&`, `^`, `|`, `**`; chained comparisons `==`, `!=`, `<`, `<=`, `>`, `>=` with trailing `in` / `not in` on tuple/set literals; ternary `a if cond else b`; short-circuit `and` / `or` returning the selected operand.
  - Allowed calls: `to_signed`, `to_unsigned`, `sign_extend`, `bit`, `parity`, and `.bit_count()` / `.bit_length()` on integers.
  - Safety bounds: max AST depth `256` and max bit width `65536` bits. Syntax/AST/target errors raise `DebugError` at install time; arithmetic errors, bound overflows, device peeks, and cleared `mem_alias` names raise `DebugError` at evaluation time.
- **Wide-integer JSONL formatting**: Integers with `abs(value) < 10**640` serialize as JSON integers; larger integers serialize losslessly as `"0x..."` (zero-padded when bit width is known) or `"-0x..."` strings.
- **Record Dataclasses**:
  - `WriteRecord(kind, name, addr, old, new, bits, is_field)`
  - `ReadRecord(space, addr, value, bits)`
  - `Event(kind, seq, cycle, pc, cells, cell_bits, pc_bits, name, text, writes, reads, consumed, code, detail)`
  - `StuckReport(pcs, reads)`
  - `RunResult(stop_reason, steps, instructions, cycles, cycle, pc, detail, exception, stuck_report)`

### Example: `mem_alias`, Context Manager, and `__repr__`

```python
from rpkbin.isasim import MCU, Simulator, assemble

mcu = MCU("alias_demo")
mcu.space("prog", cell_bits=8, size=16, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=16, access="rw", reset_state="uninitialized", init=0)
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("r0", bits=16, reset=0)
mcu.mem_alias("word16", "ram[0x00:0x02]", endian="little", bit_order="msb_first")
mcu.mem_alias("flag0", "ram[0x02][0]")
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00000001", syntax="HALT", cycles=1)
def op_halt(st):
    st.halt("done")

img = assemble(mcu, "HALT\n", filename="alias.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(img)
    sim.load_cells("ram", {0: 0x34, 1: 0x12, 2: 0x01})
    sim.mem_alias("high_byte", "ram[0x01][7:0]", bit_order="msb_first")
    print("word16:", hex(sim.read("word16")), "flag0:", sim.read("flag0"), "high_byte:", hex(sim.read("high_byte")))
    print("ranges:", sim.initialized_ranges("ram"))
    print("sim_aliases:", list(sim.mem_aliases))
    sim.clear_mem_aliases()
    print("after_clear:", list(sim.mem_aliases))
    snap = sim.snapshot()
    print(repr(mcu))
    print(repr(img))
    print(repr(sim))
    print(repr(snap))
```

Output:
```text
word16: 0x1234 flag0: 1 high_byte: 0x12
ranges: ((0, 3),)
sim_aliases: ['word16', 'flag0', 'high_byte']
after_clear: ['word16', 'flag0']
MCU('alias_demo', spaces=2, registers=2, instructions=1)
Assembly('alias.asm', cells=1, symbols=0)
Simulator('alias_demo', pc=0x0, cycle=0, steps=0)
Snapshot(mcu='alias_demo', cycle=0, steps=0)
```

## `Device` and `Trigger`

External peripheral interface and boundary-scheduled stimuli.

### `Device` Class Structure

Subclass `Device` (or supply any object implementing `read` and `write`) and attach it with `Simulator.attach(device, *, space, lo, hi)`.

#### Class Attribute

| Attribute | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `time_dependent` | `bool` | `False` | When `True`, disables stuck detection while attached. |

#### Object Functions

| Method | Signature | Description |
| :--- | :--- | :--- |
| `read` | `read(self, addr: int, now: int) -> int` (or `(self, addr, now, ctx)`) | Required. Return cell value for `addr` at cumulative cycle `now` (masked to `cell_bits`). Default raises `NotImplementedError`. |
| `write` | `write(self, addr: int, value: int, now: int) -> None` (or `(self, addr, value, now, ctx)`) | Required. Handle write of masked `value` to `addr` at cycle `now`. Default raises `NotImplementedError`. |
| `tick` | `tick(self, cycles: int, now: int) -> None` | Optional post-commit hook called with consumed step cycles and updated `now`. Default is a no-op. |
| `reset` | `reset(self) -> None` | Optional hook invoked by `Simulator.reset()`. Default is a no-op. |
| `get_state` | `get_state(self) -> dict[str, object]` | Return JSON-compatible state for `Simulator.snapshot()`. Default raises `NotImplementedError` (`SnapshotError`). |
| `set_state` | `set_state(self, state: dict[str, object]) -> None` | Restore state during `Simulator.restore()`. Default raises `NotImplementedError` (`SnapshotError`). |

- **Optional `ctx` parameter**: When the 3rd parameter after `self` on `read` or 4th parameter after `self` on `write` is named `ctx`, `Simulator` passes a callback context exposing `ctx.reg[name]` (register, alias, or field) and `ctx.add_cycles(n)` (`n >= 0`). Context writes join the step rollback journal. During `interrupt_check`, `ctx.reg` writes and `ctx.add_cycles()` raise `StateAccessError`. Writing the PC through `ctx.reg` is forbidden, and both `ctx` and retained `ctx.reg` references expire when the callback returns.
- **Overlaps**: Devices cannot attach to program spaces or overlap other devices, `read_seq` targets, or multi-cell `mem_alias` ranges (`DeviceError`).

### `Trigger` Methods

Returned by `Simulator.at_cycle()`, `Simulator.at_pc()`, `Simulator.on_read()`, and `Simulator.on_write()`. Each `Trigger` binds one terminal action:

| Method | Purpose |
| :--- | :--- |
| `delay(cycles: int) -> Trigger` | Delay action delivery by `cycles >= 0` cycles. |
| `set(target: str, value: int) -> None` | Schedule an external write to a register, field, `space[addr]`, or `mem_alias` (`StimulusError` on device targets). |
| `irq(line: str, level: int) -> None` | Schedule an IRQ line update (`0` or `1`; `line` without `irq.` prefix). |
| `call(fn: Callable[[Simulator], None]) -> None` | Schedule a Python callback receiving the `Simulator`. |

- **Stuck detection**: `stuck_window > 0` stops a `run()` when a repeated PC pattern runs for `stuck_window` steps without register/memory/IRQ/dynamic-read activity. Pending timed stimuli and `time_dependent = True` devices suppress the check; `stuck_ignore` masks specified registers or fields.

### Example: `Device` with `ctx` and `Snapshot`

```python
from rpkbin.isasim import Device, MCU, Simulator, assemble

class CounterDev(Device):
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

dmcu = MCU("dev_demo")
dmcu.space("prog", cell_bits=8, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
dmcu.space("mmio", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0, role="inout")
dmcu.reg("pc", bits=4, reset=0, is_pc=True)
dmcu.reg("acc", bits=8, reset=0)
dmcu.reg("bias", bits=8, reset=5)
dmcu.on_undefined_opcode("stop")
dmcu.on_out_of_range("stop")
dmcu.on_write_to_readonly("stop")
dmcu.on_uninitialized_read("stop")

@dmcu.instr("00010000", syntax="WRDEV", cycles=1)
def wrdev(st):
    st.mem["mmio"][0] = 10

@dmcu.instr("00100000", syntax="RDDEV", cycles=1)
def rddev(st):
    st.reg["acc"] = st.mem["mmio"][0]

@dmcu.instr("00110000", syntax="END", cycles=1)
def dend(st):
    st.halt("done")

with Simulator(dmcu) as dsim:
    dsim.attach(CounterDev(), space="mmio", lo=0, hi=1)
    dsim.load_assembly(assemble(dmcu, "WRDEV\nRDDEV\nEND\n", filename="dev.asm"))
    dsim.step()
    snap = dsim.snapshot()
    res1 = dsim.run(max_steps=10)
    val1 = dsim.read("acc")
    dsim.restore(snap)
    res2 = dsim.run(max_steps=10)
    print(res1.stop_reason, res2.cycles, val1, dsim.read("acc") == val1)
```

Output:
```text
halted 3 16 True
```

## `Snapshot`

Immutable checkpoint capturing simulator state, device states, and stimulus runtime state.

### Creation

```python
snap = sim.snapshot()
snap = Snapshot.load(path: str | os.PathLike[str])
```

### Object Functions

| Method | Purpose |
| :--- | :--- |
| `save(path)` | Write checkpoint JSON (`mcu_name`, `signature`, `state`) encoding oversized ints (`abs >= 10**640`) as hex strings (`SnapshotError` on I/O error). |
| `Snapshot.load(path)` | Load and validate JSON checkpoint, decoding wide hex strings back to `int`. |
| `sim.restore(snap)` | Verify structural compatibility (`spaces`, `registers`, `irq_lines`, `devices`, `stimuli`, and non-empty `aliases`, `mem_aliases`, `regions`) and restore state (`SnapshotError` on mismatch). |
| `repr(snap)` | Return `Snapshot(mcu='<name>', cycle=<n>, steps=<n>)`. |

Attempting to assign attributes on a `Snapshot` raises `AttributeError`. Current breakpoints, displays, and event subscribers stay active across `restore()`.

## `rpkbin.isasim.helpers`

Width-explicit integer arithmetic and bit manipulation helpers for instruction models (`from rpkbin.isasim import helpers`). All functions reject `bool` and invalid widths or bit counts with `UsageError`.

### Result Dataclasses

| Class | Fields | Description |
| :--- | :--- | :--- |
| `AddResult` | `result: int`, `carry: int`, `overflow: int` | Frozen dataclass returned by `add_with_carry`. `carry` is unsigned carry-out (`0` or `1`); `overflow` is signed two's-complement overflow (`0` or `1`). |
| `SubResult` | `result: int`, `borrow: int`, `overflow: int` | Frozen dataclass returned by `sub_with_borrow`. `borrow` is `1` when `a < b + borrow_in`, else `0`; `overflow` is signed two's-complement overflow (`0` or `1`). |

### Syntax

```python
helpers.to_unsigned(value: int, bits: int) -> int
helpers.to_signed(value: int, bits: int) -> int
helpers.sign_extend(value: int, from_bits: int, to_bits: int) -> int
helpers.bit(value: int, n: int) -> int
helpers.set_bit(value: int, n: int, b: int) -> int
helpers.parity(value: int) -> int
helpers.rotate_left(value: int, n: int, bits: int) -> int
helpers.rotate_right(value: int, n: int, bits: int) -> int
helpers.add_with_carry(a: int, b: int, carry_in: int, bits: int) -> AddResult
helpers.sub_with_borrow(a: int, b: int, borrow_in: int, bits: int) -> SubResult
helpers.carry_out_of_bit(a: int, b: int, carry_in: int, bit_index: int) -> int
```

### Description and Arguments

| Function | Arguments and Constraints | Output |
| :--- | :--- | :--- |
| `to_unsigned(value, bits)` | `value: int`, `bits >= 1` | `value & ((1 << bits) - 1)`. |
| `to_signed(value, bits)` | `value: int`, `bits >= 1` | Two's-complement signed `int` in `-(1 << (bits - 1)) .. (1 << (bits - 1)) - 1`. |
| `sign_extend(value, from_bits, to_bits)` | `value: int`, `from_bits >= 1`, `to_bits >= from_bits` | Unsigned `to_bits`-bit integer sign-extended from `from_bits`. |
| `bit(value, n)` | `value: int`, `n >= 0` | Single bit `(value >> n) & 1`. |
| `set_bit(value, n, b)` | `value: int`, `n >= 0`, `b in (0, 1)` | Integer with bit `n` replaced by `b`. |
| `parity(value)` | `value >= 0` | `1` if `value` has an odd number of `1` bits, else `0`. |
| `rotate_left(value, n, bits)` | `value: int`, `n >= 0`, `bits >= 1` | `value` masked to `bits` and rotated left by `n % bits`. |
| `rotate_right(value, n, bits)` | `value: int`, `n >= 0`, `bits >= 1` | `value` masked to `bits` and rotated right by `n % bits`. |
| `add_with_carry(a, b, carry_in, bits)` | `a: int`, `b: int`, `carry_in in (0, 1)`, `bits >= 1` | `AddResult(result, carry, overflow)` over `bits`-masked `a` and `b`. |
| `sub_with_borrow(a, b, borrow_in, bits)` | `a: int`, `b: int`, `borrow_in in (0, 1)`, `bits >= 1` | `SubResult(result, borrow, overflow)` for `(a - b - borrow_in)` over `bits`-masked `a` and `b`. |
| `carry_out_of_bit(a, b, carry_in, bit_index)` | `a: int`, `b: int`, `carry_in in (0, 1)`, `bit_index >= 0` | Carry bit out of `bit_index` (`((a & m) + (b & m) + carry_in) >> (bit_index + 1)`). |

### Example: All 11 `helpers` Functions

```python
from rpkbin.isasim import helpers

print("to_unsigned:", hex(helpers.to_unsigned(-5, 8)))
print("to_signed:", helpers.to_signed(0xFB, 8))
print("sign_extend:", hex(helpers.sign_extend(0x8A, 8, 16)))
print("bit/set_bit:", helpers.bit(0b1010, 3), bin(helpers.set_bit(0b1010, 0, 1)))
print("parity:", helpers.parity(0b1011), helpers.parity(0b1001))
print("rot:", hex(helpers.rotate_left(0x81, 1, 8)), hex(helpers.rotate_right(0x81, 1, 8)))
print("add:", helpers.add_with_carry(0x7F, 0x01, 0, 8))
print("sub:", helpers.sub_with_borrow(0x00, 0x01, 0, 8))
print("half_carry:", helpers.carry_out_of_bit(0x0F, 0x01, 0, 3))
```

Output:
```text
to_unsigned: 0xfb
to_signed: -5
sign_extend: 0xff8a
bit/set_bit: 1 0b1011
parity: 1 0
rot: 0x3 0xc0
add: AddResult(result=128, carry=0, overflow=1)
sub: SubResult(result=255, borrow=1, overflow=0)
half_carry: 1
```

## CLI and REPL

### Syntax

```bash
rpk-isasim [-h] [-V] COMMAND ...
rpk-isasim assemble MCU.py SOURCE.asm [-o OUTPUT] [--listing LISTING] [-I DIR]... [-D NAME[=VAL]]... [--endian {little,big}] [--case-insensitive]
rpk-isasim run MCU.py [PROGRAM] --max-steps N [--format {hex,bin,asm,image}] [--space NAME] [--order {big,little}] [--base N] [--symbols FILE] [--script FILE] [--trace-reads] [-I DIR]... [-D NAME[=VAL]]... [--endian {little,big}] [--case-insensitive] [--max-cycles N] [--stuck-window N] [--stuck-ignore NAME]... [--trace FILE] [--trace-format {text,jsonl}]
rpk-isasim repl MCU.py [PROGRAM] [--format {hex,bin,asm,image}] [--space NAME] [--order {big,little}] [--base N] [--symbols FILE] [--script FILE] [--trace-reads] [-I DIR]... [-D NAME[=VAL]]... [--endian {little,big}] [--case-insensitive]
```

```python
from rpkbin.isasim.cli import main
rc = main(argv: list[str] | None = None) -> int
```

### Description

`rpk-isasim`, `python -m rpkbin.isasim`, and `rpkbin.isasim.cli.main(argv)` share one parser and execution path (`main(argv)` expects argument tokens without the program name).

- `assemble`: compiles `SOURCE.asm` against `MCU.py` into `<source_parent>/<source_stem>.isa.json` unless `-o` / `--output` specifies another path. Output and `--listing` paths must differ from `MCU.py`, `SOURCE.asm`, any `.include` file, and each other.
- `run`: executes `PROGRAM` (or script-loaded state) until halt or stop condition and prints `RESULT stop_reason=... steps=... instructions=... cycles=... pc=...`.
- `repl`: starts an interactive line debugger on stdin/stdout after loading `PROGRAM` and running any `--script` `setup(sim)` hook.

Auto-detection checks `.asm` (`asm`) and `.isa.json` (`image`) suffixes; HEX and BIN files require explicit `--format`, and BIN also requires `--base`. ASM and image inputs reject `--space`, `--base`, `--order`, and `--symbols`. `-I`, `-D`, `--endian`, and `--case-insensitive` require ASM input on `run` and `repl`.

### Examples

```bash
python -m rpkbin.isasim --version
rpk-isasim assemble playground/isasim_demo/demo_mcu.py playground/isasim_demo/demo_loop.asm -I inc -D FLAG=1 --endian little --case-insensitive -o build/demo.isa.json --listing build/demo.lst
rpk-isasim run playground/isasim_demo/demo_mcu.py playground/isasim_demo/demo_loop.asm --max-steps 500
```

Output (`--version`):
```text
python -m rpkbin.isasim 0.3.0
```

### Options

#### Top-Level Options

| Flag | Env Var | Default | Description |
| :--- | :--- | :--- | :--- |
| `-h`, `--help` | `-` | `-` | Show help message and exit. |
| `-V`, `--version` | `-` | `-` | Print `<prog> <version>` (for example `rpk-isasim 0.3.0`) and exit. |

#### `assemble`, `run`, and `repl` Options

| Flag | Env Var | Default | Applies To | Description |
| :--- | :--- | :--- | :--- | :--- |
| `-o OUTPUT`, `--output OUTPUT` | `-` | `<source_parent>/<source_stem>.isa.json` | `assemble` | Output `.isa.json` bundle path. |
| `--listing LISTING` | `-` | `None` | `assemble` | Write formatted listing file. |
| `-I DIR`, `--include-dir DIR` | `-` | `[]` | `assemble`, `run`/`repl` (ASM) | Repeatable include search directory. |
| `-D NAME[=VAL]` | `-` | `[]` | `assemble`, `run`/`repl` (ASM) | Repeatable integer constant (`NAME` sets `1`; `VAL` accepts decimal/`0x`/`0b`/`0o` literals such as `-0x2`, not expressions). |
| `--endian {little,big}` | `-` | `None` | `assemble`, `run`/`repl` (ASM) | Byte order for multi-byte `.dw` values on 8-bit program spaces. |
| `--case-insensitive` | `-` | `False` | `assemble`, `run`/`repl` (ASM) | Treat labels, constants, and named operands case-insensitively. |
| `--format {hex,bin,asm,image}` | `-` | auto for `.asm`/`.isa.json` | `run`, `repl` | Explicit program input format. |
| `--space NAME` | `-` | program space | `run`, `repl` | Target memory space for `hex` / `bin` loading. |
| `--order {big,little}` | `-` | `None` | `run`, `repl` | Byte order for multi-byte cells (`cell_bits > 8`). |
| `--base N` | `-` | `None` | `run`, `repl` | Starting cell address (decimal/`0x`/`0b`/`0o`), required with `--format bin`. |
| `--symbols FILE` | `-` | `None` | `run`, `repl` | Path to legacy symbols JSON file. |
| `--script FILE` | `-` | `None` | `run`, `repl` | Python file defining `setup(sim)`. |
| `--trace-reads` | `-` | `False` | `run`, `repl` | Include semantic memory reads in emitted events. |
| `--max-steps N` | `-` | required | `run` | Maximum steps to execute (accepts decimal/`0x`/`0b`/`0o`). |
| `--max-cycles N` | `-` | `None` | `run` | Stop once elapsed cycles reach `N`. |
| `--stuck-window N` | `-` | `1000` | `run` | Quiet steps with repeated PC pattern before stopping as `stuck` (`0` disables). |
| `--stuck-ignore NAME` | `-` | `[]` | `run` | Repeatable register or `reg.field` ignored in stuck detection. |
| `--trace FILE` | `-` | `None` | `run` | Write event trace to `FILE`. |
| `--trace-format {text,jsonl}` | `-` | `text` | `run` | Event trace file format. |

### REPL Commands and Aliases

The REPL supports **25** commands (`24` debugger commands plus `quit`) and **7** aliases (`r -> run`, `continue -> run`, `c -> run`, `s -> step`, `b -> break`, `p -> print`, `q -> quit`):

| Command | Syntax | Description |
| :--- | :--- | :--- |
| `help` | `help [command]` | List all commands and aliases, or show syntax for one command. |
| `run` | `run [max_steps]` | Run until stop condition (default `max_steps=1000000`, `stuck_window=1000`). Aliases: `r`, `continue`, `c`. |
| `step` | `step [n]` | Execute `n` steps (default `1`) while echoing committed events. Alias: `s`. |
| `until` | `until <where>` | Add temporary breakpoint at `<where>` and run (`max_steps=1000000`, `stuck_window=1000`). |
| `break` | `break <where> [if <expr>]` | Set breakpoint at address, symbol, or quoted/unquoted `file:line` (`:line`), with optional condition. Alias: `b`. |
| `tbreak` | `tbreak <where> [if <expr>]` | Set temporary one-shot breakpoint. |
| `watch` | `watch <target> [r\|w\|rw] [if <expr>]` | Set watchpoint (default mode `w`) on register, field, IRQ, cell, range, or `mem_alias`. |
| `delete` | `delete <id\|all>` | Delete one breakpoint/watchpoint by ID or `"all"`. |
| `info` | `info [break]` | Show breakpoints/watchpoints (`info break`), or PC/cycle/steps, spaces (with regions), registers, aliases, `mem_aliases`, and IRQs. |
| `regs` | `regs` | Print all registers and bitfields, register aliases, `mem_aliases`, and IRQ lines. |
| `mem` | `mem <space> <addr> [count]` | Peek up to `count` cells (default `16`) starting at `<addr>`. |
| `print` | `print <expr>` | Evaluate `<expr>` via `Simulator.evaluate()` (strict `r`/`m`/`i`/`pc`/`cycle` syntax). Alias: `p`. |
| `poke` | `poke <target> <value>` | Externally write register, field, cell, `irq.line`, or `mem_alias`. |
| `irq` | `irq <line> <0\|1>` | Set IRQ line level externally (`poke("irq.<line>", level)`). |
| `stim` | `stim read_seq <target> <v,v,...> \| stim list \| stim clear` | Register a comma-separated `read_seq`, list active stimuli, or clear all stimuli. |
| `display` | `display <expr>` | Register an auto-evaluated expression printed on every stop. |
| `undisplay` | `undisplay <id>` | Remove a display expression by ID. |
| `disasm` | `disasm [<where>] [count]` | Disassemble `count` instructions (default `where=pc`, `count=8`). |
| `trace` | `trace last <n> \| trace on <path> [text\|jsonl] \| trace off` | Print recent `n` events, open trace file (default `text`), or close trace file. |
| `snapshot` | `snapshot save\|restore\|export\|import ... \| snapshot list` | Manage named checkpoints in memory and export/import `.json` files. |
| `load` | `load hex <path> [big\|little] \| load bin <path> <base> [big\|little] \| load sym <path>` | Load HEX, binary, or symbol JSON files into the session. |
| `reset` | `reset` | Call `Simulator.reset()`. |
| `where` | `where` | Print current source location, nearest label, and single-line source context with cells. |
| `source` | `source [<where>] [context]` | Print source lines near `<where>` (default `pc`, `context=2`) plus instruction cell hex. |
| `quit` | `quit` | Exit the REPL cleanly. Alias: `q`. |

In `print`, `display`, and stop details, oversized integers (`abs(value) >= 10**640` or exceeding Python's `int_max_str_digits`) fall back to hex formatting so the REPL stays responsive.

### Exit Codes and Tips

- `0`: `assemble` succeeded, `run` stopped with `"halted"`, or `repl` exited cleanly.
- `1`: `run` stopped for another reason (`breakpoint`, `watchpoint`, `max_steps`, `max_cycles`, `stuck`, `fault`, `interrupted`, or policy/runtime error). Details are printed to `stderr`.
- `2`: CLI argument error, `IsaSimError`, or file/definition load failure.

## Exceptions

All package exceptions inherit from `IsaSimError`:

| Exception | Base | Raised When |
| :--- | :--- | :--- |
| `MCUDefinitionError` | `IsaSimError` | Invalid `MCU` declaration, `mem_alias` syntax, validation failure, or `load_mcu()` failure. |
| `UsageError` | `IsaSimError` | Invalid API arguments, `helpers` bounds/types, trace state, or CLI path conflicts. |
| `LoadError` | `IsaSimError` | Missing/malformed HEX, binary, symbol, bundle, or setup script file, or ISA mismatch. |
| `AssemblyError` | `LoadError` | Preprocessing, syntax, expression, or range failure during assembly. |
| `StateAccessError` | `IsaSimError` | Mutating read-only `Simulator.state`, expired `Device` `ctx`, or reading device cells via `read()`. |
| `TargetError` | `IsaSimError` | Invalid `read`/`poke`/watch target, invalid `Simulator.mem_alias()`, or invalid source location. |
| `DebugError` | `IsaSimError` | Invalid condition AST/target, stale assembly source context, unknown breakpoint/display ID, or dangling `mem_alias`. |
| `StimulusError` | `IsaSimError` | Invalid `Trigger` / `read_seq` target or value, or `Trigger.set` targeting a device or cleared `mem_alias`. |
| `DeviceError` | `IsaSimError` | Invalid `attach()` range, missing `read`/`write`, or overlap with device/`read_seq`/multi-cell `mem_alias`. |
| `SnapshotError` | `IsaSimError` | Snapshot I/O/format error, missing `Device` `get_state`/`set_state`, or incompatible `restore()` signature. |

## See Also

- [Quickstart](README.md)
- [Scenarios](scenarios.md)
- [Assembler](assembler.md)
- [Architecture](architecture.md)
- [Visual Web Debugger (`isasim_gui`)](../app/isasim_gui/README.md)
