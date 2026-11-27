# History and SQLite Archives

How Wave stores runs in readable SQLite archive files, isolates scratch writes on network filesystems, prunes expired runs, and exposes offline queries.

## Archive Naming and Location

Every archived workspace creates one SQLite file using a sortable, human-readable filename stem:

```text
YYYYMMDD_HHMMSS_<label>_<run_id[:8]>.sqlite3
```

- `YYYYMMDD_HHMMSS`: Local creation timestamp of the archive (`time.localtime`).
- `<label>`: Sanitized stem of `source_path` (`[A-Za-z0-9_-]`, up to 40 characters; defaults to `"run"` when launched without a source file path).
- `<run_id[:8]>`: First 8 hex characters of the 32-char UUID `run_id`.
- **Legacy compatibility**: `RunArchive.catalog` still discovers and opens older 32-hex filenames (`<32-hex>.sqlite3`).

When `--archive-dir` (`RPK_WAVE_ARCHIVE_DIR`) is unset, Wave writes persisted archives to the local OS user state folder:

- **Windows**: `%LOCALAPPDATA%\rpk-wave\archives`
- **POSIX**: `$XDG_STATE_HOME/rpk-wave/archives` (or `~/.local/state/rpk-wave/archives`)

## Retention Modes and Dispositions

Choose how long archives are kept via `--archive-mode` (`RPK_WAVE_ARCHIVE_MODE`) or `WaveWorkspace(archive_mode=...)`:

| Mode | Where Active Writes Go | On Clean Close | Health `disposition` Values |
| --- | --- | --- | --- |
| `persist` (default) | Target `archive_dir` (or local `temp_dir` when staged) | Always kept (unless zero jobs started -> `"discarded_unstarted"`) | `"saved"`, `"discarded_unstarted"`, `"move_failed"` |
| `keep_on_fail` (`keep-on-fail`) | Scratch subdir in `temp_dir` | Moved to `archive_dir` if run failed or is incomplete; deleted if `PASS` | `"saved_on_failure"`, `"discarded_pass"`, `"discarded_unstarted"`, `"move_failed"` |
| `ephemeral` (`--no-save`) | Scratch subdir in `temp_dir` | Always removed along with `-wal`/`-shm` sidecars | `"discarded_ephemeral"` |

## Network Filesystem Staging (`temp_dir`)

Writing active SQLite WAL databases directly on shared NFS or SMB mounts often causes severe lock contention or `-wal` corruption across nodes. Wave prevents this automatically when `stage_on_network_fs=True` (default):

1. On POSIX hosts, Wave checks `/proc/mounts` for network filesystem types (`nfs`, `nfs4`, `cifs`, `smb3`, `smbfs`, `fuse.sshfs`, `afs`, `ceph`, `glusterfs`).
2. If `archive_dir` resides on one of those mounts and `temp_dir` points to a different local filesystem, active writes go to a per-user scratch folder in `temp_dir` (`rpk-wave-uid<UID>` or `rpk-wave-<user>`, created with `0o700` permissions when using the system temp default).
3. On clean `WaveWorkspace.close()`, Wave checkpoints the WAL (`TRUNCATE`), switches `journal_mode=DELETE`, and atomically moves (`copy2` + `os.replace`) the single self-contained `.sqlite3` file into `archive_dir`.

## Automatic Age-Based Pruning

Set `--retention-days`, `--pass-retention-days`, and `--fail-retention-days` (or matching `RPK_WAVE_*` env vars / `WaveWorkspace` arguments) to prune stale closed archives whenever a workspace enables archival, or call `prune_archives(...)` offline:

- Expiration reads `closed_at` (falling back to `created`) and `run_outcome` (`PASS`, `FAIL`, or `UNSTARTED`) directly inside each archive's `metadata` table rather than trusting filesystem `mtime`.
- Outcome-specific windows (`pass_retention_days`, `fail_retention_days`) override generic `retention_days`.
- Active runs (`exclude_run_id`), unclosed/incomplete databases (`closed_cleanly` missing or false without outcome closure), and unrelated `.sqlite3` files are skipped safely.
- When deleting an expired archive, Wave also removes its `-wal` and `-shm` sidecar files.

## Preview Attempts and Job Removal

During Preview mode (before pressing **Start**), jobs removed with **Remove** (`remove_job`) also delete their unstarted Preview attempt rows (`delete_unstarted_attempt`). Restoring a job (`restore_job`) recreates the Preview attempt row and reattaches its `Flow` so the archive matches your edited run plan.

## Examples

### 1. Minimal Example

Run a `keep_on_fail` batch targeting a local `archive_dir` and inspect `archive_health` after a clean pass:

```python
import sys
import tempfile
from pathlib import Path

from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace, run_headless

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "archives"
    scratch = Path(tmp) / "scratch"
    ws = WaveWorkspace(archive_dir=target, archive_mode="keep_on_fail", temp_dir=scratch)
    ws.add(Job("ok_job", [sys.executable, "-c", "print('done')"]))
    rc = run_headless(ws)
    health = dict(ws.archive_health)
    ws.close()
    saved = list(target.glob("*.sqlite3"))
    print("rc:", rc, "disposition:", health["disposition"], "saved files:", len(saved))
```

Output (after the Wave summary banner):

```text
rc: 0 disposition: discarded_pass saved files: 0
```

### 2. Progressive Example

Inspect saved archives with `RunArchive.catalog`, page raw logs, read full chunked lines via `read_text`, and invoke `prune_archives`:

```python
import sys
import tempfile
from pathlib import Path

from rpkbin.jobcore import Job
from rpkbin.wave import RunArchive, WaveWorkspace, prune_archives, run_headless

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "archives"
    ws = WaveWorkspace(archive_dir=target, archive_mode="persist")
    ws.add(Job("step", [sys.executable, "-c", "print('alpha\\nbeta\\ngamma')"]))
    run_headless(ws)
    ws.close()

    cat = RunArchive.catalog(target)
    entry = cat["rows"][0]
    with RunArchive(entry["path"]) as ar:
        att = ar.attempts()["rows"][0]
        page = ar.page(att["id"], att["attempt_id"], limit=10)
        seqs = [row["seq"] for row in page["rows"]]
        first_line = ar.read_text(att["id"], att["attempt_id"], seqs[0])
        print("archive:", Path(entry["path"]).name[:15], "lines:", len(seqs), "first:", first_line["text"])

    print("prune:", prune_archives(target, pass_retention_days=7))
```

Output (after the Wave summary banner):

```text
archive: 20261126_232000 lines: 3 first: alpha
prune: {'deleted': [], 'skipped': 1, 'errors': 0}
```

(The timestamp prefix in the printed filename reflects your local clock.)

## See Also

- [Wave Overview](README.md)
- [Architecture](architecture.md)
- [rpk-wave CLI Reference](reference/cli.md)
- [Python API Reference](reference/python.md)
