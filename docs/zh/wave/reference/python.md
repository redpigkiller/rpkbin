# rpkbin.wave Python API 參考

`rpkbin.wave`、`rpkbin.flow` 與 `rpkbin.jobcore` 匯出的 Python 類別、函式、工作控制、Flow 管線與本機 HTTP 端點參考。

## 語法

```python
from rpkbin.wave import (
    ArchivePolicy,
    FileSource,
    ReanalysisSource,
    RunArchive,
    SourceResult,
    TailSource,
    WaveServer,
    WaveWorkspace,
    build_demo_workspace,
    launch,
    load_work_file,
    main,
    prune_archives,
    run_headless,
)
from rpkbin.wave.columns import configure_columns
```

## 說明

`rpkbin.wave` 負責整合工作排程、即時記錄檔分析、波形與圖表投影，以及 SQLite 封存資料庫。工作檔建立 `WaveWorkspace` 後，註冊搭配 `Flow` 的 `Job` 或記錄資料來源，並可視需要設定 `ArchivePolicy` 或自訂欄位，再交由 `launch` 或 `run_headless` 執行。

---

## `WaveWorkspace`

管理 `JobManager`、各工作的 `Flow` 管線、背景記錄來源與封存設定的核心容器。

### 建立

```python
ws = WaveWorkspace(
    manager=None,
    *,
    columns=None,
    archive_dir=None,
    archive_mode=None,
    temp_dir=None,
    retention_days=None,
    pass_retention_days=None,
    fail_retention_days=None,
    stage_on_network_fs=True,
)
```

#### 輸入與具名引數

| 參數 | 預設值 | 說明 |
| --- | --- | --- |
| `manager` | `None` | 可選的既有 `JobManager`。未傳入時會自動建立 `JobManager()`。 |
| `columns` | `None` | 傳給 `configure_columns(manager, columns)` 的介面欄位定義。 |
| `archive_dir` | `None` | 目標 `.sqlite3` 封存目錄。傳入非 `None` 路徑時會立即呼叫 `enable_archive` 開啟封存。 |
| `archive_mode` | `None` | 保留模式（`"persist"`、`"keep_on_fail"`、`"ephemeral"`）。未指定時改讀 `RPK_WAVE_ARCHIVE_MODE`，預設 `"persist"`。 |
| `temp_dir` | `None` | `ephemeral`/`keep_on_fail` 與 NFS 暫存使用的本機目錄。未指定時改讀 `RPK_WAVE_TEMP_DIR` 或系統專屬暫存子目錄。 |
| `retention_days` | `None` | 已關閉封存檔的保留天數（`float >= 0`）。未指定時改讀 `RPK_WAVE_RETENTION_DAYS`。 |
| `pass_retention_days` | `None` | 已關閉 `PASS` 封存檔的保留天數，優先於 `retention_days`。未指定時改讀 `RPK_WAVE_PASS_RETENTION_DAYS`。 |
| `fail_retention_days` | `None` | 已關閉 `FAIL` 或未完成封存檔的保留天數，優先於 `retention_days`。未指定時改讀 `RPK_WAVE_FAIL_RETENTION_DAYS`。 |
| `stage_on_network_fs` | `True` | 為 `True` 時，POSIX 環境下的網路檔案系統（`nfs`、`cifs`、`smb3` 等）會先在本機 `temp_dir` 寫入 WAL，正常關閉時再原子搬移至目標目錄。 |

### 屬性

| 屬性 | 型別 | 說明 |
| --- | --- | --- |
| `started` | `bool` | 工作區是否已開始執行（預覽期間為 `False`）。 |
| `archive_catalog_dir` | `Path` | 解析後用來列出歷史封存的目標目錄。 |
| `archive_health` | `dict[str, Any]` | 封存狀態字典，包含 `enabled`、`error`、`mode`、`target_dir`、`temp_dir`、`staged`、`disposition`（`"saved"`、`"saved_on_failure"`、`"discarded_ephemeral"`、`"discarded_pass"`、`"discarded_unstarted"`、`"move_failed"` 或 `None`）、`pruned_count`；開啟封存時另含 `run_id`、`path`、`closed_cleanly`、`status` 與 `complete`。 |
| `watcher_error` | `str \| None` | 背景記錄來源監看執行緒發生的第一筆例外訊息。 |
| `jobs` | `tuple[Job, ...]` | 目前作用中的工作清單（轉委派自 `JobManager`）。 |
| `removed_jobs` | `tuple[Job, ...]` | 已軟移除、可由 `restore_job` 還原的工作清單（轉委派自 `JobManager`）。 |
| `paused`、`ready`、`max_workers`、`max_history`、`scheduling_mode` | 多種型別 | 轉委派自 `JobManager` 的佇列組態與狀態屬性。 |

### 物件函式

| 方法 | 說明 |
| --- | --- |
| `add(job, *, flow=None)` | 註冊單一 `Job` 與可選的 `Flow`。 |
| `add_many(jobs, *, flow=None)` | 批次註冊共用同一 `Flow` 的多項工作。 |
| `add_source(source, *, flow=None, name=None)` | 掛載 `FileSource`、`TailSource` 或 `ReanalysisSource`。 |
| `add_paths(paths, *, once=False, globs=("*.log",), recursive=False, flow=None, **source_options)` | 以檔案或目錄路徑便利掛載多個資料來源。 |
| `configure_archive(*, archive_dir=..., archive_mode=..., temp_dir=..., retention_days=..., pass_retention_days=..., fail_retention_days=..., stage_on_network_fs=..., override=False) -> ArchivePolicy` | 在 `start()` 前設定封存策略。傳入 `override=True` 可讓 CLI 或環境變數覆蓋工作檔預設值。 |
| `enable_archive(directory=None, *, source_path=None)` | 初始化 SQLite 封存寫入，並於啟動時執行過期清理。 |
| `flow_for(job)` | 傳回指定 `Job` 綁定的 `Flow` 或 `None`。 |
| `start()`、`resume()`、`validate()` | 啟動工作區執行、解除佇列暫停，或驗證工作組態。 |
| `snapshot(*, selected_job_id=None, attempt_id=None, include_logs=True)` | 產生包含佇列、工作、Flow 與封存健康狀態的可序列化快照。 |
| `stop_sources(job_id=None)` | 通知背景記錄來源執行緒停止。 |
| `close()` | 關閉資料來源、排空 `JobManager`、寫回並關閉 SQLite 封存，並執行保留處置（`keep_on_fail` / `ephemeral` / 暫存搬移）。 |
| `remove_job(job_id, *, expected_attempt=None, expected_submission=None)` | 將未啟動或已結束的工作移出作用中清單，並清除封存中尚未啟動的預覽嘗試記錄。 |
| `restore_job(job_id, *, expected_attempt=None, expected_submission=None)` | 從 `removed_jobs` 還原工作、重新掛回其 `Flow` 並重建預覽嘗試記錄。 |
| `control(...)`、`pause()`、`stop_all(...)`、`rerun(...)`、`set_priority(...)`、`set_held(...)`、`reorder_queue(...)`、`set_max_log_lines(...)`、`set_max_history(...)`、`update_settings(...)`、`explain_pending(...)`、`add_log_listener(...)`、`get_job(...)` | 轉委派給 `JobManager` 的佇列與工作控制方法。 |

---

## `WaveServer` 與 `launch`

提供靜態網頁介面與 REST/SSE 端點的本機 HTTP 伺服器。

### 語法

```python
srv = WaveServer(
    workspace=None,
    *,
    host="127.0.0.1",
    port=0,
    source_path=None,
    archive_path=None,
    archive_policy=None,
)

srv = launch(
    workspace=None,
    *,
    host="127.0.0.1",
    port=0,
    open_browser=False,
    block=True,
    source_path=None,
    archive_path=None,
    archive_policy=None,
)
```

### 屬性與物件函式

- `srv.port`：實際繫結的 TCP 連接埠（`int`）。
- `srv.url`：本機介面基礎網址 `http://<host>:<port>/`。
- `srv.start(*, open_browser=False) -> str`：在背景啟動 HTTP 伺服器並傳回 `url`。
- `srv.stop() -> None`：關閉 HTTP 伺服器並釋放工作區資源。
- `archive_policy`：可選字典，於開啟封存前套用至工作區。伺服器僅允許 loopback 位址（`127.0.0.1`、`localhost`、`::1`）。另可透過命令列 `rpk-wave --version`（`-V`）檢視套件版本（`rpk-wave 0.3.0`）。

---

## `ArchivePolicy` 與 `prune_archives`

描述 SQLite 封存生命週期規則的不可變資料類別，以及依據封存中繼資料清理過期檔案的工具函式。

### 語法

```python
policy = ArchivePolicy(
    mode="persist",
    archive_dir=None,
    temp_dir=None,
    retention_days=None,
    pass_retention_days=None,
    fail_retention_days=None,
    stage_on_network_fs=True,
)

summary = prune_archives(
    directory,
    *,
    pass_retention_days=None,
    fail_retention_days=None,
    retention_days=None,
    exclude_run_id=None,
    now=None,
)
```

### 物件函式與回傳結果

- `policy.resolved_archive_dir() -> Path`：依序取用明確傳入的 `archive_dir`、`RPK_WAVE_ARCHIVE_DIR` 與作業系統使用者狀態目錄。
- `policy.resolved_temp_dir() -> Path`：依序取用明確傳入的 `temp_dir`、`RPK_WAVE_TEMP_DIR` 與系統暫存下的使用者專屬目錄。
- `policy.effective_pass_retention_days() -> float | None`：優先回傳 `pass_retention_days`，否則回傳 `retention_days`。
- `policy.effective_fail_retention_days() -> float | None`：優先回傳 `fail_retention_days`，否則回傳 `retention_days`。
- `policy.with_overrides(**kwargs) -> ArchivePolicy`：回傳套用新欄位的副本；`mode` 會將連字號轉成底線（`"keep-on-fail"` 轉為 `"keep_on_fail"`）。
- `prune_archives(...)` 回傳 `{"deleted": list[str], "skipped": int, "errors": int}`。判斷過期時會讀取資料庫內部的 `metadata`（`closed_at`，缺漏時用 `created`）與 `run_outcome`（`PASS` 與 `FAIL`/未完成），不依賴檔案系統 `mtime`。系統會略過尚未關閉或受排除的封存與非 Wave 的 `.sqlite3` 檔案，並在刪除時一併移除 `-wal` 與 `-shm` 附屬檔案。

---

## `RunArchive`

讀寫原始記錄、Flow 發布資料、報表名稱、自動化歷程、圖表序列與 VCD 匯出的 SQLite 介面。

### 建立

```python
with RunArchive(path, *, create=False, run_id=None, source_path=None) as archive:
    attempts = archive.attempts()
```

### 屬性與物件函式

| 成員 | 簽章摘要 | 說明 |
| --- | --- | --- |
| `run_id`、`path`、`metadata`、`health` | 屬性 | 封存 UUID、檔案 `Path`、JSON 中繼資料字典與健康狀態字典。 |
| `catalog`（類別方法） | `catalog(directory, *, source_path=None, cursor=None, limit=50, include_all=False, max_seconds=None)` | 依最新優先順序探索目錄內的合法 Wave `.sqlite3` 封存，同時支援 `YYYYMMDD_HHMMSS_<label>_<run_id[:8]>.sqlite3` 與舊版 32 位元十六進位檔名。 |
| `attempts` | `attempts(job_id=None, *, before=None, limit=100)` | 分頁列出已儲存的嘗試記錄。 |
| `attempt` | `attempt(job_id, attempt_id, *, preview=False)` | 取得單一嘗試的完整中繼資料、計數器與摘要。 |
| `page` | `page(job_id, attempt_id, *, limit=100, cursor=None, direction="forward", start=None, end=None, around=None)` | 分頁讀取原始記錄列（長文字在清單中會依預覽長度截斷）。 |
| `reader` | `reader(job_id, attempt_id, *, limit=100, cursor=None, direction="forward", anchor=None, query="", regex=False, case_sensitive=False, whole_word=False, start=None, end=None)` | 支援正規表示式、大小寫、全字與經過時間區間搜尋原始記錄。 |
| `read_text` | `read_text(job_id, attempt_id, seq, *, offset=0, limit=65536)` | 依序號 `seq` 分塊讀取完整的原始記錄行全文。 |
| `flow_page`、`flow_text`、`flow_report_names` | `flow_page(..., stage=None, producer=None, kind=None, report_name=None, conditions=None, limit=100)` | 分頁查詢解析記錄、分析報表與完整內容。 |
| `flow_latest`、`flow_latest_text` | `flow_latest(job_id, attempt_id, *, stage, producer=None, report_name=None, mode="record", ...)` | 檢視最新一筆記錄或逐欄合併結果（`flow_latest_text` 預設 `mode="fields"`）。 |
| `automation_history` | `automation_history(job_id, attempt_id, *, producer=None, after=None, before=None, limit=100)` | 分頁讀取自動化觸發事件與動作回執。 |
| `chart_meta`、`chart_series`、`chart_window`、`chart_find` | 索引圖表與波形查詢 | 查詢宣告的 `ViewSpec` 圖表或自動圖表數值投影（`axis="time"`、`mode="raw"`/`"buckets"`）。 |
| `export_vcd` | `export_vcd(job_id, attempt_id, *, view_id, as_of_seq=None, series=None, axis="time")` | 將波形檢視匯出為 VCD 格式字串（`str`）。 |
| `replay`、`save_attempt`、`delete_unstarted_attempt`、`set_outcome`、`record_batch`、`bind_source`、`flush`、`close` | 生命週期與寫入工具 | 開啟重播上下文（`replay(attempt_id)`）、管理預覽列、寫入結果、強制同步與關閉（`clean=True`）。 |

---

## 資料來源與工作檔工具函式

| 符號 | 簽章 | 說明 |
| --- | --- | --- |
| `FileSource` | `FileSource(path, *, encoding="utf-8", decode_errors="replace", chunk_size=65536, automation=None)` | 單次讀取既有記錄檔，`.run(flow=None, *, archive_dir=None, stop_event=None)` 回傳 `SourceResult`。 |
| `TailSource` | `TailSource(path, *, start="beginning", poll_interval=0.1, idle_timeout=None, completion_file=None, end_marker=None, final_offset=None, rotation_grace=3.0, commit_interval=1.0, **options)` | 持續追蹤檔案更新，處理記錄輪替與可選完成條件。 |
| `ReanalysisSource` | `ReanalysisSource(archive, run_id, *, flow_path=None)` | 從既有封存檔重播保存的原始記錄到受信任 `.py` 檔案（`flow_path`）所匯出的 `flow`。 |
| `SourceResult` | `SourceResult(snapshot, archive_path, complete, reason, capabilities, archive_error=None)` | 獨立執行資料來源 `.run(...)` 的回傳物件。 |
| `load_work_file` | `load_work_file(target, *, reload_modules=False) -> WaveWorkspace` | 匯入受信任的 `.py` 工作檔並取得其中的 `workspace` 或 `manager`。 |
| `run_headless` | `run_headless(manager, *, timeout=None, source_mode=False) -> int` | 以終端機無頭模式執行 `WaveWorkspace` 直到結束或逾時，回傳結束代碼 `0` 或 `1`。 |
| `build_demo_workspace` | `build_demo_workspace() -> WaveWorkspace` | 建立 `--demo` 使用的有限示範工作區。 |
| `configure_columns` | `rpkbin.wave.columns.configure_columns(manager, columns)` | 在工作管理員中設定網頁介面顯示的自訂工作欄位。 |
| `main` | `main(argv=None) -> int` | `rpk-wave` 命令列的主入口函式。 |

---

## 工作定義與佇列控制（`rpkbin.jobcore`）

與互動提示、停止控制、佇列暫緩與訊號派送相關的 `Job` 與 `JobManager` 重點參數：

- **`Job(name, cmd, *, cwd=None, env=None, encoding="utf-8", decode_errors="replace", pty=None, prompts=(), pty_rows=None, pty_cols=None, resources=None, priority=0, timeout=None, max_retries=0, stop_policy=StopPolicy.GRACEFUL_THEN_FORCE, stop_ctrl_c=False, stop_cmd=None, stop_timeout=3.0, output_drain_timeout=30.0, max_log_lines=10000, log_file=None)`**
  - `prompts`：非空文字結尾字元組。即使尚未收到換行符號，只要去除 ANSI 控制碼並修剪尾端空白的輸出緩衝區結尾符合 `p.rstrip()`，系統便會立即送出該行，讓 `Automation` 或使用者回覆互動提示。
  - `pty`：POSIX 系統預設為 `True`（建立偽終端機），Windows 預設為 `False`（使用標準管線）。
- **`JobManager.control(job_id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None) -> ControlReceipt`**
  - `action` 支援 `"stop"`、`"force_stop"`、`"interrupt"`、`"signal"`（需傳入非負整數 `sig`）與 `"send"`（可搭配 `text`、`secret` 與 `newline=True`）。
- **佇列暫緩與軟移除**：
  - `set_held(job_id, held, *, expected_attempt=None, expected_submission=None)`：將待執行工作切換為 `HELD` 狀態（`explain_pending` 回報 `"Held (excluded from queue)"`），在不改變優先順序的前提下排除於排程派發外。
  - `reorder_queue(targets)`：依傳入順序重新指派優先級，回傳 `[(job_id, ControlReceipt, assigned_priority), ...]`。
  - `remove_job` / `restore_job` / `removed_jobs`：將已完成或未啟動的工作移出作用中清單（在 `WaveWorkspace` 中會一併刪除尚未啟動的預覽嘗試記錄），必要時可再還原並掛回原 `Flow`。

---

## Flow 管線與圖表檢視（`rpkbin.flow`）

- **`Flow(name, *, parsers=(), analyzers=(), automations=(), version="1", result=None, views=())`**
  - 統一註冊解析器（`Parser`）、分析器（`Analyzer`）、自動化（`Automation`）、結果判定（`result`）與圖表檢視定義（`views`）。
- **`Parser(name, parse, *, version="1", outputs=None, on_finish=None, state_factory=dict)`**
  - 每讀入一行執行 `parse(line, ctx)` 並以 `Result(...)` 發出解析字典；結束時呼叫 `on_finish(end_info, ctx)`。
- **`Analyzer(name, *, records=(), reports=(), version="1", outputs=None, on_finish=None, on_tick=None, every=None, state_factory=dict)`**
  - 透過 `Binding(source, handler)` 訂閱 `Parser` 記錄、`Analyzer` 報表、`Tick(elapsed)` 週期計時器（`every`）或 `EndInfo(complete, execution)` 結束勾點。
- **`Automation(name, *, reports=(), on_event=None, enabled=True, state_factory=dict)`**
  - 訂閱分析器報表或 `on_event` 事件（`FlowEvent`），可呼叫 `ctx.actions.send(...)`、`stop()`、`force_stop()`、`interrupt()` 或 `signal(...)`，回傳 `ActionOutcome` 收據。
- **`ViewSpec(id, kind, *, source, axis, series, title=None, options=None, report_name=None)`**
  - `source` 接受 `Parser`/`Analyzer` 物件或 `{"stage": ..., "producer": ...}` 字典（分析器可選填 `"report_name"`）；`series` 則為包含 `"mode": "wide"`（`"items": [...]`）或 `"mode": "long"`（`"id_field"`、`"value_field"`）的字典。類別建構函式包含 `ViewSpec.waveform(...)`、`ViewSpec.xy_line(...)`、`ViewSpec.scatter(...)`、`ViewSpec.histogram(...)` 以及 `.to_json()`。
- **`FlowSession`**：可在不啟動 `WaveWorkspace` 的情境下單獨執行 `Flow`，提供 `.start()`、`.feed(line)`、`.tick(elapsed)`、`.emit_event(name, data)`、`.wait_automation()`、`.finish()`、`.snapshot()` 與 `.close()`。

---

## 本機 HTTP API 路由

`WaveServer` 啟動後，網頁介面主要透過下列本機端點操作工作區：

- **`GET /api/files?mode=<mode>&path=<dir>`**：檔案瀏覽端點，支援四種模式 `"work"`（`.py`）、`"logs"`（非封存記錄檔）、`"archive"`（`.sqlite3`）與 `"rc"`（`.rc`、`.vcd`、`.json`），並提供 `Launch Dir`、`Work File Dir`、`Archives`、`Home` 目錄捷徑。
- **`GET /api/archive`**、**`POST /api/archive/open`**、**`POST /api/archive/delete`**：列出 `archive_catalog_dir` 中的近期封存、將封存以唯讀模式載入介面，或從磁碟永久刪除已關閉的封存及其附屬檔案。
- **`POST /api/jobs/hold`**、**`/api/jobs/reorder`**、**`/api/jobs/remove`**、**`/api/jobs/restore`**、**`/api/jobs/requeue`**：控制工作暫緩、佇列拖曳排序、軟移除、還原與重新排入。
- **`POST /api/job/action`**：發送互動控制動作，包含 `action="send"`（支援 `newline` 布林開關與 `secret`）與 `action="signal"`（接受數字或 POSIX 訊號名稱 `SIGINT`、`SIGTERM`、`SIGTSTP`、`SIGCONT`、`SIGUSR1`、`SIGUSR2`、`SIGHUP`、`SIGQUIT`）。
- **`POST /api/chart/file`**：於 Charts 面板載入本機 `.rc`、`.vcd` 或 `.json` 波形設定檔。

---

## 範例

### 1. 最小範例

建立帶有 `prompts` 設定的有限工作，指定暫存測試封存目錄以無頭模式執行，並讀取 `archive_health`：

```python
import sys
import tempfile
from pathlib import Path

from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace, run_headless

with tempfile.TemporaryDirectory() as tmp:
    archive_dir = Path(tmp) / "archives"
    ws = WaveWorkspace(archive_mode="persist")
    ws.enable_archive(archive_dir)
    ws.add(
        Job(
            "hello",
            [sys.executable, "-c", "print('boot ok')"],
            prompts=("Password:",),
        )
    )
    rc = run_headless(ws)
    health = dict(ws.archive_health)
    ws.close()
    print("rc:", rc, "disposition:", health["disposition"])
```

輸出（Wave 摘要表之後）：

```text
rc: 0 disposition: saved
```

### 2. 進階範例

定義包含 `ViewSpec.waveform` 的 `Flow`，在啟動前測試 `remove_job` 與 `restore_job`，執行後透過 `RunArchive` 查詢圖表點數並呼叫 `prune_archives`：

```python
import re
import sys
import tempfile
from pathlib import Path

from rpkbin.flow import Flow, Parser, ViewSpec
from rpkbin.jobcore import Job
from rpkbin.wave import RunArchive, WaveWorkspace, prune_archives, run_headless

LINE_RE = re.compile(r"t=(\d+)\s+val=(\d+)")

def parse_sig(line, ctx):
    m = LINE_RE.search(line)
    if m:
        return {"t": int(m.group(1)), "val": int(m.group(2))}

sig = Parser("sig", parse_sig)
wave_view = ViewSpec.waveform(
    "wave_main",
    source=sig,
    axis={"field": "t", "label": "Time", "unit": "ns"},
    series={"mode": "wide", "items": [{"id": "val", "field": "val", "type": "number", "label": "Value"}]},
    title="Value Trace",
)
flow = Flow("trace_flow", parsers=(sig,), views=(wave_view,))

with tempfile.TemporaryDirectory() as tmp:
    archive_dir = Path(tmp) / "archives"
    ws = WaveWorkspace(archive_mode="persist")
    ws.enable_archive(archive_dir)
    job = Job("sim", [sys.executable, "-c", "print('t=10 val=3\\nt=20 val=7')"])
    ws.add(job, flow=flow)

    ws.remove_job(job.name)
    assert len(ws.removed_jobs) == 1
    ws.restore_job(job.name)

    rc = run_headless(ws)
    db_path = Path(ws.archive_health["path"])
    run_id = ws.archive_health["run_id"]
    ws.close()

    with RunArchive(db_path) as ar:
        att = ar.attempts()["rows"][0]
        win = ar.chart_window(att["id"], att["attempt_id"], view_id="wave_main", series=["val"], lo=0, hi=50)
        print("rc:", rc, "points:", len(win["points"]))
        print("pruned:", prune_archives(archive_dir, retention_days=30, exclude_run_id=run_id))
```

輸出（Wave 摘要表之後）：

```text
rc: 0 points: 2
pruned: {'deleted': [], 'skipped': 1, 'errors': 0}
```

## 注意事項與限制

- `configure_archive` 必須在 `ws.start()` 前呼叫，執行開始後呼叫會拋出 `RuntimeError`。
- 在 Windows 上 `Job(..., pty=None)` 會走標準管線模式（`pty=False`）；POSIX 具名訊號（`SIGTSTP`、`SIGCONT`、`SIGUSR1`、`SIGUSR2`、`SIGHUP`、`SIGQUIT`）僅在 POSIX 主機上開放派送。
- 請務必正常呼叫 `ws.close()` 或以 `with RunArchive(...)` 上下文關閉資料庫，才會完成 `keep_on_fail` / `ephemeral` 保留處置與 NFS 暫存搬移。

## 另請參閱

- [Wave 總覽](../README.md)
- [rpk-wave 命令列參考](cli.md)
- [Flow 分析管線](../flow.md)
- [歷史與封存機制](../history.md)
