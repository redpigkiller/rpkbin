# `rpkbin.isasim` 使用情境

[English](../../en/isasim/scenarios.md) · [文件導覽](../README.md) · [快速入門](README.md) · [組譯器](assembler.md) · [API 參考](reference.md) · [架構](architecture.md)

四個可獨立執行的範例，涵蓋可重現 trace 擷取、多檔案來源除錯、搭配 `mem_alias` 的觀察點，以及支援 `ctx` 與 `Snapshot` 重播的記憶體映射 `Device`。

## 情境 1：擷取可重現的文字與 JSONL Trace

建立小型 5 位元 MCU（`trace_demo`），先執行四個 cell 的程式一次，接著重設並在 `Simulator` context manager 內寫入 JSONL trace，離開區塊時自動關閉串流。

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from rpkbin.isasim import MCU, Simulator, helpers

mcu = MCU("trace_demo")
mcu.space("prog", cell_bits=5, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.reg("pc", bits=5, reset=0, is_pc=True)
mcu.reg("value", bits=5, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0 iiii", syntax="PLUS {i}", cycles=2)
def plus(st, i):
    st.reg["value"] = helpers.add_with_carry(st.reg["value"], i, 0, 5).result

@mcu.instr("10000", syntax="END", cycles=1)
def end(st):
    st.halt("done")

sim = Simulator(mcu)
sim.load_cells("prog", {0: 13, 1: 11, 2: 10, 3: 16})
first = sim.run(max_steps=100)
print("first:", first.stop_reason, sim.read("value"), "recent:", len(sim.recent_events(10)))

sim.reset()
with TemporaryDirectory() as folder:
    trace_path = Path(folder) / "trace.jsonl"
    with sim:
        sim.enable_trace(trace_path, fmt="jsonl")
        second = sim.run(max_steps=100)
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    print("second:", second.stop_reason, second.cycles, "lines:", len(lines))
```
```text
first: halted 2 recent: 5
second: halted 7 lines: 5
```

每次執行都會產生四筆指令事件（`PLUS`、`PLUS`、`PLUS`、`END`）以及一筆 `"stop"` 事件。在 `Simulator` 啟用 `trace_reads=True` 會記錄語意資料讀取（`mem[...]`），但不包含取指讀取。

## 情境 2：使用引入檔組譯並停在來源行

組譯一個從引入檔取得常數的程式，載入 `Simulator` 後在主檔設定 `:line` 斷點，並檢查 `source_context()`。

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from rpkbin.isasim import MCU, Simulator, assemble, helpers

mcu = MCU("src_demo")
mcu.space("prog", cell_bits=5, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.reg("pc", bits=5, reset=0, is_pc=True)
mcu.reg("value", bits=5, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0 iiii", syntax="PLUS {i}", cycles=2)
def plus(st, i):
    st.reg["value"] = helpers.add_with_carry(st.reg["value"], i, 0, 5).result

@mcu.instr("10000", syntax="END", cycles=1)
def end(st):
    st.halt("done")

with TemporaryDirectory() as folder:
    root = Path(folder)
    (root / "const.inc").write_text("STEP = 11\n", encoding="utf-8")
    main_asm = root / "main.asm"
    main_asm.write_text('.include "const.inc"\nPLUS 13\nPLUS STEP\nEND\n', encoding="utf-8")

    image = assemble(mcu, main_asm.read_text(encoding="utf-8"), filename=str(main_asm))
    with Simulator(mcu) as sim:
        sim.load_assembly(image)
        sim.add_breakpoint(":3")
        result = sim.run(max_steps=100)
        current_line = next(row for row in sim.source_context() if row[2])
        print(result.stop_reason, result.pc, sim.read("value"))
        print(current_line[0], current_line[1], current_line[2])
```
```text
breakpoint 1 13
3 PLUS STEP True
```

`:3` 對應 `main.asm` 第 3 行；執行完 `PLUS 13` 把 `value` 更新為 `13` 後，模擬器停在 `pc=1`（即將執行 `PLUS STEP` 之前）。

## 情境 3：觀察點、暫存器欄位與 `mem_alias`

暫存器欄位觀察點會比對任何重疊的位元範圍；即使指令寫入整個 `status` 暫存器，監看 `status.high`（bits `7..4`）仍會觸發並回報該欄位的舊值與新值：

```python
from rpkbin.isasim import MCU, Simulator

mcu = MCU("watch_demo")
mcu.space("prog", cell_bits=8, size=4, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.reg("pc", bits=2, reset=0, is_pc=True)
mcu.reg("status", bits=8, reset=0, fields={"high": (7, 4), "low": (3, 0)})
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00000000", syntax="SET", cycles=1)
def set_status(st):
    st.reg["status"] = 0xA3

sim = Simulator(mcu)
sim.load_cells("prog", {0: 0})
sim.add_watchpoint("status.high", kind="w")
result = sim.run(max_steps=10)
print(result.stop_reason, result.detail["old"], result.detail["new"])
```
```text
watchpoint 0 10
```

你也能在 `MCU` 上宣告跨 cell 的 `mem_alias` 與單位元旗標別名，於執行期動態新增模擬器別名、設定條件式觀察點，最後呼叫 `clear_mem_aliases()` 清除動態別名：

```python
from rpkbin.isasim import MCU, Simulator

mcu = MCU("alias_watch_demo")
mcu.space("prog", cell_bits=8, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=16, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("status", bits=8, reset=0, fields={"high": (7, 4), "low": (3, 0)})
mcu.mem_alias("total16", "ram[0x00:0x02]", endian="little", bit_order="msb_first")
mcu.mem_alias("ready_bit", "ram[0x02][0]")
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00010000", syntax="SET_STATUS", cycles=1)
def set_status(st):
    st.reg["status"] = 0xA3

@mcu.instr("00100000", syntax="WRITE_PAIR", cycles=2)
def write_pair(st):
    st.mem["ram"][0] = 0x34
    st.mem["ram"][1] = 0x12

@mcu.instr("00110000", syntax="HALT", cycles=1)
def halt_op(st):
    st.halt("done")

with Simulator(mcu) as sim:
    sim.load_cells("prog", {0: 0x10, 1: 0x20, 2: 0x30})
    sim.mem_alias("upper_byte", "ram[0x01][7:0]", bit_order="msb_first")
    sim.poke("ready_bit", 1)

    sim.add_watchpoint("status.high", kind="w")
    first = sim.run(max_steps=10)
    print("wp1:", first.stop_reason, first.detail["target"], first.detail["old"], first.detail["new"])

    sim.add_watchpoint("total16", kind="w", condition="m.total16 == 0x1234")
    second = sim.run(max_steps=10)
    print("wp2:", second.stop_reason, second.detail["target"], hex(second.detail["old"]), hex(second.detail["new"]))
    print("ready_bit:", sim.read("ready_bit"), "upper_byte:", hex(sim.read("upper_byte")))

    sim.clear_mem_aliases()
    print("remaining_aliases:", list(sim.mem_aliases))
```
```text
wp1: watchpoint status.high 0 10
wp2: watchpoint total16 0x0 0x1234
ready_bit: 1 upper_byte: 0x12
remaining_aliases: ['total16', 'ready_bit']
```

`clear_mem_aliases()` 會移除模擬器動態註冊的 `upper_byte`，同時保留 `MCU` 宣告的 `total16` 與 `ready_bit`。

## 情境 4：具 `ctx` 存取權的記憶體映射 `Device`、刺激訊號與 `Snapshot` 重播

掛載一個在 `read` 與 `write` 中選用 `ctx`（使用 `ctx.reg` 與 `ctx.add_cycles`）的 `Device` 子類別，排程 `at_pc` 刺激訊號，以 `snapshot()` 建立檢查點，並透過 `restore()` 驗證一致的重播結果。

```python
from rpkbin.isasim import Device, MCU, Simulator, assemble

class TimerDevice(Device):
    time_dependent = True

    def __init__(self):
        self.count = 0

    def read(self, addr, now, ctx):
        ctx.add_cycles(1)
        return (self.count + ctx.reg["bias"]) & 0xFF

    def write(self, addr, value, now, ctx):
        self.count = value
        ctx.reg["acc"] = value

    def tick(self, cycles, now):
        self.count = (self.count + cycles) & 0xFF

    def reset(self):
        self.count = 0

    def get_state(self):
        return {"count": self.count}

    def set_state(self, state):
        self.count = state["count"]

mcu = MCU("dev_replay")
mcu.space("prog", cell_bits=8, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("mmio", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0, role="inout")
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("acc", bits=8, reset=0)
mcu.reg("bias", bits=8, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00010000", syntax="WRDEV", cycles=1)
def wrdev(st):
    st.mem["mmio"][0] = 10

@mcu.instr("00100000", syntax="RDDEV", cycles=1)
def rddev(st):
    st.reg["acc"] = st.mem["mmio"][0]

@mcu.instr("00110000", syntax="END", cycles=1)
def dend(st):
    st.halt("done")

with Simulator(mcu) as sim:
    sim.attach(TimerDevice(), space="mmio", lo=0, hi=1)
    sim.load_assembly(assemble(mcu, "WRDEV\nRDDEV\nEND\n", filename="dev.asm"))
    sim.at_pc(1).set("bias", 5)

    sim.step()
    snap = sim.snapshot()
    first = sim.run(max_steps=10)
    first_acc = sim.read("acc")

    sim.restore(snap)
    second = sim.run(max_steps=10)
    assert (first.stop_reason, first.cycles, first_acc) == (second.stop_reason, second.cycles, sim.read("acc"))
    print(second.stop_reason, second.cycles, sim.read("acc"), repr(snap))
```
```text
halted 3 16 Snapshot(mcu='dev_replay', cycle=1, steps=1)
```

執行 `WRDEV` 時，`write` 將 `count` 設為 `10`，接著步驟提交後的 `tick(1)` 把 `count` 遞增為 `11`。進入 `RDDEV` 前，`at_pc(1)` 刺激訊號把 `bias` 設為 `5`，隨後 `read` 透過 `ctx.add_cycles(1)` 多加 1 個 cycle 並回傳 `11 + 5 = 16`。`Snapshot` 一併記錄模擬器暫存器、刺激訊號狀態與 `TimerDevice.get_state()`，讓後續重播得到完全一致的結果。

## 延伸閱讀

- [快速入門](README.md)
- [組譯器](assembler.md)
- [API 參考](reference.md)
- [架構](architecture.md)
