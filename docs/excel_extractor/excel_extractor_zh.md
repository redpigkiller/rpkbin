# Excel Extractor — 以型別為基礎的 Excel 資料擷取工具

[![English](https://img.shields.io/badge/Language-English-blue.svg)](excel_extractor.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](excel_extractor_zh.md)

處理非制式排列的 Excel 結構化資料常常是一件麻煩的事。如果版面多了一列或偏移了一行，依賴固定座標的 `sheet.cell(row, col)` 程式碼就會整個失靈。

**Excel Extractor** 採用了另一套思維：你只需要描述你期望看到的資料「形狀」（一個具備欄位定義的樣板 Block），引擎便會掃描整份 Excel，自動找出相符格式所在的絕對位置與內容。

---

## 快速開始 (使用指南)

### 步驟一：描述你的樣板

請先用 `pip install -e .[excel]` 安裝 Excel 解析與可選的模糊比對相依套件。

假設你想從一堆亂糟糟的報表中，穩定地抽取出如下長相的薪資表格 (無論它在報表的左上角、中間還是右下角)：

| 部門 | 姓名  | 月薪  |
|------|-------|-------|
| IT   | Alice | 50000 |
| IT   | Bob   | 60000 |
| HR   | Carol | 55000 |

這時候你可以使用我們的樣板建立 API：

```python
from rpkbin.excel_extractor import match_template, Block, Row, Types

# 定義表格外觀
template = Block(
    # 第一列：精確比對這三個標題字眼
    Row(pattern=["部門", "姓名", "月薪"], node_id="header"),      
    
    # 後續列：型別分別是 字串、字串、整數。並且指定 repeat="+" 代表「至少一列，越多越好」
    Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
    
    # 幫這個樣板取個名字，方便追蹤
    block_id="salary_table",
)
```

### 步驟二：執行掃描與讀取結果

設定好樣板後，只要將它與要爬取的 Excel 檔案交給 `match_template` 即可得到定位分析結果。

```python
# match_template 會回傳 MatchOutput 物件，內含所有攤平後的配對結果
results = match_template("report.xlsx", template)

for block_match in results.blocks:
    print(f"在工作表 '{block_match.sheet_name}' 找到表格位置：{block_match.start} → {block_match.end}")
    
    # 印出表格裡所有列的資訊
    for row in block_match.rows:
        row_idx = row.row
        values = [cell.value for cell in row.cells]

        if row.node_id == "header":
            print(f"- [標題在第 {row_idx} 列] {values}")
        elif row.node_id == "data":
            print(f"  [資料在第 {row_idx} 列] {values}")
```

短短幾行程式碼，不僅克服了表格隨意被位移的問題，同時還幫你校驗了資料型別（排除了型別不對的髒資料區域）！

---

## API 參考 (詳細控制)

這部分將詳細展開 Excel Extractor 提供給你的各種強力設定選項，讓你能組裝出足以應付最複雜排版的樣板。

### 第一部分：格子型別常數（`Types`）

你可以在 `Row` 的 `pattern` 裡面混用字串本身（代表完全精確吻合）或者是下方的 `Types` 常數條件：

| 常數 | 比對對象 |
|------|---------|
| `Types.STR` (也可寫作 `Types.NONEMPTY`) | 任何非空字串 (包含數字字串) |
| `Types.INT` | 整數（例如 `42`、`-7`） |
| `Types.POS_INT` / `Types.NEG_INT` | 正整數 / 負整數 |
| `Types.FLOAT` | 浮點數（例如 `3.14`）|
| `Types.NUM` | 任何數值 (等價於 `INT \| FLOAT`) |
| `Types.BOOL` | 布林值（例如 `true`, `false`, `yes`, `1`, `0`） |
| `Types.DATE` / `Types.DATE_TW` | 日期格式（支援 YYYY-MM-DD 或 民國年 111/01/01 等） |
| `Types.TIME` / `Types.DATETIME` | 時間與日期時間格式 |
| `Types.MERGED` | 該儲存格原本是從「合併儲存格」擴展而來的 |
| `Types.SPACE` / `Types.BLANK` | 儲存格是全空的，或是只有「空白字元」 |
| `Types.EMPTY` | 經過正規化後，該格子「完全無字串長度」(`""`) |
| `Types.ANY` | 任何值包含空值（萬用比對） |

**進階用法：**
- **自訂正則**：你可以用 `Types.r(r"(?i)^[A-Z]\d{4}$")` 創造出自訂的強力比對條件。
  > [!WARNING]  
  > 由於 `Row` 元件預設會啟用 `normalize=True`，也就是將讀取到的儲存格值全數轉為「小寫」再進行比對。如果你的正則表達式內含大寫字母判定（例如 `[A-Z]`），將會永遠配對失敗！請記得加上不分大小寫的修飾詞 `(?i)`，或是將 `Row` 的 `normalize` 設為 `False`。
- **OR 條件聯集**：你可以使用 `|` 符號結合不同型別。如 `Types.STR | Types.BLANK` 接受字串或者是沒有填東西的格子。
- **連續擴展糖**：你可以使用 `()` 語法將條件重複。例如 `Row(pattern=[Types.ANY(3), Types.INT])` 等價於前面有三個任意值，最後為一個整數。

---

### 第二部分：樣板元件

有了 `Types` 基本條件後，我們便要用它來疊磚塊。所有元件最終都會被包在一個 `Block` 當中。

#### `Row` (資料列)
基礎的比對單位。
```python
Row(
    pattern=["A", Types.INT], # 格子條件陣列 
    repeat=1,                 # 出現次數範圍 (詳見下文 Repeat)
    node_id=None,             # 為該列打上辨識標籤，結果回傳時好判斷對象 
    normalize=True,           # 自動去除字串頭尾空白與轉小寫進行寬鬆判斷
    min_similarity=None,      # (搭配 rapidfuzz) 給定 0~1 的相容字串模糊條件
    match_ratio=None          # (0~1) 例如 0.9，容許列內有 10% 的格子不符合也算過關
)
```

#### `EmptyRow` (空白列)
快速比對全空白的糖衣語法，相當於自己寫了一排 `Types.BLANK`。
```python
EmptyRow(repeat=1, node_id=None, allow_whitespace=True)
```

#### `Group` (群組控制)
將多個節點組合為一個可以反覆出現的群組單元。適合有「固定區段與分隔」的複雜表單。
```python
# 例如：每 N 列資料後會有一列空白列，這樣的群體至少出現一次以上
Group(children=[
    Row(pattern=[Types.MERGED, Types.STR], repeat="+"),
    EmptyRow(repeat=1),
], repeat="+")
```

#### `AltNode`（替代方案選擇）
利用位元或符號 `|` 將不同變化的 `Row` 結合在一起。例如報表格式可能有 A 和 B 兩種表頭設計：
```python
Row(pattern=["A", "B"]) | Row(pattern=["A*", "B*"])
```

#### 關於 `repeat` 規格的寫法：
| 值 | 意義 |
|----|------|
| `1` (預設) | 出現恰好一次 |
| `"?"` | 0 或 1 次 (可選) |
| `"+"` | 1 次以上 (貪婪比對) |
| `"*"` | 0 次以上 (貪婪比對) |
| `(2, 5)` | 指定出現 2 到 5 次 |
| `(3, None)` | 至少 3 次以上 |

---

### 第三部分：處理結果物件

正如前面的範例，我們透過 `match_template` 拿到的 `MatchOutput` 物件包含了各種層級的結果特徵：

#### 1. `MatchOutput` (封裝容器)
```python
results.blocks                                # list[BlockMatch] — 攤平後所有的匹配結果列表
results.get_blocks_by_id("salary_table")      # 幫助過濾特定 block_id 的結果
results.get_blocks_by_sheet("Sheet1")         # 幫助過濾特定 工作表名稱 的結果
```

#### 2. `BlockMatch` (整塊表格)
```python
block.start       # (row, col) — 0-based 左上角起點座標
block.end         # (row, col) — 0-based 右下角終點座標
block.rows        # list[RowMatch] — 所有包含在內的列集合
block.block_id    # string — 最初定義樣板時給的 ID
block.sheet_name  # string — 配對成功所在的工作表名稱
```

#### 3. `RowMatch` (單列資料)
```python
row.row                 # 在工作表中的絕對 0-based 列號
row.cells               # list[CellMatch] — 列內的每一個格子
row.node_id             # string — 最初在樣板 Row/EmptyRow 定義時給的 ID
```

#### 4. `CellMatch` (單一格子)
```python
cell.row                # 絕對 0-based 列號
cell.col                # 絕對 0-based 欄號
cell.value              # 抓取到的字串值
cell.is_merged          # boolean — 此格的擴展是否依賴 Excel 合併儲存格產生
```

---

### 第四部分：進階掃描設定與多樣板掃描

#### 工作表過濾與提早終止掃描
你可以藉由設定 `MatchOptions` 和傳入不同的參數更改搜尋策略。
```python
from rpkbin.excel_extractor import MatchOptions

results = match_template(
    "report.xlsx", 
    template,
    sheet=["Sheet1", "Sheet3"], # 只看這兩張表 (也可給予 0-based 索引整數)
    options=MatchOptions(
        max_matched_sheets=1 # 在找到「第一張」有吻合結果的工作表之後，就直接停止掃描省時
    )
)
```

#### 多樣板同步搜刮
如果你在同一個檔案要找不只一種表，為了省去檔案重複 IO 的時間，你可以餵入多個 Template。
```python
header_block = Block(Row(pattern=["Report", "Date"]), block_id="header")
data_block   = Block(Row(pattern=["Dept", "Name", "Salary"]), block_id="data_table")

results = match_template("report.xlsx", [header_block, data_block])

# 可以直接透過內建的 helper 篩選器，將多個樣板的結果分開處理：
# header_matches = results.get_blocks_by_id("header")
# data_matches   = results.get_blocks_by_id("data_table")
```
