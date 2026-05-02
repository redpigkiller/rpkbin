# Job Manager — 實用型並行工作管理

[![English](https://img.shields.io/badge/Language-English-blue.svg)](job_manager.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](job_manager_zh.md)

`rpkbin.job_manager` 是一個輕量、跨平台的 **Job Manager**，可同時管理 Shell 指令（`CmdJob`）與 Python 函式（`FuncJob`），並提供可控的併發與資源感知排程。

它適合用在 EDA 流程、CI 輔助腳本、批次自動化等場景：你需要平行執行任務，但又不希望系統過載，並且希望在出錯時能快速定位問題。

---

## 快速上手

建議用 `JobManager` 的 context manager 形式：

```python
from rpkbin.job_manager import JobManager, CmdJob, FuncJob, DONE


def preprocess():
    return "ok"


with JobManager(max_workers=2, resources={"gpu": 1}) as manager:
    py_job = FuncJob("preprocess", preprocess)
    sim_job = CmdJob("run_sim", "./run_sim.sh", resources={"gpu": 1})

    manager.add(py_job)
    manager.add(sim_job)
    manager.wait()  # 等待 manager 完全進入穩定空閒狀態

print(py_job.status == DONE, sim_job.status == DONE)
```

這種寫法的好處：
- 進入 `with` 時自動 `start()`。
- 正常離開時自動等待並 `stop()`。
- 發生例外時會先取消 pending/running 工作，再安全停止。

---

## 核心觀念

### 1. 工作類型

- `CmdJob`：執行 shell command
- `FuncJob`：執行 Python callable

兩者都支援：
- `priority`（數字越大優先）
- `max_retries`
- `resources`
- `job_id`（UUID 或 UUID 字串）

### 2. 資源感知排程

`JobManager(resources=...)` 的容量可設定為：
- `int`（固定容量）
- `Callable[[], int]`（動態容量）

範例：

```python
flag = {"gpu": 0}

with JobManager(resources={"gpu": lambda: flag["gpu"]}) as manager:
    manager.add(CmdJob("sim", "./sim.sh", resources={"gpu": 1}))
    # 當 flag["gpu"] == 0 時，工作會維持 pending
    flag["gpu"] = 1
    manager.wait()
```

### 3. 工作狀態

終態：
- `done`
- `failed`
- `cancelled`

非終態：
- `pending`
- `running`

建議用 `job.status` 判斷流程。對 `CmdJob` 而言，成功通常是 `job.status == "done"` 且 `job.result == 0`。

### 4. `wait()` 實際等待的是什麼

`wait()` 有兩種稍微不同的語意：

- `manager.wait(job.id)`：只等待指定 job 進入終態
- `manager.wait()`：等待整個 manager 進入穩定空閒狀態

這裡的「穩定空閒」表示：
- 所有已知 job 都已進入終態
- worker thread 端的收尾流程已完成
- manager 提交的非同步 callbacks 已排空

這個較強的 whole-manager wait 是刻意設計的。它可以避免這種 race：
最後一個可見 job 看起來已經結束，但 `on_done(...)` callback 緊接著又補進後續工作。

> [!CAUTION]
> **防止 Deadlock**
> 絕對不要在非同步 callback（`on_done`、`on_fail`、`on_retry` 或 `on_queue_drained`）中呼叫 `manager.wait()`。這樣做會導致 `RuntimeError`，因為這會造成永久性的 deadlock。若需連鎖執行工作，請直接在 callback 中呼叫 `manager.add()`，不要等待 manager 空閒。

---

## 常見使用情境

### 手動生命週期控制（`start` / `pause` / `resume` / `wait` / `stop`）

```python
from rpkbin.job_manager import JobManager, CmdJob

manager = JobManager(max_workers=2)
manager.start()

try:
    manager.pause()

    j1 = CmdJob("step1", "echo step1")
    j2 = CmdJob("step2", "echo step2")
    manager.add(j1)
    manager.add(j2)

    manager.resume()
    manager.wait(j1.id)  # 先等指定工作進入終態
    manager.wait()       # 再等 manager 完全空閒
finally:
    manager.stop()
```

### 混合排程：指令 + 函式

```python
with JobManager(max_workers=4) as manager:
    manager.add(CmdJob("lint", "ruff check ."))
    manager.add(CmdJob("tests", "pytest -q"))
    manager.add(FuncJob("summarize", lambda: "report ready"))
    manager.wait()
```

### 不穩定任務的重試

```python
job = CmdJob("remote_step", "./maybe_flaky.sh", max_retries=2)
with JobManager(max_workers=1) as manager:
    manager.add(job)
    manager.wait(job.id)
print(job.status, job.error)
```

### 即時輸出與 callback

```python
job = CmdJob("test", "pytest -v")
job.on_done(lambda j: print(f"{j.name} done"))
job.on_fail(lambda j, err: print(f"{j.name} failed: {err}"))
job.watch(r"FAILED", lambda j, m: print("偵測到失敗關鍵字"))

with JobManager(max_workers=1) as manager:
    manager.add(job)
    manager.wait()

print(job.tail(20))
```

### 暫停/恢復與取消

```python
with JobManager(max_workers=2) as manager:
    j1 = FuncJob("slow1", lambda: ...)
    j2 = FuncJob("slow2", lambda: ...)

    manager.pause()      # 暫停派工（pending 不會被取出執行）
    manager.add(j1)
    manager.add(j2)
    manager.resume()     # 恢復派工

    j2.cancel()          # 取消單一工作
    manager.cancel_all() # 取消所有 pending/running 工作
```

### 執行中更新設定

```python
with JobManager(max_workers=1) as manager:
    # ... add jobs
    manager.update_config(max_workers=4)       # 動態調高併發
    manager.update_config(resources={"gpu": 2})
```

---

## API 速查

### JobManager

| 方法 | 說明 |
| --- | --- |
| `JobManager(max_workers=4, resources=None, log_dir=None, max_history=1000, poll_interval=0.5)` | 建立管理器。`resources` 支援固定/動態容量。 |
| `start()` / `stop()` | 啟動/停止排程迴圈。`stop()` 會等待目前 worker threads 結束。 |
| `add(job)` | 加入工作。若資源需求不可能滿足，會丟 `ValueError`。**注意**：若在 manager 停止後呼叫會丟 `RuntimeError`。 |
| `wait(target_id=None, timeout=None) -> bool` | 等待指定工作或整個 manager 進入穩定空閒狀態。**注意**：若在 `manager.start()` 前或從非同步 callback 中呼叫會丟 `RuntimeError`。 |
| `pause()` / `resume()` | 暫停/恢復派工。 |
| `cancel_all()` | 取消所有 pending/running 工作。 |
| `update_config(max_workers=None, resources=None)` | 動態更新併發或資源設定。**注意**：僅影響未來的派工；執行中的工作不會被中斷。 |
| `on_queue_drained(cb)` | 當 queue 無 pending/running 工作時觸發 callback。 |
| `jobs()` / `pending()` / `running()` / `finished()` | 依狀態取得工作快照。 |
| `get(target_id)` | 依 UUID（或字串）取得工作。 |
| `stats()` | 回傳 workers/resources/jobs 的即時快照。**注意**：若資源容量是 callable，會在此處被呼叫。若該 callable 丟出例外，則容量會 fallback 成 `0`。 |

### Job（Base）

| 屬性 / 方法 | 說明 |
| --- | --- |
| `id`, `name` | 穩定識別資訊。 |
| `status` | `pending` / `running` / `done` / `failed` / `cancelled`。 |
| `result`, `error` | 結果或失敗原因。 |
| `logs()`, `tail(n)` | 讀取已擷取輸出。 |
| `cancel()` | 取消工作。 |
| `on_log(cb)`, `on_done(cb)`, `on_fail(cb)` | 註冊 callback。**警告**：callback 應保持輕量。`on_log` 非常關鍵，若執行太久（如 >0.1s）可能造成 subprocess 輸出讀取卡住。 |
| `watch(pattern, cb)` | 當 log 行符合 regex 時觸發 callback。**注意**：manager 會針對過慢的 callback 發出警告。請將繁重工作移至背景執行緒。 |

### CmdJob

建構參數：
- `name`, `cmd`
- `job_id=None`
- `cwd=None`, `env=None`
- `priority=0`, `max_retries=0`, `resources=None`, `max_log_lines=10000`
- `flush_tokens=None`（互動式提示字元強制 flush）

額外方法：
- `send_input(text)`
- `send_signal(signum)`

### FuncJob

建構參數：
- `name`, `func`
- `args=None`, `kwargs=None`
- `job_id=None`
- `priority=0`, `max_retries=0`, `resources=None`, `max_log_lines=10000`

注意：
- `FuncJob` 在 Python thread 執行，受 GIL 影響。
- `cancel()` 會將狀態標為 `cancelled`。**注意**：受限於 Python threading 機制，底層 function 本體無法被強制中止，仍會在背景執行直到自然結束。

---

## FAQ（常見錯誤與對應做法）

- **我可以一個一個送 job 嗎？** 可以。每次 `manager.add(job)` 後，用 `manager.wait(job.id)` 逐筆確認。
- **為什麼最後一個 job 看起來結束了，`manager.wait()` 還沒立刻返回？** 因為 whole-manager wait 也會等待 manager 自己提交的非同步 callback 與 worker 收尾流程排空。
- **我中途發現 command 下錯了，能重來嗎？** 可以。先 `job.cancel()`，再建立正確的新 job 重新 `add()`。
- **為什麼 job 一直卡在 `pending`？** 通常是 worker 或資源限制。先檢查 `max_workers`、`resources`、`manager.stats()`。
- **我想一次全部停掉可以嗎？** 可以。用 `manager.cancel_all()`。
- **失敗時怎麼快速追問題？** 建議開 `log_dir=...`，搭配 `job.error` 與 `job.tail(n)`。
- **為什麼會看到 "Slow callback detected" 警告？** 這是因為 callback 中有耗時操作（運算、等待、或長時間 `sleep`），導致 worker 執行緒被阻塞。建議將繁重邏輯移至背景 worker 或非同步機制。
- **為什麼 `wait()` 丟出提到 deadlock 的 `RuntimeError`？** 因為你在非同步 callback 中呼叫了 `wait()`。請改為直接 `add()` 後續工作，不要在 callback 內等待。
- **執行中可以改併發或資源嗎？** 可以。用 `manager.update_config(max_workers=...)` 與/或 `manager.update_config(resources=...)`。

---

## 除錯建議

- 成功判斷請優先看 `job.status`，不要只看 `job.result` 的 truthy/falsy。
- 需要追查問題時，建議設定 `log_dir=...` 保存每個 job 的獨立 log。
- 在 Linux/macOS，`CmdJob` 使用 `shell=True`，請盡量使用 POSIX shell 相容語法。
- 如果工作長時間停在 `pending`，優先檢查：
  - `max_workers` 是否太小
  - `resources` 是否限制過嚴
  - `manager.stats()` 的即時快照
