# rpkbin.cfg — 控制流圖 IR

[![English](https://img.shields.io/badge/Language-English-blue.svg)](cfg.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](cfg_zh.md)

`rpkbin.cfg` 是一個通用的控制流圖（CFG）IR 常式庫。
它提供共用的圖結構表示、結構分析演算法、跨函式 Liveness 分析，
以及可選的 FSM / MCU-like 工作流程 adapter。

此工具涵蓋從建立 CFG 到偵測邏輯錯誤、產生有序 code layout 的全流程—
不假設任何特定指令集、register model 或硬體目標。

---

## 快速開始

### 1. 建立 CFG

先從通用的 `CFG` 類別開始。block 是節點，edge 是可能的控制流轉移。
`cond=None` 表示無條件 / default 轉移；其他值則是由你的 frontend 或
emitter 解讀的條件字串。

```python
from rpkbin.cfg import CFG, Assignment

cfg = CFG()
cfg.add_block("entry", label="ENTRY", insns=[Assignment("x", [])])
cfg.add_block("work", label="WORK")
cfg.add_block("done", label="DONE")
cfg.add_edge("entry", "work", cond="start", priority=0)
cfg.add_edge("entry", "done", cond=None, priority=1)
cfg.add_edge("work", "done")
cfg.set_entry("entry")
cfg.set_exit("done")

print(cfg.validate())       # [] 表示沒有結構問題
print(cfg.linearize("trace"))
```

### 2. 打包多個 CFG

`Program` 將一組相關 CFG 打包在一起，例如一個 entry flow 加上可呼叫的 helper
flows。跨函式分析與可選的 FSM/MCU adapter 會使用它。

```python
from rpkbin.cfg import CFG, Assignment, CallRef, Program

# --- 主流程 ---
main = CFG()
main.add_block("IDLE",  label="IDLE",  insns=[Assignment("x", [])])
main.add_block("FETCH", label="FETCH", insns=[CallRef("SUB_CHECK")])
main.add_block("DONE",  label="DONE")
main.add_edge("IDLE",  "FETCH", cond="start", priority=0)
main.add_edge("FETCH", "IDLE",  cond="loop",  priority=0)
main.add_edge("FETCH", "DONE",  cond="halt",  priority=1)
main.set_entry("IDLE")

# --- 子程式 ---
sub = CFG()
sub.add_block("sub_body", insns=[Assignment("y", ["x"])])
sub.add_block("sub_ret")
sub.add_edge("sub_body", "sub_ret")
sub.set_entry("sub_body")
sub.set_exit("sub_ret")

program = Program(cfgs={"main": main, "SUB_CHECK": sub}, entry_fn="main")
```

### 3. 可選的 FSM Adapter

```python
from rpkbin.cfg import fsm

dead   = fsm.find_dead_states(program)          # 無法從 reset 到達的狀態
sinks  = fsm.find_sink_sccs(program)            # 降落入後無法回到 entry 的 SCC
issues = fsm.check_conditions_complete(program) # 沒有預設出邊的狀態

layout = fsm.linearize(program)
for slot in layout.slots:
    for edge in slot.exits:   # 依 priority 由小到大排列
        print(slot.block.id, edge.cond, "->", edge.target)
```

### 4. 可選的 MCU Adapter

```python
from rpkbin.cfg import mcu

dead_loops = mcu.find_dead_loops(program, exit_block="HALT")  # 無限迴圈（Bug）
removed    = mcu.dead_code_elimination(main)                   # 移除不可達 block

layout = mcu.linearize(program)
for slot in layout.slots:
    emit_block(slot.block)
    if slot.needs_jump:
        emit_jump(slot.jump_target)  # 補上屬於內部的 JMP 指令
```

### 5. 跨函式 Liveness 分析

```python
from rpkbin.cfg.analysis import interprocedural_liveness, check_call_depth

check_call_depth(program, max_depth=2)         # 超過或有遞迴則 raise

results = interprocedural_liveness(program)
r = results["main"]
print(r.live_in["FETCH"])                      # frozenset，FETCH 入口時的 live 變數
print(r.is_live_at_exit("IDLE", "x"))          # True / False
```

---

## API 參考

### 指令型別

`BasicBlock.insns` 中的每個元素必須是以下其中一種，分析工具從型別自動推導 def/use：

| 型別 | 主要欄位 | `def` | `use` |
|---|---|---|---|
| `Assignment(lhs, rhs, raw="")` | `lhs: str`, `rhs: list[str]` | `{lhs}` | `set(rhs)` |
| `CallRef(callee, raw="")` | `callee: str` | 由 callee summary 推導 | 由 callee summary 推導 |
| `OtherInsn(raw="", defs=set(), uses=set())` | `defs`, `uses` | `defs` | `uses` |

> `Assignment.rhs` 只能含**變數名稱**，常數和編譯期常數必須由前端解析器排除。
> `CallRef.callee` 必須對應 `Program.cfgs` 中的一個 key。

---

### `BasicBlock`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | `str` | 唯一識別符，作為 graph node key |
| `label` | `str | None` | 人類可讀名稱（來自 Visio label 或 DSL） |
| `insns` | `list[Insn]` | 有序指令列表（`Assignment | CallRef | OtherInsn`） |
| `meta` | `dict` | 任意 metadata |

---

### `CFG`

以 `networkx.DiGraph` 為底層的控制流圖類別。

#### 建構

```python
cfg = CFG()
cfg.add_block("entry", label="IDLE", insns=[Assignment("x", [])])
cfg.add_block("end")
cfg.add_edge("entry", "end", cond="done", priority=0)
cfg.set_entry("entry")
cfg.set_exit("end")   # 可選；只有需要 exit 的分析才必須設定

# 也可以直接傳入已建好的 BasicBlock
bb = BasicBlock("aux", label="AUX", meta={"src": "visio"})
cfg.add_block(bb)
```

> `add_block` 重複 `id` 會 raise `ValueError`。
> `add_block(BasicBlock)` 時不可同時傳 `label`/`insns`/`meta`。
> `add_edge` 如果任一 block 不存在會 raise `KeyError`。
> `cond=None` 表示無條件（預設 / else）邊。

#### 修改

```python
removed_bb   = cfg.remove_block("aux")          # 移除 block 及所有關聯 edge，回傳 BasicBlock
removed_attrs = cfg.remove_edge("entry", "end")  # 移除 edge，回傳 attrs dict
```

> `remove_block` 會自動清除 entry / exit 指定（若被移除的剛好是 entry 或 exit）。

#### 存取與顯示

```python
cfg.get_block("entry")          # BasicBlock
cfg.blocks                       # list[BasicBlock] 依新增順序
cfg.edges                        # list[(src, dst, attrs)] 所有 edge
cfg.entry / cfg.exit             # BasicBlock 或 None
cfg.predecessors("end")          # list[BasicBlock]
cfg.successors("entry")          # list[BasicBlock]
cfg.edge_attrs("entry", "end")   # dict
cfg.out_edges("entry")           # list[(src, dst, attrs)]，依 priority 排序
cfg.in_edges("end")              # list[(src, dst, attrs)]，依 priority 排序
cfg.has_edge("entry", "end")     # True / False
"entry" in cfg                   # True / False
len(cfg)                         # block 數量

print(repr(cfg))                 # CFG(2 blocks, 1 edges, entry='entry')
print(cfg.format())              # 可讀性高的多行圖表（表格）輸出
```

#### 複製與驗證

```python
clone = cfg.copy()              # deep copy（修改 clone 不影響原圖）

issues = cfg.validate()         # list[str]，空 = 無問題
# 檢查項：entry/exit 是否存在、isolated block、
#         重複 priority、多個 default 出邊、
#         單一 conditional 出邊但沒有 default path
```

#### 遞歷

```python
for bb in cfg.dfs():             # 深度優先，從 entry 開始
    ...
for bb in cfg.bfs():             # 廣度優先
    ...
order = cfg.reverse_postorder()  # RPO，正向 Dataflow 分析標準順序
```

#### 可達性

```python
cfg.can_reach("entry", "end")    # True / False
cfg.find_unreachable()           # list[BasicBlock]
cfg.find_sccs()                  # list[list[str]]，拓樸順序
```

#### 迴圈分析

```python
backs = cfg.find_back_edges()     # list[(tail, header)]
loops = cfg.find_natural_loops()  # list[NaturalLoop]
# loop.header, loop.body (set[str]), loop.back_edge
```

#### 支配領域

```python
idom  = cfg.dominators()                       # {node: idom_node}
ipost = cfg.post_dominators(exit_node="end")   # 必須明確傳入 exit_node
tree  = cfg.dominator_tree()                   # networkx DiGraph
```

#### 線性化

```python
order = cfg.linearize("rpo")          # Reverse Post-Order，可處理 cycle
order = cfg.linearize("topological")  # 僅適用 DAG；有 cycle 則 raise ValueError
order = cfg.linearize("trace")        # Priority-guided trace：保持 branch chain 聚集
# => list[str]， block id 的發射順序
```

> 所有 strategy 都會從 `start` 或 CFG entry 開始，只回傳 reachable blocks。
> 若要檢查 orphan/dead blocks，請使用 `find_unreachable()`。
>
> `"trace"` 策略依 edge priority 優先走訪，將條件分支的各子鏈保持連續，
> 延遲 common join point，適合產生可讀性高的 code layout。
> 當多個 deferred node 都同樣可選時，會使用 block 新增順序作為 deterministic
> tie-breaker。

#### 合併

```python
from rpkbin.cfg import merge_cfgs

# 把多個 CFG 的 block 依 label 為鍵值合併成單一 CFG
merged = merge_cfgs(flow1, flow2) 
```

> **合併規則**：具備相同 label 的 block 將會自動合併；帶有 instruction 的會覆蓋純粹當連接錨點的佔位用 block。
> 若不同 flow 指定了完全相同屬性的連線 edge 將會平穩自動去重。
> 對同樣 label 或是連線有衝突時會 raise `CFGMergeError`。
> 合併後的 CFG 不具有 `entry` 和 `exit` 屬性，需要重新呼叫 `set_entry`/`set_exit` 指派。

---

### `Program`

```python
program = Program(
    cfgs={"main": main_cfg, "SUB_CHECK": sub_cfg},
    entry_fn="main",
)
program.main             # 等同於 program.cfgs[program.entry_fn]
program["SUB_CHECK"]     # 等同於 program.cfgs["SUB_CHECK"]
"SUB_CHECK" in program   # True
len(program)             # 函式數量
list(program)            # ["main", "SUB_CHECK"]
```

> `Program` 建立時會自動驗證：
> - `cfgs` 不可為空
> - `entry_fn` 必須存在於 `cfgs`
> - 所有 `CallRef.callee` 必須對應 `cfgs` 中的 key（否則 raise `KeyError`）

只有在需要多個 CFG 或 call-aware analysis 時才需要 `Program`。若只是單一圖，
直接使用 `CFG` 即可。

---

### `rpkbin.cfg.fsm` — FSM 分析與線性化

| 函式 | 回傳 | 說明 |
|---|---|---|
| `find_dead_states(program)` | `list[BasicBlock]` | 從 entry 無法到達的狀態 |
| `find_sink_sccs(program)` | `list[list[str]]` | 陷阱循環（無法回到 entry） |
| `check_conditions_complete(program)` | `list[str]` | 所有出邊均為條件式的狀態 |
| `linearize(program, strategy="rpo")` | `FSMLayout` | 有序狀態 layout |

```python
layout = fsm.linearize(program)
for slot in layout.slots:
    # slot.block : BasicBlock
    for edge in slot.exits:   # list[ExitEdge]，依 priority 排序
        # edge.priority : int
        # edge.cond     : str | None  (None = 無條件)
        # edge.target   : str (block id)
        ...
```

> **FSM Sink SCC**：進入後無法回到 reset 狀態的循環。
> FSM 本身就是無限迴圈，只有陷阱狀態才會被標記為錯誤。

---

### `rpkbin.cfg.mcu` — MCU 分析與線性化

| 函式 | 回傳 | 說明 |
|---|---|---|
| `find_dead_loops(program, exit_block=None)` | `list[list[str]]` | 無法到達 exit_block 的 SCC |
| `dead_code_elimination(cfg, start=None)` | `list[BasicBlock]` | In-place 移除不可達 block |
| `linearize(program, strategy="rpo")` | `MCULayout` | 含跟蹤跳轉與完整出邊資訊的有序 layout |

```python
layout = mcu.linearize(program)
for slot in layout.slots:
    # slot.block       : BasicBlock
    # slot.needs_jump  : bool
    # slot.jump_target : str | None
    # slot.exits       : list[MCUExitEdge]（完整出邊資訊）
    emit_block(slot.block)
    for edge in slot.exits:
        # edge.priority       : int
        # edge.cond           : str | None
        # edge.target         : str
        # edge.is_fallthrough : bool（僅無條件後繼且物理相鄰時為 True）
        if edge.cond is not None:
            emit_conditional_branch(edge.cond, edge.target)
    if slot.needs_jump:
        emit_jump(slot.jump_target)  # 補 JMP 指令（適配硬體）
```

> MCU 的無限迴圈必定是 Bug（機器應最終到達 HALT）。
> `exit_block` 若未傳入，預設從 `cfg.set_exit()` 讀取。
> `is_fallthrough` 只有在「無條件後繼 **且** 物理相鄰」時為 `True`，條件邊永遠為 `False`。

---

### `rpkbin.cfg.analysis` — 跨函式分析

| 函式 | 回傳 | 說明 |
|---|---|---|
| `build_call_graph(program)` | `nx.DiGraph` | 自動掃描 `CallRef` 建立 call graph |
| `check_call_depth(program, max_depth=None)` | `int` | 實際深度；超限或有遞迴則 raise |
| `interprocedural_liveness(program)` | `dict[str, LivenessResult]` | Bottom-up 跨函式 Liveness |

```python
results = interprocedural_liveness(program)
r = results["main"]               # LivenessResult
r.live_in["FETCH"]                # frozenset
r.live_out["IDLE"]                # frozenset
r.is_live_at_entry("FETCH", "x")  # bool
r.is_live_at_exit("IDLE", "x")    # bool
```

> Call graph 必須是 **DAG**（不允許遞迴）。
> Callee summary 從葉節點 bottom-up 計算，`CallRef` 的 def/use 常保正確。

---

## 目錄結構

```
mypkg/cfg/
  block.py     BasicBlock, Assignment, CallRef, OtherInsn, Insn
  cfg.py       CFG 類別 — 圖結構與結構分析
  program.py   Program 容器（dict[str, CFG] + entry_fn）
  analysis.py  build_call_graph, check_call_depth, interprocedural_liveness
  fsm.py       FSM analyzer + FSMLayout compiler
  mcu.py       MCU analyzer + MCULayout compiler
```

| 模組 | 職責 |
|---|---|
| `cfg.py` | 圖結構、遞歷、迴圈 / 支配分析 |
| `analysis.py` | Call graph + 跨函式 Liveness |
| `fsm.py` | FSM 專屬檢查（Sink SCC、條件完整性）與 layout |
| `mcu.py` | MCU 專屬檢查（Dead loop、DCE）與 layout |
