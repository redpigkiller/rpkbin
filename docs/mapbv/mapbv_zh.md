# MapBV — 暫存器與位元映射

[![English](https://img.shields.io/badge/Language-English-blue.svg)](mapbv.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](mapbv_zh.md)

`MapBV` (Map BitVector) 是一個為 IC 設計與驗證打造的輕量級 BitVector 函式庫。它能透過直覺的 Python 物件來描述暫存器 (Register)、共享記憶體 (SRAM) 映射以及邏輯運算。

本套件支援**雙向數值同步** (更改父暫存器會反映在其切片上，反之亦然)、**符號化求值 (Symbolic Evaluation)** 以及易讀的結構化列印。

---

## 快速開始 (使用指南)

要操作 `MapBV`，建議一律透過我們提供的三個工廠函式 `var`, `const`, `concat` 來建立物件。

### 1. 建立變數與常數

```python
import rpkbin.mapbv as mbv

# 宣告兩個 16-bit 暫存器 (變數)，初始值為 0
reg0 = mbv.var("REG0", 16)
reg1 = mbv.var("REG1", 16)

# 宣告一個 2-bit 常數，值永遠為 0
padding = mbv.const(0, 2)
```

### 2. 位元切片 (Slicing)

你可以像操作 Python list 一樣，直接對暫存器取切片 (硬體慣例：包含兩端，MSB在前)：
```python
reg0.value = 0xABCD

# 讀取切片即時反映父暫存器的數值
print(reg0[7:4].to_hex())  # → 0xA

# 寫入切片也會直接更新父暫存器
reg0[7:4].value = 0xF
print(reg0.to_hex())       # → 0xFBCD
```

### 3. 變數連結與打包 (Concatenation / Linking)

在建立更大的匯流排或 SRAM word 時，可以使用 `concat` 將多個來源拼接起來 (按照 高位 MSB 排到低位 LSB)。
拼接後的 MapBV 會與原來源**雙向連結**！

```python
# 從零開始連接出一個 8-bit 的 word: {reg0[3:0], padding, reg1[1:0]}
sram = mbv.concat("SRAM_00", reg0[3:0], padding, reg1[1:0])

# 修改來源暫存器，SRAM 會即時改變
reg0[3:0].value = 0x5
reg1[1:0].value = 0x2
print(sram.to_hex())       # → 0x52 (相當於 0x5 << 4 | 0x0 << 2 | 0x2)

# 從 SRAM 寫入，值會自動分配回各個來源暫存器
sram.value = 0xF3
print(reg0[3:0].to_hex())  # → 0x0F
print(reg1[1:0].to_hex())  # → 0x03
```
> **注意**：如果從 SRAM 寫入了會影響常數 `padding` 的數值 (例如 `0xFF`)，會觸發 `UserWarning` 提示常數不可變更並被忽略。因此若你接著去讀取 `sram` 的值，常數部分依舊會維持原始決定好的狀態 (回傳 `0xF3`)。

### 4. 手動連結 (link) 與解除連結 (detach)

除了 `concat` 可以在建立時順便打包外，你也可以先宣告一個空的變數，隨後再透過 `link` 手動將其與其他暫存器綁定。
如果你希望將某個變數的狀態「定格」並切斷與來源的連動，可以使用 `detach`。

```python
# 先建立一個空的 8-bit 變數
sram_b = mbv.var("SRAM_01", 8)

# 手動將其與其他變數綁定 (總寬度必須剛好 8-bit)
sram_b.link(reg0[3:0], padding, reg1[1:0])
print(sram_b.to_hex())     # → 即時反映當前 reg0, padding, reg1 的狀態

# 解除連結 (Detach)
sram_b.detach()

# 解除後，sram_b 變成獨立變數，保留 detach 瞬間的數值，且不再受來源暫存器影響
reg0[3:0].value = 0x0
print(sram_b.to_hex())     # → 維持原本的數值，不受 reg0 變成 0 的影響
```

### 5. 邏輯運算與假設性分析

`MapBV` 支援原生的邏輯操作 (`&`, `|`, `^`, `~`, `<<`, `>>`)；整數運算元會依 expression 寬度自動 mask。你也可以透過傳遞上下文字典來「假設」數值 (`eval`)，並觀察對應輸出的結果，而不會改動到真實機制的暫存器狀態。

```python
# 邏輯運算 (回傳 expr 節點)
result_expr = (reg0 & 0x00FF) | reg1
print(result_expr.value)

# 假設 REG0 現在是 0xAAAA，SRAM 的值會變多少？(不影響 sram.value 真實值)
simulated = sram.eval({"REG0": 0xAAAA, "REG1": 0x3})
print(hex(simulated))       # → 0xa3
```

---

## API 參考 (詳細控制)

### 建立物件 (工廠函式)

| 函式 | 說明 |
| --- | --- |
| `mbv.var(name, width, value=0)` | 建立一個名為 `name`、寬度 `width` bit 的變數 (`"VAR"`)。可給定初始 `value`。 |
| `mbv.const(value, width)` | 建立不可變常數 (`"CONST"`)。`value` 會自動 mask 去除掉超出 `width` 範圍的位元。 |
| `mbv.concat(name, *parts)` | 建立並回傳一個名為 `name` 的變數，並自動設定其連結包含 `parts` (`MapBV` 實例，MSB→LSB排序)。寬度為 parts 寬度總和。 |

---

### `MapBV` 核心屬性

每個 `MapBV` 節點都有以下屬性可供查詢：

- `.name` (`str`): 名稱。常數為 `"Constant"`，切片為 `"NAME[high:low]"`。
- `.width` (`int`): 位元寬度。
- `.high` / `.low` (`int`): 當前物件對應到的最高與最低位元索引 (從0開始)。
- `.value` (`int`): 當前的數值。讀寫已連結或切片的物件時，會自動即時解析。
- `.kind` (`str`): 節點類型：`"CONST"`(常數), `"VAR"`(變數), 或 `"SLICE"`(切片)。
- `.is_const` (`bool`): 是否為常數。
- `.is_linked` (`bool`): 此變數是否有使用 `link` 與別的部位綁定。

---

### 詳細控制機制

#### 1. 切片機制 (`SLICE` 與 `__getitem__`)
- **語法**: `bv[high:low]` 或是單一位元 `bv[bit]`。
- 切片是**包含式 (inclusive)** 兩端索引。例如 `[7:0]` 包含從位元 0 到位元 7 的內容。
- **限制**: 只有 `VAR` 可以呼叫 `link()`；`CONST` 與 `SLICE` 都不能成為 mapping target。如果需要映射特定區段，請先建立一個實體的 `var()` 再進行連結。

#### 2. 連結機制 (`link` 與 `detach`)
除了使用 `concat` 直接做拼接外，你也可以先建置空變數後，再手動呼叫 `link`。
- **`link(*parts)`**:
  - 將數個 `MapBV` 從高位到低位綁定位到當前變數上。
  - 當前變數的 `width` 必須完全等於所有 `parts` 的寬度加總，否則會丟出 `ValueError`。
  - `parts` 中可寫入的位元範圍不能重疊；有歧義的雙向映射會丟出 `ValueError`。
  - 防錯機制：不可產生循環參照 (Circular Link)。
  - 若在該變數已經 link 其他節點的狀態下再次呼叫，會拋出 `UserWarning` 並覆寫舊的連結。
- **`detach()`**:
  - 斷開當前與所有 `parts` 的連結關係。
  - 觸發時，系統會先快照當下的計算值 (`.value`)，並保存到內部的 `_raw_value` 中。它將變回一個沒有連結的獨立正常變數，此後來源有任何改變都不會影響它。

#### 3. 格式化導出 (`to_hex`, `to_bin`, `__str__`)
- **十六進位與二進位**: `bv.to_hex()` (格式如 `0x00FF`)，`bv.to_bin()` (格式如 `0b00001010`)。此外支援 Python 標準字串格式化，所以 `f"{bv:hex}"` 將完全等效於 `to_hex()`。
- **結構化列印**: 直接 `print(bv)` 會依照目前階層幫你漂亮地排版，包含每個位元段的數值以及來源名稱。

```python
print(sram)
# 結構化輸出範例：
# SRAM_00[7:0] (0xF3)
#   [7:4] 0x0F  <- REG0[3:0]
#   [3:2] 0x00  <- Constant
#   [1:0] 0x03  <- REG1[1:0]
```

#### 4. `==` 運算子與 `value_eq`
- 在 `MapBV` 中，為了保證物件能作為雜湊鍵放入 `dict` 或 `set` 中不混亂，**`==` 代表物件身份 (Identity) 比較** (`is`)，而不是變數內部的數值比較。
- 如果你要比較數值，請明確使用 `.value_eq(other)` 或直接比對 `.value == other.value`。
