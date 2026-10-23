# rpk-calc：可保留變數的 Python 工程計算機

`rpk-calc` 用於臨時的工程計算，可保留變數、定義函式，並直觀查看數值與位元表示。GUI 命令視窗使用 IPython，結合 Python、NumPy 和 APyTypes；Workspace 與 Inspector 檢視變數，需要時可用矩陣編輯器分頁查看陣列。

## 安裝並啟動乾淨工作區

在專案根目錄安裝 GUI 與 FXP 相依套件，再啟動應用程式：

```powershell
pip install -e ".[calc]"
rpk-calc
```

GUI 以乾淨的 `In[1]` 提示啟動，不顯示應用程式或 Python 啟動畫面，也不會放入範例變數。等命令視窗就緒後，輸入一段 Python：

```python
import numpy as np
x = np.array([1.0, 2.0, 3.0])
np.mean(x)
```

最後一行結果會顯示在 IPython 輸出中，並可在 Workspace 選取 `ans`。一般賦值遵循 Python 語意；只有有回傳值的運算才會更新 `ans`。IPython 的 stdout、stderr 與 traceback 會直接出現在命令視窗。

命令視窗保留 IPython 編輯操作：完整輸入按 **Enter** 執行，不完整語句按 Enter 繼續輸入；**Shift+Enter** 執行整段 cell，**Ctrl+Enter** 強制插入換行。按 **Tab** 使用屬性補全。輸入 `whos` 列出工作區變數，`clc` 清除畫面，`clear` 或 `clear all` 清除使用者變數。若只要切換應用程式介面語言，開啟 **設定… → 外觀**，選擇 **語言**（`English` 或 `繁體中文`）並按 **套用**。Qt 介面文字會即時更新，不重啟 kernel，也保留 namespace、命令歷史、尚未送出的輸入與選取狀態。使用者程式碼、變數名稱與資料不會翻譯。

命令視窗標題列的 **Stop** 按鈕以圖示搭配可見文字顯示；kernel 閒置時標示 **Idle／待命** 並停用，執行期間標示 **Stop／停止** 並啟用。若 Python 正在執行原生操作，中斷仍可能延遲。**Hints／提示** 會開啟非模態視窗，提供精簡的 Python、NumPy、APyTypes 建構範例與常用命令。輸入 `clc` 只清除畫面，不會刪除 Workspace 變數。

選單列中，**檔案**有 **結束**；**編輯**提供 GUI 數值編輯的復原/重做與 **設定…**；**檢視**提供數值顯示、版面及本次命令歷史；**說明**可開啟命令提示。頂端工具列與命令視窗控制項也提供常用操作的捷徑。

要在命令視窗內繪圖，可執行：

```python
%matplotlib inline
import numpy as np
import matplotlib.pyplot as plt
t = np.linspace(0, 2 * np.pi, 200)
plt.plot(t, np.sin(t))
plt.show()
```

正弦波 PNG 會出現在命令視窗的輸出區，不會另開繪圖視窗。

要找回本次工作階段輸入，按命令視窗的 **Session history** 圖示，可搜尋命令、預覽內容，再選 **Use as input**（只放回編輯區，不會執行），或按 **Export selected** / **Export all** 明確匯出為 UTF-8 `.py`。歷史最多保留 1,000 筆或 1 MiB，超限時移除最舊項目；超過 1 MiB 的單一命令不納入。它不會自動寫檔，重啟 kernel 後清空。

## 調整主題與字型

按 **Settings** 圖示或選 **Edit → Settings…**，開啟 **Appearance**。可選語言、主題、介面與程式/數值字型、字級及縮放。使用 `zh_TW` 時，勾選 **Keep workspace terms in English** 可讓 **Command Window**、**Workspace**、**Name**、**Format** 等應用程式標籤保留英文；這只改 Qt 顯示，不影響 kernel，也不改使用者內容。按 **Apply** 立即套用偏好，保留 namespace、命令歷史與輸入、開啟的矩陣分頁及 `startup.py`；不重啟 kernel 或執行啟動程式。對話框底部的 **Cancel** 與 **Apply** 固定位於可伸展的分頁內容下方。下拉選單或數字微調框上的滑鼠滾輪會繼續捲動頁面；鍵盤選項及微調箭頭仍可用。主視窗使用 Qt Fusion style；checkbox indicator 可與既有 QSS 搭配，在 Windows 11 保持可見。

若要選擇 FXP 格式的顯示方式，開啟 **設定 → FXP 格式**，選 **Q**、**MATLAB** 或 **Custom display template**。預設 **Q** 顯示採用本工具的慣例：`Qm.n` 中 `m` 是整數側位元數且包含 sign bit，`n` 是小數位元數，總長為 `m+n` bits。因此 signed `Q1.15` 是 16-bit word；`UQm.n` 則是 unsigned。Q 格式在不同工具中有其他慣例；需要精確硬體寬度時請以宣告中的位元數為準。GUI 格式欄接受 Q 與 MATLAB 格式。此設定只改格式文字，不改位元寬度或數值語意。Custom template 可顯示任意文字，但不改值或解析；可編輯格式欄固定使用標準 Q 表示。

若只想調整畫面上的有限實數文字，使用 **View → Number display (view only)** 選 **Full precision / automatic**、**Scientific notation** 或 **Fixed decimals**，再用 **Decimal places…** 設定 1–17 位小數（不是有效位數）。**What do these options mean?** 會開啟非模態說明，以 `1.23456789` 示範三種顯示方式，並使用目前的小數位設定。這只改變 Workspace 與矩陣的顯示，不改變 kernel 計算；Inspector、編輯欄、複製內容和 raw bits 保留原精度。超過 256 字元的 Fixed 輸出會改用 Scientific。**View → Reset layout** 會還原主視窗尺寸、分隔器與展開的 Inspector，不重啟 kernel，也不重設主題或字型。

若只需從終端機計算單一運算式，可用：

```powershell
rpk-calc --eval '1+2'
```

此命令會建立 headless `WorkspaceSession`、輸出結果後結束；它不啟動 GUI，也不是 GUI 使用的 IPython kernel。

## 使用 APyTypes 建立 FXP 值

命令視窗預載 `apy`、`APyFixed`、`APyFixedArray`、`APyFloat` 與 `APyFloatArray`，它們分別是原生 APyTypes module 與 class objects。Python 整數和 NumPy 值維持原生型別，直到你明確建立 APyTypes 值。要建立 8-bit signed、含 4 個 fractional bits 的 FXP：

```python
a = APyFixed.from_float(2, bits=8, frac_bits=4)
float(a)  # 2.0；raw bits 為 0x20
```

`APyFixed(...)` 的第一個 positional argument 是 raw bit pattern；`from_float(...)` 則將數值轉換成定點值。例如 `APyFixed(7, 4, 2)` 的 raw bits 是 7、`int_bits=4`、`frac_bits=2`，所以數值是 1.75。若要轉換數值 7，請用 `APyFixed.from_float(7, int_bits=4, frac_bits=2)`。

陣列也可直接建立：`APyFixedArray.from_float([0.25, -0.125], bits=16, frac_bits=12)`。`apy`、`APyFixed`、`APyFixedArray`、`APyFloat` 與 `APyFloatArray` 都是預載物件；`type(a) is APyFixed` 為 true，因為它們就是原生 APyTypes identities，不是 wrapper。`APyFloat.from_float(0.25, exp_bits=8, man_bits=23)` 支援一般 Python 算術與數值預覽。GUI Inspector 支援 APyFixed 位元操作與單一 APyFloat 的 bit 切換；其他原生數值提供唯讀進位或儲存位元檢視。

若要在 GUI 中建立有追蹤來源的 FXP 值，可在 Workspace 對原生變數按右鍵選 **Convert to FXP**，或在 Inspector 編輯 APyFixed 的 Real value。Settings 的 **RND/SAT** 套用於 GUI 數值編輯、轉換和重新格式化，不會改變 APyTypes constructor 預設。原生 constructor 使用 APyTypes 自身的量化與溢位行為（通常 nearest ties away 與 WRAP），也不會建立 conversion-reference 診斷來源。Inspector **Invert** 與 **Zero** 分別反轉整個 FXP 字的位元、將整個字清為 0；**Edit bit range** 以無號值寫入包含端點的 `[high:low]`，並保留範圍外的位元。硬體位元編號從最低有效端的 bit 0 開始；10-bit word 分組為 `[9 8][7 6 5 4][3 2 1 0]`。改變顯示方向或換列不會切開四位元分組，也不會改變數值。

Inspector 的全域 Real/Hex pin 只決定重新格式化既有 FXP 值時要保留實數值或 raw bits。選 Hex pin 後將 16-bit 值改成 8-bit signed 整數格式，會保留低 8 位 `0x85`，重新解讀為 `-123`；選 Real pin 則會盡量保留原實數並依新格式量化。FXP 陣列改格式會作用於整個陣列，不只目前選取的格子。

FXP Inspector 的 Real 數值欄可輸入有限的數學算式，例如 `pi/4`、`2**-3` 或整數位元運算；此欄不接受函式呼叫、屬性、容器或一般 Python 語句。一般 Python 運算仍可在命令視窗執行；此限制只適用數值編輯欄，不是 kernel sandbox。

以 Python 建立的原生 `APyFixed` 沒有 GUI conversion-reference source，起始會顯示 `No conversion reference (error unknown)`。原生 `APyFixedArray` 也從沒有 GUI provenance 開始，但 Inspector 編輯會把稀疏比較資料記在 session 擁有的 sidecar，保留原陣列物件與其 aliases。符合目前值的紀錄可顯示 GUI 編輯結果；raw-bit 編輯及 Hex pin 重解讀不會建立比較來源。

### 解讀轉換狀態

例如建立一個原生 APyFixed 值：

```python
x = APyFixed.from_float(0.1, bits=64, frac_bits=60)
```

此值會顯示 `No conversion reference (error unknown)`，即使其 bits 精確表示輸入的 float。Python 的 `0.1` 本身是 binary64 近似值，不等於精確十進位 0.1。只有 GUI Real 數值編輯或 Real pin 重新格式化會建立新的比較來源；raw-bit 編輯與 Hex pin 重解讀不會。

## 查看一般數值與編輯 APyFloat bits

在命令視窗建立三個變數：

```python
import numpy as np
from apytypes import APyFloat

n = -255
s = np.float32(1.5)
f = APyFloat.from_float(1.5, exp_bits=5, man_bits=2, bias=7)
assert f.to_bits() == 0x1E
```

在 Workspace 選取 `n`，Inspector 的唯讀 Hex／Binary 分別顯示 `-0xff` 與
`-0b11111111`，可按各欄的 Copy 複製。Python int 不假設固定字長或二補數；超過
4,096 個 magnitude bits 時，數值與進位表示改為有明確標記的預覽，截短內容不提供
Copy。原值與 Python 運算不受影響；完整進位文字可由 REPL 的 `hex(n)`／`bin(n)` 取得。

選取 `s`，Hex 顯示 `0x3FC00000`，bits 依 sign／exponent／fraction 標記。Python float
與 NumPy float16／32／64 顯示原型別的儲存 bits，欄位和 bit 按鈕唯讀；不會為了檢視
先轉成另一種浮點格式。正負零、正負無窮大、NaN、正規數和次正規數會明確分類。
這裡的 Hex 是儲存 bit pattern，不是 `float.hex()` 的十六進位數值表示。

選取 `f`，Hex 顯示 `0x1E`。點擊 sign bit（bit 7），數值會變為 `-1.5`，Hex 變為
`0x9E`；依序按 Undo、Redo，可看到 `0x1E`、`0x9E`。之後在 REPL 驗證：

```python
assert type(f) is APyFloat
assert (f.exp_bits, f.man_bits, f.bias, f.to_bits()) == (5, 2, 7, 0x9E)
```

APyFloat bit 編輯只適用單一變數，保留格式與 bias，包含特殊值的原始 bits。
其數值欄與格式欄唯讀；數值、格式變更及 APyFloatArray 操作請使用 REPL。有限的
APyFloat 若超出 Python float 的範圍或精度，數值以精確二進位科學式顯示。
執行 REPL cell 會依原有規則清除 GUI Undo／Redo，所以應先完成上述 Undo／Redo 再執行驗證。

## 檢視和編輯矩陣

先建立全零固定點陣列，再到 Inspector 編輯左上格。此處 `bits=8, frac_bits=4` 的 signed 範圍是 -8 到 7.9375，LSB 為 1/16：

```python
import numpy as np
A = APyFixedArray.from_float(np.zeros((2, 2)), bits=8, frac_bits=4)
```

在 Workspace 雙擊 `A` 開啟矩陣分頁，也可選取變數後按 **Open array editor** 圖示。選取儲存格可在 Inspector 查看該元素，雙擊可編輯；FXP 矩陣標示 **Array format:**，可切換 Real、Hex、2's 與 UInt 顯示，修改 FXP Format 會作用於整個陣列。一般 NumPy 陣列標示 **Dtype:**，格式欄顯示唯讀 dtype，不提供 inline FXP 格式轉換；可從 Workspace 右鍵選 **Convert to FXP**。可寫入的原生 NumPy 陣列會保留 dtype 編輯；整數欄位不接受越界值或小數，唯讀陣列及不支援的 dtype 不可編輯。Inspector 對原生值標示 **Type:**，不提供 FXP pin 或 FXP bit controls；FXP 格式列標明 **Whole variable**，表示操作套用整個變數。原生位元運算請在 Python/NumPy 命令視窗使用。

雙擊第一格，在 Inspector 的 Real value 欄輸入 `99` 並套用。該值會飽和為 7.9375（`127/16`），Inspector 標示 **SATURATED**。constructor 建立時沒有 GUI provenance；Inspector 編輯會將比較與飽和結果記入 session sidecar，原生陣列物件與 aliases 保持不變。一般複數陣列則以 Python 值檢視，不顯示 FXP bits：

```python
v = fft([1, 2, 3, 4])
v[1] = 5 - 6j
```

選取第二格時 Inspector 顯示 `5-6j`，不會出現 FXP Hex/位元欄位；編輯複數值會保留其虛部。

矩陣使用虛擬表格，只按需讀取可見區域。維度大於 2 的陣列會顯示前兩維，後續維度預設取索引 0；可在切片欄更改，例如 `0, 1` 表示 `A[:, :, 0, 1]`，頁面會標示目前切片。複製整張表格最多 100,000 個儲存格；更大的陣列請在 Python 中匯出所需資料。

要編輯一塊矩形資料，選取表格範圍後按 **Copy selected cells as TSV (Ctrl+C)** 圖示，或使用 Ctrl+C；在目標左上格按 **Paste TSV at the selected cell (Ctrl+V)** 圖示，或使用 Ctrl+V。整塊內容會先驗證 dtype、範圍與形狀，再一次寫入；失敗時不會留下部分修改。頂端 **Undo numeric edit** 可一次復原整次貼上，**Redo numeric edit** 可重做。GUI 編輯復原最多保留 100 筆、合計 8 MiB；REPL 執行、切片變更、整體格式轉換或其他結構操作會清除此歷史。這是 GUI 編輯復原，不會撤銷任意 Python 程式。若要複製整張矩陣，可按 **Copy entire matrix as TSV (up to 100,000 cells)** 圖示。

多維陣列可在切片欄輸入後續維度索引，例如 `0, 1` 表示 `A[:, :, 0, 1]`；**Jump to row, column** 使用從 0 開始的列、欄索引。選取一塊範圍後按 **Show statistics for the selected cells** 圖示，可查看有限值摘要；FXP 與大型整數會先轉成 float64/complex128，因此結果是近似值。更改 FXP Format 後，可按 **Preview format conversion for the whole array** 圖示檢視範圍、LSB、改值/飽和數量與範例，再選 **Apply**；也可按 Enter 直接套用。只把格式欄移開焦點不會套用。大於 4,096 格時 Preview 只抽查所選格，但套用仍會改整個變數。

### 判讀大型陣列的飽和提示

```python
import numpy as np
A = APyFixedArray.from_float(np.zeros(4097), bits=8, frac_bits=4)
```

這個 4097 格陣列超過 Workspace 的自動檢查上限，因此 Value 欄顯示 `[SAT?]`。選取第一格，在 Inspector 的 Real value 欄輸入 `99`，即可看到 `SATURATED`。超過 4,096 格時整體摘要維持保守的 `[SAT?]`；即使所選格已有確認結果也一樣。它表示整體狀態未知，不代表已飽和或安全。紅色 `[SAT]` 表示至少一格有目前仍有效的 GUI 飽和編輯；完整驗證且無飽和時不加標記。這些前綴只出現在 Workspace Value 欄，不改數值或複製內容；大型陣列不會停用單格 Inspector。

## 執行控制與限制

- 點擊 **Stop execution (Ctrl+C)** 圖示會向 Python kernel 要求中斷；若程式正等待原生 C 函式或作業系統操作，中斷可能要等該操作返回才生效。長運算期間 GUI 仍可回應，但數值編輯會停用。
- 按 **Restart kernel** 圖示會先確認，再清空工作區並重建 kernel。Kernel 崩潰後不會自動重啟；狀態列會顯示錯誤，使用者可明確選擇重啟。關閉應用程式會停止由它建立的 kernel。
- `~/.rpkbin/calc_settings.json` 保存設定。Kernel connection、history 與 log 位於暫存 runtime 目錄，結束時清除；命令歷史只屬於目前 session。
- FXP 顯示與數值預設會更新既有 session。更改自動載入函數群組或執行 `clear` 時，會重新注入內建 helper 與快取的啟動符號，不會重跑 `startup.py`。啟動程式只在 kernel 啟動/重啟，或於設定選 **Reload and run** 時執行。
- **RND/SAT** 預設套用於 GUI 實數值編輯、**Convert to FXP** 及以數值保值的格式轉換。原生 APyTypes constructors 使用 APyTypes 自身預設，也不會產生 GUI conversion-reference 記錄。
- Workspace 選取陣列後，可按 **Open array editor** 圖示，也可雙擊該列開啟矩陣分頁；hover tooltip 會說明雙擊操作。Inspector 標題列的箭頭與標題可收合/展開內容，收合時標題列仍可見。開啟陣列或在 Workspace 雙擊變數會重新展開 Inspector。
- 關閉視窗時會保存視窗位置與大小、分隔器尺寸，以及 Inspector 收合狀態，下次啟動時還原。`startup.py` 錯誤會在 traceback 顯示實際檔案路徑與行號。

程序邊界、矩陣傳輸和版本檢查見[架構說明](architecture.md)。


## 匯入與匯出偏好設定

開啟 **設定… → 偏好管理**。頁面顯示設定檔路徑；**Open settings folder** 可開啟所在資料夾。**Export preferences…** 會匯出有版本的 JSON，並可將目前啟動原始碼放在 `preferences.startup_script`。**Import preferences…** 會先預覽變更，並以純文字顯示啟動腳本及自訂 import。若要匯入啟動原始碼，明確勾選 **Replace startup.py after Apply (does not run code)**；若要匯入自訂 import，另勾 **Include custom imports (runs after Apply)**。按 **Apply** 後，啟動原始碼會取代 `startup.py` 但不會執行；同意匯入的自訂 import 則會在目前 kernel 載入。若啟動檔版本已變更，會拒絕覆蓋並要求重新開啟設定。取代前會備份為 `startup.before-import-HASH.py`。**還原預設值** 保留 `startup.py`；按 **Cancel** 捨棄對話框草稿，但不會撤銷外部編輯器已儲存的檔案。

例如，從一台安裝匯出偏好並在另一台匯入。先檢視預覽中的各項原始碼。若要在目前工作階段載入自訂 import，勾選 **Include custom imports (runs after Apply)**；若要複製啟動腳本，另外勾選 **Replace startup.py after Apply**。套用自訂 import 不會執行 `startup.py`；啟動檔仍只在 kernel 啟動/重啟或按 **Reload and run** 時執行。版本 envelope 例如 `{"kind":"rpk-calc-preferences","version":1,"preferences":{"startup_script":"...","custom_imports":"from math import prod as product"}}`；執行期 `startup_file`、視窗位置與大小、分隔器/收合狀態、工作區與歷史不會匯出。

在 **Auto-import** 頁，內建函數群組的內容固定，只能勾選啟用或停用。要加入 import，請在 **Custom imports** 輸入。此欄只接受明確的絕對 Python import 語句與註解；不接受萬用字元、相對 import、其他程式碼，或以 `_` 開頭的匯入名稱/別名。套件必須已安裝在 Calc 使用的 Python 環境中；此設定不會安裝套件。例如輸入 `from math import prod as product`，按 **Apply**，再於命令視窗執行 `product([2, 3])`，結果應為 `6`。載入順序是內建函數群組、自訂 import、`startup.py`。startup 預覽是獨立功能：**Refresh preview** 重新讀取已儲存檔案；**Open text editor** 在 Windows 開啟 Notepad、macOS 開啟 TextEdit；Linux 會請你選擇文字編輯器執行檔。第一次建立的 `startup.py` 與目前設定檔位於同一資料夾，預設內容只有 SciPy imports、明確覆寫 FFT、`db20` 和 `fxp` 等註解範例；啟用前請先安裝選用套件。升級時，舊 JSON 的 `startup_script` 會用來初始化 `startup.py`；若已有不同內容的檔案，會保留現有檔，將舊程式另存為 `startup.legacy-HASH.py`，並顯示通知。Imports 與 helper 函數不會顯示在 Workspace。

GUI 負責讀寫 `startup.py`；kernel 讀取後以實際檔案路徑編譯，並在目前 session namespace 執行。例：啟動檔定義 `scale(x): return x * gain`，之後在命令視窗輸入 `gain = 3`，呼叫 `scale(2)` 會得到 `6`。按 **Apply** 會儲存偏好，並在目前 kernel 載入有變更的函數群組與自訂 import；既有使用者變數及快取的 startup binding 會保留，kernel 不重啟，也不執行 `startup.py`。啟動檔在 kernel 啟動/重啟時，或按 **Reload and run** 時執行。重新載入就是一般 Python 執行：不會回滾先前副作用、不保證移除舊腳本產生的名稱，也不會自動重新載入已匯入的 module。只有仍由 calculator 擁有的舊 helper binding 才會更新；使用者重新綁定的名稱會保留，而腳本明確賦值仍依 Python 規則覆寫。切換函數群組或自訂 import，以及執行 `clear`，會恢復快取 helper，不重跑腳本。每條 import 語句會分別嘗試；失敗項目會顯示於 Auto-import 狀態及應用程式錯誤，其他成功匯入仍可使用。失敗語句不會留下部分 binding，但 Python module 副作用無法回滾。Startup 狀態會顯示成功或含檔案路徑、行號的 traceback。`rpk-calc --eval` 也使用預設設定資料夾中的 `startup.py`。
