# Wave Architecture, Contracts, and Design Limits

[繁體中文](../../zh/wave/architecture.md) · [Documentation Index](../README.md) · [API Reference](reference/python.md)

This document details the architectural boundaries between Wave and JobManager, lifecycle contracts, verdict determination rules, and platform process models.

---

## 1. Division of Responsibilities: Wave vs. JobManager

```
+-------------------------------------------------------------------+
|                            User Tier                              |
|           Python Work File (work.py) / CLI / Web Browser           |
+---------------------------------+---------------------------------+
                                  |
+---------------------------------v---------------------------------+
|                        Wave Entry Point                           |
|  - Headless batch runner (cli.py: run_headless)                   |
|  - Local Web GUI server (web.py: WaveServer)                      |
|  - File loader & definition scope (cli.py: load_work_file)        |
+---------------------------------+---------------------------------+
                                  | Invokes / Queries
+---------------------------------v---------------------------------+
|                   JobManager Scheduling Core                      |
|  - Concurrency control (max_workers) & fixed quotas (resources)   |
|  - Priority queue scheduling (priority / FIFO)                    |
|  - Log decoding and rule matching (Track / Check / Subscriptions) |
|  - Verdict aggregation (Report / Result) & bounded history        |
+---------------------------------+---------------------------------+
                                  | Abstraction
+---------------------------------v---------------------------------+
|                    Process Layer (_process.py)                    |
|  - POSIX: PTY, Process Groups (setsid), SIGTERM/SIGKILL           |
|  - Windows: Anonymous Pipes, Job Object & Gated Launcher Cleanup  |
+-------------------------------------------------------------------+
```

- **Wave is the Product Suite**: `JobManager` is the shared scheduling engine; Wave provides the CLI, headless runner, and local GUI. Both entry points share identical state management and verdict resolution.
- **Single Source of Truth**: `JobManager` and `Job` maintain all execution states, attempt records, and verdicts. The GUI and CLI are strictly presentation and control clients.

---

## 2. Lifecycle Contracts: Frozen Definitions & Attempt Isolation

### Frozen Definitions
1. **Declaration**: Jobs and rules (`track`, `check`, `on`) are declared in Python work files.
2. **Preview**: `load_work_file()` executes definitions via `runpy.run_path()` with standard Python user privileges inside `definition_scope`. This scope specifically prevents accidental calls to `mgr.start()` or `mgr.resume()` inside the work file; **it is not a security sandbox**.
3. **Dispatch & Freezing**: Once `start()` or `validate()` is called, definitions (`cmd`, `env`, `resources`, monitoring rules) are frozen. Modifying definitions during execution requires instantiating a new `Job`.

### Attempt Isolation
Every execution (initial run, automatic retry, or manual rerun) creates an independent, immutable **Attempt**:
- Each attempt receives a globally unique `attempt_id`.
- Callback arguments (`bound_job`) bind strictly to their originating attempt. Commands cannot leak across attempts or follow a rerun.
- Verdict reports (`Report`) are frozen recursively using MappingProxy and Tuples.

---

## 3. State vs. Result Verdict Determination Matrix

Wave strictly separates **Process State (`JobStatus`)** from **Verification Outcome (`Result`)**. When finalizing an Attempt and computing the final Result, the core evaluates verdicts in `Job._verdict` using the following exact priority:

1. **Priority 1 (FAIL Verdict)**:
   - If execution failed to start (`execution_failed`), the job timed out (`timed_out`), any declared check is in `FAIL` state, or the process exited non-zero without a stop request (`returncode != 0 and not stop_requested`), the verdict is **`Result.FAIL`**.
   - **Key Invariant**: If a check was already determined to be `FAIL` before the job was stopped, the final verdict remains **`Result.FAIL`**; it is never overwritten by `INCOMPLETE`.
2. **Priority 2 (ERROR Verdict)**:
   - If no FAIL condition occurred, but internal errors occurred during execution (`error_count > 0`, such as regex errors or unhandled exceptions in callbacks), the verdict is **`Result.ERROR`**.
3. **Priority 3 (INCOMPLETE Verdict)**:
   - If no higher-priority condition matched, but a stop was requested (`stop_requested`) or any check remains `INCOMPLETE`, the verdict is **`Result.INCOMPLETE`**.
   - **Key Invariant**: Even if all checks were already satisfied (`OK`), if the job was stopped before finishing naturally, the verdict is **`Result.INCOMPLETE`**, not `PASS`.
4. **Priority 4 (PASS or N/A Verdict)**:
   - The process exited cleanly with code 0 without errors:
     - If checks were declared: **`Result.PASS`**.
     - If no checks were declared: **`Result.N/A`**.

| Exit Code | Declared Checks | Error Count | Stop Requested | State | Result | Notes |
| :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **0** | All `OK` | 0 | No | `DONE` | **`PASS`** | Clean execution with verified checks. |
| **0** | Any `FAIL` | Any | No | `DONE` | **`FAIL`** | Normal exit, but checks failed. |
| **0** | None declared | 0 | No | `DONE` | **`N/A`** | Normal exit without validation criteria. |
| **Non-0** | Any | Any | No | `FAILED` | **`FAIL`** | Subprocess error or non-zero exit. |
| Any | Check FAIL confirmed before stop | Any | Yes | `CANCELLED` | **`FAIL`** | Stopped, but existing FAIL evidence is preserved. |
| Any | No FAIL, all checks were OK | 0 | Yes | `CANCELLED` | **`INCOMPLETE`** | Checks passed, but terminated early before clean natural exit. |
| Any | No FAIL, checks not all OK | 0 | Yes | `CANCELLED` | **`INCOMPLETE`** | Cancelled mid-run before checks completed. |
| Any | No confirmed FAIL | $> 0$ | Any | Any | **`ERROR`** | Monitoring/control failed; stopping does not erase the error. |

These are common cases after excluding startup failure, timeout, and other higher-priority FAIL conditions. Stop rows assume none of those conditions occurred; a non-zero exit without a stop request still takes FAIL precedence.

> [!IMPORTANT]
> **Core Invariants**:
> 1. Process exit 0 does not imply `PASS`.
> 2. Once a check resolves to `FAIL`, that failure is preserved permanently and cannot be masked by later monitoring errors or stop requests.

---

## 4. Bounded Memory Design

To support long-running processes without unbounded memory growth:

1. **Log Ring Buffers (`max_log_lines`)**: Each attempt maintains a bounded ring buffer (default 10000 lines). `total_lines` tracks cumulative line count.
2. **Independent Evidence Preservation**: When a check triggers, its matching line number and text are snapshot into the check report. Evidence is never lost when older lines are evicted from the ring buffer.
3. **Bounded Historical Attempts (`max_history`)**: Managers cap preserved historical attempts per job (default 20). Excess attempts are dropped from memory while reporting truncation counts.

---

## 5. Platform Process Models

| Platform | Terminal / I/O Model | Signal Handling | Process Tree Cleanup |
| :--- | :--- | :--- | :--- |
| **POSIX** | Pseudo-terminal (**PTY**) by default for line-buffered stdout. `pty=False` falls back to pipes. | Process groups via `setsid()`. Signals target the entire group. | Eliminates orphan processes by signalling the group. |
| **Windows** | Anonymous pipes. | Console control events or termination calls. | Windows Job Object (`SetInformationJobObject` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) combined with a gated launcher guarantees all child and grandchild processes terminate automatically when the job closes. |

> [!NOTE]
> POSIX PTY and signal behaviors should be verified on a native Linux host; skipped tests under Windows do not imply that Linux functionality has passed verification.

---

## 6. Monitor Fault Isolation and Callback Latency

### Monitor Fault Isolation
When a user-registered monitor (check, warning, track) or action callback raises an unhandled exception:
- Wave records the exception type, message, source, and associated output evidence in `job.result.errors` and snapshot `errors`; full Python tracebacks are not promised.
- **Disable the Faulty Item for This Attempt**: Other monitors and the process continue by default. Retry/rerun attempts enable their monitors again. Subscribe with `job.on(job.events.error, stop=True)` if an error should stop execution.
- **PASS Disqualification**: Because `error_count > 0`, the job can never achieve `Result.PASS`. The highest achievable verdict is `Result.ERROR` (or `Result.FAIL` if checks failed).

### Callback Latency & Non-Blocking Requirement
- **Synchronous Execution**: Callbacks such as `job.on(Event.LOG, callback=...)` run synchronously on the event-triggering thread. Event processing shares a lock within one manager, so blocking can also delay other jobs' monitoring and controls.
- **No Heavy Operations**: Never perform long-running blocking operations inside callbacks (e.g., `time.sleep()`, heavy disk I/O, synchronous HTTP calls). Doing so stalls buffer consumption, creating backpressure that can deadlock the child process.
- **Asynchronous Offloading**: For heavy work, update lightweight state within the callback or enqueue tasks into your own worker thread.

After an attempt is finalized, exceptions in completion notifications are recorded as diagnostics. They do not change its completed Result or trigger another error event.
