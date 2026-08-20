# rpkbin 架構

`rpkbin` 是由多個聚焦的 Python 模組組成，而不是一個強制性的單一 runtime。使用者依資料模型選擇模組，需要跨模組 workflow 時再自行組合。

## 頂層流程

```text
Python / Excel / HIR input
        │
        ├─ MapBV        register 與 bit-slice 關係
        ├─ NumBV        fixed-point 數值與運算
        ├─ Excel       template matching 與資料擷取
        ├─ CFG         blocks、edges、calls 與 deterministic layout
        ├─ StageTracker workflow stages、issues 與 summaries
        │
        ├─ Wave CLI/TUI
        │       └─ Session ──> Job Manager ──> CmdJob / FuncJob / PtyJob
        │
        └─ Codegen
                HIR ─> HIR validation ─> LIR lowering ─> rewrite
                    ─> optional register allocation ─> Target selection ─> pseudo ASM
```

## 元件責任

- `rpkbin.mapbv` 管理具名 bit-vector、slices、concatenated views 與 symbolic evaluation；不處理 fixed-point scaling。
- `rpkbin.numbv` 管理 fixed-point formats、quantization、overflow/rounding、scalar/array values 與可選的 JAX backend。backend 是 process-global，必須在建立物件前選定。
- `rpkbin.excel_extractor` 將 workbook cells 正規化，再比對 template 形狀；template 描述預期結構，而不是固定座標。
- `rpkbin.cfg` 管理 target-neutral blocks、edges、calls、validation、analysis、diffs 與 FSM/MCU layout recipes；不負責 parse assembly、allocate registers 或定義 ISA。
- `rpkbin.utils.stage_tracker` 為呼叫端 workflow 記錄多階段進度、issues、timing 與 summaries。

## Job Manager 與 Wave

`JobManager` 是 execution layer，排程 `CmdJob`、`FuncJob`，並提供 bounded workers、priority、retry、cancellation、callbacks 與 resource capacities。它可以直接在 Python 使用，也提供明確的 `start`、`add`、`wait`、`stop` lifecycle 與 context manager。

Wave 是建立在其上的 user-facing session layer。wave file 設定 singleton `session` 並註冊 jobs；`rpk-wave run` 載入檔案、套用 CLI overrides、啟動 manager，再選擇全螢幕 TUI 或 headless wait/REPL。Job execution 仍在本機 process 中進行；Wave 不提供 remote scheduler。

## Codegen

Codegen 的 target-neutral contract 如下：

1. Frontend 建立 `HFunction`、`HFragment` 或 `HModule`。
2. HIR validation 回報 source-level type 與 contract errors。
3. Function/fragment lower 成 LIR，再做 structural validation。
4. 可選的 rewrite patterns 執行後，再做一次 LIR validation。
5. 提供 `RegisterModel` 時才做 physical-register validation 與 allocation；沒有 model 時，function 會跳過 allocation，fragment 的 unresolved locals 則 fail closed。
6. 注入的 `Target` 或 `FragmentTarget` 選擇 instructions 並回傳 pseudo-ASM。

不同階段有不同錯誤：錯誤的 HIR 會產生 `HIRValidationError`，尚未支援的 construct 可能產生 `NotImplementedError`，無效 LIR 會產生 `ValueError`，register pressure 則可能產生 allocation error。本 package 不擁有 DSL parser、MCU ABI、real ISA、assembler、linker 或 binary encoder。

## 重要假設

- Optional dependencies 不會隨 core install 載入；使用 optional feature 前必須安裝對應 extra。
- Wave file 是可執行的 Python，應視為 trusted local code，並讓 side effects 保持明確。
- Job resource names 與 capacities 是 scheduler contract；要求超過 capacity 的 job 無法被 admission。
- Codegen 的 target 與 register model 是注入的 protocols；hardware semantics、ABI 與 final encoding 留在 target/frontend package。
- Codegen register allocation 不是通用 spilling system；在 target-level contract 完成前，production spill/reload 會刻意停用。

## 設計決策

### Wave 保持在 Job Manager 之上

**背景：** job scheduling 與 user-facing observability 是不同責任。

**決策：** `JobManager` 專注排程/執行，wave files、parsers、hooks、TUI 與 headless controls 放在 `rpkbin.wave`。

**結果：** Python caller 不需安裝 Wave extra 也能使用小型 manager；Wave UI 也能在不改變 scheduler contract 的前提下演進。

### Codegen 委派 target policy

**背景：** reusable backend 不應猜測每個 MCU 的 ABI、register aliases、flags 或 instruction encoding。

**決策：** 注入 `Target`、`FragmentTarget` 與 `RegisterModel` implementations。

**結果：** package 可以共用 IR validation/lowering，但 hardware-specific policy 與 final emission 必須由 frontend/target package 提供。

### Excel 使用 template 描述結構

**背景：** 固定 cell 座標會因列、欄或 merged cells 移動而失效。

**決策：** Excel Extractor 對 normalized workbook grid 比對宣告的 row/group/block shapes。

**結果：** template 能承受更多版面變化，但 matching 品質仍取決於 template conditions 是否足夠明確。
