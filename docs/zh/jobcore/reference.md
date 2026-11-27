# `rpkbin.jobcore` API 參考手冊

[English](../../en/jobcore/reference.md) · [文件導覽](../README.md) · [快速入門](README.md) · [實戰使用情境](scenarios.md)

本文件提供 `rpkbin.jobcore` 的技術規格，涵蓋 `JobManager`、`Job`、`JobStatus`、`ProcessVerdict`、`StopPolicy`、`ControlReceipt`、`Report`、`Capabilities` 與 `ProcessHandle`。

```python
from rpkbin.jobcore import (
    Capabilities,
    ControlReceipt,
    Job,
    JobManager,
    JobStatus,
    ProcessHandle,
    ProcessVerdict,
    Report,
    StopPolicy,
)
```

`__all__` 匯出上述九個符號。本套件只使用 Python 標準函式庫，需要 Python 3.11 或更新版本。事件契約類別（`AttemptReceiver`、`PROCESS_CAPABILITIES`、`Started`、`Line`、`SourceError`、`Finished`、`StatusChanged`、`ClockTick`、`SourceEvent`、`receiver_callback`）位於 `rpkbin.jobcore.events`；`strip_ansi` 位於 `rpkbin.jobcore.output`。

## `JobManager`

固定配額的命令排程器，負責工作註冊、原子性驗證、優先權派工、佇列編輯、attempt 歷史保留與資源關閉清理。

### 建立方式

```python
manager = JobManager(
    *,
    max_workers=4,
    resources=None,
    log_dir=None,
    max_history=20,
    scheduling_mode="backfill",
)
with JobManager(...) as manager: ...
```

所有建構子引數皆為 keyword-only。搭配 `with` 陳述式使用時，`__enter__` 會回傳 `self`（若管理器正在關閉或已關閉則引發 `RuntimeError`），離開時由 `__exit__` 呼叫 `close()` 且不抑制例外（回傳 `False`）。

#### 具名引數

| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `max_workers` | `int` | `4` | 最大並行 worker 執行緒數（`>= 1`；傳入 `bool` 會引發 `ValueError`）。 |
| `resources` | `dict[str, int] \| None` | `None` | 具名資源容量表（`None` 等同於 `{}`）。鍵必須為非空字串；值必須為 `>= 0` 的整數（拒絕 `bool`）。傳入非 `dict` 會引發 `TypeError`。 |
| `log_dir` | `str \| PathLike \| None` | `None` | 當工作未指定 `log_file` 時，輸出 `<job.id>.log` 的資料夾。建構時即以 `.absolute()` 解析，不受後續 `os.chdir()` 影響。空字串會引發 `ValueError`。 |
| `max_history` | `int` | `20` | 每項工作在 `Job.history` 中最多保留的已完成歷史 attempt 數（`>= 1`；拒絕 `bool`）。 |
| `scheduling_mode` | `str` | `"backfill"` | 優先權排程模式：`"backfill"`、`"strict"` 或 `"tier_strict"`。非字串引發 `TypeError`，無效字串引發 `ValueError`。 |

### 屬性

| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `max_workers` | `int` | 目前設定的 worker 並行上限。 |
| `resources` | `dict[str, int]` | 目前設定的資源容量字典。 |
| `log_dir` | `Path \| None` | 絕對路徑的 log 目錄，或 `None`。 |
| `max_history` | `int` | 每項工作保留的歷史 attempt 上限。 |
| `scheduling_mode` | `str` | 目前啟用的優先權排程模式（`"backfill"`、`"strict"` 或 `"tier_strict"`）。 |
| `jobs` | `list[Job]` | 依註冊順序排列的已註冊工作清單。 |
| `removed_jobs` | `tuple[Job, ...]` | 唯讀屬性，回傳透過 `remove_job()` 移除、可由 `restore_job()` 還原的工作 tuple。 |
| `started` | `bool` | 呼叫 `start()` 啟動排程器執行緒後為 `True`。 |
| `ready` | `bool` | 在非空工作清單上通過 `validate()` 或執行 `start()` 後為 `True`。移除最後一筆預覽工作會將 `ready` 重設為 `False`。 |
| `paused` | `bool` | 初始為 `True`；呼叫 `start()` 或 `resume()` 開始派工後為 `False`。 |

### 物件函式

| 方法 | 用途 |
| :--- | :--- |
| `add(job, *, receiver_factory=None)` | 註冊 `Job` 並可指定自訂 receiver 工廠；若已 `started`，會立刻驗證並凍結定義。回傳該 `Job`。 |
| `add_many(jobs)` | 依序對 `jobs` 呼叫 `add()`，回傳 `list[Job]`。 |
| `set_receiver_factory(factory)` | 在 `start()` 之前安裝一次 receiver 工廠函式 `factory(job, attempt)`。 |
| `get_job(job_id_or_name)` | 以 `Job` 物件、`job.id` 或唯一 `job.name` 查詢已註冊工作。找不到回傳 `None`；名稱重複時引發 `ValueError`。 |
| `validate()` | 檢查 `jobs` 非空、驗證所有工作資源需求並凍結定義（不啟動 worker），最後將 `ready` 設為 `True`。 |
| `start()` | 若尚未啟動則先執行 `validate()`，啟動背景排程執行緒（`Wave-scheduler`）並設為 `started = True`、`paused = False`（若已啟動則解除暫停）。 |
| `pause()` | 將 `paused` 設為 `True`，讓排隊工作暫停派發，執行中的程序繼續執行。 |
| `resume()` | 恢復派工（呼叫 `start()`）。 |
| `stop_all(*, force=False, source="user", reason=None, include_held=True)` | 暫停派工，先取消排隊中的工作（`include_held=False` 時略過已暫緩的工作），再依 ID 順序停止執行中的工作。回傳受理停止的數量。 |
| `control(job_id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None)` | 執行受身分檢查保護的控制動作；回傳 `ControlReceipt`。 |
| `set_priority(job_id, priority, *, expected_attempt=None, expected_submission=None)` | 更新排隊中工作的整數優先權；回傳 `ControlReceipt`。 |
| `set_held(job_id, held, *, expected_attempt=None, expected_submission=None)` | 暫緩（`held=True`）或解除暫緩（`held=False`）排隊工作；`held` 必須為 `bool`，回傳 `ControlReceipt`。 |
| `reorder_queue(targets)` | 以原子方式為合格的排隊目標重設嚴格遞減優先權；回傳 `list[tuple[str, ControlReceipt, int \| None]]`。 |
| `remove_job(job_id, *, expected_attempt=None, expected_submission=None)` | 將工作移至 `removed_jobs`（僅限 `start()` 前的預覽狀態，或已 `paused=True` 且完全閒置時）。回傳 `ControlReceipt`。 |
| `restore_job(job_id, *, expected_attempt=None, expected_submission=None)` | 依 `Job`、`job.id` 或唯一 `job.name` 將移除的工作還原至 `jobs` 原本的註冊位置與佇列序位。回傳 `ControlReceipt`。 |
| `rerun(job_id, *, expected_attempt=None, expected_submission=None)` | 當 `can_rerun` 為 `True` 時排入新的 submission（更換 `submission_id`、`attempt_number` 遞增、`retry_index` 重設為 `0`）。回傳 `ControlReceipt`。 |
| `set_max_log_lines(max_log_lines, job_id=None, *, expected_attempt=None, expected_submission=None)` | 調整單一或全部工作的保留行數上限，回傳被逐出的總行數。 |
| `set_max_history(max_history)` | 更新 `max_history`、修剪所有工作的舊歷史並回傳逐出的歷史筆數。 |
| `update_settings(*, max_workers=None, resources=None, max_history=None, scheduling_mode=None)` | 原子更新管理器設定且不搶占執行中工作；回傳 `{"evicted_history": int}`。 |
| `explain_pending(job)` | 若工作為 `PENDING` 回傳等候原因文字，否則回傳 `job.status.value`。 |
| `add_log_listener(callback)` | 註冊 `callback(record_dict)` 監聽即時 log 紀錄（包含 `line_no`、`text`、`source`、`time`、`job_id`、`job_name`、`attempt_id`、`attempt_number`）。 |
| `snapshot(*, selected_job_id=None, attempt_id=None, include_logs=True)` | 擷取管理器狀態、設定、`jobs`、`removed_jobs` 與選填的 `detail` 字典。 |
| `close()` | 暫停派工、停止全部工作（先優雅停止，逾時 `max(stop_timeout) + 5` 秒後強制結束）、排空最多 5 秒、關閉接收器並等待排程執行緒結束。可重複呼叫。 |
| `repr(manager)` | 回傳 `JobManager(jobs=N, active=A/W, started=..., paused=..., closed=...)`。 |

#### `control` 動作與引數

`control(job_id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None)` 必須同時傳入 `expected_attempt` 與 `expected_submission`，避免過期的介面或自動化請求誤觸重試或重跑後的新 attempt。

| `action` | 對應方法 | 使用的額外引數 | 拒絕條件與說明 |
| :--- | :--- | :--- | :--- |
| `"stop"` | `job.stop(...)` | 無 | 若為 `PENDING` 直接取消，否則啟動設定的停止流程。 |
| `"force_stop"` | `job.kill(...)` | 無 | 立即強制終止整棵程序樹（`stop(force=True)`）。 |
| `"interrupt"` | `job.send_ctrl_c(...)` | 無 | POSIX 送出 PTY `\x03` 或 `SIGINT`，Windows 送出 `CTRL_BREAK_EVENT`。 |
| `"signal"` | `job.send_signal(sig, ...)` | `sig`（必填整數 `>= 0`） | 若 `sig is None` 會回傳 `ControlReceipt("rejected", "Signal is required")`。 |
| `"send"` | `job.send(text, ...)` | `text`、`newline=True`、`secret=False` | 除非 `newline=False` 或 `text` 結尾已有 `"\n"`，否則會自動補上換行。 |
| 其他值 | 無 | 無 | 回傳 `ControlReceipt("rejected", "Unknown control action")`。 |

#### 排程模式與 `pending_code`

透過 `update_settings()` 調整容量時，不會中斷已經在執行的 worker。`resources` 不得移除任何已註冊工作仍需求的資源鍵；若要暫時停發該類工作，請將該資源容量設為 `0`。

- `"backfill"`：高優先權排隊工作優先派發，但若隊首因資源或條件受阻，所需 worker 與資源足夠的較低優先權工作仍可插隊執行。
- `"strict"`：嚴格依佇列順序（`(-priority, enqueue_seq)`）派工，只要前方合格工作因 worker 或資源受阻，後方所有工作一併等待。
- `"tier_strict"`：同一 `priority` 層級內若某項工作因資源受阻，同層其他資源足夠的工作仍可派發，較低 `priority` 層級則一律受阻（遇到 worker 滿載時立即停止掃描）。
- 使用 `set_held(job, True)` 暫緩工作會將其移出派工佇列（`pending_code == "HELD"`），即使在 `"strict"` 或 `"tier_strict"` 模式下也不會阻擋後方工作。呼叫 `stop_all(include_held=False)` 也會保留處於暫緩中的排隊工作。

`manager.snapshot()` 會為目前處於 `PENDING` 的工作填入 `pending_code` 與 `pending_reason`（執行中、已結束或歷史檢視則為 `None`）：

| `pending_code` | 意義 |
| :--- | :--- |
| `"HELD"` | 已透過 `set_held(job, True)` 暫緩並排除於派工佇列外。 |
| `"NOT_STARTED"` | 管理器尚未啟動且尚未執行 `validate()`。 |
| `"READY_TO_START_PENDING_BEGIN"` | 預覽階段已通過驗證（`ready=True`），等待 `start()`。 |
| `"PAUSED"` | 管理器已啟動且處於 `paused=True`。 |
| `"DISPATCHING"` | 排程器已保留名額，正在啟動 worker 執行緒。 |
| `"WAIT_WORKER"` | `active_workers >= max_workers`，或正在等待前方較高優先權工作釋出順序。 |
| `"WAIT_RESOURCE"` | 至少一項所需資源不足，或正被前方等待資源的工作阻擋。 |
| `"READY"` | 條件皆已滿足，將在下一輪排程派發。 |

#### 佇列重排、移除與還原

- `reorder_queue(targets)` 接受非空 list 或 tuple，元素可為 `job_id` 字串、`Job` 物件或 `(target, expected_attempt, expected_submission)` tuple。目標重複會引發 `ValueError`。合格的排隊工作會獲得嚴格遞減的整數優先權並盡量維持現有間距；執行中、已結束或身分過期的項目會在回傳的 `[(jid, receipt, assigned_priority), ...]` 清單中收到 `"rejected"` 回執與 `None` 優先權。
- `remove_job()` 與 `restore_job()` 僅允許在 `start()` 前的預覽階段，或已 `paused=True` 且完全閒置（`active_workers == 0`、無執行中的 worker 執行緒、無保留中或執行中的工作）時執行。`restore_job()` 會依目前的 `resources` 重新驗證資源需求、在已 `started` 時凍結工作定義，並還原原本在 `manager.jobs` 的註冊順序與佇列排序序位。

## `Job`

將不可變的命令定義與每次嘗試的執行狀態、log 緩衝及歷史紀錄結合的物件。

### 建立方式

```python
job = Job(
    name,
    cmd,
    *,
    cwd=None,
    env=None,
    encoding="utf-8",
    decode_errors="replace",
    pty=None,
    prompts=(),
    pty_rows=None,
    pty_cols=None,
    resources=None,
    priority=0,
    timeout=None,
    max_retries=0,
    stop_policy=StopPolicy.GRACEFUL_THEN_FORCE,
    stop_ctrl_c=False,
    stop_cmd=None,
    stop_timeout=3.0,
    output_drain_timeout=30.0,
    max_log_lines=10000,
    log_file=None,
)
```

#### 輸入引數

| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `name` | `str` | 必填 | 非空顯示名稱（會去除前後空白）。 |
| `cmd` | `str \| PathLike \| Sequence[str \| PathLike]` | 必填 | 命令字串（經平台 shell 執行）、單一 `PathLike`，或非空引數序列（拒絕 `bytes`／`bytearray` 與空字串）。 |

#### 具名引數

| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `cwd` | `str \| PathLike \| None` | `None` | 工作目錄（`Path(cwd).resolve()`）。`None` 會在凍結時擷取 `os.getcwd()`。空字串會引發 `ValueError`。 |
| `env` | `dict[str, str] \| None` | `None` | 覆寫環境變數並合併至 `os.environ`。鍵與值都必須是 `str`。 |
| `encoding` | `str` | `"utf-8"` | 經過驗證的文字編碼，用於輸出解碼與 `send()` 輸入編碼。 |
| `decode_errors` | `str` | `"replace"` | `"replace"` 會替換損毀位元組、累加 `output_decode_count` 並記錄警告；`"strict"` 會記錄 `output_decode` 錯誤並停止監控轉送（`monitor=False`），同時保留原始 log 收集。 |
| `pty` | `bool \| None` | `None` | `None` 在 POSIX 預設為 `True`，在 Windows 預設為 `False`。在 Windows 設為 `pty=True` 會引發 `ValueError`。 |
| `prompts` | `Sequence[str]` | `()` | 非空字面提示字串序列（純空白字串會引發 `ValueError`）。 |
| `pty_rows` | `int \| None` | `None` | 初始 PTY 列數（`1..65535`）；必須搭配 `pty=True`。 |
| `pty_cols` | `int \| None` | `None` | 初始 PTY 欄數（`1..65535`）；必須搭配 `pty=True`。 |
| `resources` | `dict[str, int] \| None` | `None` | 所需資源數量（每項資源須為 `>= 1` 的整數）。 |
| `priority` | `int` | `0` | 初始排程優先權（數值越大越先執行），拒絕 `bool`。 |
| `timeout` | `int \| float \| None` | `None` | 有限正數逾時秒數；到期會觸發 `stop(source="timeout", ...)` 並強制判決為 `ProcessVerdict.FAIL`。 |
| `max_retries` | `int` | `0` | 非預期非零結束碼或啟動失敗時的自動重試次數上限（`>= 0`）。`stop()`、`timeout` 或已受理的中斷訊號會抑制自動重試。 |
| `stop_policy` | `StopPolicy` | `StopPolicy.GRACEFUL_THEN_FORCE` | `GRACEFUL_THEN_FORCE` 或 `FORCE`。 |
| `stop_ctrl_c` | `bool` | `False` | 優雅停止開始時先送出 `interrupt()`。 |
| `stop_cmd` | `str \| None` | `None` | 優雅停止時改為寫入標準輸入的指令文字（若結尾無 `"\n"` 會自動補上），取代 `terminate()`。 |
| `stop_timeout` | `int \| float` | `3.0` | 送出優雅停止訊號或命令後，升級為 `kill()` 前的等待秒數。 |
| `output_drain_timeout` | `int \| float` | `30.0` | 程序退出後排空輸出的硬上限秒數（另設 3.0 秒逐行無進度閒置上限）。 |
| `max_log_lines` | `int` | `10000` | 單次 attempt 記憶體環狀緩衝區的行數上限（`>= 1`）。 |
| `log_file` | `str \| PathLike \| None` | `None` | 指定 log 輸出檔案路徑（建構時以 `.absolute()` 解析；優先於 `JobManager.log_dir`）。 |

### 屬性

| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `id` | `str` | 不可變工作識別碼（`"job_" + uuid4.hex`）。 |
| `name` | `str` | 工作名稱（驗證後凍結）。 |
| `cmd`、`cmd_str` | `str \| tuple[str, ...]`、`str` | 正規化後的命令與 shell 拼接字串。 |
| `cwd`、`env`、`encoding`、`decode_errors`、`pty`、`prompts`、`pty_rows`、`pty_cols`、`resources`、`timeout`、`max_retries`、`stop_policy`、`stop_ctrl_c`、`stop_cmd`、`stop_timeout`、`output_drain_timeout`、`max_log_lines`、`log_file` | 多種 | 定義設定（驗證後凍結；`env` 與 `resources` 會轉為 `MappingProxyType`）。 |
| `priority` | `int` | 目前 attempt 優先權（排隊時可透過 `JobManager` 調整）。 |
| `status` | `JobStatus` | 目前 attempt 狀態（`PENDING`、`RUNNING`、`WAITING_INPUT`、`STOPPING`、`DONE`、`FAILED`、`CANCELLED`）。 |
| `returncode` | `int \| None` | 程序結束碼、啟動失敗時為 `-1`，尚未結束或已取消則為 `None`。 |
| `pid` | `int \| None` | 子程序 PID；啟動前為 `None`。 |
| `elapsed`、`elapsed_str` | `float`、`str` | 經過秒數與 `"MM:SS"` 或 `"HH:MM:SS"` 格式字串。 |
| `data` | `MappingProxyType` | 目前 attempt 的凍結 receiver 投影字典。 |
| `result` | `Report` | 目前 attempt 的不可變 `Report(verdict, execution, warnings, errors, termination)`。 |
| `attempt_id`、`submission_id`、`attempt_number` | `str`、`str`、`int` | 目前 attempt UUID hex、submission UUID hex 與 1-based 嘗試次數。 |
| `history` | `tuple[MappingProxyType, ...]` | 先前 attempt 的凍結快照 tuple，上限受 `JobManager.max_history` 控制。 |

### 物件函式

| 方法 | 用途 |
| :--- | :--- |
| `send(text, *, newline=True, secret=False, expected_attempt=None)` | 編碼 `text`（當 `newline=True` 且結尾無換行時補 `"\n"`），在不持有 `_events_lock` 的情況下寫入標準輸入或 PTY，將 `WAITING_INPUT` 轉回 `RUNNING`，並在 `secret=True` 時以 `"***"` 遮罩紀錄。回傳 `ControlReceipt`。 |
| `send_signal(sig, *, source="user", expected_attempt=None)` | 送出整數訊號 `sig >= 0`（Windows 僅支援 `CTRL_BREAK_EVENT`）。除非為 `SIGUSR1`、`SIGUSR2`、`SIGTSTP` 或 `SIGCONT`，否則會抑制自動重試。回傳 `ControlReceipt`。 |
| `send_ctrl_c(*, source="user", expected_attempt=None)` | 送出平台中斷（POSIX 為 `\x03`／`SIGINT`，Windows 為 `CTRL_BREAK_EVENT`）並標記 `interrupt_requested`。回傳 `ControlReceipt`。 |
| `stop(*, source="user", reason=None, expected_attempt=None, force=False)` | 立即取消排隊中的工作，或對執行中程序啟動優雅／強制停止。回傳 `ControlReceipt`。 |
| `kill(*, expected_attempt=None, source="user", reason=None)` | `stop(force=True, ...)` 的簡寫。回傳 `ControlReceipt`。 |
| `logs(tail=None)` | 回傳保留的 log 文字行（`list[str]`）；`tail=0` 回傳 `[]`，負數、布林或非整數會引發 `ValueError`。 |
| `set_max_log_lines(max_log_lines, *, expected_attempt=None, expected_submission=None)` | 更新此工作的環狀緩衝行數上限，並回傳被逐出的行數。 |
| `log(message)` | 在目前 attempt 寫入一筆管理 log（`source="wave"`）。 |
| `to_snapshot_dict(*, include_logs=False, attempt_id=None, include_details=False)` | 回傳目前或指定保留歷史 attempt 的獨立複製字典快照。 |
| `repr(job)` | 回傳 `Job(id='...', name='...', status='...')`。 |

#### `prompts` 與 `WAITING_INPUT` 行為

- 輸出解碼器會在移除 ANSI 跳脫序列（`strip_ansi`）後，檢查尚未換行的緩衝區尾端 4,096 個字元（`_PROMPT_TAIL_CHARS`）。只要 `tail.rstrip()` 以任一已設定提示字串的 `p.rstrip()` 結尾，即使尚未收到 `\n`，該段文字也會立即作為一行送出。
- 當受監控（`monitor=True`）的文字行吻合提示字串時，`job.status` 會切換為 `JobStatus.WAITING_INPUT`。
- 成功呼叫 `job.send(...)` 或後續收到非純空白的受監控輸出行時，`job.status` 會切回 `JobStatus.RUNNING`；緊接在未換行提示後面的延遲空換行（`""`）不會清除 `WAITING_INPUT`。

## 值型別與列舉

### `JobStatus`

`str` 列舉，包含 `"PENDING"`、`"RUNNING"`、`"WAITING_INPUT"`、`"STOPPING"`、`"DONE"`、`"FAILED"` 與 `"CANCELLED"`。

| 屬性 | 型別 | 為 `True` 的狀態 |
| :--- | :--- | :--- |
| `is_active` | `bool` | `RUNNING`、`WAITING_INPUT`、`STOPPING` |
| `is_finished` | `bool` | `DONE`、`FAILED`、`CANCELLED` |

### `ProcessVerdict`

純程序執行結果的 `str` 列舉（`Job.result.verdict`）：

| 成員 | 值 | 優先序與觸發條件 |
| :--- | :--- | :--- |
| `ProcessVerdict.FAIL` | `"FAIL"` | 最高優先：啟動失敗、`timeout` 逾時，或非停止請求下的非零 `returncode`（`not stop_requested`）。 |
| `ProcessVerdict.ERROR` | `"ERROR"` | 出現錯誤紀錄（`error_count > 0`，例如 `output_reader` 或嚴格模式 `output_decode`）或 `reader_cutoff`，且未先觸發 `FAIL`。 |
| `ProcessVerdict.INCOMPLETE` | `"INCOMPLETE"` | 在 `stop()`、`kill()` 或取消請求（`stop_requested=True`）後結束或封存，且未出現 `FAIL` 或 `ERROR`。 |
| `ProcessVerdict.NA` | `"N/A"` | 正常完成（`returncode == 0`、無錯誤且無停止請求）。 |
| `None` | `None` | Attempt 尚未結束或封存。 |

### `StopPolicy`、`ControlReceipt`、`Report`、`Capabilities`

- `StopPolicy(str, Enum)`：`StopPolicy.GRACEFUL_THEN_FORCE`（`"GRACEFUL_THEN_FORCE"`）與 `StopPolicy.FORCE`（`"FORCE"`）。
- `ControlReceipt(status, reason)`：不可變 dataclass，`status` 為 `"accepted"`、`"rejected"` 或 `"failed"`，`reason` 為非空文字。僅當 `status == "accepted"` 時 `.accepted` 與 `bool(receipt)` 為 `True`。
- `Report(verdict, execution, warnings, errors, termination)`：`Job.result` 回傳的不可變 dataclass。`execution` 為凍結 mapping，包含 `status`、`returncode` 與 `elapsed`。
- `Capabilities(has_process=False, can_send=False, can_stop=False, can_interrupt=False, can_signal=False, can_kill=False, can_rerun=False)`：描述目前 attempt 支援的控制能力；`.snapshot(can_rerun=False)` 回傳普通 `dict`。

## `ProcessHandle`

單次執行的子程序傳輸介面，合併二進位 stdout／stderr、提供 1.0 秒限時非阻塞寫入，並具備作業系統層級的子樹隔離。

### 建立方式

```python
handle = ProcessHandle(
    cmd,
    *,
    cwd=None,
    env=None,
    encoding="utf-8",
    pty=False,
    pty_rows=None,
    pty_cols=None,
)
```

與 `Job` 不同，`ProcessHandle` 在所有平台皆預設 `pty=False`，且建構物件時不會啟動子程序。`pty_rows` 與 `pty_cols` 需要 `pty=True`（未指定時分別預設為 `24` 與 `80`），而 Windows 傳入 `pty=True` 會引發 `ValueError`。

### 屬性與物件函式

| 成員 | 用途 |
| :--- | :--- |
| `pid` | `int \| None`：呼叫 `start()` 後的子程序 PID，啟動前為 `None`。 |
| `start()` | 僅能啟動一次，在新的 POSIX session（`start_new_session=True`，若啟用 PTY 則透過 `_pty_exec.py`）或 Windows Job Object（`KILL_ON_JOB_CLOSE`）與 `CREATE_NEW_PROCESS_GROUP` 中啟動子程序。 |
| `poll()` | 若已結束回傳整數退出碼，否則回傳 `None`。 |
| `read()` | 讀取最多 4,096 位元組的合併 stdout／stderr 資料；到達 EOF 或已關閉時回傳 `b""`。 |
| `write(text, *, secret=False)` | 以 1.0 秒傳輸時限編碼並寫入 `text`，不自動附加換行。逾時會引發 `TimeoutError`（部分前綴可能已進入管線，切勿直接重送）；POSIX PTY 上 `secret=True` 會在寫入期間暫時關閉終端機 `ECHO`。 |
| `send_signal(sig)` | 在 POSIX 對整個程序群組呼叫 `os.killpg`；Windows 僅支援 `signal.CTRL_BREAK_EVENT`（其他訊號會引發 `ValueError`）。 |
| `interrupt()` | POSIX PTY（若啟用 `ISIG`）寫入 `\x03`，否則送出 `SIGINT`；Windows 送出 `CTRL_BREAK_EVENT`。 |
| `terminate()` | POSIX 對程序群組送出 `SIGTERM`；Windows 呼叫 `interrupt()`。 |
| `kill()` | POSIX 對程序群組送出 `SIGKILL`；Windows 呼叫 `TerminateJobObject`。 |
| `cleanup()` | 當 `poll() is not None` 時強制清理殘留子孫程序，同時保留管線或 PTY 中尚未讀取的輸出，讓 `read()` 可安全讀到 EOF。 |
| `close()` | 在排空輸出後關閉串流、檔案描述符與 Windows Job Object handle；可重複呼叫。 |

## 範例

### 1. 基本範例：提示偵測與 `manager.control`

偵測互動提示詞進入 `JobStatus.WAITING_INPUT`，透過 `manager.control(..., action="send", newline=True)` 回覆輸入並觀察正常結束。

```python
import sys
import time
from rpkbin.jobcore import Job, JobManager, JobStatus

script = (
    "import sys; "
    "sys.stdout.write('cmd> '); sys.stdout.flush(); "
    "val = sys.stdin.readline().strip(); "
    "print('got:' + val, flush=True)"
)

with JobManager(max_workers=1) as manager:
    job = manager.add(Job("prompt-job", [sys.executable, "-u", "-c", script], pty=False, prompts=("cmd> ",)))
    manager.start()

    deadline = time.monotonic() + 10.0
    while job.status is not JobStatus.WAITING_INPUT:
        if time.monotonic() >= deadline:
            raise TimeoutError("prompt not detected")
        time.sleep(0.01)

    receipt = manager.control(
        job.id,
        "send",
        expected_attempt=job.attempt_id,
        expected_submission=job.submission_id,
        text="ping",
        newline=True,
    )
    assert receipt.accepted

    while not job.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("job did not finish")
        time.sleep(0.01)

    print("logs:", job.logs())
```

輸出：
```text
logs: ['cmd> ', 'Input sent: ping', 'got:ping']
```

### 2. 進階範例：`remove_job`、`removed_jobs`、`restore_job` 與 `ProcessHandle`

在預覽狀態移除排隊中的工作、確認 `removed_jobs`、將工作還原回原本的位置，並示範直接使用 `ProcessHandle` 執行子程序。

```python
import sys
from rpkbin.jobcore import Job, JobManager, ProcessHandle

with JobManager() as manager:
    a = manager.add(Job("a", [sys.executable, "-c", "pass"], pty=False))
    b = manager.add(Job("b", [sys.executable, "-c", "pass"], pty=False))
    c = manager.add(Job("c", [sys.executable, "-c", "pass"], pty=False))

    assert manager.remove_job(b.id).accepted
    print("after remove:", [j.name for j in manager.jobs], "removed:", [j.name for j in manager.removed_jobs])
    assert manager.restore_job("b").accepted
    print("after restore:", [j.name for j in manager.jobs], "removed:", [j.name for j in manager.removed_jobs])

handle = ProcessHandle([sys.executable, "-u", "-c", "import sys; print(sys.stdin.read(2))"])
handle.start()
handle.write("ok")
while handle.poll() is None:
    pass
handle.cleanup()
chunks = []
while chunk := handle.read():
    chunks.append(chunk)
handle.close()
print("handle out:", b"".join(chunks).decode().strip())
```

輸出：
```text
after remove: ['a', 'c'] removed: ['b']
after restore: ['a', 'b', 'c'] removed: []
handle out: ok
```

## 注意事項與限制

- **事件鎖與回呼限制**：在持有另一個工作的 `_events_lock`（或位於 `AttemptReceiver` 回呼內）時呼叫 `manager.add`、`manager.stop_all`、`manager.close`、`manager.set_max_history` 或控制其他 `Job`，會引發 `RuntimeError` 或收到 `"rejected"` 回執。
- **未換行輸出的長度上限**：沒有 `\n` 的長行會以 4 MiB（`_MAX_LINE_CHARS = 4 * 1024 * 1024`）為單位分段送出，或在命中 `prompts` 時立即送出，剩餘緩衝則在 EOF 送出。
- **定義凍結**：一旦 `validate()`（或啟動後的 `add()`）凍結 `Job`，任何修改定義屬性的操作都會引發 `AttributeError`。

## 延伸閱讀

- [快速入門](README.md)
- [實戰使用情境](scenarios.md)
- [rpkbin.flow 參考手冊](../flow/reference.md)
- [文件導覽](../README.md)
