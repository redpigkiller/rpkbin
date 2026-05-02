# rpkbin.cfg — Control Flow Graph IR

[![English](https://img.shields.io/badge/Language-English-blue.svg)](cfg.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](cfg_zh.md)

`rpkbin.cfg` is a generic Control Flow Graph (CFG) IR library.
It provides a common graph representation, structural analysis algorithms,
interprocedural liveness analysis, and optional domain adapters for FSM- and
MCU-like workflows.

The tool covers everything from building a CFG to detecting logical errors and
generating an ordered code layout without assuming any proprietary instruction
set, register model, or hardware target.

---

## Quick Start

### 1. Build a CFG

Start with the generic `CFG` class. A block is a node, and an edge is a
possible control-flow transition. `cond=None` means an unconditional/default
transition; any other value is a condition string owned by your frontend or
emitter.

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

print(cfg.validate())       # [] when no structural issues are found
print(cfg.linearize("trace"))
```

### 2. Bundle Multiple CFGs

A `Program` bundles related CFGs, such as one entry flow plus callable helper
flows. It is used by interprocedural analysis and by the optional FSM/MCU
adapters.

```python
from rpkbin.cfg import CFG, Assignment, CallRef, Program

# --- Main flow ---
main = CFG()
main.add_block("IDLE",  label="IDLE",  insns=[Assignment("x", [])])
main.add_block("FETCH", label="FETCH", insns=[CallRef("SUB_CHECK")])
main.add_block("DONE",  label="DONE")
main.add_edge("IDLE",  "FETCH", cond="start", priority=0)
main.add_edge("FETCH", "IDLE",  cond="loop",  priority=0)
main.add_edge("FETCH", "DONE",  cond="halt",  priority=1)
main.set_entry("IDLE")

# --- Subroutine ---
sub = CFG()
sub.add_block("sub_body", insns=[Assignment("y", ["x"])])
sub.add_block("sub_ret")
sub.add_edge("sub_body", "sub_ret")
sub.set_entry("sub_body")
sub.set_exit("sub_ret")

program = Program(cfgs={"main": main, "SUB_CHECK": sub}, entry_fn="main")
```

### 3. Optional FSM Adapter

```python
from rpkbin.cfg import fsm

dead   = fsm.find_dead_states(program)          # unreachable states
sinks  = fsm.find_sink_sccs(program)            # trap cycles
issues = fsm.check_conditions_complete(program) # states with no default exit

layout = fsm.linearize(program)
for slot in layout.slots:
    for edge in slot.exits:   # sorted by priority
        print(slot.block.id, edge.cond, "->", edge.target)
```

### 4. Optional MCU Adapter

```python
from rpkbin.cfg import mcu

dead_loops = mcu.find_dead_loops(program, exit_block="HALT")
removed    = mcu.dead_code_elimination(main)

layout = mcu.linearize(program)
for slot in layout.slots:
    emit_block(slot.block)
    if slot.needs_jump:
        emit_jump(slot.jump_target)  # insert explicit JMP / branch
```

### 5. Interprocedural Liveness

```python
from rpkbin.cfg.analysis import interprocedural_liveness, check_call_depth

check_call_depth(program, max_depth=2)          # raises on cycle or excess depth

results = interprocedural_liveness(program)
r = results["main"]
print(r.live_in["FETCH"])                       # frozenset of live variables
print(r.is_live_at_exit("IDLE", "x"))           # True / False
```

---

## API Reference

### Instruction Types

Every element in `BasicBlock.insns` must be one of:

| Type | Key Fields | `def` | `use` |
|---|---|---|---|
| `Assignment(lhs, rhs, raw="")` | `lhs: str`, `rhs: list[str]` | `{lhs}` | `set(rhs)` |
| `CallRef(callee, raw="")` | `callee: str` | from callee summary | from callee summary |
| `OtherInsn(raw="", defs=set(), uses=set())` | `defs`, `uses` | `defs` | `uses` |

> `Assignment.rhs` must contain **variable names only** — constants must be excluded by the frontend.
> `CallRef.callee` must match a key in `Program.cfgs`.

---

### `BasicBlock`

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique identifier, used as the graph node key |
| `label` | `str | None` | Human-readable name (from Visio label or DSL) |
| `insns` | `list[Insn]` | Ordered instructions (`Assignment | CallRef | OtherInsn`) |
| `meta` | `dict` | Arbitrary metadata |

---

### `CFG`

Core CFG class backed by `networkx.DiGraph`.

#### Construction

```python
cfg = CFG()
cfg.add_block("entry", label="IDLE", insns=[Assignment("x", [])])
cfg.add_block("end")
cfg.add_edge("entry", "end", cond="done", priority=0)
cfg.set_entry("entry")
cfg.set_exit("end")   # optional; required only by analyses that need an exit

# You can also pass a pre-built BasicBlock directly
bb = BasicBlock("aux", label="AUX", meta={"src": "visio"})
cfg.add_block(bb)
```

> `add_block` raises `ValueError` on duplicate `id`.
> `add_block(BasicBlock)` must not be combined with `label`/`insns`/`meta` arguments.
> `add_edge` raises `KeyError` if either block does not exist.
> `cond=None` means an unconditional (default/else) edge.

#### Mutation

```python
removed_bb   = cfg.remove_block("aux")          # removes block + all incident edges, returns BasicBlock
removed_attrs = cfg.remove_edge("entry", "end")  # removes edge, returns attrs dict
```

> `remove_block` automatically clears entry / exit designation if the removed block was entry or exit.

#### Access & Display

```python
cfg.get_block("entry")           # BasicBlock
cfg.blocks                        # list[BasicBlock] in insertion order
cfg.edges                         # list[(src, dst, attrs)] all edges
cfg.entry / cfg.exit              # BasicBlock or None
cfg.predecessors("end")           # list[BasicBlock]
cfg.successors("entry")           # list[BasicBlock]
cfg.edge_attrs("entry", "end")    # dict
cfg.out_edges("entry")            # list[(src, dst, attrs)] sorted by priority
cfg.in_edges("end")               # list[(src, dst, attrs)] sorted by priority
cfg.has_edge("entry", "end")      # True / False
"entry" in cfg                    # True / False
len(cfg)                          # number of blocks

print(repr(cfg))                  # CFG(2 blocks, 1 edges, entry='entry')
print(cfg.format())               # Multi-line human-readable table of blocks & edges
```

#### Copy & Validation

```python
clone = cfg.copy()               # deep copy (mutating clone does not affect the original)

issues = cfg.validate()          # list[str], empty = no issues
# Checks: entry/exit existence, isolated blocks,
#         duplicate priorities, multiple default outgoing edges,
#         single conditional edge with no default path
```

#### Traversal

```python
for bb in cfg.dfs():             # depth-first pre-order from entry
    ...
for bb in cfg.bfs():             # breadth-first from entry
    ...
order = cfg.reverse_postorder()  # RPO, standard for forward dataflow
```

#### Reachability

```python
cfg.can_reach("entry", "end")    # True / False
cfg.find_unreachable()           # list[BasicBlock]
cfg.find_sccs()                  # list[list[str]], topological order
```

#### Loop Analysis

```python
backs = cfg.find_back_edges()     # list[(tail, header)]
loops = cfg.find_natural_loops()  # list[NaturalLoop]
# loop.header, loop.body (set[str]), loop.back_edge
```

#### Dominance

```python
idom  = cfg.dominators()                       # {node: idom_node}
ipost = cfg.post_dominators(exit_node="end")   # explicit exit_node required
tree  = cfg.dominator_tree()                   # networkx DiGraph
```

#### Linearization

```python
order = cfg.linearize("rpo")          # handles cycles
order = cfg.linearize("topological")  # DAG-only; raises ValueError on cycles
order = cfg.linearize("trace")        # priority-guided trace: keeps branch chains grouped
# => list[str] of block ids in emission order
```

> All strategies start from `start` or the CFG entry and return only reachable
> blocks. Use `find_unreachable()` if you need to inspect orphan/dead blocks.
>
> The `"trace"` strategy follows edges in priority order, keeping each conditional
> branch's sub-chain contiguous and deferring common join points — ideal for
> generating readable code layouts.
> When multiple deferred nodes are equally valid, insertion order is used as a
> deterministic tie-breaker.

#### Merging

```python
from rpkbin.cfg import merge_cfgs

# Unify matching blocks by label across multiple CFGs into a single CFG
merged = merge_cfgs(flow1, flow2) 
```

> **Rules**: matching labeled blocks are unified; blocks with instructions override placeholders.
> The same connecting edge across flows is silently deduplicated.
> Conflicting attributes or instructions on the same label/edge raise errors (`CFGMergeError`).
> The `entry` and `exit` fields of the returned CFG are left unset.

---

### `Program`

```python
program = Program(
    cfgs={"main": main_cfg, "SUB_CHECK": sub_cfg},
    entry_fn="main",
)
program.main             # shorthand for program.cfgs[program.entry_fn]
program["SUB_CHECK"]     # shorthand for program.cfgs["SUB_CHECK"]
"SUB_CHECK" in program   # True
len(program)             # number of functions
list(program)            # ["main", "SUB_CHECK"]
```

> `Program` validates on construction:
> - `cfgs` must not be empty
> - `entry_fn` must exist in `cfgs`
> - Every `CallRef.callee` must reference an existing key in `cfgs` (raises `KeyError` otherwise)

Use `Program` only when you need multiple CFGs or call-aware analysis. For a
single graph, using `CFG` directly is enough.

---

### `rpkbin.cfg.fsm` — FSM Analysis and Linearization

| Function | Returns | Description |
|---|---|---|
| `find_dead_states(program)` | `list[BasicBlock]` | Unreachable from entry |
| `find_sink_sccs(program)` | `list[list[str]]` | Trap cycles with no path back to entry |
| `check_conditions_complete(program)` | `list[str]` | States where all exits are conditional |
| `linearize(program, strategy="rpo")` | `FSMLayout` | Ordered state layout |

```python
layout = fsm.linearize(program)
for slot in layout.slots:
    # slot.block : BasicBlock
    for edge in slot.exits:   # list[ExitEdge], sorted by priority
        # edge.priority : int
        # edge.cond     : str | None  (None = unconditional)
        # edge.target   : str (block id)
        ...
```

> **FSM sink SCCs**: cycles from which the reset (entry) state is unreachable.
> An FSM by design loops forever; only sink traps are flagged as bugs.

---

### `rpkbin.cfg.mcu` — MCU Analysis and Linearization

| Function | Returns | Description |
|---|---|---|
| `find_dead_loops(program, exit_block=None)` | `list[list[str]]` | Cycles with no path to `exit_block` |
| `dead_code_elimination(cfg, start=None)` | `list[BasicBlock]` | Remove unreachable blocks in-place |
| `linearize(program, strategy="rpo")` | `MCULayout` | Ordered block layout with jump and exit edge info |

```python
layout = mcu.linearize(program)
for slot in layout.slots:
    # slot.block       : BasicBlock
    # slot.needs_jump  : bool
    # slot.jump_target : str | None
    # slot.exits       : list[MCUExitEdge] (full exit edge info)
    emit_block(slot.block)
    for edge in slot.exits:
        # edge.priority       : int
        # edge.cond           : str | None
        # edge.target         : str
        # edge.is_fallthrough : bool (True only for unconditional + adjacent)
        if edge.cond is not None:
            emit_conditional_branch(edge.cond, edge.target)
    if slot.needs_jump:
        emit_jump(slot.jump_target)
```

> MCU infinite loops are always bugs (the machine must eventually reach HALT).
> `exit_block` defaults to `cfg._exit` if `set_exit()` was called.
> `is_fallthrough` is `True` only when the edge is the sole unconditional successor
> **and** it is the physically next slot. Conditional edges are never fallthrough.

---

### `rpkbin.cfg.analysis` — Interprocedural Analysis

| Function | Returns | Description |
|---|---|---|
| `build_call_graph(program)` | `nx.DiGraph` | Scans `CallRef`, builds call graph automatically |
| `check_call_depth(program, max_depth=None)` | `int` | Actual depth; raises on cycle or excess |
| `interprocedural_liveness(program)` | `dict[str, LivenessResult]` | Bottom-up liveness |

```python
results = interprocedural_liveness(program)
r = results["main"]               # LivenessResult
r.live_in["FETCH"]                # frozenset
r.live_out["IDLE"]                # frozenset
r.is_live_at_entry("FETCH", "x")  # bool
r.is_live_at_exit("IDLE", "x")    # bool
```

> Call graph must be a **DAG** (no recursion allowed).
> Callee summaries are computed bottom-up, so `CallRef` def/use is always accurate.

---

## Module Layout

```
mypkg/cfg/
  block.py     BasicBlock, Assignment, CallRef, OtherInsn, Insn
  cfg.py       CFG class -- graph structure and structural analysis
  program.py   Program container (dict[str, CFG] + entry_fn)
  analysis.py  build_call_graph, check_call_depth, interprocedural_liveness
  fsm.py       FSM analyzer + FSMLayout compiler
  mcu.py       MCU analyzer + MCULayout compiler
```

| Module | Responsibility |
|---|---|
| `cfg.py` | Graph structure, traversal, loop / dominance analysis |
| `analysis.py` | Call graph + cross-function liveness |
| `fsm.py` | FSM-specific checks (sink SCCs, condition completeness) and layout |
| `mcu.py` | MCU-specific checks (dead loops, DCE) and layout |
