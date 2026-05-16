# rpkbin — IC 設計與驗證核心工具庫

[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](README_zh.md)

`rpkbin` 是一組偏實務導向的硬體設計與驗證工具。它包含 bit-true modeling、Excel 資料擷取、控制流程建模，以及長時間 batch workflow 監控等小而專注的工具。

如果你是第一次看這個 repo，可以先從你想解決的問題開始：

| 我想要... | 先看 |
| --- | --- |
| 同時跑很多 shell / Python jobs，並即時監控 | [Wave](docs/wave/wave_zh.md) |
| 在 Python 裡平行執行 commands / functions | [Job Manager](docs/job_manager/job_manager_zh.md) |
| 建模 register 與 bit fields | [MapBV](docs/mapbv/mapbv_zh.md) |
| 模擬 fixed-point arithmetic | [NumBV](docs/numbv/numbv_zh.md) |
| 從 Excel 抽出結構化資料 | [Excel Extractor](docs/excel_extractor/excel_extractor_zh.md) |
| 描述或檢查低階 control flow | [CFG](docs/cfg/cfg_zh.md) |

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

- **一般 Python 檔就是 wave file**：在 `.py` 裡宣告 jobs、parsers、hooks 與 actions。
- **預設開啟即時 TUI**：Dashboard、per-job logs、parsed data、events、system messages 與 command bar。
- **需要時可用 headless mode**：CI 或不想開 TUI 時使用 `--no-tui`。
- **執行控制**：rerun job、依 job/tag 停止或取消、送 stdin、送 OS signal、或送 PTY terminal key。
- **自動化 hooks**：對 log pattern、parsed data、經過時間、生命週期事件或自訂 action 做反應。

[閱讀 Wave 文件](docs/wave/wave_zh.md)

## 快速開始：Wave

安裝 Wave 需要的相依套件：

```bash
pip install -e .[wave]
```

建立 `hello.wave.py`：

```python
from rpkbin.wave import session, CmdJob

session.configure(max_workers=2)

session.add(CmdJob("hello", "python -c \"print('hello from wave')\""))
session.add(CmdJob("list", "python -c \"import os; print(os.getcwd())\""))
```

執行：

```bash
rpk-wave run hello.wave.py
```

常用 TUI 指令：

```text
status
logs hello
show hello
rerun hello
stop hello
```

如果是在 CI 或只想看純文字輸出：

```bash
rpk-wave run hello.wave.py --no-tui
```

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
