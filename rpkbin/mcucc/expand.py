"""Instruction selection with explicit read-before-write move scheduling."""

from dataclasses import dataclass

from .ir import Imm
from .machine import _effects_intersect, _indirect_write
from .opsem import _FLAG_RELATIONS, _MSB0_FLAGS
from .target import I, M, P, R, T, MemLoc, RegLoc, SlotLoc, _seq, _width, part, units


@dataclass(frozen=True)
class MInstr:
    form: object
    values: dict
    _origin: object = None
    _block: object = None
    _role: str = ""
    _label: str | None = None
    _asm: dict | None = None
    _target: object = None
    _abi: object = None
    _callee_abi: object = None
    _busy: frozenset = frozenset()
    _pool_bounds: tuple = ()
    _park: tuple | None = None

    @property
    def bytes(self):
        return self.form.bytes if self.form is not None else self._asm["nbytes"] if self._asm else 0

    @property
    def op(self):
        return self.form.op if self.form is not None else "asm" if self._asm else "label"


@dataclass
class Expansion:
    mir: list
    cost: tuple


@dataclass(frozen=True)
class _StackLoc:
    width: int


@dataclass(frozen=True)
class _TargetRef:
    block: int


class _Context:
    def __init__(self, target, pool, *, pool_private=False):
        self.target, self.pool = target, pool
        self.pool_private = pool_private
        self.park_depth = 0
        self.callee_depths = {}
        self.parked = ()
        self.park_points = None
        self.registers = tuple(RegLoc(r.name) for r in (*target.regs, *target.aliases))
        self.reg_order = {r.name: i for i, r in enumerate((*target.regs, *target.aliases))}
        self.spaces = {s.name: s for s in target.spaces}
        self.space_order = {s.name: i for i, s in enumerate(target.spaces)}
        self.classes = {c.name: c.members for c in target.classes}
        self.form_order = {f.id: i for i, f in enumerate(target.forms)}
        self.forms = {}
        for form in target.forms:
            self.forms.setdefault((form.op, form.width), []).append(form)
        self._edge_cache = {}
        self._move_cache = {}
        self._ptemp = 0
        self._ptemp_slots = []
        self._branch_targets = (1, 2)
        self._optimistic = False

    def _units(self, location):
        return frozenset() if isinstance(location, (Imm, _StackLoc)) or location is None else units(self.target, location)

    def _part(self, location, which):
        if isinstance(location, Imm):
            return Imm((location.value >> (8 if which == "hi" else 0)) & ((1 << 8) - 1), 8)
        return part(self.target, location, which)

    def _position(self, location):
        if isinstance(location, RegLoc):
            return 0, self.reg_order[location.name]
        if isinstance(location, MemLoc):
            return 1, self.space_order[location.space], location.addr
        if isinstance(location, SlotLoc):
            return 2, ("vreg", "callsave", "preserve", "ptemp").index(location.kind), location.key, location.first, location.n
        return 3, repr(location)

    def _same(self, src, dst):
        if src == dst:
            return True
        if isinstance(src, RegLoc) and isinstance(dst, RegLoc):
            return _seq(self.target, src.name) == _seq(self.target, dst.name)
        return False

    def _key(self, sequence):
        first, registers, others = [], [], []
        for instruction in sequence:
            form = instruction.form
            first.append(self.form_order[form.id])
            for operand in form.operands:
                value = instruction.values[operand.name]
                if isinstance(operand, (R, P)):
                    registers.append(self.reg_order[value])
                elif isinstance(operand, I):
                    others.append(value)
                elif isinstance(operand, M):
                    if isinstance(value, SlotLoc):
                        space, (low, _) = next(iter(self.pool.items()))
                        others.append((self.space_order[space], low, 1 + ("vreg", "callsave", "preserve", "ptemp").index(value.kind), value.key, value.first))
                    else:
                        others.append((self.space_order[value.space], value.addr, 0, 0, 0))
        return sum(m.form.bytes for m in sequence), len(sequence), tuple(first), tuple(registers), tuple(others)

    def _memory(self, operand, location):
        if isinstance(location, MemLoc):
            bits = operand.bits or self.spaces[operand.space].addr_bits
            return location if location.space == operand.space and 0 <= location.addr and location.addr + location.n <= 1 << bits else None
        if isinstance(location, SlotLoc) and len(self.pool) == 1:
            space, (_, high) = next(iter(self.pool.items()))
            bits = operand.bits or self.spaces[operand.space].addr_bits
            if operand.space == space and high < 1 << bits:
                return location
        return None


def _writes(ctx, instruction):
    form, values = instruction.form, instruction.values
    result = set()
    if "dst" in form.bind:
        result.update(ctx._units(RegLoc(values[form.bind["dst"]])))
    if form.op in ("st", "st_ind"):
        operand = next(o for o in form.operands if o.name == form.bind["a"])
        value = values[operand.name]
        if isinstance(operand, M):
            result.update(ctx._units(value))
        else:
            result.add(_indirect_write(operand.space, ctx.pool if ctx.pool_private else {}))
    for reg in form.clobbers:
        result.update(units(ctx.target, reg))
    return result


def _conflicts(ctx, written, protected):
    return _effects_intersect(written, set(protected), ctx.pool)


def _direct(ctx, src, dst, width, flags):
    key = src, dst, width, flags
    if key in ctx._edge_cache:
        return ctx._edge_cache[key]
    result = []
    for op in ("mov", "const", "ld", "st"):
        for form in ctx.forms.get((op, width), ()):
            if flags and form.flags_write:
                continue
            bindings = {"dst": dst, "a": src} if op in ("mov", "const", "ld") else {"a": dst, "b": src}
            values, valid = {}, True
            for arg, location in bindings.items():
                operand = next(o for o in form.operands if o.name == form.bind[arg])
                value = None
                if isinstance(operand, R) and isinstance(location, RegLoc) and location.name in ctx.classes[operand.cls]:
                    value = location.name
                elif isinstance(operand, I) and isinstance(location, Imm) and 0 <= location.value < 1 << operand.bits and (operand.values is None or location.value in operand.values):
                    value = location.value
                elif isinstance(operand, M):
                    value = ctx._memory(operand, location)
                if value is None or (operand.name in values and values[operand.name] != value):
                    valid = False
                    break
                values[operand.name] = value
            if valid:
                result.append([MInstr(form, values)])
    ctx._edge_cache[key] = result
    return result


def _move(ctx, src, dst, width, busy, protected, flags=False, split=True):
    if ctx._same(src, dst):
        return []
    busy, protected = frozenset(busy), frozenset(protected)
    cache_key = src, dst, width, busy, protected, flags, split
    if cache_key in ctx._move_cache:
        return ctx._move_cache[cache_key]
    best = None
    def accept(sequence):
        nonlocal best
        if best is None or ctx._key(sequence) < ctx._key(best):
            best = sequence
    def edge(left, right):
        routes = list(_direct(ctx, left, right, width, flags))
        if width == 16 and split and (isinstance(left, (MemLoc, SlotLoc)) or isinstance(right, (MemLoc, SlotLoc))):
            native_op = "ld" if isinstance(left, (MemLoc, SlotLoc)) else "st"
            if not ctx.forms.get((native_op, width)) and (isinstance(left, RegLoc) or isinstance(right, RegLoc)):
                pairs = [(ctx._part(left, p), ctx._part(right, p), 8) for p in ("lo", "hi")]
                pieces = _parallel(ctx, pairs, busy, protected, flags)
                if pieces is not None:
                    routes.append(pieces)
        return routes
    nodes = [r for r in ctx.registers if _width(ctx.target, r.name) == width and not ctx._units(r) & (busy | protected | ctx._units(src))]
    def search(current, sequence, visited, hops):
        if hops >= 3:
            return
        for following in (dst, *nodes):
            if following in visited or following == current:
                continue
            for instructions in edge(current, following):
                if any(_conflicts(ctx, _writes(ctx, m), busy | protected) for m in instructions):
                    continue
                combined = [*sequence, *instructions]
                if best is not None and ctx._key(combined)[:2] > ctx._key(best)[:2]:
                    continue
                if following == dst:
                    accept(combined)
                else:
                    search(following, combined, (*visited, following), hops + 1)
    if isinstance(src, _StackLoc):
        for form in ctx.forms.get(("pop", width), ()):
            operand = next(o for o in form.operands if o.name == form.bind["dst"])
            if isinstance(dst, RegLoc) and dst.name in ctx.classes[operand.cls] and not (flags and form.flags_write):
                sequence = [MInstr(form, {operand.name: dst.name})]
                if not _conflicts(ctx, _writes(ctx, sequence[0]), busy | protected):
                    accept(sequence)
    else:
        search(src, [], (src,), 0)
        if width == 16 and split and best is None:
            pairs = [(ctx._part(src, p), ctx._part(dst, p), 8) for p in ("lo", "hi")]
            pieces = _parallel(ctx, pairs, busy, protected, flags)
            if pieces is not None:
                accept(pieces)
    ctx._move_cache[cache_key] = best
    return best


def _parallel(ctx, pairs, busy, held=frozenset(), flags=False):
    pending = []
    protected = set(held)
    for src, dst, width in pairs:
        if ctx._same(src, dst):
            protected.update(ctx._units(dst))
        elif (src, dst, width) not in pending:
            pending.append((src, dst, width))
    sequence = []
    temp_index = 0
    while pending:
        ready = [p for p in pending if not any(ctx._units(p[1]) & ctx._units(q[0]) for q in pending if q is not p)]
        if ready:
            selected = min(ready, key=lambda p: ctx._position(p[1]))
            src, dst, width = selected
            others = set(protected)
            for pair in pending:
                if pair is not selected:
                    others.update(ctx._units(pair[0]))
            moves = _move(ctx, src, dst, width, busy, others, flags)
            if moves is None:
                return None
            sequence.extend(moves)
            pending.remove(selected)
            protected.update(ctx._units(dst))
            continue
        selected = min(pending, key=lambda p: ctx._position(p[1]))
        src, dst, width = selected
        used = set(busy) | protected
        for left, right, _ in pending:
            used.update(ctx._units(left) | ctx._units(right))
        scratch, backup = None, None
        for temporary in ctx.registers:
            if _width(ctx.target, temporary.name) != width or ctx._units(temporary) & used:
                continue
            saved = _move(ctx, src, temporary, width, busy, used - ctx._units(src), flags)
            restored_protection = used - ctx._units(dst)
            if sum(a == src for a, _, _ in pending) > 1:
                restored_protection |= ctx._units(temporary)
            restored = _move(ctx, temporary, dst, width, busy, restored_protection, flags)
            if saved is not None and restored is not None:
                scratch, backup = temporary, saved
                break
        if backup is None and isinstance(src, RegLoc):
            for form in ctx.forms.get(("push", width), ()):
                operand = next(o for o in form.operands if o.name == form.bind["a"])
                if src.name in ctx.classes[operand.cls] and not (flags and form.flags_write):
                    trial = MInstr(form, {operand.name: src.name})
                    restored = _move(ctx, _StackLoc(width), dst, width, busy, used - ctx._units(dst), flags)
                    if not _conflicts(ctx, _writes(ctx, trial), used) and restored is not None:
                        scratch, backup = _StackLoc(width), [trial]
                        break
        if backup is None:
            if not ctx.pool:
                return None
            scratch = SlotLoc("ptemp", temp_index, 0, width // ctx.target.cell_bits)
            while ctx._units(scratch) & used:
                temp_index += 1
                scratch = SlotLoc("ptemp", temp_index, 0, width // ctx.target.cell_bits)
            backup = _move(ctx, src, scratch, width, busy, used - ctx._units(src), flags)
            if backup is None:
                return None
            temp_index += 1
        sequence.extend(backup)
        pending = [(scratch if a == src else a, b, w) for a, b, w in pending]
    return sequence


def _bindings(ctx, form, args, dst, busy):
    operands = {o.name: o for o in form.operands}
    initial = set().union(*(ctx._units(a) for a in args.values()))
    destination = form.bind.get("dst")
    tied = destination is not None and destination == form.bind.get("a")
    destinations = [None]
    if destination is not None:
        operand = operands[destination]
        if not isinstance(operand, R):
            return
        members = [RegLoc(r) for r in ctx.classes[operand.cls]]
        if tied:
            destinations = []
            for choice in (dst, args["a"], *members):
                if isinstance(choice, RegLoc) and choice.name in ctx.classes[operand.cls] and choice not in destinations:
                    explicit = choice == dst or choice == args["a"]
                    if explicit or not ctx._units(choice) & (initial | set(busy)):
                        destinations.append(choice)
        elif isinstance(dst, RegLoc) and dst.name in ctx.classes[operand.cls]:
            destinations = [dst]
        else:
            destinations = [r for r in members if not ctx._units(r) & (initial | set(busy))]
    for chosen in destinations:
        values = {destination: chosen.name} if destination is not None else {}
        pre, direct, used = [], set(), set()
        if chosen is not None:
            used.update(ctx._units(chosen))
            if tied:
                pre.append((args["a"], chosen, form.width))
        tasks = [(key, operands[name]) for key, name in form.bind.items() if key != "dst" and not (tied and key == "a")]
        def choose(index, actual, moves, held, selected):
            if index == len(tasks):
                yield actual, moves, held, chosen
                return
            key, operand = tasks[index]
            source = args[key]
            candidates = []
            if isinstance(operand, (R, P)):
                if isinstance(source, RegLoc) and source.name in ctx.classes[operand.cls]:
                    candidates.append((source.name, None, ctx._units(source)))
                else:
                    for name in ctx.classes[operand.cls]:
                        place = RegLoc(name)
                        if not ctx._units(place) & (initial | set(busy) | selected):
                            candidates.append((name, (source, place, _width(ctx.target, name)), frozenset()))
            elif isinstance(operand, I):
                if isinstance(source, Imm) and 0 <= source.value < 1 << operand.bits and (operand.values is None or source.value in operand.values):
                    candidates.append((source.value, None, frozenset()))
            elif isinstance(operand, M):
                if ctx.pool_private and isinstance(source, SlotLoc) and form.op not in ("ld", "st"):
                    return
                place = ctx._memory(operand, source)
                if place is not None:
                    candidates.append((place, None, ctx._units(place) if not isinstance(source, Imm) else frozenset()))
            for value, move, protected in candidates:
                if operand.name in actual and actual[operand.name] != value:
                    continue
                occupied = ctx._units(RegLoc(value)) if isinstance(operand, (R, P)) else frozenset()
                yield from choose(index + 1, {**actual, operand.name: value}, moves + ([move] if move else []), held | set(protected), selected | set(occupied))
        yield from choose(0, values, pre, direct, used)


def _generic(ctx, op, width, args, dst, busy, flags, repeat=1, only_form=None):
    result = []
    forms = [only_form] if only_form is not None else ctx.forms.get((op, width), ())
    for form in forms:
        for values, moves, held, chosen in _bindings(ctx, form, args, dst, busy):
            before = _parallel(ctx, moves, busy, held, flags)
            if before is None:
                continue
            main = MInstr(form, values)
            if _conflicts(ctx, _writes(ctx, main), busy):
                continue
            main_sequence = [main]
            if repeat > 1:
                source = next(o for o in form.operands if o.name == form.bind["a"])
                if not isinstance(source, R) or chosen is None or chosen.name not in ctx.classes[source.cls]:
                    continue
                following = MInstr(form, {**values, source.name: chosen.name})
                main_sequence.extend(following for _ in range(repeat - 1))
            after = []
            if dst is not None:
                after = _parallel(ctx, [(chosen, dst, width)], busy, flags=flags)
                if after is None:
                    continue
            result.append([*before, *main_sequence, *after])
    return result


def expand(ctx, instr, locs, busy, flags_must_survive=False):
    def position(arg):
        return arg if isinstance(arg, Imm) else locs[arg]
    sources = tuple(position(a) for a in instr.args)
    dst = locs[instr.dst] if instr.dst is not None else None
    width = instr.dst.width if instr.dst is not None else instr.args[-1].width
    op, flags = instr.op, flags_must_survive
    candidates = []
    if op in ("mov", "const"):
        candidate = _move(ctx, sources[0], dst, width, busy, set(), flags)
        if candidate is not None:
            candidates.append(candidate)
    elif op in ("trunc", "hi", "zext", "pack"):
        if op in ("trunc", "hi"):
            pairs = [(ctx._part(sources[0], "lo" if op == "trunc" else "hi"), dst, 8)]
        elif op == "zext":
            pairs = [(sources[0], ctx._part(dst, "lo"), 8), (Imm(0, 8), ctx._part(dst, "hi"), 8)]
        else:
            pairs = [(sources[0], ctx._part(dst, "hi"), 8), (sources[1], ctx._part(dst, "lo"), 8)]
        candidate = _parallel(ctx, pairs, busy, flags=flags)
        if candidate is not None:
            candidates.append(candidate)
    else:
        canonical = ("ld" if isinstance(sources[0], Imm) else "ld_ind") if op == "load" else ("st" if isinstance(sources[0], Imm) else "st_ind") if op == "store" else op
        args = dict(zip(("a", "b"), sources))
        if op in ("load", "store") and isinstance(sources[0], Imm):
            args["a"] = MemLoc(instr.meta["space"], sources[0].value)
        arrangements = [args]
        if canonical in ("add", "and", "or", "xor"):
            arrangements.append({"a": sources[1], "b": sources[0]})
        for arguments in arrangements:
            if op in ("load", "store"):
                for form in ctx.forms.get((canonical, width), ()):
                    address = next(o for o in form.operands if o.name == form.bind["a"])
                    if address.space == instr.meta["space"]:
                        candidates.extend(_generic(ctx, canonical, width, arguments, dst, busy, flags, only_form=form))
            else:
                candidates.extend(_generic(ctx, canonical, width, arguments, dst, busy, flags))
        if op in ("shl", "shr") and isinstance(sources[1], Imm):
            count = sources[1].value
            if count > 0:
                for form in ctx.forms.get((op, width), ()):
                    operand = next(o for o in form.operands if o.name == form.bind["b"])
                    if isinstance(operand, I) and (operand.values is None or 1 in operand.values):
                        candidates.extend(_generic(ctx, op, width, {"a": sources[0], "b": Imm(1, width)}, dst, busy, flags, count, form))
        if op == "not":
            candidates.extend(_generic(ctx, "xor", width, {"a": sources[0], "b": Imm((1 << width) - 1, width)}, dst, busy, flags))
        if op == "neg":
            candidates.extend(_generic(ctx, "sub", width, {"a": Imm(0, width), "b": sources[0]}, dst, busy, flags))
    if not candidates:
        return None
    selected = min(candidates, key=ctx._key)
    return Expansion(selected, ctx._key(selected)[:2])


def expand_branch(ctx, cond, a, b, locs, busy, next_block):
    return _expand_branch(ctx, cond, a, b, locs, busy, next_block, msb0_ok=False)


def _expand_branch(ctx, cond, a, b, locs, busy, next_block, *, msb0_ok):
    mirror = {"eq": "eq", "ne": "ne", "ltu": "gtu", "gtu": "ltu", "leu": "geu", "geu": "leu"}
    inverse = {"eq": "ne", "ne": "eq", "ltu": "geu", "geu": "ltu", "gtu": "leu", "leu": "gtu"}
    def position(value):
        return value if isinstance(value, Imm) else locs[value]
    yes, no = ctx._branch_targets
    candidates = []
    for left, right, desired in ((a, b, cond), (b, a, mirror[cond])):
        for compare in ctx.forms.get(("cmp", a.width), ()):
            for branch in ctx.forms.get(("br", 0), ()):
                flag, tested = next(iter(branch.flags_read.items()))
                word = (compare.flags_write or {}).get(flag)
                if word not in _FLAG_RELATIONS or (word in _MSB0_FLAGS and msb0_ok is not True):
                    continue
                relation = _FLAG_RELATIONS[word][tested]
                if relation == desired:
                    taken, falling = yes, no
                elif relation == inverse[desired]:
                    taken, falling = no, yes
                else:
                    continue
                plans = _generic(ctx, "cmp", a.width, {"a": position(left), "b": position(right)}, None, busy, False, only_form=compare)
                for sequence in plans:
                    operand = next(o for o in branch.operands if isinstance(o, T))
                    branch_instruction = MInstr(branch, {operand.name: _TargetRef(taken)}, _busy=frozenset(busy))
                    if _conflicts(ctx, _writes(ctx, branch_instruction), busy):
                        continue
                    emitted = [*sequence, branch_instruction]
                    if next_block != falling and not ctx._optimistic:
                        jumps = ctx.forms.get(("jmp", 0), ())
                        if not jumps:
                            continue
                        for jump in jumps:
                            target = next(o for o in jump.operands if isinstance(o, T))
                            item = MInstr(jump, {target.name: _TargetRef(falling)}, _busy=frozenset(busy))
                            if not _conflicts(ctx, _writes(ctx, item), busy):
                                candidates.append([*emitted, item])
                    else:
                        candidates.append(emitted)
    if not candidates:
        return None
    selected = min(candidates, key=ctx._key)
    return Expansion(selected, ctx._key(selected)[:2])
