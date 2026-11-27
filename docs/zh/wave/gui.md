# 網頁圖形介面指南

說明 Wave 本機網頁介面的版面切換、佇列操作、Flow 與記錄檢視工具、封存管理對話框，以及波形檢視器的鍵盤與滑鼠快捷鍵。

## 版面切換與頂端工具列

頂端工具列可切換四種工作區版面與全域執行控制：

- **版面模式**：`Jobs`（專注佇列清單）、`Split`（左側佇列搭配右側檢視面板）、`Inspect`（全寬 Flow 與原始記錄檢視）、`Charts`（全寬波形、XY、散佈與直方圖檢視器）。
- **Open 對話框**：從目錄瀏覽器開啟 `.py` 工作檔、記錄檔／目錄或 `.sqlite3` 封存資料庫。在 **Archive (.sqlite3) -> Recent Archives** 中點擊已關閉非作用中封存旁的垃圾桶圖示，列項目會原位切換為**兩段式永久刪除確認**（`Delete <name> permanently?` 搭配 **Confirm Delete** 與 **Cancel**）。
- **預覽與執行控制**：處於預覽模式時可先整理佇列再按下 **Start**（或 **Resume**）啟動程序；執行期間可使用 **Pause / Resume** 暫停派發新嘗試，或用 **Stop All** 中止所有執行中的工作。
- **Settings 視窗**：檢視並調整執行中的 `max_workers`、`max_history`、`scheduling_mode`，以及目前生效的 `Archive retention` 保留策略摘要。

## 佇列與工作面板

- **篩選與搜尋**：依工作狀態（`RUNNING`、`PENDING`、`HELD`、`PASS`、`FAIL` 等）篩選，或以工作名稱與指令字串搜尋。
- **暫緩與解除（`HELD`）**：將待執行工作切換為 `Held`，在不更動優先順序的情況下暫時排除於排程派發之外。
- **拖曳重新排序**：直接在工作列拖曳調整順序，立即更新佇列優先級（`reorder_queue`）。
- **軟移除與還原**：可將未啟動或已結束的工作移除（**Remove**），之後仍可從 **Removed Jobs** 清單一鍵還原（**Restore**），原本綁定的 `Flow` 也會保留。
- **互動輸入與 POSIX 訊號**：向執行中的程序傳送文字時可切換 **newline** 開關（決定是否附加 `\n`）；在 POSIX 主機上亦可透過 **Signal** 選單發送 `SIGINT`、`SIGTERM`、`SIGTSTP`、`SIGCONT`、`SIGUSR1`、`SIGUSR2`、`SIGHUP`、`SIGQUIT`。

## 檢視（Inspect）面板

### Flow 分頁

可在 **Parsed Records**、**Analysis Reports** 與 **Automation** 三個子分頁之間切換：

- **摘要模式切換（`Latest: Record / Merged`）**：在 **Record**（僅顯示最新一筆發布的完整欄位）與 **Merged**（跨近期發布合併顯示每個欄位的最新有效值）之間切換。
- **欄位篩選與證據跳轉**：支援欄位名稱關鍵字與數值上下限篩選；點擊證據連結可直接在 **Original log context** 抽屜開啟並反白對應的原始記錄行。

### Log 分頁

- **範圍限定的 `Ctrl+F`**：僅當版面位於 `Split` 或 `Inspect`、檢視分頁為 `Log`，且鍵盤焦點或目標元素落在記錄檢視區域或搜尋列內時，`Ctrl+F` 才會開啟 Wave 內建搜尋列；在介面其他區域會維持瀏覽器原生的整頁搜尋。按下 `/` 則會依目前焦點位置快速聚焦至 Log 搜尋框或工作搜尋框。
- **搜尋列功能**：支援區分大小寫（`Aa`）、全字符合（`ab`）、正規表示式（`.*`），以及 **Filter matching lines**（僅顯示符合搜尋條件的行）。按 `Enter` / `Shift+Enter` 跳至下一筆或上一筆符合結果。
- **記錄選項選單**：
  - **經過時間區間（Elapsed）**：輸入起始與結束秒數並按 **Apply**，套用後上方會顯示對應的篩選標籤。
  - **顯示設定**：調整 **Log font** 字體大小（`9` 至 `20` px，可一鍵 **Reset font to 13 px**）、切換 **Wrap long lines**（自動換行）與 **ANSI colors**，以及跳轉至開頭（`Ctrl+Home`）或追蹤最新輸出（`Ctrl+End`）。
- **原始記錄脈絡抽屜（Original log context）**：點選原始行號跳轉、在篩選後的記錄列點兩下、點擊分析證據連結，或在圖表點選 `Reveal in Raw Log` 時開啟。支援並排或上下擺放切換、拖曳或使用方向鍵／`Home` 調整分隔比例，按 `Escape` 關閉。
- **長行分段全文檢視（`Preview · read full`）**：檢視封存資料庫時，超過 1024 字元的超長行會顯示 **Preview · read full** 按鈕，點擊後會開啟 `Raw Log` 視窗，以每次 **65,536 位元組** 的區塊載入全文（提供 **Start**、**Next chunk** 與 **Original Log context**）。

## 圖表與波形檢視器（Charts）

### 訊號選擇器與群組階層

- **Get Signals（`g`）**：以子字串、萬用字元或正規表示式搜尋訊號，選擇加入、取代目前清單或群組化（`Shift+G` 建立 `= G...` 群組）。
- **Defaults**：點擊 **Defaults** 會將顯示訊號重設為檢視定義的預設訊號（若未設定預設清單且為 `long` 模式，則取前 **8** 個訊號；直方圖最多 **1** 個），並清除單列覆寫設定。
- **拖曳進群組**：將訊號列拖曳至群組列的中段高度區域（約 `28% 至 72%`），放開後會移入該群組並自動展開；拖放至上緣或下緣則改為上下排序。
- **位元展開與切片**：寬度 `<= 64` 位元的匯流排支援 **Expand Bits `[msb:0]`** 展開各位元列；**Bit Select `[msb:lsb]`…** 每條訊號最多顯示 **16** 個切片列。
- **波形檔案瀏覽對話框**：開啟 `.rc`、`.vcd` 或 `.json` 檔案時提供 **Back**、**Forward**、**Parent folder**、**Refresh**、常用資料夾捷徑，以及開啟本機檔案選擇視窗的 **Browse Local…** 按鈕；亦可將目前波形匯出為 VCD 檔案（`export_vcd`）。

### 波形鍵盤與滑鼠快捷鍵

| 操作 | 功能說明 |
| --- | --- |
| `Ctrl+Wheel` | 以滑鼠指標 X 軸為中心縮放波形（於非波形圖表上，`Shift+Wheel` 亦可縮放）。 |
| `Shift+Wheel`（波形圖表） | 水平捲動時間軸。 |
| `Z`、`+`、`=` / `z`、`-` | 放大 / 縮小時間軸視窗。 |
| `f` 或 `Home` | 縮放至顯示完整 100% 資料範圍。 |
| `l` / `Shift+L` | 回到上一個縮放／捲動視角 / 向封存重新載入最新圖表資料。 |
| `Shift+Left` / `Shift+Right` | 向左或向右平移 25% 時間視窗。 |
| `Left` / `Right` 方向鍵 | 將游標 A 移動到**目前選取訊號列**的上一個／下一個轉態邊緣，不受 `By:` 條件選單影響。 |
| `n` / `N` | 依目前 **`By:`** 條件（`Any Edge`、`Rising`、`Falling`、`= Bus Value`）向前／向後搜尋並將游標 A 跳至符合處。 |
| `Up` / `Down` 方向鍵 | 在訊號清單中上下切換選取的訊號列。 |
| `c` | 依序切換選取訊號或群組的顏色（共 7 種調色盤配色循環）。 |
| `h` | 切換顯示完整階層路徑或僅顯示葉節點名稱。 |
| `Delete` / `Backspace` | 移除選取的訊號、位元切片或群組，或收合已展開的位元列。 |
| `Escape` | 依序關閉最上層彈出視窗、右鍵選單或 Get Signals；在波形畫布上則清除游標 B。 |

## 另請參閱

- [Wave 總覽](README.md)
- [Flow 分析管線](flow.md)
- [歷史與封存機制](history.md)
- [疑難排解](troubleshooting.md)
