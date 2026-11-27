# 疑難排解

彙整 Wave 在執行狀態、網頁介面操作、Flow 管線與封存機制中常見問題的症狀、成因與解決方式。

## 工作檔開啟後停在預覽狀態未執行

- **症狀**：執行 `rpk-wave work.py` 開啟瀏覽器後，所有工作都處於待命狀態且沒有輸出。
- **成因**：圖形介面預設進入預覽模式（`started == False`），讓使用者先確認指令、調整佇列或移除不需要的項目。
- **解決方式**：在頂端工具列點擊 **Start** 正式啟動，或在命令列改傳 `--headless` 立即執行批次任務。

## `--host` 位址在啟動時遭拒絕

- **症狀**：傳入 `--host 0.0.0.0` 或區域網路 IP 時無法啟動伺服器。
- **成因**：基於本機安全性考量，`WaveServer` 只允許繫結 loopback 位址（`127.0.0.1`、`localhost`、`::1`）。
- **解決方式**：維持本機 loopback 位址啟動，遠端操作請搭配 SSH 連接埠轉送（`ssh -L 8080:127.0.0.1:<port> ...`）。

## 互動提示未觸發 `Automation` 或延遲很久才出現

- **症狀**：子程式輸出 `Continue? [y/N]:` 等未換行提示字元後停住，但 Log 檢視器與 `Automation` 都還沒收到這一行。
- **成因**：預設逐行讀取必須等到 `\n` 才會送出；在 Windows 管線模式（`pty=False`）下，許多程式也會開啟區塊輸出緩衝。
- **解決方式**：在 `Job(...)` 加上 `prompts=("Continue? [y/N]:",)`，讓 Wave 比對去除 ANSI 控制碼後的緩衝尾端並立即送出該行。測試 Python 子程式時可加上 `-u` 或 `flush=True`；送出回覆時再依目標程式需求決定 `newline=True`（預設）或 `newline=False`。

## 按 `Ctrl+F` 開啟瀏覽器搜尋而非 Wave 記錄搜尋

- **症狀**：按下 `Ctrl+F` 時跳出瀏覽器右上角的網頁搜尋框，而非 Wave 內建的 Log 搜尋列。
- **成因**：為避免干擾其他面板文字搜尋，Wave 將 `Ctrl+F` 限定在版面為 `Split` 或 `Inspect`、目前分頁為 `Log`，且焦點或操作目標位於記錄檢視區域／搜尋列內時才攔截。
- **解決方式**：先點一下 Log 面板內部再按 `Ctrl+F`，或在檢視狀態按下 `/`，亦可直接點擊工具列的搜尋圖示。

## 波形顯示與預期不符

- **訊號過多或缺少預設訊號**：按 `g` 開啟 **Get Signals** 並點擊 **Defaults**，將清單重設為檢視定義的預設訊號（未設定且為 `long` 模式時為前 8 條），同時清除單列客製覆寫。
- **`Expand Bits [msb:0]` 反灰或無法展開**：位元展開僅支援寬度 `<= 64` 位元的向量訊號。
- **位元切片數量少於預期**：**Bit Select `[msb:lsb]`…** 每個訊號上限為顯示 **16** 個切片。
- **`Left`/`Right` 方向鍵與 `n`/`N` 移動行為不同**：`Left`/`Right` 會直接移動游標 A 至「目前選取訊號列」的上／下一個轉態，忽略 `By:` 條件；`n`/`N` 才會依據 **`By:`** 下拉選單（`Any Edge`、`Rising`、`Falling`、`= Bus Value`）跳轉。

## Flow 摘要顯示切換（`Latest: Record / Merged`）

- **症狀**：介面顯示的是 `Latest:` 分段切換鈕（**Record** 與 **Merged**）。
- **說明**：切換為 **Record** 僅檢視最新一筆發布的欄位；若不同時間點的解析記錄只更新部分欄位，請切換為 **Merged** 查看各欄位的最新累積值。

## 超長記錄行顯示截斷並出現 `Preview · read full`

- **症狀**：檢視 SQLite 封存記錄時，超過 1024 字元的輸出行在主清單顯示截斷預覽。
- **解決方式**：點擊該列旁的 **Preview · read full** 按鈕，即可在 `Raw Log` 視窗以每次 **65,536 位元組** 的區段瀏覽全文；在 Python 也可直接呼叫 `RunArchive.read_text(job_id, attempt_id, seq)`。

## 封存搬移失敗（`move_failed`）或清理略過檔案

- **`archive_health["disposition"] == "move_failed"`**：代表程式在本機 `temp_dir` 順利寫完，但在關閉搬移至目標 `archive_dir` 時受權限、磁碟空間或網路斷線影響失敗。可依 `archive_health["path"]` 從暫存目錄救回 `.sqlite3` 檔案。
- **`prune_archives` 回報有 `skipped` 檔案**：為確保安全，清理函式會略過目前作用中執行（`exclude_run_id`）、未正常關閉的資料庫，以及非 Wave 格式的 `.sqlite3` 檔案；到期判斷依資料庫內部中繼資料時間為準，而非檔案系統 `mtime`。

## 另請參閱

- [Wave 總覽](README.md)
- [網頁圖形介面](gui.md)
- [歷史與封存機制](history.md)
- [rpk-wave 命令列參考](reference/cli.md)
