# `rpkbin.jobcore`

[English](../../en/jobcore/README.md) · [文件導覽](../README.md) · [實戰使用情境](scenarios.md) · [API 參考手冊](reference.md)

`rpkbin.jobcore` 依固定的 worker 與資源配額排程外部命令，並在獨立的 POSIX 程序群組或 Windows Job Object 中執行每一次嘗試（attempt）。

## 語法

```python
manager = JobManager(*, max_workers=4, resources=None, log_dir=None, max_history=20, scheduling_mode="backfill")
with JobManager(...) as manager: ...
job = Job(name, cmd, *, pty=None, prompts=(), resources=None, priority=0, timeout=None, max_retries=0)
manager.add(job)
manager.start()
receipt = manager.control(job.id, action, *, expected_attempt, expected_submission, text=None, secret=False, newline=True, sig=None)
receipt = job.send(text, *, newline=True, secret=False, expected_attempt=None)
receipt = job.stop(*, source="user", reason=None, expected_attempt=None, force=False)
handle = ProcessHandle(cmd, *, cwd=None, env=None, encoding="utf-8", pty=False, pty_rows=None, pty_cols=None)
```

## 說明

- `JobManager(...)` 建立排程器，設定 worker 上限、具名資源容量、歷史保留上限與優先權排程模式（`"backfill"`、`"strict"` 或 `"tier_strict"`）。
- `with JobManager(...) as manager:` 進入管理器 context，離開區塊時自動呼叫 `manager.close()` 停止執行中的工作、排空輸出並關閉接收器。
- `Job(name, cmd, ...)` 定義命令字串或引數序列，並設定環境變數、編碼、提示字串（`prompts`）、資源需求、逾時、重試次數與停止策略。
- `manager.add(job)` 註冊工作定義；`manager.start()` 驗證所有已註冊工作、凍結定義並啟動背景排程執行緒。
- `manager.control(...)` 與 `job.send(...)`、`job.stop(...)` 執行帶有身分防護的程序控制，檢查 `expected_attempt` 與 `expected_submission` 後回傳 `ControlReceipt`。
- `ProcessHandle(cmd, ...)` 提供底層單次子程序傳輸介面，合併 stdout／stderr、支援非阻塞限時寫入與整棵程序樹清理。

## 範例

### 1. 基本範例：執行單一命令並檢查判決

註冊一個 Python 單行命令、啟動排程器、等待 attempt 封存完成，並確認擷取的輸出與 `ProcessVerdict`。

```python
import sys
import time
from rpkbin.jobcore import Job, JobManager, JobStatus, ProcessVerdict

with JobManager(max_workers=2) as manager:
    job = manager.add(Job("probe", [sys.executable, "-u", "-c", "print('ready')"], pty=False))
    manager.start()

    deadline = time.monotonic() + 10.0
    while not job.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("job did not finish")
        time.sleep(0.01)

    assert job.status is JobStatus.DONE
    assert job.returncode == 0
    assert job.result.verdict is ProcessVerdict.NA
    print("logs:", job.logs())
    print("verdict:", job.result.verdict.value)
```

輸出：
```text
logs: ['ready']
verdict: N/A
```

### 2. 進階範例：Context Manager、佇列暫緩與狀態檢視

在啟動前將較低優先權的工作設為暫緩，檢視 `repr(manager)` 與快照中的 `HELD` 等候狀態碼，隨後解除暫緩讓兩項工作依序完成。

```python
import sys
import time
from rpkbin.jobcore import Job, JobManager

with JobManager(max_workers=1, resources={"license": 1}) as manager:
    first = manager.add(Job("first", [sys.executable, "-u", "-c", "print('one')"], pty=False, resources={"license": 1}, priority=20))
    second = manager.add(Job("second", [sys.executable, "-u", "-c", "print('two')"], pty=False, resources={"license": 1}, priority=10))

    assert manager.set_held(second, True).accepted
    print("manager:", repr(manager))
    print("pending:", manager.explain_pending(second))
    snap = manager.snapshot(selected_job_id=second.id)
    print("code:", snap["detail"]["pending_code"])

    manager.start()
    deadline = time.monotonic() + 10.0
    while not first.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("first job timed out")
        time.sleep(0.01)

    assert second.status.value == "PENDING"
    assert manager.set_held(second, False).accepted
    while not second.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("second job timed out")
        time.sleep(0.01)

    print("outputs:", first.logs(), second.logs())
```

輸出：
```text
manager: JobManager(jobs=2, active=0/1, started=False, paused=True, closed=False)
pending: Held (excluded from queue)
code: HELD
outputs: ['one'] ['two']
```

## 注意事項與限制

- **跨平台傳輸預設值**：`Job` 的 `pty=None` 在 POSIX 預設為 `True`（使用 PTY），在 Windows 預設為 `False`（使用 pipe）。在 Windows 傳入 `pty=True` 會引發 `ValueError`。Windows 上的 `send_signal()` 僅支援 `signal.CTRL_BREAK_EVENT`，若要結束整個程序樹請使用 `stop()` 或 `kill()`。
- **`ProcessVerdict` 與測試通過判定**：`ProcessVerdict.NA` 表示程序以 `0` 結束且沒有發生錯誤、逾時或停止請求。`rpkbin.jobcore` 不會依輸出文字判定 `PASS`；若需依輸出內容決定測試結果，請透過 `rpkbin.wave` 搭配 `rpkbin.flow`。
- **相容性**：僅使用 Python 標準函式庫，需要 Python `>= 3.11`，可離線執行。上方範例已在 Windows、Python 3.13 實測通過。

## 延伸閱讀

- [實戰使用情境](scenarios.md)
- [API 參考手冊](reference.md)
- [rpkbin.flow](../flow/README.md)
- [Wave](../wave/README.md)
- [文件導覽](../README.md)
