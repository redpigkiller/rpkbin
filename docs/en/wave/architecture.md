# Wave Architecture

How `JobManager`, `WaveWorkspace`, `FlowSession`, `RunArchive`, and `WaveServer` separate process execution, log analysis, SQLite persistence, and the browser UI.

## Pipeline Overview

Every line emitted by a process or log source passes through a single ordered path:

```text
Process / Source -> Job / AttemptSink -> FlowSession -> RunArchive (SQLite)
                                                      -> Web State / SSE -> Browser UI
```

1. **Process & Queue Layer (`rpkbin.jobcore`)**: `JobManager` owns worker slots, resource allocations, backfill dispatch, `HELD` exclusions, priorities, and per-process output reader threads.
2. **Workspace Coordinator (`WaveWorkspace`)**: Binds each `Job` or source (`FileSource`, `TailSource`, `ReanalysisSource`) to an optional `Flow`, manages Preview vs Started states, holds soft-removed flows (`remove_job` / `restore_job`), and runs `ArchivePolicy`.
3. **Stream Processing (`rpkbin.flow`)**: `FlowSession` runs `Parser` callbacks inline per line, triggers subscribed `Analyzer` handlers and timed `Tick` events, and sends alerts to an isolated `Automation` worker thread so slow actions do not block output readers.
4. **Persistence (`RunArchive`)**: Appends raw log lines, flow records, analyzer reports, automation outcomes, and indexed `ViewSpec` chart series into SQLite batches.
5. **Local HTTP Server (`WaveServer`)**: Serves static frontend assets, REST JSON endpoints, and Server-Sent Events (SSE) for real-time updates without coupling UI rendering locks to subprocess I/O.

## Preview vs Started Lifecycle

When launched interactively without `--headless`, `WaveWorkspace` loads all jobs and attaches `Flow` bindings in Preview mode (`started == False`):

- No child processes or log source readers start automatically.
- Preview attempts are seeded in the UI and archive so you can inspect commands, reorder queue priorities, hold jobs, or remove/restore entries before pressing **Start**.
- If you exit before starting any job, `WaveWorkspace.close()` marks the unstarted archive with disposition `"discarded_unstarted"` and removes the empty file so preview checks do not clutter your archive folder.

## Threading and Lock Separation

- **No lock inversion across I/O**: Output reader threads decode UTF-8 incrementally, detect `prompts` suffixes on unterminated lines, push formatted lines into bounded in-memory ring buffers (`max_log_lines`), and write through a non-blocking attempt sink to SQLite.
- **Background automation queue**: `Automation` callbacks run on a dedicated worker thread (`ActionOutcome` is recorded back into the flow stream and archive asynchronously).
- **Buffered SQLite writes**: During active streaming (`persist` or staged runs), writes use SQLite WAL mode with periodic flushes (`commit_interval` for sources, shutdown checkpoint on clean `close()`).

## Archive Staging and Storage Layout

To prevent WAL lock corruption on shared cluster storage while keeping finalized files accessible in a shared archive directory:

- On POSIX hosts, `WaveWorkspace` checks `/proc/mounts` for network filesystems (`nfs`, `nfs4`, `cifs`, `smb3`, `smbfs`, `fuse.sshfs`, `afs`, `ceph`, `glusterfs`).
- When writing to a network-mounted target directory (`stage_on_network_fs=True`), or whenever running under `keep_on_fail` or `ephemeral` mode, Wave writes active WAL databases under a private per-user scratch directory inside `temp_dir` (`0o700` permissions when using the default system temp dir).
- On clean `close()`, Wave checkpoints the WAL (`TRUNCATE`), switches to `journal_mode=DELETE`, computes `PASS` / `FAIL` outcome, and atomically copies and replaces (`copy2` + `os.replace`) into `archive_dir` when retention rules say to keep it.

## Local Security Boundaries

- **Trusted work files**: Loading a `.py` work file imports Python code in-process. Keep `--archive` for read-only inspection of untrusted `.sqlite3` files.
- **Loopback-only HTTP server**: `WaveServer` rejects non-loopback `--host` values.
- **Scoped file browser (`/api/files`)**: Queries require an explicit `mode` (`"work"`, `"logs"`, `"archive"`, or `"rc"`) restricting visible files to matching extensions (`.py`, log files, `.sqlite3`, or `.rc`/`.vcd`/`.json`).

## See Also

- [Wave Overview](README.md)
- [Flow Pipelines](flow.md)
- [History and Archives](history.md)
- [Python API Reference](reference/python.md)
