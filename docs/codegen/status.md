# Codegen Status and TODO

See [Codegen](codegen.md) for usage.

The status below describes the backend boundary, not a promise that the experimental package is production-ready.

## Stable in the current backend

- 8/16-bit integer operations and HIR validation/lowering, including signed and
  unsigned forms currently covered by the test suite.
- Structured control flow (`if`, `while`, polling, and short-circuit operators),
  `HInsert`, volatile load/store/bit-test/bit-set operations, calls, and returns.
- Function, Fragment, and Module validation/lowering APIs.
- Rewrite hooks and Function/Fragment pseudo-ASM pipelines, including register
  allocation when a `RegisterModel` is supplied.

## Experimental

- Production spill/reload is disabled. The former pre-isel prototype could
  overwrite live registers because expression-tree LIR does not expose target
  instruction constraints.
- `cegis.minimize_cegis` is an experimental, orphaned offline helper. It is not
  invoked by the backend pipeline and has no committed consumer.

## Deferred

- A module-level one-call pseudo-ASM pipeline.
- 32-bit lowering.
- Generalized `HFor` bounds and loop-variable mutation.
- A machine-level save/restore or spill contract, which requires a real target.

## Out of scope

- DSL parsing, real MCU ISAs, assemblers, linkers, and binary encoding.
