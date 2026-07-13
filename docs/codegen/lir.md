# LIR Reference (Target Authors)

[繁體中文](lir_zh.md) | [Status and TODO](status.md)

LIR is the target-neutral representation between HIR lowering and pseudo-ASM emission. Frontends should not depend on its internal shape.

Legacy `Expr` contains `Const`, `Var`, `BinOp`, and `Cmp`. New code should use `FullExpr`, which includes every expression below.

## Expressions

| Node | Purpose |
| --- | --- |
| `Const` | Immediate value |
| `Var` | Named value |
| `VReg` | Virtual register with an optional physical-register hint |
| `BinOp` | Arithmetic or bitwise expression |
| `Cmp` | Comparison expression |
| `Extend` | Explicit zero/sign extension to a wider destination width |
| `Call` | Function call expression |
| `SymbolAddr` | Address of an external symbol; opaque to rewrites |
| `MemLoad` | Volatile memory read |
| `BitOp` | Bit test expression, or set/clear statement |
| `InlineAsmExpr` | Raw target-specific pseudo ASM |

### Extend

- `kind`: `zext` or `sext`
- `value`: source expression being widened
- `width`: destination width in bits

## Statements

| Node | Purpose |
| --- | --- |
| `Assign` | Assign an expression to a named value |
| `CallAssign` | Assign multiple call results atomically |
| `MemStore` | Volatile memory write |
| `BitOp` | Set or clear one bit |

Volatile loads and stores preserve order and are not removed by the rewrite pass.

## Terminators

| Node | Purpose |
| --- | --- |
| `BrIf` | Branch on an expression |
| `BrCmp` | Combined compare and branch |
| `Jump` | Unconditional branch |
| `Return` | Return zero or one value |
| `MultiReturn` | Return multiple values |
| `FragmentExit` | End one Fragment control-flow path |

## Containers and metadata

| Node | Purpose |
| --- | --- |
| `Block` | Label, statements, and one terminator |
| `Function` | Named parameters and basic blocks |
| `Fragment` | Bound straight-line/control-flow fragment |
| `FragmentBinding` | Fragment input/output binding |
| `Module` | Functions plus external declarations and symbols |
| `SpillSlot` | Prototype register-allocator spill location |

Targets consume `Function` or `Fragment` through `Target` / `FragmentTarget`. `SpillSlot` belongs to the experimental allocator contract.
