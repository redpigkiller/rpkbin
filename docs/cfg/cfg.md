# rpkbin.cfg — Low-Level Control Flow Toolkit

[![English](https://img.shields.io/badge/Language-English-blue.svg)](cfg.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](cfg_zh.md)

`rpkbin.cfg` helps low-level and assembly-oriented code authors organize
control flow before writing or emitting target-specific code.

It models blocks, branches, labels, and optional subroutine calls; checks common
flow-shape mistakes; prints readable text layouts; and produces deterministic
block orders for FSM, MCU, and hand-written assembly workflows.

It deliberately does not parse assembly, allocate registers, understand an ISA,
or decide calling conventions. Those target-specific choices stay in your
frontend or emitter.

---

## Quick Start

### 1. Build a Flow

A `CFG` is made of blocks and directed edges. Edge conditions are plain strings
owned by your DSL, spreadsheet parser, flowchart importer, or emitter.
`cond=None` means the default or unconditional path.

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

### 2. Check and Print It

`validate()` catches common control-flow mistakes that are easy to miss while
translating a flowchart or planning assembly labels.

```python
issues = cfg.validate()
if issues:
    for issue in issues:
        print(issue)

print(cfg.format())
```

`format()` is the primary display path: a deterministic text table of blocks,
instruction previews, and outgoing edges. It is meant to be useful in terminals,
logs, reviews, and tests.

### 3. Pick an Emission Order

`linearize()` returns reachable block ids in an order suitable for code layout.

```python
order = cfg.linearize("trace")
print(order)
```

Available strategies:

| Strategy | Use When |
|---|---|
| `rpo` | You want a stable general-purpose order that handles loops. |
| `trace` | You want branch chains kept close together for readable emitted code. |
| `topological` | You know the flow is a DAG and want a strict topological order. |

All strategies start at `start` or the CFG entry and return only reachable
blocks. Use `find_unreachable()` to inspect blocks that are not part of the
main flow.

---

## Product Shape

The package is organized around four layers:

| Layer | Purpose |
|---|---|
| Core Flow | Build, check, display, and order one control-flow graph. |
| Program Calls | Mark subroutine calls and check call depth. |
| Domain Recipes | Apply common FSM and MCU flow checks/layouts. |
| Graph Utilities | Reachability, loops, dominance, merging, and deeper analysis. |

Most users start with Core Flow. Add the other layers only when your workflow
needs them.

---

## Core Flow

### `BasicBlock`

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique graph key. Stable ids are best for tests and generated code. |
| `label` | `str | None` | Human-readable label, often the assembly label or flowchart state name. |
| `insns` | `list[Insn]` | Optional instruction annotations. Core flow features do not require them. |
| `meta` | `dict` | Source location, spreadsheet row, flowchart id, or other caller-owned data. |

You can create blocks through `CFG.add_block(...)` or pass a pre-built
`BasicBlock` object.

```python
from rpkbin.cfg import BasicBlock, CFG

cfg = CFG()
cfg.add_block("idle", label="IDLE", meta={"page": "main"})
cfg.add_block(BasicBlock("halt", label="HALT"))
```

### `CFG`

Construction:

```python
cfg = CFG()
cfg.add_block("idle", label="IDLE")
cfg.add_block("work", label="WORK")
cfg.add_edge("idle", "work", cond="go", priority=0)
cfg.set_entry("idle")
```

Access:

```python
cfg.get_block("idle")          # BasicBlock
cfg.blocks                     # list[BasicBlock], insertion order
cfg.edges                      # list[(src, dst, attrs)]
cfg.entry / cfg.exit           # BasicBlock or None
cfg.successors("idle")         # list[BasicBlock]
cfg.predecessors("work")       # list[BasicBlock]
cfg.out_edges("idle")          # sorted by priority
cfg.in_edges("work")           # sorted by priority
cfg.edge_attrs("idle", "work") # dict copy
"idle" in cfg                  # True / False
len(cfg)                       # number of blocks
```

Mutation:

```python
removed_block = cfg.remove_block("work")
removed_attrs = cfg.remove_edge("idle", "work")
clone = cfg.copy()
```

#### Safe Block Rename

Do not mutate `BasicBlock.id` directly. Use `CFG.rename_block()` so the graph
key, entry/exit markers, and edge structure stay consistent.

```python
cfg.rename_block("old", "new")
```

All incoming and outgoing edges (including self-loops) are preserved with their
original attributes. The entry and exit markers are updated automatically if
they referred to the renamed block.

Validation:

```python
issues = cfg.validate()
```

The generic validator checks:

- missing or invalid entry/exit references
- isolated blocks
- duplicate outgoing priorities
- multiple default outgoing edges
- a single conditional outgoing edge with no default path

### Edge Meaning

`add_edge(src, dst, cond=None, priority=0, **attrs)` stores a control-flow
transition.

| Attribute | Meaning |
|---|---|
| `cond=None` | Default, else, or unconditional path. |
| `cond="..."` | A caller-owned condition string. The CFG does not interpret it. |
| `priority` | Evaluation order when a block has multiple outgoing edges. Lower runs first. |
| `**attrs` | Extra caller-owned metadata. |

This keeps the CFG target-neutral: one emitter can turn `cond="start"` into an
assembly branch, while another can turn it into a table entry or HDL case item.

### Text Display

```python
print(cfg.format())
print(cfg)          # same as cfg.format()
```

`format()` orders blocks by reverse post-order when an entry is set, appends
unreachable blocks afterward, and lists outgoing edges by priority. Use it as
the first debugging view before reaching for heavier visualization.

Useful display options:

```python
print(cfg.format(start="work", show_unreachable=False))
print(cfg.format(show_meta=True))
```

---

## Program Calls

Use `Program` and `CallRef` only when you want to describe subroutine
relationships across multiple CFGs.

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
print(program)      # same as program.format()
```

`CallRef("SUB_CHECK")` means "this block calls the CFG named `SUB_CHECK`."
It does not imply any target calling convention, stack behavior, register
clobber, or return instruction. It only gives the toolkit enough information to
answer structure questions such as:

- Who calls whom?
- Is there recursion?
- Does the call depth exceed my hardware or coding-rule limit?

`Program` validates on construction:

- `cfgs` must not be empty
- `entry_fn` must exist in `cfgs`
- every `CallRef.callee` must match a key in `cfgs`

For a single flow with no call-depth checks, use `CFG` directly.

`Program.format()` accepts the same instruction preview controls as
`CFG.format()`, can show call sites, and can optionally hide the call graph or
show only selected functions:

```python
print(program.format(max_insns=4, max_insn_chars=60))
print(program.format(show_call_graph=False, fn_names=["main"]))
print(program.format(show_call_sites=False, show_meta=True))
```

`Program.validate()` collects structural issues across all CFGs:

```python
issues = program.validate(max_call_depth=2)
```

#### Function Order

`Program.function_order()` returns function names in the requested physical
placement order for assembly-like output. It does not produce block layouts or
call `CFG.linearize()`.

```python
# entry function first, then remaining in insertion order (default)
program.function_order()

# insertion order exactly as in program.cfgs
program.function_order("insertion")

# DFS pre-order of the call graph from entry_fn;
# unreachable functions are appended in insertion order
program.function_order("call_dfs")

# explicit order; unspecified functions are appended in insertion order
program.function_order("custom", order=["SUB_CHECK", "main"])

# strict=True: order must list every function, nothing is appended
program.function_order("custom", order=["SUB_CHECK", "main"], strict=True)
```

---

## Domain Recipes

The FSM and MCU modules are small, target-neutral recipes built on top of the
same CFG model.

### FSM

Use `rpkbin.cfg.fsm` when the main flow is a state machine that normally runs
forever.

```python
from rpkbin.cfg import fsm

dead_states = fsm.find_dead_states(program)
sink_cycles = fsm.find_sink_sccs(program)
missing_defaults = fsm.check_conditions_complete(program)
layout = fsm.linearize(program, strategy="rpo")
```

| Function | Returns | Use For |
|---|---|---|
| `find_dead_states(program)` | `list[BasicBlock]` | States unreachable from reset/entry. |
| `find_sink_sccs(program)` | `list[list[str]]` | Trap cycles that cannot reach reset/entry. |
| `check_conditions_complete(program)` | `list[str]` | States with only conditional exits and no default path. |
| `linearize(program, strategy="rpo")` | `FSMLayout` | Ordered state slots and priority-sorted exits. |

FSM sink SCCs are cycles from which the reset state is unreachable. Regular
loops are fine; trap cycles are suspicious.

### MCU

Use `rpkbin.cfg.mcu` when the main flow is expected to eventually reach a halt
or exit block.

```python
from rpkbin.cfg import mcu

dead_loops = mcu.find_dead_loops(program, exit_block="HALT")
removed = mcu.dead_code_elimination(program.main)
layout = mcu.linearize(program, strategy="trace")
```

| Function | Returns | Use For |
|---|---|---|
| `find_dead_loops(program, exit_block=None)` | `list[list[str]]` | Reachable cycles with no path to halt/exit. |
| `dead_code_elimination(cfg, start=None)` | `list[BasicBlock]` | Remove unreachable blocks in place. |
| `linearize(program, strategy="rpo")` | `MCULayout` | Ordered slots with exit-edge and fallthrough hints. |

`MCULayout` does not emit assembly. It tells your emitter which block comes
next, which outgoing edge is physical fallthrough, and when an unconditional
jump is needed. Branch mnemonics, condition inversion, and target-specific
instruction selection remain the emitter's job.

```python
for slot in layout.slots:
    emit_block(slot.block)
    for edge in slot.exits:
        if edge.cond is not None:
            emit_conditional_branch(edge.cond, edge.target)
    if slot.needs_jump:
        emit_jump(slot.jump_target)
```

---

## Structural Diff

Use `rpkbin.cfg.diff` to compare two CFGs or two Programs structurally.
This compares graph structure and caller-owned annotations as stored.
It does **not** check semantic equivalence.

```python
from rpkbin.cfg import diff_cfgs, cfg_structurally_equal
from rpkbin.cfg import diff_programs, program_structurally_equal

# CFG comparison
result = diff_cfgs(old_cfg, new_cfg)
if result.has_changes():
    print("added blocks:",   result.added_blocks)
    print("removed blocks:", result.removed_blocks)
    print("changed blocks:", result.changed_blocks)   # dict[key, BlockDelta]
    print("added edges:",    result.added_edges)
    print("removed edges:", result.removed_edges)
    print("changed edges:", result.changed_edges)     # dict[(src,dst), EdgeDelta]
    print("added calls:",   result.added_calls)       # CallRef relationships
    print("removed calls:", result.removed_calls)

# Convenience boolean
if not cfg_structurally_equal(old_cfg, new_cfg):
    ...

# Program comparison
result = diff_programs(old_program, new_program)
if result.has_changes():
    print("entry changed:",      result.entry_fn_changed)
    print("added functions:",    result.added_functions)
    print("removed functions:",  result.removed_functions)
    print("changed functions:",  result.changed_functions)  # dict[name, CFGDiffResult]
```

Key options for `diff_cfgs` / `diff_programs`:

| Option | Default | Effect |
|---|---|---|
| `align_by` | `"id"` | `"label"` matches blocks by `block.label` instead of `block.id`. |
| `compare_insns` | `True` | Set `False` to ignore instruction-list differences. |
| `compare_meta` | `False` | Set `True` to include `meta` dict differences. |
| `compare_edge_attrs` | `True` | Set `False` to ignore edge attribute differences. |

`CallRef` relationships are **always** compared regardless of `compare_insns`.
They represent structural caller/callee relationships, not instruction content.

---

## Graph Utilities

These helpers are available when you need deeper graph inspection. They are not
required for the common build/check/layout workflow.

### Traversal and Reachability

```python
list(cfg.dfs())
list(cfg.bfs())
cfg.reverse_postorder()
cfg.can_reach("entry", "done")
cfg.find_unreachable()
cfg.find_sccs()
```

### Loops and Dominance

```python
cfg.find_back_edges()
cfg.find_natural_loops()
cfg.dominators()
cfg.post_dominators(exit_node="done")
cfg.dominator_tree()
```

### Merging Labeled Flows

`merge_cfgs()` combines multiple CFGs by unifying blocks that share the same
non-`None` label. This is useful when separate extracted flow fragments use
labels as connection points.

```python
from rpkbin.cfg import merge_cfgs

merged = merge_cfgs(flow1, flow2)
merged.set_entry("ENTRY")
```

Rules:

- matching labeled blocks are unified
- blocks with instructions override placeholder blocks with the same label
- identical connecting edges are deduplicated
- conflicting labels, instructions, metadata, or edge attributes raise `CFGMergeError`
- the merged CFG starts with no entry or exit; set them after merging

### Instruction Annotations and Liveness

Instruction annotations are optional. They are used only by analyses that need
def/use information.

| Type | Meaning |
|---|---|
| `Assignment(lhs, rhs, raw="")` | Defines `lhs` and uses the variables in `rhs`. Constants should be excluded by the frontend. |
| `CallRef(callee, raw="")` | Marks a call to another CFG in a `Program`. |
| `OtherInsn(raw="", defs=set(), uses=set())` | Caller-provided def/use annotation for anything else. |

```python
from rpkbin.cfg import Assignment, OtherInsn
from rpkbin.cfg.analysis import interprocedural_liveness

bb = cfg.get_block("work")
bb.insns.append(Assignment("acc", ["sample"], raw="acc = sample"))
bb.insns.append(OtherInsn(raw="CUSTOM", defs={"flag"}, uses={"acc"}))

results = interprocedural_liveness(program)
live = results["main"].live_in["work"]
```

Liveness is structural and annotation-driven. It does not understand ISA
register aliases, flags, memory aliasing, stack conventions, or implicit
clobbers unless your frontend records them in `defs` and `uses`.

---

## Non-Goals

`rpkbin.cfg` intentionally stays small and target-neutral. It does not:

- parse assembly source
- generate final target assembly by itself
- allocate registers
- model ISA-specific flags, memory aliasing, stacks, or calling conventions
- optimize branch forms or instruction selection
- replace LLVM, a compiler framework, or `networkx`

The intended boundary is simple: `rpkbin.cfg` makes the control flow clear,
checkable, and ordered; your domain code decides what each block and condition
means on the target.

---

## Module Layout

```text
rpkbin/cfg/
  block.py     BasicBlock plus optional instruction annotations
  cfg.py       CFG structure, validation, layout, and graph utilities
  program.py   Program container for multiple CFGs
  analysis.py  call graph, call depth, and liveness analysis
  diff.py      structural diff and equality for CFG / Program
  fsm.py       FSM-oriented checks and layout
  mcu.py       MCU-oriented checks and layout
```
