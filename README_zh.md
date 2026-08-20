# rpkbin — IC 設計與驗證工具庫

[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](README_zh.md)

`rpkbin` 是一套 Python 工具庫，服務 IC 設計與驗證流程，涵蓋 bit/register mapping、bit-true fixed-point simulation、Excel 資料擷取、control-flow modeling、compiler backend 實驗，以及可觀測的 batch execution。

## 為什麼使用 rpkbin？

它把硬體導向的小型工具維持在清楚、可組合的模組邊界內，不要求使用者採用單一端到端框架。每個模組提供專注的 Python API；需要長時間執行時，再由 Wave 提供 CLI、TUI 與 headless 控制。

## 快速開始

從乾淨 checkout 到第一個可執行結果，最短路徑是 Wave smoke flow。

1. 安裝 Python 3.11 以上版本與 Wave extra。

   ```bash
   python -m pip install -e ".[wave]"
   ```

2. 產生包含 parser 範例的 starter wave file。

   ```bash
   rpk-wave init hello --profile parser
   ```

   預期輸出：

   ```text
   [Wave] Created hello.wave.py
          Edit the file, then run:  rpk-wave run hello.wave.py
   ```

3. 以 headless mode 執行，並列印簡短效能摘要。

   ```bash
   rpk-wave run hello.wave.py --no-tui --perf
   ```

   成功執行會包含：

   ```text
   [Wave][perf] summary
   [Wave][perf] total lines=2 parser=2 ...
   ```

   時間欄位會依機器不同而變化；batch 成功時 process exit code 為 `0`。

## 安裝與 feature extras

核心安裝提供 package、NumPy 與 Click。其他功能透過 extras 安裝：

| Extra | 啟用功能 | 主要相依套件 |
| --- | --- | --- |
| `wave` | Wave CLI、TUI、parser、hook、PTY jobs | Textual、prompt-toolkit、Rich |
| `excel` | Excel Extractor | openpyxl、xlrd、rapidfuzz |
| `cfg` / `dot` | NetworkX analysis / `CFG.export_dot()` | networkx / pydot |
| `jax` | NumBV JAX backend | JAX |

```bash
python -m pip install -e "."             # 核心功能
python -m pip install -e ".[excel]"      # Excel Extractor
python -m pip install -e ".[all]"        # 所有已列出的 extras，但不含 jax
python -m pip install -e ".[all,jax]"    # 包含 JAX 的完整安裝
```

套件要求 Python `>=3.11`。正式的相依套件與 entry point 定義請看 [pyproject.toml](pyproject.toml)。

## 常見用法

### Bit-true fixed-point arithmetic

```python
from rpkbin.numbv import Format, scalar

fmt = Format(8, 4)
a = scalar(1.25, fmt=fmt)
b = scalar(0.5, fmt=fmt)

print((a + b).val)
```

```text
1.75
```

當每個 pipeline stage 都需要明確 output format 時，使用 function-level API（`add`、`mul`、`sum`、`dot`、`mac`）。詳見 [NumBV 文件](docs/numbv/README_zh.md)。

### 平行執行 Python 與 shell jobs

```python
from rpkbin.job_manager import JobManager, FuncJob

job = FuncJob("hello", lambda: "ok")
with JobManager(max_workers=1) as manager:
    manager.add(job)
    manager.wait()

print(job.status, job.result)
```

```text
done ok
```

如果還需要 parser、hook、PTY、logs 或互動式 TUI，請使用建立在 Job Manager 之上的 [Wave](docs/wave/README_zh.md)。

## 模組導覽

| 狀態 | 模組 | 適用情境 |
| --- | --- | --- |
| Stable | [MapBV](docs/mapbv/README_zh.md)、[NumBV](docs/numbv/README_zh.md)、[StageTracker](docs/utils/README_zh.md)、[Job Manager](docs/job_manager/README_zh.md)、[Wave](docs/wave/README_zh.md) | Register/bit views、fixed-point simulation、流程摘要、平行 jobs 與可觀測 workflow |
| Beta | [CFG](docs/cfg/README_zh.md)、[Excel Extractor](docs/excel_extractor/README_zh.md) | 低階流程/layout 分析與樣板式 Excel 擷取 |
| Experimental | [Codegen](docs/codegen/README_zh.md) | HIR/LIR validation、lowering、rewrite、register allocation 與 pseudo-ASM 實驗 |

每個模組都有 English／繁體中文的短版入口；完整 API 與實作說明保留在各目錄的長版 reference。

## 架構

最上層由使用者選擇適合的模組。Wave 建立在 Job Manager 之上；Codegen 是 target-neutral 的 HIR-to-pseudo-ASM backend，不負責 DSL parsing 或真實 MCU instruction set encoding。

```text
Python / Excel / HIR input
        │
        ├─ MapBV · NumBV · Excel Extractor · CFG · StageTracker
        │
        ├─ Wave CLI/TUI ──> Job Manager ──> local commands / Python callables
        │
        └─ Codegen: HIR ─> validate ─> LIR ─> rewrite ─> allocate ─> Target ─> pseudo ASM
```

完整的責任邊界、session model、invariants 與 trade-offs 請看 [架構文件](docs/architecture_zh.md)。

## Reference

| 需求 | 從這裡開始 |
| --- | --- |
| Wave commands、options 與 exit codes | [CLI reference](docs/cli-reference_zh.md) |
| Codegen HIR/LIR constructs 與 pipeline errors | [Syntax reference](docs/syntax-reference_zh.md) |
| 各模組的 API 與範例 | [`docs/`](docs/) 下對應的模組文件 |

## 已知限制與排錯

- 不加 extra 安裝 `rpkbin` 時，不會安裝 Wave、Excel、NetworkX、pydot 或 JAX；請依使用的模組安裝對應 extra。
- NumBV 必須在建立 `NumBV` 物件前選定 process-global backend；JAX 是 optional，且不包含在 `.[all]` 中。
- Codegen 仍是 experimental，刻意不包含 DSL parser、真實 MCU ISA、assembler、linker、binary encoding、production spilling，以及一次完成 module-to-ASM 的 pipeline。使用前請查看 [status.md](docs/codegen/status_zh.md)。
- Interactive terminal 預設啟動 Wave 全螢幕 TUI；CI 或只需要 batch 完成結果時使用 `--no-tui`，並先安裝 `wave` extra。
- Wave job 失敗時，請在 TUI/headless controls 檢查 status 與 logs；batch 不成功時 CLI 會回傳 non-zero status。

## 貢獻

`rpkbin` 是以 MIT license 發布的公開 package。Bug、提案與 pull request 請使用 [GitHub repository](https://github.com/redpigkiller/rpkbin)。測試位於 [`tests/`](tests/)；開發時先跑聚焦測試，release 前再跑完整 suite。

## License

Package 使用 [MIT License](LICENSE)。開發與文件檢查請參考 [CONTRIBUTING.md](CONTRIBUTING.md)。

套件依 [MIT License](LICENSE) 發布；開發與文件檢查請參考 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 測試

在 repository root 執行：

```bash
python -m pytest tests/ -v
```
