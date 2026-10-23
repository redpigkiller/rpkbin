# Wave Quickstart: Running Your First Batch

[繁體中文](../../zh/wave/README.md) · [Documentation Index](../README.md)

Wave is a tool for defining external command batches in Python, providing prioritized scheduling, real-time log observation, and multi-check verification. Both the headless terminal runner and the local Web GUI **share the exact same Python work definition file**.

This guide walks you through completing your first batch from start to finish.

---

## Step 1: Create a Work Definition File (`work.py`)

Create a file named `work.py` in your working directory.

A work file's sole responsibility is declaring jobs (`Job`) and monitoring rules, then registering them with a manager (`JobManager`). **Do not call `start()` or `wait()` inside the file**; dispatch timing is managed by Wave's entry point.

```python
"""work.py — Defines two standard demonstration simulation jobs."""
import sys
from rpkbin.wave import Job, JobManager, Expect

# 1. Create the manager: limit concurrency to at most 2 simultaneous jobs
mgr = JobManager(max_workers=2)

# 2. Define the first job: simulating an external EDA/compilation tool
job1 = mgr.add(Job(
    "sim_core",
    [sys.executable, "-u", "-c", "import time; print('START', flush=True); time.sleep(0.5); print('ALL_TESTS_PASSED', flush=True)"]
))
# Verification criteria: must print ALL_TESTS_PASSED and exit with returncode 0
job1.check("marker", match=r"^ALL_TESTS_PASSED$", expect=Expect.PRESENT)

# 3. Define the second job: simulating another subsystem
job2 = mgr.add(Job(
    "sim_dma",
    [sys.executable, "-u", "-c", "import time; print('START', flush=True); time.sleep(0.3); print('ALL_TESTS_PASSED', flush=True)"]
))
job2.check("marker", match=r"^ALL_TESTS_PASSED$", expect=Expect.PRESENT)
```

> [!TIP]
> Subprocesses should always use unbuffered stdout (e.g., Python's `-u` flag or `print(..., flush=True)`), ensuring Wave receives log lines immediately as they are emitted.

---

## Step 2, Option A: Run in Terminal (Headless Mode)

For terminal execution, shell scripts, or CI/CD pipelines, pass the `--headless` flag:

```bash
rpk-wave work.py --headless
```

### Expected Terminal Output

Wave streams output lines in real time and prints an immutable final summary table after all jobs complete and cleanup finishes:

```text
[Wave] Starting 2 job(s); workers=2
[child sim_core #1] START
[child sim_dma #1] START
[child sim_dma #1] ALL_TESTS_PASSED
[child sim_core #1] ALL_TESTS_PASSED

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
sim_core                   DONE            PASS         0         0
  checks: marker: OK
sim_dma                    DONE            PASS         0         0
  checks: marker: OK
```

### Understanding States and Exit Codes

- **STATE**: Indicates subprocess execution status (`DONE` means the process exited normally with returncode 0).
- **RESULT**: Indicates verification criteria outcome (`PASS` means all declared checks passed and returncode was 0).
- **Exit Code**: When every job's Result is `PASS` or `N/A`, the process exits with `0`. If any job is `FAIL`, `ERROR`, or `INCOMPLETE`, it exits with `1`.

---

## Step 2, Option B: Run with the Local Web GUI

To inspect progress, view real-time logs, or control jobs interactively, omit `--headless` to launch the local browser GUI:

```bash
rpk-wave work.py
```

The terminal prints the local server URL and opens your default browser automatically:

```text
[Wave] GUI: http://127.0.0.1:54321 (Ctrl+C shuts down the core; closing the page does not)
```

### Three-Step GUI Walkthrough

1. **Review Preview**:
   - Upon loading, the status bar displays `Preview — review, then Start.`. At this stage, the scheduler **has not dispatched any processes**.
   - The left table lists both jobs with status `PENDING`.
2. **Explicitly Start Dispatch**:
   - Click the green **Start (2)** button in the top bar.
   - Dispatch begins; job statuses transition to `RUNNING`.
3. **Inspect Output and Details**:
   - Click a job row on the left to stream its logs in the right panel.
   - Upon completion, the left table updates **State** to `DONE` and **Result** to green `PASS`.
   - Double-click a job or switch to the **Details** tab on the right to inspect check criteria, line numbers, and evidence.

---

## Essential Contracts & Boundaries

1. **Separation of Preview and Dispatch**:
   - Loading a Python work file executes it inside a definition scope (`definition_scope`) using standard Python process privileges to discover jobs and validate configuration. This scope specifically prevents accidental calls to `start()` or `resume()` inside the work file; it is not a security sandbox. Dispatch only begins when you click **Start** in the GUI or invoke `--headless`.
2. **Closing the Browser Tab vs. Stopping the Core**:
   - Closing the browser tab **does not** stop running jobs. The core continues executing and logging in the background.
   - Reopening the URL reconnects to the running core. To shut down the core and terminate all child processes, press `Ctrl+C` in the terminal where `rpk-wave` was launched.
3. **Process Exit 0 Does Not Mean PASS**:
   - Returncode 0 only indicates process termination (`State=DONE`).
   - If a declared check fails (e.g., expected token was missing), `Result` is `FAIL` regardless of returncode 0.
   - A command with no checks that exits 0 yields `Result=N/A`.

---

## Next Steps

- **[Python Usage Scenarios](scenarios.md)**: Explore 8 comprehensive scenarios covering checks, warnings, data tracking, interactive input, resource quotas, and retries.
- **[GUI Operations Manual](gui.md)**: Master job filtering, batch operations, broadcast input, and the Settings modal.
- **[Python API Reference](reference/python.md)**: Review full method signatures, types, and enums.
