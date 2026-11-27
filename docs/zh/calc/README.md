# `rpk-calc` 工程與定點數計算機

[English](../../en/calc/README.md) · [文件導覽](../README.md) · [架構與資料邊界](architecture.md)

`rpk-calc` 是一款工程與定點數（FXP）計算機，整合獨立的簡易模式 GUI、以 IPython 為核心的 Python Studio 工作區、無介面 `--eval` CLI，以及 `rpkbin.app.calc` Python API（`fxp`、`fxpi`、`launch`、`main`）。

> **相容性。** 需要 Python 3.11 或更新版本。安裝 `pip install -e ".[calc]"` 可啟用完整 Qt／IPython GUI；若只需要無介面的 `fxp`／`fxpi`，安裝 `pip install -e ".[fixed]"` 即可。

---

## CLI 參考

### 語法

```text
rpk-calc [-h] [-V] [-c CONFIG] [-e EVAL_EXPR | --simple | --python | --mode {simple,python}]
python -m rpkbin.app.calc [-h] [-V] [-c CONFIG] [-e EVAL_EXPR | --simple | --python | --mode {simple,python}]
```

### 說明

- `rpk-calc` 依目前設定檔中的 `launch_mode` 啟動桌面視窗（預設 `"simple"`；若設為 `"last"` 則回到 `last_mode`）。
- `rpk-calc --simple` 與 `rpk-calc --mode simple` 以簡易獨立定點數模式開啟 GUI。
- `rpk-calc --python`、`rpk-calc --studio` 與 `rpk-calc --mode python` 以 Python Studio 工作區模式開啟 GUI。切回簡易模式時，已啟動的 Studio session 會繼續保留；兩個模式各自管理變數。
- `rpk-calc -e EVAL_EXPR`／`rpk-calc --eval EVAL_EXPR` 建立無介面 `WorkspaceSession`（`seed_demo_vars=False`），執行 `EVAL_EXPR` 並將結果印到 stdout 後結束，不啟動 Qt 或 IPython kernel。傳入空字串 `""` 同樣以無介面模式執行並以結束碼 `0` 結束，不輸出內容。
- `-c CONFIG`／`--config CONFIG` 指定設定 JSON 檔路徑（預設 `~/.rpkbin/calc_settings.json`）及其同資料夾的 `startup.py`，同時適用於 GUI 啟動與 `--eval`。
- `-V`／`--version` 印出 `rpk-calc <version>` 後結束。

### 範例

印出版本號並在終端機無介面求值：

```bash
rpk-calc --version
rpk-calc --eval "1+2"
rpk-calc --eval "fxp('0.75', 'Q4.4')"
```

輸出：

```text
rpk-calc 0.3.0
ans = 3
ans = 0.75  [Q4.4 | Hex: 0x0C | 2's: 12]
```

搭配獨立設定檔求值，或直接開啟 Python Studio：

```bash
rpk-calc --config path/to/custom_settings.json --eval "fxp('0.5', 'Q4.0')"
rpk-calc --studio
```

### 選項

| 旗標 | 環境變數 | 預設值 | 說明 |
|---|---|---|---|
| `-h`、`--help` | （無） | （無） | 顯示 CLI 說明訊息後結束。 |
| `-V`、`--version` | （無） | （無） | 印出 `rpkbin.__version__` 對應的 `rpk-calc <version>` 後結束。 |
| `-c CONFIG`、`--config CONFIG` | （無） | `~/.rpkbin/calc_settings.json` | GUI 與 `--eval` 使用的設定 JSON 檔路徑（與同目錄 `startup.py`）。 |
| `-e EVAL_EXPR`、`--eval EVAL_EXPR` | （無） | `None` | 無介面執行 Python／FXP 運算式並印出結果；與模式旗標互斥。 |
| `--simple` | （無） | `launch_mode`（`simple`） | 以簡易獨立定點數模式啟動 GUI；與 `--eval`、`--python`/`--studio`、`--mode` 互斥。 |
| `--python`、`--studio` | （無） | `launch_mode`（`simple`） | 以 Python Studio 工作區模式啟動 GUI；與 `--eval`、`--simple`、`--mode` 互斥。 |
| `--mode {simple,python}` | （無） | `launch_mode`（`simple`） | 明確指定 GUI 啟動模式（`simple` 或 `python`）；與 `--eval`、`--simple`、`--python`/`--studio` 互斥。 |

`rpk-calc` CLI 旗標不讀取環境變數；持久化偏好設定皆保存於 JSON 設定檔。

### 結束碼與注意事項

- `0`：GUI 正常關閉，或 `--eval` 成功執行完畢。
- `1`：`--eval` 發生語法或執行期錯誤（錯誤訊息或 traceback 會寫入 `stderr`）。
- `2`：CLI 參數無效或衝突（由 `argparse` 回報），或缺少選配套件（`pip install "rpkbin[calc]"`）。
- `--eval` 路徑會檢查 `apytypes` 與 `matplotlib`；GUI 啟動另會檢查 `PySide6`、`qtconsole`、`ipykernel` 與 `jupyter_client`。
- 若 `--eval` 執行時預設設定目錄無法寫入，啟動初始化會略過寫檔，運算式求值仍可繼續。

---

## Python API（`rpkbin.app.calc`）

### 語法

```python
from rpkbin.app.calc import fxp, fxpi, launch, main

val = fxp(x, fmt=None, *, bits=None, wl=None, fwl=None, int_bits=None, frac_bits=None, signed=True, quant=None, overflow=None)
raw = fxpi(x, fmt=None, *, bits=None, wl=None, fwl=None, int_bits=None, frac_bits=None, signed=True)
code = launch(argv=None, *, mode=None, config_path=None)
code = main(argv=None)
```

### 說明

- `val = fxp(x, fmt=None, ...)` 將純量（`int`、`float`、`Decimal`、`Fraction`、`APyFixed` 或十進位數字字串）或陣列／序列（`list`、`tuple`、`np.ndarray`、`APyFixedArray`）量化為 `RpkFixed` 或 `RpkFixedArray`，並保留轉換來源追蹤資訊（`target_real`/`target_values`、`is_saturated`/`saturated_mask`）。`quant` 或 `overflow` 為 `None` 時，採用目前 `CalcConfig` 的 `default_quant`（預設 `"RND"`）與 `default_overflow`（預設 `"SAT"`）。
- `raw = fxpi(x, fmt=None, ...)` 將整數、`APyFixed` 或陣列／序列的低 `wl` 位元（`raw & ((1 << wl) - 1)`）依指定硬體格式重解讀，回傳不帶轉換來源資訊（`target_real=None`）的 `RpkFixed` 或 `RpkFixedArray`。
- `code = launch(argv=None, *, mode=None, config_path=None)` 以指定的 `mode`（`None`、`"simple"` 或 `"python"`）與 `config_path`（未指定時使用 `~/.rpkbin/calc_settings.json`）開啟 Qt 桌面視窗（`CalculatorWindow`），並回傳 Qt 結束碼。
- `code = main(argv=None)` 執行 CLI 參數解析與分派（與 `rpk-calc` 相同進入點）。

`fxp` 與 `fxpi` 透過 PEP 562 `__getattr__` 延遲匯出，並與 `launch`、`main` 一同列於 `__dir__`。

### 範例

#### 1. 基礎範例：量化數值與重解讀原始位元

將 `"0.75"` 量化為有號 `Q4.4`，並將 `0xFF` 重解讀為 `Q4.4`。

```python
from rpkbin.app.calc import fxp, fxpi

val = fxp("0.75", "Q4.4")
raw = fxpi(0xFF, "Q4.4")
print(float(val), hex(val.to_bits()))
print(float(raw), hex(raw.to_bits()))
```

輸出：

```text
0.75 0xc
-0.0625 0xff
```

#### 2. 進階範例：高精確度字串、捨入模式、飽和遮罩與位元遮蔽

傳入 `Decimal` 字串以在二進位量化前保留十進位精確度，檢查陣列的飽和遮罩，並用 `fxpi` 遮蔽寬整數的低位元。

```python
from decimal import Decimal
from rpkbin.app.calc import fxp, fxpi

tie_even = fxp(Decimal("0.5"), "Q8.0", quant="RND_EVEN")
tie_up = fxp(Decimal("0.5"), "Q8.0", quant="RND")
arr = fxp([0.25, 10.0], "Q4.4", overflow="SAT")
masked = fxpi(300, bits=8, frac_bits=4)

print(float(tie_even), float(tie_up))
print(arr.to_numpy().tolist(), arr.saturated_mask.tolist())
print(float(masked), hex(masked.to_bits()))
```

輸出：

```text
0.0 1.0
[0.25, 7.9375] [False, True]
2.75 0x2c
```

### 輸入引數

| 名稱 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `x` | 純量或陣列 | 必填 | `fxp` 接受 `int`、`float`、`Decimal`、`Fraction`、`APyFixed`、十進位 `str`，或 `list`／`tuple`／`np.ndarray`／`APyFixedArray`。`fxpi` 接受整數（或實作 `__index__` 之物件）、`APyFixed`，或整數陣列／序列。 |
| `fmt` | `str \| FxpSpec \| None` | `None` | 格式字串（`"Q4.4"`、`"UQ8.8"`、`"sfix16_En12"`，或可唯一還原的自訂範本）或 `FxpSpec`。改用明確位寬關鍵字引數時請省略。 |
| `argv` | `Sequence[str] \| None` | `None` | 傳給 `launch`／`main` 的引數清單（預設為 `sys.argv`／`sys.argv[1:]`）。 |

### 具名引數（Keyword-Only）

| 名稱 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `bits` / `wl` | `int \| None` | `None` | 總硬體字長位元數（`1` 至 `256`）。兩者互斥，且不可與 `fmt` 混用。 |
| `fwl` / `frac_bits` | `int \| None` | `None` | 小數位元數（可為負數、零或大於 `wl`）。兩者互斥，且不可與 `fmt` 混用。 |
| `int_bits` | `int \| None` | `None` | 整數側位元數（有號格式包含 sign bit）。須搭配 `wl`/`bits` 或 `fwl`/`frac_bits` 其中一項（同時傳入三者會引發 `ValueError`）。 |
| `signed` | `bool` | `True` | 使用明確位寬引數時，`True` 表示有號二補數，`False` 表示無號。 |
| `quant` | `str \| None` | `None` | （僅 `fxp`）量化捨入模式：`"RND"`、`"RND_EVEN"`、`"TRN"` 或 `"TRN_ZERO"`。`None` 時採用目前 `CalcConfig.default_quant`（預設 `"RND"`）。 |
| `overflow` | `str \| None` | `None` | （僅 `fxp`）溢位策略：`"SAT"` 或 `"WRAP"`。`None` 時採用目前 `CalcConfig.default_overflow`（預設 `"SAT"`）。 |
| `mode` | `str \| None` | `None` | （僅 `launch`）`"simple"`、`"python"`，或 `None` 以依 `CalcConfig.launch_mode` 啟動。 |
| `config_path` | `Path \| str \| None` | `None` | （僅 `launch`）覆寫預設的 `~/.rpkbin/calc_settings.json`。 |

### 輸出

- `fxp` 回傳 `RpkFixed`（純量）或 `RpkFixedArray`（陣列／序列），帶有 `is_unsigned`、`hw_wl`、`target_real`／`target_values` 與 `is_saturated`／`saturated_mask`。
- `fxpi` 回傳 `RpkFixed` 或 `RpkFixedArray`，帶有 `is_unsigned` 與 `hw_wl`，且 `target_real = None`（無轉換參考）。
- `launch` 與 `main` 回傳 `int` 程序結束碼。

### 提示與限制

- 不可將 `fmt` 與明確位寬關鍵字（`bits`、`wl`、`fwl`、`int_bits`、`frac_bits`）混用。
- `fxp("0x15", "Q4.4")` 會引發 `ValueError`，因為 `fxp` 的字串輸入只接受十進位數字；若要重解讀原始位元請改用 `fxpi(0x15, "Q4.4")`。
- `fxpi` 拒絕 `bool` 與 `np.bool_`（引發 `TypeError`），且不接受 `quant` 或 `overflow` 引數。
- 在本工具的 Q 表示法中，`Qm.n` 的 `m` 包含 sign bit，總寬度為 `m + n`（例如 `Q1.15` 為 16 bits）；`UQm.n` 為無號格式，總寬度同樣為 `m + n`。

---

## 簡易模式

簡易模式直接在 Qt 程序內以精確有理數（`Fraction`）與 `Decimal` 運算，不需要 IPython kernel。透過中段控制列可切換面板配置（**僅顯示鍵盤 (Keypad)**、**僅顯示 Bit Select (Bits)**、**同時展開 Bit 與鍵盤**、**收合下方區域**），將主要格式重設為 **Int32**（`wl=32`、`fwl=0`、`signed=True`），從 FXP 歷史下拉選單挑選或釘選格式，展開 **FXP 格式與量化設定** 抽屜（`Signed`/`Unsigned`、`WL`、`FWL`、`Quantization`、`Overflow`），或以 **+對照**（`+Cmp`）新增平行的比較列（`FXP2`、`FXP3`⋯⋯）。

### 進位制與 `DEC` 的 FXP 未縮放語意

| 列位 | 格式與顯示規則 | 輸入行為 |
|---|---|---|
| `HEX` | 無號原始位元的十六進位表示，每 4 位數分組（`0x1 0015`、`0xFF`）。 | `[0, 2^wl - 1]` 範圍內的無號原始整數；空白與 `_` 會被忽略，`0x` 後接 `+`/`-` 會被拒絕。 |
| `DEC` | 目前 FXP 格式底層的**未縮放有號整數**（`raw_uint_to_signed_int`）。 | 輸入十進位整數時會自動乘上 `2^-fwl`（`value * 2^-fwl`）。 |
| `OCT` | 無號原始位元的八進位表示，每 3 位數分組（`0o200 025`）。 | `[0, 2^wl - 1]` 範圍內的無號原始整數；空白與 `_` 會被忽略，`0o` 後接 `+`/`-` 會被拒絕。 |
| `BIN` | 無號原始位元的二進位表示，左側補零至 4 位元邊界後每 4 位分組（`0b 1111 1111`；例如 `5` 會顯示為 `0b 0101`）。 | `[0, 2^wl - 1]` 範圍內的無號原始整數；空白與 `_` 會被忽略，`0b` 後接 `+`/`-` 會被拒絕。 |
| `REAL` | 未經量化的精確有理數輸入或運算結果（`display_fraction`）。 | 十進位小數或科學記號（`[eE][+-]?[0-9]{1,3}`），先精確解析再量化。 |
| `FXP` | 套用主要 FXP 格式後的量化值 `raw_int * 2^-fwl`（預設為 `Int32`）。 | 與 `REAL` 相同的十進位／科學記號輸入，依目前的 `Quantization` 與 `Overflow` 提交量化。 |

由於 `DEC` 永遠對應目前 FXP 格式底層的未縮放整數，顯示與輸入都會套用 `2^-fwl` 縮放。選擇 `Q4.4`（`wl=8`、`fwl=4`）後貼上 `0xFF`，`DEC` 會顯示 `-1`，`REAL`／`FXP` 則顯示 `-0.0625`。當作用中列為 `DEC` 時，輸入 `1` 會把原始位元設為 `1`（在 `REAL` 為 `0.0625`），輸入 `-1` 則設為 `0xFF`（`-0.0625`）。在任何進位制模式輸入帶有 `0x`、`0o`、`0b` 前綴的字面值時，一律以無號原始位元解析。

### 運算子、`NAND`／`NOR` 與優先順序

運算式最多支援 512 個字元與 64 層括號巢狀，優先順序由低到高如下：

1. 位元 OR／NOR：`|`、`NOR`（不區分大小寫，除非該名稱存在於 `operands`；在 `wl` 寬度內計算 `~(left | right)`）
2. 位元 XOR：`^`
3. 位元 AND／NAND：`&`、`NAND`（不區分大小寫，除非該名稱存在於 `operands`；在 `wl` 寬度內計算 `~(left & right)`）
4. 位元移位：`<<`、`>>`（右側移位次數子運算式，包含括號算式如 `0x01 << (1 + 2)`，即使在 `fwl != 0` 時也一律以 `unscaled=True` 解析為 `0` 至 `256` 的整數）
5. 加法與減法：`+`、`-`
6. 乘法、除法與取餘：`*`、`/`、`%`
7. 一元正負號與位元反相：`+`、`-`、`~`
8. 括號：`(...)`

所有位元運算子（`&`、`|`、`^`、`NAND`、`NOR`、`~`）以及移位的左運算元，都要求 `value * 2^fwl` 必須是精確整數；若含有小於 `2^-fwl` LSB 的小數尾數會引發 `ValueError`。在鍵盤面板按下 **NOT**（`~`）鍵會立即對目前的 `raw_uint` 取反。

### 四種位元移位模式

在計算 `<<` 或 `>>` 前，可於位元運算列上方選擇移位模式（移位次數 `n` 須在 `0..256`；`n == 0` 時數值與 `carry_bit` 均維持不變）：

| 模式 | 鍵盤標籤 | `<<`（左移） | `>>`（右移） | 進位旗標（`carry_bit`） |
|---|---|---|---|---|
| `arithmetic`（預設） | **算術**（`Arith`） | 算術／邏輯左移（`signed_val << n`）。 | 算術右移（`signed_val >> n`）：有號格式以 MSB 進行符號擴充，無號格式補零。 | `n <= wl` 時為最後移出的位元；`n > wl` 時 `<<` 為 `0`，`>>` 為符號位元（負數為 `1`，其餘為 `0`）。 |
| `logical` | **邏輯**（`Logic`） | 無號左移（`(u << n) & mask`）。 | 無號右移（`u >> n`）：無論格式是否有號，MSB 一律補零。 | `n <= wl` 時為最後移出的位元，否則為 `0`。 |
| `rotate` | **循環**（`Rot`） | 在 `wl` 位元內循環左旋 `k = n % wl` 位。 | 在 `wl` 位元內循環右旋 `k = n % wl` 位。 | `k > 0` 時：`<<` 設 `carry_bit = res & 1`；`>>` 設 `carry_bit = (res >> (wl - 1)) & 1`。`k == 0` 時不變。 |
| `rotate_carry` | **帶進位 (0/1)**（`Rot·C (0/1)`） | 以 `(wl + 1)` 位元環 `[carry_bit \| u]` 循環左旋 `k = n % (wl + 1)` 位。 | 以 `(wl + 1)` 位元環 `[carry_bit \| u]` 循環右旋 `k = n % (wl + 1)` 位。 | 旋轉後 `(wl + 1)` 位元環的最高位 bit `wl`（`k == 0` 時不變）。 |

按下 **C** 或 **Esc** 會將 `carry_bit` 重設為 `0`。運算式發生錯誤時，`carry_bit` 與原有數值都會保持原狀。

手算對照範例（`Q8.0`，`wl=8`；`display("HEX")` 不會依 `wl` 左側補零）：
- `0x80 >> 1` 在 **算術**（`Arith`）得到 `0xC0`（`-64`），在 **邏輯**（`Logic`）得到 `0x40`（`64`）。
- `0x81 << 1` 在 **循環**（`Rot`）得到 `0x3`；在 **帶進位**（`Rot·C`）且 `carry_bit = 0` 時，執行 `0x81 << 1` 得到 `0x2`（`carry_bit = 1`），接著執行 `0x02 >> 1` 會還原為 `0x81`（`carry_bit = 0`）。

在 Python 中，簡易模式底層由內部類別 `SimpleCalcEngine`（`rpkbin.app.calc.local_engine`）直接實作上述規則：

```python
from rpkbin.app.calc.core.config import CalcConfig
from rpkbin.app.calc.local_engine import SimpleCalcEngine

eng = SimpleCalcEngine(CalcConfig())

# 1. Q4.4（wl=8、fwl=4）下的 DEC 未縮放語意
eng.set_primary("Q4.4")
eng.paste("0xFF", "HEX")
print("Q4.4 0xFF ->", eng.display("DEC"), eng.display("REAL"), eng.display("FXP"))
eng.paste("-1", "DEC")
print("Q4.4 DEC -1 ->", eng.display("HEX"), eng.display("REAL"))

# 2. Q4.4 下的 NAND / NOR
eng.evaluate("0xF0 NAND 0x0F", "HEX")
print("NAND ->", eng.display("HEX"))
eng.evaluate("0x00 NOR 0x0F", "HEX")
print("NOR  ->", eng.display("HEX"))

# 3. Q8.0（wl=8）下的四種移位模式
eng.set_primary("Q8.0")
for mode in ("arithmetic", "logical"):
    eng.set_shift_mode(mode)
    eng.evaluate("0x80 >> 1", "HEX")
    print(f"{mode:10s} 0x80>>1 -> {eng.display('HEX')} ({eng.display('DEC')}), carry={eng.carry_bit}")

eng.set_shift_mode("rotate")
eng.evaluate("0x81 << 1", "HEX")
print(f"rotate     0x81<<1 -> {eng.display('HEX')}, carry={eng.carry_bit}")

eng.carry_bit = 0
eng.set_shift_mode("rotate_carry")
eng.evaluate("0x81 << 1", "HEX")
print(f"rot_carry  0x81<<1 -> {eng.display('HEX')}, carry={eng.carry_bit}")
eng.evaluate("0x02 >> 1", "HEX")
print(f"rot_carry  0x02>>1 -> {eng.display('HEX')}, carry={eng.carry_bit}")
```

輸出：

```text
Q4.4 0xFF -> -1 -0.0625 -0.0625
Q4.4 DEC -1 -> 0xFF -0.0625
NAND -> 0xFF
NOR  -> 0xF0
arithmetic 0x80>>1 -> 0xC0 (-64), carry=0
logical    0x80>>1 -> 0x40 (64), carry=0
rotate     0x81<<1 -> 0x3, carry=1
rot_carry  0x81<<1 -> 0x2, carry=1
rot_carry  0x02>>1 -> 0x81, carry=0
```

### 位元選擇、變數與歷史紀錄

- **位元選擇**：點選 4 位元 nibble 格切換單一位元、以 **~ 反相** 與 **0 歸零** 操作整個字寬、切換 **MSB↔LSB 反轉** 顯示順序，或透過 **讀取** 與 **寫入** 讀寫包含端點的 `[high:low]` 無號位元欄位。
- **變數側欄**：內建預設槽位 `M`（亦可由 **MS** 寫入）與 `A` 至 `F`，以及 **+ 新增** 自訂合法 Python 識別字名稱的變數。可透過 **存**（`STO`）、**回填**（`RCL`）、**Delete**／**清除**／**刪除** 或 **F2**（**重新命名…**）操作。從 `HEX`、`DEC`、`OCT`、`BIN` 存入的變數在喚回時會載入原始位元至目前格式；從 `REAL` 或 `FXP` 存入者則提交其精確有理數值。
- **歷史紀錄側欄**：保留最近 100 筆成功求值的運算式，點選任一卡片即可喚回當時的快照。

---

## Python Studio 模式

按 **Python** 或執行 `rpk-calc --python`（`rpk-calc --studio`）即可進入 Python Studio。切回簡易模式時，Studio kernel 會保持執行狀態，且 Studio 的變數工作區與簡易模式彼此獨立。

### 開啟乾淨工作區

在專案根目錄安裝選配相依套件並啟動 Studio：

```powershell
pip install -e ".[calc]"
rpk-calc --python
```

GUI 會以乾淨的 `In[1]` 提示字元啟動，不顯示啟動畫面或預設範例變數。一般 Python 變數指派依 Python 語意執行，只有回傳非 `None` 值的運算式會更新 `ans`。

- **鍵盤操作**：完整語句按 **Enter** 執行或於未完成語句換行，**Shift+Enter** 執行整段 cell，**Ctrl+Enter** 強制插入換行，**Tab** 觸發屬性補全。
- **內建命令**：`whos`／`who`（在 Studio 支援指定變數名稱過濾；在 `--eval` 不帶引數）列出工作區變數；`clc` 只清除畫面而不刪除變數；`clear`／`clear all` 清除使用者變數並保留已啟用的自動匯入與快取啟動符號。
- **標題列控制項**：**待命／停止** 用於中斷 kernel（原生 C 或作業系統呼叫可能使中斷延遲至結束後才生效）；**命令提示**（或選單 **說明 → 命令提示**）開啟包含 **FXP**、**Python / NumPy** 與 **Workspace** 三個分頁的非模態參考視窗。
- **本次命令歷史**：點選命令視窗的 **命令歷史** 圖示（開啟 **本次命令歷史** 視窗），可搜尋、預覽、放回輸入區（**帶入輸入區**）或匯出 UTF-8 `.py` 檔（**匯出所選命令**／**全部匯出**）。歷史最多保留 1,000 筆或 1 MiB（單筆超過 1 MiB 會略過），重啟 kernel 時清空。

要在命令視窗內直接繪圖，可執行：

```ipython
%matplotlib inline
import numpy as np
import matplotlib.pyplot as plt
t = np.linspace(0, 2 * np.pi, 200)
plt.plot(t, np.sin(t))
plt.show()
```

### 主題、字型、Q 表示法與數值顯示

開啟 **設定… → 外觀** 可設定介面語言（`English` 或 `繁體中文`）、主題（`dark` 或 `light`）、介面與程式／數值字型、字級與縮放比例。使用 `zh_TW` 時，勾選 **工作區術語保留英文** 可保留 **Command Window**、**Workspace**、**Name**、**Format** 等 Qt 標籤為英文，不影響 kernel 運算與使用者程式碼。按 **套用** 立即更新 Qt 介面，不重啟 kernel。

開啟 **設定… → FXP 格式** 可切換 **Q 格式**、**MATLAB 簡寫**（`sfix16_En12`）或 **自訂顯示模板**。**Q 格式** 預設使用 `Qm.n` 慣例：`m` 為包含 sign bit 的整數側位元數、`n` 為小數位元數（總寬度 `m + n` bits）；`UQm.n` 為無號格式。當自訂範本的簡單佔位符能唯一還原正負號、`wl` 與 `fwl` 時，GUI 也可直接作為輸入格式解析（例如 `S{iwl}|{fwl}` 會將 `S6|10` 解析為總寬 16 bits、小數 10 bits）。

使用 **檢視 → 數值顯示（僅檢視）** 可切換 Workspace 與矩陣表格的實數顯示格式（**完整精度／自動**、**科學記號** 或 **固定小數位**，支援 1 至 17 位小數，超過 256 字元會回退為科學記號）。Inspector、編輯欄位、複製內容與原始位元維持原精確度。**檢視 → 重設版面** 會還原視窗尺寸、分隔線與展開 Inspector，且不重啟 kernel。

### 使用 APyTypes 建立明確 FXP 值

命令視窗預設載入原生的 `apy`、`APyFixed`、`APyFixedArray`、`APyFloat` 與 `APyFloatArray`，以及 `fxp` 與 `fxpi`。

```python
from apytypes import APyFixed

a = APyFixed.from_float(2, bits=8, frac_bits=4)
b = APyFixed(7, 4, 2)  # 第一個引數是原始位元（7），int_bits=4、frac_bits=2
print(float(a), hex(a.to_bits()))
print(float(b), b.to_bits())
```

輸出：

```text
2.0 0x20
1.75 7
```

混用 Python 數值時遵循 APyTypes 原生轉換與結果分派規則。若對窄格式直接加上 Python 整數，該整數在轉入窄格式時可能溢位，需要精確位寬時請明確建構另一個運算元：

```python
from apytypes import APyFixed

narrow = APyFixed(2, bits=3, int_bits=1)  # 0.5，範圍 -1 .. 0.75
print(float(narrow + 1))                # -0.5：Python 1 在窄格式中回繞 (WRAP)
one = APyFixed.from_float(1, bits=8, frac_bits=4)
print(float(narrow + one))              # 1.5
```

輸出：

```text
-0.5
1.5
```

在 GUI 中：
- **轉為 FXP…** 將原生實數純量或陣列轉成 FXP；**轉為 float…** 將 FXP 轉回 `float`（純量）或 `np.float64`（陣列，保留 shape）。
- **Real / Hex 釘選** 僅影響 FXP 對 FXP 的格式變更（Real 盡可能保留數值；Hex 保留原始位元）。原生轉 FXP 與 FXP 轉浮點一律執行數值轉換。
- **Value** 卡片編輯會作用於所選純量或陣列元素。FXP 數值欄支援受限數學運算式（如 `pi/4`、`2**-3`），拒絕函式呼叫、屬性存取與容器。
- **位元控制**（**反轉**、**歸零**、**編輯位元範圍** 以無號值寫入包含端點的 `[high:low]`）以最低有效端的 bit 0 起算，每 4 位元分組。
- **轉換狀態**：直接呼叫原生 `APyFixed`／`APyFixedArray` 建構式沒有 GUI 轉換來源紀錄，起始會顯示 `沒有轉換參考值（誤差未知）`（英文介面為 `No conversion reference (error unknown)`），即使是 `APyFixed.from_float(0.1, bits=64, frac_bits=60)` 也一樣（因 Python 的 `0.1` 本身已是 binary64 近似值）。唯有 GUI Real 編輯、`fxp(...)` 或 Real 釘選重設格式才會建立比較來源；原始位元編輯與 Hex 釘選重解讀不會建立來源。

### 查看原生數值與編輯 APyFloat 位元

```python
import numpy as np
from apytypes import APyFloat

n = -255
s = np.float32(1.5)
f = APyFloat.from_float(1.5, exp_bits=5, man_bits=2, bias=7)
assert f.to_bits() == 0x1E
print(n, float(s), hex(f.to_bits()))
```

輸出：

```text
-255 1.5 0x1e
```

- **Python `int`**：唯讀 Hex／Binary 欄位顯示 `-0xff` 與 `-0b11111111`。超過 4,096 個 magnitude bits 時會改為有界預覽並停用截短複製。
- **Python `float` 與 NumPy 浮點數**：展開 **位元表示（唯讀）** 即可直接依儲存位元檢視 sign、exponent、fraction 與分類（正負零、無窮大、NaN、正規數、次正規數），不會轉換精度。
- **純量 `APyFloat`**：支援點擊切換個別位元（例如切換 bit 7 符號位元會讓 `0x1E`／`1.5` 變成 `0x9E`／`-1.5`）並支援 GUI 復原／重做；數值與格式欄保持唯讀，`APyFloatArray` 操作請在 REPL 執行。

### 檢視與編輯矩陣

```python
import numpy as np
from apytypes import APyFixedArray

A = APyFixedArray.from_float(np.zeros((2, 2)), bits=8, frac_bits=4)
v = np.fft.fft([1, 2, 3, 4])
v[1] = 5 - 6j
print(A.shape, v[1])
```

輸出：

```text
(2, 2) (5-6j)
```

在 Studio 中，`fft` 已預先載入於工作區命名空間。虛擬矩陣編輯器每次按需載入 32 × 16 分頁，預設顯示前兩個維度並將後續軸索引設為 `0`（在切片欄輸入 `0, 1` 可檢視 `A[:, :, 0, 1]`），並以零起始列／欄座標跳轉。

- **TSV 複製／貼上**：選取矩形區塊後按 `Ctrl+C`／`Ctrl+V`（或工具列圖示）。貼上最多支援 100,000 個儲存格與 8 MiB，並於寫入前先驗證整塊資料型別、範圍與形狀；失敗時不留下部分修改。複製整張矩陣上限為 100,000 格。
- **GUI 復原／重做**：保留最多 100 筆或 8 MiB 的 GUI 純量、儲存格與貼上編輯紀錄；執行 REPL cell、變更切片或整體陣列轉換格式會清空編輯日誌。
- **選區統計**：對最多 100,000 格的矩形選區轉換為 `float64`／`complex128` 後統計有限數值摘要。
- **大型陣列飽和標記**：超過 4,096 格的陣列不掃描整張陣列，會在 Workspace 顯示保守的 `[SAT?]`（來源未完整驗證）：

```python
import numpy as np
from apytypes import APyFixedArray

big = APyFixedArray.from_float(np.zeros(4097), bits=8, frac_bits=4)
print(big.shape)
```

輸出：

```text
(4097,)
```

超過 4,096 格時，單格 Inspector 仍會照常顯示所選格子的飽和狀態；紅色 `[SAT]` 標記於已驗證至少一格經 GUI 飽和編輯的小型陣列。

### 執行控制、自動載入、啟動檔與偏好設定

- **執行期檔案**：設定保存於 `~/.rpkbin/calc_settings.json`（或 `--config PATH`）。Kernel 連線資訊、IPython 歷史與 Log 置於暫存執行目錄，關閉時清除。
- **自動載入**：在 **設定… → 自動載入** 中可各自啟用或停用內建函式群組（`fxp_core`、`numpy`、`math`）。自訂匯入僅接受明確的絕對 Python `import` 語句與註解（不接受萬用字元、相對匯入、一般程式碼或 `_` 開頭的名稱／別名），且套件須已安裝於 Calc 使用的環境中。
- **啟動檔（`startup.py`）**：建立於目前設定檔所在資料夾（GUI 與 `--eval` 皆同）。載入順序為內建群組、自訂匯入、接著 `startup.py`。啟動檔僅在 kernel 啟動／重啟或按 **重新載入並執行** 時執行；按 **套用** 只會在既有 kernel 重新載入變更的匯入群組，不執行 `startup.py` 也不重啟 kernel。
- **偏好設定匯出／匯入**：在 **設定… → 偏好管理** 以具版本 JSON 格式（`{"kind":"rpk-calc-preferences","version":1,"preferences":{...}}`）匯出與驗證匯入。匯入時須明確勾選 **套用後取代 startup.py（不執行程式碼）**（寫入前備份為 `startup.before-import-HASH.py` 並檢查修訂版本衝突）或 **匯入自訂 import（套用後會載入）**。**還原預設值** 會保留現有的 `startup.py`。

---

## 延伸閱讀

- [架構與資料邊界](architecture.md)
- [文件導覽](../README.md)
- [rpk-calc (English)](../../en/calc/README.md)
