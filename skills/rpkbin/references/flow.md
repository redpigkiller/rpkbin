# `rpkbin.flow` Reference

Standalone, thread-safe streaming line parser, stateful analyzer, event automation, and `ViewSpec` waveform/chart projection engine.

## Public API (`rpkbin.flow`)

- `Flow(name, *, parsers=(), analyzers=(), automations=(), version='1', result=None, views=())`
- `Parser(name, parse, *, version='1', outputs=None, on_finish=None, state_factory=dict)` — `parse(line, ctx)` returns `None`, one `dict`, or an iterable of `dict` records; `on_finish(end_info, ctx)` runs on completion.
- `Analyzer(name, *, records=(), reports=(), version='1', outputs=None, on_finish=None, on_tick=None, every=None, state_factory=dict)` — Pass `Binding(source, handler)` entries to `records=` or `reports=`; handler receives `(item, ctx)` where `ctx.state` persists and `ctx.summary.result(Result.PASS | Result.FAIL, reason=...)` sets the verdict.
- `Automation(name, *, reports=(), on_event=None, enabled=True, state_factory=dict)`
- `Binding(source, handler)` — Explicit link between a `Parser`/`Analyzer` and a handler `(item, ctx)`.
- `AnalysisReport(name, *, result=None, reason='', values=None, evidence=None)`
- `ViewSpec` — Use helpers such as `ViewSpec.xy_line(id, *, source, axis={"field": ...}, series={"mode": "wide", "items": [...]}, title=None, options=None, report_name=None)` (or `waveform`/`scatter`/`histogram`).
- `FlowSession(flow, *, publish=None, execution_provider=None, clock_enabled=False, automation_enabled=False, control=None, on_automation_idle=None)`
  - Context manager: `with FlowSession(flow) as sess:` ensures `close()` seals unfinished sessions as `INCOMPLETE`.
  - Methods: `start()`, `feed(line, evidence=None)`, `tick(elapsed=None)`, `emit_event(name, data=None)`, `finish(complete=True, execution=None)`, `wait_automation(timeout=2.0)`, `snapshot()`, `close()`.
- Support types: `Result` (`PASS`, `FAIL`, `ERROR`, `INCOMPLETE`, `NA`), `ActionOutcome(status, reason)`, `FlowEvent(name, data)`, `Tick(elapsed)`, `EndInfo(complete, execution)`, `ParserContext`, `AnalyzerContext`, `AutomationContext`.

## Gotchas

- `FlowSession` runs cleanly offline without `wave` or HTTP servers; in `wave`, pass `flow=...` to `WaveWorkspace.add(...)` or `--flow` to `rpk-wave reanalyze`.
- `Parser.parse` **returns** parsed dict(s) rather than calling `emit` on `ctx`; bind downstream `Analyzer` callbacks explicitly via `Binding(parser, fn)`.
- Pass `result=analyzer` to `Flow(...)` if you want `session.snapshot()["result"]` populated automatically from that analyzer's verdict.

## Recipes

### 1. Streaming `Parser` + Bound `Analyzer` Verdict (`FlowSession`)

```python
from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, FlowSession, Parser, Result

def parse_temp(line, ctx):
    if line.startswith("temp="):
        return {"temp": int(line.split("=", 1)[1])}

def check_temp(record, ctx):
    val = record["temp"]
    ctx.state["max"] = max(ctx.state.get("max", val), val)
    verdict = Result.FAIL if val > 85 else Result.PASS
    ctx.summary.result(verdict, reason=f"peak={ctx.state['max']}")
    return AnalysisReport("thermal", result=verdict.value, values={"temp": val, "max": ctx.state["max"]})

temps = Parser("temps", parse_temp)
guard = Analyzer("guard", records=[Binding(temps, check_temp)])
flow = Flow("thermal_check", parsers=[temps], analyzers=[guard], result=guard)

with FlowSession(flow) as sess:
    sess.start()
    sess.feed("temp=72")
    sess.feed("temp=80")
    snap = sess.finish()
    assert snap["result"] == "PASS" and snap["parsers"][0]["latest"] == {"temp": 80}
```

### 2. Declare a `ViewSpec.xy_line` Chart Projection

```python
from rpkbin.flow import Flow, FlowSession, Parser, ViewSpec

def parse_xy(line, ctx):
    if "," in line:
        x, y = line.split(",", 1)
        return {"x": int(x), "y": float(y)}

p = Parser("pts", parse_xy)
v = ViewSpec.xy_line(
    "curve",
    source=p,
    axis={"field": "x", "label": "X"},
    series={"mode": "wide", "items": [{"id": "y", "field": "y"}]},
    title="Curve",
)
flow = Flow("chart_demo", parsers=(p,), views=(v,))
with FlowSession(flow) as sess:
    sess.start()
    sess.feed("1,2.5")
    snap = sess.finish()
    assert snap["complete"] is True and len(snap["views"]["items"]) == 1
```
