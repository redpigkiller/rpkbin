# Wave Python Usage Scenarios

[繁體中文](../../zh/wave/scenarios.md) · [Documentation Index](../README.md) · [API Reference](reference/python.md)

This document presents 8 runnable, progressive scenarios demonstrating how to define jobs, declare verification checks, monitor logs, and handle process lifecycles with Wave.

---

## Table of Contents

1. [Scenario 1: Single Job and Output Verification](#scenario-1-single-job-and-output-verification)
2. [Scenario 2: Concurrency Limits and Priority Scheduling](#scenario-2-concurrency-limits-and-priority-scheduling)
3. [Scenario 3: Multi-Check Verdicts and Result Determinations](#scenario-3-multi-check-verdicts-and-result-determinations)
4. [Scenario 4: Dynamic Metric Extraction (Track)](#scenario-4-dynamic-metric-extraction-track)
5. [Scenario 5: Warnings, Event Subscriptions, and Custom Actions](#scenario-5-warnings-event-subscriptions-and-custom-actions)
6. [Scenario 6: Interactive Commands: Prompts, Stdin, and Stop Policies](#scenario-6-interactive-commands-prompts-stdin-and-stop-policies)
7. [Scenario 7: Fixed Resource Quotas, Timeouts, and Automatic Retries](#scenario-7-fixed-resource-quotas-timeouts-and-automatic-retries)
8. [Scenario 8: Attempt History, Bounded Logs, and Manual Reruns](#scenario-8-attempt-history-bounded-logs-and-manual-reruns)

---

## Scenario 1: Single Job and Output Verification

### Goal
Execute a command and verify that a success marker (`SIMULATION_SUCCESS`) appears in the output while ensuring no fatal errors (`FATAL:`) are emitted. If both conditions hold and the returncode is 0, the final Result is `PASS`.

### Complete Runnable Code (`scenario1.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager()

job = mgr.add(Job(
    name="single_check",
    cmd=[sys.executable, "-u", "-c", "print('STARTING'); print('SIMULATION_SUCCESS'); print('FINISHED')"],
))

# 1. Required token: must appear before process termination
job.check("success_marker", match=r"^SIMULATION_SUCCESS$", expect=Expect.PRESENT)

# 2. Forbidden token: must never appear
job.check("no_fatal", match=r"^FATAL:", expect=Expect.ABSENT)
```

### Execution
```bash
rpk-wave scenario1.py --headless
```

### Output
```text
[Wave] Starting 1 job(s); workers=4
[child single_check #1] STARTING
[child single_check #1] SIMULATION_SUCCESS
[child single_check #1] FINISHED

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
single_check               DONE            PASS         0         0
  checks: success_marker: OK; no_fatal: OK
```

### Explanation
- `expect=Expect.PRESENT`: As soon as a matching line appears, the check state transitions to `OK`. If the process terminates without matching, it resolves to `FAIL`.
- `expect=Expect.ABSENT`: If any line matches, the check immediately resolves to `FAIL`. If the process terminates without matching, it resolves to `OK`.
- When all checks are `OK` and exit code is 0, the overall `Result` is `PASS`.

---

## Scenario 2: Concurrency Limits and Priority Scheduling

### Goal
Submit 4 jobs to a manager configured with `max_workers=2`. A critical job should preempt queued routine jobs when a worker slot becomes available.

### Complete Runnable Code (`scenario2.py`)

```python
import sys
from rpkbin.wave import Job, JobManager

# Limit concurrency to 2 active processes
mgr = JobManager(max_workers=2)

# Routine jobs with default priority=0
for i in (1, 2, 3):
    mgr.add(Job(
        f"routine_job_{i}",
        [sys.executable, "-u", "-c", f"import time; print('Routine {i} working'); time.sleep(0.4); print('Done {i}')"],
        priority=0
    ))

# Critical job with priority=10 (dispatches immediately in first batch due to highest priority)
mgr.add(Job(
    "critical_job",
    [sys.executable, "-u", "-c", "import time; print('CRITICAL TASK running'); time.sleep(0.2); print('CRITICAL Done')"],
    priority=10
))
```

### Execution
```bash
rpk-wave scenario2.py --headless
```

### Output
```text
[Wave] Starting 4 job(s); workers=2
[child critical_job #1] CRITICAL TASK running
[child routine_job_1 #1] Routine 1 working
[child critical_job #1] CRITICAL Done
[child routine_job_1 #1] Done 1
[child routine_job_2 #1] Routine 2 working
[child routine_job_3 #1] Routine 3 working
[child routine_job_2 #1] Done 2
[child routine_job_3 #1] Done 3

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
routine_job_1              DONE            N/A          0         0
routine_job_2              DONE            N/A          0         0
routine_job_3              DONE            N/A          0         0
critical_job               DONE            N/A          0         0
```

### Explanation
- The scheduler orders pending jobs by `(-priority, order)`. Even though `critical_job` was added last, its higher priority (`priority=10`) places it first in the queue. Thus, in the initial dispatch batch of 2 worker slots, `critical_job` and `routine_job_1` (first FIFO job with priority 0) launch immediately.
- Once `critical_job` finishes (0.2s) and frees a slot, `routine_job_2` launches, followed by `routine_job_3` when `routine_job_1` completes.
- Because no checks were declared, each job's `Result` is `N/A`.

---

## Scenario 3: Multi-Check Verdicts and Result Determinations

### Goal
Differentiate between **process termination state** and **verification verdict (Result)**:
1. Subprocess exits 0, but fails a required check (`Result=FAIL`).
2. Subprocess crashes with returncode 2 (`Result=FAIL`).

### Complete Runnable Code (`scenario3.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager(max_workers=2)

# Job A: Normal exit (code 0), but missing PASS_TOKEN
job_a = mgr.add(Job(
    "silent_failure",
    [sys.executable, "-u", "-c", "import sys; print('Initialization complete'); sys.exit(0)"]
))
job_a.check("token", match=r"^PASS_TOKEN$", expect=Expect.PRESENT)

# Job B: Crashes with code 2
job_b = mgr.add(Job(
    "crash_exit",
    [sys.executable, "-u", "-c", "import sys; print('Fatal memory fault'); sys.exit(2)"]
))
job_b.check("no_fatal", match=r"Fatal", expect=Expect.ABSENT)
```

### Execution
```bash
rpk-wave scenario3.py --headless
```

### Output
```text
[Wave] Starting 2 job(s); workers=2
[child silent_failure #1] Initialization complete
[child crash_exit #1] Fatal memory fault

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
silent_failure             DONE            FAIL         0         0
  checks: token: FAIL
  check token: FAIL; required condition never matched
crash_exit                 FAILED          FAIL         0         2
  checks: no_fatal: FAIL
  check no_fatal: FAIL; forbidden condition matched #1: Fatal memory fault
```

### Explanation
- `silent_failure` shows `STATE=DONE` (exit 0), but `RESULT=FAIL` because its required check was never matched.
- `crash_exit` shows `STATE=FAILED` and `RESULT=FAIL`.
- The CLI exit code is `1` because failures occurred in the batch.

---

## Scenario 4: Dynamic Metric Extraction (Track)

### Goal
Extract live metrics (such as iteration cycles and floating-point rates) from process logs and verify that target thresholds are reached.

### Complete Runnable Code (`scenario4.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager(columns=["cycle", "pass_rate"])

job = mgr.add(Job(
    "dsp_simulation",
    [sys.executable, "-u", "-c", """
import time
for c in (100, 200, 300):
    print(f'cycle={c} pass_rate=99.{c//100}', flush=True)
    time.sleep(0.1)
print('SIMULATION_DONE')
"""]
))

# Regex must contain exactly one capture group ()
cycle_track = job.track("cycle", r"cycle=(\d+)", cast=int)
rate_track = job.track("pass_rate", r"pass_rate=([0-9.]+)", cast=float)

# Declare a check evaluating tracked values
job.check(
    "reach_target_cycle",
    match=lambda job, old, new: new >= 300,
    on=cycle_track.events.changed,
    expect=Expect.PRESENT
)
job.check("completed", match=r"^SIMULATION_DONE$", expect=Expect.PRESENT)
```

### Execution
```bash
rpk-wave scenario4.py --headless
```

### Output
```text
[Wave] Starting 1 job(s); workers=4
[child dsp_simulation #1] cycle=100 pass_rate=99.1
[child dsp_simulation #1] cycle=200 pass_rate=99.2
[child dsp_simulation #1] cycle=300 pass_rate=99.3
[child dsp_simulation #1] SIMULATION_DONE

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
dsp_simulation             DONE            PASS         0         0
  checks: reach_target_cycle: OK; completed: OK
```

### Explanation
- Each time the regex matches, Wave parses the captured group using `cast` and updates `job.data[name]`.
- Tracked metrics are rendered in the GUI table as custom columns.
- `track.events.changed` provides `(old, new)` tuple arguments to callbacks or checks.

---

## Scenario 5: Warnings, Event Subscriptions, and Custom Actions

### Goal
Record non-fatal warnings without altering `Result=PASS`, and execute custom Python callbacks upon log events.

### Complete Runnable Code (`scenario5.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Event, Expect

mgr = JobManager()

job = mgr.add(Job(
    "audit_job",
    [sys.executable, "-u", "-c", """
print('WARN: deprecation notice on line 1')
print('WARN: deprecation notice on line 2')
print('MILESTONE_REACHED')
print('ALL_PASSED')
"""]
))

# 1. Record warnings: warn=True does not fail the job
job.on(
    Event.LOG,
    match=r"^WARN:",
    warn=True,
    message="Deprecation warning detected",
    name="deprecation",
    once=False
)

# 2. Custom action: invoke Python callback on milestone
def on_milestone(bound_job, line):
    bound_job.log(f"Custom auditor triggered by line: {line.strip()}")

job.on(Event.LOG, match=r"^MILESTONE_REACHED$", callback=on_milestone, name="milestone_hook")
job.check("finish", match=r"^ALL_PASSED$", expect=Expect.PRESENT)
```

### Execution
```bash
rpk-wave scenario5.py --headless
```

### Output
```text
[Wave] Starting 1 job(s); workers=4
[child audit_job #1] WARN: deprecation notice on line 1
[child audit_job #1] WARN: deprecation notice on line 2
[child audit_job #1] MILESTONE_REACHED
[Wave audit_job #1] Custom auditor triggered by line: MILESTONE_REACHED
[child audit_job #1] ALL_PASSED

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
audit_job                  DONE            PASS         2         0
  checks: finish: OK
  warning: deprecation (2 match(es))
```

### Explanation
- Warnings accumulate in `attempt.warnings` without forcing a `FAIL` verdict.
- The callback argument `bound_job` is tied to the current attempt, preventing actions from affecting future reruns.

---

## Scenario 6: Interactive Commands: Prompts, Stdin, and Stop Policies

### Goal
Automate an interactive process (e.g., GDB or an EDA shell) by detecting prompt tokens, transmitting stdin, and terminating gracefully.

### Complete Runnable Code (`scenario6.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Event, StopPolicy

mgr = JobManager()

interactive_script = """
import sys
print('ready> ', end='', flush=True)
for line in sys.stdin:
    cmd = line.strip()
    print(f'ACK: {cmd}', flush=True)
    if cmd == 'exit':
        print('BYE', flush=True)
        break
    print('ready> ', end='', flush=True)
"""

job = mgr.add(Job(
    "interactive_cli",
    [sys.executable, "-u", "-c", interactive_script],
    prompts=("ready> ",),
    stop_policy=StopPolicy.GRACEFUL_THEN_FORCE,
    stop_cmd="exit",
    stop_timeout=2.0
))

# Automatically send a command when the prompt is reached
def handle_ready(bound_job, line):
    bound_job.send("status_check")

job.on(Event.LOG, match=r"ready>", callback=handle_ready, once=True)
job.on(Event.LOG, match=r"ACK: status_check", stop=True, message="Session finished")
```

### Execution
```bash
rpk-wave scenario6.py --headless
```

### Output
```text
[Wave] Starting 1 job(s); workers=4
[child interactive_cli #1] ready>
[Wave interactive_cli #1] Input sent: status_check
[Wave interactive_cli #1] Send status: True
[child interactive_cli #1] ACK: status_check
[child interactive_cli #1] ready>
[child interactive_cli #1] ACK: exit
[child interactive_cli #1] BYE

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
interactive_cli            CANCELLED       INCOMPLETE   0         0
  stopped: monitor: Session finished
```

### Explanation
- `prompts=("ready> ",)` flags partial output lines as `WAITING_INPUT`.
- `job.send(...)` transmits bytes to stdin, emitting `Input sent: ...` before the callback continues.
- When the stop rule triggers, Wave sends `stop_cmd="exit"` over stdin to request a clean exit. Because the job was stopped externally rather than running to completion, the state is `CANCELLED` and result is `INCOMPLETE`.

---

## Scenario 7: Fixed Resource Quotas, Timeouts, and Automatic Retries

### Goal
Coordinate access to limited hardware tokens (`license_gpu`) and automatically retry non-zero exits.

### Complete Runnable Code (`scenario7.py`)

```python
import sys
import atexit
import tempfile
from pathlib import Path
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager(max_workers=3, resources={"license_gpu": 2})

# Each load owns a unique directory; retries share its counter without affecting other batches.
# Keep it alive until core exit and clean it then, leaving the working directory untouched.
_counter_dir = tempfile.TemporaryDirectory(prefix="wave-retry-")
atexit.register(_counter_dir.cleanup)
counter_file = Path(_counter_dir.name) / "counter.txt"

# Flakey simulation using a counter file to pass on retry
flakey_script = f"""
import sys
from pathlib import Path

p = Path({str(counter_file)!r})
count = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(count))

print(f"Running with 2 GPUs... Attempt #{{count}}")
if count < 2:
    print("Hardware glitch: simulating flakey exit")
    sys.exit(1)
print("SUCCESS_RUN")
"""

job = mgr.add(Job(
    "heavy_sim",
    [sys.executable, "-u", "-c", flakey_script],
    resources={"license_gpu": 2},
    max_retries=1,
    timeout=5.0
))
job.check("success", match=r"^SUCCESS_RUN$", expect=Expect.PRESENT)
```

### Execution
```bash
rpk-wave scenario7.py --headless
```

### Output
```text
[Wave] Starting 1 job(s); workers=3
[child heavy_sim #1] Running with 2 GPUs... Attempt #1
[child heavy_sim #1] Hardware glitch: simulating flakey exit
[child heavy_sim #2] Running with 2 GPUs... Attempt #2
[child heavy_sim #2] SUCCESS_RUN

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
heavy_sim                  DONE            PASS         0         0
  checks: success: OK
  latest attempt: #2; earlier history retained: 1
```

### Explanation
- When Attempt #1 fails with code 1, Wave finishes the attempt, **releases its 2 `license_gpu` slots**, creates Attempt #2, and rejoins the queue tail.
- Attempt #2 succeeds, yielding `Result=PASS`.

---

## Scenario 8: Attempt History, Bounded Logs, and Manual Reruns

### Goal
Demonstrate how bounded log ring buffers protect memory without losing verification evidence.

### Complete Runnable Code (`scenario8.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager(max_history=5)

job = mgr.add(Job(
    "spammy_job",
    [sys.executable, "-u", "-c", """
print('CRUCIAL_HEADER_PASS')
for i in range(50):
    print(f'Spam line {i}')
print('FINISHED')
"""],
    max_log_lines=10  # Retain only the last 10 lines in memory
))

job.check("header", match=r"^CRUCIAL_HEADER_PASS$", expect=Expect.PRESENT)
```

### Execution
```bash
rpk-wave scenario8.py --headless
```

### Output
```text
[Wave] Starting 1 job(s); workers=4
[child spammy_job #1] CRUCIAL_HEADER_PASS
[child spammy_job #1] Spam line 0
...
[child spammy_job #1] Spam line 49
[child spammy_job #1] FINISHED

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
spammy_job                 DONE            PASS         0         0
  checks: header: OK
```

### Explanation
- Even though older lines were evicted from the ring buffer, the check evidence (line `#1` and text) was saved independently in the report.
