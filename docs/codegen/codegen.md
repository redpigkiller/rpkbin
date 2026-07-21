# rpkbin.codegen — MCU Compiler Backend

[繁體中文](codegen_zh.md) | [Status and TODO](status.md)

`rpkbin.codegen` validates frontend-produced HIR, lowers it to LIR, applies optional rewrite rules, and delegates pseudo-ASM generation to an injected `Target`.

DSL parsing, real MCU ISAs, assemblers, linkers, and binary encoding are intentionally out of scope.

## Boundary and compatibility contract

`rpkbin.codegen` owns target-neutral HIR/LIR, validation and lowering, CFG and
dataflow mechanics, allocator mechanics, rewrite mechanics, target protocols,
and the pseudo-ASM pipeline.  `Target`, `FragmentTarget`, and `RegisterModel`
are public protocols for a backend package to implement; none encodes a
particular MCU ABI, register file, or flag convention.

A UC frontend such as MyRtkPkg owns its DSL grammar/parser, UC register/alias/
bit mapping, ABI, hardware semantics such as `CY`, `Z`, and `b_signext`,
UC-specific patterns and costs, and real assembly/encoding.  Keep those
policies outside this package.

`HFragment` remains a deliberately narrow compatibility API: its acyclic
control-flow, `HExit`, and phased scratch-register reuse behavior are recorded
contracts, not a general-purpose fragment-policy framework.  They are not
generalized by this package.  `region_semantics` remains here as an offline,
target-neutral facility.  CEGIS is experimental/orphaned: it is not part of
the production compilation pipeline and currently has no committed consumer.

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

`run_codegen_from_fragment` returns `FragmentCodegenResult`, whose typed
intermediate fields match the function result and whose `asm_text` property is
the same formatted pseudo-ASM convenience interface as `CodegenResult`.

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

Rewrite rules are applied in declaration order.  `RewritePattern.cost_delta`
is accepted as reserved metadata for compatibility, but it currently does not
participate in rule selection; this package does not implement a cost-based
optimizer.  Rewrite matching also treats calls, volatile loads, and inline ASM
as effect boundaries, and reports an error when rules cycle rather than
silently looping. Each expression permits up to 256 accepted state transitions
(`max_steps`); the final stable probe does not consume a transition. A separate
16,384-node `max_nodes` budget is checked for the initial expression and each
candidate before it is hashed for cycle tracking. The diagnostics report the
last rule, completed transitions, node count, and seen-state count.

Replacement construction preserves captured metadata and derives new integer
result/operand widths from the matched expression context, not matching tree
positions. Comparison and `BitOp("test")` results are width 1; comparisons
retain matched signedness. A root replacement whose known result width differs
from the matched expression is rejected, while a captured `ref` preserves its
own metadata. Ambiguous type-changing replacements are rejected instead of
silently falling back to an 8-bit or unsigned default.

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
