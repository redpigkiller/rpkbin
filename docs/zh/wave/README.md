# rpkbin.wave

結合 Python 工作排程、即時記錄檔解析、波形圖表檢視與 SQLite 封存資料庫的執行環境。

## 語法

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
run_headless(ws)   # 終端機無頭執行
launch(ws)         # 開啟本機網頁介面
```

## 說明

`rpkbin.wave` 在單一工作區中串接四項能力：

1. 工作定義與排程控制（`Job`、`JobManager`、資源分配、`HELD` 暫緩排除與優先排序）。
2. 逐行串流解析、分析與自動化回應（`Flow`、`Parser`、`Analyzer`、`Automation`、`ViewSpec`）。
3. 本機網頁圖形介面監控與除錯（佇列操作、限定範圍的記錄搜尋、解析檢視、波形與 XY 圖表）。
4. 寫入 SQLite 封存資料庫（`RunArchive`），支援保留模式（`persist`、`keep_on_fail`、`ephemeral`）、NFS 本機暫存分段與離線重析（`reanalyze`）。

未傳入 `--headless` 時，網頁伺服器預設會以預覽模式啟動，讓您先檢查工作內容、調整設定或移除不想執行的項目，按下啟動按鈕後才會建立子程序。

## 範例

### 1. 最小範例

在暫存封存目錄中執行單一有限工作，並印出回傳碼與封存處置結果：

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

輸出（Wave 摘要表之後）：

```text
rc: 0 status: closed disposition: saved
```

### 2. 進階範例

替兩項工作掛上包含 `Parser` 與 `Analyzer` 的 `Flow`，無頭執行後從 `RunArchive` 讀取分析結果報表：

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

輸出（Wave 摘要表之後）：

```text
clean PASS 0
noisy FAIL 1
batch rc: 0
```

## 文件導覽

| 文件 | 涵蓋主題 |
| --- | --- |
| [系統架構](architecture.md) | 資料流、執行緒分工、預覽與執行狀態、本機安全邊界 |
| [Flow 分析管線](flow.md) | `Parser`、`Analyzer`、`Automation`、`ViewSpec` 圖表與 `FlowSession` 離線測試 |
| [網頁圖形介面](gui.md) | 版面模式、佇列暫緩與排序、Flow/Log 檢視器與波形操作快捷鍵 |
| [歷史與封存機制](history.md) | 封存檔名格式、保留策略、NFS 暫存分段、自動過期清理與 `RunArchive` 查詢 |
| [情境與工作流範例](scenarios.md) | 模擬批次、互動提示處理、`logs`/`reanalyze` 流程與保留策略設定 |
| [疑難排解](troubleshooting.md) | 常見異常現象、成因診斷與排解方式 |
| [命令列參考](reference/cli.md) | CLI 旗標、`RPK_WAVE_*` 環境變數與結束代碼 |
| [Python API 參考](reference/python.md) | 類別、函式簽章、預設值與本機 HTTP 端點 |

## 注意事項與限制

- **受信任的工作檔**：載入 `.py` 工作檔會執行其中的頂層 Python 程式碼。若只需唯讀檢視外部記錄，請改以 `--archive <file>.sqlite3` 開啟。
- **PTY 與管線差異**：在 POSIX 系統中，`Job` 預設啟用 PTY（`pty=True`）；Windows 則預設使用管線（`pty=False`）。在管線下緩衝標準輸出的互動程式，可能需要適時 flush 或搭配 `prompts` 觸發即時輸出。
- **僅限本機繫結**：`WaveServer` 只允許繫結 loopback 位址（`127.0.0.1`、`localhost`、`::1`）。若需遠端存取，請透過 SSH 連接埠轉送使用。

## 另請參閱

- [JobCore 文件](../jobcore/README.md)
- [Flow 文件](../flow/README.md)
- [rpk-wave 命令列參考](reference/cli.md)
- [Python API 參考](reference/python.md)
