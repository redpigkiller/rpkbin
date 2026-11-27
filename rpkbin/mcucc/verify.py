"""Independent checks on final forms, physical flow, locations and value flow."""

from dataclasses import replace

from .assemble import _arguments, _encode, _placement
from .errors import InternalError, TargetError, UnsupportedError, _EncodeLengthError
from .ir import Imm, VReg, _ir_defs_uses, _live, _successors
from .machine import _call_units, _callee_name, _in_pool, _reported_clobbers, _written_units, defs_uses
from .opsem import _MSB0_FLAGS, condition_holds, evaluate, flag_value
from .target import I, M, P, R, T, MemLoc, RegLoc, SlotLoc, _seq, part, units


def _fail(fn, check, detail):
    raise InternalError(f"{fn.name}: {check}: {detail}")


def _branch_msb0(block, width):
    """Reconstruct proof facts for each reaching definition in the final IR."""
    bound = 1 << (width - 1)
    facts = {}

    def depth(arg):
        return 0 if isinstance(arg, Imm) and arg.value < bound else facts.get(arg, 5)

    for instruction in block.instrs:
        dst = instruction.dst
        for value in instruction.meta.get("outs", ()):
            facts.pop(value, None)
        if dst is None:
            for value in _ir_defs_uses(instruction)[0]:
                facts.pop(value, None)
            continue
        args = instruction.args
        match instruction.op:
            case "const":
                proof = 0 if args[0].value < bound else 5
            case "zext":
                proof = 0 if args[0].width < width else 5
            case "and":
                proof = 0 if any(isinstance(arg, Imm) and arg.value < bound for arg in args) else 1 + min(map(depth, args))
            case "shr":
                proof = 0 if isinstance(args[1], Imm) and args[1].value >= 1 else 5
            case "mov":
                proof = 1 + depth(args[0])
            case _:
                proof = 5
        facts[dst] = proof
    return all(depth(arg) <= 4 for arg in block.term.args)


def _ordered(target, location):
    if isinstance(location, RegLoc):
        location = location.name
    if isinstance(location, MemLoc):
        return tuple((location.space, i) for i in reversed(range(location.addr, location.addr + location.n)))
    if isinstance(location, SlotLoc):
        return tuple(("slot", location.kind, location.key, i) for i in reversed(range(location.first, location.first + location.n)))
    return _seq(target, location)


def _copy_match(target, instruction, defined, other, locations):
    if instruction.dst != defined or other not in instruction.args:
        return False
    destination, source = locations[defined], locations[other]
    try:
        if instruction.op == "mov":
            return _ordered(target, destination) == _ordered(target, source)
        if instruction.op in ("trunc", "hi"):
            return units(target, destination) == units(target, part(target, source, "lo" if instruction.op == "trunc" else "hi"))
        if instruction.op == "zext":
            return units(target, source) == units(target, part(target, destination, "lo"))
        if instruction.op == "pack" and instruction.args[0] != instruction.args[1]:
            which = "hi" if instruction.args[0] == other else "lo"
            return units(target, source) == units(target, part(target, destination, which))
    except TargetError:
        return False
    return False


def _assignment(fn, target, points=None):
    if points is None:
        points = _live(fn._ir)[2]
    for block in fn._ir.blocks:
        for index, instruction in enumerate((*block.instrs, block.term)):
            defined, _ = _ir_defs_uses(instruction)
            for v in sorted(defined, key=lambda v: v.id):
                for w in sorted(points[block.id, index] - {v}, key=lambda v: v.id):
                    if units(target, fn._locations[v]) & units(target, fn._locations[w]) and not _copy_match(target, instruction, v, w, fn._locations):
                        _fail(fn, "V5", f"v{v.id}/v{w.id} overlap at {block.id}:{index}")
    return points


def _physical_cfg(fn):
    labels = {m._label: index for index, m in enumerate(fn.mir) if m._label is not None}
    successors = {}
    for index, instruction in enumerate(fn.mir):
        following = (index + 1,) if index + 1 < len(fn.mir) else ()
        if instruction.op in ("jmp", "br"):
            operand = next(o for o in instruction.form.operands if isinstance(o, T))
            destination = instruction.values[operand.name]
            if destination not in labels:
                _fail(fn, "V1", f"missing local destination {destination!r}")
            successors[index] = (labels[destination],) + (following if instruction.op == "br" else ())
        else:
            successors[index] = () if instruction.op == "ret" else following
    return successors


def _stack_flow(fn, callee_depths):
    successors = _physical_cfg(fn)
    incoming = {0: ()}
    pending = [0]
    peak = 0
    while pending:
        index = pending.pop()
        stack = incoming[index]
        m = fn.mir[index]
        park = m._role == "park"
        if park and (m.op not in ("push", "pop") or not isinstance(m._park, tuple)
                     or len(m._park) != 2 or any(type(n) is not int or n < 0 for n in m._park)):
            _fail(fn, "V11", f"invalid park marker at {index}")
        if m.op in ("push", "pop"):
            marker = (m._park, m.values[m.form.operands[0].name]) if park else None
            if m.op == "push":
                stack += ((m.form.width, marker),)
            else:
                if not stack or stack[-1][0] != m.form.width:
                    _fail(fn, "V3", f"unmatched pop at {index}")
                if stack[-1][1] != marker:
                    _fail(fn, "V11(a)", f"park pop does not match latest push at {index}")
                stack = stack[:-1]
        elif m.op == "ret" and stack:
            _fail(fn, "V3", f"nonzero return depth at {index}")
        depth = callee_depths.get(_callee_name(m), 0) if m.op == "call" else 0
        peak = max(peak, len(stack) + depth)
        for nxt in successors[index]:
            if nxt in incoming and incoming[nxt] != stack:
                check = "V3" if [pair[0] for pair in incoming[nxt]] != [pair[0] for pair in stack] else "V11(e)"
                _fail(fn, check, f"inconsistent stack at join {nxt}")
            if nxt not in incoming:
                incoming[nxt] = stack
                pending.append(nxt)
    return peak, incoming, successors


def _park_liveness(fn, target, incoming):
    original = _live(fn._ir)[2]
    points = {point: set(values) for point, values in original.items()}
    values = {v.id: v for v in fn._locations}
    blocks = {b.id: b for b in fn._ir.blocks}
    starts, ends, block_entry = {}, {}, {}
    seen_origins = set()
    for index, m in enumerate(fn.mir):
        if index not in incoming:
            if m._role == "park":
                _fail(fn, "V11", f"unreachable park at {index}")
            continue
        stack = [marker for _, marker in incoming[index] if marker is not None]
        if m._block is not None:
            block_entry.setdefault(m._block, tuple(marker[0] for marker in stack))
        if m._role != "park":
            if m._origin in original:
                seen_origins.add(m._origin)
                block, position = m._origin
                ins = (*blocks[block].instrs, blocks[block].term)[position]
                mentioned = set.union(*_ir_defs_uses(ins))
                for marker, _ in stack:
                    v = values[marker[0]]
                    if v in mentioned:
                        _fail(fn, "V11(b)", f"v{v.id} mentioned inside park at {index}")
            continue
        point = m._origin
        if point not in original or point[0] != m._block or point in seen_origins:
            _fail(fn, "V11", f"park is not before its IR point at {index}")
        v = values.get(m._park[0])
        register = m.values[m.form.operands[0].name]
        block, position = point
        ins = (*blocks[block].instrs, blocks[block].term)[position]
        defined, used = _ir_defs_uses(ins)
        live = (original[point] - defined) | used
        if v not in live or units(target, fn._locations[v]) != units(target, register):
            _fail(fn, "V11(c)", f"invalid park location or liveness at {index}")
        parked = {values[marker[0]] for marker, _ in stack}
        if m.op == "pop":
            written = set(units(target, register))
            written.update(unit for name in m.form.clobbers for unit in units(target, name))
            if any(written & units(target, fn._locations[w]) for w in live - parked - {v}):
                _fail(fn, "V11(d)", f"park restore overwrites a live value at {index}")
        endpoints = starts if m.op == "push" else ends
        if m._park in endpoints:
            _fail(fn, "V11(a)", f"duplicate park endpoint at {index}")
        endpoints[m._park] = point

    if starts.keys() != ends.keys():
        _fail(fn, "V11(a)", "park endpoint has no partner")
    for marker, start in starts.items():
        end = ends[marker]
        v = values[marker[0]]
        if start[0] == end[0]:
            if start[1] >= end[1]:
                _fail(fn, "V11(a)", "park endpoints reversed")
            covered = [(start[0], i) for i in range(start[1], end[1])]
        else:
            if start[1] != len(blocks[start[0]].instrs) or end[1] != 0:
                _fail(fn, "V11", "cross-block park does not use block boundaries")
            covered = []
            visited, pending = {start[0], end[0]}, list(_successors(blocks[start[0]]))
            while pending:
                bid = pending.pop()
                if bid in visited:
                    continue
                visited.add(bid)
                covered.extend((bid, i) for i in range(len(blocks[bid].instrs) + 1))
                pending.extend(_successors(blocks[bid]))
        for point in covered:
            bid, position = point
            ins = (*blocks[bid].instrs, blocks[bid].term)[position]
            if v in set.union(*_ir_defs_uses(ins)):
                _fail(fn, "V11(b)", f"v{v.id} mentioned at covered IR point {point}")
            points[point].discard(v)
    return points, block_entry


def _flow(fn, target, *, pool_private=False, incoming=None):
    if not fn.mir:
        _fail(fn, "V7", "missing machine return: empty function body")
    terminal = None
    for index, instruction in enumerate(fn.mir):
        if instruction._label is not None:
            terminal = None
        elif terminal is not None:
            _fail(fn, "V7", f"instruction {index} follows {terminal} before a label")
        elif instruction.op in ("ret", "jmp"):
            terminal = f"{instruction.op} at {index}"
    if incoming is None:
        _, incoming, successors = _stack_flow(fn, {})
    else:
        successors = _physical_cfg(fn)
    ir_blocks = {b.id: b for b in fn._ir.blocks}
    def return_target(bid):
        visited = set()
        while bid not in visited:
            visited.add(bid)
            term = ir_blocks[bid].term
            if term.op == "ret":
                return bid
            if term.op != "jmp":
                return None
            bid = term.meta["target"]
        return None
    returned = set()
    for index in sorted(incoming):
        instruction = fn.mir[index]
        if index + 1 == len(fn.mir) and instruction.op not in ("ret", "jmp"):
            _fail(fn, "V7", f"missing machine return: control falls out at {index}")
        if instruction.op == "ret":
            origin = instruction._origin
            block = ir_blocks.get(origin[0]) if origin is not None else None
            if block is None or origin[1] != len(block.instrs):
                _fail(fn, "V7", f"return has no IR terminator at {index}")
            term = block.term
            # P4 can replace a jump to a return with the return itself.
            destinations = (term.meta["target"],) if term.op == "jmp" else (
                term.meta["true"], term.meta["false"]) if term.op == "br" else ()
            targets = {block.id} if term.op == "ret" else {
                result for bid in destinations if (result := return_target(bid)) is not None}
            if not targets:
                _fail(fn, "V7", f"return has no corresponding IR ret at {index}")
            returned.update(targets)
    reachable = set()
    pending = [fn._ir.blocks[0].id]
    while pending:
        bid = pending.pop()
        if bid not in reachable:
            reachable.add(bid)
            pending.extend(_successors(ir_blocks[bid]))
    missing = sorted(bid for bid in reachable if ir_blocks[bid].term.op == "ret" and bid not in returned)
    if missing:
        _fail(fn, "V7", f"missing machine return for IR blocks {missing}")
    flags = {0: {f.name: frozenset({None}) for f in target.flags}}
    pending = [0]
    while pending:
        index = pending.pop()
        instruction = fn.mir[index]
        state = dict(flags[index])
        if instruction.op == "call":
            written, read = {f.name for f in target.flags}, set(instruction.form.flags_read or ())
        else:
            _, _, written, read = defs_uses(instruction, pool_private=pool_private)
        for name in sorted(read):
            for writer in sorted(state[name], key=lambda v: -1 if v is None else v):
                if writer is None:
                    _fail(fn, "V4", f"uninitialized flag {name!r} at {index}")
                producer = fn.mir[writer]
                semantic = (producer.form.flags_write or {}).get(name) if producer.form else None
                paired = producer.op == "cmp" and producer._origin == instruction._origin and semantic != "undef"
                zero_producer = semantic == "zero" and writer == index-1 and producer._block == instruction._block
                if not (paired or zero_producer):
                    _fail(fn, "V4", f"unpaired flag producer {writer} at {index}")
        for name in sorted(written):
            state[name] = frozenset({index})
        for nxt in successors[index]:
            if nxt not in flags:
                flags[nxt] = dict(state)
                pending.append(nxt)
            else:
                merged = {name: flags[nxt][name] | state[name] for name in state}
                if merged != flags[nxt]:
                    flags[nxt] = merged
                    pending.append(nxt)
    return incoming.keys()


def _origins(fn, target, reachable, interfaces, private):
    blocks = {b.id: (*b.instrs, b.term) for b in fn._ir.blocks}
    entry = fn._ir.blocks[0].id
    last = {}
    move_roles = {"entry": "entry", "return": "ret", "branch": "br",
                  "preserve-save": "prologue", "preserve-restore": "ret",
                  "call-args": "call", "call-result": "call",
                  "call-save": "call", "call-restore": "call",
                  "asm-save": "asm", "asm-restore": "asm"}
    before_effect = {"call-args", "call-save", "asm-save"}
    after_effect = {"call-result", "call-restore", "asm-restore"}
    effects, moves = {}, {}
    entered, split_blocks, straight_line = set(), set(), set()
    for index, machine in enumerate(fn.mir):
        block = machine._block
        if index == 0 or block != fn.mir[index - 1]._block:
            if block in entered:
                split_blocks.add(block)
            entered.add(block)
            before_jump = True
        if before_jump and index in reachable:
            straight_line.add(index)
        if machine.op in ("br", "jmp", "ret"):
            before_jump = False
        if machine._label is not None:
            continue
        if machine._origin is None:
            if index in reachable:
                _fail(fn, "V6", f"reachable {machine.op} has no IR origin at {index}")
            continue
        origin = machine._origin
        if not isinstance(origin, tuple) or len(origin) != 2 or any(type(n) is not int for n in origin):
            _fail(fn, "V6", f"invalid IR origin {origin!r} at {index}")
        bid, position = origin
        role = machine._role
        prologue = bid == entry and position == -1 and role == "preserve-save"
        if bid != machine._block or bid not in blocks or not (prologue or 0 <= position < len(blocks[bid])):
            _fail(fn, "V6", f"invalid IR origin {origin!r} at {index}")
        if position < last.get(bid, -1):
            _fail(fn, "V6", f"machine order disagrees with IR origin {origin!r} at {index}")
        last[bid] = position
        source = None if prologue else blocks[bid][position]
        op = source.op if source is not None else "prologue"
        if role == "park":
            continue
        if role in before_effect or role in after_effect:
            if move_roles[role] != op:
                _fail(fn, "V6", f"role {role!r} has no matching effect at {origin}")
            moves.setdefault(origin, []).append((index, role))
        if machine.op in ("call", "asm") and op == machine.op:
            effects.setdefault(origin, []).append(index)
            continue
        if machine.op == "st_ind" and op == "store" and isinstance(source.args[0], VReg):
            effects.setdefault(origin, []).append(index)
            continue
        if machine.op == "st":
            destination = machine.values[machine.form.bind["a"]]
            if op == "store" and isinstance(source.args[0], Imm) and destination == MemLoc(source.meta["space"], source.args[0].value):
                effects.setdefault(origin, []).append(index)
                continue
        if index not in reachable or machine.op not in ("st", "st_ind", "push", "call", "asm"):
            continue
        move = move_roles.get(role) == op or (
            role in ("body", "spill") and op not in ("entry", "ret", "jmp", "br", "call", "asm", "prologue"))
        if move and machine.op == "push":
            continue
        if move and machine.op == "st":
            if role == "entry":
                locations = (fn._locations[v] for v in source.args)
            elif role == "return":
                locations = (fn._ir.abi["ret"],)
            elif role == "call-args":
                locations = interfaces[source.meta["callee"]]["args"]
            elif role == "asm-restore":
                locations = fn._locations.values()
            else:
                locations = (fn._locations.get(source.dst),) if source is not None else ()
            allowed = private | set().union(*(units(target, loc) for loc in locations if isinstance(loc, MemLoc)))
            if units(target, destination) <= allowed:
                continue
        _fail(fn, "V6", f"unauthorized {machine.op} effect at {index}, origin {origin!r}, role {role!r}")

    for bid, instructions in blocks.items():
        for position, source in enumerate(instructions):
            op = source.op
            if op not in ("store", "call", "asm"):
                continue
            origin = bid, position
            sites = effects.get(origin, ())
            if not sites:
                _fail(fn, "V6", f"missing {op} effect at {origin}")
            if len(sites) != 1:
                _fail(fn, "V6", f"duplicate {op} effects at {origin}: {sites}")
            index = sites[0]
            if bid in split_blocks or index not in straight_line:
                _fail(fn, "V6", f"{op} effect at {origin} is outside its straight-line block")
            if op == "call" and _callee_name(fn.mir[index]) != source.meta["callee"]:
                _fail(fn, "V6", f"wrong call target at {origin}")
            for move, role in moves.get(origin, ()):
                if (move < index) != (role in before_effect):
                    side = "before" if role in before_effect else "after"
                    _fail(fn, "V6", f"role {role!r} must be {side} its {op} at {origin}")


def _piece(value, index, cell_bits):
    if isinstance(value, int):
        return (value >> (index * cell_bits)) & ((1 << cell_bits) - 1)
    if isinstance(value, tuple) and value[0] == "join":
        return value[1] if index else value[2]
    return ("piece", value, index)


def _join(high, low, cell_bits):
    if isinstance(high, int) and isinstance(low, int):
        return (high << cell_bits) | low
    if isinstance(high, tuple) and isinstance(low, tuple) and high[0] == low[0] == "piece" and high[1] == low[1] and high[2] == 1 and low[2] == 0:
        return high[1]
    return ("join", high, low)


def _expression(op, width, args):
    if all(isinstance(a, int) for a in args):
        return evaluate(op, width, *args)
    if op in ("mov", "const"):
        return args[0]
    if op == "not":
        op, args = "xor", (args[0], (1 << width) - 1)
    elif op == "neg":
        op, args = "sub", (0, args[0])
    if len(args) == 2:
        a, b = args
        if b == 0 and op in ("add", "sub", "or", "xor", "shl", "shr"):
            return a
        if op == "and" and b == (1 << width) - 1:
            return a
        if a == b and op in ("xor", "sub"):
            return 0
        if op in ("add", "and", "or", "xor"):
            args = tuple(sorted(args, key=repr))
        if op in ("shl", "shr") and isinstance(a, tuple) and a[:2] == (op, width) and isinstance(a[2][1], int) and isinstance(b, int):
            args = (a[2][0], a[2][1] + b)
    return (op, width, args)


class _State:
    def __init__(self, target, pool, *, pool_private):
        self.pool = pool
        self.pool_private = pool_private
        self.target, self.cells, self.stack = target, {}, []
        self.condition_values = {}
        self.memory_versions = {}

    def read(self, location):
        if isinstance(location, Imm):
            return location.value
        ordered = _ordered(self.target, location)
        values = [self.cells.get(u, ("unknown-store", self.memory_versions[u[0]], u)
                  if isinstance(u, tuple) and len(u) == 2 and u[0] in self.memory_versions
                  and not _in_pool(self.pool, *u) else ("initial", u)) for u in ordered]
        return values[0] if len(values) == 1 else _join(*values, self.target.cell_bits)

    def invalidate(self, space, token):
        self.memory_versions[space] = token
        for unit in list(self.cells):
            if (isinstance(unit, tuple) and len(unit) == 2 and unit[0] == space
                    and not _in_pool(self.pool, *unit)):
                del self.cells[unit]

    def write(self, location, value):
        ordered = _ordered(self.target, location)
        for index, unit in enumerate(reversed(ordered)):
            self.cells[unit] = value if len(ordered) == 1 else _piece(value, index, self.target.cell_bits)

    def machine(self, instruction, token, callee=None):
        if instruction.form is None:
            if instruction._asm:
                for unit in defs_uses(instruction, pool_private=self.pool_private)[0]:
                    self.cells[unit] = ("unknown-asm", token, unit)
                for value,location in zip(instruction._asm["out_values"],instruction._asm["out_locs"]):
                    self.write(location,("unknown-asm-result",token,value.id))
                if not instruction._asm["flags_ok"]:
                    self.condition_values.clear()
            return
        form, values = instruction.form, instruction.values
        operands = {o.name: o for o in form.operands}
        args = {}
        for arg, name in form.bind.items():
            operand, value = operands[name], values[name]
            args[arg] = value if isinstance(operand, I) else self.read(value)
        op, result = form.op, None
        if op in ("const", "mov", "ld"):
            result = args["a"]
        elif op in ("add", "sub", "and", "or", "xor", "not", "neg", "shl", "shr"):
            result = _expression(op, form.width, tuple(args[a] for a in ("a", "b") if a in args))
        elif op == "st":
            self.write(values[form.bind["a"]], args["b"])
        elif op == "push":
            self.stack.append(args["a"])
        elif op == "pop":
            if not self.stack:
                raise InternalError("V6: symbolic stack underflow")
            result = self.stack.pop()
        elif op == "ld_ind":
            result = ("unknown-load", token)
        elif op == "st_ind":
            space = operands[form.bind["a"]].space
            self.invalidate(space, token)
        if op == "call":
            self.condition_values.clear()
            for unit in _call_units(self.target, callee):
                self.cells[unit] = ("unknown-call", token, unit)
            ret = callee["ret"]
            if ret is not None:
                self.write(ret, ("unknown-result", token))
        for name in (() if op == "call" else form.clobbers):
            for unit in units(self.target, name):
                self.cells[unit] = ("clobbered", token, unit)
        if result is not None:
            self.write(values[form.bind["dst"]], result)
        for name,word in (form.flags_write or {}).items():
            if word == "zero":
                self.condition_values[name] = _expression("sub",form.width,(args["a"],args["b"])) if op == "cmp" else result
            else:
                self.condition_values.pop(name,None)


def _values(fn, target, points, interfaces, pool, *, pool_private, park_entries=None):
    function, locations = fn._ir, fn._locations
    physical_blocks = {m._block for m in fn.mir}
    ir_blocks = {b.id:b for b in function.blocks}
    def canonical(bid):
        visited = set()
        while bid not in physical_blocks and bid not in visited and ir_blocks[bid].term.op == "jmp":
            visited.add(bid)
            bid = ir_blocks[bid].term.meta["target"]
        return bid
    label_indices={m._label:index for index,m in enumerate(fn.mir) if m._label is not None}
    def edge(index,bid):
        visited=set()
        while index<len(fn.mir) and index not in visited:
            visited.add(index)
            machine=fn.mir[index]
            if machine._label is not None and machine._origin is None:
                return canonical(machine._block)
            if machine._block != bid:
                return canonical(machine._block)
            if machine.op=="jmp":
                destination=next(machine.values[o.name] for o in machine.form.operands if isinstance(o,T))
                index=label_indices[destination]
                continue
            if machine.op=="ret":
                return canonical(next(b.id for b in function.blocks if b.term.op=="ret"))
            index+=1
        _fail(fn,"V6",f"invalid physical edge in block {bid}")
    coverage = set()
    groups = {}
    for instruction in fn.mir:
        if instruction._origin is not None:
            groups.setdefault(instruction._origin, []).append(instruction)
    initial = _State(target, pool, pool_private=pool_private)
    for param, location in zip(function.params, function.abi["args"]):
        initial.write(location, ("argument", param.id, param.width))
    preserved = {name: initial.read(name) for name in function.abi.get("preserves", ())}
    prologue = groups.get((function.blocks[0].id, -1), ())
    for instruction in prologue:
        initial.machine(instruction, ("prologue", instruction._origin))
    saved_units = set().union(*(defs_uses(m, pool_private=pool_private)[0] for m in prologue))
    saved_cells = {unit: value for unit, value in initial.cells.items() if unit in saved_units and isinstance(unit, tuple)}
    saved_stack = list(initial.stack)
    written = _written_units(fn.mir, target, interfaces, pool_private=pool_private)
    for block in function.blocks:
        state = _State(target, pool, pool_private=pool_private)
        state.cells.update(saved_cells)
        state.stack = list(saved_stack)
        if block is function.blocks[0]:
            state.cells.update(initial.cells)
        else:
            for reg in target.regs:
                state.cells[reg.name] = ("block-boundary", block.id, reg.name)
            for name, value in preserved.items():
                if not units(target, name) & written:
                    state.write(name, value)
        parked = dict(park_entries.get(block.id, ())) if park_entries is not None else {}
        expected = {v: ("park", v.id, parked[v.id]) if v.id in parked else state.read(location)
                    for v, location in locations.items()}
        state.stack.extend(("park", value, gap) for value, gap in parked.items())
        values_by_id = {v.id: v for v in locations} if park_entries is not None else {}
        logical = _State(target, pool, pool_private=pool_private)
        logical.cells.update(state.cells)
        for index, instruction in enumerate((*block.instrs, block.term)):
            origin = block.id, index
            op = instruction.op
            defined, used = _ir_defs_uses(instruction)
            # P3 may redirect a read away from its original assigned location.
            # Compare the independently calculated expressions at observable stores,
            # branch/call boundaries, returns and all live-out locations instead.
            operands = [a.value if isinstance(a, Imm) else expected[a] for a in instruction.args]
            result = None
            if op == "entry":
                for value in instruction.args:
                    expected[value] = ("argument", value.id, value.width)
            elif op == "load":
                if isinstance(instruction.args[0], Imm):
                    result = logical.read(MemLoc(instruction.meta["space"], operands[0]))
                else:
                    coverage.add("indirect memory is abstract")
                    result = ("unknown-load", origin)
            elif op == "store":
                if isinstance(instruction.args[0], Imm):
                    logical.write(MemLoc(instruction.meta["space"], operands[0]), operands[1])
                else:
                    coverage.add("indirect memory is abstract")
                    logical.invalidate(instruction.meta["space"], origin)
            elif op == "call":
                coverage.add("call semantics are abstract; boundary moves checked")
                result = ("unknown-result", origin)
            elif op == "asm":
                coverage.add("inline assembly semantics are abstract")
                for value in instruction.meta["outs"]:
                    expected[value]=("unknown-asm-result",origin,value.id)
            elif op in ("trunc", "hi", "zext", "pack"):
                coverage.add("alias parts use symbolic cell views")
                if op in ("trunc", "hi"):
                    result = _piece(operands[0], int(op == "hi"), target.cell_bits)
                elif op == "zext":
                    result = _join(0, operands[0], target.cell_bits)
                else:
                    result = _join(*operands, target.cell_bits)
            elif instruction.dst is not None:
                result = _expression(op, instruction.dst.width, tuple(operands))
            sequence = groups.get(origin, ())
            for machine in sequence:
                if machine._role != "park":
                    break
                vid, gap = machine._park
                value = values_by_id[vid]
                marker = ("park", vid, gap)
                register = machine.values[machine.form.operands[0].name]
                if machine.op == "push":
                    if state.read(register) != expected[value]:
                        _fail(fn, "V11(e)", f"push does not hold v{vid} at {origin}")
                    state.stack.append(marker)
                    parked[vid] = gap
                else:
                    if not state.stack or state.stack.pop() != marker:
                        _fail(fn, "V11(e)", f"wrong symbolic park pop at {origin}")
                    state.write(register, expected[value])
                    del parked[vid]
                for name in machine.form.clobbers:
                    state.write(name, ("unknown-park-clobber", origin, name))
            sequence = tuple(m for m in sequence if m._role != "park")
            before = {v: state.read(locations[v]) for v in sorted(points[origin] - defined, key=lambda v: v.id)
                      if v.id not in parked}
            if op == "br" and not any(m.op == "br" for m in sequence):
                _fail(fn, "V4", f"missing conditional control flow at {origin}")
            if op == "jmp" and block.id in physical_blocks:
                indices = [i for i, m in enumerate(fn.mir) if m._block == block.id and m._origin == origin]
                if indices:
                    start = min(indices)
                else:
                    start = 1 + max(i for i, m in enumerate(fn.mir) if m._block == block.id)
                if edge(start, block.id) != canonical(instruction.meta["target"]):
                    _fail(fn, "V4", f"jump successor differs from IR at {origin}")
            comparison = None
            for machine in sequence:
                if machine.op in ("ld_ind", "st_ind"):
                    wanted = "load" if machine.op == "ld_ind" else "store"
                    address = next(o for o in machine.form.operands if o.name == machine.form.bind["a"])
                    if op != wanted or address.space != instruction.meta["space"] or state.read(machine.values[address.name]) != operands[0]:
                        _fail(fn, "V6", f"indirect space/pointer differs from IR at {origin}")
                    if op == "store" and state.read(machine.values[machine.form.bind["b"]]) != operands[1]:
                        _fail(fn, "V6", f"indirect stored value differs from IR at {origin}")
                if op == "br" and machine.op == "cmp":
                    bound = {}
                    declared = {o.name: o for o in machine.form.operands}
                    for key, name in machine.form.bind.items():
                        value = machine.values[name]
                        bound[key] = value if isinstance(declared[name], I) else state.read(value)
                    comparison = bound["a"], bound["b"], machine.form
                if op == "br" and machine.op == "br":
                    if comparison is None:
                        flag,required=next(iter(machine.form.flags_read.items()))
                        tested=operands[1] if operands[0]==0 else operands[0] if operands[1]==0 else None
                        if instruction.meta["cond"] not in ("eq","ne") or tested is None or state.condition_values.get(flag)!=tested:
                            _fail(fn,"V4",f"zero producer differs from IR comparison at {origin}")
                        destination=next(machine.values[o.name] for o in machine.form.operands if isinstance(o,T))
                        destination_block=edge(label_indices[destination],block.id)
                        machine_index=next(i for i,m in enumerate(fn.mir) if m is machine)
                        other=edge(machine_index+1,block.id)
                        if {destination_block,other}!={canonical(instruction.meta["true"]),canonical(instruction.meta["false"])}:
                            _fail(fn,"V4",f"zero branch successors differ at {origin}")
                        condition=instruction.meta["cond"]
                        if destination_block==canonical(instruction.meta["false"]):
                            condition="ne" if condition=="eq" else "eq"
                        elif destination_block!=canonical(instruction.meta["true"]):
                            _fail(fn,"V4",f"zero branch has wrong successor at {origin}")
                        if required != int(condition=="eq"):
                            _fail(fn,"V4",f"zero branch has wrong polarity at {origin}")
                        state.machine(machine,origin)
                        continue
                    left, right, cmp_form = comparison
                    if (left, right) == tuple(operands):
                        mirrored = False
                    elif (right, left) == tuple(operands):
                        mirrored = True
                    else:
                        _fail(fn, "V6", f"comparison operands differ from IR at {origin}")
                    flag, required = next(iter(machine.form.flags_read.items()))
                    semantic = cmp_form.flags_write[flag]
                    if semantic in _MSB0_FLAGS and (
                            cmp_form.width != instruction.args[0].width
                            or not _branch_msb0(block, cmp_form.width)):
                        _fail(fn, "V10", f"comparison at {origin} lacks proof of zero high bits")
                    destination = next(machine.values[o.name] for o in machine.form.operands if isinstance(o, T))
                    destination_block = edge(label_indices[destination],block.id)
                    machine_index=next(i for i,m in enumerate(fn.mir) if m is machine)
                    other=edge(machine_index+1,block.id)
                    if {destination_block,other}!={canonical(instruction.meta["true"]),canonical(instruction.meta["false"])}:
                        _fail(fn,"V6",f"branch successors differ at {origin}")
                    # Identical symbolic expressions cannot take different values.
                    samples = ((0, 0), (1, 1)) if operands[0] == operands[1] else ((0, 0), (0, 1), (1, 0))
                    for a, b in samples:
                        x, y = (b, a) if mirrored else (a, b)
                        taken = flag_value(semantic, "cmp", cmp_form.width, x, y, evaluate("cmp", cmp_form.width, x, y)) == required
                        expected_taken = condition_holds(instruction.meta["cond"], a, b, instruction.args[0].width)
                        if destination_block == canonical(instruction.meta["false"]):
                            expected_taken = not expected_taken
                        elif destination_block != canonical(instruction.meta["true"]):
                            _fail(fn, "V6", f"branch has wrong successor at {origin}")
                        if taken != expected_taken:
                            _fail(fn, "V4", f"branch condition differs from IR at {origin}")
                callee = interfaces[_callee_name(machine)] if machine.op == "call" else None
                if op=="call" and callee is not None:
                    for actual,value in zip(callee["args"],operands):
                        if state.read(actual)!=value:
                            _fail(fn,"V6",f"call argument differs at {origin}")
                if op=="asm" and machine._asm is not None:
                    for actual,value in zip(machine._asm["in_locs"],operands):
                        if state.read(actual)!=value:
                            _fail(fn,"V6",f"asm input differs at {origin}")
                state.machine(machine, origin, callee)
                if machine.op == "ret":
                    return_arg = next((b.term.args[0] for b in function.blocks if b.term.op == "ret" and b.term.args), None)
                    return_value = operands[0] if op == "ret" and operands else expected.get(return_arg)
                    if return_arg is not None and state.read(function.abi["ret"]) != return_value:
                        _fail(fn, "V7", f"wrong return value at {origin}")
                    for name, value in preserved.items():
                        if state.read(name) != value:
                            _fail(fn, "V7", f"preserved register {name!r} changed at return")
            for value, previous in before.items():
                if state.read(locations[value]) != previous:
                    _fail(fn, "V5/INV1", f"v{value.id} overwritten across {origin}")
            if instruction.dst is not None:
                expected[instruction.dst] = result
            if op == "store" and isinstance(instruction.args[0], Imm):
                location = MemLoc(instruction.meta["space"], operands[0])
                if state.read(location) != logical.read(location):
                    _fail(fn, "V6", f"wrong store at {origin}")
            if op == "call":
                roles = [m._role for m in sequence]
                if "call-restore" in roles and "call-result" in roles and roles.index("call-restore") < max(i for i, r in enumerate(roles) if r == "call-result"):
                    _fail(fn, "V5", f"restore precedes result at {origin}")
        for value in sorted(_live(function)[1][block.id], key=lambda v: v.id):
            if value.id not in parked and state.read(locations[value]) != expected[value]:
                _fail(fn, "V6", f"wrong live-out v{value.id} in block {block.id}")
    return tuple(sorted(coverage))


_POOL_MOVE_ROLES = frozenset({
    "spill", "reload", "entry", "return", "branch",
    "call-args", "call-result", "call-save", "call-restore",
    "preserve-save", "preserve-restore", "asm-save", "asm-restore",
})


def _verify_pool_access(fn, target, pool):
    slots = {(space, cell) for space, address, count in fn.slots
             for cell in range(address, address + count)}
    origins = {(block.id, index): instruction for block in fn._ir.blocks
               for index, instruction in enumerate((*block.instrs, block.term))}
    for index, instruction in enumerate(fn.mir):
        for location in instruction.values.values():
            if not isinstance(location, MemLoc):
                continue
            cells = units(target, location)
            if not any(_in_pool(pool, *unit) for unit in cells):
                continue
            source = origins.get(instruction._origin)
            source_access = (source is not None and
                (instruction.op == "st" and source.op == "store" or
                 instruction.op == "ld" and source.op == "load" and
                 isinstance(source.args[0], Imm) and
                 (source.meta["space"], source.args[0].value) in cells))
            if (source_access or instruction.op not in ("ld", "st") or
                    instruction._role not in _POOL_MOVE_ROLES or not cells <= slots):
                _fail(fn, "V8", f"unauthorized pool access at {index}: {location!r}")


def _verify(fn, target, config, interfaces, *, pool_private=False, callee_depths=None):
    forms = {f.id: f for f in target.forms}
    classes = {c.name: c.members for c in target.classes}
    spaces = {s.name: s for s in target.spaces}
    symbols, addresses, _, _ = _placement({fn.name: fn}, 0)
    for index, instruction in enumerate(fn.mir):
        if instruction._label is not None and (instruction.form is not None or instruction._asm is not None or instruction.values):
            _fail(fn, "V1", f"label carries a machine effect at {index}")
        if instruction.form is None:
            if instruction._asm is not None:
                fields={"rendered","nbytes","clobbers","flags_ok","in_locs","out_locs","out_values"}
                if set(instruction._asm)!=fields or instruction._origin is None:
                    _fail(fn,"V1",f"invalid asm metadata at {index}")
                bid,position=instruction._origin
                block=next((b for b in fn._ir.blocks if b.id==bid),None)
                if block is None or not 0<=position<len(block.instrs) or block.instrs[position].op!="asm":
                    _fail(fn,"V1",f"asm has no IR origin at {index}")
                source=block.instrs[position].meta
                for key in ("nbytes","clobbers","flags_ok"):
                    if instruction._asm[key]!=source[key]:
                        _fail(fn,"V1/V2",f"asm {key} differs from IR at {index}")
                if tuple(instruction._asm["out_values"])!=tuple(source["outs"]):
                    _fail(fn,"V1",f"asm output list differs at {index}")
                names=[]
                seen=set()
                for field,declared in (("in_locs",source["inputs"]),("out_locs",source["outputs"])):
                    actual=instruction._asm[field]
                    if len(actual)!=len(declared):
                        _fail(fn,"V1",f"asm operand count differs at {index}")
                    for location,(value,hint) in zip(actual,declared):
                        if not isinstance(location,RegLoc) or location.name not in {r.name for r in (*target.regs,*target.aliases)} or location!=fn._locations[value] or (hint is not None and location.name!=hint):
                            _fail(fn,"V1",f"asm register/hint differs at {index}")
                        if len(_ordered(target,location))*target.cell_bits!=value.width:
                            _fail(fn,"V1",f"asm register width differs at {index}")
                        if value not in seen:
                            seen.add(value)
                            names.append(location.name)
                if instruction._asm["rendered"]!=source["text"].format(*names):
                    _fail(fn,"V1",f"asm substitution differs at {index}")
            continue
        form = instruction.form
        if form.id not in forms or form != forms[form.id] or set(instruction.values) != {o.name for o in form.operands}:
            _fail(fn, "V1", f"invalid form/operands at {index}")
        for operand in form.operands:
            value = instruction.values[operand.name]
            if isinstance(operand, (R, P)):
                valid = value in classes[operand.cls]
            elif isinstance(operand, I):
                valid = type(value) is int and 0 <= value < 1 << operand.bits and (operand.values is None or value in operand.values)
            elif isinstance(operand, M):
                valid = isinstance(value, MemLoc) and value.space == operand.space and 0 <= value.addr and value.addr + value.n <= 1 << (operand.bits or spaces[operand.space].addr_bits)
            else:
                valid = isinstance(value, str)
            if not valid:
                _fail(fn, "V1", f"{form.id}.{operand.name}={value!r}")
        if form.op == "call":
            name = _callee_name(instruction)
            authority = interfaces.get(name)
            if authority is None:
                _fail(fn, "V5", f"missing authoritative callee interface for {name!r} at {index}")
            actual = instruction._callee_abi
            if not isinstance(actual, dict):
                _fail(fn, "V5", f"invalid callee interface for {name!r} at {index}")
            for key in sorted(actual.keys() | authority.keys(), key=repr):
                if key not in actual or key not in authority or actual[key] != authority[key]:
                    _fail(fn, "V5", f"callee {name!r} field {key!r} differs at {index}")
        try:
            _arguments(instruction, addresses[fn.name, index], symbols, unresolved=True)
            if form.encode is not None:
                _encode(instruction, addresses[fn.name, index], symbols, target, unresolved=True)
        except _EncodeLengthError:
            raise
        except (TargetError, UnsupportedError) as exc:
            _fail(fn, "V1/V2", str(exc))
    used = set()
    abi_memory = [loc for values in target.abi.arg_locs.values() for loc in values]
    abi_memory.extend(target.abi.ret_locs.values())
    for interface in interfaces.values():
        abi_memory.extend((*interface["args"], interface["ret"]))
    for space, address, count in fn.slots:
        if space not in config.spill_pool:
            _fail(fn, "V8", "slot without pool")
        low, high = config.spill_pool[space]
        cells = {(space, i) for i in range(address, address + count)}
        if count < 1 or address < low or address + count - 1 > high or cells & used:
            _fail(fn, "V8", "slot overlap or overflow")
        if any(cells & units(target, loc) for loc in abi_memory if isinstance(loc, MemLoc)):
            _fail(fn, "V8", "slot overlaps ABI memory")
        used.update(cells)
    pool = config.spill_pool if pool_private else {}
    if pool_private:
        _verify_pool_access(fn, target, pool)
        # Rebuild the effect bounds from authority, independently of MIR metadata.
        bounds = tuple(pool.items())
        fn = replace(fn, mir=tuple(replace(m, _pool_bounds=bounds) if m.op == "st_ind" else m
                                   for m in fn.mir))
    park_entries = points = incoming = None
    if any(m._role == "park" for m in fn.mir):
        depth, incoming, _ = _stack_flow(fn, callee_depths or {})
        if depth > config.park_depth:
            _fail(fn, "V3", f"push depth {depth} exceeds park_depth {config.park_depth}")
        points, park_entries = _park_liveness(fn, target, incoming)
    reachable = _flow(fn, target, pool_private=pool_private, incoming=incoming)
    _origins(fn, target, reachable, interfaces, used)
    points = _assignment(fn, target, points)
    coverage = _values(fn, target, points, interfaces, pool, pool_private=pool_private,
                       park_entries=park_entries)
    clobbers = _reported_clobbers(fn.mir, target, fn._ir.abi, interfaces, pool_private=pool_private)
    if fn.clobbers != clobbers:
        _fail(fn, "V9", f"clobbers {fn.clobbers!r} differ from {clobbers!r}")
    return coverage
