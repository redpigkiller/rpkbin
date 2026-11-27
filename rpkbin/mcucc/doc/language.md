# 原始語言參考

mcucc 編譯的是 Python 3.12 的一個子集。要用同一份原始碼在不同位址寬度的目標上讀寫資料，可以用 `Ptr` 宣告位址，用 `u8` 宣告每次讀寫的資料。以下是在 Python 裡核對回傳值與記憶體效果的完整範例。`data` 是範例的資料空間名稱。

```python
from rpkbin.mcucc.lang import run_reference

source = """from rpkbin.mcucc.lang import u8, Ptr, mem

DataPtr = Ptr("data")

def bump(where: DataPtr, delta: u8) -> u8:
    mem.data[where] += delta
    return mem.data[where]
"""

memory = {"data": [0] * (1 << 8)}
memory["data"][16] = 250
result, after = run_reference(source, "bump", (16, 9), memory, addr_bits={"data": 8})
assert result == 3
assert after["data"][16] == 3
assert memory["data"][16] == 250
```

要編譯自己的 `.py`，請搭配宣告 `data` 空間、相符的呼叫慣例及所需指令形式的 Target。編譯指令及設定見 [快速開始](../README.md)，目標欄位見 [Target 參考](target.md)。

## 匯入與模組層級

- 原始檔使用 `from rpkbin.mcucc.lang import u8, u16, Ptr, mem, fn, extern, asm`。可只匯入實際用到的名稱。其他匯入、`as` 改名及萬用字元匯入會被拒絕。
- `NAME = 常數運算式` 宣告整數常數。運算式可引用先前宣告的常數，使用整數常值、括號、`+ - * // % & | ^ << >> ~`，並依 Python 整數規則在編譯期精確求值。常數名稱須避開 Python 內建名稱及語言保留名稱。
- `NAME = Ptr("data")` 宣告型別別名，後續可用於註記與轉換，例如 `where: DataPtr` 或 `DataPtr(value)`。
- 模組層級也可定義函式、宣告外部函式、加入單獨的字串作為 docstring。

## 型別與運算

| 型別 | 意義 |
|---|---|
| `u8`、`u16` | 8 位元或 16 位元無號整數。結果按型別位寬取模。 |
| `Ptr("data")` | `data` 空間的無號位址。位寬由 Target 決定，支援 8 或 16 位元。不同空間的指標是不同型別。 |
| 條件 | 比較、真值測試及布林組合只用來決定分支。變數、參數、回傳值均須使用上列無號型別。 |

| 寫法 | 規則 |
|---|---|
| `x + y`、`x - y`、`-x` | 按運算結果的位寬取模。要減 1 時可寫 `x - 1`。負整數常值不能當成一般無號運算元。 |
| `x & y`、`x \| y`、`x ^ y`、`~x` | 按位元執行，`~x` 翻轉該型別的每一位。 |
| `x << k`、`x >> k` | `k` 為整數常值或具名整數常數，且 `0 <= k < 位寬`。左移捨去超出的高位，右移從高位補零。結果保留 `x` 的型別。 |
| `u8(x)`、`u16(x)` | 對有型別的值，轉窄時取低位，轉寬時補零。傳入無型別的整數常數時，值必須在目的型別範圍內。取出 `u16` 的高位元組可寫 `u8(x >> 8)`。 |
| `DataPtr(x)` | 將數值按 `data` 的位址位寬取模。若引數只含整數常值、具名常數與合法的常數運算，先按 Python 整數規則精確算完整個引數，再取模一次。例如位址寬為 8 時，`DataPtr((0xFF + 1) >> 1)` 的位址是 `0x80`。 |
| `expr1 if cond else expr2` | 兩個分支的值須符合相同型別，只求值被選中的分支。 |
| `helper(x, y)` | 直接呼叫已定義或已宣告的函式。引數個數和型別須符合宣告。無回傳值的呼叫只能當敘述。 |

二元運算及比較要求有型別的運算元型別相同。無型別的常值可依另一個運算元或上下文取得型別，值須符合該型別範圍。有型別的值要改用另一個位寬或指標型別時，請明確轉換。

比較運算子是 `==`、`!=`、`<`、`<=`、`>`、`>=`，採無號比較。`if x:` 可測試有型別的 `u8`、`u16` 或指標是否為非零。`if 1:` 等無型別整數真值測試會被拒絕。`and`、`or`、`not` 只用於條件，`and` 及 `or` 會短路。要回傳數值形式的判斷結果，可以用 `return u8(1) if x < y else u8(0)`。

## 指派與控制流

| 敘述 | 規則 |
|---|---|
| `x = expr`、`x: u8 = expr` | 第一次指派決定型別。右式已有型別時可直接指派。若第一次右式是無型別的整數，請加註記，例如 `x: u8 = 0`。註記只可出現在第一次指派，之後沿用原型別。 |
| `x op= expr` | 更新已指派的變數。支援 `+=`、`-=`、`&=`、`\|=`、`^=`、`<<=`、`>>=`。 |
| `if`、`elif`、`else` | 條件依上一節。合流後讀取的變數，須在所有可能抵達該處的路徑上先指派。 |
| `while cond:` | 每輪開始時測試條件。支援 `break` 和 `continue`。迴圈的 `else` 不支援。 |
| `for i in range(stop)`、`for i in range(start, stop)` | `start` 預設為 0，`stop` 不含在範圍內。先求值下界，再求值上界，兩者只算一次。`i` 使用上界型別，無型別上界依值是否能放進 `u8` 來選 `u8` 或 `u16`，超出 `u16` 範圍會被拒絕。下界須符合上界型別，步進固定為 1。`continue` 仍會遞增，`break` 則立即離開。在迴圈本體中不得指派 `i`，離開迴圈後若要使用該名稱，須重新指派。迴圈的 `else` 不支援。 |
| `return expr`、`return` | 有回傳註記的函式在每條可返回的路徑上都須提供相符型別的值。省略註記的函式可用 `return` 或走到函式末尾。 |
| 呼叫、`asm(...)`、`pass` | 呼叫可丟棄回傳值。`asm` 的宣告另見下節。`pass` 不執行動作。 |

須在所有可能的路徑上先指派再讀取。只在某個 `if` 分支或某個可能不執行的迴圈內指派，若有一條可能的路徑沒有指派，就不能在後續讀取該變數。

執行期乘、除、取餘、次方、非常數位移、鏈式比較、`is`、`in` 均不支援。也不支援多重或解包指派、一般關鍵字引數、容器常值、推導式、`assert`、`del`、`try`、`with`、`class`、`global`、`nonlocal`、巢狀函式、f-string、walrus、`yield` 及 `async`。字串、tuple、布林值、`None` 及 `lambda` 只有在相應宣告欄位或 docstring 中才可能使用，例如 `Ptr` 的空間名稱、`asm` 的 `flags_ok` 與 `ref`。

## 記憶體 `mem`

`mem.data[addr]` 讀出一個 `u8` cell。`mem.data[addr] = expr` 寫入一個 `u8`，也可使用 `+=` 等擴充指派。只可存取 Target 宣告的資料空間。位址須是同一空間的 `Ptr`，或在該空間位址寬度內的非負整數常數。要在記憶體讀寫 16 位元值，須自己選擇位元組順序，分兩個 cell 存取。

擴充指派依序求值位址、讀取舊值、求值右式，再寫回同一位址。即使位址是有副作用的函式呼叫，也只求值一次。常數位址可用直接存取形式，可變位址需要間接形式。缺少可用形式時會得到 `UnsupportedError`。在同一空間中，兩個不同的已知常數位址可判定互不重疊，其餘情況保守視為可能重疊。選擇資料位址時須避開 [spill pool](guarantees.md) 的保留範圍。

## 函式與 ABI

函式最多八個一般位置參數，每個參數須有型別註記。回傳註記可省略。參數預設值、僅限位置的 `/`、僅限關鍵字的參數以及 `*args`、`**kwargs` 都不支援。呼叫圖須無環，包含間接遞迴。函式名稱只用 ASCII 英文字母、數字、底線，以字母或底線起頭，且須避開 Target 的暫存器及別名名稱。參數、區域變數與迴圈變數不能遮蔽模組常數、型別別名、函式或語言名稱。

`@fn(...)` 可省略，也可覆寫 Target 的預設呼叫慣例（ABI）。`args` 是與參數等長的 tuple，元素是暫存器名稱或 `("data", 位址)`。`ret` 使用相同的位置格式。`preserves` 是被呼叫者承諾保留的暫存器名稱 tuple。各欄位省略時由 Target 的 ABI 決定。暫存器的寬度須與值相符，固定記憶體位置只支援單一 cell，參數位置不得彼此重疊，`preserves` 不得覆蓋回傳位置。

`@extern(args=..., ret=..., clobbers=...)` 宣告已存在的外部函式。三個欄位都必填。無回傳值時省略函式的回傳註記並填 `ret=None`，沒有要宣告的暫存器變動時填 `clobbers=()`。外部函式本體寫成同一種受限 Python，供參考執行；編譯輸出只在呼叫處引用外部符號。呼叫後的旗標視為未定義。差異測試須另外提供符合此宣告及參考語意的機器碼樁（stub）。

```python
from rpkbin.mcucc.lang import extern, fn, u8


@extern(args=("a",), ret="a", clobbers=("b",))
def invert_low(value: u8) -> u8:
    return value ^ 0x0F


@fn(args=("a",), ret="a", preserves=())
def convert(value: u8) -> u8:
    return invert_low(value)
```

## 內嵌組語 `asm`

`asm` 把文字原樣交給目的組語環境，只替換 `{0}`、`{1}` 等位置。下例使用虛構的 `opaque` 文字和 `a`、`b` 兩個暫存器。

```python
from rpkbin.mcucc.lang import asm, u8


def inline_sum(left: u8, right: u8) -> u8:
    asm(
        "opaque {0}, {1}",
        ins=((left, "a"), (right, "b")),
        outs=((left, "a"),),
        clobbers=(),
        nbytes=1,
        ref=lambda left, right: left + right,
    )
    return left
```

- 文字須為字串常值。替代順序是 `ins` 後接 `outs`，相同變數只使用第一次出現的位置。位置須使用數字索引，無格式修飾。
- `ins`、`outs` 是已指派的參數或區域變數所組成的 tuple。兩者皆有的變數會先讀取再寫入。可為每個變數指定 `(變數, "暫存器名稱")`，讓編譯器依文字實際要求配置。省略位置提示時，編譯器從符合該值寬度的暫存器配置位置。
- `clobbers` 列出額外會改動的暫存器。`nbytes` 必填，須是正整數常數，表示整段文字組譯後的位元組數。只有確定整段文字不改旗標時才寫 `flags_ok=True`，否則將旗標視為未定義。
- `ref` 可省略。用它提供參考執行的效果時，依 `ins` 原順序接收值，單一 `outs` 回傳單值，多個 `outs` 依序回傳 tuple。未填 `ref` 而參考執行走到該敘述時會得到 `SourceError`。
- 任何含有 `asm` 的函式，其編譯報告 `verified` 為 `"unverified: asm"`。機器層級的差異測試會略過需要這類函式的測項。`assemble` 遇到原始 `asm` 文字會回報 `UnsupportedError`。請自行核對輸出、額外改動、旗標與 `nbytes` 宣告是否符合實際組語。

## 參考執行與錯誤

`rpkbin.mcucc.lang.run_reference(module_path_or_source, func_name, args, memory, *, addr_bits)` 接受來源字串或 `.py` 路徑、要執行的函式名稱、依宣告順序排列的引數、以及以資料空間名稱為鍵、整數 cell 清單為值的初始記憶體。`addr_bits` 必填，鍵為來源用的資料空間名稱，值為位址寬度。回傳 `(ret, memory_after)`，無回傳值時 `ret` 為 `None`。獨立呼叫 `run_reference` 沒有 spill pool 資訊。來源是否觸碰保留區由 `run_differential` 或 CLI `verify` 檢查。

每次呼叫都複製輸入記憶體，建立本次的模組執行環境，先綁定位址寬度，再執行模組，所以改變 `addr_bits` 也會讓同一份來源的指標別名取得不同寬度。執行有步數上限，超出時拋出 `TimeoutError`。差異測試如何計入逾時及比對記憶體，見 [保證與假設](guarantees.md)。

若來源的語法、型別、控制流或常數範圍違反語言規則，編譯會以含檔名和行號的 `SourceError` 回報。兩種執行流程都會檢查只依來源及位址寬度即可判定的規則。需要 Target 才能判定的暫存器、ABI、指令形式及資源問題，則由編譯時檢查。直接在 Python 使用 `u8`、`u16` 時，值或型別不相容也可能得到 `ValueError` 或 `TypeError`。要執行使用 `Ptr` 或 `mem` 的來源，請透過 `run_reference` 提供位址寬度及記憶體。常見訊息與修正方式見 [錯誤目錄](errors.md)。
