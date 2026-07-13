# HIR 參考（Frontend 作者）

[English](hir.md) | [目前進度與 TODO](status_zh.md)

HIR 是 `rpkbin.codegen` 對 Frontend 提供的 typed、structured input contract。Frontend 應建立 HIR，不應依賴 LIR 內部形狀。

## 型別

- Lowering 支援 `UInt(8|16)`、`SInt(8|16)`。
- `Void` 代表沒有回傳值。
- 32-bit 型別已有表示方式，但尚未支援 lowering。

## Expressions

| 節點 | 用途 | 主要限制 |
| --- | --- | --- |
| `HConst` | Typed constant | 數值必須能由型別表示 |
| `HVar` | 具名值，可帶 register hint | Hint 由 `RegisterModel` 驗證 |
| `HBinOp` | 算術與 bitwise operation | Shift amount 必須是 constant |
| `HCmp` | 比較 | 只能出現在 condition position |
| `HLogical`、`HNot` | Short-circuit logic | 只能出現在 condition position |
| `HBitTest` | 測試單一 bit | Condition only；bit index 必須是 constant |
| `HCast` | Byte extraction、signedness 或 width cast | Cast kind 會被驗證；`u16_from` / `s16_from` 會降低為 `lir.Extend` |
| `HExtract` | 擷取 bit range | Range 必須是合法 constant |
| `HInsert` | 覆寫 bit range | 以 mask、shift、OR 降階 |
| `HConcat` | 串接數值 | 結果寬度必須符合輸入 |
| `HLoad` | Volatile memory read | 保留 load ordering |
| `HCall` | 單一 expression call | Module-level 檢查 signature |
| `HSymbolAddr` | 外部 symbol address | Module-level 檢查 symbol |

## Statements

| 節點 | 用途 | 主要限制 |
| --- | --- | --- |
| `HAssign` | 指派數值 | Target 與 value type 必須相符 |
| `HCallAssign` | 接收一或多個 call results | Target 數量與型別必須符合 signature |
| `HBitSet` | Set/clear 單一 bit | Value 只能是 0/1；bit index 必須是 constant |
| `HStore` | Volatile memory write | 保留 store ordering |
| `HIf` | 條件分支 | 支援 `elif`、`else` |
| `HFor` | Fixed-count loop | `init`/`bound` 必須是 constant；body 可讀但不可寫 loop variable |
| `HWhile` | Pre-check loop | 支援 `HBreak`、`HContinue` |
| `HPoll` | Body-first polling loop | 支援 `HBreak`、`HContinue` |
| `HBreak` | 離開最近的 loop | 只能在 loop body 使用 |
| `HContinue` | 繼續最近的 loop | 在 `HFor` 中會跳到 step block |
| `HReturn` | 回傳零、一或多個值 | 必須符合 function signature |
| `HInlineAsm` | Raw pseudo-ASM passthrough | Target-specific opaque content |
| `HExprStmt` | 執行只需要 side effect 的 call | 只接受 call expression |
| `HExit` | 離開 Fragment | 只能用於 Fragment |

## Containers

- `HFunction`、`HParam`、`HExternFn`：Function definition/declaration。
- `HFragment`、`HFragmentBinding`：具有明確 binding 的 code fragment。
- `HExternalSymbol`、`HModule`：Module-level symbols 與 cross-reference validation。

直接 lowering 前使用 `validate_hfunction`、`validate_hfragment` 或 `validate_hmodule`。公開 pipeline 會自動執行必要驗證。
