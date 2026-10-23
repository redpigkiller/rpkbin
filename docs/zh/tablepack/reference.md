# TablePack API 與精確語意參考手冊

[English](../../en/tablepack/reference.md) · [文件導覽](../README.md) · [情境教學](scenarios.md)

本文件提供 `rpkbin.tablepack` 套件的完整規格：函式、schema 與欄位型別、可接受的值、例外，以及二進位格式。

匯入方式：
```python
from rpkbin.tablepack import (
    Schema, Uint, Int, Bool, Float, Bytes, Str, Enum, Category, DecodeLimits,
    pack, unpack, iter_unpack, infer, explain,
)
```

需要 Python 3.11 以上。brotli >= 1.2 為選配（`pip install -e ".[table]"`），只有在有可用的有界輸出解碼 API 時才會使用；否則 `auto` 會改用 zlib。以 `_` 開頭的私有模組與名稱不屬於 API。

---

## 函式

### `pack(data, schema=None, *, embed_schema=True, compress="auto", verify=False) -> bytes`
把表格編碼成 blob。輸出是確定性的：相同的輸入、schema 與選項，在相同的壓縮器版本下一定得到相同的位元組。

| 名稱 | 型別 | 說明 |
| :--- | :--- | :--- |
| `data` | `list[dict]` 或 `dict[str, sequence]` | 每列一個 mapping，或每欄一個等長序列。任何由 mapping 組成的 iterable 都可接受。`str`／`bytes` 不是有效的欄序列。 |
| `schema` | `Schema \| None` | 預設 `None`：以 `infer` 推斷並嵌入 schema。此時至少要有一列資料，且 `embed_schema=True`。 |
| `embed_schema` | `bool` | 預設 `True`。把 schema 存進 blob，讓 `unpack(blob)` 不需另給 schema。 |
| `compress` | `str \| None` | 預設 `"auto"`：有相容的 brotli（>= 1.2）就使用，否則用 zlib。也可用 `"brotli"`、`"zlib"`、`"lzma"` 或 `None`（不壓縮）。會記錄在 blob 標頭。 |
| `verify` | `bool` | 預設 `False`。把結果解碼並與輸入比對。 |

**回傳** `bytes`。

**例外**
- `ValueError` — 列／欄不符（缺少或多出欄位、各欄長度不同）、值超出欄位範圍、非 nullable 欄位出現 `None`、未知的 `Enum` 值、沒有 schema 的空表格、沒有 schema 卻設 `embed_schema=False`、未知的 `compress` 值、`verify=True` 驗證失敗。數值錯誤會指出列與欄：`row 3, column 'addr': ...`。
- `TypeError` — 值的型別不符欄位、列不是 mapping、欄不是序列，或 `Category` 欄位混用型別。
- `ImportError` — 指定 `compress="brotli"` 但沒有可用的相容 brotli（>= 1.2）。

### `unpack(blob, schema=None, *, as_columns=False, limits=DecodeLimits()) -> list[dict] | dict[str, list]`
解碼 `pack` 產生的 blob。

| 名稱 | 型別 | 說明 |
| :--- | :--- | :--- |
| `blob` | `bytes` | `pack` 產生的 blob。 |
| `schema` | `Schema \| None` | blob 沒有嵌入 schema 時必須提供。若 blob 已嵌入 schema 而你又傳入一份，兩者必須相等。 |
| `as_columns` | `bool` | 預設 `False`。`True` 時回傳 `{欄名: list}`，而不是列 dict 的 list。 |
| `limits` | `DecodeLimits` | 解碼預算：解壓後 payload、schema、列數、儲存格數、欄數與整數 varint。 |

**回傳** 依原列順序排列的 dict list（鍵依 schema 欄位順序），或由 list 組成的 dict。值為 Python 的 `int`、`float`、`bool`、`str`、`bytes` 或 `None`。

**例外**
- `ValueError` — 不是 TablePack blob（magic 錯誤）、不支援的格式版本、資料損毀或被截斷（`Corrupt or truncated tablepack blob`）、超出 reader 預算（會以資料損毀前綴回報）、沒有嵌入 schema 也沒有提供，或提供的 schema 與嵌入的不同。
- `ImportError` — blob 使用 brotli 壓縮，但目前沒有可用的相容解碼器（brotli >= 1.2）。

### `iter_unpack(blob, schema=None, *, limits=DecodeLimits()) -> Iterator[dict]`
解碼方式與例外和 `unpack` 相同，但一次產生一個列 dict。在產生第一列之前，blob 會先逐欄解碼，所以它省下的是建立完整列 list 的成本，不是已解碼的欄資料。

### `infer(data, *, report=False, compress="auto", embed_schema=True, optimize=True) -> Schema | tuple[Schema, dict]`
提出 schema。先依每個欄位單獨使用 zlib 的大小排列候選，再於 `optimize=True` 時，以指定壓縮器及 schema 嵌入設定量測最多八個整表候選。逐欄基準一定會納入，大小相同時優先保留基準。這是有限搜尋，只提供建議，不保證全域最小值。

| 名稱 | 型別 | 說明 |
| :--- | :--- | :--- |
| `data` | 同 `pack` | 至少要有一列。 |
| `report` | `bool` | 預設 `False`。為 `True` 時回傳 `(schema, report)`，其中 `report[name]` 是 `[(Column, 位元組數), ...]`，由小到大排序。 |
| `compress` | `str \| None` | 預設 `"auto"`。整表評分使用的壓縮器：`"auto"`、`"brotli"`、`"zlib"`、`"lzma"` 或 `None`。 |
| `embed_schema` | `bool` | 預設 `True`。評分整表候選時是否包含 schema。 |
| `optimize` | `bool` | 預設 `True`。設為 `False` 時只回傳快速逐欄基準。 |

依值型別推斷（忽略 `None`）：

| 值 | 候選 |
| :--- | :--- |
| 全為 `bool` | `Bool` |
| 非負整數 | `Uint`：varint／固定寬度／`delta`／`pack`（寬度 16 位元以內），低基數資料另外加上 `Category` |
| 含負數的整數 | `Int` 一般／`delta`，低基數資料另外加上 `Category` |
| 整數與浮點數 | `Float` 64 位元與 32 位元 |
| 全為 `str` | `Str`，低基數資料另外加上 `Category` |
| 全為 bytes-like | `Bytes` |
| 只有 `None` | `Str(nullable=True)` |

只要有任何值是 `None` 就會設 `nullable=True`。相鄰列相同的比例超過 10% 時，會嘗試 `repeat=True` 的版本。相異值數量不超過非空值的 60% 時視為低基數。整表最佳化會從有限候選組合中評估，例如共同封裝窄整數、整表 repeat 策略及不使用 `Category`；不會窮舉所有欄位組合。

`infer` 不會提出 `Enum`。

`report` 仍是逐欄 zlib 排名，即使整表評分最後選出其他候選。省略 schema 的 `pack(data, schema=None)` 會依目標 `compress` 和 `embed_schema` 設定使用相同推斷。省略 schema 的 `explain(data, schema=None)` 會先正規化一次資料，再以目標壓縮器及 `embed_schema=False` 推斷，接著量測欄位與總量。

**例外** 空表格或沒有任何候選可用時引發 `ValueError`；同一欄混用或使用不支援的值型別時引發 `TypeError`。

### `explain(data, schema=None, *, compress="auto") -> str`
回傳文字表格，列出每欄的編碼與位元組數，以及總量。省略 `schema` 時會以目標壓縮器及 `embed_schema=False` 推斷。每欄的位元組數來自把該欄位單獨打包（不嵌入 schema），所以只是參考：數字包含各自 blob 的額外開銷，加總不等於總量。`share` 是相對於這些數字加總的比例。可接受任意列 iterable，正規化後的資料會供推斷與量測重複使用。例外同 `pack` 與 `infer`。

### `DecodeLimits`
公開的 frozen dataclass，用來限制 reader 的工作量。`unpack` 與 `iter_unpack` 都接受 `limits`；預設實例包含以下正整數預算：

| 欄位 | 預設值 | 意義 |
| :--- | ---: | :--- |
| `max_rows` | `1_000_000` | 宣告列數上限。 |
| `max_cells` | `10_000_000` | 解碼值數上限（`rows * columns`）。 |
| `max_payload_bytes` | `67_108_864`（64 MiB） | 解壓後 payload 位元組上限。 |
| `max_schema_bytes` | `1_048_576`（1 MiB） | 嵌入 schema JSON 位元組上限。 |
| `max_columns` | `4096` | schema 欄數上限。 |
| `max_integer_bytes` | `8192` | 整數 varint 的位元組上限。 |

例如：`unpack(blob, limits=DecodeLimits(max_rows=2_000_000))`。超限時會以前綴 `Corrupt or truncated tablepack blob` 的 `ValueError` 拒絕。Reader 先有界解壓，再驗證 metadata、列數／儲存格數、旗標、值範圍、repeat 狀態與 EOF。LZMA 解碼另固定使用 128 MiB decoder 記憶體限制（仍可讀取 V1 writer 使用的 64 MiB dictionary）。這些預算用來限制 parser 放大效應，不是作業系統層級的記憶體上限。

---

## `Schema`

`Schema(*columns)` 或 `Schema([columns])`：由名稱唯一的欄位組成的有序集合，至少一個欄位。建立後不可變。

| 成員 | 說明 |
| :--- | :--- |
| `columns` | `Column` 的 tuple。 |
| `names` | 依序排列的欄名 list。 |
| `iter(schema)`、`len(schema)` | 逐一走訪或計算欄位數。 |
| `schema[name]` | 該名稱的欄位；不存在時引發 `KeyError`。 |
| `==`、`hash()` | 欄位 tuple 相等即相等。 |
| `repr(schema)` | 可重建此 schema 的 Python 原始碼（只顯示非預設的選項）。 |
| `to_dict()` / `Schema.from_dict(d)` | 純 dict 形式，`{"columns": [{"type": ..., "name": ..., ...}]}`。 |
| `to_json(indent=None)` / `Schema.from_json(text)` | dict 形式的 JSON 文字。 |

**例外** 沒有欄位、欄名重複、未知的欄位 `type`，或 `from_dict`／`from_json` 中的選項有誤時引發 `ValueError`；引數不是 `Column` 時引發 `TypeError`。

---

## 欄位型別

所有欄位都是不可變的 dataclass。`name` 是第一個位置引數；除了 `Enum.values` 之外，所有選項都只能用關鍵字傳入。不合理的組合會在建立物件時引發 `ValueError` 並指出欄位名稱。所有欄位都有兩個共通選項：

| 選項 | 預設 | 說明 |
| :--- | :--- | :--- |
| `nullable` | `False` | 值可以是 `None`。每列花 1 個旗標位元；缺值時不儲存內容。 |
| `repeat` | `False` | 值常與上一列相同。每列花 1 個旗標位元；重複的值不會再儲存。`Bool`、`Enum`、`Category` 與 `Uint(pack=True)` 不支援。 |

| 型別 | 選項（共通選項之外） | 可接受的輸入值 | 儲存方式 |
| :--- | :--- | :--- | :--- |
| `Uint(name)` | `bits=None`、`varint=False`、`delta=False`、`pack=False` | `>= 0` 的整數（任何有 `__index__` 的物件，例如 NumPy 整數；`bool` 會被拒絕） | 預設為 varint。`bits=N` 會把值限制在 `< 2**N`，且在沒有 `delta`／`varint` 時以 `ceil(N/8)` 個 big-endian 位元組儲存。`delta=True` 儲存與前一個非空值之差的 zigzag varint（從 0 起算）。`pack=True`（需要 `bits`；不可與 `repeat`、`delta`、`varint` 合用）把值存進共用旗標位元。 |
| `Int(name)` | `delta=False` | 整數（同上） | 值的 zigzag varint；`delta=True` 時為與前一個非空值之差的 zigzag varint。 |
| `Bool(name)` | 無 | 接受 `True`／`False` 與 NumPy 布林純量 | 1 個旗標位元。 |
| `Float(name)` | `bits=64` | `int` 或 `float`（不含 `bool`）；以 `float` 回傳 | Big-endian IEEE 754。整數必須能以 float64 精確表示。`bits=32` 只有在 float64 位元樣式能精確往返時才接受，否則引發 `ValueError`；可表示的 NaN 也可接受，但 payload 必須能逐位元往返。Float64 的 NaN 與帶號零保留位元樣式。 |
| `Bytes(name)` | 無 | `bytes`、`bytearray`、`memoryview`；以 `bytes` 回傳 | varint 長度加上資料。 |
| `Str(name)` | 無 | `str`；以 `str` 回傳 | varint 長度加上 UTF-8。 |
| `Enum(name, values)` | `values`：非空序列，全為 `str` 或全為 `int`，且不可重複 | `values` 其中之一；`StrEnum`、`IntEnum`、NumPy 整數與其他字串／整數子類會正規化成 Python `str`／`int`（bool 不接受為整數） | 以索引存放在共用旗標位元，佔 `ceil(log2(len(values)))` 位元（只有一個值時為 0 位元）。 |
| `Category(name)` | 無 | 全為 `str` 或全為 `int`（不可混用）；`StrEnum`、`IntEnum`、NumPy 整數與其他子類會正規化成內建型別 | 類似 `Enum`，但排序後的相異值集合在打包時從資料學出，並只在 blob 中儲存一次。當表格有 16 列以上且相異值超過列數一半時會發出 `UserWarning`。 |

判斷重複時，浮點數以位元樣式比較，所以 `0.0` 接著 `-0.0` 不算重複。

---

## 二進位格式

版本 1。除非另有說明，所有整數都是無號 LEB128 varint。

```text
"TPK" | version (1 byte) | codec (1 byte) | 壓縮後的 payload
```

`codec`：`0` 不壓縮、`1` zlib、`2` lzma、`3` brotli。payload 在壓縮前的內容：

```text
flags (1 byte；bit 0 = 已嵌入 schema)
[ schema JSON 長度 | schema JSON (UTF-8) ]          僅在嵌入時存在
列數
Category 表：依 schema 順序，對每個 Category 欄位：
    型別標籤 (0 = str, 1 = int) | 值的數量 | 各個值
旗標區：每一列各有 ceil(旗標總位元數 / 8) 個位元組的 big-endian 位元欄位
欄資料：依 schema 順序，每個欄位在所有列中儲存的值
```

每一列的旗標位元會依欄位順序放入：`Enum`／`Category` 的索引、`Bool` 的值、`Uint(pack=True)` 的值、`nullable` 的存在位元，以及 `repeat` 的「與上一列相同」位元。不放在旗標位元中的值會寫進該欄位的資料區，因此同一欄的值彼此相鄰，有利於壓縮。版本位元組不被支援的 blob 會被拒絕並引發 `ValueError`。V1 不會儲存 schema fingerprint；停用 schema 嵌入時，應用程式必須選取相符版本的外部 schema。
