# rpkbin Documentation / 文件中心

Welcome to the documentation for **rpkbin**. Select your language or jump straight to a tool guide.

歡迎來到 **rpkbin** 文件中心，請選擇語言或直接前往對應工具文件。

[English Root README](../README.md) · [繁體中文首頁](../README_zh.md) · [Contributing](../CONTRIBUTING.md) · [CI Guide](../.github/ci.md)

- **[繁體中文（Traditional Chinese）](zh/README.md)**
  - [MapBV 硬體位元欄位工具](zh/mapbv/README.md)
  - [TablePack 表格壓縮編碼工具](zh/tablepack/README.md)
  - [JobCore 外部命令排程器](zh/jobcore/README.md)
  - [Flow 文字分析與自動化管線](zh/flow/README.md)
  - [Wave 工作排程、監控與封存](zh/wave/README.md)
  - [mcucc 受限 Python 編譯器](zh/mcucc/README.md)
  - [isasim 指令模擬器與組譯器](zh/isasim/README.md)
  - [isasim_gui 瀏覽器視覺化除錯器](zh/app/isasim_gui/README.md)
  - [rpk-calc 工程與定點數計算機](zh/calc/README.md)
- **[English](en/README.md)**
  - [MapBV Bitfield Library](en/mapbv/README.md)
  - [TablePack Table Codec](en/tablepack/README.md)
  - [JobCore Process Scheduler](en/jobcore/README.md)
  - [Flow Text Analysis and Automation](en/flow/README.md)
  - [Wave Command Scheduler, Monitor, and Archive](en/wave/README.md)
  - [mcucc Restricted Python Compiler](en/mcucc/README.md)
  - [isasim Instruction Simulator and Assembler](en/isasim/README.md)
  - [isasim_gui Visual Web Debugger](en/app/isasim_gui/README.md)
  - [rpk-calc Engineering and FXP Calculator](en/calc/README.md)

## Tools Overview / 工具概覽

| Tool / 工具 | Purpose / 用途 | Interfaces / 介面 |
| :--- | :--- | :--- |
| **MapBV** | Unsigned hardware register bitfields with live bidirectional views, `concat` buses, and symbolic evaluation. / 無號硬體暫存器位元欄位工具，支援切片雙向連動、`concat` 匯流排串接與符號求值。 | Python API (`rpkbin.mapbv`) |
| **TablePack** | Schema-driven codec that packs tabular rows or columns into compact `bytes` and restores them exactly without file I/O. / 以 schema 驅動的表格編解碼工具，把資料列或欄式序列打包成緊湊 `bytes` 並完整還原，不讀取檔案。 | Python API (`rpkbin.tablepack`) |
| **JobCore** | Process scheduler with worker and resource quotas, priority queues, guarded controls, and process-tree cleanup. / 具備 worker 與資源配額、優先權佇列、受保護控制與程序樹清理的命令排程器。 | Python API (`rpkbin.jobcore`) |
| **Flow** | Line-by-line parser, analyzer, automation, and `ViewSpec` chart projection inside an isolated `FlowSession`. / 在隔離的 `FlowSession` 中執行逐行解析、分析、自動化回應與 `ViewSpec` 圖表投影。 | Python API (`rpkbin.flow`) |
| **Wave** | Combines `jobcore` and `flow` with a local Web GUI, live/saved log ingestion, and SQLite run archives (`RunArchive`). / 整合 `jobcore` 與 `flow`，提供本機 Web GUI、即時／既有記錄匯入與 SQLite 封存資料庫。 | Python API (`rpkbin.wave`), Web GUI, CLI (`rpk-wave`) |
| **mcucc** | Compiles restricted Python into target assembly, runs static verification (`V1` to `V11`), and checks behavior against `isasim`. / 將受限 Python 編譯為目標組語、執行靜態驗證（`V1` 至 `V11`），並搭配 `isasim` 執行差異驗證。 | Python API (`rpkbin.mcucc`), CLI (`rpk-mcucc`) |
| **isasim** | Retargetable instruction simulator and two-pass assembler driven by a Python `MCU` model, with trace, debugging, and snapshots. / 依 Python `MCU` 模型執行指令模擬與兩階段組譯，提供 trace、除錯與快照。 | Python API (`rpkbin.isasim`), CLI (`rpk-isasim`) |
| **isasim_gui** | Loopback browser visual debugger on `rpkbin.isasim` with forward/reverse stepping, conditional breakpoints, and inline poke. / 建構於 `rpkbin.isasim` 之上的本機瀏覽器除錯器，支援前進／回溯單步、條件斷點與原位修改。 | Python API (`rpkbin.app.isasim_gui`), Web GUI, CLI (`rpk-isasim-gui`) |
| **rpk-calc** | Interactive Python workspace and desktop calculator for NumPy and APyTypes fixed-point (FXP) modeling. / 互動式 Python 與桌面定點數計算機，支援 NumPy、APyTypes 明確 FXP 轉換與矩陣編輯。 | Python API (`rpkbin.app.calc`), Qt GUI, headless CLI (`rpk-calc`) |

## CLI Entry Points / 命令列進入點

| CLI Command | Module (`python -m`) | English Reference | 繁體中文參考 |
|---|---|---|---|
| `rpk-wave` | `rpkbin.wave` | [rpk-wave CLI](en/wave/reference/cli.md) | [rpk-wave 命令列參考](zh/wave/reference/cli.md) |
| `rpk-isasim` | `rpkbin.isasim` | [isasim CLI and REPL](en/isasim/reference.md#cli-and-repl) | [isasim CLI 與 REPL](zh/isasim/reference.md#cli-與-repl) |
| `rpk-isasim-gui` | `rpkbin.app.isasim_gui` | [isasim_gui CLI](en/app/isasim_gui/README.md#cli-reference) | [isasim_gui CLI 參數參考](zh/app/isasim_gui/README.md#cli-參數參考) |
| `rpk-mcucc` | `rpkbin.mcucc` | [mcucc CLI](en/mcucc/README.md#cli-reference) | [mcucc CLI 參考](zh/mcucc/README.md#cli-參考) |
| `rpk-calc` | `rpkbin.app.calc` | [rpk-calc CLI](en/calc/README.md#cli-reference) | [rpk-calc CLI 參考](zh/calc/README.md#cli-參考) |
