# rpkbin — Core IC Design & Verification Utilities

[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](README_zh.md)

`rpkbin` is a practical toolbox for hardware design and verification work. It includes small, focused utilities for bit-true modeling, spreadsheet extraction, control-flow modeling, and long-running batch workflows.

If you are new here, start with the task you want to solve:

| I want to... | Start with |
| --- | --- |
| Run many shell/Python jobs and watch them live | [Wave](docs/wave/wave.md) |
| Run commands/functions in parallel from Python | [Job Manager](docs/job_manager/job_manager.md) |
| Model registers and bit fields | [MapBV](docs/mapbv/mapbv.md) |
| Simulate fixed-point arithmetic | [NumBV](docs/numbv/numbv.md) |
| Extract structured data from Excel files | [Excel Extractor](docs/excel_extractor/excel_extractor.md) |
| Sketch or validate low-level control flow | [CFG](docs/cfg/cfg.md) |

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

### 4. CFG — Low-Level Control Flow Toolkit
Organize assembly-like flows, FSM state machines, and MCU branch layouts before writing target-specific code.
- **Explicit Flow Modeling**: Build labeled blocks and priority-ordered branch edges without committing to an ISA.
- **Readable Checks & Layouts**: Validate common control-flow mistakes, print text layouts, and choose deterministic block emission order.
- **Program Call Checks**: Mark subroutine calls with `CallRef` and check call depth against hardware or coding-rule limits.

[Learn more in CFG Documentation](docs/cfg/cfg.md)

### 5. Job Manager
A practical, cross-platform job manager for running shell commands and Python callables safely in parallel.

- **One API for Common Workloads**: Run local functions (`FuncJob`) and CLI tasks (`CmdJob`) with the same manager.
- **Resource-Aware Scheduling**: Limit concurrency by global resources such as GPU count or license tokens.
- **Operationally Friendly**: Built-in cancellation, retries, live logs, and callbacks for automation pipelines.

[Job Manager Documentation](docs/job_manager/job_manager.md)

### 6. Wave — Batch Workflow Orchestration with Live TUI
A workflow layer built on top of Job Manager for declaring and observing long-running batch flows.

- **Plain Python wave files**: declare jobs, parsers, hooks, and actions in a normal `.py` file.
- **Live TUI by default**: dashboard, per-job logs, parsed data, events, system messages, and a command bar.
- **Headless mode when needed**: use `--no-tui` for CI or script-only environments.
- **Operational controls**: rerun jobs, stop/cancel by job or tag, send stdin, send OS signals, or send PTY terminal keys.
- **Automation hooks**: react to log patterns, parsed data, elapsed time, lifecycle events, or user-defined actions.

[Wave Documentation](docs/wave/wave.md)

## Quick Start: Wave

Install Wave support:

```bash
pip install -e .[wave]
```

Create `hello.wave.py`:

```python
from rpkbin.wave import session, CmdJob

session.configure(max_workers=2)

session.add(CmdJob("hello", "python -c \"print('hello from wave')\""))
session.add(CmdJob("list", "python -c \"import os; print(os.getcwd())\""))
```

Run it:

```bash
rpk-wave run hello.wave.py
```

Useful TUI commands:

```text
status
logs hello
show hello
rerun hello
stop hello
```

For CI or plain terminal output:

```bash
rpk-wave run hello.wave.py --no-tui
```

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
