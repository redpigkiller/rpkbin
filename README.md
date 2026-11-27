# rpkbin: IC Design Libraries and Interactive Applications

`rpkbin` is a Python toolkit for IC design, hardware modeling, and verification. It splits into core libraries and engines (`rpkbin.*`) and interactive applications (`rpkbin.app.*`).

[繁體中文](README_zh.md) · [Documentation Index](docs/en/README.md) · [Bilingual Portal](docs/README.md) · [Contributing](CONTRIBUTING.md) · [CI Guide](.github/ci.md) · [MIT License](LICENSE)

## Modules and Entry Points

### Core Libraries and Engines (`rpkbin.*`)

| Module | Package / CLI | Purpose | Docs |
|---|---|---|---|
| MapBV | `rpkbin.mapbv` | Unsigned register modeling with live bidirectional slices, `concat` buses, read-only constants, and deferred logic expressions. | [MapBV](docs/en/mapbv/README.md) |
| TablePack | `rpkbin.tablepack` | Schema-driven codec that packs tabular rows or column sequences into compact `bytes` and restores them without file I/O. | [TablePack](docs/en/tablepack/README.md) |
| JobCore | `rpkbin.jobcore` | Process scheduler with worker limits, resource quotas, priority queues, guarded controls, and process-tree cleanup. | [JobCore](docs/en/jobcore/README.md) |
| Flow | `rpkbin.flow` | Standalone line-by-line text parser, analyzer, automation, and chart projection (`ViewSpec`) engine inside `FlowSession`. | [Flow](docs/en/flow/README.md) |
| Wave | `rpkbin.wave`, `rpk-wave` | Combines `jobcore` and `flow` with a local Web GUI, log tailing, SQLite run archives (`RunArchive`), and archive reanalysis. | [Wave](docs/en/wave/README.md) |
| mcucc | `rpkbin.mcucc`, `rpk-mcucc` | Compiles a restricted Python subset into target assembly, runs static checks (`V1` to `V11`), and cross-checks against `isasim`. | [mcucc](docs/en/mcucc/README.md) |
| isasim | `rpkbin.isasim`, `rpk-isasim` | Retargetable instruction simulator and two-pass assembler driven by a Python `MCU` model, with trace, debugging, and snapshots. | [isasim](docs/en/isasim/README.md) |

### Interactive Applications (`rpkbin.app.*`)

| Application | Package / CLI | Purpose | Docs |
|---|---|---|---|
| isasim_gui | `rpkbin.app.isasim_gui`, `rpk-isasim-gui` | Loopback browser debugger on `rpkbin.isasim` with forward/reverse stepping, conditional breakpoints, inline poke, and cycle/step counters. | [isasim_gui](docs/en/app/isasim_gui/README.md) |
| rpk-calc | `rpkbin.app.calc`, `rpk-calc` | Desktop calculator and IPython workspace for NumPy and APyTypes fixed-point (FXP) modeling, plus a headless `--eval` CLI. | [rpk-calc](docs/en/calc/README.md) |

## Installation

Requires Python 3.11 or newer (`numpy>=1.21` and `regex>=2024.11.6` are installed as core dependencies):

```bash
# Core package (MapBV, TablePack, JobCore, Flow, Wave, mcucc, isasim, and isasim_gui)
python -m pip install --upgrade rpkbin

# Optional: brotli >= 1.2 compression for TablePack (uses zlib without it)
python -m pip install --upgrade "rpkbin[table]"

# Optional: apytypes >= 0.5.0 for headless fixed-point math (fxp / fxpi)
python -m pip install --upgrade "rpkbin[fixed]"

# Optional: compatibility extra for isasim_gui (uses only the Python standard library)
python -m pip install --upgrade "rpkbin[isasim_gui]"

# Optional: rpk-calc Qt/IPython GUI and fixed-point support
python -m pip install --upgrade "rpkbin[calc]"

# Optional: install all optional extras
python -m pip install --upgrade "rpkbin[all]"
```

Installing `rpkbin` provides five CLI commands (`rpk-wave`, `rpk-isasim`, `rpk-isasim-gui`, `rpk-mcucc`, `rpk-calc`), each also runnable as `python -m rpkbin.<module>` and supporting `-V` / `--version`.

## Quickstart: Getting Your First Verifiable Results

### 1. Wave: Run Your First Batch

Create `work.py` to define your jobs. A work file registers jobs and `Flow` definitions without starting subprocesses:

```python
import sys
from rpkbin.flow import Analyzer, Binding, Flow, Parser
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import WaveWorkspace


def parse_marker(line, ctx):
    if line == "TEST_PASS":
        return {"passed": True}


def remember_marker(record, ctx):
    ctx.state["seen"] = record["passed"]


def finish(end, ctx):
    passed = end.complete and end.execution.get("returncode") == 0 and ctx.state.get("seen", False)
    ctx.summary.result("PASS" if passed else "FAIL", reason="marker and exit status", final=end.complete)


marker = Parser("marker", parse_marker)
verification = Analyzer("verification", records=[Binding(marker, remember_marker)], on_finish=finish)
flow = Flow("verification", parsers=[marker], analyzers=[verification], result=verification)
workspace = WaveWorkspace(JobManager(max_workers=2))
for name in ("sim_core", "sim_dma"):
    command = [sys.executable, "-u", "-c", "print('TEST_PASS'); print('DONE')"]
    workspace.add(Job(name, command), flow=flow)
```

Run the batch in the terminal (`--headless`):

```bash
rpk-wave work.py --headless
```

The terminal streams child output lines and prints a summary when both jobs finish. Output lines from concurrent jobs can appear in either order.

```text
[Wave] Starting 2 job(s); workers=2
[child sim_core #1] TEST_PASS
[child sim_core #1] DONE
[child sim_dma #1] TEST_PASS
[child sim_dma #1] DONE

[Wave] Final summary
[Wave] History saved: run <run-id>
JOB                        STATE           RESULT       WARNINGS  EXIT
sim_core                   DONE            PASS         0         0
  analysis verification: PASS; marker and exit status
sim_dma                    DONE            PASS         0         0
  analysis verification: PASS; marker and exit status
```

To inspect jobs in the local Web GUI before running them, omit `--headless`:

```bash
rpk-wave work.py
```

Open the URL printed by Wave if your browser does not open automatically, check the queue in **Preview**, and click **Start 2 Jobs**.

If another tool writes the logs, run `rpk-wave logs session.log logs/` to preview a file and a folder in one workspace. Add `--once` for finished files, `--headless` for terminal execution, and `--flow FLOW.py` to attach analysis. See [Wave Log and Reanalysis Options](docs/en/wave/scenarios.md#log-and-reanalysis-options) for a runnable example.

### 2. MapBV: Create and Slice Register Fields

Define a 16-bit register variable, extract slices, and observe bidirectional updates:

```python
import rpkbin.mapbv as mbv

# Create a 16-bit register initialized to 0x1234
reg = mbv.var("CSR", 16, 0x1234)

# Slice [15:8] (high byte) and [3:0] (low nibble)
# Slices are live, bidirectional views linked to the parent register
high_byte = reg[15:8]
low_nibble = reg[3:0]

print(f"Original register: {reg:hex} (0x{reg.value:04X})")
print(f"High byte slice:   {high_byte:hex}")
print(f"Low nibble slice:  {low_nibble:hex}")

# Modifying the slice updates the parent register immediately
high_byte.value = 0xAB
print(f"After modifying slice: {reg:hex}")
```

Output:

```text
Original register: 0x1234 (0x1234)
High byte slice:   0x12
Low nibble slice:  0x4
After modifying slice: 0xAB34
```

> [!IMPORTANT]
> `reg == 0xAB34` compares Python object identity and raises `TypeError` to catch accidental value comparisons. Use `reg.value == 0xAB34` or `reg.value_eq(0xAB34)` to compare values.

### 3. TablePack: Pack a Table into Bytes and Back

Describe the columns once, pack rows into a `bytes` blob, and restore them exactly:

```python
from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack

schema = Schema(
    Str("module", repeat=True),             # repeats cost 1 bit when equal to the previous row
    Uint("addr", bits=16, delta=True),      # store the step from the previous row
    Enum("kind", ["RW", "RO", "W1C"]),      # known set of values
    Str("note", nullable=True),             # optional string or None
)
rows = [
    {"module": "uart", "addr": 0x10, "kind": "RW", "note": None},
    {"module": "uart", "addr": 0x14, "kind": "RO", "note": "status"},
    {"module": "spi", "addr": 0x40, "kind": "W1C", "note": None},
]

blob = pack(rows, schema)
assert unpack(blob) == rows               # the schema is embedded in the blob by default
print(unpack(blob)[1])
```

Output:

```text
{'module': 'uart', 'addr': 20, 'kind': 'RO', 'note': 'status'}
```

TablePack never reads files: pass rows from any source and it returns `bytes`. Calling `pack(rows)` without a schema infers one automatically. Install `python -m pip install "rpkbin[table]"` for brotli compression; without it, TablePack uses `zlib`.

## Where to Read Next

### Core Libraries and Engines (`rpkbin.*`)

#### MapBV
- [MapBV Quickstart](docs/en/mapbv/README.md): Variables, constants, bit widths, and hex/binary formatting.
- [Practical Scenarios](docs/en/mapbv/scenarios.md): Live slices, bus concatenation (`concat`), read-only bits, and symbolic `eval()`.
- [API Reference](docs/en/mapbv/reference.md): `MapBV`, `MapBVExpr`, factory functions, operators, and exceptions.

#### TablePack
- [TablePack Quickstart](docs/en/tablepack/README.md): Schema-free packing, explicit schemas, `infer`, and `explain`.
- [Usage Scenarios](docs/en/tablepack/scenarios.md): Column types, JSON schemas, byte breakdowns, compressors, and error diagnosis.
- [API Reference](docs/en/tablepack/reference.md): Functions, column types, `DecodeLimits`, exceptions, and binary format.

#### JobCore
- [JobCore Quickstart](docs/en/jobcore/README.md): Running isolated commands, context managers, and queue holds.
- [Usage Scenarios](docs/en/jobcore/scenarios.md): Resource quotas, scheduling modes, prompts, retries, timeouts, and `ProcessHandle`.
- [API Reference](docs/en/jobcore/reference.md): `JobManager`, `Job`, `ControlReceipt`, `ProcessHandle`, and status enums.

#### Flow
- [Flow Quickstart](docs/en/flow/README.md): `Parser`, `Analyzer`, `Automation`, and `FlowSession` basics.
- [Usage Scenarios](docs/en/flow/scenarios.md): Chained stages, `Tick` watchdogs, `EndInfo`, `ViewSpec` charts, and error isolation.
- [API Reference](docs/en/flow/reference.md): Pipeline definitions, contexts, `FlowSession`, `ViewSpec`, and runtime limits.

#### Wave
- [Wave Quickstart](docs/en/wave/README.md): Running your first batch, Preview vs Start, and `RunArchive`.
- [Workflow Scenarios](docs/en/wave/scenarios.md): Waveform extraction, queue control, `logs`/`reanalyze`, and NFS retention.
- [Web GUI Guide](docs/en/wave/gui.md): Layouts, queue controls, log filtering, Flow inspection, and waveform shortcuts.
- [Python API Reference](docs/en/wave/reference/python.md) and [CLI Reference](docs/en/wave/reference/cli.md#general-and-server-options): `WaveWorkspace`, `ArchivePolicy`, `RunArchive`, `rpk-wave` flags, `RPK_WAVE_*` env vars, and exit codes.
- [Flow Guide](docs/en/wave/flow.md), [History and Archives](docs/en/wave/history.md), [Architecture](docs/en/wave/architecture.md), and [Troubleshooting](docs/en/wave/troubleshooting.md).

#### mcucc
- [mcucc Quickstart and Reference](docs/en/mcucc/README.md): Target declarations, `rpk-mcucc` CLI, `CompileConfig`, stack parking, and differential checks against `isasim`.

#### isasim
- [isasim Quickstart](docs/en/isasim/README.md): Define a small `MCU`, run a program, and replay from a snapshot.
- [Scenarios](docs/en/isasim/scenarios.md): Deterministic traces, includes, watchpoints, `mem_alias`, and `Device` stimuli.
- [Assembler](docs/en/isasim/assembler.md), [API Reference](docs/en/isasim/reference.md), and [Architecture](docs/en/isasim/architecture.md): Assembly syntax, `Simulator`, `helpers`, CLI/REPL commands, and state boundaries.

### Interactive Applications (`rpkbin.app.*`)

#### isasim_gui
- [isasim_gui Visual Debugger](docs/en/app/isasim_gui/README.md): Launch `rpk-isasim-gui`, step forward/backward, set conditional breakpoints, poke registers/memory, and use `DebugSession`.

#### rpk-calc
- [rpk-calc Quickstart and Reference](docs/en/calc/README.md): Launch Simple mode or Python Studio, use `fxp`/`fxpi` and `rpk-calc --eval`, inspect matrices, and configure startup files.
- [Architecture and Data Boundaries](docs/en/calc/architecture.md): `SimpleCalcEngine`, kernel ownership, JSON snapshots, and matrix paging limits.

## License

This project's code is licensed under the [MIT License](LICENSE). Embedded SVG icons include Feather (MIT) and Lucide (ISC) designs, whose notices are retained in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), so the distribution declares `MIT AND ISC`. Third-party dependencies keep their own licenses; MIT does not relicense those packages. In particular, `rpk-calc` uses PySide6 / Qt for Python, whose community distribution is subject to [LGPL/GPL licensing terms](https://doc.qt.io/qtforpython-6/licenses.html). The `rpkbin` wheel contains this project's code without bundling Qt or other external dependencies; redistributing a standalone bundle with those packages requires checking their license terms separately.
