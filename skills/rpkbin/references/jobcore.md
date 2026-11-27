# `rpkbin.jobcore` Reference

Multi-worker subprocess scheduler with resource quotas, backfill/strict priority scheduling, guarded process controls, interactive prompt automation, and process-tree containment.

## Public API

- `JobManager(*, max_workers=4, resources=None, log_dir=None, max_history=20, scheduling_mode='backfill')`
  - Context manager: `with JobManager(...) as mgr:` stops active jobs and closes handles on exit.
  - Adding & running: `add(job, *, receiver_factory=None) -> Job`, `add_many(jobs)`, `start()`, `pause()`, `resume()`, `stop_all(*, force=False, source='user', reason=None, include_held=True)`, `close()`.
  - Queue & priority: `set_held(job_id, held, ...)`, `set_priority(job_id, priority, ...)`, `reorder_queue(targets)`, `remove_job(job_id, ...)`, `restore_job(job_id, ...)`, `.removed_jobs`, `rerun(job_id, ...)`, `explain_pending(job)`.
  - Guarded control: `control(job_id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None) -> ControlReceipt` (`action` in `'send'`, `'interrupt'`, `'signal'`, `'stop'`, `'kill'`, `'rerun'`).
  - Snapshots & listeners: `snapshot(*, selected_job_id=None, attempt_id=None, include_logs=True)`, `add_log_listener(callback)`.
- `Job(name: str, cmd: str | Sequence[str], *, cwd=None, env=None, encoding='utf-8', decode_errors='replace', pty=None, prompts=(), pty_rows=None, pty_cols=None, resources=None, priority=0, timeout=None, max_retries=0, stop_policy=StopPolicy.GRACEFUL_THEN_FORCE, stop_ctrl_c=False, stop_cmd=None, stop_timeout=3.0, output_drain_timeout=30.0, max_log_lines=10000, log_file=None)`
  - Methods: `send(...)`, `send_ctrl_c(...)`, `send_signal(sig, ...)`, `stop(...)`, `kill(...)`, `logs(tail=None)`, `to_snapshot_dict(...)`.
  - Properties: `.status` (`JobStatus`), `.result` (`Report`), `.returncode`, `.attempt_id`, `.attempt_number`, `.submission_id`, `.pid`, `.elapsed`, `.history`.
- `ProcessHandle(cmd, *, cwd=None, env=None, encoding='utf-8', pty=False, pty_rows=None, pty_cols=None)` — Standalone low-level process transport (`start()`, `read()`, `write()`, `interrupt()`, `send_signal()`, `terminate()`, `kill()`, `poll()`, `close()`).
- Enums / Dataclasses: `JobStatus` (`PENDING`, `RUNNING`, `WAITING_INPUT`, `STOPPING`, `DONE`, `FAILED`, `CANCELLED`), `ProcessVerdict`, `StopPolicy`, `ControlReceipt`, `Capabilities`, `Report`.

## Gotchas

- PTY (`pty=True`) is POSIX-only; passing `pty=True` raises `ValueError` on Windows (`pty=None` auto-picks pipe on Windows / PTY when requested).
- Wait for idle completion in scripts by closing cleanly inside `with JobManager() as mgr:` or polling status/logs until terminal `JobStatus`.
- `expected_attempt` / `expected_submission` guards in `control(...)` protect async callers from mutating a rerun attempt accidentally (`ControlReceipt(status='stale', ...)`).

## Recipes

### 1. Run Prioritized Jobs with Resource Quotas & Retries

```python
import sys, time
from rpkbin.jobcore import Job, JobManager, JobStatus

with JobManager(max_workers=2, resources={"gpu": 1}) as mgr:
    j1 = mgr.add(Job("sim_a", [sys.executable, "-c", "print('A ok')"], resources={"gpu": 1}, priority=1))
    j2 = mgr.add(Job("sim_b", [sys.executable, "-c", "print('B ok')"], resources={"gpu": 1}, priority=10))
    mgr.start()
    while any(j.status in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.WAITING_INPUT, JobStatus.STOPPING) for j in (j1, j2)):
        time.sleep(0.02)
    assert j1.status is JobStatus.DONE and j2.status is JobStatus.DONE
    assert "B ok" in j2.logs()
```

### 2. Guarded Interactive Input with `control()`

```python
import sys, time
from rpkbin.jobcore import Job, JobManager, JobStatus

with JobManager(max_workers=1) as mgr:
    j = mgr.add(Job("echo", [sys.executable, "-u", "-c", "print('ASK', flush=True); print('GOT:' + input(), flush=True)"]))
    mgr.start()
    while "ASK" not in j.logs():
        time.sleep(0.02)
    receipt = mgr.control(j.id, "send", text="go", expected_attempt=j.attempt_id, expected_submission=j.submission_id)
    assert receipt.status == "accepted"
    while j.status is not JobStatus.DONE:
        time.sleep(0.02)
    assert "GOT:go" in j.logs()
```
