# TablePack 使用情境

[English](../../en/tablepack/scenarios.md) · [文件導覽](../README.md) · [快速入門](README.md) · [API 參考](reference.md)

情境由最簡單的單一操作，逐步進展到組合與調校。**除非文中另有說明，每個程式碼區塊都是獨立的，可以單獨執行。** 輸出中的大小僅為範例值：嵌入 schema 與 `compress="zlib"` 的數字可用標準庫重現；使用預設壓縮方式的數字，則取決於是否安裝 brotli。

---

## 情境 1：用各種欄位型別描述一張表

**任務：** 打包含有帶號數、布林、浮點數與二進位資料的資料列，並取回完全相同的值。

```python
from rpkbin.tablepack import Schema, Uint, Int, Bool, Float, Bytes, Str, pack, unpack

schema = Schema(
    Str("sensor"),
    Int("temp_c10"),                 # 帶號整數，例如十分之一度
    Bool("valid"),
    Float("volt", bits=32),          # 32 位元浮點數：只接受能被 float32 精確表示的值
    Bytes("raw", nullable=True),     # 任意二進位內容，可以缺值
)
rows = [
    {"sensor": "s1", "temp_c10": -125, "valid": True, "volt": 3.25, "raw": b"\x01\x02"},
    {"sensor": "s2", "temp_c10": 300, "valid": False, "volt": 0.5, "raw": None},
]
blob = pack(rows, schema)
back = unpack(blob)
assert back == rows
print(back[0])
```

輸出：

```text
{'sensor': 's1', 'temp_c10': -125, 'valid': True, 'volt': 3.25, 'raw': b'\x01\x02'}
```

欄位以名稱對應，回傳 dict 的鍵順序就是 schema 的欄位順序。`Uint` 用於 `>= 0` 的值，可能出現負數時請用 `Int`。完整的型別與選項清單見[參考手冊](reference.md)。

**下一步：** 你不必手動挑選選項，見情境 2。

---

## 情境 2：讓 `infer` 提出 schema，並存起來

**任務：** 對還沒分析過的資料取得第一份 schema，檢視後以 JSON 文字保存。

```python
from rpkbin.tablepack import Schema, infer, pack, unpack

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "kind": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

schema = infer(rows)             # 僅供參考：請自行檢視，它不知道你的資料代表什麼
print(schema)

text = schema.to_json()          # 純 JSON 文字：想存在哪裡都可以
assert Schema.from_json(text) == schema
assert unpack(pack(rows, schema)) == rows
print(len(text), "characters of JSON")
```

輸出：

```text
Schema(
    Str('module', repeat=True),
    Uint('addr', bits=13, delta=True),
    Str('kind'),
    Uint('reset', nullable=True, bits=4, delta=True),
)
209 characters of JSON
```

`infer` 先按各欄位單獨使用 zlib 的大小排列候選，再以指定的壓縮器和 schema 嵌入設定量測最多八個整表方案。有限搜尋可能選出與逐欄第一選項不同的 schema；它只提供建議，不保證全域最小值。逐欄基準一定會納入，大小相同時優先保留基準。`bits=13` 只符合目前資料的最大值，之後若出現更大的位址便會報錯。如果某欄的範圍是硬體事實，請自己寫上寬度。`infer` 不會提出 `Enum`，因為它無法得知值集合是否封閉；它可能提出 `Category`（從資料學出值集合）或單純的 `Str`。

`print(schema)` 輸出的是 Python 程式碼，可以直接貼進你的腳本再修改。

---

## 情境 3：看看位元組花在哪

**任務：** 找出哪些欄位佔了大部分空間，並比較 `infer` 考慮過的各種方案。

```python
from rpkbin.tablepack import explain, infer

rows = [
    {"block": f"blk{i // 8:02d}", "offset": 4 * (i % 8), "width": 1 + i % 4,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 5 else 0xFF}
    for i in range(160)
]
print(explain(rows, compress="zlib"))

schema, report = infer(rows, report=True)
for col, size in report["offset"]:           # 單一欄位的所有候選方案，由小到大
    print(f"{size:4d} B  {col.describe()}")
```

輸出：

```text
column  encoding               bytes  share
block   str                       69    40%
offset  uint+delta                23    13%
width   uint                      22    13%
access  str                       31    18%
reset   uint8+pack+nullable       24    14%
total: 160 rows -> 110 bytes (compress=zlib, schema not embedded)
  18 B  uint+delta
  21 B  uint
  21 B  uint5
  21 B  uint5+pack
  31 B  category
```

每個欄位的 `bytes` 數字來自把該欄位單獨打包，所以這些數字是參考，不是精確拆分：欄位之間共用旗標位元，而且每個單獨的 blob 都帶有自己的標頭與壓縮額外開銷，因此這裡的總量比各欄位加總還小。`share` 是相對於該加總值的比例。報告仍是逐欄 zlib 排名；`infer` 最後選 schema 時會量測整張表。

`describe()` 會顯示欄位的編碼方式（`uint5` 表示固定 5 位元寬度，`+pack` 表示存放在共用旗標位元中）。

---

## 情境 4：共用同一份 schema，不嵌入 blob

**任務：** 一支腳本負責產生 blob，另一支負責讀取；希望 blob 越小越好。

```python
from rpkbin.tablepack import infer, pack, unpack, Schema

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "kind": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

# 產生端：依目標壓縮器推斷 schema，且不嵌入每個 blob。
schema = infer(rows, compress="brotli", embed_schema=False)
schema_text = schema.to_json()       # 作為由應用程式版本管理的檔案保存
small = pack(rows, schema, embed_schema=False, compress="brotli")
print(len(small))

# 讀取端：載入與產生端應用程式版本相符的 schema 檔案。
consumer_schema = Schema.from_json(schema_text)
print(unpack(small, consumer_schema) == rows)
```

輸出：

```text
84
True
```

產生端會以目標壓縮器量測有限數量的整表候選，並在不嵌入 schema 的情況下選擇一份 schema，再另外保存。讀取端必須使用相同應用程式版本管理的 schema。V1 沒有 schema fingerprint，因此無法保證偵測到外部 schema 版本不符。預設仍會嵌入 schema，讓 blob 可自我描述。

若對**已經嵌入** schema 的 blob 再傳入 schema 給 `unpack`，兩者必須相等，否則會引發 `ValueError`。

---

## 情境 5：欄式資料與逐列處理

**任務：** 你的資料本來就是每欄一個 list，或想一次處理一列。

```python
from rpkbin.tablepack import pack, unpack, iter_unpack

columns = {"addr": [0x10, 0x14, 0x18], "name": ["ctrl", "status", "data"], "ro": [False, True, True]}
blob = pack(columns)                       # 等長序列組成的 dict
print(unpack(blob, as_columns=True) == columns)
print(unpack(blob)[0])                     # 預設回傳列 dict 的 list

for row in iter_unpack(blob):              # 一次一個 dict，而不是完整的 list
    print(row["name"], hex(row["addr"]))
```

輸出：

```text
True
{'addr': 16, 'name': 'ctrl', 'ro': False}
ctrl 0x10
status 0x14
data 0x18
```

`iter_unpack` 省下的是建立完整 `list[dict]` 的成本；blob 仍會先在限制內解壓縮，並於產生第一列前逐欄解碼。Reader 限制會約束這些工作，但不是作業系統層級的記憶體上限。

---

## 情境 6：調校欄位並量測效果

**任務：** 選擇更好的欄位選項來縮小表格，並用數字驗證成效。

```python
from rpkbin.tablepack import Schema, Uint, Enum, Category, Str, pack

rows = [
    {"module": "dma" if i < 100 else "gpio", "addr": 0x2000 + 4 * i, "msb": i % 16, "lsb": 0,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 3 else 0}
    for i in range(200)
]

naive = Schema(Str("module"), Uint("addr", bits=16), Uint("msb", bits=8), Uint("lsb", bits=8),
               Str("access"), Uint("reset", bits=8, nullable=True))
tuned = Schema(
    Category("module"),                         # 值集合從資料中學出
    Uint("addr", bits=16, delta=True),          # 儲存步進量，而不是位址
    Uint("msb", bits=4, pack=True),             # 很小的欄位：放進共用旗標位元
    Uint("lsb", bits=4, pack=True),
    Enum("access", ["RW", "RO", "W1C"]),        # 事先已知：不需要儲存字典
    Uint("reset", bits=8, nullable=True),       # None 只花 1 bit，不存內容
)
for label, s in (("naive", naive), ("tuned", tuned)):
    print(label, len(pack(rows, s, compress="zlib", embed_schema=False)))
```

輸出：

```text
naive 405
tuned 171
```

經驗法則（上面的數字來自實際的 `pack`，不是猜測）：

| 資料特徵 | 使用 |
| :--- | :--- |
| 計數器、位址、已排序的 ID | `Uint(..., delta=True)` 或 `Int(..., delta=True)` |
| 與上一列相同的值（合併儲存格、分組資料） | 在 `Str`/`Uint`/`Int`/`Float`/`Bytes` 加上 `repeat=True` |
| 只有少數幾種字串，且值集合事先已知 | `Enum([...])` |
| 只有少數幾種值，但值集合事先未知 | `Category` |
| 很窄的欄位（只有幾個 bit） | `Uint(bits=N, pack=True)` |
| 有時會缺值 | `nullable=True`（1 個旗標位元；缺值時不儲存內容） |

當欄位至少有 16 列，且相異值超過列數的一半時，`Category` 會發出 `UserWarning`；這通常代表改用 `Str` 比較合適。

---

## 情境 7：選擇壓縮方式並在建置時驗證

**任務：** 比較各種壓縮方式，並讓建置腳本在表格無法正確還原時明確失敗。

```python
from rpkbin.tablepack import Schema, Uint, Str, pack, unpack

schema = Schema(Str("k"), Uint("v"))
rows = [{"k": f"key{i % 7}", "v": i * i} for i in range(300)]
for c in ("auto", "brotli", "zlib", "lzma", None):
    print(f"{str(c):7s}", len(pack(rows, schema, compress=c)))
blob = pack(rows, schema, compress="zlib", verify=True)   # 回傳前先解碼並比對
assert unpack(blob) == rows                               # 壓縮方式從標頭讀取
```

已安裝相容 brotli >= 1.2（`pip install -e ".[table]"`）時的輸出；此處 `auto` 使用 brotli，實際大小取決於各壓縮器版本：

```text
auto    765
brotli  765
zlib    808
lzma    829
None    2335
```

沒有相容 brotli 時，`"auto"` 會使用 zlib，而 `compress="brotli"` 會引發 `ImportError` 並提示升級或安裝套件。解碼 brotli blob 的讀取端也需要相容 brotli。

`verify=True` 在解碼結果與輸入不同時會引發 `ValueError("verify failed: ...")`。它多花一次解碼的時間，在建置腳本中可以接受。

---

## 情境 8：看懂並修正錯誤

**任務：** 理解驗證錯誤訊息的意思。

```python
from rpkbin.tablepack import Schema, Uint, Enum, Float, pack

s = Schema(Uint("addr", bits=12), Enum("kind", ["RW", "RO"]))
cases = [
    ([{"addr": 4095, "kind": "RW"}, {"addr": 4096, "kind": "RW"}], s),
    ([{"addr": 1, "kind": "XX"}], s),
    ([{"addr": 1}], s),
    ([{"addr": 1, "kind": "RW", "extra": 0}], s),
    ([{"v": 0.1}], Schema(Float("v", bits=32))),
]
for rows, schema in cases:
    try:
        pack(rows, schema)
    except (ValueError, TypeError) as e:
        print(type(e).__name__ + ":", e)
```

輸出：

```text
ValueError: row 1, column 'addr': 4096 doesn't fit in 12 bits
ValueError: row 0, column 'kind': unknown value 'XX'; known values: ['RW', 'RO']
ValueError: row 0: missing column 'kind'
ValueError: row 0: unexpected column(s) ['extra']
ValueError: row 0, column 'v': 0.1 isn't exactly representable as float32
```

列號從 0 開始。*型別*錯誤（例如對 `Uint` 傳入 `True`，或對 `Int` 傳入 `str`）會引發 `TypeError`，並帶有相同的「列／欄」前綴；數值問題則引發 `ValueError`。若問題出在 schema 本身而非資料（例如 `Uint("a", pack=True)` 沒給 `bits`），錯誤會在建立 `Schema(...)` 時就引發。

---

## 下一步

- **[API 參考手冊](reference.md)**：精確的函式簽章、預設值、可接受的值型別、例外與 blob 格式。
