"""Gap enumeration and allocation-side park plans."""

from collections import defaultdict
from dataclasses import dataclass

from .expand import MInstr
from .ir import _ir_defs_uses, _live, _successors
from .target import units


@dataclass(frozen=True)
class _Gap:
    number: int
    value: object
    kind: str
    start: tuple
    end: tuple
    covered: frozenset


def _before(instruction, live_after):
    defined, used = _ir_defs_uses(instruction)
    return (live_after - defined) | used


def _regions(function):
    blocks = {b.id: b for b in function.blocks}
    entry = function.blocks[0].id
    successors = {bid: set(_successors(b)) for bid, b in blocks.items()}
    reached, pending = set(), [entry]
    while pending:
        bid = pending.pop()
        if bid not in reached:
            reached.add(bid)
            pending.extend(sorted(successors[bid]))
    predecessors = {bid: {p for p in reached if bid in successors[p]} for bid in reached}
    dominators = {bid: ({entry} if bid == entry else set(reached)) for bid in reached}
    while True:
        updated = {bid: ({entry} if bid == entry else
                         {bid} | set.intersection(*(dominators[p] for p in predecessors[bid])))
                   for bid in sorted(reached)}
        if updated == dominators:
            break
        dominators = updated
    for head in sorted(reached):
        latches = sorted(p for p in predecessors[head] if head in dominators[p])
        if not latches:
            continue
        loop = {head}
        pending = latches
        while pending:
            bid = pending.pop()
            if bid not in loop:
                loop.add(bid)
                pending.extend(sorted(predecessors[bid]))
        outside = predecessors[head] - loop
        exits = set().union(*(successors[b] for b in loop)) - loop
        if len(outside) != 1 or len(exits) != 1:
            continue
        before, = outside
        after, = exits
        if blocks[before].term.op == "jmp" and predecessors[after] <= loop:
            yield before, after, loop


class _ParkPlan:
    def __init__(self, function, ctx, candidates):
        self.ctx = ctx
        self.base = _live(function)[2]
        self.instructions = {(b.id, i): ins for b in function.blocks
                             for i, ins in enumerate((*b.instrs, b.term))}
        self.before = {point: _before(ins, self.base[point]) for point, ins in self.instructions.items()}
        self.forms = {}
        for location in ctx.registers:
            pair = []
            for op in ("push", "pop"):
                forms = [f for f in ctx.forms.get((op, ctx.target.cell_bits * len(units(ctx.target, location))), ())
                         if location.name in ctx.classes[f.operands[0].cls]]
                if forms:
                    pair.append(min(forms, key=lambda f: (f.bytes, ctx.form_order[f.id])))
            if len(pair) == 2:
                self.forms[location] = tuple(pair)
        self.candidates = {v: tuple(loc for loc in choices if loc in self.forms)
                           for v, choices in candidates.items()}
        self.gaps = tuple(self._enumerate(function)) if self.forms else ()

    def _enumerate(self, function):
        number = 0
        entry = function.blocks[0].id
        sizes = {b.id: len(b.instrs) for b in function.blocks}
        mentions = {point: set.union(*_ir_defs_uses(ins)) for point, ins in self.instructions.items()}
        regions = tuple(_regions(function))
        for value, locations in self.candidates.items():
            if not locations:
                continue
            for bid, end in sizes.items():
                begin = int(bid == entry)
                boundaries = [i for i in range(begin, end) if value in mentions[bid, i]]
                for prior, last in zip([begin - 1, *boundaries], [*boundaries, end]):
                    first = prior + 1
                    if first < last and value in self.before[bid, first] and value in self.before[bid, last]:
                        yield _Gap(number, value, "B", (bid, first), (bid, last),
                                   frozenset((bid, i) for i in range(first, last)))
                        number += 1
            for before, after, loop in regions:
                start, end = (before, sizes[before]), (after, 0)
                covered = frozenset(point for point in self.instructions if point[0] in loop)
                if (value in self.base[start] and value in self.before[end]
                        and all(value not in mentions[point] for point in covered)):
                    yield _Gap(number, value, "R", start, end, covered)
                    number += 1

    def choose(self, point, pinned, mentions):
        selected = self.ctx.parked
        for gap in sorted(self.gaps, key=lambda g: (mentions[g.value], g.value.id,
                                                    -len(g.covered), g.kind != "R", g.start)):
            if gap.value in pinned or gap in selected or point not in gap.covered:
                continue
            if any(gap.covered & other.covered and not
                   (gap.covered <= other.covered or other.covered <= gap.covered) for other in selected):
                continue
            for p in gap.covered:
                ins = self.instructions[p]
                depth = self.ctx.callee_depths.get(ins.meta["callee"], 0) if ins.op == "call" else 0
                if 1 + sum(p in g.covered for g in selected) + depth > self.ctx.park_depth:
                    break
            else:
                return gap
        return None

    def masks(self, selected):
        masked = defaultdict(set)
        for g in selected:
            for point in g.covered:
                masked[point].add(g.value)
        return masked

    def events(self, selected):
        result = defaultdict(list)
        for gap in sorted(selected, key=lambda g: (len(g.covered), -g.value.id)):
            result[gap.end].append((gap, "pop"))
        for gap in sorted(selected, key=lambda g: (-len(g.covered), g.value.id)):
            result[gap.start].append((gap, "push"))
        return result

    def boundary(self, point, events, masks):
        parked = masks[point] - {g.value for g, op in events if op == "push"}
        parked |= {g.value for g, op in events if op == "pop"}
        for gap, op in events:
            if op == "pop":
                parked.discard(gap.value)
            yield gap, op, self.before[point] - parked - {gap.value}
            if op == "push":
                parked.add(gap.value)

    def sequence(self, point, locations, *, partial=False):
        events = self.ctx.park_events.get(point, ())
        result = []
        for gap, op, live in self.boundary(point, events, self.ctx.park_masks):
            if gap.value not in locations and partial:
                continue
            location = locations[gap.value]
            if location not in self.forms:
                return None
            form = self.forms[location][op == "pop"]
            writes = set().union(*(units(self.ctx.target, name) for name in form.clobbers))
            if op == "pop":
                writes.update(units(self.ctx.target, location))
            elif writes & units(self.ctx.target, location):
                return None
            if any(writes & units(self.ctx.target, locations[v]) for v in live if v in locations):
                return None
            result.append(MInstr(form, {form.operands[0].name: location.name}, _park=(gap.value.id, gap.number)))
        return result
