# `rpkbin.app.isasim_gui` 視覺化瀏覽器除錯器

[English](../../../en/app/isasim_gui/README.md) · [isasim 快速入門](../../isasim/README.md) · [文件導覽](../../README.md)

`rpkbin.app.isasim_gui` 是在 [`rpkbin.isasim`](../../isasim/README.md) 之上提供僅限本機 loopback 的瀏覽器視覺化除錯器，支援單步前進與回溯、條件斷點、原位修改暫存器與記憶體，以及 `DebugSession` Python API。

## 安裝與啟動

此 GUI 僅依賴 Python 標準函式庫與 `rpkbin.isasim`。請在專案根目錄以 editable 模式安裝：

```bash
python -m pip install -e .
```

使用 `rpk-isasim-gui` 或 `python -m rpkbin.app.isasim_gui` 啟動伺服器（若需在載入程式後執行 `setup(sim)`，可加上 `--script`）：

```bash
rpk-isasim-gui path/to/mcu.py path/to/program.asm --port 8000
rpk-isasim-gui path/to/mcu.py path/to/program.asm --script path/to/setup_stim.py --port 8000 --open
```

### 執行隨附範例

在專案根目錄啟動 [demo_loop.asm](../../../../playground/isasim_demo/demo_loop.asm) 除錯器：

```bash
python -m rpkbin.app.isasim_gui playground/isasim_demo/demo_mcu.py playground/isasim_demo/demo_loop.asm
```

由於 `demo_mcu.py` 已宣告 `kind="call"`、`kind="return"` 與 `role="flag"`，啟動時不需傳入 `--call-mnemonics` 或 `--return-mnemonics`。開啟 `http://127.0.0.1:8000` 並按下 **Run** 後，程式會停在 `HLT`（`r0` 與 `data[0x11]` 均為 `30` / `0x1e`）。可在 `CALL accumulate_step` 設定斷點並按 **Step Over (F10)** 跳過整個副程式。

## 語法

```bash
rpk-isasim-gui [-h] [-V] [--script SCRIPT] [--host {127.0.0.1,localhost}] [-p PORT] [--open] [--no-browser] [--max-history MAX_HISTORY] [--call-mnemonics CALL_MNEMONICS] [--return-mnemonics RETURN_MNEMONICS] mcu program
```

```python
from rpkbin.app.isasim_gui import DebugSession, create_server, relocate_breakpoints

session = DebugSession(mcu_path, asm_path, script_path=None, max_history=200, *, call_mnemonics=(), return_mnemonics=())
server = create_server(session, *, host="127.0.0.1", port=8000)
relocated = relocate_breakpoints(old_lines, new_lines, breakpoints)
```

## 說明

- `rpk-isasim-gui` 為 MCU `.py` 檔案與 `.asm` / `.isa.json` 程式建立 `DebugSession`，透過 `create_server()` 啟動本機 loopback HTTP 伺服器，並可搭配 `--open` 自動開啟預設瀏覽器。
- `DebugSession` 以環狀快照佇列包裝 `Simulator`（支援 `Step Back`），並提供來源行斷點管理、副程式深度單步（`Step Over` / `Step Out`）、暫存器與記憶體差異追蹤，以及記憶體篩選狀態。
- `create_server()` 回傳嚴格綁定 `127.0.0.1` 的 `DebuggerHTTPServer`（`ThreadingHTTPServer` 子類別），並驗證每個請求的 `X-Isasim-Token` 與同源標頭。
- `relocate_breakpoints()` 在重新載入原始碼後，利用前後共 5 行的空白修剪上下文視窗重新定位 1-based 行號斷點。

## 介面與核心功能

### 1. 頂部工具列與執行控制

| 按鈕 | 快捷鍵 | 行為說明 |
| :--- | :---: | :--- |
| **Run** | `F5` | 持續執行直到遇到斷點、`halt`、觀察點、錯誤或步數上限。 |
| **Step Over** | `F10` | 若目前 `PC` 為呼叫指令（`kind="call"` 或符合 `--call-mnemonics`），執行完整副程式並停在下一行（中途命中斷點或觀察點會提前停止）；否則等同 Step In。 |
| **Step In** | `F11` | 執行單一步驟。 |
| **Step Out** | `Shift+F11` | 執行至跳出目前副程式（`kind="return"` 或符合 `--return-mnemonics`）後停止。 |
| **Step Back** | `B` | 從歷史快照佇列還原上一步的 `PC`、暫存器、記憶體與 trace 狀態。 |
| **Reset** | `-` | 重設模擬器狀態（若有 `--script` 會重新執行 `setup(sim)`），並保留目前斷點與觀察點。 |
| **Reload** | `-` | 從磁碟重新載入 `mcu.py` 與 `.asm`，並透過 `relocate_breakpoints()` 重新定位既有斷點。 |
| **Clear BP** | `-` | 清除所有斷點。 |
| **Watch** | `-` | 切換右側面板為 **Watchpoints** 管理視圖（啟用觀察點數量 `> 0` 時顯示角標）。 |

### 2. 狀態列與區間 Cycles / Steps 碼錶

右上角狀態膠囊顯示目前的停止原因與累計的 **Cycles**、**Steps**。點擊右側 **`↺`** 按鈕可將目前計數歸零作為區間基準（若 `Step Back` 退回基準點之前會夾住於 `0`；按下 `Reset` 或 `Reload` 會重設基準偏移）。

所有狀態膠囊標籤與對應的底層 `stop_reason` 值如下：

| 圓點顏色 | 膠囊顯示標籤 | 對應 `stop_reason` 值 |
| :--- | :--- | :--- |
| 綠色 | `READY`、`STEP`、`STEP BACK`、`SET PC`、`POKE` | `ready`、`step_done`、`step_back`、`set_pc`、`poke` |
| 琥珀色（警示） | `BREAKPOINT`、`WATCHPOINT`、`STEP LIMIT`、`CYCLE LIMIT`、`INTERRUPTED` | `breakpoint`、`watchpoint`、`step_limit` / `max_steps`、`max_cycles`、`interrupted` |
| 紅色（停機／錯誤） | `HALTED`、`DEBUG ERROR` | `halted`、`debug_error` |
| 紅色＋錯誤橫幅 | `FAULT`、`UNINIT READ`、`UNDEF OPCODE`、`OUT OF RANGE`、`READONLY WRITE`、`PYTHON ERROR`、`STUCK` | `fault`、`uninitialized_read`、`undefined_opcode`、`out_of_range`、`readonly_write`、`python_error`、`stuck` |

### 3. 左側 ASM Source 面板

- **斷點與條件（`if`）**：點擊行號左側圓點切換斷點，或點擊漏斗圖示輸入條件。支援 [isasim API 參考](../../isasim/reference.md) 所定義的受限整數／布林運算式，可使用簡寫名稱（如 `r1 == 3`、`ram[0x04] > 10`）或明確寫法（`r["reg"].field`）。系統會原樣保存條件文字並在安裝時驗證。
- **直接跳轉 `PC`（`Set PC`）**：目前 `PC` 所在行顯示實心 `▶`；滑鼠移至其他可執行指令行時會顯示 `▷`，雙擊即可直接將 `PC` 移至該行。
- **多 Word 指令碼對齊**：`Code` 欄將多字組機器碼（例如 `8009 2ab7`）向左對齊並依最長指令自動擴展欄寬。

### 4. 右側 Registers、Constants、Aliases、Memory、Mem Aliases、Trace 與 Watchpoints 面板

- **右上方區塊（`Registers` | `Constants` | `Aliases`）**：
  - `Registers`：當 MCU 宣告 `role="flag"` 暫存器時於上方顯示自動換行的 `FLAGS` 列（單擊 1 位元旗標即可切換，雙擊數值欄可輸入十進位、`0x` 或 `0b` 值原位修改）。支援列拖曳排序、逗號分隔篩選、隱藏下拉清單與欄位拖曳調寬。
  - `Constants`：顯示組譯常數（`Name`、`Hex`、`Unsigned`、`Signed`）與逗號分隔篩選。
  - `Aliases`：顯示 MCU 暫存器別名（`mcu.alias`）並支援雙擊原位修改。
- **右下方區塊（`Memory` | `Mem Aliases` | `Trace`）**：
  - `Memory`：支援單一位址（`0x04`）、閉區間（`0x10..0x17` 或 `16-23`）與切片長度（`0x20:4`）篩選。預設勾選 `Hide Uninitialized`；無法讀取的裝置 cell 顯示 `--`。支援雙擊原位修改。
  - `Mem Aliases`：追蹤 `mcu.mem_alias` 與 `setup(sim)` 中 `sim.mem_alias` 宣告的記憶體別名。點擊 **`+ Select...`** 開啟選單並篩選（預設支援 `*`/`?` 萬用字元，另提供 **`Aa`** 區分大小寫、**`ab`** 全字符合與 **`.*`** 正則表達式切換鈕，追蹤清單依程式保存在瀏覽器 `localStorage`），支援雙擊原位修改與單筆取消追蹤。
  - `Trace`：由新到舊列出最近最多 30 筆事件。單擊任一列會捲動左側原始碼面板至對應行，不會改變目前 `PC`。
- **Watchpoints 管理視圖（工具列 `Watch` 按鈕）**：針對暫存器、別名、`mem_alias`、`space[addr]` 或 `space[lo:hi]` 新增 `Write`、`Read` 或 `Read/Write` 觀察點與選填條件。
- **面板分隔線記憶**：垂直與水平分隔線位置自動保存於 `localStorage`（`isasim_right_width`、`isasim_reg_height`）。

### 5. 介面與字體設定（`⚙`）

調整 `Toolbar`（`Full` / `Compact`）、`Source Font`（`10px` 到 `20px`）、`Tables Font`（`10px` 到 `18px`）、`Freeze Cols`（`None` / `PC` / `Code`）、`Reg Hover Tip`（`On` / `Off`），或點擊 `Reset Defaults` 還原預設值（`--right-width: 480px`、`--reg-height: 48%`）。

## CLI 參數參考

### 選項

| 旗標 | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `-h`、`--help` | `-` | `-` | 顯示說明訊息並結束。 |
| `-V`、`--version` | `-` | `-` | 印出 `rpk-isasim-gui 0.3.0` 並結束。 |
| `mcu` | `-` | 必填 | Python MCU 定義檔路徑（`.py`）。 |
| `program` | `-` | 必填 | 組譯原始碼（`.asm`）或組譯 bundle（`.isa.json`）路徑。 |
| `--script SCRIPT` | `-` | `None` | 選配的 Python 設定腳本路徑，須定義 `setup(sim)`。 |
| `--host {127.0.0.1,localhost}` | `-` | `127.0.0.1` | 本機 GUI 綁定位址（`localhost` 實際綁定 `127.0.0.1`）。 |
| `-p PORT`、`--port PORT` | `-` | `8000` | TCP 連接埠（`0..65535`；設為 `0` 時自動指派可用連接埠）。 |
| `--open` | `-` | `False` | 啟動後自動以預設瀏覽器開啟網址。 |
| `--no-browser` | `-` | `True` | 不自動開啟瀏覽器（不可與 `--open` 併用）。 |
| `--max-history MAX_HISTORY` | `-` | `200` | `Step Back` 保留的歷史快照上限（設為 `0` 停用）。 |
| `--call-mnemonics LIST` | `-` | `()` | MCU 未宣告 `kind="call"` 時用於辨識呼叫指令的助記詞清單。 |
| `--return-mnemonics LIST` | `-` | `()` | MCU 未宣告 `kind="return"` 時用於辨識返回指令的助記詞清單。 |

**Mnemonic 分隔符號說明**：`--call-mnemonics` 與 `--return-mnemonics` 會以逗號或空白分隔字詞（`re.split(r"[,\s]+", value.strip())`），因此在命令列使用空白分隔時請加上引號（例如 `--call-mnemonics "CALL, JSR"` 或 `--call-mnemonics CALL,JSR`）。比對不分大小寫，且呼叫與返回清單不可重複。

### 結束碼

- `0`：伺服器正常停止或收到 `KeyboardInterrupt`。
- `2`：CLI 引數錯誤或啟動失敗（`IsaSimError`、`OSError`、`SyntaxError`、`ImportError`、`ValueError`）。

## Python API

`rpkbin.app.isasim_gui` 匯出 `DebugSession`、`create_server` 與 `relocate_breakpoints`（`__all__`）。

### `DebugSession`

#### 建立方式

```python
session = DebugSession(
    mcu_path: str | Path,
    asm_path: str | Path,
    script_path: str | Path | None = None,
    max_history: int = 200,
    *,
    call_mnemonics: Iterable[str] = (),
    return_mnemonics: Iterable[str] = (),
)
```

`repr(session)` 回傳 `DebugSession('<mcu_path>', '<asm_path>', max_history=<n>)`。

#### 屬性

| 屬性 | 型別 | 意義 |
| :--- | :--- | :--- |
| `mcu` | `MCU` | 目前載入程式對應的 `MCU` 模型。 |
| `sim` | `Simulator` | 底層的 `Simulator` 執行個體。 |
| `mcu_path` | `Path` | 解析後的 MCU Python 檔案路徑。 |
| `asm_path` | `Path` | 解析後的 `.asm` 或 `.isa.json` 檔案路徑。 |
| `script_path` | `Path \| None` | 解析後的選配設定腳本路徑。 |
| `max_history` | `int` | 歷史快照環狀緩衝上限（`0` 代表停用 `step_back`）。 |

#### 物件函式

| 方法 | 用途 |
| :--- | :--- |
| `step_in() -> dict[str, Any]` | 執行單一步驟並回傳更新後的狀態字典。 |
| `step_over(max_steps=10000) -> dict[str, Any]` | 跨越目前呼叫指令（依 `kind_of(pc)` 判斷）。 |
| `step_out(max_steps=10000) -> dict[str, Any]` | 執行至跳出目前副程式。 |
| `step_back() -> dict[str, Any]` | 從歷史快照佇列還原上一步狀態（`step_back`）。 |
| `continue_run(max_steps=50000) -> dict[str, Any]` | 透過 `sim.run(max_steps=max_steps)` 持續執行。 |
| `run_to_cursor(file, line, max_steps=50000) -> dict[str, Any]` | 在 `(file, line)` 設定暫時斷點並執行至該處。 |
| `set_pc(file, line) -> dict[str, Any]` | 直接將 `PC` 設為 `(file, line)` 的指令位址。 |
| `poke(target, value) -> dict[str, Any]` | 檢查範圍後修改暫存器、欄位、cell、`irq.line` 或 `mem_alias`。 |
| `toggle_breakpoint(file, line, condition=None) -> dict[str, Any]` | 新增、更新或移除來源行斷點（`condition` 支援簡寫）。 |
| `clear_breakpoints() -> dict[str, Any]` | 清除所有斷點。 |
| `add_watchpoint(target, kind="w", condition=None) -> dict[str, Any]` | 安裝觀察點並回傳更新後的狀態。 |
| `remove_watchpoint(watchpoint_id) -> dict[str, Any]` | 依整數或十進位字串 ID 移除觀察點。 |
| `clear_watchpoints() -> dict[str, Any]` | 清除所有觀察點。 |
| `reset() -> dict[str, Any]` | 重設模擬狀態（有 `--script` 時會重跑 `setup(sim)`）並保留斷點與觀察點。 |
| `reload() -> dict[str, Any]` | 從磁碟重新載入 `mcu_path` 與 `asm_path` 並重定位斷點。 |
| `get_state(memory_space=None, memory_filter=None, hide_uninit=None) -> dict[str, Any]` | 更新保留的記憶體檢視篩選並回傳完整狀態字典。 |
| `view_state(memory_space=None, memory_filter=None, hide_uninit=None) -> dict[str, Any]` | 依暫時性記憶體檢視參數回傳狀態字典，不改動預設檢視。 |
| `set_memory_view(memory_space=None, memory_filter=None, hide_uninit=None) -> None` | 更新記憶體檢視參數，不重建完整狀態字典。 |
| `kind_of(pc) -> str \| None` | 依 MCU 指令種類或指定助記詞回傳 `"call"`、`"return"` 或 `None`。 |

### `create_server`

#### 語法

```python
server = create_server(session: DebugSession, *, host: str = "127.0.0.1", port: int = 8000) -> DebuggerHTTPServer
```

#### 說明

建立提供靜態網頁（`/`）、`GET /api/state` 與 `POST` 操作端點的 `DebuggerHTTPServer`（`ThreadingHTTPServer` 子類別）。僅接受 `127.0.0.1` 來源 IP、合法本機 `Host`、同源 `Origin`，以及在變更請求標頭檢查 `X-Isasim-Token`（`server.token`）。若 `host != "127.0.0.1"` 或 `port` 非 `0..65535` 的整數會引發 `ValueError`；傳入 `port=0` 可配置系統臨時連接埠，並由 `server.server_port` 取得實際埠號。

### `relocate_breakpoints`

#### 語法

```python
relocated = relocate_breakpoints(old_lines: list[str], new_lines: list[str], breakpoints: dict[int, str | None]) -> dict[int, str | None]
```

#### 說明

在原始碼重新載入後重定位 1-based 行號斷點（`{line: condition_or_none}`）。每個斷點會建立去除前後空白的 5 行上下文視窗（`line - 2 .. line + 2`，超出檔案邊界處補上 `"<BOF>"` 與 `"<EOF>"`）。若 `new_lines` 同一行號視窗仍符合則保留原行號，否則只在 `new_lines` 中存在唯一符合視窗時重新定位。

### 範例：`DebugSession`、`relocate_breakpoints` 與 `create_server`

在專案根目錄執行：

```python
from rpkbin.app.isasim_gui import DebugSession, create_server, relocate_breakpoints

session = DebugSession(
    "playground/isasim_demo/demo_mcu.py",
    "playground/isasim_demo/demo_loop.asm",
)
st1 = session.step_in()
st2 = session.continue_run()
print("states:", st1["status"]["stop_reason"], st2["status"]["stop_reason"], st2["status"]["pc_hex"], session.sim.read("r0"))

old_src = ["start:", "LDI r0, 0", "LDI r1, 5", "loop:", "CALL sub", "ST r0, [0x10]", "HLT"]
new_src = ["; banner", "start:", "LDI r0, 0", "LDI r1, 5", "loop:", "CALL sub", "ST r0, [0x10]", "HLT"]
print("relocated:", relocate_breakpoints(old_src, new_src, {5: "r1 == 3"}))

server = create_server(session, port=0)
try:
    print("server_bound:", server.server_port > 0, len(server.token) > 0)
finally:
    server.server_close()
```

輸出：
```text
states: step_done halted 0x0c 30
relocated: {6: 'r1 == 3'}
server_bound: True True
```

## 注意事項與限制

- **受信任的檔案**：`mcu.py` 與 `--script` 會在本地執行 Python 程式碼，請僅載入可信的定義與設定腳本。
- **支援 `Step Back` 的條件**：若掛載的裝置未實作 `get_state` / `set_state`，`Step Back` 會自動停用（`can_step_back == False`）並於介面提示，但不影響向前單步或持續執行。
- **僅限本機連線**：`create_server` 僅綁定 `127.0.0.1`；若需從遠端瀏覽器連線，請透過 SSH 連接埠轉送使用。

## 延伸閱讀

- [isasim 快速入門](../../isasim/README.md)
- [isasim API 參考](../../isasim/reference.md)
- [文件導覽](../../README.md)
