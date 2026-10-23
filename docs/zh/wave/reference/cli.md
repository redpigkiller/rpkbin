# Wave CLI 與退出碼參考手冊

[English](../../../en/wave/reference/cli.md) · [文件導覽](../../README.md) · [快速入門](../README.md)

Wave 提供命令列入口工具 `rpk-wave`，用於啟動本機 Web GUI 或執行非互動式的 Headless 批次任務。

```bash
rpk-wave [TARGET] [OPTIONS]
```

---

## 參數說明

### 位置參數 (Positional Arguments)

| 參數 | 型別 | 說明 |
| :--- | :--- | :--- |
| **`TARGET`** | 檔案路徑 | 信任的 Python 工作定義檔（`.py`）。GUI 模式下載入後先進入預覽；Headless 模式下驗證後立即開始排程。 |

---

### 選項參數 (Options)

| 選項 | 參數型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| **`--headless`** | flag | `False` | 啟用純終端機批次執行模式。即時串流各子程序日誌，執行結束後印出不可變摘要表格並返回退出碼。 |
| **`--timeout`** | 浮點數 (秒) | `None` | Headless 模式專用。整個批次執行的時間上限。超時會向所有未完成工作發送停止請求，等待清理後退出。 |
| **`-j`, `--max-workers`** | 正整數 | 依工作檔宣告 | 在排程器啟動前覆寫最大並行工作數。必須 $\ge 1$。 |
| **`--demo`** | flag | `False` | 載入內建的 3 個通用示範工作。不可與 `TARGET` 同時使用。 |
| **`--host`** | 字串 (IP) | `127.0.0.1` | Web GUI 伺服器綁定的本機位址。基於安全限制僅支援 loopback（`127.0.0.1` 或 `localhost`）。 |
| **`-p`, `--port`** | 整數 (0~65535) | `0` | Web GUI 連接埠。設為 `0` 表示由作業系統自動分配可用埠號。 |
| **`--no-browser`** | flag | `False` | 啟動 Web GUI 時不自動開啟瀏覽器視窗（適用於遠端伺服器或 headless 環境）。 |
| **`-h`, `--help`** | flag | — | 顯示命令列參數說明並退出。 |

---

## 程序退出碼 (Exit Codes)

Wave 遵循標準 UNIX 退出碼規範，方便整合至 CI/CD 流程與 Shell 腳本：

| 退出碼 | 名稱 | 觸發情境與意義 |
| :---: | :--- | :--- |
| **`0`** | **SUCCESS** | **所有工作均正常驗收通過**。<br>- Headless 模式：所有已派發工作的最終 Result 皆為 `PASS` 或 `N/A`，且無超時。<br>- GUI 模式：核心正常關閉退出。 |
| **`1`** | **FAILURE** | **驗收不符合預期或發生逾時**。<br>- Headless 模式中，至少有 1 個工作的 Result 為 `FAIL`、`ERROR` 或 `INCOMPLETE`。<br>- 或整批執行觸發了 `--timeout` 逾時限制。 |
| **`2`** | **ERROR** | **命令列參數錯誤或工作檔載入失敗**。<br>- 命令列參數不合法（如未指定 `--headless` 卻提供 `--timeout`）。<br>- Python 工作檔路徑不存在或並非 `.py` 檔案。<br>- 工作定義檔在載入期拋出語法例外、非法宣告或呼叫了被禁用的 `sys.exit()`。 |
| **`130`** | **INTERRUPT** | **使用者中斷 (SIGINT / Ctrl+C)**。<br>終端機收到中斷信號，Wave 順利完成程序樹停止與日誌排空清理後安全退出。 |

---

## 常用指令組合範例

### 1. 本機開發：GUI 預覽並自動開啟瀏覽器
```bash
rpk-wave work.py
```

### 2. CI/CD 自動化流程：超時限制與並行覆寫
```bash
rpk-wave work.py --headless --timeout 1800 -j 8
```
- 若 30 分鐘內未完成，自動啟動停止清理並以退出碼 `1` 退出。
- 將並行數強制設定為 8。

### 3. 無工作檔時快速體驗 GUI
```bash
rpk-wave --demo
```
載入 3 個示範工作，方便直接觀察介面互動與日誌流。
