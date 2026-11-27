# `rpkbin.flow` Usage Scenarios

[繁體中文](../../zh/flow/scenarios.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [API Reference](reference.md)

Six self-contained runnable scenarios covering chained `Parser` and `Analyzer` pipelines, `Tick` and `EndInfo` completion rules, live `Automation` controls, custom `Result` aggregation, declarative `ViewSpec` charts, and error isolation.

## Table of Contents

1. [Scenario 1: Chain `Parser` and `Analyzer` Stages with Explicit `Binding`](#scenario-1-chain-parser-and-analyzer-stages-with-explicit-binding)
2. [Scenario 2: Run `Tick` Watchdogs and `EndInfo` Completion Checks](#scenario-2-run-tick-watchdogs-and-endinfo-completion-checks)
3. [Scenario 3: Drive `Automation` via `FlowEvent`, Reports, and `control`](#scenario-3-drive-automation-via-flowevent-reports-and-control)
4. [Scenario 4: Custom `Result` Aggregation, Step Isolation, and Bounded Tails](#scenario-4-custom-result-aggregation-step-isolation-and-bounded-tails)
5. [Scenario 5: Declare `ViewSpec` Charts on a `Flow`](#scenario-5-declare-viewspec-charts-on-a-flow)
6. [Scenario 6: Error Isolation, Generator Rollback, and Incomplete Sealing](#scenario-6-error-isolation-generator-rollback-and-incomplete-sealing)

## Scenario 1: Chain `Parser` and `Analyzer` Stages with Explicit `Binding`

Route `Parser` records into a primary `Analyzer`, forward its `AnalysisReport` to a downstream `Analyzer` regardless of declaration order, and inspect read-only `ctx.source`.

```python
from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, FlowSession, Parser, Result

def parse_metric(line, ctx):
    if line.startswith("val="):
        return {"val": int(line.split("=", 1)[1])}

def first_stage(record, ctx):
    assert ctx.source["producer"] == "metrics"
    flag = "HIGH" if record["val"] > 50 else "OK"
    return AnalysisReport("level", result=flag, values={"val": record["val"]})

def second_stage(report, ctx):
    assert isinstance(report, AnalysisReport)
    ctx.state["highs"] = ctx.state.get("highs", 0) + (1 if report.result == "HIGH" else 0)
    if ctx.state["highs"] >= 2:
        ctx.summary.result(Result.FAIL, reason="two high readings")
    return AnalysisReport("tally", values={"highs": ctx.state["highs"]})

p = Parser("metrics", parse_metric, outputs={"val": {"label": "Reading"}})
stage1 = Analyzer("classify", records=[Binding(p, first_stage)])
stage2 = Analyzer("tally", reports=[Binding(stage1, second_stage)])

# Notice stage2 appears before stage1; Flow sorts analyzers topologically.
flow = Flow("chained", parsers=[p], analyzers=[stage2, stage1], result=stage2)
published = []
with FlowSession(flow, publish=published.append) as session:
    for line in ("val=20", "val=60", "val=75"):
        session.feed(line)
    snap = session.finish()

print("order:", [row["name"] for row in snap["analyzers"]])
print("result:", snap["result"], "reason:", snap["analyzers"][1]["reason"])
print("publications:", len(published))
```

Output:
```text
order: ['classify', 'tally']
result: FAIL reason: two high readings
publications: 10
```

## Scenario 2: Run `Tick` Watchdogs and `EndInfo` Completion Checks

Drive `Analyzer.on_tick` with `clock_enabled=True` across decimal steps (`0.1, 0.2, 0.3`), supply `execution_provider`, and compare `finish(complete=True)` with `finish(complete=False)`.

```python
from rpkbin.flow import Analyzer, Binding, Flow, FlowSession, Parser, Result

ticks_seen = []
facts = {"elapsed": 0.0, "returncode": 0}

def on_tick(tick, ctx):
    ticks_seen.append(round(tick.elapsed, 4))
    assert tick.elapsed == ctx.source["execution"]["elapsed"]

def on_finish(end, ctx):
    if end.complete and end.execution.get("returncode") == 0:
        ctx.summary.result(Result.PASS, reason="clean eof", final=True)

p = Parser("raw", lambda line, ctx: {"line": line})
watchdog = Analyzer("watchdog", records=[Binding(p, lambda r, ctx: None)], every=0.1, on_tick=on_tick, on_finish=on_finish)
flow = Flow("timed_flow", parsers=[p], analyzers=[watchdog], result=watchdog)

with FlowSession(flow, clock_enabled=True, execution_provider=lambda: facts) as s1:
    for t in (0.0, 0.1, 0.2, 0.3):
        facts["elapsed"] = t
        s1.tick()
    snap_ok = s1.finish(complete=True)

with FlowSession(flow, clock_enabled=True, execution_provider=lambda: facts) as s2:
    s2.feed("sample")
    snap_inc = s2.finish(complete=False)

print("ticks:", ticks_seen)
print("complete:", snap_ok["result"], "| incomplete:", snap_inc["result"])
```

Output:
```text
ticks: [0.1, 0.2, 0.3]
complete: PASS | incomplete: INCOMPLETE
```

## Scenario 3: Drive `Automation` via `FlowEvent`, Reports, and `control`

React to both `AnalysisReport` bindings and `FlowEvent` notifications, compare object `control` methods with and without a `source` parameter, and mask secrets with `send(..., secret=True)`.

```python
from rpkbin.flow import ActionOutcome, AnalysisReport, Analyzer, Automation, Binding, Flow, FlowSession, Parser

calls = []
audit = []

class ObjectControl:
    def send(self, text, *, secret=False):
        calls.append(("send", text, secret))
        return ActionOutcome("accepted", f"sent {text}")

    def stop(self, reason="", *, source="user"):
        calls.append(("stop", reason, source))
        return ActionOutcome("accepted", reason)

def on_report(report, ctx):
    if report.name == "alert":
        r1 = ctx.actions.send("TOP_SECRET", secret=True)
        r2 = ctx.actions.stop(reason=report.reason)
        assert r1.accepted and r2.accepted
        return r2

def on_event(event, ctx):
    calls.append(("event", event.name, dict(event.data)))

p = Parser("ev", lambda line, ctx: {"cmd": line})
a = Analyzer("detector", records=[Binding(p, lambda r, ctx: AnalysisReport("alert", reason="trigger") if r["cmd"] == "BAD" else None)])
auto = Automation("responder", reports=[Binding(a, on_report)], on_event=on_event)
flow = Flow("auto_demo", parsers=[p], analyzers=[a], automations=[auto])

with FlowSession(flow, automation_enabled=True, control=ObjectControl(), publish=audit.append) as session:
    session.start()
    session.feed("BAD")
    assert session.wait_automation(timeout=5.0)
    snap = session.finish()
    assert session.wait_automation(timeout=5.0)

action_rows = [row["data"] for row in audit if row["kind"] == "action" and row["data"]["phase"] == "returned"]
print("control calls:", [c[0] for c in calls])
print("stop source injected:", [c for c in calls if c[0] == "stop"][0][2])
print("masked send receipt:", action_rows[0]["receipt"]["reason"])
print("automation count:", snap["automations"][0]["action_count"])
```

Output:
```text
control calls: ['event', 'send', 'stop', 'event']
stop source injected: automation:responder
masked send receipt: sent ***
automation count: 2
```

## Scenario 4: Custom `Result` Aggregation, Step Isolation, and Bounded Tails

Supply a custom `result=fn` aggregator over immutable analyzer summaries, keep step `state_factory` independent per session, and verify the 100-entry tail cap on `ctx.summary.event()` and `ctx.debug()`.

```python
from collections import deque
from rpkbin.flow import Analyzer, Binding, Flow, FlowSession, Parser, Result

def aggregate(analyzers):
    if any(item["result"] == "FAIL" for item in analyzers):
        return Result.FAIL
    if sum(item["event_count"] for item in analyzers) >= 120:
        return Result.PASS
    return Result.NA

def record_step(record, ctx):
    ctx.state.append(record["x"])
    ctx.summary.event("sample", data={"len": len(ctx.state)})
    ctx.debug("seen", {"x": record["x"]})

p = Parser("seq", lambda line, ctx: {"x": int(line)})
a = Analyzer("counter", records=[Binding(p, record_step)], state_factory=lambda: deque(maxlen=5))
flow = Flow("agg_demo", parsers=[p], analyzers=[a], result=aggregate)

published = []
with FlowSession(flow, publish=published.append) as s1:
    for i in range(130):
        s1.feed(str(i))
    snap1 = s1.finish()

with FlowSession(flow) as s2:
    s2.feed("1")
    snap2 = s2.finish()

print("s1 result:", snap1["result"], "event_count:", snap1["analyzers"][0]["event_count"], "tail len:", len(snap1["analyzers"][0]["events"]))
print("s1 diagnostics tail:", len(snap1["diagnostics"]), "published total:", len(published))
print("s2 result:", snap2["result"], "event_count:", snap2["analyzers"][0]["event_count"])
```

Output:
```text
s1 result: PASS event_count: 130 tail len: 100
s1 diagnostics tail: 100 published total: 390
s2 result: N/A event_count: 1
```

## Scenario 5: Declare `ViewSpec` Charts on a `Flow`

Configure all four `ViewSpec` chart kinds (`waveform` in long mode with overrides, `xy_line` in wide mode with thresholds, `scatter` with `equal_aspect`, and `histogram` with explicit bins) on a single `Flow`.

```python
from rpkbin.flow import Flow, FlowSession, Parser, ViewSpec

p = Parser("dsp", lambda line, ctx: {"cycle": int(line), "i": 0.5, "q": -0.5, "snr": 24.0, "sig": "clk", "val": 1})

wave = ViewSpec.waveform(
    "rtl_wave",
    source=p,
    axis={"field": "cycle", "label": "Cycle", "fallback": ["seq"]},
    series={
        "mode": "long",
        "id_field": "sig",
        "value_field": "val",
        "overrides": [{"id": "clk", "type": "bit"}, {"id": "gain", "type": "fxp", "bits": 16, "frac_bits": 8}],
    },
    options={"radix": "hex"},
)
xy = ViewSpec.xy_line(
    "snr_xy",
    source=p,
    axis={"field": "cycle", "origin": 1},
    series={"mode": "wide", "items": [{"id": "snr", "field": "snr", "unit": "dB"}]},
    options={"thresholds": [{"series_id": "snr", "side": "min", "value": 20.0, "label": "Min SNR"}]},
)
iq = ViewSpec.scatter(
    "iq_plot",
    source=p,
    axis={"field": "i", "label": "I"},
    series={"mode": "wide", "items": [{"id": "q", "field": "q", "label": "Q"}]},
    options={"equal_aspect": True},
)
hist = ViewSpec.histogram(
    "snr_hist",
    source=p,
    axis={"field": "cycle"},
    series={"mode": "wide", "items": [{"id": "snr", "field": "snr"}]},
    options={"bins": [0.0, 15.0, 25.0, 40.0]},
)

flow = Flow("charts", parsers=[p], views=(wave, xy, iq, hist))
snap = FlowSession(flow).snapshot()
print("view kinds:", [(v["id"], v["kind"]) for v in snap["views"]["items"]])
print("origin:", snap["views"]["items"][1]["axis"]["origin"])
print("bins:", snap["views"]["items"][3]["options"]["bins"])
```

Output:
```text
view kinds: [('rtl_wave', 'waveform'), ('snr_xy', 'xy_line'), ('iq_plot', 'scatter'), ('snr_hist', 'histogram')]
origin: 1
bins: [0.0, 15.0, 25.0, 40.0]
```

## Scenario 6: Error Isolation, Generator Rollback, and Incomplete Sealing

Confirm that an exception inside a generator rolls back staged reports and `ctx.summary` updates, while a `publish` failure surfaces in `publication_error` without corrupting a `PASS` verdict.

```python
from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, FlowSession, Parser, Result

def parse_val(line, ctx):
    return {"x": int(line)}

def faulty_gen(record, ctx):
    ctx.summary.issue("staged_warn", level="warning")
    yield AnalysisReport("before_boom", values=dict(record))
    raise ValueError("bad tail")

def good_check(record, ctx):
    ctx.summary.result(Result.PASS, final=True)

p = Parser("p", parse_val)
bad_a = Analyzer("bad", records=[Binding(p, faulty_gen)])
good_a = Analyzer("good", records=[Binding(p, lambda r, ctx: None)], on_finish=lambda end, ctx: good_check(end, ctx))

out = []
with FlowSession(Flow("rollback", parsers=[p], analyzers=[bad_a, good_a]), publish=out.append) as s1:
    s1.feed("5")
    snap1 = s1.finish()

print("bad result:", snap1["analyzers"][0]["result"], "issues:", snap1["analyzers"][0]["issues"])
print("good result:", snap1["analyzers"][1]["result"], "published kinds:", [r["kind"] for r in out])

def broken_publish(pub):
    raise OSError("disk full")

with FlowSession(Flow("pub_err", parsers=[p], analyzers=[good_a]), publish=broken_publish) as s2:
    s2.feed("5")
    snap2 = s2.finish()

print("s2 result:", snap2["result"], "publication_error:", snap2["publication_error"])
```

Output:
```text
bad result: ERROR issues: []
good result: PASS published kinds: ['record', 'diagnostic', 'result']
s2 result: PASS publication_error: OSError: disk full
```

## See Also

- [Quickstart](README.md)
- [API Reference](reference.md)
- [rpkbin.jobcore Scenarios](../jobcore/scenarios.md)
- [Documentation Index](../README.md)
