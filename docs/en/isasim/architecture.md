# `rpkbin.isasim` Architecture and Boundaries

[繁體中文](../../zh/isasim/architecture.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [Scenarios](scenarios.md) · [Assembler](assembler.md) · [API Reference](reference.md)

`rpkbin.isasim` separates declarative machine structure (`MCU`), static assembled bundles (`Assembly`), transactional runtime execution (`Simulator`), and portable checkpoints (`Snapshot`) into distinct layers with single-owner state mutation.

## Responsibilities

`MCU` is a declarative specification of memory spaces, registers, register aliases, memory aliases (`mem_alias`), IRQ lines, instruction patterns, callbacks, and access policies. It is an input to a simulator rather than live machine state. Any Python description file loaded by `load_mcu()` must define an `mcu` object.

The preprocessor expands `.include` files and conditional directives while tracking source locations. The assembler encodes active lines against the `MCU` instruction declarations and emits an `Assembly` holding cell data, symbols, and source mappings without calling instruction callbacks.

`Simulator` is the sole owner of mutable runtime state. `State` is the checked access boundary passed into instruction and interrupt callbacks; the public `Simulator.state` attribute is read-only. External mutations pass through `poke()` and emit `"external"` events. `Simulator` also supports the context-manager protocol (`__enter__` / `__exit__`) so active trace streams close automatically when leaving a `with` block.

File-level responsibilities:

| File | Responsibility |
| :--- | :--- |
| `mcu.py` | `MCU` declarations, `MemAliasInfo` parsing, validation, `MCUDescription` export, and pattern compilation. |
| `state.py` | Checked accessors (`State`, `RegAccess`, `MemAccess`, `SpaceAccess`, `IrqAccess`) for callbacks and read-only views. |
| `sim.py` | Instruction decoding, step loop, write journal and rollback, runtime `mem_alias` / `clear_mem_aliases`, event delivery, and sole mutable-state ownership. |
| `events.py` | `Event`, `ReadRecord`, `WriteRecord`, `RunResult`, `StuckReport`, wide-integer JSON safety, and text/JSONL formatting. |
| `stimulus.py` | `Device` base class, `_DeviceContext` (`ctx`), `Trigger` / `read_seq` stimuli, IRQ bridge, and stuck-state detection. |
| `debug.py` | Restricted AST condition compiler/evaluator, breakpoints, watchpoints (including `mem_alias` observation), and displays. |
| `_preprocess.py`, `assembler.py` | Source expansion, two-pass layout, encoding, `SourceRecord`, and `.isa.json` bundle replay. |
| `loader.py` | Intel HEX, binary, symbol JSON, and `Assembly` loading plus source-location lookup. |
| `snapshot.py` | Immutable `Snapshot` checkpoints and structural configuration compatibility checks (including `mem_aliases`). |
| `helpers.py` | Width-explicit integer arithmetic, bit manipulation, and `AddResult` / `SubResult` dataclasses. |
| `cli.py`, `repl.py` | `rpk-isasim` command-line interface and stream-based 25-command REPL. |

`Simulator` inherits four internal service mixins: `_External`, `_Debug`, `_Snapshots`, and `_Loaders`. They operate on the simulator owner and never maintain separate machine state. Core step execution and transaction rollback live in `sim.py`.

## Data Flow and Invariants

```text
MCU description (+ mem_aliases) + Assembly / HEX / BIN image
                  │ input
                  ▼
       Simulator owns mutable state
        ├── callbacks mutate only through State (journaled)
        ├── runtime mem_alias / clear_mem_aliases / watchpoints
        ├── Event stream & RunResult output
        └── Snapshot save / restore (signature-checked)
```

- **Target-neutral widths and semantics**: Program/data cell widths and instruction semantics come from the `MCU` description. The package assumes no fixed processor architecture or memory map.
- **Transactional step rollback**: Each instruction step journals simulator-owned register, memory, IRQ, cycle, and `next_pc` changes. Raising inside a semantic callback or `Device` `read`/`write` rolls back journaled simulator state. Memory reads feed observation and trace; external side effects in a `Device` and consumed `read_seq` positions are not rolled back.
- **Interrupt boundary checks**: `interrupt_check(st)` runs at step boundaries. Returning `None` discards writes and extra cycles recorded during the check. Returning `(event_name, cycles)` commits those changes as an `"interrupt"` step; `event_name` is an event label and does not need to match a declared IRQ line.
- **Memory aliases (`mem_alias`)**:
  - `MCU.mem_alias()` stores machine-level memory alias declarations (`MemAliasInfo`) on the `MCU` in declaration order (`MCU.mem_aliases`).
  - `Simulator` seeds its active `mem_aliases` mapping from `mcu._mem_aliases` at initialization. `Simulator.mem_alias()` adds dynamic simulator-level aliases (rejecting collisions or multi-cell overlaps with attached `Device` ranges), and `Simulator.clear_mem_aliases()` removes simulator-registered aliases while keeping MCU-declared ones. `Simulator.reset()` preserves simulator-registered `mem_aliases`.
  - `read()`, `poke()`, `Trigger.set()`, condition expressions (`m.<alias>` / `m["<alias>"]`), and watchpoints share `_pack_mem_alias_cells` and `_mem_alias_write`. Single-cell bit slices perform read-modify-write on `poke()` and `Trigger.set()`.
  - At every step start (`S0`), `_validate_mem_alias_refs()` verifies that `mem_alias` names referenced by installed watchpoints or `Trigger.set` actions still exist and do not target devices (`DebugError` / `StimulusError` otherwise).
- **Snapshot compatibility**: `Snapshot` captures simulator registers, memory, initialization flags, IRQ lines, counters, device states (`get_state` / `set_state`), and stimulus runtime state. Restore compares structural signatures (`spaces`, `registers`, `irq_lines`, `devices`, `stimuli`, and non-empty `aliases`, `mem_aliases`, and `regions`), not Python callback bodies.
- **Assembly and source mapping**: `load_assembly()` validates the bundle before replacing the program image and source mapping atomically. It does not reset registers or cycle counts, and `reset()` preserves the attached assembly. Loading raw cells, HEX, binary, or legacy symbols clears the source mapping. Modifying assembly-mapped program cells via `poke()` or `restore()` invalidates source lookups (`DebugError` on `source_context()`, `TargetError` on new source breakpoints), while already registered breakpoints keep their resolved addresses.
- **Post-commit subscribers and `tick`**: Trace subscribers and `Device.tick()` run after a step commits. Failures in `tick` or post-commit subscribers do not roll back the committed step.

## Inputs and Outputs

Inputs consist of an `MCU` model plus program cells supplied via a Python dict, Intel HEX, raw binary, or a self-contained `.isa.json` assembly bundle (including multi-file `.include` sources and symbol mappings). Legacy symbol JSON files can also attach labels and line entries. External behavior enters through `Device` objects, `read_seq`, `poke()`, and `at_cycle` / `at_pc` / `on_read` / `on_write` stimuli.

Outputs are structured `Event` objects and `RunResult` values, formatted as text or JSONL traces, inspected in the REPL, or consumed in Python. `Snapshot` serializes checkpoints to UTF-8 JSON. `rpkbin.isasim` has no GUI layer; visual debugging lives in `rpkbin.app.isasim_gui`.

## Scope and Limits

Each instruction declaration has a fixed encoding length, though different declarations may have different lengths. The assembler provides no macros, linker, or instruction relaxation. `rpkbin.isasim` executes the patterns and semantics supplied by the user and does not verify that a model matches physical silicon; target accuracy requires a validated model, program, and golden reference results.

## See Also

- [Quickstart](README.md)
- [API Reference](reference.md)
- [Assembler](assembler.md)
- [Scenarios](scenarios.md)
- [Visual Web Debugger (`isasim_gui`)](../app/isasim_gui/README.md)
