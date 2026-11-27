# TablePack 實戰使用情境

[English](../../en/tablepack/scenarios.md) · [文件導覽](../README.md) · [快速入門](README.md) · [API 參考手冊](reference.md)

以下八個獨立情境從基本有型別欄位開始，包含 schema 推斷、位元組剖析、外部共用 schema、串流解包、欄位調校、壓縮選擇與錯誤判讀。使用 `"auto"` 或 `"brotli"` 的輸出大小為已安裝 `brotli >= 1.2` 的實測結果；`compress="zlib"` 與 `compress=None` 的大小只依賴 Python 標準函式庫即可重現。

## 情境 1：用各種欄位型別描述一張表

打包包含帶號整數、布林值、浮點數與二進位資料的資料列，並完整取回原值。

```python
from rpkbin.tablepack import Schema, Uint, Int, Bool, Float, Bytes, Str, pack, unpack

schema = Schema(
    Str("sensor"),
    Int("temp_c10"),                 # 帶號整數，例如十分之一度
    Bool("valid"),
    Float("volt", bits=32),          # 32 位元浮點數：只接受能被 float32 精確表示的值
    Bytes("raw", nullable=True),     # 任意二進位資料，允許缺值
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

欄位依名稱對應，回傳 dict 的鍵順序與 schema 欄位順序相同。`Uint` 僅接受 `>= 0` 的整數；可能出現負數時請使用 `Int`。完整欄位型別與選項見 [`Column` 與欄位型別](reference.md#column-與欄位型別)。

## 情境 2：讓 `infer` 提出 schema，並存起來

對尚未分析的資料取得第一份建議 schema，檢視 Python 建構程式碼後轉成 JSON 儲存。

```python
from rpkbin.tablepack import Schema, infer, pack, unpack

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "kind": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

schema = infer(rows)             # 僅供參考：投入正式使用前請依實際領域範圍審查
print(schema)

text = schema.to_json()
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

`infer` 先按逐欄 zlib 大小排序候選，再以指定壓縮器與嵌入設定量測最多八個整表方案（逐欄基準一定會納入，大小相同時保留基準）。此處的 `bits=13` 只反映目前樣本最大值；若硬體位址實際上可達 16 位元，請手動改為 `bits=16`。`infer` 永遠不會提出 `Enum`，因為樣本無法證明值集合為封閉集合。

## 情境 3：看看位元組花在哪

用 `explain()` 檢視各欄大小佔比，並列出 `infer(..., report=True)` 評估過的所有候選方案。

```python
from rpkbin.tablepack import explain, infer

rows = [
    {"block": f"blk{i // 8:02d}", "offset": 4 * (i % 8), "width": 1 + i % 4,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 5 else 0xFF}
    for i in range(160)
]
print(explain(rows, compress="zlib"))

schema, report = infer(rows, report=True)
for col, size in report["offset"]:
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

各欄 `bytes` 是單獨打包該欄（含各自標頭與壓縮開銷）的大小，`share` 為相對於這些單欄大小總和的比例。`col.describe()` 顯示精簡編碼摘要（`uint5` 為固定 5 位元寬度，`+pack` 代表存進共用旗標位元）。

## 情境 4：共用同一份 schema，不嵌入 blob

將應用程式版本化的 schema 另行儲存，以產生最小的 blob。

```python
from rpkbin.tablepack import infer, pack, unpack, Schema

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "kind": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

schema = infer(rows, compress="brotli", embed_schema=False)
schema_text = schema.to_json()
small = pack(rows, schema, embed_schema=False, compress="brotli")
print(len(small))

consumer_schema = Schema.from_json(schema_text)
print(unpack(small, consumer_schema) == rows)
```

輸出：
```text
84
True
```

V1 格式不含 schema fingerprint，因此讀取未嵌入 schema 的 blob 時必須自行選用相符版本的外部 schema。若對**已嵌入** schema 的 blob 傳入不同 `schema` 給 `unpack()`，會引發 `ValueError`。

## 情境 5：欄式資料與逐列處理

直接以 `dict[str, sequence]` 打包欄式資料，並透過 `iter_unpack()` 逐列走訪。

```python
from rpkbin.tablepack import pack, unpack, iter_unpack

columns = {"addr": [0x10, 0x14, 0x18], "name": ["ctrl", "status", "data"], "ro": [False, True, True]}
blob = pack(columns)
print(unpack(blob, as_columns=True) == columns)
print(unpack(blob)[0])

for row in iter_unpack(blob):
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

`iter_unpack()` 可省下建立完整 `list[dict]` 的記憶體配置，但仍會在產出第 0 列前完成所有欄位的解碼。引數與解壓驗證會延後到開始疊代時才執行，呼叫 `iter_unpack()` 當下不會立刻觸發錯誤。

## 情境 6：調校欄位並量測效果

依資料分布特性挑選欄位選項，直接以位元組數量測改善幅度。

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
    Category("module"),
    Uint("addr", bits=16, delta=True),
    Uint("msb", bits=4, pack=True),
    Uint("lsb", bits=4, pack=True),
    Enum("access", ["RW", "RO", "W1C"]),
    Uint("reset", bits=8, nullable=True),
)
for label, s in (("naive", naive), ("tuned", tuned)):
    print(label, len(pack(rows, s, compress="zlib", embed_schema=False)))
```

輸出：
```text
naive 405
tuned 171
```

| 資料特徵 | 建議欄位選項 |
| :--- | :--- |
| 計數器、位址、遞增識別碼 | `Uint(..., delta=True)` 或 `Int(..., delta=True)` |
| 與上一列重複的值 | 在 `Str`、`Uint`、`Int`、`Float` 或 `Bytes` 加上 `repeat=True` |
| 少數已知固定值集合 | `Enum([...])` |
| 少數相異值，集合事先未知 | `Category` |
| 窄欄位（少數位元） | `Uint(bits=N, pack=True)` |
| 有明確位寬上限但實值偏小的整數 | `Uint(bits=N, varint=True)` |
| 偶爾缺值（`None`） | `nullable=True` |

當欄位至少有 16 列且相異值超過半數時，`Category` 會發出 `UserWarning`（此時通常改用 `Str` 更合適）。

## 情境 7：選擇壓縮方式並在建置時驗證

比較各種壓縮演算法，並在建置腳本中啟用 `verify=True` 以確認資料能無誤往返。

```python
from rpkbin.tablepack import Schema, Uint, Str, pack, unpack

schema = Schema(Str("k"), Uint("v"))
rows = [{"k": f"key{i % 7}", "v": i * i} for i in range(300)]
for c in ("auto", "brotli", "zlib", "lzma", None):
    print(f"{str(c):7s}", len(pack(rows, schema, compress=c)))
blob = pack(rows, schema, compress="zlib", verify=True)
assert unpack(blob) == rows
```

輸出（已安裝 `brotli >= 1.2`）：
```text
auto    765
brotli  765
zlib    808
lzma    829
None    2335
```

沒有相容 brotli 時，`"auto"` 會改用 `zlib`，而 `compress="brotli"` 會引發 `ImportError`。`verify=True` 會多執行一次解碼比對，若任一欄位不符便引發 `ValueError("verify failed: ...")`。

## 情境 8：看懂並修正錯誤

檢視 schema 與資料驗證所回報的實際例外型別與訊息格式。

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

列號從 0 開始。型別不符（例如對 `Uint` 傳入 `True` 或對 `Int` 傳入 `str`）會引發帶有相同 `row i, column '...'` 前綴的 `TypeError`；數值越界或不合法則引發 `ValueError`。若問題出在 schema 定義（例如 `Uint("a", pack=True)` 未指定 `bits`），建立 `Schema(...)` 時就會立刻引發錯誤。

## 延伸閱讀

- [快速入門](README.md)
- [API 參考手冊](reference.md)
- [文件導覽](../README.md)
