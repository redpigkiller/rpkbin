"""Check declared instruction effects against an independent simulator."""

from dataclasses import dataclass
import itertools
import random

from .errors import TargetError
from .opsem import _FLAG_RELATIONS, _MSB0_FLAGS, condition_holds, evaluate, flag_value
from .target import I, M, P, R, T, _width, units


@dataclass(frozen=True)
class Failure:
    form_id: str
    check: str
    seed: int
    detail: str


def _native(binding, name):
    return (binding.names or {}).get(name, name)


def _encode(target, form, values):
    if form.encode is None:
        raise TargetError(f"form {form.id!r}: missing encode")
    try:
        cells = form.encode(values)
    except Exception as exc:
        raise TargetError(f"form {form.id!r}: encode failed for {values!r}") from exc
    if not isinstance(cells, tuple) or len(cells) != form.bytes or any(type(c) is not int or not 0 <= c < 1 << target.cell_bits for c in cells):
        raise TargetError(f"form {form.id!r}: encoded cells {cells!r}; expected length {form.bytes} and in-range cells")
    return cells


def _fresh(target, binding, rng):
    try:
        sim = binding.make_sim()
        if binding.init is not None:
            binding.init(sim)
    except Exception as exc:
        raise TargetError("simulation binding: factory/init failed") from exc
    for reg in target.regs:
        sim.poke(_native(binding, reg.name), rng.getrandbits(reg.bits))
    for flag in target.flags:
        sim.poke(_native(binding, flag.name), rng.randrange(2))
    for space in target.spaces:
        if space.kind == "data":
            sim.load_cells(_native(binding, space.name), {i: rng.getrandbits(target.cell_bits) for i in range(1 << space.addr_bits)})
    events = []
    sim.subscribe(events.append)
    return sim, events


def _register_cases(target, form):
    """Cover independent operands and explicit unit-overlap witnesses."""
    classes = {c.name: c.members for c in target.classes}
    operands = [o for o in form.operands if isinstance(o, (R, P))]
    domains = [tuple((name, units(target, name)) for name in classes[o.cls]) for o in operands]
    pairs = tuple(itertools.combinations(range(len(operands)), 2))
    disjoint = dict.fromkeys(pairs, False)
    profiles = [disjoint, dict.fromkeys(pairs, True)]
    for pair in pairs:
        profiles.extend(({pair: True}, {**disjoint, pair: True}))
    cases = []
    for required in profiles:
        def witness(chosen):
            index = len(chosen)
            if index == len(operands):
                return {o.name: name for o, (name, _) in zip(operands, chosen)}
            for name, occupied in domains[index]:
                if all((previous, index) not in required or bool(occupied & other) == required[previous, index]
                       for previous, (_, other) in enumerate(chosen)):
                    result = witness((*chosen, (name, occupied)))
                    if result is not None:
                        return result
            return None
        case = witness(())
        if case is not None and case not in cases:
            cases.append(case)
    return cases


def _numeric_cases(target, form, rng):
    spaces = {s.name: s for s in target.spaces}
    fields = {}
    for operand in form.operands:
        allowed = None
        signed = False
        if isinstance(operand, I):
            width, allowed, signed = operand.bits, operand.values, operand.signed
        elif isinstance(operand, M):
            width = operand.bits or spaces[operand.space].addr_bits
        elif isinstance(operand, T):
            width = operand.rel_bits or operand.abs_bits
            signed = operand.rel_bits is not None
        else:
            continue
        limit = 1 << width
        high = limit - 1
        sign = limit >> 1
        bits = tuple(1 << i for i in range(width))
        patterns = [0, high, high // 2, *bits, *(high ^ bit for bit in bits)]
        if isinstance(operand, M):
            # Also probe neighbouring bits together to expose address interactions.
            patterns.extend(left | right for left, right in itertools.pairwise(bits))
        def decode(value):
            return value - limit if signed and value >= sign else value
        boundaries = list(dict.fromkeys(map(decode, patterns)))
        if allowed is not None:
            domain = tuple(dict.fromkeys(allowed))
            boundaries = list(dict.fromkeys((min(domain), max(domain), *(v for v in boundaries if v in domain))))
            draws = rng.sample(domain, min(4, len(domain)))
        else:
            # randrange also handles fields whose domains exceed len(range(...)).
            sampled = {}
            while len(sampled) < min(4, limit):
                sampled[rng.randrange(limit)] = None
            draws = map(decode, sampled)
        fields[operand.name] = (*boundaries, *draws)
    return fields


def _sample(target, form, index, rng, fields=None, cases=None):
    classes = {c.name: c.members for c in target.classes}
    if fields is None:
        fields = _numeric_cases(target, form, rng)
    if cases is None:
        cases = _register_cases(target, form)
    values = {}
    for operand in form.operands:
        if isinstance(operand, (R, P)):
            # Explicit overlapping and independent operands precede independent
            # seeded draws. Aliases overlap by units, not by spelling.
            values[operand.name] = cases[index][operand.name] if index < len(cases) else rng.choice(classes[operand.cls])
        else:
            choices = fields[operand.name]
            values[operand.name] = choices[index % len(choices)]
    return values


def _samples(target, form, rng):
    fields = _numeric_cases(target, form, rng)
    cases = _register_cases(target, form)
    count = max(12, len(cases), *(len(values) for values in fields.values()))
    for index in range(count):
        yield _sample(target, form, index, rng, fields, cases)


def _msb0_samples(target, form, rng):
    limit = 1 << (form.width - 1)
    boundaries = (0, 1, limit - 1, limit // 2)
    operands = {o.name: o for o in form.operands}
    left, right = (operands[form.bind[key]] for key in ("a", "b"))
    numeric = _numeric_cases(target, form, rng)
    registers = _register_cases(target, form)

    def values(operand):
        if isinstance(operand, I):
            if operand.values is not None:
                legal = tuple(v for v in operand.values if v < limit)
                if not legal:
                    return (), ()
                return legal, tuple(rng.choice(legal) for _ in range(12))
            stop = min(1 << operand.bits, limit)
        else:
            stop = limit
        return tuple(v for v in boundaries if v < stop), tuple(rng.randrange(stop) for _ in range(12))

    a_edges, a_random = values(left)
    b_edges, b_random = values(right)
    if not a_edges or not b_edges:
        return
    for index, (a, b) in enumerate(itertools.chain(itertools.product(a_edges, b_edges),
                                                   zip(a_random, b_random))):
        binding = _sample(target, form, 0, rng, numeric, registers)
        for operand, value in ((left, a), (right, b)):
            if isinstance(operand, I):
                binding[operand.name] = value
        if left.name == right.name:
            b = a
        elif isinstance(left, R) and isinstance(right, R) and (
                units(target, binding[left.name]) & units(target, binding[right.name])):
            # Equal-width overlapping registers can always represent the same
            # value when they have the same ordered cells.
            a = b = 0
        yield binding, a, b


def _set_msb0_inputs(sim, binding, form, values, a, b):
    operands = {o.name: o for o in form.operands}
    for key, value in (("a", a), ("b", b)):
        operand = operands[form.bind[key]]
        location = values[operand.name]
        if isinstance(operand, (R, P)):
            sim.poke(_native(binding, location), value)
        elif isinstance(operand, M):
            sim.poke(f"{_native(binding, operand.space)}[{location}]", value)


def _argument(sim, binding, form, values, key):
    operand = next(o for o in form.operands if o.name == form.bind[key])
    value = values[operand.name]
    if isinstance(operand, (R, P)):
        return sim.read(_native(binding, value))
    if isinstance(operand, M) and not (form.op in ("ld", "st") and key == "a"):
        return sim.read(f"{_native(binding, operand.space)}[{value}]")
    return value


def _boundary_inputs(target, sim, binding, form, values, index):
    if index >= 5:
        return
    for position, key in enumerate(("a", "b")):
        if key not in form.bind:
            continue
        operand = next(o for o in form.operands if o.name == form.bind[key])
        if isinstance(operand, (R, P)):
            name = values[operand.name]
            limit = 1 << _width(target, name)
            choices = (0, 1, limit - 1, limit // 2, limit // 2 - 1)
            sim.poke(_native(binding, name), choices[(index + position) % len(choices)])


def _snapshot(target, sim, binding):
    return {r.name: sim.read(_native(binding, r.name)) for r in (*target.regs, *target.flags)}


def _step(sim, events):
    events.clear()
    result = sim.step()
    if result.exception is not None or result.stop_reason in ("fault", "python_error", "undefined_opcode", "out_of_range"):
        raise TargetError(f"simulation step: {result.stop_reason}, {result.detail!r}")
    return tuple(e for e in events if e.kind == "instr")


def _effects(target, binding, form, values):
    allowed = set()
    if "dst" in form.bind:
        allowed.update(units(target, values[form.bind["dst"]]))
    for reg in form.clobbers:
        allowed.update(units(target, reg))
    allowed.update(form.flags_write or {})
    return allowed


def _unchanged(target, sim, binding, form, values, before, events, allowed_memory=()):
    allowed = _effects(target, binding, form, values)
    after = _snapshot(target, sim, binding)
    for name, old in before.items():
        if name not in allowed and after[name] != old:
            return f"undeclared write to {name!r}: {old} -> {after[name]}"
    # The simulator's public write journal covers every changed memory cell.
    # All data cells are initialized; unchanged cells keep their initial value.
    data_names = {_native(binding, s.name) for s in target.spaces if s.kind == "data"}
    for event in events:
        for write in event.writes:
            if write.kind == "mem" and write.name in data_names and (write.name, write.addr) not in allowed_memory and write.old != write.new:
                return f"undeclared write to {write.name}[{write.addr}]"
    return None


def _install(sim, binding, start, cells):
    sim.load_cells(binding.program_space, {start + i: c for i, c in enumerate((*cells, *binding.halt_cells))})
    sim.poke(binding.pc, start)


def check_forms(target, sim, seeds):
    failures, _, _ = _check_forms(target, sim, seeds)
    return failures


def _check_forms(target, sim, seeds):
    if sim is None:
        return [], 0, 0
    binding = sim
    failures = []
    out_of_space = ret_not_fit = 0
    def failed(form, check, seed, detail):
        failures.append(Failure(form.id, check, seed, str(detail)))
    for seed in seeds:
        rng = random.Random(seed)
        calls = {}
        for form in target.forms:
            ordinary = _samples(target, form, rng)
            if form.op == "br":
                ordinary = itertools.chain.from_iterable((values, values) for values in ordinary)
            samples = ((values, None) for values in ordinary)
            if _MSB0_FLAGS.intersection((form.flags_write or {}).values()):
                extra = ((values, (a, b)) for values, a, b in _msb0_samples(target, form, rng))
                samples = itertools.chain(samples, extra)
            if form.op == "call":
                call_operand = next(o for o in form.operands if isinstance(o, T))
                calls[form.id] = {}
            for index, (values, low_pair) in enumerate(samples):
                try:
                    cells = _encode(target, form, values)
                except TargetError as exc:
                    failed(form, "C10", seed, exc)
                    continue
                if form.op == "call":
                    calls[form.id].setdefault(values[call_operand.name], cells)
                    continue
                if form.op in ("ret", "pop"):
                    continue
                machine, events = _fresh(target, binding, rng)
                start = 0
                if form.op in ("jmp", "br"):
                    operand = next(o for o in form.operands if isinstance(o, T))
                    if operand.rel_bits:
                        base = form.bytes if operand.base == "end" else 0
                        start = max(0, -values[operand.name] - base)
                    if form.op == "br":
                        for flag, wanted in form.flags_read.items():
                            machine.poke(_native(binding, flag), wanted if index % 2 == 0 else 1 - wanted)
                _install(machine, binding, start, cells)
                if low_pair is None:
                    _boundary_inputs(target, machine, binding, form, values, index)
                else:
                    _set_msb0_inputs(machine, binding, form, values, *low_pair)
                before = _snapshot(target, machine, binding)
                a = _argument(machine, binding, form, values, "a") if "a" in form.bind else None
                b = _argument(machine, binding, form, values, "b") if "b" in form.bind else None
                memory = ()
                expected = None
                if form.op in ("ld", "ld_ind", "st", "st_ind"):
                    operand = next(o for o in form.operands if o.name == form.bind["a"])
                    cell = (_native(binding, operand.space), a)
                    if form.op in ("ld", "ld_ind"):
                        expected = machine.read(f"{cell[0]}[{a}]")
                    else:
                        memory = (cell,)
                elif form.op not in ("jmp", "br", "push"):
                    expected = evaluate(form.op, form.width, a, b)
                try:
                    writes = _step(machine, events)
                except TargetError as exc:
                    failed(form, "C1", seed, exc)
                    continue
                if form.op in ("jmp", "br"):
                    take = form.op == "jmp" or all(before[k] == v for k, v in form.flags_read.items())
                    value = values[operand.name]
                    dest = value + (start + form.bytes if operand.base == "end" else start) if operand.rel_bits else value
                    wanted = dest if take else start + form.bytes
                    if machine.read(binding.pc) != wanted:
                        failed(form, "C6", seed, f"expected PC {wanted}, got {machine.read(binding.pc)}")
                elif machine.read(binding.pc) != start + form.bytes:
                    failed(form, "C1", seed, "PC increment differs from declared bytes")
                if "dst" in form.bind and expected is not None:
                    destination = values[form.bind["dst"]]
                    actual = machine.read(_native(binding, destination))
                    if actual != expected:
                        failed(form, "C2", seed, f"{destination}: expected {expected}, got {actual}")
                        if form.op in ("ld", "ld_ind"):
                            failed(form, "C5", seed, f"cell {cell!r}: expected {expected}, got {actual}")
                if memory:
                    actual = machine.read(f"{memory[0][0]}[{a}]")
                    if actual != b:
                        failed(form, "C5", seed, f"expected stored value {b}, got {actual}")
                detail = _unchanged(target, machine, binding, form, values, before, writes, memory)
                if detail:
                    failed(form, "C3", seed, detail)
                if expected is not None:
                    for flag, word in (form.flags_write or {}).items():
                        try:
                            wanted = flag_value(word, form.op, form.width, a, b, expected)
                        except ValueError as exc:
                            failed(form, "C4", seed, exc)
                            continue
                        if wanted is not None and machine.read(_native(binding, flag)) != wanted:
                            failed(form, "C4", seed, f"flag {flag!r}: expected {wanted}")
        _check_stack(target, binding, seed, rng, failed)
        skipped_calls, skipped_returns = _check_calls(target, binding, seed, rng, failed, calls)
        out_of_space += skipped_calls
        ret_not_fit += skipped_returns
        _check_pairs(target, binding, seed, rng, failed)
    return failures, out_of_space, ret_not_fit


def _fixture_encode(target, form, values, check, seed, failed):
    try:
        return _encode(target, form, values)
    except TargetError as exc:
        failed(form, check, seed, exc)
        return None


def _fixture_step(machine, events, form, check, seed, failed):
    try:
        return _step(machine, events)
    except TargetError as exc:
        failed(form, check, seed, exc)
        return None


def _check_stack(target, binding, seed, rng, failed):
    for push, pop in itertools.product((f for f in target.forms if f.op == "push"), (f for f in target.forms if f.op == "pop")):
        if push.width != pop.width:
            continue
        xop, yop = push.operands[0], pop.operands[0]
        xs = next(c.members for c in target.classes if c.name == xop.cls)
        ys = next(c.members for c in target.classes if c.name == yop.cls)
        combinations = tuple(itertools.product(xs, ys))
        for index in range(max(12, len(combinations))):
            x, y = combinations[index % len(combinations)]
            machine, events = _fresh(target, binding, rng)
            v1 = (1 << push.width) - 1 if index % 2 else 0x1234 & ((1 << push.width) - 1)
            v2 = v1 ^ ((1 << push.width) - 1)
            xv, yv = {xop.name: x}, {yop.name: y}
            first = _fixture_encode(target, push, xv, "C8", seed, failed)
            second = _fixture_encode(target, pop, yv, "C8", seed, failed)
            if first is None or second is None:
                continue
            _install(machine, binding, 0, (*first, *second))
            machine.poke(_native(binding, x), v1)
            s0 = _snapshot(target, machine, binding)
            writes = _fixture_step(machine, events, push, "C8", seed, failed)
            if writes is None:
                continue
            detail = _unchanged(target, machine, binding, push, xv, s0, writes)
            if detail:
                failed(push, "C8", seed, detail)
            machine.poke(_native(binding, x), v2)
            s2 = _snapshot(target, machine, binding)
            writes = _fixture_step(machine, events, pop, "C8", seed, failed)
            if writes is None:
                continue
            detail = _unchanged(target, machine, binding, pop, yv, s2, writes)
            if detail or machine.read(_native(binding, y)) != v1:
                failed(pop, "C8", seed, detail or f"expected popped value {v1}")


def _program_contains(machine, binding, address):
    from rpkbin.isasim.errors import TargetError as SimTargetError

    if address < 0:
        return False
    try:
        machine.read(f"{binding.program_space}[{address}]")
    except SimTargetError:
        return False
    return True


def _check_calls(target, binding, seed, rng, failed, calls):
    out_of_space = ret_not_fit = 0
    for call, ret in itertools.product((f for f in target.forms if f.op == "call"), (f for f in target.forms if f.op == "ret")):
        operand = next(o for o in call.operands if isinstance(o, T))
        base = call.bytes if operand.rel_bits is not None and operand.base == "end" else 0
        second = _fixture_encode(target, ret, {}, "C9", seed, failed)
        for sample, first in calls[call.id].items():
            destination = base + sample
            values = {operand.name: sample}
            machine, events = _fresh(target, binding, rng)
            _install(machine, binding, 0, first)
            if not _program_contains(machine, binding, destination):
                out_of_space += 1
                continue
            fits = _program_contains(machine, binding, destination + ret.bytes - 1)
            if not fits:
                ret_not_fit += 1
            before = _snapshot(target, machine, binding)
            writes = _fixture_step(machine, events, call, "C9", seed, failed)
            if writes is None:
                continue
            detail = _unchanged(target, machine, binding, call, values, before, writes)
            actual = machine.read(binding.pc)
            if detail or actual != destination:
                failed(call, "C9", seed, detail or f"expected PC {destination}, got {actual}")
            if actual != destination or not fits or second is None:
                continue
            # The callee can overlap the driver. Load it after the call has executed.
            machine.load_cells(binding.program_space, {destination + i: c for i, c in enumerate(second)})
            before = _snapshot(target, machine, binding)
            writes = _fixture_step(machine, events, ret, "C9", seed, failed)
            if writes is None:
                continue
            detail = _unchanged(target, machine, binding, ret, {}, before, writes)
            if detail or machine.read(binding.pc) != call.bytes:
                failed(ret, "C9", seed, detail or "return PC differs")
            machine.load_cells(binding.program_space, dict(enumerate(binding.halt_cells, call.bytes)))
    return out_of_space, ret_not_fit


def _check_pairs(target, binding, seed, rng, failed):
    program = next(s for s in target.spaces if s.kind == "program")
    for cmp, branch in itertools.product((f for f in target.forms if f.op == "cmp"), (f for f in target.forms if f.op == "br")):
        flag, wanted = next(iter(branch.flags_read.items()))
        word = (cmp.flags_write or {}).get(flag)
        if word not in _FLAG_RELATIONS:
            continue
        relation = _FLAG_RELATIONS[word][wanted]
        operand = next(o for o in branch.operands if isinstance(o, T))
        fallthrough = cmp.bytes + branch.bytes
        if operand.rel_bits is not None:
            base = fallthrough if operand.base == "end" else cmp.bytes
            radius = 1 << (operand.rel_bits - 1)
            low, high = max(0, base - radius), base + radius - 1
        else:
            base = low = 0
            high = (1 << operand.abs_bits) - 1
        destination = min(high, (1 << program.addr_bits) - 1, fallthrough + 1)
        if destination == fallthrough:
            destination = low
        samples = (_msb0_samples(target, cmp, rng) if word in _MSB0_FLAGS
                   else ((values, None, None) for values in _samples(target, cmp, rng)))
        for index, (values, low_a, low_b) in enumerate(samples):
            first = _fixture_encode(target, cmp, values, "C7", seed, failed)
            second = _fixture_encode(target, branch, {operand.name: destination - base}, "C7", seed, failed)
            if first is None or second is None:
                continue
            machine, events = _fresh(target, binding, rng)
            _install(machine, binding, 0, (*first, *second))
            if low_a is None:
                _boundary_inputs(target, machine, binding, cmp, values, index)
            else:
                _set_msb0_inputs(machine, binding, cmp, values, low_a, low_b)
            a, b = (_argument(machine, binding, cmp, values, key) for key in ("a", "b"))
            if _fixture_step(machine, events, cmp, "C7", seed, failed) is None:
                continue
            if _fixture_step(machine, events, branch, "C7", seed, failed) is None:
                continue
            expected = destination if condition_holds(relation, a, b, cmp.width) else fallthrough
            if machine.read(binding.pc) != expected:
                failed(cmp, "C7", seed, f"pair {branch.id!r}: expected PC {expected}")
