# `rpkbin.flow`

[繁體中文](../../zh/flow/README.md) · [Documentation Index](../README.md) · [Usage Scenarios](scenarios.md) · [API Reference](reference.md)

`rpkbin.flow` analyzes decoded text lines through explicit Python bindings between `Parser`, `Analyzer`, and `Automation` steps inside an isolated `FlowSession`, without depending on subprocesses or storage.

## Syntax

```python
parser = Parser(name, parse, *, version="1", outputs=None, on_finish=None, state_factory=dict)
analyzer = Analyzer(name, *, records=(), reports=(), version="1", outputs=None, on_finish=None, on_tick=None, every=None, state_factory=dict)
automation = Automation(name, *, reports=(), on_event=None, enabled=True, state_factory=dict)
binding = Binding(source, handler)
report = AnalysisReport(name, *, result=None, reason="", values=None, evidence=None)
view = ViewSpec.xy_line(id, *, source, axis, series, title=None, options=None, report_name=None)
flow = Flow(name, *, parsers=(), analyzers=(), automations=(), version="1", result=None, views=())
with FlowSession(flow, *, publish=None, execution_provider=None, clock_enabled=False, automation_enabled=False, control=None) as session: ...
```

## Description

- `Parser(name, parse, ...)` turns each decoded line into `None`, one `dict`, or an ordered iterable of `dict` records, and optionally emits tail records on `on_finish(end, ctx)`.
- `Analyzer(name, ...)` binds to explicit `Parser` records (`records=...`) or upstream `Analyzer` reports (`reports=...`), optional periodic `on_tick(tick, ctx)` timers (`every=seconds`), and `on_finish(end, ctx)`.
- `Automation(name, ...)` subscribes to `Analyzer` reports or lifecycle `FlowEvent` values (`on_event=`) and requests bounded actions through `ctx.actions` when `automation_enabled=True`.
- `Binding(source, handler)` connects a specific `Parser` or `Analyzer` definition to a synchronous two-argument callback `(item, ctx)`.
- `AnalysisReport(name, ...)` carries named analysis data (`values`, `result` display text, `reason`, `evidence`); `ctx.summary.result(...)` sets the analyzer verdict independently.
- `ViewSpec` declares immutable, JSON-serializable chart contracts (`waveform`, `xy_line`, `scatter`, `histogram`) validated by `Flow(..., views=...)`.
- `FlowSession(flow, ...)` executes or replays one `Flow` in isolation, supports `with FlowSession(...) as session:`, and seals unfinished sessions as `INCOMPLETE` on close.

## Examples

### 1. Minimal Example: Parse, Analyze, and Finish a `FlowSession`

Define one `Parser` and one `Analyzer`, run a `FlowSession` as a context manager, feed decoded lines, and inspect `repr(session)` and the aggregated verdict.

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

published = []
with FlowSession(flow, publish=published.append) as session:
    session.start()
    session.feed("temp=72")
    session.feed("temp=80")
    snap = session.finish()
    print("repr:", repr(session))
    print("result:", snap["result"], "reports:", snap["analyzers"][0]["report_count"])
    print("latest:", snap["parsers"][0]["latest"])
```

Output:
```text
repr: FlowSession(flow='thermal_check', finished=True, sealed=False, result='PASS')
result: PASS reports: 2
latest: {'temp': 80}
```

### 2. Progressive Example: `Tick`, `EndInfo`, `Automation`, and `ViewSpec`

Enable explicit ticks (`clock_enabled=True`), live automation (`automation_enabled=True`), and a `ViewSpec.xy_line` chart bound to `source=temps`, then verify the action receipt and view digest.

```python
from rpkbin.flow import (
    ActionOutcome,
    AnalysisReport,
    Analyzer,
    Automation,
    Binding,
    Flow,
    FlowSession,
    Parser,
    Result,
    ViewSpec,
)

actions_seen = []

def parse_snr(line, ctx):
    cycle, snr = line.split(",")
    return {"cycle": int(cycle), "snr": float(snr)}

def check_snr(record, ctx):
    ctx.state["samples"] = ctx.state.get("samples", 0) + 1
    if record["snr"] < 18.0:
        return AnalysisReport("low_snr", result="LOW", values=dict(record))
    return AnalysisReport("ok_snr", result="OK", values=dict(record))

def on_tick(tick, ctx):
    ctx.state["last_tick"] = tick.elapsed

def on_finish(end, ctx):
    if end.complete and ctx.state.get("last_tick", 0) >= 1.0:
        ctx.summary.result(Result.PASS, final=True)

def react(report, ctx):
    if report.name == "low_snr":
        return ctx.actions.stop(reason=f"snr={report.values['snr']}")

p = Parser("tdd", parse_snr)
a = Analyzer("snr_check", records=[Binding(p, check_snr)], every=1.0, on_tick=on_tick, on_finish=on_finish)
u = Automation("halt_low_snr", reports=[Binding(a, react)])
chart = ViewSpec.xy_line(
    "snr_curve",
    source=p,
    axis={"field": "cycle", "label": "Cycle"},
    series={"mode": "wide", "items": [{"id": "snr", "field": "snr", "unit": "dB"}]},
)
flow = Flow("tdd_flow", parsers=[p], analyzers=[a], automations=[u], result=a, views=(chart,))

with FlowSession(
    flow,
    clock_enabled=True,
    automation_enabled=True,
    control={"stop": lambda reason="": actions_seen.append(reason) or ActionOutcome("accepted", "stopped")},
) as session:
    session.start()
    session.feed("1,22.5")
    session.tick(1.0)
    session.feed("2,16.0")
    assert session.wait_automation(timeout=5.0)
    snap = session.finish()

print("result:", snap["result"], "actions:", actions_seen, "views:", len(snap["views"]["items"]))
```

Output:
```text
result: PASS actions: ['snr=16.0'] views: 1
```

## Tips and Limitations

- **Explicit verdicts vs. `AnalysisReport.result`**: Returning `AnalysisReport(..., result="FAIL")` only tags that report for display and downstream `Binding` subscribers. Call `ctx.summary.result(...)` to set the analyzer's verdict.
- **Standalone clock and automation opt-ins**: `FlowSession` defaults `clock_enabled=False` and `automation_enabled=False`. Leaving a `with FlowSession(...)` block before calling `finish()` closes the session and seals `result` as `"INCOMPLETE"` unless an `"ERROR"` or `"FAIL"` verdict was already recorded.
- **Compatibility and limits**: Pure Python standard library, requires Python `>= 3.11`, zero dependency on `rpkbin.jobcore` or `rpkbin.wave`. Each callback can emit at most 10,000 outputs and 4 MiB of JSON data/evidence; `Flow(..., views=...)` allows up to 8 views and 32 projected samples per publication.

## See Also

- [Usage Scenarios](scenarios.md)
- [API Reference](reference.md)
- [rpkbin.jobcore](../jobcore/README.md)
- [Wave Flow Guide](../wave/flow.md)
- [Documentation Index](../README.md)
