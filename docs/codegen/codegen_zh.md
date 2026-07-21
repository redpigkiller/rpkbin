# rpkbin.codegen — MCU 編譯器後端

[English](codegen.md) | [目前進度與 TODO](status_zh.md)

`rpkbin.codegen` 接收 Frontend 建立的 HIR，驗證並降低為 LIR，套用選擇性的改寫規則，再交給外部 `Target` 產生 pseudo ASM。

它刻意不包含 DSL parser、真實 MCU ISA、assembler、linker 或 binary encoding。

## 選擇入口

| 輸入 | 使用時機 | API |
| --- | --- | --- |
| `HFunction` | 單一函式 | `validate_hfunction`、`run_codegen_from_hir` |
| `HFragment` | 具明確 binding 的片段 | `validate_hfragment`、`run_codegen_from_fragment` |
| `HModule` | 多函式、extern 或 global symbol | `validate_hmodule`、`lower_module` |

目前沒有 `HModule` 到 pseudo ASM 的一鍵 pipeline。Module 應先完成 cross-reference 驗證，再由呼叫端決定各 function 的 codegen 流程。

## Function 快速開始

```python
from rpkbin.codegen import HFunction, HParam, HReturn, UInt, hconst, run_codegen_from_hir
from rpkbin.codegen.toy_target import ToyTarget

func = HFunction(
    name="answer",
    params=(HParam("x", UInt(8)),),
    return_ty=UInt(8),
    body=(HReturn(values=(hconst(42),)),),
)

result = run_codegen_from_hir(func, ToyTarget())
print(result.asm_text)
```

`ToyTarget` 只供測試與展示，不是 production target。

## Pipeline

```text
HIR
 └─ validate
     └─ lower to LIR
         └─ optional rewrite
             └─ 提供 RegisterModel 時進行 register allocation
                 └─ Target instruction selection
                     └─ pseudo ASM
```

提供 `RegisterModel` 時，pipeline 會依 target register file 驗證並執行暫存器分配；
傳入 `None` 會略過兩者。暫存器壓力目前會 fail closed；production spill 尚未實作。

## 離線 bounded-region semantics

`rpkbin.codegen.region_semantics` 可以找出小型、pure、acyclic、
single-entry/single-exit 的 LIR region，並具體執行其 fixed-width 語意。
遇到 call、volatile memory、raw assembly、loop、signedness 不明的
relational `BrCmp` 或不支援的 expression 時會 fail closed。這個 API 供
離線 rule mining、differential check 與 target experiment 使用；它不包含
solver、ISA、instruction cost 或 production rewrite policy。

## 延伸文件

- [Frontend / external DSL frontend 接入指南](frontend_integration_zh.md)
- [HIR 使用參考](hir_zh.md)
- [LIR 使用參考](lir_zh.md)
- [目前進度與 TODO](status_zh.md)

## 穩定化邊界與相容性契約

`rpkbin.codegen` 擁有 target-neutral 的 HIR/LIR、validation/lowering、CFG/dataflow、
allocator mechanics、rewrite mechanics、target protocols 與 pseudo-ASM pipeline。
`Target`、`FragmentTarget`、`RegisterModel` 是 backend package 實作的公開 protocol，
不包含任何特定 MCU 的 ABI、register file 或 flag convention。

MyRtkPkg/UC frontend 應擁有 DSL grammar/parser、UC register/alias/bit mapping、ABI、
`CY`/`Z`/`b_signext` 等硬體語意、UC-specific patterns/costs，以及 real assembly/encoding；
這些 policy 不應移入 `rpkbin.codegen`。

`HFragment` 的 acyclic control flow、`HExit` 與 phased scratch-register reuse 是既有的
窄相容契約；本 package 只記錄、不中途泛化為新的 FragmentPolicy。`region_semantics`
仍留在 codegen 作為 offline、target-neutral facility。CEGIS 是 experimental/orphan，
不在 production pipeline，也沒有 committed consumer。

`RewritePattern.cost_delta` 僅為 reserved compatibility metadata，目前不參與 rule
selection；本 package 沒有 cost-based optimizer。rewrite 對含 `Call`、volatile `MemLoad`、
`InlineAsmExpr` 或 `SymbolAddr` 的整個 expression subtree 採保守 effect boundary，不會
被 wildcard rule 消除。

rewrite 採 declaration order。每個 expression 預設允許最多 256 次已接受的 state transition
（`max_steps`）；最後一次確認 stable 的 probe 不消耗 transition。另有 16,384 nodes 的
`max_nodes` budget，初始 expression 與每個 candidate 都會在 cycle state hash 前檢查。exact
cycle 或非重複成長都會拋出 `RewriteConvergenceError`，診斷包含 last rule、completed steps、
expression node count 與 seen-state count。這些 guard 限制 rewrite pass 的異常成長，不宣稱
可處理任意外部建立的大型 expression。replacement 會保留
captured ref 的 metadata，並從 matched expression context 推導新建 integer 的 result/operand
width；comparison 與 `BitOp("test")` result 固定為 width 1，comparison signedness 保留自
被取代的 comparison。root replacement 的已知 result width 若與 template 不同會 fail closed。
無法可靠推導的 type-changing replacement 會清楚拒絕，絕不靜默退回 8-bit/unsigned。
