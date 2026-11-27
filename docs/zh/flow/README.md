# `rpkbin.flow`

[English](../../en/flow/README.md) · [文件導覽](../README.md) · [實戰使用情境](scenarios.md) · [API 參考手冊](reference.md)

`rpkbin.flow` 透過明確的 Python 綁定（`Binding`）在獨立的 `FlowSession` 中執行 `Parser`、`Analyzer` 與 `Automation` 步驟，進行文字解析、分析與自動化反應，不依賴子程序或外部儲存。

## 語法

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

## 說明

- `Parser(name, parse, ...)` 將每行已解碼文字轉為 `None`、單一 `dict` 或依序產出 `dict` 的 iterable，也可透過 `on_finish(end, ctx)` 在結尾補送資料列。
- `Analyzer(name, ...)` 明確訂閱 `Parser` 資料列（`records=...`）或上游 `Analyzer` 報告（`reports=...`），可選配週期計時器 `on_tick(tick, ctx)`（搭配 `every=seconds`）與 `on_finish(end, ctx)`。
- `Automation(name, ...)` 訂閱 `Analyzer` 報告或生命週期 `FlowEvent`（`on_event=`），並在 `automation_enabled=True` 時透過 `ctx.actions` 提出受稽核的控制請求。
- `Binding(source, handler)` 將指定的 `Parser` 或 `Analyzer` 定義連結至接受 `(item, ctx)` 的同步雙引數回呼函式。
- `AnalysisReport(name, ...)` 承載具名分析輸出（`values`、顯示用 `result` 文字、`reason`、`evidence`）；Analyzer 的整體判決須另行呼叫 `ctx.summary.result(...)`。
- `ViewSpec` 宣告不可變且可序列化為 JSON 的圖表規格（`waveform`、`xy_line`、`scatter`、`histogram`），並由 `Flow(..., views=...)` 驗證。
- `FlowSession(flow, ...)` 在隔離環境中執行或重播一個 `Flow`，支援 `with FlowSession(...) as session:`，若未呼叫 `finish()` 即離開會封存為 `INCOMPLETE`。

## 範例

### 1. 基本範例：解析、分析並完成 `FlowSession`

定義一個 `Parser` 與一個 `Analyzer`，以 `with` 管理 `FlowSession`、餵入文字行並檢視 `repr(session)` 與彙總結果。

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

輸出：
```text
repr: FlowSession(flow='thermal_check', finished=True, sealed=False, result='PASS')
result: PASS reports: 2
latest: {'temp': 80}
```

### 2. 進階範例：`Tick`、`EndInfo`、`Automation` 與 `ViewSpec`

同時啟用顯式時脈（`clock_enabled=True`）、即時自動化（`automation_enabled=True`）與綁定至 `source=temps` 的 `ViewSpec.xy_line` 圖表，並檢查動作回執與圖表摘要。

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

輸出：
```text
result: PASS actions: ['snr=16.0'] views: 1
```

## 注意事項與限制

- **明確判決與 `AnalysisReport.result`**：回傳 `AnalysisReport(..., result="FAIL")` 只會標記該份報告的顯示文字並供下游 `Binding` 讀取，不會設定工作判決；必須呼叫 `ctx.summary.result(...)` 才會影響 `Analyzer` 與整體 `Flow` 的判決。
- **獨立 session 的時脈與自動化預設關閉**：`FlowSession` 預設 `clock_enabled=False` 與 `automation_enabled=False`。若在呼叫 `finish()` 前離開 `with FlowSession(...)` 區塊，session 會自動封存為 `"INCOMPLETE"`（除非先前已紀錄 `"ERROR"` 或 `"FAIL"`）。
- **相容性與上限**：僅使用 Python 標準函式庫，需要 Python `>= 3.11`，不依賴 `rpkbin.jobcore` 或 `rpkbin.wave`。單次回呼最多輸出 10,000 筆與 4 MiB JSON 資料；`Flow(..., views=...)` 最多 8 個 view、每次發布最多投影 32 個樣本。

## 延伸閱讀

- [實戰使用情境](scenarios.md)
- [API 參考手冊](reference.md)
- [rpkbin.jobcore](../jobcore/README.md)
- [Wave Flow 指南](../wave/flow.md)
- [文件導覽](../README.md)
