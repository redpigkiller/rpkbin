# Wave - 帶觀測能力與互動控制的實用批次流程

[![English](https://img.shields.io/badge/Language-English-blue.svg)](wave.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](wave_zh.md)

`rpkbin.wave` 是建立在 `rpkbin.job_manager` 之上的 workflow layer。
它讓 job manager 專注在排程與執行，並補上實際批次流程常會需要的能力：

- 用 wave file 宣告 jobs 的共享 `session`
- 從 log 擷取結構化狀態的 parser
- 對 log、parsed data、生命週期事件做反應的 hook
- 每個 job 的事件與資料快照
- 預設的**即時互動 TUI**，提供全螢幕 job dashboard 與指令列
- 用於 CI / pipe 環境的 headless REPL

它適合本機自動化、EDA flow、測試批次等場景：jobs 可能會跑一段時間、透過 log 持續輸出狀態，偶爾也需要人工介入。

### 架構概覽

```
wave file (Python)          執行環境
┌──────────────────┐       ┌──────────────────────────────────────┐
│ session.configure│       │  Session                             │
│ session.add(job) │──────▶│  ├─ JobManager（排程 + 執行）        │
│   ├─ parser      │       │  ├─ Timer Thread（elapsed hook 驅動）│
│   ├─ hook        │       │  └─ Events / Summary                 │
│   └─ stop policy │       │                                      │
└──────────────────┘       │  ┌─ TUI（預設）或 Headless REPL ─┐   │
                           │  │ Dashboard │ Job Detail │ Cmd Bar│  │
                           │  └────────────────────────────────┘   │
                           └──────────────────────────────────────┘
```

### 「我想做 X，該用什麼？」

| 我想做的事 | 用什麼 | 在哪裡設定 |
|---|---|---|
| 跑一個 shell 指令 | `CmdJob(name, cmd)` | wave file |
| 跑一個 Python function | `FuncJob(name, fn)` | wave file |
| 跑一個互動式 CLI 程式 | `PtyJob(name, cmd)` | wave file |
| 從 log 擷取進度或狀態 | `job.add_parser(fn)` | wave file |
| 超時自動停止 | `Hook(when=Hook.elapsed_exceeds(s), action=Hook.action_kill())` | wave file |
| 在特定 log 出現時做事 | `Hook(when=Hook.log_matches(pattern), action=...)` | wave file |
| 完成時發通知 | `Hook(when=Hook.on_done(), action=Hook.action_emit(...))` | wave file |
| 對 PTY job 送 Ctrl-C | `key <job> ctrl-c` 或 TUI `F9` | REPL / TUI |
| 對 job 送 OS signal | `signal <job> SIGTERM` | REPL / TUI |
| 溫和關閉互動式程式 | `job.set_stop_policy(graceful_key=..., graceful_input=..., graceful_signal=...)` | wave file |
| 失敗時自動重試 | `CmdJob(name, cmd, max_retries=3)` | wave file |
| 限制 GPU / license 並行 | `session.configure(resources={"gpu": 2})` + `CmdJob(..., resources={"gpu": 1})` | wave file |
| 整批跑完後寫報告 | `session.on_finish(callback)` | wave file |
| 依標籤批量取消 | `session.cancel_group(tag)` | REPL / TUI / wave file |
| 即時監控所有 job | 用 TUI（預設模式）| CLI：不加 `--no-tui` |
| CI 環境自動跑完 | `--no-tui` | CLI |

---

## 快速上手

建議的使用方式是：

1. 寫一個 Python wave file
2. 設定共享 `session`
3. 先把 jobs 加進去
4. 用 `rpk-wave run ...` 或 `runner.run(...)` 執行

最小可執行範例：

```python
from rpkbin.wave import session, CmdJob

session.configure(max_workers=1)
session.add(CmdJob("hello", "python -c \"print('hello from wave')\""))
```

執行：

```powershell
rpk-wave run path\to\hello_wave.py --no-tui
```

稍微完整一點的範例：

```python
from rpkbin.wave import session, CmdJob, Hook

session.configure(max_workers=2)

build = CmdJob("build", "python build.py")
test = CmdJob("test suite", "pytest -q")

def parse_state(line: str) -> dict:
    if line.startswith("STATE="):
        return {"state": line.split("=", 1)[1].strip()}
    return {}

test.add_parser(parse_state)
test.add_hook(
    Hook(
        when=Hook.elapsed_exceeds(300),
        action=Hook.action_kill(),
        policy="once",
    )
)

session.add(build)
session.add(test)
```

也可以直接從 Python 呼叫：

```python
from rpkbin.wave.runner import run

run("path/to/my_wave.py")                # 預設啟動 TUI
run("path/to/my_wave.py", no_tui=True)   # headless 模式
```

或從 CLI：

```powershell
rpk-wave run path\to\my_wave.py            # 啟動 TUI
rpk-wave run path\to\my_wave.py --no-tui   # headless
```

這種模式的好處：

- wave file 可以維持宣告式
- `runner.run(...)` 會在載入前先 `session.reset()`
- 像 `--workers` 這類 CLI override 會在 wave file 載入後套用
- headless 模式提供簡單的互動控制面，但不改變底層執行模型

關於 `CmdJob(...)`：

- 最常見的形式是直接給 shell command 字串，例如 `CmdJob("build", "python build.py")`
- 如果你不確定命令細節，先優先用簡單字串形式
- 更細的 command-job 行為，請參考 job manager 文件

---

## 核心觀念

### 1. Session

`session` 是單次 batch run 的共享註冊入口。

- `session.configure(...)`：在啟動前設定 manager 層級參數
- `session.add(job)`：註冊 job
- `session.emit(tag, message)`：記錄 batch-level 使用者事件
- `session.pause()`、`session.resume()`：暫停/恢復 job manager
- `session.cancel_group(tag)`：依標籤批量取消 jobs
- `session.wait(...)`：等待整批收斂或特定 job 完成
- `session.on_finish(...)`、`session.on_done(...)`、`session.on_fail(...)`：註冊 batch-level lifecycle callback（單次 run 範圍；`session.reset()` 會清掉這些 callback）
- `session.summary()`：回傳 batch-level 摘要快照
- `runner.run(...)`：載入 wave file、啟動 session、等待整批收斂

實務上，wave file 通常比較像 setup code，而不是長時間執行的主程式。

> [!IMPORTANT]
> **動態新增 Job 的時機限制**
> `session.add()` 只能在 session 尚未結束前使用。一旦 session 進入 finish / finalize 階段，呼叫 `session.add()` 會拋出 `RuntimeError`。請勿在 `on_finish()`、`on_done()` 或 `on_fail()` 等 session-level callback 中嘗試新增 job。若需動態連鎖產生新 job，請在 job-level callback 中且在 session 結束前進行。

### 2. Job 類型

Wave 在 job manager job 之上加入了觀測能力。

- `CmdJob` / `WaveCmdJob`
  - shell command job（PIPE-based stdin/stdout）
  - 支援 log parser、log-driven hook、stdin input、OS signal
  - 預設 stop policy：`graceful_signal=SIGINT`
- `PtyJob` / `PtyCmdJob`
  - 在**偽終端 (PTY)** 裡跑 shell command
  - child process 會看到 `isatty() == True`，適合互動式程式
  - 支援 log parser、hook、terminal control key (`key`)、stdin input、OS signal
  - 預設 stop policy：`graceful_key="ctrl-c"`
  - 僅限 Linux / macOS；Windows 上可以建構物件但執行時會回報清楚的錯誤
- `FuncJob` / `WaveFuncJob`
  - Python callable job
  - 支援 hook、events、parsed data update
  - **注意**：取消行為只會將 job 狀態標為 `cancelled`；底層 Python function 常規下無法被強制中斷，仍會在背景繼續執行直到自然結束。

Wave 額外提供的 job API：

- `job.add_parser(fn)`
- `job.add_hook(hook)`
- `job.parsed_data` / `job.peek_data()`
- `job.events` / `job.peek_events()`
- `job.emit(tag, message)`
- `job.skip()`
- `job.is_skipped`
- `job.tags`
- `job.set_progress(value)`
- `job.retry_count`

#### CmdJob vs PtyJob：何時用哪個

| | `CmdJob` | `PtyJob` |
|---|---|---|
| Child I/O | PIPE (stdin/stdout) | PTY（偽終端）|
| `isatty()` | `False` | `True` |
| 適用場景 | 批次腳本、build、test | 互動式 REPL、`read`-based script |
| Ctrl-C 行為 | `signal <job> SIGINT`（OS signal）| `key <job> ctrl-c`（terminal key → kernel SIGINT）|
| 預設 stop | `graceful_signal=SIGINT` | `graceful_key="ctrl-c"` |
| 平台支援 | 全平台（Windows、Linux、macOS）| 僅 Linux / macOS |

除非程式需要 `isatty() == True` 或 terminal control key 語意，否則請優先使用 `CmdJob`。

#### `input` vs `key` vs `signal`

| 指令 | 做了什麼 | 適用對象 |
|---|---|---|
| `input <job> <text>` | 將文字寫入 stdin / PTY master（資料通道）| `CmdJob`、`PtyJob` |
| `key <job> <key>` | 將 terminal control byte 寫入 PTY master | 僅 `PtyJob` |
| `signal <job> <sig>` | 對 process group 發送 OS signal | `CmdJob`、`PtyJob` |

`key ctrl-c` 會寫入 `\x03` 到 PTY。因為 `PtyJob` 使用 `pty.fork()` 建立了正確的 controlling terminal，kernel 的 line discipline 會將這個 byte 轉換成對 child foreground process group 的真實 SIGINT，就像在真實終端機按下 Ctrl-C 一樣。

`signal SIGINT` 則是透過 `os.killpg()` 直接發送 signal，完全繞過 terminal driver。當你需要發送非 terminal key 對應的 signal（如 `SIGTERM`、`SIGUSR1`）時，使用 `signal`。

### 3. Hooks

Hook 由三部分構成：

- 條件 `when`
- 動作 `action`
- 觸發策略 `policy`（`once`、`always`、`every_n`）

常見條件：

- `Hook.log_matches(pattern)`
- `Hook.data_equals(key, value)`
- `Hook.elapsed_exceeds(seconds)`
- `Hook.on_start()`
- `Hook.on_done()`
- `Hook.on_fail()`
- `Hook.on_retry()`（context: `attempt`）
- `Hook.on_cancel()`（context: `skipped`）

常見動作：

- `Hook.action_kill()`
- `Hook.action_request_stop(force=False)`
- `Hook.action_send_signal(sig)`
- `Hook.action_send_input(text)`
- `Hook.action_send_key(key)`
- `Hook.action_emit(tag, message)`
- `Hook.action_set_data(key, value)`
- `Hook.action_chain(...)`

> [!WARNING]
> **避免在 Callback 中執行阻塞操作**
> Parser、hook action 以及任何同步執行的 callback（如 `on_log`、`on_done` 等）通常執行於 job worker thread 中。**絕對不要**在這些地方呼叫 `session.wait()`、`manager.wait()` 或長時間的 `sleep()` 等會等待整批 batch 完成的操作，否則會導致 Deadlock 或讓整個 batch 卡住。

> [!NOTE]
> **同步 Hook 連鎖觸發**
> Hook action 是同步反應的。若在 action 中使用 `Hook.action_set_data()` 更新資料，且該更新命中了其他的 `data_equals` hook，則後續的 hook 會在同一個 call stack 中立即連鎖觸發。請務必注意避免在使用 `policy="always"` 時造成無窮迴圈。

經驗法則：

- 如果你想對 log、parsed data、經過時間、Wave lifecycle 做反應，用 Wave hook
- **注意**：在 timer polling 機制下，`elapsed_exceeds` 若搭配 `policy="always"` 會重複觸發；多數場景建議使用 `policy="once"`。
- 如果你只是想在完成或失敗時收到簡單通知，用 job manager 原生 callback `on_done(...)` / `on_fail(...)`

### 4. Job 結果語意

Wave 會保留「失敗」與「刻意跳過」之間的小差異：

- `done`
  - job 正常成功
- `failed`
  - job 正常失敗
- `cancelled`
  - job 被取消或 force-stop
- `skipped`
  - 內部上仍是 cancelled，但 Wave 會額外標記 `is_skipped=True`

這會影響 summary 與 exit code：

- skipped job 不會讓整個 batch 失敗
- failed job 與非 skipped 的 cancelled job 會讓 batch 視為失敗

在 headless `status` 輸出裡，skipped job 會像這樣：

```text
ID       NAME                           STATUS       STATE            SKIPPED
-----------------------------------------------------------------------------
0f3a21b9 compile docs                   cancelled                      yes
```

---

## 常見工作流

### 從 Log 解析狀態

```python
job = CmdJob("sim", "python run_sim.py")

def parse_progress(line: str) -> dict:
    if "PROGRESS=" in line:
        return {"progress": line.split("=", 1)[1].strip()}
    return {}

job.add_parser(parse_progress)
session.add(job)
```

### 在 Parsed Data 命中時觸發 Hook

```python
job.add_hook(
    Hook(
        when=Hook.data_equals("progress", "done"),
        action=Hook.action_emit("phase", "simulation finished"),
        policy="once",
    )
)
```

### 優雅停止長時間執行的 Job

```python
import signal

job = CmdJob("interactive shell", "python interactive_tool.py")
job.set_stop_policy(
    graceful_input="exit\n",
    graceful_signal=signal.SIGINT,
    graceful_timeout=5.0,
)

session.add(job)
```

這樣 Wave 會先嘗試 graceful stop，不行再 fallback 到 force cancel。

### 做 Batch-Level 收尾

```python
from pathlib import Path

out_dir = Path("out")

def finalize(sess):
    summary = sess.summary()
    out_dir.mkdir(exist_ok=True)
    (out_dir / "summary.txt").write_text(
        f"outcome={summary['outcome']}\n"
        f"failed={','.join(summary['failed_names'])}\n",
        encoding="utf-8",
    )

session.on_finish(finalize)
```

這很適合用來整理 memo、搬 artifact、做最後報告。

### 用 Headless 模式執行並保有互動控制

```powershell
rpk-wave run path\to\my_wave.py --no-tui
```

如果 terminal 是互動式的，Wave 會打開 REPL。
如果 terminal 不是互動式的，就會直接跑到結束。

常見情況：

- 互動式 PowerShell / cmd / terminal 視窗：會打開 REPL
- CI、redirected stdin、pipe execution：不會開 REPL，直接跑完

---

## Wave File 撰寫教學

Wave file 是一個普通的 Python 檔案。Wave runner 會把它當模組載入並執行頂層程式碼。
執行完之後，所有已 `session.add()` 的 jobs 就會被排程。

> [!TIP]
> wave file 的職責是**宣告**和**設定**。所有長時間的工作都在 jobs 裡跑，不要把長邏輯放在 wave file 的頂層。

### 最小範本

```python
# my_wave.py
from rpkbin.wave import session, CmdJob

session.configure(max_workers=2)

session.add(CmdJob("hello", "echo Hello from Wave"))
session.add(CmdJob("world", "echo World"))
```

```powershell
# 啟動 TUI
rpk-wave run my_wave.py

# 或 headless
rpk-wave run my_wave.py --no-tui
```

### CmdJob + Parser + Hook

一個從 log 中解析進度，並在超時時自動停止的範例：

```python
# sim_wave.py
import re
from rpkbin.wave import session, CmdJob, Hook

session.configure(max_workers=4)

sim = CmdJob("simulation", "python run_sim.py --steps=10000")

# -- Parser：從 log 中擷取進度 --
# 假設 run_sim.py 輸出 "PROGRESS: 42.0%"
def parse_progress(line: str) -> dict:
    m = re.search(r"PROGRESS:\s*([\d.]+)%", line)
    if m:
        pct = float(m.group(1))
        sim.set_progress(pct)           # 更新 Dashboard 的進度條
        return {"progress_pct": m.group(1)}  # 寫入 parsed_data（可在 TUI DATA tab 看到）
    return {}

sim.add_parser(parse_progress)

# -- Hook：5 分鐘超時就強制停止 --
sim.add_hook(
    Hook(
        when=Hook.elapsed_exceeds(300),
        action=Hook.action_kill(),
        policy="once",
    )
)

# -- Hook：完成時發 event --
sim.add_hook(
    Hook(
        when=Hook.on_done(),
        action=Hook.action_emit("milestone", "simulation done"),
        policy="once",
    )
)

session.add(sim)
```

### FuncJob

`FuncJob` 用於跑 Python callable。它沒有 log 串流，所以主要透過 `emit()` 和 `update_parsed_data()` 回報狀態：

```python
# func_wave.py
import time
from rpkbin.wave import session, FuncJob

session.configure(max_workers=2)

def train_model(job):
    for epoch in range(10):
        time.sleep(1)
        job.set_progress((epoch + 1) * 10)
        job.update_parsed_data({"epoch": str(epoch + 1)})
        if job.is_cancelled:
            return
    job.emit("result", "training complete")

session.add(FuncJob("train", train_model))
```

> [!NOTE]
> `FuncJob` 的 callable 會收到 job 本身作為第一個參數。你可以用 `job.set_progress()`、`job.emit()`、`job.update_parsed_data()` 來回報狀態，用 `job.is_cancelled` 來檢查是否被取消。

### Resources、Priority、Tags

使用 `resources` 控制並行存取限制，`priority` 控制排程順序，`tags` 用於分組操作：

```python
# resource_wave.py
from rpkbin.wave import session, CmdJob

session.configure(
    max_workers=8,
    resources={"gpu": 2, "license": 1},   # 系統共有 2 GPU、1 license
)

# 這個 job 需要 1 GPU
build_a = CmdJob("build-A", "python build.py --variant=A",
                 resources={"gpu": 1}, priority=10, tags={"build"})

# 這個 job 需要 2 GPU（會獨佔所有 GPU）
build_b = CmdJob("build-B", "python build.py --variant=B",
                 resources={"gpu": 2}, priority=5, tags={"build"})

# 這個 job 需要 license
lint = CmdJob("lint", "python lint.py",
              resources={"license": 1}, tags={"check"})

session.add(build_a)
session.add(build_b)
session.add(lint)
```

- `priority` 越高越先被排程（預設 0）
- 若 job 宣告了 manager 不認識的 resource key，會收到 warning 且該 job 永遠不會被排程
- 在 TUI 或 REPL 裡可以用 `session.cancel_group("build")` 批量取消所有帶 `build` tag 的 jobs

### Retry

```python
# retry_wave.py
from rpkbin.wave import session, CmdJob, Hook

session.configure(max_workers=2)

flaky = CmdJob("flaky-test", "python test_flaky.py", max_retries=3)

# 在每次重試時記錄
flaky.add_hook(
    Hook(
        when=Hook.on_retry(),
        action=Hook.action_emit("retry", "retrying..."),
        policy="always",
    )
)

session.add(flaky)
```

`max_retries=3` 表示最多重試 3 次（加上原始嘗試，共執行最多 4 次）。
`job.retry_count` 可查看目前已重試幾次。

### Stop Policy（優雅停止）

對於需要清理動作的長期 job，設定 stop policy 讓 Wave 先嘗試溫和關閉：

```python
# stop_policy_wave.py
import signal
from rpkbin.wave import session, CmdJob

session.configure(max_workers=2)

server = CmdJob("server", "python my_server.py")
server.set_stop_policy(
    graceful_input="shutdown\n",     # 先送 stdin 指令
    graceful_signal=signal.SIGINT,   # 再送 SIGINT
    graceful_timeout=10.0,           # 等 10 秒；還沒停就 force-kill
)

session.add(server)
```

在 TUI 裡輸入 `stop server` 時，Wave 會依序執行上述步驟。

### Session Callbacks（Batch-Level 收尾）

在整批 jobs 跑完後執行收尾邏輯：

```python
# callback_wave.py
from pathlib import Path
from rpkbin.wave import session, CmdJob

session.configure(max_workers=2)

session.add(CmdJob("step1", "echo step1 done"))
session.add(CmdJob("step2", "echo step2 done"))

# on_finish：不論成功或失敗都會執行
def write_report(sess):
    summary = sess.summary()
    Path("report.txt").write_text(
        f"outcome={summary['outcome']}\n"
        f"done={summary['done']}, failed={summary['failed']}\n",
        encoding="utf-8",
    )

# on_done：只在所有 job 都成功時執行
def celebrate(sess):
    sess.emit("batch", "All jobs passed!")

# on_fail：只在有 job 失敗時執行
def alert(sess):
    names = sess.summary()["failed_names"]
    sess.emit("batch", f"Failed jobs: {', '.join(names)}")

session.on_finish(write_report)
session.on_done(celebrate)
session.on_fail(alert)
```

> [!WARNING]
> **不要**在 `on_finish` / `on_done` / `on_fail` 裡呼叫 `session.add()`，此時 session 已進入 finalize 階段。若需動態新增 job，請在 job-level callback（如 `on_done` hook）中且在 session 結束前進行。

### TUI 使用技巧

當你的 wave file 寫好之後，用 TUI 啟動來互動式監控：

```powershell
rpk-wave run my_wave.py
```

常用操作速查：

| 你想做什麼 | 做法 |
|---|---|
| 看所有 job 狀態 | `F1` 切到 Dashboard |
| 快速預覽某個 job 的 log | `F1` 中移動 Dashboard highlight，右側會顯示 log preview |
| 查看完整 job detail | 在 Dashboard 選 row → `Enter`；或輸入 `show <job>` / `logs <job>` |
| 切換 detail jobs | 在 JOB DETAIL 按 `[` / `]`；使用 `{` / `}` 切換 running jobs |
| 查看某個 job 的 parsed data | 輸入 `data <job>`；或進入 JOB DETAIL 後切到 DATA |
| 查看某個 job 的 events | 輸入 `events <job>`；或進入 JOB DETAIL 後切到 EVENTS |
| 暫停 / 恢復 dispatch | 輸入 `pause` / `resume`；running jobs 會繼續執行 |
| Graceful stop 某個 job | 在 command bar 輸入 `stop <job>` |
| Force-cancel 某個 job | 在 command bar 輸入 `cancel <job>` |
| 跳過 pending 的 job | 在 command bar 輸入 `skip <job>` |
| 看 help | `F4` 或輸入 `help` |
| 退出 | `Ctrl+C`（jobs 還在跑會警告，再按一次強制退出）|



---

## TUI（互動介面）

TUI 是在互動式 terminal 下執行 `rpk-wave run` 時的預設模式。
它以 [Textual](https://github.com/Textualize/textual) 建構，需要先安裝：`pip install -e .[wave]`。

### 版面配置

```
┌─ WAVE ─ my_flow.py ──────────── 2/8 running  ✓1 done  ●3 pending  ✗0 failed ─┐
│ [DASHBOARD] [JOB DETAIL] [SYSTEM LOG] [HELP]                                   │
│─────────────────────────────────────────────────────────────────────────────── │
│  Name          Status     Elapsed   Progress  Exit Code  Tags │ build [0f3a21b9]│
│▶ build         RUNNING    00:02:30   42%                  gpu │ INFO compile... │
│  lint          DONE       00:01:12             0              │ WARN retry...   │
│  test-unit     PENDING                                 test│                  │
├─ wave>  _                                                                      │
│ [F1] Dashboard  [F2] Job Detail  [F3] System Log  [Ctrl+C] Quit               │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 分頁說明

- **DASHBOARD** — 左側是所有 jobs 的即時表格，右側是目前 highlight job 的 log preview。
  - 預設表格欄位：Name / ID / Status / Elapsed / Progress / Retries / Exit Code / Tags。
  - 可在 wave file 透過 `session.configure_tui(...)` 自訂 Dashboard 欄位。
  - Running 中的 job 每秒更新 Elapsed。
  - 右側 log preview 會跟著 highlight row 變化，方便不用進入 JOB DETAIL 就快速掃不同 job 的 log。
  - 在任意 row 按下 Enter，可切換到 **JOB DETAIL** 查看該 job 的細節。
- **JOB DETAIL** — 單一特定 job 的全頁分割畫面。
  - Header：目前 job 名稱、位置、狀態、elapsed time、progress、可用時的 exit code，以及 detail navigation 提示。
  - 左邊 60%：串流 log 輸出（僅 append，不閃爍）。
  - 右邊 40%：五個子分頁：
    - **INFO** — job metadata 摘要，包含 id、status、state、skip flag 與 stop policy。
    - **DATA** — `job.peek_data()` 的 key/value 表格，以 upsert 方式更新；沒有資料時會顯示 `(no parsed data)`。
    - **EVENTS** — 使用者發送的事件（`source="user"`），依時間順序顯示。
    - **SYSTEM** — 系統發送的事件（`source="system"`），例如 `parser_error`、`hook_error`；parser/hook 錯誤會以紅色醒目顯示，並包含 exception detail。
    - **TERMINAL** — `PtyJob` 的 append-only PTY 輸出。非 PTY job 會顯示「不支援」的提示。這是 fake-terminal view，不是完整的 terminal emulator。
- **SYSTEM LOG** — session 層級的事件，以及所有 Command Bar 指令的輸出結果。
- **HELP** — 鍵盤快捷鍵與指令說明。

### 如何切換到 JOB DETAIL

1. **移動 DASHBOARD highlight** — 在 F1 右側預覽該 job 的 log。
2. **在 DASHBOARD row 按下 Enter** — 直接切換到 JOB DETAIL 並載入該 job。
3. **Command Bar** — 輸入 `show <job>`、`logs <job>`、`data <job>` 或 `events <job>`，可依名稱、完整 id 或唯一 id prefix 切換到任意 job。

### Dashboard 欄位

可以在 wave file 透過 `session.configure_tui(...)` 讓 Dashboard 顯示最符合目前 flow 的欄位：

```python
session.configure_tui(dashboard_columns=[
    "name",
    "status",
    {"label": "Final", "data": "FINAL_RESULT"},
    "exit_code",
    "tags",
])
```

內建欄位包含 `name`、`id`、`status`、`elapsed`、`progress`、`retries`、`exit_code`、`tags`。
Parsed-data 欄位使用 `{"label": "...", "data": "KEY"}`，會顯示 `job.peek_data()` 裡目前的值。
未知的內建欄位或格式錯誤的欄位設定會在 wave file 載入時直接丟出清楚的例外。

### Command Bar

在 **JOB DETAIL** 中，可按 `[` / `]` 切換上一個 / 下一個 job，或按 `{` / `}` 切換上一個 / 下一個 running job。Dashboard highlight 與 log preview 會跟著目前 detail job 同步。
在 TUI 模式中，`show <job>` / `logs <job>` / `data <job>` / `events <job>` 會直接開啟 **JOB DETAIL**；`show`、`data` 與 `events` 也會切到對應的 detail sub-tab。

底部的 `wave>` 輸入框無論在哪個分頁都可見。
它接受與 headless REPL 相同的所有指令（見下方）。
所有指令的 `print()` 輸出都會被導入 **SYSTEM LOG** 分頁，TUI 版面不會被打亂。

TUI 專屬快捷鍵：

| 鍵 | 動作 |
| --- | --- |
| `F1` | 切換到 DASHBOARD |
| `F2` | 切換到 JOB DETAIL |
| `F3` | 切換到 SYSTEM LOG |
| `F4` | 切換到 HELP |
| `F8` | 聚焦 Command Bar 並預填 `input . `（僅限 TERMINAL tab）|
| `F9` | 透過 PTY 對 job 發送 Ctrl-C（僅限 TERMINAL tab）|
| `F10` | 透過 PTY 對 job 發送 Ctrl-D（僅限 TERMINAL tab）|
| `:` | 聚焦 Command Bar（Vim 風格）|
| `Esc` | 離開 Command Bar，焦點回到目前面板 |
| `Enter`（在 DASHBOARD row 上）| 開啟該 job 的 JOB DETAIL |
| `[` / `]`（在 JOB DETAIL）| 切換上一個 / 下一個 job |
| `{` / `}`（在 JOB DETAIL）| 切換上一個 / 下一個 running job |
| `Tab`（在 Command Bar）| 自動完成指令和 job name |
| `↑` / `↓`（在 Command Bar）| 瀏覽指令歷史 |
| `Shift + 拖曳` | 選取文字（右鍵或 Ctrl+Shift+C 複製，**不要用 Ctrl+C**）|
| `Ctrl+C` | 退出（若 jobs 還在執行中會提示，3 秒內再按一次強制退出）|

### Job 狀態顏色

| 狀態 | 顏色 |
| --- | --- |
| RUNNING | 綠色 |
| DONE | 青色 |
| PENDING | 黃色 |
| FAILED | 紅色 |
| CANCELLED | 灰色 |

---

## Headless REPL

Headless REPL 是給執行中 batch 用的較佳輸入介面。
它不是第二套執行模型；jobs 仍然走同一條 `session -> manager -> job_manager` 路徑。

當 jobs 還在跑時，你可以檢查與控制它們。
當所有 jobs 都完成時，REPL 會印出 summary，並保持開啟，讓你看完結果後再用 `exit` 離開。

### 指令

- `help`
  - 顯示可用指令
- `status`
  - 顯示所有 jobs 的表格狀態
- `show <job>`
  - 顯示單一 job 的摘要，包含 exit code、短錯誤訊息，以及 parser/hook error 計數
- `logs <job> [n]`
  - 顯示最後 `n` 行 log，預設 `50`
- `data <job>`
  - 顯示 `parsed_data`
- `events <job>`
  - 顯示 job event history
- `pause`
  - 暫停 pending jobs 的 dispatch；running jobs 會繼續執行
- `resume`
  - 在 `pause` 之後恢復 dispatch
- `stop <job>`
  - 優先 graceful stop，逾時後再 force-stop
- `stop -g <job>` / `stop --graceful <job>`
  - 只做 graceful stop
- `stop -f <job>` / `stop --force <job>`
  - 立即 force stop
- `stop --all`
  - 對所有 active jobs 請求 graceful stop
- `stop --group <tag>`
  - 對帶有 `tag` 的 active jobs 請求 graceful stop
- `cancel <job>`
  - 立即 force-cancel 單一 active job
- `cancel --all`
  - 立即 force-cancel 所有 active jobs
- `cancel --group <tag>`
  - 立即 force-cancel 帶有 `tag` 的 active jobs
- `skip <job>`
  - 跳過 pending 的 Wave job
- `input <job> <text>`
  - 對 running job 寫入 stdin（支援 `\n`, `\r`, `\t` 等 escape sequences）
- `signal <job> <sig>`
  - 對 running job 發送 OS signal
- `watch status`
  - 輪詢並重印 status，直到 `Ctrl+C`
- `watch logs <job> [n]`
  - 輪詢並重印 log tail，直到 `Ctrl+C`
- `exit`
  - 只有在沒有 active jobs 時才會離開 REPL
- `exit --stop`
  - 對 active jobs 請求 stop，然後離開 REPL
- `exit --force`
  - 強制取消 active jobs，然後離開 REPL

`<job>` 可以是唯一的 job name、完整 job id，或 `status` / `show` 顯示的唯一 id prefix。
在 TUI 模式中，如果目前已開啟 JOB DETAIL，還可以使用特殊的 `.` 代稱目前選取的 job。
如果多個 jobs 使用同一個 name，Wave 會拒絕模糊名稱，並要求你改用 id。
模糊名稱提示會列出每個符合 job 的完整 id 與狀態，因此可以直接複製完整 id，或使用 8 個字元以上的唯一 prefix。

名稱含空白時必須加引號：

```text
logs "test suite"
stop -g "sim run 1"
input "interactive shell" "exit\n"
```

### Watch 行為

`watch` 是刻意做得比較保守的：

- 使用 polling
- 目前刷新間隔固定約 1 秒
- 不做複雜 terminal redraw
- 按 `Ctrl+C` 只會停止 watch，並回到 REPL

這樣可以讓實作保持小而穩定。

### 離開 REPL

Wave 對 REPL 的退出行為刻意保持明確：

- `exit`
  - 只有在沒有 active jobs 時才會成功
- `exit --stop`
  - 先對 active jobs 請求 stop，再離開 REPL
- `exit --force`
  - 直接 force-cancel active jobs，再離開 REPL

目前故意不提供 detach/background mode。
這樣可以避免 jobs 在使用者不知情的情況下繼續執行，也讓 process ownership 保持簡單。

離開 REPL 之後，Wave 仍然會完成正常的 session shutdown，命令才會返回。

---

## Stop Policy 語意

Wave 支援 per-job stop policy，對互動式工具特別有用：

```python
import signal

# CmdJob：用 stdin + OS signal
job.set_stop_policy(
    graceful_input="exit\n",
    graceful_signal=signal.SIGINT,
    graceful_timeout=5.0,
)

# PtyJob：用 terminal control key（預設 ctrl-c）
pty_job.set_stop_policy(
    graceful_key="ctrl-c",       # terminal key → kernel SIGINT
    graceful_timeout=5.0,
)
```

意思是：

- `graceful_key`
  - 寫入 PTY master 的 terminal control key（僅 PtyJob）
  - 例如 `"ctrl-c"` → `\x03` → 透過 line discipline 產生 kernel SIGINT
- `graceful_input`
  - 送到 stdin / PTY master 的文字
- `graceful_signal`
  - graceful shutdown 過程中送出的 OS signal
- `graceful_timeout`
  - 如果過了這段時間 job 還在跑，Wave 就 fallback 到 force cancel

設定了多個步驟時，Wave 會依序執行：

1. 送 terminal key（如果設定了 `graceful_key`；僅 PTY jobs）
2. 送 input（如果設定了 `graceful_input`）
3. 送 signal（如果設定了 `graceful_signal`）
4. 等 graceful timeout
5. 如果 job 還在跑，就 force-cancel

每個步驟不管前一個是否成功都會嘗試。如果某個步驟失敗（如 PTY 不可用），會 log 錯誤後繼續下一步。

對應指令行為：

- `stop <job>`
  - 有 stop policy 時先走 graceful
  - timeout 後再 force
- `stop -g <job>`
  - 只做 graceful
  - 不會自動 force
- `stop -f <job>`
  - 直接 force cancel

如果 job 沒有 graceful stop 能力：

- `stop <job>` 會退回 force stop
- `stop -g <job>` 會提示 unsupported

---

## API 速查

### Session

| 方法 | 說明 |
| --- | --- |
| `session.configure(max_workers=..., resources=..., log_dir=..., timeout=...)` | 在 session 開始前設定 Wave session。`timeout` 為整份 batch 的時限。 |
| `session.configure_tui(dashboard_columns=...)` | 設定 TUI 顯示方式。Dashboard 欄位支援內建欄位與 parsed-data 欄位，例如 `{"label": "Final", "data": "FINAL_RESULT"}`。 |
| `session.add(job, timeout=None)` | 註冊 job；若 session 已啟動，會立即 dispatch。此 `timeout` 僅對 Wave job 生效；若傳入 plain scheduler job，實作會發出 warning 且不生效。**注意**：若在 session finalize 後呼叫會拋出 `RuntimeError`。 |
| `session.emit(tag, message)` / `session.peek_events()` | 新增與查看 batch-level 事件。使用者建立的事件會標記 `source="user"`；Wave 內建 lifecycle 事件會標記 `source="system"`。 |
| `session.pause()` / `session.resume()` | 暫停或恢復 job manager 的排程循環。 |
| `session.cancel_group(tag)` | 取消所有 active 與 pending 且帶有 *tag* 標籤的 jobs。 |
| `session.wait(timeout=None, *, job=None)` | 等待整批收斂或特定 job 完成。 |
| `session.on_finish(cb)` | 在整個 batch 完全收斂後執行一次 `cb(session)`。 |
| `session.on_done(cb)` / `session.on_fail(cb)` | 在 batch 成功或失敗完成時各執行一次 `cb(session)`（單次 session 範圍；`reset()` 會清掉）。 |
| `session.summary()` | 回傳 batch-level 摘要快照，包含 counts、names、outcome 與 exit code。 |
| `session.jobs()` | 回傳所有已知 jobs。 |
| `session.running()` / `session.pending()` / `session.done()` | 依狀態回傳 job 快照。 |
| `session.failed(include_skipped=False)` | 回傳 failed jobs 與非 skipped 的 cancelled jobs。 |
| `session.skipped()` | 回傳刻意 skip 的 jobs。 |
| `session.reset()` | 將共享 session 重置成乾淨狀態。 |

### Wave Jobs

| 屬性 / 方法 | 說明 |
| --- | --- |
| `parsed_data` / `peek_data()` | 從 log 解析出的結構化狀態。 |
| `events` / `peek_events()` | job 事件歷史。 |
| `add_parser(fn)` | 註冊每行 log 都會呼叫的 parser。 |
| `add_hook(hook)` | 註冊 Wave hook。 |
| `emit(tag, message, source="user")` | 在 job event stream 中加入事件。`source` 預設為 `"user"`，表示應用程式程式碼發送的事件；Wave 內部在 parser 或 hook 失敗時會以 `source="system"` 發送 `parser_error` / `hook_error`。在 TUI JOB DETAIL 的 Events / System 子分頁中可見。 |
| `skip()` | 跳過 pending 的 Wave job。 |
| `is_skipped` | 這個 job 是否為刻意 skip。 |
| `tags` | 與 job 關聯的標籤組合。 |
| `set_progress(value)` | 手動更新 job 進度 (0-100)。 |
| `retry_count` | 目前已重試的次數。 |
| `set_stop_policy(...)` | 設定 graceful key / input / signal stop 行為。 |

### PtyJob 專屬 API

| 屬性 / 方法 | 說明 |
| --- | --- |
| `supports_pty` | `PtyJob` / `PtyCmdJob` 為 `True`。TUI 用此判斷是否啟用 TERMINAL 分頁。 |
| `send_key(key)` | 對 PTY 送 terminal control key（如 `"ctrl-c"`、`"ctrl-d"`）。 |
| `send_input(text)` | 將文字寫入 PTY master fd（資料通道）。 |
| `send_signal(signum)` | 對 process group 發送 OS signal。 |

### CLI

| 指令 | 說明 |
| --- | --- |
| `rpk-wave run <wave_file>` | 以 TUI 執行 wave file（互動式 terminal 下的預設行為）。 |
| `rpk-wave run <wave_file> --no-tui` | 用 headless 模式執行；若 stdin 是互動式的就開 REPL。 |
| `rpk-wave run <wave_file> --workers N` | 覆寫 wave file 內的 `max_workers`。 |

---

## Exit Code

`rpk-wave run ...` 會依 batch 結果回傳 shell-style exit code。

- `0`
  - 所有 jobs 都成功完成
  - 或只有被刻意 skipped 的 jobs
- `1`
  - 至少有一個 job failed
  - 或有非 skipped 的 job 以 cancelled 結束

Parser output 與 emitted events 不會影響 process exit code。

---

## Batch-Level Events 與 Summary

Wave 現在有兩層事件：

- job events
  - 由 `job.emit(...)` 記錄
  - 代表單一 job 的重要事件
- session events
  - 由 `session.emit(...)` 記錄
  - 代表整個 batch 的重要事件

Session event 常見用途：

- 記錄最後報告的位置
- 記錄某個 cleanup step 已執行
- 記錄 batch-level 的決策或 fallback

`session.summary()` 則是 batch-level 收尾邏輯常用的讀取 API。
它會回傳穩定的摘要快照，常見欄位有：

- `outcome`
- `exit_code`
- `done`、`failed`、`cancelled`、`skipped`
- `done_names`、`failed_names`、`skipped_names`
- `duration_s`

---

## FAQ 與 Troubleshooting

### 執行與環境

| 問題 | 原因與解法 |
|---|---|
| `--no-tui` 沒有打開 REPL | REPL 只會在 stdin 是互動式時打開。CI、pipe、redirected execution 會直接跑完。 |
| TUI 啟動失敗 | 確認已安裝 `pip install -e .[wave]`（需要 `textual`）。 |
| `exit` 不讓我離開 | 還有 active jobs。改用 `exit --stop` 或 `exit --force`。 |

### Parser 與 Hook

| 問題 | 原因與解法 |
|---|---|
| Parser 沒有觸發 | 確認 job 是 `CmdJob`、log 行確實命中、且 parser 回傳了非空 `dict`。 |
| Hook 或 parser 的錯誤去哪看？ | Wave 會發送 `parser_error` / `hook_error` 事件，內容包含 exception type、message、輸入/action context 與短 traceback。可查看 `job.peek_events()`、`show <job>`，或 TUI 的 SYSTEM sub-tab。 |
| `stop -g` 說 unsupported | 該 job 沒有設定 `set_stop_policy(...)`。 |

### 結果與 Exit Code

| 問題 | 原因與解法 |
|---|---|
| 手動 stop 了 job，exit code 還是 `1` | 確認是否有 job 的 status 為 `failed`（不是 `cancelled`）。使用者主動取消的 job 不再計入 exit code。 |
| Job name 有空白時指令失敗 | REPL 使用 shell-style parsing，需加引號：`logs "test suite"`。 |
| 可以在 REPL 裡定義新 job 嗎？ | 不行。Jobs 在 wave file 裡宣告；REPL 負責 inspection 與 control。 |

---

## 建議用法

Wave 最適合在以下方式下使用：

- jobs 先在 wave file 裡定義好
- 自動反應用 hooks
- 從 log 擷取狀態用 parser
- 執行中需要檢查與控制時，用 TUI 或 headless REPL
- 互動指令保持簡單、明確

這樣可以讓 code path 維持可理解，也避免把 CLI 變成半套 shell 或臨時 job authoring 環境。
