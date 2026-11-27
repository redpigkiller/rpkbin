# `rpkbin.mcucc`

[English](../../en/mcucc/README.md) · [文件導覽](../README.md) · [isasim 快速入門](../isasim/README.md)

`rpkbin.mcucc` 將受限的 Python 子集編譯為目標組語，每次編譯皆執行獨立靜態驗證，並可搭配 `isasim` 模擬器核對指令編碼與整支函式的執行結果。

## 安裝方式

需要 Python 3.11 以上版本（`pyproject.toml` 與前端 `feature_version=(3, 11)`）。在專案根目錄以可編輯模式安裝套件：

```bash
python -m pip install -e .
```

安裝後即可直接使用 `rpk-mcucc` 命令列指令，或執行 `python -m rpkbin.mcucc`。核心編譯流程只使用 Python 標準函式庫；指令形式與差異模擬驗證則使用隨附的 [`rpkbin.isasim`](../isasim/README.md)。

## CLI 參考

### 語法

```text
rpk-mcucc [-h] [-V] {compile,verify,lint-target,check-forms} ...
rpk-mcucc lint-target target
rpk-mcucc check-forms target [--seeds N]
rpk-mcucc compile source --target TARGET --spill-pool SPACE:LO-HI [--park-depth N] [--park-policy {smallest,stack_first,slot_first}] [--no-comments] [-o OUTPUT | --output OUTPUT] [--report]
rpk-mcucc verify source --target TARGET --spill-pool SPACE:LO-HI --window WINDOW [--park-depth N] [--park-policy {smallest,stack_first,slot_first}] [--inputs N] [--seed S]
python -m rpkbin.mcucc ...
```

### 說明

- `rpk-mcucc` 與 `python -m rpkbin.mcucc` 共用同一套參數解析器與子命令（`rpkbin.mcucc.cli.main`）。傳入 `-V` 或 `--version` 會依 `rpkbin.__version__` 印出 `<prog> <version>` 後結束。
- `rpk-mcucc lint-target target` 載入並驗證目標描述檔，接著於 stdout 輸出以 tab 分欄的可用性矩陣（`op`、`width`、`available`、`forms`、`min_bytes`）。
- `rpk-mcucc check-forms target [--seeds N]` 使用種子 `0` 到 `N - 1` 比對每個 `Form` 宣告與 `SIM` 模擬器行為。未綁定 `SIM` 時印出 `unverified: no SIM` 並以結束碼 `0` 結束。
- `rpk-mcucc compile source ...` 將受限 Python 來源編譯為目標組語、執行靜態驗證（`V1` 至 `V11`），並將組語輸出至 stdout 或 `-o`／`--output` 指定的檔案。加上 `--report` 會另於 stderr 輸出 JSON 報告。
- `rpk-mcucc verify source ...` 編譯各函式並以邊界值與隨機輸入對 `SIM` 執行差異測試，比對回傳值、受檢資料記憶體以及未列於 `clobbers` 的暫存器。未綁定 `SIM` 時逐函式印出 `unverified: no SIM`（或 `unverified: asm`／`unsupported: ...`）並以 `0` 結束。

### 範例

在專案根目錄執行隨附範例，依序顯示版本、檢查指令形式、編譯 `add2`，並執行差異驗證：

```bash
python -m rpkbin.mcucc --version
python -m rpkbin.mcucc check-forms rpkbin/mcucc/examples/target.py.txt --seeds 1
python -m rpkbin.mcucc compile rpkbin/mcucc/examples/program.py.txt --target rpkbin/mcucc/examples/target.py.txt --spill-pool data:0x60-0x6f --no-comments --report
python -m rpkbin.mcucc verify rpkbin/mcucc/examples/program.py.txt --target rpkbin/mcucc/examples/target.py.txt --spill-pool data:0x60-0x6f --window 256
```

輸出：

```text
python -m rpkbin.mcucc 0.3.0
c9 skipped: out_of_space=0 ret_not_fit=0
verified: forms
{"functions": {"add2": {"bytes": 2, "clobbers": ["a", "b"], "slots": [], "findings": [], "verified": true}}}
add2:
    inksum a, b
    inkdone
add2: verified
```

若目標的低位址已保留給記憶體映射 I/O 或 ABI 位置，可指定寬度至少 512 cells 的具名取樣視窗，並依需求加上堆疊暫存設定：

```bash
rpk-mcucc compile source.py --target target.py --spill-pool data:0x60-0x6f --park-depth 4 --park-policy smallest -o out.asm
rpk-mcucc verify source.py --target target.py --spill-pool data:0x60-0x6f --window data:0x300-0x5ff
```

### 選項

| 旗標 | 環境變數 | 預設值 | 說明 |
|---|---|---|---|
| `-h`、`--help` | （無） | （無） | 顯示主命令或子命令說明訊息後結束。 |
| `-V`、`--version` | （無） | （無） | 印出 `rpkbin.__version__` 對應的 `<prog> <version>` 後結束。 |
| `--target TARGET` | （無） | 必填（`compile`、`verify`） | 可信任的 Python 目標描述檔路徑，須定義 `TARGET`（可另含 `SIM`、`STUBS`）。 |
| `--spill-pool SPACE:LO-HI` | （無） | 必填（`compile`、`verify`） | 指定單一資料空間內含首尾的編譯器專用範圍（十進位或 `0x` 十六進位），不接受超過一個資料空間。 |
| `--park-depth N` | （無） | `0` | 允許同時佔用的最高 `push` 個數，計入堆疊暫存、`preserves`、呼叫保存與模組內被呼叫者深度（`0` 表示停用堆疊暫存）。 |
| `--park-policy {smallest,stack_first,slot_first}` | （無） | `smallest` | `--park-depth > 0` 時的候選選擇策略：`smallest` 取位元組較少者（平手維持未暫存版），`stack_first` 在未暫存版用到槽時改採暫存版，`slot_first` 沿用未暫存版。 |
| `--no-comments` | （無） | `False` | （僅 `compile`）關閉組語函式標頭的 `bytes`、`clobbers` 與 `slots` 註解。 |
| `-o OUTPUT`、`--output OUTPUT` | （無） | `None`（stdout） | （僅 `compile`）以 UTF-8 與 LF（`\n`）換行寫入組語至 `OUTPUT`；傳入空字串會引發 `ConfigError`（結束碼 `2`）。 |
| `--report` | （無） | `False` | （僅 `compile`）於 stderr 輸出 JSON 格式的 `CompileResult.report()`。 |
| `--window WINDOW` | （無） | 必填（`verify`） | 可為從 `0` 起始的正整數初始化範圍（如 `256`），或可對不同空間重複指定的具名區間 `SPACE:LO-HI`（十進位或 `0x` 十六進位，含首尾，寬度至少 `512`）。兩種寫法與多個整數視窗皆不可混用。 |
| `--inputs N` | （無） | `120` | （僅 `verify`）每個函式的最少取樣輸入筆數（須 `>= 1`）。 |
| `--seed S` | （無） | `0` | （僅 `verify`）差異輸入取樣使用的隨機種子。 |
| `--seeds N` | （無） | `1` | （僅 `check-forms`）檢查使用的種子數 `0..N-1`（須 `>= 1`）。 |

`rpk-mcucc` CLI 不會讀取任何環境變數。

### 結束碼與注意事項

- `0`：命令完成。請注意，當未發生比對失敗時，`unverified: no SIM`、`unverified: asm` 與 `unsupported: ...` 同樣會以 `0` 結束；判斷結果前須檢查各函式的狀態列。
- `1`：`check-forms` 發現指令驗證失敗，或 `verify` 的 `report.failures` 非空（包含回傳值／記憶體不符、未宣告的暫存器變動、pool／window 違規，或單一函式的參考執行逾時率超過 10%）。
- `2`：CLI 參數語法錯誤（`argparse`），或於 stderr 以 `error: ...` 印出的 `McuccError`（`SourceError`、`TargetError`、`UnsupportedError`、`AllocationError`、`ConfigError`、`InternalError`），例如 `verify` 漏填 `--window` 時顯示 `error: --window is required`，或 `-o ""` 傳入空路徑。

## Python API（`rpkbin.mcucc`）

### 語法

```python
from rpkbin.mcucc import CompileConfig, CompileResult, FunctionResult, compile_file, compile_source
from rpkbin.mcucc.target import load_target, load_target_file
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.formcheck import check_forms
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.cli import main

config = CompileConfig(spill_pool, comments, *, park_depth=0, park_policy="smallest")
result = compile_source(source, target, config, filename="<source>")
result = compile_file(path, target, config)
report_dict = result.report()

target = load_target(path)
target, sim, stubs = load_target_file(path)
program = assemble(result, target, origin)
failures = check_forms(target, sim, seeds)
diff_report = run_differential(source, target, sim, config, *, stubs=None, seeds, inputs_per_function, window, post_check=None)
ret, memory_after = run_reference(module_path_or_source, func_name, args, memory, *, addr_bits)
exit_code = main(argv=None)
```

### 說明

- `CompileConfig(spill_pool, comments, *, park_depth=0, park_policy="smallest")` 驗證編譯設定，並將 `spill_pool` 複製封裝為唯讀的 `MappingProxyType`。在 Python API 傳入 `{}` 可停用 spill 槽。
- `compile_source(source, target, config, filename="<source>")` 與 `compile_file(path, target, config)` 依 `target` 編譯受限 Python 模組、執行靜態驗證（`V1` 至 `V11`），並回傳 `CompileResult`。
- `result.report()` 回傳以 `"functions"` 為頂層鍵的可 JSON 序列化字典，省略 `mir` 指令紀錄，並在適用時加入 `"pool_contract": "private"` 或 `"parks"`／`"push_depth"`。
- `load_target(path)` 與 `load_target_file(path)` 透過 `runpy.run_path` 執行可信任的 Python 目標檔，驗證 `TARGET` 後回傳 `Target` 或 `(target, sim, stubs)`，未提供的選填項目為 `None`。
- `assemble(result, target, origin)` 從非負整數 `origin` 進行兩階段位址配置與編碼，回傳 `Program`。若任何函式含內嵌 `asm`，會引發 `UnsupportedError`。
- `check_forms(target, sim, seeds)` 核對 form 編碼、長度、算術、旗標、記憶體、分支、堆疊配對與呼叫／回傳。當 `sim` 為 `None` 時直接回傳 `[]`，呼叫端須自行確認 `sim is not None`。
- `run_differential(...)` 為各非外部函式編譯並組譯測試驅動程式，同步執行 `run_reference` 與 `sim`，比對回傳值、pool 以外資料記憶體及 `clobbers` 以外暫存器後回傳 `DiffReport`。
- `run_reference(module_path_or_source, func_name, args, memory, *, addr_bits)` 深層複製 `memory` 並於隔離環境執行受限 Python 參考語意，回傳 `(ret, memory_after)`。
- `main(argv=None)` 以 `prog="rpk-mcucc"` 執行命令列進入點。

### 範例

#### 1. 基本範例：編譯、讀取報告、組譯與執行參考語意

以隨附的 `target.py.txt` 編譯 `program.py.txt`，檢視 `result.report()`，呼叫 `assemble` 產生機器碼 cells，並用 `Path` 物件呼叫 `run_reference` 對照結果。

```python
from pathlib import Path
from rpkbin.mcucc import CompileConfig, compile_file
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.target import load_target

target = load_target("rpkbin/mcucc/examples/target.py.txt")
config = CompileConfig({"data": (0x60, 0x6F)}, False)
result = compile_file("rpkbin/mcucc/examples/program.py.txt", target, config)
print(result.asm.strip())
print("report:", result.report())

program = assemble(result, target, origin=0)
print("cells:", [program.cells[a] for a in sorted(program.cells)])

ret, _ = run_reference(
    Path("rpkbin/mcucc/examples/program.py.txt"),
    "add2",
    (250, 9),
    {"data": [0] * 256},
    addr_bits={"data": 8},
)
print("ref_ret:", ret)
```

輸出：

```text
add2:
    inksum a, b
    inkdone
report: {'functions': {'add2': {'bytes': 2, 'clobbers': ('a', 'b'), 'slots': [], 'findings': (), 'verified': True}}}
cells: [17, 97]
ref_ret: 3
```

#### 2. 進階範例：堆疊暫存、私有槽契約與 `borrow_msb0`

比較 `park_depth=4` 下三種 `park_policy` 策略的報告輸出，觸發跨間接寫入的私有槽重試（`pool_contract="private"`），並驗證 `borrow_msb0` 接受遮罩後的比較（`& 0x7F`）同時拒絕未證明的 `a < b`。

```python
from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import UnsupportedError
from rpkbin.mcucc.target import Abi, Cls, Flag, Form, I, M, P, R, Reg, Space, T, Target

regs = ("a", "b", "c")
park_target = Target(
    name="park_demo",
    cell_bits=8,
    spaces=(Space("code", 12, "program"), Space("data", 8, "data")),
    regs=tuple(Reg(r, 8) for r in regs),
    aliases=(),
    classes=(Cls("byte", regs),),
    flags=(Flag("Z"), Flag("C")),
    forms=(
        Form("mov", "mov", 8, (R("d", "byte"), R("s", "byte")), {"dst": "d", "a": "s"}, 1, "mov {d}, {s}"),
        Form("ldi", "const", 8, (R("d", "byte"), I("k", 8)), {"dst": "d", "a": "k"}, 2, "ldi {d}, #{k}"),
        Form("add", "add", 8, (R("d", "byte"), R("s", "byte")), {"dst": "d", "a": "d", "b": "s"}, 1, "add {d}, {s}"),
        Form("and_ri", "and", 8, (R("d", "byte"), I("k", 8)), {"dst": "d", "a": "d", "b": "k"}, 2, "and {d}, #{k}"),
        Form("ld", "ld", 8, (R("d", "byte"), M("m", "data")), {"dst": "d", "a": "m"}, 2, "ld {d}, [{m}]"),
        Form("st", "st", 8, (M("m", "data"), R("s", "byte")), {"a": "m", "b": "s"}, 2, "st [{m}], {s}"),
        Form("st_i", "st_ind", 8, (P("p", "byte", "data"), R("s", "byte")), {"a": "p", "b": "s"}, 2, "st [{p}], {s}"),
        Form("push", "push", 8, (R("s", "byte"),), {"a": "s"}, 1, "push {s}"),
        Form("pop", "pop", 8, (R("d", "byte"),), {"dst": "d"}, 1, "pop {d}"),
        Form("cmp", "cmp", 8, (R("a", "byte"), R("b", "byte")), {"a": "a", "b": "b"}, 1,
             "cmp {a}, {b}", flags_write={"C": "borrow_msb0", "Z": "zero"}),
        Form("jc", "br", 0, (T("t", rel_bits=8),), {}, 2, "jc {t}", flags_read={"C": 1}),
        Form("jnc", "br", 0, (T("t", rel_bits=8),), {}, 2, "jnc {t}", flags_read={"C": 0}),
        Form("jz", "br", 0, (T("t", rel_bits=8),), {}, 2, "jz {t}", flags_read={"Z": 1}),
        Form("jnz", "br", 0, (T("t", rel_bits=8),), {}, 2, "jnz {t}", flags_read={"Z": 0}),
        Form("jmp", "jmp", 0, (T("t", rel_bits=8),), {}, 2, "jmp {t}"),
        Form("ret", "ret", 0, (), {}, 1, "ret"),
    ),
    abi=Abi({8: regs}, {8: "a"}, ()),
    long_branch="none",
    comment=";",
    local_label=".L{fn}_{n}",
    label_def="{name}:",
)

park_src = """from rpkbin.mcucc.lang import mem, u8

def mix(a: u8, b: u8) -> u8:
    k = a + b
    x = mem.data[0x10]
    y = mem.data[0x11]
    z = mem.data[0x12]
    mem.data[0x20] = x + y
    mem.data[0x21] = y + z
    return k + mem.data[0x20] + mem.data[0x21]
"""
for policy in ("slot_first", "smallest", "stack_first"):
    cfg = CompileConfig({"data": (0x60, 0x6F)}, False, park_depth=4, park_policy=policy)
    info = compile_source(park_src, park_target, cfg).report()["functions"]["mix"]
    print(f"{policy}: bytes={info['bytes']} slots={info['slots']} parks={info['parks']} push_depth={info['push_depth']}")

private_src = """from rpkbin.mcucc.lang import Ptr, mem, u8

DataPtr = Ptr("data")

def keep_and_cmp(p: DataPtr, a: u8, b: u8) -> u8:
    k = a + b
    x = mem.data[0x10]
    y = mem.data[0x11]
    z = mem.data[0x12]
    mem.data[p] = x
    if (a & 0x7F) < (y & 0x7F):
        return k + x + y + z
    return z + 1
"""
priv_info = compile_source(
    private_src, park_target, CompileConfig({"data": (0x60, 0x6F)}, False)
).report()["functions"]["keep_and_cmp"]
print("private:", priv_info["pool_contract"], "verified:", priv_info["verified"])

try:
    compile_source(
        "from rpkbin.mcucc.lang import u8\ndef raw_lt(a: u8, b: u8) -> u8:\n    return u8(1) if a < b else u8(0)\n",
        park_target,
        CompileConfig({"data": (0x60, 0x6F)}, False),
    )
except UnsupportedError as exc:
    print("unproved_msb0:", type(exc).__name__)
```

輸出：

```text
slot_first: bytes=28 slots=[('data', 96, 1), ('data', 97, 1)] parks=0 push_depth=0
smallest: bytes=20 slots=[] parks=1 push_depth=1
stack_first: bytes=20 slots=[] parks=1 push_depth=1
private: private verified: partial: indirect memory is abstract
unproved_msb0: UnsupportedError
```

#### 3. 進階範例：差異驗證、字典格式 `window` 與內嵌 `asm`／`@extern`

先以整數 `window` 搭配 `post_check` 執行 `run_differential`，接著在 10 位元資料空間上使用 512 格的字典 `window={"data": (0x200, 0x3FF)}`，最後編譯並以參考語意執行結合 `@extern` 與內嵌 `asm` 的函式。

```python
from dataclasses import replace
from rpkbin.isasim import MCU, Simulator
from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.target import SimBinding, Space, load_target_file

base_target, base_sim, _ = load_target_file("rpkbin/mcucc/examples/target.py.txt")
depths = []
report = run_differential(
    "from rpkbin.mcucc.lang import u8\ndef add2(a: u8, b: u8) -> u8:\n    return a + b\n",
    base_target,
    base_sim,
    CompileConfig({"data": (0x60, 0x6F)}, False),
    seeds=(0,),
    inputs_per_function=10,
    window=256,
    post_check=lambda phase, sim: depths.append((phase, sim.read("depth"))),
)
print("int_window:", report.checked, report.failures, depths[:2])

def rr_op(dd, ss, add):
    def step(st):
        st.reg[dd] = ((st.reg[dd] + st.reg[ss]) if add else st.reg[ss]) & 0xFF
    return step

m = MCU("win10")
m.space("code", cell_bits=8, size=4096, access="ro", reset_state="initialized", init=0, is_program=True)
m.space("data", cell_bits=8, size=1024, access="rw", reset_state="initialized", init=0)
m.space("frames", cell_bits=8, size=4096, access="rw", reset_state="initialized", init=0)
for r in ("a", "b"):
    m.reg(r, bits=8, reset=0)
m.reg("cursor", bits=12, reset=0, is_pc=True)
m.reg("depth", bits=16, reset=0)
m.on_undefined_opcode("stop")
m.on_out_of_range("stop")
m.on_write_to_readonly("stop")
m.on_uninitialized_read("stop")
m.instr("00000000", syntax="halt", cycles=1, name="halt")(lambda st: st.halt("done"))
for d, dst in enumerate(("a", "b")):
    for s, src in enumerate(("a", "b")):
        m.instr(format(0x10 + 2 * d + s, "08b"), syntax=f"inksum {dst}, {src}", cycles=1, name=f"add_{d}_{s}")(rr_op(dst, src, True))
        m.instr(format(0x20 + 2 * d + s, "08b"), syntax=f"inkcopy {dst}, {src}", cycles=1, name=f"mov_{d}_{s}")(rr_op(dst, src, False))
m.instr("011000000000" + "t" * 12, syntax="inkcall {t}", cycles=1, name="call")(
    lambda st, t: (st.mem["frames"].write_word(st.reg["depth"], 2, st.next_pc, "little"),
                   st.reg.__setitem__("depth", st.reg["depth"] + 2),
                   setattr(st, "next_pc", t)))
m.instr("01100001", syntax="inkdone", cycles=1, name="ret")(
    lambda st: (st.reg.__setitem__("depth", st.reg["depth"] - 2),
                setattr(st, "next_pc", st.mem["frames"].read_word(st.reg["depth"], 2, "little"))))
m.instr("01100010" + "t" * 8, syntax="inkskip {t}", cycles=1, name="jmp")(
    lambda st, t: setattr(st, "next_pc", st.next_pc + (t if t < 128 else t - 256)))

target_10 = replace(base_target, spaces=(Space("code", 12, "program"), Space("data", 10, "data")))
sim_10 = SimBinding(lambda: Simulator(m), "code", "cursor", (0,))
dict_report = run_differential(
    "from rpkbin.mcucc.lang import u8\ndef add2(a: u8, b: u8) -> u8:\n    return a + b\n",
    target_10,
    sim_10,
    CompileConfig({"data": (0x60, 0x6F)}, False),
    seeds=(0,),
    inputs_per_function=10,
    window={"data": (0x200, 0x3FF)},
)
print("dict_window:", dict_report.checked, dict_report.failures)

asm_src = """from rpkbin.mcucc.lang import asm, extern, u8

@extern(args=("a",), ret="a", clobbers=())
def existing(x: u8) -> u8:
    return x + 1

def step_both(a: u8) -> u8:
    asm("inc {0}", ins=((a, "a"),), outs=((a, "a"),),
        clobbers=("b",), nbytes=1, ref=lambda v: (v + 1) & 0xFF)
    return existing(a)
"""
asm_info = compile_source(asm_src, base_target, CompileConfig({"data": (0x60, 0x6F)}, False)).report()["functions"]["step_both"]
ref_val, _ = run_reference(asm_src, "step_both", (10,), {"data": [0] * 256}, addr_bits={"data": 8})
print("asm_verified:", asm_info["verified"], "ref_val:", ref_val)
```

輸出：

```text
int_window: 120 () [('before', 0), ('after', 0)]
dict_window: 120 ()
asm_verified: unverified: asm ref_val: 12
```

### 輸入引數

| 名稱 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `spill_pool` | `Mapping[str, tuple[int, int]]` | 必填（`CompileConfig`） | 最多一個資料空間的含首尾 `(low, high)` 範圍，或傳入 `{}` 停用 spill 槽。 |
| `comments` | `bool` | 必填（`CompileConfig`） | 必須為 `bool`；控制是否在組語輸出函式標頭的大小、clobbers 與槽位註解。 |
| `source` | `str` | 必填（`compile_source`、`run_differential`） | 受限 Python 來源程式碼文字。 |
| `path` | `str \| PathLike` | 必填（`compile_file`、`load_target`、`load_target_file`） | UTF-8 來源檔或可信任目標描述檔路徑。 |
| `target` | `Target` | 必填 | 已驗證的目標機器宣告。 |
| `config` | `CompileConfig` | 必填 | 編譯設定，含 spill pool、註解開關與堆疊暫存選項。 |
| `filename` | `str` | `"<source>"`（`compile_source`） | 診斷訊息顯示的檔名。 |
| `origin` | `int` | 必填（`assemble`） | 程式空間起始位址（非負整數）。 |
| `sim` | `SimBinding \| None` | 必填 | 模擬器綁定（`check_forms` 傳 `None` 回傳 `[]`；`run_differential` 須傳 `SimBinding`）。 |
| `seeds` | `Iterable[int]` | 必填（`check_forms`、`run_differential`） | 用於指令檢查或差異測試輸入取樣的種子序列。 |
| `inputs_per_function` | `int` | 必填（`run_differential`） | 每個函式的最少差異輸入數（含兩個以上參數時至少 120 筆）。 |
| `window` | `int \| Mapping[str, tuple[int, int]]` | 必填（`run_differential`） | 從 `0` 起始的整數初始化數量，或寬度至少 `512` 的 `{space: (low, high)}` 含首尾區間字典。 |
| `module_path_or_source` | `Path \| str` | 必填（`run_reference`） | `Path` 物件、單行 `.py` 路徑字串，或多行來源程式碼字串。 |
| `func_name` | `str` | 必填（`run_reference`） | 要執行的函式名稱。 |
| `args` | `Sequence[int]` | 必填（`run_reference`） | 依 `func_name` 參數型別轉型的位置引數。 |
| `memory` | `dict[str, list[int]]` | 必填（`run_reference`） | 以資料空間名稱為鍵的初始 cell 清單字典（每次呼叫皆深層複製）。 |
| `argv` | `Sequence[str] \| None` | `None`（`main`） | 不含執行檔名的 CLI 引數清單（`None` 時讀取 `sys.argv[1:]`）。 |

### 具名引數（Keyword-Only）

| 名稱 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `park_depth` | `int` | `0`（`CompileConfig`） | 堆疊暫存、保存暫存器與被呼叫者累計的最高同時 `push` 個數（非負整數）。 |
| `park_policy` | `str` | `"smallest"`（`CompileConfig`） | `park_depth > 0` 時的策略：`"smallest"`、`"stack_first"` 或 `"slot_first"`。 |
| `stubs` | `Mapping[str, tuple[int, ...]] \| None` | `None`（`run_differential`） | 以 `@extern` 函式名為鍵的機器碼 cell tuple（`None` 視為 `{}`）。 |
| `post_check` | `Callable[[str, Simulator], None] \| None` | `None`（`run_differential`） | 於模擬前後以 `("before", sim)` 與 `("after", sim)` 呼叫；拋出 `AssertionError` 計為該輸入失敗。 |
| `addr_bits` | `Mapping[str, int]` | 必填（`run_reference`） | 來源透過 `Ptr` 或 `mem` 使用之各資料空間位址寬度（`8` 或 `16`）。 |

### 輸出

- `compile_source` 與 `compile_file` 回傳 `CompileResult(asm, functions)`，其中 `asm` 為組語字串，`functions` 依來源順序對應 `FunctionResult(name, bytes, clobbers, slots, findings, verified, mir)`。
- `CompileResult.report()` 回傳 `{"functions": {name: entry, ...}}`，各函式項目欄位如下：
  - `bytes`（`int`）：函式組語的靜態位元組數。
  - `clobbers`（`tuple[str, ...]`）：呼叫後可能改動的基本暫存器。
  - `slots`（`list[tuple[str, int, int]]`）：配置的私有槽清單，每項為 `(space, start, cells)`。
  - `findings`（`tuple[dict, ...]`）：尚可簡化的 peephole 樣式紀錄（`rule`、`function`、`index`）。
  - `verified`（`bool | str`）：`True`、`"partial: ..."`（因間接存取或呼叫等抽象追蹤而受限的原因），或 `"unverified: asm"`。
  - `pool_contract`（`str`，選填欄位）：僅於保守配置失敗並成功改採私有槽模式時出現，值為 `"private"`。
  - `parks`、`push_depth`（`int`，選填欄位）：僅於 `park_depth > 0` 時出現，分別記錄堆疊暫存 `push`／`pop` 配對數與含被呼叫者的最高同時 `push` 深度。
- `assemble` 回傳 `Program(cells, symbols, sizes)`。
- `check_forms` 回傳 `list[Failure]`，每筆含 `form_id`、`check`、`seed` 與 `detail`。
- `run_differential` 回傳 `DiffReport(checked, failures, unsupported, timeouts)`。
- `run_reference` 回傳 `(ret, memory_after)`，無回傳值函式的 `ret` 為 `None`。

## 可執行操作範例（`small_target.py`）

### 編譯加法函式

建立 `program.py`：

```python
# program.py
from rpkbin.mcucc.lang import u8

def add2(a: u8, b: u8) -> u8:
    return a + b
```

建立完整示範目標 `small_target.py`（具兩個暫存器與基本加減／搬移／跳躍／呼叫指令；實務目標請依實際硬體補上編碼與模擬語意）：

<details>
<summary>完整的 small_target.py</summary>

```python
# small_target.py
from rpkbin.mcucc.target import (
    Target, Space, Reg, Cls, Form, R, I, T, Abi, SimBinding,
)
from rpkbin.isasim import MCU, Simulator

names = ("a", "b")
mask = (1 << 8) - 1

def rr_encoder(base):
    def encode(v):
        return (base + 2 * names.index(v["d"]) + names.index(v["s"]),)
    return encode

forms = (
    Form("add_rr", "add", 8, (R("d", "byte"), R("s", "byte")),
         {"dst": "d", "a": "d", "b": "s"}, 1, "add {d}, {s}",
         encode=rr_encoder(0x10)),
    Form("mov", "mov", 8, (R("d", "byte"), R("s", "byte")),
         {"dst": "d", "a": "s"}, 1, "mov {d}, {s}",
         encode=rr_encoder(0x20)),
    Form("add_ri", "add", 8, (R("d", "byte"), I("k", 8)),
         {"dst": "d", "a": "d", "b": "k"}, 2, "add {d}, #{k}",
         encode=lambda v: (0x40 + names.index(v["d"]), v["k"])),
    Form("call", "call", 0, (T("t", abs_bits=12),), {}, 3, "call {t}",
         encode=lambda v: (0x60, v["t"] >> 8, v["t"] & mask)),
    Form("ret", "ret", 0, (), {}, 1, "ret", encode=lambda v: (0x61,)),
    Form("jmp", "jmp", 0, (T("t", rel_bits=8),), {}, 2, "jmp {t}",
         encode=lambda v: (0x62, v["t"] & mask)),
)
TARGET = Target(
    name="small", cell_bits=8,
    spaces=(Space("code", 12, "program"), Space("data", 8, "data")),
    regs=tuple(Reg(name, 8) for name in names), aliases=(),
    classes=(Cls("byte", names),), flags=(), forms=forms,
    abi=Abi({8: names}, {8: "a"}, ()), long_branch="none",
    comment=";", local_label=".L{fn}_{n}", label_def="{name}:",
)

machine = MCU("small")
for name, width, access, program in (
    ("code", 12, "ro", True), ("data", 8, "rw", False),
    ("frames", 12, "rw", False),
):
    machine.space(name, cell_bits=8, size=1 << width, access=access,
                  is_program=program, reset_state="initialized", init=0)
for name in names:
    machine.reg(name, bits=8, reset=0)
machine.reg("cursor", bits=12, reset=0, is_pc=True)
machine.reg("depth", bits=16, reset=0)
machine.on_undefined_opcode("stop")
machine.on_out_of_range("stop")
machine.on_write_to_readonly("stop")
machine.on_uninitialized_read("stop")
machine.instr("00000000", syntax="halt", cycles=1, name="halt")(
    lambda state: state.halt("example"))

def rr_handler(destination, source, add):
    def execute(state):
        value = state.reg[source]
        if add:
            value += state.reg[destination]
        state.reg[destination] = value & mask
    return execute

def immediate_handler(destination):
    def execute(state, k):
        state.reg[destination] = (state.reg[destination] + k) & mask
    return execute

for d, destination in enumerate(names):
    for s, source in enumerate(names):
        for base, add, mnemonic in ((0x10, True, "add"), (0x20, False, "mov")):
            machine.instr(format(base + 2*d + s, "08b"),
                          syntax=f"{mnemonic} {destination}, {source}", cycles=1,
                          name=f"{mnemonic}_{d}_{s}")(
                rr_handler(destination, source, add))
    machine.instr(format(0x40 + d, "08b") + "k"*8,
                  syntax=f"add {destination}, {{k}}", cycles=1, name=f"add_i_{d}")(
        immediate_handler(destination))

def call_handler(state, t):
    depth = state.reg["depth"]
    state.mem["frames"].write_word(depth, 2, state.next_pc, "little")
    state.reg["depth"] = depth + 2
    state.next_pc = t

def return_handler(state):
    depth = state.reg["depth"] - 2
    state.next_pc = state.mem["frames"].read_word(depth, 2, "little")
    state.reg["depth"] = depth

def jump_handler(state, t):
    offset = t if t < (1 << 7) else t - (1 << 8)
    state.next_pc = state.next_pc + offset

machine.instr("011000000000" + "t"*12, syntax="call {t}", cycles=1)(call_handler)
machine.instr("01100001", syntax="ret", cycles=1)(return_handler)
machine.instr("01100010" + "t"*8, syntax="jmp {t}", cycles=1)(jump_handler)
SIM = SimBinding(lambda: Simulator(machine), "code", "cursor", (0,))
STUBS = {"existing": (*forms[2].encode({"d": "a", "k": 1}), 0x61)}
```

</details>

將 `add2` 編譯至 `program.asm`：

```sh
python -m rpkbin.mcucc compile program.py --target small_target.py --spill-pool data:0x60-0x6f --no-comments -o program.asm --report
```

`program.asm` 輸出內容為：

```text
add2:
    add a, b
    ret
```

### 驗證目標宣告與機器執行

```sh
python -m rpkbin.mcucc lint-target small_target.py
python -m rpkbin.mcucc check-forms small_target.py --seeds 1
python -m rpkbin.mcucc verify program.py --target small_target.py --spill-pool data:0x60-0x6f --window 256
```

`check-forms` 輸出：

```text
c9 skipped: out_of_space=0 ret_not_fit=0
verified: forms
```

在 `isasim` 指令語意函式中，`state.pc` 與 `state.cycle` 為唯讀屬性；分支與呼叫需更新 `state.next_pc`，外部驅動可用 `sim.poke("cursor", address)` 修改 PC。

### 在 Python 中編譯與檢查結果

```python
# api_check.py
from rpkbin.mcucc import CompileConfig, compile_file
from rpkbin.mcucc.target import load_target
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.lang import run_reference

target = load_target("small_target.py")
result = compile_file("program.py", target, CompileConfig({"data": (0x60, 0x6f)}, False))
assert result.functions["add2"].bytes == 2
assert not result.functions["add2"].findings
program = assemble(result, target, origin=16)
assert program.sizes["add2"] == 2
value, memory = run_reference("program.py", "add2", (250, 9),
                             {"data": [0] * 256}, addr_bits={"data": 8})
assert value == 3
```

```sh
python api_check.py
```

### 呼叫既有組語函式

```python
# existing.py
from rpkbin.mcucc.lang import u8, extern

@extern(args=("a",), ret="a", clobbers=())
def existing(x: u8) -> u8:
    return x + 1

def use_existing(x: u8) -> u8:
    return existing(x) + 2
```

```sh
python -m rpkbin.mcucc compile existing.py --target small_target.py --spill-pool data:0x60-0x6f --no-comments -o existing.asm
python -m rpkbin.mcucc verify existing.py --target small_target.py --spill-pool data:0x60-0x6f --window 256
```

### 內嵌組語輸出與靜態檢查

```python
# inline.py
from rpkbin.mcucc.lang import u8, asm

def inline(a: u8) -> u8:
    asm("inc {0}", ins=((a, "a"),), outs=((a, "a"),),
        clobbers=("b",), nbytes=1, ref=lambda value: (value + 1) & 0xff)
    return a
```

```sh
python -m rpkbin.mcucc compile inline.py --target small_target.py --spill-pool data:0x60-0x6f --no-comments --report
python -m rpkbin.mcucc verify inline.py --target small_target.py --spill-pool data:0x60-0x6f --window 256
```

## 目標宣告與旗標語意

目標描述檔以 Python 撰寫並匯出 `TARGET = Target(...)`（可選填 `SIM = SimBinding(...)` 與 `STUBS = {...}`）：

- 儲存與結構：`Space(name, addr_bits, kind)`（`"program"` 或 `"data"`）、`Reg(name, bits)`（`cell_bits=8`）、`Alias(name, parts)`（由高位到低位組合 16 位元視圖）、`Cls(name, members)`、`Flag(name)` 與 `Abi(arg_locs, ret_locs, callee_saved)`（固定單一 cell 記憶體位置使用 `MemLoc(space, addr, n=1)`）。
- 運算元與形式：`R(name, cls)`、`I(name, bits, signed=False, values=None)`、`M(name, space, bits=None)`、`P(name, cls, space)`、`T(name, rel_bits=None, abs_bits=None, base="end")`，以及 `Form(id, op, width, operands, bind, bytes, asm, flags_read=None, flags_write=None, clobbers=(), encode=None)`。

| `flags_write` 語意詞 | 值為 `1` 的條件或意義 | 適用 `Form.op` 與編譯器規則 |
|---|---|---|
| `zero` | 依寬度截斷後的結果等於 `0`。 | 適用於有結果的資料運算與 `cmp`。 |
| `carry` | 無號 `a + b >= 2**width`。 | 用於 `add`；不參與 `cmp`／`br` 配對。 |
| `borrow` | 無號 `a < b`。 | 用於 `sub` 或 `cmp`；可自由與 `br` 配對。 |
| `nborrow` | 無號 `a >= b`。 | 用於 `sub` 或 `cmp`；可自由與 `br` 配對。 |
| `borrow_msb0` | 僅當 `max(a, b) < 2**(width - 1)` 時為無號 `a < b`。 | 僅限 `sub` 或 `cmp`（其他運算引發 `TargetError`）。只在 V10 靜態證明兩運算元最高位元皆為 `0` 時使用（例如 `< 2**(width-1)` 常數、`zext`、`shr >= 1`、`&` 遮罩或 `mov` 鏈）；否則若支援 16 位元比較會自動以 `zext` 加寬，否則引發 `UnsupportedError`。 |
| `nborrow_msb0` | 僅當 `max(a, b) < 2**(width - 1)` 時為無號 `a >= b`。 | 與 `borrow_msb0` 具相同 `sub`／`cmp` 限制、V10 證明要求與 `check-forms` 最高位元為 0 取樣規則。 |
| `shift_out` | 最後移出的位元（`1 <= shift <= width`）。 | 用於 `shl`／`shr`。 |
| `undef` | 旗標可能被改寫成未定義值。 | 明確標記副作用，避免編譯器誤用殘留旗標。 |

## 注意事項與限制

- **來源語言邊界**：支援 `u8`、`u16`、`Ptr("space")`、單 byte `mem.space[addr]` 讀寫、常數位移、六種無號比較（僅限條件）、`if`／`elif`／`else`、`while`、步進為 1 的 `for ... in range(...)`、`break`／`continue`、`@fn`、`@extern` 與 `asm(...)`。不支援執行期 `*`、`/`、`%`、`**`、非常數位移、有號整數、浮點數、容器型別、遞迴、中斷，以及多 cell `MemLoc` ABI 參數或回傳值。
- **`run_differential` 行為細節**：`stubs` 預設為 `None`（視為 `{}`）；傳入 `sim=None` 會在讀取 `sim.halt_cells` 時引發 `AttributeError`（除非所有函式皆不支援或為 `unverified: asm`）。參考執行的 `TimeoutError` 按個別函式累計，若單一函式逾時比例超過 10%（`fn_timeouts * 10 > fn_attempts`）會在 `report.failures` 加入 `(name, "reference timeout ratio", fn_timeouts, fn_attempts)`，同時 `report.timeouts` 記錄總逾時次數。使用字典 `window` 時，列出的空間寬度須 `>= 512` 且禁止存取視窗外位址；未列出的空間允許常數位址讀寫（初始為 `0`），但不納入最終記憶體比對。
- **`run_reference` 路徑處理**：只有傳入 `pathlib.Path` 或以 `".py"` 結尾的單行字串時，才會將 `module_path_or_source` 當成檔案讀取。直接傳入 `"rpkbin/mcucc/examples/program.py.txt"` 這類非 `.py` 路徑字串會被當成 Python 程式碼解析而引發 `SourceError`；請改用 `Path(...)` 包裝或先讀取文字內容。缺少或無法讀取檔案會引發 `SourceError`。獨立呼叫 `run_reference` 不具 `spill_pool` 資訊，不會攔截 pool 存取。
- **相容性**：離線運作，核心編譯只用標準函式庫，目標環境為 Linux RHEL 8.8 與 Python >= 3.11（`pyproject.toml`、`feature_version=(3, 11)`；前端會拒絕如 `def f[T](...)` 等 Python 3.12 以上語法）。本頁所有範例均已在 Windows 環境以 `venv\Scripts\python.exe` 實測通過。

## 延伸閱讀

- [原始語言參考](../../../rpkbin/mcucc/doc/language.md)
- [目標描述參考](../../../rpkbin/mcucc/doc/target.md)
- [保證與假設](../../../rpkbin/mcucc/doc/guarantees.md)
- [錯誤目錄](../../../rpkbin/mcucc/doc/errors.md)
- [虛構目標範例](../../../rpkbin/mcucc/examples/README.md)
- [AI 操作指南](../../../rpkbin/mcucc/AI-GUIDE.md)
- [變更紀錄](../../../rpkbin/mcucc/CHANGELOG.md)
- [isasim 快速入門](../isasim/README.md)
- [文件導覽](../README.md)
