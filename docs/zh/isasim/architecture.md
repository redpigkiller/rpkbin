# `rpkbin.isasim` 架構與邊界

[English](../../en/isasim/architecture.md) · [文件導覽](../README.md) · [快速入門](README.md) · [使用情境](scenarios.md) · [組譯器](assembler.md) · [API 參考](reference.md)

`rpkbin.isasim` 將宣告式機器結構（`MCU`）、靜態組譯產物（`Assembly`）、交易式執行期模擬（`Simulator`）與可攜式檢查點（`Snapshot`）分層管理，並由單一擁有者管理可變狀態。

## 職責劃分

`MCU` 是宣告式規格，包含記憶體空間、暫存器、暫存器別名、記憶體別名（`mem_alias`）、IRQ 線、指令編碼、回呼函式與存取政策。它是模擬器的輸入，不持有執行期機器狀態。經由 `load_mcu()` 載入的 Python 描述檔必須定義一個 `mcu` 物件。

前處理器負責展開 `.include` 檔案與條件組譯指示詞，同時記錄每一行的來源位置。組譯器依 `MCU` 指令宣告編碼有效行，輸出包含 cell 資料、符號與來源對照的 `Assembly`，過程中不執行指令回呼函式。

`Simulator` 是所有可變執行期狀態的唯一擁有者。`State` 是傳入指令與中斷回呼的受檢查存取邊界，公開的 `Simulator.state` 屬性則為唯讀檢視。外部修改必須透過 `poke()` 進行並產生 `"external"` 事件。`Simulator` 實作 context manager 協定（`__enter__` / `__exit__`），離開 `with` 區塊時會自動關閉作用中的 trace 檔案。

各模組檔案的職責如下：

| 檔案 | 職責 |
| :--- | :--- |
| `mcu.py` | `MCU` 宣告、`MemAliasInfo` 解析、驗證、`MCUDescription` 匯出與指令模式編譯。 |
| `state.py` | 供回呼函式與唯讀檢視使用的受檢查存取器（`State`、`RegAccess`、`MemAccess`、`SpaceAccess`、`IrqAccess`）。 |
| `sim.py` | 指令解碼、step 迴圈、寫入日誌與回滾、執行期 `mem_alias` / `clear_mem_aliases`、事件派送與唯一可變狀態所有權。 |
| `events.py` | `Event`、`ReadRecord`、`WriteRecord`、`RunResult`、`StuckReport`、超寬整數 JSON 保護與文字／JSONL 格式化。 |
| `stimulus.py` | `Device` 基底類別、`_DeviceContext`（`ctx`）、`Trigger` / `read_seq` 刺激訊號、IRQ 橋接與卡住偵測。 |
| `debug.py` | 受限 AST 條件編譯器與求值器、斷點、觀察點（含 `mem_alias` 觀察）與顯示運算式。 |
| `_preprocess.py`、`assembler.py` | 來源展開、兩階段位址配置、編碼、`SourceRecord` 與 `.isa.json` bundle 重播。 |
| `loader.py` | Intel HEX、二進位檔、符號 JSON 與 `Assembly` 載入，以及來源位置查詢。 |
| `snapshot.py` | 不可變 `Snapshot` 檢查點與結構設定相容性檢查（包含 `mem_aliases`）。 |
| `helpers.py` | 明確位寬整數運算、位元操作與 `AddResult` / `SubResult` dataclass。 |
| `cli.py`、`repl.py` | `rpk-isasim` 命令列介面與串流式 25 命令 REPL。 |

`Simulator` 繼承四個內部服務 mixin：`_External`、`_Debug`、`_Snapshots` 與 `_Loaders`。它們透過模擬器擁有者協作，不會平行保留獨立的機器狀態。核心執行流程與交易回滾直接實作於 `sim.py`。

## 資料流與不變條件

```text
MCU 描述（含 mem_aliases）+ Assembly / HEX / BIN 程式映像
                  │ 輸入
                  ▼
       Simulator 擁有可變狀態
        ├── 回呼僅透過 State 寫入（記入回滾日誌）
        ├── 執行期 mem_alias / clear_mem_aliases / 觀察點
        ├── Event 串流與 RunResult 輸出
        └── Snapshot 儲存與還原（簽章驗證）
```

- **不預設目標硬體**：程式與資料 cell 位寬及指令語意全由 `MCU` 描述提供，套件不假設特定處理器架構或記憶體配置。
- **單步交易與回滾**：每個指令步驟都會記錄模擬器持有的暫存器、記憶體、IRQ、cycle 與 `next_pc` 異動。指令語意回呼或 `Device` `read`/`write` 發生失敗時，已記錄的模擬器狀態會回滾。讀取僅供觀察點與 trace 使用；`Device` 的外部副作用與已消耗的 `read_seq` 位置不會回滾。
- **步界中斷檢查**：`interrupt_check(st)` 在步驟邊界執行。回傳 `None` 時會丟棄檢查期間的寫入與額外 cycle；回傳 `(event_name, cycles)` 時則將這些異動提交為 `"interrupt"` 步驟，其中 `event_name` 僅為事件標籤，不必對應已宣告的 IRQ 線。
- **記憶體別名（`mem_alias`）**：
  - `MCU.mem_alias()` 將機器層級的記憶體別名宣告（`MemAliasInfo`）依宣告順序儲存於 `MCU`（`MCU.mem_aliases`）。
  - `Simulator` 初始化時會從 `mcu._mem_aliases` 載入別名表。`Simulator.mem_alias()` 可在模擬器執行個體上動態註冊別名（名稱衝突或跨 cell 別名與已掛載 `Device` 重疊時拒絕），而 `Simulator.clear_mem_aliases()` 會清除模擬器動態註冊的別名並保留 MCU 原先宣告的別名。`Simulator.reset()` 會保留模擬器已註冊的 `mem_aliases`。
  - `read()`、`poke()`、`Trigger.set()`、除錯運算式（`m.<alias>` / `m["<alias>"]`）與觀察點共用 `_pack_mem_alias_cells` 和 `_mem_alias_write`。單一 cell 位元切片在 `poke()` 與 `Trigger.set()` 時執行讀改寫。
  - 在每步開始的 `S0` 邊界，`_validate_mem_alias_refs()` 會確認已安裝的觀察點與 `Trigger.set` 所引用的 `mem_alias` 名稱仍然存在且未落於裝置範圍（否則拋出 `DebugError` 或 `StimulusError`）。
- **快照相容性**：`Snapshot` 保存模擬器暫存器、記憶體、初始化旗標、IRQ 線、計數器、裝置狀態（`get_state` / `set_state`）與刺激訊號執行期狀態。還原時比對結構簽章（`spaces`、`registers`、`irq_lines`、`devices`、`stimuli`，以及非空的 `aliases`、`mem_aliases`、`regions`），不比對 Python 回呼函式內容。
- **組譯映像與來源對照**：`load_assembly()` 會在原子替換程式映像與來源對照前先完成驗證。它不重設暫存器或 cycle 計數，`reset()` 亦會保留已掛載的組譯映像。載入原始 cell、HEX、二進位或舊版符號檔會清除來源對照。透過 `poke()` 或 `restore()` 修改已對照的程式記憶體會使來源查詢失效（`source_context()` 拋出 `DebugError`，新增來源斷點拋出 `TargetError`），已登記的斷點仍保留解析出的位址。
- **提交後訂閱者與 `tick`**：Trace 訂閱者與 `Device.tick()` 在步驟提交後執行，若此時拋出例外不會撤銷已提交的步驟。

## 輸入與輸出

輸入包含一個 `MCU` 模型，以及來自 Python 字典、Intel HEX、原始二進位檔或完整 `.isa.json` 組譯 bundle（內含多檔 `.include` 來源與符號對照）的程式 cell。舊版符號 JSON 也可提供標籤與行號資訊。外部行為透過 `Device` 物件、`read_seq`、`poke()` 以及 `at_cycle` / `at_pc` / `on_read` / `on_write` 刺激訊號進入系統。

輸出為結構化的 `Event` 與 `RunResult`，可格式化為文字或 JSONL trace、在 REPL 中檢視，或由 Python 程式碼直接處理。`Snapshot` 可將檢查點序列化為 UTF-8 JSON。`rpkbin.isasim` 本身不含圖形介面，視覺化除錯請使用 `rpkbin.app.isasim_gui`。

## 範圍與限制

每條指令宣告具有固定的編碼長度，但不同指令可採用不同長度。組譯器不支援巨集（macro）、連結器（linker）或指令長度自動鬆弛（relaxation）。`rpkbin.isasim` 只執行使用者提供的模式與語意，不驗證模型是否符合實體晶片規格；目標精確度仍取決於經過驗證的模型、測試程式與標準參考結果。

## 延伸閱讀

- [快速入門](README.md)
- [API 參考](reference.md)
- [組譯器](assembler.md)
- [使用情境](scenarios.md)
- [視覺化瀏覽器除錯器（`isasim_gui`）](../app/isasim_gui/README.md)
