# rpkbin — Core IC Design & Verification Utilities

[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](README_zh.md)

`rpkbin` is a comprehensive toolkit providing essential data types and utilities for hardware design and verification flows.

## Core Features

### 1. MapBV — BitVector with Bidirectional Mapping
MapBV allows you to define a hierarchy of bit-fields that stay synchronized automatically.
- **Hierarchical Slicing**: Define a 32-bit register and slice it into named fields.
- **Bidirectional Linking**: Use `concat` to build a new view from existing variables; updating the view updates the sources.
- **Symbolic Evaluation**: Use `.eval()` to test "what-if" scenarios without changing actual values.

[Learn more in MapBV Documentation](docs/mapbv/mapbv.md)

### 2. NumBV — Bit-True Fixed-Point Arithmetic
Bit-exact fixed-point simulation for DSP pipeline verification. Pure NumPy, no external dependency.
- **Two-layer API**: Operator layer (`+`, `*`) for convenience; function layer (`nbv.add()`, `nbv.mul()`) for explicit pipeline staging.
- **Five Rounding Modes**: `trunc`, `round`, `round_half_even` (convergent, Xilinx DSP48), `ceil`, `round_to_zero`.
- **Unified Operations**: One `NumBV` class handles both scalar and array computations. Backed by NumPy by default, with an optional drop-in JAX backend for transparent XLA hardware acceleration.

[Learn more in NumBV Documentation](docs/numbv/numbv.md)

### 3. Excel Extractor — Template-Based Extraction
Intelligently extract data from complex spreadsheets.
- **Layout Description**: Define the "shape" of data instead of hardcoded coordinates.
- **Fuzzy Matching**: Matches headers even with slight spelling variations.
- **Merged Cell Support**: Correctly resolves values spanning across merged rows/cols.

[Learn more in Excel Extractor Documentation](docs/excel_extractor/excel_extractor.md)

### 4. Job Manager
A practical, cross-platform job manager for running shell commands and Python callables safely in parallel.

- **One API for Common Workloads**: Run local functions (`FuncJob`) and CLI tasks (`CmdJob`) with the same manager.
- **Resource-Aware Scheduling**: Limit concurrency by global resources such as GPU count or license tokens.
- **Operationally Friendly**: Built-in cancellation, retries, live logs, and callbacks for automation pipelines.

[Job Manager Documentation](docs/job_manager/job_manager.md)

### 5. Wave — Batch Workflow Orchestration with Live TUI
A workflow layer built on top of Job Manager for declaring and observing long-running batch flows.

- **Wave File Pattern**: Declare jobs, parsers, and hooks in a plain Python file. Run it with `rpk-wave run`.
- **Live TUI**: A full-screen Textual interface with a job dashboard, per-job log/data/event panels, and a built-in command bar.
- **Headless REPL**: For CI or terminal sessions where a TUI isn't wanted — full inspection and control commands via stdin.
- **Hooks & Parsers**: React to log output, structured parsed data, elapsed time, or lifecycle events automatically.
- **Job Events with Source Tracking**: `job.emit()` records named events; `source="system"` marks internal errors (parser/hook failures) for easy filtering in the TUI.
- **Operational Control**: Pause/resume dispatch, gracefully stop jobs, force-cancel jobs, or target active jobs by tag from either the TUI command bar or headless REPL.
- **Fast Job Inspection**: Open `show`, `logs`, `data`, or `events <job>` directly in JOB DETAIL, then switch between jobs with `[` / `]` or between running jobs with `{` / `}`.
- **Intentional Cancellation Semantics**: User-cancelled jobs are reported as `cancelled` without turning the whole Wave run into a failed exit; real job failures and session timeouts still return a non-zero exit code.

[Wave Documentation](docs/wave/wave.md)

## Installation

```bash
# Core only (zero dependencies)
pip install -e .

# Install specific features
pip install -e .[wave]   # Installs textual, prompt_toolkit, rich
pip install -e .[excel]  # Installs openpyxl, xlrd, rapidfuzz
pip install -e .[cfg]    # Installs networkx

# Install everything
pip install -e .[all]
```

## Testing

Run tests using `pytest` from the root directory:
```bash
pytest tests/ -v
```
*(All tests require only `numpy` — no optional dependencies needed.)*
