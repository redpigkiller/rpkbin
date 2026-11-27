# `rpk-calc` 架構與資料邊界

[快速入門與參考](README.md) · [文件導覽](../README.md) · [English](../../en/calc/architecture.md)

`rpk-calc` 依模式區分執行邊界：簡易模式透過 `SimpleCalcEngine` 在 Qt 程序內直接運算，不啟動 Python／APyTypes 或 IPython 子程序；Python Studio 由 Qt 前端對接專用的本機 IPython kernel，由 kernel 擁有所有工作區物件；`rpk-calc --eval` 則直接建立無介面 `WorkspaceSession`，不啟動 Qt 與 kernel 子程序。

---

## 執行模式與架構邊界

```text
rpk-calc (cli.py)
 ├── -e / --eval ──> WorkspaceSession（無介面；讀取 CalcConfig 與同目錄 startup.py；無 Qt、無 kernel）
 └── GUI (ui/app.py: CalculatorWindow)
      ├── 簡易頁面 ──> SimpleCalcWidget -> SimpleCalcEngine (local_engine.py，同程序執行)
      │                                    （使用 Fraction / Decimal + scalar_fxp；無 kernel、無 apytypes）
      └── Python 頁面 ──> CalcMainWindow -> CalcController (controller.py)
                                              │ 127.0.0.1 Jupyter 通道（僅傳遞 JSON DTO）
                                              ▼
                                         KernelTransport (kernel_client.py)
                                              │
                                              ▼
                                         CalcKernelRuntime (kernel_runtime.py) -> WorkspaceSession
```

| 檔案 | 職責 |
|---|---|
| `cli.py` | 解析引數（`-V`/`--version`、`-c`/`--config`、`-e`/`--eval`、`--simple`、`--python`/`--studio`、`--mode`）、分派無介面 `--eval`，以及檢查選配套件錯誤。 |
| `local_engine.py` | 實作 `SimpleCalcEngine` 狀態、運算式解析與求值器、4 種移位模式、`NAND`/`NOR`、`DEC` 未縮放整數語意與 `display_fraction`。 |
| `ui/app.py` | `CalculatorWindow` 外殼管理 `SimpleCalcWidget` 與延遲建立的 `CalcMainWindow` 切換，同步共用 `CalcConfig` 與視窗幾何狀態。 |
| `controller.py` | `CalcController` 序列化 Studio GUI 請求、追蹤 `view_id` 與 `revision`、協調忙碌與不同步狀態，並同步 `CalcConfig`。 |
| `kernel_client.py` | `KernelTransport` 管理單一由應用程式擁有的 IPython 子程序生命週期，透過 `127.0.0.1` Jupyter 通道通訊並處理逾時與資源清理。 |
| `kernel_runtime.py` | `CalcKernelRuntime` 在 kernel 內執行命令視窗程式碼、維護 `WorkspaceSession` 與 `EditJournal`，並處理快照、分頁與編輯請求。 |
| `protocol.py` | JSON-only DTO 轉換、`PROTOCOL_VERSION = 1`、16 進位 `build_id()`、`PAGE_ROWS = 32`、`PAGE_COLS = 16`、`MAX_COPY_CELLS = 100_000` 與 `CONTROL_MIME`。 |
| `core/session.py` | `WorkspaceSession` 命名空間、`whos`/`clc`/`clear`、`WorkspaceRowSnapshot`/`MatrixSnapshot` 及稀疏陣列來源追蹤 sidecar。 |
| `core/config.py` | `CalcConfig` 讀寫（`~/.rpkbin/calc_settings.json` 或 `--config`）、`KERNEL_CONFIG_FIELDS` 與 `ALL_WORKSPACE_COLUMNS`。 |
| `core/public_fxp.py` | 公開建構函式 `fxp` 與 `fxpi`（`RpkFixed`／`RpkFixedArray`），繫結目前工作階段的設定提供者。 |
| `core/editing.py` | `EditJournal`（100 筆／8 MiB）、原子性 TSV `paste_rectangle`、`conversion_preview` 與 `selection_statistics`。 |
| `core/representations.py` | 唯讀 `native_bits` 描述子與 `APyFloat` 位元欄位中繼資料（含 4,096 位元 `int` 顯示上限）。 |
| `core/startup.py`、`core/auto_imports.py` | `StartupFile` 修訂版本檢查與舊版遷移，以及驗證後的絕對路徑自訂匯入載入。 |

---

## `SimpleCalcEngine`（`local_engine.py`）架構邊界

`SimpleCalcEngine` 負責簡易模式的獨立定點數與程式設計師計算機核心：

- **同程序執行且無選配套件依賴**：`SimpleCalcEngine` 在 Qt 程序內同步執行，只使用 Python 標準函式庫 `fractions.Fraction`、`decimal.Decimal`，以及 `core/format_engine.py` 和 `core/scalar_fxp.py`。它不啟動 IPython 子程序，也不匯入 `apytypes` 或 `numpy`。
- **擁有的本機狀態**：每個 `SimpleCalcEngine(config: CalcConfig)` 實例擁有：
  - `primary_spec: FxpSpec | None`（未設定時 `.spec` 回退至 `INT32 = FxpSpec(wl=32, fwl=0, signed=True)`）與 `compare_specs: list[FxpSpec]`。
  - 目前純量狀態：`exact: Fraction`、`raw_uint: int`、`status_kind: str`（`"exact"`、`"quantized"`、`"saturated"` 或 `"wrapped"`）。
  - 移位狀態：`shift_mode: str`（`SHIFT_MODES`：`"arithmetic"`、`"logical"`、`"rotate"`、`"rotate_carry"`）與 `carry_bit: int`（`0` 或 `1`）。
  - 本機變數表 `variables: dict[str, VariableSnapshot | None]`，預設鍵為 `"M"`、`"A"`、`"B"`、`"C"`、`"D"`、`"E"`、`"F"`（加上 `SimpleCalcWidget` 新增的自訂識別字槽位），以及最多保留最近 100 筆的 `history: list[tuple[str, VariableSnapshot]]`。
- **模式隔離與共用設定**：
  - `SimpleCalcEngine.variables` 與 `SimpleCalcEngine.history` 不會進入 `WorkspaceSession.user_ns`、`EditJournal` 或 `CommandWindowWidget` 歷史。
  - 在 `CalculatorWindow` 中切換簡易與 Python 模式時，未啟用頁面在 `ActivePageStack` 上會設為停用並隱藏。Python Studio（`CalcMainWindow` 與 `CalcController`）於首次切換至 `"python"` 時才延遲建立，切回 `"simple"` 後仍保持執行。
  - 兩個模式共用 `CalcConfig`。簡易模式中的本機設定變更（主要 FXP 格式、格式歷史、量化／溢位抽屜、位元順序）會發出 `local_state_changed`，更新 `CalculatorWindow.config`；若 Studio 已建立則同步呼叫 `CalcController.update_config(cfg)`，否則直接寫回設定檔。
- **有界精確求值與不變條件**：
  - 輸入與格式字串上限為 `_MAX_INPUT = 512` 字元；括號巢狀上限為 `_MAX_DEPTH = 64` 層，一元運算子（`+`、`-`、`~`）採迴圈迭代解析以避免 `RecursionError`。
  - 字長 `wl` 限制在 `[1, 256]`（UI 格式抽屜微調框限制 `WL` 為 `1..128`、`FWL` 為 `-128..128`），`parse_format()` 要求 `abs(fwl) <= 256`，移位次數限制為 `[0, 256]` 的整數且以 `unscaled=True` 解析，不受 `2^-fwl` 縮放影響。
  - `evaluate()` 具交易性：解析前先保留 `carry_bit`，若求值失敗便還原 `carry_bit`，且不改動 `exact`、`raw_uint` 與 `status_kind`。
  - `set_primary()` 從 `self.exact` 而非 `self.real` 重新量化，在切換主要格式時保留精確的 `REAL` 數值。
  - `parse_operand()` 拒絕緊接在 `0x`、`0o`、`0b` 前綴後的 `+` 或 `-`，對非 `DEC` 進位字面值檢查 `0 <= value < 2^wl`，對 `DEC` 整數字面值則乘以 `2^-fwl`。
  - `display_fraction()` 在分母為 `2^a * 5^b` 且位數不超過 `_MAX_EXACT_BITS = 3322`、冪次不超過 `_MAX_EXACT_POWER = 1000` 時輸出精確有限小數（避開 Python 4,300 位數整數轉字串上限），其餘情況改用 16 位有效數字的 `Decimal` 表示。
- **範圍邊界**：`SimpleCalcEngine` 僅支援純量定點數與整數位元運算；陣列、NumPy／SciPy、`APyFloat`、`RpkFixed`／`RpkFixedArray`、`EditJournal` 與 `startup.py` 皆僅屬於 `WorkspaceSession`（Python Studio 與 `--eval`）。

---

## Python Studio 職責與不變條件

- `kernel_client.py` 透過綁定 `127.0.0.1` 的 Jupyter 通道啟動並管理單一由應用程式擁有的 IPython 子程序，將 `Path(__file__).resolve().parents[3]` 置於 `PYTHONPATH` 前端，並以非阻塞方式處理回覆。`_idle_clock()` 在計算內部請求 15 秒逾時時會扣除使用者 Cell 執行時間；`_timed_out_ids` 忽略逾期內部請求的遲到回覆，避免誤清正在執行的使用者狀態；`_cleanup_resources()` 會在正常關閉、啟動逾時（25 秒）或健康檢查失敗時統一清理通道、kernel、Log 檔案與暫存執行目錄。Kernel 不會自動重啟，關閉視窗時由應用程式終止。
- `kernel_runtime.py` 在 kernel 內執行使用者程式碼、擁有 `WorkspaceSession` 與 `EditJournal`，並處理檢視、分頁、複製、預覽、統計與修改請求。GUI 僅接收格式化後的快照，不接收 Python 物件或完整陣列。
- `controller.py` 序列化 GUI 請求（佇列上限 32 筆）、追蹤 `view_id` 與工作區 `revision`，並透過 `interaction_enabled` 協調忙碌狀態。`restart_kernel()` 會將 `self.revision` 重設為 `-1`，確保重啟後從 `revision = 0` 開始的回覆不會被誤丟棄。當符合目前 `revision` 的矩陣分頁請求失敗或回傳空頁時，發出 `matrix_page_failed` 以清除未決圖磚狀態。`update_config()` 透過 `op: "config"` 同步 `KERNEL_CONFIG_FIELDS`（`format_preset`、`custom_signed_tpl`、`custom_unsigned_tpl`、`custom_int_tpl`、`default_quant`、`default_overflow`、`auto_import_groups`、`custom_import_groups`、`startup_file`、`visible_columns`），並在 `transport.ready` 為否或 `_out_of_sync` 時略過無效排隊。`inspector_pin_mode` 不在 `KERNEL_CONFIG_FIELDS` 內，由 `CalcController` 在 `preview`／`reformat` 請求中明確傳遞 `pin_mode`。
- `CalcMainWindow` 採用 Qt Fusion 樣式，確保搭配既有 QSS 在 Windows 11 上正常繪製核取方塊勾選狀態。Workspace 的純量 Value／Format 操作導向 Inspector 控制項，陣列 Value 則開啟矩陣編輯器；**命令提示**（或 **說明 → 命令提示**）開啟包含 **FXP**、**Python / NumPy** 與 **Workspace** 的三分頁非模態參考視窗，預設顯示 **FXP**。
- `SettingsDialog` 收集偏好並提供唯讀啟動檔預覽。外觀設定與 `keep_english_terms` 僅更新 Qt；數值預設則更新執行中的 session。套用內建函式群組或自訂匯入變更時，只會在既有 kernel 重新載入設定的匯入項目，不執行 `startup.py`、不清除命名空間、也不重啟 kernel。啟動程式只在 kernel 啟動／重啟或明確執行 **重新載入並執行** 時執行。
- 設定對話框將取消／套用頁尾置於可伸展的分頁區下方，捲動設定頁時頁尾維持對齊。
- 收合 Inspector 僅隱藏主體並保留標題列箭頭，開啟陣列時會重新展開 Inspector；關閉視窗時保存幾何位置、分隔線尺寸與 Inspector 收合狀態。
- `protocol.py` 與 `MatrixTableModel` 定義 JSON DTO 與有界矩陣分頁（`PAGE_ROWS = 32`、`PAGE_COLS = 16`，`MatrixTableModel` 最多快取 `MAX_CACHED_PAGES = 8` 頁）。形狀與切片中繼資料查詢不讀取完整陣列；Workspace Min/Max 僅在該欄顯示時掃描；FXP 陣列飽和摘要最多檢查 4,096 格（`MAX_SATURATION_CHECK_CELLS`）；複製整張矩陣上限為 100,000 格（`MAX_COPY_CELLS`）。
- 原生數值的 **轉為 FXP…** 對話框送出帶有 `expected_revision` 防護的轉換請求；FXP 行內格式控制與 **轉為浮點數…** 共用同一轉換路徑。

---

## FXP 轉換來源與狀態

原生 APyTypes 建構式建立的數值沒有計算機轉換來源中繼資料，即使其位元完全符合輸入，仍會顯示 `No conversion reference (error unknown)`。Python `float` 採用 binary64（例如 `0.1` 本身已是十進位 0.1 的二進位近似值）。唯有 GUI Real 編輯、`fxp(...)` 或 Real 釘選重設格式會記錄比較來源；原始位元編輯與 Hex 釘選重解讀不會記錄。

對於原生 `APyFixedArray` 的 GUI 編輯，`WorkspaceSession` 會在有界 sidecar（`_native_sources`）依原擁有者物件稀疏保存來源資料，不替換原陣列也不破壞別名參考。讀取時驗證擁有者身分、shape、FXP 規格與目前原始位元，任一不符便回報未知。Sidecar 最多保留 32 個擁有者與 100,000 筆儲存格紀錄並採 LRU 淘汰（僅移除診斷資料，不改動數值）。重設會清空 sidecar，重新整理會移除已不存在命名空間的擁有者，複製變數會一併複製有效紀錄，復原／重做則還原來源中繼資料。

設定中的 `default_quant` 與 `default_overflow`（預設 `"RND"` 與 `"SAT"`）套用於 GUI 實數編輯、原生轉 FXP、Real 釘選格式變更，以及 `fxp(...)` 預設參數；它們不覆蓋原生 APyTypes 建構式（`APyFixed.from_float(...)` 預設為 nearest ties away 與 `WRAP`）或主控台算術。

`RpkFixed` 與 `RpkFixedArray` 保留 GUI 中繼資料與 `copy()` 行為，算術運算子直接繼承 APyTypes，不額外擴寬運算元或包裝結果。運算結果型別遵循原生 APyTypes／NumPy 分派，且不附帶 GUI 轉換來源。

---

## Workspace 陣列飽和標記

Kernel 產生 `WorkspaceRowSnapshot.is_saturated: bool | None` 供 Qt 顯示：

- `True`：至少一格目前的位元仍符合追蹤到的飽和 GUI 編輯。
- `False`：每格皆有符合的已驗證來源且均未飽和。
- `None`：來源不完整、位元與紀錄來源不符，或陣列超過 4,096 格（`MAX_SATURATION_CHECK_CELLS`）。

Qt 在 FXP 陣列的 Workspace Value 欄加上前綴：紅色 `[SAT]` 代表 `True`，中性 `[SAT?]`（附帶說明來源不完整的 tooltip）代表 `None`，`False` 不加標記。標記僅屬於顯示文字，不影響 `value_str` 或複製內容。單格編輯僅更新該格的飽和中繼資料，不執行全陣列掃描；單格 Inspector 檢查亦不受陣列大小上限影響。

---

## 原生數值與 FXP 邊界

Python `int` 保有任意精度，主控台中的 `a = 2` 依 Python 語意重新繫結變數。Inspector 以 `PlainValueSnapshot` 呈現原生數值，不推測隱含的 FXP 字長或釘選語意。Inspector 可將原生實數純量／陣列轉成 FXP，或將 FXP 轉為 Python `float`／NumPy `float64`（保留陣列 shape）；`APyFloat` 與其餘型別請於主控台轉換。

Kernel 的 `core/representations.py` 產生唯讀 `native_bits` 描述子：
- NumPy 固定寬度整數純量回報 dtype、位寬、有號性、十六進位與二進位，不開放 FXP 位元編輯。
- Python `int` 的 Hex／Binary 採有號絕對值表示；超過 4,096 個 magnitude bits（`MAX_INTEGER_DISPLAY_BITS`）時改為有界預覽並停用截短複製。
- Python `float` 與 NumPy `float16`／`float32`／`float64` 依儲存位元顯示 sign、exponent、fraction 與特殊值分類（不推測 `longdouble`、`complex` 與 `APyFloatArray` 佈局）。

GUI 格式輸入由 `FormatEngine.parse_input()` 依 `Q`、`MATLAB` 與目前啟用的自訂範本解析。僅有能唯一還原正負號、`wl` 與 `fwl` 的簡單佔位符可逆向解析；衝突輸入會被拒絕，`CalcController` 在送出請求前會將 `FxpSpec` 正規化為標準 `Qm.n`／`UQm.n` 字串。

FXP Inspector 的 Real 數值欄使用受限 AST 求值器，接受數值字面值、`pi`/`e`、四則與整數位元運算，拒絕函式呼叫、屬性存取、容器與一般語句。輸入上限為 4,096 字元、512 個 AST 節點、16,384 位元整數結果、絕對值不超過 4,096 的指數與移位量，且須為有限實數。Python REPL 不受此欄位解析器限制。

---

## 數值表示 DTO 與 `APyFloat` 編輯

`PlainValueSnapshot.native_bits` 是 kernel session 產生給 Qt 的 JSON DTO，單一 `APyFloat` 的描述子範例如下：

```json
{"kind":"float","dtype":"APyFloat","width":8,"hex":"0x1E","binary":"00011110","exp_bits":5,"man_bits":2,"bias":7,"classification":"Normal","editable":true}
```

`native_bits["editable"]` 獨立於 `PlainValueSnapshot.editable` 控制位元切換按鈕。只有純量 `APyFloat` 支援位元切換（kernel 以 `to_bits`／`from_bits` 保留格式、bias 與 NaN payload，不經由 Python `float` 重建）；一般浮點數與選取的陣列元素保持唯讀。`EditJournal` 指紋比較原始位元與完整格式（區分 `+0.0` 與 `-0.0`）。

---

## GUI 編輯、矩陣操作與命令歷史

- **`EditJournal`**（`core/editing.py`）：由 `CalcKernelRuntime` 擁有，追蹤 GUI 對純量、單格與矩形貼上的數值／位元編輯，上限為 100 筆或 8 MiB，不複製大型陣列。復原／重做前會驗證變數繫結、切片／儲存格座標、shape、dtype、strides、FXP 格式與目前數值指紋。執行 REPL cell、陣列結構操作、整體重新格式化或變更切片皆會清空日誌。
- **TSV 貼上**：`paste_rectangle()` 先解析並驗證整塊矩形、邊界、dtype／範圍與可編輯性（上限為 100,000 格 `MAX_EDIT_CELLS` 與 8 MiB），驗證通過後才寫入，發生未預期錯誤時自動回復。
- **矩陣快照與統計**：`MatrixSnapshot` 攜帶 `full_shape` 與 `slice_indices`，超過二維時後續軸預設為 `0`。`selection_statistics()` 最多對 100,000 格轉換為 `float64`／`complex128` 計算有限值摘要（`count`、`finite_count`、`sum`、`mean`、`min_abs`、`max_abs`，實數資料另含 `min`／`max`）。
- **命令歷史**：`CommandWindowWidget` 在 Qt 端保存已送出的命令文字（獨立於 IPython 歷史），上限為 1,000 筆或 1 MiB（單筆超過 1 MiB 會略過）。匯出的 UTF-8 `.py` 檔會註明其中 IPython magic 可能需要 IPython 才能執行。

---

## 顯示與 DTO 範圍

Workspace `numeric_display`（`"auto"`、`"scientific"`、`"fixed"`）與 `numeric_precision`（`1` 至 `17` 位小數）僅作用於 Workspace Value 與 Matrix `DisplayRole` 的有限實數顯示文字；Inspector、Matrix `EditRole`、複製內容與原始位元保留原精確度。Fixed 輸出預估超過 256 字元時會回退為科學記號。**檢視 → 重設版面** 只還原視窗尺寸、分隔線與 Inspector 收合狀態。

目前單一命令視窗對應單一 IPython kernel；未來若支援多命令視窗，須先釐清 kernel／命名空間共用策略、輸出串流歸屬與中斷目標。

---

## Kernel 協定封包

控制請求透過 Jupyter `user_expressions` 傳回 `application/json`（不使用 pickle）；`clc` 則透過 IOPub `display_data` 發送 `CONTROL_MIME`（`"application/vnd.rpk.calc.control+json"`）並附帶 `action: "clear_console"`。

```json
{
  "protocol": 1,
  "build": "<16-hex-build-id>",
  "op": "snapshot",
  "request_id": 12,
  "view_id": 4,
  "selected_var": "A",
  "selected_coord": [3, 2],
  "open_matrices": {"A": [3, 2]}
}
```

```json
{
  "protocol": 1,
  "build": "<same-build-id>",
  "view_id": 4,
  "revision": 8,
  "workspace": [{"name": "A", "shape_str": "1000000x4"}],
  "selected_var": "A",
  "selected_coord": [3, 2],
  "inspector": {"kind": "plain", "value": {"...": "formatted snapshot fields"}},
  "matrices": [{"name": "A", "shape": [1000000, 4]}]
}
```

`validate_envelope()` 檢查 `type(payload.get("protocol")) is int` 與 `protocol == 1`，並核對由 `protocol.py`、`kernel_runtime.py` 及 11 個 `core/*.py` 檔案計算出的 `build`；任一不符皆會拒收並要求重啟 kernel。

---

## 偏好轉移與啟動檔擁有權

`SettingsDialog` 提供四個頁面：外觀、FXP 格式、自動載入與偏好管理。`core/settings_transfer.py` 匯出與驗證可攜式 JSON 封包：

```json
{"kind":"rpk-calc-preferences","version":1,"preferences":{"language":"zh_TW","theme":"light"}}
```

執行期 `startup_file`、視窗幾何、分隔線／收合狀態、工作區變數與命令歷史均不匯出。匯入時須明確勾選 **套用後取代 startup.py（不執行程式碼）**（具備 SHA-256 修訂版本檢查與 `startup.before-import-HASH.py` 備份）與 **匯入自訂 import（套用後會載入）** 才會套用對應內容。

`StartupFile`（`core/startup.py`）管理位於目前設定 JSON 檔同目錄下的 `startup.py`（GUI 與 `rpk-calc --eval` 皆同，依 `--config` 或預設 `~/.rpkbin/calc_settings.json` 決定路徑）。舊版 JSON `startup_script` 僅在初始化遷移時寫入一次，若同目錄已存在不同內容的 `startup.py` 則保留現檔並將舊碼備份為 `startup.legacy-HASH.py`。載入順序為內建函式群組、自訂匯入（由 `parse_imports()` 驗證僅允許明確絕對匯入與註解，禁止 `_` 開頭綁定）、最後執行 `startup.py`。每條自訂匯入語句各自獨立嘗試，單一失敗不影響其餘有效匯入。

---

## 延伸閱讀

- [rpk-calc 快速入門與參考](README.md)
- [文件導覽](../README.md)
- [rpk-calc Architecture and Data Boundaries (English)](../../en/calc/architecture.md)
