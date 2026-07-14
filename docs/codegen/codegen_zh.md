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
傳入 `None` 會略過兩者。Spill 支援仍取決於 target model。

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
