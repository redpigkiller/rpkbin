# rpkbin 文件導覽

[English](../en/README.md) · [專案首頁](../../README_zh.md)

`rpkbin` 是針對 IC 設計、驗證與自動化腳本需求開發的實用工具庫，包含四個獨立工具：

1. **Wave（排程與程序監控）**：使用 Python 定義外部指令批次，提供命令優先排程、固定資源容量配額、即時 stdout 監看、宣告式 `track` / `check` / `on` 規則、不可變 Result 驗收報告，以及本機 Web GUI 與非互動式 Headless CLI 兩種執行入口。
2. **MapBV（硬體暫存器位元欄位）**：輕量級 BitVector 工具，提供暫存器欄位宣告、閉區間切片共享視圖（雙向即時連動）、硬體 Bus 串接 (`concat`)、組合邏輯運算式延遲求值與符號代入。
3. **TablePack（表格壓縮編碼）**：以 schema 驅動的函式庫，把資料列打包成緊湊的 `bytes`（有型別的欄位、delta／repeat／nullable 編碼、可選 brotli 壓縮）並完整還原，不會讀取檔案。
4. **rpk-calc（工程與定點數計算）**：互動式 Python 工作區，支援 NumPy、明確 FXP 量化，以及 GUI 數值與陣列檢視編輯。

---

## Wave 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[Wave 快速入門](wave/README.md)** | 第一次完成一批工作：工作定義檔結構、以 Headless 執行並判讀摘要、以 GUI 載入預覽並手動派發。 | 初次接觸 Wave 者 |
| **[由淺入深的 Python 使用情境](wave/scenarios.md)** | 8 個完整可執行的實務情境：單一工作、多工作排程、check 判定、警告與 track、互動送文字與 signal、資源配額與重試、attempt 歷史與日誌保留、CI 批次整合。 | 撰寫測試與自動化腳本開發者 |
| **[GUI 操作指南](wave/gui.md)** | Web GUI 完整操作：載入預覽與 Start、工作篩選與即時 Log、批次控制與文字群發、Settings（執行配額、記憶體保留、顯示偏好）設定。 | 使用瀏覽器監看與操作工作的工程師 |
| **[Python API 參考](wave/reference/python.md)** | `Job`、`JobManager`、`Track`、`Check`、狀態列舉、事件常數與回傳值規格查閱。 | 查閱精確參數型別與預設值 |
| **[CLI 與退出碼參考](wave/reference/cli.md)** | `rpk-wave` 命令列選項（`--headless`、`--timeout`、`-j` 等）與標準退出碼（0, 1, 2, 130）。 | CI/CD 與 shell 腳本整合 |
| **[架構、契約與限制](wave/architecture.md)** | Wave 與 JobManager 職責劃分、State 與 Result 判定矩陣、有界歷史與記憶體保護、跨平台程序模型契約。 | 系統維護與深度客製者 |
| **[疑難排解](wave/troubleshooting.md)** | 子程序輸出緩衝（無即時 log）、Exit 0 但 Result 為 FAIL、send 成功但命令未執行、連接埠衝突等排查指南。 | 遇到非預期行為時查閱 |

---

## MapBV 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[MapBV 快速入門](mapbv/README.md)** | 第一次建立變數與常數、位元寬度、讀寫數值，以及十六進位／二進位格式化輸出。 | 初次接觸 MapBV 者 |
| **[實戰使用情境](mapbv/scenarios.md)** | 暫存器切片雙向連動、硬體 Bus 串接 (`concat`) 與唯讀位元防護、邏輯運算式延遲求值、符號求值 (`eval`) 與常見誤區排查（如 `==` 與數值比對）。 | 撰寫暫存器模型與驗證測試者 |
| **[API、型別與精確語意](mapbv/reference.md)** | `MapBV` 與 `MapBVExpr` 完整方法、工廠函式 (`var`, `const`, `concat`)、運算子行為、例外與限制。 | 查閱精確型別與例外行為 |

---

## TablePack 文件

| 文件 | 內容說明 | 適用對象 |
| :--- | :--- | :--- |
| **[TablePack 快速入門](tablepack/README.md)** | 不給 schema 直接打包、自己描述欄位、`infer` 與 `explain`，以及第一次大小比較。 | 初次接觸 TablePack 者 |
| **[實戰使用情境](tablepack/scenarios.md)** | 各種欄位型別、把 schema 存成 JSON、找出位元組花在哪、共用 schema 而不嵌入、欄式資料、選項調校、壓縮方式選擇、建置時驗證與錯誤判讀。 | 需要儲存或傳遞表格的工程師 |
| **[API 與精確語意參考](tablepack/reference.md)** | `pack`、`unpack`、`iter_unpack`、`infer`、`explain`、`Schema`、所有欄位型別與選項、可接受的值、例外與二進位格式。 | 查閱精確行為 |

---

## rpk-calc 文件

- **[rpk-calc 快速入門](calc/README.md)**：安裝選配依賴、取得第一個命令列結果、在 Python 與 FXP 間明確轉換，以及檢視和編輯陣列。
