# `rpkbin.isasim` 組譯器與來源除錯

[English](../../en/isasim/assembler.md) · [文件導覽](../README.md) · [快速入門](README.md) · [使用情境](scenarios.md) · [API 參考](reference.md) · [架構](architecture.md)

兩階段組譯器依 `MCU` 宣告將主組譯檔案與 `.include` 引入樹轉成經過驗證的 `.isa.json` bundle，過程中不會執行任何指令回呼函式。

## 語法

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
image = Assembly.load(mcu, path)
sim.load_assembly(image)
rows = sim.source_context(where=None, *, context=2)
```

```bash
rpk-isasim assemble MCU.py SOURCE.asm [-o OUTPUT] [--listing LISTING] [-I DIR]... [-D NAME[=VAL]]... [--endian {little,big}] [--case-insensitive]
```

## 說明

- `Operand(...)` 為指令編碼欄位指定有號數、相對位址（`"pc"` 或 `"next_pc"`）、具名列舉對照表或閉區間整數範圍。
- `assemble(mcu, source, ...)` 執行前處理與兩階段配置／編碼，回傳 `Assembly` 物件。
- `Assembly.load(mcu, path)` 會驗證保存的 SHA-256 雜湊、ISA 指紋與重建後的 cell 資料，通過後才回傳 `Assembly`。
- `Simulator.load_assembly(image)` 以原子方式替換程式記憶體並建立來源行對照，供 `source_context()`、`source_line()` 與 `filename:line` / `:line` 斷點使用。
- `rpk-isasim assemble` 在未指定 `-o` / `--output` 時，預設輸出至 `<source_parent>/<source_stem>.isa.json`。

## 宣告指令運算元

`MCU.instr()` 以 `syntax` 範本作為組譯語法，並透過 `operands` 設定每個編碼字母欄位：

```text
MCU.instr(pattern, *, syntax, cycles, name=None, operands=None, overrides=None, kind=None) -> decorator
Operand(signed: bool = False, relative: str | None = None, names: Mapping[str, int] | None = None, range: tuple[int, int] | None = None)
```

| 引數 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `signed` | `bool` | `False` | 將欄位數值以二補數有號整數編碼，而非無號原始整數。 |
| `relative` | `str \| None` | `None` | 設為 `"pc"` 或 `"next_pc"` 時，組譯輸入為絕對目標 cell 位址，組譯器會編碼相對於該基準的有號位移，不依 PC 位寬環繞。 |
| `names` | `Mapping[str, int] \| None` | `None` | 將識別碼名稱對應至欄位寬度內的唯一整數；反組譯時會還原為宣告名稱。 |
| `range` | `tuple[int, int] \| None` | `None` | 在位址配置階段檢查的閉區間 `(lo, hi)` 篩選條件（不可依賴標籤）。 |

每個編碼欄位佔位符都必須在 `syntax` 中恰好出現一次。缺少、重複、相鄰或僅以空白隔開的佔位符都會被拒絕，必須使用明確分隔符，例如 `OP {a}, {b}` 或 `OP r{a}, r{b}`。Don't-care（`-`）位元一律編碼為 0。`names` 與 `range` 都不能與 `relative` 併用。當同一個助記詞有多個變體時，`range` 用於篩選可用範本，組譯結果必須恰好符合一個範本。`disassemble()` 會將有號欄位還原為有號整數、將相對欄位還原為絕對目標位址；指令回呼函式與 trace 事件仍收到或呈現原始解碼位元值。

## 原始碼語法

組譯器採用兩階段處理。指令名稱與指示詞不分大小寫；標籤、常數與具名運算元預設區分大小寫（設定 `case_sensitive_names=False` 時會以 casefold 比對並保留宣告拼寫）。分號開始行註解。標籤可獨立成行或放在同一行陳述式前，指示詞與引數之間可使用空格或 tab。

```asm
limit = (2 + 3) * 2
.org 0x04
entry: PLUS limit
    PLUS 1 ; 再加一
    END
```

`.org` 只調整輸出 cell 位址，不會變更 MCU 的 PC 重設值或程式起始點。

| 陳述式 | 說明 |
| :--- | :--- |
| `label:` | 將標籤名稱綁定至目前的輸出 cell 位址。 |
| `NAME = expr`、`NAME EQU expr`、`.equ NAME, expr` | 定義整數常數（支援前向引用，不可形成循環）。 |
| `.org expr` | 設定目前輸出 cell 位址（必須在配置階段求值，不可依賴後方標籤）。 |
| `.cell expr[, expr ...]` | 輸出一個或多個無號原始程式 cell。 |
| `.db v1, v2, ...` | 僅適用於 `cell_bits == 8` 的程式空間，接受 `-128..255`（負數以二補數編碼）。 |
| `.dw v1, v2, ...` | 輸出 16 位元值（`-32768..65535`）；在 16 位元空間輸出一個 cell，在 8 位元空間依 `endian` 輸出兩個 cell。 |
| `.space n[, fill]`、`.ds n[, fill]` | 輸出 `n >= 0` 個以 `fill` 填滿的 cell（預設 `0`，即使 `n=0` 仍會檢查無號 cell 範圍）；`n` 不可依賴標籤。 |
| `MNEMONIC operand[, ...]` | 依 `MCU.instr()` 宣告編碼指令。 |

運算式支援十進位、`0x`、`0b`、`0o` 整數、標籤與常數、括號、一元 `+`、`-`、`~`，以及二元 `+`、`-`、`*`、`//`、`%`、`<<`、`>>`、`&`、`|`、`^`。運算式由組譯器 AST 求值器自行解析，不會交給 Python `eval()`。上限為每個運算式最多 4096 字元與 128 個 AST 節點、整數最多 4096 位元、位移量 `0..4096`，以及 64 層巢狀常數引用。`.cell` 資料行可標記標籤，但不是可執行斷點位置。

## 引入檔案與條件組譯

`.include "path"` 會先搜尋包含該指令的檔案所在目錄，再依序搜尋 `include_paths`（若主來源為 `"<string>"` 等虛擬名稱，則僅搜尋 `include_paths`）。位於主檔目錄下的檔案以相對路徑作為來源鍵，其餘檔案使用 POSIX 絕對路徑。循環引入或深度超過 16 層會引發 `AssemblyError`。輸入檔案接受 UTF-8 BOM 並統一轉為 LF 換行；雙引號內的分號會視為檔名的一部分。

`.if`、`.elseif`、`.else`、`.endif`、`.ifdef` 與 `.ifndef` 必須在同一個檔案內成對開啟與關閉。未啟用分支不會進行運算式求值，也不會開啟其中的 `.include` 檔案。`.if` 與 `.elseif` 只能使用相依項已定義且不含標籤的常數；`.ifdef` 與 `.ifndef` 僅檢查常數名稱是否已定義。條件運算優先序由一元 `+`、`-`、`~`、`not`、`!` 開始，經算術與位元運算、左向右結合的比較運算（`==`、`!=`、`<`、`<=`、`>`、`>=`）、`and` 到 `or`，最後回傳 `0` 或 `1`（`and` 與 `or` 兩側皆會求值）。

### 範例 1：引入檔案、條件組譯與可攜式 Bundle

組譯一個含 `.include` 與條件分支的程式，啟用 `defines` 與 `case_sensitive_names=False`，將 `.isa.json` bundle 存檔並刪除被引入檔，最後從 bundle 載入執行。

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from rpkbin.isasim import Assembly, MCU, Simulator, assemble, helpers

mcu = MCU("demo")
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
    included = root / "amount.inc"
    included.write_text("AMOUNT EQU 2\n", encoding="utf-8")
    source = """
.include "amount.inc"
.if (not OFF) == 1
PLUS amount
.endif
END
"""
    image = assemble(
        mcu,
        source,
        include_paths=[root],
        defines={"OFF": 0},
        case_sensitive_names=False,
    )
    bundle = root / "demo.isa.json"
    image.save(bundle)
    included.unlink()

    with Simulator(mcu) as sim:
        sim.load_assembly(Assembly.load(mcu, bundle))
        result = sim.run(max_steps=4)
        print(result.stop_reason, sim.read("value"))
```

輸出：
```text
halted 2
```

## Bundle 格式與來源記錄

| 成員 | 型別 | 說明 |
| :--- | :--- | :--- |
| `Assembly.cells` | `dict[int, int]` | 輸出的位址對 cell 值字典副本。 |
| `Assembly.symbols` | `dict[str, int]` | 標籤對位址字典副本。 |
| `Assembly.constants` | `dict[str, int]` | 已解析常數字典副本（含 `defines`）。 |
| `Assembly.sources` | `dict[str, str]` | 依來源鍵儲存的原始碼文字字典副本。 |
| `Assembly.main_key` | `str` | `Assembly.sources` 中主檔案的唯讀來源鍵。 |
| `Assembly.records()` | `tuple[SourceRecord, ...]` | 依 listing 順序傳回 `SourceRecord(addr, file, line, cells, is_instruction)`。 |
| `Assembly.listing()` | `str` | 格式化的位址、cell 十六進位、`file:line` 與原始碼內容。 |
| `Assembly.save(path)` | `None` | 寫出無 BOM 的 UTF-8 JSON（LF 換行）。 |
| `Assembly.load(mcu, path)` | `Assembly` | 驗證格式、SHA-256 摘要、ISA 指紋與重建後內容的一致性。 |

不含前處理指示詞且 `defines`、`endian` 與 `case_sensitive_names=True` 皆為預設值時，輸出格式為 `"isasim-assembly-1"`。只要包含前處理指示詞、非空 `defines`、明確的 `endian` 或 `case_sensitive_names=False`，輸出格式即為 `"isasim-assembly-2"`（額外保存 `"sources"`、`"main"`、`"sources_sha256"`、`"options"`、`"includes"` 與 `"constants"`）。`Assembly.load()` 會檢查內部一致性與 ISA 指紋相容性（`load_mcu()` 會將 MCU 描述檔位元組納入指紋計算，但描述檔自行匯入的輔助模組內容不計入其中）。

### 範例 2：檢查 `SourceRecord` 與來源斷點

載入 `playground/isasim_demo/demo_mcu.py`，組譯短程式後檢查 `Assembly.records()` 與來源斷點比對。請在專案根目錄執行。

```python
from rpkbin.isasim import Simulator, assemble, load_mcu

mcu = load_mcu("playground/isasim_demo/demo_mcu.py")
image = assemble(mcu, "LDI r0, 1\nHLT\n", filename="main.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(image)
    program = next(space for space in sim.describe().spaces if space.is_program)
    first = image.records()[0]
    assert program.name == "prog"
    assert sim.source_line(first.addr) == (image.main_key, first.line)

    bp_id = sim.add_breakpoint(f"{image.main_key}:{first.line}", temporary=True)
    print(repr(image))
    print(first)
    print("next_bp:", sim.next_breakpoint() == bp_id)
```

輸出：
```text
Assembly('main.asm', cells=2, symbols=0)
SourceRecord(addr=0, file='main.asm', line=1, cells=(4097,), is_instruction=True)
next_bp: True
```

## 模擬器來源導覽

`Simulator.load_assembly(image)` 在驗證 bundle 無誤後，會以原子方式替換程式記憶體與來源對照。它不會重設暫存器或 cycle 計數，而 `reset()` 會還原已掛載的組譯映像並保留來源對照。透過 `load_cells()`、`load_hex()`、`load_bin()` 載入程式或透過 `load_symbols()` 載入舊版符號檔，則會清除組譯來源對照。

掛載組譯映像時，`add_breakpoint()`、`disassemble()` 以及 REPL 的 `break`、`tbreak`、`until`、`source` 都支援 `filename:line` 或 `:line`（Windows 路徑以最後一個冒號分隔檔名與行號；在 REPL 中含空白的路徑須加引號）。目標行必須包含可執行指令（`.cell` 屬於資料行）。查詢會先比對完整來源鍵，再比對唯一檔名。若重複 `.include` 使同一來源行對應多個位址，`add_breakpoint()` 會回傳單一群組 ID，而 `disassemble("part.inc:1")` 等單一位址查詢會因歧義拒絕。若透過 `poke()` 或 `restore()` 修改已對照的程式記憶體，`source_context()` 會引發 `DebugError`，新增來源斷點會引發 `TargetError`，先前已登記的斷點則保留原本解析出的位址。

## CLI 與 REPL

```bash
rpk-isasim assemble tiny_mcu.py path/to/demo.asm
# 寫出 path/to/demo.isa.json
rpk-isasim assemble tiny_mcu.py path/to/demo.asm -o build/demo.isa.json --listing build/demo.lst -I inc -D FLAG=1 --endian little --case-insensitive
rpk-isasim run tiny_mcu.py path/to/demo.asm --max-steps 100
rpk-isasim run tiny_mcu.py build/demo.isa.json --max-steps 100
rpk-isasim repl tiny_mcu.py path/to/demo.asm
```

未指定 `-o` / `--output` 時，`rpk-isasim assemble` 會寫出至 `<source_parent>/<source_stem>.isa.json`（與來源檔位於同一目錄）。輸出與 `--listing` 路徑不得與 MCU 檔、主來源檔、任何引入檔或彼此重複。`run` 與 `repl` 依副檔名自動辨識 `.asm` 與 `.isa.json`（或以 `--format asm` / `--format image` 明確指定），且讀取 ASM/image 時不接受 `--space`、`--base`、`--order` 與 `--symbols`。

### 組譯 CLI 選項

以下選項適用於 `assemble`，以及輸入格式為 `asm` 的 `run` 與 `repl`（在 `hex`、`bin` 或 `image` 格式傳入 `-I`、`-D`、`--endian`、`--case-insensitive` 會產生命令列錯誤）：

| 旗標 | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `-o OUTPUT`、`--output OUTPUT` | `-` | `<source_parent>/<source_stem>.isa.json` | 輸出的 `.isa.json` bundle 路徑（僅限 `assemble`）。 |
| `--listing LISTING` | `-` | `None` | 輸出格式化組譯清單至 `LISTING`（僅限 `assemble`）。 |
| `-I DIR`、`--include-dir DIR` | `-` | `[]` | 可重複指定的引入檔搜尋目錄。 |
| `-D NAME[=VAL]` | `-` | `[]` | 可重複指定的整數常數定義（省略值時設為 `1`；`VAL` 接受如 `-0x2` 的整數字面值，不接受運算式）。 |
| `--endian {little,big}` | `-` | `None` | 8 位元程式空間中多位元組 `.dw` 資料的位元組序。 |
| `--case-insensitive` | `-` | `False` | 對標籤、常數與具名運算元不區分大小寫。 |

在 REPL 中，`source [where] [context]` 會輸出 `filename:line pc=0xADDR`，以 `> line: text` 標示目前行、`  line: text` 顯示上下文，並在下方列出 `cells: 0x...`。`where` 與停止提示在有對照時會顯示同一份來源檢視，無對照時印出 `no source at 0xADDR`，對照過期時印出 `source unavailable: ...`。全部 25 個 REPL 命令與 7 個別名請見 [API 參考](reference.md#cli-與-repl)。

## 注意事項與限制

- **固定指令長度**：每條指令宣告的 cell 長度固定，不支援巨集、連結器或指令長度自動調整。
- **驗證邊界**：組譯成功僅代表來源依 MCU 宣告的編碼模式完成編碼，不代表該宣告符合實體晶片；目標精確度仍需可靠的硬體模型與獨立的標準參考測試。

## 延伸閱讀

- [快速入門](README.md)
- [API 參考](reference.md)
- [使用情境](scenarios.md)
- [架構](architecture.md)
