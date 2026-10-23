# Wave Python API Reference

[繁體中文](../../../zh/wave/reference/python.md) · [Documentation Index](../../README.md) · [Usage Scenarios](../scenarios.md)

This document provides technical specifications for Wave classes, methods, parameters, types, enums, and return structures.

Import statement:
```python
from rpkbin.wave import (
    Job,
    JobManager,
    JobStatus,
    Result,
    CheckState,
    Expect,
    StopPolicy,
    Event,
    Report,
)
```

---

## Core Scheduler: `JobManager`

`JobManager` manages command queuing, concurrency limits, resource quotas, streaming log observers, and system snapshots.

### Constructor
```python
JobManager(
    *,
    max_workers: int = 4,
    resources: dict[str, int] | None = None,
    log_dir: str | Path | None = None,
    columns: list[str] | tuple[str, ...] | None = None,
    max_history: int = 20,
)
```

- **`max_workers`** (`int`, default 4): Maximum simultaneous active worker slots. Must be $\ge 1$.
- **`resources`** (`dict[str, int] | None`, default None): Capacities of named resources (e.g., `{"gpu": 2}`). Values must be non-negative integers.
- **`log_dir`** (`str | Path | None`, default None): Directory where `{job.id}.log` files are automatically written.
- **`columns`** (`list[str] | None`, default None): List of custom metric column names enabled by default.
- **`max_history`** (`int`, default 20): Capacity limit of completed attempts preserved in memory per job. Must be $\ge 1$.

### Methods

#### `add(job: Job) -> Job`
Registers a job with the manager.
- **Restrictions**: Cannot register the same job twice; cannot register a job belonging to another manager.
- **Returns**: The registered `Job` instance.

#### `add_many(jobs: Sequence[Job]) -> list[Job]`
Batch registration of multiple jobs.

#### `get_job(job_id_or_name: str | Job) -> Job | None`
Looks up a job by ID or name. Raises `ValueError` if a name is ambiguous.

#### `validate() -> None`
Validates job definitions and freezes configuration; dispatch begins only after successful validation. Raises an exception if any definition is invalid.

#### `start() -> None`
Begins job scheduling and process dispatch. Raises `ValueError` if called inside `definition_scope`.

#### `pause() -> None`
Pauses dispatch. Active jobs continue; pending jobs remain queued.

#### `resume() -> None`
Resumes dispatch (equivalent to `start()`).

#### `stop_all(*, force: bool = False, source: str = "user", reason: str | None = None) -> int`
Cancels all pending jobs and initiates stopping of active jobs. Returns the count of jobs stopped.

#### `rerun(job_id: str | Job, *, expected_attempt: str | None = None, expected_submission: str | None = None) -> Job`
Schedules a new execution attempt for a finished job. The prior attempt is archived into history (bounded by `max_history`), and a fresh Attempt enters the dispatch queue.

#### `set_priority(job_id: str | Job, priority: int, *, expected_attempt: str | None = None, expected_submission: str | None = None) -> None`
Dynamically adjusts the dispatch priority of a job while it is in `PENDING` state. Raises `ValueError` if the job has already reserved a worker or begun execution.

#### `set_max_history(max_history: int) -> int`
Dynamically updates the attempt history retention capacity across all jobs. If current history exceeds the new limit, older attempts are evicted immediately. Returns the count of evicted attempts.

#### `set_max_log_lines(max_log_lines: int, job_id: str | None = None, *, expected_attempt: str | None = None, expected_submission: str | None = None) -> int`
Updates in-memory log ring buffer capacity for a specific job or all jobs. Returns the total count of evicted lines.

#### `update_settings(*, max_workers: int | None = None, resources: dict[str, int] | None = None, columns: list[str] | None = None, max_history: int | None = None) -> dict`
Dynamically updates worker concurrency, resource capacities, visible columns, or history limits.

#### `snapshot(*, selected_job_id: str | None = None, attempt_id: str | None = None) -> dict`
Returns a complete system snapshot dictionary containing operational flags, worker load, resource allocations, and structured details for all jobs (including logs and history for a selected job).

#### `explain_pending(job: str | Job) -> str`
Returns a human-readable explanation of why a job is currently in `PENDING` status (e.g. waiting for worker slot, waiting for a resource, or scheduler paused).

#### `add_log_listener(callback: Callable[[dict], None]) -> None`
Registers a global log listener callback. Invoked with a log record dictionary every time any job emits an output line.

#### `close() -> None`
Gracefully shuts down the manager, stops all child processes, and drains execution threads.

---

## Command Definition and Instance: `Job`

`Job` represents a single command definition and its execution attempts. Definitions are frozen once dispatch commences.

### Constructor
```python
Job(
    name: str,
    cmd: str | Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    pty: bool | None = None,
    prompts: Sequence[str] = (),
    pty_rows: int | None = None,
    pty_cols: int | None = None,
    resources: dict[str, int] | None = None,
    priority: int = 0,
    timeout: float | None = None,
    max_retries: int = 0,
    stop_policy: StopPolicy = StopPolicy.GRACEFUL_THEN_FORCE,
    stop_ctrl_c: bool = False,
    stop_cmd: str | None = None,
    stop_timeout: float = 3.0,
    max_log_lines: int = 10000,
    log_file: str | Path | None = None,
)
```

- **`name`** (`str`): Non-empty job name.
- **`cmd`** (`str | Sequence[str]`): Command string or argument list.
- **`cwd`** (`str | Path | None`): Working directory for the process.
- **`env`** (`dict[str, str] | None`): Custom environment variables.
- **`pty`** (`bool | None`): Enables PTY on POSIX (default `True`). Windows uses pipes.
- **`prompts`** (`Sequence[str]`): Literal prompt tokens indicating `WAITING_INPUT`.
- **`pty_rows`, `pty_cols`** (`int | None`): PTY terminal dimensions (POSIX only, $\le 65535$).
- **`resources`** (`dict[str, int] | None`): Named resource units required by this job.
- **`priority`** (`int`, default 0): Integer priority (higher values dispatch first).
- **`timeout`** (`float | None`): Maximum runtime in seconds before stopping is triggered.
- **`max_retries`** (`int`, default 0): Maximum additional retries after a non-zero exit without a stop request. A check FAIL alone does not trigger retry; timeout-induced stopping does not retry automatically.
- **`stop_policy`** (`StopPolicy`, default `GRACEFUL_THEN_FORCE`): Termination policy.
- **`stop_ctrl_c`** (`bool`, default False): Transmit interrupt signal during graceful stop.
- **`stop_cmd`** (`str | None`): Command text sent to stdin during graceful stop.
- **`stop_timeout`** (`float`, default 3.0): Graceful timeout in seconds before force kill.
- **`max_log_lines`** (`int`, default 10000): In-memory ring buffer capacity.
- **`log_file`** (`str | Path | None`): Dedicated disk log file path.

### Observable Properties

- **`job.status`** (`JobStatus`): Lifecycle state of the active attempt (e.g., `PENDING`, `RUNNING`, `DONE`, `FAILED`, `CANCELLED`).
- **`job.returncode`** (`int | None`): OS exit code of the process; `None` while running.
- **`job.pid`** (`int | None`): OS Process ID; `None` before launch.
- **`job.elapsed`** (`float | None`): Cumulative runtime of the active attempt in seconds.
- **`job.elapsed_str`** (`str`): Formatted elapsed runtime string (`MM:SS` or `HH:MM:SS`).
- **`job.priority`** (`int`): Scheduling priority integer.
- **`job.cmd_str`** (`str`): Shell-escaped command representation string.
- **`job.attempt_id`** (`str`): Unique hexadecimal identifier for the current attempt.
- **`job.submission_id`** (`str`): Identifier of the parent submission.
- **`job.attempt_number`** (`int`): Sequential attempt index starting from 1.
- **`job.data`** (`MappingProxyType`): Read-only mapping of metric values captured by `Track` rules for the active attempt.
- **`job.context`** (`dict`): Mutable per-attempt user dictionary accessible to callbacks.
- **`job.result`** (`Report`): Detailed verdict and evidence report for the active attempt:
  - **`job.result.verdict`** (`Result | None`): Current aggregate verdict: confirmed FAIL/ERROR is visible during execution; otherwise it remains `None` until completion. PASS/N/A requires normal complete execution.
  - **`job.result.checks`** (`tuple`): Immutable snapshots of current check states, finalized when the attempt ends.
  - **`job.result.warnings`** (`tuple`): Tuple of recorded warning structures and evidence lines.
  - **`job.result.errors`** (`tuple`): Tuple of caught exception records.
- **`job.history`** (`tuple[dict, ...]`): Tuple of immutable snapshot dictionaries of completed past attempts (bounded by `max_history`).

### Monitoring Methods

#### `track(name: str, pattern: str | re.Pattern, *, cast: Callable = str) -> Track`
Declares metric extraction rules. Regex must have exactly one capture group `()`.
- **Returns**: `Track` object exposing `.events.changed` and `.events.error`.

#### `check(name: str, *, match: str | re.Pattern | Callable, expect: Expect = Expect.PRESENT, on: Event | EventRef = Event.LOG, every: float | None = None) -> Check`
Declares verification checks against output lines, ticks, or tracked metrics.
- **Returns**: `Check` object exposing `.events.failed`, `.events.satisfied`, and `.events.error`.

#### `on(event: Event | EventRef = Event.LOG, *, match: str | re.Pattern | Callable | None = None, warn: bool = False, stop: bool = False, callback: Callable | None = None, name: str | None = None, message: str | None = None, once: bool = True, every: float | None = None) -> Subscription`
Subscribes actions or warnings to events.
- **Action params**: `callback` and `warn`/`stop` are mutually exclusive.
- **`warn=True`**: Records a warning without stopping execution or disqualifying `PASS`.
- **`stop=True`**: Requests termination according to `stop_policy`.
- **`callback`**: Function with signature `callback(bound_job, *args)`.
- **`once`** (`bool`, default True): Set to `False` to trigger repeatedly on every match.

### Interactive Controls, Logging & Snapshots

- **`send(text: str, *, newline: bool = True, expected_attempt: str | None = None) -> bool`**: Transmits text to stdin.
- **`send_ctrl_c(*, expected_attempt: str | None = None) -> bool`**: Sends an interrupt signal.
- **`send_signal(sig: int, *, expected_attempt: str | None = None) -> bool`**: Sends a specific OS signal.
- **`stop(*, source: str = "user", reason: str | None = None, force: bool = False, expected_attempt: str | None = None) -> bool`**: Requests job termination.
- **`kill(*, expected_attempt: str | None = None) -> bool`**: Forcefully terminates the process tree.
- **`log(message: Any) -> None`**: Emits a text entry with `[Wave]` source into this job's live record stream.
- **`logs(tail: int | None = None) -> list[str]`**: Retrieves the list of log line strings preserved in memory. Pass `tail=N` to fetch only the last $N$ lines.
- **`set_max_log_lines(max_log_lines: int, *, expected_attempt: str | None = None, expected_submission: str | None = None) -> int`**: Dynamically adjusts ring buffer capacity. Returns count of evicted lines.
- **`to_snapshot_dict(*, include_logs: bool = False, attempt_id: str | None = None) -> dict`**: Serializes full job state into a structured dictionary.

> [!NOTE]
> **Regarding `expected_attempt`**:
> The `expected_attempt` and `expected_submission` parameters in control methods are internal safeguards against rerun race conditions. User code in scripts and callbacks should call `job.send("cmd")` or `job.stop()` directly without passing these parameters.

---

## Event System & Callback Signatures

Wave provides a structured event pub/sub mechanism. Event kinds, triggers, and callback signatures are defined as follows:

| Event Reference | Trigger Condition | Callback Signature | Parameters |
| :--- | :--- | :--- | :--- |
| **`Event.LOG`** | Output line decoded | `callback(bound_job, line: str)` | `line`: Decoded line text stripped of ANSI codes. |
| **`Event.TICK`** | Periodic timer triggered | `callback(bound_job, elapsed: float)` | `elapsed`: Cumulative attempt runtime in seconds (use with `every=1.0`). |
| **`track.events.changed`** | Extracted metric appeared or changed | `callback(bound_job, old_value: Any, new_value: Any)` | `old_value`: Previous value (initially `None`); `new_value`: Parsed and cast value. |
| **`track.events.error`** | Cast function threw error | `callback(bound_job, error: dict)` | `error`: Contains `source`, `message`, `evidence`. |
| **`check.events.satisfied`** | Check condition met (`OK`) | `callback(bound_job, check_state: dict)` | `check_state`: Contains `name`, `state`, `hits`, `evidence`. |
| **`check.events.failed`** | Check condition failed (`FAIL`) | `callback(bound_job, check_state: dict)` | Same as above. |
| **`check.events.error`** | Check matcher threw error | `callback(bound_job, error: dict)` | Same as track.events.error. |
| **`job.events.status_changed`** | Status transitioned | `callback(bound_job, prev_status: JobStatus, new_status: JobStatus)` | Previous and new states. |
| **`job.events.attempt_finished`** | Attempt finalized | `callback(bound_job, attempt_snapshot: dict)` | Immutable snapshot dictionary of attempt. |
| **`job.events.error`** | Reportable monitoring, control, or execution error | `callback(bound_job, error: dict)` | Internal error structure. |

### Event Callback Example

```python
import sys
from rpkbin.wave import Job, JobManager, Event, Expect

mgr = JobManager()
job = mgr.add(Job("event_demo", [sys.executable, "-u", "-c", "import time; print('PROGRESS=50'); time.sleep(0.1); print('PASS_TOKEN')"]))

# 1. Subscribe to log output
job.on(Event.LOG, match=r"^PROGRESS=(\d+)$", callback=lambda bound, line: bound.log(f"Matched line: {line}"))

# 2. Track metric change
progress = job.track("progress", r"^PROGRESS=(\d+)$", cast=int)
job.on(progress.events.changed, callback=lambda bound, old, new: bound.log(f"Progress changed: {old} -> {new}%"))

# 3. Check satisfaction event
chk = job.check("pass_check", match=r"^PASS_TOKEN$", expect=Expect.PRESENT)
job.on(chk.events.satisfied, callback=lambda bound, state: bound.log(f"Check {state['name']} satisfied!"))
```

Callable matchers receive the same event arguments and return bool, for example `match=lambda bound, old, new: new >= 80`. Put all declarations before starting or validating work.

Append these declarations to the example to stop on forbidden output or a monitoring error:

```python
# Continues the job above: record FAIL first, then use the job's stop policy.
no_fatal = job.check("no_fatal", match=r"^FATAL:", expect=Expect.ABSENT)
job.on(no_fatal.events.failed, stop=True)
job.on(job.events.error, stop=True)  # Without this, an isolated monitor error does not stop the process.

# Report elapsed time for long work; a short job may finish before its first tick.
job.on(Event.TICK, every=1.0, once=False,
       callback=lambda bound, elapsed: bound.log(f"Elapsed: {elapsed:.1f}s"))
```

---

## Monitor Fault Isolation and Callback Latency

1. **Monitor Fault Isolation**:
   - If a user-registered callback, cast function, or check matcher raises an unhandled exception, Wave records the exception to `job.result.errors`, increments `error_count`, and **disables the faulty monitor for that attempt**. Other monitors and the process continue by default. Retry/rerun attempts enable their monitors again. Subscribe to `job.events.error` explicitly if an error should stop execution.
   - That attempt cannot achieve `Result.PASS`: it yields `Result.ERROR` unless a higher-priority FAIL exists. Exceptions in completion notifications after finalization become diagnostics without changing the completed Result.
2. **Callback Latency Constraints**:
   - Callbacks execute **synchronously** on the thread that triggers the event. Event handling shares a lock within one manager, so blocking can also delay other jobs' monitoring and controls.
   - Callbacks **must not** perform long-running blocking operations (such as `time.sleep`, heavy disk writes, or synchronous HTTP requests), as this blocks child pipe draining and verdict progression.

---

## Enumerations

### `JobStatus`
- `PENDING`: Queued, awaiting worker or resource slot.
- `RUNNING`: Process is executing.
- `WAITING_INPUT`: A configured prompt token was detected; input may be expected, but actual blocking is not established.
- `STOPPING`: Shutdown sequence in progress.
- `DONE`: Process terminated normally with returncode 0.
- `FAILED`: Process exited non-zero or timed out.
- `CANCELLED`: Process was stopped or cancelled.

### `Result`
- `PASS`: Completed normally with returncode 0, healthy monitoring, and all declared checks OK.
- `FAIL`: A check failed, execution failed to start, a timeout occurred, or a non-zero exit occurred without a stop request.
- `ERROR`: Unhandled exception in monitor rules or callbacks.
- `INCOMPLETE`: Stopped or cancelled before complete execution, without confirmed FAIL/ERROR. This also applies when all checks were already OK.
- `NA` (`"N/A"`): Finished normally with no checks declared.

### `CheckState`
- `UNDECIDED` (`"-"`): Not yet decided.
- `OK`: Check passed.
- `FAIL`: Check failed.
- `ERROR`: Matcher or cast function failed with exception.
- `INCOMPLETE`: Execution interrupted before resolution.

### `Expect`
- `PRESENT`: Condition must occur.
- `ABSENT`: Condition must never occur.

### `StopPolicy`
- `GRACEFUL_THEN_FORCE`: Attempt graceful stop command/interrupt before falling back to kill.
- `FORCE`: Immediate process tree termination.

### `Event`
- `LOG` (`"log"`): Subprocess output line.
- `TICK` (`"tick"`): Periodic timer tick (requires `every` in seconds).
