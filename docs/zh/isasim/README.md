# `rpkbin.isasim`

[English](../../en/isasim/README.md) · [文件導覽](../README.md) · [使用情境](scenarios.md) · [組譯器](assembler.md) · [API 參考](reference.md) · [架構](architecture.md) · [視覺化瀏覽器除錯器（`isasim_gui`）](../app/isasim_gui/README.md)

`rpkbin.isasim` 依據宣告式的 Python `MCU` 模型執行指令模擬，將兩階段組譯器與可回滾交易執行、斷點、觀察點、裝置及快照整合在一起。

## 安裝方式

在專案根目錄執行以下命令，以 editable 模式安裝目前原始碼：

```bash
python -m pip install -e .
```

本套件僅使用 Python 標準函式庫，需要 Python 3.11 或更新版本。安裝後即可使用 `rpk-isasim` 命令與 `python -m rpkbin.isasim`。

## 語法

```text
mcu = MCU(name)
image = assemble(mcu, source, *, filename="<string>", include_paths=(), defines=None, endian=None, case_sensitive_names=True)
with Simulator(mcu, *, trace_reads=False, history=1000) as sim:
    sim.load_assembly(image)
    result = sim.run(max_steps=100)
snap = sim.snapshot()
sim.restore(snap)
```

```bash
rpk-isasim assemble MCU.py SOURCE.asm [-o IMAGE] [--listing FILE]
rpk-isasim run MCU.py PROGRAM --max-steps N
rpk-isasim repl MCU.py [PROGRAM]
```

## 說明

- `MCU(name)` 宣告記憶體空間、暫存器、位元欄位、暫存器別名、記憶體別名（`mem_alias`）、IRQ 線、指令編碼與停止政策。
- `assemble(mcu, source, ...)` 展開引入檔與條件分支並編碼為 `Assembly` bundle，過程不會執行指令回呼函式。
- `Simulator(mcu, ...)` 擁有所有可變執行期狀態，透過受檢查的 `State` 傳入回呼，對外提供唯讀的 `Simulator.state`，並在離開 `with` 區塊時自動關閉已開啟的 trace 檔案。
- `sim.snapshot()` 與 `sim.restore(snap)` 擷取與還原模擬器、裝置及刺激訊號狀態，還原前會驗證結構相容性。
- `rpk-isasim` 在命令列提供 `assemble`、無介面批次執行 `run` 與互動式 `repl`。

## 範例

### 1. 基本範例：組譯並執行小型程式

定義一個 5 位元程式 cell 與 5 位元 `value` 暫存器，組譯四行組語並在 `Simulator` context manager 中執行。`PLUS` 依 32 取模累加（13、24，最後 34 截為 5 位元得到 2），`END` 停機。

```python
from rpkbin.isasim import MCU, Simulator, assemble, helpers

mcu = MCU("demo")
mcu.space("prog", cell_bits=5, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.reg("cursor", bits=5, reset=0, is_pc=True)
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

source = """start:
PLUS 13
PLUS 11
PLUS 10
END
"""
image = assemble(mcu, source, filename="demo.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(image)
    result = sim.run(max_steps=100)
    print(result.stop_reason, result.steps, result.cycles, result.pc)
    print(sim.read("value"))
```
```text
halted 4 7 4
2
```

### 2. 進階範例：來源行斷點、快照與重播

在 `:3`（第二條 `PLUS` 指令前）設定來源行斷點，擷取 `Snapshot` 後一路執行至結束，再還原快照並驗證重播結果完全一致，同時檢視 `MCU`、`Assembly`、`Simulator` 與 `Snapshot` 的 `repr()` 摘要。

```python
from rpkbin.isasim import MCU, Simulator, assemble, helpers

mcu = MCU("demo")
mcu.space("prog", cell_bits=5, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.reg("cursor", bits=5, reset=0, is_pc=True)
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

source = """start:
PLUS 13
PLUS 11
PLUS 10
END
"""
image = assemble(mcu, source, filename="demo.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(image)
    sim.add_breakpoint(":3")
    paused = sim.run(max_steps=100)
    print(paused.stop_reason, paused.steps, paused.cycle, paused.pc, sim.read("value"))

    snap = sim.snapshot()
    first = sim.run(max_steps=100)
    sim.restore(snap)
    second = sim.run(max_steps=100)
    assert (first.stop_reason, first.cycles, first.pc) == (second.stop_reason, second.cycles, second.pc)
    print(second.stop_reason, second.steps, second.cycles, sim.read("value"))
    print(repr(mcu))
    print(repr(image))
    print(repr(sim))
    print(repr(snap))
```
```text
breakpoint 1 2 1 13
halted 3 5 2
MCU('demo', spaces=1, registers=2, instructions=2)
Assembly('demo.asm', cells=4, symbols=1)
Simulator('demo', pc=0x04, cycle=7, steps=4)
Snapshot(mcu='demo', cycle=2, steps=1)
```

## 文件導覽

| 文件 | 說明 |
| :--- | :--- |
| [使用情境](scenarios.md) | 可獨立執行的 trace、來源除錯、觀察點、`mem_alias`、裝置與快照範例 |
| [組譯器](assembler.md) | 運算元宣告、運算式、`.include`/`.if` 前處理、`.isa.json` bundle 與 CLI 組譯 |
| [API 參考](reference.md) | `MCU`、`Simulator`、`Device`、`Snapshot`、`helpers`、CLI 參數與全部 25 個 REPL 命令 |
| [架構](architecture.md) | 檔案職責、狀態所有權、交易回滾與記憶體別名不變條件 |
| [視覺化瀏覽器除錯器（`isasim_gui`）](../app/isasim_gui/README.md) | 瀏覽器單步除錯、條件斷點、原位修改與 `DebugSession` Python API |

## 注意事項與限制

- **狀態邊界**：指令執行期間回呼會收到可寫入的 `State`。`Simulator.state` 是唯讀檢視；外部改寫請使用 `poke(target, value)`。
- **斷點與 `reset()`**：從斷點位址繼續執行 `run()` 時會略過該斷點一次，而 `step()` 全程不檢查斷點。`reset()` 會保留已載入的程式映像與 `mem_alias` 註冊，並清除暫存器、非映像記憶體初始化狀態及刺激訊號進度。
- **模型範圍**：每條指令宣告的 cell 長度固定。`isasim` 只執行您提供給 MCU 模型的模式與語意，不會依 MCU 名稱推測硬體行為。

## 延伸閱讀

- [使用情境](scenarios.md)
- [組譯器](assembler.md)
- [API 參考](reference.md)
- [架構](architecture.md)
- [視覺化瀏覽器除錯器（`isasim_gui`）](../app/isasim_gui/README.md)
- [文件導覽](../README.md)
