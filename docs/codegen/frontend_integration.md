# Frontend and external DSL integration

This page defines the boundary between `rpkbin.codegen` and a private or external DSL frontend.

## Ownership

`rpkbin.codegen` owns:

- HIR validation and lowering to LIR;
- rewrite hooks and target-neutral pseudo-ASM generation;
- the `Target`, `FragmentTarget`, and `RegisterModel` protocols.

The frontend or concrete target owns parsing, syntax sugar, legal HIR construction, the real MCU ISA and register model, target patterns, and assembler/linker/binary encoding.

## Integration contract

1. Build public HIR objects (`HFunction`, `HFragment`, and `HModule`) rather than reaching into private implementation details.
2. Expand DSL sugar into canonical HIR before calling validation.
3. Call `validate_hmodule()` or the corresponding function/fragment validator and stop on failure; do not guess defaults for unknown semantics.
4. Treat `ToyTarget` output as a backend smoke test, not as a production instruction stream.
5. Keep deferred features explicit. A frontend must not assume 32-bit lowering, generalized `HFor`, production spilling, or a one-call module-to-ASM pipeline.

## Current boundary

The backend covers the currently supported integer widths, structured control flow, volatile memory operations, calls/returns, rewrites, and function/fragment pseudo-ASM. Register allocation requires a `RegisterModel`; without a valid model the backend fails closed rather than silently inventing spill behavior.

See the [Chinese integration guide](frontend_integration_zh.md), [status](status.md), and [full Codegen reference](codegen.md) for details.
