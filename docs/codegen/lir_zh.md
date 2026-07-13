# LIR 參考（Target 作者）

[English](lir.md) | [目前進度與 TODO](status_zh.md)

LIR 是 HIR lowering 與 pseudo-ASM emission 之間的 target-neutral representation。Frontend 不應依賴其內部形狀。

Legacy `Expr` 只包含 `Const`、`Var`、`BinOp`、`Cmp`。新程式應使用涵蓋下列完整節點的 `FullExpr`。

## Expressions

| 節點 | 用途 |
| --- | --- |
| `Const` | Immediate value |
| `Var` | Named value |
| `VReg` | 可帶 physical-register hint 的 virtual register |
| `BinOp` | Arithmetic 或 bitwise expression |
| `Cmp` | Comparison expression |
| `Extend` | 明確的 zero/sign extension，擴成較寬的 destination width |
| `Call` | Function call expression |
| `SymbolAddr` | External symbol address；rewrite 視為 opaque |
| `MemLoad` | Volatile memory read |
| `BitOp` | Bit test expression，或 set/clear statement |
| `InlineAsmExpr` | Raw target-specific pseudo ASM |

### Extend

- `kind`：`zext` 或 `sext`
- `value`：要被擴寬的來源 expression
- `width`：destination width（bit）

## Statements

| 節點 | 用途 |
| --- | --- |
| `Assign` | 將 expression 指派給 named value |
| `CallAssign` | 一次接收多個 call results |
| `MemStore` | Volatile memory write |
| `BitOp` | Set 或 clear 單一 bit |

Volatile load/store 保留順序，rewrite pass 不會移除它們。

## Terminators

| 節點 | 用途 |
| --- | --- |
| `BrIf` | 依 expression branch |
| `BrCmp` | Combined compare-and-branch |
| `Jump` | Unconditional branch |
| `Return` | 回傳零或一個值 |
| `MultiReturn` | 回傳多個值 |
| `FragmentExit` | 結束 Fragment 的一條控制流路徑 |

## Containers 與 metadata

| 節點 | 用途 |
| --- | --- |
| `Block` | Label、statements 與單一 terminator |
| `Function` | Named parameters 與 basic blocks |
| `Fragment` | Bound code fragment |
| `FragmentBinding` | Fragment input/output binding |
| `Module` | Functions、external declarations 與 symbols |
| `SpillSlot` | Prototype register allocator 的 spill location |

Target 透過 `Target` / `FragmentTarget` 消費 `Function` 或 `Fragment`。`SpillSlot` 屬於 experimental allocator contract。
