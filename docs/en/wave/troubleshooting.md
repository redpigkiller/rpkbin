# Troubleshooting

Diagnosis and fixes for common Wave execution, UI, Flow, and archive issues.

## Work File Loads in Preview Instead of Running

- **Symptom**: Running `rpk-wave work.py` opens the browser, and all jobs sit idle without output.
- **Cause**: GUI launches enter Preview mode (`started == False`) so you can inspect jobs, hold or remove entries, and adjust settings first.
- **Fix**: Click **Start** in the top bar for GUI sessions, or pass `--headless` on the command line for immediate batch execution.

## `--host` Rejected at Startup

- **Symptom**: Startup raises an error when setting `--host 0.0.0.0` or a LAN IP.
- **Cause**: `WaveServer` rejects non-loopback bind addresses (`127.0.0.1`, `localhost`, `::1` only).
- **Fix**: Keep the loopback bind address and tunnel the port over SSH (`ssh -L 8080:127.0.0.1:<port> ...`) when working remotely.

## Interactive Prompt Does Not Trigger `Automation` or Appears Late

- **Symptom**: A child tool stops on a question without a trailing newline (`Continue? [y/N]:`), and neither the Log view nor `Automation` receives the line.
- **Cause**: Standard line buffering waits for `\n`, or block buffering holds stdout on Windows pipes (`pty=False`).
- **Fix**: Add `prompts=("Continue? [y/N]:",)` to `Job(...)` so Wave flushes matching ANSI-stripped suffixes immediately before a newline arrives. When testing Python scripts on Windows, pass `-u` (`python -u`) or call `flush=True`. When sending replies manually or from `Automation`, verify whether the target tool expects `newline=True` (default) or `newline=False`.

## `Ctrl+F` Opens Browser Find Instead of Wave Log Search

- **Symptom**: Pressing `Ctrl+F` opens the browser page finder instead of Wave's Find bar.
- **Cause**: Wave scopes `Ctrl+F` strictly to `Split` and `Inspect` layouts while the `Log` view is active and focus/pointer target is inside the Log viewer or Find bar.
- **Fix**: Click inside the Log panel or press `/` while focused in Inspect/Log, or click the Find icon directly.

## Waveform Controls Look Incomplete

- **Missing expected signals or extra signals appear**: Open **Get Signals (`g`)** and press **Defaults** to reset back to declared view defaults (or the first 8 signals in `long` mode) and clear row overrides.
- **`Expand Bits [msb:0]` is disabled or missing**: Bit expansion applies only to vectors with width `<= 64` bits.
- **Fewer slices appear than expected**: **Bit Select `[msb:lsb]`…** displays at most **16** slices per signal.
- **`Left`/`Right` arrow keys vs `n`/`N` seem to jump differently**: `Left`/`Right` arrows step Cursor A to transitions on the currently selected signal row (regardless of `By:`), whereas `n`/`N` follow the **`By:`** condition selector (`Any Edge`, `Rising`, `Falling`, `= Bus Value`).

## `Latest: Record / Merged` Toggle Looks Different Than Older Docs

- **Symptom**: The Flow panel displays a segmented `Latest:` control with `Record` and `Merged`.
- **Fix**: Use `Record` when you want only keys from the single latest record, or `Merged` when records emit partial updates and you want the newest value per key.

## Very Long Log Lines Show `[truncated]` or `Preview · read full`

- **Symptom**: Archived log rows longer than 1024 characters show a truncated preview in the main Log stream.
- **Fix**: Click **Preview · read full** on that row to inspect full **65,536-byte** chunks in the Raw Log viewer, or call `RunArchive.read_text(job_id, attempt_id, seq)` in Python.

## Archive Retention or Staging Errors (`move_failed`, Skipped Prune)

- **`archive_health["disposition"] == "move_failed"`**: Staged writes succeeded locally under `temp_dir`, but moving the finished `.sqlite3` file to `archive_dir` failed (permissions, disk full, or network outage). Recover the `.sqlite3` file from `archive_health["path"]` inside `temp_dir` before cleaning scratch folders.
- **`prune_archives` reports `skipped` files**: Pruning skips the active run (`exclude_run_id`), unclosed archives lacking closure metadata, and non-Wave `.sqlite3` files. Age checks use internal archive metadata timestamps rather than filesystem `mtime`.

## See Also

- [Wave Overview](README.md)
- [Web GUI Guide](gui.md)
- [History and Archives](history.md)
- [rpk-wave CLI Reference](reference/cli.md)
