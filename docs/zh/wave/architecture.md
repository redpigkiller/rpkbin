# 系統架構

說明 `JobManager`、`WaveWorkspace`、`FlowSession`、`RunArchive` 與 `WaveServer` 如何區隔子程序執行、記錄解析、SQLite 封存與網頁介面。

## 管線總覽

子程序或記錄檔來源輸出的每一行，都會經過單一且有順序的處理路徑：

```text
Process / Source -> Job / AttemptSink -> FlowSession -> RunArchive (SQLite)
                                                      -> Web State / SSE -> Browser UI
```

1. **程序與佇列層（`rpkbin.jobcore`）**：`JobManager` 掌管工作執行緒數量、資源分配、Backfill 排程、`HELD` 暫緩狀態、優先順序與子程序輸出讀取執行緒。
2. **工作區協調層（`WaveWorkspace`）**：替每個 `Job` 或資料來源（`FileSource`、`TailSource`、`ReanalysisSource`）綁定對應的 `Flow`，控制預覽與執行切換、保留軟移除的工作清單（`remove_job` / `restore_job`），並執行 `ArchivePolicy`。
3. **串流分析層（`rpkbin.flow`）**：`FlowSession` 逐行呼叫 `Parser`，依訂閱關係觸發 `Analyzer` 與定時 `Tick` 事件，並把 `Automation` 動作放進獨立背景執行緒，避免控制操作反過來卡住輸出讀取。
4. **持久化層（`RunArchive`）**：將原始記錄行、Flow 解析資料、分析報表、自動化歷程與 `ViewSpec` 索引圖表序列寫入 SQLite。
5. **本機 HTTP 伺服器（`WaveServer`）**：提供靜態網頁前端、REST JSON API 與 Server-Sent Events（SSE）即時更新，讓介面繪圖不與子程序 I/O 互鎖。

## 預覽模式與正式執行週期

未加上 `--headless` 啟動時，`WaveWorkspace` 會先以預覽模式載入工作區（`started == False`）：

- 此時不會啟動任何子程序或背景檔案監看執行緒。
- 系統會在介面與資料庫預先建立預覽嘗試項目，方便您在按下 **Start** 前先檢查指令、調整佇列順序、暫緩（Hold）或軟移除／還原工作。
- 若未啟動任何工作就直接結束，`WaveWorkspace.close()` 會將該空白封存標記為 `"discarded_unstarted"` 並刪除，避免空白檔累積在封存目錄中。

## 執行緒與鎖定隔離

- **避免跨越 I/O 鎖死**：輸出讀取執行緒逐塊解碼 UTF-8、在未換行緩衝尾端偵測 `prompts` 提示後立即送出該行，同時寫入上限受控的記憶體環狀緩衝區（`max_log_lines`）與封存寫入佇列。
- **獨立自動化執行緒**：`Automation` 回呼在專屬工作執行緒執行，執行結果以 `ActionOutcome` 非同步寫回分析串流與資料庫。
- **批次 SQLite 提交**：串流期間採用 WAL 模式並定期提交（資料來源依 `commit_interval`；關閉時執行完整收尾）。

## 封存暫存與儲存分層

為了避免多人共享的網路檔案系統發生 SQLite WAL 鎖損壞，同時保留集中式封存目錄的便利性，Wave 設計了兩段式寫入機制：

- 在 POSIX 主機上，`WaveWorkspace` 會檢查 `/proc/mounts` 判斷目標目錄是否位於網路檔案系統（`nfs`、`nfs4`、`cifs`、`smb3`、`smbfs`、`fuse.sshfs`、`afs`、`ceph`、`glusterfs`）。
- 當目標目錄為網路檔案系統（且 `stage_on_network_fs=True`），或目前處於 `keep_on_fail` / `ephemeral` 模式時，執行期間的 WAL 檔案會寫在 `temp_dir` 下的本機使用者專屬暫存目錄（使用預設系統暫存目錄時權限為 `0o700`）。
- 正常呼叫 `close()` 關閉時，Wave 會先對 WAL 執行 `TRUNCATE` 檢查點並切回 `journal_mode=DELETE`，再依運行結果（`PASS` / `FAIL`）決定保留或丟棄；需要保留時才透過 `copy2` 與 `os.replace` 原子搬移至 `archive_dir`。

## 本機安全邊界

- **受信任的工作檔**：載入 `.py` 工作檔會在同一程序內執行 Python 程式碼。若需唯讀檢視來源不明的記錄檔，請使用 `--archive` 直接開啟 `.sqlite3` 檔案。
- **僅限本機迴路位址**：`WaveServer` 會拒絕任何非 loopback 的 `--host` 位址。
- **受限檔案瀏覽（`/api/files`）**：查詢必須指定 `mode`（`"work"`、`"logs"`、`"archive"` 或 `"rc"`），只允許列舉對應副檔名（`.py`、非封存記錄檔、`.sqlite3` 或 `.rc`/`.vcd`/`.json`）。

## 另請參閱

- [Wave 總覽](README.md)
- [Flow 分析管線](flow.md)
- [歷史與封存機制](history.md)
- [Python API 參考](reference/python.md)
