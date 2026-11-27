# rpk-wave 命令列參考

啟動 Wave 圖形介面、執行無頭批次任務、追蹤即時記錄檔，以及重析既有封存資料庫的命令列入口。

## 語法

```bash
rpk-wave [-h] [-V] [--host HOST] [-p PORT] [--open | --no-browser]
         [--headless] [--timeout TIMEOUT] [-j MAX_WORKERS]
         [--demo | --archive ARCHIVE] [archive-options] [target]

rpk-wave logs TARGET [TARGET ...] [--flow FLOW.py] [--once] [--glob PATTERN]
              [-r] [--encoding ENCODING] [--decode-errors {replace,strict}]
              [--start {beginning,end}] [--poll-interval SEC]
              [--commit-interval SEC] [--rotation-grace SEC]
              [--idle-timeout SEC]
              [--completion-file PATH | --end-marker TEXT | --final-offset N]
              [--headless] [--timeout TIMEOUT] [--host HOST] [-p PORT]
              [--open | --no-browser] [archive-options]

rpk-wave reanalyze ARCHIVE --flow FLOW.py [--attempt ATTEMPT_ID]
                   [--headless] [--timeout TIMEOUT] [--host HOST] [-p PORT]
                   [--open | --no-browser] [archive-options]
```

## 說明

`rpk-wave` 可直接在終端機啟動 `rpkbin.wave`（與 `python -m rpkbin.wave` 相同）。未指定 `--headless` 時，各種模式預設都會以預覽模式開啟本機網頁介面：Python 工作檔只會載入並顯示工作清單，直到您在瀏覽器按下「Start」或「Resume」才會啟動子程序。加上 `--headless` 則會直接在終端機啟動佇列或資料來源、輸出摘要並回傳結束代碼。

兩個內建子命令負責不需撰寫完整工作檔的常見流程：

- `rpk-wave logs`：讀取既有記錄檔或持續追蹤更新，並可透過 `--flow` 套用 `Flow` 分析管線。
- `rpk-wave reanalyze`：將既有 SQLite 封存資料庫的原始記錄行重播到新的 `Flow` 定義，並寫入新的封存檔。

Python 工作檔與 `--flow` 檔案會直接執行 Python 程式碼，請只開啟您自己撰寫或已檢查過的檔案。傳入 `--archive ARCHIVE` 會以唯讀模式開啟既有 `.sqlite3` 封存檔，不會執行原本的工作檔。

## 範例

### 1. 最小範例

查詢已安裝的 CLI 版本、在瀏覽器開啟內建有限示範工作區，或以無頭模式執行工作檔：

```bash
rpk-wave --version
```

輸出：

```text
rpk-wave 0.3.0
```

```bash
rpk-wave --demo --open
rpk-wave work.py --headless
```

### 2. 進階範例

一次性掃描模擬記錄目錄並套用自訂 `Flow`，只在失敗時保留封存檔，同時設定成功與失敗記錄的自動清理天數：

```bash
rpk-wave logs ./sim_logs --once -r --glob "*.log" --flow parse_flow.py \
  --archive-mode keep-on-fail --pass-retention-days 1 --fail-retention-days 14 \
  --headless
```

修正解析器規則後，針對已封存的執行記錄重跑分析，不必重新執行原始模擬：

```bash
rpk-wave reanalyze 20261126_120000_work_ab12cd34.sqlite3 --flow fixed_flow.py --headless
```

## 選項

### 一般與伺服器選項

`-V`/`--version` 與 `--demo`/`--archive` 屬於頂層 `rpk-wave` 命令。網頁介面、逾時與封存選項同時適用於頂層命令以及 `logs`、`reanalyze` 子命令。

| 旗標 | 環境變數 | 預設值 | 說明 |
| --- | --- | --- | --- |
| `-h`, `--help` | `-` | `-` | 顯示說明訊息並結束。 |
| `-V`, `--version` | `-` | `-` | 顯示版本字串（`rpk-wave 0.3.0`）並結束（僅限頂層命令）。 |
| `target` | `-` | `None` | 位置參數，指定要以預覽模式載入的受信任 Python（`.py`）工作檔。 |
| `--host HOST` | `-` | `127.0.0.1` | 本機網頁介面繫結位址，非 loopback 位址會被拒絕。 |
| `-p PORT`, `--port PORT` | `-` | `0` | 網頁介面連接埠（`0` 代表自動選擇可用連接埠）。 |
| `--open` | `-` | `False` | 啟動後自動在預設瀏覽器開啟網頁介面。 |
| `--no-browser` | `-` | `True` | 不自動開啟瀏覽器視窗。 |
| `--headless` | `-` | `False` | 不開啟網頁伺服器，直接在終端機執行並於完成後結束。 |
| `--timeout TIMEOUT` | `-` | `None` | 無頭批次執行的秒數上限；收尾清理時間可能超出此值。 |
| `-j MAX_WORKERS`, `--max-workers MAX_WORKERS` | `-` | `None` | 在任何工作啟動前覆寫 `JobManager` 同時執行上限（僅限頂層命令）。 |
| `--demo` | `-` | `False` | 載入內建的有限示範工作區（僅限頂層命令）。 |
| `--archive ARCHIVE` | `-` | `None` | 以唯讀模式開啟既有 `.sqlite3` 封存檔，不載入原本的工作檔（僅限頂層命令）。 |

### 封存與保留選項

這七個選項控制 SQLite 封存資料庫的存放位置、保留條件、暫存目錄與啟動時的自動過期清理。

| 旗標 | 環境變數 | 預設值 | 說明 |
| --- | --- | --- | --- |
| `--archive-dir ARCHIVE_DIR` | `RPK_WAVE_ARCHIVE_DIR` | 本機使用者狀態目錄（Windows 為 `%LOCALAPPDATA%\rpk-wave\archives`，POSIX 為 `$XDG_STATE_HOME/rpk-wave/archives` 或 `~/.local/state/rpk-wave/archives`） | 儲存 `.sqlite3` 封存資料庫的目標目錄。 |
| `--archive-mode {persist,keep-on-fail,ephemeral}` | `RPK_WAVE_ARCHIVE_MODE` | `persist` | 保留策略：全部已啟動執行皆保留（`persist`）、僅保留失敗或未完成（`keep-on-fail`）、關閉時丟棄（`ephemeral`）。 |
| `--no-save` | 相當於 `RPK_WAVE_ARCHIVE_MODE=ephemeral` | `False` | `--archive-mode ephemeral` 的簡寫；不可與明確指定的 `persist` 或 `keep-on-fail` 並用。 |
| `--temp-dir TEMP_DIR` | `RPK_WAVE_TEMP_DIR` | 系統暫存目錄下的使用者專屬子目錄（`rpk-wave-uid<UID>` 或 `rpk-wave-<user>`） | 供 `ephemeral`、`keep-on-fail` 及 POSIX 網路檔案系統自動暫存使用的本機暫存目錄。 |
| `--retention-days RETENTION_DAYS` | `RPK_WAVE_RETENTION_DAYS` | `None` | 自動刪除 `archive_dir` 中超過指定天數（非負浮點數）的已關閉封存檔。 |
| `--pass-retention-days PASS_RETENTION_DAYS` | `RPK_WAVE_PASS_RETENTION_DAYS` | `None` | 自動刪除超過指定天數的已關閉 `PASS` 封存檔；優先覆蓋 `--retention-days`。 |
| `--fail-retention-days FAIL_RETENTION_DAYS` | `RPK_WAVE_FAIL_RETENTION_DAYS` | `None` | 自動刪除超過指定天數的已關閉 `FAIL` 或未完成封存檔；優先覆蓋 `--retention-days`。 |

### `logs` 子命令選項

| 旗標 | 環境變數 | 預設值 | 說明 |
| --- | --- | --- | --- |
| `TARGET` | `-` | 必填（1 個以上） | 一或多個要監控或載入的記錄檔或目錄。 |
| `--flow FLOW` | `-` | `None` | 匯出 `Flow` 物件 `flow` 的受信任 Python 檔案。 |
| `--once` | `-` | `False` | 透過 `FileSource` 讀取既有內容一次後結束。 |
| `--glob PATTERN` | `-` | `*.log` | 掃描目錄目標時套用的檔名樣式。 |
| `-r`, `--recursive` | `-` | `False` | 遞迴掃描子目錄。 |
| `--encoding ENCODING` | `-` | `utf-8` | 記錄檔文字解碼編碼。 |
| `--decode-errors {replace,strict}` | `-` | `replace` | 遇到無效位元組序列時的解碼錯誤處理方式。 |
| `--start {beginning,end}` | `-` | `beginning` | 追蹤即時檔案時的起始讀取位置（`TailSource`）。 |
| `--poll-interval POLL_INTERVAL` | `-` | `0.1` | 追蹤檔案更新的輪詢間隔（秒）。 |
| `--commit-interval COMMIT_INTERVAL` | `-` | `1.0` | 追蹤期間提交 SQLite 封存更新的間隔（秒）。 |
| `--rotation-grace ROTATION_GRACE` | `-` | `3.0` | 偵測記錄輪替後的寬限保留時間（秒）。 |
| `--idle-timeout IDLE_TIMEOUT` | `-` | `None` | 無新輸出超過此秒數後停止追蹤。 |
| `--completion-file COMPLETION_FILE` | `-` | `None` | 當指定完成標記檔案存在時停止追蹤（結束條件互斥）。 |
| `--end-marker END_MARKER` | `-` | `None` | 讀到符合標記文字的行時停止追蹤（結束條件互斥）。 |
| `--final-offset FINAL_OFFSET` | `-` | `None` | 當監控檔案達到指定位元組位移時停止追蹤（結束條件互斥）。 |

### `reanalyze` 子命令選項

| 旗標 | 環境變數 | 預設值 | 說明 |
| --- | --- | --- | --- |
| `archive` | `-` | 必填 | 要讀取原始嘗試資料的 Wave `.sqlite3` 封存檔路徑。 |
| `--flow FLOW` | `-` | 必填 | 匯出 `Flow` 物件 `flow` 的受信任 Python 檔案。 |
| `--attempt`, `--run ATTEMPT` | `-` | `None` | 只重播單一指定的嘗試 ID，否則預設處理每項工作最後一次已完成封裝的嘗試。 |

## 結束代碼與注意事項

| 代碼 | 意義 |
| --- | --- |
| `0` | 所有工作均未失敗並完成，或網頁介面伺服器正常結束。 |
| `1` | 無頭模式有工作失敗、遭強制中止或逾時、觸發 `--timeout`，或啟動驗證失敗。 |
| `2` | 命令列語法錯誤或旗標互相衝突（由 `argparse` 產生）。 |

- 明確傳入的 CLI 旗標優先於 `RPK_WAVE_*` 環境變數，未設定時才套用 `ArchivePolicy` 預設值。
- 進行臨時測試而不希望在封存目錄留下 `.sqlite3` 檔案時，可加上 `--no-save` 或 `--archive-mode ephemeral`。
- 在採用 NFS/CIFS 家目錄或專案目錄的 POSIX 叢集上，建議保留預設暫存分段機制（`--temp-dir` 指向 `/tmp` 等本機磁碟），避免 SQLite WAL 在網路檔案系統上受損或變慢。

## 另請參閱

- [Wave 總覽](../README.md)
- [Python API 參考](python.md)
- [歷史與封存機制](../history.md)
- [情境與工作流範例](../scenarios.md)
