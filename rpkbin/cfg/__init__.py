"""rpkbin.cfg - low-level control-flow modeling and layout helpers.

``rpkbin.cfg`` is a small toolkit for assembly-like flows, FSM state machines,
MCU branch layouts, and generated low-level code. It gives callers explicit
blocks, edges, labels, validation, readable text formatting, deterministic
layout order, optional call-depth checks, and domain recipes.

The package is target-neutral: it does not parse assembly, allocate registers,
model an ISA, or choose final branch instructions. Frontends and emitters keep
owning those target-specific decisions.

Public API
----------

Core flow::

    from rpkbin.cfg import CFG, BasicBlock
    cfg = CFG()
    cfg.add_block("IDLE", label="IDLE")
    cfg.add_block("WORK", label="WORK")
    cfg.add_edge("IDLE", "WORK", cond="go", priority=0)
    cfg.set_entry("IDLE")
    print(cfg.format())
    order = cfg.linearize("trace")

Program calls::

    from rpkbin.cfg import CallRef, Program
    from rpkbin.cfg.analysis import build_call_graph, check_call_depth

    main.get_block("FETCH").insns.append(CallRef("SUB_CHECK"))
    program = Program({"main": main, "SUB_CHECK": sub})
    depth = check_call_depth(program, max_depth=2)

Domain recipes::

    from rpkbin.cfg import fsm, mcu
    fsm.find_dead_states(program)
    fsm.find_sink_sccs(program)
    fsm.check_conditions_complete(program)
    fsm_layout = fsm.linearize(program)

    mcu.find_dead_loops(program, exit_block="HALT")
    mcu.dead_code_elimination(program.main)
    mcu_layout = mcu.linearize(program)

Graph utilities and deeper analysis::

    from rpkbin.cfg import NaturalLoop, merge_cfgs
    from rpkbin.cfg import (
        CFGMergeError, DuplicateLabelError, InsnConflictError,
        EdgeConflictError, MetaConflictError,
    )
    from rpkbin.cfg import Assignment, OtherInsn, Insn
    from rpkbin.cfg.analysis import (
        interprocedural_liveness,
        FunctionSummary,
        LivenessResult,
    )
"""

from .block import Assignment, CallRef, Insn, OtherInsn, BasicBlock
from .cfg import (
    CFG,
    NaturalLoop,
    CFGMergeError,
    DuplicateLabelError,
    InsnConflictError,
    EdgeConflictError,
    MetaConflictError,
    merge_cfgs,
)
from .program import Program
from . import fsm
from . import mcu

__all__ = [
    # Core flow
    "BasicBlock",
    "CFG",
    # Program calls and optional instruction annotations
    "CallRef",
    "Program",
    "Assignment",
    "OtherInsn",
    "Insn",
    # Graph utilities
    "NaturalLoop",
    "merge_cfgs",
    "CFGMergeError",
    "DuplicateLabelError",
    "InsnConflictError",
    "EdgeConflictError",
    "MetaConflictError",
    # Domain recipe modules
    "fsm",
    "mcu",
]
