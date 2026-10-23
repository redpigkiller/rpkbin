# Wave 由淺入深的 Python 使用情境

[English](../../en/wave/scenarios.md) · [文件導覽](../README.md) · [API 參考](reference/python.md)

本文件透過 8 個由淺入深的實務使用情境，示範如何以 Python 定義工作、設定驗收規則、監控程序與處理異常。所有範例皆為通用且可直接執行的程式碼。

---

## 目錄

1. [情境一：最小單一工作與輸出驗收](#情境一最小單一工作與輸出驗收)
2. [情境二：多工作並行限制與優先權排程](#情境二多工作並行限制與優先權排程)
3. [情境三：多重 Check 驗收與 Result 判定](#情境三多重-check-驗收與-result-判定)
4. [情境四：從輸出中擷取動態數值 (Track)](#情境四從輸出中擷取動態數值-track)
5. [情境五：警告 (Warning)、事件監聽與自訂 Python 動作](#情境五警告-warning事件監聽與自訂-python-動作)
6. [情境六：互動命令：偵測提示字元、送文字與停止策略](#情境六互動命令偵測提示字元送文字與停止策略)
7. [情境七：資源容量配額 (Resources)、超時與自動重試](#情境七資源容量配額-resources超時與自動重試)
8. [情境八：Attempt 執行歷史、有界日誌保留與手動重跑](#情境八attempt-執行歷史有界日誌保留與手動重跑)

---

## 情境一：最小單一工作與輸出驗收

### 使用者想完成什麼
執行一個外部指令，確保其輸出包含預期的完成標記（如 `SIMULATION_SUCCESS`），且輸出中不能出現錯誤字眼（如 `FATAL:`）。若條件皆滿足且程序退出碼為 0，驗收結果才判定為 `PASS`。

### 完整可執行範例 (`scenario1.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager()

# 建立單一工作
job = mgr.add(Job(
    name="single_check",
    # 使用 Python 內建指令模擬產生正常輸出
    cmd=[sys.executable, "-u", "-c", "print('STARTING'); print('SIMULATION_SUCCESS'); print('FINISHED')"],
))

# 1. 期望條件：必須出現 SIMULATION_SUCCESS
job.check("success_marker", match=r"^SIMULATION_SUCCESS$", expect=Expect.PRESENT)

# 2. 禁止條件：不能出現 FATAL 開頭的訊息
job.check("no_fatal", match=r"^FATAL:", expect=Expect.ABSENT)
```

### 執行指令
```bash
rpk-wave scenario1.py --headless
```

### 實際輸出結果
```text
[Wave] Starting 1 job(s); workers=4
[child single_check #1] STARTING
[child single_check #1] SIMULATION_SUCCESS
[child single_check #1] FINISHED

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
single_check               DONE            PASS         0         0
  checks: success_marker: OK; no_fatal: OK
```

### 結果解釋
- `expect=Expect.PRESENT`：在程序執行期間，只要有一行完全命中 regex，該 check 狀態立即定案為 `OK`；若程序結束時從未命中，則為 `FAIL`。
- `expect=Expect.ABSENT`：在執行期間只要有任何一行命中，該 check 立即定案為 `FAIL`；若程序正常結束且從未命中，則定案為 `OK`。
- 當所有 checks 皆為 `OK` 且 exit code 為 0，最終 `Result` 定案為 `PASS`。

---

## 情境二：多工作並行限制與優先權排程

### 使用者想完成什麼
同時提交 4 個耗時工作，但受限於本機 CPU 核心數，最多只允許 2 個工作同時運行（`max_workers=2`）。其中關鍵工作（如回歸驗證的核心測試）希望優先取得空出的執行槽位。

### 完整可執行範例 (`scenario2.py`)

```python
import sys
from rpkbin.wave import Job, JobManager

# 設定最大並行工作數為 2
mgr = JobManager(max_workers=2)

# 一般工作：預設 priority=0
for i in (1, 2, 3):
    mgr.add(Job(
        f"routine_job_{i}",
        [sys.executable, "-u", "-c", f"import time; print('Routine {i} working'); time.sleep(0.4); print('Done {i}')"],
        priority=0
    ))

# 關鍵工作：設定較高的 priority=10
# 雖然最後才被加入管理器，但在佇列啟動排序時會享有最高優先權
mgr.add(Job(
    "critical_job",
    [sys.executable, "-u", "-c", "import time; print('CRITICAL TASK running'); time.sleep(0.2); print('CRITICAL Done')"],
    priority=10
))
```

### 執行指令
```bash
rpk-wave scenario2.py --headless
```

### 實際輸出結果
```text
[Wave] Starting 4 job(s); workers=2
[child critical_job #1] CRITICAL TASK running
[child routine_job_1 #1] Routine 1 working
[child critical_job #1] CRITICAL Done
[child routine_job_1 #1] Done 1
[child routine_job_2 #1] Routine 2 working
[child routine_job_3 #1] Routine 3 working
[child routine_job_2 #1] Done 2
[child routine_job_3 #1] Done 3

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
routine_job_1              DONE            N/A          0         0
routine_job_2              DONE            N/A          0         0
routine_job_3              DONE            N/A          0         0
critical_job               DONE            N/A          0         0
```

### 結果解釋
- 排程器在派發待命工作時，Pending 佇列先依 `(-priority, order)` 排序。雖然 `critical_job` 最晚被加入管理器，但因其優先權較高（`priority=10`），在初始派發的第一批 2 個工作名額中，立即與 `routine_job_1`（`priority=0`，加入順序第一）優先啟動執行。
- `critical_job` 耗時 0.2 秒先行結束並釋出 worker 槽位後，排程器隨即啟動佇列中的 `routine_job_2`；待 `routine_job_1` 結束後再啟動 `routine_job_3`。
- 本例中未宣告 checks，因此所有正常結束的工作其 `Result` 皆為 `N/A`（表示沒有檢驗規則，而非失敗）。

---

## 情境三：多重 Check 驗收與 Result 判定

### 使用者想完成什麼
精確理解 **程序退出碼（State）** 與 **業務驗收結果（Result）** 的差別。示範兩種常見情況：
1. 子程序 exit code 為 0，但漏印了必要標記（Result 應判定為 `FAIL`）。
2. 子程序 exit code 為非 0，即使輸出包含特定字串，Result 依然為 `FAIL`。

### 完整可執行範例 (`scenario3.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager(max_workers=2)

# 工作 A：子程序正常退出 (exit 0)，但未輸出必要標記 PASS_TOKEN
job_a = mgr.add(Job(
    "silent_failure",
    [sys.executable, "-u", "-c", "import sys; print('Initialization complete'); sys.exit(0)"]
))
job_a.check("token", match=r"^PASS_TOKEN$", expect=Expect.PRESENT)

# 工作 B：子程序遇到嚴重錯誤並以 code 2 崩潰退出
job_b = mgr.add(Job(
    "crash_exit",
    [sys.executable, "-u", "-c", "import sys; print('Fatal memory fault'); sys.exit(2)"]
))
# 禁止出現 Fatal
job_b.check("no_fatal", match=r"Fatal", expect=Expect.ABSENT)
```

### 執行指令
```bash
rpk-wave scenario3.py --headless
```

### 實際輸出結果
```text
[Wave] Starting 2 job(s); workers=2
[child silent_failure #1] Initialization complete
[child crash_exit #1] Fatal memory fault

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
silent_failure             DONE            FAIL         0         0
  checks: token: FAIL
  check token: FAIL; required condition never matched
crash_exit                 FAILED          FAIL         0         2
  checks: no_fatal: FAIL
  check no_fatal: FAIL; forbidden condition matched #1: Fatal memory fault
```

### 結果解釋
- `silent_failure` 的程序狀態為 `DONE`（exit 0），但因宣告的 check `token` 從未命中，Result 被正確判定為 `FAIL`。
- `crash_exit` 的程序狀態為 `FAILED`（exit 2），且禁用的正則命中，證據顯示於第 1 行。
- 終端機指令最終退出碼為 `1`（因為批次中存在 FAIL 的工作）。

---

## 情境四：從輸出中擷取動態數值 (Track)

### 使用者想完成什麼
從長期的輸出日誌中動態擷取關鍵指標（例如循環進度 `cycle=15000` 或誤碼率 `ber=0.002`），將字串轉型為整數或浮點數，供 GUI 表格欄位顯示或後續邏輯判定。

### 完整可執行範例 (`scenario4.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager(columns=["cycle", "pass_rate"])

job = mgr.add(Job(
    "dsp_simulation",
    [sys.executable, "-u", "-c", """
import time
for c in (100, 200, 300):
    print(f'cycle={c} pass_rate=99.{c//100}', flush=True)
    time.sleep(0.1)
print('SIMULATION_DONE')
"""]
))

# track 的正則必須恰好包含一個捕獲括號 ()
# cast 參數指定轉型函式，轉型後的數值會即時反映於 job.data[name]
cycle_track = job.track("cycle", r"cycle=(\d+)", cast=int)
rate_track = job.track("pass_rate", r"pass_rate=([0-9.]+)", cast=float)

# 可以針對 track 的數值變化宣告 check
# 這裡使用自訂 callable 檢查最終 cycle 是否達到 300
job.check(
    "reach_target_cycle",
    match=lambda job, old, new: new >= 300,
    on=cycle_track.events.changed,
    expect=Expect.PRESENT
)
job.check("completed", match=r"^SIMULATION_DONE$", expect=Expect.PRESENT)
```

### 執行指令
```bash
rpk-wave scenario4.py --headless
```

### 實際輸出結果
```text
[Wave] Starting 1 job(s); workers=4
[child dsp_simulation #1] cycle=100 pass_rate=99.1
[child dsp_simulation #1] cycle=200 pass_rate=99.2
[child dsp_simulation #1] cycle=300 pass_rate=99.3
[child dsp_simulation #1] SIMULATION_DONE

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
dsp_simulation             DONE            PASS         0         0
  checks: reach_target_cycle: OK; completed: OK
```

### 結果解釋
- `job.track` 回傳一個 `Track` 欄位物件。每次正則命中時，Wave 會將捕獲字串送入 `cast` 轉型，並寫入當次 attempt 的 `data` 字典中。
- 在 GUI 中，這些欄位會直接作為自訂欄位出現在工作清單中。
- `track.events.changed` 事件提供 `(old_value, new_value)`，可做為 check 或 on 的觀察來源。

---

## 情境五：警告 (Warning)、事件監聽與自訂 Python 動作

### 使用者想完成什麼
偵測特定文字並記錄為非致命的警告（Warning，不影響 Result=PASS）；或在特定事件發生時執行自訂 Python 函式（如寫入稽核紀錄、向另一個工作發送協調信號）。

### 完整可執行範例 (`scenario5.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Event, Expect

mgr = JobManager()

job = mgr.add(Job(
    "audit_job",
    [sys.executable, "-u", "-c", """
print('WARN: deprecation notice on line 1')
print('WARN: deprecation notice on line 2')
print('MILESTONE_REACHED')
print('ALL_PASSED')
"""]
))

# 1. 記錄警告：warn=True 不會中斷程序，也不會將 Result 改為 FAIL
# once=False 表示每次命中該 pattern 都累積一次計數
job.on(
    Event.LOG,
    match=r"^WARN:",
    warn=True,
    message="偵測到廢棄警告",
    name="deprecation",
    once=False
)

# 2. 自訂回呼動作：當特定里程碑出現時執行 Python 函式
# 注意：callback 與 warn/stop 為二選一
def on_milestone(bound_job, line):
    # bound_job 代表該次 attempt 綁定的 Job 代理物件
    bound_job.log(f"Custom auditor triggered by line: {line.strip()}")

job.on(Event.LOG, match=r"^MILESTONE_REACHED$", callback=on_milestone, name="milestone_hook")

job.check("finish", match=r"^ALL_PASSED$", expect=Expect.PRESENT)
```

### 執行指令
```bash
rpk-wave scenario5.py --headless
```

### 實際輸出結果
```text
[Wave] Starting 1 job(s); workers=4
[child audit_job #1] WARN: deprecation notice on line 1
[child audit_job #1] WARN: deprecation notice on line 2
[child audit_job #1] MILESTONE_REACHED
[Wave audit_job #1] Custom auditor triggered by line: MILESTONE_REACHED
[child audit_job #1] ALL_PASSED

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
audit_job                  DONE            PASS         2         0
  checks: finish: OK
  warning: deprecation (2 match(es))
```

### 結果解釋
- `deprecation` 警告累計了 2 次命中，顯示於摘要的 WARNINGS 欄位，但整體 Result 依舊為 `PASS`。
- 自訂回呼執行的 `bound_job.log(...)` 會以 `[Wave job_name]` 來源記錄於該工作的輸出佇列中。
- 回呼函式的第一個參數為 `bound_job`，其操作均嚴格綁定該次 attempt，避免在 rerun 換輪時誤操作新一輪。

---

## 情境六：互動命令：偵測提示字元、送文字與停止策略

### 使用者想完成什麼
管理一個具備互動 CLI（例如 Python REPL、EDA shell 或 GDB）的程序。程序啟動後印出提示字元 `cmd> ` 等待輸入。Wave 偵測到提示後自動發送指令，最後在需要終止時透過既定策略優雅結束。

### 完整可執行範例 (`scenario6.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Event, StopPolicy

mgr = JobManager()

# 模擬一個互動式 Shell 程序
interactive_script = """
import sys
print('ready> ', end='', flush=True)
for line in sys.stdin:
    cmd = line.strip()
    print(f'ACK: {cmd}', flush=True)
    if cmd == 'exit':
        print('BYE', flush=True)
        break
    print('ready> ', end='', flush=True)
"""

job = mgr.add(Job(
    "interactive_cli",
    [sys.executable, "-u", "-c", interactive_script],
    # 宣告預期的提示字元 tokens（字面比對）
    prompts=("ready> ",),
    # 停止策略：優先使用 stop_cmd 送出優雅結束命令；若逾時 2 秒未退出則強制 kill
    stop_policy=StopPolicy.GRACEFUL_THEN_FORCE,
    stop_cmd="exit",
    stop_timeout=2.0
))

# 當日誌出現提示字元時，自動送出測試指令
def handle_ready(bound_job, line):
    # send 預設會補換行符號 (\n)
    success = bound_job.send("status_check")
    # send 回傳值僅代表成功寫入管道，不代表外部工具已完成執行
    bound_job.log(f"Send status: {success}")

job.on(Event.LOG, match=r"ready>", callback=handle_ready, once=True)

# 偵測到 ACK 後要求優雅停止工作
job.on(Event.LOG, match=r"ACK: status_check", stop=True, message="互動測試完成")
```

### 執行指令
```bash
rpk-wave scenario6.py --headless
```

### 實際輸出結果
```text
[Wave] Starting 1 job(s); workers=4
[child interactive_cli #1] ready>
[Wave interactive_cli #1] Input sent: status_check
[Wave interactive_cli #1] Send status: True
[child interactive_cli #1] ACK: status_check
[child interactive_cli #1] ready>
[child interactive_cli #1] ACK: exit
[child interactive_cli #1] BYE

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
interactive_cli            CANCELLED       INCOMPLETE   0         0
  stopped: monitor: 互動測試完成
```

### 結果解釋
- `prompts=("ready> ",)`：使程序在輸出停在未換行的 `ready> ` 時，GUI 狀態能即時標示為 `WAITING_INPUT`。
- `job.send(...)`：執行標準輸入傳輸，日誌會先記錄 `Input sent: ...`，隨後回呼函式接續輸出 `Send status: True`。
- 停止策略觸發時，Wave 依照 `stop_cmd="exit"` 向 stdin 送出退出指令，程序隨後順利結束。因工作是被主動停止的，其未判定的檢查將標為 `INCOMPLETE`，狀態為 `CANCELLED`。

---

## 情境七：資源容量配額 (Resources)、超時與自動重試

### 使用者想完成什麼
管理需要特定硬體資源（如 GPU、授權 Token）的工作，避免多個工作同時耗盡資源。當工作遇到偶發性失敗（非零退出）時自動重試，並為每個工作設定執行時間上限（`timeout`）。

### 完整可執行範例 (`scenario7.py`)

```python
import sys
import atexit
import tempfile
from pathlib import Path
from rpkbin.wave import Job, JobManager, Expect

# 宣告全局可用資源容量：有 2 張 GPU 授權
mgr = JobManager(max_workers=3, resources={"license_gpu": 2})

# 每次載入建立獨立目錄；同一批的 retry 共用計數檔，其他批次不會互相重置。
# atexit 保持目錄存活到核心結束，結束時清理；不把檔案留在使用者工作目錄。
_counter_dir = tempfile.TemporaryDirectory(prefix="wave-retry-")
atexit.register(_counter_dir.cleanup)
counter_file = Path(_counter_dir.name) / "counter.txt"

# 透過計數檔案模擬偶發失敗並在重試時通過
flakey_script = f"""
import sys
from pathlib import Path

p = Path({str(counter_file)!r})
count = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(count))

print(f"Running with 2 GPUs... Attempt #{{count}}")
if count < 2:
    print("Hardware glitch: simulating flakey exit")
    sys.exit(1)
print("SUCCESS_RUN")
"""

job = mgr.add(Job(
    "heavy_sim",
    [sys.executable, "-u", "-c", flakey_script],
    # 宣告此工作需要佔用 2 個 license_gpu
    resources={"license_gpu": 2},
    # 允許最多重試 1 次（非零退出時自動重試）
    max_retries=1,
    # 單次執行超時上限為 5 秒
    timeout=5.0
))
job.check("success", match=r"^SUCCESS_RUN$", expect=Expect.PRESENT)
```

### 執行指令
```bash
rpk-wave scenario7.py --headless
```

### 實際輸出結果
```text
[Wave] Starting 1 job(s); workers=3
[child heavy_sim #1] Running with 2 GPUs... Attempt #1
[child heavy_sim #1] Hardware glitch: simulating flakey exit
[child heavy_sim #2] Running with 2 GPUs... Attempt #2
[child heavy_sim #2] SUCCESS_RUN

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
heavy_sim                  DONE            PASS         0         0
  checks: success: OK
  latest attempt: #2; earlier history retained: 1
```

### 結果解釋
- **資源釋放與重新排隊**：當 Attempt #1 以 code 1 失敗時，Wave 會**先完整收尾、排空日誌並釋放佔用的 2 個 `license_gpu` 配額**，接著產生全新的 Attempt #2 並重新加入佇列尾端，確保排程的公平性。
- Attempt #2 順利印出 `SUCCESS_RUN` 並退出 0，最終結果判定為 `PASS`。
- 摘要中清楚記錄：`latest attempt: #2; earlier history retained: 1`。

---

## 情境八：Attempt 執行歷史、有界日誌保留與手動重跑

### 使用者想完成什麼
理解 Wave 如何透過有界緩衝區（`max_log_lines`）防止記憶體爆炸，同時妥善保存關鍵判定證據；以及在工作結束後進行手動重跑（Rerun）時，舊輪次的歷史資料如何被安全隔離。

### 完整可執行範例 (`scenario8.py`)

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

mgr = JobManager(max_history=5)

job = mgr.add(Job(
    "spammy_job",
    [sys.executable, "-u", "-c", """
print('CRUCIAL_HEADER_PASS')
for i in range(50):
    print(f'Spam line {i}')
print('FINISHED')
"""],
    # 為了示範，將記憶體中保留的 log 行數設為 10 行（預設為 10000）
    max_log_lines=10
))

# 宣告檢查首行的特定標頭
job.check("header", match=r"^CRUCIAL_HEADER_PASS$", expect=Expect.PRESENT)
```

### 執行指令
```bash
rpk-wave scenario8.py --headless
```

### 實際輸出結果
```text
[Wave] Starting 1 job(s); workers=4
[child spammy_job #1] CRUCIAL_HEADER_PASS
[child spammy_job #1] Spam line 0
...
[child spammy_job #1] Spam line 49
[child spammy_job #1] FINISHED

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
spammy_job                 DONE            PASS         0         0
  checks: header: OK
```

### 結果解釋
- **有界日誌與獨立證據保存**：即使日誌總行數（52 行）遠超過 `max_log_lines=10`，較早的行已被環形緩衝區淘汰，但 `header` check 在命中當下所保存的行號（`#1`）與證據文字依然被獨立保存在 check 報告中，不會因日誌被逐出而丟失證據或改判。
- **手動重跑（Rerun）的隔離性**：在 GUI 中點擊 Rerun 或於 Python 呼叫 `mgr.rerun(job)` 時，舊的 Attempt #1 會被凍結並存入歷史紀錄（受 `max_history` 限制），新啟動的 Attempt #2 會擁有全新的日誌與狀態。使用者在檢視歷史紀錄時，所有控制按鈕與 stdin 輸入列均自動處於唯讀禁用狀態，保證不會誤操作到新一輪。
