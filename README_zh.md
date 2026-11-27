# rpkbin：IC 設計核心函式庫與互動應用

`rpkbin` 是一套用於 IC 設計、硬體建模與驗證流程的 Python 工具集，分為核心函式庫與引擎（`rpkbin.*`）以及互動應用（`rpkbin.app.*`）。

[English](README.md) · [完整文件導覽](docs/zh/README.md) · [雙語文件入口](docs/README.md) · [貢獻指南](CONTRIBUTING.md) · [CI 與套件檢查](.github/ci.md) · [MIT 授權](LICENSE)

## 模組與進入點

### 核心函式庫與引擎（`rpkbin.*`）

| 模組 | 套件／CLI | 用途 | 文件 |
|---|---|---|---|
| MapBV | `rpkbin.mapbv` | 無號硬體暫存器建模，支援雙向即時連動切片、`concat` 匯流排串接、唯讀常數位元與延遲組合邏輯運算式。 | [MapBV](docs/zh/mapbv/README.md) |
| TablePack | `rpkbin.tablepack` | 以 schema 驅動的編解碼函式庫，將資料列或欄式序列打包成緊湊 `bytes` 並完整還原，不讀取任何檔案。 | [TablePack](docs/zh/tablepack/README.md) |
| JobCore | `rpkbin.jobcore` | 外部命令排程器，提供 worker 上限、資源配額、優先權佇列、受保護的程序控制與整棵程序樹清理。 | [JobCore](docs/zh/jobcore/README.md) |
| Flow | `rpkbin.flow` | 在隔離的 `FlowSession` 中執行逐行文字解析、分析、自動化回應與圖表投影（`ViewSpec`）。 | [Flow](docs/zh/flow/README.md) |
| Wave | `rpkbin.wave`、`rpk-wave` | 整合 `jobcore` 與 `flow`，提供本機 Web GUI、即時記錄追蹤、SQLite 封存資料庫（`RunArchive`）與離線重析。 | [Wave](docs/zh/wave/README.md) |
| mcucc | `rpkbin.mcucc`、`rpk-mcucc` | 將受限 Python 子集編譯為目標組語、執行靜態驗證（`V1` 至 `V11`），並可搭配 `isasim` 執行差異驗證。 | [mcucc](docs/zh/mcucc/README.md) |
| isasim | `rpkbin.isasim`、`rpk-isasim` | 依 Python `MCU` 模型執行可重定義指令模擬與兩階段組譯，支援 trace、除錯與快照重播。 | [isasim](docs/zh/isasim/README.md) |

### 互動應用（`rpkbin.app.*`）

| 應用 | 套件／CLI | 用途 | 文件 |
|---|---|---|---|
| isasim_gui | `rpkbin.app.isasim_gui`、`rpk-isasim-gui` | 建構於 `rpkbin.isasim` 之上的本機瀏覽器除錯器，支援前進／回溯單步、條件斷點、雙擊原位修改與區間計數器。 | [isasim_gui](docs/zh/app/isasim_gui/README.md) |
| rpk-calc | `rpkbin.app.calc`、`rpk-calc` | 桌面計算機與 IPython 工作區，整合 NumPy 與 APyTypes 定點數（FXP）建模，另提供無介面 `--eval` CLI。 | [rpk-calc](docs/zh/calc/README.md) |

## 安裝方式

需要 Python 3.11 或更新版本（核心相依套件為 `numpy>=1.21` 與 `regex>=2024.11.6`）：

```bash
# 核心安裝（包含 MapBV、TablePack、JobCore、Flow、Wave、mcucc、isasim 與 isasim_gui）
python -m pip install --upgrade rpkbin

# 選配：TablePack 的 brotli >= 1.2 壓縮（未安裝時改用 zlib）
python -m pip install --upgrade "rpkbin[table]"

# 選配：無介面定點數運算（加入 apytypes >= 0.5.0）
python -m pip install --upgrade "rpkbin[fixed]"

# 選配：isasim_gui 相容性 extra（除錯器僅使用 Python 標準函式庫）
python -m pip install --upgrade "rpkbin[isasim_gui]"

# 選配：rpk-calc Qt／IPython GUI 與定點數支援
python -m pip install --upgrade "rpkbin[calc]"

# 選配：安裝所有選配相依套件
python -m pip install --upgrade "rpkbin[all]"
```

安裝後會提供五個 CLI 指令（`rpk-wave`、`rpk-isasim`、`rpk-isasim-gui`、`rpk-mcucc`、`rpk-calc`），各指令也都能以 `python -m rpkbin.<模組>` 執行，並支援 `-V`／`--version`。

## 快速上手：取得第一個可驗證結果

### 1. Wave：第一次執行批次工作

建立 `work.py` 定義兩項工作。工作定義檔只負責註冊工作與 `Flow` 規則，不會直接啟動子程序：

```python
import sys
from rpkbin.flow import Analyzer, Binding, Flow, Parser
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import WaveWorkspace


def parse_marker(line, ctx):
    if line == "TEST_PASS":
        return {"passed": True}


def remember_marker(record, ctx):
    ctx.state["seen"] = record["passed"]


def finish(end, ctx):
    passed = end.complete and end.execution.get("returncode") == 0 and ctx.state.get("seen", False)
    ctx.summary.result("PASS" if passed else "FAIL", reason="marker and exit status", final=end.complete)


marker = Parser("marker", parse_marker)
verification = Analyzer("verification", records=[Binding(marker, remember_marker)], on_finish=finish)
flow = Flow("verification", parsers=[marker], analyzers=[verification], result=verification)
workspace = WaveWorkspace(JobManager(max_workers=2))
for name in ("sim_core", "sim_dma"):
    command = [sys.executable, "-u", "-c", "print('TEST_PASS'); print('DONE')"]
    workspace.add(Job(name, command), flow=flow)
```

在終端機以無頭模式執行批次工作（`--headless`）：

```bash
rpk-wave work.py --headless
```

終端機會即時顯示子程序輸出，並在全部完成後印出摘要。並行工作的輸出行順序可能不同。

```text
[Wave] Starting 2 job(s); workers=2
[child sim_core #1] TEST_PASS
[child sim_core #1] DONE
[child sim_dma #1] TEST_PASS
[child sim_dma #1] DONE

[Wave] Final summary
[Wave] History saved: run <run-id>
JOB                        STATE           RESULT       WARNINGS  EXIT
sim_core                   DONE            PASS         0         0
  analysis verification: PASS; marker and exit status
sim_dma                    DONE            PASS         0         0
  analysis verification: PASS; marker and exit status
```

若想先在瀏覽器檢查工作內容再啟動，省略 `--headless` 即可開啟本機 Web GUI：

```bash
rpk-wave work.py
```

若瀏覽器沒有自動開啟，請開啟 Wave 印出的完整網址，在 **Preview** 檢查佇列後按下 **Start 2 Jobs**。

若記錄檔由其他工具寫入，可執行 `rpk-wave logs session.log logs/` 在同一個工作區預覽檔案與資料夾。讀取已寫完的檔案可加 `--once`，終端機執行加 `--headless`，套用分析管線則加 `--flow FLOW.py`。可執行的範例請見 [Wave Log 與重析選項](docs/zh/wave/scenarios.md#log-與重析選項)。

### 2. MapBV：建立與讀寫位元欄位

建立 16 位元暫存器變數、切出位元欄位，並確認切片與原暫存器會即時同步更新：

```python
import rpkbin.mapbv as mbv

# 建立 16 位元暫存器，初始值為 0x1234
reg = mbv.var("CSR", 16, 0x1234)

# 切出 [15:8] 高位元組與 [3:0] 低四位元（閉區間雙向同步視圖）
high_byte = reg[15:8]
low_nibble = reg[3:0]

print(f"原暫存器: {reg:hex} (0x{reg.value:04X})")
print(f"高位元組: {high_byte:hex}")
print(f"低四位元: {low_nibble:hex}")

# 修改切片後，原暫存器會立即同步更新
high_byte.value = 0xAB
print(f"修改高位元組後原暫存器: {reg:hex}")
```

輸出：

```text
原暫存器: 0x1234 (0x1234)
高位元組: 0x12
低四位元: 0x4
修改高位元組後原暫存器: 0xAB34
```

> [!IMPORTANT]
> `reg == 0xAB34` 比對的是 Python 物件識別身分，會引發 `TypeError` 以避免誤寫數值比較。請使用 `reg.value == 0xAB34` 或 `reg.value_eq(0xAB34)` 比對數值。

### 3. TablePack：把表格打包成位元組並還原

只需宣告一次欄位定義，就能把資料列打包成緊湊的 `bytes` 並完整還原：

```python
from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack

schema = Schema(
    Str("module", repeat=True),             # 與上一列相同時只花 1 bit
    Uint("addr", bits=16, delta=True),      # 儲存與上一列的差值
    Enum("kind", ["RW", "RO", "W1C"]),      # 已知的固定值集合
    Str("note", nullable=True),             # 允許為字串或 None
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

TablePack 不會讀取任何檔案，從任何來源傳入資料列都會回傳 `bytes`。呼叫 `pack(rows)` 不傳 `schema` 時會自動推斷欄位結構。若要使用 brotli 壓縮，請安裝 `python -m pip install "rpkbin[table]"`；未安裝時會改用 `zlib`。

## 接著讀哪裡

### 核心函式庫與引擎（`rpkbin.*`）

#### MapBV
- [MapBV 快速入門](docs/zh/mapbv/README.md)：建立變數與常數、位元寬度與十六進位／二進位格式化。
- [實戰使用情境](docs/zh/mapbv/scenarios.md)：切片雙向連動、匯流排串接（`concat`）、唯讀位元保護與符號求值 `eval()`。
- [API 參考手冊](docs/zh/mapbv/reference.md)：`MapBV`、`MapBVExpr`、工廠函式、運算子表與例外規則。

#### TablePack
- [TablePack 快速入門](docs/zh/tablepack/README.md)：無 schema 打包、自訂欄位、`infer` 與 `explain`。
- [實戰使用情境](docs/zh/tablepack/scenarios.md)：欄位型別、JSON schema、位元組分析、壓縮選項與錯誤判讀。
- [API 參考手冊](docs/zh/tablepack/reference.md)：函式、欄位型別、`DecodeLimits`、例外與二進位格式。

#### JobCore
- [JobCore 快速入門](docs/zh/jobcore/README.md)：執行隔離命令、context manager 與佇列暫緩。
- [實戰使用情境](docs/zh/jobcore/scenarios.md)：資源配額、排程模式、互動提示、重試、逾時與 `ProcessHandle`。
- [API 參考手冊](docs/zh/jobcore/reference.md)：`JobManager`、`Job`、`ControlReceipt`、`ProcessHandle` 與狀態列舉。

#### Flow
- [Flow 快速入門](docs/zh/flow/README.md)：`Parser`、`Analyzer`、`Automation` 與 `FlowSession` 基本用法。
- [實戰使用情境](docs/zh/flow/scenarios.md)：多階段串接、`Tick` 監視、`EndInfo`、`ViewSpec` 圖表與錯誤隔離。
- [API 參考手冊](docs/zh/flow/reference.md)：管線定義、Context、`FlowSession`、`ViewSpec` 與執行期上限。

#### Wave
- [Wave 快速入門](docs/zh/wave/README.md)：第一次執行批次、預覽與啟動差異，以及 `RunArchive`。
- [情境與工作流範例](docs/zh/wave/scenarios.md)：波形擷取、佇列控制、`logs`／`reanalyze` 與 NFS 保留策略。
- [網頁圖形介面指南](docs/zh/wave/gui.md)：版面模式、佇列操作、Log 篩選、Flow 檢視與波形快捷鍵。
- [Python API 參考](docs/zh/wave/reference/python.md) 與 [命令列參考](docs/zh/wave/reference/cli.md#一般與伺服器選項)：`WaveWorkspace`、`ArchivePolicy`、`RunArchive`、`rpk-wave` 旗標、`RPK_WAVE_*` 環境變數與結束代碼。
- [Flow 分析管線](docs/zh/wave/flow.md)、[歷史與封存機制](docs/zh/wave/history.md)、[系統架構](docs/zh/wave/architecture.md) 與 [疑難排解](docs/zh/wave/troubleshooting.md)。

#### mcucc
- [mcucc 快速入門與參考](docs/zh/mcucc/README.md)：目標宣告、`rpk-mcucc` CLI、`CompileConfig`、堆疊暫存與搭配 `isasim` 的差異驗證。

#### isasim
- [isasim 快速入門](docs/zh/isasim/README.md)：定義小型 `MCU`、執行程式並從快照重播。
- [使用情境](docs/zh/isasim/scenarios.md)：可重現 trace、引入檔、觀察點、`mem_alias` 與 `Device` 刺激訊號。
- [組譯器](docs/zh/isasim/assembler.md)、[API 與行為參考](docs/zh/isasim/reference.md) 與 [架構](docs/zh/isasim/architecture.md)：組譯語法、`Simulator`、`helpers`、CLI／REPL 命令與狀態邊界。

### 互動應用（`rpkbin.app.*`）

#### isasim_gui
- [isasim_gui 視覺化除錯器](docs/zh/app/isasim_gui/README.md)：啟動 `rpk-isasim-gui`、前進／回溯單步、條件斷點、雙擊修改暫存器與記憶體，以及 `DebugSession` API。

#### rpk-calc
- [rpk-calc 快速入門與參考](docs/zh/calc/README.md)：開啟簡易模式或 Python Studio、使用 `fxp`／`fxpi` 與 `rpk-calc --eval`、檢視矩陣與設定啟動檔。
- [架構與資料邊界](docs/zh/calc/architecture.md)：`SimpleCalcEngine`、kernel 所有權、JSON 快照與矩陣分頁上限。

## 授權

本專案程式碼採用 [MIT License](LICENSE)。內嵌 SVG 圖示包含 Feather（MIT）及 Lucide（ISC）設計，原始聲明收錄於 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，因此套件整體宣告為 `MIT AND ISC`。第三方相依套件保留各自授權，MIT 不會變更這些套件的授權。特別是 `rpk-calc` 使用 PySide6 / Qt for Python，其社群版本適用 [LGPL／GPL 授權條款](https://doc.qt.io/qtforpython-6/licenses.html)。`rpkbin` wheel 只包含本專案程式碼，未綑綁 Qt 或其他外部相依套件；若另外製作含有這些依賴的獨立執行檔，需另行確認各套件的授權與散布要求。
