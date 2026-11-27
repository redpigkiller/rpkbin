# `rpkbin.wave` Reference

Combines `jobcore` and `flow` with a local loopback Web GUI, live/static log ingestion (`FileSource`, `TailSource`, `ReanalysisSource`), and SQLite `RunArchive` storage with RHEL-safe NFS staging.

## Public API

- `WaveWorkspace(manager=None, *, columns=None, archive_dir=None, archive_mode=None, temp_dir=None, retention_days=None, pass_retention_days=None, fail_retention_days=None, stage_on_network_fs=True)`
  - Context manager: `with WaveWorkspace(...) as ws:` closes workers and finalizes/moves/prunes SQLite archives cleanly.
  - Methods: `add(job, *, flow=None)`, `add_many(jobs, *, flow=None)`, `add_paths(paths, *, once=False, globs=('*.log',), recursive=False, flow=None, **source_options)`, `add_source(source, *, flow=None, name=None)`, `configure_archive(..., override=False) -> ArchivePolicy`, `enable_archive(directory=None, *, source_path=None)`, `remove_job(job_id, ...)`, `restore_job(job_id, ...)`, `start()`, `resume()`, `stop_sources()`, `snapshot(...)`, `validate()`, `close()`.
  - Properties: `.archive_catalog_dir`, `.archive_health`, `.started`, `.watcher_error`.
- `ArchivePolicy(mode='persist', archive_dir=None, temp_dir=None, retention_days=None, pass_retention_days=None, fail_retention_days=None, stage_on_network_fs=True)` — `mode`: `'persist'`, `'keep-on-fail'`, `'ephemeral'`.
- `RunArchive(path, *, create=False, run_id=None, source_path=None)` — Query/export SQLite `.sqlite3` archives (`page`, `reader`, `read_text`, `attempts`, `attempt`, `flow_page`, `flow_latest`, `chart_meta`, `chart_series`, `chart_window`, `chart_find`, `export_vcd`, `replay`, `catalog`, `close`).
- `prune_archives(directory, *, pass_retention_days=None, fail_retention_days=None, retention_days=None, exclude_run_id=None, now=None) -> dict`
- Sources: `FileSource(path, ...)`, `TailSource(path, *, start='beginning', ...)`, `ReanalysisSource(archive, run_id, *, flow_path=None)`.
- Server / helpers: `WaveServer(workspace=None, *, host='127.0.0.1', port=0, source_path=None, archive_path=None, archive_policy=None)`, `launch(...)`, `load_work_file(...)`, `build_demo_workspace()`, `run_headless(manager, *, timeout=None, source_mode=False)`.

## CLI (`rpk-wave` / `python -m rpkbin.wave`)

```bash
rpk-wave work.py --headless --timeout 60 -j 4
rpk-wave logs sim/*.log --once --headless --flow check.py
rpk-wave reanalyze run.sqlite3 --flow new_flow.py --headless
rpk-wave --archive run.sqlite3              # Open saved archive read-only in Web UI
```

Key flags & env vars: `--archive-dir` (`RPK_WAVE_ARCHIVE_DIR`), `--temp-dir` (`RPK_WAVE_TEMP_DIR`), `--archive-mode {persist,keep-on-fail,ephemeral}`, `--no-save`, `--retention-days`, `--pass-retention-days`, `--fail-retention-days`, `-V` / `--version`.

## Gotchas

- Non-loopback `--host` binds are rejected for untrusted network exposure; use `127.0.0.1` (default) or `localhost` + SSH tunnel for remote access.
- Deleting archives in Web GUI uses modal confirmation; `Ctrl+F` scopes to log search only when focus is inside the log area (`/` works anywhere outside inputs).
- On network filesystems (NFS), `stage_on_network_fs=True` avoids SQLite WAL corruption by staging in `--temp-dir` and moving atomically on close.

## Recipes

### 1. Headless `WaveWorkspace` Run with Ephemeral Archive Mode

```python
import sys, tempfile
from pathlib import Path
from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace, run_headless

with tempfile.TemporaryDirectory() as tmp:
    with WaveWorkspace(archive_dir=Path(tmp) / "arch", archive_mode="ephemeral", temp_dir=tmp) as ws:
        job = ws.add(Job("ping", [sys.executable, "-c", "print('WAVE_OK')"]))
        rc = run_headless(ws, timeout=10.0)
        assert rc == 0 and "WAVE_OK" in job.logs()
```

### 2. Inspect Closed `RunArchive` & Export VCD / Query Reader

```python
import tempfile
from pathlib import Path
from rpkbin.wave import RunArchive

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "sample.sqlite3"
    with RunArchive(db, create=True) as arch:
        with arch.record_batch():
            arch.set_outcome("PASS")
    with RunArchive(db) as reader:
        assert reader.attempts()["rows"] == []
```
