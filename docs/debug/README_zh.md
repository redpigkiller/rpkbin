# Debug 與 DAP

`rpkbin.debug` 是 experimental 的 target-neutral debugger 整合層，提供
immutable records、structural protocols，以及使用標準庫實作的 stdio DAP
server；它不提供 emulator，也不要求不同 target 共用 machine state。

instruction semantics、source map、register schema、breakpoint condition、
history 與 memory serialization 仍由 target 擁有。address space 會明確宣告
單位，因此共用層不假設 address 一定是 byte。

- 完整 contract、adapter 範例、request coverage 與限制：
  [Debug 與 DAP reference](debug.md)
- English entry：[README.md](README.md)
- Focused tests：`python -m pytest tests/debug -q`
