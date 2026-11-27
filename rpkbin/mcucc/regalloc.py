"""Deterministic greedy allocation, immediate local improvements and restarts."""

from itertools import combinations, product
from types import MappingProxyType

from .errors import AllocationError, UnsupportedError
from .expand import MInstr, _expand_branch, _move, _parallel, expand
from .frames import _Builder, _active
from .ir import Imm, VReg, _ir_defs_uses
from .liveness import _analyze, _compatible
from .target import M, MemLoc, RegLoc, SlotLoc, _width, units
from .saves import _save_plans
from .park import _ParkPlan


class _Allocator:
    def __init__(self, function, ctx, interfaces):
        self.function, self.ctx, self.interfaces = function, ctx, interfaces
        self.points, self.mentions, self.reasons = _analyze(function)
        self.values = _active(function)
        self.order = sorted(self.values, key=lambda v: (-self.mentions[v], v.id))
        self.candidates = {v: self._candidates(v) for v in self.values}
        self.instructions = [(b.id, index, i) for b in function.blocks for index, i in enumerate((*b.instrs, b.term))]
        self.cache = {}
        self.full_cache = {}
        self.failed = None
        self.base_candidates = self.candidates
        self.park = _ParkPlan(function, ctx, self.base_candidates) if ctx.park_depth else None
        self.pinned_slots = set()
        if self.park is not None:
            self._refresh_candidates(())

    def _refresh_candidates(self, gaps):
        parked = {gap.value for gap in gaps}
        self.candidates = {
            value: self.park.candidates[value] if value in parked else
            tuple(loc for loc in choices if isinstance(loc, SlotLoc) == (value in self.pinned_slots))
            for value, choices in self.base_candidates.items()
        }
        self.cache.clear()
        self.full_cache.clear()

    def _select_gaps(self, gaps):
        self.ctx.parked = gaps
        masks = self.ctx.park_masks = self.park.masks(gaps)
        self.ctx.park_events = self.park.events(gaps)
        points = {key: live - masks[key] for key, live in self.park.base.items()}
        self.ctx.park_points = points
        self.points, self.mentions, self.reasons = _analyze(self.function, points)
        self._refresh_candidates(gaps)
        for point, events in self.ctx.park_events.items():
            for gap, op, live in self.park.boundary(point, events, masks):
                if op == "pop":
                    for other in sorted(live, key=lambda v: v.id):
                        edge = tuple(sorted((gap.value, other), key=lambda v: v.id))
                        self.reasons.setdefault(edge, []).append((None, gap.value, other))
        self.ctx.park_plan = self.park

    def _prune_gaps(self, assignments, cost):
        for gap in reversed(self.ctx.parked):
            old = self.ctx.parked
            self._select_gaps(tuple(g for g in old if g != gap))
            revised = self(assignments, total=True)
            if revised is None or revised >= cost:
                self._select_gaps(old)
            else:
                cost = revised

    def _candidates(self, value):
        choices = [r for r in self.ctx.registers if _width(self.ctx.target, r.name) == value.width]
        assembly=[(v,hint) for b in self.function.blocks for i in b.instrs if i.op=="asm" for v,hint in (*i.meta["inputs"],*i.meta["outputs"]) if v==value]
        if assembly:
            return tuple(r for r in choices if all(hint is None or r.name==hint for _,hint in assembly))
        if value in self.function.params:
            location = self.function.abi["args"][self.function.params.index(value)]
            if isinstance(location, MemLoc):
                choices.append(location)
        if self.ctx.pool:
            space, (low, high) = next(iter(self.ctx.pool.items()))
            def available(op):
                return any(isinstance(o, M) and o.space == space and high < 1 << (o.bits or self.ctx.spaces[space].addr_bits)
                           for form_width in dict.fromkeys((value.width, self.ctx.target.cell_bits))
                           for f in self.ctx.forms.get((op, form_width), ()) for o in f.operands)
            count = value.width // self.ctx.target.cell_bits
            if high - low + 1 >= count and available("ld") and available("st"):
                choices.append(SlotLoc("vreg", value.id, 0, count))
        return tuple(sorted(choices, key=self.ctx._position))

    def _instruction(self, bid, index, instruction, assignments, ideal=False):
        op = instruction.op
        defined, used = _ir_defs_uses(instruction)
        operands = sorted(defined | used, key=lambda v: v.id)
        unassigned = [v for v in operands if v not in assignments]
        known_busy = self.points[bid, index] - defined
        relevant = set(operands) | known_busy
        key = (bid, index, ideal, tuple((v.id, assignments[v]) for v in sorted(relevant & assignments.keys(), key=lambda v: v.id)))
        if key in self.cache:
            if self.cache[key] is None:
                self.failed = bid, index, instruction
            return self.cache[key]
        best = None
        zero = op in ("entry", "jmp", "mov", "trunc", "hi", "zext", "pack")
        width = instruction.dst.width if instruction.dst is not None else instruction.args[0].width if instruction.args else 0
        if op=="asm":
            lower=(instruction.meta["nbytes"],1,0)
        elif zero:
            lower = (0, 0, 0)
        elif op == "ret":
            ret_form = _Builder(self.function, self.ctx, assignments, self.interfaces)._return_form()
            lower = (ret_form.bytes, 1, 0)
        elif op == "br":
            cmps, branches = self.ctx.forms.get(("cmp", width), ()), self.ctx.forms.get(("br", 0), ())
            lower = (min((f.bytes for f in cmps), default=0) + min((f.bytes for f in branches), default=0), 2, 0)
        else:
            selected = ("ld" if op == "load" else "st" if op == "store" else op)
            if op in ("load", "store") and isinstance(instruction.args[0], VReg):
                selected += "_ind"
            forms = self.ctx.forms.get((selected, 0 if op == "call" else width), ())
            # Every unary route contains at least one MAIN, including the
            # xor/sub equivalents. PRE/POST bytes and slot counts are
            # nonnegative, so this remains a lexicographic lower bound.
            if op in ("not", "neg"):
                equivalent = "xor" if op == "not" else "sub"
                forms = (*forms, *self.ctx.forms.get((equivalent, width), ()))
            lower = (min((f.bytes for f in forms), default=0), 1, 0)
        def options(value):
            if not ideal:
                return self.candidates[value]
            if op=="asm":
                return self.base_candidates[value]
            # The ideal check asks whether forms exist, independently of actual pool capacity.
            memory = tuple(MemLoc(s.name, 0, value.width // self.ctx.target.cell_bits) for s in self.ctx.target.spaces if s.kind == "data")
            return tuple(dict.fromkeys((*self.base_candidates[value], *memory)))
        for choices in product(*(options(v) for v in unassigned)):
            local = {**assignments, **dict(zip(unassigned, choices))}
            if not ideal and not _compatible(self.ctx.target, self.reasons, local):
                continue
            busy = set().union(*(self.ctx._units(local[v]) for v in sorted(known_busy & assignments.keys(), key=lambda v: v.id))) if not ideal else set()
            def position(v):
                return v if isinstance(v, Imm) else local[v]
            try:
                if op == "entry":
                    pairs = []
                    for param in instruction.args:
                        location = self.function.abi["args"][self.function.params.index(param)]
                        if location is None:
                            raise UnsupportedError(f"{self.function.name}: ABI has no argument location for width {param.width}")
                        pairs.append((RegLoc(location) if isinstance(location, str) else location, local[param], param.width))
                    sequence = _parallel(self.ctx, pairs, set())
                elif op == "call":
                    builder = _Builder(self.function, self.ctx, local, self.interfaces)
                    builder._relaxed_dst=instruction.dst is not None and instruction.dst not in assignments
                    builder.points = {**self.points, (bid, index): self.points[bid, index] & assignments.keys()}
                    sequence = builder._call(instruction, bid, index)
                elif op == "asm":
                    builder=_Builder(self.function,self.ctx,local,self.interfaces)
                    builder.points={**self.points,(bid,index):self.points[bid,index]&assignments.keys()}
                    sequence=builder._assembly(instruction,bid,index)
                elif op == "ret":
                    if instruction.args:
                        location = self.function.abi["ret"]
                        if location is None:
                            raise UnsupportedError(f"{self.function.name}: ABI has no return location for width {self.function.ret_width}")
                        sequence = _move(self.ctx, position(instruction.args[0]), RegLoc(location) if isinstance(location, str) else location, self.function.ret_width, set(), set())
                    else:
                        sequence = []
                    if sequence is not None:
                        sequence = [*sequence, MInstr(ret_form, {})]
                elif op == "jmp":
                    sequence = []
                elif op == "br":
                    self.ctx._branch_targets = instruction.meta["true"], instruction.meta["false"]
                    result = _expand_branch(self.ctx, instruction.meta["cond"], *instruction.args, local, busy,
                                            instruction.meta["false"], msb0_ok=instruction.meta.get("msb0_ok"))
                    sequence = result.mir if result else None
                else:
                    result = expand(self.ctx, instruction, local, busy)
                    sequence = result.mir if result else None
            except AllocationError:
                sequence = None
            if sequence is None:
                continue
            slots = {}
            for m in sequence:
                for value in m.values.values():
                    if isinstance(value, SlotLoc) and value.kind not in ("ptemp", "vreg"):
                        slot_key = value.kind, value.key
                        slots[slot_key] = max(slots.get(slot_key, 0), value.first + value.n)
            cost = (sum(m.bytes for m in sequence), len(sequence), sum(slots.values()))
            if best is None or cost < best[0]:
                best = cost, slots
                if cost == lower:
                    break
        self.cache[key] = best
        if best is None:
            self.failed = bid, index, instruction
        return best

    def _partial(self, assignments):
        total = [0, 0]
        slots = {(loc.kind, loc.key): loc.n for loc in assignments.values() if isinstance(loc, SlotLoc)}
        if self.ctx.parked:
            for point in self.ctx.park_events:
                sequence = self.park.sequence(point, assignments, partial=True)
                if sequence is None:
                    self.failed = *point, self.park.instructions[point]
                    return None
                total[0] += sum(m.bytes for m in sequence)
                total[1] += len(sequence)
        for bid, index, instruction in self.instructions:
            result = self._instruction(bid, index, instruction, assignments)
            if result is None:
                return None
            cost, used = result
            total[0] += cost[0]
            total[1] += cost[1]
            for key, count in used.items():
                slots[key] = max(slots.get(key, 0), count)
        # R2 charges occupation of a preserved register once, including read-only use.
        pushed=()
        for name in self.function.abi.get("preserves", ()):
            if any(units(self.ctx.target, name) & self.ctx._units(loc) for loc in assignments.values()):
                width = _width(self.ctx.target, name)
                slot = SlotLoc("preserve", self.ctx.reg_order[name], 0, width // self.ctx.target.cell_bits)
                destination=units(self.ctx.target,self.function.abi["ret"]) if self.function.abi["ret"] is not None else frozenset()
                options=_save_plans(self.ctx,RegLoc(name),slot,width,set(),destination,destination,pushed)
                if not options:
                    return None
                save,restore,group,_=options[0]
                total[0] += sum(m.bytes for m in (*save, *restore))
                total[1] += len(save) + len(restore)
                if group is None:
                    slots[slot.kind, slot.key] = slot.n
                elif group not in pushed:
                    pushed=(*pushed,group)
        return *total, sum(slots.values())

    def _total(self, assignments):
        key = tuple(assignments[v] for v in self.values)
        if key in self.full_cache:
            return self.full_cache[key]
        if not self.function.abi.get("preserves"):
            result = self._partial(assignments)
            self.full_cache[key] = result
            return result
        try:
            blocks = _Builder(self.function, self.ctx, assignments, self.interfaces)._run()
        except AllocationError:
            self.full_cache[key] = None
            return None
        sequence = [m for group in blocks.values() for m in group]
        slots = {}
        for loc in (*assignments.values(), *(v for m in sequence for v in m.values.values())):
            if isinstance(loc, SlotLoc) and loc.kind != "ptemp":
                slots[loc.kind, loc.key] = max(slots.get((loc.kind, loc.key), 0), loc.first + loc.n)
        result = sum(m.bytes for m in sequence), len(sequence), sum(slots.values())
        self.full_cache[key] = result
        return result

    def __call__(self, assignments, *, total=False, instruction=None, ideal=False):
        if self.park is not None and not ideal and any(loc not in self.candidates[v] for v, loc in assignments.items()):
            return None
        if not ideal and not _compatible(self.ctx.target, self.reasons, assignments):
            return None
        if instruction is not None:
            return self._instruction(*instruction, assignments, ideal=ideal)
        return self._total(assignments) if total else self._partial(assignments)

    def run(self):
        self.ctx._optimistic = True
        try:
            return assign(self.function, (self.points, self.mentions, self.reasons), self,
                          lambda value: self.candidates[value])
        finally:
            self.ctx._optimistic = False


def _heuristic(ir, liveness, expandcost_fn, cand_fn):
    points, mentions, reasons = liveness
    values = _active(ir)
    order = sorted(values, key=lambda v: (-mentions[v], v.id))
    candidates_by_value = {v: tuple(cand_fn(v)) for v in values}
    instructions = [(b.id, index, i) for b in ir.blocks for index, i in enumerate((*b.instrs, b.term))]
    def error(instruction):
        line = instruction.meta.get("line", 1)
        raise AllocationError(f"{ir.name}:{line}: instruction {instruction.op}: no finite allocation; missing register class/scratch or spill pool slot")
    allocator = expandcost_fn if isinstance(expandcost_fn, _Allocator) else None
    base = allocator.base_candidates if allocator is not None else candidates_by_value
    for value in values:
        if not base[value]:
            raise AllocationError(f"{ir.name}: v{value.id}: no register satisfies asm hints and width {value.width}")
    for bid, index, instruction in instructions:
        if expandcost_fn({}, instruction=(bid, index, instruction), ideal=True) is None:
            width = instruction.dst.width if instruction.dst is not None else instruction.args[0].width if instruction.args else 0
            raise UnsupportedError(f"{ir.name}:{instruction.meta.get('line', 1)}: ({instruction.op}, {width}) has no ideal expansion; missing form/register class")
    park_enabled = allocator is not None and allocator.park is not None
    pinned = allocator.pinned_slots if park_enabled else set()
    while True:
        if allocator is not None:
            points, reasons = allocator.points, allocator.reasons
            candidates_by_value = allocator.candidates
        assignments, failure = {}, None
        for value in order:
            candidates = []
            for location in candidates_by_value[value]:
                if value in pinned and not isinstance(location, SlotLoc):
                    continue
                trial = {**assignments, value: location}
                cost = expandcost_fn(trial)
                if cost is not None:
                    candidates.append((cost, candidates_by_value[value].index(location), location))
            if not candidates:
                failure = value
                break
            assignments[value] = min(candidates, key=lambda item: item[:2])[2]
        if failure is None:
            break
        instruction = getattr(expandcost_fn, "failed", None)[2] if getattr(expandcost_fn, "failed", None) else instructions[0][2]
        if park_enabled and allocator.failed is not None:
            gap = allocator.park.choose(allocator.failed[:2], pinned, mentions)
            if gap is not None:
                allocator._select_gaps((*allocator.ctx.parked, gap))
                continue
        base = allocator.base_candidates if allocator is not None else candidates_by_value
        if failure in pinned or not any(isinstance(c, SlotLoc) for c in base[failure]):
            error(instruction)
        involved = _ir_defs_uses(instruction)
        relevant = involved[0] | involved[1]
        if getattr(expandcost_fn, "failed", None):
            relevant |= points[getattr(expandcost_fn, "failed", None)[:2]]
        neighbors = {v for edge in reasons if failure in edge for v in edge}
        evict = [v for v, loc in assignments.items() if isinstance(loc, RegLoc) and v not in pinned and v in relevant | neighbors and any(isinstance(c, SlotLoc) for c in base[v])]
        if not evict:
            error(instruction)
        victim = min(evict, key=lambda v: (mentions[v], v.id))
        pinned.add(victim)
        if park_enabled:
            gaps = allocator.ctx.parked
            if any(gap.value == victim for gap in gaps):
                allocator._select_gaps(tuple(gap for gap in gaps if gap.value != victim))
            else:
                allocator._refresh_candidates(gaps)
    current = expandcost_fn(assignments, total=True)
    if current is None:
        error(getattr(expandcost_fn, "failed", None)[2] if getattr(expandcost_fn, "failed", None) else instructions[0][2])
    for _ in range(8):
        changed = False
        for value in order:
            choices = []
            for location in candidates_by_value[value]:
                if location == assignments[value] or (value in pinned and not isinstance(location, SlotLoc)):
                    continue
                trial = {**assignments, value: location}
                cost = expandcost_fn(trial, total=True)
                if cost is not None and cost < current:
                    choices.append((cost, candidates_by_value[value].index(location), location))
            if choices:
                current, _, location = min(choices, key=lambda item: item[:2])
                assignments[value], changed = location, True
        for left, right in combinations(values, 2):
            a, b = assignments[left], assignments[right]
            if left.width != right.width or not isinstance(a, RegLoc) or not isinstance(b, RegLoc) or a == b:
                continue
            trial = {**assignments, left: b, right: a}
            cost = expandcost_fn(trial, total=True)
            if cost is not None and cost < current:
                assignments, current, changed = trial, cost, True
        if not changed:
            break
    if allocator is not None and allocator.ctx.parked:
        allocator._prune_gaps(assignments, current)
    return assignments


_BACKENDS = MappingProxyType({"heuristic": _heuristic})


def assign(ir, liveness, expandcost_fn, cand_fn):
    """Assign locations through the registered v1 deterministic backend.

    The per-compilation cost callback supplies partial/total costs and ideal
    instruction costs; candidates arrive in the target's location order.
    No backend selection or mutable registration is exposed by v1.
    """
    return _BACKENDS["heuristic"](ir, liveness, expandcost_fn, cand_fn)


def _allocate(function, ctx, interfaces):
    return _Allocator(function, ctx, interfaces).run()
