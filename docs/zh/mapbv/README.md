# MapBV 快速入門：第一次建立、讀取與修改位元欄位

[English](../../en/mapbv/README.md) · [文件導覽](../README.md) · [情境教學](scenarios.md) · [API 參考](reference.md)

MapBV (MapBitVector) 是一套針對數位 IC 設計、硬體暫存器映射（Register Mapping）與驗證測試平台開發的輕量級 BitVector 工具庫。

它核心解決的問題是：**如何在軟體中直覺、高效地操作暫存器的特定位元欄位，並保證欄位切片與原暫存器、以及暫存器與 Bus 串接之間的雙向即時同步**。

---

## 5 分鐘快速上手

### 1. 建立變數與常數

使用工廠函式 `var()` 建立具名的暫存器變數，使用 `const()` 建立唯讀常數：

```python
import rpkbin.mapbv as mbv

# 建立名為 REG0 的 16 位元暫存器變數，初始值為 0x1234
reg = mbv.var("REG0", 16, 0x1234)

# 建立 4 位元的唯讀常數 0xF
mask = mbv.const(0xF, 4)

print(f"變數: {reg.name}, 寬度: {reg.width} bits, 數值: 0x{reg.value:04X}")
print(f"常數: {mask.name}, 寬度: {mask.width} bits, 數值: 0x{mask.value:X}")
```

### 2. 格式化輸出 (Hex 與 Binary)

MapBV 內建十六進位與二進位轉換方法，亦原生支援 Python f-string 格式化格式代碼：

```python
# 方法一：呼叫方法
print(reg.to_hex())  # 輸出: 0x1234
print(reg.to_bin())  # 輸出: 0b0001001000110100

# 方法二：f-string 格式化代碼
print(f"{reg:hex}")  # 輸出: 0x1234
print(f"{reg:bin}")  # 輸出: 0b0001001000110100
```

### 3. 切片讀取與單一位元操作

MapBV 採用硬體描述語言（Verilog / SystemVerilog）習慣的**閉區間**語法 `[high:low]`（同時包含 high 與 low 位元，且 $high \ge low$）：

```python
# 讀取高位元組 [15:8]
high_byte = reg[15:8]
print(f"high_byte 寬度: {len(high_byte)} bits, 數值: {high_byte:hex}")  # 8 bits, 0x12

# 讀取單一位元 [3]
bit3 = reg[3]
print(f"bit3 寬度: {len(bit3)} bits, 數值: {bit3.value}")  # 1 bit, 0 (因 0x1234 的 bit 3 為 0)
```

### 4. 共享視圖與雙向即時連動

由暫存器切出的子視圖是**動態活連結（Live View）**：
- 修改切片會立刻寫回原暫存器。
- 修改原暫存器，切片讀取的值也會同步改變。

```python
# 直接賦值修改切片視圖
reg[15:8] = 0xAB

# 原暫存器數值自動更新
print(f"更新後原暫存器: {reg:hex}")  # 輸出: 0xAB34
print(f"切片視圖讀取值: {high_byte:hex}")  # 輸出: 0xAB

# 也可以直接對切片物件的 .value 賦值
high_byte.value = 0x55
print(f"再次更新後原暫存器: {reg:hex}")  # 輸出: 0x5534
```

---

## 關鍵注意事項與常見誤區

> [!WARNING]
> ### 1. 數值比較 vs. 物件同一性比較 (`==`)
> 在 Python 中，`reg == other` 會比較**物件身分識別（Identity，即 `self is other`）**，而非數值相等！
> 若誤寫 `reg == 0x5534`，MapBV 會主動拋出 `TypeError` 阻止潛在的邏輯 Bug。
>
> **正確的數值比對寫法：**
> ```python
> # 寫法 A：比對 .value 屬性
> assert reg.value == 0x5534
>
> # 寫法 B：使用 value_eq() 方法
> assert reg.value_eq(0x5534)
> ```

> [!CAUTION]
> ### 2. 位元寬度與負數檢查
> MapBV 模擬的是無號數位硬體信號。
> - 賦值負數會拋出 `ValueError`。
> - 賦值超出位元寬度上限的數值會拋出 `ValueError`。
> ```python
> reg[3:0] = 0x10  # 拋出 ValueError: 0x10 超出 4 位元最大值 0xF
> reg[3:0] = -1    # 拋出 ValueError: 不支援負數
> ```

---

## 下一步

- **[實戰使用情境](scenarios.md)**：深入學習多欄位組合、硬體 Bus 串接 (`concat`)、唯讀常數位元防護、組合邏輯運算式延遲求值與符號代入。
- **[API 參考手冊](reference.md)**：查閱所有方法、魔術函式與例外型別的完整規格。
