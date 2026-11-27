# rpkbin Documentation

[繁體中文](../zh/README.md) · [Bilingual Portal](../README.md) · [Project Root](../../README.md) · [Contributing](../../CONTRIBUTING.md) · [CI Guide](../../.github/ci.md)

`rpkbin` is a Python toolkit for IC design, modeling, and verification. Its modules are grouped into **Core Libraries and Engines** (`rpkbin.*`) and **Interactive Applications** (`rpkbin.app.*`).

## Module Summary

### Core Libraries and Engines (`rpkbin.*`)

| Module | Package / CLI | Summary |
|---|---|---|
| **MapBV** | `rpkbin.mapbv` | Unsigned hardware register modeling with closed-interval bidirectional slices, `concat` buses, read-only constants, and deferred logic trees. |
| **TablePack** | `rpkbin.tablepack` | Schema-driven codec that packs tabular rows or column sequences into compact `bytes` and restores them without file I/O. |
| **JobCore** | `rpkbin.jobcore` | Process scheduler with worker limits, resource quotas, priority scheduling, guarded process controls, and process-tree cleanup. |
| **Flow** | `rpkbin.flow` | Standalone line parser, analyzer, automation, and `ViewSpec` chart projection engine inside an isolated `FlowSession`. |
| **Wave** | `rpkbin.wave`, `rpk-wave` | Combines `jobcore` and `flow` with a local Web GUI, live/saved log ingestion, and SQLite run archives (`RunArchive`). |
| **mcucc** | `rpkbin.mcucc`, `rpk-mcucc` | Compiles restricted Python into target assembly, runs static verification (`V1` to `V11`), and cross-checks behavior against `isasim`. |
| **isasim** | `rpkbin.isasim`, `rpk-isasim` | Retargetable instruction simulator and two-pass assembler driven by a Python `MCU` model, with trace, debugging, and snapshots. |

### Interactive Applications (`rpkbin.app.*`)

| Application | Package / CLI | Summary |
|---|---|---|
| **isasim_gui** | `rpkbin.app.isasim_gui`, `rpk-isasim-gui` | Loopback browser visual debugger on `rpkbin.isasim` with forward/reverse stepping, conditional breakpoints, inline poke, and cycle/step counters. |
| **rpk-calc** | `rpkbin.app.calc`, `rpk-calc` | Desktop calculator and IPython workspace for NumPy and APyTypes fixed-point (FXP) modeling, plus a headless `--eval` CLI. |

## CLI Reference Summary

Every console script supports `-V` / `--version` and shares options with its `python -m` module entry point:

| CLI Command | Module Entry Point | Reference |
|---|---|---|
| `rpk-wave` | `python -m rpkbin.wave` | [rpk-wave CLI Reference](wave/reference/cli.md) |
| `rpk-isasim` | `python -m rpkbin.isasim` | [isasim CLI and REPL](isasim/reference.md#cli-and-repl) |
| `rpk-isasim-gui` | `python -m rpkbin.app.isasim_gui` | [isasim_gui CLI Reference](app/isasim_gui/README.md#cli-reference) |
| `rpk-mcucc` | `python -m rpkbin.mcucc` | [mcucc CLI Reference](mcucc/README.md#cli-reference) |
| `rpk-calc` | `python -m rpkbin.app.calc` | [rpk-calc CLI Reference](calc/README.md#cli-reference) |

## Core Libraries and Engines (`rpkbin.*`)

### MapBV Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[MapBV Quickstart](mapbv/README.md)** | Variables, constants, bit widths, live slices, and hex/binary formatting. | First-time MapBV users |
| **[Practical Scenarios](mapbv/scenarios.md)** | Bidirectional slicing, `concat` buses, read-only bit enforcement, symbolic `eval()`, and common pitfalls. | Engineers building register models |
| **[API Reference](mapbv/reference.md)** | `MapBV` and `MapBVExpr`, factory functions (`var`, `const`, `concat`), operator semantics, and exceptions. | Looking up exact methods and types |

### TablePack Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[TablePack Quickstart](tablepack/README.md)** | Packing rows without a schema, explicit column definitions, `infer`, and `explain`. | First-time TablePack users |
| **[Usage Scenarios](tablepack/scenarios.md)** | All column types, JSON schemas, byte breakdowns, shared schemas, compressors, and error handling. | Engineers storing or shipping tables |
| **[API Reference](tablepack/reference.md)** | `pack`, `unpack`, `iter_unpack`, `infer`, `explain`, `Schema`, `DecodeLimits`, column classes, and binary format. | Looking up exact behavior |

### JobCore Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[JobCore Quickstart](jobcore/README.md)** | Running isolated commands, context managers, queue holds, and `ProcessVerdict`. | First-time JobCore users |
| **[Usage Scenarios](jobcore/scenarios.md)** | Resource quotas, scheduling modes, queue reordering, prompt responses, retries, timeouts, and `ProcessHandle`. | Engineers automating external processes |
| **[API Reference](jobcore/reference.md)** | `JobManager`, `Job`, `ControlReceipt`, `ProcessHandle`, enums, and lifecycle rules. | Looking up scheduler contracts |

### Flow Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[Flow Quickstart](flow/README.md)** | `Parser`, `Analyzer`, `Automation`, and `FlowSession` basics. | First-time Flow users |
| **[Usage Scenarios](flow/scenarios.md)** | Chained stages, `Tick` watchdogs, `EndInfo`, `ViewSpec` charts, and error isolation. | Engineers writing log analysis pipelines |
| **[API Reference](flow/reference.md)** | Pipeline definitions, contexts, `FlowSession`, `ViewSpec`, and runtime limits. | Looking up exact pipeline APIs |

### Wave Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[Wave Quickstart](wave/README.md)** | Run your first batch, understand Preview vs Start, and inspect `RunArchive`. | First-time Wave users |
| **[Workflow Scenarios](wave/scenarios.md)** | Waveform extraction, queue control, `logs`/`reanalyze`, and NFS retention setup. | Engineers writing verification scripts |
| **[Flow Pipelines](wave/flow.md)** | Integrating `rpkbin.flow` parsers, charts, and interactive prompts in Wave. | Engineers analyzing live simulation logs |
| **[Web GUI Guide](wave/gui.md)** | Layouts, queue hold/reorder, log filtering, Flow tabs, and waveform shortcuts. | Engineers monitoring runs in the browser |
| **[Python API Reference](wave/reference/python.md)** | `WaveWorkspace`, `WaveServer`, `ArchivePolicy`, `RunArchive`, and HTTP routes. | Looking up Wave Python contracts |
| **[CLI Reference](wave/reference/cli.md)** | `rpk-wave` flags, `logs` / `reanalyze` options, `RPK_WAVE_*` env vars, and exit codes. | CI and shell script integration |
| **[History and Archives](wave/history.md)** | Archive naming, retention modes, NFS staging, and automatic pruning. | Managing long-term run storage |
| **[Architecture](wave/architecture.md)** | Package boundaries, threading, lifecycle states, and loopback security. | Developers maintaining or extending Wave |
| **[Troubleshooting](wave/troubleshooting.md)** | Buffering, `--host` errors, prompt timing, chart limits, and archive errors. | Diagnosing unexpected behavior |

### mcucc Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[mcucc Quickstart and Reference](mcucc/README.md)** | Target declarations, `rpk-mcucc` CLI, `CompileConfig`, stack parking, `borrow_msb0`, and differential verification against `isasim`. | Compiler users and target authors |
| **[Bundled Language & Target Specs](../../rpkbin/mcucc/doc/language.md)** | Detailed source subset (`language.md`), target contract ([`target.md`](../../rpkbin/mcucc/doc/target.md)), guarantees ([`guarantees.md`](../../rpkbin/mcucc/doc/guarantees.md)), error catalog ([`errors.md`](../../rpkbin/mcucc/doc/errors.md)), and [bundled example](../../rpkbin/mcucc/examples/README.md). | Writing custom MCU compiler targets |

### isasim Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[isasim Quickstart](isasim/README.md)** | Define a small `MCU`, run a program, and replay from a snapshot. | First-time isasim users |
| **[Scenarios](isasim/scenarios.md)** | Deterministic traces, includes, watchpoints, `mem_alias`, and `Device` stimuli. | Engineers building or testing instruction models |
| **[Assembler](isasim/assembler.md)** | Operand declarations, source syntax, `.include`/`.if` preprocessing, and `.isa.json` bundles. | Writing assembly and portable bundles |
| **[API Reference](isasim/reference.md)** | `MCU`, `Simulator`, `Device`, `Snapshot`, `helpers`, CLI flags, and REPL commands. | Looking up exact APIs and commands |
| **[Architecture](isasim/architecture.md)** | State ownership, transaction rollback, and memory-alias invariants. | Model authors and maintainers |

## Interactive Applications (`rpkbin.app.*`)

### isasim_gui Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[isasim_gui Visual Web Debugger](app/isasim_gui/README.md)** | Launch `rpk-isasim-gui`, step forward/backward (`Over` / `In` / `Out` / `Back`), attach conditional breakpoints, poke registers/memory, and use `DebugSession`. | Engineers writing and debugging MCU assembly |

### rpk-calc Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[rpk-calc Quickstart and Reference](calc/README.md)** | Use Simple mode or Python Studio, `rpk-calc --eval`, `fxp`/`fxpi`, bit shifts, matrix editing, and startup preferences. | Engineers performing fixed-point modeling |
| **[Architecture and Data Boundaries](calc/architecture.md)** | `SimpleCalcEngine`, kernel ownership, JSON snapshots, matrix paging limits, and startup ownership. | Maintainers and advanced workspace users |
