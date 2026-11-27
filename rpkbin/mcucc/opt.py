"""Conservative fixed-point IR simplification without register renumbering."""

from collections import Counter

from .ir import Imm, Instr, VReg, _ir_defs_uses, _live, _successors
from .opsem import condition_holds, evaluate


def _resolve(value, copies):
    seen = set()
    while isinstance(value, VReg) and value in copies and value not in seen:
        seen.add(value)
        value = copies[value]
    return value


def _definitions(function):
    result = {}
    for block in function.blocks:
        for index, instruction in enumerate(block.instrs):
            for value in sorted(_ir_defs_uses(instruction)[0], key=lambda v: v.id):
                result.setdefault(value, []).append((block.id, index, instruction))
    return result


def _dominators(function):
    ids = {b.id for b in function.blocks}
    entry = function.blocks[0].id
    predecessors = {b.id: [] for b in function.blocks}
    for block in function.blocks:
        for successor in _successors(block):
            predecessors[successor].append(block.id)
    dom = {b.id: {entry} if b.id == entry else set(ids) for b in function.blocks}
    changed = True
    while changed:
        changed = False
        for block in function.blocks[1:]:
            new = {block.id} | (set.intersection(*(dom[p] for p in predecessors[block.id])) if predecessors[block.id] else set())
            if new != dom[block.id]:
                dom[block.id], changed = new, True
    return dom


def _propagate(function):
    definitions = _definitions(function)
    dom = _dominators(function)
    candidates = {}
    for value, locations in definitions.items():
        if len(locations) != 1:
            continue
        bid, index, instruction = locations[0]
        if instruction.op in ("mov", "const"):
            src = instruction.args[0]
            if isinstance(src, Imm) or len(definitions.get(src, ())) == 1:
                candidates[value] = (src, bid, index)
    for block in function.blocks:
        copies = {}
        for index, instruction in enumerate((*block.instrs, block.term)):
            def substitute(value):
                value = _resolve(value, copies)
                seen = set()
                while isinstance(value, VReg) and value in candidates and value not in seen:
                    source, bid, defined_at = candidates[value]
                    if bid not in dom[block.id] or (bid == block.id and defined_at >= index):
                        break
                    seen.add(value)
                    value = _resolve(source, copies)
                return value
            if instruction.op not in ("entry","asm"):
                instruction.args = tuple(substitute(a) for a in instruction.args)
            defined, _ = _ir_defs_uses(instruction)
            copies = {k: v for k, v in copies.items() if k not in defined and not (isinstance(v, VReg) and v in defined)}
            if instruction.op in ("mov", "const") and instruction.dst != instruction.args[0]:
                copies[instruction.dst] = instruction.args[0]


def _constant_fold(function):
    numeric = {"mov", "const", "add", "sub", "and", "or", "xor", "not", "neg", "shl", "shr"}
    for block in function.blocks:
        for instruction in block.instrs:
            if instruction.dst is None or not instruction.args or not all(isinstance(a, Imm) for a in instruction.args):
                continue
            values = [a.value for a in instruction.args]
            width = instruction.dst.width
            if instruction.op in numeric:
                value = evaluate(instruction.op, width, *values)
            elif instruction.op in ("trunc", "zext"):
                value = values[0] & ((1 << width) - 1)
            elif instruction.op == "hi":
                value = values[0] >> 8
            elif instruction.op == "pack":
                value = (values[0] << 8) | values[1]
            else:
                continue
            instruction.op, instruction.args = "const", (Imm(value, width),)
        term = block.term
        if term.op == "br" and all(isinstance(a, Imm) for a in term.args):
            a, b = term.args
            edge = "true" if condition_holds(term.meta["cond"], a.value, b.value, a.width) else "false"
            block.term = Instr("jmp", None, (), {"target": term.meta[edge]})


def _algebra(function):
    for block in function.blocks:
        producers = {}
        for index, instruction in enumerate(block.instrs):
            op, args = instruction.op, instruction.args
            if instruction.dst is None:
                continue
            replacement = None
            if len(args) == 2:
                a, b = args
                if isinstance(b, Imm) and b.value == 0 and op in ("add", "sub", "or", "xor", "shl", "shr"):
                    replacement = a
                if op == "and" and isinstance(b, Imm):
                    if b.value == 0:
                        replacement = Imm(0, instruction.dst.width)
                    elif b.value == (1 << instruction.dst.width) - 1:
                        replacement = a
                if a == b and op in ("sub", "xor"):
                    replacement = Imm(0, instruction.dst.width)
                if op in ("add", "sub") and isinstance(b, Imm) and a in producers:
                    previous, position = producers[a]
                    if previous.op in ("add", "sub") and isinstance(previous.args[1], Imm):
                        base, constant = previous.args
                        changed_units = set().union(*(_ir_defs_uses(i)[0] for i in block.instrs[position + 1:index]))
                        if not isinstance(base, VReg) or (base != previous.dst and base not in changed_units):
                            merged = constant.value + (b.value if previous.op == op else -b.value)
                            instruction.op = previous.op
                            instruction.args = base, Imm(merged & ((1 << instruction.dst.width) - 1), instruction.dst.width)
            if replacement is not None:
                instruction.op = "const" if isinstance(replacement, Imm) else "mov"
                instruction.args = (replacement,)
            producers[instruction.dst] = instruction, index


def _pack_patterns(function):
    definitions = _definitions(function)
    for block in function.blocks:
        local = {}
        for index, instruction in enumerate(block.instrs):
            if instruction.op == "or" and instruction.dst.width == 16:
                for high, low in (instruction.args, tuple(reversed(instruction.args))):
                    upper, lower = local.get(high), local.get(low)
                    if upper is None or lower is None:
                        continue
                    shift, upper_index = upper
                    extend_low, low_index = lower
                    if shift.op != "shl" or shift.args[1] != Imm(8, 16) or extend_low.op != "zext":
                        continue
                    extend_high = local.get(shift.args[0])
                    if extend_high is None or extend_high[0].op != "zext":
                        continue
                    h, lo = extend_high[0].args[0], extend_low.args[0]
                    changes = set().union(*(_ir_defs_uses(i)[0] for i in block.instrs[min(extend_high[1], low_index) + 1:index]))
                    if (isinstance(h, VReg) and h in changes) or (isinstance(lo, VReg) and lo in changes):
                        continue
                    instruction.op, instruction.args = "pack", (h, lo)
                    break
            if instruction.dst is not None and len(definitions.get(instruction.dst, ())) == 1:
                local[instruction.dst] = instruction, index


def _value_numbering(function):
    for block in function.blocks:
        known = {}
        for instruction in block.instrs:
            defined, _ = _ir_defs_uses(instruction)
            known = {key: value for key, value in known.items() if value not in defined and not any(a in defined for a in key[2] if isinstance(a, VReg))}
            if instruction.op in ("store", "call", "asm"):
                known = {key: value for key, value in known.items() if key[0] != "load"}
            if instruction.dst is None or instruction.op in ("call", "asm"):
                continue
            key = instruction.op, instruction.dst.width, instruction.args, instruction.meta.get("space")
            previous = known.get(key)
            if previous is not None:
                instruction.op, instruction.args = "mov", (previous,)
            elif instruction.dst not in instruction.args:
                known[key] = instruction.dst


def _dead_code(function):
    _, _, points = _live(function)
    for block in function.blocks:
        kept = []
        for index, instruction in enumerate(block.instrs):
            if instruction.op == "entry":
                instruction.args = tuple(v for v in instruction.args if v in points[block.id, index])
            elif instruction.op not in ("call", "store", "asm") and instruction.dst is not None and instruction.dst not in points[block.id, index]:
                continue
            kept.append(instruction)
        block.instrs = kept


def _cfg(function):
    changed = True
    while changed:
        changed = False
        blocks = {b.id: b for b in function.blocks}
        def follow(bid):
            seen = set()
            while bid not in seen and not blocks[bid].instrs and blocks[bid].term.op == "jmp":
                seen.add(bid)
                bid = blocks[bid].term.meta["target"]
            return bid
        for block in function.blocks:
            term = block.term
            for key in (("target",) if term.op == "jmp" else ("true", "false") if term.op == "br" else ()):
                term.meta[key] = follow(term.meta[key])
            if term.op == "br" and term.meta["true"] == term.meta["false"]:
                block.term = Instr("jmp", None, (), {"target": term.meta["true"]})
        reached, pending = set(), [function.blocks[0].id]
        while pending:
            bid = pending.pop()
            if bid not in reached:
                reached.add(bid)
                pending.extend(_successors(blocks[bid]))
        if len(reached) != len(function.blocks):
            function.blocks = [b for b in function.blocks if b.id in reached]
            changed = True
        predecessors = Counter(s for b in function.blocks for s in _successors(b))
        for block in function.blocks:
            if block.term.op == "jmp":
                target = block.term.meta["target"]
                if target != block.id and target != function.blocks[0].id and predecessors[target] == 1:
                    other = blocks[target]
                    block.instrs.extend(other.instrs)
                    block.term = other.term
                    function.blocks = [b for b in function.blocks if b.id != target]
                    changed = True
                    break


def _signature(function):
    return repr([(b.id, [(i.op, i.dst, i.args, i.meta) for i in b.instrs], (b.term.op, b.term.args, b.term.meta)) for b in function.blocks])


def _optimize(function):
    for _ in range(20):
        before = _signature(function)
        _propagate(function)
        _constant_fold(function)
        _algebra(function)
        _pack_patterns(function)
        _value_numbering(function)
        _dead_code(function)
        _cfg(function)
        if _signature(function) == before:
            break
    return function
