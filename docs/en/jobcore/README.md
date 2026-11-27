# `rpkbin.jobcore`

[繁體中文](../../zh/jobcore/README.md) · [Documentation Index](../README.md) · [Usage Scenarios](scenarios.md) · [API Reference](reference.md)

`rpkbin.jobcore` schedules external commands with fixed worker and resource quotas and runs each attempt in an isolated process group or Windows Job Object.

## Syntax

```python
manager = JobManager(*, max_workers=4, resources=None, log_dir=None, max_history=20, scheduling_mode="backfill")
with JobManager(...) as manager: ...
job = Job(name, cmd, *, pty=None, prompts=(), resources=None, priority=0, timeout=None, max_retries=0)
manager.add(job)
manager.start()
receipt = manager.control(job.id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None)
receipt = job.send(text, *, newline=True, secret=False, expected_attempt=None)
receipt = job.stop(*, source="user", reason=None, expected_attempt=None, force=False)
handle = ProcessHandle(cmd, *, cwd=None, env=None, encoding="utf-8", pty=False, pty_rows=None, pty_cols=None)
```

## Description

- `JobManager(...)` creates a scheduler with worker limits, named resource capacities, history depth, and a priority scheduling mode (`"backfill"`, `"strict"`, or `"tier_strict"`).
- `with JobManager(...) as manager:` enters the manager context and guarantees `manager.close()` runs on exit to stop active jobs, drain output, and close receivers.
- `Job(name, cmd, ...)` defines a command string or argument sequence together with environment, encoding, prompt tokens, resource needs, timeouts, retries, and stop policy.
- `manager.add(job)` registers a job definition; `manager.start()` validates all registered jobs, freezes their definitions, and starts background dispatch.
- `manager.control(...)` and `job.send(...)` / `job.stop(...)` issue guarded process controls that verify `expected_attempt` and `expected_submission` and return a `ControlReceipt`.
- `ProcessHandle(cmd, ...)` exposes the low-level one-shot subprocess transport with merged stdout/stderr, non-blocking bounded writes, and process-tree cleanup.

## Examples

### 1. Minimal Example: Run One Command and Check Its Verdict

Register a Python one-liner, start the manager, wait for the attempt to finalize, and inspect the captured logs and `ProcessVerdict`.

```python
import sys
import time
from rpkbin.jobcore import Job, JobManager, JobStatus, ProcessVerdict

with JobManager(max_workers=2) as manager:
    job = manager.add(Job("probe", [sys.executable, "-u", "-c", "print('ready')"], pty=False))
    manager.start()

    deadline = time.monotonic() + 10.0
    while not job.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("job did not finish")
        time.sleep(0.01)

    assert job.status is JobStatus.DONE
    assert job.returncode == 0
    assert job.result.verdict is ProcessVerdict.NA
    print("logs:", job.logs())
    print("verdict:", job.result.verdict.value)
```

Output:
```text
logs: ['ready']
verdict: N/A
```

### 2. Progressive Example: Context Manager, Queue Hold, and Inspection

Hold a lower-priority job before starting, inspect `repr(manager)` and the `HELD` pending code in `manager.snapshot()`, then unhold it so both jobs finish cleanly.

```python
import sys
import time
from rpkbin.jobcore import Job, JobManager

with JobManager(max_workers=1, resources={"license": 1}) as manager:
    first = manager.add(Job("first", [sys.executable, "-u", "-c", "print('one')"], pty=False, resources={"license": 1}, priority=20))
    second = manager.add(Job("second", [sys.executable, "-u", "-c", "print('two')"], pty=False, resources={"license": 1}, priority=10))

    assert manager.set_held(second, True).accepted
    print("manager:", repr(manager))
    print("pending:", manager.explain_pending(second))
    snap = manager.snapshot(selected_job_id=second.id)
    print("code:", snap["detail"]["pending_code"])

    manager.start()
    deadline = time.monotonic() + 10.0
    while not first.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("first job timed out")
        time.sleep(0.01)

    assert second.status.value == "PENDING"
    assert manager.set_held(second, False).accepted
    while not second.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("second job timed out")
        time.sleep(0.01)

    print("outputs:", first.logs(), second.logs())
```

Output:
```text
manager: JobManager(jobs=2, active=0/1, started=False, paused=True, closed=False)
pending: Held (excluded from queue)
code: HELD
outputs: ['one'] ['two']
```

## Tips and Limitations

- **OS transport defaults**: `Job` defaults `pty=None` to `True` on POSIX (using a pseudo-terminal) and `False` on Windows (using pipes). Passing `pty=True` on Windows raises `ValueError`. On Windows, `send_signal()` supports only `signal.CTRL_BREAK_EVENT`; use `stop()` or `kill()` to terminate the process tree.
- **ProcessVerdict vs. test pass/fail**: `ProcessVerdict.NA` means the process exited `0` without errors, timeouts, or stop requests. `rpkbin.jobcore` does not grade log content as `PASS`; attach `rpkbin.flow` via `rpkbin.wave` when output content decides pass or fail.
- **Compatibility**: Pure Python standard library, requires Python `>= 3.11`, offline. Examples above were verified on Windows with Python 3.13.

## See Also

- [Usage Scenarios](scenarios.md)
- [API Reference](reference.md)
- [rpkbin.flow](../flow/README.md)
- [Wave](../wave/README.md)
- [Documentation Index](../README.md)
