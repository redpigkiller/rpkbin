# rpk-wave CLI reference

`rpk-wave` is the console entry point declared in `pyproject.toml`. Install it with `python -m pip install -e ".[wave]"`.

## `rpk-wave init NAME`

Creates `NAME.wave.py` in the current directory.

| Option | Default | Description |
| --- | --- | --- |
| `--profile minimal\|parser\|full\|pty` | `minimal` | Select the generated starter sections. `parser` adds a stateless parser, `full` adds stateful parser and hooks, and `pty` adds a POSIX PTY example. |
| `--force`, `-f` | off | Overwrite an existing generated file. |

```bash
rpk-wave init hello --profile parser
```

Exit code `1` is returned when the target exists and `--force` was not supplied.

## `rpk-wave run WAVE_FILE`

Loads the Python wave file, starts its registered jobs, and runs the full-screen TUI when both stdin and stdout are interactive. Otherwise it runs headlessly.

| Option | Default | Description |
| --- | --- | --- |
| `--no-tui` | off | Force CI/headless execution. |
| `--workers N` | wave-file setting | Override the maximum concurrent workers; `N` must be at least `1`. |
| `--perf` | off | Print lightweight parser, hook, and TUI refresh diagnostics on exit. |
| `--tui-profile lite\|normal\|heavy` | unset | Scale TUI refresh rates and retained log limits. |

```bash
rpk-wave run hello.wave.py --no-tui --workers 4
```

The command returns `0` when the session summary is successful and a non-zero code when the batch is unsuccessful. The exact code is derived from the session/job outcome; inspect the job status and logs for the cause.

## `rpk-wave export-docs DEST`

Copies the packaged Wave documentation into `DEST/wave/`.

| Option | Default | Description |
| --- | --- | --- |
| `--force`, `-f` | off | Allow copying into a non-empty destination directory. |

```bash
rpk-wave export-docs ./generated-docs
```

Exit code `1` is returned when the packaged docs cannot be found or the destination already contains content without `--force`.

## Headless controls

When Wave runs in an interactive headless terminal, the REPL supports commands including `status`, `show <job>`, `logs <job>`, `data <job>`, `events <job>`, `pause`, `resume`, `stop`, `cancel`, `skip`, `rerun`, `send-line`, `send-key`, `send-signal`, `watch`, and `exit`. In CI/non-interactive mode it waits for completion instead of opening the REPL.
