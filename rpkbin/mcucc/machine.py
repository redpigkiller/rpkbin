"""Form-derived machine effects, independent of allocation decisions."""

from dataclasses import replace

from .target import M, P, R, T, units


def _callee_name(instruction):
    return next(instruction.values[o.name] for o in instruction.form.operands if isinstance(o, T))


def _call_units(target, abi):
    result = set()
    for loc in (*abi["args"], abi.get("ret")):
        if loc is not None:
            result.update(units(target, loc))
    for name in abi.get("clobbers", ()):
        result.update(units(target, name))
    for form in target.forms:
        if form.op == "call":
            for name in form.clobbers:
                result.update(units(target, name))
    return result


def _in_pool(pool, space, address):
    bounds = pool.get(space)
    return bounds is not None and bounds[0] <= address <= bounds[1]


def _indirect_write(space, pool):
    bounds = pool.get(space)
    return ("indirect", space) if bounds is None else ("indirect", space, bounds)


def _effects_intersect(written, protected, pool=None):
    if written & protected:
        return True
    for effect in written:
        if not isinstance(effect, tuple) or effect[0] != "indirect":
            continue
        space = effect[1]
        for unit in protected:
            if not isinstance(unit, tuple):
                continue
            if len(unit) == 2 and unit[0] == space:
                if len(effect) == 2 or not effect[2][0] <= unit[1] <= effect[2][1]:
                    return True
            if len(unit) == 4 and unit[0] == "slot" and len(effect) == 2 and pool and space in pool:
                return True
    return False


def _written_units(mir, target, interfaces, *, pool_private=False):
    written = set()
    for instruction in mir:
        if instruction.op == "call":
            written.update(_call_units(target, interfaces[_callee_name(instruction)]))
        else:
            if instruction._target is not target:
                instruction = replace(instruction, _target=target)
            written.update(defs_uses(instruction, pool_private=pool_private)[0])
    return written


def _reported_clobbers(mir, target, abi, interfaces, *, pool_private=False):
    written = _written_units(mir, target, interfaces, pool_private=pool_private)
    for name in abi["preserves"]:
        written.difference_update(units(target, name))
    for location in (*abi["args"], abi["ret"]):
        if location is not None:
            written.update(units(target, location))
    return tuple(reg.name for reg in target.regs if units(target, reg.name) & written)


def defs_uses(minstr, *, pool_private=False):
    if minstr.form is None:
        if minstr._asm:
            meta = minstr._asm
            definitions = set().union(*(units(minstr._target, n) for n in meta["clobbers"]))
            definitions.update(u for loc in meta["out_locs"] for u in units(minstr._target, loc))
            uses = set().union(*(units(minstr._target, loc) for loc in meta["in_locs"]))
            flag_defs = set() if meta["flags_ok"] else {f.name for f in minstr._target.flags}
            return definitions, uses, flag_defs, set()
        return set(), set(), set(), set()
    target, form, values = minstr._target, minstr.form, minstr.values
    definitions, uses = set(), set()
    for name in form.clobbers:
        definitions.update(units(target, name))
    operands = {o.name: o for o in form.operands}
    for arg, name in form.bind.items():
        operand, value = operands[name], values[name]
        if isinstance(operand, (R, P)):
            (definitions if arg == "dst" else uses).update(units(target, value))
        elif isinstance(operand, M):
            if form.op == "st":
                if arg == "a":
                    definitions.update(units(target, value))
            else:
                uses.update(units(target, value))
    if form.op == "st_ind":
        operand = operands[form.bind["a"]]
        definitions.add(_indirect_write(operand.space, dict(minstr._pool_bounds) if pool_private else {}))
    if form.op == "call":
        definitions = _call_units(target, minstr._callee_abi)
        uses = set().union(*(units(target, loc) for loc in minstr._callee_abi["args"]))
    elif form.op == "ret":
        uses = set().union(*(units(target, loc) for loc in (*minstr._abi.get("preserves", ()), minstr._abi.get("ret")) if loc is not None))
    return definitions, uses, set(form.flags_write or {}), set(form.flags_read or {})
