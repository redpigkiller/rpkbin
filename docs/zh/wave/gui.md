# Wave Web GUI 操作指南

[English](../../en/wave/gui.md) · [文件導覽](../README.md) · [快速入門](README.md)

Wave 提供一個免安裝第三方前端相依、基於本機瀏覽器的 Web GUI 介面。本文件詳細說明版面配置、操作流程、批次控制與各項設定。

---

## 啟動與連線

透過指令啟動 GUI：

```bash
# 預設載入工作檔並自動開啟瀏覽器
rpk-wave work.py

# 指定本機連接埠，且不自動彈出瀏覽器
rpk-wave work.py -p 8080 --no-browser

# 不帶工作檔啟動空白介面，稍後在 GUI 中載入
rpk-wave
```

終端機會顯示伺服器位址：
```text
[Wave] GUI: http://127.0.0.1:54321 (Ctrl+C shuts down the core; closing the page does not)
```

> [!NOTE]
> **本機綁定與安全性邊界**：
> Wave GUI 伺服器僅設計作為本機開發與監看工具，內部無多使用者密碼認證機制。因此核心**明確僅接受 loopback 介面（`127.0.0.1` 或 `localhost`）**；傳入 `0.0.0.0` 會引發 `ValueError`。若需於遠端主機檢視，請透過 SSH port forwarding（例如 `ssh -L 54321:127.0.0.1:54321 user@host`）安全轉發。

> [!IMPORTANT]
> **生命週期提醒**：
> - **關閉瀏覽器分頁**：核心程式與所有子程序依然在本機背景正常運行並持續記錄日誌。重新開啟網址即可恢復監看。
> - **結束核心**：在啟動 `rpk-wave` 的終端機按下 `Ctrl+C`，核心才會啟動收尾流程，依序中斷所有子程序並清理資源後退出。

---

## 介面總覽

GUI 採用現代高密度深色設計，主要劃分為三大區域：

```
+---------------------------------------------------------------------------------+
| 頂部導覽列 (Topbar): 狀態燈號、工作狀態膠囊統計、Queue/All 操作按鈕、Settings 按鈕  |
+----------------------------------------+----------------------------------------+
| 左側：工作清單與批次操作 (Job Table)     | 右側：選定工作日誌與診斷 (Log & Details)|
| - 搜尋與狀態過濾器                      | - Attempt 輪次切換 (歷史唯讀)           |
| - 勾選多選批次工具列                    | - Log / Details 檢視切換               |
| - 工作表格 (State, Result, Warnings,    | - 即時日誌過濾 (純文字) 與 Auto-scroll   |
|   Priority, 耗時, 自訂 Track 欄位)     | - 底部 stdin 輸入列與 Ctrl+C 按鈕       |
+----------------------------------------+----------------------------------------+
```

---

## 核心操作流程

### 1. 載入工作檔與預覽 (Preview)
- **載入方式**：
  - 在啟動時指定：`rpk-wave work.py`
  - 或在 GUI 頂部點擊 **Load Work File...**，輸入 Python 檔案路徑並點擊 Load。
- **載入權限與邊界說明**：
  - 載入工作檔時，Python 解譯器會以**您當前使用者的完整系統權限**執行該腳本以解析 `JobManager` 與工作宣告。
  - Wave 的 `definition_scope` **僅禁止呼叫 `mgr.start()` / `mgr.resume()`**，避免在預覽確認前派發程序；它**不是**沙盒（Sandbox），也不阻止任意 Python 檔案系統操作或啟動外部程式。**請務必僅載入您信任的 Python 工作定義檔**。
  - 載入成功後，頂部狀態列會顯示 `Preview — review, then Start.`，所有工作狀態為 `PENDING`。
  - **此時排程器尚未開始派發任何工作**。你可以安心檢查工作清單、參數與宣告。

### 2. 啟動與佇列管理 (Start / Pause / Stop)
- **Start**：點擊頂部綠色的 **Start N Jobs** 按鈕，核心正式開始依照優先權與資源限制派發工作。
- **Pause Queue / Resume Queue**：暫停派發。已在運行的工作會繼續執行，但處於等待狀態的工作不會被派發，直到點擊 Resume。
- **Stop All / Force Stop All**：
  - **Stop All**：依據各工作的 `stop_policy`（預設為優雅終止後超時強制終止）取消所有等待工作並停止運行中的工作。
  - **Force Stop All**：立即向所有活躍子程序發送強制終止信號（Kill）。

---

## 工作清單操作 (左側面板)

### 篩選與搜尋
- **Search jobs...**：依工作名稱即時過濾。
- **狀態下拉選單 (State)**：可篩選 `All`、`PENDING`、`RUNNING`、`PROMPT DETECTED` (對應內部 WAITING_INPUT)、`STOPPING`、`FINISHED` (對應內部 DONE)、`FAILED`、`CANCELLED`。
- **結果下拉選單 (Result)**：可篩選 `All`、`Undecided`、`PASS`、`FAIL`、`ERROR`、`INCOMPLETE`、`N/A`。
- **Warnings 開關**：勾選時僅列出有產生警告的工作。

### 點擊與檢視
- **單擊工作列**：在右側載入該工作的即時日誌（Log）。
- **雙擊工作列**：直接切換至右側的 **Details** 診斷分頁。
- **點擊 Warning 標籤**：直接跳轉至 Details 面板查看警告觸發證據。

### 批次勾選與控制
在工作列左側勾選核取方塊後，表格上方會顯示勾選統計（如 `3 checked`）並彈出批次操作列：
- **按鈕適用計數顯示**：按鈕文字會明確標示「實際可套用數量 / 勾選總數」，例如：
  - `Stop/Cancel 2/3`：例如 1 個執行中、1 個 PENDING、1 個已完成；會停止執行中的工作、取消 PENDING，略過已完成者。按鈕提示會列出取消／停止數量與略過原因。
  - `Rerun 1/3`：代表僅有 1 個已結束的工作可被重跑。
  - `Send (2/3)…`：代表向 2 個運行中的工作發送 stdin。
- **略過提示**：將滑鼠懸停於按鈕上，會以浮動提示（title）清楚列出哪些工作被略過（skipped）以及當前狀態。

---

## 工作日誌與診斷 (右側面板)

### Attempt 輪次切換與歷史隔離
頂部下拉選單可切換檢視當前輪次或過往歷史：
- **`Current (#N · RESULT/STATE)`**：當前或最新的 Attempt。操作仍受執行狀態限制：執行中可送 stdin／停止，完成且清理結束後才可 Rerun。
- **`History #N · RESULT/STATE`**：已完成的過往歷史輪次。
- **歷史唯讀保證**：當檢視歷史 Attempt 時，面板會明確提示唯讀，所有停止按鈕、Rerun 與 stdin 輸入列均自動處於**禁用狀態**，杜絕跨輪次誤操作。
- **歷史被淘汰後的處置**：若在檢視某個歷史 Attempt 時，因全域 `max_history` 縮小而導致該 Attempt 被淘汰，下拉選單會顯示 `History unavailable`，介面維持禁用狀態，點擊 **Back to Current** 即可返回活躍輪次。

### Log 與 Details 切換
- **Log 分頁**：
  - 即時輸出監控，上方搜尋框為**不分大小寫的純文字子字串比對 (`includes`)**（非正則）。
  - **Auto-scroll**：預設開啟。若使用者手動向上滾動查看歷史，自動滾動會暫停；滾動回底部時重新自動勾選。
- **Details 分頁**：
  - **Checks 狀態列表**：清楚顯示各 check 的狀態（`OK`、`FAIL`、`ERROR`、`INCOMPLETE`）。
  - **觸發證據跳轉**：點擊 check 失敗或 warning 的證據列，會自動切換至 Log 視圖並高亮定位至該行日誌。
  - **終止原因 (Termination)**：顯示工作結束的原因與停止發起者（如 `user`、`monitor`、`timeout`）。

### stdin 互動輸入與信號傳送
位於右側面板底部：
- **文字輸入框**：在工作處於 `RUNNING` 或 `WAITING_INPUT` 時可用。輸入文字後按 `Enter` 送出。
- **傳輸反饋**：送出成功僅代表資料已寫入子程序的 stdin 管道，**不等於外部工具已處理或接受命令**。
- **Ctrl+C 按鈕**：向子程序發送中斷信號。

---

## 設定面板 (Settings Modal)

點擊頂部導覽列右側的 **Settings** 按鈕可開啟全域設定視窗。設定分為三大分區：

### Section A: Execution Limits (執行配額)
*作用範圍：當次核心運行狀態（Core Session）。重啟後依 Python 原始定義。*
- **Concurrent jobs (`max_workers`)**：動態調整最大並行數。降低數值不會終止正在運行的工作，但會延後後續等待工作的派發。
- **Fixed Quotas (`resources`)**：動態調整各項命名資源的容量上限。
- 點擊 **Apply Execution Limits** 套用。**注意：修改配額設定不會主動解除佇列的暫停狀態（Pause）**。

### Section B: Memory Retention Policy (記憶體保留政策)
*作用範圍：當次核心運行狀態（Core Session）。*
1. **Per-Job Log Retention (`max_log_lines`)**：
   - 每個工作在記憶體中保留的日誌行數上限（Python 預設 10000 行）。
   - 可選擇 **Apply to Selected Job**（僅套用至當前選定工作）或 **Apply to All Jobs**（套用至所有工作）。
   - 調小數值會立即淘汰過舊日誌；調大數值不會復原已淘汰的行。**日誌淘汰不影響行號計數、已儲存的 check 證據與 Result 判定**。
2. **Per-Job Historical Attempts Retention (`max_history`)**：
   - 每個工作在記憶體中保留的最大過往 attempt 輪數（預設 20 輪）。
   - 調小數值會淘汰最舊的已完成歷史，介面會揭露截斷資訊，但不會重算歷史 Result。

### Section C: Display Preferences & Table Columns (顯示偏好與欄位)
*作用範圍：顯示偏好存於瀏覽器端 `localStorage`；自訂欄位由當次核心持有。*
- **Log font size**：可調整日誌字級（10px 至 15px）。
- **Wrap long log lines**：是否自動換行。
- **Monitor layout**：切換 **Split（左右分割）** 或 **Stack（上下分割）** 佈局，並可手動設定分割百分比。點擊 **Reset Display Preferences** 可恢復預設。
- **Visible Table Columns**：`Elapsed`／`Priority` 預設收起，可勾選恢復；已儲存的個人偏好仍會保留。自訂 track 欄位勾選後點擊 **Apply Columns** 套用至核心狀態（`State`、`Result` 與 `Warnings` 固定顯示）。
