# Codegen

`rpkbin.codegen` 是實驗性的 target-neutral backend，負責 HIR validation、lowering 到 LIR、rewrite，以及透過 target model 輸出 pseudo-ASM。

最短的使用路徑是建立小型 `HFunction`、執行 validation，再做 lowering。Production DSL parsing、真實 MCU ISA、assembler/linker 輸出與 production spilling 都不在目前範圍內。

- 完整 API 與 pipeline：[codegen reference](codegen_zh.md)
- Frontend contract：[frontend integration](frontend_integration_zh.md)
- 目前支援與延後項目：[status](status_zh.md)
- English entry：[README.md](README.md)
- Focused tests：`python -m pytest tests/codegen -q`
