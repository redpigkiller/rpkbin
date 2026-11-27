# `rpkbin.isasim` API 與行為參考

[English](../../en/isasim/reference.md) · [文件導覽](../README.md) · [快速入門](README.md) · [使用情境](scenarios.md) · [組譯器](assembler.md) · [架構](architecture.md)

`rpkbin.isasim` 完整技術規格，涵蓋 `MCU`、`Assembly`、`Simulator`、`Device`、`Snapshot`、`helpers`、CLI 參數、REPL 命令與例外型別。

## 公開套件 API

`from rpkbin.isasim import ...` 匯出以下 **31** 個名稱（`__all__`）：

```python
from rpkbin.isasim import (
    MCU,
    Assembly,
    AssemblyError,
    DebugError,
    Device,
    DeviceError,
    Event,
    FieldInfo,
    IsaSimError,
    LoadError,
    MCUDefinitionError,
    MCUDescription,
    MemAliasInfo,
    Operand,
    ReadRecord,
    RegisterInfo,
    RunResult,
    Simulator,
    Snapshot,
    SnapshotError,
    SourceRecord,
    SpaceInfo,
    StateAccessError,
    StimulusError,
    StuckReport,
    TargetError,
    UsageError,
    WriteRecord,
    assemble,
    helpers,
    load_mcu,
)
```

`rpk-isasim` 與 `python -m rpkbin.isasim` 使用相同的命令列介面。

## `MCU` 與 `load_mcu`

宣告式機器描述，包含記憶體空間、暫存器、別名、記憶體別名、IRQ 線、指令模式與停止政策。

### 建立方式

```python
mcu = MCU(name: str)
mcu = load_mcu(path: str | os.PathLike[str])
```

- `MCU(name)` 建立空的機器模型（`name` 須為非空字串）。
- `load_mcu(path)` 執行定義了模組層級 `mcu` 物件的 Python 檔案、完成驗證，並雜湊該檔案位元組（`_description_sha256`）。請只載入受信任的 `.py` 檔案。
- `repr(mcu)` 回傳 `MCU('<name>', spaces=<n>, registers=<n>, instructions=<n>)`。

### 屬性

| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `aliases` | `Mapping[str, str]` | 依宣告順序排列的唯讀暫存器／欄位別名檢視。 |
| `mem_aliases` | `Mapping[str, MemAliasInfo]` | 依宣告順序排列的唯讀記憶體別名檢視。 |

### 物件函式

| 方法 | 用途 |
| :--- | :--- |
| `space(name, *, cell_bits, size, access, reset_state, init, is_program=False, role=None, regions=None)` | 宣告記憶體空間（`access`：`ro`/`rw`；`reset_state`：`initialized`/`uninitialized`；`role`：`internal`/`input`/`output`/`inout`）。 |
| `reg(name, *, bits, reset, is_pc=False, fields=None, role=None)` | 宣告暫存器（`role`：`internal`/`input`/`output`/`inout`/`flag`）。`fields` 將名稱對應到 `bit` 或 `(hi, lo)`。 |
| `alias(name, target)` | 將 `name`（不可為 `_` 開頭）綁定至既有非 PC 暫存器或 `reg.field`。 |
| `mem_alias(name, target, *, endian=None, bit_order=None)` | 宣告具名記憶體別名，對應至單一 cell、位元、位元切片或半開 cell 區間。 |
| `irq_line(name)` | 宣告外部中斷線。 |
| `instr(pattern, *, syntax, cycles, name=None, operands=None, overrides=None, kind=None)` | 裝飾器，註冊指令編碼與語意（`kind`：`None`、`"call"` 或 `"return"`）。 |
| `interrupt_check(fn)` | 裝飾器，註冊單一的步界中斷檢查函式 `fn(st) -> (event_name, cycles) \| None`。 |
| `on_undefined_opcode(action, *, nop_cells=None, nop_cycles=None)` | 未定義指令政策：`"stop"` 或 `"nop"`（需提供 `nop_cells >= 1` 與 `nop_cycles >= 0`）。 |
| `on_out_of_range(action, *, read_value=None)` | 記憶體越界存取政策：`"stop"` 或 `"ignore"`（需提供整數 `read_value`）。 |
| `on_write_to_readonly(action)` | 語意寫入唯讀空間或區間的政策：`"stop"` 或 `"ignore"`。 |
| `on_uninitialized_read(action)` | 語意讀取未初始化 cell 的政策：`"stop"`、`"warn"` 或 `"allow"`。 |
| `describe()` | 回傳已驗證 MCU 宣告的不可變 `MCUDescription` 快照。 |

### 詳細規則：政策、區間、別名、`mem_alias` 與 `MCUDescription`

- **驗證條件**：每個 `MCU` 至少須有一條指令、四項 `on_*` 政策皆須宣告，且最多只能有一個 `interrupt_check`。空間中必須恰好有一個 `is_program=True`，暫存器中必須恰好有一個 `is_pc=True`。目前宣告尚未通過驗證時，`describe()` 會引發 `MCUDefinitionError`。
- **存取區間（`regions`）**：`regions=[(lo, hi, access), ...]` 以不重疊的半開區間（`0 <= lo < hi <= size`，`access` 為 `"ro"` 或 `"rw"`）覆寫空間的預設存取權限。透過 `State` 執行的語意寫入依有效區間權限判斷；載入器、`load_cells()`、`poke()` 與 `Device` 接管的 cell 不受此限制。
- **暫存器別名（`alias`）**：別名與空間、暫存器、IRQ 線及 `mem_aliases` 共用同一全域命名空間，名稱不得為 `"irq"` 或以 `"_"` 開頭。整顆暫存器別名可接 `.field` 後綴，欄位別名則直接對應至該欄位。
- **記憶體別名（`mem_alias`）**：
  - `target` 支援 `space[addr]`、`space[addr][bit]`、`space[addr][hi:lo]` 或半開區間 `space[lo:hi]`（支援十進位或 `0x` 十六進位索引，須滿足 `0 <= lo < hi <= size` 與 `0 <= bit_lo <= bit_hi < cell_bits`）。
  - `endian` 可為 `None`、`"big"` 或 `"little"`；跨多 cell 目標（`hi - lo > 1`）必須指定 `"big"` 或 `"little"`。
  - `bit_order` 可為 `None`、`"msb_first"` 或 `"lsb_first"`；位元寬度 `bits > 1` 時必須指定。
  - 語法、邊界或名稱衝突時 `MCU.mem_alias()` 引發 `MCUDefinitionError`，並將 `MemAliasInfo` 存入 `MCU.mem_aliases`。
- **特例覆蓋（`overrides`）**：`overrides` 指定一個或多個同長度且較一般的指令，特例必須固定所有該一般指令的固定位元且多固定至少一位元。覆蓋關係可遞移。
- **結構檢查資料類別**（皆為不可變 slotted dataclass）：
  - `MCUDescription(spaces, registers, irq_lines, aliases, has_instruction_kinds)`（`mem_aliases` 由 `MCU.mem_aliases` 與 `Simulator.mem_aliases` 提供，不包含在 `MCUDescription` 內）。
  - `SpaceInfo(name, cell_bits, size, access, regions, is_program, role, reset_state)`
  - `RegisterInfo(name, bits, reset, is_pc, role, fields)`
  - `FieldInfo(name, hi, lo)`（單位元欄位的 `hi == lo`）
  - `MemAliasInfo(name, target, space, lo, hi, bit_hi, bit_lo, endian, bit_order, bits)`

## 組譯器與來源導覽

### 語法

```python
op = Operand(signed=False, relative=None, names=None, range=None)
image = assemble(
    mcu,
    source,
    *,
    filename="<string>",
    include_paths=(),
    defines=None,
    endian=None,
    case_sensitive_names=True,
)
image = Assembly(mcu, source, *, filename="<string>", include_paths=(), defines=None, endian=None, case_sensitive_names=True)
image = Assembly.load(mcu, path)
```

### 說明

`assemble()` 在不執行指令回呼函式的前提下回傳 `Assembly`。`MCU.instr(..., syntax=...)` 中的每個編碼欄位佔位符必須恰好出現一次，相鄰運算元之間必須有明確的非空白分隔符（如 `OP {a}, {b}` 或 `OP r{a}, r{b}`）。指示詞、前處理與 bundle 驗證細節請見[組譯器指南](assembler.md)。

### `Assembly` 屬性與物件函式

| 成員 | 型別／輸出 | 說明 |
| :--- | :--- | :--- |
| `cells` | `dict[int, int]` | 輸出的位址對 cell 值獨立字典副本。 |
| `symbols` | `dict[str, int]` | 標籤位址獨立字典副本。 |
| `constants` | `dict[str, int]` | 已解析常數與 `defines` 的獨立字典副本。 |
| `sources` | `dict[str, str]` | 依來源鍵保存的原始碼文字獨立字典副本。 |
| `main_key` | `str` | `sources` 中主檔案的唯讀來源鍵。 |
| `records()` | `tuple[SourceRecord, ...]` | 依 listing 順序傳回 `SourceRecord(addr, file, line, cells, is_instruction)`。 |
| `listing()` | `str` | 格式化的位址、cell 十六進位、`file:line` 與來源行文字。 |
| `save(path)` | `None` | 寫入 UTF-8 `.isa.json` bundle（LF 換行）。 |
| `Assembly.load(mcu, path)` | `Assembly` | 從 bundle 載入並驗證雜湊、ISA 指紋與重建後的 cell。 |
| `repr(image)` | `str` | `Assembly('<filename>', cells=<n>, symbols=<n>)`。 |

## `Simulator`

交易式模擬引擎，唯一擁有所有可變機器狀態。

### 建立方式

```python
sim = Simulator(mcu: MCU, *, trace_reads: bool = False, history: int = 1000)
with Simulator(mcu, *, trace_reads=False, history=1000) as sim:
    ...
```

- `history >= 1` 指定 `recent_events(n)` 保留的事件緩衝上限（`< 1` 引發 `UsageError`）。
- Context manager（`__enter__` / `__exit__`）：`__enter__` 回傳 `sim`，`__exit__` 呼叫 `sim.disable_trace()` 自動關閉開啟中的 trace 檔案。
- `repr(sim)` 回傳 `Simulator('<mcu_name>', pc=0x<hex>, cycle=<n>, steps=<n>)`，其中 `pc` 依 `(pc_bits + 3) // 4` 補零為固定位數的十六進位。

### 屬性

| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `state` | `State` | 暫存器（`reg`）、記憶體（`mem`）、IRQ 線（`irq`）、`pc`、`next_pc` 與 `cycle` 的唯讀檢視。 |
| `cycle` | `int` | 自建立或 `reset()` 起累計的 cycle 數。 |
| `steps_total` | `int` | 累計已提交的步驟數（含指令與中斷進入步驟）。 |
| `instructions_total` | `int` | 累計已提交的指令步驟數。 |
| `mem_aliases` | `Mapping[str, MemAliasInfo]` | 目前有效記憶體別名的唯讀檢視（MCU 宣告加上模擬器動態註冊）。 |

### 物件函式

| 方法 | 用途 |
| :--- | :--- |
| `describe() -> MCUDescription` | 回傳所綁定 MCU 的已驗證 `MCUDescription` 快照。 |
| `reset() -> None` | 重設暫存器、記憶體、IRQ 線、計數器、裝置與刺激訊號位置；保留已載入映像、斷點、訂閱者與 `mem_aliases`。 |
| `load_cells(space, cells) -> None` | 寫入 `{addr: value}` 至 `space` 並標為已初始化（若寫入程式空間會清除組譯來源對照）。 |
| `load_hex(path, *, space=None, order=None) -> None` | 載入 Intel HEX 至 `space`（預設程式空間；`cell_bits > 8` 須指定 `order` 為 `"big"` 或 `"little"`）。 |
| `load_bin(path, *, base, space=None, order=None) -> None` | 從非負 cell 位址 `base` 開始載入二進位檔案。 |
| `load_symbols(path) -> None` | 載入舊版 UTF-8 JSON 符號檔 `{"symbols": ..., "lines": ...}`（會清除組譯來源對照）。 |
| `load_assembly(image) -> None` | 驗證並原子安裝 `Assembly` 與來源對照（不重設暫存器或 cycle）。 |
| `initialized_ranges(space) -> tuple[tuple[int, int], ...]` | 依遞增順序回傳合併後的已初始化非裝置 cell 半開區間 `[lo, hi)`（未知空間引發 `StateAccessError`）。 |
| `read(target) -> int` | 無副作用讀取暫存器、`reg.field`、`space[addr]`、`irq.line` 或 `mem_alias`（裝置位址引發 `StateAccessError`）。 |
| `poke(target, value) -> None` | 從外部修改暫存器、`reg.field`、`space[addr]`、`irq.line` 或 `mem_alias` 並發出 `"external"` 事件（裝置位址引發 `StimulusError`）。 |
| `mem_alias(name, target, *, endian=None, bit_order=None) -> None` | 在此模擬器上動態註冊記憶體別名（規格錯誤、名稱衝突或跨 cell 與裝置重疊時引發 `TargetError`）。 |
| `clear_mem_aliases() -> None` | 清除模擬器動態註冊的記憶體別名，保留 MCU 原先宣告的別名。 |
| `step(n=1) -> RunResult` | 執行最多 `n >= 1` 步，不檢查斷點。 |
| `run(*, max_steps, max_cycles=None, stuck_window=1000, raise_exceptions=False, stuck_ignore=()) -> RunResult` | 執行最多 `max_steps >= 1` 步，啟用斷點、觀察點、cycle 上限與卡住偵測。 |
| `disassemble(where, count=1) -> list[tuple[int, str, int]]` | 從 `where`（位址、標籤或 `file:line`）反組譯最多 `count >= 1` 條指令，回傳 `(addr, text, length)`。 |
| `instruction_kind(where) -> str \| None` | 回傳 `where` 位址指令的宣告 `kind`（`"call"`、`"return"` 或 `None`，不查來源行）。 |
| `subscribe(fn) -> None` | 註冊 `fn(event: Event)` 回呼以接收已發出的事件。 |
| `enable_trace(path, *, fmt="text") -> None` | 開啟 trace 輸出檔案（`fmt` 為 `"text"` 或 `"jsonl"`）。 |
| `disable_trace() -> None` | 關閉目前開啟的 trace 檔案。 |
| `recent_events(n) -> list[Event]` | 回傳緩衝區中最近最多 `n >= 0` 筆事件。 |
| `add_breakpoint(where, *, condition=None, temporary=False) -> int` | 在位址、標籤或 `file:line` / `:line` 設定斷點。 |
| `add_watchpoint(target, *, kind, condition=None) -> int` | 對暫存器、欄位、`irq.line`、`space[addr]`、`space[lo:hi]` 或 `mem_alias` 設定觀察點（`kind` 為 `"r"`、`"w"`、`"rw"`）。 |
| `delete(which) -> None` | 依整數 ID 或 `"all"` 刪除斷點／觀察點。 |
| `list_breakpoints() -> list[str]` | 回傳作用中斷點與觀察點的格式化清單。 |
| `next_breakpoint() -> int \| None` | 以 `S0` 檢查比對目前 PC／cycle／狀態並回傳第一個命中的斷點 ID（不執行也不移除暫時斷點）。 |
| `evaluate(expr) -> int` | 依目前狀態求值唯讀除錯運算式。 |
| `add_display(expr) -> int` | 註冊每次停止時自動求值的 REPL 顯示運算式並回傳 ID。 |
| `remove_display(display_id) -> None` | 移除已註冊的顯示運算式。 |
| `source_context(where=None, *, context=2) -> list[tuple[int, str, bool]]` | 回傳 `where`（預設目前 PC）鄰近的 `(行號, 文字, 是否為目前行)`；無映像或對照過期時引發 `DebugError`。 |
| `source_line(addr) -> tuple[str, int] \| None` | 回傳對應 `addr` 的 `(file, line)`，無對照則回傳 `None`。 |
| `label_at(addr) -> tuple[str, int] \| None` | 回傳小於等於 `addr` 的最近符號 `(name, offset)`，或 `None`。 |
| `read_seq(target, values) -> None` | 為單一非程式記憶體 cell 的語意讀取提供序列值（peek 不消耗；`reset()` 將位置歸零）。 |
| `at_cycle(n) -> Trigger` | 排程於 `cycle >= n` 時觸發的動作。 |
| `at_pc(where, *, times=1) -> Trigger` | 排程於 PC 抵達 `where` 時觸發的動作（`times=None` 表示無限次）。 |
| `on_read(target, *, nth) -> Trigger` | 排程於 `space[addr]` 第 `nth >= 1` 次語意讀取時觸發。 |
| `on_write(target, *, nth) -> Trigger` | 排程於 `space[addr]` 第 `nth >= 1` 次語意寫入時觸發。 |
| `list_stimuli() -> list[str]` | 列出所有作用中刺激訊號與 `read_seq` 設定的簽章字串。 |
| `clear_stimuli() -> None` | 清除所有已註冊刺激訊號與 `read_seq` 序列。 |
| `attach(device, *, space, lo, hi) -> None` | 將 `Device`（或含 `read`/`write` 的物件）掛載至非程式空間的半開區間 `space[lo:hi]`。 |
| `snapshot() -> Snapshot` | 擷取包含模擬器、裝置與刺激訊號狀態的不可變檢查點。 |
| `restore(snap) -> None` | 驗證結構簽章相容性後還原 `Snapshot` 狀態。 |

### 詳細規格：`State`、條件運算式、`mem_alias` 與 Trace 事件

- **指令 `State` 存取邊界**：指令回呼收到可寫入的 `State`，支援 `st.reg[name]`、`st.mem[space][addr]`、`st.mem[space].read_word(addr, n, order)` / `write_word(addr, n, value, order)`（`n >= 1`，`order` 為 `"big"` 或 `"little"`）、`st.irq[line]`、`st.pc`、`st.next_pc`、`st.cycle`、`st.add_cycles(n)`、`st.halt(reason)` 與 `st.fault(code, message="")`。所有寫入都會記入日誌，當語意函式或 `Device` `read`/`write` 引發例外時自動回滾。
- **停止原因**：`RunResult.stop_reason` 可能為 `"step_done"`、`"halted"`、`"breakpoint"`、`"watchpoint"`、`"max_steps"`、`"max_cycles"`、`"stuck"`、`"fault"`、`"interrupted"`、`"python_error"`、`"undefined_opcode"`、`"out_of_range"`、`"readonly_write"` 或 `"uninitialized_read"`。
- **條件運算式語言**：受限的整數／布林 AST，不含 Python builtins、`eval()`、浮點數或 `/` 運算子。
  - 可用目標：`pc`、`cycle`、`r.reg`（含 `.field`）、`m.space[addr]`、`m.mem_alias`、`i.line`，以及明確下標 `r["name"]`、`m["space"][addr]`、`m["mem_alias"]`、`i["line"]`。
  - 運算子：一元 `+`、`-`、`~`、`not`；二元 `+`、`-`、`*`、`//`、`%`、`<<`、`>>`、`&`、`^`、`|`、`**`；鏈式比較 `==`、`!=`、`<`、`<=`、`>`、`>=` 與末尾搭配 tuple/set 字面值的 `in` / `not in`；條件運算式 `a if cond else b`；具備短路語意並回傳被選中運算元的 `and` / `or`。
  - 允許呼叫：`to_signed`、`to_unsigned`、`sign_extend`、`bit`、`parity`，以及整數方法 `.bit_count()` 與 `.bit_length()`。
  - 安全上限：AST 最大深度 `256`，最大整數位寬 `65536` 位元。語法、AST 或目標錯誤於安裝時引發 `DebugError`；算術錯誤、位寬溢位、裝置 peek 與已清除的 `mem_alias` 於求值時引發 `DebugError`。
- **超寬整數 JSONL 輸出**：絕對值小於 `10**640` 的整數以 JSON 整數輸出；大於等於 `10**640` 時無損輸出為 `"0x..."`（已知位寬時補零）或 `"-0x..."` 字串。
- **事件資料類別**：
  - `WriteRecord(kind, name, addr, old, new, bits, is_field)`
  - `ReadRecord(space, addr, value, bits)`
  - `Event(kind, seq, cycle, pc, cells, cell_bits, pc_bits, name, text, writes, reads, consumed, code, detail)`
  - `StuckReport(pcs, reads)`
  - `RunResult(stop_reason, steps, instructions, cycles, cycle, pc, detail, exception, stuck_report)`

### 範例：`mem_alias`、Context Manager 與 `__repr__`

```python
from rpkbin.isasim import MCU, Simulator, assemble

mcu = MCU("alias_demo")
mcu.space("prog", cell_bits=8, size=16, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=16, access="rw", reset_state="uninitialized", init=0)
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("r0", bits=16, reset=0)
mcu.mem_alias("word16", "ram[0x00:0x02]", endian="little", bit_order="msb_first")
mcu.mem_alias("flag0", "ram[0x02][0]")
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00000001", syntax="HALT", cycles=1)
def op_halt(st):
    st.halt("done")

img = assemble(mcu, "HALT\n", filename="alias.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(img)
    sim.load_cells("ram", {0: 0x34, 1: 0x12, 2: 0x01})
    sim.mem_alias("high_byte", "ram[0x01][7:0]", bit_order="msb_first")
    print("word16:", hex(sim.read("word16")), "flag0:", sim.read("flag0"), "high_byte:", hex(sim.read("high_byte")))
    print("ranges:", sim.initialized_ranges("ram"))
    print("sim_aliases:", list(sim.mem_aliases))
    sim.clear_mem_aliases()
    print("after_clear:", list(sim.mem_aliases))
    snap = sim.snapshot()
    print(repr(mcu))
    print(repr(img))
    print(repr(sim))
    print(repr(snap))
```

輸出：
```text
word16: 0x1234 flag0: 1 high_byte: 0x12
ranges: ((0, 3),)
sim_aliases: ['word16', 'flag0', 'high_byte']
after_clear: ['word16', 'flag0']
MCU('alias_demo', spaces=2, registers=2, instructions=1)
Assembly('alias.asm', cells=1, symbols=0)
Simulator('alias_demo', pc=0x0, cycle=0, steps=0)
Snapshot(mcu='alias_demo', cycle=0, steps=0)
```

## `Device` 與 `Trigger`

外部周邊介面與步界排程刺激訊號。

### `Device` 類別結構

繼承 `Device`（或提供具備 `read` 與 `write` 方法的物件），並透過 `Simulator.attach(device, *, space, lo, hi)` 掛載。

#### 類別屬性

| 屬性 | 型別 | 預設值 | 意義 |
| :--- | :--- | :--- | :--- |
| `time_dependent` | `bool` | `False` | 設為 `True` 時，掛載期間停用卡住偵測。 |

#### 物件函式

| 方法 | 簽名 | 說明 |
| :--- | :--- | :--- |
| `read` | `read(self, addr: int, now: int) -> int`（或 `(self, addr, now, ctx)`） | 必填。回傳累計週期 `now` 時 `addr` 的值（自動依 `cell_bits` 遮罩）。預設引發 `NotImplementedError`。 |
| `write` | `write(self, addr: int, value: int, now: int) -> None`（或 `(self, addr, value, now, ctx)`） | 必填。處理於週期 `now` 寫入 `addr` 的遮罩後數值。預設引發 `NotImplementedError`。 |
| `tick` | `tick(self, cycles: int, now: int) -> None` | 選配的提交後勾點，傳入該步消耗 cycle 數與更新後的 `now`。預設不做任何動作。 |
| `reset` | `reset(self) -> None` | 選配勾點，由 `Simulator.reset()` 呼叫。預設不做任何動作。 |
| `get_state` | `get_state(self) -> dict[str, object]` | 回傳可序列化為 JSON 的裝置狀態供 `Simulator.snapshot()` 使用。預設引發 `NotImplementedError`（`SnapshotError`）。 |
| `set_state` | `set_state(self, state: dict[str, object]) -> None` | 在 `Simulator.restore()` 時還原裝置狀態。預設引發 `NotImplementedError`（`SnapshotError`）。 |

- **選用 `ctx` 參數**：當 `read` 的第 3 個位置參數（`self` 之後）或 `write` 的第 4 個位置參數（`self` 之後）命名為 `ctx` 時，`Simulator` 會傳入回呼上下文，提供 `ctx.reg[name]`（支援暫存器、別名與欄位）及 `ctx.add_cycles(n)`（`n >= 0`）。上下文寫入會納入單步回滾日誌。在 `interrupt_check` 期間呼叫 `ctx.reg` 寫入或 `ctx.add_cycles()` 會引發 `StateAccessError`。不可透過 `ctx.reg` 寫入 PC；回呼結束後 `ctx` 與保留的 `ctx.reg` 參照均立即失效。
- **範圍衝突**：裝置不可掛載至程式空間，也不可與其他裝置、`read_seq` 或跨 cell `mem_alias` 重疊（引發 `DeviceError`）。

### `Trigger` 方法

由 `Simulator.at_cycle()`、`Simulator.at_pc()`、`Simulator.on_read()` 與 `Simulator.on_write()` 回傳。每個 `Trigger` 只能綁定一個終止動作：

| 方法 | 用途 |
| :--- | :--- |
| `delay(cycles: int) -> Trigger` | 將動作延遲 `cycles >= 0` 個週期再套用。 |
| `set(target: str, value: int) -> None` | 排程外部寫入暫存器、欄位、`space[addr]` 或 `mem_alias`（對裝置位址引發 `StimulusError`）。 |
| `irq(line: str, level: int) -> None` | 排程 IRQ 線電位更新（`0` 或 `1`；`line` 不含 `irq.` 前綴）。 |
| `call(fn: Callable[[Simulator], None]) -> None` | 排程接收 `Simulator` 的 Python 回呼函式。 |

- **卡住偵測（`stuck`）**：當 `stuck_window > 0` 且連續 `stuck_window` 步出現重複 PC 模式、期間又沒有暫存器／記憶體／IRQ／動態讀取活動時，停止 `run()`。尚有未觸發計時刺激或掛載 `time_dependent = True` 裝置時停用此檢查；`stuck_ignore` 可遮罩指定暫存器或欄位。

### 範例：搭配 `ctx` 與 `Snapshot` 的 `Device`

```python
from rpkbin.isasim import Device, MCU, Simulator, assemble

class CounterDev(Device):
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

dmcu = MCU("dev_demo")
dmcu.space("prog", cell_bits=8, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
dmcu.space("mmio", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0, role="inout")
dmcu.reg("pc", bits=4, reset=0, is_pc=True)
dmcu.reg("acc", bits=8, reset=0)
dmcu.reg("bias", bits=8, reset=5)
dmcu.on_undefined_opcode("stop")
dmcu.on_out_of_range("stop")
dmcu.on_write_to_readonly("stop")
dmcu.on_uninitialized_read("stop")

@dmcu.instr("00010000", syntax="WRDEV", cycles=1)
def wrdev(st):
    st.mem["mmio"][0] = 10

@dmcu.instr("00100000", syntax="RDDEV", cycles=1)
def rddev(st):
    st.reg["acc"] = st.mem["mmio"][0]

@dmcu.instr("00110000", syntax="END", cycles=1)
def dend(st):
    st.halt("done")

with Simulator(dmcu) as dsim:
    dsim.attach(CounterDev(), space="mmio", lo=0, hi=1)
    dsim.load_assembly(assemble(dmcu, "WRDEV\nRDDEV\nEND\n", filename="dev.asm"))
    dsim.step()
    snap = dsim.snapshot()
    res1 = dsim.run(max_steps=10)
    val1 = dsim.read("acc")
    dsim.restore(snap)
    res2 = dsim.run(max_steps=10)
    print(res1.stop_reason, res2.cycles, val1, dsim.read("acc") == val1)
```

輸出：
```text
halted 3 16 True
```

## `Snapshot`

不可變的檢查點物件，保存模擬器狀態、裝置狀態與刺激訊號執行期狀態。

### 建立方式

```python
snap = sim.snapshot()
snap = Snapshot.load(path: str | os.PathLike[str])
```

### 物件函式

| 方法 | 用途 |
| :--- | :--- |
| `save(path)` | 寫入檢查點 JSON（`mcu_name`、`signature`、`state`），將超寬整數（`abs >= 10**640`）編碼為十六進位字串（I/O 錯誤引發 `SnapshotError`）。 |
| `Snapshot.load(path)` | 讀取並驗證檢查點 JSON，將寬十六進位字串還原為 `int`。 |
| `sim.restore(snap)` | 檢查結構相容性（`spaces`、`registers`、`irq_lines`、`devices`、`stimuli` 以及非空的 `aliases`、`mem_aliases`、`regions`）並還原狀態（不相容時引發 `SnapshotError`）。 |
| `repr(snap)` | 回傳 `Snapshot(mcu='<name>', cycle=<n>, steps=<n>)`。 |

對 `Snapshot` 指派屬性會引發 `AttributeError`。現有的斷點、顯示運算式與事件訂閱者會在 `restore()` 後繼續沿用。

## `rpkbin.isasim.helpers`

供指令模型使用的明確位寬整數運算與位元操作工具函式（`from rpkbin.isasim import helpers`）。所有函式遇到 `bool` 或不合法的位寬／範圍時皆會引發 `UsageError`。

### 結果資料類別

| 類別 | 欄位 | 說明 |
| :--- | :--- | :--- |
| `AddResult` | `result: int`、`carry: int`、`overflow: int` | `add_with_carry` 回傳的不可變 dataclass。`carry` 為無號進位（`0` 或 `1`）；`overflow` 為有號二補數溢位（`0` 或 `1`）。 |
| `SubResult` | `result: int`、`borrow: int`、`overflow: int` | `sub_with_borrow` 回傳的不可變 dataclass。當 `a < b + borrow_in` 時 `borrow` 為 `1`，否則為 `0`；`overflow` 為有號二補數溢位（`0` 或 `1`）。 |

### 語法

```python
helpers.to_unsigned(value: int, bits: int) -> int
helpers.to_signed(value: int, bits: int) -> int
helpers.sign_extend(value: int, from_bits: int, to_bits: int) -> int
helpers.bit(value: int, n: int) -> int
helpers.set_bit(value: int, n: int, b: int) -> int
helpers.parity(value: int) -> int
helpers.rotate_left(value: int, n: int, bits: int) -> int
helpers.rotate_right(value: int, n: int, bits: int) -> int
helpers.add_with_carry(a: int, b: int, carry_in: int, bits: int) -> AddResult
helpers.sub_with_borrow(a: int, b: int, borrow_in: int, bits: int) -> SubResult
helpers.carry_out_of_bit(a: int, b: int, carry_in: int, bit_index: int) -> int
```

### 說明與引數

| 函式 | 引數與條件 | 回傳值 |
| :--- | :--- | :--- |
| `to_unsigned(value, bits)` | `value: int`，`bits >= 1` | `value & ((1 << bits) - 1)`。 |
| `to_signed(value, bits)` | `value: int`，`bits >= 1` | 範圍為 `-(1 << (bits - 1)) .. (1 << (bits - 1)) - 1` 的二補數有號整數。 |
| `sign_extend(value, from_bits, to_bits)` | `value: int`，`from_bits >= 1`，`to_bits >= from_bits` | 從 `from_bits` 符號擴展為 `to_bits` 位元的無號整數。 |
| `bit(value, n)` | `value: int`，`n >= 0` | 單一位元 `(value >> n) & 1`。 |
| `set_bit(value, n, b)` | `value: int`，`n >= 0`，`b in (0, 1)` | 將第 `n` 位元改為 `b` 後的整數。 |
| `parity(value)` | `value >= 0` | 若 `value` 的 `1` 位元數為奇數則回傳 `1`，偶數回傳 `0`。 |
| `rotate_left(value, n, bits)` | `value: int`，`n >= 0`，`bits >= 1` | 遮罩為 `bits` 位元後向左旋轉 `n % bits` 位。 |
| `rotate_right(value, n, bits)` | `value: int`，`n >= 0`，`bits >= 1` | 遮罩為 `bits` 位元後向右旋轉 `n % bits` 位。 |
| `add_with_carry(a, b, carry_in, bits)` | `a: int`，`b: int`，`carry_in in (0, 1)`，`bits >= 1` | 對 `bits` 位元遮罩後的 `a` 與 `b` 回傳 `AddResult(result, carry, overflow)`。 |
| `sub_with_borrow(a, b, borrow_in, bits)` | `a: int`，`b: int`，`borrow_in in (0, 1)`，`bits >= 1` | 對 `bits` 位元遮罩後的 `a` 與 `b` 計算 `(a - b - borrow_in)`，回傳 `SubResult(result, borrow, overflow)`。 |
| `carry_out_of_bit(a, b, carry_in, bit_index)` | `a: int`，`b: int`，`carry_in in (0, 1)`，`bit_index >= 0` | 第 `bit_index` 位元的進位輸出（`((a & m) + (b & m) + carry_in) >> (bit_index + 1)`）。 |

### 範例：全部 11 個 `helpers` 函式

```python
from rpkbin.isasim import helpers

print("to_unsigned:", hex(helpers.to_unsigned(-5, 8)))
print("to_signed:", helpers.to_signed(0xFB, 8))
print("sign_extend:", hex(helpers.sign_extend(0x8A, 8, 16)))
print("bit/set_bit:", helpers.bit(0b1010, 3), bin(helpers.set_bit(0b1010, 0, 1)))
print("parity:", helpers.parity(0b1011), helpers.parity(0b1001))
print("rot:", hex(helpers.rotate_left(0x81, 1, 8)), hex(helpers.rotate_right(0x81, 1, 8)))
print("add:", helpers.add_with_carry(0x7F, 0x01, 0, 8))
print("sub:", helpers.sub_with_borrow(0x00, 0x01, 0, 8))
print("half_carry:", helpers.carry_out_of_bit(0x0F, 0x01, 0, 3))
```

輸出：
```text
to_unsigned: 0xfb
to_signed: -5
sign_extend: 0xff8a
bit/set_bit: 1 0b1011
parity: 1 0
rot: 0x3 0xc0
add: AddResult(result=128, carry=0, overflow=1)
sub: SubResult(result=255, borrow=1, overflow=0)
half_carry: 1
```

## CLI 與 REPL

### 語法

```bash
rpk-isasim [-h] [-V] COMMAND ...
rpk-isasim assemble MCU.py SOURCE.asm [-o OUTPUT] [--listing LISTING] [-I DIR]... [-D NAME[=VAL]]... [--endian {little,big}] [--case-insensitive]
rpk-isasim run MCU.py [PROGRAM] --max-steps N [--format {hex,bin,asm,image}] [--space NAME] [--order {big,little}] [--base N] [--symbols FILE] [--script FILE] [--trace-reads] [-I DIR]... [-D NAME[=VAL]]... [--endian {little,big}] [--case-insensitive] [--max-cycles N] [--stuck-window N] [--stuck-ignore NAME]... [--trace FILE] [--trace-format {text,jsonl}]
rpk-isasim repl MCU.py [PROGRAM] [--format {hex,bin,asm,image}] [--space NAME] [--order {big,little}] [--base N] [--symbols FILE] [--script FILE] [--trace-reads] [-I DIR]... [-D NAME[=VAL]]... [--endian {little,big}] [--case-insensitive]
```

```python
from rpkbin.isasim.cli import main
rc = main(argv: list[str] | None = None) -> int
```

### 說明

`rpk-isasim`、`python -m rpkbin.isasim` 與 `rpkbin.isasim.cli.main(argv)` 共用同一套參數解析與執行流程（`main(argv)` 傳入不含程式名稱的引數清單）。

- `assemble`：依 `MCU.py` 組譯 `SOURCE.asm`，未指定 `-o` / `--output` 時預設寫至 `<source_parent>/<source_stem>.isa.json`。輸出與 `--listing` 路徑不得與 `MCU.py`、`SOURCE.asm`、任何引入檔或彼此重複。
- `run`：執行 `PROGRAM`（或設定腳本載入的狀態）直到停機或停止條件，並輸出 `RESULT stop_reason=... steps=... instructions=... cycles=... pc=...`。
- `repl`：載入 `PROGRAM` 並執行選配的 `--script` `setup(sim)` 後，透過標準輸入／輸出啟動互動式除錯器。

系統依 `.asm`（`asm`）與 `.isa.json`（`image`）副檔名自動判別格式；HEX 與 BIN 檔案必須明確指定 `--format`，BIN 亦須提供 `--base`。ASM 與 image 輸入不接受 `--space`、`--base`、`--order` 與 `--symbols`。在 `run` 與 `repl` 上使用 `-I`、`-D`、`--endian` 與 `--case-insensitive` 時，輸入格式必須為 ASM。

### 範例

```bash
python -m rpkbin.isasim --version
rpk-isasim assemble playground/isasim_demo/demo_mcu.py playground/isasim_demo/demo_loop.asm -I inc -D FLAG=1 --endian little --case-insensitive -o build/demo.isa.json --listing build/demo.lst
rpk-isasim run playground/isasim_demo/demo_mcu.py playground/isasim_demo/demo_loop.asm --max-steps 500
```

輸出（`--version`）：
```text
python -m rpkbin.isasim 0.3.0
```

### 選項

#### 頂層選項

| 旗標 | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `-h`、`--help` | `-` | `-` | 顯示說明訊息並結束。 |
| `-V`、`--version` | `-` | `-` | 印出 `<prog> <version>`（例如 `rpk-isasim 0.3.0`）並結束。 |

#### `assemble`、`run` 與 `repl` 選項

| 旗標 | 環境變數 | 預設值 | 適用子命令 | 說明 |
| :--- | :--- | :--- | :--- | :--- |
| `-o OUTPUT`、`--output OUTPUT` | `-` | `<source_parent>/<source_stem>.isa.json` | `assemble` | 輸出 `.isa.json` bundle 路徑。 |
| `--listing LISTING` | `-` | `None` | `assemble` | 輸出格式化組譯清單檔案。 |
| `-I DIR`、`--include-dir DIR` | `-` | `[]` | `assemble`、`run`/`repl`（ASM） | 可重複指定的引入檔搜尋目錄。 |
| `-D NAME[=VAL]` | `-` | `[]` | `assemble`、`run`/`repl`（ASM） | 可重複指定的整數常數（`NAME` 預設設為 `1`；`VAL` 接受十進位／`0x`／`0b`／`0o` 字面值如 `-0x2`，不接受運算式）。 |
| `--endian {little,big}` | `-` | `None` | `assemble`、`run`/`repl`（ASM） | 8 位元程式空間中多位元組 `.dw` 資料的位元組序。 |
| `--case-insensitive` | `-` | `False` | `assemble`、`run`/`repl`（ASM） | 對標籤、常數與具名運算元不區分大小寫。 |
| `--format {hex,bin,asm,image}` | `-` | `.asm`/`.isa.json` 自動判別 | `run`、`repl` | 明確指定程式輸入格式。 |
| `--space NAME` | `-` | 程式空間 | `run`、`repl` | `hex` / `bin` 載入目標記憶體空間。 |
| `--order {big,little}` | `-` | `None` | `run`、`repl` | 多位元組 cell（`cell_bits > 8`）的位元組序。 |
| `--base N` | `-` | `None` | `run`、`repl` | 起始 cell 位址（十進位／`0x`／`0b`／`0o`），搭配 `--format bin` 時必填。 |
| `--symbols FILE` | `-` | `None` | `run`、`repl` | 舊版符號 JSON 檔案路徑。 |
| `--script FILE` | `-` | `None` | `run`、`repl` | 定義 `setup(sim)` 的 Python 設定檔。 |
| `--trace-reads` | `-` | `False` | `run`、`repl` | 在事件中加入語意記憶體讀取紀錄。 |
| `--max-steps N` | `-` | 必填 | `run` | 執行的最大步數（十進位／`0x`／`0b`／`0o`）。 |
| `--max-cycles N` | `-` | `None` | `run` | 累計週期達到 `N` 時停止。 |
| `--stuck-window N` | `-` | `1000` | `run` | 出現重複 PC 模式且無狀態變動的靜止步數門檻（`0` 停用）。 |
| `--stuck-ignore NAME` | `-` | `[]` | `run` | 可重複指定在卡住偵測時忽略的暫存器或 `reg.field`。 |
| `--trace FILE` | `-` | `None` | `run` | 將事件 trace 寫入 `FILE`。 |
| `--trace-format {text,jsonl}` | `-` | `text` | `run` | 事件 trace 輸出格式。 |

### REPL 命令與別名

REPL 支援 **25** 個命令（24 個除錯命令加上 `quit`）與 **7** 個別名（`r -> run`、`continue -> run`、`c -> run`、`s -> step`、`b -> break`、`p -> print`、`q -> quit`）：

| 命令 | 語法 | 說明 |
| :--- | :--- | :--- |
| `help` | `help [command]` | 列出所有命令與別名，或顯示單一命令語法。 |
| `run` | `run [max_steps]` | 執行直到觸發停止條件（預設 `max_steps=1000000`、`stuck_window=1000`）。別名：`r`、`continue`、`c`。 |
| `step` | `step [n]` | 執行 `n` 步（預設 `1`）並即時印出已提交事件。別名：`s`。 |
| `until` | `until <where>` | 在 `<where>` 設暫時斷點並執行（`max_steps=1000000`、`stuck_window=1000`）。 |
| `break` | `break <where> [if <expr>]` | 在位址、標籤或加引號／不加引號的 `file:line`（`:line`）設定斷點，可附帶條件。別名：`b`。 |
| `tbreak` | `tbreak <where> [if <expr>]` | 設定單次命中後自動移除的暫時斷點。 |
| `watch` | `watch <target> [r\|w\|rw] [if <expr>]` | 在暫存器、欄位、IRQ、cell、區間或 `mem_alias` 上設定觀察點（預設模式 `w`）。 |
| `delete` | `delete <id\|all>` | 依 ID 或 `"all"` 刪除斷點／觀察點。 |
| `info` | `info [break]` | 顯示斷點／觀察點清單（`info break`），或顯示 PC／cycle／steps、空間（含 regions）、暫存器、別名、`mem_aliases` 與 IRQ 狀態。 |
| `regs` | `regs` | 印出所有暫存器與欄位、暫存器別名、`mem_aliases` 與 IRQ 線電位。 |
| `mem` | `mem <space> <addr> [count]` | 從 `<addr>` 起讀取並顯示最多 `count` 個 cell（預設 `16`）。 |
| `print` | `print <expr>` | 透過 `Simulator.evaluate()` 求值 `<expr>`（須用嚴格 `r`/`m`/`i`/`pc`/`cycle` 語法）。別名：`p`。 |
| `poke` | `poke <target> <value>` | 從外部修改暫存器、欄位、cell、`irq.line` 或 `mem_alias`。 |
| `irq` | `irq <line> <0\|1>` | 從外部設定 IRQ 線電位（`poke("irq.<line>", level)`）。 |
| `stim` | `stim read_seq <target> <v,v,...> \| stim list \| stim clear` | 註冊逗號分隔的 `read_seq`、列出作用中刺激或清除全部刺激。 |
| `display` | `display <expr>` | 註冊每次停止時自動顯示的求值運算式。 |
| `undisplay` | `undisplay <id>` | 依 ID 移除顯示運算式。 |
| `disasm` | `disasm [<where>] [count]` | 反組譯 `count` 條指令（預設 `where=pc`、`count=8`）。 |
| `trace` | `trace last <n> \| trace on <path> [text\|jsonl] \| trace off` | 印出最近 `n` 筆事件、開啟 trace 檔案（預設 `text`）或關閉 trace 檔案。 |
| `snapshot` | `snapshot save\|restore\|export\|import ... \| snapshot list` | 管理記憶體中的具名檢查點並匯出／匯入 `.json` 檔案。 |
| `load` | `load hex <path> [big\|little] \| load bin <path> <base> [big\|little] \| load sym <path>` | 載入 HEX、二進位或符號 JSON 檔案。 |
| `reset` | `reset` | 呼叫 `Simulator.reset()`。 |
| `where` | `where` | 印出目前來源位置、最近標籤，以及單行來源程式碼與 cell 編碼。 |
| `source` | `source [<where>] [context]` | 印出 `<where>` 鄰近來源行（預設 `pc`、`context=2`）與指令 cell 十六進位。 |
| `quit` | `quit` | 正常結束 REPL。別名：`q`。 |

`print`、`display` 與停止原因細節遇到超大整數（`abs(value) >= 10**640` 或超過 Python 執行期 `int_max_str_digits` 上限）時會自動切換為十六進位格式，避免中斷除錯階段。

### 結束碼與注意事項

- `0`：`assemble` 成功、`run` 以 `"halted"` 停機，或 `repl` 正常結束。
- `1`：`run` 因其他原因停止（`breakpoint`、`watchpoint`、`max_steps`、`max_cycles`、`stuck`、`fault`、`interrupted` 或政策／執行期錯誤），細節輸出至 `stderr`。
- `2`：CLI 參數錯誤、`IsaSimError` 或檔案／描述載入失敗。

## 例外型別

所有套件錯誤皆繼承自 `IsaSimError`：

| 例外 | 基底類別 | 觸發情境 |
| :--- | :--- | :--- |
| `MCUDefinitionError` | `IsaSimError` | 無效的 `MCU` 宣告、`mem_alias` 語法錯誤、驗證失敗或 `load_mcu()` 失敗。 |
| `UsageError` | `IsaSimError` | 無效的 API 引數、`helpers` 邊界／型別錯誤、trace 狀態錯誤或 CLI 路徑衝突。 |
| `LoadError` | `IsaSimError` | 找不到或格式錯誤的 HEX、二進位、符號、bundle 或設定腳本檔，或 ISA 指紋不符。 |
| `AssemblyError` | `LoadError` | 組譯階段的前處理、語法、運算式或範圍錯誤。 |
| `StateAccessError` | `IsaSimError` | 嘗試寫入唯讀 `Simulator.state`、使用已過期 `Device` `ctx`，或用 `read()` 讀取裝置 cell。 |
| `TargetError` | `IsaSimError` | 無效的 `read`/`poke`/觀察點目標、無效的 `Simulator.mem_alias()`，或無效來源位置。 |
| `DebugError` | `IsaSimError` | 無效條件 AST／目標、過期的組譯來源對照、未知斷點／顯示 ID 或失效的 `mem_alias`。 |
| `StimulusError` | `IsaSimError` | 無效的 `Trigger` / `read_seq` 目標或數值，或 `Trigger.set` 指向裝置或已清除的 `mem_alias`。 |
| `DeviceError` | `IsaSimError` | 無效的 `attach()` 範圍、缺少 `read`/`write`，或與裝置／`read_seq`／跨 cell `mem_alias` 重疊。 |
| `SnapshotError` | `IsaSimError` | 快照 I/O 或格式錯誤、裝置未實作 `get_state`/`set_state`，或 `restore()` 簽章不相容。 |

## 延伸閱讀

- [快速入門](README.md)
- [使用情境](scenarios.md)
- [組譯器](assembler.md)
- [架構](architecture.md)
- [視覺化瀏覽器除錯器（`isasim_gui`）](../app/isasim_gui/README.md)
