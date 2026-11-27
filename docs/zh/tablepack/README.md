# `rpkbin.tablepack`

[English](../../en/tablepack/README.md) · [文件導覽](../README.md) · [實戰使用情境](scenarios.md) · [API 參考手冊](reference.md)

`rpkbin.tablepack` 依據有型別的欄位 schema，將資料列（`list[dict]`）或欄式序列（`dict[str, sequence]`）編碼為緊湊的 `bytes` blob，並完整還原原始數值。

TablePack 是純函式庫，不進行任何檔案 I/O：你傳入資料列或欄式序列，函式庫回傳 `bytes`。

## 安裝方式

```bash
# 核心安裝：使用 Python 標準函式庫的 zlib 壓縮
pip install -e .

# 建議：加入 brotli >= 1.2，啟用 TablePack 的有界輸出解碼器
pip install -e ".[table]"
```

沒有 `brotli >= 1.2` 時，預設的 `compress="auto"` 會自動改用 `zlib`。明確指定 `compress="brotli"` 需要 `brotli >= 1.2`。選擇的壓縮器會記錄在 blob 標頭中，因此解碼 brotli blob 的環境也必須安裝 `brotli >= 1.2`。

## 語法

```python
blob = pack(data, schema=None, *, embed_schema=True, compress="auto", verify=False)
rows = unpack(blob, schema=None, *, as_columns=False, limits=DecodeLimits())
row_iter = iter_unpack(blob, schema=None, *, limits=DecodeLimits())
schema = infer(data, *, report=False, compress="auto", embed_schema=True, optimize=True)
summary = explain(data, schema=None, *, compress="auto")
```

## 說明

- `blob = pack(data, schema=None, ...)` 將資料列或等長欄式序列編碼為確定性位元組；省略 `schema` 時會透過 `infer` 自動推斷並嵌入 schema。
- `rows = unpack(blob, schema=None, ...)` 將 `bytes`、`bytearray` 或 `memoryview` blob 還原為 `list[dict]`（或於 `as_columns=True` 時回傳 `dict[str, list]`）。
- `row_iter = iter_unpack(blob, schema=None, ...)` 回傳一個生成器，在開始疊代時才驗證引數與 blob 並逐欄解碼，之後一次產生一列 dict。
- `schema = infer(data, ...)` 先排序逐欄 zlib 大小，再以目標壓縮器量測最多八個整表候選方案並提出 `Schema`。
- `summary = explain(data, schema=None, ...)` 回傳包含各欄編碼名稱、單獨打包位元組數與整表總大小的文字表格。

## 範例

### 1. 基本範例：不給 schema 直接打包與解包

不指定 schema 直接打包 200 列類似暫存器表的資料；TablePack 會自動推斷並把 schema 嵌入 blob。

```python
import json
from rpkbin.tablepack import pack, unpack

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

blob = pack(rows)
assert unpack(blob) == rows

as_json = json.dumps(rows).encode()
print(f"JSON {len(as_json)} bytes -> blob {len(blob)} bytes")
print(unpack(blob)[1])
```

輸出（已安裝 `brotli >= 1.2`；使用 `zlib` 時 blob 大小略有不同）：
```text
JSON 12596 bytes -> blob 183 bytes
{'module': 'uart', 'addr': 4100, 'access': 'RO', 'reset': 1}
```

### 2. 進階範例：明確指定 Schema、`infer` 與 `explain`

使用 `Schema` 明確指定欄位編碼，並透過 `explain()` 與 `infer()` 檢視空間佔比與建議編碼。

```python
from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack, infer, explain

rows = [
    {"module": "uart", "addr": 0x10, "kind": "RW", "note": None},
    {"module": "uart", "addr": 0x14, "kind": "RO", "note": "status"},
    {"module": "spi", "addr": 0x40, "kind": "W1C", "note": None},
]

schema = Schema(
    Str("module", repeat=True),
    Uint("addr", bits=16, delta=True),
    Enum("kind", ["RW", "RO", "W1C"]),
    Str("note", nullable=True),
)

blob = pack(rows, schema)
assert unpack(blob) == rows
print("Row 1:", unpack(blob)[1])
print("Inferred:", repr(infer(rows)))
print(explain(rows, schema, compress="zlib"))
```

輸出：
```text
Row 1: {'module': 'uart', 'addr': 20, 'kind': 'RO', 'note': 'status'}
Inferred: Schema(
    Str('module'),
    Uint('addr'),
    Str('kind'),
    Str('note', nullable=True),
)
column  encoding        bytes  share
module  str+repeat         27    30%
addr    uint+delta         18    20%
kind    enum(3)            18    20%
note    str+nullable       25    28%
total: 3 rows -> 37 bytes (compress=zlib, schema not embedded)
```

## 注意事項與限制

- **輸入形式**：`pack()` 接受 `list[dict]`（任意 mapping 的 iterable）或 `dict[str, sequence]`（等長欄式序列）。`str`、`bytes`、`bytearray` 與 `memoryview` 不可作為欄容器（會引發 `TypeError`），但 `bytes`、`bytearray` 與 `memoryview` 可作為 `Bytes` 欄位的儲存格值。每一列必須剛好包含 schema 的所有欄位。
- **嵌入 schema 與最小輸出**：預設 `embed_schema=True` 會將 schema JSON 存進 blob，讓 `unpack(blob)` 不需額外資訊。若需最小輸出，請設為 `embed_schema=False` 並在 `unpack(blob, schema)` 傳入相符 schema。見[情境 4](scenarios.md#情境-4共用同一份-schema不嵌入-blob)。
- **嚴格型別檢查**：數值會檢查而不自動轉型（`Uint` 與 `Int` 拒絕 `True`、`Int` 拒絕 `"7"`、`Uint` 拒絕負數）。錯誤訊息會標明列號與欄名。見[情境 8](scenarios.md#情境-8看懂並修正錯誤)。
- **解碼限制**：讀取端會依 `DecodeLimits` 限制列數、儲存格數、payload 位元組數、schema 位元組數、欄數及整數 varint 長度，用來約束解析放大效應（不是作業系統層級的記憶體上限）。見 [`DecodeLimits`](reference.md#decodelimits)。
- **相容性**：需要 Python `>= 3.11`（`pyproject.toml`）。純 Python 且無網路呼叫；範例已在 Windows、Python 3.13 實測驗證。

## 延伸閱讀

- [實戰使用情境](scenarios.md)
- [API 參考手冊](reference.md)
- [文件導覽](../README.md)
