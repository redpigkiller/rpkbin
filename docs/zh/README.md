# rpkbin 文件導覽

[English](../en/README.md) · [雙語文件入口](../README.md) · [專案首頁](../../README_zh.md) · [貢獻指南](../../CONTRIBUTING.md) · [CI 與套件檢查](../../.github/ci.md)

`rpkbin` 是一套針對 IC 設計、硬體建模與驗證流程開發的 Python 工具集，分為 **核心函式庫與引擎（`rpkbin.*`）** 與 **互動應用（`rpkbin.app.*`）**。

## 模組總覽

### 核心函式庫與引擎（`rpkbin.*`）

| 模組 | 套件／CLI | 說明 |
|---|---|---|
| **MapBV** | `rpkbin.mapbv` | 無號硬體暫存器建模工具，支援閉區間雙向同步切片、`concat` 匯流排串接、唯讀常數與延遲組合邏輯樹。 |
| **TablePack** | `rpkbin.tablepack` | 以 schema 驅動的編解碼工具，將資料列或欄式序列打包成緊湊 `bytes` 並完整還原，不讀取檔案。 |
| **JobCore** | `rpkbin.jobcore` | 外部命令排程器，提供 worker 上限、資源配額、優先權排程、受保護程序控制與整棵程序樹清理。 |
| **Flow** | `rpkbin.flow` | 在隔離的 `FlowSession` 中執行逐行解析、分析、自動化回應與 `ViewSpec` 圖表投影。 |
| **Wave** | `rpkbin.wave`、`rpk-wave` | 整合 `jobcore` 與 `flow`，提供本機 Web GUI、即時／既有記錄匯入與 SQLite 封存資料庫（`RunArchive`）。 |
| **mcucc** | `rpkbin.mcucc`、`rpk-mcucc` | 將受限 Python 編譯為目標組語、執行靜態驗證（`V1` 至 `V11`），並搭配 `isasim` 執行差異驗證。 |
| **isasim** | `rpkbin.isasim`、`rpk-isasim` | 依 Python `MCU` 模型執行指令模擬與兩階段組譯，提供 trace、除錯與快照。 |

### 互動應用（`rpkbin.app.*`）

| 應用 | 套件／CLI | 說明 |
|---|---|---|
| **isasim_gui** | `rpkbin.app.isasim_gui`、`rpk-isasim-gui` | 建構於 `rpkbin.isasim` 之上的本機瀏覽器除錯器，支援前進／回溯單步、條件斷點、雙擊原位修改與區間碼錶。 |
| **rpk-calc** | `rpkbin.app.calc`、`rpk-calc` | 桌面計算機與 IPython 工作區，整合 NumPy 與 APyTypes 定點數（FXP）建模，另提供無介面 `--eval` CLI。 |

## CLI 參考總覽

五個命令列指令皆支援 `-V`／`--version`，並與對應的 `python -m` 模組入口共用參數與行為：

| CLI 指令 | 模組入口 | 參考文件 |
|---|---|---|
| `rpk-wave` | `python -m rpkbin.wave` | [rpk-wave 命令列參考](wave/reference/cli.md) |
| `rpk-isasim` | `python -m rpkbin.isasim` | [isasim CLI 與 REPL](isasim/reference.md#cli-與-repl) |
| `rpk-isasim-gui` | `python -m rpkbin.app.isasim_gui` | [isasim_gui CLI 參數參考](app/isasim_gui/README.md#cli-參數參考) |
| `rpk-mcucc` | `python -m rpkbin.mcucc` | [mcucc CLI 參考](mcucc/README.md#cli-參考) |
| `rpk-calc` | `python -m rpkbin.app.calc` | [rpk-calc CLI 參考](calc/README.md#cli-參考) |

## 核心函式庫與引擎（`rpkbin.*`）

### MapBV 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[MapBV 快速入門](mapbv/README.md)** | 建立變數與常數、位元寬度、即時切片與十六進位／二進位格式化。 | 初次使用 MapBV 者 |
| **[實戰使用情境](mapbv/scenarios.md)** | 雙向切片同步、`concat` 匯流排、唯讀位元防護、符號求值 `eval()` 與常見誤區。 | 撰寫暫存器模型與驗證測試者 |
| **[API 參考手冊](mapbv/reference.md)** | `MapBV` 與 `MapBVExpr`、工廠函式（`var`、`const`、`concat`）、運算子語意與例外。 | 查閱精確型別與例外行為 |

### TablePack 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[TablePack 快速入門](tablepack/README.md)** | 無 schema 打包、自訂欄位描述、`infer` 與 `explain`。 | 初次使用 TablePack 者 |
| **[實戰使用情境](tablepack/scenarios.md)** | 各種欄位型別、JSON schema、位元組分佈分析、共用 schema、壓縮選項與錯誤判讀。 | 需要儲存或傳遞表格的工程師 |
| **[API 參考手冊](tablepack/reference.md)** | `pack`、`unpack`、`iter_unpack`、`infer`、`explain`、`Schema`、`DecodeLimits`、欄位類別與二進位格式。 | 查閱精確行為 |

### JobCore 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[JobCore 快速入門](jobcore/README.md)** | 執行隔離命令、context manager、佇列暫緩與 `ProcessVerdict`。 | 初次使用 JobCore 者 |
| **[實戰使用情境](jobcore/scenarios.md)** | 資源配額、排程模式、佇列重排、互動提示回應、重試、逾時與 `ProcessHandle`。 | 需要排程與控制外部程序的工程師 |
| **[API 參考手冊](jobcore/reference.md)** | `JobManager`、`Job`、`ControlReceipt`、`ProcessHandle`、狀態列舉與生命週期規則。 | 查閱排程器契約 |

### Flow 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[Flow 快速入門](flow/README.md)** | `Parser`、`Analyzer`、`Automation` 與 `FlowSession` 基本操作。 | 初次使用 Flow 者 |
| **[實戰使用情境](flow/scenarios.md)** | 多階段串接、`Tick` 監視計時器、`EndInfo`、`ViewSpec` 圖表與錯誤隔離。 | 撰寫記錄檔分析管線的工程師 |
| **[API 參考手冊](flow/reference.md)** | 管線定義、Context、`FlowSession`、`ViewSpec` 與執行期上限。 | 查閱精確管線 API |

### Wave 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[Wave 快速入門](wave/README.md)** | 執行第一批工作、理解預覽與啟動狀態，以及查詢 `RunArchive`。 | 初次使用 Wave 者 |
| **[情境與工作流範例](wave/scenarios.md)** | 波形擷取、佇列控制、`logs`／`reanalyze` 與 NFS 保留策略設定。 | 撰寫驗證腳本的工程師 |
| **[Flow 分析管線](wave/flow.md)** | 在 Wave 中串接 `rpkbin.flow` 解析器、圖表與互動提示。 | 分析即時模擬記錄的工程師 |
| **[網頁圖形介面指南](wave/gui.md)** | 版面模式、佇列暫緩／排序、Log 篩選、Flow 分頁與波形快捷鍵。 | 在瀏覽器監看工作的工程師 |
| **[Python API 參考](wave/reference/python.md)** | `WaveWorkspace`、`WaveServer`、`ArchivePolicy`、`RunArchive` 與本機 HTTP 路由。 | 查閱 Wave Python 契約 |
| **[命令列參考](wave/reference/cli.md)** | `rpk-wave` 旗標、`logs`／`reanalyze` 選項、`RPK_WAVE_*` 環境變數與結束代碼。 | 整合 CI 或 shell 腳本 |
| **[歷史與封存機制](wave/history.md)** | 封存命名規則、保留模式、NFS 暫存分段與自動過期清理。 | 管理長期執行記錄 |
| **[系統架構](wave/architecture.md)** | 套件邊界、執行緒分工、生命週期狀態與本機安全限制。 | 維護或擴充 Wave 的開發者 |
| **[疑難排解](wave/troubleshooting.md)** | 輸出緩衝、`--host` 拒絕、提示延遲、圖表上限與封存錯誤排查。 | 排查非預期行為 |

### mcucc 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[mcucc 快速入門與參考](mcucc/README.md)** | 目標宣告、`rpk-mcucc` CLI、`CompileConfig`、堆疊暫存、`borrow_msb0` 與搭配 `isasim` 的差異驗證。 | 編譯器使用者與目標作者 |
| **[內建語言與目標規格](../../rpkbin/mcucc/doc/language.md)** | 詳細來源語言子集（`language.md`）、目標契約（[`target.md`](../../rpkbin/mcucc/doc/target.md)）、靜態保證（[`guarantees.md`](../../rpkbin/mcucc/doc/guarantees.md)）、錯誤目錄（[`errors.md`](../../rpkbin/mcucc/doc/errors.md)）與[隨附範例](../../rpkbin/mcucc/examples/README.md)。 | 撰寫自訂 MCU 編譯器目標 |

### isasim 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[isasim 快速入門](isasim/README.md)** | 定義小型 `MCU`、執行程式並從快照重播。 | 初次使用 isasim 者 |
| **[使用情境](isasim/scenarios.md)** | 可重現 trace、引入檔、觀察點、`mem_alias` 與 `Device` 刺激訊號。 | 開發或測試指令模型者 |
| **[組譯器與來源除錯](isasim/assembler.md)** | 運算元宣告、原始碼語法、`.include`/`.if` 前處理與 `.isa.json` bundle。 | 撰寫組語與可攜式 bundle |
| **[API 與行為參考](isasim/reference.md)** | `MCU`、`Simulator`、`Device`、`Snapshot`、`helpers`、CLI 旗標與 REPL 命令。 | 查閱精確 API 與命令 |
| **[架構](isasim/architecture.md)** | 狀態所有權、交易回滾與記憶體別名不變條件。 | 模型作者與維護者 |

## 互動應用（`rpkbin.app.*`）

### isasim_gui 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[isasim_gui 視覺化瀏覽器除錯器](app/isasim_gui/README.md)** | 啟動 `rpk-isasim-gui`、前進／回溯單步（`Over` / `In` / `Out` / `Back`）、條件斷點、雙擊修改暫存器與記憶體，以及 `DebugSession` API。 | 撰寫與除錯 MCU 組譯程式的工程師 |

### rpk-calc 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[rpk-calc 快速入門與參考](calc/README.md)** | 使用簡易模式或 Python Studio、`rpk-calc --eval`、`fxp`/`fxpi`、位元移位、矩陣編輯與啟動設定。 | 進行定點數建模的工程師 |
| **[架構與資料邊界](calc/architecture.md)** | `SimpleCalcEngine`、kernel 所有權、JSON 快照、矩陣分頁上限與啟動檔權責。 | 維護者與進階工作區使用者 |
