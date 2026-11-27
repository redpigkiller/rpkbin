# MapBV 實戰使用情境

[English](../../en/mapbv/scenarios.md) · [文件導覽](../README.md) · [快速入門](README.md) · [API 參考手冊](reference.md)

以下六個獨立情境示範硬體暫存器欄位切片、匯流排串接、常數位元防護、組合邏輯運算式、符號求值與邊界檢查。

## 目錄

1. [情境一：暫存器欄位宣告與雙向即時連動](#情境一暫存器欄位宣告與雙向即時連動)
2. [情境二：硬體 Bus 串接 (`concat`) 與位元分派](#情境二硬體-bus-串接-concat-與位元分派)
3. [情境三：硬體保留位元與常數防護機制](#情境三硬體保留位元與常數防護機制)
4. [情境四：組合邏輯運算式延遲求值](#情境四組合邏輯運算式延遲求值)
5. [情境五：情境字典代入與符號求值 (`eval`)](#情境五情境字典代入與符號求值-eval)
6. [情境六：常見誤區、型別陷阱與邊界檢查](#情境六常見誤區型別陷阱與邊界檢查)

## 情境一：暫存器欄位宣告與雙向即時連動

### 目標
建立一個 32 位元的控制暫存器 `CTRL_REG`，包含 `[31]`（`enable`）、`[23:16]`（`burst_len`）與 `[7:0]`（`priority`）。寫入任一欄位會立刻反映於整個暫存器；更新整個暫存器也會同步反映於各欄位。

```python
import rpkbin.mapbv as mbv

ctrl_reg = mbv.var("CTRL_REG", 32, 0x0000_0000)

enable = ctrl_reg[31]
burst_len = ctrl_reg[23:16]
priority = ctrl_reg[7:0]

enable.value = 1
burst_len.value = 0x10
priority.value = 0x05

print(f"CTRL_REG 總值: {ctrl_reg:hex}")
assert ctrl_reg.value == 0x8010_0005

ctrl_reg.value = 0x00FF_0000
print(f"enable 欄位:    {enable.value}")
print(f"burst_len 欄位: {burst_len:hex}")
print(f"priority 欄位:  {priority:hex}")

assert enable.value == 0
assert burst_len.value == 0xFF
assert priority.value == 0
print("情境一驗證通過！")
```

輸出：
```text
CTRL_REG 總值: 0x80100005
enable 欄位:    0
burst_len 欄位: 0xFF
priority 欄位:  0x00
情境一驗證通過！
```

## 情境二：硬體 Bus 串接 (`concat`) 與位元分派

### 目標
將多個暫存器拼裝成一條較寬的匯流排視圖，向匯流排寫入時會依位元寬度自動拆解並寫回各底層暫存器。

```python
import rpkbin.mapbv as mbv

reg_a = mbv.var("REG_A", 4, 0x1)
reg_b = mbv.var("REG_B", 4, 0x2)
reg_c = mbv.var("REG_C", 8, 0x34)

# 依 MSB 到 LSB 順序拼裝：[15:12]=REG_A, [11:8]=REG_B, [7:0]=REG_C
sram_bus = mbv.concat("SRAM_BUS", reg_a, reg_b, reg_c)

print(f"串接後的 Bus 名稱: {sram_bus.name}")
print(f"總位元寬度: {sram_bus.width} bits")
print(f"初始讀取值: {sram_bus:hex}")
assert sram_bus.value == 0x1234

print("\n--- 內部結構映射 ---")
print(str(sram_bus))

sram_bus.value = 0xABCD
print("\n寫入 0xABCD 後：")
print(f"REG_A: {reg_a:hex}")
print(f"REG_B: {reg_b:hex}")
print(f"REG_C: {reg_c:hex}")

assert reg_a.value == 0xA
assert reg_b.value == 0xB
assert reg_c.value == 0xCD
print("情境二驗證通過！")
```

輸出：
```text
串接後的 Bus 名稱: SRAM_BUS
總位元寬度: 16 bits
初始讀取值: 0x1234

--- 內部結構映射 ---
SRAM_BUS[15:0] (0x1234)
  [15:12] 0x1  <- REG_A
  [11:8] 0x2   <- REG_B
  [7:0] 0x34   <- REG_C

寫入 0xABCD 後：
REG_A: 0xA
REG_B: 0xB
REG_C: 0xCD
情境二驗證通過！
```

## 情境三：硬體保留位元與常數防護機制

### 目標
直接寫入常數時必須引發 `TypeError`；透過 `concat()` 匯流排寫入時，常數位元保持不變，僅在寫入值與常數不符時發出 `UserWarning`。

```python
import warnings
import rpkbin.mapbv as mbv

field_data = mbv.var("DATA", 8, 0x12)
reserved = mbv.const(0x0, 4)
field_id = mbv.const(0xF, 4)

try:
    reserved.value = 0x1
    assert False, "常數不應允許直接寫入！"
except TypeError as exc:
    print(f"成功攔截非法寫入: {exc}")

packed_reg = mbv.concat("PACKED", field_data, reserved, field_id)
assert packed_reg.value == 0x120F

# 常數位元與原常數相同：不發出警告
packed_reg.value = 0xAA0F
assert field_data.value == 0xAA
assert packed_reg.value == 0xAA0F

# 常數位元被寫入不同值：發出警告並維持原常數
with warnings.catch_warnings(record=True) as recorded_warnings:
    warnings.simplefilter("always")
    packed_reg.value = 0x55FF

    assert packed_reg.value == 0x550F
    assert len(recorded_warnings) >= 1
    print(f"攔截到警告: {recorded_warnings[0].message}")

print("情境三驗證通過！")
```

輸出：
```text
成功攔截非法寫入: Cannot write 1 to a constant MapBV (width=4).
攔截到警告: Write of 0xF to constant bits of Constant (width=4, value=0x0) ignored.
情境三驗證通過！
```

## 情境四：組合邏輯運算式延遲求值

### 目標
建立組合邏輯運算式樹（`&`、`|`、`^`、`~`、`<<`、`>>`），讀取 `.value` 時依最新輸入變數動態求值。

```python
import rpkbin.mapbv as mbv

sig_a = mbv.var("A", 8, 0b1010_0000)
sig_b = mbv.var("B", 8, 0b0000_1111)

expr_or = sig_a | sig_b
expr_and = sig_a & 0xF0

print(f"expr_or 型別: {type(expr_or).__name__}")
print(f"expr_or 初始值:  {expr_or:bin}")
print(f"expr_and 初始值: {expr_and:hex}")

assert expr_or.value == 0b1010_1111
assert expr_and.value == 0xA0

sig_a.value = 0b1111_0000

print(f"sig_a 更新後 expr_or:  {expr_or:bin}")
print(f"sig_a 更新後 expr_and: {expr_and:hex}")
assert expr_or.value == 0b1111_1111
assert expr_and.value == 0xF0
print("情境四驗證通過！")
```

輸出：
```text
expr_or 型別: MapBVExpr
expr_or 初始值:  0b10101111
expr_and 初始值: 0xA0
sig_a 更新後 expr_or:  0b11111111
sig_a 更新後 expr_and: 0xF0
情境四驗證通過！
```

## 情境五：情境字典代入與符號求值 (`eval`)

### 目標
在不更動真實暫存器內容的前提下，透過 `eval(ctx)` 傳入測試情境字典來計算運算式結果。

```python
import rpkbin.mapbv as mbv

reg_x = mbv.var("X", 8, 10)
reg_y = mbv.var("Y", 8, 20)

pipeline = (reg_x << 2) | reg_y
assert pipeline.value == 60

sim_result_1 = pipeline.eval({"X": 1, "Y": 2})
sim_result_2 = pipeline.eval({"X": 0, "Y": 255})

assert sim_result_1 == 6
assert sim_result_2 == 255
assert reg_x.value == 10
assert reg_y.value == 20
print(f"模擬情境 1 結果: {sim_result_1}, 模擬情境 2 結果: {sim_result_2}")
print("情境五驗證通過！")
```

輸出：
```text
模擬情境 1 結果: 6, 模擬情境 2 結果: 255
情境五驗證通過！
```

## 情境六：常見誤區、型別陷阱與邊界檢查

### 1. 誤用 `==` 比對整數

```python
import rpkbin.mapbv as mbv

reg = mbv.var("R", 8, 0x55)

try:
    if reg == 0x55:
        pass
except TypeError as exc:
    print(f"成功攔截誤用: {exc}")

assert reg.value == 0x55
assert reg.value_eq(0x55)
```

輸出：
```text
成功攔截誤用: MapBV == int compares identity and is almost certainly a bug; use `bv.value == n` or `bv.value_eq(n)`
```

### 2. 切片語法限制

```python
import rpkbin.mapbv as mbv

reg = mbv.var("R", 8, 0x55)

try:
    bad_slice = reg[0:7]
except IndexError as exc:
    print(f"成功攔截反向切片: {exc}")

try:
    bad_step = reg[7:0:2]
except ValueError as exc:
    print(f"成功攔截步長切片: {exc}")
```

輸出：
```text
成功攔截反向切片: Slice [0:7] is reversed: write bv[high:low] with high >= low
成功攔截步長切片: MapBV slices do not support a step: use bv[high:low]
```

### 3. 禁止重疊寫入目標

使用 `concat()` 串接多個視圖時，若底層可寫入位元重疊，建構時會立刻引發 `ValueError`，避免寫入目標衝突。

```python
import rpkbin.mapbv as mbv

target = mbv.var("TARGET", 8)
try:
    bad_bus = mbv.concat("BAD_BUS", target[3:0], target[3:0])
except ValueError as exc:
    print(f"成功攔截重疊寫入衝突: {exc}")
```

輸出：
```text
成功攔截重疊寫入衝突: Linked parts contain overlapping writable bits
```
