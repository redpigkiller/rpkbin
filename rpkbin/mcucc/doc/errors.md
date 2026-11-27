# 錯誤目錄

CLI 的參數或 mcucc 診斷走 stderr，前綴是 `error: `，結束碼為 2。`check-forms` 或 `verify` 的比對結果失敗時，結果寫到 stdout、結束碼為 1。Python API 以 `rpkbin.mcucc.errors.McuccError` 為共用基底；一致性或差異測試的個別資料列另放在回傳值。

下列文字逐字取自 [small 範例](../examples/README.md) 與其修改案例的實測。行號、函式名、值及 form 識別字會隨輸入而異。

| 案例 | 實際訊息片段 | 類別 | 原因 | 處理方式 |
|---|---|---|---|---|
| first-width | `first integer assignment needs a type annotation` | SourceError | 新變數首次以裸整數指定，例如 `value = 1`。 | 改為 `value: u8 = 1` 或使用適合資料的寬度。 |
| statement | `Try: unsupported statement` | SourceError | 來源出現 `try/except`。 | 用 `if` 檢查條件，以定寬值回傳錯誤狀態。 |
| operation | `BinOp: unsupported expression or operation` | SourceError | 函式內的 `a * b` 超出運算子集合。 | 若乘數是二的冪且位移在範圍內，可用常數位移；否則以合法運算重寫或呼叫外部函式。 |
| recursive | `recursive call graph` | SourceError | 函式直接或間接呼叫自己。 | 移除呼叫環，使用明確迴圈。 |
| target-width | `target.cell_bits: 16; expected 8` | TargetError | Target 的 cell 寬度與編譯器模型不合。 | 確認硬體資料單位。只有在可如實描述成 8 位元 cell 時才改宣告。 |
| address-kind | `target.forms['read_cell'].operands.m: R(name='m', cls='byte'); expected M address operand` | TargetError | 直接載入把暫存器運算元宣告為位址。 | 查訊息的 form id 和綁到 `a` 的位址。直接載入／儲存使用 `M`，間接載入／儲存使用 `P`。 |
| missing-form | `(sub, 8) has no ideal expansion; missing form/register class` | UnsupportedError | small 的 8 位元減法沒有可配對的 form。 | 查可用性矩陣，核對寬度與位置類別。若目標確有合規指令，補 form 並先跑一致性檢查。 |
| raw-code | `raw asm 沒有編碼入口` | UnsupportedError | 對含內嵌組語的函式呼叫 `assemble`。 | 使用文字組語與專案自己的組譯流程；需要機器差異測試時，以可編碼的來源操作取代內嵌組語。 |
| allocation | `no finite allocation; missing register class/scratch or spill pool slot` | AllocationError | 保留值所需的位置、暫存器或可用槽不足。範例只有兩個暫存器，且沒有搬至槽的記憶體 form。 | 查訊息中的函式與指令。檢查位置類別、可用的載入／儲存、pool 容量，再降低同時要保留的值。只有 pool 容量不足時才擴大已保留區。 |
| pool-contiguous | `spill pool needs 1 contiguous cells, available 0` | AllocationError | 已保留的 pool 合法，但分配下一個槽時沒有連續空間。`available 0` 是當下的剩餘容量。 | 確認沒有與來源、外部函式或 ABI 共用位址後再擴大 pool，或縮短同時在用的值。 |
| window | `error: --window is required` | ConfigError、CLI 2 | 未傳驗證取樣窗口。 | 明定安全窗口，例如 small 的 `--window 256`。 |
| verify-driver | `verify driver requires an absolute call form` | UnsupportedError、CLI 2 | 目標不提供絕對呼叫，驗證驅動程式無法進入被測函式。 | 補上目標實際支援的絕對呼叫、編碼與模擬語意。若硬體沒有此機制，回報驗證限制。 |
| invalid-depth | `park_depth -1: expected nonnegative integer` | ConfigError | Python 設定負的堆疊暫存預算。 | 停用時用 0；啟用時根據實際堆疊空間指定正整數。 |
| encoder-length | `form 'ret': encode length/cells disagree with form (expected 1, got 0)` | TargetError | 編譯的編碼長度檢查發現回傳 form 宣告 1 byte，encoder 卻回傳空序列。 | 停用該輸出，先比對宣告、回呼與硬體編碼。若在正確的回呼下仍能重現，提交最小案例。 |
| forms-failure | `encoded cells (); expected length 1 and in-range cells` | CLI 1 | 上例的回呼錯誤由 form 檢查列為失敗。 | 修正宣告或 encoder 後重新執行檢查。 |

若 `AllocationError` 帶有 `[private-pool mode]`，表示私有槽重試仍失敗。若 `verify` 列出 `unsupported:`、`unverified: asm` 或 `unverified: no SIM`，該函式沒有機器層的通過證據，即使 CLI 結束碼為 0。

## 回報有問題的輸出

保留最小來源、Target 檔、版本、完整命令、結束碼、stdout、stderr，以及你期待的結果和依據。若發生機器差異，附失敗輸入和窗口；先確認 Target 宣告及模擬器和硬體的關係。若收到內部檢查錯誤，請勿使用該次產生的輸出。[操作流程](../README.md) · [驗證邊界](guarantees.md)
