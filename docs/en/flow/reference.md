# `rpkbin.flow` API Reference

[繁體中文](../../zh/flow/reference.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [Usage Scenarios](scenarios.md)

Technical specification for `rpkbin.flow`: pipeline step definitions (`Parser`, `Analyzer`, `Automation`), `Binding`, `Flow`, `FlowSession`, contexts, `Tick`, `EndInfo`, `FlowEvent`, `AnalysisReport`, `ActionOutcome`, `Result`, and `ViewSpec`.

```python
from rpkbin.flow import (
    ActionOutcome,
    AnalysisReport,
    Analyzer,
    AnalyzerContext,
    Automation,
    AutomationContext,
    Binding,
    EndInfo,
    Flow,
    FlowEvent,
    FlowSession,
    Parser,
    ParserContext,
    Result,
    Tick,
    ViewSpec,
)
```

`__all__` exports those 16 symbols. Requires Python 3.11 or newer. Importing `rpkbin.flow` never imports `rpkbin.jobcore` or `rpkbin.wave`.

## Pipeline Definitions and Steps

`rpkbin.flow` has no standalone `Step` class; pipeline steps are declared with `Parser`, `Analyzer`, and `Automation` definitions, wired with `Binding`, grouped into a `Flow`, and instantiated per run inside `FlowSession` (which tracks step state and projects `"parsers"`, `"analyzers"`, and `"automations"` step entries in `snapshot()`).

### `Parser`

Frozen dataclass (`eq=False`) that transforms each decoded text line into structured JSON-compatible records.

```python
parser = Parser(name, parse, *, version="1", outputs=None, on_finish=None, state_factory=dict)
```

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | required | Unique producer name without surrounding whitespace or `"."` (`.` is reserved for field namespaces). |
| `parse` | `Callable[[str, ParserContext], Any]` | required | Synchronous two-argument callback returning `None`, one plain `dict`, or an ordered iterable (e.g., `list` or generator) of plain `dict` objects. Arbitrary `Mapping` classes are rejected. |
| `version` | `str` | `"1"` | Non-empty trimmed version tag. |
| `outputs` | `Mapping \| None` | `None` | Optional JSON-compatible field metadata mapping (detached and frozen as `MappingProxyType`). |
| `on_finish` | `Callable[[EndInfo, ParserContext], Any] \| None` | `None` | Optional synchronous two-argument callback invoked once on `finish()` to flush buffered tail records. |
| `state_factory` | `Callable[[], Any]` | `dict` | Zero-argument synchronous factory (including zero-arg builtins such as `dict`, `list`, `deque`, `defaultdict`, `int`). |

### `Analyzer`

Frozen dataclass (`eq=False`) that processes subscribed records, upstream reports, periodic ticks, and completion.

```python
analyzer = Analyzer(
    name,
    *,
    records=(),
    reports=(),
    version="1",
    outputs=None,
    on_finish=None,
    on_tick=None,
    every=None,
    state_factory=dict,
)
```

An `Analyzer` requires at least one of `records`, `reports`, `on_finish`, or `on_tick`.

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | required | Unique producer name without surrounding whitespace or `"."`. |
| `records` | `Sequence[Binding]` | `()` | `Binding(parser, handler)` entries with unique `Parser` sources. Handler receives `(MappingProxyType, AnalyzerContext)`. |
| `reports` | `Sequence[Binding]` | `()` | `Binding(analyzer, handler)` entries with unique `Analyzer` sources. Handler receives `(AnalysisReport, AnalyzerContext)`. |
| `version` | `str` | `"1"` | Non-empty trimmed version tag. |
| `outputs` | `Mapping \| None` | `None` | Optional JSON-compatible field metadata mapping. |
| `on_finish` | `Callable[[EndInfo, AnalyzerContext], Any] \| None` | `None` | Optional synchronous finish callback invoked in topological analyzer order. |
| `on_tick` | `Callable[[Tick, AnalyzerContext], Any] \| None` | `None` | Optional synchronous timer callback; must be paired with `every`. |
| `every` | `int \| float \| None` | `None` | Positive finite interval in seconds (`bool` rejected); must be paired with `on_tick`. |
| `state_factory` | `Callable[[], Any]` | `dict` | Zero-argument synchronous working-state factory. |

Analyzer callbacks return `None`, one `AnalysisReport`, or an ordered iterable of `AnalysisReport` objects.

### `Automation`

Frozen dataclass (`eq=False`) that reacts asynchronously on a dedicated daemon worker thread when `automation_enabled=True`.

```python
automation = Automation(name, *, reports=(), on_event=None, enabled=True, state_factory=dict)
```

Requires at least one of `reports` or `on_event`.

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | required | Unique producer name without surrounding whitespace or `"."`. |
| `reports` | `Sequence[Binding]` | `()` | `Binding(analyzer, handler)` entries (`Parser` sources raise `TypeError`). Handler receives `(AnalysisReport, AutomationContext)`. |
| `on_event` | `Callable[[FlowEvent, AutomationContext], Any] \| None` | `None` | Synchronous callback receiving `FlowEvent(name, data)` for `"started"`, `"finished"`, `"status_changed"`, and `"error"`. |
| `enabled` | `bool` | `True` | Exact `bool`. Disabled automations skip `state_factory` and never queue callbacks. |
| `state_factory` | `Callable[[], Any]` | `dict` | Zero-argument synchronous working-state factory. |

Automation callbacks return `None` or an `ActionOutcome`.

### `Binding`

```python
binding = Binding(source, handler)
```

Frozen dataclass pairing a `Parser` or `Analyzer` definition (`source`) with a synchronous two-argument `handler(value, ctx)`. Async functions, async generators, and callable objects with `async def __call__` are rejected with `TypeError`.

### `Flow`

Immutable, reusable graph definition compiled from `Parser`, `Analyzer`, `Automation`, and `ViewSpec` items.

#### Creation

```python
flow = Flow(
    name,
    *,
    parsers=(),
    analyzers=(),
    automations=(),
    version="1",
    result=None,
    views=(),
)
```

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | required | Non-empty trimmed Flow name. |
| `parsers` | `Sequence[Parser]` | `()` | Parser definitions registered in this Flow. |
| `analyzers` | `Sequence[Analyzer]` | `()` | Analyzer definitions (topologically sorted by `records`/`reports` dependencies; cycles raise `ValueError`). |
| `automations` | `Sequence[Automation]` | `()` | Automation definitions referencing analyzers in this Flow. |
| `version` | `str` | `"1"` | Non-empty trimmed version string. |
| `result` | `Analyzer \| Callable \| None` | `None` | `None` aggregates across all analyzers; an `Analyzer` selects that analyzer's verdict; a 1-arg synchronous callable `fn(analyzers)` returns `Result \| str \| None`. |
| `views` | `Sequence[ViewSpec]` | `()` | Up to 8 `ViewSpec` definitions whose `source` matches a `Parser` or `Analyzer` in this Flow. |

All producer names across `parsers`, `analyzers`, and `automations` must be unique, and every `Binding.source` and `ViewSpec` definition reference must belong to the same `Flow`.

## `FlowSession`

Isolated execution or replay instance of a `Flow`.

### Creation

```python
session = FlowSession(
    flow,
    *,
    publish=None,
    execution_provider=None,
    clock_enabled=False,
    automation_enabled=False,
    control=None,
    on_automation_idle=None,
)
with FlowSession(flow, ...) as session: ...
```

Calling `FlowSession` inside a `with` block returns `self` from `__enter__` (raising `RuntimeError` if sealed or finished) and calls `close()` from `__exit__`. Exiting before `finish()` seals the session as `complete=False`, `finished=True`, and `result="INCOMPLETE"` (unless already `"ERROR"` or `"FAIL"`).

#### Name-Value Arguments

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `publish` | `Callable[[dict], Any] \| None` | `None` | Synchronous 1-arg callback receiving deep-copied publication dicts (`seq`, `stage`, `producer`, `kind`, `data`, `evidence`). Exceptions are captured in `publication_error` without altering the analysis verdict. |
| `execution_provider` | `Callable[[], Mapping] \| None` | `None` | Zero-arg callable returning JSON-compatible execution facts for `ctx.source["execution"]`, `EndInfo.execution`, and `tick()` fallback `elapsed`. |
| `clock_enabled` | `bool` | `False` | Exact `bool`. Must be `True` for `tick()` to invoke `Analyzer.on_tick`. |
| `automation_enabled` | `bool` | `False` | Exact `bool`. Enables `Automation` state initialization, event delivery, report queueing, and `ctx.actions`. |
| `control` | `Mapping \| object \| None` | `None` | Control handler mapping (`{"stop": fn, ...}`) or object with methods (`stop`, `send`, `interrupt`/`send_ctrl_c`, `send_signal`, `kill`) returning `ActionOutcome`. |
| `on_automation_idle` | `Callable[[], Any] \| None` | `None` | Optional zero-arg hook invoked after each automation item or `close_automation()`. |

### Properties

| Property | Type | Meaning |
| :--- | :--- | :--- |
| `flow` | `Flow` | Bound `Flow` definition. |
| `clock_enabled` | `bool` | Whether `tick()` delivers `on_tick` callbacks. |
| `automation_enabled` | `bool` | Whether live `Automation` callbacks are active. |
| `complete` | `bool` | Value passed to `finish(complete=...)` (`False` until complete finish). |
| `finished` | `bool` | `True` once `finish()` or incomplete `close()` runs. |
| `error_count` | `int` | Count of parser, analyzer, and aggregation failures. |
| `automation_error_count` | `int` | Count of isolated automation callback/overflow failures. |
| `publication_error` | `str \| None` | First `publish` callback error formatted as `"{Type}: {msg}"`, or `None`. |
| `aggregation_error` | `str \| None` | First custom `Flow.result` callable error, or `None`. |
| `automation_pending` | `bool` | `True` while automation items are queued or running and the runner is open. |

### Object Functions

| Method | Purpose |
| :--- | :--- |
| `start()` | Deliver `"started"` `FlowEvent` to enabled `Automation.on_event` callbacks. Raises `RuntimeError` if already started or finished. |
| `feed(line, evidence=None)` | Process one decoded `str` line through parsers, topologically ordered analyzers, automation queueing, and result aggregation. Default `evidence` is `{"line_no": n, "line": line}`. |
| `tick(elapsed=None)` | Deliver `Tick(elapsed)` to due analyzers (`clock_enabled=True`) whose `every` interval has passed (with `1e-12` relative float tolerance). Uses `execution_provider().get("elapsed", 0)` when `elapsed is None`. |
| `emit_event(name, data=None)` | Deliver `"status_changed"` or `"error"` `FlowEvent` before `finish()`. |
| `finish(complete=True, execution=None)` | Idempotently run `on_finish(EndInfo(...))` across parsers then analyzers, backfill `"INCOMPLETE"` when `complete=False`, update final `result`, emit `"finished"`, start the 2.0 s automation completion deadline, and return `snapshot()`. |
| `wait_automation(timeout=2.0)` | Wait up to `timeout` non-negative finite seconds for queued automation deliveries; returns `bool`. Raises `RuntimeError` if called from the automation thread. |
| `close_automation()` | Reject further automation operations, fail pending/running automation steps, and run `on_automation_idle`. |
| `close()` | Wait up to 2.0 s for pending automations if already finished, seal the session (`incomplete=not self.finished`), and join the automation thread (2.0 s). Raises `RuntimeError` inside a callback. |
| `snapshot()` | Return a deep-copied state snapshot without executing user callbacks or custom aggregators. |
| `repr(session)` | Return `FlowSession(flow=..., finished=..., sealed=..., result=...)`. |

### Step Snapshots (`snapshot()`)

`session.snapshot()` exposes top-level keys `name`, `version`, `parsers`, `analyzers`, `automations`, `views` (`{"schema_version": 1, "items": [...], "digest": sha256_hex}`), `diagnostics` (tail max 100), `error_count`, `automation_error_count`, `complete`, `finished`, `result`, `result_source`, `result_code`, `publication_error`, and `aggregation_error`.

Each step entry in `parsers`, `analyzers`, and `automations` contains:

| Stage | Step Fields |
| :--- | :--- |
| All steps | `name`, `count`, `publication_count`, `processed`, `code`, and optional `error` |
| `parsers` & `analyzers` | `version`, `outputs`, `latest`, `integer_fields`, `latest_evidence`, `evidence` |
| `analyzers` | `inputs`, `report_count`, `result`, `reason`, `final`, `issues`, `event_count`, `events` (tail max 100) |
| `automations` | `inputs`, `enabled`, `on_report`, `on_job_event`, `action_count`, `last` |

## Contexts and Payload Types

### Contexts (`ParserContext`, `AnalyzerContext`, `AutomationContext`)

All three contexts share `.state` (working state from `state_factory`), read-only `.source` (`stage`, `producer`, `kind`, optional `seq`/`name`, `evidence`, `execution`), `.debug(message, data=None)`, and `repr(ctx)` (`"<ContextClass>(producer=..., kind=..., open=...)"`). Contexts expire immediately when the callback (including any returned generator) finishes; later access raises `RuntimeError`.

- `AnalyzerContext.summary`:
  - `issue(name, level="info", message="", active=True, evidence=None)`: `level` in `"info" | "warning" | "error"`. `active=False` clears the active issue.
  - `event(name, level="info", message="", data=None, evidence=None)`: appends an occurrence (bounded tail of 100 in `snapshot()`; full stream via `publish`).
  - `result(value, reason="", final=False, evidence=None)`: `value` is `Result` or its string value. Severity order is `"ERROR"` (2) > `"FAIL"` (1) > `"PASS"` / `"INCOMPLETE"` / `"N/A"` (0). Once `final=True`, equal or lower-severity values are ignored. Setting `final=True` with `"PASS"` or `"N/A"` requires a complete `on_finish` callback (`ValueError` otherwise).
- `AutomationContext.actions`:
  - Available only when `automation_enabled=True`. Exposes `.stop`, `.send`, `.interrupt`, `.send_ctrl_c`, `.send_signal`, `.kill`.
  - Rejects `expected_attempt` and `expected_submission` keyword arguments (`TypeError`).
  - For non-Mapping `control` objects, `source=f"automation:{name}"` is injected automatically only when the target method's signature accepts a `source` keyword or `**kwargs` parameter.
  - `send(..., secret=True)` masks the text with `"***"` in audit records and `ActionOutcome.reason`.

### `Tick`, `EndInfo`, `FlowEvent`, `AnalysisReport`, `ActionOutcome`, `Result`

| Type | Fields / Members | Notes |
| :--- | :--- | :--- |
| `Tick` | `elapsed: float` | Passed to `Analyzer.on_tick(tick, ctx)`. Never synthesized from log text. |
| `EndInfo` | `complete: bool`, `execution: Mapping` | Passed to `on_finish(end, ctx)`. `execution` is frozen `MappingProxyType`. |
| `FlowEvent` | `name: str`, `data: Mapping` | `name` in `"started" \| "finished" \| "status_changed" \| "error"`; `data` is frozen. |
| `AnalysisReport` | `name: str`, `result: str \| None = None`, `reason: str = ""`, `values: Mapping \| None = None`, `evidence: Mapping \| None = None` | `name` max 256 chars; `values`/`evidence` must be JSON-compatible. |
| `ActionOutcome` | `status: str`, `reason: str` | `status` in `"accepted" \| "rejected" \| "failed"`, non-empty `reason`; `.accepted` and `bool()` are `True` iff `"accepted"`. |
| `Result` | `PASS`, `FAIL`, `ERROR`, `INCOMPLETE`, `NA = "N/A"` | `str` Enum for analyzer summaries and `Flow` aggregation. |

## `ViewSpec`

Immutable, strictly validated chart specification exported by `rpkbin.flow`.

### Creation

```python
spec = ViewSpec(id, kind, *, source, axis, series, title=None, options=None, report_name=None)
spec = ViewSpec.waveform(id, *, source, axis, series, title=None, options=None, report_name=None)
spec = ViewSpec.xy_line(id, *, source, axis, series, title=None, options=None, report_name=None)
spec = ViewSpec.scatter(id, *, source, axis, series, title=None, options=None, report_name=None)
spec = ViewSpec.histogram(id, *, source, axis, series, title=None, options=None, report_name=None)
```

#### `source=` Contract

- Pass a `Parser` object in the same `Flow` (`report_name` must be `None`), an `Analyzer` object in the same `Flow` together with `report_name="..."`, or a JSON mapping `{"stage": "parser", "producer": "..."}` / `{"stage": "analyzer", "producer": "...", "report_name": "..."}`.
- Passing `report_name=` alongside a mapping `source` raises `ValueError`.

### Properties and Object Functions

| Member | Type / Return | Meaning |
| :--- | :--- | :--- |
| `id`, `kind`, `title` | `str`, `str`, `str` | Unique view ID (`"__auto__"` is reserved), kind (`"waveform"`, `"xy_line"`, `"scatter"`, `"histogram"`), and display title (defaults to `id`). |
| `source`, `axis`, `series`, `options` | `MappingProxyType` | Deeply frozen configuration mappings. |
| `to_json()` | `dict` | Returns a detached JSON-compatible dictionary with keys `id`, `kind`, `title`, `source`, `axis`, `series`, `options`. |

### Configuration Schemas and Limits

- `axis`: `{"field": str | None, "label": str, "unit": str, "fallback": ["elapsed", "seq"], "origin": int | float}`. `origin` requires `field` (supports ints up to `2**256 - 1` or finite floats `|v| <= 1e150`). `scatter` requires `axis["field"]` not `None`.
- `series`:
  - `"mode": "wide"`: `{"mode": "wide", "items": [{"id", "field", ...}], "defaults": [...]}` (1 to 16 items; `defaults` subset of item IDs, defaulting to first 4).
  - `"mode": "long"`: `{"mode": "long", "id_field", "value_field", "path_field", "value_type", "bits", "frac_bits", "signed", "radix", "overrides": [...], "defaults": [...]}` (up to 128 overrides, 16 defaults).
  - Types: `"bit"`, `"bus"`, `"signed_bus"`, `"fxp"` (requires `frac_bits` in `0..256`), `"state"`, `"number"`. Buses require `bits` in `1..256` and `radix` in `"hex" | "bin" | "unsigned" | "signed" | "fxp"`. Scientific charts (`xy_line`, `scatter`, `histogram`) allow `"number"` only.
- `options`:
  - `waveform`: `{"radix": "hex" | "bin" | "unsigned" | "signed" | "fxp"}`
  - `xy_line`: `{"thresholds": [{"series_id", "side": "min"|"max", "value", "label"}]}` (max 32, unique `(series_id, side)`)
  - `scatter`: `{"equal_aspect": bool}` (default `False`)
  - `histogram`: `{"bins": int | list[number]}` (default `32`; int `1..128` or `2..129` strictly increasing edges)
- Flow & text bounds: max 8 views per `Flow`, max 32 projected samples per publication, canonical JSON payload `<= 32,688` bytes (`32 * 1024 - 80`), general view text `<= 128` UTF-8 bytes, series/override/default `id` and `label` `<= 512` UTF-8 bytes, no ASCII control chars (`< 32`) or `<`/`>`, numeric values finite (`|int| <= 2**53 - 1`, `|float| <= 1e150`).

## Examples

### 1. Minimal Example: Context Manager Sealing vs. Explicit `finish()`

Inspect `repr(session)` and `repr(ctx)`, and verify that exiting `with FlowSession(...)` before calling `finish()` seals the session as `"INCOMPLETE"`.

```python
from rpkbin.flow import Analyzer, Binding, Flow, FlowSession, Parser

ctx_reprs = []

def parse_line(line, ctx):
    ctx_reprs.append(repr(ctx))
    return {"val": int(line)}

p = Parser("nums", parse_line)
a = Analyzer("seen", records=[Binding(p, lambda r, ctx: ctx.summary.result("PASS"))])
flow = Flow("lifecycle", parsers=[p], analyzers=[a], result=a)

with FlowSession(flow) as unfinished:
    unfinished.start()
    unfinished.feed("10")

print("unfinished repr:", repr(unfinished))
print("unfinished result:", unfinished.snapshot()["result"])
print("ctx repr:", ctx_reprs[0])
```

Output:
```text
unfinished repr: FlowSession(flow='lifecycle', finished=True, sealed=True, result='INCOMPLETE')
unfinished result: INCOMPLETE
ctx repr: ParserContext(producer='nums', kind='line', open=True)
```

### 2. Progressive Example: `ViewSpec` Mapping `source=`, `Analyzer` `report_name=`, and `to_json()`

Create one `ViewSpec` from a `Parser` object, one from an `Analyzer` with `report_name=`, and one from a JSON `dict` mapping, then attach them to `Flow(..., views=...)`.

```python
from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, FlowSession, Parser, ViewSpec

p = Parser("metrics", lambda line, ctx: {"step": int(line), "err": float(line) * 0.1})
a = Analyzer("stat", records=[Binding(p, lambda r, ctx: AnalysisReport("summary", values={"err": r["err"]}))])

v_parser = ViewSpec.xy_line(
    "raw_err",
    source=p,
    axis={"field": "step"},
    series={"mode": "wide", "items": [{"id": "err", "field": "err"}]},
)
v_analyzer = ViewSpec.histogram(
    "err_hist",
    source=a,
    report_name="summary",
    axis={"field": None},
    series={"mode": "wide", "items": [{"id": "err", "field": "err"}]},
    options={"bins": 8},
)
v_dict = ViewSpec.scatter(
    "step_vs_err",
    source={"stage": "parser", "producer": "metrics"},
    axis={"field": "step"},
    series={"mode": "wide", "items": [{"id": "err", "field": "err"}]},
    options={"equal_aspect": True},
)

flow = Flow("views_demo", parsers=[p], analyzers=[a], views=(v_parser, v_analyzer, v_dict))
snap = FlowSession(flow).snapshot()
print("view ids:", [item["id"] for item in snap["views"]["items"]])
print("has digest:", len(snap["views"]["digest"]) == 64)
print("to_json keys:", sorted(v_analyzer.to_json().keys()))
```

Output:
```text
view ids: ['raw_err', 'err_hist', 'step_vs_err']
has digest: True
to_json keys: ['axis', 'id', 'kind', 'options', 'series', 'source', 'title']
```

## Runtime Limits and Errors

- **Per-callback and per-cycle caps**: `MAX_OUTPUTS_PER_CALL = 10000`, `MAX_OUTPUTS_PER_CYCLE = 10000`, `MAX_OUTPUT_BYTES = 4 * 1024 * 1024` (ASCII-escaped JSON), `TAIL_LIMIT = 100` (for `diagnostics` and step `events`), `MAX_CODE_CHARS = 16384`.
- **Automation backlog and finish timeout**: `AutomationRunner` queues at most `256` items (`4 MiB` encoded payload) and enforces `COMPLETION_TIMEOUT = 2.0` s after `finish()`.
- **Fail-fast validation**: JSON-incompatible data (`NaN`, `Infinity`, non-string dict keys, arbitrary objects) or batch/exception failures inside a `Parser` or `Analyzer` disable that step and all downstream dependent `Analyzer` steps (`ERROR`), discarding any staged outputs from the failing invocation.

## See Also

- [Quickstart](README.md)
- [Usage Scenarios](scenarios.md)
- [rpkbin.jobcore Reference](../jobcore/reference.md)
- [Wave Flow Guide](../wave/flow.md)
- [Documentation Index](../README.md)
