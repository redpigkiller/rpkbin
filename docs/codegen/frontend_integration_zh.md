# Frontend / external DSL frontend 接入指南

本文件說明 external DSL frontend 如何使用 `rpkbin.codegen`。它只描述兩個 package 之間的 contract，不依賴 private target package 的內部實作。

## 責任邊界

`rpkbin.codegen` 負責：

- HIR 型別與結構驗證。
- HIR 到 LIR 的 lowering。
- 機器無關的 rewrite hooks。
- `Target`、`FragmentTarget` 與 `RegisterModel` protocol。
- 產生 pseudo ASM。

Frontend / private target 負責：

- DSL parsing 與 syntax sugar 展開。
- 建立合法的 HIR / HModule。
- 真實 MCU ISA 與 register model。
- target-specific patterns。
- assembler、linker 與 binary encoding。

## 選擇正確入口

### Function-level

使用 `HFunction`、`validate_hfunction`、`run_codegen_from_hir`。適合沒有 module-level cross-reference 的獨立函式。

### Fragment-level

使用 `HFragment`、`validate_hfragment`、`run_codegen_from_fragment`。適合具有明確輸入、輸出與實體 binding 的程式片段；target 必須實作 `FragmentTarget`。

### Module-level

包含多個函式、`HExternFn`、`HExternalSymbol` 或 `HSymbolAddr` 時，先建立 `HModule` 並呼叫 `validate_hmodule`。

目前 module-level API 只提供驗證與 lowering，尚未提供完整 module 到 pseudo ASM 的一鍵 pipeline。

## Frontend 合約

1. Frontend 只依賴公開 HIR API，不依賴 LIR 內部形狀。
2. 不依賴 `ToyTarget` 的輸出文字；它不是穩定格式。
3. Syntax sugar 應在 Frontend 展開成 canonical HIR。
4. 產生 HIR 前先依 [狀態表](status_zh.md) 阻擋 deferred 功能。
5. 有 cross-reference 時一律先執行 `validate_hmodule`。

## 目前限制

- `UInt(32)` / `SInt(32)` lowering 尚未完成。
- `HFor` 只接受 compile-time constant 的 `init` / `bound`，且 body 不能寫入 loop variable。
- 提供 `RegisterModel` 時 pipeline 會執行 register allocation；暫存器不足時目前
  fail closed，不會產生未驗證的 spill/reload。
- 真實 target 與 encoding 不屬於本 package。
