# rpkbin documentation

Use this page to choose a short module entry point. The linked reference pages keep the detailed API, recipes, and limitations out of the main README.

## Start here

| Area | Entry point | What it covers |
| --- | --- | --- |
| Bit/register views | [MapBV](mapbv/README.md) | Register maps, slices, packing, and bit-level access |
| Fixed-point simulation | [NumBV](numbv/README.md) | Bit-true scalar, vector, and pipeline arithmetic |
| Control-flow analysis | [CFG](cfg/README.md) | Graph construction, dominance, and DOT export |
| Spreadsheet extraction | [Excel Extractor](excel_extractor/README.md) | Template-driven workbook extraction |
| Concurrent jobs | [Job Manager](job_manager/README.md) | Function, shell, and lifecycle-managed jobs |
| Progress reporting | [StageTracker](utils/README.md) | Lightweight stage and progress reporting |
| Observable workflows | [Wave](wave/README.md) | CLI, parser, hooks, PTY jobs, and TUI/headless runs |
| Compiler backend | [Codegen](codegen/README.md) | HIR/LIR validation, lowering, rewrites, and pseudo-ASM |
| Debugger integration | [Debug & DAP](debug/README.md) | Target-neutral debugger adapters and a stdio DAP server |

Cross-cutting references:

- [Architecture](architecture.md)
- [CLI reference](cli-reference.md)
- [Syntax reference](syntax-reference.md)
- [Traditional Chinese documentation](README_zh.md)

Every module entry point has a matching `_zh.md` page. The longer `*.md` pages in each directory are the implementation and API references.
