# Job Manager - Practical Concurrent Job Orchestration

[![English](https://img.shields.io/badge/Language-English-blue.svg)](job_manager.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](job_manager_zh.md)

`rpkbin.job_manager` provides a lightweight, cross-platform **Job Manager** for running shell commands (`CmdJob`) and Python callables (`FuncJob`) with bounded concurrency and resource-aware scheduling.

It is designed for real-world automation tasks such as EDA flows, CI helpers, and batch local scripts where you want safe parallelism, clear status transitions, and actionable logs.

---

## Quick Start

The recommended pattern is using `JobManager` as a context manager.

```python
from rpkbin.job_manager import JobManager, CmdJob, FuncJob, DONE


def preprocess():
    return "ok"


with JobManager(max_workers=2, resources={"gpu": 1}) as manager:
    py_job = FuncJob("preprocess", preprocess)
    sim_job = CmdJob("run_sim", "./run_sim.sh", resources={"gpu": 1})

    manager.add(py_job)
    manager.add(sim_job)
    manager.wait()  # wait until the manager is fully idle

print(py_job.status == DONE, sim_job.status == DONE)
```

Why this pattern:
- `with JobManager(...)` automatically calls `start()` on enter.
- On normal exit, it waits for jobs and then stops cleanly.
- On exception, it cancels running/pending jobs before stopping.

---

## Core Concepts

### 1. Job Types

- `CmdJob`: run shell commands (`cmd="..."`)
- `FuncJob`: run Python callables (`func=...`)

Both support:
- `priority` (higher first)
- `max_retries`
- `resources` (for scheduler admission)
- `job_id` (UUID or UUID string)

### 2. Resource-Aware Scheduling

`JobManager(resources=...)` accepts per-resource capacity as either:
- `int` (static capacity), or
- `Callable[[], int]` (dynamic capacity at runtime)

Example:

```python
flag = {"gpu": 0}

with JobManager(resources={"gpu": lambda: flag["gpu"]}) as manager:
    manager.add(CmdJob("sim", "./sim.sh", resources={"gpu": 1}))
    # job stays pending while flag["gpu"] == 0
    flag["gpu"] = 1
    manager.wait()
```

### 3. Job Status

Terminal states:
- `done`
- `failed`
- `cancelled`

Non-terminal states:
- `pending`
- `running`

Use `job.status` for lifecycle checks. For `CmdJob`, success means `job.status == "done"` and usually `job.result == 0`.

### 4. What `wait()` Actually Waits For

`wait()` has two slightly different modes:

- `manager.wait(job.id)` waits for that specific job to reach a terminal state.
- `manager.wait()` waits for the whole manager to become quiescent.

For the whole-manager case, "quiescent" means:
- every known job is already in a terminal state
- worker-side finishing/cleanup for those jobs is done
- async callbacks submitted by the manager have drained

This stronger whole-manager wait is intentional. It prevents races where an
`on_done(...)` callback submits follow-up work just after the last visible job
has finished.

> [!CAUTION]
> **Deadlock Prevention**
> Never call `manager.wait()` from within an async callback (`on_done`, `on_fail`, `on_retry`, or `on_queue_drained`). Doing so will result in a `RuntimeError` because it can cause a permanent deadlock. If you need to chain work, simply call `manager.add()` from the callback without waiting for manager quiescence.

---

## Common Workflows

### Manual Lifecycle Control (`start` / `pause` / `resume` / `wait` / `stop`)

```python
from rpkbin.job_manager import JobManager, CmdJob

manager = JobManager(max_workers=2)
manager.start()

try:
    manager.pause()

    j1 = CmdJob("step1", "echo step1")
    j2 = CmdJob("step2", "echo step2")
    manager.add(j1)
    manager.add(j2)

    manager.resume()
    manager.wait(j1.id)  # wait for this job to reach a terminal state
    manager.wait()       # wait until the manager is fully idle
finally:
    manager.stop()
```

### Command + Function Mixed Queue

```python
with JobManager(max_workers=4) as manager:
    manager.add(CmdJob("lint", "ruff check ."))
    manager.add(CmdJob("tests", "pytest -q"))
    manager.add(FuncJob("summarize", lambda: "report ready"))
    manager.wait()
```

### Retry on Flaky Work

```python
job = CmdJob("remote_step", "./maybe_flaky.sh", max_retries=2)
with JobManager(max_workers=1) as manager:
    manager.add(job)
    manager.wait(job.id)
print(job.status, job.error)
```

### Live Output and Callbacks

```python
job = CmdJob("test", "pytest -v")
job.on_done(lambda j: print(f"{j.name} done"))
job.on_fail(lambda j, err: print(f"{j.name} failed: {err}"))
job.watch(r"FAILED", lambda j, m: print("failure marker seen"))

with JobManager(max_workers=1) as manager:
    manager.add(job)
    manager.wait()

print(job.tail(20))
```

### Pause/Resume and Cancellation

```python
with JobManager(max_workers=2) as manager:
    j1 = FuncJob("slow1", lambda: ...)
    j2 = FuncJob("slow2", lambda: ...)

    manager.pause()         # stop dispatching new pending jobs
    manager.add(j1)
    manager.add(j2)
    manager.resume()        # continue dispatch

    # cancel specific job
    j2.cancel()

    # or cancel everything still pending/running
    manager.cancel_all()
```

### Update Configuration at Runtime

```python
with JobManager(max_workers=1) as manager:
    # ... add jobs
    manager.update_config(max_workers=4)  # increase concurrency
    manager.update_config(resources={"gpu": 2})
```

---

## API Reference

### JobManager

| Method | Description |
| --- | --- |
| `JobManager(max_workers=4, resources=None, log_dir=None, max_history=1000, poll_interval=0.5)` | Create manager. `resources` supports static int or callable capacities. |
| `start()` / `stop()` | Start/stop scheduling loop. `stop()` waits for running worker threads to finish. |
| `add(job)` | Enqueue a job. Raises `ValueError` for impossible resource requirements. **Note**: Raises `RuntimeError` if called after the manager has stopped. |
| `wait(target_id=None, timeout=None) -> bool` | Wait for a job or the manager to become quiescent. **Note**: Raises `RuntimeError` if called before `manager.start()` or from within an async callback. |
| `pause()` / `resume()` | Pause or resume dispatching of pending jobs. |
| `cancel_all()` | Cancel all pending/running jobs. |
| `update_config(max_workers=None, resources=None)` | Change worker count and/or resources at runtime. **Note**: Only affects future dispatches; running jobs are never interrupted. |
| `on_queue_drained(cb)` | Register callback triggered when no pending/running jobs remain. |
| `jobs()` / `pending()` / `running()` / `finished()` | Snapshot job lists by status. |
| `get(target_id)` | Get a job by UUID (or UUID string). |
| `stats()` | Return current workers/resources/jobs snapshot. **Note**: If resource capacity is a callable, it is invoked here. If it raises an exception, the capacity falls back to `0`. |

### Job (Base)

| Property / Method | Description |
| --- | --- |
| `id`, `name` | Stable identity fields. |
| `status` | One of `pending`, `running`, `done`, `failed`, `cancelled`. |
| `result`, `error` | Result payload or failure reason. |
| `logs()`, `tail(n)` | Read captured output lines. |
| `cancel()` | Cancel this job. |
| `on_log(cb)`, `on_done(cb)`, `on_fail(cb)` | Register callbacks. **Warning**: Callbacks must be lightweight. `on_log` is critical; if too slow (e.g., >0.1s), it can stall the subprocess output reader. |
| `watch(pattern, cb)` | Trigger callback on log match. **Note**: The manager issues warnings for slow callbacks (e.g., >0.1s for `on_log`). |

### CmdJob

Constructor parameters:
- `name`, `cmd`
- `job_id=None`
- `cwd=None`, `env=None`
- `priority=0`, `max_retries=0`, `resources=None`, `max_log_lines=10000`
- `flush_tokens=None` (force-flush token fragments for interactive prompts)

Extra methods:
- `send_input(text)`
- `send_signal(signum)`

### FuncJob

Constructor parameters:
- `name`, `func`
- `args=None`, `kwargs=None`
- `job_id=None`
- `priority=0`, `max_retries=0`, `resources=None`, `max_log_lines=10000`

Note:
- `FuncJob` runs in Python threads and is subject to the GIL.
- `cancel()` marks the status as `cancelled`. **Note**: Due to Python threading limitations, the underlying function cannot be forcefully stopped and will continue running in the background until it returns naturally.

---

## FAQ (Common User Mistakes)

- **Can I submit jobs one-by-one?** Yes. Call `manager.add(job)` per job, then `manager.wait(job.id)` for strict sequential confirmation.
- **Why did `manager.wait()` return later than the last job status change?** Because whole-manager waits also wait for manager-submitted async callbacks and worker finishing logic to drain.
- **I submitted the wrong command. Can I redo it?** Yes. Call `job.cancel()`, create a corrected job, then `manager.add(new_job)`.
- **Why is my job stuck in `pending`?** Usually worker/resource limits. Check `max_workers`, `resources`, and `manager.stats()`.
- **Can I stop everything quickly?** Yes. Use `manager.cancel_all()` to cancel all pending/running jobs.
- **How do I debug failures fast?** Enable `log_dir=...`, inspect `job.error`, and check `job.tail(n)`.
- **Why do I see "Slow callback detected" warnings?** Because a callback is performing heavy work (computation, waiting, or long sleeps) on a worker thread. Move heavy logic to a background worker or non-blocking mechanism.
- **Why did `wait()` throw a `RuntimeError` mentioning deadlocks?** Because you called `wait()` from inside an async callback. Instead of waiting, simply `add()` any follow-up work directly.
- **Can I change concurrency/resources while running?** Yes. Use `manager.update_config(max_workers=...)` and/or `manager.update_config(resources=...)`.

---

## Debugging Tips

- Prefer checking `job.status` over truthiness of `job.result`.
- Use `log_dir=...` to persist per-job log files.
- For Linux/macOS command jobs, keep command strings POSIX-shell compatible (`shell=True` uses system shell).
- If a job appears stuck in `pending`, inspect:
  - worker limit (`max_workers`)
  - resource caps (`resources`)
  - current snapshot from `manager.stats()`
