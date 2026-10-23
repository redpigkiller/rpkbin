# rpkbin Documentation

[繁體中文](../zh/README.md) · [Project Root](../../README.md)

`rpkbin` is a specialized Python toolkit for IC design, verification, and script automation. It consists of four dedicated utilities:

1. **Wave (Process Scheduler & Monitor)**: Defines external command batches in Python, providing prioritized scheduling, fixed resource capacity limits, real-time stdout filtering, declarative `track` / `check` / `on` rules, immutable verification reports, and dual execution interfaces (local Web GUI and headless CLI).
2. **MapBV (Hardware Register Bitfields)**: Lightweight BitVector library offering register field definitions, closed-interval slicing with live bidirectional synchronization, bus concatenation (`concat`), combinational logic trees with deferred evaluation, and symbolic substitution.
3. **TablePack (Table Codec)**: Schema-driven library that packs a table of rows into a compact `bytes` blob (typed columns, delta/repeat/nullable encoding, optional brotli) and restores it exactly. It never reads files.
4. **rpk-calc (Engineering and Fixed-Point Calculator)**: Interactive Python workspace for NumPy and explicit FXP conversion, with GUI inspection and editing of numeric values and arrays.

---

## Wave Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[Wave Quickstart](wave/README.md)** | Completing your first batch: work file structure, headless batch execution, and GUI preview/start. | Beginners using Wave for the first time |
| **[Python Usage Scenarios](wave/scenarios.md)** | 8 complete, runnable scenarios from basic execution to concurrency, multi-check combinations, warnings, data tracking, interactive input, resource quotas, timeouts, retries, and bounded history. | Engineers writing verification scripts |
| **[GUI Operations Guide](wave/gui.md)** | Comprehensive browser UI manual: Preview and Start, job selection, log filtering, batch controls, and Settings modal (Execution Limits, Memory Retention, Display Preferences). | Engineers monitoring and controlling jobs via browser |
| **[Python API Reference](wave/reference/python.md)** | Complete parameter signatures, types, default values, enums (`StopPolicy`, `JobStatus`, `Result`, `CheckState`, `Expect`), events, and return structures. | Looking up exact API contracts |
| **[CLI & Exit Codes Reference](wave/reference/cli.md)** | `rpk-wave` command-line options (`--headless`, `--timeout`, `-j`, etc.) and process exit codes (0, 1, 2, 130). | CI/CD and shell script integration |
| **[Architecture & Contracts](wave/architecture.md)** | Responsibilities of Wave vs. JobManager, State vs. Result verdict matrix, bounded history memory guarantees, and POSIX PTY vs. Windows process models. | Developers maintaining or extending Wave |
| **[Troubleshooting Guide](wave/troubleshooting.md)** | Subprocess stdout buffering (`-u`/`flush=True`), Exit 0 with Result FAIL, send success vs. command execution, and port conflicts. | Diagnosing unexpected behaviors |

---

## MapBV Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[MapBV Quickstart](mapbv/README.md)** | First steps creating variables and constants, bit width properties, value reads/writes, and hex/binary formatting. | Beginners using MapBV |
| **[Practical Scenarios](mapbv/scenarios.md)** | Field slicing with live synchronization, hardware bus concatenation (`concat`) with read-only bit enforcement, combinational logic expressions, symbolic evaluation (`eval`), and common pitfalls (`==` vs. `.value_eq`). | Engineers building register models |
| **[API & Semantics Reference](mapbv/reference.md)** | In-depth reference for `MapBV` and `MapBVExpr`, factory functions (`var`, `const`, `concat`), operator behaviors, and exceptions. | Looking up exact methods and types |

---

## TablePack Documentation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[TablePack Quickstart](tablepack/README.md)** | Packing rows without a schema, describing columns yourself, `infer` and `explain`, and the first size comparison. | Beginners using TablePack |
| **[Usage Scenarios](tablepack/scenarios.md)** | Every column type, saving a schema as JSON, finding where bytes go, sharing a schema instead of embedding it, column-oriented data, tuning options, choosing a compressor, build-time verification, and reading errors. | Engineers storing or shipping tables |
| **[API & Semantics Reference](tablepack/reference.md)** | `pack`, `unpack`, `iter_unpack`, `infer`, `explain`, `Schema`, all column types and options, accepted values, exceptions, and the binary format. | Looking up exact behavior |

---

## rpk-calc Documentation

- **[rpk-calc Quickstart](calc/README.md)**: Install the optional dependencies, get a first CLI result, use Python and FXP explicitly, and inspect or edit arrays.
