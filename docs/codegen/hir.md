# HIR Reference (Frontend Authors)

[繁體中文](hir_zh.md) | [Status and TODO](status.md)

HIR is the typed, structured input contract for `rpkbin.codegen`. Frontends should construct HIR and avoid depending on LIR internals.

## Types

- `UInt(8|16)` and `SInt(8|16)` are supported by lowering.
- `Void` represents no return value.
- 32-bit types are reserved but not lowered yet.

## Expressions

| Node | Purpose | Main restriction |
| --- | --- | --- |
| `HConst` | Typed constant | Value must fit its type |
| `HVar` | Named value with optional register hint | Hint validity depends on `RegisterModel` |
| `HBinOp` | Arithmetic and bitwise operation | Shift amounts must be constants |
| `HCmp` | Comparison | Condition positions only |
| `HLogical`, `HNot` | Short-circuit logic | Condition positions only |
| `HBitTest` | Test one bit | Condition positions only; constant bit index |
| `HCast` | Byte extraction or signedness/width cast | Supported cast kinds are validated; `u16_from` / `s16_from` lower to `lir.Extend` |
| `HExtract` | Extract a bit range | Constant, valid range |
| `HInsert` | Replace a bit range | Lowered with masks, shifts, and OR |
| `HConcat` | Concatenate values | Result width must match both inputs |
| `HLoad` | Volatile memory read | Load ordering is preserved |
| `HCall` | Single-expression function call | Signature checked at module level |
| `HSymbolAddr` | Address of an external symbol | Symbol checked at module level |

## Statements

| Node | Purpose | Main restriction |
| --- | --- | --- |
| `HAssign` | Assign a value | Target and value types must match |
| `HCallAssign` | Assign one or more call results | Target count and types must match the signature |
| `HBitSet` | Set or clear one bit | Value is 0 or 1; bit index is constant |
| `HStore` | Volatile memory write | Store ordering is preserved |
| `HIf` | Conditional branch | Supports `elif` and `else` |
| `HFor` | Fixed-count loop | Constant `init`/`bound`; body may read but not write the loop variable |
| `HWhile` | Pre-check loop | Supports `HBreak` and `HContinue` |
| `HPoll` | Body-first polling loop | Supports `HBreak` and `HContinue` |
| `HBreak` | Exit the nearest loop | Loop body only |
| `HContinue` | Continue the nearest loop | In `HFor`, jumps to the step block |
| `HReturn` | Return zero, one, or multiple values | Must match function signature |
| `HInlineAsm` | Raw pseudo-ASM passthrough | Target-specific and intentionally opaque |
| `HExprStmt` | Evaluate a call for side effects | Call expressions only |
| `HExit` | Exit a Fragment | Fragment only |

## Containers

- `HFunction`, `HParam`, `HExternFn`: function definitions and declarations.
- `HFragment`, `HFragmentBinding`: explicitly bound code fragments.
- `HExternalSymbol`, `HModule`: module-level symbols and cross-reference validation.

Use `validate_hfunction`, `validate_hfragment`, or `validate_hmodule` before lowering directly. The public pipelines perform their required validation automatically.
