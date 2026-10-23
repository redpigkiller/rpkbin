# rpkbin — Wave、MapBV、TablePack 與 rpk-calc

`rpkbin` 是一套針對 IC 設計與驗證流程開發的輕量 Python 工具庫，包含四項工具：

1. **Wave**：以 Python 定義外部命令的批次排程與監控核心，提供 Web GUI 監控介面與 Headless 批次執行入口。解決多工作並行、資源配額、即時日誌過濾、多重規則驗收與歷史追蹤問題。
2. **MapBV**：專為硬體暫存器設計的位元欄位工具。解決位元欄位切片（雙向即時同步）、暫存器與 Bus 串接 (`concat`)、唯讀常數位元保護以及組合邏輯運算式延遲求值等常見硬體塑模需求。
3. **TablePack**：以 schema 驅動的表格編解碼工具，把資料列打包成緊湊的 `bytes`（有型別的欄位、delta／repeat／nullable 編碼、可選 brotli 壓縮）並完整還原。純函式庫，不會讀取檔案。
4. **rpk-calc**：互動式 Python 工程計算工作區，將 NumPy 與明確轉換的 APyTypes 定點數（FXP）放在同一個 GUI 中檢視與編輯。

[English](README.md) · [完整文件導覽](docs/zh/README.md) · [MIT 授權](LICENSE)

---

## 安裝方式

支援 Python 3.11 或更新版本。

```bash
# 核心安裝（包含 Wave、MapBV 與 TablePack，僅依賴 NumPy 與標準庫）
pip install -e .

# 選配套件：TablePack 的 brotli 壓縮（未安裝時改用 zlib）
pip install -e ".[table]"

# 選配套件：若需要定點數模型支援（加入 apytypes）
pip install -e ".[fixed]"

# 選配套件：安裝 rpk-calc GUI 與定點數支援
pip install -e ".[calc]"
```

---

## 快速上手：取得第一個可確認的結果

### 1. Wave：第一次執行批次工作

建立 `work.py` 定義兩個工作。在工作定義檔中只負責建立工作與設定規則，不主動呼叫派發：

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

# 建立管理器並設定最多同時執行 2 個工作
mgr = JobManager(max_workers=2)

for name in ("sim_core", "sim_dma"):
    job = mgr.add(Job(name, [sys.executable, "-u", "-c", "print('TEST_PASS'); print('DONE')"]))
    # 宣告驗收條件：輸出必須出現 TEST_PASS，且子程序退出碼必須為 0
    job.check("pass_marker", match=r"^TEST_PASS$", expect=Expect.PRESENT)
```

**命令列批次執行（Headless）：**

```bash
rpk-wave work.py --headless
```

終端機將即時串流子程序輸出，並於結束時輸出不可變驗收摘要：

```text
[Wave] Starting 2 job(s); workers=2
[child sim_core #1] TEST_PASS
[child sim_core #1] DONE
[child sim_dma #1] TEST_PASS
[child sim_dma #1] DONE

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
sim_core                   DONE            PASS         0         0
  checks: pass_marker: OK
sim_dma                    DONE            PASS         0         0
  checks: pass_marker: OK
```

**以 Web GUI 介面執行：**

```bash
rpk-wave work.py
```

瀏覽器會開啟本機 GUI 頁面。工作檔載入後會先進入「預覽（Preview）」狀態，明確點擊頁面頂部的 **Start** 按鈕後才開始派發執行。

---

### 2. MapBV：第一次建立與讀寫位元欄位

建立一個 16 位元的硬體暫存器變數，切出欄位並觀察即時連動：

```python
import rpkbin.mapbv as mbv

# 建立 16 位元暫存器（初始值 0x1234）
reg = mbv.var("CSR", 16, 0x1234)

# 切出 [15:8] 高位元組與 [3:0] 低 4 位元（閉區間視圖，與原暫存器即時雙向連動）
high_byte = reg[15:8]
low_nibble = reg[3:0]

print(f"原暫存器: {reg:hex} (0x{reg.value:04X})")  # 0x1234
print(f"高位元組: {high_byte:hex}")                 # 0x12
print(f"低四位元: {low_nibble:hex}")                # 0x4

# 修改切片視圖，原暫存器會立即同步更新
high_byte.value = 0xAB
print(f"修改高位元組後原暫存器: {reg:hex}")        # 0xAB34
```

執行此腳本，輸出可直接確認數值已正確寫入並連動。

> [!IMPORTANT]
> **注意比較語意**：`reg == 0xAB34` 比較的是物件識別身分（會引發 `TypeError` 以防止誤用）。比對數值請使用 `reg.value == 0xAB34` 或 `reg.value_eq(0xAB34)`。

### 3. TablePack：把表格打包成位元組並還原

只需描述一次欄位，就能把資料列打包成緊湊的 `bytes`，並完整還原：

```python
from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack

schema = Schema(
    Str("module", repeat=True),             # 常與上一列相同：重複時只花 1 bit
    Uint("addr", bits=16, delta=True),      # 儲存與上一列的差值
    Enum("kind", ["RW", "RO", "W1C"]),      # 已知的固定值集合
    Str("note", nullable=True),             # 可以是 None
)
rows = [
    {"module": "uart", "addr": 0x10, "kind": "RW", "note": None},
    {"module": "uart", "addr": 0x14, "kind": "RO", "note": "status"},
    {"module": "spi", "addr": 0x40, "kind": "W1C", "note": None},
]

blob = pack(rows, schema)
assert unpack(blob) == rows               # schema 預設會嵌入 blob
print(unpack(blob)[1])                    # {'module': 'uart', 'addr': 20, 'kind': 'RO', 'note': 'status'}
```

TablePack 不會讀取任何檔案：資料列從哪裡來都可以，它只回傳 `bytes`。不給 schema 時，`pack(rows)` 會自動推斷。若要使用 brotli 壓縮，請安裝 `pip install -e ".[table]"`；沒有安裝時會改用 zlib。

---

## 接著讀哪裡？

根據您的使用需求，請參閱詳細文件：

### 深入 Wave
- **[Wave 快速入門](docs/zh/wave/README.md)**：第一次完成一批工作的完整流程與觀念。
- **[由淺入深的 Python 使用情境](docs/zh/wave/scenarios.md)**：並行排程、多重 checks、警告、即時 track、互動輸入與 signal、資源配額、超時重試與歷史記錄。
- **[Web GUI 操作指南](docs/zh/wave/gui.md)**：預覽與派發、即時 Log 檢視、篩選、批次控制與 Settings（並行限制、記憶體日誌保留、顯示偏好）設定。
- **[Python API 參考](docs/zh/wave/reference/python.md)** 及 **[CLI 參考](docs/zh/wave/reference/cli.md)**：查閱精確參數、列舉與退出碼。
- **[架構契約](docs/zh/wave/architecture.md)** 與 **[疑難排解](docs/zh/wave/troubleshooting.md)**。

### 深入 MapBV
- **[MapBV 快速入門](docs/zh/mapbv/README.md)**：宣告變數與常數、位元範圍與進制格式化。
- **[實戰使用情境](docs/zh/mapbv/scenarios.md)**：暫存器切片雙向連動、硬體 Bus 串接 (`concat`) 與唯讀位元防護、邏輯運算式延遲求值與符號代入。
- **[API 與精確語意參考](docs/zh/mapbv/reference.md)**：方法規格、運算子支援與例外邊界。

### 深入 TablePack
- **[TablePack 快速入門](docs/zh/tablepack/README.md)**：不給 schema 直接打包、描述欄位，以及第一次大小比較。
- **[實戰使用情境](docs/zh/tablepack/scenarios.md)**：欄位型別、`infer`／`explain`、共用 schema、欄式資料、欄位調校、壓縮方式、建置時驗證與錯誤處理。
- **[API 與精確語意參考](docs/zh/tablepack/reference.md)**：函式、欄位型別與選項、例外與二進位格式。

### rpk-calc 指南
- **[rpk-calc 快速入門](docs/zh/calc/README.md)**：啟動乾淨的 IPython 工作區、混用 Python 與 FXP、檢視和編輯陣列，以及了解執行控制。`rpk-calc --eval` 是獨立的 headless `WorkspaceSession` 命令。
- **[架構與資料邊界](docs/zh/calc/architecture.md)**：Kernel 所有權、JSON snapshot、矩陣分頁限制與版本相容性。

## 授權

本專案程式碼採用 [MIT License](LICENSE)。內嵌 SVG 圖示包含 Feather（MIT）及
Lucide（ISC）設計，原始聲明收錄於 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，
因此套件整體宣告為 `MIT AND ISC`。第三方依賴保留各自授權，MIT 不會變更
這些套件的授權。特別是 Calc 使用 PySide6 / Qt for Python，其社群版本適用
[LGPL／GPL 授權條款](https://doc.qt.io/qtforpython-6/licenses.html)。
rpkbin wheel 包含本專案程式碼，未綑綁 Qt 或其他依賴套件。如果另外製作含有這些依賴的
獨立執行檔，需另行確認各依賴的授權與散布要求。
