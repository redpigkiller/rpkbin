# Codegen 目前進度與 TODO

此頁只記錄能力狀態。API 用法請看 [Codegen 文件](codegen_zh.md)。

## Stable

- `u8` / `u16`、`s8` / `s16` 基本型別與運算。
- HIR validation 與 HIR → LIR lowering。
- `if`、`while`、`poll`、short-circuit logical。
- Fixed-count `HFor`：可讀取 loop variable，支援 `break` / `continue`，但不可寫入 loop variable。
- volatile load/store 與 bit test/set。
- `HInsert` bit-field lowering。
- 單值與多值 return/call。
- Function、Fragment、Module validation/lowering API。
- Pattern rewrite hooks。
- Function 與 Fragment pseudo-ASM pipeline；提供 `RegisterModel` 時包含暫存器分配。

## Experimental

- Spill/reload path 與複雜 live-range translation 仍取決於 target，尚需更廣泛驗證。

## Trigger-based / not planned

| 項目 | 現況 |
| --- | --- |
| Module-level codegen pipeline | 目前 consumer 的 per-function/per-fragment pipeline 已足夠；只有實際 consumer 需要 module-wide selection 時才設計 |
| 32-bit lowering | 不規劃；至少有第二個 target 或實際 workload 需要時再設計多暫存器表示 |
| 一般化 `HFor` | 不規劃；frontend 應將其他迴圈表示為現有 structured `while` |
| Register allocation hardening | 只有 target 提供真實 spill storage，或實際 workload 暴露 allocator 問題時才擴充 |

## Codegen algorithm decision catalog

本節是 decision catalog，不是待辦清單。新技術只有在以下條件同時成立時才啟動：

1. 有可重現 workload 與量測，能指出現行演算法的具體失敗或成本。
2. 小修既有 lowering、selector 或 allocator 無法解決。
3. volatile、call、alias、fixed register 與 raw-asm effect boundary 已有明確模型。
4. 先以離線 prototype/oracle 證明收益，再決定是否進 production compile path。
5. solver、e-graph、property testing 等基礎設施優先採用成熟套件，不自行重寫。

| 技術 | 解決的問題 | 現況與啟動條件 | 難度 / 建議位置 |
| --- | --- | --- | --- |
| Local peephole / algebraic rewrite | 相鄰指令或純 expression 的明確冗餘 | 已有 matcher/rewrite hook；只有 pattern 保證不碰 effect、width 與 target policy 時使用 | 低～中；generic pure rule 放 rpkbin，ISA rule 放 target |
| DCE / copy propagation / coalescing | dead definition、copy chain、暫存器搬移 | 需要 statement liveness、完整 read/write 與 opaque barrier；不得跨 volatile、call、raw asm 或 fixed alias | 中～高；先做單一窄 pattern，不先建通用 optimizer |
| SSA + SCCP / GVN / PRE | 跨 basic block 的 constant、common expression 與資料流最佳化 | 只有多個實際 cross-block optimization 都被非 SSA IR 阻擋時才考慮；單一 pass 不足以合理化 SSA conversion | 高；rpkbin 架構工程 |
| Linear-scan allocation | 大量、近似線性 live interval 的快速分配 | 適合 JIT 或大函數追求 compile speed；不自然處理複雜 alias、fixed tuple 與稀少 registers | 中；不是目前 greedy coloring 的預設替代品 |
| Advanced graph coloring / coalescing | greedy allocator 有可行解卻失敗，或 copy 可安全合併 | 目前已有 interference graph + greedy coloring。只有人工或 oracle 證明存在合法配置時，才加入 simplify/coalesce、live-range split 或 spill cost | 高；rpkbin generic constraints，target 提供 alias/width |
| PBQP / ILP / CP-SAT allocation | register classes、tuple、alias、spill 等離散 constraints 過於複雜 | 先當小函數的離線 optimality oracle；只有 compile-time 可接受且 heuristic 長期失敗才進 production | 高～研究；不要手刻 solver，可評估 OR-Tools CP-SAT |
| BURG / BURS tree tiling | 大量重疊 instruction patterns，需要以 cost 覆蓋 expression tree | 只有 selector 出現許多互斥 pattern、穩定 cost model，且手寫選擇造成可量測損失時啟動；少數 pattern 繼續用現有 matcher/target selector | 高；generator 與 target pattern schema 都需設計 |
| E-graph / equality saturation | 純代數 expression 有多條 rewrite 路徑，固定 pass order 經常錯過最佳式 | effect-heavy、volatile 或 fixed-register IR 不適合。只有抽出足夠大的 pure region 且規則數持續成長時使用 | 高～研究；優先評估 `egglog`，不要自製 e-graph/e-matcher/extractor |
| SMT translation validation | 證明 rewrite 前後在 fixed-width bit-vector 語意等價 | 高風險 rewrite 或自動產生 pattern 時很有價值；適合作為測試/CI oracle，不應預設進每次 compile | 中～高；使用 `z3-solver`，不要自製 bit-vector solver |
| Enumerative / stochastic superoptimization | 很短的 hot sequence 搜尋最小合法 instruction sequence | 只適合 bounded、無 effect 或 effect model 完整的 instruction window；候選結果必須再驗證 | 研究；離線工具，搭配 emulator/exhaustive test 或 Z3 |
| Branch relaxation / block layout | relative branch range、trampoline 與 code size | 必須知道 final instruction size/layout；由 target/linker policy 決定，不應放 generic algebraic rewrite | 中～高；target-specific layout pass |
| Instruction scheduling | pipeline hazard、latency、dual issue 或 memory ordering | 只有 MCU timing model與 benchmark 顯示 scheduling 有收益時啟動；volatile ordering 是硬 boundary | 高；target-specific |
| PGO / basic-block placement | 真實 hot path、branch direction與 code layout | 需要穩定 runtime profile 收集與代表 workload；沒有 profile 時不要猜 | 高；target/toolchain feature |
| Property-based / differential testing | 大量 width、alias、CFG、rewrite edge cases | 在導入高風險 optimizer 前優先使用，可比較 interpreter/emulator、原始 IR 與 optimized IR | 中；測試工具，優先評估 Hypothesis |
| LLVM/MLIR migration | 多 target、多層 IR、既有 pass ecosystem | 只有 project 成為多 target compiler、現有 Python IR 成為主要瓶頸時才評估；不能只為使用一個 pass 而遷移 | 極高；另立專案，不在一般 roadmap 中順手進行 |

### External tools: do not reinvent

- Equality saturation：[egglog Python](https://egglog-python.readthedocs.io/latest/)。
- SMT / bit-vector proof：[Z3](https://github.com/Z3Prover/z3)，Python package 為 `z3-solver`。
- Constraint/optimality oracle：[OR-Tools CP-SAT](https://developers.google.com/optimization/cp)。
- Property-based generation 與 shrinking：[Hypothesis](https://hypothesis.readthedocs.io/)。
- `networkx` 可用於 CFG/graph prototype，但一般 graph coloring 不理解 register
  width、alias、fixed hints、calling convention 或 spill cost，不能直接當 production
  register allocator。
- BURG/BURS 生態與 host language 綁定較深；採用前先找仍維護且能整合目前 Python
  IR 的 generator。若沒有，不要因名稱漂亮就自行建立 generator；先證明現有 matcher
  與手寫 selector 已無法維護。

## Out of scope

- DSL parser。
- 真實 MCU ISA、register definitions 與 private rewrite patterns。
- assembler、linker 與 binary encoding。
