# Flow 分析管線

透過 `rpkbin.flow` 將工作的原始輸出行轉換成結構化記錄、即時 PASS/FAIL 報表、自動化控制動作與互動式波形圖表。

## 管線模型

每個 `Flow` 由三個處理階段與可選的圖表檢視組成：

```python
from rpkbin.flow import Flow, Parser, Analyzer, Automation, Binding, ViewSpec

flow = Flow(
    "sim_flow",
    parsers=(...,),
    analyzers=(...,),
    automations=(...,),
    result=None,       # 可選的主要 Analyzer 或結果選擇規則
    views=(...,),      # 可選的 ViewSpec 圖表定義
)
```

- **第一階段（`Parser`）**：透過 `parse(line, ctx)` 逐行解析原始文字，並回傳標準化的 Python 字典（`return {...}` 或 `Result(...)`）。產出的資料會顯示在 **Inspect -> Flow -> Parsed Records** 供篩選與搜尋。
- **第二階段（`Analyzer`）**：透過 `Binding(source, handler)` 訂閱解析記錄（`records=`）、上游分析報表（`reports=`）、定期計時器（`on_tick=`、`every=秒數`，接收 `Tick(elapsed)`），或在結束時執行 `on_finish(info, ctx)`（接收 `EndInfo(complete, execution)`）。回傳 `AnalysisReport(name, result="PASS"|"FAIL", reason=..., values=..., evidence=...)`。
- **第三階段（`Automation`）**：透過 `Binding(source, handler)` 訂閱分析報表（`reports=`）或介面／API 事件（`on_event(event, ctx)`，接收 `FlowEvent`），經由 `ctx.actions` 發送非阻塞控制動作（`send`、`stop`、`force_stop`、`interrupt`、`signal`），並將 `ActionOutcome` 寫入歷程。

## 圖表檢視與投影（`ViewSpec`）

在 `Flow(..., views=(...))` 中宣告一或多個 `ViewSpec`，就能在網頁介面的 **Charts** 面板與 SQLite 封存索引中建立對應圖表：

| 類型 | 輔助建構函式 | 常見用途 |
| --- | --- | --- |
| `waveform` | `ViewSpec.waveform(id, source=..., axis=..., series=..., title=None, options=None)` | 多位元匯流排、單一位元旗標、邏輯狀態（`0`/`1`/`X`/`Z`）、步階類比曲線、VCD 匯出 |
| `xy_line` | `ViewSpec.xy_line(...)` | 時間序列或參數掃描折線圖 |
| `scatter` | `ViewSpec.scatter(...)` | 抽樣成對數值分佈圖 |
| `histogram` | `ViewSpec.histogram(...)` | 數值區間直方圖 |

當 `Flow` 沒有宣告任何 `views` 時，Wave 會檢查每個 `Parser` 最近最多 **100** 筆數值與純量解析記錄，符合條件時自動產生圖表。明確宣告 `ViewSpec` 時，需傳入 `source`（`Parser`/`Analyzer` 物件或 `{"stage": ..., "producer": ...}` 字典）與 `series`（包含 `"mode": "wide"` 與 `"items": [...]`，或 `"mode": "long"` 與 `"id_field"`、`"value_field"` 的字典），資料會直接建入 SQLite 索引，即使記錄高達數百萬行也能透過 `RunArchive.chart_window` 快速分段載入。

## 互動提示與訊號派送

許多模擬器或互動式工具會在等待輸入時停止輸出換行符號（例如 `Continue? [y/N]: `）。只要在 `Job` 中設定 `prompts`：

```python
Job("bench", cmd, prompts=("Continue? [y/N]:", "Password:"))
```

Wave 會在輸出緩衝區（去除 ANSI 控制碼與尾端空白後）的結尾符合 `prompts` 任一項目時立即送出該行，不必等待 `\n`。接著 `Automation` 規則或使用者就能立刻回覆：

- `ctx.actions.send("y", newline=True)` 會預設附上換行字元；若傳入 `newline=False`，則只傳送原始字元。
- `ctx.actions.signal(sig)` 或網頁介面的 **Signal** 選單可在 POSIX 主機派送 `SIGINT`、`SIGTERM`、`SIGTSTP`、`SIGCONT`、`SIGUSR1`、`SIGUSR2`、`SIGHUP`、`SIGQUIT`。

## 範例

### 1. 最小範例（`FlowSession` 離線單元測試）

不必啟動任何子程序，直接在記憶體中驗證 `Parser` 與 `Analyzer` 邏輯：

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

輸出：

```text
FAIL complete=True, errors=2
```

### 2. 進階範例（`EndInfo` 與 `ViewSpec.waveform`）

執行輸出匯流排與旗標變化的批次工作，搭配結束判定與波形宣告，最後從 `RunArchive` 讀取波形點數：

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

輸出（Wave 摘要表之後）：

```text
rc: 0 total points: 6
```

## 注意事項與限制

- **保持 `Parser` 輕量無副作用**：`Parser.parse` 會直接在讀取每一行時同步呼叫，切勿在其中進行磁碟寫入或網路連線。
- **將主動介入集中在 `Automation`**：寄送互動指令、發出警報或緊急終止程序都應寫在 `Automation` 內，避免阻塞資料解析執行緒。
- **先測試再重析**：修改 `Parser` 或 `ViewSpec` 後，建議先用 `FlowSession` 跑離線單元測試，再對既有的封存檔執行 `rpk-wave reanalyze`。

## 另請參閱

- [Wave 總覽](README.md)
- [網頁圖形介面](gui.md)
- [情境與工作流範例](scenarios.md)
- [Python API 參考](reference/python.md)
