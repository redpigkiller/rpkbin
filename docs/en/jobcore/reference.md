# `rpkbin.jobcore` API Reference

[繁體中文](../../zh/jobcore/reference.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [Usage Scenarios](scenarios.md)

Technical specification for `rpkbin.jobcore`: `JobManager`, `Job`, `JobStatus`, `ProcessVerdict`, `StopPolicy`, `ControlReceipt`, `Report`, `Capabilities`, and `ProcessHandle`.

```python
from rpkbin.jobcore import (
    Capabilities,
    ControlReceipt,
    Job,
    JobManager,
    JobStatus,
    ProcessHandle,
    ProcessVerdict,
    Report,
    StopPolicy,
)
```

`__all__` exports those nine symbols. The package uses the Python standard library and requires Python 3.11 or newer. Event classes (`AttemptReceiver`, `PROCESS_CAPABILITIES`, `Started`, `Line`, `SourceError`, `Finished`, `StatusChanged`, `ClockTick`, `SourceEvent`, `receiver_callback`) live in `rpkbin.jobcore.events`; `strip_ansi` lives in `rpkbin.jobcore.output`.

## `JobManager`

Fixed-quota command scheduler managing job registration, atomic validation, priority dispatch, queue editing, attempt history, and shutdown.

### Creation

```python
manager = JobManager(
    *,
    max_workers=4,
    resources=None,
    log_dir=None,
    max_history=20,
    scheduling_mode="backfill",
)
with JobManager(...) as manager: ...
```

All constructor arguments are keyword-only. Calling `JobManager` with a `with` statement returns `self` from `__enter__` (raising `RuntimeError` if closing or closed) and calls `close()` from `__exit__` without suppressing exceptions (`return False`).

#### Name-Value Arguments

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `max_workers` | `int` | `4` | Maximum concurrent worker threads (`>= 1`; `bool` rejected with `ValueError`). |
| `resources` | `dict[str, int] \| None` | `None` | Named resource capacities (`None` means `{}`). Keys must be non-empty strings; values must be integers `>= 0` (`bool` rejected). Non-`dict` raises `TypeError`. |
| `log_dir` | `str \| PathLike \| None` | `None` | Directory for per-job `<job.id>.log` files when a job does not set `log_file`. Resolved with `.absolute()` at construction so later `os.chdir()` calls do not move output paths. Empty strings raise `ValueError`. |
| `max_history` | `int` | `20` | Maximum finalized prior attempts retained per job in `Job.history` (`>= 1`; `bool` rejected). |
| `scheduling_mode` | `str` | `"backfill"` | Priority dispatch policy: `"backfill"`, `"strict"`, or `"tier_strict"`. Non-`str` raises `TypeError`; other strings raise `ValueError`. |

### Properties

| Property | Type | Meaning |
| :--- | :--- | :--- |
| `max_workers` | `int` | Configured worker concurrency limit. |
| `resources` | `dict[str, int]` | Configured resource capacity table. |
| `log_dir` | `Path \| None` | Absolute log directory, or `None`. |
| `max_history` | `int` | Retained historical attempt limit per job. |
| `scheduling_mode` | `str` | Active priority scheduling policy (`"backfill"`, `"strict"`, or `"tier_strict"`). |
| `jobs` | `list[Job]` | Currently registered jobs in registration order. |
| `removed_jobs` | `tuple[Job, ...]` | Read-only property returning jobs removed via `remove_job()` that can be restored with `restore_job()`. |
| `started` | `bool` | `True` once `start()` launches the scheduler thread. |
| `ready` | `bool` | `True` after `validate()` succeeds on a non-empty roster or after `start()`. Removing the last preview job resets `ready` to `False`. |
| `paused` | `bool` | Initially `True`; `False` while dispatch is active after `start()` or `resume()`. |

### Object Functions

| Method | Purpose |
| :--- | :--- |
| `add(job, *, receiver_factory=None)` | Register a `Job` and optionally override its receiver factory; validates and freezes immediately if `started` is `True`. Returns the `Job`. |
| `add_many(jobs)` | Register each `Job` in `jobs` via `add()` and return `list[Job]`. |
| `set_receiver_factory(factory)` | Install a receiver factory `factory(job, attempt)` once before `start()`. |
| `get_job(job_id_or_name)` | Look up a registered `Job` by `Job` instance, `job.id`, or unique `job.name`. Returns `None` if absent; raises `ValueError` on duplicate names. |
| `validate()` | Check non-empty `jobs`, validate all resource requests against `resources`, freeze job definitions without starting workers, and set `ready = True`. |
| `start()` | Call `validate()` if unstarted, start the scheduler daemon thread (`Wave-scheduler`), and set `started = True`, `paused = False` (or unpause if already started). |
| `pause()` | Set `paused = True` so queued jobs wait while running processes continue. |
| `resume()` | Unpause dispatch (calls `start()`). |
| `stop_all(*, force=False, source="user", reason=None, include_held=True)` | Pause dispatch, cancel pending jobs first (skipping held jobs when `include_held=False`), then stop active jobs in ID order. Returns accepted stop count. |
| `control(job_id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None)` | Issue a snapshot-guarded control action; returns `ControlReceipt`. |
| `set_priority(job_id, priority, *, expected_attempt=None, expected_submission=None)` | Update a queued job's integer priority; returns `ControlReceipt`. |
| `set_held(job_id, held, *, expected_attempt=None, expected_submission=None)` | Exclude (`held=True`) or re-include (`held=False`) a queued job; requires `bool` `held`, returns `ControlReceipt`. |
| `reorder_queue(targets)` | Atomically assign strictly decreasing priorities across eligible queued targets; returns `list[tuple[str, ControlReceipt, int \| None]]`. |
| `remove_job(job_id, *, expected_attempt=None, expected_submission=None)` | Move a job to `removed_jobs` (allowed only in Preview before `start()`, or when `paused=True` and completely idle). Returns `ControlReceipt`. |
| `restore_job(job_id, *, expected_attempt=None, expected_submission=None)` | Restore a removed job by `Job`, `job.id`, or unique `job.name` back to `jobs` in its original registration order and queue rank. Returns `ControlReceipt`. |
| `rerun(job_id, *, expected_attempt=None, expected_submission=None)` | Queue a fresh submission (`submission_id` changes, `attempt_number` increments, `retry_index` resets to `0`) when `can_rerun` is `True`. Returns `ControlReceipt`. |
| `set_max_log_lines(max_log_lines, job_id=None, *, expected_attempt=None, expected_submission=None)` | Update retained line cap on one job or all jobs and return total evicted lines. |
| `set_max_history(max_history)` | Update `max_history`, trim older attempts across all jobs, and return count of evicted historical attempts. |
| `update_settings(*, max_workers=None, resources=None, max_history=None, scheduling_mode=None)` | Atomically update manager settings without preempting running jobs; returns `{"evicted_history": int}`. |
| `explain_pending(job)` | Return display pending text if `PENDING`, else `job.status.value`. |
| `add_log_listener(callback)` | Subscribe `callback(record_dict)` to live log records (`line_no`, `text`, `source`, `time`, `job_id`, `job_name`, `attempt_id`, `attempt_number`). |
| `snapshot(*, selected_job_id=None, attempt_id=None, include_logs=True)` | Capture manager state, settings, `jobs`, `removed_jobs`, and optional `detail` dict. |
| `close()` | Pause, stop all jobs (graceful then forced after `max(stop_timeout) + 5` s), drain up to 5 s, close sinks, and join the scheduler thread. Idempotent. |
| `repr(manager)` | Return `JobManager(jobs=N, active=A/W, started=..., paused=..., closed=...)`. |

#### `control` Actions and Parameters

`control(job_id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None)` requires both `expected_attempt` and `expected_submission` so stale UI or automation callers cannot act on a retried or rerun attempt.

| `action` | Target Method | Extra Arguments Used | Rejection / Notes |
| :--- | :--- | :--- | :--- |
| `"stop"` | `job.stop(...)` | none | Cancels if `PENDING`, else starts graceful/configured stop. |
| `"force_stop"` | `job.kill(...)` | none | Sends immediate process-tree kill (`stop(force=True)`). |
| `"interrupt"` | `job.send_ctrl_c(...)` | none | Sends PTY `\x03` / `SIGINT` on POSIX or `CTRL_BREAK_EVENT` on Windows. |
| `"signal"` | `job.send_signal(sig, ...)` | `sig` (required `int >= 0`) | Returns `ControlReceipt("rejected", "Signal is required")` when `sig is None`. |
| `"send"` | `job.send(text, ...)` | `text`, `newline=True`, `secret=False` | Appends `"\n"` unless `newline=False` or `text` already ends with `"\n"`. |
| any other | none | none | Returns `ControlReceipt("rejected", "Unknown control action")`. |

#### Scheduling Modes and `pending_code`

Runtime capacity changes in `update_settings()` never preempt running workers. `resources` cannot remove a resource key referenced by a registered job; set that resource's capacity to `0` to hold dispatch instead.

- `"backfill"`: Higher-priority queued jobs run first, but lower-priority jobs whose required worker slot and resources fit still launch immediately.
- `"strict"`: Queue order (`(-priority, enqueue_seq)`) blocks all lower-ranked jobs as soon as an earlier eligible job is waiting for a worker slot or resource.
- `"tier_strict"`: Jobs within the same `priority` tier may backfill when a resource blocks one member of that tier, while lower `priority` tiers stay blocked until the higher tier clears (worker saturation stops scanning immediately).
- Holding a job with `set_held(job, True)` excludes it from the queue (`pending_code == "HELD"`), so it never blocks downstream jobs even under `"strict"` or `"tier_strict"`. Calling `stop_all(include_held=False)` also leaves held pending jobs queued.

`manager.snapshot()` populates `pending_code` and `pending_reason` for current `PENDING` jobs (returning `None` for active, finished, or historical views):

| `pending_code` | Meaning |
| :--- | :--- |
| `"HELD"` | Excluded from the dispatch queue via `set_held(job, True)`. |
| `"NOT_STARTED"` | Manager has not started and `validate()` has not run. |
| `"READY_TO_START_PENDING_BEGIN"` | Validated in Preview (`ready=True`), waiting for `start()`. |
| `"PAUSED"` | Manager is started and `paused=True`. |
| `"DISPATCHING"` | Reserved by the scheduler and launching its worker thread. |
| `"WAIT_WORKER"` | `active_workers >= max_workers`, or blocked behind an earlier waiting job. |
| `"WAIT_RESOURCE"` | Insufficient capacity for at least one required resource, or blocked behind a resource-waiting job. |
| `"READY"` | Eligible to launch on the next scheduler pass. |

#### Queue Reordering, Removal, and Restore

- `reorder_queue(targets)` accepts a non-empty list or tuple whose elements are `job_id` strings, `Job` objects, or `(target, expected_attempt, expected_submission)` tuples. Duplicate jobs raise `ValueError`. Eligible pending jobs receive strictly decreasing integer priorities preserving existing spacing where possible, while active, finished, or stale entries receive a `"rejected"` receipt and `None` priority in the returned `[(jid, receipt, assigned_priority), ...]` list.
- `remove_job()` and `restore_job()` are permitted only in Preview (`not started`) or when `paused=True` and completely idle (`active_workers == 0`, no worker threads, and no job reserved or active). `restore_job()` re-validates resource demands against current `resources`, freezes the job if `started` is `True`, and restores its original list index in `manager.jobs` and queue rank.

## `Job`

Frozen command definition paired with per-attempt live execution state, logs, and history.

### Creation

```python
job = Job(
    name,
    cmd,
    *,
    cwd=None,
    env=None,
    encoding="utf-8",
    decode_errors="replace",
    pty=None,
    prompts=(),
    pty_rows=None,
    pty_cols=None,
    resources=None,
    priority=0,
    timeout=None,
    max_retries=0,
    stop_policy=StopPolicy.GRACEFUL_THEN_FORCE,
    stop_ctrl_c=False,
    stop_cmd=None,
    stop_timeout=3.0,
    output_drain_timeout=30.0,
    max_log_lines=10000,
    log_file=None,
)
```

#### Input Arguments

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | required | Non-empty display name (leading/trailing whitespace is stripped). |
| `cmd` | `str \| PathLike \| Sequence[str \| PathLike]` | required | Command string (executed via platform shell), single `PathLike`, or non-empty argument sequence (rejects `bytes`/`bytearray` and empty strings). |

#### Name-Value Arguments

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `cwd` | `str \| PathLike \| None` | `None` | Working directory (`Path(cwd).resolve()`). `None` captures `os.getcwd()` when frozen. Empty strings raise `ValueError`. |
| `env` | `dict[str, str] \| None` | `None` | Environment overrides merged on top of `os.environ`. Keys and values must be `str`. |
| `encoding` | `str` | `"utf-8"` | Validated text codec used for output decoding and `send()` input encoding. |
| `decode_errors` | `str` | `"replace"` | `"replace"` substitutes invalid bytes, increments `output_decode_count`, and records a warning; `"strict"` records an `output_decode` error and stops monitored forwarding (`monitor=False`) while continuing raw log retention. |
| `pty` | `bool \| None` | `None` | `None` selects `True` on POSIX and `False` on Windows. `pty=True` raises `ValueError` on Windows. |
| `prompts` | `Sequence[str]` | `()` | Non-empty literal prompt tokens (whitespace-only strings raise `ValueError`). |
| `pty_rows` | `int \| None` | `None` | Initial PTY row count (`1..65535`); requires `pty=True`. |
| `pty_cols` | `int \| None` | `None` | Initial PTY column count (`1..65535`); requires `pty=True`. |
| `resources` | `dict[str, int] \| None` | `None` | Required resource counts (`>= 1` per named resource). |
| `priority` | `int` | `0` | Initial dispatch priority (higher values run earlier). `bool` is rejected. |
| `timeout` | `int \| float \| None` | `None` | Positive finite timeout in seconds; triggers `stop(source="timeout", ...)` and forces `ProcessVerdict.FAIL`. |
| `max_retries` | `int` | `0` | Automatic retry budget (`>= 0`) on unsolicited non-zero exit or startup failure. Suppressed by `stop()`, `timeout`, or accepted interrupting signals. |
| `stop_policy` | `StopPolicy` | `StopPolicy.GRACEFUL_THEN_FORCE` | `GRACEFUL_THEN_FORCE` or `FORCE`. |
| `stop_ctrl_c` | `bool` | `False` | Send `interrupt()` at the start of graceful stop. |
| `stop_cmd` | `str \| None` | `None` | Command text written to stdin (appending `"\n"` if absent) during graceful stop instead of `terminate()`. |
| `stop_timeout` | `int \| float` | `3.0` | Seconds to wait after graceful stop signal/command before escalating to `kill()`. |
| `output_drain_timeout` | `int \| float` | `30.0` | Hard post-exit output drain limit in seconds (paired with a 3.0 s line-progress idle timeout). |
| `max_log_lines` | `int` | `10000` | In-memory ring-buffer line cap per attempt (`>= 1`). |
| `log_file` | `str \| PathLike \| None` | `None` | Explicit log output file path (resolved via `.absolute()` at init; takes precedence over `JobManager.log_dir`). |

### Properties

| Property | Type | Meaning |
| :--- | :--- | :--- |
| `id` | `str` | Immutable job identifier (`"job_" + uuid4.hex`). |
| `name` | `str` | Job name (frozen after validation). |
| `cmd`, `cmd_str` | `str \| tuple[str, ...]`, `str` | Normalized command and shell-joined string representation. |
| `cwd`, `env`, `encoding`, `decode_errors`, `pty`, `prompts`, `pty_rows`, `pty_cols`, `resources`, `timeout`, `max_retries`, `stop_policy`, `stop_ctrl_c`, `stop_cmd`, `stop_timeout`, `output_drain_timeout`, `max_log_lines`, `log_file` | various | Definition settings (frozen once validated; `env` and `resources` become `MappingProxyType`). |
| `priority` | `int` | Current attempt priority (updatable while queued via `JobManager`). |
| `status` | `JobStatus` | Current attempt status (`PENDING`, `RUNNING`, `WAITING_INPUT`, `STOPPING`, `DONE`, `FAILED`, `CANCELLED`). |
| `returncode` | `int \| None` | Process exit code, `-1` on launch failure, or `None` while unfinished/cancelled. |
| `pid` | `int \| None` | Child process ID while running/finished, or `None` before launch. |
| `elapsed`, `elapsed_str` | `float`, `str` | Elapsed seconds and `"MM:SS"` or `"HH:MM:SS"` display string. |
| `data` | `MappingProxyType` | Frozen receiver projection dictionary for the current attempt. |
| `result` | `Report` | Immutable `Report(verdict, execution, warnings, errors, termination)` for the current attempt. |
| `attempt_id`, `submission_id`, `attempt_number` | `str`, `str`, `int` | Current attempt UUID hex, submission UUID hex, and 1-based attempt counter. |
| `history` | `tuple[MappingProxyType, ...]` | Frozen snapshots of prior attempts up to `JobManager.max_history`. |

### Object Functions

| Method | Purpose |
| :--- | :--- |
| `send(text, *, newline=True, secret=False, expected_attempt=None)` | Encode `text` (appending `"\n"` if `newline=True` and missing), write to process stdin/PTY outside `_events_lock`, clear `WAITING_INPUT` to `RUNNING`, and mask `"***"` when `secret=True`. Returns `ControlReceipt`. |
| `send_signal(sig, *, source="user", expected_attempt=None)` | Send integer signal `sig >= 0` (`CTRL_BREAK_EVENT` only on Windows). Suppresses automatic retries unless `sig` is `SIGUSR1`, `SIGUSR2`, `SIGTSTP`, or `SIGCONT`. Returns `ControlReceipt`. |
| `send_ctrl_c(*, source="user", expected_attempt=None)` | Send platform interrupt (`\x03` / `SIGINT` on POSIX, `CTRL_BREAK_EVENT` on Windows) and mark `interrupt_requested`. Returns `ControlReceipt`. |
| `stop(*, source="user", reason=None, expected_attempt=None, force=False)` | Cancel a pending job immediately or initiate graceful/forced stop on a running process. Returns `ControlReceipt`. |
| `kill(*, expected_attempt=None, source="user", reason=None)` | Shorthand for `stop(force=True, ...)`. Returns `ControlReceipt`. |
| `logs(tail=None)` | Return retained log lines (`list[str]`); `tail=0` returns `[]`, negative/bool/non-int raises `ValueError`. |
| `set_max_log_lines(max_log_lines, *, expected_attempt=None, expected_submission=None)` | Update ring-buffer capacity on this job and return evicted line count. |
| `log(message)` | Emit a management log record (`source="wave"`) into the current attempt. |
| `to_snapshot_dict(*, include_logs=False, attempt_id=None, include_details=False)` | Return a detached dictionary snapshot of the current or a retained historical attempt. |
| `repr(job)` | Return `Job(id='...', name='...', status='...')`. |

#### `prompts` and `WAITING_INPUT` Behavior

- Output decoding inspects the trailing 4,096 characters (`_PROMPT_TAIL_CHARS`) of any unterminated line buffer after ANSI escape stripping (`strip_ansi`). If `tail.rstrip()` ends with `p.rstrip()` for any configured prompt `p`, that segment flushes immediately as a line without waiting for `\n`.
- When a flushed monitored line matches a prompt token, `job.status` changes to `JobStatus.WAITING_INPUT`.
- `JobStatus.WAITING_INPUT` returns to `JobStatus.RUNNING` when `job.send(...)` succeeds or when a subsequent non-whitespace monitored line arrives. A delayed empty newline (`""`) right after an unterminated prompt does not clear `WAITING_INPUT`.

## Value Types and Enums

### `JobStatus`

`str` Enum with states `"PENDING"`, `"RUNNING"`, `"WAITING_INPUT"`, `"STOPPING"`, `"DONE"`, `"FAILED"`, and `"CANCELLED"`.

| Property | Type | True For |
| :--- | :--- | :--- |
| `is_active` | `bool` | `RUNNING`, `WAITING_INPUT`, `STOPPING` |
| `is_finished` | `bool` | `DONE`, `FAILED`, `CANCELLED` |

### `ProcessVerdict`

`str` Enum for process-only outcomes (`Job.result.verdict`):

| Member | Value | Precedence / Trigger |
| :--- | :--- | :--- |
| `ProcessVerdict.FAIL` | `"FAIL"` | Highest precedence: launch failure, `timeout` expiration, or unsolicited non-zero `returncode` (`not stop_requested`). |
| `ProcessVerdict.ERROR` | `"ERROR"` | Non-empty `errors` (`error_count > 0`, e.g., `output_reader` or strict `output_decode`) or `reader_cutoff` when no process failure took precedence. |
| `ProcessVerdict.INCOMPLETE` | `"INCOMPLETE"` | Finalized or complete check after a `stop()` / `kill()` / cancellation (`stop_requested=True`) with no `FAIL` or `ERROR`. |
| `ProcessVerdict.NA` | `"N/A"` | Clean completion (`returncode == 0`, no errors, no stop request). |
| `None` | `None` | Attempt has not finished/finalized yet. |

### `StopPolicy`, `ControlReceipt`, `Report`, `Capabilities`

- `StopPolicy(str, Enum)`: `StopPolicy.GRACEFUL_THEN_FORCE` (`"GRACEFUL_THEN_FORCE"`), `StopPolicy.FORCE` (`"FORCE"`).
- `ControlReceipt(status, reason)`: frozen dataclass where `status` is `"accepted"`, `"rejected"`, or `"failed"`, and `reason` is non-empty text. `.accepted` and `bool(receipt)` are `True` only when `status == "accepted"`.
- `Report(verdict, execution, warnings, errors, termination)`: frozen dataclass returned by `Job.result`. `execution` is a frozen mapping containing `status`, `returncode`, and `elapsed`.
- `Capabilities(has_process=False, can_send=False, can_stop=False, can_interrupt=False, can_signal=False, can_kill=False, can_rerun=False)`: frozen dataclass describing supported controls; `.snapshot(can_rerun=False)` returns a plain `dict`.

## `ProcessHandle`

One-shot subprocess transport with merged binary stdout/stderr, non-blocking 1.0 s bounded writes, and OS-level descendant containment.

### Creation

```python
handle = ProcessHandle(
    cmd,
    *,
    cwd=None,
    env=None,
    encoding="utf-8",
    pty=False,
    pty_rows=None,
    pty_cols=None,
)
```

Unlike `Job`, `ProcessHandle` defaults `pty=False` on all platforms, and constructing it never spawns a child process. `pty_rows` and `pty_cols` require `pty=True` (defaulting to `24` and `80` when `None`) and `pty=True` raises `ValueError` on Windows.

### Properties and Object Functions

| Member | Purpose |
| :--- | :--- |
| `pid` | `int \| None`: child process ID after `start()`, else `None`. |
| `start()` | Spawn the child once inside a new POSIX session (`start_new_session=True`, optional PTY via `_pty_exec.py`) or a Windows Job Object (`KILL_ON_JOB_CLOSE`) and `CREATE_NEW_PROCESS_GROUP`. |
| `poll()` | Return exit code (`int`) if exited, else `None`. |
| `read()` | Read up to 4,096 merged stdout/stderr bytes; returns `b""` on EOF or after `close()`. |
| `write(text, *, secret=False)` | Encode and write `text` with a 1.0 s deadline without appending a newline. Raises `TimeoutError` if blocked (noting partial bytes may already be in the pipe; do not blindly retry). `secret=True` clears PTY `ECHO` during the write on POSIX. |
| `send_signal(sig)` | Send `sig` to the POSIX process group (`os.killpg`) or `signal.CTRL_BREAK_EVENT` on Windows (other signals raise `ValueError` on Windows). |
| `interrupt()` | Send PTY `\x03` (if `ISIG`) or `SIGINT` on POSIX; send `CTRL_BREAK_EVENT` on Windows. |
| `terminate()` | Send `SIGTERM` to the POSIX process group, or call `interrupt()` on Windows. |
| `kill()` | Send `SIGKILL` to the POSIX process group, or call `TerminateJobObject` on Windows. |
| `cleanup()` | Once `poll() is not None`, kill remaining descendants without closing output pipes/PTY so unread buffered bytes drain cleanly to EOF. |
| `close()` | Idempotently close streams, descriptors, and the Windows Job Object handle after output is drained. |

## Examples

### 1. Minimal Example: Prompt Detection and `manager.control`

Detect an interactive prompt, transition to `JobStatus.WAITING_INPUT`, reply via `manager.control(..., action="send", newline=True)`, and observe clean exit.

```python
import sys
import time
from rpkbin.jobcore import Job, JobManager, JobStatus

script = (
    "import sys; "
    "sys.stdout.write('cmd> '); sys.stdout.flush(); "
    "val = sys.stdin.readline().strip(); "
    "print('got:' + val, flush=True)"
)

with JobManager(max_workers=1) as manager:
    job = manager.add(Job("prompt-job", [sys.executable, "-u", "-c", script], pty=False, prompts=("cmd> ",)))
    manager.start()

    deadline = time.monotonic() + 10.0
    while job.status is not JobStatus.WAITING_INPUT:
        if time.monotonic() >= deadline:
            raise TimeoutError("prompt not detected")
        time.sleep(0.01)

    receipt = manager.control(
        job.id,
        "send",
        expected_attempt=job.attempt_id,
        expected_submission=job.submission_id,
        text="ping",
        newline=True,
    )
    assert receipt.accepted

    while not job.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("job did not finish")
        time.sleep(0.01)

    print("logs:", job.logs())
```

Output:
```text
logs: ['cmd> ', 'Input sent: ping', 'got:ping']
```

### 2. Progressive Example: `remove_job`, `removed_jobs`, `restore_job`, and `ProcessHandle`

Remove a queued job in Preview, verify `removed_jobs`, restore it back to its original position, and run a direct `ProcessHandle` command.

```python
import sys
from rpkbin.jobcore import Job, JobManager, ProcessHandle

with JobManager() as manager:
    a = manager.add(Job("a", [sys.executable, "-c", "pass"], pty=False))
    b = manager.add(Job("b", [sys.executable, "-c", "pass"], pty=False))
    c = manager.add(Job("c", [sys.executable, "-c", "pass"], pty=False))

    assert manager.remove_job(b.id).accepted
    print("after remove:", [j.name for j in manager.jobs], "removed:", [j.name for j in manager.removed_jobs])
    assert manager.restore_job("b").accepted
    print("after restore:", [j.name for j in manager.jobs], "removed:", [j.name for j in manager.removed_jobs])

handle = ProcessHandle([sys.executable, "-u", "-c", "import sys; print(sys.stdin.read(2))"])
handle.start()
handle.write("ok")
while handle.poll() is None:
    pass
handle.cleanup()
chunks = []
while chunk := handle.read():
    chunks.append(chunk)
handle.close()
print("handle out:", b"".join(chunks).decode().strip())
```

Output:
```text
after remove: ['a', 'c'] removed: ['b']
after restore: ['a', 'b', 'c'] removed: []
handle out: ok
```

## Tips and Limitations

- **Callback lock safety**: Calling `manager.add`, `manager.stop_all`, `manager.close`, `manager.set_max_history`, or controlling a second `Job` while holding another job's `_events_lock` (or inside an `AttemptReceiver` callback) raises `RuntimeError` or returns a `"rejected"` `ControlReceipt`.
- **Unterminated output lines**: Lines without `\n` are flushed in 4 MiB chunks (`_MAX_LINE_CHARS = 4 * 1024 * 1024`) or immediately on prompt match, and remaining bytes flush at EOF.
- **Definition immutability**: Once `validate()` (or post-start `add()`) freezes a `Job`, setting any definition attribute raises `AttributeError`.

## See Also

- [Quickstart](README.md)
- [Usage Scenarios](scenarios.md)
- [rpkbin.flow Reference](../flow/reference.md)
- [Documentation Index](../README.md)
