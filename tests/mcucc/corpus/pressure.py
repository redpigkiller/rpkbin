"""Deterministic indirect-store register-pressure corpus."""
from random import Random


def pressure_source(k):
    offsets = list(range(k))
    Random(71).shuffle(offsets)
    loads = [f"    a{i} = mem.sram[{128 + offset}]" for i, offset in enumerate(offsets)]
    stores = [f"    mem.sram[{160 + offset}] = a{i}" for i, offset in enumerate(offsets)]
    return "\n".join([
        "from rpkbin.mcucc.lang import Ptr, mem, fn",
        'P = Ptr("sram")',
        "@fn()",
        "def pressure():",
        *loads,
        "    address = P(mem.sram[152] | 192)",
        "    mem.sram[address] = a0",
        *stores,
        "",
    ])

