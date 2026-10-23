# Wave Troubleshooting Guide

[繁體中文](../../zh/wave/troubleshooting.md) · [Documentation Index](../README.md) · [Quickstart](README.md)

This document diagnoses common unexpected behaviors, root causes, and verified fixes when working with Wave and JobManager.

---

## 1. Process is running, but no log lines appear in GUI or terminal

### Symptom
Job status is `RUNNING`, but the log viewer remains blank until the command terminates, at which point all output appears at once.

### Root Cause
Standard C runtimes (glibc, MSVCRT) and languages like Python default to **block buffering** (typically 4KB–8KB) when stdout connects to a pipe rather than an interactive terminal.

### Resolution
- **Python children**: Pass `-u` or set unbuffered environment variables:
  ```python
  cmd = [sys.executable, "-u", "script.py"]
  ```
  Or explicitly flush within code: `print("...", flush=True)`.
- **C/C++ children**: Insert `setvbuf(stdout, NULL, _IOLBF, 0);` or call `fflush(stdout);`.
- **POSIX PTY**: Keep `pty=True` (default on POSIX) to simulate interactive line buffering.

---

## 2. Process exited 0, but Result is FAIL or N/A

### Symptom
Terminal indicates `Exit: 0`, but the final verdict is not `PASS`.

### Root Cause
- **Result is FAIL**:
  - A required check (`Expect.PRESENT`) never matched any output line.
  - A forbidden check (`Expect.ABSENT`) matched an output line.
  - Invariant: **Exit code 0 only reflects process termination, not business verification success**.
- **Result is N/A**:
  - The job declared no `job.check(...)` rules. Commands that finish without checks resolve to `Result=N/A`.

---

## 3. `job.send()` returns True, but the process does not react

### Symptom
Sending text via Python or the GUI input line reports transmission success, but the child process ignores the input.

### Root Cause
- `send()` returning `True` only confirms that bytes were accepted by the OS pipe buffer.
- **Successful transmission does not guarantee the external application parsed or executed the command**.
- Common reasons include:
  1. The tool requires a newline delimiter (`send(text, newline=True)` sends `\n`, but some Windows tools require `\r\n`).
  2. The tool's stdin reader is block-buffered.
  3. The tool is currently busy executing a previous task and has not returned to its event loop.

---

## 4. Web GUI port conflicts or remote connection issues

### Symptom
Launching `rpk-wave work.py` fails with `OSError: [Errno 98/10048] Address already in use`.

### Resolution
- Default port is `0` (system assigns an available ephemeral port). If you specified `-p 8080` and the port is busy, select another or let the system choose:
  ```bash
  rpk-wave work.py -p 0
  ```
- **Remote Access Note**: Wave GUI server binds strictly to local loopback addresses (`127.0.0.1` or `localhost`) for security; binding to `0.0.0.0` is explicitly disallowed and raises `ValueError`. If you need remote access to a headless server, use SSH port forwarding (e.g., `ssh -L 8080:127.0.0.1:8080 user@remote`).

---

## 5. Early log lines disappear or show "Older logs evicted"

### Symptom
Long-running jobs show truncated log views and cannot scroll back to initial lines.

### Root Cause
Wave enforces bounded memory via `max_log_lines` (default 10000). When line count exceeds capacity, the ring buffer evicts older lines.

### Invariant
- **Evidence is preserved**: All check matches, line numbers, and timestamps were snapshot independently and remain fully accessible in the Details tab.
- For unbounded disk retention, pass `Job(..., log_file="full.log")` or `JobManager(log_dir="logs/")`.
- You can also increase `max_log_lines` via GUI **Settings -> Section B**.
