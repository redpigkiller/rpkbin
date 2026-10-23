# Wave 疑難排解指南 (Troubleshooting)

[English](../../en/wave/troubleshooting.md) · [文件導覽](../README.md) · [快速入門](README.md)

本文件收錄使用 Wave 與 JobManager 時常見的非預期行為、根本成因分析與建議處理解決方案。

---

## 1. 子程序正在運行，但 GUI 或終端機沒有即時輸出

### 現象
程序狀態顯示為 `RUNNING`，但右側日誌面板長時間空白，直到程序即將結束時才一口氣噴出所有日誌。

### 根本原因
作業系統的標準 C 函式庫（glibc、MSVCRT 等）或語言執行環境（如 Python、Perl）在 stdout 連接到非 TTY（管道 Pipes）時，預設採用 **全緩衝（Block Buffering，通常為 4KB 或 8KB）**，而非行緩衝（Line Buffering）。

### 解決方案
- **Python 子程序**：
  - 在執行參數加入 `-u` 參數：`cmd=[sys.executable, "-u", "script.py"]`
  - 或在輸出時強制 flush：`print("message", flush=True)`
- **C/C++ 外部工具**：
  - 在代碼中加入 `setvbuf(stdout, NULL, _IOLBF, 0);` 或主動 `fflush(stdout);`
- **Linux/POSIX 環境**：
  - Wave 在 POSIX 環境預設開啟 `pty=True`（虛擬終端），可讓多數工具自動切換為行緩衝。若手動設為 `pty=False` 則會改用管道。

---

## 2. 子程序正常結束 (Exit 0)，但 Result 顯示為 FAIL 或 N/A

### 現象
終端機輸出 `Exit: 0`，但最終驗收結果 `Result` 不是綠色的 `PASS`。

### 根本原因
- **若 Result 顯示為 FAIL**：
  - 檢查規則中宣告了 `Expect.PRESENT`（例如必須出現 `PASS_TOKEN`），但子程序輸出中完全未匹配到該正則。
  - 或宣告了 `Expect.ABSENT`（例如禁止出現 `ERROR:`），但輸出中命中了該字串。
  - 核心原則：**程序的 exit code 0 僅代表程序正常終止，不代表業務邏輯驗收通過**。
- **若 Result 顯示為 N/A**：
  - 該工作未宣告任何 `job.check(...)` 規則。無檢查規則的工作正常結束時，Result 固定定案為 `N/A`。

---

## 3. `job.send()` 回傳成功，但外部工具沒有執行指令

### 現象
在 GUI 輸入列或透過 Python 呼叫 `job.send("status")`，介面顯示已傳輸，但子程序似乎無動於衷。

### 根本原因
- `send()` 回傳 `True` 僅代表文字已成功寫入作業系統提供給該子程序的 stdin 管道緩衝區。
- **寫入傳輸成功不等於外部應用程式已讀取、解析或執行該命令**。
- 常見原因包括：
  1. 外部工具預設需要換行符號才處理整行指令（`send(text, newline=True)` 預設會補 `\n`，但若外部工具需要 Windows 的 `\r\n` 則可能停滯）。
  2. 外部工具未以無緩衝方式讀取 stdin。
  3. 外部工具此時正處於繁忙狀態，尚未回到等待輸入的事件迴圈。

---

## 4. Web GUI 無法連線或提示埠號衝突

### 現象
啟動 `rpk-wave work.py` 時終端機報錯 `OSError: [Errno 98/10048] Address already in use`。

### 解決方案
- 預設 port 為 `0`（由系統隨機分配空閒 port）。若使用者明確指定了 `-p 8080` 但該 port 被其他程式佔用，請更換埠號或改為 `0`：
  ```bash
  rpk-wave work.py -p 0
  ```
- **遠端存取說明**：Wave GUI 伺服器僅綁定本機 loopback（`127.0.0.1` 或 `localhost`），不支援直接綁定 `0.0.0.0`。若需於遠端主機檢視，請透過 SSH port forwarding（例如 `ssh -L 8080:127.0.0.1:8080 user@remote`）安全轉發。

---

## 5. 早期的日誌不見了，或提示「Older logs evicted」

### 現象
長時間運行的工作在日誌面板上方顯示日誌已截斷，滾動不到最開始的輸出。

### 根本原因
這是 Wave 為了防止記憶體耗盡的有界日誌機制（`max_log_lines`，預設 10000 行）。當總行數超過上限時，最舊的行會被環形緩衝區淘汰。

### 處置與確認
- **不必擔心證據丟失**：所有在早期命中的 Check 證據與行號均已獨立封存，日誌淘汰絕不影響 Result 判定。
- 若需要完整輸出留檔，可在建構時指定 `Job(..., log_file="full.log")` 或 `JobManager(log_dir="logs/")` 將完整無截斷日誌寫入磁碟。
- 亦可在 GUI 的 **Settings -> Section B** 中調大 `max_log_lines`。

---

## 6. 在過往 Attempt 歷史中，所有控制按鈕與輸入列都變灰無效

### 現象
切換到 Attempt #1 檢視時，Stop、Rerun 與底部文字輸入框皆為禁用狀態。

### 根本原因
這是設計上的**歷史隔離契約**。過往已結束的 Attempt 是唯讀的不可變紀錄。所有停止、訊號發送與 stdin 輸入操作僅能作用於當前活躍的 Attempt，核心嚴格禁止跨輪次操作以防止競爭與誤殺。若需重新執行，請切換回當前輪次或在工作清單進行 Rerun。
