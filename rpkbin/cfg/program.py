"""Program — multi-function CFG container.

A :class:`Program` bundles together all the :class:`~rpkbin.cfg.CFG` objects
that make up a single FSM or MCU program (main flow + subroutines) along with
the entry function name.

It is the primary input to the interprocedural analysis functions in
:mod:`rpkbin.cfg.analysis` and the domain-specific analyzers in
:mod:`rpkbin.cfg.fsm` and :mod:`rpkbin.cfg.mcu`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .block import CallRef
from .cfg import CFG


@dataclass
class Program:
    """Container for all CFGs that make up a single FSM or MCU program.

    Attributes:
        cfgs:     Mapping from function name to its :class:`CFG`.
                  The main flow is stored under the key given by *entry_fn*.
                  Subroutines are stored under their own names.
                  Every :class:`~rpkbin.cfg.block.CallRef` in any CFG's
                  instruction list must reference a key present in this dict.
        entry_fn: Name of the program entry-point function (default: ``"main"``).

    Example::

        main_cfg = CFG()
        main_cfg.add_block("IDLE",  label="IDLE",  insns=[Assignment("x", [])])
        main_cfg.add_block("FETCH", label="FETCH")
        main_cfg.add_edge("IDLE", "FETCH", cond="start", priority=0)
        main_cfg.set_entry("IDLE")

        sub_cfg = CFG()
        sub_cfg.add_block("sub_body", insns=[Assignment("y", ["x"])])
        sub_cfg.add_block("sub_ret")
        sub_cfg.add_edge("sub_body", "sub_ret")
        sub_cfg.set_entry("sub_body")
        sub_cfg.set_exit("sub_ret")

        program = Program(
            cfgs={"main": main_cfg, "SUB_CHECK": sub_cfg},
            entry_fn="main",
        )
    """

    cfgs: dict[str, CFG]
    entry_fn: str = "main"

    def __post_init__(self) -> None:
        if not self.cfgs:
            raise ValueError("Program.cfgs must not be empty.")
        if self.entry_fn not in self.cfgs:
            raise KeyError(
                f"Entry function {self.entry_fn!r} not found in program.cfgs."
            )
        for fn_name, cfg in self.cfgs.items():
            for bb in cfg.blocks:
                for insn in bb.insns:
                    if isinstance(insn, CallRef) and insn.callee not in self.cfgs:
                        raise KeyError(
                            f"CallRef to unknown function {insn.callee!r} "
                            f"in block {bb.id!r} of function {fn_name!r}."
                        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def main(self) -> CFG:
        """Return the entry-point CFG (shorthand for ``cfgs[entry_fn]``)."""
        return self.cfgs[self.entry_fn]

    def __contains__(self, fn_name: str) -> bool:
        return fn_name in self.cfgs

    def __getitem__(self, fn_name: str) -> CFG:
        return self.cfgs[fn_name]

    def __iter__(self):
        return iter(self.cfgs)

    def __len__(self) -> int:
        return len(self.cfgs)

    def __repr__(self) -> str:
        fns = list(self.cfgs.keys())
        return f"Program(entry_fn={self.entry_fn!r}, functions={fns!r})"
