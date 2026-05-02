# StageTracker — 多階段流程紀錄

[![English](https://img.shields.io/badge/Language-English-blue.svg)](stage_tracker.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](stage_tracker_zh.md)

`StageTracker` 是一個多階段執行追蹤器，提供系統日誌 (Logging)、問題收集、執行時間記錄與結束時的自動摘要報告。

**唯一合法的使用方式是將整個流程包裝在 `with StageTracker() as t:` 中，不支援其他用法。**

---

## 快速入門 (User Guide)

### 1. 扁平模式 (Flat Mode)

最適合由上而下、循序執行的腳本。手動呼叫 `begin_stage(name)` 來切換階段。

> [!IMPORTANT]
> 呼叫 `begin_stage` 會自動結算 (finalize) 前一個階段。如果前一個階段有累積任何 `error()` 或 `fatal()` 呼叫，`begin_stage` 將會**立刻拋出** `StageFailedError`。

最後一個階段的結帳與報表總結會由 `with` 區塊自然結束時完成。

```python
from rpkbin.utils.stage_tracker import StageTracker

with StageTracker("MainTracker", mode="flat") as t:
    t.begin_stage("Initialization")
    t.info("Starting workflow", track=True)
    t.warning("Debug mode enabled")

    # 隱式結束 "Initialization" 並開啟 "Data Processing"
    # 若 "Initialization" 階段有錯誤，此處會立即拋出 StageFailedError
    t.begin_stage("Data Processing")
    t.error("File 'corrupt.txt' is corrupt")  # 紀錄錯誤但不立刻中斷
    t.error("File 'missing.txt' not found")

    # 因為累積了 error，這裡會立即拋出 StageFailedError
    t.checkpoint()

    t.begin_stage("Export")
    t.info("Done writing.")
    # with 結束時自動收尾 "Export" 階段並印出報表
```

### 2. 內文管理器模式 (Context Manager Mode)

適合用在獨立區塊、迴圈或者較複雜的巢狀邏輯中。
使用 `with t.stage(name):` 包住每個階段的邏輯，擁有明確的進入與結束點。**不支援巢狀 stage！**

> [!TIP]
> **迴圈中必須使用唯一名稱：** 在迴圈中使用 Context Mode 時，請確保產生獨一無二的階段名稱（例如：`with t.stage(f"Process_{i}"):`）。重複使用相同階段名稱將會拋出 `UsageError`。

```python
from rpkbin.utils.stage_tracker import StageTracker

with StageTracker("ContextTracker", mode="context") as t:
    with t.stage("Download"):
        t.info("Downloading files...")
        # 離開 with 時會自動跑 health checked. 
        # 若在區塊中有任何 `t.error()` 呼叫，StageFailedError 將於此拋出。

    with t.stage("Parsing"):
        t.fatal("Out of memory!") # 紀錄嚴重錯誤並立即拋出 StageFailedError
```

> **注意事項**：請勿在同一個 `StageTracker` 實例中混合使用 Flat Mode 與 Context Manager Mode。

---

## API 參考 (Detailed Control)

### 建立物件 (Construction)

```python
StageTracker(
    name: str = "StageTracker",
    mode: Literal["flat", "context"] = "flat",
    plain: bool | None = None,
    track_time: bool = True,
)
```

**純文字模式 (`plain`)的自動偵測：**
若未明確指定 (`plain=None`)，它在以下任一條件成立時會自動關閉 `rich` 顏色優化並轉向純文字報表：環境變數具有 `NO_COLOR`、`TERM` 為 `dumb`/`unknown`、`stdout` 不是 TTY 等。

### 日誌紀錄 (Logging)

所有的 Log 方法底部都是呼叫標準 Python `logging` 模組，並支援將紀錄存入 issue 清單供報表追蹤。

| 方法 | Level | 預設 `track` | 說明 |
| --- | --- | --- | --- |
| `t.debug(msg)` | DEBUG | `False` | 偵錯訊息，預設不追蹤 |
| `t.info(msg)` | INFO | `False` | 一般資訊，預設不追蹤 |
| `t.warning(msg)` | WARNING | `True` | 警告，預設追蹤但不阻斷流程 |
| `t.error(msg)` | ERROR | `True` (強制) | 錯誤，追蹤並在 stage 結束時阻斷流程 |
| `t.fatal(msg)` | CRITICAL | `True` (強制) | 致命錯誤，立即拋出 `StageFailedError` |

*(註：可傳入 `track=True` 將 INFO 或 DEBUG 記錄加進最終報表裡展示)*

### 階段管理方法

| 方法 | 說明 |
| --- | --- |
| `begin_stage(name)` | Flat Mode 專用。開啟一個新階段，並自動檢查前一個階段的健康狀態（若失敗則拋出 `StageFailedError`）。 |
| `stage(name)` | Context Mode 專用。作為 Context Manager 開啟一個新階段，離開時自動檢查健康狀態。 |
| `checkpoint()` | 主動檢查當前階段的健康狀態。如果當前階段存在任何 ERROR/CRITICAL issue，會提早拋出 `StageFailedError`。 |

### 總結與報表

| 方法 | 說明 |
| --- | --- |
| `summary(title=..., raise_errors=True)` | 印出執行總結報告。若 `raise_errors=True` 且執行過程有錯誤，則會在印完後拋出 `StageFailedError`。在扁平模式下也會先收尾當前階段。 |

### 設定與擴充 (Configuration)

| 方法 | 說明 |
| --- | --- |
| `add_console_handler(level="INFO")` | 新增主控台輸出 (預設初始化時已加入)。若重複呼叫將不會產生疊加。 |
| `add_file_handler(path, level="DEBUG", max_bytes=0, backup_count=0)` | 新增檔案日誌輸出，可設定 `max_bytes` 與 `backup_count`。**提示：** 強烈建議在進入第一個階段前呼叫。 |

### Issue 查詢管理

| 方法 | 說明 |
| --- | --- |
| `get_issues(stage=None, level=None)` | 進行多條件過濾搜尋並回傳符合的 `Issue` dataclasses。 |
| `clear_issues()` | 清空紀錄資料板與歷史記錄。如果在某個 stage 執行期間呼叫，將拋出 `UsageError`。 |

---

## 行為與設計細節

### 例外處理行為與總結報表

`StageTracker` 會在退出 `with` 區塊時結帳並輸出 `summary()` 報表：

- **正常結束 (無例外)：** 將收尾最後一階段狀態，印出 `EXECUTION SUMMARY`；如果發現階段曾收到錯誤訊號，在此時會對外拋出 `StageFailedError`。
- **異常結束 (包含 `fatal`)：** 任何異常都會導致印出 `EXECUTION FAILED (例外類型)`，然後原始的異常物件會依原封不動地向外拋出。
- **未分配的記錄（"System" 階段）：** 如果在第一次 `begin_stage()` 之前，或在任何 `with t.stage()` 區塊外呼叫記錄方法（如 `t.error()`），這些記錄會被分配到 `"System"` 階段。如果 `"System"` 階段累積了 error，追蹤器依然會在最終結束時準確地攔截並報告失敗。

**報表輸出範例：**
```text
============================================================
                     EXECUTION SUMMARY                      
============================================================
Execution Paths by Thread:
  MainThread: Initialization → Data Processing → Export
------------------------------------------------------------
[ WARNING] Data Processing | File 'corrupt.txt' is corrupt (1.20s)
[  ERROR ] Data Processing | File 'missing.txt' not found (0.01s)
------------------------------------------------------------
FAILED: 1 critical/errors found.
============================================================
```

### `StageFailedError`

當階段結帳時認定有累積的錯誤等級記錄即拋出 `StageFailedError`。包含：
- `e.stage`: 造成失敗的模塊階段名稱。
- `e.error_count`: 計算阻斷流程的錯誤數。
- `e.issues`: 保留了對應問題的清單陣列，方便除錯。

### 多執行緒安全 (Thread Safety)

- `_issues` 清單狀態具備鎖定 (Lock) 保護，多執行緒同時 Log 不會出現衝突。
- 為了讓多條執行緒具備彼此獨立的執行流，`current_stage` 被實作為 `threading.local`。各執行緒會自己推進與處理自己的階段。
- 各執行的時間紀錄未加入鎖，設計上預設每條執行緒跑自己的自訂階段名（因此已做了名稱差異處理）。
- **重要提醒：** 當使用多執行緒時，必須在退出 `with` 區塊前手動將所有子執行緒 `join()`，以確保最終總結報表能完整收錄所有執行緒的紀錄。
