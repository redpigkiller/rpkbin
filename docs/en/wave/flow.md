# Flow Pipelines

Turn raw job output lines into structured records, live PASS/FAIL reports, automated actions, and interactive charts with `rpkbin.flow`.

## Pipeline Model

A `Flow` organizes stream processing into three stages plus optional chart projections:

```python
from rpkbin.flow import Flow, Parser, Analyzer, Automation, Binding, ViewSpec

flow = Flow(
    "sim_flow",
    parsers=(...,),
    analyzers=(...,),
    automations=(...,),
    result=None,       # optional primary Analyzer or selector
    views=(...,),      # optional ViewSpec definitions for Charts tab
)
```

- **Stage 1 (`Parser`)**: Receives each raw text line via `parse(line, ctx)` and emits normalized Python dicts (`return {...}` or `Result(...)`). Emitted rows are searchable and inspectable under **Inspect -> Flow -> Parsed Records**.
- **Stage 2 (`Analyzer`)**: Subscribes via `Binding(source, handler)` to parser records (`records=`), upstream analyzer reports (`reports=`), periodic timers (`on_tick=`, `every=seconds`, receiving `Tick(elapsed)`), or job completion (`on_finish(info, ctx)`, receiving `EndInfo(complete, execution)`). Returns an `AnalysisReport(name, result="PASS"|"FAIL", reason=..., values=..., evidence=...)`.
- **Stage 3 (`Automation`)**: Subscribes via `Binding(source, handler)` to analyzer reports (`reports=`) or UI/API events (`on_event(event, ctx)`, receiving `FlowEvent`). Dispatches non-blocking actions through `ctx.actions` (`send`, `stop`, `force_stop`, `interrupt`, `signal`) and logs each `ActionOutcome`.

## Views and Chart Projections (`ViewSpec`)

Declare one or more `ViewSpec` objects in `Flow(..., views=(...))` to plot metrics or digital signals in the **Charts** pane and archive index:

| Kind | Helper Constructor | Typical Use |
| --- | --- | --- |
| `waveform` | `ViewSpec.waveform(id, source=..., axis=..., series=..., title=None, options=None)` | Multi-bit buses, 1-bit flags, logic states (`0`/`1`/`X`/`Z`), analog step curves, VCD export |
| `xy_line` | `ViewSpec.xy_line(...)` | Time-series or swept parameter curves |
| `scatter` | `ViewSpec.scatter(...)` | Sampled paired metric distributions |
| `histogram` | `ViewSpec.histogram(...)` | Value bucket distributions |

When a `Flow` defines no explicit `views`, Wave checks the latest parser output (scanning up to the **100** most recent records per parser) and builds an automatic numeric/scalar fallback chart when suitable fields exist. Explicit `ViewSpec` declarations require `source` (either the `Parser`/`Analyzer` instance or a `{"stage": ..., "producer": ...}` mapping) and `series` (a mapping with `"mode": "wide"` and `"items": [...]`, or `"mode": "long"` with `"id_field"` and `"value_field"`), and are stored permanently in the SQLite archive index for fast windowed queries (`RunArchive.chart_window`) even on multi-million-line logs.

## Interactive Prompts and Signals

Many simulators and CLI tools ask interactive questions without ending the line with `\n` (for example, `Continue? [y/N]: `). Configure `prompts` on `Job`:

```python
Job("bench", cmd, prompts=("Continue? [y/N]:", "Password:"))
```

Wave inspects the incomplete tail buffer (after stripping ANSI escapes and trailing whitespace) and flushes the prompt line immediately once it ends with any entry in `prompts`. An `Automation` rule or the UI operator can then send input:

- `ctx.actions.send("y", newline=True)` appends `\n` (or sends raw bytes without a newline when `newline=False`).
- `ctx.actions.signal(sig)` or the GUI **Signal** menu dispatches POSIX signals (`SIGINT`, `SIGTERM`, `SIGTSTP`, `SIGCONT`, `SIGUSR1`, `SIGUSR2`, `SIGHUP`, `SIGQUIT` on POSIX hosts).

## Examples

### 1. Minimal Example (`FlowSession` Unit Test)

Test `Parser` and `Analyzer` logic offline in memory without spawning any subprocesses:

```python
from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, FlowSession, Parser

def parse_metric(line, ctx):
    if line.startswith("cycle="):
        parts = dict(item.split("=", 1) for item in line.split())
        return {"cycle": int(parts["cycle"]), "err": int(parts["err"])}

def check_err(rec, ctx):
    if rec["err"] > 0:
        ctx.state["errors"] = ctx.state.get("errors", 0) + rec["err"]

def summarize(info, ctx):
    errs = ctx.state.get("errors", 0)
    return AnalysisReport(
        "err_gate",
        result="PASS" if info.complete and errs == 0 else "FAIL",
        reason=f"complete={info.complete}, errors={errs}",
        values={"errors": errs},
    )

metrics = Parser("metrics", parse_metric)
gate = Analyzer("gate", records=(Binding(metrics, check_err),), on_finish=summarize)
flow = Flow("offline_test", parsers=(metrics,), analyzers=(gate,))

published = []
with FlowSession(flow, publish=lambda pub: published.append(pub)) as sess:
    sess.start()
    sess.feed("cycle=10 err=0")
    sess.feed("cycle=20 err=2")
    sess.finish(complete=True, execution={"returncode": 0})

reports = [p for p in published if p["stage"] == "analyzer"]
data = reports[-1]["data"]
print(data["result"], data["reason"])
```

Output:

```text
FAIL complete=True, errors=2
```

### 2. Progressive Example (`EndInfo` and `ViewSpec.waveform`)

Run a batch job producing bus/flag transitions, declare `Analyzer` + `ViewSpec.waveform`, and inspect waveform samples from `RunArchive`:

```python
import re
import sys
import tempfile
from pathlib import Path

from rpkbin.flow import AnalysisReport, Analyzer, Flow, Parser, ViewSpec
from rpkbin.jobcore import Job
from rpkbin.wave import RunArchive, WaveWorkspace, run_headless

WAVE_RE = re.compile(r"@(\d+)\s+valid=(\d)\s+addr=(0x[0-9a-fA-F]+)")

def parse_wave(line, ctx):
    m = WAVE_RE.search(line)
    if m:
        return {"t": int(m.group(1)), "valid": int(m.group(2)), "addr": int(m.group(3), 16)}

def on_finish(info, ctx):
    return AnalysisReport("run_done", result="PASS" if info.complete else "FAIL")

bus = Parser("bus", parse_wave)
flow = Flow(
    "bus_flow",
    parsers=(bus,),
    analyzers=(Analyzer("watchdog", on_finish=on_finish),),
    views=(
        ViewSpec.waveform(
            "bus_view",
            source=bus,
            axis={"field": "t", "label": "Time", "unit": "ns"},
            series={
                "mode": "wide",
                "items": [
                    {"id": "valid", "field": "valid", "type": "bit"},
                    {"id": "addr", "field": "addr", "type": "bus", "bits": 16},
                ],
            },
        ),
    ),
)

with tempfile.TemporaryDirectory() as tmp:
    ws = WaveWorkspace(archive_dir=Path(tmp) / "archives")
    script = "print('@0 valid=0 addr=0x0000\\n@10 valid=1 addr=0x1040\\n@20 valid=0 addr=0x1040')"
    ws.add(Job("rtl", [sys.executable, "-c", script]), flow=flow)
    rc = run_headless(ws)
    db = Path(ws.archive_health["path"])
    ws.close()

    with RunArchive(db) as ar:
        att = ar.attempts()["rows"][0]
        win = ar.chart_window(att["id"], att["attempt_id"], view_id="bus_view", series=["valid", "addr"], lo=0, hi=30)
        print("rc:", rc, "total points:", len(win["points"]))
```

Output (after the Wave summary banner):

```text
rc: 0 total points: 6
```

## Tips and Limitations

- **Keep `Parser` fast and side-effect free**: `Parser.parse` runs inline for every output line; avoid disk or network calls inside parsers.
- **Discrete events via `Automation`**: Put interactive sends, alerts, or emergency stops inside `Automation` handlers instead of `Parser` or `Analyzer` callbacks so background worker isolation protects log throughput.
- **Deterministic reanalysis**: When you update a `Parser` or `ViewSpec`, test it offline with `FlowSession` first, then run `rpk-wave reanalyze` against archived raw logs.

## See Also

- [Wave Overview](README.md)
- [Web GUI Guide](gui.md)
- [Workflow Scenarios](scenarios.md)
- [Python API Reference](reference/python.md)
