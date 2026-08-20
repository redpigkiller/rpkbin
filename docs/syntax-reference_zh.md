# rpkbin compiler IR reference

`rpkbin.codegen` 不定義 source-language grammar，因此本頁是給 frontend 使用的 IR construction reference，不表示 package 會解析 textual DSL。

## 支援的 type nodes

- `UInt(width)` 與 `SInt(width)` 是 signed/unsigned HIR types；目前 lowering 支援 8 與 16 bit。
- `Void` 代表沒有回傳值的 function。
- 32-bit types 可以表示，但目前尚未支援 lowering。

## HIR 入口

| Input | 用途 | 主要 API |
| --- | --- | --- |
| `HFunction` | 編譯單一 function | `run_codegen_from_hir()` |
| `HFragment` | 編譯具 explicit bindings 的 fragment | `run_codegen_from_fragment()` |
| `HModule` | 驗證多個 functions、externs 或 symbols | `validate_hmodule()`、`lower_module()` |

目前沒有一個呼叫就完成 `HModule` 到 pseudo-ASM 的 pipeline。

## Expressions 與 statements

公開 HIR constructors 包含 `HConst`、`HVar`、`HBinOp`、`HCmp`、`HLogical`、`HCast`、`HExtract`、`HInsert`、`HLoad`、`HCall` 等 expressions，以及 `HAssign`、`HBitSet`、`HStore`、`HExprStmt`、`HCallAssign`、`HIf`、`HWhile`、`HFor`、`HPoll`、`HBreak`、`HContinue`、`HReturn`、`HInlineAsm`、`HExit` 等 statements。

請使用 `rpkbin.codegen` export 的 typed constructors；frontend 不應依賴 private LIR node shapes。

## Pipeline 與錯誤階段

```text
HIR
 └─ validate_hfunction / validate_hfragment
     └─ lower_function / lower_fragment → LIR
         └─ validate_function / validate_fragment
             └─ optional rewrite
                 └─ post-rewrite validation
                     └─ optional register allocation
                         └─ Target instruction selection → pseudo ASM
```

| Error | 發生時機 | 常見修正 |
| --- | --- | --- |
| `HIRValidationError` | HIR types、control flow、calls 或 target/register hints 不符合 contract | 修正 frontend 產生的 HIR 或 target model |
| `NotImplementedError` | HIR 使用了目前 lowering 尚未支援的 construct | 限制輸入在 status 文件列出的 subset |
| `ValueError` | 產生的 LIR 未通過 structural validation，或 fragment 沒有 register model 卻仍有 unresolved locals | 檢查 lowered shape，並為 fragment 提供合適的 `RegisterModel` |
| Register allocation error | 可用 registers 無法滿足 live-value constraints | 提供相容 register model 或降低 register pressure；production spilling 尚未實作 |

## 最小 function 範例

```python
from rpkbin.codegen import HFunction, HReturn, UInt, hconst, run_codegen_from_hir
from rpkbin.codegen.toy_target import ToyTarget

func = HFunction(
    name="answer",
    params=(),
    return_ty=UInt(8),
    body=(HReturn(values=(hconst(42),)),),
)
result = run_codegen_from_hir(func, ToyTarget())
print(result.asm_text)
```

`ToyTarget` 是 reference/test target，不是 production MCU backend。
