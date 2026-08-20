# Codegen

`rpkbin.codegen` is an experimental target-neutral backend for validating HIR, lowering to LIR, applying rewrites, and emitting pseudo-ASM through a target model.

The shortest useful path is to build a small `HFunction`, validate it, and lower it. Production DSL parsing, a real MCU ISA, assembler/linker output, and production spilling are out of scope.

- Full API and pipeline reference: [codegen reference](codegen.md)
- Frontend contract: [frontend integration](frontend_integration.md)
- Current support and deferrals: [status](status.md)
- 中文入口：[README_zh.md](README_zh.md)
- Focused tests: `python -m pytest tests/codegen -q`
