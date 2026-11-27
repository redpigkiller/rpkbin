# `rpkbin.mapbv` API 參考手冊

[English](../../en/mapbv/reference.md) · [文件導覽](../README.md) · [快速入門](README.md) · [實戰使用情境](scenarios.md)

本文件說明 `rpkbin.mapbv` 的技術規格，包含工廠函式、`MapBV`、`MapBVExpr`、格式化輸出、符號求值、運算子與例外觸發條件。

```python
from rpkbin.mapbv import MapBV, MapBVExpr, const, var, concat
```

`__all__` 匯出上述五個名稱。需要 Python 3.11 或更新版本。

## 工廠函式

### `var`

#### 語法
```python
reg = var(name, width, value=0)
```

#### 說明
建立一個可讀寫的具名暫存器變數（`kind == "VAR"`）。

#### 輸入引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `name` | `str` | 必填 | 非空且合法的 Python 識別碼（`name.isidentifier()`），例如 `"REG0"` 或 `"sig_out"`。 |
| `width` | `int` | 必填 | 正整數位元寬度（`> 0`）。接受實作 `__index__` 的整數（包含 NumPy 純量）；傳入 `bool` 引發 `TypeError`。 |
| `value` | `int` | `0` | 初始無號整數，範圍為 `0 <= value < 2**width`。 |

#### 輸出
回傳 `MapBV` 實例，其中 `kind == "VAR"`、`high == width - 1`、`low == 0`、`is_const == False`、`is_linked == False`。

### `const`

#### 語法
```python
c = const(value, width)
```

#### 說明
建立唯讀常數節點（`kind == "CONST"`、`name == "Constant"`）。

#### 輸入引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `value` | `int` | 必填 | 非負整數，範圍為 `0 <= value < 2**width`。 |
| `width` | `int` | 必填 | 正整數位元寬度（`> 0`）。傳入 `bool` 引發 `TypeError`。 |

#### 輸出
回傳 `MapBV` 實例，其中 `kind == "CONST"`、`is_const == True`。

### `concat`

#### 語法
```python
bus = concat(name, *parts)
```

#### 說明
將一個或多個既有 `MapBV` 節點依 MSB 到 LSB 順序串接成動態匯流排視圖。讀取時串接各部分；寫入時依 LSB 到 MSB 順序分派至各可寫入節點，常數位元則忽略寫入（僅在寫入值與常數不符時發出 `UserWarning`）。

#### 輸入引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `name` | `str` | 必填 | 合法的 Python 識別碼，作為匯流排名稱。 |
| `*parts` | `MapBV` | 必填 | 一個或多個 `MapBV` 節點（`var`、`const`、`slice` 或其他 `concat` 匯流排）。可寫入位元不得重疊；多個常數可同時出現。 |

#### 輸出
回傳 `MapBV` 實例，其中 `kind == "VAR"`、`is_linked == True`、`width == sum(p.width for p in parts)`。

## `MapBV`

代表硬體位元向量節點：變數、常數、切片或串接匯流排。

### 建立方式
建議透過 `var()`、`const()` 與 `concat()` 建立。底層建構式 `MapBV(parent, high, low, value=0)` 接受 `parent: str | MapBV | None`（當 `parent` 為 `str | None` 時 `low` 必須為 `0`）。

### 屬性
| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `name` | `str` | `"VAR"` 為變數名稱，`"CONST"` 為 `"Constant"`，`"SLICE"` 為 `f"{parent.name}[{high}:{low}]"`。 |
| `width` | `int` | 位元寬度（`high - low + 1`，等於 `len(bv)`）。 |
| `high` | `int` | 閉區間最高位元索引（獨立節點為 `width - 1`）。 |
| `low` | `int` | 閉區間最低位元索引（獨立節點為 `0`）。 |
| `kind` | `str` | `"VAR"`、`"CONST"` 或 `"SLICE"`。 |
| `is_const` | `bool` | 僅在 `kind == "CONST"` 時為 `True`。注意：常數切片的 `kind` 為 `"SLICE"`（`is_const == False`），但寫入仍會引發 `TypeError`。 |
| `is_linked` | `bool` | 由 `concat()` 建立時為 `True`。 |
| `value` | `int` | 可讀寫的無號整數屬性。對常數或常數切片寫入會引發 `TypeError`。 |

### 物件函式
| 方法／運算子 | 用途 |
| :--- | :--- |
| `bv[high:low]`、`bv[bit]` | 回傳即時連動切片視圖（寬度為 `high - low + 1`，單一位元為 `1`）。 |
| `bv[high:low] = val`、`bv[bit] = val` | 將 `val` 寫回底層暫存器或匯流排。 |
| `bv.value_eq(other)` | 比較 `.value` 數值是否等於另一個 `MapBV`、`MapBVExpr` 或 `__index__` 整數（對 `None`、`str`、`float` 等非整數型別回傳 `False`）。 |
| `bv.to_hex()` | 回傳大寫十六進位字串，補零至 `(width + 3) // 4` 位（例如 4 位元 `0xF` 為 `"0xF"`，8 位元 `0xA` 為 `"0x0A"`）。 |
| `bv.to_bin()` | 回傳二進位字串，補零至 `width` 位（例如 4 位元 `0x3` 為 `"0b0011"`）。 |
| `format(bv, spec)`（`__format__`） | `"hex"` 呼叫 `to_hex()`；`"bin"` 呼叫 `to_bin()`；其他格式碼（包含 `""`、`"x"`、`"08b"`、`"d"`）交由 `format(bv.value, spec)` 處理。 |
| `bv.eval(ctx)` | 使用 `ctx: dict[str, int]` 進行符號求值，只比對最底層葉節點 `var()` 的名稱。 |
| `str(bv)` | 回傳多行結構文字：標頭為 `name[high:low] (0x...)`，未串接時附加 `\n  (no link)`，已串接時依 MSB 到 LSB 列出欄寬一致的 `[high:low] 0x...  <- source_name`。 |
| `repr(bv)` | 回傳除錯摘要，例如 `<MapBV VAR R[15:0] value=0x1234>` 或 `<MapBV VAR linked BUS[11:0] value=0x125>`。 |
| `bv1 == bv2`、`hash(bv)` | 比較物件同一性（`self is other`）並以 `id(self)` 雜湊。與 `numbers.Integral`（`int`、`bool`、NumPy 整數）比較會引發 `TypeError`；與 `None` 或 `"x"` 比較回傳 `False`。 |
| `len(bv)`、`int(bv)`、`bool(bv)` | 分別回傳 `bv.width`、`bv.value` 與 `bv.value != 0`。 |

#### 切片規則
- 索引採閉區間（`[high:low]` 包含兩端），必須滿足 `parent.low <= low <= high <= parent.high`。
- 巢狀切片沿用根暫存器座標：對 `r = var("R", 16)`，`r[15:8][11:8]` 合法（名稱為 `"R[15:8][11:8]"`），但 `r[15:8][3:0]` 因 `0 < 8` 會引發 `IndexError`。
- 省略邊界（`bv[:4]`、`bv[4:]`、`bv[:]`）與步長（`bv[7:0:2]`）引發 `ValueError`；反向切片（`high < low`）或越界引發 `IndexError`；`bool` 索引或邊界引發 `TypeError`。

#### 符號求值（`eval(ctx)`）行為
- `CONST` 永遠回傳固定常數值。
- `SLICE` 遞迴呼叫 `parent.eval(ctx)` 並擷取對應位元；對 `concat()` 匯流排再切片（如 `bus[7:4].eval(ctx)`）同樣會往下解析至最底層的 `var()` 節點。
- `concat()` 串接匯流排遞迴呼叫每個子節點的 `.eval(ctx)`，依 MSB 到 LSB 重組數值。
- 只有最底層的 `var()` 葉節點會檢查 `self.name in ctx`。傳入串接匯流排名稱或切片名稱的鍵（例如 `{"BUS": 0xAB}` 或 `{"R[7:0]": 0xF}`）會被忽略，求值仍以底層 `var()` 名稱解析。
- `ctx` 必須支援 `name in ctx` 與 `ctx[name]` 查找（傳入 `None` 或非容器物件會引發 `TypeError`）。命中的 `ctx` 值會透過 `operator.index` 轉換並檢查 `0 <= v < 2**width`（超出範圍引發 `ValueError`）；未命中的 `var()` 則回退使用其目前 `.value`。

## `MapBVExpr`

對 `MapBV` 或 `MapBVExpr` 執行位元運算時產生的延遲求值運算式樹。

### 建立方式
使用 `&`、`|`、`^`、`~`、`<<` 或 `>>` 建立：

| 運算子 | 語意 | 輸出位元寬度 |
| :--- | :--- | :--- |
| `a & b` | 位元 AND | `max(width_a, width_b)`（若 `b` 為整數則為 `width_a`） |
| `a \| b` | 位元 OR | `max(width_a, width_b)`（若 `b` 為整數則為 `width_a`） |
| `a ^ b` | 位元 XOR | `max(width_a, width_b)`（若 `b` 為整數則為 `width_a`） |
| `~a` | 位元 NOT | `width_a` |
| `a << n` | 左移非負整數 `n` 位（超出 `width_a` 的位元截斷） | `width_a` |
| `a >> n` | 右移非負整數 `n` 位（高位補零） | `width_a` |

- `&`、`|` 與 `^` 的另一側可為 `MapBV`、`MapBVExpr`，或符合 `0 <= n` 且 `n.bit_length() <= bv.width` 的 `__index__` 整數。`bv & 0x0F` 與反射寫法 `0x0F & bv`（含 NumPy 純量，透過 `__array_ufunc__ = None`）皆可運作。
- `<<` 與 `>>` 右側必須為非負 `__index__` 整數；不支援 `n << bv`，該寫法會引發 `TypeError`。

### 屬性
| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `value` | `int` | 唯讀屬性。依所有運算元目前的 `.value` 動態計算結果；嘗試賦值會引發 `AttributeError`。 |
| `width` | `int` | 運算式輸出位元寬度。 |

### 物件函式
| 方法／運算子 | 用途 |
| :--- | :--- |
| `expr.eval(ctx)` | 依 `ctx: dict[str, int]` 遞迴求值，只比對底層 `var()` 名稱（忽略 `concat()` 匯流排與切片名稱）。 |
| `expr.value_eq(other)` | 比較 `.value` 是否等於另一個 `MapBV`、`MapBVExpr` 或 `__index__` 整數。 |
| `expr.to_hex()`、`expr.to_bin()` | 將目前計算結果依 `expr.width` 補零格式化為十六進位或二進位字串。 |
| `format(expr, spec)`（`__format__`） | 支援 `:hex`、`:bin`，其餘格式碼委派給 `format(expr.value, spec)`。 |
| `repr(expr)`、`str(expr)` | 回傳 `<MapBVExpr <tree> width=N value=0x...>`（`MapBVExpr` 未覆寫 `__str__`，因此 `str(expr)` 直接沿用 `repr(expr)`）。 |
| `expr1 == expr2`、`hash(expr)` | 物件同一性（`self is other`）與 `id(self)`；與整數比對引發 `TypeError`。 |
| `len(expr)`、`int(expr)`、`bool(expr)` | 分別回傳 `expr.width`、`expr.value` 與 `expr.value != 0`。 |

## 範例

### 1. 基本範例：`str(bv)`、`__format__` 與 `repr`

檢視暫存器、串接匯流排與運算式的格式化輸出、`str(bv)` 結構表與 `repr()` 結果。

```python
import rpkbin.mapbv as mbv

hi = mbv.var("HI", 4, 0xA)
lo = mbv.var("LO", 4, 0x3)
bus = mbv.concat("BUS", hi, mbv.const(0, 2), lo[1:0])
expr = (hi << 1) ^ 0xF

print(f"format: hex={bus:hex} bin={bus:bin} x={bus:x} d={bus:d}")
print(repr(bus))
print(repr(expr))
print(str(bus))
```

輸出：
```text
format: hex=0xA3 bin=0b10100011 x=a3 d=163
<MapBV VAR linked BUS[7:0] value=0xA3>
<MapBVExpr ((HI << 1) ^ 0xF) width=4 value=0xB>
BUS[7:0] (0xA3)
  [7:4] 0xA  <- HI
  [3:2] 0x0  <- Constant
  [1:0] 0x3  <- LO[1:0]
```

### 2. 進階範例：`MapBVExpr.value` 與 `eval(ctx)` 只比對底層 `var()` 名稱

驗證 `MapBVExpr.value` 會隨來源變數即時更新，並確認 `eval(ctx)` 只比對底層 `var()` 名稱（`"HI"`、`"LO"`），忽略中間 `concat()` 匯流排名稱（`"BUS"`）。

```python
import rpkbin.mapbv as mbv

hi = mbv.var("HI", 4, 0x2)
lo = mbv.var("LO", 4, 0x5)
bus = mbv.concat("BUS", hi, lo)
expr = bus ^ 0x0F

print("initial expr.value:", hex(expr.value))

hi.value = 0xC
print("after HI=0xC, expr.value:", hex(expr.value))

# "BUS" 會被忽略，因為 eval() 會往下解析底層 var() 節點 "HI" 與 "LO"。
print("eval ignoring BUS:", hex(expr.eval({"BUS": 0x00, "HI": 0xA})))
print("eval with leaf vars:", hex(expr.eval({"HI": 0x1, "LO": 0x2})))
print("real bus.value unchanged:", bus.to_hex())
```

輸出：
```text
initial expr.value: 0x2a
after HI=0xC, expr.value: 0xca
eval ignoring BUS: 0xaa
eval with leaf vars: 0x1d
real bus.value unchanged: 0xC5
```

## 例外與警告規格

| 例外／警告 | 觸發條件 |
| :--- | :--- |
| `TypeError` | `bv == integer` 或 `expr == integer`（任何非 `_BVBase` 的 `numbers.Integral`，含 `bool` 與 NumPy 整數）；寫入 `CONST` 或其切片的 `.value`；以 `bool` 作為 `width`、切片邊界或位元索引；傳入非 `str` 給 `var()` 或 `concat()` 的 `name`（例如 `var(123, 8)`），或 `MapBV()` 的 `parent` 不是 `str`、`MapBV` 或 `None`；傳入不支援整數索引的 `value`（`var("A", 8, "x")`、`bv.value = "x"`）、切片鍵（`bv["0"]`）、運算元（`bv & "x"`、`bv \| 1.5`、`1 << bv`）或 `eval()` 的 `ctx` 值；傳入非容器 `ctx`（例如 `None`）給 `eval()`；傳入非 `MapBV`（例如 `int` 或 `MapBVExpr`）給 `concat()`。 |
| `ValueError` | `var()` 或 `const()` 的 `width <= 0`；對獨立節點（`parent` 為 `str \| None`）呼叫 `MapBV(parent, high, low)` 時 `low != 0`，或 `high - low + 1 <= 0`；`var()` 或 `concat()` 的名稱不是合法 Python 識別碼；建立、賦值或 `eval(ctx)` 時出現負數或 `>= 2**width`；`&`、`\|`、`^` 遇到負整數或 `bit_length() > bv.width` 的整數；`<<`、`>>` 遇到負位移量；切片包含 `step`（`bv[7:0:2]`）或省略邊界（`bv[:4]`、`bv[4:]`、`bv[:]`）；`concat(name)` 未提供任何元件；`concat()` 元件包含重疊的可寫入位元。 |
| `IndexError` | 反向切片（`high < low`）；切片或位元索引超出 `[parent.low, parent.high]`（包含巢狀切片未使用根座標）。 |
| `AttributeError` | 嘗試對唯讀屬性賦值，包含 `MapBVExpr.value` 與 `MapBV` 結構屬性（`width`、`name`、`kind`、`high`、`low`、`is_const`、`is_linked`）。 |
| `UserWarning` | 寫入 `concat()` 匯流排時，對應到 `CONST`（或常數切片）的位元與原常數不同；常數位元維持不變。 |

## 注意事項與限制

- `MapBV` 節點可變且具有即時連結，因此 `==` 與 `hash()` 依物件同一性運作。請勿直接以 `==` 拿節點與整數比對，應使用 `.value` 或 `.value_eq()`。
- 位元位移維持原節點寬度：`(var("X", 4, 5) << 4)` 的結果為 `0`（`width=4`），而非 `0x50`。需要更寬的結果時，請先擴寬或使用 `concat()`。

## 延伸閱讀

- [快速入門](README.md)
- [實戰使用情境](scenarios.md)
- [文件導覽](../README.md)
