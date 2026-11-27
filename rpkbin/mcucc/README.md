# mcucc

mcucc 把受限 Python 函式編譯成指定目標的組語。用途是寫少量、要放回既有組語專案的函式。編譯器依 Target 宣告尋找合法指令，預設以靜態位元組數為優先。若你也提供機器碼編碼器和模擬器，可以用同一份來源做差異測試。

## 安裝與準備

使用 Python 3.12。在 MyPkg 工作樹根目錄執行 `python -m pip install -e .`。安裝結束碼為 0 時，同一個 Python 環境應能匯入 `rpkbin.mcucc`。內附的驗證範例也使用同一個套件發行的 `rpkbin.isasim`。

下方命令從 `rpkbin/mcucc` 目錄執行。若在另一個工作目錄操作，先把 [examples 內的檔案](examples/README.md) 放進當地的 `examples` 子目錄。雙檔都是 UTF-8 Python 文字，命令可直接讀取原本的 `.py.txt` 檔名。

## 五分鐘快速開始

[來源](examples/program.py.txt) 用 `u8` 宣告兩個參數和回傳寬度。[Target](examples/target.py.txt) 的兩個暫存器接收參數，其中第一個也放回傳值。

### 1. 檢查宣告並讀取可用性矩陣

```bash exec
python -B -m rpkbin.mcucc lint-target examples/target.py.txt
```
<!-- exit: 0 -->
<!-- stdout: examples/lint.tsv -->

stdout 是 [可用性矩陣](examples/lint.tsv)，每列列出運算、寬度、是否有 form、form 數和最短位元組數。`False` 表示該運算和寬度未宣告；範例的 `add` 寬度 8 顯示 `True`。

### 2. 用模擬器對照 form 宣告

```bash exec
python -B -m rpkbin.mcucc check-forms examples/target.py.txt --seeds 1
```
<!-- exit: 0 -->

```text stdout
c9 skipped: out_of_space=0 ret_not_fit=0
verified: forms
```

這次沒有略過呼叫／回傳樣本。若計數非零，使用 [Target 參考](doc/target.md) 檢查略過的是哪一部分。若印出 `unverified: no SIM`，此步未執行模擬。

### 3. 編譯並讀報告

```bash exec
python -B -m rpkbin.mcucc compile examples/program.py.txt --target examples/target.py.txt --spill-pool data:0x60-0x6f --no-comments --report
```
<!-- exit: 0 -->

```text stdout
add2:
    inksum a, b
    inkdone
```

```text stderr
{"functions": {"add2": {"bytes": 2, "clobbers": ["a", "b"], "slots": [], "findings": [], "verified": true}}}
```

`add2` 佔 2 bytes，沒有用到槽。加上 `-o program.asm` 會把同樣的組語寫到指定檔案。整份輸出可對照 [組語](examples/program.asm) 與 [報告](examples/report.json)。

### 4. 比較來源與機器執行

```bash exec
python -B -m rpkbin.mcucc verify examples/program.py.txt --target examples/target.py.txt --spill-pool data:0x60-0x6f --window 256
```
<!-- exit: 0 -->

```text stdout
add2: verified
```

這裡的 `256` 是呼叫者指定的資料初始化窗口。驗證以同一組引數和初始資料分別執行來源與機器碼。先看逐函式狀態，再看結束碼。

## CLI 參數

| 子命令 | 必填 | 選用與輸出 |
|---|---|---|
| `lint-target` | Target 檔路徑 | stdout 輸出以 tab 分欄的矩陣。 |
| `check-forms` | Target 檔路徑 | `--seeds N` 預設 1，使用 0 到 N−1，N 須為正整數。具備 `SIM` 時，每個 form 都需要 `encode`，省略就會列為失敗。stdout 先列失敗，再列略過計數。無失敗則另印 `verified: forms`。 |
| `compile` | 來源檔路徑、`--target`、`--spill-pool` | `--no-comments` 關閉函式標頭註解，`-o` 指定輸出檔，省略時組語寫 stdout。`--report` 另將 JSON 寫 stderr。 |
| `verify` | 來源檔路徑、`--target`、`--spill-pool`、`--window` | `--inputs N` 預設 120，N 須為正整數。`--seed S` 預設 0。帶有 `@extern` 的呼叫需要 `STUBS`。 |

兩個編譯相關子命令都接受 `--park-depth N` 與 `--park-policy smallest`、`stack_first` 或 `slot_first`。pool 格式為 `SPACE:LO-HI`，含首尾位址，數值接受十進位或 `0x` 十六進位。只指定一個資料空間。

`--window 256` 從 0 初始化每個資料空間的前 256 cells。整數只界定初始化範圍，參考執行實際觸及的其他 cell 也會納入比對，spill pool 除外。`--window data:0x300-0x5ff` 是適用於**具備該位址範圍的目標**的具名窗口寫法，含首尾；每個具名窗口至少 512 cells，可為不同空間重複指定，不能和整數形式混用。具名窗口的指標引數取樣在下半部，必須與 pool 分開，程式的衍生位址也須有足夠空間。[取樣與檢查界線](doc/guarantees.md)

結束碼 0 表示命令完成。遇到 `unsupported: …` 或 `unverified: …` 時，即使結束碼是 0，該函式仍未通過機器比較。結束碼 1 表示有 form 檢查或機器比較失敗，結束碼 2 表示參數或 mcucc 診斷錯誤，格式是 stderr 的 `error: …`。[對照錯誤與處理方式](doc/errors.md)

## Python API

除了 `lang` 的來源符號，以下是工作流程使用的介面。

| 匯入位置 | 簽名及結果 |
|---|---|
| `rpkbin.mcucc` | `CompileConfig(spill_pool, comments, *, park_depth=0, park_policy="smallest")`。前兩項必填。 |
| `rpkbin.mcucc` | `compile_source(source, target, config, filename="<source>")` 讀取字串，`compile_file(path, target, config)` 讀取 UTF-8 檔案。兩者回傳 `CompileResult`。 |
| `rpkbin.mcucc.target` | `load_target(path)` 取 `TARGET`。`load_target_file(path)` 回傳 `(target, sim, stubs)`，後兩項省略時為 `None`。Target 檔會以 Python 執行，只載入可信檔案。 |
| `rpkbin.mcucc.assemble` | `assemble(result, target, origin)`。指定非負起始位址，回傳 `Program`，含 `cells`（位址到 cell）、`symbols`（符號到位址）、`sizes`（函式名稱到 bytes）。需要可編碼的指令和可解析的符號。 |
| `rpkbin.mcucc.formcheck` | `check_forms(target, sim, seeds)` 回傳 `list[Failure]`，各筆有 `form_id`、`check`、`seed`、`detail`。沒有 `sim` 時回傳空清單，須自行確認確實有綁定模擬器。略過計數請讀 CLI。 |
| `rpkbin.mcucc.diff` | `run_differential(source, target, sim, config, *, stubs, seeds, inputs_per_function, window, post_check=None)`。回傳 `DiffReport`，有 `checked`、`failures`、`unsupported`、`timeouts`。用 `post_check(phase, sim)` 加查目標自己的狀態，時點為 `"before"` 或 `"after"`，`AssertionError` 算該輸入失敗。若要看每個函式的未驗證原因，使用 CLI。 |
| `rpkbin.mcucc.lang` | `run_reference(module_path_or_source, func_name, args, memory, *, addr_bits)` 回傳 `(ret, memory_after)`。呼叫者提供資料內容與各空間寬度。獨立呼叫沒有 spill pool 檢查，該檢查由差異測試執行。來源參考執行與型別規則見[語言參考](doc/language.md)。 |
| `rpkbin.mcucc.errors` | `McuccError` 是共用基底。其細分類別與實際診斷見[錯誤目錄](doc/errors.md)。 |

同一目錄下的 Python 範例，會印出 `2` 和 `[17, 97]`。

```python
from rpkbin.mcucc import CompileConfig, compile_file
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.target import load_target

target = load_target("examples/target.py.txt")
config = CompileConfig({"data": (0x60, 0x6F)}, False)
result = compile_file("examples/program.py.txt", target, config)
print(result.functions["add2"].bytes)
program = assemble(result, target, 0)
print([program.cells[address] for address in sorted(program.cells)])
```

### 編譯設定與記憶體責任

- `spill_pool` 是一個資料空間及含首尾的區間。Python 可傳 `{}` 來禁用槽。pool 必須完全保留給編譯器，來源、指標、外部函式和固定 ABI 位置都須避開。配置遇到 `AllocationError` 時會以私有槽模式重試；該模式的來源常數位址若碰到 pool 會被拒絕。
- `comments` 是布林值，控制組語的函式標頭註解。
- `park_depth` 預設 0，停用堆疊暫存。正值的單位是同時佔用的 push 個數，包含暫存、函式與呼叫保存，以及呼叫點的模組內函式深度。先預留返回位址、外部函式和呼叫前的堆疊用量。用不到的暫存器值只有跨過合法區間且有配對的同寬 push／pop 才能暫存，區間可相離或巢狀。
- `smallest` 預設選同環境中位元組較少的可用結果，相同時保留原配置。`stack_first` 在原配置有值放到槽且堆疊配置可用時選後者，可能比較大。`slot_first` 使用原配置。深度 0 時策略不影響輸出。

### 讀取報告

`CompileResult.asm` 是組語字串。`functions` 按來源檔函式順序，值為含 `name`、下列欄位及 `mir`（指令記錄序列）的 `FunctionResult`。`report()` 轉成以 `functions` 為頂層鍵的 JSON 可序列化資料，省略指令記錄。

| 函式欄位 | 讀法 |
|---|---|
| `bytes` | 函式組語的靜態位元組數。 |
| `clobbers` | 呼叫後可能改動的基本暫存器。呼叫者須保存還要使用的值。 |
| `slots` | 每項是 `[space, start, cells]`，其中數值是起始位址與 cell 數量。 |
| `findings` | 尚可簡化的指令樣式。每項含 `rule`、`function`、指令 `index`，空陣列代表無已知命中。 |
| `verified` | `true` 表示靜態檢查通過，`partial: …` 表示列出的靜態覆蓋缺口，`unverified: asm` 表示內嵌組語不做機器差異驗證。 |
| `pool_contract` | 只在成功採用私有槽模式時出現，值是 `"private"`。 |
| `parks`、`push_depth` | 只在正深度出現，分別是堆疊暫存對數與實際最高 push 深度。 |

## 從零描述一個小目標

先在紙上列出程式空間、資料空間、基本暫存器、指令編碼與呼叫方式，再把已知的事實填入描述。用同一張表個別寫 form 的編碼和模擬器的解碼，才有可互相對照的檢查。下列配方使用範例的欄位命名，暫存器、位址與指令文字可依你的虛構模型選定。

1. 從 `rpkbin.mcucc.target` 匯入 `Target, Space, Reg, Cls, Form, R, I, M, T, Abi, SimBinding`。以 `TARGET = Target(...)` 匯出表。在這個配方中，`name` 是你選的名稱，`cell_bits=8`，`spaces` 包含 `Space("code", 12, "program")` 與 `Space("data", 8, "data")`。`regs` 包含兩個或更多個 `Reg`（名稱與 8 位元），`classes` 用 `Cls("byte", names)` 列這些名稱的 tuple。無別名或旗標時，`aliases=()`、`flags=()`。
2. `forms` 填入各 `Form` 的識別字、標準運算、資料寬度、運算元 tuple、語意對應、位元組數、組語模板，以及 `encode=` 回呼。二元加法可用 `R("d", "byte"), R("s", "byte")`，對應 `{"dst": "d", "a": "d", "b": "s"}`。搬移用同一組運算元和 `{"dst": "d", "a": "s"}`。資料載入用 `R("d", "byte"), M("m", "data")` 和 `{"dst": "d", "a": "m"}`，儲存用 `M("m", "data"), R("s", "byte")` 和 `{"a": "m", "b": "s"}`。分別採標準運算 `add`、`mov`、`ld`、`st`，寬度皆為 8。若來源需要載入整數，加入 `const`，用 `R("d", "byte"), I("k", 8)` 和 `{"dst": "d", "a": "k"}`。
3. 目標必須提供 `jmp` 與 `ret`。兩者寬度為 0，語意對應 `{}`。`ret` 運算元是 `()`，`jmp` 可用 `(T("t", abs_bits=12),)`。再以相同寬度與運算元形式宣告 `call`，讓驗證驅動程式可從位址 0 呼叫。分配獨立的 halt 編碼給模擬器，halt 無需列入 Target。用 `Abi({8: names}, {8: names[0]}, ())` 表示按名稱順序放引數，回傳至第一個位置，無固定保存暫存器。可用 `long_branch="none"`、`comment=";"`、`local_label=".L{fn}_{n}"`、`label_def="{name}:"`。
4. 為不同指令分配互不衝突的前導 cell。雙暫存器可用 `base + len(names) * names.index(values["d"]) + names.index(values["s"])` 作唯一編碼。直接位址 form 可把暫存器編進第一個 cell、位址放第二個 cell。絕對呼叫可用三個 cells，依序是呼叫碼、目標位址高位、低位。`encode(values)` 的 keys 是你宣告的運算元名稱，暫存器值為名稱字串，立即值／位址是整數。固定回傳與 halt 只需一個 cell。組語模板用相同運算元名稱，例如 `"inksum {d}, {s}"`。
5. 從 `rpkbin.isasim` 匯入 `MCU, Simulator`。使用 `MCU("small")` 建立模型，名稱可自選。用 `machine.space(name, cell_bits=8, size=1 << width, access=access, is_program=is_program, reset_state="initialized", init=0)` 登記每個空間。程式空間用 `access="ro"`、`is_program=True`，普通資料及另外保留的返回堆疊空間用 `access="rw"`、`is_program=False`。用 `machine.reg(name, bits=8, reset=0)` 逐個登記暫存器，另登記寬度 12 且 `is_pc=True` 的 `"cursor"`，以及記錄堆疊深度的暫存器。
   建立 `Simulator(machine)` 前還須明定四種停止策略。這個模型對未定義指令、越界、寫入唯讀空間、讀取未初始化資料都停機，依序呼叫 `machine.on_undefined_opcode("stop")`、`machine.on_out_of_range("stop")`、`machine.on_write_to_readonly("stop")`、`machine.on_uninitialized_read("stop")`。若 `check-forms` 回報模擬器工廠或初始化失敗，在 Python 直接呼叫該 Target 的 `SIM.make_sim()`，查看原始例外指出的欄位。
6. 用 `machine.instr(pattern, syntax=文字, cycles=1, name=識別字)(handler)` 安裝一條機器指令。每個識別字必須非空且唯一，例如用 `f"add_{d}_{s}"` 表示你選的來源與目的暫存器編號。若省略 `name`，模型使用處理函式的名稱，工廠產生的閉包可能因此重名。固定前導 cell 的 pattern 用 `format(cell, "08b")`。有 8 位元位址可再接 `"m" * 8`，位址由 `handler(state, m)` 接收；12 位元絕對目的地可接 `"0000" + "t" * 12`，由 `handler(state, t)` 接收。處理函式透過 `state.reg[name]` 與 `state.mem["data"][m]` 讀寫，算術結果用 `(1 << 8) - 1` 的遮罩。halt 用 `state.halt("example")`。
7. `jmp` 的處理函式只將 `state.next_pc` 設為絕對目的地。呼叫的處理函式先把原本的 `state.next_pc`（下一指令位址）保存到另設的返回堆疊，再將它改成呼叫目的地。回傳時以反序讀回並還原深度。堆疊資料可用 `state.mem["frames"].write_word(depth, 2, state.next_pc, "little")` 與對應的 `read_word(depth, 2, "little")`，每次占兩個 cells。以 `SIM = SimBinding(lambda: Simulator(machine), "code", "cursor", (halt_code,))` 匯出；`halt_code` 是你在第 3 步選的整數。
8. 依快速開始的四個子命令驗證新檔。把 `--target`、來源路徑換成自己的檔名。若要驗證槽分配，寫一支先從數個不在 pool 的直接位址各讀一個 byte、再合計回傳的函式。當同時需要保留的值超過暫存器數，先用一個 cell 的 pool 找資源錯誤，再選較大的保留區重試。若回報缺 form，先比對可用性矩陣以及來源的資料寬度及直接／間接存取種類。

## 文件索引與適用界線

- 寫函式、選型別、取記憶體或宣告外部函式。[來源語言](doc/language.md)
- 新增位置、指令或模擬綁定。[Target 參考](doc/target.md)
- 設置 pool、估算堆疊預算或判讀驗證覆蓋。[保證與假設](doc/guarantees.md)
- 看到錯誤或結束碼 1／2。[錯誤目錄](doc/errors.md)
- 由 AI 整合到另一個專案。[AI 操作指南](AI-GUIDE.md)
- 查目前 beta 的變更。[變更紀錄](CHANGELOG.md)

支援的語言限於 8／16 位元無號資料、固定寬度的指標與受限控制流程。目標若缺少可配對的指令會得到診斷。位元組成本是選擇合法輸出的依據，沒有全域最小的承諾。遞迴、動態物件、連結任意外部機器碼及機器驗證內嵌組語均有範圍限制。
