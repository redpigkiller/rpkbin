# Debug & DAP

`rpkbin.debug` is an experimental, target-neutral debugger integration layer.
It provides immutable records, structural protocols, and a standard-library
stdio DAP server without imposing a shared emulator or machine-state model.

Targets retain instruction semantics, source mapping, register schemas,
breakpoint-condition evaluation, history, and memory serialization. Address
spaces declare their own units, so the generic layer does not assume byte
addressing.

- Full contract, adapter example, request coverage, and limitations:
  [Debug & DAP reference](debug.md)
- Traditional Chinese entry: [README_zh.md](README_zh.md)
- Focused tests: `python -m pytest tests/debug -q`
