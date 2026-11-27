# `rpkbin.tablepack` API 參考手冊

[English](../../en/tablepack/reference.md) · [文件導覽](../README.md) · [快速入門](README.md) · [實戰使用情境](scenarios.md)

本文件說明 `rpkbin.tablepack` 的技術規格，包含編解碼函式、`DecodeLimits`、`Schema`、`Column` 欄位型別、例外規格與 V1 二進位格式。

```python
from rpkbin.tablepack import (
    Schema, Column, Uint, Int, Bool, Float, Bytes, Str, Enum, Category, DecodeLimits,
    pack, unpack, iter_unpack, infer, explain,
)
```

`__all__` 匯出上述 16 個名稱。需要 Python 3.11 或更新版本。`brotli >= 1.2` 為選配（`pip install -e ".[table]"`），只有在偵測到 `brotli.Decompressor.can_accept_more_data` 時才會使用，否則 `"auto"` 會改用 `zlib`。以 `_` 開頭的私有模組屬於內部實作。

## 函式

### `pack`

#### 語法
```python
blob = pack(data, schema=None, *, embed_schema=True, compress="auto", verify=False)
```

#### 說明
將表格編碼為確定性的 V1 `bytes`。只要輸入資料、schema、選項與壓縮器版本相同，輸出位元組完全一致。

#### 輸入引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `data` | `list[dict] \| Iterable[Mapping] \| dict[str, sequence]` | 必填 | 每列一個 mapping，或每欄一個等長序列。`str`、`bytes`、`bytearray` 與 `memoryview` 不可作為欄序列（引發 `TypeError`）；若要打包二進位資料，請放入序列中（例如 `[bytearray(b"ab")]`）搭配 `Bytes` 欄位。 |
| `schema` | `Schema \| None` | `None` | 欄位定義。省略時由 `infer` 自動推斷並嵌入（此時至少須有一列一欄，且 `embed_schema` 必須為 `True`）。 |

#### 具名引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `embed_schema` | `bool` | `True` | 將 schema JSON 寫入 blob，讓 `unpack(blob)` 不需另傳 schema。 |
| `compress` | `str \| None` | `"auto"` | 寫入標頭的壓縮方式：`"auto"`（有 `brotli >= 1.2` 時用 brotli，否則用 `zlib`）、`"brotli"`、`"zlib"`、`"lzma"` 或 `None`。 |
| `verify` | `bool` | `False` | 打包後立刻解碼並比對所有欄位（不符時引發 `ValueError`）。 |

#### 輸出
回傳 `bytes`。

#### 例外
- `ValueError`：缺少或多出欄位、欄長度不一致、無 schema 時傳入空表格（`[]`、`[{}]`、`{}`、`{"a": []}`）、無 schema 時設定 `embed_schema=False`、未知的 `compress` 值、數值越界、非 nullable 欄位出現 `None`、未知的 `Enum` 值、無法精確表示的浮點數、`Str`/`Category` 出現無效 UTF-8 surrogate（附帶 `row i, column '...':` 前綴），或 `verify=True` 往返驗證失敗。
- `TypeError`：儲存格值的型別不符（例如對 `Uint`/`Int`/`Float`/`Enum`/`Category` 傳入 `bool`）、`data` 不可疊代或列不是 mapping、欄不是序列（含 `str`、`bytes`、`bytearray`、`memoryview`），或 `Category` 欄位混用 `str` 與 `int`。
- `AttributeError`：`schema` 不是 `None` 也不是 `Schema` 實例（`unpack` 與 `iter_unpack` 則會以 `TypeError` 驗證 `schema`）。
- `ImportError`：指定 `compress="brotli"` 但系統沒有可用的 `brotli >= 1.2`（須支援有界輸出解碼）。

### `unpack`

#### 語法
```python
rows = unpack(blob, schema=None, *, as_columns=False, limits=DecodeLimits())
```

#### 說明
將 TablePack V1 blob 解碼為資料列（`list[dict]`）或欄式字典（`dict[str, list]`）。

#### 輸入引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `blob` | `bytes \| bytearray \| memoryview` | 必填 | 由 `pack` 產生的 bytes-like V1 blob。 |
| `schema` | `Schema \| None` | `None` | 當 blob 未嵌入 schema 時必填；若 blob 已嵌入 schema 且呼叫端又傳入 `schema`，兩者必須相等。 |

#### 具名引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `as_columns` | `bool` | `False` | 設為 `True` 時回傳 `{欄名: list}` 而非 `list[dict]`。 |
| `limits` | `DecodeLimits` | `DecodeLimits()` | 限制解壓後 payload、metadata、列數、儲存格數、欄數與整數 varint 的讀取預算。 |

#### 輸出
依原列順序回傳 `list[dict[str, Any]]`（鍵順序同 schema），或於 `as_columns=True` 時回傳 `dict[str, list[Any]]`。還原值型別為 `int`、`float`、`bool`、`str`、`bytes` 或 `None`（以 `Bytes` 打包的 `bytearray`/`memoryview` 會還原為 `bytes`）。

#### 例外
- `TypeError`：`blob` 不是 `bytes`、`bytearray` 或 `memoryview`（於呼叫 `bytes(blob)` 配置記憶體前即檢查，例如 `unpack(1_000_000_000)`）；`schema` 不是 `None` 或 `Schema`；`limits` 不是 `DecodeLimits` 實例。
- `ValueError`：magic bytes 錯誤（非 `b"TPK"`）、不支援的格式版本、資料損毀或截斷（`Corrupt or truncated tablepack blob`）、超出任一 `DecodeLimits` 上限（以損毀前綴回報）、裸 blob 缺少 `schema`，或傳入的 `schema` 與嵌入的不符。
- `ImportError`：blob 使用 `brotli` 壓縮但沒有相容的 `brotli >= 1.2`。

### `iter_unpack`

#### 語法
```python
row_iter = iter_unpack(blob, schema=None, *, limits=DecodeLimits())
```

#### 說明
`unpack` 的生成器版本，每次產出一列 dict。

由於 `iter_unpack` 是生成器函式，呼叫 `iter_unpack(bad_blob)` 會立刻回傳 generator 物件，並將**引數型別檢查、解壓縮、標頭解析與 `DecodeLimits` 驗證延後到開始疊代時**（例如第一次呼叫 `next()`、`for` 迴圈或 `list()`）才執行。開始疊代後，系統仍會在產出第 0 列之前先逐欄解碼整個 blob，因此省下的是建立完整 `list[dict]` 的成本，而非已解碼的欄位清單。

#### 輸入引數
與 `unpack` 相同（`blob`、`schema` 及僅限關鍵字傳入的 `limits`）。

#### 輸出
回傳 `Iterator[dict[str, Any]]`。開始疊代時會引發與 `unpack` 相同的 `TypeError`、`ValueError` 與 `ImportError`。

### `infer`

#### 語法
```python
schema = infer(data, *, report=False, compress="auto", embed_schema=True, optimize=True)
schema, report = infer(data, report=True, ...)
```

#### 說明
針對 `data` 提出建議的 `Schema`。先以獨立 zlib 等級 9 壓縮大小排列每欄候選編碼；當 `optimize=True`（預設）時，再以指定的 `compress` 與 `embed_schema` 量測最多八個整表候選方案（逐欄基準一定會納入，大小相同時優先保留基準）。即使整表評分選出其他方案，`report` 仍維持逐欄 zlib 排名。`infer` 僅供參考，且永遠不會提出 `Enum`。

#### 輸入引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `data` | `list[dict] \| Iterable[Mapping] \| dict[str, sequence]` | 必填 | 非空表格（至少一列一欄）。 |

#### 具名引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `report` | `bool` | `False` | 設為 `True` 時回傳 `(schema, report)`，其中 `report[name]` 為依 zlib 位元組數由小到大排列的 `[(Column, size), ...]`。 |
| `compress` | `str \| None` | `"auto"` | 整表評分使用的壓縮器。 |
| `embed_schema` | `bool` | `True` | 評分整表候選方案時是否納入嵌入 schema JSON。 |
| `optimize` | `bool` | `True` | 設為 `False` 時直接回傳逐欄 zlib 基準，不進行整表量測。 |

依非空值型別產生的候選編碼（有 `None` 時自動設 `nullable=True`；相鄰列相同比例 `> 10%` 時嘗試 `repeat=True`；相異值數 `<= 60%` 非空值數時納入 `Category`）：

| 非空值型別 | 評估候選 |
| :--- | :--- |
| 全為 `bool`（或 NumPy bool） | `Bool` |
| 非負整數（`__index__`） | `Uint`（varint、固定 `bits=N`、`delta=True`，當 `bits <= 16` 且 `not repeat` 時含 `pack=True`），低基數另含 `Category` |
| 含負整數 | `Int`（一般、`delta=True`），低基數另含 `Category` |
| `numbers.Real`（整數與浮點數，不含 `bool`） | `Float`（64 位元、32 位元） |
| 全為 `str` | `Str`，低基數另含 `Category` |
| 全為 `bytes` / `bytearray` / `memoryview` | `Bytes` |
| 全為 `None` | `Str(nullable=True)` |

#### 輸出
回傳 `Schema`，或於 `report=True` 時回傳 `(Schema, dict[str, list[tuple[Column, int]]])`。空表格（`[]`、`[{}]`、`{}`、`{"a": []}`）或無可用候選時引發 `ValueError`；混用或不支援的值型別／欄容器引發 `TypeError`。

### `explain`

#### 語法
```python
text = explain(data, schema=None, *, compress="auto")
```

#### 說明
回傳文字表格，顯示各欄的 `describe()` 編碼名稱、單獨打包位元組數（`embed_schema=False`）、佔單欄加總的比例，以及整表實際大小。因每個單獨打包的 blob 都包含各自的標頭與壓縮開銷，各欄大小加總不等於整表總計。省略 `schema` 時會沿用同一份正規化資料，以目標 `compress` 與 `embed_schema=False` 執行 `infer`。

#### 輸入引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `data` | `list[dict] \| Iterable[Mapping] \| dict[str, sequence]` | 必填 | 表格資料（若傳入明確 `schema` 可為空表）。 |
| `schema` | `Schema \| None` | `None` | 選填的 `Schema`；省略時自動推斷（需非空資料）。 |

#### 具名引數
| 名稱 | 型別 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `compress` | `str \| None` | `"auto"` | 量測單欄與整表大小的壓縮器。 |

#### 輸出
回傳格式化 `str`。錯誤條件與 `pack` 及 `infer` 相同。

## 類別

### `DecodeLimits`

控制讀取端資源預算的 frozen dataclass（不寫入 V1 二進位格式）。

#### 建立方式
```python
limits = DecodeLimits(
    max_rows=1_000_000,
    max_cells=10_000_000,
    max_payload_bytes=64 * 1024 * 1024,
    max_schema_bytes=1024 * 1024,
    max_columns=4096,
    max_integer_bytes=8192,
)
```

每個欄位都必須是正的純整數（`type(v) is int and v >= 1`）；傳入 `0`、負數、`bool`、`float` 或 `None` 會引發 `ValueError`。

#### 屬性
| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `max_rows` | `int` | 預設 `1_000_000`。宣告列數上限。 |
| `max_cells` | `int` | 預設 `10_000_000`。解碼儲存格數上限（`rows * columns`）。 |
| `max_payload_bytes` | `int` | 預設 `67_108_864`（64 MiB）。解壓後 payload 位元組上限。 |
| `max_schema_bytes` | `int` | 預設 `1_048_576`（1 MiB）。嵌入 schema JSON 位元組上限。 |
| `max_columns` | `int` | 預設 `4096`。schema 欄數上限。 |
| `max_integer_bytes` | `int` | 預設 `8192`。整數 varint 位元組上限。 |

#### 注意事項與限制
- 超出預算時會引發帶 `Corrupt or truncated tablepack blob` 前綴的 `ValueError`。
- 讀取端會先執行有界解壓，再檢查 metadata、列數／儲存格數、旗標位元、數值範圍與 EOF。LZMA 解碼另設固定 128 MiB 解碼記憶體上限（V1 寫入端使用的 64 MiB 字典仍可正常讀取）。
- 這些預算用來抑制解析放大效應，不是作業系統層級的記憶體上限。

### `Schema`

由名稱唯一的 `Column` 物件組成的不可變有序集合（至少一個欄位）。

#### 建立方式
```python
schema = Schema(*columns)
schema = Schema([col1, col2, ...])
schema = Schema.from_dict(d)
schema = Schema.from_json(text)
```

#### 屬性
| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `columns` | `tuple[Column, ...]` | 依序排列的 `Column` tuple。 |
| `names` | `list[str]` | 依 schema 順序排列的欄名 list。 |

#### 物件函式
| 方法／運算子 | 用途 |
| :--- | :--- |
| `len(schema)`、`iter(schema)` | 回傳欄數或依序走訪欄位。 |
| `schema[name]` | 依欄名取得 `Column`（找不到時引發 `KeyError`）。 |
| `name in schema`、`col in schema` | 成員測試（`__contains__`），支援 `str` 欄名與 `Column` 物件比對（其他型別回傳 `False`）。 |
| `s1 == s2`、`hash(schema)` | 依 `.columns` tuple 比較相等與雜湊。 |
| `repr(schema)` | 輸出可重建此 schema 的 Python `Schema(...)` 程式碼（只列出非預設選項）。 |
| `schema.to_dict()`、`Schema.from_dict(d)` | 轉換為或重建自 `{"columns": [{"type": ..., "name": ..., ...}]}`。 |
| `schema.to_json(indent=None)`、`Schema.from_json(text)` | 序列化或反序列化為 UTF-8 JSON 文字（`indent=None` 使用緊湊分隔符 `(",", ":")`）。 |

無欄位、欄名重複、未知 `type` 或 `from_dict`/`from_json` 選項錯誤會引發 `ValueError`；傳入非 `Column` 物件給 `Schema(...)` 引發 `TypeError`。

### `Column` 與欄位型別

`Column` 是所有欄位型別的不可變基底 dataclass。`name`（非空 `str`）為第一個位置引數；除了 `Enum.values` 外，其餘選項皆為 keyword-only。布林選項（`nullable`、`repeat`、`varint`、`delta`、`pack`）嚴格要求 `type(v) is bool`（否則引發 `ValueError`）。無效組合會在建立物件時立刻引發 `ValueError`。

#### `Column` 屬性
| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `name` | `str` | 非空欄位名稱。 |
| `nullable` | `bool` | 預設 `False`。允許 `None`（每列多花 1 個共用旗標位元；缺值不寫入資料）。 |
| `repeat` | `bool` | 預設 `False`。當本列與上一列相同時不重複儲存（每列多花 1 個共用旗標位元）。`Bool`、`Enum`、`Category` 與 `Uint(pack=True)` 禁止啟用（`ValueError`）。 |

#### `Column` 物件函式
| 方法 | 用途 |
| :--- | :--- |
| `col.describe()` | 回傳精簡編碼摘要 `<base>[+delta][+pack][+repeat][+nullable]`。對 `Uint` 而言，僅在指定 `bits` 且 `varint` 與 `delta` 皆為 `False` 時 `<base>` 為 `f"uint{bits}"`，否則為 `"uint"`（因此 `Uint("a", bits=12, varint=True).describe() == "uint"`，`Uint("a", bits=12, delta=True).describe() == "uint+delta"`）。其他基底名稱為 `"int"`、`"bool"`、`f"float{bits}"`、`"bytes"`、`"str"`、`f"enum({len(values)})"` 與 `"category"`。 |
| `col.to_dict()` | 回傳 `{"type": TYPE, "name": name, ...}`，僅輸出與預設值不同的欄位（`Enum` 會以 `list` 輸出 `"values"`）。 |
| `repr(col)` | 輸出建構式形式的 Python 字串，省略預設選項（保留 `Enum.values`）。 |

#### 欄位子類別
| 型別 | 選項（`nullable`、`repeat` 之外） | 可接受的輸入值 | 儲存方式 |
| :--- | :--- | :--- | :--- |
| `Uint(name)` | `bits=None`、`varint=False`、`delta=False`、`pack=False` | 非負 `__index__` 整數（拒絕 `bool`，引發 `TypeError`） | 預設（`bits=None`）：無號 LEB128 varint。`bits=N`（純整數 `>= 1`）：編碼與解碼時一律檢查 `0 <= v < 2**N`；當 `varint=False`、`delta=False`、`pack=False` 時以 `ceil(N / 8)` 個 big-endian 位元組儲存。`varint=True`：保留 `0 <= v < 2**N` 範圍檢查，但改以無號 LEB128 varint 儲存而非固定位元組（`describe()` 回傳 `"uint"`）。`delta=True`：儲存與前一個非空值之差的 zigzag varint（起始基準為 `0`；會覆寫固定位寬／varint 儲存並保留 `bits` 檢查）。`pack=True`：必須搭配 `bits`、不可與 `repeat`/`delta`/`varint` 合用，直接存進每列共用旗標整數的 `N` 個位元。 |
| `Int(name)` | `delta=False` | 任意 `__index__` 整數（拒絕 `bool`） | 數值的 zigzag varint；`delta=True` 時為與前一個非空值之差的 zigzag varint。 |
| `Bool(name)` | 無（禁止 `repeat`） | `bool` 與 NumPy 布林純量 | 每列 1 個共用旗標位元。 |
| `Float(name)` | `bits=64` | `numbers.Real`（`int`、`float`、`Fraction`、NumPy float；拒絕 `bool`）；解碼為 `float` | Big-endian IEEE 754。整數與實數必須能無損轉成 float64。`bits=32` 會拒絕往返後 float64 位元樣式改變的值（標準 NaN 可寫入，自訂 NaN payload 會失敗）。Float64 保留 NaN payload 與 `-0.0`。啟用 `repeat=True` 時依 IEEE 位元樣式比較（`0.0` 接 `-0.0` 不算重複）。 |
| `Bytes(name)` | 無 | `bytes`、`bytearray`、`memoryview`；解碼為 `bytes` | 無號 varint 長度加上原始位元組。 |
| `Str(name)` | 無 | `str`（須為合法 UTF-8，不可含孤立代理項）；解碼為 `str` | 無號 varint 位元組長度加上 UTF-8 位元組。 |
| `Enum(name, values)` | `values`（非空且不重複的序列，全為含 `StrEnum` 的 `str`，或全為含 `IntEnum` 與 NumPy 整數的 `__index__` 非 `bool` 整數，會正規化為 `str`/`int`；`values` 內含 `bool` 會引發 `ValueError`；禁止 `repeat`） | `values` 成員（`StrEnum`、`IntEnum`、`__index__` 正規化為 `str`/`int`；列儲存格傳入 `bool` 引發 `TypeError`） | 存於共用旗標位元索引，佔 `(len(values) - 1).bit_length()` 位元（單一值時佔 `0` 位元）。 |
| `Category(name)` | 無（禁止 `repeat`） | 全為 `str`（合法 UTF-8）或全為 `__index__` `int`（拒絕 `bool`；不可混用） | 打包時從資料學出排序後的相異值字典並寫入一次，每列僅存共用旗標位元索引。列數 `>= 16` 且相異值超過半數時發出 `UserWarning`。 |

## 範例

### 1. 基本範例：`Column.describe()`、`Column.to_dict()`、`Uint(..., varint=True)` 與 `bytearray`/`memoryview`

檢視欄位描述字串與字典輸出，並往返打包帶有邊界檢查的 varint 整數及 `bytearray`/`memoryview` 資料。

```python
from rpkbin.tablepack import Schema, Uint, Bytes, Enum, pack, unpack

cols = [
    Uint("reg_val", bits=32, varint=True),
    Uint("flags", bits=4, pack=True, nullable=True),
    Bytes("payload"),
    Enum("mode", ["R", "W"]),
]
for c in cols:
    print(f"{c.describe():20s} -> {c.to_dict()}")

schema = Schema(cols)
assert "reg_val" in schema and cols[0] in schema

blob = pack(
    {"reg_val": [1, 255], "flags": [None, 9], "payload": [bytearray(b"\x01\x02"), memoryview(b"ok")], "mode": ["R", "W"]},
    schema,
)
print(unpack(memoryview(blob)))
```

輸出：
```text
uint                 -> {'type': 'uint', 'name': 'reg_val', 'bits': 32, 'varint': True}
uint4+pack+nullable  -> {'type': 'uint', 'name': 'flags', 'nullable': True, 'bits': 4, 'pack': True}
bytes                -> {'type': 'bytes', 'name': 'payload'}
enum(2)              -> {'type': 'enum', 'name': 'mode', 'values': ['R', 'W']}
[{'reg_val': 1, 'flags': None, 'payload': b'\x01\x02', 'mode': 'R'}, {'reg_val': 255, 'flags': 9, 'payload': b'ok', 'mode': 'W'}]
```

### 2. 進階範例：`unpack` `TypeError` 與 `iter_unpack` 延後驗證

驗證 `unpack` 的即時 `TypeError`、`bytearray` 欄容器被拒絕，以及 `iter_unpack` 生成器的延後驗證行為。

```python
from rpkbin.tablepack import Schema, Uint, pack, unpack, iter_unpack

for bad_call in (lambda: unpack(1_000_000_000), lambda: pack({"u": bytearray(b"abc")}, Schema(Uint("u")))):
    try:
        bad_call()
    except TypeError as exc:
        print(f"Immediate TypeError: {exc}")

# iter_unpack 會先回傳 generator，直到開始疊代才驗證引數與資料。
gen = iter_unpack(b"not-a-blob")
print("Created:", type(gen).__name__)
try:
    next(gen)
except ValueError as exc:
    print(f"Deferred ValueError on next(): {exc}")
```

輸出：
```text
Immediate TypeError: blob must be bytes-like
Immediate TypeError: Column 'u': expected a sequence of values, got bytearray
Created: generator
Deferred ValueError on next(): Not a tablepack blob (bad magic bytes)
```

## 二進位格式

版本 1。除特別註明外，所有整數皆為無號 LEB128 varint。

```text
"TPK" | version (1 byte) | codec (1 byte) | 壓縮後的 payload
```

`codec` 編號：`0` 不壓縮、`1` zlib、`2` lzma、`3` brotli。解壓後的 payload 結構如下：

```text
flags (1 byte；bit 0 = 是否嵌入 schema)
[ schema JSON 長度 | schema JSON (UTF-8) ]          僅在嵌入時存在
列數
Category 表（依 schema 順序排列每個 Category 欄位）：
    型別標籤 (0 = str, 1 = int) | 值的數量 | 各個值
旗標區：每一列佔 ceil(旗標總位元數 / 8) 個 big-endian 位元組
欄資料：依 schema 順序連續存放各欄的所有列數值
```

不支援的版本位元組會引發 `ValueError`。V1 不儲存 schema fingerprint，因此當 `embed_schema=False` 時，讀取端必須自行選用相符版本的外部 schema。

## 注意事項與限制

- 欄位選項錯誤會在建立 `Column`/`Schema` 時立刻引發；資料驗證錯誤則會回報從 0 起算的列號與欄名（`row i, column 'name': ...`）。
- `iter_unpack` 可省下建立完整 `list[dict]` 的記憶體，但在產出第 0 列之前仍須先解碼所有欄位。

## 延伸閱讀

- [快速入門](README.md)
- [實戰使用情境](scenarios.md)
- [文件導覽](../README.md)
