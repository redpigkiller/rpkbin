"""rpkbin.cfg — Control Flow Graph IR, analysis, and domain-specific tools.

Public API
----------

Core IR::

    from rpkbin.cfg import CFG, BasicBlock
    from rpkbin.cfg import Assignment, CallRef, OtherInsn, Insn
    from rpkbin.cfg import NaturalLoop, Program

Merging::

    from rpkbin.cfg import merge_cfgs
    from rpkbin.cfg import (
        CFGMergeError, DuplicateLabelError, InsnConflictError,
        EdgeConflictError, MetaConflictError,
    )
    merged = merge_cfgs(flow1, flow2, flow3)
    merged.set_entry("IDLE")

Interprocedural analysis::

    from rpkbin.cfg.analysis import (
        build_call_graph,
        check_call_depth,
        interprocedural_liveness,
        FunctionSummary,
        LivenessResult,
    )

FSM tools::

    from rpkbin.cfg import fsm
    fsm.find_dead_states(program)
    fsm.find_sink_sccs(program)
    fsm.check_conditions_complete(program)
    layout = fsm.linearize(program)          # → FSMLayout

MCU tools::

    from rpkbin.cfg import mcu
    mcu.find_dead_loops(program, exit_block="HALT")
    mcu.dead_code_elimination(cfg)
    layout = mcu.linearize(program)          # → MCULayout
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
    # IR types
    "BasicBlock",
    "Assignment",
    "CallRef",
    "OtherInsn",
    "Insn",
    # CFG
    "CFG",
    "NaturalLoop",
    # Merge
    "merge_cfgs",
    "CFGMergeError",
    "DuplicateLabelError",
    "InsnConflictError",
    "EdgeConflictError",
    "MetaConflictError",
    # Program container
    "Program",
    # Domain-specific sub-modules
    "fsm",
    "mcu",
]
