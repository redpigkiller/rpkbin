# rpkbin.cfg — 低階控制流工具組

[![English](https://img.shields.io/badge/Language-English-blue.svg)](cfg.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](cfg_zh.md)

`rpkbin.cfg` 是給 low-level / assembly-oriented 程式作者使用的控制流整理工具。

它用來描述 block、branch、label，以及可選的 subroutine call；檢查常見的流程形狀錯誤；輸出可讀的文字 layout；並產生 deterministic 的 block 排列，方便 FSM、MCU、手寫 assembly 或 generated assembly workflow 使用。

它刻意不負責 parse assembly、不配置 register、不理解特定 ISA，也不決定 calling convention。這些 target-specific 的選擇應該留在你的 frontend 或 emitter。

---

## 快速開始

### 1. 建立 Flow

一個 `CFG` 由 blocks 與 directed edges 組成。Edge condition 是單純的字串，由你的 DSL、spreadsheet parser、flowchart importer 或 emitter 解讀。`cond=None` 表示 default 或 unconditional path。

```python
from rpkbin.cfg import CFG

cfg = CFG()
cfg.add_block("entry", label="ENTRY")
cfg.add_block("work", label="WORK")
cfg.add_block("done", label="DONE")

cfg.add_edge("entry", "work", cond="start", priority=0)
cfg.add_edge("entry", "done", cond=None, priority=1)
cfg.add_edge("work", "done")

cfg.set_entry("entry")
cfg.set_exit("done")
```

### 2. 檢查並印出

`validate()` 會找出從流程圖或 assembly label 規劃轉換時常見、但很容易漏看的控制流問題。

```python
issues = cfg.validate()
if issues:
    for issue in issues:
        print(issue)

print(cfg.format())
```

`format()` 是主要的顯示方式：它會產生 deterministic 的文字表格，列出 blocks、instruction preview 與 outgoing edges。它適合用在 terminal、log、review 與測試裡。

### 3. 選擇發射順序

`linearize()` 回傳 reachable block ids，順序適合拿來做 code layout。

```python
order = cfg.linearize("trace")
print(order)
```

可用策略：

| Strategy | 適合情境 |
|---|---|
| `rpo` | 需要穩定、通用、可處理 loop 的排列。 |
| `trace` | 希望 branch chain 盡量聚在一起，讓 emitted code 比較好讀。 |
| `topological` | 已知 flow 是 DAG，並希望取得嚴格拓樸順序。 |
| `custom` | 想要自行指定 blocks 的排序偏好。 |

#### Custom Order (自訂順序) 策略

你可以使用 `strategy="custom"` 並傳入 `order` 參數來指定排序偏好：

```python
order = cfg.linearize(strategy="custom", order=["entry", "B", "C"])
```

`custom` 策略的規則：
- `order` 是偏好清單 (preference list)，不是硬性覆蓋。
- 最終只會輸出從 start node (或 CFG entry) 可達 (reachable) 的 blocks。
- 每個 reachable block 在結果中恰好只會出現一次。
- 若 `order` 裡包含未知的 block ID，會 raise `ValueError`。
- 若 `order` 裡包含 unreachable (不可達) 的 block ID，會 raise `ValueError`。
- 漏掉沒寫在 `order` 裡的 reachable blocks 會以 RPO 排序補在最尾端。

所有策略都會從 `start` 或 CFG entry 開始，只回傳 reachable blocks。若要檢查不在主流程上的 blocks，使用 `find_unreachable()`。

### 選擇適合的 Linearization 方式 (CFG vs. FSM vs. MCU)

根據你的 target 輸出風格，選擇最合適的 linearization 工具：

- **`cfg.linearize()`**：當你只需要一個單純的 reachable block ID 排序（例如 topological、trace、custom 或 RPO），不需要額外處理 transition metadata 或 jump 指令時使用。
- **`fsm.linearize()`**：適用於 state machine、transition table 或 state dispatch 類的程式碼發射，需要將 state 對應到 slots 並依 priority 處理 exits 的場景。
- **`mcu.linearize()`**：適用於 assembly-like 或 MCU sequential code emission。當你需要處理 block 的物理排序，並計算 `needs_jump`、`jump_target` 以及 `is_fallthrough` 時使用。

---

## 產品結構

這個 package 分成四層：

| Layer | 用途 |
|---|---|
| Core Flow | 建立、檢查、顯示、排序單一控制流圖。 |
| Program Calls | 標註 subroutine calls，並檢查 call depth。 |
| Domain Recipes | 套用常見 FSM 與 MCU flow 檢查 / layout。 |
| Graph Utilities | 可達性、loop、dominance、merge 與 deeper analysis。 |

大多數使用者從 Core Flow 開始。只有 workflow 真的需要時，才加入其他層。

---

## Core Flow

### `BasicBlock`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | `str` | 唯一 graph key。穩定的 id 很適合測試與 generated code。 |
| `label` | `str | None` | 人類可讀的 label，通常是 assembly label 或 flowchart state name。 |
| `insns` | `list[Insn]` | 可選的 instruction annotations。Core flow 功能不需要它。 |
| `meta` | `dict` | source location、spreadsheet row、flowchart id 或 caller-owned data。 |

你可以透過 `CFG.add_block(...)` 建立 block，也可以傳入已建立好的 `BasicBlock`。

```python
from rpkbin.cfg import BasicBlock, CFG

cfg = CFG()
cfg.add_block("idle", label="IDLE", meta={"page": "main"})
cfg.add_block(BasicBlock("halt", label="HALT"))
```

### `CFG`

建構：

```python
cfg = CFG()
cfg.add_block("idle", label="IDLE")
cfg.add_block("work", label="WORK")
cfg.add_edge("idle", "work", cond="go", priority=0)
cfg.set_entry("idle")
```

存取：

```python
cfg.get_block("idle")          # BasicBlock
cfg.blocks                     # list[BasicBlock]，依新增順序
cfg.edges                      # list[(src, dst, attrs)]
cfg.entry / cfg.exit           # BasicBlock 或 None
cfg.successors("idle")         # list[BasicBlock]
cfg.predecessors("work")       # list[BasicBlock]
cfg.out_edges("idle")          # 依 priority 排序
cfg.in_edges("work")           # 依 priority 排序
cfg.edge_attrs("idle", "work") # dict copy
"idle" in cfg                  # True / False
len(cfg)                       # block 數量
```

修改：

```python
removed_block = cfg.remove_block("work")
removed_attrs = cfg.remove_edge("idle", "work")
clone = cfg.copy()
```

#### 安全改名 Block

不要直接修改 `BasicBlock.id`。請使用 `CFG.rename_block()`，以確保 graph key、entry/exit markers 與 edge 結構保持一致。

```python
cfg.rename_block("old", "new")
```

所有 incoming / outgoing edges（包含 self-loop）都會保留原本的 attrs。如果 entry 或 exit 指向被改名的 block，也會自動更新。

驗證：

```python
issues = cfg.validate()
```

Generic validator 會檢查：

- missing 或 invalid entry/exit references
- isolated blocks
- duplicate outgoing priorities
- multiple default outgoing edges
- 單一 conditional outgoing edge 但沒有 default path

### Edge 語意

`add_edge(src, dst, cond=None, priority=0, **attrs)` 會儲存一條控制流轉移。

| Attribute | 意義 |
|---|---|
| `cond=None` | Default、else 或 unconditional path。 |
| `cond="..."` | Caller-owned condition string。CFG 不解讀。 |
| `priority` | 同一 block 有多條 outgoing edges 時的 evaluation order。數字越小越先檢查。 |
| `**attrs` | 額外 caller-owned metadata。 |

這讓 CFG 保持 target-neutral：同一個 `cond="start"` 可以被一個 emitter 轉成 assembly branch，也可以被另一個 emitter 轉成 table entry 或 HDL case item。

### 文字顯示

```python
print(cfg.format())
print(cfg)          # 等同於 cfg.format()
```

`format()` 在 entry 已設定時會使用 reverse post-order 排列 blocks，並把 unreachable blocks 接在後面；outgoing edges 依 priority 列出。在使用更重的視覺化之前，先用它當第一層 debug view。

常用顯示選項：

```python
print(cfg.format(start="work", show_unreachable=False))
print(cfg.format(show_meta=True))
```

---

## Program Calls

只有當你想描述多個 CFG 之間的 subroutine 關係時，才需要使用 `Program` 與 `CallRef`。

```python
from rpkbin.cfg import CFG, CallRef, Program
from rpkbin.cfg.analysis import build_call_graph, check_call_depth

main = CFG()
main.add_block("entry", label="ENTRY", insns=[CallRef("SUB_CHECK")])
main.add_block("done", label="DONE")
main.add_edge("entry", "done")
main.set_entry("entry")

sub = CFG()
sub.add_block("body", label="SUB_CHECK")
sub.add_block("ret", label="RET")
sub.add_edge("body", "ret")
sub.set_entry("body")
sub.set_exit("ret")

program = Program({"main": main, "SUB_CHECK": sub}, entry_fn="main")

call_graph = build_call_graph(program)
depth = check_call_depth(program, max_depth=2)

print(program.format())
print(program)      # 等同於 program.format()
```

`CallRef("SUB_CHECK")` 表示「這個 block 會 call 名為 `SUB_CHECK` 的 CFG」。它不代表任何 target calling convention、stack behavior、register clobber 或 return instruction。它只提供足夠資訊，讓工具回答結構性問題：

- 誰 call 誰？
- 是否有 recursion？
- call depth 是否超過 hardware 或 coding-rule limit？

`Program` 建立時會驗證：

- `cfgs` 不可為空
- `entry_fn` 必須存在於 `cfgs`
- 每個 `CallRef.callee` 都必須對應 `cfgs` 中的一個 key

若只有單一 flow，且不需要 call-depth 檢查，直接使用 `CFG` 即可。

`Program.format()` 接受和 `CFG.format()` 相同的 instruction preview 控制參數，可以顯示 call sites，也可以隱藏 call graph 或只顯示指定 functions：

```python
print(program.format(max_insns=4, max_insn_chars=60))
print(program.format(show_call_graph=False, fn_names=["main"]))
print(program.format(show_call_sites=False, show_meta=True))
```

`Program.validate()` 會彙整所有 CFG 的結構問題：

```python
issues = program.validate(max_call_depth=2)
```

#### Function Order（函數排列順序）

`Program.function_order()` 回傳 function names，順序用於 assembly-like output 中多個 CFG 的物理排列。它不會產生 block layout 或呼叫 `CFG.linearize()`。

```python
# entry function 排第一，其餘依 insertion order（預設）
program.function_order()

# 與 program.cfgs 的 insertion order 完全相同
program.function_order("insertion")

# 從 entry_fn 開始對 call graph 做 DFS pre-order 排列；
# 從 entry 無法到達的 functions 會依 insertion order 補在後面
program.function_order("call_dfs")

# 由 caller 自訂順序；未列出的 functions 依 insertion order 補在後面
program.function_order("custom", order=["SUB_CHECK", "main"])

# strict=True：order 必須列出所有 functions，不補充
program.function_order("custom", order=["SUB_CHECK", "main"], strict=True)
```

---

## Domain Recipes

FSM 與 MCU modules 是建立在同一套 CFG model 上的小型 target-neutral recipes。

### FSM

當 main flow 是通常會永遠運行的 state machine 時，使用 `rpkbin.cfg.fsm`。

```python
from rpkbin.cfg import fsm

dead_states = fsm.find_dead_states(program)
sink_cycles = fsm.find_sink_sccs(program)
missing_defaults = fsm.check_conditions_complete(program)
layout = fsm.linearize(program, strategy="rpo")
```

| Function | Returns | 用途 |
|---|---|---|
| `find_dead_states(program)` | `list[BasicBlock]` | 從 reset/entry 無法到達的 states。 |
| `find_sink_sccs(program)` | `list[list[str]]` | 無法回到 reset/entry 的 trap cycles。 |
| `check_conditions_complete(program)` | `list[str]` | 只有 conditional exits、沒有 default path 的 states。 |
| `linearize(program, strategy="rpo")` | `FSMLayout` | Ordered state slots 與依 priority 排序的 exits。 |

FSM sink SCCs 是進入後無法回到 reset state 的 cycles。一般 loop 是正常的；trap cycles 才可疑。

#### FSM Custom Order 自訂順序

你可以使用 `strategy="custom"` 並傳入 `order` 參數來指定 FSM state 的排序偏好：

```python
layout = fsm.linearize(program, strategy="custom", order=["IDLE", "FETCH", "DONE"])
```

說明：
- FSM layout 面向 state 與 transition。
- `custom` order 只會影響 slot emission 的物理排序（哪一個 state 先被輸出）。
- 每個 state slot 的 exits 仍會依 edge priority 進行排序。
- FSM 不使用 MCU fallthrough policy。

### MCU

當 main flow 預期最後應該到達 halt 或 exit block 時，使用 `rpkbin.cfg.mcu`。

```python
from rpkbin.cfg import mcu

dead_loops = mcu.find_dead_loops(program, exit_block="HALT")
removed = mcu.dead_code_elimination(program.main)
layout = mcu.linearize(program, strategy="trace")
```

| Function | Returns | 用途 |
|---|---|---|
| `find_dead_loops(program, exit_block=None)` | `list[list[str]]` | Reachable cycles 但沒有 path 到 halt/exit。 |
| `dead_code_elimination(cfg, start=None)` | `list[BasicBlock]` | In-place 移除 unreachable blocks。 |
| `linearize(program, strategy="rpo")` | `MCULayout` | Ordered slots，包含 exit-edge 與 fallthrough hints。 |

`MCULayout` 不會產生 assembly。它告訴你的 emitter 哪個 block 在下一個位置、哪條 outgoing edge 是 physical fallthrough，以及何時需要 unconditional jump。Branch mnemonic、condition inversion 與 target-specific instruction selection 仍然是 emitter 的工作。

```python
for slot in layout.slots:
    emit_label(slot.block.id)
    emit_block(slot.block)
    for edge in slot.exits:
        if edge.cond is not None:
            emit_conditional_branch(edge.cond, edge.target)
    if slot.needs_jump:
        emit_jump(slot.jump_target)
```

- Emitter 說明：
  - Emitter (程式碼產生器) 仍需負責目標平台特定的 branch 助記符 (mnemonic，例如 `jmp`、`jne`、`beq`)。
  - `MCULayout` 本身**不產生 assembly**。
  - `MCULayout` **不做 condition inversion**。

#### Fallthrough Policy 與 Layout Hints

你可以透過設定 `fallthrough_policy` 並在 edge 上提供 layout hints 來客製化 block 的重新排序。

```python
layout = mcu.linearize(
    program,
    strategy="trace",
    fallthrough_policy="layout",
)
```

支援的 `fallthrough_policy` 選項：
- `"none"`：預設值。維持 base strategy 的排列順序，不做 layout hint 重新排序。
- `"default"`：偏好將 unconditional edge (`cond is None`) 指向的 target 放在緊接的下一個位置。
- `"layout"`：使用 edge 的 `layout_role` 屬性進行排序（優先順序為 `"main"` > `"normal"` > `"cold"`）。
- `"likelihood"`：使用 edge 的 `likelihood` 屬性進行排序（優先順序為 `"likely"` > `"normal"` > `"unlikely"`）。
- `"weight"`：使用 edge 的 `weight` 屬性進行排序（數值越高越優先）。

> [!IMPORTANT]
> **Heuristics 限制說明**
> 這些 policy 是保守的貪婪啟發式演算法 (conservative greedy heuristics)，無法保證全局最優的 layout (global optimal layout)。
> - Policy 僅會影響 block 排列的**偏好順序**。
> - 它們**不會**修改原本的 CFG 結構。
> - 它們**不會**修改 edge conditions。
> - 它們**不會**進行 branch inversion (分支翻轉)。

#### Edge Layout 屬性

你可以在建立 edge 時附加 layout 屬性。`add_edge()` 的 API 簽名不需修改，因為它支援 `**attrs` 任意關鍵字參數：

```python
# layout_role 支援: "main", "normal", "cold"
cfg.add_edge("A", "B", cond=None, layout_role="main")
cfg.add_edge("A", "RESET", cond="fail", layout_role="cold")

# likelihood 支援: "likely", "normal", "unlikely"
cfg.add_edge("B", "C", cond=None, likelihood="likely")
cfg.add_edge("B", "RESET", cond="timeout", likelihood="unlikely")

# weight 支援: 任何非負整數/浮點數 (non-negative int/float)
cfg.add_edge("C", "D", cond=None, weight=10.0)
cfg.add_edge("C", "RESET", cond="error", weight=1.0)
```

欄位驗證只在 `mcu.linearize()` 使用對應的 policy 時發生。未明確設定屬性時，預設值為：
- `layout_role = "normal"`
- `likelihood = "normal"`
- `weight = 1.0`

#### 物理 Fallthrough 規則

> [!WARNING]
> **重要：物理 Fallthrough (順序直通) 判定條件**
> 在 `MCULayout` 中，只有在滿足以下**所有**條件時，`MCUExitEdge.is_fallthrough` 才會是 `True`：
> 1. Edge 沒有條件限制 (`edge.cond is None`)。
> 2. Edge 的 target 剛好是緊接的下一個物理 slot (`edge.target` 位於 layout 的下一個位置)。
> 
> Conditional edges (有條件的 edge) **永遠不可能是** physical fallthrough，即使 target 在 layout 中剛好相鄰。系統不會進行 condition inversion。
> 
> 請注意：
> - `layout_role="main"` 不等於 fallthrough。
> - `likelihood="likely"` 不等於 fallthrough。
> - `weight` 高不等於 fallthrough。
> - 它們**只是排序偏好**，真正的 fallthrough 仍必須滿足上述 `cond is None` 且 target 是下一個 slot 的條件。

#### 安全性與相容性

- 預設 `mcu.linearize()` 的 `fallthrough_policy` 為 `"none"`，以避免破壞既有行為 (breaking changes)。
- 既有的 `rpo` / `trace` / `topological` 策略行為維持不變。
- Custom order 與 fallthrough policies 是完全 opt-in 的功能。

---

## Structural Diff（結構比對）

使用 `rpkbin.cfg.diff` 對兩個 CFG 或兩個 Program 做結構比對。
這只比較 graph 結構與 caller-owned annotations，**不做 semantic equivalence 檢查**。

```python
from rpkbin.cfg import diff_cfgs, cfg_structurally_equal
from rpkbin.cfg import diff_programs, program_structurally_equal

# CFG 比對
result = diff_cfgs(old_cfg, new_cfg)
if result.has_changes():
    print("新增 blocks:",   result.added_blocks)
    print("刪除 blocks:",   result.removed_blocks)
    print("變更 blocks:",   result.changed_blocks)   # dict[key, BlockDelta]
    print("新增 edges:",    result.added_edges)
    print("刪除 edges:",    result.removed_edges)
    print("變更 edges:",    result.changed_edges)     # dict[(src,dst), EdgeDelta]
    print("新增 calls:",    result.added_calls)       # CallRef 關係
    print("刪除 calls:",    result.removed_calls)

# 方便的 boolean 形式
if not cfg_structurally_equal(old_cfg, new_cfg):
    ...

# Program 比對
result = diff_programs(old_program, new_program)
if result.has_changes():
    print("entry 改變:",       result.entry_fn_changed)
    print("新增 functions:",   result.added_functions)
    print("刪除 functions:",   result.removed_functions)
    print("變更 functions:",   result.changed_functions)  # dict[name, CFGDiffResult]
```

`diff_cfgs` / `diff_programs` 的主要選項：

| 選項 | 預設值 | 效果 |
|---|---|---|
| `align_by` | `"id"` | `"label"` 改用 `block.label` 來對應兩邊的 blocks。 |
| `compare_insns` | `True` | 設 `False` 可忽略 instruction list 差異。 |
| `compare_meta` | `False` | 設 `True` 可偵測 `meta` dict 差異。 |
| `compare_edge_attrs` | `True` | 設 `False` 可忽略 edge attribute 差異。 |

`CallRef` 關係**永遠都會比對**，不受 `compare_insns` 影響。
CallRef 代表 structural 的 caller/callee 關係，而非 instruction 內容。

---

## Graph Utilities

當你需要更深的 graph inspection 時，可以使用以下 helpers。一般 build/check/layout workflow 不需要先懂這些。

### Traversal 與 Reachability

```python
list(cfg.dfs())
list(cfg.bfs())
cfg.reverse_postorder()
cfg.can_reach("entry", "done")
cfg.find_unreachable()
cfg.find_sccs()
```

### Loops 與 Dominance

```python
cfg.find_back_edges()
cfg.find_natural_loops()
cfg.dominators()
cfg.post_dominators(exit_node="done")
cfg.dominator_tree()
```

### 合併 Labeled Flows

`merge_cfgs()` 會把多個 CFG 中具有相同 non-`None` label 的 blocks 合併。當多個被抽出的 flow fragments 使用 label 作為連接點時，這很有用。

```python
from rpkbin.cfg import merge_cfgs

merged = merge_cfgs(flow1, flow2)
merged.set_entry("ENTRY")
```

規則：

- 具有相同 label 的 blocks 會合併
- 帶有 instructions 的 block 會覆蓋同 label 的 placeholder block
- 完全相同的 connecting edges 會自動去重
- label、instruction、metadata 或 edge attributes 衝突時會 raise `CFGMergeError`
- merged CFG 一開始沒有 entry 或 exit；合併後再設定

### Instruction Annotations 與 Liveness

Instruction annotations 是可選的。只有需要 def/use 資訊的分析才會使用它。

| Type | 意義 |
|---|---|
| `Assignment(lhs, rhs, raw="")` | 定義 `lhs`，並使用 `rhs` 中的變數。Constants 應由 frontend 排除。 |
| `CallRef(callee, raw="")` | 標註對 `Program` 中另一個 CFG 的呼叫。 |
| `OtherInsn(raw="", defs=set(), uses=set())` | Caller-provided def/use annotation，給其他指令使用。 |

```python
from rpkbin.cfg import Assignment, OtherInsn
from rpkbin.cfg.analysis import interprocedural_liveness

bb = cfg.get_block("work")
bb.insns.append(Assignment("acc", ["sample"], raw="acc = sample"))
bb.insns.append(OtherInsn(raw="CUSTOM", defs={"flag"}, uses={"acc"}))

results = interprocedural_liveness(program)
live = results["main"].live_in["work"]
```

Liveness 是 structural 且 annotation-driven 的。除非你的 frontend 把資訊記錄在 `defs` / `uses`，否則它不理解 ISA register alias、flags、memory aliasing、stack convention 或 implicit clobbers。

---

## Non-Goals

`rpkbin.cfg` 刻意保持小而 target-neutral。它不做：

- parse assembly source
- 自行產生最終 target assembly
- register allocation
- modeling ISA-specific flags、memory aliasing、stack 或 calling conventions
- branch form 或 instruction selection 最佳化
- 取代 LLVM、compiler framework 或 `networkx`

它的邊界很簡單：`rpkbin.cfg` 讓控制流本身變得清楚、可檢查、可排序；你的 domain code 決定每個 block 與 condition 在 target 上代表什麼。

---

## 目錄結構

```text
rpkbin/cfg/
  block.py     BasicBlock 與可選的 instruction annotations
  cfg.py       CFG structure、validation、layout 與 graph utilities
  program.py   多個 CFG 的 Program container
  analysis.py  call graph、call depth 與 liveness analysis
  diff.py      CFG / Program 的結構比對與等價判斷
  fsm.py       FSM-oriented checks 與 layout
  mcu.py       MCU-oriented checks 與 layout
```
