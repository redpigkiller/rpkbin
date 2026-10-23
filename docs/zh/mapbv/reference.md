# MapBV API、型別與精確語意參考手冊

[English](../../en/mapbv/reference.md) · [文件導覽](../README.md) · [實戰情境](scenarios.md)

本文件提供 `rpkbin.mapbv` 模組的完整 API 規範、型別定義、運算子支援與例外規格。

模組匯入方式：
```python
import rpkbin.mapbv as mbv
```

---

## 工廠函式 (Factory Functions)

工廠函式是建立 MapBV 物件的標準推薦入口。

### `var(name: str, width: int, value: int = 0) -> MapBV`
建立一個具名的暫存器變數。
- **`name`** (`str`): 變數名稱，必須為合法的 Python 識別碼（如 `"REG0"`、`"data_in"`）。
- **`width`** (`int`): 位元寬度，必須 $> 0$。
- **`value`** (`int`, 預設 0): 初始整數數值，必須在 $[0, 2^{\text{width}} - 1]$ 範圍內。
- **回傳**：`kind == "VAR"` 的 `MapBV` 實例。

### `const(value: int, width: int) -> MapBV`
建立一個唯讀常數。
- **`value`** (`int`): 常數整數值，必須在 $[0, 2^{\text{width}} - 1]$ 範圍內。
- **`width`** (`int`): 位元寬度，必須 $> 0$。
- **回傳**：`kind == "CONST"` 的 `MapBV` 實例。

### `concat(name: str, *parts: MapBV) -> MapBV`
將多個 MapBV 物件依 **MSB → LSB**（高位元至低位元）順序串接為一個新的 Bus 視圖。
- **`name`** (`str`): 串接後的名稱。
- **`parts`** (`MapBV`): 一個或多個來源物件（可為 `var`、`const`、`slice` 或其他串接物件）。
- **約束**：串接的 parts 中不得包含重疊的底層可寫入位元，否則引發 `ValueError`。
- **回傳**：`kind == "VAR"` 且 `is_linked == True` 的 `MapBV` 實例。

---

## 核心類別：`MapBV`

`MapBV` 代表一個硬體位元向量節點（獨立變數、常數、切片或串接匯流排）。

### 屬性 (Properties)

| 屬性 | 型別 | 說明 |
| :--- | :--- | :--- |
| **`name`** | `str` | 節點名稱。獨立變數為名稱；切片為 `parent[high:low]`；常數為 `"Constant"`。 |
| **`width`** | `int` | 位元寬度（等於 `len(bv)`）。 |
| **`high`** | `int` | 最高位元索引（閉區間）。獨立變數為 `width - 1`。 |
| **`low`** | `int` | 最低位元索引（閉區間）。獨立變數為 `0`。 |
| **`kind`** | `str` | 節點種類：`"VAR"`（變數）、`"CONST"`（常數）或 `"SLICE"`（切片）。 |
| **`is_const`**| `bool` | 是否為常數節點（`kind == "CONST"`）。 |
| **`is_linked`**| `bool`| 是否為由 `concat` 串接產生的 Bus 視圖。 |
| **`value`** | `int` | **可讀寫**。讀取時返回無號整數；寫入時會更新底層暫存器或分派至各 parts。 |

### 索引與切片語意 (`__getitem__`, `__setitem__`)

- **閉區間切片**：`bv[high:low]` 同時包含 high 與 low 位元。切片寬度為 $high - low + 1$。
- **約束條件**：
  - 必須滿足 $low \le high$。若 $high < low$ 拋出 `IndexError`。
  - 必須在父節點的 $[high:low]$ 邊界內，否則拋出 `IndexError`。
  - 不支援步長（step），若指定 `bv[high:low:step]` 拋出 `ValueError`。
- **單一位元**：`bv[bit]` 等同於 `bv[bit:bit]`，寬度為 1。
- **切片賦值**：`bv[high:low] = val` 會直接將數值寫入對應的底層暫存器欄位。

### 比較方法與魔術方法

- **`bv1 == bv2`**：比較物件識別同一性（`self is other`）。
  - **重要例外**：若與整數比較（`bv == 0x12`），主動拋出 `TypeError` 以防邏輯錯誤。
- **`bv.value_eq(other: MapBV | int) -> bool`**：比較數值是否相等。
- **`len(bv) -> int`**：返回位元寬度 `bv.width`。
- **`int(bv) -> int`**：返回數值 `bv.value`。
- **`bool(bv) -> bool`**：若 `bv.value != 0` 返回 `True`，否則 `False`。
- **`to_hex() -> str`**：返回大寫十六進位字串（如 4 位元為 `'0xF'`、8 位元為 `'0xFF'`；依位元寬度對齊十六進位位數補零至 $(width + 3) // 4$ 位，而非固定位元組對齊）。
- **`to_bin() -> str`**：返回二進位字串，如 `'0b00001111'`（按位元寬度補零）。
- **`eval(ctx: dict[str, int]) -> int`**：使用情境字典計算該節點數值。若字典無對應名稱則回退至真實 `.value`。
- **`str(bv) -> str`**：若為串接物件，列印出對齊的位元映射結構表。

---

## 運算式節點：`MapBVExpr`

當對 `MapBV` 或 `MapBVExpr` 執行位元邏輯運算時，會回傳 `MapBVExpr` 物件。運算式在求值時延遲計算，並永遠依據當前底層變數的值進行動態解析。

### 支援運算子

| 運算子 | 語意 | 位元寬度規則 |
| :---: | :--- | :--- |
| **`a & b`** | 位元 AND | $\max(\text{width}_a, \text{width}_b)$ |
| **`a \| b`** | 位元 OR | $\max(\text{width}_a, \text{width}_b)$ |
| **`a ^ b`** | 位元 XOR | $\max(\text{width}_a, \text{width}_b)$ |
| **`~a`** | 位元 NOT (反相) | $\text{width}_a$ |
| **`a << n`**| 左移（非負整數 $n$） | $\text{width}_a$（溢位部分依寬度截斷） |
| **`a >> n`**| 右移（非負整數 $n$） | $\text{width}_a$（高位補零） |

- **整數運算元支援**：運算子右側（或反射運算左側）可為 Python 非負整數。整數的 bit_length 不得大於 MapBV 的位元寬度。
- **方法**：
  - **`.value`**：動態計算並返回當前整數結果。
  - **`eval(ctx: dict[str, int]) -> int`**：將字典代入運算式各變數並計算結果。

---

## 例外規格 (Exceptions)

| 例外類別 | 觸發時機 |
| :--- | :--- |
| **`TypeError`** | - 直接使用 `bv == integer` 進行相等比對。<br>- 直接對 `kind == "CONST"` 的常數物件賦值。<br>- `concat()` 傳入非 MapBV 類型的參數。 |
| **`ValueError`** | - 宣告寬度 $\le 0$。<br>- 賦值負數。<br>- 賦值數值超出位元寬度最大值（$2^{\text{width}} - 1$）。<br>- 位元運算元包含負數或超出位元寬度之整數。<br>- 切片語法指定了 `step`。<br>- `concat()` 串接中存在重疊的可寫入位元目標。 |
| **`IndexError`** | - 切片索引超出父物件範圍。<br>- 切片索引為反向（$high < low$）。 |
| **`UserWarning`**| - 當向包含常數/保留位元的串接 Bus 寫入時，若寫入的值與常數定義值不符，會發出警告並維持原常數不變。 |
