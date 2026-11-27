# rpk-wave CLI

Command-line entry point for launching the Wave GUI, running headless batches, tailing log files, and reanalyzing saved archives.

## Syntax

```bash
rpk-wave [-h] [-V] [--host HOST] [-p PORT] [--open | --no-browser]
         [--headless] [--timeout TIMEOUT] [-j MAX_WORKERS]
         [--demo | --archive ARCHIVE] [archive-options] [target]

rpk-wave logs TARGET [TARGET ...] [--flow FLOW.py] [--once] [--glob PATTERN]
              [-r] [--encoding ENCODING] [--decode-errors {replace,strict}]
              [--start {beginning,end}] [--poll-interval SEC]
              [--commit-interval SEC] [--rotation-grace SEC]
              [--idle-timeout SEC]
              [--completion-file PATH | --end-marker TEXT | --final-offset N]
              [--headless] [--timeout TIMEOUT] [--host HOST] [-p PORT]
              [--open | --no-browser] [archive-options]

rpk-wave reanalyze ARCHIVE --flow FLOW.py [--attempt ATTEMPT_ID]
                   [--headless] [--timeout TIMEOUT] [--host HOST] [-p PORT]
                   [--open | --no-browser] [archive-options]
```

## Description

`rpk-wave` launches `rpkbin.wave` from the shell (`python -m rpkbin.wave` is equivalent). Without `--headless`, every mode opens the local web GUI in preview mode first: a Python work file is imported and displayed in the queue without launching child processes until you press Start or Resume in the browser. Adding `--headless` runs the queue or source immediately in the terminal, prints status summaries, and returns a process exit code.

Subcommands cover two common workflows outside Python work files:

- `rpk-wave logs`: ingests existing log files or follows live log updates, optionally applying a `Flow` pipeline from `--flow`.
- `rpk-wave reanalyze`: replays saved raw log lines from an existing SQLite archive against a new `Flow` definition into a fresh archive.

Python work files and `--flow` files execute arbitrary Python code. Only open files you wrote or reviewed. Passing `--archive ARCHIVE` opens a saved `.sqlite3` database read-only without executing its original work file.

## Examples

### 1. Minimal Example

Check the installed CLI version, launch the built-in finite demonstration workspace in a browser, or run a work file headlessly:

```bash
rpk-wave --version
```

Output:

```text
rpk-wave 0.3.0
```

```bash
rpk-wave --demo --open
rpk-wave work.py --headless
```

### 2. Progressive Example

Follow or batch-ingest a simulation log directory with a custom `Flow`, keep archives only when failures occur, and age out old archives automatically:

```bash
rpk-wave logs ./sim_logs --once -r --glob "*.log" --flow parse_flow.py \
  --archive-mode keep-on-fail --pass-retention-days 1 --fail-retention-days 14 \
  --headless
```

Reanalyze an archived run after fixing a parser bug without re-running the original simulation:

```bash
rpk-wave reanalyze 20261126_120000_work_ab12cd34.sqlite3 --flow fixed_flow.py --headless
```

## Options

### General and Server Options

`-V`/`--version` and `--demo`/`--archive` belong to the top-level `rpk-wave` command. The GUI, timeout, and archive flags apply across top-level execution as well as `logs` and `reanalyze`.

| Flag | Env Var | Default | Description |
| --- | --- | --- | --- |
| `-h`, `--help` | `-` | `-` | Show help message and exit. |
| `-V`, `--version` | `-` | `-` | Print installed version (`rpk-wave 0.3.0`) and exit (top-level only). |
| `target` | `-` | `None` | Positional trusted Python (`.py`) work file loaded in preview mode. |
| `--host HOST` | `-` | `127.0.0.1` | Local GUI bind address. Rejects non-loopback hosts outside localhost. |
| `-p PORT`, `--port PORT` | `-` | `0` | TCP port for the web GUI (`0` picks a free port automatically). |
| `--open` | `-` | `False` | Open the GUI URL automatically in your default browser. |
| `--no-browser` | `-` | `True` | Do not open a browser window automatically. |
| `--headless` | `-` | `False` | Start execution immediately in the terminal without opening the web server. |
| `--timeout TIMEOUT` | `-` | `None` | Headless deadline in seconds before requesting graceful shutdown. |
| `-j MAX_WORKERS`, `--max-workers MAX_WORKERS` | `-` | `None` | Override `JobManager` worker concurrency before any job starts (top-level only). |
| `--demo` | `-` | `False` | Load the built-in finite demonstration workspace (top-level only). |
| `--archive ARCHIVE` | `-` | `None` | Open a saved Wave `.sqlite3` archive read-only without importing its work file (top-level only). |

### Archive and Retention Options

These seven options control where SQLite run databases are written, when they are kept, where scratch staging happens, and how expired archives are pruned on startup.

| Flag | Env Var | Default | Description |
| --- | --- | --- | --- |
| `--archive-dir ARCHIVE_DIR` | `RPK_WAVE_ARCHIVE_DIR` | Local user state dir (`%LOCALAPPDATA%\rpk-wave\archives` on Windows, `$XDG_STATE_HOME/rpk-wave/archives` or `~/.local/state/rpk-wave/archives` on POSIX) | Target directory for persisted `.sqlite3` run archives. |
| `--archive-mode {persist,keep-on-fail,ephemeral}` | `RPK_WAVE_ARCHIVE_MODE` | `persist` | Keep every started run (`persist`), keep only failed or incomplete runs (`keep-on-fail`), or discard on clean exit (`ephemeral`). |
| `--no-save` | `RPK_WAVE_ARCHIVE_MODE=ephemeral` | `False` | Alias for `--archive-mode ephemeral`. Conflicts with explicit `persist` or `keep-on-fail`. |
| `--temp-dir TEMP_DIR` | `RPK_WAVE_TEMP_DIR` | Per-user temp subdir (`rpk-wave-uid<UID>` or `rpk-wave-<user>`) | Scratch directory for `ephemeral`, `keep-on-fail`, and automatic POSIX network-filesystem staging. |
| `--retention-days RETENTION_DAYS` | `RPK_WAVE_RETENTION_DAYS` | `None` | Delete closed archives in `archive_dir` older than this non-negative number of days (`float`). |
| `--pass-retention-days PASS_RETENTION_DAYS` | `RPK_WAVE_PASS_RETENTION_DAYS` | `None` | Delete closed `PASS` archives older than this non-negative number of days. Overrides `--retention-days`. |
| `--fail-retention-days FAIL_RETENTION_DAYS` | `RPK_WAVE_FAIL_RETENTION_DAYS` | `None` | Delete closed `FAIL` or incomplete archives older than this non-negative number of days. Overrides `--retention-days`. |

### `logs` Subcommand Options

| Flag | Env Var | Default | Description |
| --- | --- | --- | --- |
| `TARGET` | `-` | Required (1+) | One or more log files or directories to monitor or ingest. |
| `--flow FLOW` | `-` | `None` | Trusted Python file exposing a `Flow` instance named `flow`. |
| `--once` | `-` | `False` | Read existing file contents once via `FileSource` and finish. |
| `--glob PATTERN` | `-` | `*.log` | Filename pattern when scanning directory targets. |
| `-r`, `--recursive` | `-` | `False` | Recurse into subdirectories when scanning directories. |
| `--encoding ENCODING` | `-` | `utf-8` | Text decoding encoding for log bytes. |
| `--decode-errors {replace,strict}` | `-` | `replace` | Error handler when log input contains invalid byte sequences. |
| `--start {beginning,end}` | `-` | `beginning` | Initial offset when tailing live files (`TailSource`). |
| `--poll-interval POLL_INTERVAL` | `-` | `0.1` | File polling interval in seconds while tailing. |
| `--commit-interval COMMIT_INTERVAL` | `-` | `1.0` | SQLite archive flush interval in seconds during tailing. |
| `--rotation-grace ROTATION_GRACE` | `-` | `3.0` | Grace window in seconds before dropping rotated files. |
| `--idle-timeout IDLE_TIMEOUT` | `-` | `None` | Stop tailing after this many idle seconds with no new output. |
| `--completion-file COMPLETION_FILE` | `-` | `None` | Stop tailing once this sentinel file path exists (mutually exclusive end codec). |
| `--end-marker END_MARKER` | `-` | `None` | Stop tailing after reading a line matching this marker text (mutually exclusive). |
| `--final-offset FINAL_OFFSET` | `-` | `None` | Stop tailing once the watched file reaches this byte offset (mutually exclusive). |

### `reanalyze` Subcommand Options

| Flag | Env Var | Default | Description |
| --- | --- | --- | --- |
| `archive` | `-` | Required | Existing Wave `.sqlite3` archive path to read raw attempts from. |
| `--flow FLOW` | `-` | Required | Trusted Python file exposing a `Flow` instance named `flow`. |
| `--attempt`, `--run ATTEMPT` | `-` | `None` | Replay only a single saved attempt ID instead of the latest finalized attempt per job. |

## Exit Codes and Tips

| Code | Meaning |
| --- | --- |
| `0` | All jobs finished without failure, or GUI server exited normally. |
| `1` | Headless run had failed/killed/timed-out jobs, reached `--timeout`, or encountered a startup/validation error. |
| `2` | Invalid command-line syntax or conflicting flags (handled by `argparse`). |

- Explicit CLI flags take precedence over `RPK_WAVE_*` environment variables, which in turn fall back to defaults in `ArchivePolicy`.
- Use `--no-save` or `--archive-mode ephemeral` for quick experiments when you do not want `.sqlite3` databases left in your archive directory.
- On POSIX clusters with NFS/CIFS home or project directories, keep default staging enabled (`--temp-dir` pointing to local disk such as `/tmp` or node scratch) so SQLite WAL writes stay fast and corruption-free.

## See Also

- [Wave Overview](../README.md)
- [Python API Reference](python.md)
- [History and Archives](../history.md)
- [Workflow Scenarios](../scenarios.md)
