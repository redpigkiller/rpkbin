# StageTracker — Multi-Stage Workflow Logging

[![English](https://img.shields.io/badge/Language-English-blue.svg)](stage_tracker.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](stage_tracker_zh.md)

`StageTracker` is a multi-stage execution tracker that provides logging, issue collection, execution time tracking, and a summary report upon completion.

**The only valid usage is wrapping the entire workflow using `with StageTracker() as t:`.** Other usage patterns are not supported.

---

## Quick Start (User Guide)

### 1. Flat Mode

Best for top-down, sequential scripts. Manually call `begin_stage(name)` to switch stages.

> [!IMPORTANT]
> Calling `begin_stage` automatically finalizes the previous stage. If the previous stage had any accumulated `error()` or `fatal()` calls, `begin_stage` will raise `StageFailedError` immediately.

The final stage is finalized and reported automatically when exiting the `with` block.

```python
from rpkbin.utils.stage_tracker import StageTracker

with StageTracker("MainTracker", mode="flat") as t:
    t.begin_stage("Initialization")
    t.info("Starting workflow", track=True)
    t.warning("Debug mode enabled")

    # Implicitly finalizes "Initialization" and starts "Data Processing"
    # If "Initialization" had errors, StageFailedError is raised here.
    t.begin_stage("Data Processing")
    t.error("File 'corrupt.txt' is corrupt") # Accumulates issue
    t.error("File 'missing.txt' not found")

    # Will raise StageFailedError immediately if there are accumulated errors
    t.checkpoint()

    t.begin_stage("Export")
    t.info("Done writing.")
    # Exiting `with` automatically finalizes "Export" and prints summary
```

### 2. Context Manager Mode

Best for isolated blocks, loops, or complex nested logic. Use `with t.stage(name):` to wrap each stage. **Nested stages are not supported.**
> [!TIP]
> **Loops require unique names:** When using context mode inside a loop, ensure you generate unique stage names (e.g., `with t.stage(f"Process_{i}"):`). Reusing a stage name will raise a `UsageError`.

```python
from rpkbin.utils.stage_tracker import StageTracker

with StageTracker("ContextTracker", mode="context") as t:
    with t.stage("Download"):
        t.info("Downloading files...")
        # Health checked automatically upon exit. 
        # If any `t.error()` was called, StageFailedError is raised here.

    with t.stage("Parsing"):
        t.fatal("Out of memory!") # Raises StageFailedError immediately
```

> **Note**: You cannot mix Flat Mode and Context Manager Mode loops within the same `StageTracker` instance.

---

## API Reference (Detailed Control)

### Construction

```python
StageTracker(
    name: str = "StageTracker",
    mode: Literal["flat", "context"] = "flat",
    plain: bool | None = None,
    track_time: bool = True,
)
```

**Auto-detecting `plain`:**
If `plain=None`, it automatically falls back to plain text (no Rich colors) if `NO_COLOR` is set, `TERM` is dumb/unknown, standard output is not a TTY, or the `rich` package is not installed.

### Logging

All logging methods route through the standard Python `logging` module and conditionally add the message to the issue tracker.

| Method | Level | Default `track` | Description |
| --- | --- | --- | --- |
| `t.debug(msg)` | DEBUG | `False` | Debug message, untracked by default. |
| `t.info(msg)` | INFO | `False` | General info, untracked by default. |
| `t.warning(msg)` | WARNING | `True` | Warning, tracked in summary but doesn't block execution. |
| `t.error(msg)` | ERROR | `True` (forced) | Error, tracked and blocks execution at the end of the stage. |
| `t.fatal(msg)` | CRITICAL | `True` (forced) | Critical error, immediately raises `StageFailedError`. |

*(Note: Passing `track=True` adds any info or debug log to the final issue summary report.)*

### Stage Management Methods

| Method | Description |
| --- | --- |
| `begin_stage(name)` | Starts a new flat-mode stage. Triggers health check on the previous stage (raises `StageFailedError` if it failed). |
| `stage(name)` | Context manager to execute a code block as a distinct stage (Context mode only). Health checked on exit. |
| `checkpoint()` | Proactively checks the current stage health. Raises `StageFailedError` if any ERROR/CRITICAL issues have accumulated. |

### Summary & Report

| Method | Description |
| --- | --- |
| `summary(title=..., raise_errors=True)` | Prints the summary report. If `raise_errors=True`, it raises `StageFailedError` if the workflow failed. In flat mode, also finalizes the current stage. |

### Configuration

| Method | Description |
| --- | --- |
| `add_console_handler(level="INFO")` | Adds a console output (auto-added on init). Prevents duplicates. |
| `add_file_handler(path, level="DEBUG", max_bytes=0, backup_count=0)` | Adds file logging, with optional log rotation. **Tip:** Highly recommended to call this before beginning the first stage. |

### Issue Management

| Method | Description |
| --- | --- |
| `get_issues(stage=None, level=None)` | Returns a list of `Issue` dataclasses matching the stage and/or level filters. |
| `clear_issues()` | Clears all tracked data and history. Cannot be called while a stage is currently active (Raises `UsageError`). |

---

## Behavior Details

### Exception Handling & Summaries

The tracker automatically triggers a summary report when the `with` block exits:

- **Normal Exit:** Finalizes the current stage, prints `EXECUTION SUMMARY`, and raises `StageFailedError` if the last stage had errors.
- **Exception Exit:** If any exception occurred (including `fatal`), it prints `EXECUTION FAILED (ExceptionType)` and re-raises the exception.
- **Unassigned Logs (The "System" Stage):** Any log methods (`t.error()`, `t.warning()`, etc.) called *before* the first `begin_stage()` or outside any `with t.stage()` block are assigned to the `"System"` stage. If an error is logged to `"System"`, the tracker will still accurately report a failure at the end.

**Example Summary Output:**
```text
============================================================
                     EXECUTION SUMMARY                      
============================================================
Execution Paths by Thread:
  MainThread: Initialization → Data Processing → Export
------------------------------------------------------------
[ WARNING] Data Processing | File 'corrupt.txt' is corrupt (1.20s)
[  ERROR ] Data Processing | File 'missing.txt' not found (0.01s)
------------------------------------------------------------
FAILED: 1 critical/errors found.
============================================================
```

### `StageFailedError`

Raised whenever a stage evaluates with an `ERROR` or `CRITICAL` issue, storing:
- `e.stage`: The failed stage's name.
- `e.error_count`: The number of blocking errors.
- `e.issues`: The full issue list for debugging.

### Thread Safety

- `_issues` and related resources are protected by locks; logging from multiple threads is safe.
- `current_stage` relies on Python's `threading.local()`, so each thread independently manages its current stage natively.
- Stage times and starts do not use locks during updates, expecting each thread to manage isolated stages logically.
- **Important:** When using multiple threads, you must manually `join()` all threads before exiting the `with StageTracker()` block to ensure the final summary report accurately reflects all thread activities.
