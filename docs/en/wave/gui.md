# Wave Web GUI Operations Manual

[繁體中文](../../zh/wave/gui.md) · [Documentation Index](../README.md) · [Quickstart](README.md)

Wave provides a zero-dependency, local browser-based GUI for real-time monitoring and process control. This document details its layout, workflows, batch operations, and configuration options.

---

## Launch and Connectivity

Start the GUI using the CLI:

```bash
# Load a work file and open the default browser automatically
rpk-wave work.py

# Specify local port without popping up a browser
rpk-wave work.py -p 8080 --no-browser

# Start with an empty workspace and load a file later
rpk-wave
```

The terminal displays the server URL:
```text
[Wave] GUI: http://127.0.0.1:54321 (Ctrl+C shuts down the core; closing the page does not)
```

> [!NOTE]
> **Loopback Binding & Security Boundaries**:
> The Wave GUI server is designed strictly as a local tool without multi-user password authentication. Consequently, the core **binds only to loopback interfaces (`127.0.0.1` or `localhost`)**; passing `0.0.0.0` raises `ValueError`. To inspect jobs running on a remote headless machine, forward ports securely via SSH (e.g., `ssh -L 54321:127.0.0.1:54321 user@remote`).

> [!IMPORTANT]
> **Lifecycle Boundaries**:
> - **Closing the browser tab**: Jobs continue executing in the background, and output logs continue streaming. Reopening the URL reconnects to the dashboard.
> - **Shutting down the core**: Pressing `Ctrl+C` in the terminal where `rpk-wave` was launched initiates graceful shutdown, terminates child processes, and cleans up resources.

---

## Dashboard Overview

```
+---------------------------------------------------------------------------------+
| Top Bar: Status dot, summary pills, Queue / All actions, Settings button        |
+----------------------------------------+----------------------------------------+
| Left Panel: Job Table & Batch Toolbar  | Right Panel: Log Viewer & Diagnostics  |
| - Filter by search & state/result pills| - Attempt selector (history read-only) |
| - Batch checkboxes & actions           | - Log / Details tab switch             |
| - Columns (State, Result, Warnings,    | - Substring log filter & auto-scroll   |
|   Priority, Elapsed, Track metrics)    | - Bottom stdin input & Ctrl+C button   |
+----------------------------------------+----------------------------------------+
```

---

## Core Operational Workflows

### 1. Loading Work Files and Preview Mode
- **Loading**: Pass `work.py` via CLI or click **Load Work File...** in the top bar.
- **Execution Privileges & Sandbox Clarification**:
  - Loading executes the Python definition file with **your current normal Python user permissions** to construct the `JobManager` and discover jobs.
  - `definition_scope` **only prevents calls to `mgr.start()` / `mgr.resume()`**; it is **not** a Python sandbox and does not restrict arbitrary file operations or child processes initiated by the script. **Only load trusted Python work files**.
  - Upon loading, the status bar displays `Preview — review, then Start.`, with all jobs listed in `PENDING` state.
  - **No processes are dispatched until you explicitly click Start**.

### 2. Dispatch Control (Start / Pause / Stop)
- **Start**: Click the green **Start N Jobs** button in the top bar to begin dispatch.
- **Pause Queue / Resume Queue**: Pauses new dispatch. Running jobs continue; pending jobs remain queued.
- **Stop All / Force Stop All**:
  - **Stop All**: Requests cancellation of pending jobs and stops active jobs according to their configured `stop_policy`.
  - **Force Stop All**: Sends immediate force-kill signals to all active child processes.

---

## Left Panel: Job Table & Filtering

### Search and Filters
- **Search jobs...**: Instant text search by job name.
- **State Filter Dropdown**: Filter by `All`, `PENDING`, `RUNNING`, `PROMPT DETECTED` (internal WAITING_INPUT), `STOPPING`, `FINISHED` (internal DONE), `FAILED`, `CANCELLED`.
- **Result Filter Dropdown**: Filter by `All`, `Undecided`, `PASS`, `FAIL`, `ERROR`, `INCOMPLETE`, `N/A`.
- **Has Warnings**: Checkbox to isolate jobs that emitted warnings.

### Batch Operations
Selecting checkboxes on job rows reveals the batch action toolbar:
- **Applicable Count Display**: Buttons display the exact applicable count vs. total checked count, for example:
  - `Stop/Cancel 2/3`: For example, with one active, one PENDING and one finished job, the active job is stopped, the pending job is cancelled, and the finished job is skipped. The tooltip lists cancellation/stop counts and skipped reasons.
  - `Rerun 1/3`: 1 finished job is scheduled for a new attempt.
  - `Send (2/3)…`: Transmits stdin to the 2 active jobs.
- **Skipped Tooltips**: Hovering over batch action buttons displays tooltips explaining which jobs were skipped and why.

---

## Right Panel: Logs, Diagnostics, and Stdin

### Attempt Selector & Historical Isolation
Use the dropdown in the header to switch between attempts:
- **`Current (#N · RESULT/STATE)`**: Active or latest attempt. Controls still depend on execution state: stdin/stop applies while active, and rerun becomes available after completion and cleanup.
- **`History #N · RESULT/STATE`**: Finished historical attempt.
- **Read-Only Guarantee**: When inspecting a historical attempt, all action buttons and stdin inputs are **disabled** to prevent accidental disruption of active runs.
- **Evicted History Handling**: If an inspected historical attempt is evicted when `max_history` is lowered, the selector displays `History unavailable`, controls remain disabled, and clicking **Back to Current** returns to the active attempt.

### Log and Details Views
- **Log Tab**: Live streaming output with case-insensitive **substring filtering** (`includes`, not regex) and toggleable auto-scroll.
- **Details Tab**:
  - **Checks List**: Inspection of individual check statuses (`OK`, `FAIL`, `ERROR`, `INCOMPLETE`).
  - **Evidence Jump**: Clicking a check failure or warning jumps directly to the matching line in the Log tab.
  - **Termination**: Source and reason for job termination.

### Stdin Input & Signals
Located at the bottom of the right panel:
- **Input Line**: Active when the job is `RUNNING` or `WAITING_INPUT`. Press `Enter` to transmit.
- **Transmission Feedback**: A successful transmission indicates data was written to the OS pipe; **it does not guarantee the external tool processed or accepted the command**.
- **Ctrl+C**: Transmits an interrupt signal to the active process.

---

## Settings Modal

Click **Settings** in the top bar to configure engine limits and preferences across three distinct sections:

### Section A: Execution Limits (Concurrency & Quotas)
*Scope: Active core session. Python definition files are never modified.*
- **Concurrent jobs (`max_workers`)**: Adjusts maximum simultaneous active workers. Lowering limits does not kill active jobs, but delays queued dispatch.
- **Fixed Quotas (`resources`)**: Adjusts named resource capacities.
- Click **Apply Execution Limits** to apply. **Note: Applying settings does not unpause a paused queue**.

### Section B: Memory Retention Policy
*Scope: Active core session.*
1. **Per-Job Log Retention (`max_log_lines`, default: 10000)**:
   - Sets ring buffer capacity for the selected job or all jobs.
   - Shrinking evicts older lines immediately; enlarging does not restore evicted lines. **Eviction does not alter line numbers, saved evidence, or Result verdicts**.
2. **Historical Attempts Retention (`max_history`, default: 20)**:
   - Caps completed historical attempts preserved in memory per job.

### Section C: Display Preferences & Table Columns
*Scope: Display preferences persist in browser `localStorage`; custom columns persist in core session.*
- **Log font size**: 10px to 15px.
- **Wrap long log lines**: Toggle line wrapping.
- **Monitor layout**: Switch between **Split (Left/Right)** and **Stack (Top/Bottom)** modes.
- **Visible Table Columns**: `Elapsed` and `Priority` start hidden and can be enabled here; existing saved preferences are preserved. Toggle custom `track()` columns and click **Apply Columns** to update the core session (`State`, `Result` and `Warnings` stay visible).
