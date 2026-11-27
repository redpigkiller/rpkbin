# `rpkbin.mapbv`

[English](../../en/mapbv/README.md) · [文件導覽](../README.md) · [實戰使用情境](scenarios.md) · [API 參考手冊](reference.md)

`rpkbin.mapbv` 以純 Python 建立無號硬體暫存器與位元欄位模型，讓切片、父暫存器、串接匯流排與邏輯運算式維持即時同步。

## 語法

```python
reg = mbv.var(name, width, value=0)
c = mbv.const(value, width)
bus = mbv.concat(name, *parts)
field = reg[high:low]
bit = reg[bit_index]
text = f"{reg:hex}"
bits = f"{reg:bin}"
```

## 說明

- `reg = mbv.var(name, width, value=0)` 建立可寫入的具名無號暫存器，寬度為 `width` 位元，初始值為 `value`。
- `c = mbv.const(value, width)` 建立唯讀常數節點，用來表示固定識別碼或保留位元。
- `bus = mbv.concat(name, *parts)` 依 MSB 到 LSB 順序串接既有的變數、常數、切片或匯流排，寫入時會依寬度拆解並寫回底層可寫入節點。
- `field = reg[high:low]` 與 `bit = reg[bit_index]` 建立雙端包含的閉區間切片視圖（`high >= low`）；指派 `.value` 或 `reg[high:low] = val` 會立刻更新原暫存器。
- `f"{reg:hex}"` 與 `f"{reg:bin}"` 依節點位元寬度補零格式化輸出（十六進位補至 `ceil(width / 4)` 位，二進位補至 `width` 位）。

## 範例

### 1. 基本範例：建立變數、常數與格式化輸出

建立一個 16 位元暫存器變數與一個 4 位元常數，並用 `to_hex()`、`to_bin()` 及 f-string 格式碼印出結果。

```python
import rpkbin.mapbv as mbv

reg = mbv.var("REG0", 16, 0x1234)
mask = mbv.const(0xF, 4)

print(f"Variable: {reg.name}, width: {reg.width} bits, hex: {reg.to_hex()}")
print(f"Constant: {mask.name}, width: {mask.width} bits, hex: {mask:hex}")
print(f"Binary:   {reg:bin}")
```

輸出：
```text
Variable: REG0, width: 16 bits, hex: 0x1234
Constant: Constant, width: 4 bits, hex: 0xF
Binary:   0b0001001000110100
```

### 2. 進階範例：欄位切片與雙向即時同步

使用 Verilog 風格的閉區間 `[high:low]` 切出欄位，並驗證原暫存器與切片之間的雙向更新。

```python
import rpkbin.mapbv as mbv

reg = mbv.var("REG0", 16, 0x1234)
high_byte = reg[15:8]
bit3 = reg[3]

print(f"high_byte width: {len(high_byte)} bits, value: {high_byte:hex}")
print(f"bit3 width: {len(bit3)} bits, value: {bit3.value}")

reg[15:8] = 0xAB
print(f"After slice index write: reg={reg:hex}, high_byte={high_byte:hex}")

high_byte.value = 0x55
print(f"After slice .value write: reg={reg:hex}")
```

輸出：
```text
high_byte width: 8 bits, value: 0x12
bit3 width: 1 bits, value: 0
After slice index write: reg=0xAB34, high_byte=0xAB
After slice .value write: reg=0x5534
```

## 注意事項與限制

- **物件同一性與數值比較（`==`）**：`reg == other` 比較物件同一性（`self is other`），確保可變節點仍可作為 dict 鍵或放入 set。直接拿 `MapBV` 或 `MapBVExpr` 與整數比對（`reg == 0x5534`）會引發 `TypeError`；請改用 `reg.value == 0x5534` 或 `reg.value_eq(0x5534)`。
- **無號範圍與閉區間規則**：不接受負數、大於等於 `2**width` 的數值、切片步長（`reg[7:0:2]`）與反向索引（`reg[0:7]`）。

```python
import rpkbin.mapbv as mbv

reg = mbv.var("REG0", 16, 0x5534)
assert reg.value == 0x5534
assert reg.value_eq(0x5534)

for bad in (lambda: reg == 0x5534, lambda: reg[3:0].__setattr__("value", 0x10)):
    try:
        bad()
    except (TypeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}")
```

輸出：
```text
TypeError: MapBV == int compares identity and is almost certainly a bug; use `bv.value == n` or `bv.value_eq(n)`
ValueError: Value 0x10 out of bounds for 4-bit MapBV (max 0xF)
```

- **相容性**：純 Python `>= 3.11`，離線執行且不進行外部 I/O。位寬、數值與索引接受實作 `__index__` 的整數物件（包含 NumPy 整數純量），但位寬與切片索引會拒絕 `bool`（`TypeError`）。範例已在 Windows、Python 3.13 實測驗證。

## 延伸閱讀

- [實戰使用情境](scenarios.md)
- [API 參考手冊](reference.md)
- [文件導覽](../README.md)
