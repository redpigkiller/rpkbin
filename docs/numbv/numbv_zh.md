# NumBV — Bit-True 定點運算核心

[![English](https://img.shields.io/badge/Language-English-blue.svg)](numbv.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](numbv_zh.md)

`NumBV`（Number BitVector）是一個面向 bit-true DSP pipeline 驗證的定點數模擬核心。
以純 NumPy（`int64`）為預設後端，選用 JAX 後端可獲得 XLA 加速（CPU/GPU/TPU），不依賴任何外部定點函式庫。

核心特色：**兩層 API**（operator 便利層 / 函式型 pipeline 層）的清楚分工、**五種硬體對齊 rounding mode**
（含 Xilinx DSP48 的 convergent rounding）、明確的 **format tracking**，以及適合固定 shape／format pipeline 的 **可選 JAX 後端**。

---

## 快速入門

```python
import rpkbin.numbv as nbv
```

安裝 `rpkbin` 時會預設安裝 NumPy；若要使用選用的 JAX 後端，請安裝 `pip install 'rpkbin[jax]'`。

### 1. 定義格式（Format）

`Format` 描述定點數的表示方式：位寬、小數位數、有號/無號、rounding 模式、overflow 政策。

```python
# Q16.12 — 16-bit signed, 12 fractional bits, 截斷, 飽和
fmt = nbv.Format(16, 12)

# 自訂政策
acc_fmt = nbv.Format(32, 22, rounding="round_half_even", overflow="saturate")
```

**Rounding 模式：**

| 模式 | Literal | 行為 | 硬體對應 |
|------|---------|------|---------|
| 截斷 | `"trunc"` | 向 −∞ 取整（floor） | Verilog `>>` 預設 |
| 四捨五入 | `"round"` | 最近整數，0.5 → +∞ | 簡易 DSP adder |
| **Convergent** | `"round_half_even"` | 最近整數，0.5 → 偶數 | **Xilinx DSP48 預設** |
| 向上取整 | `"ceil"` | 向 +∞ 取整 | 特定演算法 |
| 向零取整 | `"round_to_zero"` | 向零取整 | C 語言整數截斷 |

### 2. 建立數值

使用 factory 函式，不直接 `NumBV(...)` 建構。

```python
fmt = nbv.Format(16, 12)

a = nbv.scalar(1.5,          fmt=fmt)         # scalar（純量）
b = nbv.array([0.5, 1.0],    fmt=fmt)         # 1-d array
c = nbv.zeros(fmt=fmt, shape=256)             # 全零 array
d = nbv.from_bits(0xFF80, fmt=nbv.Format(16, 8, signed=True))  # 直接匯入 bits
```

> **注意（float64 精度）**：`scalar()`、`array()` 以 float64 為中間值。當 `frac > 48` 時，
> float64 的 53-bit mantissa 可能導致量化誤差。需要 bit-exact 初始化時請改用 `from_bits()`。

### 3. 讀取數值

```python
a.val    # → float 或 ndarray：數值域
a.raw    # → int64 ndarray：有號 raw integer
a.bits   # → int64 ndarray：無號 bit pattern
a.hex    # → '0x00C0'
a.bin    # → '0b0000000011000000'
a.fmt    # → Format(...)
a.report()  # 人性化 debug 摘要
```

### 4. Operator 層（便利使用）

所有算術 operator 一律把結果量化回**左運算元的 format**。與 NumPy / MATLAB 習慣一致，適合日常試算。

```python
fmt = nbv.Format(16, 12)
a = nbv.scalar(0.75, fmt=fmt)
b = nbv.scalar(0.50, fmt=fmt)

y = a + b    # → Q16.12
p = a * b    # → Q16.12（量化回左值，非 full-precision！）
z = -a       # → Q16.12
w = abs(a)   # → Q16.12
a += b       # in-place，format 保留
```

> 需要完整控制輸出格式時，請使用函式型 API。

### 5. 函式型 API（Pipeline 精確使用）

> **注意**：operator 一律量化回左操作數的 format，所以當 `a` 和 `b` format 不同時，`a + b` 可能和 `b + a` 不同。
> 混格式運算若需要可預期、可審查的 pipeline 行為，建議改用函式型 API。
>
> 同樣地，`a * b` 也是 convenience shortcut，不是 full-precision 的 DSP pipeline primitive。

正式 bit-true pipeline 驗證中，使用函式型 API 顯式指定輸出 format。

```python
# add / sub 必須提供 out_fmt
y = nbv.add(a, b, out_fmt=nbv.Format(32, 22))

# mul：預設回傳 full-precision，可選提供 out_fmt
p = nbv.mul(a, b)                          # → Q32.24（full precision）
y = nbv.mul(a, b, out_fmt=acc_fmt)         # → acc_fmt（full-prec → 量化）
```

### 6. 格式轉換

```python
# quantize：保留數值，套用目標 fmt 的 rounding + overflow
y = x.quantize(nbv.Format(8, 4, rounding="round_half_even"))

# reinterpret：保留 bits，改變解讀（不做 rounding）
u = x.reinterpret(signed=False)          # signed → unsigned 解讀
lo = x.reinterpret(width=8)              # 取低 8 bits
wide = x.reinterpret(width=32)           # 展開（source 有號 → sign-extend）

# clip：數值限幅，format 不變
y = x.clip(-1.0, 1.0)
```

### 7. Bit 操作

```python
# get_bits / set_bits（inclusive, 0-based from LSB）
high_byte = x.get_bits(15, 8)               # → int 或 ndarray
field     = x.get_bits(7, 0, fmt=fmt8)      # → NumBV

x.set_bits(7, 0, 0xAB)                      # in-place 寫入

# Bitwise 一行式：.bits + .with_bits
y = x.with_bits(x.bits & 0xFF00)            # AND mask，保留原 format
y = x.with_bits(x.bits | other.bits)        # OR 合併
```

---

## API 參考

### Format

```python
Format(width, frac, signed=True, rounding="trunc", overflow="saturate")
```

| 屬性 | 說明 |
|------|------|
| `.width` | 總位元寬度 |
| `.frac` | 小數位數 |
| `.signed` | 是否有號（Two's complement） |
| `.int_bits` | 整數位數（可為負） |
| `.scale` | `2 ** frac` |
| `.min_val` / `.max_val` | 數值域範圍 |
| `.precision` | `1 / scale` — 最小可表示精度 |
| `.replace(**changes)` | 回傳修改後的新 Format |

### Factory 函式

| 函式 | 說明 |
|------|------|
| `nbv.scalar(value, *, fmt)` | 建立 scalar NumBV；拒絕 array 輸入 |
| `nbv.array(data, *, fmt)` | 建立 array NumBV；拒絕 scalar 輸入 |
| `nbv.zeros(*, fmt, shape=None)` | 全零 NumBV |
| `nbv.ones(*, fmt, shape=None)` | 量化 1.0 NumBV |
| `nbv.full(fill, *, fmt, shape=None)` | 填滿 NumBV |
| `nbv.from_bits(bits, *, fmt)` | 直接匯入 bit pattern（不做量化） |

### 序列化方法

| 方法 | 說明 |
|------|------|
| `a.to_dict()` | 輸出為 Python dict |
| `a.to_json()` | 輸出為 JSON 字串 |
| `NumBV.from_dict(d)` | 從 dict 還原 |
| `NumBV.from_json(s)` | 從 JSON 還原 |

### Backend 控制

| 函式 | 說明 |
|------|------|
| `nbv.set_backend("numpy")` | 切換到 NumPy（預設） |
| `nbv.set_backend("jax")` | 切換到 JAX（XLA 加速） |
| `nbv.get_backend()` | 取得目前 backend 名稱 |

### Operator（結果量化回左值 format）

| Operator | 說明 |
|----------|------|
| `a + b`, `a += b` | 加法 |
| `a - b`, `a -= b` | 減法 |
| `a * b`, `a *= b` | 乘法 |
| `-a`, `abs(a)` | 取負 / 絕對值 |
| `==`, `!=`, `<`, `<=`, `>`, `>=` | NumBV 間先對齊 frac；Python 數值 literal 依實際數值比較 |

> 有號與無號 NumBV 混合運算時，runtime 會 raise `TypeError`。

### 函式型 API

| 函式 | 說明 |
|------|------|
| `nbv.add(a, b, *, out_fmt)` | 加法 → `out_fmt`（必填） |
| `nbv.sub(a, b, *, out_fmt)` | 減法 → `out_fmt`（必填） |
| `nbv.mul(a, b, *, out_fmt=None)` | Full-precision 乘法，可選量化 |
| `nbv.neg(a, *, out_fmt=None)` | 取負，可選量化 |

### Reduction / DSP Helper

| 函式 | 說明 |
|------|------|
| `nbv.sum(x, *, acc_fmt)` | Sequential 累加，每步量化到 acc_fmt |
| `nbv.dot(a, b, *, acc_fmt, out_fmt=None)` | 內積，顯式 accumulator format |
| `nbv.mac(acc, a, b, *, acc_fmt)` | 乘法累加：`acc + mul(a, b)` |

> **效能注意（純 NumPy 下的 `sum` / `dot` 迴圈）**：為了確保 bit-true 精確度，在每一次累加（Accumulation）當下都必須即時套用 rounding 與 overflow policy。因此 `sum` 與 `dot` 內部使用 **pure Python loop** 逐元素運算，無法直接調用底層的 C 向量運算，在處理大陣列時速度較慢。這是精確模擬硬體行為的必要 Tradeoff。若需要高效能，請搭配 **JAX backend** 使用 `@jax.jit` 加速。

> **迭代順序**：`sum()` 與 `dot()` 對 array 輸入採用 flattened C-order（也就是 `reshape(-1)` 的順序）。這樣多維輸入的行為會固定且可預期，不另外引入 axis-specific reduction 規則。

### Format 推導 Helper

```python
safe = nbv.infer_add_format(fmt_a, fmt_b)   # 無損加法 format
safe = nbv.infer_mul_format(fmt_a, fmt_b)   # full-precision 乘法 format
```

純工具函式，計算能承載結果而不丟失精度的最小 `Format`。rounding / overflow 需使用者自行設定。

---

## Backend 選擇（NumPy / JAX）

backend 是 process-global；必須在建立第一個 NumBV **之前**選定，之後不可切換成不同 backend。

```python
import rpkbin.numbv as nbv

nbv.set_backend("numpy")  # 預設，永遠可用
nbv.set_backend("jax")    # XLA 加速（需先 pip install 'rpkbin[jax]'）

print(nbv.get_backend())  # 'numpy' 或 'jax'
```

**JAX 後端特性：**
- `jax_enable_x64` 自動啟用（確保 int64/float64 正確運作）
- `NumBV` 自動註冊為 JAX PyTree，可用於固定 shape／format 的 `@jax.jit` pipeline
- 所有 bit-true 操作結果在 numpy 與 JAX 間完全一致（bit-identical）

NumBV 的 raw leaf 是整數，因此不支援以 `jax.grad` 對 fixed-point pipeline 微分。`sum()`／`dot()` 的 Python loop 也可能增加 JIT 編譯成本。

```python
import jax
nbv.set_backend("jax")

@jax.jit
def run_fir(x, h):
    return nbv.dot(x, h, acc_fmt=acc_fmt)

y = run_fir(x, h)  # XLA 編譯加速，第一次呼叫後快取
```

> Windows 上 JAX CPU 後端完全支援。GPU 支援需對應 CUDA 驅動與 JAX GPU 版本。

---

## 序列化（Serialization）

```python
# 儲存
d = a.to_dict()   # → Python dict（含 raw 與 fmt）
s = a.to_json()   # → JSON 字串

# 還原
from rpkbin.numbv import NumBV
b = NumBV.from_dict(d)   # 從 dict
b = NumBV.from_json(s)   # 從 JSON

assert b.fmt == a.fmt        # format 完整保留
assert all(b.bits == a.bits) # bits 完全一致
```

序列化與 backend 無關：dict/JSON 內部一律使用 Python int/float，
可跨平台存取或寫入檔案。

---

## Bit-True FIR Pipeline 範例

```python
import rpkbin.numbv as nbv

x_fmt   = nbv.Format(16, 12)
h_fmt   = nbv.Format(12, 10, rounding="round_half_even")
acc_fmt = nbv.Format(32, 22, rounding="round_half_even")
out_fmt = nbv.Format(16, 12, rounding="round_half_even", overflow="saturate")

x = nbv.array(input_samples, fmt=x_fmt)
h = nbv.array(fir_coeffs,    fmt=h_fmt)

# Full-precision element-wise 乘法 → 累加 → 輸出
y = nbv.dot(x, h, acc_fmt=acc_fmt, out_fmt=out_fmt)
```

此模型精確對應 Xilinx DSP48 FIR 的行為：
1. 每個乘法保留 `width_x + width_h` 的完整精度。
2. 每個部分積在累加前以 convergent rounding 量化。
3. 最終結果飽和到輸出位寬。

---

## 常見陷阱

### `a + b ≠ b + a`（不同 format 時）

```python
a = nbv.scalar(1.0, fmt=nbv.Format(16, 12))
b = nbv.scalar(0.5, fmt=nbv.Format(8,  4))

y1 = a + b   # → Q16.12（精度保留）
y2 = b + a   # → Q8.4 （可能截斷或飽和）
```

輸出格式不確定時，請使用 `nbv.add(a, b, out_fmt=...)` 顯式指定。

### `*` 會截斷（Operator 層）

```python
p = a * b           # Q16.12 — 量化回左值，精度可能流失
p = nbv.mul(a, b)   # Q32.24 — full precision
```

正式 pipeline 驗證中，乘法一律用 `nbv.mul()`。

### `reinterpret()` 不是 `quantize()`

```python
y = x.quantize(fmt2)      # 數值保留，bits 隨之改變（套用 rounding）
z = x.reinterpret(frac=8) # bits 保留，數值解讀改變（無 rounding）
```

### 不可使用 NumPy ufunc

```python
# ✗ 錯誤：np.add(a, b) 會被攔截並 raise TypeError
import numpy as np
np.add(a, b)  # TypeError：NumBV 不支援 numpy ufuncs

# ✓ 正確：使用 nbv 函式 API 或 operator
nbv.add(a, b, out_fmt=out_fmt)
a + b
```

Bit-true 精度必須透過明確的 format 追蹤維護，NumPy ufuncs 會繞過此機制。

### Python literal 的算術與比較語意

```python
y = a + 0.1     # 0.1 先量化到 a.fmt
same = a == 0.1 # 直接依實際數值比較，不先 saturate 或 wrap
```

算術 operator 會將 Python literal 量化到左 operand 的 format；comparison literal 則依數值比較。

### `clip()` 會量化 bounds

`clip(lo, hi)` 會依目前 format 的 rounding mode 量化 bounds；超出可表示範圍的 bounds 會 clamp 到 `min_raw`／`max_raw`，不套用 wrap。

### Backend 切換時機

`set_backend()` 必須在建立任何 NumBV 物件**之前**呼叫。建立第一個物件後，要求不同 backend 會 raise `RuntimeError`；重複設定目前 backend 則是安全的 no-op。
最安全的做法是在程式進入點呼叫一次，之後不再更改。
---

## 如何撰寫自己的 Simulation Function

如果你要在自己的 simulation platform 上用 NumBV 建立新的運算函式，例如 FFT butterfly、
CIC、CORDIC 或其他自訂 DSP stage，建議遵循下面的寫法：

1. 盡量在 raw integer domain 中思考每一級的資料流。
2. 每一次 format 轉換都明確寫出來，不要把量化藏在不明確的 helper 裡。
3. 明確決定每一級在哪裡做 rounding、overflow、register truncation。
4. 只有在 pipeline shape 穩定、會重複跑很多次時，再考慮用 JAX `@jax.jit` 加速。

### 建議骨架

```python
import rpkbin.numbv as nbv

def butterfly(a: nbv.NumBV, b: nbv.NumBV, *, stage_fmt: nbv.Format, out_fmt: nbv.Format):
    s = nbv.add(a, b, out_fmt=stage_fmt)
    d = nbv.sub(a, b, out_fmt=stage_fmt)
    return s.quantize(out_fmt), d.quantize(out_fmt)
```

### 用 NumPy 開發時

- 先把每一級 stage format 寫清楚
- 先驗證 `.bits` / `.raw` 是否符合你的 hardware model
- 不要只看 `.val`，因為 bit-true 的關鍵通常在 rounding 與 overflow 位置

### 用 JAX 加速時

```python
import jax
import rpkbin.numbv as nbv

nbv.set_backend("jax")

@jax.jit
def run_stage(x0, x1, stage_fmt, out_fmt):
    s = nbv.add(x0, x1, out_fmt=stage_fmt)
    d = nbv.sub(x0, x1, out_fmt=stage_fmt)
    return s.quantize(out_fmt), d.quantize(out_fmt)
```

### 實務建議

- 可重用的 simulation block 優先用 `nbv.add`、`nbv.sub`、`nbv.mul`、`nbv.dot`、`quantize()`
- `+`、`*` 比較適合做 convenience 寫法，不建議當成 pipeline stage 的正式實作
- function signature 最好把 stage format、accumulator format、output format 分開
- 如果某一步要對應硬體 bit-true，請在文件中直接寫出 truncation / convergent rounding / saturation 發生的位置
- FFT 這類多級運算，請把 reduction 順序與每一級的量化點明確寫出來，讓 bit growth path 可以被 review
---

## TODO / 可能的後續補強

這些目前都不算 NumBV 的致命缺口，但之後如果要往更完整的 simulation platform 擴充，可以再考慮。

- 公開的 bit-true shift / rescale helper，例如 `shift_right`、`shift_left`、`rescale`
  這類 API 看起來很方便，但也最容易把 rounding、saturation、sign-extension、register truncation 藏起來。
  如果未來要加，必須讓 format transition 與量化位置保持完全明確。
- axis-aware reduction，例如 `sum(axis=...)`
  現在的 flatten-then-accumulate 規則已經足夠 deterministic。若未來要支援 axis 版本，重點會是避免 reduction order 變得不明確。
- 更多 DSP-oriented helper block，例如 butterfly / FIR stage / accumulator tree
  這些目前都可以用既有 function-level API 自己組出來，所以不算核心缺功能；但若 simulation platform 變大，適度提供幾個標準 pattern 會減少重複 boilerplate。
