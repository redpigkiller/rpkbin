# rpkbin — IC Design & Verification Utilities

[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](README_zh.md)

`rpkbin` is a Python toolkit for IC design and verification workflows: bit/register mapping, bit-true fixed-point simulation, spreadsheet extraction, control-flow modeling, compiler-backend experiments, and observable batch execution.

## Why rpkbin?

The package keeps small, hardware-oriented utilities composable instead of forcing a single end-to-end framework. Each module exposes a focused Python API, while Wave adds a CLI and live/headless controls for longer-running jobs.

## Quick start

The shortest path from a clean checkout to a working result is the Wave smoke flow.

1. Install Python 3.11+ and the Wave extra.

   ```bash
   python -m pip install -e ".[wave]"
   ```

2. Generate a starter wave file with a parser example.

   ```bash
   rpk-wave init hello --profile parser
   ```

   Expected output:

   ```text
   [Wave] Created hello.wave.py
          Edit the file, then run:  rpk-wave run hello.wave.py
   ```

3. Run it in headless mode and print the lightweight performance summary.

   ```bash
   rpk-wave run hello.wave.py --no-tui --perf
   ```

   A successful run includes:

   ```text
   [Wave][perf] summary
   [Wave][perf] total lines=2 parser=2 ...
   ```

   The timing fields vary by machine; the process exits with code `0` when the batch succeeds.

## Installation and feature extras

The core install provides the package, NumPy, and Click. Optional functionality is installed with extras:

| Extra | Enables | Main dependency |
| --- | --- | --- |
| `wave` | Wave CLI, TUI, parsers, hooks, PTY jobs | Textual, prompt-toolkit, Rich |
| `excel` | Excel Extractor | openpyxl, xlrd, rapidfuzz |
| `cfg` / `dot` | NetworkX analysis / `CFG.export_dot()` | networkx / pydot |
| `jax` | NumBV JAX backend | JAX |

```bash
python -m pip install -e "."             # core
python -m pip install -e ".[excel]"      # Excel Extractor
python -m pip install -e ".[all]"        # all listed extras except jax
python -m pip install -e ".[all,jax]"    # everything, including JAX
```

The package requires Python `>=3.11`. The authoritative dependency and entry-point definitions are in [pyproject.toml](pyproject.toml).

## Common usage

### Bit-true fixed-point arithmetic

```python
from rpkbin.numbv import Format, scalar

fmt = Format(8, 4)
a = scalar(1.25, fmt=fmt)
b = scalar(0.5, fmt=fmt)

print((a + b).val)
```

```text
1.75
```

Use the function-level API (`add`, `mul`, `sum`, `dot`, `mac`) when each pipeline stage needs an explicit output format. See [NumBV](docs/numbv/README.md).

### Concurrent Python and shell jobs

```python
from rpkbin.job_manager import JobManager, FuncJob

job = FuncJob("hello", lambda: "ok")
with JobManager(max_workers=1) as manager:
    manager.add(job)
    manager.wait()

print(job.status, job.result)
```

```text
done ok
```

Use [Wave](docs/wave/README.md) when the same jobs need parsers, hooks, PTY support, logs, or an interactive TUI.

## Module guide

| Status | Module | Use it for |
| --- | --- | --- |
| Stable | [MapBV](docs/mapbv/README.md), [NumBV](docs/numbv/README.md), [StageTracker](docs/utils/README.md), [Job Manager](docs/job_manager/README.md), [Wave](docs/wave/README.md) | Register/bit views, fixed-point simulation, stage reporting, concurrent jobs, and observable workflows |
| Beta | [CFG](docs/cfg/README.md), [Excel Extractor](docs/excel_extractor/README.md) | Low-level flow/layout analysis and template-based Excel extraction |
| Experimental | [Codegen](docs/codegen/README.md), [Debug & DAP](docs/debug/README.md) | Compiler-backend experiments and target-neutral debugger/DAP integration contracts |

Each module has a short English/Traditional Chinese entry point under [`docs/`](docs/); the longer `*.md` pages are the detailed references.

## Architecture

At the top level, callers choose a focused module. Wave is layered on Job Manager; Codegen is a target-neutral HIR-to-pseudo-ASM backend and does not parse a DSL or encode a real MCU instruction set.

```text
Python / Excel / HIR input
        │
        ├─ MapBV · NumBV · Excel Extractor · CFG · StageTracker
        │
        ├─ Wave CLI/TUI ──> Job Manager ──> local commands / Python callables
        │
        └─ Codegen: HIR ─> validate ─> LIR ─> rewrite ─> allocate ─> Target ─> pseudo ASM
```

See [architecture.md](docs/architecture.md) for responsibilities, session boundaries, invariants, and trade-offs.

## Reference

| Need | Start here |
| --- | --- |
| Wave commands, options, and exit codes | [CLI reference](docs/cli-reference.md) |
| Codegen HIR/LIR constructs and pipeline errors | [Syntax reference](docs/syntax-reference.md) |
| Module-specific API and examples | The relevant page under [`docs/`](docs/) |

## Known limitations and troubleshooting

- Installing `rpkbin` without an extra does not install Wave, Excel, NetworkX, pydot, or JAX. Install the extra for the module you use.
- NumBV selects its process-global backend before creating `NumBV` objects; JAX is optional and is not included by `.[all]`.
- Codegen is experimental. It intentionally excludes DSL parsing, real MCU ISAs, assemblers, linkers, binary encoding, production spilling, and a one-call module-to-ASM pipeline. Check [status.md](docs/codegen/status.md) before depending on it.
- Debug & DAP is experimental. Targets retain execution semantics, source mapping, breakpoint-condition evaluation, register schemas, and memory serialization; `rpkbin.debug` only supplies the integration contract and stdio DAP lifecycle.
- Wave defaults to the full-screen TUI in an interactive terminal. Use `--no-tui` in CI or when only batch completion is required; install the `wave` extra first.
- If a Wave job fails, inspect its status and logs in the TUI/headless controls; the CLI returns a non-zero status when the batch is unsuccessful.

## Contributing

`rpkbin` is published as an MIT-licensed public package. For bugs, proposals, or pull requests, use the [GitHub repository](https://github.com/redpigkiller/rpkbin). See [CONTRIBUTING.md](CONTRIBUTING.md) for local development and documentation checks.

## License

The package is released under the [MIT License](LICENSE).

## Testing

From the repository root:

```bash
python -m pytest tests/ -v
```
