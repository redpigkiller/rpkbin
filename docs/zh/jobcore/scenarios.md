# `rpkbin.jobcore` 實戰使用情境

[English](../../en/jobcore/scenarios.md) · [文件導覽](../README.md) · [快速入門](README.md) · [API 參考手冊](reference.md)

以下六個獨立且可執行的情境示範資源配額與排程模式、佇列編輯、互動式提示與防護控制、重試與判決優先序、log 留存與嚴格解碼，以及直接使用 `ProcessHandle`。

## 目錄

1. [情境 1：資源配額與排程模式切換](#情境-1資源配額與排程模式切換)
2. [情境 2：暫緩、重排、移除與還原排隊工作](#情境-2暫緩重排移除與還原排隊工作)
3. [情境 3：回應提示詞並執行受保護的控制操作](#情境-3回應提示詞並執行受保護的控制操作)
4. [情境 4：自動重試、逾時判決優先序與手動重跑](#情境-4自動重試逾時判決優先序與手動重跑)
5. [情境 5：寫入 Log 檔案、調整留存上限與嚴格解碼錯誤](#情境-5寫入-log-檔案調整留存上限與嚴格解碼錯誤)
6. [情境 6：以 `ProcessHandle` 直接控制單次子程序樹](#情境-6以-processhandle-直接控制單次子程序樹)

## 情境 1：資源配額與排程模式切換

比較 `"strict"` 阻塞與 `"backfill"` 插隊：高優先權工作等待稀缺授權時，只需可用資源的低優先權工作可在切換模式後立即派發。

```python
import sys
import threading
import time
from rpkbin.jobcore import Job, JobManager

def wait_for(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() >= deadline:
            raise TimeoutError("condition not met")
        time.sleep(0.01)

hold_code = "import sys; print('STARTED', flush=True); sys.stdin.readline()"

with JobManager(max_workers=4, resources={"license": 1, "grid": 2}, scheduling_mode="strict") as manager:
    started = {}
    manager.add_log_listener(lambda r: started.setdefault(r["job_name"], threading.Event()).set() if r["text"] == "STARTED" else None)

    a = manager.add(Job("a", [sys.executable, "-u", "-c", hold_code], pty=False, resources={"license": 1, "grid": 1}, priority=30))
    b = manager.add(Job("b", [sys.executable, "-u", "-c", hold_code], pty=False, resources={"license": 1, "grid": 1}, priority=20))
    c = manager.add(Job("c", [sys.executable, "-u", "-c", hold_code], pty=False, resources={"grid": 1}, priority=10))

    manager.start()
    assert started.setdefault("a", threading.Event()).wait(5)
    wait_for(lambda: "Waiting behind higher-priority job b" in manager.explain_pending(c))
    print("strict c pending:", manager.explain_pending(c))

    manager.update_settings(scheduling_mode="backfill")
    assert started.setdefault("c", threading.Event()).wait(5)
    print("backfill c status:", c.status.value)

    for job in (a, c):
        assert job.send("quit").accepted
    assert started.setdefault("b", threading.Event()).wait(5)
    assert b.send("quit").accepted
    wait_for(lambda: all(j.to_snapshot_dict()["finalized"] for j in (a, b, c)))
    print("final statuses:", [j.status.value for j in (a, b, c)])
```

輸出：
```text
strict c pending: Waiting behind higher-priority job b (Waiting for license (1/1 in use, need 1))
backfill c status: RUNNING
final statuses: ['DONE', 'DONE', 'DONE']
```

在 `"strict"` 模式下，即使 `grid` 仍有餘裕，工作 `c` 仍會排在 `b` 後方等待；透過 `update_settings()` 切換為 `"backfill"` 後，排程器立即派發 `c` 且不會中斷 `a`。

## 情境 2：暫緩、重排、移除與還原排隊工作

使用 `set_held`、`reorder_queue`、`remove_job`、`removed_jobs` 與 `restore_job`，維持 `jobs` 原本的註冊順序，並透過 `stop_all(include_held=False)` 保留暫緩工作。

```python
import sys
import time
from rpkbin.jobcore import Job, JobManager, JobStatus

wait_code = "import sys; print('READY', flush=True); sys.stdin.readline()"

with JobManager(max_workers=1) as manager:
    a = manager.add(Job("alpha", [sys.executable, "-u", "-c", wait_code], pty=False, priority=10))
    b = manager.add(Job("beta", [sys.executable, "-u", "-c", wait_code], pty=False, priority=10))
    c = manager.add(Job("gamma", [sys.executable, "-u", "-c", wait_code], pty=False, priority=10))

    results = manager.reorder_queue([c, (b, b.attempt_id, b.submission_id), a])
    print("reordered statuses:", [receipt.status for _, receipt, _ in results])
    print("reordered priorities:", [prio for _, _, prio in results])
    assert c.priority > b.priority > a.priority

    assert manager.set_held(a, True).accepted
    assert manager.remove_job("beta").accepted
    print("removed_jobs:", [j.name for j in manager.removed_jobs])
    assert manager.restore_job(b).accepted
    print("restored order:", [j.name for j in manager.jobs])

    manager.start()
    deadline = time.monotonic() + 10.0
    while "READY" not in c.logs():
        if time.monotonic() >= deadline:
            raise TimeoutError("gamma did not start")
        time.sleep(0.01)

    stopped = manager.stop_all(include_held=False)
    print("stopped excluding held:", stopped, "alpha status:", a.status.value)
    assert a.status is JobStatus.PENDING
    while not (b.to_snapshot_dict()["finalized"] and c.to_snapshot_dict()["finalized"]):
        if time.monotonic() >= deadline:
            raise TimeoutError("stopped jobs did not finalize")
        time.sleep(0.01)
    assert manager.stop_all(include_held=True) == 1
```

輸出：
```text
reordered statuses: ['accepted', 'accepted', 'accepted']
reordered priorities: [12, 11, 10]
removed_jobs: ['beta']
restored order: ['alpha', 'beta', 'gamma']
stopped excluding held: 2 alpha status: PENDING
```

## 情境 3：回應提示詞並執行受保護的控制操作

在尚未收到換行前偵測字面 `prompts`，以 `send(..., newline=False)` 與 `send(..., secret=True)` 回覆輸入，並搭配平台支援的訊號執行 `manager.control(..., action="signal", sig=...)`。

```python
import os
import signal
import sys
import time
from rpkbin.jobcore import Job, JobManager, JobStatus

sig = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
sig_name = "SIGBREAK" if os.name == "nt" else "SIGINT"

script = (
    f"import signal, sys, time; "
    f"signal.signal(signal.{sig_name}, lambda *_: (print('CAUGHT', flush=True), sys.exit(0))); "
    "sys.stdout.write('token? '); sys.stdout.flush(); "
    "part = sys.stdin.read(3); "
    "line = sys.stdin.readline().strip(); "
    "print('LEN=' + str(len(part)) + ':' + line, flush=True); "
    "time.sleep(5)"
)

with JobManager(max_workers=1) as manager:
    job = manager.add(Job("interactive", [sys.executable, "-u", "-c", script], pty=False, prompts=("token? ",)))
    manager.start()

    deadline = time.monotonic() + 10.0
    while job.status is not JobStatus.WAITING_INPUT:
        if time.monotonic() >= deadline:
            raise TimeoutError("prompt not reached")
        time.sleep(0.01)

    r1 = manager.control(job.id, "send", expected_attempt=job.attempt_id, expected_submission=job.submission_id, text="abc", newline=False, secret=True)
    r2 = job.send("done", newline=True)
    print("send receipts:", r1.status, r1.reason, "|", r2.status, r2.reason)

    while "LEN=3:done" not in job.logs():
        if time.monotonic() >= deadline:
            raise TimeoutError("reply not logged")
        time.sleep(0.01)

    r3 = manager.control(job.id, "signal", expected_attempt=job.attempt_id, expected_submission=job.submission_id, sig=sig)
    assert r3.accepted
    while not job.to_snapshot_dict()["finalized"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("signal exit timed out")
        time.sleep(0.01)

    print("logs:", job.logs()[:4], job.logs()[-1])
    print("returncode:", job.returncode)
```

輸出：
```text
send receipts: accepted Input sent: *** | accepted Input sent
logs: ['token? ', 'Input sent: ***', 'Input sent: done', 'LEN=3:done'] CAUGHT
returncode: 0
```

## 情境 4：自動重試、逾時判決優先序與手動重跑

觀察非零退出時的自動重試（`max_retries=1`）、檢視 `Job.history`、呼叫 `rerun()` 啟動新 submission，並確認 `timeout` 到期時即使程序以 `0` 結束仍會判定為 `ProcessVerdict.FAIL`。

```python
import sys
import tempfile
import time
from pathlib import Path
from rpkbin.jobcore import Job, JobManager, ProcessVerdict

with tempfile.TemporaryDirectory() as td:
    marker = Path(td) / "count.txt"
    retry_code = (
        f"from pathlib import Path; import sys; "
        f"p = Path({str(marker)!r}); "
        "n = int(p.read_text()) + 1 if p.exists() else 1; "
        "p.write_text(str(n)); "
        "print(f'run={n}', flush=True); "
        "sys.exit(1 if n == 1 else 0)"
    )
    timeout_code = "import sys; sys.stdin.readline()"

    with JobManager(max_workers=2) as manager:
        retrier = manager.add(Job("retrier", [sys.executable, "-u", "-c", retry_code], pty=False, max_retries=1))
        timed = manager.add(Job("timed", [sys.executable, "-u", "-c", timeout_code], pty=False, timeout=0.2, stop_cmd="quit", stop_timeout=1.0))
        manager.start()

        deadline = time.monotonic() + 10.0
        while not (retrier.to_snapshot_dict()["finalized"] and timed.to_snapshot_dict()["finalized"]):
            if time.monotonic() >= deadline:
                raise TimeoutError("jobs did not finish")
            time.sleep(0.01)

        print("retrier attempt:", retrier.attempt_number, "verdict:", retrier.result.verdict.value, "history0:", retrier.history[0]["result"])
        print("timed rc:", timed.returncode, "verdict:", timed.result.verdict.value)

        prev_sub = retrier.submission_id
        assert manager.rerun(retrier.id, expected_attempt=retrier.attempt_id, expected_submission=prev_sub).accepted
        while not retrier.to_snapshot_dict()["finalized"]:
            if time.monotonic() >= deadline:
                raise TimeoutError("rerun did not finish")
            time.sleep(0.01)
        print("after rerun attempt:", retrier.attempt_number, "new submission:", retrier.submission_id != prev_sub)
```

輸出：
```text
retrier attempt: 2 verdict: N/A history0: FAIL
timed rc: 0 verdict: FAIL
after rerun attempt: 3 new submission: True
```

## 情境 5：寫入 Log 檔案、調整留存上限與嚴格解碼錯誤

將輸出寫入 `log_dir` 與 `log_file`、使用 `logs(tail=...)` 與 `set_max_log_lines()` 控制記憶體留存，並觀察 `decode_errors="strict"` 遇到無效位元組時產生 `ProcessVerdict.ERROR`。

```python
import sys
import tempfile
import time
from pathlib import Path
from rpkbin.jobcore import Job, JobManager, ProcessVerdict

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    explicit_log = root / "explicit.log"

    with JobManager(max_workers=2, log_dir=root / "logs") as manager:
        writer = manager.add(Job("writer", [sys.executable, "-u", "-c", "for i in range(5): print(f'line-{i}')"], pty=False, log_file=explicit_log))
        bad = manager.add(Job("bad-utf8", [sys.executable, "-u", "-c", "import sys; sys.stdout.buffer.write(b'ok\\n\\xff\\nafter\\n')"], pty=False, decode_errors="strict"))
        manager.start()

        deadline = time.monotonic() + 10.0
        while not (writer.to_snapshot_dict()["finalized"] and bad.to_snapshot_dict()["finalized"]):
            if time.monotonic() >= deadline:
                raise TimeoutError("jobs did not finish")
            time.sleep(0.01)

        print("tail(2):", writer.logs(tail=2))
        print("tail(0):", writer.logs(tail=0))
        print("file has line-4:", "line-4" in explicit_log.read_text(encoding="utf-8"))
        print("bad verdict:", bad.result.verdict.value, "error sources:", [e["source"] for e in bad.result.errors])
        print("dir log exists:", (root / "logs" / f"{bad.id}.log").exists())
```

輸出：
```text
tail(2): ['line-3', 'line-4']
tail(0): []
file has line-4: True
bad verdict: ERROR error sources: ['output_decode']
dir log exists: True
```

## 情境 6：以 `ProcessHandle` 直接控制單次子程序樹

直接建立 `ProcessHandle` 啟動命令、寫入精確位元組（不附帶隱含換行）、在主程序結束後用 `cleanup()` 清理殘留子樹並讀盡緩衝輸出，最後呼叫 `close()` 釋放資源。

```python
import sys
import time
from rpkbin.jobcore import ProcessHandle

code = (
    "import subprocess, sys; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
    "data = sys.stdin.buffer.read(4); "
    "sys.stdout.buffer.write(b'ECHO:' + data + b'\\n'); "
    "sys.stderr.buffer.write(b'ERR:done\\n')"
)

handle = ProcessHandle([sys.executable, "-u", "-c", code])
handle.start()
print("started pid:", handle.pid is not None)
handle.write("PING")

deadline = time.monotonic() + 10.0
while handle.poll() is None:
    if time.monotonic() >= deadline:
        handle.kill()
        raise TimeoutError("process did not exit")
    time.sleep(0.01)

handle.cleanup()
buf = bytearray()
while chunk := handle.read():
    buf.extend(chunk)
rc = handle.poll()
handle.close()
print("returncode:", rc)
print("output:", sorted(buf.decode().splitlines()))
```

輸出：
```text
started pid: True
returncode: 0
output: ['ECHO:PING', 'ERR:done']
```

## 延伸閱讀

- [快速入門](README.md)
- [API 參考手冊](reference.md)
- [rpkbin.flow 實戰情境](../flow/scenarios.md)
- [文件導覽](../README.md)
