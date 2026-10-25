# rpk-calc 架構與資料邊界

GUI 將互動 Python 執行與 Qt 顯示分在不同程序。IPython kernel 是數值物件的唯一擁有者；Qt 主程序負責命令視窗、Workspace、Inspector、矩陣表格與使用者操作。既有 `core` 層提供 `WorkspaceSession`、FXP/一般數值檢視與編輯，不直接把完整 Python namespace 複製到 GUI。


原生數值的 **Convert to FXP…** 對話框產生轉換請求，由 kernel 消費；請求沿用既有的 `expected_revision` 防護。FXP 格式列與 **Convert to float…** 也沿用既有 kernel 轉換流程。此 UI 不新增 config 欄位或 schema，Inspector 仍遵循已儲存的 list/grid 版面偏好。

## 責任與不變條件

- `kernel_client.py` 啟動並管理一個由應用程式擁有的 IPython 子程序，透過 `127.0.0.1` 的 Jupyter 通道送出請求；Qt 事件迴圈非阻塞地接收回覆。Kernel 不自動重啟。應用程式關閉時停止這個程序。
- `kernel_runtime.py` 在 kernel 內執行使用者程式，維護 `WorkspaceSession`，並執行檢視、分頁、編輯等請求。GUI 只收到格式化後的 snapshot，不收到 Python 物件或完整陣列。
- `controller.py` 序列化 GUI 請求、追蹤 `view_id` 與 workspace `revision`，並協調忙碌狀態。執行期間 GUI 保持回應，但數值編輯會停用。Workspace scalar 的 Value/Format 操作會導向 Inspector 的 Value 卡或共用轉換控制項；array Value 仍開啟矩陣編輯器。Help 是三個精簡查表分頁，預設顯示 FXP。
- `CalcMainWindow` 使用 Qt 內建 Fusion style，由 style 正常繪製 checkbox indicator，並可搭配既有應用程式 QSS；Windows 11 上的勾選狀態仍可見。
- `SettingsDialog` 收集偏好並顯示唯讀 startup 預覽；`CalcController` 分辨 Qt 外觀、kernel 數值預設、內建函數群組、自訂 import 與啟動檔操作。內建群組內容固定，checkbox 只控制啟用或停用。外觀及 `keep_english_terms` 只更新 Qt；數值預設更新既有 session。套用變更後的群組或自訂 import，會在既有 kernel 重新載入設定的匯入項目，不執行 `startup.py` 或清空 namespace；使用者變數和快取 startup binding 保留既有綁定。啟動程式在 kernel 啟動/重啟或明確選擇 Reload and run 時執行。Apply 不重啟 kernel，因此保留 console 輸入/歷史與矩陣分頁。
- Settings 對話框將 Cancel/Apply footer 放在可伸展的分頁區下方；設定頁捲動或內容延伸時，footer 仍維持在底部。
- Workspace 陣列按鈕與雙擊操作都開啟既有矩陣分頁；Inspector 收合只隱藏內容，標題列上的箭頭/標題仍可展開。開啟陣列時會展開 Inspector。關閉時保存視窗 geometry、splitter 尺寸與 Inspector 收合偏好。
- `protocol.py` 與 `MatrixTableModel` 定義 JSON DTO 和有界矩陣分頁。一般矩陣每頁最多 32 × 16 個值，表格最多保留 8 頁快取；矩陣分頁的 shape/slice metadata 查詢不讀取整張 array。Workspace 的 Min/Max 欄位在顯示時才掃描數值；FXP 陣列的飽和摘要最多檢查 4,096 格，超過上限不掃描整張陣列並回報未知。複製整張矩陣上限為 100,000 cells，超過時請由 Python 匯出。

## FXP 轉換來源與狀態

APyTypes constructors 建立的原生值沒有 calculator conversion-reference metadata，因此會顯示 `No conversion reference (error unknown)`，即使 bits 精確表示輸入值也一樣。Python `float` 是 binary64，例如 `0.1` 已是近似十進位值。GUI Real 數值編輯或 Real pin 重新格式化會建立新的比較來源；raw-bit 編輯與 Hex pin 重解讀不會。Inspector 的精確轉換狀態只比較有追蹤的 GUI 操作結果與該次實際輸入，不代表原始十進位意圖或此前算術沒有誤差。

原生 constructor 起始沒有 GUI conversion-reference 資料；APyFixed scalar 會顯示 `No conversion reference (error unknown)`，直到追蹤到 GUI Real 編輯或 Real pin 重新格式化。原生 APyFixedArray 的 GUI 編輯會由 session sidecar 依原 owner 稀疏記錄 provenance，不替換陣列，也不破壞 aliases。讀取時檢查 owner、shape、FXP spec 與目前 raw bits；不相符時診斷為 unknown。這是有界診斷 metadata，不是追蹤任意 Python 編輯的歷史；Python 寫入相同 bits 後無法分辨是來源未變或重新寫回。

APyFixedArray constructor 不會建立 GUI provenance。GUI 編輯將變更格子的來源稀疏記在 session sidecar，避免複製整個 array。sidecar 最多保留 32 個 owner、100,000 筆 cell records；LRU 淘汰只移除診斷資料，不改數值。Reset 清除 sidecar；refresh 會移除 namespace 已不存在的 owner。Duplicate 會複製有效紀錄，Undo/Redo 會還原來源 metadata。Settings 的 `default_quant` / `default_overflow`（預設 RND/SAT）套用於 GUI 實數值編輯、native-to-FXP 轉換與 Real pin 的 FXP 格式變更；不會覆蓋 APyTypes constructor 行為，也不是一般 Python/NumPy/APyTypes 算術的全域設定。原生 APyTypes constructors 使用自己的量化和溢位預設（通常 nearest ties away 與 WRAP）。`protocol.py` 的 build id 涵蓋 FXP/session/protocol 核心來源；不相容的 GUI/kernel build 會拒絕請求並要求重啟。

`RpkFixed` 與 `RpkFixedArray` 保留 GUI metadata 與 `copy()` 行為。算術運算子直接繼承 APyTypes，不由計算機額外擴寬、轉換操作數或包裝結果。結果 bits、格式與例外依安裝的 APyTypes 版本決定；Python 混合操作數在原生轉換時可能溢位。結果型別依原生分派決定（包含適用時的 NumPy 分派），計算機不額外包裝或替新結果建立 GUI 轉換來源；明確的 GUI 編輯仍採所選 Pin／量化／溢位策略。

## Workspace 陣列飽和標記

Kernel 產生 `WorkspaceRowSnapshot.is_saturated: bool | null`，Qt 消費此 DTO。`true` 表示至少一格目前 bits 仍對應已追蹤的 GUI 飽和編輯；`false` 表示每格都有符合目前 bits 的來源且均未飽和；`null` 表示來源不完整、bits 與來源不符，或陣列超過 4,096 格。超過上限的整體摘要保守維持 unknown 且不掃描全陣列；所選格仍可顯示 `SATURATED`。小型陣列以 sparse GUI `cell_sources` 確認來源。單格 Inspector 不受 array size 限制，仍依所選 cell 的來源精確判斷。

Qt 只在 FXP array 的 Workspace Value 欄加 `[SAT]` 或 `[SAT?]` 前綴；`true` 的 `[SAT]` 用紅色，`null` 的 `[SAT?]` 為中性色並帶有解釋來源未完整驗證的 tooltip；`false` 不加標記。`[SAT?]` 代表未知，不代表已飽和或安全。標記是顯示層文字，不改 `value_str` 或複製的數值。4,097 格的 `APyFixedArray` 超過掃描上限，因此 Workspace 顯示 `[SAT?]`。若使用者在 8-bit、4 個 fractional bits 的 Inspector Real 欄輸入 `99`，該格會飽和為 7.9375，Inspector 顯示 `SATURATED`；超過 4,096 格時整體摘要保守維持 unknown。

DTO 例：`{"is_saturated":null}`。此欄位由 kernel 計算並經 JSON 傳給 Qt；build id 不一致時沿用既有拒絕請求與要求重啟的檢查。單格 array 編輯只更新該格的 saturation metadata，不再對整個 saturation mask 執行 `np.any` 全陣列掃描。

## Native 值與 FXP 邊界

Python `int` 保有任意精度；在 console 執行 `a = 2` 會依 Python 語意將 `a` 重新綁定為 int。Inspector 以 `PlainValueSnapshot` 表示一般數值；進位與 bits 按原型別解讀，不套用隱含 FXP word length 或 pin；Workspace 以原生型別/dtype 顯示，不假定 native 是 FXP 或帶有 LSB。Inspector 轉換控制項可將原生實數 scalar/array 轉成 FXP，也可將 FXP 轉成 Python `float` 或 NumPy `float64`；陣列轉換保留 shape。APyFloat 與不支援型別的轉換或格式操作使用 console。Native ndarray 的矩陣 Format 欄仍顯示 dtype。FXP 格式變更作用於整個變數/陣列，並依 Inspector Real/Hex pin 決定保值方式；矩陣編輯器既有的 Format/Preview 控制也使用同一轉換流程。Native↔FXP 與 FXP→float 是數值轉換，不受 pin 影響。

Inspector 的 `native_bits` 由 kernel 內的 `representations.py` 產生，Qt 依 descriptor 顯示原生表示。NumPy 固定寬度整數 scalar：Inspector 可帶有唯讀 `native_bits` 摘要（dtype、位寬、signedness、Hex 與 binary），不提供 FXP bit 編輯。Python `int` 的 HEX/BIN 使用負號與 magnitude，不指定儲存字長。Python float 與 NumPy float16/32/64 依儲存 bits 顯示浮點欄位與特殊值分類，維持唯讀；longdouble、complex 與 APyFloatArray 不猜測格式。

Kernel 預載 `apy`、`APyFixed`、`APyFixedArray`、`APyFloat` 與 `APyFloatArray`，它們是原生 APyTypes module/class objects，不是 wrapper。直接使用 constructor：`APyFixed.from_float(0.25, bits=16, frac_bits=12)` 轉換數值；`APyFixed(0x400, bits=16, frac_bits=12)` 將第一個參數當 raw bits；`APyFixedArray.from_float([0.25, -0.125], bits=16, frac_bits=12)` 建立陣列；`APyFloat.from_float(0.25, exp_bits=8, man_bits=23)` 建立 APyFloat。`APyFixed(7, 4, 2)` 的 raw bits 是 7、`int_bits=4`、`frac_bits=2`，值為 1.75；若要編碼數值 7，使用 `APyFixed.from_float(7, int_bits=4, frac_bits=2)`。APyTypes constructors 使用自己的預設（通常 nearest ties away 與 WRAP），不套用 GUI RND/SAT，也不建立 conversion-reference metadata。單一 APyFloat 支援數值預覽、浮點 bits 檢視與 bit 切換；格式與 bias 固定，數值/格式欄唯讀。

GUI 格式輸入由 `FormatEngine.parse_input` 依標準語法與目前啟用的自訂模板解析。只有能唯一還原 signedness、WL、FWL 的簡單 placeholder 才支援；重複欄位必須一致，相鄰數字欄位與進階格式規格不做逆解析。包含與標準語法衝突的歧義一律拒絕。`CalcController` 將解析後的 `FxpSpec` 正規化為 canonical Q 字串，再產生既有 JSON 請求，kernel 仍消費相同的標準格式契約；不新增 config 欄位或 transport schema，也不改 APyTypes 原生建構方式。本工具的 `Qm.n` 中 `m` 包含 sign bit，總寬度為 `m+n`；`UQm.n` 為 unsigned。其他工具可能使用不同慣例，因此 constructor 位元數是最清楚的硬體宣告。

若需要重複使用簡寫，可在 **設定 → 自動載入** 定義，例如 `def fxp(value, bits=16, frac_bits=12): return APyFixed.from_float(value, bits=bits, frac_bits=frac_bits)`。這是使用者自行定義的 startup script，不是預載的 calculator constructor。GUI 轉換由 Inspector 的轉換控制項執行；Workspace 只負責導向該區，不直接編輯數值。

矩陣標籤依型別顯示 **Array format:**（FXP）或 **Dtype:**（native）；Inspector 對 native 顯示 **Type:**，FXP 格式列明示 **Whole variable**。

Kernel DTO 回報整個變數可用的轉換能力，不依目前選取的陣列格判斷。Inspector Value 編輯只作用於選取元素；轉換操作則作用於整個變數或陣列。轉換請求會在 envelope 帶上 `expected_revision`，kernel 在修改前檢查版本。Preview DTO 回傳 pin、quantization、overflow 與 revision；若預覽後策略已變更，UI 會拒絕 Apply。可寫入的 NumPy 陣列編輯會保留 dtype；整數值有小數或超出範圍時拒絕。Native-to-FXP 轉換與 Real pin 的 FXP 格式變更採用 GUI 量化/溢位設定；這些設定不會改變 APyTypes constructors 或 Python console 算術。FXP Inspector 的 Real 數值欄使用受限 AST evaluator：接受數值字面值、`pi`/`e`、算術與整數 bitwise 運算，拒絕函式呼叫、屬性存取、容器和一般 Python 語句。輸入上限為 4,096 字元與 512 AST 節點，整數結果上限 16,384 bits；指數絕對值及移位量最多 4,096，且結果須為有限實數。一般 Python REPL 不受此欄位解析器限制；此限制也不是 kernel sandbox。Native bit operations 由 Python/NumPy REPL 執行；GUI 的 FXP raw-bit 操作不套用到原生值。

Inspector 的一般數值 HEX/BIN 欄位唯讀。Python int 超過 4,096 個 magnitude bits 時回傳有標記的有界預覽，並停用截短內容的 Copy；原值不變。Workspace 名稱/type 篩選只改變列表呈現，若目前 Inspector 目標被篩掉，目標和表格捲動位置仍保留。

## 數值表示 DTO 與 APyFloat 編輯

`PlainValueSnapshot.native_bits` 是內部 JSON DTO，producer 是 kernel 的 session，consumer
是 Qt Inspector。例如單一 APyFloat 的表示（其餘 snapshot 欄位省略）：

```json
{"kind":"float","dtype":"APyFloat","width":8,"hex":"0x1E","binary":"00011110","exp_bits":5,"man_bits":2,"bias":7,"classification":"Normal","editable":true}
```

descriptor 的 `editable` 只控制 bit 按鈕，與數值輸入欄的 `PlainValueSnapshot.editable`
分開。只有單一 APyFloat 開放 bit 編輯；一般浮點數與選取的原生陣列元素保持唯讀 bits。
Qt 不接收 APyFloat 物件或以 Python float 重建；kernel 以 `to_bits`／`from_bits` 保留
格式、bias 與 NaN payload。journal 的指紋使用 raw bits 與完整格式，包含正負零，
不用浮點數相等判定 Undo/Redo。REPL 仍依既有規則清除 GUI 編輯歷史。

build id 包含 `representations.py`；GUI/kernel 來源不一致時，既有 envelope 驗證拒絕
回覆並要求重啟。這是內部傳輸 metadata，使用者不需要製作設定或 sidecar。

## GUI 編輯、矩陣操作與命令歷史

`EditJournal` 由 `CalcKernelRuntime` 擁有，只追蹤 GUI 對 scalar、單格及矩形貼上的數值/位元編輯；最多 100 筆或 8 MiB。它不複製大型陣列，而記錄編輯目標、原 owner、位置、值指紋和結構簽章；Undo/Redo 前會驗證變數綁定、slice/cell、shape、dtype、strides、FXP 格式及當前值。若執行期間有 REPL cell、array 結構操作、整體 array reformat 或 slice 改變，journal 清空；檢查不符也會清空並拒絕 replay。這不是任意 Python 程式的通用復原機制，console 的 Ctrl+Z 仍是文字編輯。

Paste TSV 先解析並驗證完整矩形、目標邊界、dtype/範圍與可編輯性，之後才寫入；上限為 100,000 cells 與 8 MiB。驗證失敗不會部分套用，非預期寫入錯誤會回復已修改格。單格輸入失敗則保留被拒的字串、顯示錯誤原因，讓使用者修正後重試。

Matrix snapshot 帶有 `full_shape` 和 `slice_indices`；GUI 固定顯示前兩軸，後續軸預設索引 0，並可由切片欄調整；列/欄跳轉採零起始索引。選取矩形統計最多 100,000 cells，只摘要有限值；資料先轉成 float64/complex128，FXP 與大型整數結果是近似值。結果包含 count、finite_count、sum、mean、min_abs、max_abs；實數資料也提供 min/max。整個陣列的轉換及 FXP 格式 Preview/Apply 都由 Inspector 的轉換控制項處理；矩陣編輯器保留給逐格檢視與編輯。

History 由 Qt `CommandWindowWidget` 在前端保存使用者提交的 source，不是 kernel 的 IPython history。每次新 kernel client 建立時清除；最多 1,000 筆/1 MiB，過大的單筆命令整筆省略。搜尋與預覽不執行程式；Use as input 只還原至輸入區，匯出需使用者明確選擇 UTF-8 `.py` 檔。匯出檔註明其中 IPython magic 可能需要 IPython 才能執行。

## 顯示與 DTO 範圍

`native_bits` 是 Inspector snapshot 中的唯讀 NumPy 整數檢視資料，不建立 FXP 格式。Undo state、矩陣 `full_shape`/`slice_indices`、格式 Preview 與選區 Statistics 都由 kernel 計算後經 JSON DTO 傳給 Qt；例如 Undo 狀態為 `{"can_undo":true,"can_redo":false,"undo_count":1,"redo_count":0}`。Workspace `numeric_display`/`numeric_precision` 只在 Qt 的 Workspace Value 與 Matrix `DisplayRole` 對有限實數顯示字串套用 Full/automatic、Scientific 或 Fixed，小數位數為 1–17；不改 Inspector、Matrix `EditRole`、複製內容或 raw bits。Fixed 預估超過 256 字元時回退為 scientific。View 的 Reset layout 只還原視窗尺寸、splitter 與展開 Inspector，不重建 kernel、不更改外觀偏好。DTO 不包含可由 Qt 獨立修改的數值物件或 namespace。`protocol.py` build id 涵蓋 `core/editing.py`，GUI/kernel build 不一致時拒絕不相容訊息。

## 延後項目

目前只有一個命令視窗與一個由應用程式擁有的 kernel。增加多個命令視窗前，需先決定它們共用或各自擁有 kernel/namespace、輸出由哪個視窗接收，以及 Stop/interrupt 如何作用；此功能尚未實作。

矩陣維度超過 2 時，顯示其前兩個維度，後續維度索引預設為 0（例如 `A[:, :, 0]`），也可在切片欄設定其他索引，並在頁面提示目前切片。Inspector 和矩陣編輯仍操作 kernel 中原始值；顯示用 snapshot 不是另一份可獨立修改的工作區。

## Kernel 協定範例

控制操作透過 Jupyter `user_expressions` 要求 `application/json` 結果，使用 JSON-only 資料，不使用 pickle。`clc` 會另外以 Jupyter `display_data` 發送 `application/vnd.rpk.calc.control+json` 控制訊息，內容含 `protocol`、`build` 與 `action: "clear_console"`；這只通知前端清畫面，不承載數值資料。簡化的請求與回覆如下：

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

欄位為示意，實際 DTO 由 `protocol.py` 與 `core` snapshot dataclass 定義。每個請求帶 protocol 版本及由協定和執行核心來源計算的 build id；兩者任一不相符即拒收並要求重新啟動，避免 GUI 與 kernel 使用不同資料結構或語意。`view_id` 用來忽略已過期的 UI 選取結果；`revision` 用來拒絕舊矩陣頁面。

設定寫入 `~/.rpkbin/calc_settings.json`。connection file、IPython history 和 kernel log 放在 kernel runtime 暫存目錄，關閉時清除；互動歷史不跨 session 保存。`rpk-calc --eval` 是另一條路徑，直接建立 headless `WorkspaceSession`，不啟動或連接 IPython kernel。


## 設定、語言與偏好轉移

`SettingsDialog` 管理四個設定頁：外觀、FXP 格式、自動載入、偏好管理。`CalcConfig` 是記憶體與設定檔使用的設定模型；`core/settings_transfer.py` 定義可攜出的欄位，並在匯入前驗證，再合併到設定草稿。設定管理產生與讀取的 JSON 例如：

```json
{"kind":"rpk-calc-preferences","version":1,"preferences":{"language":"zh_TW","theme":"light"}}
```

必須符合指定 kind 與版本；未知欄位、不支援的版本、無效型別或值都會遭拒。執行期 `startup_file`、視窗幾何、分隔器/收合狀態、工作區變數與命令歷史不會匯出；可攜用的 `startup_script` 與 `custom_imports` 以原始碼保存在 envelope。匯入先預覽變更；啟動原始碼預設不匯入，除非使用者勾選 **Replace startup.py after Apply (does not run code)**；自訂 import 也預設不匯入，須另外勾選 **Include custom imports (runs after Apply)**。Apply 寫入同意匯入的 startup 原始碼但不執行，並在目前 kernel 載入同意匯入的自訂 import。startup revision 不符即拒絕寫入，取代前備份為 `startup.before-import-HASH.py`。還原預設會保留 startup 檔。Cancel 會捨棄設定草稿，但不會撤銷外部編輯器已儲存的變更。

語言切換與 `keep_english_terms` 都是 Qt 顯示設定。`keep_english_terms` 預設 `false`，不傳入 kernel 設定；繁體中文介面可用它保留 Command Window、Workspace、Name、Format 等指定應用程式標籤為英文，說明文字仍採所選語言。送出的 Python、變數名稱、數值及使用者啟動程式碼不變。既有 widget tree 就地重新翻譯，不重建 kernel，也不清除 namespace、歷史、待送出的命令輸入或選取狀態。翻譯範圍限於應用程式介面字串。

## 啟動檔的擁有權與執行

GUI 的 `StartupFile` 會在目前設定檔所在資料夾建立 `startup.py`；CLI headless `--eval` 使用預設設定資料夾。初始化後，GUI 以 `startup.py` 為單一來源。`CalcConfig.startup_file` 只在執行期攜帶解析後路徑，不寫入設定檔。舊版 JSON 的 `startup_script` 僅供遷移讀取，並保留為偏好匯出 envelope 中的可攜原始碼。首次建立的 `startup.py` 只有註解範例，包括 SciPy imports、明確覆寫 FFT helper、`db20` 與 `fxp`。

遷移時若尚無 `startup.py`，舊 JSON 程式碼會寫入新檔；若同資料夾已有不同的 `startup.py`，程式保留現有檔案，將舊程式另存 `startup.legacy-HASH.py` 並顯示通知。Settings 的 Auto-import 頁只讀取並預覽檔案，不提供內嵌編輯器。**Open text editor** 在 Windows 開啟 Notepad、macOS 開啟 TextEdit；Linux 會要求指定文字編輯器執行檔。**Refresh preview** 重新讀取外部儲存內容；Cancel 不會回復外部儲存。Kernel 讀取檔案後以實際路徑編譯，並在目前 session namespace 執行；錯誤 traceback 包含檔案路徑與行號。啟動檔定義的函數可讀取之後在命令視窗設定的變數。

Apply 儲存偏好，並立即在目前 kernel 載入變更的內建函數群組/自訂 import；使用者變數及快取 startup binding 保留，且不執行 `startup.py`。啟動檔只在 kernel 啟動/重啟或使用者選 **Reload and run** 時執行。自訂 import 僅接受明確的絕對 Python import 語句與註解；萬用字元、相對 import、其他程式碼，以及以 `_` 開頭的匯入名稱或別名都會遭拒。套件須已安裝，Calc 不會安裝套件。載入順序為內建函數群組 → 自訂 import → `startup.py`。各 import 語句分別嘗試；失敗會顯示於 Auto-import 狀態與應用程式錯誤，其餘成功項目仍會載入。失敗語句不發布部分 binding，但 Python module 副作用無法回滾。Startup reload 就是一般 Python 執行：不會回滾先前副作用、不保證移除舊腳本產生的名稱，也不會自動重新載入已匯入的 module。只有仍由 calculator 擁有的舊 binding 才會更新；使用者重新綁定的名稱會保留，腳本明確賦值則依 Python 規則覆寫。`clear` 會恢復快取 helper，但不重跑腳本。匯入的啟動原始碼須明確同意並按 Apply 才會取代檔案；revision 檢查會拒絕與外部編輯衝突的覆寫，並先建立 `startup.before-import-HASH.py` 備份。
