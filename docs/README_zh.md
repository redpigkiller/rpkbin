# rpkbin 文件導覽

這一頁只負責選擇入口；完整 API、操作配方與限制保留在各模組的長版 reference，避免主 README 變得難讀。

## 從這裡開始

| 領域 | 入口 | 內容 |
| --- | --- | --- |
| 位元／暫存器檢視 | [MapBV](mapbv/README_zh.md) | register map、slice、packing 與 bit-level access |
| Fixed-point 模擬 | [NumBV](numbv/README_zh.md) | bit-true scalar、vector 與 pipeline arithmetic |
| Control-flow 分析 | [CFG](cfg/README_zh.md) | graph、dominance 與 DOT export |
| Excel 擷取 | [Excel Extractor](excel_extractor/README_zh.md) | 依 template 擷取 workbook |
| 並行工作 | [Job Manager](job_manager/README_zh.md) | function、shell 與 lifecycle-managed jobs |
| 進度回報 | [StageTracker](utils/README_zh.md) | 輕量 stage／progress reporting |
| 可觀測 workflow | [Wave](wave/README_zh.md) | CLI、parser、hooks、PTY jobs 與 TUI/headless |
| Compiler backend | [Codegen](codegen/README_zh.md) | HIR/LIR validation、lowering、rewrite 與 pseudo-ASM |

跨模組參考：

- [Architecture](architecture_zh.md)
- [CLI reference](cli-reference_zh.md)
- [Syntax reference](syntax-reference_zh.md)
- [English documentation](README.md)

每個模組入口都有對應的 `_zh.md`。各目錄中的長版 `*.md` 則保留實作與 API reference。
