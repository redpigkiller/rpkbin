"""Program — multi-function CFG container.

A :class:`Program` bundles together all the :class:`~rpkbin.cfg.CFG` objects
that make up a single FSM or MCU program (main flow + subroutines) along with
the entry function name.

It is the primary input to the interprocedural analysis functions in
:mod:`rpkbin.cfg.analysis` and the domain-specific analyzers in
:mod:`rpkbin.cfg.fsm` and :mod:`rpkbin.cfg.mcu`.
"""

from __future__ import annotations

from typing import Literal
from dataclasses import dataclass
import networkx as nx

from .analysis import build_call_graph
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

    def __str__(self) -> str:
        """Return the same formatted text as :meth:`format`."""
        return self.format()

    def format(
        self,
        max_insns: int = 2,
        max_insn_chars: int = 35,
        *,
        show_call_graph: bool = True,
        show_call_sites: bool = True,
        show_empty_call_graph: bool = True,
        show_unreachable: bool = True,
        show_meta: bool = False,
        fn_names: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> str:
        """Return a readable multi-CFG text view of the program.

        Args:
            max_insns: Maximum instructions to preview per block in each CFG.
            max_insn_chars: Maximum characters per instruction preview.
            show_call_graph: Include a compact caller -> callee summary.
            show_call_sites: Include block ids where each call appears.
            show_empty_call_graph: If ``False``, omit the call graph section
                                   when the program has no calls.
            show_unreachable: Pass through to each CFG's ``format()``.
            show_meta: Pass through to each CFG's ``format()``.
            fn_names: Optional subset of functions to display. Order follows
                      ``program.cfgs`` insertion order.
        """
        requested = None if fn_names is None else set(fn_names)
        selected = (
            list(self.cfgs)
            if requested is None
            else [fn for fn in self.cfgs if fn in requested]
        )
        missing = (
            [] if fn_names is None else [fn for fn in fn_names if fn not in self.cfgs]
        )
        if missing:
            raise KeyError(f"Function(s) not found in program.cfgs: {missing!r}")

        lines: list[str] = [
            f"Program  {len(self.cfgs)} function{'s' if len(self.cfgs) != 1 else ''}  "
            f"entry_fn={self.entry_fn!r}"
        ]

        if show_call_graph:
            calls: list[tuple[str, str, list[str]]] = []
            call_sites: dict[tuple[str, str], list[str]] = {}
            for fn_name, cfg in self.cfgs.items():
                for bb in cfg.blocks:
                    for insn in bb.insns:
                        if isinstance(insn, CallRef):
                            key = (fn_name, insn.callee)
                            call_sites.setdefault(key, []).append(bb.id)
            calls = [
                (caller, callee, sites)
                for (caller, callee), sites in call_sites.items()
            ]
            if calls or show_empty_call_graph:
                lines.append("Call graph:")
                if calls:
                    for caller, callee, sites in calls:
                        suffix = f" at {', '.join(sites)}" if show_call_sites else ""
                        lines.append(f"  {caller} -> {callee}{suffix}")
                else:
                    lines.append("  (no calls)")

        for fn_name in selected:
            cfg = self.cfgs[fn_name]
            lines.append("")
            lines.append(f"Function {fn_name}")
            lines.append("-" * (9 + len(fn_name)))
            lines.append(
                cfg.format(
                    max_insns=max_insns,
                    max_insn_chars=max_insn_chars,
                    show_unreachable=show_unreachable,
                    show_meta=show_meta,
                )
            )

        return "\n".join(lines)

    def validate(
        self,
        *,
        include_unreachable: bool = True,
        require_entry: bool = True,
        max_call_depth: int | None = None,
    ) -> list[str]:
        """Return structural issues found across all CFGs in the program.

        The result is empty when no issues are found.  Issues are prefixed with
        the function name so they can be traced back to the source CFG.

        Args:
            include_unreachable: Report blocks unreachable from each CFG entry.
            require_entry: Pass through to :meth:`CFG.validate`.
            max_call_depth: Optional maximum allowed call depth.
        """
        issues: list[str] = []

        for fn_name, cfg in self.cfgs.items():
            for issue in cfg.validate(require_entry=require_entry):
                issues.append(f"{fn_name}: {issue}")
            if include_unreachable and cfg.entry is not None:
                for bb in cfg.find_unreachable():
                    issues.append(f"{fn_name}: block {bb.id!r} is unreachable")

        try:
            from .analysis import check_call_depth

            check_call_depth(self, max_depth=max_call_depth)
        except ValueError as exc:
            issues.append(str(exc))

        return issues

    def function_order(
        self,
        strategy: Literal[
            "entry_first",
            "insertion",
            "call_dfs",
            "bottom_up",
            "alphabetical",
            "custom",
        ] = "entry_first",
        *,
        order: "list[str] | tuple[str, ...] | None" = None,
        strict: bool = False,
    ) -> "list[str]":
        """Return a list of function names in the requested physical placement order.

        This determines the order in which CFGs are emitted in assembly-like
        output.  It does **not** call :meth:`CFG.linearize` or produce block
        layouts — it only orders the functions themselves.

        Args:
            strategy: One of:

                * ``"entry_first"`` *(default)* — entry function first, then
                  all remaining functions in insertion order.
                * ``"insertion"`` — exactly ``list(program.cfgs)``; no special
                  handling of the entry function.
                * ``"call_dfs"`` — DFS pre-order traversal of the call graph
                  starting from ``entry_fn``; functions unreachable from the
                  entry are appended in insertion order.  Handles cycles
                  (recursive calls) gracefully.  Callers appear before callees.
                * ``"bottom_up"`` — callee-before-caller order derived from the
                  call graph.  Leaf functions (those that call nothing, or
                  whose callees have already been listed) appear first; the
                  entry function appears last among reachable functions.
                  Functions not reachable from the entry are appended in
                  insertion order.  Useful for assemblers or linkers that
                  require a function to be defined before it can be called.
                  Handles cycles (recursive calls) by falling back to a
                  heuristic order within strongly-connected components.
                * ``"alphabetical"`` — all functions sorted lexicographically
                  by name.  Produces a deterministic order independent of
                  insertion history; useful for generated documentation or
                  deterministic build artefacts.
                * ``"custom"`` — caller supplies an explicit *order* list.
                  Unspecified functions are appended in insertion order unless
                  ``strict=True`` requires all functions to be listed.

            order: Required when *strategy* is ``"custom"``.  An explicit
                   sequence of function names.
            strict: Only used with ``"custom"``.  When ``True``, *order* must
                    list every function in the program; missing functions raise
                    :class:`ValueError`.

        Returns:
            An ordered :class:`list` of function name strings.

        Raises:
            ValueError: For unknown strategy, duplicate names in *order*,
                        missing functions under ``strict=True``, or ``custom``
                        without an *order*.
            KeyError:   If any name in *order* is not in ``program.cfgs``.
        """
        if strategy == "entry_first":
            rest = [fn for fn in self.cfgs if fn != self.entry_fn]
            return [self.entry_fn] + rest

        elif strategy == "insertion":
            return list(self.cfgs)

        elif strategy == "call_dfs":
            cg = build_call_graph(self)
            ordered = list(nx.dfs_preorder_nodes(cg, source=self.entry_fn))
            ordered += [fn for fn in self.cfgs if fn not in ordered]
            return ordered

        elif strategy == "bottom_up":
            cg = build_call_graph(self)
            ordered = list(nx.dfs_postorder_nodes(cg, source=self.entry_fn))
            ordered += [fn for fn in self.cfgs if fn not in set(ordered)]
            return ordered

        elif strategy == "alphabetical":
            return sorted(self.cfgs)

        elif strategy == "custom":
            if order is None:
                raise ValueError(
                    "strategy='custom' requires an explicit 'order' argument."
                )
            order_list = list(order)

            # Reject unknown names
            for fn in order_list:
                if fn not in self.cfgs:
                    raise KeyError(f"Function {fn!r} is not in program.cfgs.")

            # Reject duplicates
            seen: set[str] = set()
            for fn in order_list:
                if fn in seen:
                    raise ValueError(f"Duplicate function name {fn!r} in order list.")
                seen.add(fn)

            if strict:
                missing = [fn for fn in self.cfgs if fn not in seen]
                if missing:
                    raise ValueError(
                        f"strict=True but these functions are not in order: {missing!r}"
                    )
                return order_list

            # Non-strict: append unspecified functions in insertion order
            rest = [fn for fn in self.cfgs if fn not in seen]
            return order_list + rest

        else:
            raise ValueError(f"Unknown function order strategy: {strategy!r}")
