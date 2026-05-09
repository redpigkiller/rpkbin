# Wave - Practical Batch Execution with Observability and Interactive Control

[![English](https://img.shields.io/badge/Language-English-blue.svg)](wave.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](wave_zh.md)

`rpkbin.wave` is a workflow layer built on top of `rpkbin.job_manager`.
It keeps the job manager focused on execution, and adds the parts you usually want
for real batch flows:

- a shared session for declaring jobs in a wave file
- parsers that extract structured state from logs
- hooks that react to logs, parsed data, or lifecycle events
- per-job events and parsed data snapshots
- a **live interactive TUI** (default) with a full-screen job dashboard and command bar
- a headless REPL for CI / pipe environments

It is designed for practical local automation flows where jobs may run for a
while, emit useful state through logs, and occasionally need human intervention.

### Architecture Overview

```
wave file (Python)          Runtime
┌──────────────────┐       ┌──────────────────────────────────────┐
│ session.configure│       │  Session                             │
│ session.add(job) │──────▶│  ├─ JobManager (scheduling + execution)│
│   ├─ parser      │       │  ├─ Timer Thread (elapsed hook driver)│
│   ├─ hook        │       │  └─ Events / Summary                 │
│   └─ stop policy │       │                                      │
└──────────────────┘       │  ┌─ TUI (default) or Headless REPL─┐   │
                           │  │ Dashboard │ Job Detail │ Cmd Bar│  │
                           │  └────────────────────────────────┘   │
                           └──────────────────────────────────────┘
```

### "I want to do X — what should I use?"

| Goal | What to use | Where |
|---|---|---|
| Run a shell command | `CmdJob(name, cmd)` | wave file |
| Run a Python function | `FuncJob(name, fn)` | wave file |
| Run an interactive CLI program | `PtyJob(name, cmd)` | wave file |
| Extract progress/state from logs | `job.add_parser(fn)` | wave file |
| Auto-kill on timeout | `Hook(when=Hook.elapsed_exceeds(s), action=Hook.action_kill())` | wave file |
| React to a specific log pattern | `Hook(when=Hook.log_matches(pattern), action=...)` | wave file |
| Notify on completion | `Hook(when=Hook.on_done(), action=Hook.action_emit(...))` | wave file |
| Send Ctrl-C to a PTY job | `key <job> ctrl-c` or TUI `F9` | REPL / TUI |
| Send an OS signal to a job | `signal <job> SIGTERM` | REPL / TUI |
| Graceful shutdown of interactive tool | `job.set_stop_policy(graceful_key=..., graceful_input=..., graceful_signal=...)` | wave file |
| Auto-retry on failure | `CmdJob(name, cmd, max_retries=3)` | wave file |
| Limit GPU / license concurrency | `session.configure(resources={"gpu": 2})` + `CmdJob(..., resources={"gpu": 1})` | wave file |
| Write report after batch completes | `session.on_finish(callback)` | wave file |
| Cancel jobs by tag | `session.cancel_group(tag)` | REPL / TUI / wave file |
| Live-monitor all jobs | Use TUI (default mode) | CLI: omit `--no-tui` |
| Run unattended in CI | `--no-tui` | CLI |

---

## Quick Start

The recommended pattern is:

1. Write a Python wave file.
2. Configure the shared `session`.
3. Add jobs up front.
4. Run the file with `rpk-wave run ...` or `runner.run(...)`.

Smallest complete wave file:

```python
from rpkbin.wave import session, CmdJob

session.configure(max_workers=1)
session.add(CmdJob("hello", "python -c \"print('hello from wave')\""))
```

Run it with:

```powershell
rpk-wave run path\to\hello_wave.py --no-tui
```

```python
from rpkbin.wave import session, CmdJob, Hook

session.configure(max_workers=2)

build = CmdJob("build", "python build.py")
test = CmdJob("test suite", "pytest -q")

def parse_state(line: str) -> dict:
    if line.startswith("STATE="):
        return {"state": line.split("=", 1)[1].strip()}
    return {}

test.add_parser(parse_state)
test.add_hook(
    Hook(
        when=Hook.elapsed_exceeds(300),
        action=Hook.action_kill(),
        policy="once",
    )
)

session.add(build)
session.add(test)
```

Run it with:

```python
from rpkbin.wave.runner import run

run("path/to/my_wave.py")           # TUI (default)
run("path/to/my_wave.py", no_tui=True)  # headless
```

Or from the CLI:

```powershell
rpk-wave run path\to\my_wave.py            # launches TUI
rpk-wave run path\to\my_wave.py --no-tui   # headless
```

Why this pattern:

- the wave file stays declarative
- `runner.run(...)` resets the shared session before loading the file
- CLI overrides such as `--workers` apply after the file is loaded
- headless mode gives you a simple interactive control surface without changing
  the underlying execution model

About `CmdJob(...)` command forms:

- the common form is a shell command string, for example `CmdJob("build", "python build.py")`
- if you are unsure how command execution behaves, treat it like a shell-driven command job and prefer simple command strings first
- for shell-specific details, see the underlying job manager command-job behavior in the job manager docs

---

## Core Concepts

### 1. Session

`session` is the shared registration surface for one batch run.

- `session.configure(...)` sets manager-level config before the run starts
- `session.add(job)` registers jobs
- `session.emit(tag, message)` records batch-level user events
- `session.pause()`, `session.resume()` pause/resume the job manager
- `session.cancel_group(tag)` cancels jobs by metadata tags
- `session.wait(...)` waits for session idleness or a specific job
- `session.on_finish(...)`, `session.on_done(...)`, `session.on_fail(...)` register batch-level lifecycle callbacks (scoped to the current run; cleared by `session.reset()`)
- `session.summary()` returns a batch-level summary snapshot
- `runner.run(...)` loads the wave file, starts the session, and drains it

In practice, a wave file is usually setup code rather than a long-running main
program.

> [!IMPORTANT]
> **Dynamic Job Addition Limits**
> `session.add()` is only valid while the session is active. Once the session has finished or finalized, calling `session.add()` will raise a `RuntimeError`. Do not attempt to add new jobs from session-level callbacks like `on_finish()`, `on_done()`, or `on_fail()`. If you need to add jobs dynamically, do so from job-level callbacks before the session begins its final shutdown.

### 2. Job Types

Wave extends job manager jobs with observability features.

- `CmdJob` / `WaveCmdJob`
  - shell command job (PIPE-based stdin/stdout)
  - supports log parsers, log-driven hooks, stdin input, and OS signals
  - default stop policy: `graceful_signal=SIGINT`
- `PtyJob` / `PtyCmdJob`
  - shell command job inside a **pseudo-terminal** (PTY)
  - child process sees `isatty() == True`; suitable for interactive programs
  - supports log parsers, hooks, terminal control keys (`key`), stdin input, and OS signals
  - default stop policy: `graceful_key="ctrl-c"`
  - Linux / macOS only; on Windows, construction succeeds but execution fails with a clear error
- `FuncJob` / `WaveFuncJob`
  - Python callable job
  - supports hooks, events, and parsed data updates
  - **Note**: cancellation only marks the job as `cancelled`; the underlying Python function cannot be forcefully stopped and will continue until it returns naturally.

Wave-specific job features include:

- `job.add_parser(fn)`
- `job.add_hook(hook)`
- `job.parsed_data` / `job.peek_data()`
- `job.events` / `job.peek_events()`
- `job.emit(tag, message)`
- `job.skip()`
- `job.is_skipped`
- `job.tags`
- `job.set_progress(value)`
- `job.retry_count`

#### CmdJob vs PtyJob: When to Use Which

| | `CmdJob` | `PtyJob` |
|---|---|---|
| Child I/O | PIPE (stdin/stdout) | PTY (pseudo-terminal) |
| `isatty()` | `False` | `True` |
| Use case | Batch scripts, builds, tests | Interactive REPLs, `read`-based scripts |
| Ctrl-C behavior | `signal <job> SIGINT` (OS signal) | `key <job> ctrl-c` (terminal key → kernel SIGINT) |
| Default stop | `graceful_signal=SIGINT` | `graceful_key="ctrl-c"` |
| Platform | All (Windows, Linux, macOS) | Linux / macOS only |

Use `CmdJob` unless the program specifically needs `isatty() == True` or terminal control key semantics.

#### `input` vs `key` vs `signal`

| Command | What it does | Works on |
|---|---|---|
| `input <job> <text>` | Write text to stdin / PTY master (data channel) | `CmdJob`, `PtyJob` |
| `key <job> <key>` | Write a terminal control byte to the PTY master | `PtyJob` only |
| `signal <job> <sig>` | Send an OS signal to the process group | `CmdJob`, `PtyJob` |

`key ctrl-c` writes `\x03` to the PTY. Because `PtyJob` uses `pty.fork()` to establish a proper controlling terminal, the kernel line discipline translates this byte into a real SIGINT for the child's foreground process group — just like pressing Ctrl-C in a real terminal.

`signal SIGINT` sends the signal directly via `os.killpg()`, bypassing the terminal driver entirely. Use `signal` when you need to send signals not mapped to terminal keys (e.g. `SIGTERM`, `SIGUSR1`).

### 3. Hooks

Hooks combine:

- a condition (`when`)
- an action
- a firing policy (`once`, `always`, `every_n`)

Common conditions:

- `Hook.log_matches(pattern)`
- `Hook.data_equals(key, value)`
- `Hook.elapsed_exceeds(seconds)`
- `Hook.on_start()`
- `Hook.on_done()`
- `Hook.on_fail()`
- `Hook.on_retry()` (context: `attempt`)
- `Hook.on_cancel()` (context: `skipped`)

Common actions:

- `Hook.action_kill()`
- `Hook.action_request_stop(force=False)`
- `Hook.action_send_signal(sig)`
- `Hook.action_send_input(text)`
- `Hook.action_send_key(key)`
- `Hook.action_emit(tag, message)`
- `Hook.action_set_data(key, value)`
- `Hook.action_chain(...)`

> [!WARNING]
> **Avoid Blocking Operations in Callbacks**
> Parsers, hook actions, and log-style callbacks (`on_log`, `on_done`, etc.) typically run within job worker threads. **Never** call blocking operations like `session.wait()`, `manager.wait()`, or long `time.sleep()` inside these functions. Doing so can lead to deadlocks or cause the entire batch to hang while waiting for itself to finish.

> [!NOTE]
> **Synchronous Hook Chaining**
> Hook actions are reactive and synchronous. If an action uses `Hook.action_set_data()` and that update satisfies another `data_equals` hook, the second hook will trigger immediately in the same call stack. Be careful to avoid creating infinite feedback loops, especially when using `policy="always"`.

Rule of thumb:

- use Wave hooks for reactions based on logs, parsed data, elapsed time, or Wave lifecycle events
- **Note**: `elapsed_exceeds` with `policy="always"` will trigger repeatedly under timer polling; `policy="once"` is recommended for most use cases.
- use plain job manager callbacks such as `on_done(...)` / `on_fail(...)` for simple completion/failure notifications

### 4. Job Outcome Semantics

Wave keeps a small distinction between "failed" and "intentionally skipped":

- `done`
  - job completed successfully
- `failed`
  - job failed normally
- `cancelled`
  - job was cancelled or force-stopped
- `skipped`
  - represented internally as a cancelled Wave job with `is_skipped=True`

This matters for reporting and exit codes:

- skipped jobs do not make the batch fail
- failed jobs and non-skipped cancelled jobs do

In headless `status` output, a skipped job will usually look like:

```text
ID       NAME                           STATUS       STATE            SKIPPED
-----------------------------------------------------------------------------
0f3a21b9 compile docs                   cancelled                      yes
```

---

## Common Workflows

### Parse State from Logs

```python
job = CmdJob("sim", "python run_sim.py")

def parse_progress(line: str) -> dict:
    if "PROGRESS=" in line:
        return {"progress": line.split("=", 1)[1].strip()}
    return {}

job.add_parser(parse_progress)
session.add(job)
```

### Trigger a Hook on Parsed Data

```python
job.add_hook(
    Hook(
        when=Hook.data_equals("progress", "done"),
        action=Hook.action_emit("phase", "simulation finished"),
        policy="once",
    )
)
```

### Stop a Long-Running Job Gracefully

```python
import signal

job = CmdJob("interactive shell", "python interactive_tool.py")
job.set_stop_policy(
    graceful_input="exit\n",
    graceful_signal=signal.SIGINT,
    graceful_timeout=5.0,
)

session.add(job)
```

This lets Wave prefer a graceful stop before falling back to force cancel.

### Run Batch-Level Finalization

```python
from pathlib import Path

out_dir = Path("out")

def finalize(sess):
    summary = sess.summary()
    out_dir.mkdir(exist_ok=True)
    (out_dir / "summary.txt").write_text(
        f"outcome={summary['outcome']}\n"
        f"failed={','.join(summary['failed_names'])}\n",
        encoding="utf-8",
    )

session.on_finish(finalize)
```

This is useful for final memo generation, artifact movement, and end-of-batch reporting.

### Run Headless with Interactive Control

```powershell
rpk-wave run path\to\my_wave.py --no-tui
```

If the terminal is interactive, Wave opens a REPL for inspection and control.
If the terminal is non-interactive, it simply runs to completion.

Typical examples:

- interactive PowerShell / cmd / terminal window -> REPL opens
- CI, redirected stdin, or piped execution -> no REPL; Wave just runs

---

## Wave File Authoring Guide

A wave file is a regular Python file. The Wave runner imports it as a module and
executes its top-level code. After execution completes, all jobs registered via
`session.add()` are scheduled.

> [!TIP]
> A wave file's job is to **declare** and **configure**. All long-running work happens inside jobs — don't put heavy logic at the top level of the wave file.

### Minimal Template

```python
# my_wave.py
from rpkbin.wave import session, CmdJob

session.configure(max_workers=2)

session.add(CmdJob("hello", "echo Hello from Wave"))
session.add(CmdJob("world", "echo World"))
```

```powershell
# Launch TUI
rpk-wave run my_wave.py

# Or headless
rpk-wave run my_wave.py --no-tui
```

### CmdJob + Parser + Hook

An example that parses progress from logs and auto-kills on timeout:

```python
# sim_wave.py
import re
from rpkbin.wave import session, CmdJob, Hook

session.configure(max_workers=4)

sim = CmdJob("simulation", "python run_sim.py --steps=10000")

# -- Parser: extract progress from log --
# Assumes run_sim.py prints "PROGRESS: 42.0%"
def parse_progress(line: str) -> dict:
    m = re.search(r"PROGRESS:\s*([\d.]+)%", line)
    if m:
        pct = float(m.group(1))
        sim.set_progress(pct)           # updates Dashboard progress bar
        return {"progress_pct": m.group(1)}  # written to parsed_data (visible in TUI DATA tab)
    return {}

sim.add_parser(parse_progress)

# -- Hook: kill after 5 minutes --
sim.add_hook(
    Hook(
        when=Hook.elapsed_exceeds(300),
        action=Hook.action_kill(),
        policy="once",
    )
)

# -- Hook: emit event on completion --
sim.add_hook(
    Hook(
        when=Hook.on_done(),
        action=Hook.action_emit("milestone", "simulation done"),
        policy="once",
    )
)

session.add(sim)
```

### FuncJob

`FuncJob` runs a Python callable. It has no log stream, so state is reported via
`emit()` and `update_parsed_data()`:

```python
# func_wave.py
import time
from rpkbin.wave import session, FuncJob

session.configure(max_workers=2)

def train_model(job):
    for epoch in range(10):
        time.sleep(1)
        job.set_progress((epoch + 1) * 10)
        job.update_parsed_data({"epoch": str(epoch + 1)})
        if job.is_cancelled:
            return
    job.emit("result", "training complete")

session.add(FuncJob("train", train_model))
```

> [!NOTE]
> The `FuncJob` callable receives the job itself as its first argument. Use `job.set_progress()`, `job.emit()`, and `job.update_parsed_data()` to report state, and `job.is_cancelled` to check for cancellation.

### Resources, Priority, Tags

Use `resources` for concurrency limits, `priority` for scheduling order, and `tags` for group operations:

```python
# resource_wave.py
from rpkbin.wave import session, CmdJob

session.configure(
    max_workers=8,
    resources={"gpu": 2, "license": 1},   # system has 2 GPUs, 1 license
)

# This job needs 1 GPU
build_a = CmdJob("build-A", "python build.py --variant=A",
                 resources={"gpu": 1}, priority=10, tags={"build"})

# This job needs 2 GPUs (will occupy all GPUs exclusively)
build_b = CmdJob("build-B", "python build.py --variant=B",
                 resources={"gpu": 2}, priority=5, tags={"build"})

# This job needs a license
lint = CmdJob("lint", "python lint.py",
              resources={"license": 1}, tags={"check"})

session.add(build_a)
session.add(build_b)
session.add(lint)
```

- Higher `priority` means scheduled first (default 0)
- If a job declares a resource key that the manager doesn't recognize, a warning is logged and the job will never be scheduled
- Use `session.cancel_group("build")` in TUI/REPL to cancel all jobs with the `build` tag

### Retry

```python
# retry_wave.py
from rpkbin.wave import session, CmdJob, Hook

session.configure(max_workers=2)

flaky = CmdJob("flaky-test", "python test_flaky.py", max_retries=3)

# Log an event on each retry
flaky.add_hook(
    Hook(
        when=Hook.on_retry(),
        action=Hook.action_emit("retry", "retrying..."),
        policy="always",
    )
)

session.add(flaky)
```

`max_retries=3` means up to 3 retries (4 total attempts including the original).
Check `job.retry_count` to see how many retries have occurred.

### Stop Policy (Graceful Shutdown)

For long-running jobs that need cleanup, configure a stop policy so Wave tries a
gentle shutdown first:

```python
# stop_policy_wave.py
import signal
from rpkbin.wave import session, CmdJob

session.configure(max_workers=2)

server = CmdJob("server", "python my_server.py")
server.set_stop_policy(
    graceful_input="shutdown\n",     # send stdin command first
    graceful_signal=signal.SIGINT,   # then send SIGINT
    graceful_timeout=10.0,           # wait 10s; force-kill if still running
)

session.add(server)
```

When you type `stop server` in TUI, Wave executes these steps in order.

### Session Callbacks (Batch-Level Finalization)

Run finalization logic after all jobs complete:

```python
# callback_wave.py
from pathlib import Path
from rpkbin.wave import session, CmdJob

session.configure(max_workers=2)

session.add(CmdJob("step1", "echo step1 done"))
session.add(CmdJob("step2", "echo step2 done"))

# on_finish: runs regardless of success or failure
def write_report(sess):
    summary = sess.summary()
    Path("report.txt").write_text(
        f"outcome={summary['outcome']}\n"
        f"done={summary['done']}, failed={summary['failed']}\n",
        encoding="utf-8",
    )

# on_done: runs only when all jobs succeed
def celebrate(sess):
    sess.emit("batch", "All jobs passed!")

# on_fail: runs only when some jobs failed
def alert(sess):
    names = sess.summary()["failed_names"]
    sess.emit("batch", f"Failed jobs: {', '.join(names)}")

session.on_finish(write_report)
session.on_done(celebrate)
session.on_fail(alert)
```

> [!WARNING]
> **Do not** call `session.add()` inside `on_finish` / `on_done` / `on_fail` — the session is already finalizing. If you need to add jobs dynamically, do so from job-level callbacks (e.g. `on_done` hooks) before the session begins shutdown.

### TUI Tips

Once your wave file is ready, launch with TUI for interactive monitoring:

```powershell
rpk-wave run my_wave.py
```

Quick reference:

| Goal | How |
|---|---|
| See all job status | `F1` to Dashboard |
| Preview a job's logs | `F1`, move the Dashboard highlight; log preview is shown on the right |
| View full job detail | Select row in Dashboard → `Enter`; or type `show <job>` / `logs <job>` |
| View a job's parsed data | Type `data <job>`; or enter JOB DETAIL and switch to DATA |
| View a job's events | Type `events <job>`; or enter JOB DETAIL and switch to EVENTS |
| Switch detail jobs | In JOB DETAIL, press `[` / `]`; use `{` / `}` for running jobs |
| Pause / resume dispatch | Type `pause` / `resume`; running jobs continue |
| Stop a job gracefully | Type `stop <job>` in the command bar |
| Force-cancel a job | Type `cancel <job>` in the command bar |
| Skip a pending job | Type `skip <job>` in the command bar |
| See help | `F4` or type `help` |
| Quit | `Ctrl+C` (warns if jobs active; double-tap to force quit) |

---

## TUI (Interactive Interface)

The TUI is the default mode when running `rpk-wave run` from an interactive terminal.
It is built with [Textual](https://github.com/Textualize/textual) and requires
`pip install -e .[wave]`.

### Layout

```
┌─ WAVE ─ my_flow.py ───── 2/8 running  OK 1 done  WAIT 3 pending  FAIL 0 failed ─┐
│ [DASHBOARD] [JOB DETAIL] [SYSTEM LOG] [HELP]                                     │
│─────────────────────────────────────────────────────────────────────────────────│
│  Name          Status     Elapsed   Progress  Exit Code  Tags │ build [0f3a21b9]│
│▶ build         RUNNING    00:02:30   42%                  gpu │ INFO compile... │
│  lint          DONE       00:01:12             0              │ WARN retry...   │
│  test-unit     PENDING                                      test│               │
├─ wave>  _                        [Tab: autocomplete]  [Enter: submit]           │
│ [F1] Dashboard  [F2] Job Detail  [F3] System Log  [Ctrl+C] Quit                 │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Tabs

- **DASHBOARD** — split view with a live job table on the left and a log preview for the highlighted job on the right.
  - Default table columns: Name / ID / Status / Elapsed / Progress / Retries / Exit Code / Tags.
  - Dashboard columns can be customized from the wave file with `session.configure_tui(...)`.
  - Elapsed is updated every second for running jobs.
  - The right-side log preview follows the highlighted row, so you can scan logs without entering JOB DETAIL.
  - Press Enter on any row to open that job in **JOB DETAIL**.
- **JOB DETAIL** — full-page split view for one selected job.
  - Header: current job name, position, status, elapsed time, progress, exit code when available, and detail navigation hints.
  - Left 60%: streaming log output (append-only, no flicker).
  - Right 40%: five sub-tabs:
    - **INFO** — compact job metadata, including id, status, state, skip flag, and stop policy.
    - **DATA** — key/value table of `job.peek_data()`, updated by upsert. Empty jobs show `(no parsed data)`.
    - **EVENTS** — user-emitted events (`source="user"`) in chronological order.
    - **SYSTEM** — system-emitted events (`source="system"`), e.g. `parser_error`, `hook_error`; parser/hook errors are highlighted in red and include exception details.
    - **TERMINAL** — append-only PTY output for `PtyJob` jobs. For non-PTY jobs, shows a "not supported" message. This is a fake-terminal view, not a full terminal emulator.
- **SYSTEM LOG** — session-level events and the output of all command-bar commands.
- **HELP** — inline keyboard shortcut and command reference.

### Navigating to a Job's Detail

1. **Move the DASHBOARD highlight** — preview that job's log on the right side of F1.
2. **Enter key on DASHBOARD row** — switch directly to JOB DETAIL for that job.
3. **Command bar** — type `show <job>`, `logs <job>`, `data <job>`, or `events <job>` to switch and load any job by name, full id, or unique id prefix.

### Dashboard Columns

Use `session.configure_tui(...)` in the wave file to keep the dashboard focused
on the fields that matter for your flow:

```python
session.configure_tui(dashboard_columns=[
    "name",
    "status",
    {"label": "Final", "data": "FINAL_RESULT"},
    "exit_code",
    "tags",
])
```

Built-in columns are `name`, `id`, `status`, `elapsed`, `progress`, `retries`,
`exit_code`, and `tags`. Parsed-data columns use
`{"label": "...", "data": "KEY"}` and render the current value from
`job.peek_data()`. Unknown built-ins or malformed column specs fail early with a
clear exception when the wave file is loaded.

### Command Bar

Inside **JOB DETAIL**, press `[` / `]` to move between jobs, or `{` / `}` to move between running jobs. The Dashboard highlight and log preview follow the selected detail job.
In TUI mode, `show <job>` / `logs <job>` / `data <job>` / `events <job>` open **JOB DETAIL** directly; `show`, `data`, and `events` also switch to their matching detail sub-tabs.

The bottom `wave>` input is always visible regardless of which tab is active.
It accepts the same commands as the headless REPL (see below).
All `print()` output from commands is redirected to the **SYSTEM LOG** tab so
the TUI layout stays intact.

Additional TUI-only keyboard bindings:

| Key | Action |
| --- | --- |
| `F1` | Switch to DASHBOARD |
| `F2` | Switch to JOB DETAIL |
| `F3` | Switch to SYSTEM LOG |
| `F4` | Switch to HELP |
| `F8` | Focus Command Bar and pre-fill `input . ` (TERMINAL tab only) |
| `F9` | Send Ctrl-C to the current PTY job (TERMINAL tab only) |
| `F10` | Send Ctrl-D to the current PTY job (TERMINAL tab only) |
| `:` | Focus Command Bar (Vim style) |
| `Esc` | Leave Command Bar, return focus to active panel |
| `Enter` (on DASHBOARD row) | Open JOB DETAIL for selected job |
| `[` / `]` (in JOB DETAIL) | Open previous / next job |
| `{` / `}` (in JOB DETAIL) | Open previous / next running job |
| `Tab` (in Command Bar) | Auto-complete commands and job names |
| `↑` / `↓` (in Command Bar) | Browse command history |
| `Shift + Drag` | Select text (Right-click or Ctrl+Shift+C to copy, **never Ctrl+C**) |
| `Ctrl+C` | Quit (warns if jobs active; double-tap within 3s to force quit) |

### Job Status Colors

| Status | Color |
| --- | --- |
| RUNNING | green |
| DONE | cyan |
| PENDING | yellow |
| FAILED | red |
| CANCELLED | grey |

---

## Headless REPL

The headless REPL is a better input interface for a running batch.
It is not a second execution model. Jobs still run through the normal
session -> manager -> job_manager path.

When jobs are still active, you can inspect and control them.
When all jobs are complete, the REPL prints a summary and stays open so you can
inspect results before leaving with `exit`.

### Commands

- `help`
  - show available commands
- `status`
  - print a table of all jobs
- `show <job>`
  - print a compact summary for one job, including exit code, short error text, and parser/hook error counts when present
- `logs <job> [n]`
  - print the last `n` log lines, default `50`
- `data <job>`
  - print `parsed_data`
- `events <job>`
  - print emitted events
- `pause`
  - pause dispatch of pending jobs; running jobs continue
- `resume`
  - resume dispatch after `pause`
- `stop <job>`
  - prefer graceful stop, then force-stop after the configured timeout
- `stop -g <job>` / `stop --graceful <job>`
  - graceful stop only
- `stop -f <job>` / `stop --force <job>`
  - immediate force stop
- `stop --all`
  - request graceful stop for all active jobs
- `stop --group <tag>`
  - request graceful stop for all active jobs carrying `tag`
- `cancel <job>`
  - force-cancel one active job immediately
- `cancel --all`
  - force-cancel all active jobs
- `cancel --group <tag>`
  - force-cancel all active jobs carrying `tag`
- `skip <job>`
  - skip a pending Wave job
- `input <job> <text>`
  - send stdin text to a running job (supports `\n`, `\r`, `\t` escape sequences)
- `signal <job> <sig>`
  - send an OS signal to a running job
- `watch status`
  - poll and reprint status until `Ctrl+C`
- `watch logs <job> [n]`
  - poll and reprint the log tail until `Ctrl+C`
- `exit`
  - leave the REPL only when no jobs are still active
- `exit --stop`
  - request stop for active jobs, then leave the REPL
- `exit --force`
  - force-cancel active jobs, then leave the REPL

`<job>` accepts a unique job name, a full job id, or a unique id prefix shown by `status` / `show`.
In TUI mode, if a JOB DETAIL is open, the special shorthand `.` can be used to target the currently selected job.
If multiple jobs share the same name, Wave refuses the ambiguous name and asks you to use an id.
The ambiguous-name message prints each matching job's full id and status, so you can copy an exact id or use an 8+ character unique prefix.

Names containing spaces must be quoted:

```text
logs "test suite"
stop -g "sim run 1"
input "interactive shell" "exit\n"
```

### Watch Behavior

`watch` is intentionally simple:

- it uses polling
- the refresh interval is currently fixed at about 1 second
- it does not do advanced terminal redraw
- pressing `Ctrl+C` stops watching and returns to the REPL

This keeps the implementation small and robust.

### Leaving the REPL

Wave keeps REPL exit behavior explicit:

- `exit`
  - only works when no jobs are still active
- `exit --stop`
  - requests stop for active jobs, then leaves the REPL
- `exit --force`
  - force-cancels active jobs, then leaves the REPL

There is intentionally no detach/background mode.
This avoids leaving work running outside the user's awareness and keeps process
ownership simple.

After leaving the REPL, Wave still completes normal session shutdown before the
command returns.

---

## Stop Policy Semantics

Wave supports per-job stop policy configuration for common interactive cases.

```python
import signal

# CmdJob: use stdin + OS signal
job.set_stop_policy(
    graceful_input="exit\n",
    graceful_signal=signal.SIGINT,
    graceful_timeout=5.0,
)

# PtyJob: use terminal control key (default is ctrl-c)
pty_job.set_stop_policy(
    graceful_key="ctrl-c",       # terminal key → kernel SIGINT
    graceful_timeout=5.0,
)
```

Meaning:

- `graceful_key`
  - terminal control key written to the PTY master (PtyJob only)
  - e.g. `"ctrl-c"` → `\x03` → kernel SIGINT via line discipline
- `graceful_input`
  - text sent to stdin / PTY master
- `graceful_signal`
  - OS signal sent as part of graceful shutdown
- `graceful_timeout`
  - if the job is still running after that delay, Wave falls back to force cancel

When multiple steps are configured, Wave applies them in this order:

1. send terminal key (if `graceful_key` is set; PTY jobs only)
2. send input (if `graceful_input` is set)
3. send signal (if `graceful_signal` is set)
4. wait for the graceful timeout
5. force-cancel if the job is still active

Each step is attempted regardless of whether the previous step succeeded.
If a step fails (e.g. PTY not available), the failure is logged and the next step is tried.

Behavior by command:

- `stop <job>`
  - uses the configured graceful policy when available
  - falls back to force stop after timeout
- `stop -g <job>`
  - graceful only
  - never force stops automatically
- `stop -f <job>`
  - immediate force cancel

For jobs without graceful stop support:

- `stop <job>` behaves like force stop
- `stop -g <job>` reports that graceful stop is unsupported

---

## API Reference

### Session

| Method | Description |
| --- | --- |
| `session.configure(max_workers=..., resources=..., log_dir=..., timeout=...)` | Configure the Wave session before it starts. `timeout` is a session-wide time limit. |
| `session.configure_tui(dashboard_columns=...)` | Configure TUI presentation. Dashboard columns support built-ins plus parsed-data columns such as `{"label": "Final", "data": "FINAL_RESULT"}`. |
| `session.add(job, timeout=None)` | Register a job. If already started, dispatch immediately. `timeout` is only supported for Wave jobs; plain scheduler jobs will issue a warning and ignore it. **Note**: Raises `RuntimeError` if called after session finalization. |
| `session.emit(tag, message)` / `session.peek_events()` | Append and inspect batch-level events. User-created events are marked with `source="user"`; Wave's own lifecycle events use `source="system"`. |
| `session.pause()` / `session.resume()` | Pause or resume the job manager's dispatch loop. |
| `session.cancel_group(tag)` | Cancel all active and pending jobs that have *tag* in their tags set. |
| `session.wait(timeout=None, *, job=None)` | Wait until the session is idle, or until a specific job completes. |
| `session.on_finish(cb)` | Run `cb(session)` once after the whole batch fully settles. |
| `session.on_done(cb)` / `session.on_fail(cb)` | Run `cb(session)` once for successful or failed batch completion (per-session; cleared by `reset()`). |
| `session.summary()` | Return a batch-level summary snapshot including counts, names, outcome, and exit code. |
| `session.jobs()` | Return all known jobs. |
| `session.running()` / `session.pending()` / `session.done()` | Return jobs filtered by state. |
| `session.failed(include_skipped=False)` | Return failed jobs and non-skipped cancelled jobs. |
| `session.skipped()` | Return intentionally skipped jobs. |
| `session.reset()` | Reset the shared session to a clean state. |

### Wave Jobs

| Property / Method | Description |
| --- | --- |
| `parsed_data` / `peek_data()` | Parsed structured state from logs. |
| `events` / `peek_events()` | Emitted event history. |
| `add_parser(fn)` | Register a parser called for each log line. |
| `add_hook(hook)` | Register a Wave hook. |
| `emit(tag, message, source="user")` | Append a named event to the job event stream. `source` defaults to `"user"` for events created by application code; Wave uses `"system"` for internal error events such as `parser_error` and `hook_error`. Visible in the TUI JOB DETAIL Events / System tabs. |
| `skip()` | Skip a pending Wave job. |
| `is_skipped` | Whether the job was intentionally skipped. |
| `tags` | Set of tags associated with the job. |
| `set_progress(value)` | Manually update job progress (0-100). |
| `retry_count` | Number of retry attempts made so far. |
| `set_stop_policy(...)` | Configure graceful key/input/signal stop behavior. |

### PtyJob-Specific API

| Property / Method | Description |
| --- | --- |
| `supports_pty` | `True` for `PtyJob` / `PtyCmdJob`. Used by TUI to enable the TERMINAL tab. |
| `send_key(key)` | Send a terminal control key to the PTY (e.g. `"ctrl-c"`, `"ctrl-d"`). |
| `send_input(text)` | Write text to the PTY master fd (data channel). |
| `send_signal(signum)` | Send an OS signal to the process group. |

### CLI

| Command | Description |
| --- | --- |
| `rpk-wave run <wave_file>` | Run a wave file with the TUI (default when terminal is interactive). |
| `rpk-wave run <wave_file> --no-tui` | Run headless; open REPL when stdin is interactive. |
| `rpk-wave run <wave_file> --workers N` | Override `max_workers` from the wave file. |

---

## Exit Codes

`rpk-wave run ...` returns a shell-style exit code based on batch outcome.

- `0`
  - all jobs completed successfully
  - or jobs were intentionally skipped
- `1`
  - at least one job failed
  - or a non-skipped job ended as cancelled

Parser output and emitted events do not affect the process exit code.

---

## Batch-Level Events and Summary

Wave now has two event layers:

- job events
  - recorded with `job.emit(...)`
  - represent important events for one job
- session events
  - recorded with `session.emit(...)`
  - represent important events for the whole batch

Typical uses for session events:

- record a final report path
- note that a cleanup step ran
- record a batch-level decision or fallback

`session.summary()` is the companion read API for end-of-batch logic.
It provides a stable snapshot with fields such as:

- `outcome`
- `exit_code`
- `done`, `failed`, `cancelled`, `skipped`
- `done_names`, `failed_names`, `skipped_names`
- `duration_s`

---

## FAQ & Troubleshooting

### Execution & Environment

| Problem | Cause & Solution |
|---|---|
| REPL didn't open with `--no-tui` | REPL only opens when stdin is interactive. CI, pipes, and redirected execution run straight through. |
| TUI fails to start | Make sure `pip install -e .[wave]` was run (requires `textual`). |
| `exit` refuses to leave | Jobs are still active. Use `exit --stop` or `exit --force`. |

### Parser & Hook

| Problem | Cause & Solution |
|---|---|
| Parser didn't fire | Confirm the job is a `CmdJob`, the log line actually matches, and the parser returns a non-empty `dict`. |
| Where are hook/parser errors? | Wave emits `parser_error` / `hook_error` events with exception type, message, input/action context, and a short traceback. Check `job.peek_events()`, `show <job>`, or the TUI SYSTEM sub-tab. |
| `stop -g` says unsupported | The job has no `set_stop_policy(...)` configured. |

### Results & Exit Code

| Problem | Cause & Solution |
|---|---|
| Stopped a job manually, exit code is still `1` | Non-skipped cancelled jobs count as failure. Use `skip` instead of `stop` for intentional skips. |
| Commands fail with spaces in job name | REPL uses shell-style parsing; quote the name: `logs "test suite"`. |
| Can I define new jobs from the REPL? | No. Jobs are declared in the wave file; REPL is for inspection and control. |

---

## Suggested Usage

Wave is strongest when you:

- define jobs in the wave file up front
- use hooks for automatic reactions
- use parsers to turn logs into structured state
- use the TUI or headless REPL for inspection and control during execution
- keep interactive commands simple and explicit

This keeps the code path understandable and avoids turning the CLI into a full
shell or ad-hoc job authoring environment.
