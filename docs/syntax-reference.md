# rpkbin compiler IR reference

`rpkbin.codegen` does not define a source-language grammar. This page is therefore an IR construction reference for frontends, not a claim that the package parses a textual DSL.

## Supported type nodes

- `UInt(width)` and `SInt(width)` are the signed and unsigned integer HIR types currently supported by lowering for 8- and 16-bit widths.
- `Void` represents a function with no return value.
- 32-bit types can be represented but are not lowered yet.

## HIR entry points

| Input | Use | Main API |
| --- | --- | --- |
| `HFunction` | Compile one function | `run_codegen_from_hir()` |
| `HFragment` | Compile a fragment with explicit bindings | `run_codegen_from_fragment()` |
| `HModule` | Validate multiple functions, externs, or symbols | `validate_hmodule()`, `lower_module()` |

There is no one-call `HModule`-to-pseudo-ASM pipeline yet.

## Expressions and statements

The public HIR constructors include expressions such as `HConst`, `HVar`, `HBinOp`, `HCmp`, `HLogical`, `HCast`, `HExtract`, `HInsert`, `HLoad`, and `HCall`, plus statements such as `HAssign`, `HBitSet`, `HStore`, `HExprStmt`, `HCallAssign`, `HIf`, `HWhile`, `HFor`, `HPoll`, `HBreak`, `HContinue`, `HReturn`, `HInlineAsm`, and `HExit`.

Use the typed constructors exported from `rpkbin.codegen`; do not depend on private LIR node shapes from a frontend.

## Pipeline and error stages

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

| Error | Raised when | Likely fix |
| --- | --- | --- |
| `HIRValidationError` | HIR types, control flow, calls, or target/register hints violate the HIR contract | Correct the frontend-produced HIR or target model |
| `NotImplementedError` | The HIR uses a construct not supported by the current lowering implementation | Restrict the input to the supported status subset |
| `ValueError` | Generated LIR fails structural validation, or a fragment has unresolved locals without a register model | Inspect the lowered shape and provide a suitable `RegisterModel` for fragments |
| Register allocation error | Available registers cannot satisfy the live-value constraints | Provide a compatible register model or reduce register pressure; production spilling is not implemented |

## Minimal function example

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

`ToyTarget` is a reference/test target. It is not a production MCU backend.
