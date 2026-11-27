# 情境與工作流範例

整理模擬批次執行、佇列優先順序管理、記錄檔載入與重析，以及共享叢集保留規則的常見做法。

## 情境一：批次模擬搭配波形擷取

結合 Python `Job` 執行、`Parser` 串流解析、`Analyzer` 通過判定與 `ViewSpec.waveform` 索引投影：

```python
import re
import sys
import tempfile
from pathlib import Path

from rpkbin.flow import AnalysisReport, Analyzer, Binding, Flow, Parser, ViewSpec
from rpkbin.jobcore import Job
from rpkbin.wave import RunArchive, WaveWorkspace, run_headless

EVENT_RE = re.compile(r"t=(\d+)\s+req=(\d)\s+ack=(\d)\s+data=(0x[0-9a-fA-F]+)")

def parse_rtl(line, ctx):
    m = EVENT_RE.search(line)
    if m:
        return {
            "t": int(m.group(1)),
            "req": int(m.group(2)),
            "ack": int(m.group(3)),
            "data": int(m.group(4), 16),
        }

def check_handshake(rec, ctx):
    if rec["req"] and rec["ack"]:
        ctx.state["acks"] = ctx.state.get("acks", 0) + 1

def on_finish(info, ctx):
    acks = ctx.state.get("acks", 0)
    return AnalysisReport("handshake", result="PASS" if acks > 0 else "FAIL", values={"acks": acks})

rtl = Parser("rtl", parse_rtl)
hs = Analyzer("hs", records=(Binding(rtl, check_handshake),), on_finish=on_finish)
flow = Flow(
    "rtl_bench",
    parsers=(rtl,),
    analyzers=(hs,),
    views=(
        ViewSpec.waveform(
            "wave",
            source=rtl,
            axis={"field": "t", "label": "Time", "unit": "ns"},
            series={
                "mode": "wide",
                "items": [
                    {"id": "req", "field": "req", "type": "bit"},
                    {"id": "ack", "field": "ack", "type": "bit"},
                    {"id": "data", "field": "data", "type": "bus", "bits": 16},
                ],
            },
        ),
    ),
)

with tempfile.TemporaryDirectory() as tmp:
    ws = WaveWorkspace(archive_dir=Path(tmp) / "archives")
    sim_code = "print('t=0 req=0 ack=0 data=0x0\\nt=5 req=1 ack=0 data=0xa1\\nt=10 req=1 ack=1 data=0xb2')"
    ws.add(Job("rtl_smoke", [sys.executable, "-c", sim_code]), flow=flow)
    rc = run_headless(ws)
    db = Path(ws.archive_health["path"])
    ws.close()

    with RunArchive(db) as ar:
        att = ar.attempts()["rows"][0]
        vcd = ar.export_vcd(att["id"], att["attempt_id"], view_id="wave")
        print("rc:", rc, "vcd lines:", len(vcd.splitlines()))
```

輸出（Wave 摘要表之後）：

```text
rc: 0 vcd lines: 23
```

## 情境二：佇列控制、優先級排序與資源補空

設定工作執行緒上限、授權或硬體資源計數、暫緩項目（`HELD`）以及佇列相對排序：

```python
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import WaveWorkspace

mgr = JobManager(max_workers=2, resources={"license": 2}, scheduling_mode="backfill")
ws = WaveWorkspace(manager=mgr)

heavy = Job("heavy", ["python", "-V"], resources={"license": 2}, priority=10)
light = Job("light", ["python", "-V"], resources={"license": 1}, priority=1)
later = Job("later", ["python", "-V"], priority=5)
ws.add_many([heavy, light, later])

ws.set_held("later", True)
print("pending reason:", ws.explain_pending(later))
ws.reorder_queue([light, heavy, later])
print("reordered:", [(j.name, j.priority) for j in ws.jobs])
ws.close()
```

輸出：

```text
pending reason: Held (excluded from queue)
reordered: [('heavy', 5), ('light', 10), ('later', 1)]
```

## Log 與重析選項

當記錄檔已經存在於磁碟上（或由外部模擬器持續輸出）時，可透過命令列或 Python API 使用 `FileSource`、`TailSource` 與 `ReanalysisSource`：

| 模式 | 命令 / 類別 | 主要選項 | 適用情境 |
| --- | --- | --- | --- |
| 單次匯入 | `rpk-wave logs FILE ... --once` / `FileSource` | `--flow`、`--glob`、`-r`、`--encoding` | 讀入已完成產出的記錄檔並套用 `Flow` 解析一次。 |
| 即時追蹤 | `rpk-wave logs TARGET` / `TailSource` | `--start`、`--poll-interval`、`--idle-timeout`、`--completion-file`、`--end-marker`、`--final-offset` | 持續追蹤寫入中或輪替的記錄檔，直到觸發結束條件或手動停止。 |
| 離線重析 | `rpk-wave reanalyze ARCHIVE --flow FLOW.py` / `ReanalysisSource` | `--attempt`、`--archive-dir`、`--headless` | 修改 `Parser`、`Analyzer` 或 `ViewSpec` 後，將已封存的原始行重播產生新封存檔。 |

下列範例以 `FileSource` 匯入靜態記錄，再用 `ReanalysisSource` 搭配更新版 `Flow` 檔案重析產生第二份封存檔：

```python
import re
import tempfile
from pathlib import Path

from rpkbin.flow import Flow, Parser
from rpkbin.wave import FileSource, ReanalysisSource, RunArchive

def parse_err(line, ctx):
    m = re.search(r"ERR=(\d+)", line)
    if m:
        return {"err": int(m.group(1))}

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    log_file = tmp_path / "sim.log"
    log_file.write_text("boot ok\nERR=2\n", encoding="utf-8")

    flow_file = tmp_path / "flow_v2.py"
    flow_file.write_text(
        "import re\n"
        "from rpkbin.flow import Flow, Parser\n"
        "def p(line, ctx):\n"
        "    m = re.search(r'ERR=(\\d+)', line)\n"
        "    if m: return {'err': int(m.group(1))}\n"
        "flow = Flow('v2', parsers=(Parser('p2', p),))\n",
        encoding="utf-8",
    )

    flow_v1 = Flow("v1", parsers=(Parser("p1", parse_err),))
    src = FileSource(log_file)
    res1 = src.run(flow=flow_v1, archive_dir=tmp_path / "a1")

    with RunArchive(res1.archive_path) as ar:
        att = ar.attempts()["rows"][0]
        re_src = ReanalysisSource(res1.archive_path, att["attempt_id"])
        res2 = re_src.run(flow_file, archive_dir=tmp_path / "a2")

    with RunArchive(res2.archive_path) as ar2:
        att2 = ar2.attempts()["rows"][0]
        rec = ar2.flow_latest(att2["id"], att2["attempt_id"], stage="parser")
        print("v1 complete:", res1.complete, "v2 data:", rec["rows"][0]["data"])
```

輸出：

```text
v1 complete: True v2 data: {'err': 2}
```

## 情境四：NFS 叢集本機暫存與自動保留清理

在專案目錄位於 NFS 或 SMB 的多人叢集環境下，透過環境變數只保留失敗記錄，同時把執行期 WAL 留在運算節點本機暫存目錄：

```bash
export RPK_WAVE_ARCHIVE_DIR=/proj/team/wave_archives
export RPK_WAVE_TEMP_DIR=/tmp
export RPK_WAVE_ARCHIVE_MODE=keep-on-fail
export RPK_WAVE_PASS_RETENTION_DAYS=1
export RPK_WAVE_FAIL_RETENTION_DAYS=30

rpk-wave regression_work.py --headless
```

執行時 SQLite WAL 寫在 `/tmp/rpk-wave-uid<UID>`。任務結束後，通過的執行會在節點端清除，失敗或中斷的執行會先完成檢查點，再原子搬移至 `/proj/team/wave_archives/YYYYMMDD_HHMMSS_<label>_<run_id[:8]>.sqlite3`，過期的舊封存也會同步清理。

## 另請參閱

- [Wave 總覽](README.md)
- [歷史與封存機制](history.md)
- [rpk-wave 命令列參考](reference/cli.md)
- [Python API 參考](reference/python.md)
