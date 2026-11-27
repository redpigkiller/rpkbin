"""Bounded deterministic source generation for semantic differential tests."""

import random


def generate(seed, helper=None):
    rng = random.Random(seed)
    count = rng.randint(1, 3)
    parameters = ("a", "b", "c")[:count]
    right = rng.choice(parameters)
    operation = rng.choice(("+", "-", "&", "|", "^"))
    constant = rng.randrange(16)
    lines = ["from rpkbin.mcucc.lang import u8, mem", "", "def generated(" + ", ".join(p + ": u8" for p in parameters) + ") -> u8:"]
    kind = seed % 6
    if kind == 0:
        lines += [f"    x = a {operation} {right}", f"    return x ^ {constant}"]
    elif kind == 1:
        comparison = rng.choice(("==", "!=", "<", "<=", ">", ">="))
        condition = f"a {comparison} {right}"
        connective = rng.choice((None, "and", "or", "not"))
        if connective == "not":
            condition = f"not ({condition})"
        elif connective is not None:
            other = rng.choice(("==", "!=", "<", "<=", ">", ">="))
            condition = f"({condition}) {connective} ({right} {other} {constant})"
        lines += [f"    x = a {operation} {constant}", f"    if {condition}:", "        x = ~x", "    else:", "        x = x + 1", "    return x"]
    elif kind == 2:
        bound = str(rng.randint(1, 8)) if rng.randrange(2) else f"{right} & 7"
        lines += ["    x: u8 = 0", f"    for i in range({bound}):", "        x = x + a", "    return x"]
    elif kind == 3:
        address = rng.randrange(0x30, 0x40)
        lines += [f"    x = mem.sram[{address}]", f"    mem.sram[{address}] = a", f"    return x {operation} {right}"]
    elif kind == 4:
        amount = rng.randint(1, 3)
        lines += [f"    return (a << {amount}) ^ ({right} >> {rng.randint(1, 3)})"]
    elif helper:
        lines += [f"    return {helper}(a) {operation} {right}"]
    else:
        lines += ["    x = a", "    for i in range(a & 7):", "        if i == 2:", "            continue", "        x = x + i", "    return x"]
    return "\n".join(lines) + "\n"
