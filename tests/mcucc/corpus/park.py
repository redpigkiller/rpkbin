"""Deterministic source generator for the stack-parking pressure corpus."""

from random import Random


def park_source(m, t, loop):
    """Keep m independent words live across a pressure region of t bytes."""
    rng = Random(71)
    word_addresses = list(range(128, 140))
    pressure_addresses = list(range(144, 150))
    rng.shuffle(word_addresses)
    rng.shuffle(pressure_addresses)
    signature = "n: u8" if loop else ""
    lines = [
        "from rpkbin.mcucc.lang import fn, mem, u8, u16",
        "@fn()",
        f"def pressure({signature}):",
    ]
    for index in range(m):
        high, low = word_addresses[2 * index:2 * index + 2]
        lines.extend([
            f"    high{index} = mem.sram[{high}]",
            f"    low{index} = mem.sram[{low}]",
            f"    v{index} = (u16(high{index}) << 8) | u16(low{index})",
        ])

    indent = "    "
    if loop:
        lines.append("    for i in range(n):")
        indent += "    "
    for index, address in enumerate(pressure_addresses[:t]):
        lines.append(f"{indent}a{index} = mem.sram[{address}]")
    for index in range(t):
        lines.append(f"{indent}mem.sram[{160 + index}] = a{index} + {index + 1}")

    total = " + ".join(f"v{index}" for index in range(m))
    lines.extend([
        f"    total = {total}",
        "    mem.sram[176] = u8(total)",
        "    mem.sram[177] = u8(total >> 8)",
        "",
    ])
    return "\n".join(lines)
