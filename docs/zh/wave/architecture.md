# Wave 架構、契約與設計限制

[English](../../en/wave/architecture.md) · [文件導覽](../README.md) · [Python API 參考](reference/python.md)

本文件記載 Wave 與 JobManager 的核心架構職責分工、生命週期契約、結果判定矩陣與跨平台程序模型設計。

---

## 1. 職責劃分：Wave 與 JobManager

```
+-------------------------------------------------------------------+
|                           使用者層                                 |
|            Python 工作檔 (work.py) / CLI / Web 瀏覽器              |
+---------------------------------+---------------------------------+
                                  |
+---------------------------------v---------------------------------+
|                        Wave 產品入口層                              |
|  - Headless 批次執行器 (cli.py: run_headless)                      |
|  - 本機 Web GUI 伺服器 (web.py: WaveServer)                        |
|  - 檔案載入與定義作用域 (cli.py: load_work_file, definition_scope)   |
+---------------------------------+---------------------------------+
                                  | 呼叫 / 查詢
+---------------------------------v---------------------------------+
|                    JobManager 排程與監控核心                       |
|  - 並行限制 (max_workers) 與固定資源配額 (resources)                 |
|  - 優先權佇列排序 (priority / FIFO)                                |
|  - 輸出日誌解碼與規則比對 (Track / Check / Subscription)             |
|  - 判定結果彙整 (Report / Result) 與有界歷史封存                    |
+---------------------------------+---------------------------------+
                                  | 抽象介面
+---------------------------------v---------------------------------+
|                    程序控制層 (_process.py)                        |
|  - POSIX: PTY、Process Group (setsid)、SIGTERM/SIGKILL            |
|  - Windows: 匿名管道 (Pipes)、Job Object 與 Gated Launcher 終止     |
+-------------------------------------------------------------------+
```

- **Wave 不是獨立於 JobManager 的另一個排程器**：Wave 是產品名稱與執行入口集合；`JobManager` 是共用的核心 Python 管理物件。GUI 與 Headless 共享完全一致的狀態機與判定邏輯。
- **單一信任來源**：所有的狀態變更、Result 判定、日誌截斷與 Attempt 歷史均由 `JobManager` 與 `Job` 物件維護。GUI 伺服器與 CLI 僅為觀測者，不自行推算或改寫判定。

---

## 2. 生命週期契約：定義凍結與 Attempt 隔離

### 工作定義凍結 (Frozen Definitions)
1. **宣告期**：使用者在 Python 工作定義檔中建立 `Job`，設定參數並宣告 `track`、`check`、`on`。
2. **預覽期**：透過 `load_work_file()` 載入時，腳本使用 `runpy.run_path()` 在一般 Python 使用者權限下執行，並置於 `definition_scope` 作用域中。此作用域僅用來攔截工作檔內誤呼叫 `start()` 或 `resume()`，**並非抵禦惡意程式碼之安全沙盒**。
3. **凍結與派發**：一旦 `mgr.start()` 或 `mgr.validate()` 被觸發，工作之 `cmd`、`cwd`、`env`、`resources` 及所有監控規則即被**凍結**，後續禁止動態修改。若需不同驗收標準，應定義新的 Job。

### Attempt 隔離與不可變性
每次子程序派發（無論是初始執行、失敗自動重試、或手動 Rerun）皆視為一次獨立的 **Attempt**：
- 每次 Attempt 擁有全局唯一的 `attempt_id`。
- 回呼函式接收到的 `bound_job` 嚴格綁定發起該事件的 Attempt 身分。即使畫面或背景發生 Rerun 換輪，舊 Attempt 發起的控制請求（如 `send` 或 `stop`）會被核心驗證並拒絕，絕不會誤送至新輪次。
- Attempt 完成後的判定報告（`Report`）為不可變資料結構（透過 `freeze()` 遞迴轉為 MappingProxy 與 Tuple）。

---

## 3. 狀態 (State) 與結果 (Result) 判定矩陣

Wave 嚴格區分 **程序執行狀態 (`JobStatus`)** 與 **業務驗收結果 (`Result`)**。在 Attempt 結束並彙整最終 Result 時，核心依 `Job._verdict` 嚴格遵循以下順序決策：

1. **第一優先級（FAIL 判定）**：
   - 若發生啟動失敗（`execution_failed`）、執行逾時（`timed_out`）、任何宣告的 check 狀態為 `FAIL`、或子程序非零退出（`returncode != 0` 且未要求 `stop`），一律判定為 **`Result.FAIL`**。
   - **關鍵規則**：若工作在被停止（stop）之前已經有 check 確定判定為 `FAIL`，即使收到 stop 請求，最終 Result 依然維持 **`Result.FAIL`**，絕不被覆蓋為 `INCOMPLETE`。
2. **第二優先級（ERROR 判定）**：
   - 若未滿足上述 FAIL 條件，但執行過程中發生內部異常（`error_count > 0`，例如 monitor 正則損毀或回呼丟出未處理例外），判定為 **`Result.ERROR`**。
3. **第三優先級（INCOMPLETE 判定）**：
   - 若未滿足上述條件，但收到停止請求（`stop_requested`）或仍有任何 check 處於 `INCOMPLETE` 狀態，判定為 **`Result.INCOMPLETE`**。
   - **關鍵規則**：即使所有的 check 均已全數達成（`OK`），若子程序後續被使用者主動終止（或透過 stop 策略結束），由於程序未自然走完正常流程，最終 Result 為 **`Result.INCOMPLETE`**，而非 `PASS`。
4. **第四優先級（PASS 或 N/A 判定）**：
   - 子程序正常自然結束（`returncode == 0`）且無錯誤：
     - 若有宣告 checks，判定為 **`Result.PASS`**。
     - 若完全未宣告任何 checks，判定為 **`Result.N/A`**。

| 子程序 exit code | 宣告的 Checks 狀況 | 執行中異常 (error_count) | 停止請求 (stop_requested) | 最終 State | 最終 Result | 意義說明 |
| :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **0** | 全部 `OK` | 0 | 否 | `DONE` | **`PASS`** | 正常結束且所有檢查皆驗收通過。 |
| **0** | 至少一個 `FAIL` | 任意 | 否 | `DONE` | **`FAIL`** | 程序正常結束，但驗收標記缺失或命中禁用字串。 |
| **0** | 無宣告任何 checks | 0 | 否 | `DONE` | **`N/A`** | 正常結束，但未設定檢驗標準。 |
| **非 0** | 任意 | 任意 | 否 | `FAILED` | **`FAIL`** | 子程序非預期崩潰或退出。 |
| 任意 | 終止前已確認 FAIL | 任意 | 是 | `CANCELLED` | **`FAIL`** | 雖被主動中止，但已知檢查未通過，保留 FAIL 證據。 |
| 任意 | 無 FAIL 且已全 OK | 0 | 是 | `CANCELLED` | **`INCOMPLETE`** | 檢查雖全數達成，但被主動終止，判定為未完整結束。 |
| 任意 | 無 FAIL 且未全 OK | 0 | 是 | `CANCELLED` | **`INCOMPLETE`** | 主動中途停止，判定未完成。 |
| 任意 | 無已確認的 FAIL | $> 0$ | 任意 | 依程序結果 | **`ERROR`** | 監控／控制失效；要求停止也不會抹掉 ERROR。 |

上表為常見案例，仍先排除啟動失敗、逾時與其他較高優先級的 FAIL。停止列假設沒有這些情況；非零退出且未要求停止時，仍優先判 FAIL。

> [!IMPORTANT]
> **核心不變性**：
> 1. 程序退出碼 0 絕不等於 PASS。
> 2. 一旦在執行中確認某個 check `FAIL`，即使後續監控發生例外或被 stop，該 `FAIL` 結果會被完整保留。

---

## 4. 記憶體有界性 (Bounded Memory) 設計

為支援長時間運行、巨量日誌輸出的工作，Wave 實作了嚴格的記憶體保護機制：

1. **日誌環形緩衝區 (`max_log_lines`)**：
   - 每個 Attempt 僅在記憶體中保留最新的 $N$ 行輸出（預設 10000 行）。
   - 過舊日誌被淘汰時，行號計數（`total_lines`）持續累加，不打亂全域行號。
2. **獨立證據存檔**：
   - 當 Check 命中或 Warning 產生時，觸發當下的行號與行文字證據會被**獨立快照**在該規則的資料結構中。即使該行日誌稍後被環形緩衝區淘汰，檢驗證據與報告依然完整保存。
3. **有界歷史輪數 (`max_history`)**：
   - 每個工作在記憶體中保留最多 $M$ 輪已結束的 Attempt 歷史（預設 20 輪）。
   - 超出上限時，最舊的歷史記錄會被安全丟棄，介面揭露截斷計數（`_history_truncated`），且不重算剩餘歷史的 Result。

---

## 5. 跨平台程序模型契約

| 平台 | 終端與輸入輸出介面 | 信號與中斷機制 | 程序樹清理保證 |
| :--- | :--- | :--- | :--- |
| **POSIX (Linux / macOS)** | 預設啟用虛擬終端 (**PTY**)，支援真實行緩衝與終端控制序列；亦可配置 `pty=False` 切換為管道 (Pipes)。 | 啟動時透過 `start_new_session=True` 建立獨立 Process Group；發送 `SIGINT` 或 `SIGTERM` 至整個進程群組。 | 終止時向群組發送信號，防止孤兒程序殘留。 |
| **Windows** | 不支援 POSIX PTY，固定使用標準管道 (**Pipes**)。 | 透過 Windows Console Control 事件或終止信號；受限於 Windows 控制台機制，未附加 console 之進程主要依賴終止信號。 | 透過 Windows Job Object（`SetInformationJobObject` 搭配 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）與 Gated Launcher，確保父程序退出或終止時所有衍生子進程皆由作業系統自動銷毀清理。 |

> [!NOTE]
> Linux 環境之 PTY 與信號行為應於真實 Linux 主機上進行驗證；在 Windows 環境下略過（skipped）的 POSIX 測試不代表新版 Linux 行為已驗收通過。

---

## 6. 監控錯誤隔離與回呼執行限制

### 錯誤隔離機制 (Monitor Fault Isolation)
當使用者註冊的 monitor（如 check、warning、track）或 action 回呼函式在執行過程中拋出未處理例外時：
- Wave 會保存例外型別、訊息、來源與相關輸出證據，可透過 `job.result.errors` 或快照的 `errors` 查詢；不承諾保存完整 Python traceback。
- **當次 Attempt 停用故障項**：其餘監控與程序預設繼續；新的 retry／rerun Attempt 會重新啟用監控。需要遇錯即停時，明確訂閱 `job.on(job.events.error, stop=True)`。
- **PASS 禁能**：由於 `error_count > 0`，該工作最終無法判定為 `PASS`，最高僅能獲得 `ERROR`（若有 check 失敗則為 `FAIL`）。

### 回呼延遲與非阻塞要求 (Callback Latency Constraints)
- **同步執行**：使用者回呼函式（例如 `job.on(Event.LOG, callback=...)`）在觸發事件的執行緒中同步執行，同一管理器的事件處理共用鎖。阻塞可能同時拖慢其他工作的監控與控制，不能假定每個工作互不影響。
- **禁止耗時阻塞**：回呼函式內部切勿執行長時間阻塞操作（例如 `time.sleep()`、大量磁碟 I/O 或耗時的網路請求）。任何延遲都會直接阻礙作業系統管道資料的排空速度，甚至反壓子程序導致死結。
- **非同步工作分流**：若需進行耗時處理，請在回呼內僅進行簡單狀態變更，或將資料推入使用者自有的佇列/執行緒中非同步執行。

Attempt 定稿後，完成通知回呼的例外只記入 diagnostics，不倒改已完成的 Result，也不再次觸發 error 事件。
