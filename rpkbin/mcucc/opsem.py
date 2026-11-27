"""Canonical unsigned arithmetic shared by compiler verification."""

from types import MappingProxyType


_MSB0_FLAGS = frozenset(("borrow_msb0", "nborrow_msb0"))
_BORROW_FLAGS = _MSB0_FLAGS | {"borrow", "nborrow"}
_FLAG_RELATIONS = MappingProxyType({
    "zero": ("ne", "eq"),
    "borrow": ("geu", "ltu"),
    "nborrow": ("ltu", "geu"),
    "borrow_msb0": ("geu", "ltu"),
    "nborrow_msb0": ("ltu", "geu"),
})


def evaluate(op, width, a, b=None):
    mask = (1 << width) - 1
    if op in ("mov", "const"):
        result = a
    elif op == "add":
        result = a + b
    elif op in ("sub", "cmp"):
        result = a - b
    elif op == "and":
        result = a & b
    elif op == "or":
        result = a | b
    elif op == "xor":
        result = a ^ b
    elif op == "not":
        result = ~a
    elif op == "neg":
        result = -a
    elif op == "shl":
        result = a << b
    elif op == "shr":
        result = a >> b
    else:
        raise ValueError(f"operation {op!r}: no numeric semantics")
    return result & mask


def flag_value(word, op, width, a, b, result):
    if word == "undef":
        return None
    if word == "zero":
        return int(result == 0)
    if word == "carry" and op == "add":
        return int(a + b >= 1 << width)
    if word in _BORROW_FLAGS and op in ("sub", "cmp"):
        if word in _MSB0_FLAGS and max(a, b) >= 1 << (width - 1):
            return None
        return int((a < b) == (word in ("borrow", "borrow_msb0")))
    if word == "shift_out" and op in ("shl", "shr") and b:
        return (a >> (width - b if op == "shl" else b - 1)) & 1
    raise ValueError(f"flag word {word!r}: incompatible operation {op!r}")


def condition_holds(cond, a, b, width):
    mask = (1 << width) - 1
    a, b = a & mask, b & mask
    if cond == "eq":
        return a == b
    if cond == "ne":
        return a != b
    if cond == "ltu":
        return a < b
    if cond == "geu":
        return a >= b
    if cond == "gtu":
        return a > b
    if cond == "leu":
        return a <= b
    raise ValueError(f"condition {cond!r}: unknown condition")
