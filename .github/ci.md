# CI 與套件檢查

修改程式碼、測試、套件設定、根目錄 README、授權或 CI 設定檔後，push 與 pull request 會觸發 [Cross-platform regression](workflows/ci.yml)，也能從 GitHub Actions 手動執行。

| Job | 環境 | 檢查內容 |
|---|---|---|
| `core` | Windows／Ubuntu，Python 3.11（`-e .`）／3.13（`-e ".[table]"`），Node.js 24 | `tests/jobcore`、`tests/flow`、`tests/wave`、`tests/mapbv`、`tests/tablepack`（含 Wave 前端 JavaScript 測試）；若存在 `scripts/test_sync_to_public.py` 則一併執行 `unittest` |
| `isasim` | Windows／Ubuntu，Python 3.11／3.12／3.13 | `tests/isasim` 與 `tests/isasim_gui`：包含 `isasim` 行為契約、差異測試、中立性 lint、`isasim_gui` 視覺化除錯器測試與雙語文件可執行範例 |
| `mcucc` | Windows／Ubuntu，Python 3.11／3.12／3.13 | `tests/mcucc`：包含編譯器、靜態與差異驗證、`rpk-mcucc` CLI 與雙語文件可執行範例（測試步驟將 `PYTHONUTF8` 清空為 `""`） |
| `calc` | Windows／Ubuntu，Python 3.11／3.13（Linux 安裝 `libegl1`、`libopengl0`） | `tests/calc`：使用 `QT_QPA_PLATFORM=offscreen` 測試運算、工作區、位元／定點數、Qt 元件與真實 IPython kernel |
| `package` | Windows／Ubuntu，Python 3.13（Linux 安裝 `libegl1`、`libopengl0`） | 先由 `python -m build` 建置 sdist 與 wheel，再由 `tools/ci/check_wheel.py` 在原始碼樹外建立全新 venv 驗證基本安裝與 `[all]` 安裝 |

`core` 的 Python 3.11 安裝基本依賴，3.13 加入 `[table]`，分別涵蓋未安裝與已安裝 `brotli` 的執行路徑。`calc` 依賴獨立安裝，避免基本依賴缺漏被其他工具掩蓋。

## 本機重跑相同測試

`pyproject.toml` 已設定 `testpaths = ["tests"]`，因此在專案根目錄執行 `pytest` 只會收集 `tests/` 下的測試。使用專案虛擬環境的 Python 執行以下指令：

```powershell
python -m pip install -e ".[all]" pytest
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest tests/jobcore tests/flow tests/wave tests/mapbv tests/tablepack -q -ra
python -m pytest tests/isasim tests/isasim_gui -q -ra
python -m pytest tests/calc -q -ra

# mcucc job 在 CI 會清空 PYTHONUTF8，以驗證預設環境編碼行為
$env:PYTHONUTF8 = ""
python -m pytest tests/mcucc -q -ra
```

預期 pytest 全部通過。`-ra` 會列出略過的測試原因，例如 POSIX PTY 相關測試在 Ubuntu 驗證、在 Windows 會略過。Wave 前端邏輯測試需要 Node.js（CI 使用 Node.js 24）。若要連同 Wave playground 測試一併執行，可將 `playground/wave` 加進第一條 pytest 指令的目標清單。測試不需要 Excel、私人資料、相機或人工目視操作。

## 檢查發行安裝包與 CLI 進入點

使用空白的輸出目錄，避免混入前一次產出的 wheel：

```powershell
python -m pip install build
python -m build --outdir .pytest_cache/package-check
python tools/ci/check_wheel.py .pytest_cache/package-check
```

建置工具會產生 `rpkbin-<version>.tar.gz` 與 `rpkbin-<version>-py3-none-any.whl`；`check_wheel.py` 會檢查該目錄內唯一的 wheel，建立新的 venv，先驗證基本安裝，再加入 `[all]` 測試完整安裝，最後依序輸出 `Installed base smoke passed` 與 `Installed all smoke passed`。腳本執行期間會下載宣告的依賴並於結束後刪除暫存環境；只有應用程式檢查會改用隔離的 home 目錄，避免載入開發者本機的 Calc 設定或啟動檔。

安裝檢查會在原始碼目錄之外以隔離模式（`-I`）驗證所有打包模組與入口：

- **模組匯入與版本**：確認匯入路徑位於新 venv 內、已安裝版本與 wheel metadata 一致、`pip check` 成功，並確認已退役的 `rpkbin.job_manager` 未被打包。
- **獨立函式庫與核心檢查**：實跑 `CORE_SMOKE`（確認 `rpkbin.jobcore` 不匯入 `flow` 或 `wave`）、`FLOW_SMOKE`（確認 `rpkbin.flow` 不匯入 `jobcore` 或 `wave`）、MapBV 切片與 TablePack 往返測試（`base` 測 `zlib`，`all` 加測 `brotli`）。
- **五個 CLI 與工具套件檢查**：`pyproject.toml` 註冊了五個命令列入口，分別為 `rpk-wave`、`rpk-isasim`、`rpk-isasim-gui`、`rpk-mcucc` 與 `rpk-calc`。`check_wheel.py` 會執行 `rpk-wave --help`、Wave headless 執行與 `WaveServer` 靜態資源檢查；`rpk-isasim --help`、`rpk-isasim run`、`python -m rpkbin.isasim` 與 `Simulator`/`Snapshot`；`rpk-isasim-gui --help` 與 `rpkbin.app.isasim_gui/static/index.html`；`python -X utf8 -I -B -m rpkbin.mcucc`（含打包的 `.md` 文件與範例、`lint-target`、`check-forms`、`compile`、`verify`）；以及在 `[all]` 階段執行 `rpk-calc --help`、`rpk-calc --eval`、`WorkspaceSession`、`EditJournal` 與 Qt 應用程式圖示檢查。Ubuntu 環境另外會驗證 wheel 內的 POSIX PTY 輔助腳本。

這些檢查能及早抓出遺漏的套件資料檔、依賴宣告錯誤、進入點失效，或測試誤連到開發者本機 editable install 的問題。

## 查閱失敗與驗證範圍

`core`、`isasim`、`mcucc` 與 `calc` 四個 job 都會產生 JUnit XML 報告，並由 GitHub Actions 保存七天（例如 `mcucc-windows-latest-py3.12` 或 `calc-windows-latest-py3.13`）。維護者可下載對應平台的報告查看失敗案例；建置與安裝階段的錯誤則直接顯示於 `package` job 的紀錄中。

offscreen 測試驗證可自動觀測的程式行為，不代表字型渲染、輸入法或實際螢幕外觀已人工驗收。Windows 本機通過測試也不代表 Ubuntu 矩陣一定通過，仍須以該次 GitHub Actions 結果為準。
