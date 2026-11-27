"""Block order scoring and monotone branch relaxation."""

from dataclasses import replace
from itertools import permutations

from .assemble import _arguments
from .emit import _label_blocks
from .errors import UnsupportedError
from .expand import MInstr, _TargetRef, _expand_branch
from .frames import _Builder
from .target import T, units


def _renumber(mir, target, name):
    labels = {}
    number = 1
    for instruction in mir:
        if instruction._label is not None:
            if instruction._label == name:
                labels[instruction._label] = name
            else:
                labels[instruction._label] = target.local_label.format(fn=name, n=number)
                number += 1
    return tuple(replace(m, _label=labels[m._label] if m._label is not None else None,
                         values={k: labels.get(v, v) if m.op in ("jmp", "br") and any(isinstance(o, T) and o.name == k for o in m.form.operands) else v
                                 for k, v in m.values.items()}) for m in mir)


def _relax(mir, target, name):
    sequence = list(mir)
    def safe(form, instruction):
        return not any(units(target, name) & instruction._busy for name in form.clobbers)
    def candidates(instruction):
        return sorted((f for f in target.forms if f.op == instruction.op
                       and safe(f, instruction) and (instruction.op != "br" or f.flags_read == instruction.form.flags_read)),
                      key=lambda f: (f.bytes, target.forms.index(f)))
    # Callers supply shortest forms; a final pass after P starts again from shortest.
    for index, instruction in enumerate(sequence):
        if instruction.op in ("jmp", "br"):
            form = candidates(instruction)[0]
            old = next(o for o in instruction.form.operands if isinstance(o, T))
            operand = next(o for o in form.operands if isinstance(o, T))
            sequence[index] = replace(instruction, form=form, values={operand.name: instruction.values[old.name]})
    while True:
        addresses, labels, address = {}, {}, 0
        for index, instruction in enumerate(sequence):
            addresses[index] = address
            if instruction._label is not None:
                labels[instruction._label] = address
            address += instruction.bytes
        changed = False
        for index, instruction in enumerate(sequence):
            if instruction.op not in ("jmp", "br"):
                continue
            try:
                _arguments(instruction, addresses[index], labels)
                continue
            except UnsupportedError:
                pass
            current = next(o for o in instruction.form.operands if isinstance(o, T))
            destination = instruction.values[current.name]
            ordered = candidates(instruction)
            forms = ordered[ordered.index(instruction.form) + 1:]
            if forms:
                form = forms[0]
                operand = next(o for o in form.operands if isinstance(o, T))
                sequence[index] = replace(instruction, form=form, values={operand.name: destination})
            elif instruction.op == "br" and target.long_branch == "invert_over_jump":
                flag, value = next(iter(instruction.form.flags_read.items()))
                opposite = [f for f in target.forms if f.op == "br" and safe(f, instruction) and f.flags_read == {flag: 1 - value}]
                jumps = [f for f in target.forms if f.op == "jmp" and safe(f, instruction) and any(isinstance(o, T) and o.abs_bits is not None for o in f.operands)]
                if not opposite or not jumps:
                    raise UnsupportedError(f"{name}: missing opposite branch/absolute jump for long conditional")
                branch = min(opposite, key=lambda f: (f.bytes, target.forms.index(f)))
                jump = min(jumps, key=lambda f: (f.bytes, target.forms.index(f)))
                number = 1 + sum(m._label is not None for m in sequence)
                skip = target.local_label.format(fn=name, n=number)
                while skip in labels:
                    number += 1
                    skip = target.local_label.format(fn=name, n=number)
                boperand = next(o for o in branch.operands if isinstance(o, T))
                joperand = next(o for o in jump.operands if isinstance(o, T))
                sequence[index:index + 1] = [replace(instruction, form=branch, values={boperand.name: skip}),
                    replace(instruction, form=jump, values={joperand.name: destination}),
                    replace(instruction, form=None, values={}, _label=skip)]
            else:
                raise UnsupportedError(f"{name}: branch to {destination!r} exceeds all declared ranges")
            changed = True
            break
        if not changed:
            return _renumber(sequence, target, name)


def _layout(function, blocks, locations, ctx, interfaces):
    mapping = {b.id: b for b in function.blocks}
    entry = function.blocks[0].id
    prefix = {bid: [m for m in sequence if m._origin is not None and (m._role == "park" or m._origin[1] < len(mapping[bid].instrs))] for bid, sequence in blocks.items()}
    removable = {b.id: b.term.meta["target"] for b in function.blocks if b.id != entry and not prefix[b.id] and b.term.op == "jmp"}
    for bid in sorted(tuple(removable)):
        seen, current = [], bid
        while current in removable:
            if current in seen:
                del removable[min(seen[seen.index(current):])]
                break
            seen.append(current)
            current = removable[current]
    def redirect(bid):
        while bid in removable:
            bid = removable[bid]
        return bid
    identifiers = sorted(bid for bid in blocks if bid not in removable)
    builder = _Builder(function, ctx, locations, interfaces)
    points = builder.points
    term_cache = {}
    def tail(bid, following):
        key = bid, following
        if key in term_cache:
            return term_cache[key]
        term, index = mapping[bid].term, len(mapping[bid].instrs)
        if term.op == "ret":
            sequence = [m for m in blocks[bid] if m._origin is not None and m._origin[1] == index and m._role != "park"]
        elif term.op == "jmp":
            destination = redirect(term.meta["target"])
            if destination == following:
                sequence = []
            else:
                busy = frozenset(builder._units(points[bid,index]))
                forms = [f for f in ctx.forms["jmp", 0] if not any(units(ctx.target, name) & busy for name in f.clobbers)]
                if not forms:
                    raise UnsupportedError(f"{function.name}: no jump form preserves live units in block {bid}")
                form = min(forms, key=lambda f: (f.bytes, ctx.form_order[f.id]))
                operand = next(o for o in form.operands if isinstance(o, T))
                sequence = builder._tag([MInstr(form,{operand.name:_TargetRef(destination)}, _busy=busy)],bid,index,"jump")
        else:
            ctx._branch_targets = redirect(term.meta["true"]), redirect(term.meta["false"])
            result = _expand_branch(ctx, term.meta["cond"], *term.args, locations,
                                    builder._units(points[bid, index]), following,
                                    msb0_ok=term.meta.get("msb0_ok"))
            if result is None:
                raise UnsupportedError(f"{function.name}: no branch sequence for block {bid}")
            sequence = builder._tag(result.mir,bid,index,"branch")
        term_cache[key] = sequence
        return sequence
    def emit(order):
        arranged = {bid:[*prefix[bid],*tail(bid,order[index+1] if index+1<len(order) else None)] for index,bid in enumerate(order)}
        return _label_blocks(function,arranged,order,ctx.target)
    scores = {}
    def score(order):
        key = tuple(order)
        if key not in scores:
            try:
                sequence = _relax(emit(order),ctx.target,function.name)
                scores[key] = sum(m.bytes for m in sequence), sum(m.form is not None or m._asm is not None for m in sequence)
            except UnsupportedError:
                scores[key] = (float("inf"),float("inf"))
        return scores[key], key
    if len(identifiers) <= 8:
        order = min(((entry,*rest) for rest in permutations(b for b in identifiers if b != entry)),key=score)
    else:
        order = [entry]
        while len(order) < len(identifiers):
            remaining = [b for b in identifiers if b not in order]
            current = order[-1]
            normal = sum(m.bytes for m in tail(current,None))
            saving = [b for b in remaining if sum(m.bytes for m in tail(current,b)) < normal]
            order.append(min(saving or remaining))
        for _ in range(20):
            candidates = {tuple(order)}
            for old in range(1,len(order)):
                for new in range(1,len(order)):
                    trial = list(order)
                    item = trial.pop(old)
                    trial.insert(new,item)
                    candidates.add(tuple(trial))
            for index in range(1,len(order)-1):
                trial = list(order)
                trial[index:index+2] = reversed(trial[index:index+2])
                candidates.add(tuple(trial))
            best = min(sorted(candidates),key=score)
            if score(best)[0] >= score(order)[0]:
                break
            order = list(best)
    if score(order)[0][0] == float("inf"):
        raise UnsupportedError(f"{function.name}: no layout fits declared branch ranges")
    return emit(order)
