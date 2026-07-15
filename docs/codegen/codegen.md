# rpkbin.codegen — MCU Compiler Backend

[繁體中文](codegen_zh.md) | [Status and TODO](status.md)

`rpkbin.codegen` validates frontend-produced HIR, lowers it to LIR, applies optional rewrite rules, and delegates pseudo-ASM generation to an injected `Target`.

DSL parsing, real MCU ISAs, assemblers, linkers, and binary encoding are intentionally out of scope.

## Entry points

| Input | Use case | API |
| --- | --- | --- |
| `HFunction` | One function | `validate_hfunction`, `run_codegen_from_hir` |
| `HFragment` | A fragment with explicit bindings | `validate_hfragment`, `run_codegen_from_fragment` |
| `HModule` | Multiple functions, externs, or global symbols | `validate_hmodule`, `lower_module` |

There is no one-call `HModule`-to-pseudo-ASM pipeline yet. Validate cross-references first, then let the caller choose how each function is compiled.

## Function quick start

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

`ToyTarget` is a test/reference target, not a production target.

## Pipeline

```text
HIR
 └─ validate
     └─ lower to LIR
         └─ optional rewrite
             └─ register allocation when RegisterModel is supplied
                 └─ Target instruction selection
                     └─ pseudo ASM
```

Supplying a `RegisterModel` enables validation against the target register file
and register allocation. Passing `None` skips both. Register pressure currently
fails closed; production spilling is not implemented.

## Offline bounded-region semantics

`rpkbin.codegen.region_semantics` discovers small pure, acyclic,
single-entry/single-exit LIR regions and executes their fixed-width semantics
concretely.  It fails closed on calls, volatile memory, raw assembly, loops,
ambiguous relational `BrCmp`, and unsupported expressions.  This API is for
offline rule mining, differential checks, and target experiments; it contains
no solver, ISA, instruction cost, or production rewrite policy.

## Further reading

- [HIR reference](hir.md)
- [LIR reference](lir.md)
- [Status and TODO](status.md)
