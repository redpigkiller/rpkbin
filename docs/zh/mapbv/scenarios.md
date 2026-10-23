# MapBV 實戰使用情境

[English](../../en/mapbv/scenarios.md) · [文件導覽](../README.md) · [API 參考](reference.md)

本文件透過 6 個實務場景，示範如何使用 MapBV 進行硬體暫存器定義、位元切片、Bus 串接以及邏輯運算。所有範例皆為完整且可直接執行的 Python 程式碼。

---

## 目錄

1. [情境一：暫存器欄位宣告與雙向即時連動](#情境一暫存器欄位宣告與雙向即時連動)
2. [情境二：硬體 Bus 串接 (`concat`) 與位元分派](#情境二硬體-bus-串接-concat-與位元分派)
3. [情境三：硬體保留位元與常數防護機制](#情境三硬體保留位元與常數防護機制)
4. [情境四：組合邏輯運算式延遲求值 (Deferred Evaluation)](#情境四組合邏輯運算式延遲求值-deferred-evaluation)
5. [情境五：測試環境字典代入與符號求值 (`eval`)](#情境五測試環境字典代入與符號求值-eval)
6. [情境六：常見誤區、型別陷阱與邊界檢查](#情境六常見誤區型別陷阱與邊界檢查)

---

## 情境一：暫存器欄位宣告與雙向即時連動

### 使用者想完成什麼
在驗證測試平台中塑模一個 32 位元的控制暫存器 `CTRL_REG`，包含多個不同功能欄位：
- `[31]`: 啟用位元 (`enable`, 1 bit)
- `[23:16]`: 突發傳輸長度 (`burst_len`, 8 bits)
- `[7:0]`: 優先權等級 (`priority`, 8 bits)

任何對特定欄位的寫入必須立即反映於整個暫存器；對整個暫存器的讀取或重置也必須同步反映於各欄位。

### 完整可執行範例 (`scenario_slice.py`)

```python
import rpkbin.mapbv as mbv

# 1. 建立 32 位元控制暫存器，預設值為 0
ctrl_reg = mbv.var("CTRL_REG", 32, 0x0000_0000)

# 2. 切出各具名欄位視圖（閉區間切片，語意等同 Verilog 的 [high:low]）
enable    = ctrl_reg[31]
burst_len = ctrl_reg[23:16]
priority  = ctrl_reg[7:0]

# 3. 欄位寫入：單獨配置各欄位
enable.value = 1
burst_len.value = 0x10
priority.value = 0x05

# 驗證原暫存器是否同步更新
print(f"CTRL_REG 總值: {ctrl_reg:hex}")
assert ctrl_reg.value == 0x8010_0005

# 4. 整體暫存器寫入：模擬匯流排寫入整個暫存器
ctrl_reg.value = 0x00FF_0000

# 驗證各欄位視圖是否即時反映新值
print(f"enable 欄位:    {enable.value}")      # 0
print(f"burst_len 欄位: {burst_len:hex}")    # 0xFF
print(f"priority 欄位:  {priority:hex}")     # 0x00
assert enable.value == 0
assert burst_len.value == 0xFF
assert priority.value == 0
print("情境一驗證通過！")
```

### 結果解釋
- 切片物件繼承了原變數的位址映射。切片與原物件之間不存在快取過期問題，所有讀寫皆即時穿透。

---

## 情境二：硬體 Bus 串接 (`concat`) 與位元分派

### 使用者想完成什麼
在暫存器塑模或測試平台中，經常需要將分散的多個暫存器或信號線拼裝成一條較寬的 Bus 視圖（例如模擬 SRAM 寫入 Bus）。
當向這條 Bus 寫入時，位元必須自動依照拼裝順序拆解並分派寫入回各個底層暫存器。

### 完整可執行範例 (`scenario_concat.py`)

```python
import rpkbin.mapbv as mbv

# 準備來源暫存器
reg_a = mbv.var("REG_A", 4, 0x1)  # 4-bit
reg_b = mbv.var("REG_B", 4, 0x2)  # 4-bit
reg_c = mbv.var("REG_C", 8, 0x34) # 8-bit

# 依據 MSB → LSB 順序拼裝成 16-bit 的 SRAM 寫入匯流排
# 結構為：[15:12]=REG_A, [11:8]=REG_B, [7:0]=REG_C
sram_bus = mbv.concat("SRAM_BUS", reg_a, reg_b, reg_c)

print(f"串接後的 Bus 名稱: {sram_bus.name}")
print(f"總位元寬度: {sram_bus.width} bits")
print(f"初始讀取值: {sram_bus:hex}")  # 0x1234
assert sram_bus.value == 0x1234

# 列印其內部結構映射圖
print("\n--- 內部結構映射 ---")
print(str(sram_bus))

# 關鍵操作：向 sram_bus 寫入新值，觀察底層暫存器是否自動分派
sram_bus.value = 0xABCD

print(f"\n寫入 0xABCD 後：")
print(f"REG_A: {reg_a:hex}")  # 應分配高 4 位元: 0xA
print(f"REG_B: {reg_b:hex}")  # 應分配中 4 位元: 0xB
print(f"REG_C: {reg_c:hex}")  # 應分配低 8 位元: 0xCD

assert reg_a.value == 0xA
assert reg_b.value == 0xB
assert reg_c.value == 0xCD
print("情境二驗證通過！")
```

### 結果解釋
- `concat` 產生的物件為動態雙向連結視圖。
- 寫入時，MapBV 會根據每個 part 的位元寬度由右至左（LSB 到 MSB）自動遮罩並寫入對應的底層暫存器。
- 列印 `str(sram_bus)` 可直觀看到每個位元範圍對應的來源名稱與目前十六進位值。

---

## 情境三：硬體保留位元與常數防護機制

### 使用者想完成什麼
在暫存器佈局中，往往存在固定為 0 的保留位元（Reserved Bits）或唯讀的硬體識別碼（ID/Version）。
我們希望：
1. 若程式嘗試直接向常數賦值，應立即拋出錯誤。
2. 當常數作為串接 Bus 的一部分時，寫入 Bus 時這些常數位元應維持不變（忽略寫入）；若寫入值與常數不符，應發出警告（`UserWarning`）。

### 完整可執行範例 (`scenario_const.py`)

```python
import warnings
import rpkbin.mapbv as mbv

# 定義一個包含常數保留欄位的暫存器
field_data = mbv.var("DATA", 8, 0x12)
reserved   = mbv.const(0x0, 4)        # 4 位元固定為 0 的保留欄位
field_id   = mbv.const(0xF, 4)        # 4 位元固定為 0xF 的晶片 ID

# 1. 直接寫入常數測試：必須拋出 TypeError
try:
    reserved.value = 0x1
    assert False, "常數不應允許直接寫入！"
except TypeError as exc:
    print(f"成功攔截非法寫入: {exc}")

# 2. 將常數與變數拼成一條 16 位元暫存器: [15:8]=DATA, [7:4]=RESERVED (0), [3:0]=ID (F)
packed_reg = mbv.concat("PACKED", field_data, reserved, field_id)
print(f"初始打包值: {packed_reg:hex}")  # 0x120F
assert packed_reg.value == 0x120F

# 3. 寫入 Bus，但常數位元與原常數相同（不觸發警告）
packed_reg.value = 0xAA0F
assert field_data.value == 0xAA
assert packed_reg.value == 0xAA0F

# 4. 寫入 Bus，且常數位元被寫入不同值（發出警告但常數值保持不變）
with warnings.catch_warnings(record=True) as recorded_warnings:
    warnings.simplefilter("always")
    packed_reg.value = 0x55FF  # 將中間 reserved 寫入 F，但它應維持 0

    print(f"寫入 0x55FF 後實際值: {packed_reg:hex}")  # 依然為 0x550F
    assert packed_reg.value == 0x550F
    assert len(recorded_warnings) >= 1
    print(f"攔截到警告: {recorded_warnings[0].message}")

print("情境三驗證通過！")
```

### 結果解釋
- 常數的獨立寫入會被完全封鎖（`TypeError`）。
- 匯流排模式下的寫入保護機制精確模擬了硬體真實行為：對唯讀/保留位元的寫入被安全忽略，並發出警告提醒測試腳本檢查位元映射。

---

## 情境四：組合邏輯運算式延遲求值 (Deferred Evaluation)

### 使用者想完成什麼
使用 MapBV 物件建立組合邏輯閘運算式（`&`、`|`、`^`、`~`、`<<`、`>>`）。
運算結果應保留為**運算式樹（Expression Node）**，而不是立即算死成一個靜態整數。當輸入變數改變時，運算式的輸出值必須自動反映最新結果。

### 完整可執行範例 (`scenario_expr.py`)

```python
import rpkbin.mapbv as mbv

sig_a = mbv.var("A", 8, 0b1010_0000)
sig_b = mbv.var("B", 8, 0b0000_1111)

# 建立邏輯閘運算式樹
expr_or  = sig_a | sig_b
expr_and = sig_a & 0xF0  # 支援與整數常數直接運算
expr_xor = sig_a ^ sig_b
expr_not = ~sig_a

print(f"expr_or 型別: {type(expr_or).__name__}")  # MapBVExpr
print(f"expr_or 初始值:  {expr_or:bin}")           # 0b10101111
print(f"expr_and 初始值: {expr_and:hex}")          # 0xA0

assert expr_or.value == 0b1010_1111
assert expr_and.value == 0xA0

# 動態更新來源變數 sig_a
sig_a.value = 0b1111_0000

# 運算式會自動動態求值，反映來源更新
print(f"\nsig_a 更新後 expr_or:  {expr_or:bin}")   # 0b11111111
print(f"sig_a 更新後 expr_and: {expr_and:hex}")   # 0xF0
assert expr_or.value == 0b1111_1111
assert expr_and.value == 0xF0
print("情境四驗證通過！")
```

### 結果解釋
- 運算子產生的 `MapBVExpr` 物件繼承自 `_BVBase`，其 `.value` 屬性是在讀取時動態 resolve 各操作數並執行位元遮罩，因此永遠呈現最新狀態。

---

## 情境五：測試環境字典代入與符號求值 (`eval`)

### 使用者想完成什麼
建立包含多個變數的邏輯運算式後，在不改變真實暫存器數值的前提下，使用情境字典（Context Dict）進行模擬或假設分析（"What-if" analysis）。

### 完整可執行範例 (`scenario_eval.py`)

```python
import rpkbin.mapbv as mbv

reg_x = mbv.var("X", 8, 10)
reg_y = mbv.var("Y", 8, 20)

# 建立運算式：(X << 2) | Y
pipeline = (reg_x << 2) | reg_y

print(f"當前暫存器實際值計算: {pipeline.value}")  # (10 << 2) | 20 = 40 | 20 = 60
assert pipeline.value == 60

# 使用 eval() 帶入自訂數值進行情境模擬
sim_result_1 = pipeline.eval({"X": 1, "Y": 2})   # (1 << 2) | 2 = 6
sim_result_2 = pipeline.eval({"X": 0, "Y": 255}) # (0 << 2) | 255 = 255

print(f"模擬情境 1 結果: {sim_result_1}")
print(f"模擬情境 2 結果: {sim_result_2}")

assert sim_result_1 == 6
assert sim_result_2 == 255

# 驗證真實暫存器值未受影響
assert reg_x.value == 10
assert reg_y.value == 20
print("情境五驗證通過！")
```

### 結果解釋
- `eval(ctx)` 支援傳入字典，字典鍵為變數名稱字串。若運算式中某變數未在字典中提供，則自動回退使用該變數目前的真實 `.value`。

---

## 情境六：常見誤區、型別陷阱與邊界檢查

### 1. 誤用 `==` 比對整數
```python
import rpkbin.mapbv as mbv

reg = mbv.var("R", 8, 0x55)

# 錯誤寫法：直接比對整數會引發 TypeError
try:
    if reg == 0x55:
        pass
except TypeError as exc:
    print(f"成功攔截誤用: {exc}")

# 正確寫法：
assert reg.value == 0x55
assert reg.value_eq(0x55)
```

### 2. 切片語法限制
```python
# 注意：本程式片段依賴上方已宣告之 reg 物件（reg = mbv.var("R", 8, 0x55)）
# MapBV 切片為閉區間，high 必須大於等於 low
try:
    bad_slice = reg[0:7]  # 反向切片非法
except IndexError as exc:
    print(f"成功攔截反向切片: {exc}")

# 不支援步長 (step)
try:
    bad_step = reg[7:0:2]
except ValueError as exc:
    print(f"成功攔截步長切片: {exc}")
```

### 3. 禁止重疊寫入目標
當使用 `concat` 串接多個視圖時，為避免寫入目標模糊與驅動衝突（Multiple Drivers），若串接中包含同一個底層位元欄位的重複寫入目標，MapBV 會在建構時立即拋出錯誤進行防護：
```python
# 注意：若單獨執行本程式碼，需預先 import rpkbin.mapbv as mbv
target = mbv.var("TARGET", 8)
# 嘗試將同一個 target 的 [3:0] 重複串接入同一個 Bus
try:
    bad_bus = mbv.concat("BAD_BUS", target[3:0], target[3:0])
except ValueError as exc:
    print(f"成功攔截重疊寫入衝突: {exc}")
```
