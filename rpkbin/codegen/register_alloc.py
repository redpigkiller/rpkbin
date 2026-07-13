"""Register allocator for the codegen pipeline.

Assigns physical registers to all ``Var`` / ``VReg`` nodes in a
``lir.Function``.  The allocator is driven entirely by a ``RegisterModel``
Protocol; no MCU-specific details are hardcoded here.

Register allocation runs whenever a codegen pipeline receives a
``RegisterModel``.  Spill code remains target-dependent and must be validated
against that target's memory model.

Algorithm: greedy graph-colouring with basic spill
--------------------------------------------------
1. Collect all named values (Var / VReg names) appearing in the function.
2. Compute block live-in/live-out sets and build an interference graph.
3. Greedy colour: hinted variables first (sorted by hint name), then
   unhinted variables (sorted by name for determinism).
4. If no compatible register is available, mark var as *spilled*:
   - spilled dict: var_name → SpillSlot  (not placed in assignment)
   - If no spill slot available → raise RegisterAllocationError.
5. Insert spill code:
   - After each definition of a spilled var: insert MemStore to slot.
   - Before each use of a spilled var: insert MemLoad from slot + replace ref.
6. Apply the assignment: replace every non-spilled Var/VReg with
   its physical register name.

Spill addressing currently uses ``Const(slot.address, 16)``; register models
must expose compatible spill slots.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import lir
from .target import RegisterModel, can_allocate, registers_overlap, is_physical_register


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class RegisterAllocationError(Exception):
    """Raised when allocation is impossible and no spill slots are available."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def allocate_registers(
    func: lir.Function,
    register_model: RegisterModel,
    var_hints: Dict[str, str] | None = None,
) -> Tuple[lir.Function, Dict[str, str]]:
    """Assign physical registers to all Var / VReg nodes in *func*.

    Parameters
    ----------
    func:
        LIR function after rewriting (but before isel).
    register_model:
        Protocol implementation describing the physical register file.
    var_hints:
        Optional extra hint map: var_name → preferred_reg.
        Merged with VReg.hint values found in the LIR (LIR hints take
        priority over entries in this dict).

    Returns
    -------
    (new_func, assignment)
        ``new_func`` has the same structure as *func* but with all
        non-spilled Var/VReg names replaced by physical register names,
        and spill code (MemStore/MemLoad) inserted for spilled vars.
        ``assignment`` maps only non-spilled var_name → physical_reg.

    Raises
    ------
    RegisterAllocationError
        If more simultaneously-live variables exist than available
        registers AND no spill slots are available.
    """
    allocator = _Allocator(func, register_model, var_hints or {})
    return allocator.run()


def allocate_fragment_registers(
    fragment: lir.Fragment,
    register_model: RegisterModel,
) -> tuple[lir.Fragment, Dict[str, str]]:
    """Allocate Fragment locals from ``scratch_regs`` without spilling."""
    _validate_fragment_register_contract(fragment, register_model)
    constrained = _FragmentRegisterModel(register_model, fragment.scratch_regs)
    function = lir.Function(
        fragment.name,
        tuple(lir.VReg(b.name, b.width, b.reg) for b in fragment.bindings),
        fragment.blocks,
    )
    allocated, assignment = allocate_registers(function, constrained)
    allocated_fragment = lir.Fragment(
        fragment.name, fragment.bindings, fragment.scratch_regs, allocated.blocks
    )
    unresolved = _fragment_unresolved_local_names(allocated_fragment)
    if unresolved:
        raise RegisterAllocationError(
            "Fragment locals were not fully allocated: "
            + ", ".join(repr(name) for name in sorted(unresolved))
        )
    return allocated_fragment, assignment


def _validate_fragment_register_contract(
    fragment: lir.Fragment,
    register_model: RegisterModel,
) -> None:
    binding_regs: dict[str, str] = {}
    seen_bindings: list[lir.FragmentBinding] = []
    seen_binding_regs: list[str] = []
    phased_pair_reg: str | None = None
    for binding in fragment.bindings:
        binding_regs[binding.name] = binding.reg
        if not is_physical_register(register_model, binding.reg):
            raise RegisterAllocationError(
                f"binding '{binding.name}' register '{binding.reg}' is not a valid physical register."
            )
        if not can_allocate(register_model, binding.reg, binding.width):
            raise RegisterAllocationError(
                f"binding register '{binding.reg}' cannot hold {binding.width}-bit binding '{binding.name}'."
            )
        for other in seen_bindings:
            same_reg = binding.reg == other.reg
            overlaps = same_reg or registers_overlap(
                register_model, binding.reg, other.reg
            )
            if not overlaps:
                continue
            if same_reg and {binding.mode, other.mode} == {"in", "out"} and phased_pair_reg is None:
                phased_pair_reg = binding.reg
                continue
            if same_reg:
                raise RegisterAllocationError(
                    f"binding register '{binding.reg}' overlaps with an existing binding; exact sharing is only allowed for exactly one 'in' binding and one 'out' binding."
                )
            raise RegisterAllocationError(
                f"binding register '{binding.reg}' overlaps with '{other.reg}'."
            )
        seen_bindings.append(binding)
        seen_binding_regs.append(binding.reg)

    seen_scratch: list[str] = []
    for scratch in fragment.scratch_regs:
        if not is_physical_register(register_model, scratch):
            raise RegisterAllocationError(
                f"scratch register '{scratch}' is not a valid physical register."
            )
        for other in seen_scratch:
            if registers_overlap(register_model, scratch, other):
                raise RegisterAllocationError(
                    f"scratch register '{scratch}' overlaps with scratch register '{other}'."
                )
        for binding_reg in seen_binding_regs:
            if registers_overlap(register_model, scratch, binding_reg):
                raise RegisterAllocationError(
                    f"scratch register '{scratch}' overlaps with binding register '{binding_reg}'."
                )
        seen_scratch.append(scratch)

    function = lir.Function(
        fragment.name,
        tuple(lir.VReg(b.name, b.width, b.reg) for b in fragment.bindings),
        fragment.blocks,
    )
    names_hints = _collect_names(function, {})
    scratch_set = set(fragment.scratch_regs)
    for var_name, hint in names_hints.items():
        if hint is None:
            continue
        width = _find_width(function, var_name)
        binding_reg = binding_regs.get(var_name)
        if binding_reg is not None:
            if hint != binding_reg:
                raise RegisterAllocationError(
                    f"binding '{var_name}' fixed register '{hint}' conflicts with declared binding register '{binding_reg}'."
                )
        elif hint not in scratch_set:
            raise RegisterAllocationError(
                f"local fixed register '{hint}' for '{var_name}' is not in fragment scratch_regs."
            )
        if not can_allocate(register_model, hint, width):
            raise RegisterAllocationError(
                f"Fixed register '{hint}' cannot hold {width}-bit value '{var_name}'."
            )


def _fragment_unresolved_local_names(fragment: lir.Fragment) -> set[str]:
    binding_names = {binding.name for binding in fragment.bindings}
    allowed_regs = {binding.reg for binding in fragment.bindings} | set(fragment.scratch_regs)
    unresolved: set[str] = set()

    def _record(node) -> None:
        if not isinstance(node, (lir.Var, lir.VReg)):
            return
        if getattr(node, "width", 1) == 0:
            return
        if node.name not in binding_names and node.name not in allowed_regs:
            unresolved.add(node.name)

    for block in fragment.blocks:
        for stmt in block.statements:
            _scan_stmt_for_names(stmt, _record)
        _scan_terminator_for_names(block.terminator, _record)
    return unresolved


class _FragmentRegisterModel:
    def __init__(self, base: RegisterModel, scratch_regs: Sequence[str]) -> None:
        self._base = base
        self._scratch_regs = tuple(scratch_regs)

    def allocatable_registers(self) -> Sequence[str]:
        return self._scratch_regs

    def is_physical_register(self, reg: str) -> bool:
        """Delegate to the base model.  Scratch regs are physical by
        construction (already validated at fragment-decl time), but binding
        regs and any other registers asked about should still route through
        the base model's physical-register knowledge.
        """
        return is_physical_register(self._base, reg)

    def fixed_register_hints(self) -> bool:
        return True

    def register_width(self, reg: str) -> int:
        return self._base.register_width(reg)

    def can_allocate(self, reg: str, width: int) -> bool:
        return can_allocate(self._base, reg, width)

    def register_aliases(self):
        return self._base.register_aliases()

    def registers_overlap(self, lhs: str, rhs: str) -> bool:
        return registers_overlap(self._base, lhs, rhs)

    def spill_slots(self) -> Sequence[lir.SpillSlot]:
        return ()


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------

class _Allocator:
    def __init__(
        self,
        func: lir.Function,
        rm: RegisterModel,
        extra_hints: Dict[str, str],
    ) -> None:
        self._func = func
        self._rm = rm
        self._extra_hints = extra_hints
        self._fresh_counter = 0
        self._fixed_hints = getattr(self._rm, "fixed_register_hints", lambda: False)()

    def _fresh_id(self) -> int:
        n = self._fresh_counter
        self._fresh_counter += 1
        return n

    def run(self) -> Tuple[lir.Function, Dict[str, str]]:
        # Step 1: collect all named values and their hints
        names_hints = _collect_names(self._func, self._extra_hints)
        self._validate_fixed_hints(names_hints)

        # Step 2: build interference graph
        physical_regs = set(self._rm.allocatable_registers())
        physical_regs.update(hint for hint in names_hints.values() if hint is not None)
        interference, forbidden = _build_interference(self._func, physical_regs)

        # Step 3: greedy colouring → (assignment, spilled)
        assignment, spilled = self._greedy_colour(
            names_hints, interference, forbidden
        )
        if self._fixed_hints:
            fixed_spills = sorted(
                name for name, hint in names_hints.items()
                if hint is not None and name in spilled
            )
            if fixed_spills:
                raise RegisterAllocationError(
                    "Fixed-register values cannot be spilled: "
                    + ", ".join(repr(name) for name in fixed_spills)
                )

        # Step 4a: insert spill store/load code if any vars were spilled
        func_after_spill = self._func
        if spilled:
            func_after_spill = self._insert_spill_code(
                self._func, spilled, assignment
            )

        # Step 4b: replace non-spilled var names with physical registers
        new_func = _apply_assignment(func_after_spill, assignment)
        return new_func, assignment

    def _validate_fixed_hints(
        self,
        names_hints: Dict[str, Optional[str]],
    ) -> None:
        if not self._fixed_hints:
            return
        for var_name, hint in names_hints.items():
            if hint is None:
                continue
            width = _find_width(self._func, var_name)
            if not is_physical_register(self._rm, hint):
                raise RegisterAllocationError(
                    f"Fixed register '{hint}' for '{var_name}' is not a valid physical register."
                )
            if not can_allocate(self._rm, hint, width):
                raise RegisterAllocationError(
                    f"Fixed register '{hint}' cannot hold {width}-bit value '{var_name}'."
                )

    # ------------------------------------------------------------------
    # Step 3: greedy colouring
    # ------------------------------------------------------------------

    def _greedy_colour(
        self,
        names_hints: Dict[str, Optional[str]],
        interference: Dict[str, Set[str]],
        forbidden: Dict[str, Set[str]],
    ) -> Tuple[Dict[str, str], Dict[str, lir.SpillSlot]]:
        """Assign physical registers greedily.

        Returns (assignment, spilled):
          assignment: var_name → physical_reg   (non-spilled vars)
          spilled:    var_name → SpillSlot       (spilled vars)
        """
        all_regs: List[str] = list(self._rm.allocatable_registers())

        # Sort: hinted first
        hinted = sorted(
            [(n, h) for n, h in names_hints.items() if h is not None],
            key=lambda x: (x[1], x[0]),
        )
        unhinted = sorted(
            [(n, None) for n, h in names_hints.items() if h is None],
            key=lambda x: x[0],
        )
        ordered = hinted + unhinted

        assignment: Dict[str, str] = {}
        spilled: Dict[str, lir.SpillSlot] = {}
        spill_slots = list(self._rm.spill_slots())
        spill_idx = 0

        for var_name, hint in ordered:
            var_width = _find_width(self._func, var_name)

            # Determine which physical regs are blocked by conflicting vars
            conflicting_regs: Set[str] = set(forbidden.get(var_name, ()))
            for conf_name in interference.get(var_name, set()):
                if conf_name in assignment:
                    phys = assignment[conf_name]
                    conflicting_regs.add(phys)

            # Filter candidates: width-compatible and not conflicting
            pool = [hint] if hint is not None and self._fixed_hints else all_regs
            candidates = [
                r for r in pool
                if can_allocate(self._rm, r, var_width)
                and not any(
                    registers_overlap(self._rm, r, conflict)
                    for conflict in conflicting_regs
                )
            ]

            if candidates:
                assignment[var_name] = (
                    hint if hint is not None and hint in candidates else candidates[0]
                )
            elif hint is not None and self._fixed_hints:
                conflicts = sorted(
                    conflict for conflict in conflicting_regs
                    if registers_overlap(self._rm, hint, conflict)
                )
                detail = (
                    f" conflicts with {', '.join(repr(conflict) for conflict in conflicts)}"
                    if conflicts else
                    " is unavailable"
                )
                raise RegisterAllocationError(
                    f"Fixed register '{hint}' for '{var_name}'{detail}."
                )
            elif spill_idx < len(spill_slots):
                # Mark as spilled — NOT entered in assignment
                spilled[var_name] = spill_slots[spill_idx]
                spill_idx += 1
            else:
                raise RegisterAllocationError(
                    f"No register available for '{var_name}' and no spill slots provided."
                )

        return assignment, spilled

    # ------------------------------------------------------------------
    # Step 4a: insert spill store/load code
    # ------------------------------------------------------------------

    def _insert_spill_code(
        self,
        func: lir.Function,
        spilled: Dict[str, lir.SpillSlot],
        assignment: Dict[str, str],
    ) -> lir.Function:
        """Insert MemStore after each def of a spilled var, MemLoad before each use.

        Spilled vars are replaced with fresh temporaries; those temporaries
        will later be handled by _apply_assignment (they will have been given
        fresh names that map through the assignment dict).

        The temp register for spill operations is picked as the first
        width-compatible register — live range is a single statement so
        conflicts are impossible within one expression tree.
        """
        all_regs: List[str] = list(self._rm.allocatable_registers())

        # Pre-compute the width of each spilled var
        spilled_widths: Dict[str, int] = {
            name: _find_width(func, name) for name in spilled
        }

        def _pick_temp_reg(width: int) -> str:
            for r in all_regs:
                if can_allocate(self._rm, r, width):
                    return r
            return all_regs[0]

        def _fresh_temp(base: str, width: int) -> str:
            return f"__spill_reload_{base}_{self._fresh_id()}"

        def _used_before_def(block: lir.Block, name: str) -> bool:
            for stmt in block.statements:
                stmt_defs, stmt_uses = _stmt_defs_uses(stmt)
                if name in stmt_uses:
                    return True
                if name in stmt_defs:
                    return False
            return name in _term_uses(block.terminator)

        param_sources = {
            param.name: (
                param.hint
                if isinstance(param, lir.VReg) and param.hint is not None
                else self._extra_hints.get(param.name)
            )
            for param in func.params
        }

        new_blocks = []
        for block in func.blocks:
            new_stmts: List = []

            if block is func.blocks[0]:
                for name, source in param_sources.items():
                    if name not in spilled or not _used_before_def(block, name):
                        continue
                    if source is None:
                        raise RegisterAllocationError(
                            f"Cannot spill live-in parameter '{name}' without a register hint."
                        )
                    slot = spilled[name]
                    new_stmts.append(
                        lir.MemStore(
                            lir.Const(slot.address, 16),
                            lir.Var(source, spilled_widths[name]),
                        )
                    )

            for stmt in block.statements:
                # ── Definition point: Assign whose target is a spilled var ──
                if (isinstance(stmt, lir.Assign)
                        and isinstance(stmt.target, (lir.Var, lir.VReg))
                        and stmt.target.name in spilled):
                    spill_name = stmt.target.name
                    slot = spilled[spill_name]
                    width = spilled_widths[spill_name]
                    temp_reg = _pick_temp_reg(width)

                    # 1. Execute the assignment into a temp register name
                    #    We give the temp a fresh var name so assignment can map it.
                    #    But actually, we can use the physical reg name directly here
                    #    since we know it and it's just one statement.
                    new_stmts.append(lir.Assign(
                        target=lir.Var(temp_reg, width),
                        value=stmt.value,
                    ))
                    # 2. Immediately store temp to spill slot
                    new_stmts.append(lir.MemStore(
                        addr=lir.Const(slot.address, 16),
                        value=lir.Var(temp_reg, width),
                    ))
                    # Record the temp_reg in assignment so _apply_assignment
                    # leaves it as-is (it's already a physical reg name).
                    if temp_reg not in assignment.values():
                        assignment[temp_reg] = temp_reg
                    else:
                        # temp_reg is already in use; identity mapping is fine
                        assignment.setdefault(temp_reg, temp_reg)

                elif isinstance(stmt, lir.CallAssign):
                    load_map: Dict[str, str] = {}
                    for spill_name, slot in spilled.items():
                        if any(_expr_contains_var(arg, spill_name) for arg in stmt.call.args):
                            width = spilled_widths[spill_name]
                            temp_reg = _pick_temp_reg(width)
                            fresh_name = _fresh_temp(spill_name, width)
                            new_stmts.append(lir.Assign(
                                target=lir.Var(fresh_name, width),
                                value=lir.MemLoad(
                                    addr=lir.Const(slot.address, 16),
                                    width=width,
                                ),
                            ))
                            assignment[fresh_name] = temp_reg
                            load_map[spill_name] = fresh_name

                    if load_map:
                        stmt = _replace_spilled_in_stmt(stmt, load_map)

                    rewritten_targets = []
                    spill_stores = []
                    for target in stmt.targets:
                        if not isinstance(target, (lir.Var, lir.VReg)) or target.name not in spilled:
                            rewritten_targets.append(target)
                            continue
                        slot = spilled[target.name]
                        width = spilled_widths[target.name]
                        temp_reg = _pick_temp_reg(width)
                        fresh_name = _fresh_temp(target.name, width)
                        assignment[fresh_name] = temp_reg
                        if isinstance(target, lir.VReg):
                            rewritten_targets.append(
                                lir.VReg(
                                    name=fresh_name,
                                    width=width,
                                    hint=target.hint,
                                )
                            )
                        else:
                            rewritten_targets.append(lir.Var(fresh_name, width))
                        spill_stores.append(
                            lir.MemStore(
                                addr=lir.Const(slot.address, 16),
                                value=lir.Var(fresh_name, width),
                            )
                        )

                    new_stmts.append(
                        lir.CallAssign(
                            targets=tuple(rewritten_targets),
                            call=stmt.call,
                            abi_return_regs=stmt.abi_return_regs,
                        )
                    )
                    new_stmts.extend(spill_stores)

                else:
                    # ── Use point: check if any spilled var appears in stmt ──
                    load_map: Dict[str, str] = {}  # spill_name → fresh_var_name

                    for spill_name, slot in spilled.items():
                        if _stmt_contains_var(stmt, spill_name):
                            width = spilled_widths[spill_name]
                            temp_reg = _pick_temp_reg(width)
                            fresh_name = _fresh_temp(spill_name, width)
                            # Insert load before this stmt
                            new_stmts.append(lir.Assign(
                                target=lir.Var(fresh_name, width),
                                value=lir.MemLoad(
                                    addr=lir.Const(slot.address, 16),
                                    width=width,
                                ),
                            ))
                            # Map the fresh temp name → physical temp reg
                            assignment[fresh_name] = temp_reg
                            load_map[spill_name] = fresh_name

                    if load_map:
                        stmt = _replace_spilled_in_stmt(stmt, load_map)
                    new_stmts.append(stmt)

            # Handle terminator: check for spilled var uses in terminator
            term = block.terminator
            load_map_term: Dict[str, str] = {}
            for spill_name, slot in spilled.items():
                if _term_contains_var(term, spill_name):
                    width = spilled_widths[spill_name]
                    temp_reg = _pick_temp_reg(width)
                    fresh_name = _fresh_temp(spill_name, width)
                    new_stmts.append(lir.Assign(
                        target=lir.Var(fresh_name, width),
                        value=lir.MemLoad(
                            addr=lir.Const(slot.address, 16),
                            width=width,
                        ),
                    ))
                    assignment[fresh_name] = temp_reg
                    load_map_term[spill_name] = fresh_name

            if load_map_term:
                term = _replace_spilled_in_term(term, load_map_term)

            new_blocks.append(lir.Block(
                label=block.label,
                statements=tuple(new_stmts),
                terminator=term,
            ))

        return lir.Function(
            name=func.name,
            params=func.params,
            blocks=tuple(new_blocks),
        )


# ---------------------------------------------------------------------------
# Spill helpers: contains + replace
# ---------------------------------------------------------------------------

def _stmt_contains_var(stmt, var_name: str) -> bool:
    """Return True if *stmt*'s expressions reference *var_name*."""
    if isinstance(stmt, lir.Assign):
        return _expr_contains_var(stmt.value, var_name)
    elif isinstance(stmt, lir.CallStmt):
        return any(_expr_contains_var(arg, var_name) for arg in stmt.call.args)
    elif isinstance(stmt, lir.CallAssign):
        return any(_expr_contains_var(arg, var_name) for arg in stmt.call.args)
    elif isinstance(stmt, lir.MemStore):
        return (
            _expr_contains_var(stmt.addr, var_name)
            or _expr_contains_var(stmt.value, var_name)
        )
    elif isinstance(stmt, lir.BitOp):
        return _expr_contains_var(stmt.var, var_name)
    return False


def _term_contains_var(term, var_name: str) -> bool:
    """Return True if *term* references *var_name*."""
    if isinstance(term, lir.BrIf):
        return _expr_contains_var(term.cond, var_name)
    elif isinstance(term, lir.BrCmp):
        return (
            _expr_contains_var(term.left, var_name)
            or _expr_contains_var(term.right, var_name)
        )
    elif isinstance(term, lir.Return):
        return _expr_contains_var(term.value, var_name)
    elif isinstance(term, lir.MultiReturn):
        return any(_expr_contains_var(v, var_name) for v in term.values)
    return False


def _expr_contains_var(expr, var_name: str) -> bool:
    """Return True if *expr* references *var_name*."""
    if isinstance(expr, (lir.Var, lir.VReg)):
        return expr.name == var_name
    elif isinstance(expr, lir.BinOp):
        return (
            _expr_contains_var(expr.left, var_name)
            or _expr_contains_var(expr.right, var_name)
        )
    elif isinstance(expr, lir.Cmp):
        return (
            _expr_contains_var(expr.left, var_name)
            or _expr_contains_var(expr.right, var_name)
        )
    elif isinstance(expr, lir.Extend):
        return _expr_contains_var(expr.value, var_name)
    elif isinstance(expr, lir.MemLoad):
        return _expr_contains_var(expr.addr, var_name)
    elif isinstance(expr, lir.BitOp):
        return _expr_contains_var(expr.var, var_name)
    elif isinstance(expr, lir.Call):
        return any(_expr_contains_var(a, var_name) for a in expr.args)
    return False


def _replace_spilled_in_expr(expr, load_map: Dict[str, str]):
    """Replace Var/VReg names in *load_map* with their fresh reload names."""
    if isinstance(expr, (lir.Var, lir.VReg)):
        if expr.name in load_map:
            return lir.Var(load_map[expr.name], expr.width)
        return expr
    elif isinstance(expr, lir.BinOp):
        return lir.BinOp(
            expr.op,
            _replace_spilled_in_expr(expr.left, load_map),
            _replace_spilled_in_expr(expr.right, load_map),
            expr.width,
        )
    elif isinstance(expr, lir.Cmp):
        return lir.Cmp(
            expr.op,
            _replace_spilled_in_expr(expr.left, load_map),
            _replace_spilled_in_expr(expr.right, load_map),
            expr.width,
            expr.signed,
        )
    elif isinstance(expr, lir.Extend):
        return lir.Extend(
            kind=expr.kind,
            value=_replace_spilled_in_expr(expr.value, load_map),
            width=expr.width,
        )
    elif isinstance(expr, lir.MemLoad):
        return lir.MemLoad(
            addr=_replace_spilled_in_expr(expr.addr, load_map),
            width=expr.width,
            volatile=expr.volatile,
        )
    elif isinstance(expr, lir.BitOp):
        return lir.BitOp(
            kind=expr.kind,
            var=_replace_spilled_in_expr(expr.var, load_map),
            bit_idx=expr.bit_idx,
        )
    elif isinstance(expr, lir.Call):
        return lir.Call(
            name=expr.name,
            args=tuple(_replace_spilled_in_expr(a, load_map) for a in expr.args),
            arg_regs=expr.arg_regs,
            return_regs=expr.return_regs,
            clobbers=expr.clobbers,
        )
    return expr


def _replace_spilled_in_stmt(stmt, load_map: Dict[str, str]):
    """Replace all spilled var references in *stmt* with fresh reload names."""
    if isinstance(stmt, lir.Assign):
        return lir.Assign(
            target=stmt.target,
            value=_replace_spilled_in_expr(stmt.value, load_map),
        )
    elif isinstance(stmt, lir.CallStmt):
        return lir.CallStmt(
            call=lir.Call(
                name=stmt.call.name,
                args=tuple(_replace_spilled_in_expr(a, load_map) for a in stmt.call.args),
                arg_regs=stmt.call.arg_regs,
                return_regs=stmt.call.return_regs,
                clobbers=stmt.call.clobbers,
            ),
        )
    elif isinstance(stmt, lir.CallAssign):
        return lir.CallAssign(
            targets=stmt.targets,
            call=lir.Call(
                name=stmt.call.name,
                args=tuple(_replace_spilled_in_expr(a, load_map) for a in stmt.call.args),
                arg_regs=stmt.call.arg_regs,
                return_regs=stmt.call.return_regs,
                clobbers=stmt.call.clobbers,
            ),
            abi_return_regs=stmt.abi_return_regs,
        )
    elif isinstance(stmt, lir.MemStore):
        return lir.MemStore(
            addr=_replace_spilled_in_expr(stmt.addr, load_map),
            value=_replace_spilled_in_expr(stmt.value, load_map),
            volatile=stmt.volatile,
        )
    elif isinstance(stmt, lir.BitOp):
        return lir.BitOp(
            kind=stmt.kind,
            var=_replace_spilled_in_expr(stmt.var, load_map),
            bit_idx=stmt.bit_idx,
        )
    return stmt


def _replace_spilled_in_term(term, load_map: Dict[str, str]):
    """Replace all spilled var references in *term* with fresh reload names."""
    if isinstance(term, lir.BrIf):
        return lir.BrIf(
            cond=_replace_spilled_in_expr(term.cond, load_map),
            true_label=term.true_label,
            false_label=term.false_label,
        )
    elif isinstance(term, lir.BrCmp):
        return lir.BrCmp(
            op=term.op,
            left=_replace_spilled_in_expr(term.left, load_map),
            right=_replace_spilled_in_expr(term.right, load_map),
            true_label=term.true_label,
            false_label=term.false_label,
        )
    elif isinstance(term, lir.Return):
        return lir.Return(value=_replace_spilled_in_expr(term.value, load_map))
    elif isinstance(term, lir.MultiReturn):
        return lir.MultiReturn(
            values=tuple(_replace_spilled_in_expr(v, load_map) for v in term.values)
        )
    return term


# ---------------------------------------------------------------------------
# Step 1: collect all names and hints
# ---------------------------------------------------------------------------

def _collect_names(
    func: lir.Function,
    extra_hints: Dict[str, str],
) -> Dict[str, Optional[str]]:
    """Return {var_name: hint_or_None}.

    VReg.hint takes priority over extra_hints.
    """
    result: Dict[str, Optional[str]] = {}

    def _record(node) -> None:
        if getattr(node, "width", 1) == 0:
            return
        if isinstance(node, lir.Var):
            if node.name not in result:
                result[node.name] = extra_hints.get(node.name)
        elif isinstance(node, lir.VReg):
            result[node.name] = node.hint if node.hint else extra_hints.get(node.name)

    # Scan function params
    for p in func.params:
        _record(p)

    # Scan blocks
    for block in func.blocks:
        for stmt in block.statements:
            _scan_stmt_for_names(stmt, _record)
        _scan_terminator_for_names(block.terminator, _record)

    return result


def _scan_stmt_for_names(stmt, record_fn) -> None:
    if isinstance(stmt, lir.Assign):
        record_fn(stmt.target)
        _scan_expr_for_names(stmt.value, record_fn)
    elif isinstance(stmt, lir.CallStmt):
        _scan_expr_for_names(stmt.call, record_fn)
    elif isinstance(stmt, lir.CallAssign):
        for target in stmt.targets:
            if target is not None:
                record_fn(target)
        _scan_expr_for_names(stmt.call, record_fn)
    elif isinstance(stmt, lir.MemStore):
        _scan_expr_for_names(stmt.addr, record_fn)
        _scan_expr_for_names(stmt.value, record_fn)
    elif isinstance(stmt, lir.BitOp):
        _scan_expr_for_names(stmt.var, record_fn)


def _scan_expr_for_names(expr, record_fn) -> None:
    if isinstance(expr, (lir.Var, lir.VReg)):
        record_fn(expr)
    elif isinstance(expr, lir.BinOp):
        _scan_expr_for_names(expr.left, record_fn)
        _scan_expr_for_names(expr.right, record_fn)
    elif isinstance(expr, lir.Cmp):
        _scan_expr_for_names(expr.left, record_fn)
        _scan_expr_for_names(expr.right, record_fn)
    elif isinstance(expr, lir.Extend):
        _scan_expr_for_names(expr.value, record_fn)
    elif isinstance(expr, lir.MemLoad):
        _scan_expr_for_names(expr.addr, record_fn)
    elif isinstance(expr, lir.BitOp):
        _scan_expr_for_names(expr.var, record_fn)
    elif isinstance(expr, lir.Call):
        for arg in expr.args:
            _scan_expr_for_names(arg, record_fn)
    # Const: no names


def _scan_terminator_for_names(term, record_fn) -> None:
    if isinstance(term, lir.BrIf):
        _scan_expr_for_names(term.cond, record_fn)
    elif isinstance(term, lir.BrCmp):
        _scan_expr_for_names(term.left, record_fn)
        _scan_expr_for_names(term.right, record_fn)
    elif isinstance(term, lir.Return):
        _scan_expr_for_names(term.value, record_fn)
    elif isinstance(term, lir.MultiReturn):
        for v in term.values:
            _scan_expr_for_names(v, record_fn)
    # Jump: no names


# ---------------------------------------------------------------------------
# Step 2: build interference graph
# ---------------------------------------------------------------------------

def _build_interference(
    func: lir.Function, all_regs: Set[str]
) -> tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Build variable interference and per-variable call-clobber exclusions."""
    _, live_out = _compute_block_liveness(func)
    interference: Dict[str, Set[str]] = {}
    forbidden: Dict[str, Set[str]] = {}

    def connect(names: Set[str]) -> None:
        for name in names:
            interference.setdefault(name, set()).update(names - {name})

    for block in func.blocks:
        live = set(live_out[block.label])
        live.update(_term_uses(block.terminator))
        connect(live)
        for stmt in reversed(block.statements):
            stmt_defs, stmt_uses = _stmt_defs_uses(stmt)
            call = _stmt_call(stmt)
            if call is not None:
                clobbers = _call_effect_regs(call, all_regs)
                for name in live - stmt_defs:
                    forbidden.setdefault(name, set()).update(clobbers)
            connect(live | stmt_defs)
            live.difference_update(stmt_defs)
            live.update(stmt_uses)
            connect(live)

    return interference, forbidden


def _compute_block_liveness(
    func: lir.Function | lir.Fragment,
) -> tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Return live-in/live-out variable names for each block."""
    uses: Dict[str, Set[str]] = {}
    defs: Dict[str, Set[str]] = {}
    successors: Dict[str, Set[str]] = {}

    for block in func.blocks:
        block_uses: Set[str] = set()
        block_defs: Set[str] = set()
        for stmt in block.statements:
            stmt_defs, stmt_uses = _stmt_defs_uses(stmt)
            block_uses.update(stmt_uses - block_defs)
            block_defs.update(stmt_defs)
        term_uses = _term_uses(block.terminator)
        block_uses.update(term_uses - block_defs)
        uses[block.label] = block_uses
        defs[block.label] = block_defs
        successors[block.label] = _successors(block.terminator)

    live_in = {block.label: set() for block in func.blocks}
    live_out = {block.label: set() for block in func.blocks}
    changed = True
    while changed:
        changed = False
        for block in reversed(func.blocks):
            label = block.label
            new_out = set().union(
                *(live_in[target] for target in successors[label])
            ) if successors[label] else set()
            new_in = uses[label] | (new_out - defs[label])
            if new_in != live_in[label] or new_out != live_out[label]:
                live_in[label], live_out[label] = new_in, new_out
                changed = True

    return live_in, live_out


def statement_live_after(
    func: lir.Function | lir.Fragment,
) -> Dict[tuple[str, int], Set[str]]:
    """Return variable names live immediately after each block statement."""
    _, live_out = _compute_block_liveness(func)
    result: Dict[tuple[str, int], Set[str]] = {}
    for block in func.blocks:
        live = set(live_out[block.label])
        live.update(_term_uses(block.terminator))
        for index in range(len(block.statements) - 1, -1, -1):
            result[(block.label, index)] = set(live)
            stmt_defs, stmt_uses = _stmt_defs_uses(block.statements[index])
            live.difference_update(stmt_defs)
            live.update(stmt_uses)
    return result


def terminator_live_before(
    func: lir.Function | lir.Fragment,
) -> Dict[str, Set[str]]:
    """Return variable names live immediately before each block terminator."""
    _, live_out = _compute_block_liveness(func)
    return {
        block.label: live_out[block.label] | _term_uses(block.terminator)
        for block in func.blocks
    }


def _call_effect_regs(call: lir.Call, all_regs: Set[str]) -> Set[str]:
    """Return call-boundary occupied registers for allocator conflicts."""
    effect_regs = set(all_regs) if call.clobbers is None else set(call.clobbers)
    effect_regs.update(reg for reg in call.arg_regs if reg is not None)
    effect_regs.update(reg for reg in call.return_regs if reg is not None)
    return effect_regs


def _names(expr) -> Set[str]:
    result: Set[str] = set()
    _scan_expr_for_names(expr, lambda node: result.add(node.name))
    return result


def _stmt_defs_uses(stmt) -> tuple[Set[str], Set[str]]:
    if isinstance(stmt, lir.Assign):
        return {stmt.target.name}, _names(stmt.value)
    if isinstance(stmt, lir.CallStmt):
        # No definitions: void call produces no named result.
        return set(), _names(stmt.call)
    if isinstance(stmt, lir.CallAssign):
        return (
            {target.name for target in stmt.targets if target is not None},
            _names(stmt.call),
        )
    if isinstance(stmt, lir.MemStore):
        return set(), _names(stmt.addr) | _names(stmt.value)
    if isinstance(stmt, lir.BitOp):
        names = _names(stmt.var)
        return names, names
    return set(), set()


def _term_uses(term) -> Set[str]:
    result: Set[str] = set()
    _scan_terminator_for_names(term, lambda node: result.add(node.name))
    return result


def _successors(term) -> Set[str]:
    if isinstance(term, lir.Jump):
        return {term.label}
    if isinstance(term, (lir.BrIf, lir.BrCmp)):
        return {term.true_label, term.false_label}
    return set()


def _stmt_call(stmt) -> lir.Call | None:
    if isinstance(stmt, lir.Assign) and isinstance(stmt.value, lir.Call):
        return stmt.value
    if isinstance(stmt, lir.CallStmt):
        return stmt.call
    if isinstance(stmt, lir.CallAssign):
        return stmt.call
    return None


# ---------------------------------------------------------------------------
# Width lookup helper
# ---------------------------------------------------------------------------

def _find_width(func: lir.Function, var_name: str) -> int:
    """Return the bit-width of the named variable by scanning the function."""
    for block in func.blocks:
        for stmt in block.statements:
            if isinstance(stmt, lir.Assign):
                if isinstance(stmt.target, (lir.Var, lir.VReg)) and stmt.target.name == var_name:
                    return stmt.target.width
            elif isinstance(stmt, lir.CallAssign):
                for target in stmt.targets:
                    if isinstance(target, (lir.Var, lir.VReg)) and target.name == var_name:
                        return target.width
    for p in func.params:
        if hasattr(p, "name") and p.name == var_name:
            return p.width
    return 8  # fallback


# ---------------------------------------------------------------------------
# Step 5: apply assignment (name replacement)
# ---------------------------------------------------------------------------

def _apply_assignment(
    func: lir.Function,
    assignment: Dict[str, str],
) -> lir.Function:
    """Replace all Var/VReg names with assigned physical register names."""

    def _remap_expr(expr):
        if isinstance(expr, lir.Var):
            new_name = assignment.get(expr.name, expr.name)
            return lir.Var(new_name, expr.width)
        elif isinstance(expr, lir.VReg):
            new_name = assignment.get(expr.name, expr.name)
            return lir.Var(new_name, expr.width)
        elif isinstance(expr, lir.BinOp):
            return lir.BinOp(
                expr.op,
                _remap_expr(expr.left),
                _remap_expr(expr.right),
                expr.width,
            )
        elif isinstance(expr, lir.Cmp):
            return lir.Cmp(
                expr.op,
                _remap_expr(expr.left),
                _remap_expr(expr.right),
                expr.width,
                expr.signed,
            )
        elif isinstance(expr, lir.Extend):
            return lir.Extend(
                kind=expr.kind,
                value=_remap_expr(expr.value),
                width=expr.width,
            )
        elif isinstance(expr, lir.MemLoad):
            return lir.MemLoad(
                addr=_remap_expr(expr.addr),
                width=expr.width,
                volatile=expr.volatile,
            )
        elif isinstance(expr, lir.BitOp):
            return lir.BitOp(
                kind=expr.kind,
                var=_remap_expr(expr.var),
                bit_idx=expr.bit_idx,
            )
        elif isinstance(expr, lir.Call):
            return lir.Call(
                name=expr.name,
                args=tuple(_remap_expr(a) for a in expr.args),
                arg_regs=expr.arg_regs,
                return_regs=expr.return_regs,
                clobbers=expr.clobbers,
            )
        return expr

    def _remap_stmt(stmt):
        if isinstance(stmt, lir.Assign):
            if isinstance(stmt.target, lir.Var):
                new_name = assignment.get(stmt.target.name, stmt.target.name)
                new_target = lir.Var(new_name, stmt.target.width)
            elif isinstance(stmt.target, lir.VReg):
                new_name = assignment.get(stmt.target.name, stmt.target.name)
                new_target = lir.Var(new_name, stmt.target.width)
            else:
                new_target = stmt.target
            return lir.Assign(target=new_target, value=_remap_expr(stmt.value))
        elif isinstance(stmt, lir.CallStmt):
            return lir.CallStmt(call=_remap_expr(stmt.call))
        elif isinstance(stmt, lir.CallAssign):
            new_targets = []
            for target in stmt.targets:
                if isinstance(target, lir.Var):
                    new_targets.append(
                        lir.Var(assignment.get(target.name, target.name), target.width)
                    )
                elif isinstance(target, lir.VReg):
                    new_targets.append(
                        lir.VReg(
                            name=assignment.get(target.name, target.name),
                            width=target.width,
                            hint=target.hint,
                        )
                    )
                else:
                    new_targets.append(None)
            return lir.CallAssign(
                targets=tuple(new_targets),
                call=_remap_expr(stmt.call),
                abi_return_regs=stmt.abi_return_regs,
            )
        elif isinstance(stmt, lir.MemStore):
            return lir.MemStore(
                addr=_remap_expr(stmt.addr),
                value=_remap_expr(stmt.value),
                volatile=stmt.volatile,
            )
        elif isinstance(stmt, lir.BitOp):
            return lir.BitOp(
                kind=stmt.kind,
                var=_remap_expr(stmt.var),
                bit_idx=stmt.bit_idx,
            )
        return stmt

    def _remap_term(term):
        if isinstance(term, lir.BrIf):
            return lir.BrIf(
                cond=_remap_expr(term.cond),
                true_label=term.true_label,
                false_label=term.false_label,
            )
        elif isinstance(term, lir.BrCmp):
            return lir.BrCmp(
                op=term.op,
                left=_remap_expr(term.left),
                right=_remap_expr(term.right),
                true_label=term.true_label,
                false_label=term.false_label,
            )
        elif isinstance(term, lir.Return):
            return lir.Return(value=_remap_expr(term.value))
        elif isinstance(term, lir.MultiReturn):
            return lir.MultiReturn(values=tuple(_remap_expr(v) for v in term.values))
        return term

    new_params = tuple(
        lir.Var(assignment.get(p.name, p.name), p.width) for p in func.params
    )

    new_blocks = []
    for block in func.blocks:
        new_stmts = tuple(_remap_stmt(s) for s in block.statements)
        new_term = _remap_term(block.terminator)
        new_blocks.append(lir.Block(
            label=block.label,
            statements=new_stmts,
            terminator=new_term,
        ))

    return lir.Function(
        name=func.name,
        params=new_params,
        blocks=tuple(new_blocks),
    )
