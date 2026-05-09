# rpkbin — IC 設計與驗證核心工具庫

[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](README_zh.md)

`rpkbin` 提供硬體設計與驗證流程常用的資料型別與工具，重點在於可組合、可維護、可落地到日常自動化。

## 核心功能

### 1. MapBV — 可映射的 BitVector
MapBV 讓你把一個大寄存器切成多個欄位，並保持雙向同步。
- **階層切片**：例如把 32-bit register 拆成具名欄位。
- **雙向連動**：用 `concat` 組合出的 view，更新 view 會回寫來源。
- **假設推演**：用 `.eval()` 做 what-if 測試，不汙染真實值。

[閱讀 MapBV 文件](docs/mapbv/mapbv_zh.md)

### 2. NumBV — Bit-True 定點數運算
Bit-exact 定點數模擬，專為 DSP pipeline 驗證設計。純 NumPy，無外部相依套件。
- **兩層 API**：Operator 層（`+`、`*`）方便日常計算；函式層（`nbv.add()`、`nbv.mul()`）用於精確 pipeline 驗證。
- **五種 Rounding 模式**：`trunc`、`round`、`round_half_even`（Convergent，Xilinx DSP48）、`ceil`、`round_to_zero`。
- **Scalar & Array 統一**：單一類別處理純量與陣列。預設以純 NumPy 為底層，並可透過 `set_backend("jax")` 獲得透明的 XLA 硬體加速。

[閱讀 NumBV 文件](docs/numbv/numbv_zh.md)

### 3. Excel Extractor — 樣板式資料擷取
針對複雜 Excel 版面進行穩定擷取。
- **版面描述導向**：描述資料形狀，而非硬編座標。
- **模糊比對**：標頭有小差異也能匹配。
- **合併儲存格支援**：可正確還原跨列跨欄資料。

[閱讀 Excel Extractor 文件](docs/excel_extractor/excel_extractor_zh.md)

### 4. CFG — 低階控制流工具組
在撰寫 target-specific code 之前，先整理 assembly-like flows、FSM state machines 與 MCU branch layouts。
- **明確的 Flow Modeling**：建立 labeled blocks 與依 priority 排序的 branch edges，不綁定特定 ISA。
- **好讀的檢查與 Layout**：驗證常見控制流錯誤、輸出文字 layout，並取得 deterministic 的 block 發射順序。
- **Program Call 檢查**：用 `CallRef` 標註 subroutine calls，並檢查 call depth 是否符合硬體或 coding-rule 限制。

[閱讀 CFG 文件](docs/cfg/cfg_zh.md)

### 5. Job Manager
一個實用、跨平台的工作管理器，可安全地平行執行 Shell 指令與 Python 函式。

- **同一套 API 管兩種工作**：`FuncJob` 跑 Python；`CmdJob` 跑 CLI。
- **資源感知排程**：可用 `gpu`、`license`、`slot` 等全域資源限制併發。
- **除錯友善**：支援取消、重試、即時 logs 與 callback，方便串接自動化流程。

[閱讀完整 Job Manager 文件](docs/job_manager/job_manager_zh.md)

### 6. Wave — 帶即時 TUI 的批次流程排程器
建立在 Job Manager 之上的 workflow layer，用來宣告、執行與觀測長時間批次流程。

- **Wave File 模式**：在一個 Python 檔案裡宣告 jobs、parsers 與 hooks，再用 `rpk-wave run` 執行。
- **即時 TUI**：全螢幕 Textual 介面，包含 job 總覽、per-job log/data/event 面板，以及底部的指令列。
- **Headless REPL**：不想使用 TUI 時（例如 CI），透過 stdin 做完整的 inspection 與 control。
- **Hooks 與 Parsers**：自動對 log 輸出、結構化 parsed data、執行時間或生命週期事件做反應。
- **事件來源追蹤**：`job.emit()` 記錄具名事件；`source="system"` 標記內部錯誤（parser / hook 失敗），方便在 TUI 中過濾。
- **執行控制**：可在 TUI command bar 或 headless REPL 中 `pause` / `resume` dispatch、graceful stop job、force-cancel job，或用 tag 對一群 active jobs 操作。
- **快速檢視 job**：`show`、`logs`、`data`、`events <job>` 會直接開到 JOB DETAIL；在 detail 中可用 `[` / `]` 切換 jobs，或用 `{` / `}` 切換 running jobs。
- **明確的取消語意**：使用者主動取消的 job 會顯示為 `cancelled`，但不會讓整個 Wave run 變成失敗 exit；真正 failed 的 job 與 session timeout 仍會回傳非零 exit code。

[閱讀 Wave 文件](docs/wave/wave_zh.md)

## 安裝

```bash
# 只裝核心模組（最少相依套件）
pip install -e .

# 依照需求安裝特定功能的相依套件
pip install -e .[wave]   # 安裝 textual, prompt_toolkit, rich
pip install -e .[excel]  # 安裝 openpyxl, xlrd, rapidfuzz
pip install -e .[cfg]    # 安裝 networkx

# 安裝所有功能（建議）
pip install -e .[all]
```

## 測試

在根目錄使用 `pytest`：
```bash
pytest tests/ -v
```

（所有測試只需要 `numpy`，不需要額外相依套件。）
