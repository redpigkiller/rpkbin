# rpkbin.wave Python API Reference

Python classes, functions, job controls, flow definitions, and local HTTP endpoints exported by `rpkbin.wave`, `rpkbin.flow`, and `rpkbin.jobcore`.

## Syntax

```python
from rpkbin.wave import (
    ArchivePolicy,
    FileSource,
    ReanalysisSource,
    RunArchive,
    SourceResult,
    TailSource,
    WaveServer,
    WaveWorkspace,
    build_demo_workspace,
    launch,
    load_work_file,
    main,
    prune_archives,
    run_headless,
)
from rpkbin.wave.columns import configure_columns
```

## Description

`rpkbin.wave` coordinates job scheduling, streaming log analysis, waveform/chart projection, and SQLite run persistence. Work files build a `WaveWorkspace`, register `Job` objects or log sources paired with `Flow` objects, and optionally pass custom `ArchivePolicy` or column definitions before returning the workspace to `launch` or `run_headless`.

---

## `WaveWorkspace`

Container binding a `JobManager`, per-job `Flow` specifications, background log sources, and run archive configuration.

### Creation

```python
ws = WaveWorkspace(
    manager=None,
    *,
    columns=None,
    archive_dir=None,
    archive_mode=None,
    temp_dir=None,
    retention_days=None,
    pass_retention_days=None,
    fail_retention_days=None,
    stage_on_network_fs=True,
)
```

#### Input Arguments and Name-Value Arguments

| Parameter | Default | Description |
| --- | --- | --- |
| `manager` | `None` | Optional existing `JobManager`. When omitted, creates `JobManager()`. |
| `columns` | `None` | Optional UI column specifications passed to `configure_columns(manager, columns)`. |
| `archive_dir` | `None` | Target `.sqlite3` archive directory. Passing a non-`None` path immediately opens the archive via `enable_archive`. |
| `archive_mode` | `None` | Retention mode (`"persist"`, `"keep_on_fail"`, `"ephemeral"`). Falls back to `RPK_WAVE_ARCHIVE_MODE`, then `"persist"`. |
| `temp_dir` | `None` | Scratch directory for `ephemeral`/`keep_on_fail` runs and NFS staging. Falls back to `RPK_WAVE_TEMP_DIR` or per-user system temp dir. |
| `retention_days` | `None` | Days to keep closed archives (`float >= 0`). Falls back to `RPK_WAVE_RETENTION_DAYS`. |
| `pass_retention_days` | `None` | Days to keep closed `PASS` archives. Overrides `retention_days`. Falls back to `RPK_WAVE_PASS_RETENTION_DAYS`. |
| `fail_retention_days` | `None` | Days to keep closed `FAIL`/incomplete archives. Overrides `retention_days`. Falls back to `RPK_WAVE_FAIL_RETENTION_DAYS`. |
| `stage_on_network_fs` | `True` | When `True`, POSIX writes on network filesystems (`nfs`, `cifs`, `smb3`, etc.) stage SQLite WAL files in `temp_dir` and copy atomically on clean close. |

### Properties

| Property | Type | Description |
| --- | --- | --- |
| `started` | `bool` | Whether workspace execution has started (`False` during Preview). |
| `archive_catalog_dir` | `Path` | Resolved target archive directory for listing saved runs. |
| `archive_health` | `dict[str, Any]` | Archive status dictionary containing `enabled`, `error`, `mode`, `target_dir`, `temp_dir`, `staged`, `disposition` (`"saved"`, `"saved_on_failure"`, `"discarded_ephemeral"`, `"discarded_pass"`, `"discarded_unstarted"`, `"move_failed"`, or `None`), `pruned_count`, plus `run_id`, `path`, `closed_cleanly`, `status`, and `complete` when an archive is open. |
| `watcher_error` | `str \| None` | First background log-source watcher exception message, if any. |
| `jobs` | `tuple[Job, ...]` | Registered active jobs (delegated from `JobManager`). |
| `removed_jobs` | `tuple[Job, ...]` | Soft-removed jobs available for `restore_job` (delegated from `JobManager`). |
| `paused`, `ready`, `max_workers`, `max_history`, `scheduling_mode` | Various | Queue configuration and runtime state delegated from `JobManager`. |

### Object Functions

| Method | Description |
| --- | --- |
| `add(job, *, flow=None)` | Register a single `Job` and optional `Flow`. |
| `add_many(jobs, *, flow=None)` | Register multiple jobs sharing the same optional `Flow`. |
| `add_source(source, *, flow=None, name=None)` | Attach a `FileSource`, `TailSource`, or `ReanalysisSource`. |
| `add_paths(paths, *, once=False, globs=("*.log",), recursive=False, flow=None, **source_options)` | Convenience wrapper attaching file/directory sources. |
| `configure_archive(*, archive_dir=..., archive_mode=..., temp_dir=..., retention_days=..., pass_retention_days=..., fail_retention_days=..., stage_on_network_fs=..., override=False) -> ArchivePolicy` | Update archive policy before `start()`. Pass `override=True` when applying CLI/environment overrides over work-file defaults. |
| `enable_archive(directory=None, *, source_path=None)` | Initialize SQLite archive opening and startup retention pruning. |
| `flow_for(job)` | Return the bound `Flow` for a `Job` or `None`. |
| `start()`, `resume()`, `validate()` | Start execution, release queue pause, or validate work definitions. |
| `snapshot(*, selected_job_id=None, attempt_id=None, include_logs=True)` | Return JSON-serializable state snapshot of queue, jobs, flows, and archive health. |
| `stop_sources(job_id=None)` | Signal background source threads to stop. |
| `close()` | Close sources, drain `JobManager`, flush/finalize SQLite archive, and apply retention disposition (`keep_on_fail` / `ephemeral` / staging move). |
| `remove_job(job_id, *, expected_attempt=None, expected_submission=None)` | Remove an unstarted or finalized job from the active list and move its unstarted Preview attempt out of the archive (`_removed_flows`). |
| `restore_job(job_id, *, expected_attempt=None, expected_submission=None)` | Restore a previously removed job from `removed_jobs`, reattach its `Flow`, and recreate its Preview attempt row. |
| `control(...)`, `pause()`, `stop_all(...)`, `rerun(...)`, `set_priority(...)`, `set_held(...)`, `reorder_queue(...)`, `set_max_log_lines(...)`, `set_max_history(...)`, `update_settings(...)`, `explain_pending(...)`, `add_log_listener(...)`, `get_job(...)` | Core `JobManager` control operations forwarded through `WaveWorkspace`. |

---

## `WaveServer` and `launch`

Local HTTP/UI server hosting the static GUI and REST/SSE endpoints.

### Syntax

```python
srv = WaveServer(
    workspace=None,
    *,
    host="127.0.0.1",
    port=0,
    source_path=None,
    archive_path=None,
    archive_policy=None,
)

srv = launch(
    workspace=None,
    *,
    host="127.0.0.1",
    port=0,
    open_browser=False,
    block=True,
    source_path=None,
    archive_path=None,
    archive_policy=None,
)
```

### Properties and Object Functions

- `srv.port`: Bound TCP port (`int`).
- `srv.url`: Base `http://<host>:<port>/` string.
- `srv.start(*, open_browser=False) -> str`: Start background HTTP server and return `url`.
- `srv.stop() -> None`: Stop HTTP server and close any bound workspace resources.
- `archive_policy`: Optional `dict` (or policy fields) merged into workspace configuration before opening the workspace archive. Only loopback hosts (`127.0.0.1`, `localhost`, `::1`) are accepted. Note CLI `--version` (`-V`) prints the package version (`rpk-wave 0.3.0`) from the command line.

---

## `ArchivePolicy` and `prune_archives`

Frozen dataclass describing SQLite archive lifecycle rules, plus offline metadata-based archive pruning.

### Syntax

```python
policy = ArchivePolicy(
    mode="persist",
    archive_dir=None,
    temp_dir=None,
    retention_days=None,
    pass_retention_days=None,
    fail_retention_days=None,
    stage_on_network_fs=True,
)

summary = prune_archives(
    directory,
    *,
    pass_retention_days=None,
    fail_retention_days=None,
    retention_days=None,
    exclude_run_id=None,
    now=None,
)
```

### Object Functions and Output

- `policy.resolved_archive_dir() -> Path`: Returns explicit `archive_dir`, else `RPK_WAVE_ARCHIVE_DIR`, else OS user state dir.
- `policy.resolved_temp_dir() -> Path`: Returns explicit `temp_dir`, else `RPK_WAVE_TEMP_DIR`, else private per-user system temp dir.
- `policy.effective_pass_retention_days() -> float | None`: Returns `pass_retention_days` when set, otherwise `retention_days`.
- `policy.effective_fail_retention_days() -> float | None`: Returns `fail_retention_days` when set, otherwise `retention_days`.
- `policy.with_overrides(**kwargs) -> ArchivePolicy`: Returns a copy updating specified fields. `mode` normalizes hyphens to underscores (`"keep-on-fail"` becomes `"keep_on_fail"`).
- `prune_archives(...)` returns `{"deleted": list[str], "skipped": int, "errors": int}`. Age checks inspect SQLite `metadata` (`closed_at`, falling back to `created`) and `run_outcome` (`PASS` vs `FAIL`/incomplete) rather than filesystem `mtime`, skip unclosed or excluded runs and non-Wave `.sqlite3` files, and delete `-wal` and `-shm` sidecar files alongside expired archives.

---

## `RunArchive`

SQLite reader/writer storing raw logs, flow publications, report names, automation history, chart series, and VCD exports.

### Creation

```python
with RunArchive(path, *, create=False, run_id=None, source_path=None) as archive:
    attempts = archive.attempts()
```

### Properties and Object Functions

| Member | Signature Summary | Description |
| --- | --- | --- |
| `run_id`, `path`, `metadata`, `health` | Properties | Archive UUID, filesystem `Path`, JSON metadata dict, and health dict. |
| `catalog` (classmethod) | `catalog(directory, *, source_path=None, cursor=None, limit=50, include_all=False, max_seconds=None)` | Discover valid Wave `.sqlite3` archives sorted newest first. Recognizes both `YYYYMMDD_HHMMSS_<label>_<run_id[:8]>.sqlite3` and legacy 32-hex filenames. |
| `attempts` | `attempts(job_id=None, *, before=None, limit=100)` | Paginated list of saved attempts. |
| `attempt` | `attempt(job_id, attempt_id, *, preview=False)` | Fetch full attempt metadata, counters, and summary. |
| `page` | `page(job_id, attempt_id, *, limit=100, cursor=None, direction="forward", start=None, end=None, around=None)` | Paginated raw log rows (`text` truncated to preview cap if long). |
| `reader` | `reader(job_id, attempt_id, *, limit=100, cursor=None, direction="forward", anchor=None, query="", regex=False, case_sensitive=False, whole_word=False, start=None, end=None)` | Search raw logs with regex/case/word/time-window filters and jump anchors. |
| `read_text` | `read_text(job_id, attempt_id, seq, *, offset=0, limit=65536)` | Read chunked full raw line text by sequence number (`seq`). |
| `flow_page`, `flow_text`, `flow_report_names` | `flow_page(..., stage=None, producer=None, kind=None, report_name=None, conditions=None, limit=100)` | Query paginated parsed records, analyzer reports, and full text payloads. |
| `flow_latest`, `flow_latest_text` | `flow_latest(job_id, attempt_id, *, stage, producer=None, report_name=None, mode="record", ...)` | Inspect latest record or merged per-field summary (`mode="fields"` in `flow_latest_text`). |
| `automation_history` | `automation_history(job_id, attempt_id, *, producer=None, after=None, before=None, limit=100)` | Paginated automation trigger and action receipts. |
| `chart_meta`, `chart_series`, `chart_window`, `chart_find` | Indexed waveform & chart queries | Query declared `ViewSpec` charts or auto-chart numeric projections (`axis="time"`, `mode="raw"`/`"buckets"`). |
| `export_vcd` | `export_vcd(job_id, attempt_id, *, view_id, as_of_seq=None, series=None, axis="time")` | Export a waveform view as VCD formatted text (`str`). |
| `replay`, `save_attempt`, `delete_unstarted_attempt`, `set_outcome`, `record_batch`, `bind_source`, `flush`, `close` | Lifecycle & write helpers | Open replay context (`replay(attempt_id)`), manage Preview rows, update outcome, flush WAL, and close (`clean=True`). |

---

## Sources and Work File Utilities

| Symbol | Signature | Description |
| --- | --- | --- |
| `FileSource` | `FileSource(path, *, encoding="utf-8", decode_errors="replace", chunk_size=65536, automation=None)` | Single-pass file reader; `.run(flow=None, *, archive_dir=None, stop_event=None)` returns `SourceResult`. |
| `TailSource` | `TailSource(path, *, start="beginning", poll_interval=0.1, idle_timeout=None, completion_file=None, end_marker=None, final_offset=None, rotation_grace=3.0, commit_interval=1.0, **options)` | Live file tailer handling log rotation and optional completion triggers. |
| `ReanalysisSource` | `ReanalysisSource(archive, run_id, *, flow_path=None)` | Replays saved attempt lines from an existing archive against a trusted `.py` `flow_path` exposing `flow`. |
| `SourceResult` | `SourceResult(snapshot, archive_path, complete, reason, capabilities, archive_error=None)` | Result returned by standalone `.run(...)` calls on source objects. |
| `load_work_file` | `load_work_file(target, *, reload_modules=False) -> WaveWorkspace` | Import a trusted `.py` work file and retrieve its `workspace`/`manager` object. |
| `run_headless` | `run_headless(manager, *, timeout=None, source_mode=False) -> int` | Run a `WaveWorkspace` in terminal mode until completion or timeout; returns exit code `0` or `1`. |
| `build_demo_workspace` | `build_demo_workspace() -> WaveWorkspace` | Construct the finite demonstration workspace used by `--demo`. |
| `configure_columns` | `rpkbin.wave.columns.configure_columns(manager, columns)` | Attach custom table columns to the manager metadata shown in the web UI. |
| `main` | `main(argv=None) -> int` | CLI entry point backing `rpk-wave`. |

---

## Job Definition and Queue Control (`rpkbin.jobcore`)

Key `Job` and `JobManager` options relevant to interactive prompts, stopping, queue holding, and signal dispatch:

- **`Job(name, cmd, *, cwd=None, env=None, encoding="utf-8", decode_errors="replace", pty=None, prompts=(), pty_rows=None, pty_cols=None, resources=None, priority=0, timeout=None, max_retries=0, stop_policy=StopPolicy.GRACEFUL_THEN_FORCE, stop_ctrl_c=False, stop_cmd=None, stop_timeout=3.0, output_drain_timeout=30.0, max_log_lines=10000, log_file=None)`**
  - `prompts`: sequence of non-empty literal suffixes. Before a newline arrives, Wave checks whether the ANSI-stripped tail of the pending output buffer (`rstrip()`) ends with `p.rstrip()`. Matching flushes that prompt immediately as a log line so `Automation` or the UI can reply.
  - `pty`: defaults to `True` on POSIX (allocating a PTY) and `False` on Windows (pipes).
- **`JobManager.control(job_id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None) -> ControlReceipt`**
  - `action` values: `"stop"`, `"force_stop"`, `"interrupt"`, `"signal"` (requires integer `sig >= 0`), and `"send"` (`text`, `secret`, and `newline=True`).
- **Queue Hold and Soft Removal**:
  - `set_held(job_id, held, *, expected_attempt=None, expected_submission=None)`: places pending jobs in the `HELD` state (`explain_pending` reports `"Held (excluded from queue)"`), excluding them from dispatch without changing priorities.
  - `reorder_queue(targets)`: reassigns priorities according to ordered job sequence and returns `[(job_id, ControlReceipt, assigned_priority), ...]`.
  - `remove_job` / `restore_job` / `removed_jobs`: remove finished or unstarted jobs from the active queue (and clean unstarted Preview archive attempts via `WaveWorkspace`) and restore them later with their `Flow`.

---

## Flow Pipeline and Chart Views (`rpkbin.flow`)

- **`Flow(name, *, parsers=(), analyzers=(), automations=(), version="1", result=None, views=())`**
  - Registers parsing (`Parser`), analysis (`Analyzer`), automation (`Automation`), result selector (`result`), and declarative chart specs (`views`).
- **`Parser(name, parse, *, version="1", outputs=None, on_finish=None, state_factory=dict)`**
  - `parse(line, ctx)` emits parsed record dicts (`Result(...)`) per line; `on_finish(end_info, ctx)` runs on completion.
- **`Analyzer(name, *, records=(), reports=(), version="1", outputs=None, on_finish=None, on_tick=None, every=None, state_factory=dict)`**
  - Subscriptions `Binding(source, handler)` invoke callbacks for matching `Parser` records, `Analyzer` reports, `Tick(elapsed)` timers (`every`), or `EndInfo(complete, execution)` finish hooks.
- **`Automation(name, *, reports=(), on_event=None, enabled=True, state_factory=dict)`**
  - Subscribes to analyzer `Binding` rules (`reports`) and/or `on_event` events (`FlowEvent`), dispatching `ctx.actions.send(...)`, `ctx.actions.stop()`, `ctx.actions.force_stop()`, `ctx.actions.interrupt()`, or `ctx.actions.signal(...)` with `ActionOutcome` receipts.
- **`ViewSpec(id, kind, *, source, axis, series, title=None, options=None, report_name=None)`**
  - `source` accepts a `Parser`/`Analyzer` definition object or a `{"stage": ..., "producer": ...}` mapping (`"report_name"` optional for analyzers); `series` takes a mapping with `"mode": "wide"` (`"items": [...]`) or `"mode": "long"` (`"id_field"`, `"value_field"`). Class constructors: `ViewSpec.waveform(...)`, `ViewSpec.xy_line(...)`, `ViewSpec.scatter(...)`, `ViewSpec.histogram(...)`, plus `.to_json()`.
- **`FlowSession`**: runs a `Flow` in memory outside `WaveWorkspace` via `.start()`, `.feed(line)`, `.tick(elapsed)`, `.emit_event(name, data)`, `.wait_automation()`, `.finish()`, `.snapshot()`, and `.close()`.

---

## Local HTTP API Routes

When `WaveServer` is active, the web UI communicates over these primary local endpoints:

- **`GET /api/files?mode=<mode>&path=<dir>`**: directory browser supporting `mode` values `"work"` (`.py`), `"logs"` (non-archive log files), `"archive"` (`.sqlite3`), and `"rc"` (`.rc`, `.vcd`, `.json`), alongside shortcuts (`Launch Dir`, `Work File Dir`, `Archives`, `Home`).
- **`GET /api/archive`**, **`POST /api/archive/open`**, **`POST /api/archive/delete`**: list recent archives in `archive_catalog_dir`, open an archive read-only in the workspace, or permanently delete a closed archive (and sidecars) from disk.
- **`POST /api/jobs/hold`**, **`/api/jobs/reorder`**, **`/api/jobs/remove`**, **`/api/jobs/restore`**, **`/api/jobs/requeue`**: mutate queue holding, priority order, removal/restoration, and reruns.
- **`POST /api/job/action`**: dispatch interactive controls including `action="send"` (with optional `newline` boolean and `secret`) and `action="signal"` (accepting signal integer or POSIX names `SIGINT`, `SIGTERM`, `SIGTSTP`, `SIGCONT`, `SIGUSR1`, `SIGUSR2`, `SIGHUP`, `SIGQUIT`).
- **`POST /api/chart/file`**: load local `.rc`, `.vcd`, or `.json` waveform configuration data into the Charts viewer.

---

## Examples

### 1. Minimal Example

Run a finite job using `prompts`, configure a clean temporary archive directory, execute headlessly, and inspect `archive_health`:

```python
import sys
import tempfile
from pathlib import Path

from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace, run_headless

with tempfile.TemporaryDirectory() as tmp:
    archive_dir = Path(tmp) / "archives"
    ws = WaveWorkspace(archive_mode="persist")
    ws.enable_archive(archive_dir)
    ws.add(
        Job(
            "hello",
            [sys.executable, "-c", "print('boot ok')"],
            prompts=("Password:",),
        )
    )
    rc = run_headless(ws)
    health = dict(ws.archive_health)
    ws.close()
    print("rc:", rc, "disposition:", health["disposition"])
```

Output (after the Wave summary banner):

```text
rc: 0 disposition: saved
```

### 2. Progressive Example

Combine a `Flow` with `ViewSpec.waveform`, test `remove_job`/`restore_job` before start, query persisted chart series via `RunArchive`, and run `prune_archives`:

```python
import re
import sys
import tempfile
from pathlib import Path

from rpkbin.flow import Flow, Parser, ViewSpec
from rpkbin.jobcore import Job
from rpkbin.wave import RunArchive, WaveWorkspace, prune_archives, run_headless

LINE_RE = re.compile(r"t=(\d+)\s+val=(\d+)")

def parse_sig(line, ctx):
    m = LINE_RE.search(line)
    if m:
        return {"t": int(m.group(1)), "val": int(m.group(2))}

sig = Parser("sig", parse_sig)
wave_view = ViewSpec.waveform(
    "wave_main",
    source=sig,
    axis={"field": "t", "label": "Time", "unit": "ns"},
    series={"mode": "wide", "items": [{"id": "val", "field": "val", "type": "number", "label": "Value"}]},
    title="Value Trace",
)
flow = Flow("trace_flow", parsers=(sig,), views=(wave_view,))

with tempfile.TemporaryDirectory() as tmp:
    archive_dir = Path(tmp) / "archives"
    ws = WaveWorkspace(archive_mode="persist")
    ws.enable_archive(archive_dir)
    job = Job("sim", [sys.executable, "-c", "print('t=10 val=3\\nt=20 val=7')"])
    ws.add(job, flow=flow)

    ws.remove_job(job.name)
    assert len(ws.removed_jobs) == 1
    ws.restore_job(job.name)

    rc = run_headless(ws)
    db_path = Path(ws.archive_health["path"])
    run_id = ws.archive_health["run_id"]
    ws.close()

    with RunArchive(db_path) as ar:
        att = ar.attempts()["rows"][0]
        win = ar.chart_window(att["id"], att["attempt_id"], view_id="wave_main", series=["val"], lo=0, hi=50)
        print("rc:", rc, "points:", len(win["points"]))
        print("pruned:", prune_archives(archive_dir, retention_days=30, exclude_run_id=run_id))
```

Output (after the Wave summary banner):

```text
rc: 0 points: 2
pruned: {'deleted': [], 'skipped': 1, 'errors': 0}
```

## Tips and Limitations

- Call `configure_archive` before `ws.start()`. Calling it after execution begins raises `RuntimeError`.
- On Windows, `Job(..., pty=None)` uses standard pipes (`pty=False`), and POSIX named signals (`SIGTSTP`, `SIGCONT`, `SIGUSR1`, `SIGUSR2`, `SIGHUP`, `SIGQUIT`) are only registered for dispatch on POSIX hosts.
- Pass `close()` cleanly on `WaveWorkspace` or exit a `with RunArchive(...)` block so `keep_on_fail` / `ephemeral` disposition and NFS staging moves finalize properly.

## See Also

- [Wave Overview](../README.md)
- [rpk-wave CLI Reference](cli.md)
- [Flow Pipelines](../flow.md)
- [History and Archives](../history.md)
