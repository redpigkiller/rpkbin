"""Register allocator for the codegen pipeline.

Assigns physical registers to all ``Var`` / ``VReg`` nodes in a
``lir.Function``.  The allocator is driven entirely by a ``RegisterModel``
Protocol; no MCU-specific details are hardcoded here.

Register allocation runs whenever a codegen pipeline receives a
``RegisterModel``.

Algorithm: greedy graph-colouring, fail-closed on pressure
---------------------------------------------------------
1. Collect all named values (Var / VReg names) appearing in the function.
2. Compute block live-in/live-out sets and build an interference graph.
3. Greedy colour: hinted variables first (sorted by hint name), then
   unhinted variables (sorted by name for determinism).
4. If no compatible register is available, raise RegisterAllocationError.
5. Apply the assignment: replace every Var/VReg with its physical register.

The old pre-isel spill prototype could overwrite live registers while loading
spill temporaries.  Register pressure therefore fails closed until spilling is
implemented at a representation that knows target instruction constraints.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import lir
from .target import RegisterModel, can_allocate, registers_overlap, is_physical_register


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class RegisterAllocationError(Exception):
    """Raised when the available registers cannot satisfy allocation."""


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
        ``new_func`` has the same structure as *func* but with all Var/VReg
        names replaced by physical register names. ``assignment`` maps each
        var_name → physical_reg.

    Raises
    ------
    RegisterAllocationError
        If more simultaneously-live variables exist than available registers.
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
        self._fixed_hints = getattr(self._rm, "fixed_register_hints", lambda: False)()

    def run(self) -> Tuple[lir.Function, Dict[str, str]]:
        # Step 1: collect all named values and their hints
        names_hints = _collect_names(self._func, self._extra_hints)
        self._validate_fixed_hints(names_hints)

        # Step 2: build interference graph
        physical_regs = set(self._rm.allocatable_registers())
        physical_regs.update(hint for hint in names_hints.values() if hint is not None)
        interference, forbidden = _build_interference(self._func, physical_regs)

        # Step 3: greedy colouring
        assignment = self._greedy_colour(
            names_hints, interference, forbidden
        )

        # Step 4: replace var names with physical registers
        new_func = _apply_assignment(self._func, assignment)
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
    ) -> Dict[str, str]:
        """Assign physical registers greedily.

        Returns:
          assignment: var_name → physical_reg
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
            else:
                raise RegisterAllocationError(
                    f"No register available for '{var_name}'; register spilling "
                    "is not implemented safely."
                )

        return assignment

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
