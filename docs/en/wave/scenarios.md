# Workflow Scenarios

Practical end-to-end recipes for simulation batches, queue management, log ingestion, archive reanalysis, and cluster retention rules.

## Scenario 1: Batch Simulation with Waveform Extraction

Combine Python `Job` execution, a streaming `Parser`, PASS/FAIL gating in `Analyzer`, and an indexed `ViewSpec.waveform`:

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

Output (after the Wave summary banner):

```text
rc: 0 vcd lines: 23
```

## Scenario 2: Queue Control, Priorities, and Resource Backfill

Configure worker concurrency, shared hardware/license resources, queue holding (`HELD`), and relative queue ordering:

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

Output:

```text
pending reason: Held (excluded from queue)
reordered: [('heavy', 5), ('light', 10), ('later', 1)]
```

## Log and Reanalysis Options

When raw logs already exist on disk (or come from an external simulator), use `FileSource`, `TailSource`, or `ReanalysisSource` via CLI or Python:

| Mode | Command / Class | Key Options | When to Use |
| --- | --- | --- | --- |
| Single pass ingest | `rpk-wave logs FILE ... --once` / `FileSource` | `--flow`, `--glob`, `-r`, `--encoding` | Import completed logs and run `Flow` parsers once. |
| Live log following | `rpk-wave logs TARGET` / `TailSource` | `--start`, `--poll-interval`, `--idle-timeout`, `--completion-file`, `--end-marker`, `--final-offset` | Follow rotating or actively appended logs until a stop marker or manual stop. |
| Offline archive replay | `rpk-wave reanalyze ARCHIVE --flow FLOW.py` / `ReanalysisSource` | `--attempt`, `--archive-dir`, `--headless` | Re-run updated `Parser`/`Analyzer`/`ViewSpec` logic over archived raw lines into a new archive. |

Example ingesting a completed log file with `FileSource` and replaying it into a fresh archive with `ReanalysisSource`:

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

Output:

```text
v1 complete: True v2 data: {'err': 2}
```

## Scenario 4: NFS-Safe Cluster Archiving and Retention Policies

On shared compute clusters where home/project directories live on NFS or SMB mounts, configure retention windows so passing jobs do not consume quota while failing runs move safely from local node scratch:

```bash
export RPK_WAVE_ARCHIVE_DIR=/proj/team/wave_archives
export RPK_WAVE_TEMP_DIR=/tmp
export RPK_WAVE_ARCHIVE_MODE=keep-on-fail
export RPK_WAVE_PASS_RETENTION_DAYS=1
export RPK_WAVE_FAIL_RETENTION_DAYS=30

rpk-wave regression_work.py --headless
```

During execution, SQLite WAL files stay inside `/tmp/rpk-wave-uid<UID>`. On completion, passing runs are discarded locally, failing runs are checkpointed and copied atomically to `/proj/team/wave_archives/YYYYMMDD_HHMMSS_<label>_<run_id[:8]>.sqlite3`, and closed archives older than the configured days are pruned automatically.

## See Also

- [Wave Overview](README.md)
- [History and Archives](history.md)
- [rpk-wave CLI Reference](reference/cli.md)
- [Python API Reference](reference/python.md)
