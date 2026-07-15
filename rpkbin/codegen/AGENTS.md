# `rpkbin.codegen` Developer Guide

## Boundary

This package is a target-agnostic MCU compiler backend.

It owns HIR/LIR definitions, validation, lowering, machine-independent rewrites, target protocols, and pseudo-ASM orchestration. It does not own DSL parsing, private MCU targets, real assembly syntax, linking, or binary encoding.

Never import a private target or frontend package here. Add target-specific behavior through an existing Protocol.

## Stable path

```text
HIR → validate → lower to LIR → optional rewrite → Target → pseudo ASM
```

Public entry points:

- Function: `validate_hfunction`, `run_codegen_from_hir`
- Fragment: `validate_hfragment`, `run_codegen_from_fragment`
- Module: `validate_hmodule`, `lower_module`

There is no module-to-pseudo-ASM pipeline yet.

## File responsibilities

| File | Responsibility |
| --- | --- |
| `hir.py` | HIR types and nodes |
| `hir_validate.py` | HIR and module validation |
| `lir.py` | LIR nodes and structural validation |
| `ir.py` | Backward-compatible LIR import shim |
| `lower.py` | Function, Fragment, and Module lowering |
| `rewrite.py`, `matcher.py`, `patterns.py` | Machine-independent rewrites |
| `asm.py`, `isel.py`, `target.py` | Pseudo ASM and target protocols |
| `pipeline.py` | Public Function and Fragment pipelines |
| `toy_target.py` | Test/reference target only |
| `register_alloc.py` | Register allocation used by Function/Fragment pipelines when a `RegisterModel` is supplied; register pressure fails closed |

## Rules

- Keep the default pipeline independent of experimental passes.
- Preserve backward-compatible names in `ir.py` and documented public APIs.
- Put target-specific rewrite data in the private target package.
- Add one focused regression test for non-trivial behavior changes.
- Do not update milestone prose in source files; update `docs/codegen/status_zh.md` instead.

## Verification

```bash
pytest -q tests/codegen
```

User documentation starts at `docs/codegen/codegen_zh.md`. Current capability status and deferred work live in `docs/codegen/status_zh.md`.
