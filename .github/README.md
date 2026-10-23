# 持續驗證 MyPkg

修改程式、測試、套件設定、根 README、授權或 CI 檔案後，push／pull request 會執行
[MyPkg cross-platform regression](workflows/ci.yml)。也能從 GitHub Actions 手動執行。

| Job | 環境 | 檢查內容 |
| --- | --- | --- |
| `core` | Windows／Ubuntu，Python 3.11／3.13 | Wave、JobManager、MapBV、TablePack；Node.js 24 執行 Wave 前端邏輯測試 |
| `calc` | Windows／Ubuntu，Python 3.11／3.13 | Calc 運算、workspace、bit／FXP、Qt widget 與真實 IPython kernel 測試；Qt 使用 offscreen |
| `package` | Windows／Ubuntu，Python 3.13 | 建置 sdist，再從 sdist 建置 wheel；於新的 venv 檢查基本安裝與 `[all]` 安裝 |

`core` 的 Python 3.11 安裝基本依賴，3.13 加入 `[table]`，分別涵蓋未安裝 brotli
與已安裝 brotli 的路徑。Calc 依賴獨立安裝，避免基本依賴缺漏被其他工具掩蓋。

## 修改後，在本機重跑相同測試

在專案根目錄，以專案虛擬環境的 Python 執行：

```powershell
python -m pip install -e ".[all]" pytest
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest tests/job_manager tests/wave tests/mapbv tests/tablepack -q -ra
python -m pytest tests/calc -q -ra
```

預期 pytest 回報通過；`-ra` 會列出跳過原因。Windows 會跳過 POSIX PTY 測試；
未安裝 Node.js 時，Wave 前端邏輯測試也會跳過。CI 明確安裝 Node.js，Ubuntu 執行
POSIX 路徑。測試不需要 Excel、私人資料、相機或目視操作。

## 檢查交給使用者的安裝包

使用空的輸出目錄，避免混入前一次產出的 wheel：

```powershell
python -m pip install build
python -m build --outdir .pytest_cache/package-check
python tools/ci/check_wheel.py .pytest_cache/package-check
```

建置工具產生 `rpkbin-<version>.tar.gz` 與 `rpkbin-<version>-py3-none-any.whl`；
`check_wheel.py` 消費該目錄內唯一的 wheel，建立新的 venv，先安裝基本依賴，再加入
`[all]`。預期最後分別顯示 `Installed base smoke passed` 和 `Installed all smoke passed`。
腳本會下載宣告的依賴，並於結束後移除自己的暫存環境。pip 沿用一般快取；只有
應用程式檢查使用隔離的 home，避免載入開發者的 Calc 設定或啟動程式。

安裝檢查在原始碼目錄之外執行，以隔離模式的 Python 確認匯入位置屬於新 venv、
安裝版本等於 wheel metadata、`pip check` 成功，並實跑 MapBV 切片、TablePack
round trip、Wave headless 工作及 Calc CLI 的計算。這可發現遺漏檔案、依賴宣告錯誤、
entry point 失效，以及誤用本機 editable install 的情況。

## 查閱失敗與驗證範圍

`core`／`calc` 的 pytest 產生 JUnit XML，Actions 將它們保存七天，例如
`calc-windows-latest-py3.13`。維護者可下載報告查看失敗案例；每個環境使用獨立名稱。
安裝與建置失敗直接顯示於 `package` job 的 log。

offscreen 測試驗證可自動觀測的行為，不代表字型、IME 或實際螢幕上的外觀已經驗收。
Windows 的本機測試也不代表 Ubuntu 矩陣已通過；需以該次 Actions 結果為準。
