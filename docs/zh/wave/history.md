# 歷史與封存機制

說明 Wave 如何以可讀檔名儲存 SQLite 封存、在網路檔案系統上進行本機暫存分段、依期限清理舊檔，以及透過 Python 離線查詢歷史記錄。

## 封存檔名與存放位置

每次啟用封存的工作區都會以可依時間排序、便於辨識的檔名建立單一 SQLite 檔案：

```text
YYYYMMDD_HHMMSS_<label>_<run_id[:8]>.sqlite3
```

- `YYYYMMDD_HHMMSS`：封存建立時的本機時間戳記（`time.localtime`）。
- `<label>`：由 `source_path` 主檔名淨化後的標籤（僅保留 `[A-Za-z0-9_-]`，上限 40 字元；未指定工作檔時為 `"run"`）。
- `<run_id[:8]>`：32 碼 UUID `run_id` 的前 8 個十六進位字元。
- **相容舊版檔名**：`RunArchive.catalog` 仍可探索並開啟舊版的 32 碼十六進位檔名（`<32-hex>.sqlite3`）。

若未設定 `--archive-dir`（或環境變數 `RPK_WAVE_ARCHIVE_DIR`），Wave 會存入作業系統的本機使用者狀態目錄：

- **Windows**：`%LOCALAPPDATA%\rpk-wave\archives`
- **POSIX**：`$XDG_STATE_HOME/rpk-wave/archives`（未設定時為 `~/.local/state/rpk-wave/archives`）

## 保留模式與關閉處置

可透過 `--archive-mode`（`RPK_WAVE_ARCHIVE_MODE`）或 `WaveWorkspace(archive_mode=...)` 選擇保留策略：

| 模式 | 執行期間寫入位置 | 正常關閉後行為 | `archive_health["disposition"]` |
| --- | --- | --- | --- |
| `persist`（預設） | 目標 `archive_dir`（若需暫存分段則先寫在 `temp_dir`） | 一律保留（若無任何工作啟動則刪除，標記 `"discarded_unstarted"`） | `"saved"`、`"discarded_unstarted"`、`"move_failed"` |
| `keep_on_fail`（`keep-on-fail`） | `temp_dir` 暫存子目錄 | 失敗或未完成時搬移至 `archive_dir`；全部通過（`PASS`）時自動刪除 | `"saved_on_failure"`、`"discarded_pass"`、`"discarded_unstarted"`、`"move_failed"` |
| `ephemeral`（`--no-save`） | `temp_dir` 暫存子目錄 | 連同 `-wal`、`-shm` 一律清除 | `"discarded_ephemeral"` |

## 網路檔案系統暫存分段（`temp_dir`）

直接在共享 NFS 或 SMB/CIFS 掛載目錄操作啟用 WAL 的 SQLite 資料庫，容易產生鎖競爭甚至 `-wal` 損壞。在預設 `stage_on_network_fs=True` 下，Wave 會自動介入保護：

1. 在 POSIX 主機上檢查 `/proc/mounts`，辨識網路檔案系統類型（`nfs`、`nfs4`、`cifs`、`smb3`、`smbfs`、`fuse.sshfs`、`afs`、`ceph`、`glusterfs`）。
2. 若目標 `archive_dir` 位於網路檔案系統且 `temp_dir` 位於不同的本機磁碟，寫入會自動導向 `temp_dir` 下的使用者專屬暫存目錄（使用系統預設暫存目錄時權限設為 `0o700`）。
3. 正常呼叫 `WaveWorkspace.close()` 關閉時，Wave 先執行 WAL `TRUNCATE` 檢查點、將 `journal_mode` 切回 `DELETE`，再以 `copy2` 與 `os.replace` 將單一完整 `.sqlite3` 檔案原子搬移至 `archive_dir`。

## 依期限自動清理

設定 `--retention-days`、`--pass-retention-days` 與 `--fail-retention-days`（或對應的 `RPK_WAVE_*` 環境變數／`WaveWorkspace` 參數），工作區啟用封存時就會自動清理過期檔案，也可離線呼叫 `prune_archives(...)`：

- 系統直接讀取資料庫內部 `metadata` 表的 `closed_at`（缺漏時改讀 `created`）與 `run_outcome`（`PASS`、`FAIL` 或 `UNSTARTED`），不依賴檔案系統可能被搬移或複製改寫的 `mtime`。
- 針對結果個別設定的 `pass_retention_days` 與 `fail_retention_days`，會覆蓋通用的 `retention_days`。
- 執行中的封存（`exclude_run_id`）、尚未正常關閉的資料庫，以及非 Wave 格式的 `.sqlite3` 檔案都會安全略過。
- 刪除過期封存時，系統會同步清除對應的 `-wal` 與 `-shm` 附屬檔案。

## 預覽嘗試記錄與工作移除

處於預覽模式（尚未按下 **Start**）時，若透過介面或 `remove_job` 移除工作，Wave 會一併刪除尚未啟動的預覽嘗試記錄（`delete_unstarted_attempt`）；使用 `restore_job` 還原工作則會重新建立預覽嘗試列並掛回原本的 `Flow`。

## 範例

### 1. 最小範例

設定 `keep_on_fail` 模式與暫存目錄執行全部通過的工作，觀察正常結束後的 `archive_health` 與目標目錄狀態：

```python
import sys
import tempfile
from pathlib import Path

from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace, run_headless

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "archives"
    scratch = Path(tmp) / "scratch"
    ws = WaveWorkspace(archive_dir=target, archive_mode="keep_on_fail", temp_dir=scratch)
    ws.add(Job("ok_job", [sys.executable, "-c", "print('done')"]))
    rc = run_headless(ws)
    health = dict(ws.archive_health)
    ws.close()
    saved = list(target.glob("*.sqlite3"))
    print("rc:", rc, "disposition:", health["disposition"], "saved files:", len(saved))
```

輸出（Wave 摘要表之後）：

```text
rc: 0 disposition: discarded_pass saved files: 0
```

### 2. 進階範例

使用 `RunArchive.catalog` 列出封存清單、分頁讀取原始輸出、透過 `read_text` 取得全文，並呼叫 `prune_archives`：

```python
import sys
import tempfile
from pathlib import Path

from rpkbin.jobcore import Job
from rpkbin.wave import RunArchive, WaveWorkspace, prune_archives, run_headless

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "archives"
    ws = WaveWorkspace(archive_dir=target, archive_mode="persist")
    ws.add(Job("step", [sys.executable, "-c", "print('alpha\\nbeta\\ngamma')"]))
    run_headless(ws)
    ws.close()

    cat = RunArchive.catalog(target)
    entry = cat["rows"][0]
    with RunArchive(entry["path"]) as ar:
        att = ar.attempts()["rows"][0]
        page = ar.page(att["id"], att["attempt_id"], limit=10)
        seqs = [row["seq"] for row in page["rows"]]
        first_line = ar.read_text(att["id"], att["attempt_id"], seqs[0])
        print("archive:", Path(entry["path"]).name[:15], "lines:", len(seqs), "first:", first_line["text"])

    print("prune:", prune_archives(target, pass_retention_days=7))
```

輸出（Wave 摘要表之後）：

```text
archive: 20261126_232000 lines: 3 first: alpha
prune: {'deleted': [], 'skipped': 1, 'errors': 0}
```

（輸出中的日期時間前綴會隨實際執行時的本機時間而異。）

## 另請參閱

- [Wave 總覽](README.md)
- [系統架構](architecture.md)
- [rpk-wave 命令列參考](reference/cli.md)
- [Python API 參考](reference/python.md)
