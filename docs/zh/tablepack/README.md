# TablePack 快速入門：把表格變成緊湊的位元組並還原

[English](../../en/tablepack/README.md) · [文件導覽](../README.md) · [情境教學](scenarios.md) · [API 參考](reference.md)

TablePack (`rpkbin.tablepack`) 能把一張表格（由 dict 組成的 list）打包成很小的 `bytes`，並且完整還原。

它解決的問題是：**你有規律、重複性高的表格資料（暫存器表、測試向量、查找表、日誌），想緊湊地儲存或傳遞，卻不想每換一種欄位格式就手寫一份二進位編解碼器。** 你只需描述一次每個欄位（或讓 TablePack 自動推斷），其餘的位元層級打包由函式庫決定。

和 MapBV 一樣，它是純函式庫，不會開啟任何檔案。你把資料列（來自 Excel、CSV、資料庫或程式）交給它，拿回 `bytes`；這些位元組要放去哪裡由你決定。

> **相容性。** 需要 Python 3.11 或更新版本（來自 `pyproject.toml`）。本文範例在 Windows、Python 3.13 上實際執行過；程式碼是純 Python，但其他平台並未實測。

---

## 安裝方式

```bash
# 核心安裝：TablePack 可用，以標準庫的 zlib 壓縮
pip install -e .

# 建議：加入 brotli >= 1.2，TablePack 會使用它的有界輸出 API
pip install -e ".[table]"
```

沒有可用的 brotli 時，預設的 `compress="auto"` 會自動改用 zlib。明確指定 `compress="brotli"` 需要 brotli >= 1.2。壓縮方式會記錄在 blob 標頭，因此 brotli blob 只能在有相容 brotli 的環境解開。

---

## 5 分鐘快速上手

### 1. 零設定：不給 schema 直接打包

```python
import json
from rpkbin.tablepack import pack, unpack

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

blob = pack(rows)                         # 沒給 schema：自動推斷，並嵌入 blob 中
assert unpack(blob) == rows               # blob 自帶說明，不需要其他資訊

as_json = json.dumps(rows).encode()
print(f"JSON {len(as_json)} bytes -> blob {len(blob)} bytes")
print(unpack(blob)[1])
```

輸出（有安裝 brotli；改用 zlib 時 blob 大小會略有不同）：

```text
JSON 12596 bytes -> blob 183 bytes
{'module': 'uart', 'addr': 4100, 'access': 'RO', 'reset': 1}
```

`unpack()` 會依原本的列順序回傳 dict 的 list。`None`、整數、字串與布林值都會以相同型別還原。

### 2. 自己描述欄位

當你了解資料時，schema 能讓你掌控細節並得到更小的輸出。每個欄位都是有型別的物件；不合理的設定（例如 `Enum(..., repeat=True)`）會在建立 schema 時立刻報錯，訊息會指出是哪個欄位。

```python
from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack

schema = Schema(
    Str("module", repeat=True),             # 常與上一列相同：重複時只花 1 bit
    Uint("addr", bits=16, delta=True),      # 儲存與上一列的差值，而不是位址本身
    Enum("kind", ["RW", "RO", "W1C"]),      # 已知的固定值集合：每列 2 bits
    Str("note", nullable=True),             # 可以是 None：缺值時花 1 bit
)
rows = [
    {"module": "uart", "addr": 0x10, "kind": "RW", "note": None},
    {"module": "uart", "addr": 0x14, "kind": "RO", "note": "status"},
    {"module": "spi", "addr": 0x40, "kind": "W1C", "note": None},
]

blob = pack(rows, schema)
assert unpack(blob) == rows               # schema 預設會嵌入 blob
print(unpack(blob)[1])
```

輸出：

```text
{'module': 'uart', 'addr': 20, 'kind': 'RO', 'note': 'status'}
```

### 3. 不確定該用哪些選項？

此步驟沿用步驟 2 的 `rows`。

```python
from rpkbin.tablepack import infer, explain

schema = infer(rows)       # 以目標壓縮器與嵌入 schema 的設定量測整張表
print(schema)              # 印出可直接貼上修改的 Schema(...) 程式碼
print(explain(rows))       # 每個欄位花了多少 bytes
```

`infer` 先快速建立逐欄 zlib 基準，再以指定的壓縮器和 schema 嵌入設定，量測最多八個整表候選方案。這是有限搜尋，不保證找到全域最小值；基準一定會納入，大小相同時優先保留基準。`report=True` 仍回報逐欄 zlib 排名，最後選出的 schema 不一定是第一項。

如何解讀輸出請見[情境 3](scenarios.md)。

---

## 關鍵注意事項與常見誤區

> [!NOTE]
> ### 1. 輸入形式
> `pack()` 接受 `list[dict]`（每列一個 dict）或 `dict[str, sequence]`（每欄一個等長序列，適合資料已經放在陣列裡的情況；TablePack 本身不依賴 NumPy 或 pandas）。每一列必須剛好包含 schema 的所有欄位：缺少或多出的鍵會引發 `ValueError` 並指出是第幾列。

> [!IMPORTANT]
> ### 2. 嵌入 schema 與最小輸出
> 預設會把 schema 存進 blob（約數十 bytes），讓 `unpack(blob)` 不需要其他資訊。若要最小輸出，請傳入 `embed_schema=False`，解包時再把同一份 schema 交給 `unpack(blob, schema)`。見[情境 4](scenarios.md)。

> [!WARNING]
> ### 3. 型別是檢查，不是轉換
> 值必須符合欄位型別：`Uint` 不接受 `True`，`Int` 不接受 `"7"`，`Uint` 也不接受負數。錯誤訊息會指出列與欄。見[情境 8](scenarios.md)。

> ### 4. 解碼限制
> Reader 會限制列數、儲存格數、payload 與 schema 位元組數、欄數及整數 varint 長度。這些限制用來約束解碼工作與 payload 膨脹，不是作業系統層級的記憶體上限。請見[API 參考手冊](reference.md#decodelimits)。

---

## 下一步

- **[情境教學](scenarios.md)**：欄位調校、比較不同方案、欄式資料、共用 schema、壓縮方式選擇、建置時驗證，以及錯誤處理。
- **[API 參考手冊](reference.md)**：所有函式、欄位型別、選項、預設值、例外，以及二進位格式。
