# Wave CLI & Exit Codes Reference

[繁體中文](../../../zh/wave/reference/cli.md) · [Documentation Index](../../README.md) · [Quickstart](../README.md)

Wave provides the `rpk-wave` command-line interface for starting the local Web GUI or running non-interactive headless batches.

```bash
rpk-wave [TARGET] [OPTIONS]
```

---

## Positional Arguments

| Argument | Type | Description |
| :--- | :--- | :--- |
| **`TARGET`** | File path | Path to a trusted Python work file (`.py`). In GUI mode, loads definitions for preview. In headless mode, validates and starts dispatch immediately. |

---

## Options

| Option | Argument | Default | Description |
| :--- | :--- | :--- | :--- |
| **`--headless`** | flag | `False` | Run batch in non-interactive terminal mode. Streams output lines, prints final summary table, and exits. |
| **`--timeout`** | float (seconds) | `None` | Headless mode only. Maximum duration for the entire batch. Triggers graceful stop upon timeout. |
| **`-j`, `--max-workers`** | positive int | Work file default | Overrides maximum concurrent jobs before dispatch begins. Must be $\ge 1$. |
| **`--demo`** | flag | `False` | Loads 3 finite generic demonstration jobs. Cannot be combined with `TARGET`. |
| **`--host`** | string (IP) | `127.0.0.1` | Local GUI server bind address. Binds strictly to loopback (`127.0.0.1` or `localhost`) for security. |
| **`-p`, `--port`** | int (0~65535) | `0` | Local GUI server port. `0` selects a random available port. |
| **`--no-browser`** | flag | `False` | Do not automatically open the default web browser on launch. |
| **`-h`, `--help`** | flag | — | Display help text and exit. |

---

## Process Exit Codes

Wave adheres to standard UNIX exit code conventions for CI/CD and shell integration:

| Exit Code | Name | Meaning & Trigger Condition |
| :---: | :--- | :--- |
| **`0`** | **SUCCESS** | **All jobs verified successfully**.<br>- Headless mode: All finished jobs resolved to `PASS` or `N/A`, and no batch timeout occurred.<br>- GUI mode: Server core closed normally. |
| **`1`** | **FAILURE** | **Verification failure or deadline exceeded**.<br>- Headless mode: At least one job resolved to `FAIL`, `ERROR`, or `INCOMPLETE`.<br>- Or the batch exceeded `--timeout`. |
| **`2`** | **ERROR** | **CLI usage or definition validation error**.<br>- Invalid CLI flags or missing target.<br>- Target file missing, invalid suffix, or syntax error.<br>- Script invoked `sys.exit()` during loading. |
| **`130`** | **INTERRUPT** | **Terminal Ctrl+C (SIGINT)**.<br>The user interrupted the runner; child processes and output queues drained cleanly before exit. |

---

## Common Command Examples

### 1. Local Development: GUI Preview and Auto-Open Browser
```bash
rpk-wave work.py
```

### 2. CI/CD Automated Batch: Concurrency & Timeout
```bash
rpk-wave work.py --headless --timeout 1800 -j 8
```
- Stops cleanly and exits with code `1` if the batch does not finish within 30 minutes.
- Forces maximum concurrency to 8 worker slots.

### 3. Quick Demo Without Work File
```bash
rpk-wave --demo
```
Loads 3 finite demo jobs to explore the GUI and live log streaming.
