# rpkbin.wave

Job runner, streaming log parser, waveform viewer, and SQLite archive suite for Python workflows.

## Syntax

```bash
rpk-wave work.py
rpk-wave work.py --headless
rpk-wave logs sim.log --once --flow parse_flow.py
```

```python
from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace, launch, run_headless

ws = WaveWorkspace()
ws.add(Job("build", ["python", "-V"]))
run_headless(ws)   # terminal batch execution
launch(ws)         # local browser UI
```

## Description

`rpkbin.wave` coordinates four tasks in one workspace:

1. Defining and scheduling jobs (`Job`, `JobManager`, resource reservations, `HELD` exclusions, priority reordering).
2. Parsing and analyzing output streams line by line (`Flow`, `Parser`, `Analyzer`, `Automation`, `ViewSpec`).
3. Monitoring and inspecting runs in a local browser GUI (queue controls, scoped log search, decoded flow views, waveform and XY charts).
4. Saving runs into SQLite archives (`RunArchive`) with retention policies (`persist`, `keep_on_fail`, `ephemeral`), NFS-safe staging, and offline log/flow replay (`reanalyze`).

Opening a work file without `--headless` starts the web server in preview mode so you can inspect jobs, change limits, or remove queued entries before running any command.

## Examples

### 1. Minimal Example

Run a single finite command inside a temporary archive directory and print the exit status:

```python
import sys
import tempfile
from pathlib import Path

from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace, run_headless

with tempfile.TemporaryDirectory() as tmp:
    ws = WaveWorkspace(archive_dir=Path(tmp) / "archives")
    ws.add(Job("hello", [sys.executable, "-c", "print('wave ready')"]))
    rc = run_headless(ws)
    health = dict(ws.archive_health)
    ws.close()
    print("rc:", rc, "status:", health["status"], "disposition:", health["disposition"])
```

Output (after the Wave summary banner):

```text
rc: 0 status: closed disposition: saved
```

### 2. Progressive Example

Attach a `Flow` with a `Parser` and `Analyzer` to two jobs, run headlessly, and inspect the saved analyzer report from `RunArchive`:

```python
import re
import sys
import tempfile
from pathlib import Path

from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, Parser
from rpkbin.jobcore import Job
from rpkbin.wave import RunArchive, WaveWorkspace, run_headless

WARN_RE = re.compile(r"WARN\s+(.+)")

def parse_line(line, ctx):
    m = WARN_RE.search(line)
    if m:
        return {"warn": m.group(1)}

def on_warn(rec, ctx):
    ctx.state["count"] = ctx.state.get("count", 0) + 1

def on_finish(info, ctx):
    count = ctx.state.get("count", 0)
    status = "FAIL" if count > 0 else "PASS"
    return AnalysisReport("warn_check", result=status, reason=f"{count} warnings", values={"count": count})

log_scan = Parser("log_scan", parse_line)
checker = Analyzer("checker", records=(Binding(log_scan, on_warn),), on_finish=on_finish)
flow = Flow("lint_flow", parsers=(log_scan,), analyzers=(checker,))

with tempfile.TemporaryDirectory() as tmp:
    ws = WaveWorkspace(archive_dir=Path(tmp) / "archives")
    ws.add(Job("clean", [sys.executable, "-c", "print('OK done')"]), flow=flow)
    ws.add(Job("noisy", [sys.executable, "-c", "print('WARN clock skew')"]), flow=flow)
    rc = run_headless(ws)
    db_path = Path(ws.archive_health["path"])
    ws.close()

    with RunArchive(db_path) as ar:
        for att in sorted(ar.attempts()["rows"], key=lambda r: r["name"]):
            latest = ar.flow_latest(att["id"], att["attempt_id"], stage="analyzer", report_name="warn_check")
            rep = latest["rows"][0]["data"]
            print(att["name"], rep["result"], rep["values"]["count"])
    print("batch rc:", rc)
```

Output (after the Wave summary banner):

```text
clean PASS 0
noisy FAIL 1
batch rc: 0
```

## Documentation Map

| Guide | Covers |
| --- | --- |
| [Architecture](architecture.md) | Data flow, threading, preview vs running states, local security boundaries |
| [Flow Pipelines](flow.md) | `Parser`, `Analyzer`, `Automation`, `ViewSpec` charts, offline `FlowSession` testing |
| [Web GUI Guide](gui.md) | Layout views, Queue hold/reorder, Inspect Flow/Log tools, Waveform viewer shortcuts |
| [History and Archives](history.md) | Archive naming, retention modes, NFS staging, automatic pruning, `RunArchive` queries |
| [Workflow Scenarios](scenarios.md) | Simulation batches, prompt automation, `logs`/`reanalyze` workflows, retention setup |
| [Troubleshooting](troubleshooting.md) | Common failure symptoms, diagnosis steps, and fixes |
| [CLI Reference](reference/cli.md) | Flags, `RPK_WAVE_*` environment variables, exit codes |
| [Python API Reference](reference/python.md) | Classes, methods, defaults, and HTTP endpoints |

## Tips and Limitations

- **Trusted work files**: Loading a `.py` work file executes top-level Python code in that file. Use `--archive <file>.sqlite3` when inspecting untrusted runs read-only.
- **PTY vs pipe behavior**: On POSIX hosts, `Job` allocates a PTY by default (`pty=True`); on Windows, `pty` defaults to `False` and uses pipes. Interactive programs that buffer stdout under pipes can require explicit flush or `prompts` matching.
- **Localhost binding**: `WaveServer` only binds loopback addresses (`127.0.0.1`, `localhost`, `::1`). Access from remote machines requires an SSH port forward or local terminal session.

## See Also

- [JobCore Documentation](../jobcore/README.md)
- [Flow Documentation](../flow/README.md)
- [rpk-wave CLI Reference](reference/cli.md)
- [Python API Reference](reference/python.md)
