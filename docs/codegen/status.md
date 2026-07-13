# Codegen Status and TODO

See [Codegen](codegen.md) for usage.

## Stable

- 8/16-bit integer operations and HIR validation/lowering.
- Structured control flow, `HInsert`, volatile memory access, calls, and returns.
- Function, Fragment, and Module validation/lowering APIs.
- Rewrite hooks and Function/Fragment pseudo-ASM pipelines, including register
  allocation when a `RegisterModel` is supplied.

## Experimental

- Spill/reload paths and complex live-range translation remain target-dependent
  and need broader validation.

## Deferred

- Module-level pseudo-ASM pipeline.
- 32-bit lowering.
- Generalized `HFor` bounds and loop-variable mutation.
- Broader spill, live-range, and allocation translation validation.

## Out of scope

- DSL parsing, real MCU ISAs, assemblers, linkers, and binary encoding.
