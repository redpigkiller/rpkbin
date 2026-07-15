# Codegen Status and TODO

See [Codegen](codegen.md) for usage.

## Stable

- 8/16-bit integer operations and HIR validation/lowering.
- Structured control flow, `HInsert`, volatile memory access, calls, and returns.
- Function, Fragment, and Module validation/lowering APIs.
- Rewrite hooks and Function/Fragment pseudo-ASM pipelines, including register
  allocation when a `RegisterModel` is supplied.

## Experimental

- Production spill/reload is disabled. The former pre-isel prototype could
  overwrite live registers because expression-tree LIR does not expose target
  instruction constraints.

## Deferred

- Module-level pseudo-ASM pipeline.
- 32-bit lowering.
- Generalized `HFor` bounds and loop-variable mutation.
- A machine-level save/restore or spill contract, justified by a real target.

## Out of scope

- DSL parsing, real MCU ISAs, assemblers, linkers, and binary encoding.
