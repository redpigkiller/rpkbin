# `rpkbin.flow` API 參考手冊

[English](../../en/flow/reference.md) · [文件導覽](../README.md) · [快速入門](README.md) · [實戰使用情境](scenarios.md)

本文件提供 `rpkbin.flow` 的技術規格，涵蓋管線步驟定義（`Parser`、`Analyzer`、`Automation`）、`Binding`、`Flow`、`FlowSession`、context 物件、`Tick`、`EndInfo`、`FlowEvent`、`AnalysisReport`、`ActionOutcome`、`Result` 與 `ViewSpec`。

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

`__all__` 匯出上述 16 個符號。需要 Python 3.11 或更新版本。匯入 `rpkbin.flow` 時不會載入 `rpkbin.jobcore` 或 `rpkbin.wave`。

## 管線定義與步驟

`rpkbin.flow` 沒有獨立公開的 `Step` 類別；管線步驟由 `Parser`、`Analyzer` 與 `Automation` 定義宣告，透過 `Binding` 連接並組合成 `Flow`，再由 `FlowSession` 在每次執行時建立步驟狀態，並於 `snapshot()` 輸出 `"parsers"`、`"analyzers"` 與 `"automations"` 步驟項目。

### `Parser`

不可變 dataclass（`eq=False`），將每行已解碼文字轉換成 JSON 相容的資料列（record）。

```python
parser = Parser(name, parse, *, version="1", outputs=None, on_finish=None, state_factory=dict)
```

| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `name` | `str` | 必填 | 唯一產生者名稱，前後不可有空白且不可包含 `"."`（`.` 保留給欄位命名空間）。 |
| `parse` | `Callable[[str, ParserContext], Any]` | 必填 | 同步雙引數回呼，回傳 `None`、單一純 `dict`，或依序產出純 `dict` 的 iterable（例如 `list` 或 generator）；不接受自訂 `Mapping` 類別。 |
| `version` | `str` | `"1"` | 非空且前後無空白的版本字串。 |
| `outputs` | `Mapping \| None` | `None` | 選填的 JSON 相容欄位描述 mapping（會複製並凍結為 `MappingProxyType`）。 |
| `on_finish` | `Callable[[EndInfo, ParserContext], Any] \| None` | `None` | 選填的同步雙引數回呼，於 `finish()` 時呼叫一次，用來送出緩衝的尾端資料列。 |
| `state_factory` | `Callable[[], Any]` | `dict` | 零引數同步工廠函式（支援 `dict`、`list`、`deque`、`defaultdict`、`int` 等零引數內建型別）。 |

### `Analyzer`

不可變 dataclass（`eq=False`），處理訂閱的資料列、上游報告、週期計時器與結束事件。

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

`Analyzer` 必須至少設定 `records`、`reports`、`on_finish` 或 `on_tick` 其中一項。

| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `name` | `str` | 必填 | 唯一產生者名稱，前後不可有空白且不可包含 `"."`。 |
| `records` | `Sequence[Binding]` | `()` | `Binding(parser, handler)` 清單，`Parser` 來源不可重複。處理函式接收 `(MappingProxyType, AnalyzerContext)`。 |
| `reports` | `Sequence[Binding]` | `()` | `Binding(analyzer, handler)` 清單，`Analyzer` 來源不可重複。處理函式接收 `(AnalysisReport, AnalyzerContext)`。 |
| `version` | `str` | `"1"` | 非空且前後無空白的版本字串。 |
| `outputs` | `Mapping \| None` | `None` | 選填的 JSON 相容欄位描述 mapping。 |
| `on_finish` | `Callable[[EndInfo, AnalyzerContext], Any] \| None` | `None` | 選填的同步結束回呼，依 Analyzer 拓樸順序呼叫。 |
| `on_tick` | `Callable[[Tick, AnalyzerContext], Any] \| None` | `None` | 選填的同步計時回呼；必須與 `every` 同時指定。 |
| `every` | `int \| float \| None` | `None` | 有限正數秒數間隔（拒絕 `bool`）；必須與 `on_tick` 同時指定。 |
| `state_factory` | `Callable[[], Any]` | `dict` | 零引數同步工作狀態工廠函式。 |

Analyzer 回呼可回傳 `None`、單一 `AnalysisReport`，或依序產出 `AnalysisReport` 的 iterable。

### `Automation`

不可變 dataclass（`eq=False`），在 `automation_enabled=True` 時於專屬背景執行緒非同步執行反應。

```python
automation = Automation(name, *, reports=(), on_event=None, enabled=True, state_factory=dict)
```

必須至少設定 `reports` 或 `on_event` 其中一項。

| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `name` | `str` | 必填 | 唯一產生者名稱，前後不可有空白且不可包含 `"."`。 |
| `reports` | `Sequence[Binding]` | `()` | `Binding(analyzer, handler)` 清單（傳入 `Parser` 來源會引發 `TypeError`）。處理函式接收 `(AnalysisReport, AutomationContext)`。 |
| `on_event` | `Callable[[FlowEvent, AutomationContext], Any] \| None` | `None` | 同步回呼，接收 `"started"`、`"finished"`、`"status_changed"` 與 `"error"` 的 `FlowEvent(name, data)`。 |
| `enabled` | `bool` | `True` | 嚴格 `bool`。停用時不會呼叫 `state_factory`，也不會排入回呼。 |
| `state_factory` | `Callable[[], Any]` | `dict` | 零引數同步工作狀態工廠函式。 |

Automation 回呼可回傳 `None` 或 `ActionOutcome`。

### `Binding`

```python
binding = Binding(source, handler)
```

不可變 dataclass，將 `Parser` 或 `Analyzer` 定義（`source`）配對到同步雙引數 `handler(value, ctx)`。非同步函式、非同步 generator 與具有 `async def __call__` 的可呼叫物件都會引發 `TypeError`。

### `Flow`

由 `Parser`、`Analyzer`、`Automation` 與 `ViewSpec` 定義編譯而成的不可變、可重複使用流程圖。

#### 建立方式

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

| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `name` | `str` | 必填 | 非空且前後無空白的 Flow 名稱。 |
| `parsers` | `Sequence[Parser]` | `()` | 此 Flow 中的 Parser 定義。 |
| `analyzers` | `Sequence[Analyzer]` | `()` | Analyzer 定義（依 `records`／`reports` 相依性進行拓樸排序；存在環狀相依會引發 `ValueError`）。 |
| `automations` | `Sequence[Automation]` | `()` | 訂閱此 Flow 中 Analyzer 或事件的 Automation 定義。 |
| `version` | `str` | `"1"` | 非空且前後無空白的版本字串。 |
| `result` | `Analyzer \| Callable \| None` | `None` | `None` 彙總所有 Analyzer；傳入 `Analyzer` 代表採用該 Analyzer 的判決；傳入單引數同步函式 `fn(analyzers)` 時回傳 `Result \| str \| None`。 |
| `views` | `Sequence[ViewSpec]` | `()` | 最多 8 個 `ViewSpec` 定義，其 `source` 必須指向同一個 Flow 中的 `Parser` 或 `Analyzer`。 |

`parsers`、`analyzers` 與 `automations` 的產生者名稱在同一個 `Flow` 內不可重複，且所有 `Binding.source` 與 `ViewSpec` 定義參考都必須屬於同一個 `Flow`。

## `FlowSession`

`Flow` 定義的單次隔離執行或重播實例。

### 建立方式

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

搭配 `with` 使用時，`__enter__` 會回傳 `self`（若已封存或已完成則引發 `RuntimeError`），離開時由 `__exit__` 呼叫 `close()`。若在尚未呼叫 `finish()` 前離開區塊，會將 session 封存為 `complete=False`、`finished=True`，並把 `result` 設為 `"INCOMPLETE"`（除非先前已為 `"ERROR"` 或 `"FAIL"`）。

#### 具名引數

| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `publish` | `Callable[[dict], Any] \| None` | `None` | 同步單引數函式，接收深複製的發布字典（`seq`、`stage`、`producer`、`kind`、`data`、`evidence`）。拋出例外會記錄於 `publication_error`，不會改變分析判決。 |
| `execution_provider` | `Callable[[], Mapping] \| None` | `None` | 零引數函式，回傳 JSON 相容執行資訊 mapping，供 `ctx.source["execution"]`、`EndInfo.execution` 與 `tick()` 預設 `elapsed` 使用。 |
| `clock_enabled` | `bool` | `False` | 嚴格 `bool`。必須為 `True` 才會讓 `tick()` 觸發 `Analyzer.on_tick`。 |
| `automation_enabled` | `bool` | `False` | 嚴格 `bool`。啟用 `Automation` 狀態初始化、事件派送、報告佇列與 `ctx.actions`。 |
| `control` | `Mapping \| object \| None` | `None` | 控制處理字典（`{"stop": fn, ...}`）或提供對應方法（`stop`、`send`、`interrupt`／`send_ctrl_c`、`send_signal`、`kill`）且回傳 `ActionOutcome` 的物件。 |
| `on_automation_idle` | `Callable[[], Any] \| None` | `None` | 選填的零引數勾點，在每筆自動化任務完成或 `close_automation()` 後呼叫。 |

### 屬性

| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `flow` | `Flow` | 綁定的 `Flow` 定義。 |
| `clock_enabled` | `bool` | `tick()` 是否會派送 `on_tick` 回呼。 |
| `automation_enabled` | `bool` | 即時 `Automation` 回呼是否啟用。 |
| `complete` | `bool` | 傳給 `finish(complete=...)` 的值（完整結束前為 `False`）。 |
| `finished` | `bool` | 呼叫 `finish()` 或未完成即 `close()` 後為 `True`。 |
| `error_count` | `int` | Parser、Analyzer 與彙總函式的錯誤總數。 |
| `automation_error_count` | `int` | 隔離的 Automation 回呼與溢位錯誤數。 |
| `publication_error` | `str \| None` | 第一筆 `publish` 失敗訊息（格式 `"{Type}: {msg}"`），或 `None`。 |
| `aggregation_error` | `str \| None` | 第一筆自訂 `Flow.result` 彙總函式錯誤訊息，或 `None`。 |
| `automation_pending` | `bool` | 自動化執行器未關閉且仍有排隊或執行中項目時為 `True`。 |

### 物件函式

| 方法 | 用途 |
| :--- | :--- |
| `start()` | 向已啟用的 `Automation.on_event` 派送 `"started"` `FlowEvent`。若已開始或已結束會引發 `RuntimeError`。 |
| `feed(line, evidence=None)` | 將一行已解碼 `str` 送入 Parser、依拓樸順序執行 Analyzer、排入自動化反應並更新結果。預設 `evidence` 為 `{"line_no": n, "line": line}`。 |
| `tick(elapsed=None)` | 在 `clock_enabled=True` 時向到期的 Analyzer 派送 `Tick(elapsed)`（含 `1e-12` 相對浮點容差）。省略 `elapsed` 時讀取 `execution_provider().get("elapsed", 0)`。 |
| `emit_event(name, data=None)` | 在 `finish()` 前派送 `"status_changed"` 或 `"error"` `FlowEvent`。 |
| `finish(complete=True, execution=None)` | 具冪等性，依序在 Parser 與 Analyzer 執行 `on_finish(EndInfo(...))`，當 `complete=False` 時補寫 `"INCOMPLETE"`，更新最終 `result`、發出 `"finished"`、啟動 2.0 秒自動化收尾計時並回傳 `snapshot()`。 |
| `wait_automation(timeout=2.0)` | 最多等待 `timeout` 非負有限秒數，直到目前排隊的自動化任務完成；回傳 `bool`。不可在自動化執行緒內呼叫。 |
| `close_automation()` | 拒絕後續自動化操作、將執行中與排隊中的自動化標為失敗並觸發 `on_automation_idle`。 |
| `close()` | 若已結束且尚有待處理自動化則最多等 2.0 秒，隨後封存 session（`incomplete=not self.finished`）並等待自動化執行緒（2.0 秒）。不可在回呼內呼叫。 |
| `snapshot()` | 回傳深複製的狀態快照，不會觸發使用者回呼或自訂彙總函式。 |
| `repr(session)` | 回傳 `FlowSession(flow=..., finished=..., sealed=..., result=...)`。 |

### 步驟快照（`snapshot()`）

`session.snapshot()` 的頂層鍵包含 `name`、`version`、`parsers`、`analyzers`、`automations`、`views`（`{"schema_version": 1, "items": [...], "digest": sha256_hex}`）、`diagnostics`（尾端最多 100 筆）、`error_count`、`automation_error_count`、`complete`、`finished`、`result`、`result_source`、`result_code`、`publication_error` 與 `aggregation_error`。

`parsers`、`analyzers` 與 `automations` 內每個步驟項目包含以下欄位：

| 階段 | 步驟欄位 |
| :--- | :--- |
| 所有步驟 | `name`、`count`、`publication_count`、`processed`、`code`，以及選填的 `error` |
| `parsers` 與 `analyzers` | `version`、`outputs`、`latest`、`integer_fields`、`latest_evidence`、`evidence` |
| `analyzers` | `inputs`、`report_count`、`result`、`reason`、`final`、`issues`、`event_count`、`events`（尾端最多 100 筆） |
| `automations` | `inputs`、`enabled`、`on_report`、`on_job_event`、`action_count`、`last` |

## Context 與資料載體型別

### Context（`ParserContext`、`AnalyzerContext`、`AutomationContext`）

三種 context 皆提供 `.state`（由 `state_factory` 建立的私有工作狀態）、唯讀 `.source`（`stage`、`producer`、`kind`，以及選填的 `seq`、`name`、`evidence`、`execution`）、`.debug(message, data=None)` 與 `repr(ctx)`（`"<ContextClass>(producer=..., kind=..., open=...)"`）。Context 在回呼（含回傳的 generator）結束後立即失效，逾期存取會引發 `RuntimeError`。

- `AnalyzerContext.summary`：
  - `issue(name, level="info", message="", active=True, evidence=None)`：`level` 為 `"info" | "warning" | "error"`；`active=False` 會清除該進行中提示。
  - `event(name, level="info", message="", data=None, evidence=None)`：新增事件紀錄（`snapshot()` 保留尾端 100 筆；完整串流透過 `publish` 送出）。
  - `result(value, reason="", final=False, evidence=None)`：`value` 為 `Result` 或其字串值。嚴重度順序為 `"ERROR"`（2）> `"FAIL"`（1）> `"PASS"`／`"INCOMPLETE"`／`"N/A"`（0）。一旦 `final=True`，同級或較低嚴重度的更新都會被忽略；以 `"PASS"` 或 `"N/A"` 設為 `final=True` 時，必須位於完整結束的 `on_finish` 回呼中，否則引發 `ValueError`。
- `AutomationContext.actions`：
  - 僅在 `automation_enabled=True` 時可用，提供 `.stop`、`.send`、`.interrupt`、`.send_ctrl_c`、`.send_signal` 與 `.kill`。
  - 禁止傳入 `expected_attempt` 與 `expected_submission`（`TypeError`）。
  - 當 `control` 為非 Mapping 物件時，只有方法簽章接受 `source` 具名引數或 `**kwargs` 時，才會自動帶入 `source=f"automation:{name}"`。
  - `send(..., secret=True)` 會在稽核紀錄與 `ActionOutcome.reason` 中以 `"***"` 遮罩內容。

### `Tick`、`EndInfo`、`FlowEvent`、`AnalysisReport`、`ActionOutcome`、`Result`

| 型別 | 欄位／成員 | 說明 |
| :--- | :--- | :--- |
| `Tick` | `elapsed: float` | 傳給 `Analyzer.on_tick(tick, ctx)`；不會從 log 文字推算。 |
| `EndInfo` | `complete: bool`、`execution: Mapping` | 傳給 `on_finish(end, ctx)`；`execution` 為凍結的 `MappingProxyType`。 |
| `FlowEvent` | `name: str`、`data: Mapping` | `name` 為 `"started" \| "finished" \| "status_changed" \| "error"`；`data` 已凍結。 |
| `AnalysisReport` | `name: str`、`result: str \| None = None`、`reason: str = ""`、`values: Mapping \| None = None`、`evidence: Mapping \| None = None` | `name` 最多 256 字元；`values` 與 `evidence` 須為 JSON 相容資料。 |
| `ActionOutcome` | `status: str`、`reason: str` | `status` 為 `"accepted" \| "rejected" \| "failed"`，非空 `reason`；僅在 `"accepted"` 時 `.accepted` 與 `bool()` 為 `True`。 |
| `Result` | `PASS`、`FAIL`、`ERROR`、`INCOMPLETE`、`NA = "N/A"` | `Analyzer` 摘要與 `Flow` 彙總使用的 `str` 列舉。 |

## `ViewSpec`

由 `rpkbin.flow` 匯出的不可變、嚴格驗證圖表規格。

### 建立方式

```python
spec = ViewSpec(id, kind, *, source, axis, series, title=None, options=None, report_name=None)
spec = ViewSpec.waveform(id, *, source, axis, series, title=None, options=None, report_name=None)
spec = ViewSpec.xy_line(id, *, source, axis, series, title=None, options=None, report_name=None)
spec = ViewSpec.scatter(id, *, source, axis, series, title=None, options=None, report_name=None)
spec = ViewSpec.histogram(id, *, source, axis, series, title=None, options=None, report_name=None)
```

#### `source=` 契約

- 可傳入同一個 `Flow` 的 `Parser` 物件（`report_name` 必須為 `None`）、同一個 `Flow` 的 `Analyzer` 物件並必填 `report_name="..."`，或 JSON mapping `{"stage": "parser", "producer": "..."}`／`{"stage": "analyzer", "producer": "...", "report_name": "..."}`。
- 若 `source` 為 mapping 卻同時在外部傳入 `report_name=`，會引發 `ValueError`。

### 屬性與物件函式

| 成員 | 型別／回傳 | 意義 |
| :--- | :--- | :--- |
| `id`、`kind`、`title` | `str`、`str`、`str` | 唯一 view ID（`"__auto__"` 為保留字）、圖表類型（`"waveform"`、`"xy_line"`、`"scatter"`、`"histogram"`）與顯示標題（預設同 `id`）。 |
| `source`、`axis`、`series`、`options` | `MappingProxyType` | 遞迴凍結的設定 mapping。 |
| `to_json()` | `dict` | 回傳獨立的 JSON 相容字典，包含鍵 `id`、`kind`、`title`、`source`、`axis`、`series`、`options`。 |

### 設定結構與上限

- `axis`：`{"field": str | None, "label": str, "unit": str, "fallback": ["elapsed", "seq"], "origin": int | float}`。`origin` 必須搭配 `field`（整數支援至 `2**256 - 1`，有限浮點數 `|v| <= 1e150`）。`scatter` 要求 `axis["field"]` 不得為 `None`。
- `series`：
  - `"mode": "wide"`：`{"mode": "wide", "items": [{"id", "field", ...}], "defaults": [...]}`（1 至 16 個項目；`defaults` 須為項目 ID 子集，預設取前 4 項）。
  - `"mode": "long"`：`{"mode": "long", "id_field", "value_field", "path_field", "value_type", "bits", "frac_bits", "signed", "radix", "overrides": [...], "defaults": [...]}`（`overrides` 最多 128 筆、`defaults` 最多 16 筆）。
  - 型別：`"bit"`、`"bus"`、`"signed_bus"`、`"fxp"`（必填 `frac_bits` `0..256`）、`"state"`、`"number"`。Bus 要求 `bits` 在 `1..256`、`radix` 為 `"hex" | "bin" | "unsigned" | "signed" | "fxp"`。科學圖表（`xy_line`、`scatter`、`histogram`）僅接受 `"number"`。
- `options`：
  - `waveform`：`{"radix": "hex" | "bin" | "unsigned" | "signed" | "fxp"}`
  - `xy_line`：`{"thresholds": [{"series_id", "side": "min"|"max", "value", "label"}]}`（最多 32 條，`(series_id, side)` 不可重複）
  - `scatter`：`{"equal_aspect": bool}`（預設 `False`）
  - `histogram`：`{"bins": int | list[number]}`（預設 `32`；整數 `1..128` 或 `2..129` 個嚴格遞增邊界）
- Flow 與文字上限：每個 `Flow` 最多 8 個 view、每次發布最多投影 32 個樣本、canonical JSON 總長 `<= 32,688` 位元組（`32 * 1024 - 80`）、一般 view 文字 `<= 128` UTF-8 位元組、series／override／default 的 `id` 與 `label` `<= 512` UTF-8 位元組、禁止 ASCII 控制字元（`< 32`）與 `<`／`>`、數值須有限（`|int| <= 2**53 - 1`、`|float| <= 1e150`）。

## 範例

### 1. 基本範例：Context Manager 封存與 `finish()` 對比

檢視 `repr(session)` 與 `repr(ctx)`，並驗證未呼叫 `finish()` 即離開 `with FlowSession(...)` 時會自動封存為 `"INCOMPLETE"`。

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

輸出：
```text
unfinished repr: FlowSession(flow='lifecycle', finished=True, sealed=True, result='INCOMPLETE')
unfinished result: INCOMPLETE
ctx repr: ParserContext(producer='nums', kind='line', open=True)
```

### 2. 進階範例：`ViewSpec` 字典 `source=`、`Analyzer` `report_name=` 與 `to_json()`

分別從 `Parser` 物件、`Analyzer`（搭配 `report_name=`）與 JSON `dict` 建立三個 `ViewSpec`，掛載於 `Flow(..., views=...)`。

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

輸出：
```text
view ids: ['raw_err', 'err_hist', 'step_vs_err']
has digest: True
to_json keys: ['axis', 'id', 'kind', 'options', 'series', 'source', 'title']
```

## 執行期上限與錯誤

- **單次回呼與單次輸入週期上限**：`MAX_OUTPUTS_PER_CALL = 10000`、`MAX_OUTPUTS_PER_CYCLE = 10000`、`MAX_OUTPUT_BYTES = 4 * 1024 * 1024`（ASCII 跳脫 JSON）、`TAIL_LIMIT = 100`（適用於 `diagnostics` 與步驟 `events`）、`MAX_CODE_CHARS = 16384`。
- **自動化佇列與收尾逾時**：`AutomationRunner` 最多排隊 `256` 筆（編碼後 `4 MiB`），且在 `finish()` 後套用 `COMPLETION_TIMEOUT = 2.0` 秒收尾期限。
- **即時驗證與失敗隔離**：`Parser` 或 `Analyzer` 若輸出非 JSON 相容資料（`NaN`、`Infinity`、非字串字典鍵或自訂物件）、超出輸出上限或拋出例外，該步驟與所有下游相依 `Analyzer` 會停用並標為 `ERROR`，失敗當次回呼的任何暫存輸出都不會發布。

## 延伸閱讀

- [快速入門](README.md)
- [實戰使用情境](scenarios.md)
- [rpkbin.jobcore 參考手冊](../jobcore/reference.md)
- [Wave Flow 指南](../wave/flow.md)
- [文件導覽](../README.md)
