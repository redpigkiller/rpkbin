# Text-Diff — 純文字差異比對報告器

[![English](https://img.shields.io/badge/Language-English-blue.svg)](text_diff.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](text_diff_zh.md)

`Text-Diff` 是一個專為終端機與純文字環境設計的進階文字比對工具，能夠產生高可讀性的「左右並排 (Side-by-Side)」差異報告。

底層採用 Google 的 `diff-match-patch` 演算法，它不僅能精準追蹤字元級別的修改，還能完美處理東亞字元（全形/半形）的視覺寬度計算、智慧自動換行，以及未修改段落的自動摺疊隱藏。

---

## 快速開始 (User Guide)

您通常只需透過對外提供的兩個主要 API 即可使用所有功能：`diff_files` (檔案比對) 或 `diff_lines` (記憶體 List 比對)。

### 1. 比較兩個檔案

最簡單的產生方式是直接將兩個檔案路徑交給 `diff_files`。它會自動讀取內容、進行比對，並選擇性地將排版好的報告寫入目的地檔案。

```python
from rpkbin.utils.text_diff import diff_files

# 比較 old.txt 與 new.txt，並將報表輸出至 diff_out.txt
report_string = diff_files(
    file1_path="old.txt", 
    file2_path="new.txt", 
    output_path="diff_out.txt"
)

print(report_string)
```

### 2. 比較記憶體內的文字陣列

如果您的文字已經存在於記憶體中（例如：系統即時 Log、動態生成的腳本），您可以直接傳入 List 給 `diff_lines`：

```python
from rpkbin.utils.text_diff import diff_lines

left_lines = [
    "def calculate_tax(price, rate):",
    "    return price * rate"
]

right_lines = [
    "def calculate_tax(price, tax_rate):",
    "    return price * tax_rate"
]

# 產生左右並排的報告字串
report = diff_lines(left_lines, right_lines)
print(report)
```

### 3. 看懂視覺化排版

`Text-Diff` 採用嚴格的雙欄位佈局，中間以狀態分隔線 (Spine) 切開。

- **`|` (相同)**: 兩邊內容完全一致。若連續相同行數超過門檻，將會被智慧摺疊隱藏。
- **`-` (刪除)**: 該行存在於左側，但右側已被移除。
- **`+` (新增)**: 該行是被加入到右側的新內容。
- **`*` (取代/修改)**: 該行內容有變動。如果左右兩邊修改的「行數完全相等 (1:1)」，引擎會啟動深度的字元級別比對：
  - `~` 會精準標示在左側被刪除的字元正下方。
  - `^` 會精準標示在右側被新增的字元正下方。

```text
=================================================================================================
   1  # This is a sample file                    |    1  # This is a sample file
   2  def calculate_tax(price, rate):            *    2  def calculate_tax(price, tax_rate):
                               ~~~~                                               ^^^^^^^^
   3      return price * rate                    *    3      return price * tax_rate
                         ~~~~                                               ^^^^^^^^
=================================================================================================
```

---

## API 參考手冊 (Detailed Control)

### 核心函式

| API 函式 | 說明 |
| --- | --- |
| `diff_files(file1_path, file2_path, output_path=None, **kwargs)` | 讀取兩個檔案，產生對齊的 Diff 報告字串。若有提供 `output_path` 則會同步寫入檔案，並回傳該字串。 |
| `diff_lines(lines1, lines2, **kwargs)` | 傳入兩個字串 List 作為左右內容，計算並構造完整的左右並排報告字串。 |

### 排版設定參數 (`**kwargs`)

您可以透過傳遞以下參數給上述兩個函式，來客製化比對引擎的排版行為：

- **`col_width`** (`int`, 預設值: `40`): 左右兩邊程式碼欄位**各自單側**的「視覺字元寬度」。(注意：總報告寬度約為 `num_width*2 + col_width*2 + 9`)
- **`num_width`** (`int`, 預設值: `4`): 行號最小顯示寬度。如果檔案行數過大，該寬度會自動往上動態擴充以對齊。
- **`fold_threshold`** (`int`, 預設值: `6`): 觸發「智慧摺疊」所需要的連續相同行數最低門檻。
- **`context_lines`** (`int`, 預設值: `1`): 在摺疊區塊的上下，要保留多少行「上下文」不被摺疊。
- **`wrap_mode`** (`bool`, 預設值: `True`): 開啟時，超過 `col_width` 的長句子會被聰明地自動換行至佈局下方。如果設為 `False`，過長的句子會被無情截斷並以 `...` 結尾 (截斷為不可逆操作，若內容完整性重要請保持預設 `True`)。
- **`show_hints`** (`bool`, 預設值: `True`): 開啟時，會在修改過的地方下方，精準標示出字元級別的增加與刪除提示 (`^` 與 `~`)。
- **`diff_style`** (`str`, 預設值: `"side_by_side"`): 控制差異報告的排版格式。可設定為 `"side_by_side"` (預設的左右雙欄模式) 或 `"unified"` (一般標準的單欄模式)。

### 架構與設計細節

#### 1. 視覺寬度計算 (Visual Width)
因為標準的 `len()` 函數會把中文字與英文字母都算作寬度 1，這會導致終端機排版徹底崩壞。`Text-Diff` 底層將所有數學計算交由 `unicodedata.east_asian_width` 處理，確保中文字算作寬度 `2`、英文字母算作寬度 `1`，實現像素級的等寬字型對齊。

#### 2. 安全的區塊降級機制 (Robust Block Fallbacks)
一般的 Diff 引擎在遇到「行數不對稱的取代」（例如將 3 行程式碼改成 6 行）時，很容易在中間分隔線發生錯位混亂。為了確保極致的閱讀體驗，`Text-Diff` 在遇到此情況時會安全降級回類似 Git 的做法：先將舊的段落全部視為 `Delete` (`-`) 印出，緊接著再把新的段落全部視為 `Insert` (`+`) 印出，徹底避免對齊錯亂的問題。
