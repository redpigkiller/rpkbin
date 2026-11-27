# mcucc AI 操作指南

mcucc 用明確的 Target 把受限 Python 轉成組語。
只用 CLI 或公開 Python API 操作。不得修改 mcucc 原始碼。

## 操作順序

先按 [README](README.md) 準備 Python 3.12。要執行下列範例，請從含 `examples` 子目錄的工作目錄下指令。自行寫 Target 時，請使用自己的檔案路徑，並保留硬體欄位到資料來源的對照。

```bash exec
python -B -m rpkbin.mcucc lint-target examples/target.py.txt
```
<!-- exit: 0 -->
<!-- stdout: examples/lint.tsv -->

結束碼 0，且 [可用性矩陣](examples/lint.tsv) 中所有直接需要的 `op`／寬度都顯示 `True`，才進下一步。

```bash exec
python -B -m rpkbin.mcucc check-forms examples/target.py.txt --seeds 1
```
<!-- exit: 0 -->

```text stdout
c9 skipped: out_of_space=0 ret_not_fit=0
verified: forms
```

結束碼 0 且印出 `verified: forms` 才有一致性結果。`c9 skipped` 的兩個計數須均為 0 才能宣稱所有安排的呼叫／回傳樣本都執行。非零時回報略過的計數及原因；`unverified: no SIM` 代表沒有實測。

```bash exec
python -B -m rpkbin.mcucc compile examples/program.py.txt --target examples/target.py.txt --spill-pool data:0x60-0x6f --no-comments --report
```
<!-- exit: 0 -->
<!-- stdout: examples/program.asm -->
<!-- stderr: examples/report.json -->

結束碼 0，並取得 [組語](examples/program.asm)（stdout）與 [JSON](examples/report.json)（stderr）。在報告確認 `verified` 狀態與 `clobbers`，後者是呼叫者要處理的暫存器。

```bash exec
python -B -m rpkbin.mcucc verify examples/program.py.txt --target examples/target.py.txt --spill-pool data:0x60-0x6f --window 256
```
<!-- exit: 0 -->

```text stdout
add2: verified
```

結束碼 0 且每個要驗的函式都印 `verified` 才算通過。結束碼 1 查失敗案例，2 查 stderr。出現 `unsupported:` 或 `unverified:` 就回報未驗證的函式；不要把整個檔案稱作通過。

## 來源語言白名單

- 只從 `rpkbin.mcucc.lang` 匯入 `u8, u16, Ptr, mem, fn, extern, asm` 中用到的名稱。
- 模組層級只放匯入、整數常數、`P = Ptr("data")` 這類型別別名、函式或 docstring。資料存取用 `mem.data[位址]`，位址為同空間指標或範圍內常數，一次一個 byte。
- 參數都註記 `u8`、`u16` 或指標型別。沒有回傳值可省略回傳註記。呼叫傳位置引數；`@fn` 可覆寫 ABI，`@extern` 要明定位置、clobbers 及參考本體。
- 允許一般及擴充指派、`if/elif/else`、`while`、單／雙引數的 `for ... in range(...)`、`break`、`continue`、`return`、`pass`、直接呼叫。所有路徑上先指派再讀。
- 在函式內用同型別的 `+ - & | ^`、單元 `-`／`~`、常數位移與明確轉型。比較和 `and/or/not` 只用在條件。允許同型別分支的條件運算式。

## 黑名單與替代

| 避免的 Python 寫法 | 用 mcucc 寫法替代 |
|---|---|
| list、dict、set、tuple 當作執行期資料 | 用分開的定寬區域變數，或在已宣告資料空間使用固定地址及指標。 |
| 推導式、生成器、`yield`、`async` | 用 `for i in range(n)` 逐次處理，保留一個有註記的計數值。 |
| 遞迴或巢狀函式 | 用非遞迴的模組層級函式；逐次步驟用迴圈。 |
| 浮點、字串、`None`、一般 Python 物件 | 選擇明確比例的 `u8`／`u16` 數值。文字輸出與物件處理放在外部專案。 |
| `try/except`、`with`、`assert`、`raise` | 先用 `if` 檢查條件，回傳定寬狀態碼，例如 `return u8(1)`。 |
| `x = 1` 或假定整數自動加寬 | 用 `x: u8 = 1`；需加寬時寫 `u16(x)`。 |
| 乘除、取餘數、可變位移 | 只在模組整數常數中計算支援的常數運算。執行期的二次冪乘法可用已知常數位移；其他演算法用合規的運算與迴圈，或交給外部函式。 |
| `a < b < c` 或把比較結果指派給變數 | 在條件中寫 `a < b and b < c`。若需回傳狀態，用分支產生 `u8(0)`／`u8(1)`。 |
| 以任意步長走 `range`、修改迴圈變數或在迴圈後讀它 | 用註記的計數器與 `while` 明確更新。 |
| 預設引數、關鍵字呼叫、星號解包、一般匯入 | 固定函式簽名並傳所有位置引數。資料和工具由呼叫者提供。 |

裝飾器與 `asm` 的宣告資料有專用格式。需要它們時先讀語言參考。

## 寫 Target 時的判斷

每項暫存器、別名、空間、寬度、編碼、寫入、旗標、ABI 都要能對到目標文件的章節。缺依據就寫「待確認」，省略尚不能證實的 form。虛構模型先寫下自己的編碼與效果表。多 cell 的 ABI 記憶體位置不支援參數和回傳；16 位元 ABI 資料請使用相符的暫存器別名。

只宣告同寬度的資料 form。控制操作用寬度 0；記憶體的位址寬度另由空間和運算元描述。若只知道旗標可能改變，或不確定它是否改變，宣告 `undef`，別把未知的旗標拿給分支當條件。只有目標能證明比較語意時才宣告相應的旗標語意詞。

若有 `SIM`，每個 form 都需要 `encode`，缺少就會列為失敗。`check-forms` 任一失敗時，先核對 Target、編碼器與模擬器的同一條指令。修正可對到硬體文件的描述；若宣告已有證據，留下可重現案例交維護者。

## 選編譯與取樣設定

- `spill_pool` 是編譯器專用資料範圍，含首尾。查清來源、衍生指標、外部函式和 ABI 永遠不會碰它，才傳該區間。差異測試遇到來源碰 pool 就會把該輸入列為失敗。獨立 `run_reference` 沒有 pool 資訊。
- 起始值用 `park_depth=0`、`park_policy="smallest"`。若有明確堆疊預算，扣掉返回位址及既有與外部用量後，才用正的 push 個數。想優先試堆疊且接受較大的程式碼才用 `stack_first`。只用原配置時選 `slot_first`。
- `--window 256` 適合有從 0 開始的 256-cell 安全資料區的範例。整數只界定初始化範圍，參考執行實際觸及的其他 cell 也會被比對。若低位址不適合取樣，對具備相應範圍的目標改用 `--window data:0x300-0x5ff` 這類具名窗口，每個最少 512 cells。把指標樣本與偏移留在已知可讀寫 RAM，且指標取樣範圍不得與 pool 重疊。不可混用兩種格式。

## 黃金範例

[來源檔](examples/program.py.txt) 完整內容如下。

```python
from rpkbin.mcucc.lang import u8

def add2(a: u8, b: u8) -> u8:
    return a + b
```

```text
add2:
    inksum a, b
    inkdone
```

```json
{"functions": {"add2": {"bytes": 2, "clobbers": ["a", "b"], "slots": [], "findings": [], "verified": true}}}
```

## 常見錯誤

| 案例 | 訊息片段 | 原因 | 應做 |
|---|---|---|---|
| first-width | `first integer assignment needs a type annotation` | 第一次以整數常數指派。 | 加定寬註記。 |
| statement | `Try: unsupported statement` | 使用例外區塊。 | 改成條件與狀態碼。 |
| operation | `BinOp: unsupported expression or operation` | 函式內用了乘法。 | 改用受支援運算或外部函式。 |
| recursive | `recursive call graph` | 呼叫圖有環。 | 拿掉反向呼叫並用迴圈。 |
| address-kind | `target.forms['read_cell'].operands.m: R(name='m', cls='byte'); expected M address operand` | 直接載入的位址種類錯誤。 | 直接用 `M`，間接用 `P`，重跑 `lint-target`。 |
| missing-form | `(sub, 8) has no ideal expansion; missing form/register class` | 此目標無 8 位元減法配對。 | 核對寬度與 form，僅補已知可用的指令。 |
| allocation | `no finite allocation; missing register class/scratch or spill pool slot` | 可用位置或暫存資源不足。 | 檢查資料位置與 pool，再縮短同時要保留的值。 |
| pool-contiguous | `spill pool needs 1 contiguous cells, available 0` | 分配下一個槽時，合法 pool 已無空間。 | 在同一安全資料區擴大保留區，或減少同時需要保存的值。 |
| window | `error: --window is required` | 漏填取樣窗口。 | 選安全的資料區並傳窗口。 |
| verify-driver | `verify driver requires an absolute call form` | 機器驅動無法呼叫測試函式。 | 提供有依據的絕對呼叫及 encoder。 |
| encoder-length | `form 'ret': encode length/cells disagree with form (expected 1, got 0)` | 回傳指令的 encoder 回傳長度不符。 | 依 form id 比對編碼與宣告長度。 |
| invalid-depth | `park_depth -1: expected nonnegative integer` | 堆疊預算是負數。 | 用 0 或已核准的正預算。 |

## 功能該放哪裡

1. 只依賴某目標的編碼、記憶體、裝置或 ABI 嗎？放自己的 Target 或呼叫端。
2. 能用白名單和有定義的 Target 完成嗎？放自己的來源或外部函式。使用驗證用樁描述外部語意。
3. 多個目標都需要新語言能力，或有效輸入重現錯碼／診斷不符嗎？回報維護者，先保留能重現的最小程式。

需求清單記錄用途、最小來源、至少一個目標的依據、期望行為及現行替代。疑似錯誤清單記錄版本、來源、Target、完整命令與設定、結束碼與輸出、期望結果與依據、可重現與否。

## 何時查詳細文件

- 出現型別、控制流或 `@extern`／`asm` 問題時，讀[語言參考](doc/language.md)。
- 要新增 form、旗標或模擬器連結時，讀 [Target 參考](doc/target.md)。
- 要判斷樣本覆蓋、pool 或堆疊責任時，讀[保證與假設](doc/guarantees.md)。
- 日誌出現失敗而本頁沒有對應時，讀[錯誤目錄](doc/errors.md)。
