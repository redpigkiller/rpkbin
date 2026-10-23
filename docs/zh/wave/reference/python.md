# Wave Python API 參考手冊

[English](../../../en/wave/reference/python.md) · [文件導覽](../../README.md) · [情境教學](../scenarios.md)

本文件提供 Wave Python 介面的精確類別、函式簽名、參數型別、預設值、列舉與回傳規格。

匯入命名空間：
```python
from rpkbin.wave import (
    Job,
    JobManager,
    JobStatus,
    Result,
    CheckState,
    Expect,
    StopPolicy,
    Event,
    Report,
)
```

---

## 核心管理器：`JobManager`

`JobManager` 負責管理所有工作之排程、並行限制、資源配額、日誌分發與狀態快照。

### 建構式
```python
JobManager(
    *,
    max_workers: int = 4,
    resources: dict[str, int] | None = None,
    log_dir: str | Path | None = None,
    columns: list[str] | tuple[str, ...] | None = None,
    max_history: int = 20,
)
```

- **`max_workers`** (`int`, 預設 4): 最大同時執行的子程序數。必須 $\ge 1$。
- **`resources`** (`dict[str, int] | None`, 預設 None): 固定命名資源容量字典，例如 `{"gpu": 2, "token": 10}`。數值必須為非負整數。
- **`log_dir`** (`str | Path | None`, 預設 None): 若指定，各工作的日誌會自動存入 `{log_dir}/{job.id}.log`。
- **`columns`** (`list[str] | None`, 預設 None): 自訂表格預設顯示的 track 欄位名稱清單。
- **`max_history`** (`int`, 預設 20): 每個工作在記憶體中保留的過往已完成 Attempt 數量上限。必須 $\ge 1$。

### 核心方法

#### `add(job: Job) -> Job`
將一個已定義的工作加入管理器。
- **限制**：同一工作不能重複加入；不可加入已屬於其他管理器的工作。
- **回傳**：傳入的 `job` 物件本身。

#### `add_many(jobs: Sequence[Job]) -> list[Job]`
批次加入多個工作。

#### `get_job(job_id_or_name: str | Job) -> Job | None`
根據工作 ID 或名稱查詢工作。若有多個同名工作引發 `ValueError`（此時應以唯一 ID 查詢）。

#### `validate() -> None`
驗證所有已加入工作的設定與宣告，並凍結工作定義；驗證成功前不派發工作。若工作檔內無工作或資源需求超出容量，引發例外。

#### `start() -> None`
正式開始工作排程與派發。若在載入工作定義範圍內（`definition_scope`）呼叫會引發 `ValueError`。

#### `pause() -> None`
暫停派發新工作。已在運行的工作會繼續執行。

#### `resume() -> None`
恢復工作派發（等同呼叫 `start()`）。

#### `stop_all(*, force: bool = False, source: str = "user", reason: str | None = None) -> int`
停止所有工作。首先取消所有處於 `PENDING` 的等待工作，再依序停止運行中的工作。回傳成功請求停止的工作數量。

#### `rerun(job_id: str | Job, *, expected_attempt: str | None = None, expected_submission: str | None = None) -> Job`
手動重新執行一個已結束的工作。舊的 Attempt 會被封存進歷史（受 `max_history` 限制），並建立一個全新的 Attempt 重新排隊。

#### `set_priority(job_id: str | Job, priority: int, *, expected_attempt: str | None = None, expected_submission: str | None = None) -> None`
動態修改處於 `PENDING` 等待佇列中工作的優先權。若工作已在派發或執行中，引發 `ValueError`。

#### `set_max_history(max_history: int) -> int`
動態修改所有工作在記憶體中保留的 Attempt 歷史上限。若現有歷史數量超出新上限，立即從最舊記錄開始淘汰。回傳被淘汰的歷史 Attempt 總數。

#### `set_max_log_lines(max_log_lines: int, job_id: str | None = None, *, expected_attempt: str | None = None, expected_submission: str | None = None) -> int`
調整日誌保留行數。若未指定 `job_id`，則同時套用到全部工作。回傳被淘汰的日誌行數總合。

#### `update_settings(*, max_workers: int | None = None, resources: dict[str, int] | None = None, columns: list[str] | None = None, max_history: int | None = None) -> dict`
動態更新管理器的並行數、資源容量、顯示欄位或歷史上限。調小歷史上限時會立即淘汰過舊 Attempt。

#### `snapshot(*, selected_job_id: str | None = None, attempt_id: str | None = None) -> dict`
取得管理器目前的全局狀態快照字典，包含運行旗標、worker 負載、資源佔用狀況，以及所有工作（含指定工作的完整日誌與歷史）的結構化資料。

#### `explain_pending(job: str | Job) -> str`
查詢特定工作目前處於 `PENDING` 狀態的原因（例如正在等待 worker 槽位、等待特定資源容量，或排程器處於暫停狀態）。

#### `add_log_listener(callback: Callable[[dict], None]) -> None`
註冊全局日誌監聽回呼。每當任何工作記錄一行日誌時，以字典格式接收日誌事件資料。

#### `close() -> None`
安全關閉管理器，向所有子程序發送停止請求，等待收尾排空後終止背景排程緒。

---

## 工作定義與實例：`Job`

`Job` 代表一個命令的執行單位。工作通過 `validate()` 或開始派發時，其名稱、命令、環境變數與監控宣告即被**凍結（Frozen）**。

### 建構式
```python
Job(
    name: str,
    cmd: str | Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    pty: bool | None = None,
    prompts: Sequence[str] = (),
    pty_rows: int | None = None,
    pty_cols: int | None = None,
    resources: dict[str, int] | None = None,
    priority: int = 0,
    timeout: float | None = None,
    max_retries: int = 0,
    stop_policy: StopPolicy = StopPolicy.GRACEFUL_THEN_FORCE,
    stop_ctrl_c: bool = False,
    stop_cmd: str | None = None,
    stop_timeout: float = 3.0,
    max_log_lines: int = 10000,
    log_file: str | Path | None = None,
)
```

- **`name`** (`str`): 工作名稱，不可為空。
- **`cmd`** (`str | Sequence[str]`): 執行的命令字串或引數字串清單。
- **`cwd`** (`str | Path | None`): 子程序工作目錄。預設為工作檔啟動時的當前目錄。
- **`env`** (`dict[str, str] | None`): 覆寫或新增的環境變數。
- **`pty`** (`bool | None`): 是否啟用虛擬終端（PTY）。POSIX 環境預設 `True`；Windows 不支援 PTY，固定使用管道（Pipes）。
- **`prompts`** (`Sequence[str]`): 提示字元字面標記清單（如 `("ready> ", "gdb> ")`）。當子程序輸出結尾匹配此標記時，狀態轉為 `WAITING_INPUT`。
- **`pty_rows`, `pty_cols`** (`int | None`): PTY 終端行列尺寸（僅 POSIX PTY 有效，$\le 65535$）。
- **`resources`** (`dict[str, int] | None`): 宣告此工作運行時必須佔用的資源份數（如 `{"license": 1}`）。
- **`priority`** (`int`, 預設 0): 派發優先權。數值愈大愈優先派發；同優先權依先進先出（FIFO）排程。
- **`timeout`** (`float | None`): 單次執行的時間上限（秒）。超時會自動觸發停止流程。
- **`max_retries`** (`int`, 預設 0): 未被要求停止且非零退出時的額外重試次數上限。單獨 check FAIL 不會觸發重試；逾時停止也不自動重試。
- **`stop_policy`** (`StopPolicy`, 預設 `StopPolicy.GRACEFUL_THEN_FORCE`): 停止策略。可選 `FORCE` 或 `GRACEFUL_THEN_FORCE`。
- **`stop_ctrl_c`** (`bool`, 預設 False): 優雅停止時是否先發送中斷訊號（Ctrl+C）。
- **`stop_cmd`** (`str | None`): 優雅停止時發送給子程序 stdin 的命令字串（如 `"quit"`）。
- **`stop_timeout`** (`float`, 預設 3.0): 優雅停止等待子程序自願結束的最長時間（秒），超時則升級為強制終止。
- **`max_log_lines`** (`int`, 預設 10000): 該工作在記憶體中保留的日誌最大行數（環形緩衝區）。
- **`log_file`** (`str | Path | None`): 獨立寫入的實體日誌檔案路徑。

### 可觀測屬性 (Observable Properties)

- **`job.status`** (`JobStatus`): 當前 Attempt 的執行狀態（如 `PENDING`、`RUNNING`、`DONE`、`FAILED`、`CANCELLED`）。
- **`job.returncode`** (`int | None`): 子程序的作業系統退出碼；尚未結束時為 `None`。
- **`job.pid`** (`int | None`): 子程序的作業系統 Process ID；尚未啟動時為 `None`。
- **`job.elapsed`** (`float | None`): 當前 Attempt 的累計執行秒數。
- **`job.elapsed_str`** (`str`): 格式化為 `MM:SS` 或 `HH:MM:SS` 的執行耗時字串。
- **`job.priority`** (`int`): 工作的排程優先權整數值。
- **`job.cmd_str`** (`str`): 轉為命令列字串的完整指令。
- **`job.attempt_id`** (`str`): 當前 Attempt 的唯一十六進位識別碼。
- **`job.submission_id`** (`str`): 關聯的提交識別碼。
- **`job.attempt_number`** (`int`): 當前 Attempt 的輪次序號（從 1 起算）。
- **`job.data`** (`MappingProxyType`): 當前 Attempt 由 `Track` 規則擷取出的鍵值資料（不可變 MappingProxy）。
- **`job.context`** (`dict`): 供使用者回呼函式自由存放自訂狀態的 Attempt 專屬字典。
- **`job.result`** (`Report`): 當前 Attempt 的完整判定報告物件。包含以下屬性：
  - **`job.result.verdict`** (`Result | None`): 目前的彙整判定；執行中已確認的 FAIL／ERROR 會立即可見，其餘尚未定案時為 `None`。正常完整結束後才可能為 PASS／N/A。
  - **`job.result.checks`** (`tuple`): 各 check 目前狀態的唯讀快照元組；Attempt 結束後定稿。
  - **`job.result.warnings`** (`tuple`): 各 warning 的命中計數與證據元組。
  - **`job.result.errors`** (`tuple`): 監控規則或回呼拋出的異常記錄元組。
- **`job.history`** (`tuple[dict, ...]`): 過往已完成 Attempt 的唯讀快照字典元組（受 `max_history` 限制）。

### 監控宣告方法

#### `track(name: str, pattern: str | re.Pattern, *, cast: Callable = str) -> Track`
宣告動態資料擷取規則。
- **`pattern`**：正則表達式，**必須恰好包含一個捕獲括號組 `()`**。
- **`cast`**：轉換函式（如 `int`, `float`, `json.loads`）。
- **回傳**：`Track` 物件，提供 `.events.changed` 與 `.events.error` 事件。

#### `check(name: str, *, match: str | re.Pattern | Callable, expect: Expect = Expect.PRESENT, on: Event | EventRef = Event.LOG, every: float | None = None) -> Check`
宣告驗收檢查規則。
- **`match`**：比對規則。可為正則字串、編譯後正則或回傳 `bool` 的自訂函式。
- **`expect`**：`Expect.PRESENT`（必須出現）或 `Expect.ABSENT`（禁止出現）。
- **`on`**：觀察來源。預設 `Event.LOG`（日誌行）；亦可為 `Event.TICK`（週期檢查，搭配 `every=秒數`）或 `track.events.changed`。
- **回傳**：`Check` 物件，提供 `.events.failed`、`.events.satisfied`、`.events.error` 事件。

#### `on(event: Event | EventRef = Event.LOG, *, match: str | re.Pattern | Callable | None = None, warn: bool = False, stop: bool = False, callback: Callable | None = None, name: str | None = None, message: str | None = None, once: bool = True, every: float | None = None) -> Subscription`
宣告事件訂閱與反應動作。
- **動作參數**：`callback` 與 `warn`/`stop` 為**二選一**。
- **`warn=True`**：記錄為警告，不中斷工作，不影響 PASS。
- **`stop=True`**：依據此工作的 `stop_policy` 要求停止工作。
- **`callback`**：簽名為 `callback(bound_job, *args)` 的 Python 函式。
- **`once`** (`bool`, 預設 True): 是否僅觸發一次。若要持續觸發請明確設為 `False`。

### 互動、日誌與控制方法

- **`send(text: str, *, newline: bool = True, expected_attempt: str | None = None) -> bool`**：向子程序 stdin 送出文字。回傳是否成功寫入管道。
- **`send_ctrl_c(*, expected_attempt: str | None = None) -> bool`**：向子程序發送中斷信號。
- **`send_signal(sig: int, *, expected_attempt: str | None = None) -> bool`**：向子程序發送指定 OS 訊號。
- **`stop(*, source: str = "user", reason: str | None = None, force: bool = False, expected_attempt: str | None = None) -> bool`**：要求停止該工作。
- **`kill(*, expected_attempt: str | None = None) -> bool`**：強制終止該工作程序樹。
- **`log(message: Any) -> None`**：以 `[Wave]` 來源向此工作的即時日誌串流中寫入一筆文字訊息。
- **`logs(tail: int | None = None) -> list[str]`**：讀取當前 Attempt 保留於記憶體環形緩衝區中的日誌字串清單。可透過 `tail=N` 僅取得最後 $N$ 行。
- **`set_max_log_lines(max_log_lines: int, *, expected_attempt: str | None = None, expected_submission: str | None = None) -> int`**：動態更新此工作的環形緩衝區上限。回傳被淘汰的行數。
- **`to_snapshot_dict(*, include_logs: bool = False, attempt_id: str | None = None) -> dict`**：產生該工作完整結構化快照字典。

> [!NOTE]
> **關於 `expected_attempt` 參數**：
> 控制方法上的 `expected_attempt` 與 `expected_submission` 為 Wave 內部防止換輪競態（Rerun Race Condition）之防禦性參數。當使用者在 Python 腳本或回呼中撰寫標準邏輯時，**直接呼叫 `job.send("cmd")` 或 `job.stop()` 即可**，無需手動傳遞該參數。

---

## 事件系統與回呼簽名 (Events & Callbacks)

Wave 採用統一的事件發布與訂閱模型。各類事件的來源、型別與回呼簽名如下：

| 事件參考 | 觸發時機 | 回呼函式簽名 | 參數說明 |
| :--- | :--- | :--- | :--- |
| **`Event.LOG`** | 子程序輸出了一行日誌 | `callback(bound_job, line: str)` | `line` 為去除 ANSI 控制碼後的整行文字。 |
| **`Event.TICK`** | 定期時鐘計時器觸發 | `callback(bound_job, elapsed: float)` | `elapsed` 為當前 Attempt 累計運行秒數（搭配 `every=1.0` 設定週期）。 |
| **`track.events.changed`** | Track 擷取到與前值不同的數值 | `callback(bound_job, old_value: Any, new_value: Any)` | `old_value` 為前值（第一次為 `None`），`new_value` 為經 `cast` 轉換後的新數值。 |
| **`track.events.error`** | Track 的 `cast` 轉換函式拋出例外 | `callback(bound_job, error: dict)` | `error` 包含 `source`、`message`、`evidence`。 |
| **`check.events.satisfied`** | Check 條件判定達成（`OK`） | `callback(bound_job, check_state: dict)` | `check_state` 包含 `name`、`state`、`hits`、`evidence`。 |
| **`check.events.failed`** | Check 條件判定未通過（`FAIL`） | `callback(bound_job, check_state: dict)` | 同上。 |
| **`check.events.error`** | Check 的比對函式拋出例外 | `callback(bound_job, error: dict)` | 同 track.events.error。 |
| **`job.events.status_changed`** | 工作的執行狀態發生轉變 | `callback(bound_job, prev_status: JobStatus, new_status: JobStatus)` | 前後狀態值。 |
| **`job.events.attempt_finished`** | 單次 Attempt 結束收尾完成 | `callback(bound_job, attempt_snapshot: dict)` | 該次 Attempt 的不可變快照字典。 |
| **`job.events.error`** | 監控、控制或執行發生可回報的錯誤 | `callback(bound_job, error: dict)` | 內部錯誤結構。 |

### 事件回呼程式碼範例

```python
import sys
from rpkbin.wave import Job, JobManager, Event, Expect

mgr = JobManager()
job = mgr.add(Job("event_demo", [sys.executable, "-u", "-c", "import time; print('PROGRESS=50'); time.sleep(0.1); print('PASS_TOKEN')"]))

# 1. 訂閱日誌輸出
job.on(Event.LOG, match=r"^PROGRESS=(\d+)$", callback=lambda bound, line: bound.log(f"Matched line: {line}"))

# 2. 資料擷取與變更事件
progress = job.track("progress", r"^PROGRESS=(\d+)$", cast=int)
job.on(progress.events.changed, callback=lambda bound, old, new: bound.log(f"Progress changed: {old} -> {new}%"))

# 3. Check 判定達成事件
chk = job.check("pass_check", match=r"^PASS_TOKEN$", expect=Expect.PRESENT)
job.on(chk.events.satisfied, callback=lambda bound, state: bound.log(f"Check {state['name']} satisfied!"))
```

`match` 若使用 callable，也接收相同事件參數，並回傳 bool，例如 `match=lambda bound, old, new: new >= 80`。所有宣告應放在工作開始／驗證之前。

若要遇到禁止輸出或監控錯誤就停止，可接在上述定義後：

```python
# 延續上例的 job；禁止條件一旦命中，先記 FAIL，再依工作停止策略收尾。
no_fatal = job.check("no_fatal", match=r"^FATAL:", expect=Expect.ABSENT)
job.on(no_fatal.events.failed, stop=True)
job.on(job.events.error, stop=True)  # 不設定這行時，可隔離的監控錯誤預設不停止程序。

# 長工作可每秒回報耗時；短工作可能在第一個 tick 前就結束。
job.on(Event.TICK, every=1.0, once=False,
       callback=lambda bound, elapsed: bound.log(f"Elapsed: {elapsed:.1f}s"))
```

---

## 監控錯誤隔離與回呼延遲限制

1. **監控錯誤隔離 (Monitor Fault Isolation)**：
   - 當使用者註冊的回呼函式、`cast` 函式或比對條件拋出未處理例外時，Wave 會將例外記錄至 `job.result.errors`，使 `error_count` 累加，並**在該 Attempt 停用故障的監控項目**；其他監控與工作預設繼續。新的 retry／rerun Attempt 會重新啟用監控。若希望錯誤發生後停止，明確訂閱 `job.events.error`。
   - 該次 Attempt 無法獲得 `Result.PASS`；沒有較高優先級 FAIL 時為 `Result.ERROR`，已有 FAIL 則保留。Attempt 定稿後的完成通知錯誤只記 diagnostics，不倒改結果。
2. **回呼執行延遲限制 (Callback Latency Constraints)**：
   - 回呼函式在觸發事件的執行緒中**同步執行**；同一管理器的事件處理共用鎖，因此阻塞也可能拖慢其他工作的監控與控制。
   - 回呼中**嚴禁**執行長耗時或阻塞操作（如 `time.sleep`、大檔磁碟寫入或同步網路請求），否則會阻礙子程序管道排空與即時判定。

---

## 列舉型別 (Enums)

### `JobStatus` (子程序執行狀態)
- `PENDING`：在佇列中等待派發。
- `RUNNING`：子程序正在執行。
- `WAITING_INPUT`：偵測到配置的 prompt token；提示可能需要輸入，不保證程序真的在等待。
- `STOPPING`：正在進行停止與收尾流程。
- `DONE`：子程序正常結束且退出碼為 0。
- `FAILED`：子程序以非零退出碼結束或執行時超時。
- `CANCELLED`：子程序被手動停止或取消。

### `Result` (業務驗收結果)
- `PASS`：正常完整結束、退出碼為 0、監控有效且所有宣告的 checks 皆為 OK。
- `FAIL`：任一 check 失敗、啟動失敗、超時，或未要求停止時程序非零退出。
- `ERROR`：監控或回呼發生非預期例外（且無已確定的 FAIL）。
- `INCOMPLETE`：無已確認的 FAIL／ERROR，但工作未完整完成即被停止或取消；即使 checks 已全 OK 也不能改判 PASS。
- `NA` (`"N/A"`): 程序正常結束但未宣告任何 checks。

### `CheckState` (單項檢查狀態)
- `UNDECIDED` (`"-"`): 尚未判定。
- `OK`：檢查通過。
- `FAIL`：檢查未通過。
- `ERROR`：檢查比對函式或轉換失敗。
- `INCOMPLETE`：執行提前中斷，無法完成判定。

### `Expect`
- `PRESENT` (`"present"`): 預期條件必須出現。
- `ABSENT` (`"absent"`): 預期條件禁止出現。

### `StopPolicy`
- `GRACEFUL_THEN_FORCE`：優先嘗試優雅退出指令或中斷，超時則發送強制終止信號。
- `FORCE`：直接強制終止程序樹。

### `Event`
- `LOG` (`"log"`): 子程序輸出了一行日誌。
- `TICK` (`"tick"`): 定期時鐘事件（需指定 `every` 秒數）。
