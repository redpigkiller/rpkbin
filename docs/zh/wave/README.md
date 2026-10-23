# Wave 快速入門：第一次完成一批工作

[English](../../en/wave/README.md) · [文件導覽](../README.md)

Wave 是一個以 Python 定義外部命令、統一負責排程、即時輸出過濾與驗收判定的工具。無論是透過終端機批次執行（Headless），或是透過本機瀏覽器介面（Web GUI）監看與操作，都**使用同一份 Python 工作定義檔**。

本指南將帶你走過第一次完成一批工作的完整流程。

---

## 步驟 1：建立工作定義檔 (`work.py`)

在工作目錄下建立一個名為 `work.py` 的檔案。

工作定義檔的職責是宣告工作（`Job`）與監控規則，並將它們加入管理器（`JobManager`）。**不要在工作檔中呼叫 `start()` 或 `wait()`**，派發的時機由 Wave 執行入口決定。

```python
"""work.py — 定義兩個標準的模擬示範工作。"""
import sys
from rpkbin.wave import Job, JobManager, Expect

# 1. 建立管理器：限制最多同時執行 2 個工作
mgr = JobManager(max_workers=2)

# 2. 定義第一個工作：正常通過的模擬工作
# 這裡使用 Python 內建指令模擬外部 EDA/編譯工具輸出
job1 = mgr.add(Job(
    "sim_core",
    [sys.executable, "-u", "-c", "import time; print('START', flush=True); time.sleep(0.5); print('ALL_TESTS_PASSED', flush=True)"]
))
# 宣告檢查條件：日誌中必須出現 ALL_TESTS_PASSED，且程序退出碼為 0 才算 PASS
job1.check("marker", match=r"^ALL_TESTS_PASSED$", expect=Expect.PRESENT)

# 3. 定義第二個工作：模擬另一個子系統
job2 = mgr.add(Job(
    "sim_dma",
    [sys.executable, "-u", "-c", "import time; print('START', flush=True); time.sleep(0.3); print('ALL_TESTS_PASSED', flush=True)"]
))
job2.check("marker", match=r"^ALL_TESTS_PASSED$", expect=Expect.PRESENT)
```

> [!TIP]
> 子程序建議使用無緩衝輸出（如 Python 的 `-u` 參數或 `print(..., flush=True)`），以確保 Wave 能即時接收每行日誌並進行判定。

---

## 步驟 2 方式 A：以命令列批次執行（Headless 模式）

若你想在終端機或 CI/CD 流程中自動化執行，請加上 `--headless` 參數：

```bash
rpk-wave work.py --headless
```

### 預期終端機輸出

執行時終端機會即時串流各工作的日誌，所有工作完成並排空清理後，會輸出最終摘要表格：

```text
[Wave] Starting 2 job(s); workers=2
[child sim_core #1] START
[child sim_dma #1] START
[child sim_dma #1] ALL_TESTS_PASSED
[child sim_core #1] ALL_TESTS_PASSED

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
sim_core                   DONE            PASS         0         0
  checks: marker: OK
sim_dma                    DONE            PASS         0         0
  checks: marker: OK
```

### 結果說明與退出碼

- **STATE**：表示子程序執行狀態（`DONE` 代表程序正常退出）。
- **RESULT**：表示業務驗收結果（`PASS` 代表所有宣告的 checks 皆達成，且程序退出碼為 0）。
- **退出碼**：當所有工作的 Result 皆為 `PASS` 或 `N/A` 時，命令退出碼為 `0`；若有任何工作為 `FAIL`、`ERROR` 或 `INCOMPLETE`，退出碼為 `1`。

---

## 步驟 2 方式 B：以 Web GUI 介面執行

若你想在圖形介面中觀察進度、檢視即時日誌或動態控制，不加 `--headless` 即可啟動本機 GUI：

```bash
rpk-wave work.py
```

終端機會顯示啟動位址，並自動在瀏覽器中開啟：

```text
[Wave] GUI: http://127.0.0.1:54321 (Ctrl+C shuts down the core; closing the page does not)
```

### GUI 操作三步驟

1. **檢查預覽（Preview）**：
   - 載入工作檔後，狀態列會顯示 `Preview — review, then Start.`。此時排程器**尚未開始執行任何子程序**。
   - 左側工作表格會列出剛定義的 `sim_core` 與 `sim_dma`，狀態皆為 `PENDING`（等待派發）。
2. **點擊 Start 開始派發**：
   - 點擊頂部工具列綠色的 **Start (2)** 按鈕。
   - 核心開始派發工作，狀態轉為 `RUNNING`。
3. **監看與檢查結果**：
   - 點擊左側表格中的工作名稱，右側 Log 區域會即時更新該工作的輸出。
   - 工作完成後，左側表格的 **State** 欄位變為 `DONE`，**Result** 欄位變為綠色 `PASS`。
   - 雙擊工作列或點擊右側的 **Details** 分頁，可以查看檢查項目 `marker: OK` 的達成細節與行號證據。

---

## 核心認知與重要邊界

1. **預覽與派發分離**：
   - 載入 Python 工作定義檔時，Wave 會在定義作用域（`definition_scope`）內執行檔案以收集工作與驗證參數。此作用域僅攔截工作檔內誤呼叫 `start()` 或 `resume()`，以一般 Python 使用者權限執行，並非抵禦惡意程式碼的安全沙盒。載入後排程器不會自動啟動程序，必須明確點擊 GUI 的 **Start** 或由 `--headless` 命令啟動，工作才會開始派發。
2. **關閉瀏覽器 vs. 關閉核心**：
   - 關閉瀏覽器分頁**不會**終止背後執行的工作。核心仍在本機背景持續運行與記錄。
   - 重新打開網址即可繼續監看。若要徹底停止核心與所有子程序，請在啟動它的終端機按下 `Ctrl+C`。
3. **程序退出 0 不等於 PASS**：
   - 子程序 exit code 0 僅代表程序正常結束（`State=DONE`）。
   - 若有 check 未滿足（例如漏印了特定文字），即使程序退出碼是 0，`Result` 依然是 `FAIL`！
   - 若未設定任何 check，且正常結束，`Result` 會是 `N/A`。

---

## 下一步

現在你已掌握最基礎的工作執行流程，請繼續參閱：

- **[由淺入深的 Python 使用情境](scenarios.md)**：學習如何組合多個 checks、設定警告（warnings）、擷取動態資料（track）、送出文字與訊號、設定資源配額與自動重試。
- **[Web GUI 操作指南](gui.md)**：深入學習多工作篩選、批次控制、群發文字，以及 Settings 面板的使用。
- **[Python API 參考手冊](reference/python.md)**：查閱各函式與類別的完整簽名與規格。
