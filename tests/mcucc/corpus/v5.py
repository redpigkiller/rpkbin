from rpkbin.mcucc.lang import u8, u16, Ptr, mem, fn

P = Ptr("sram")


@fn()
def level_scan(tbl: P, n: u8, x: u8, out: P):
    for i in range(n):
        if x >= mem.sram[tbl + P(i)]:
            mem.sram[out] = i


@fn()
def lt16(a: u16, b: u16) -> u8:
    if a < b:
        return 1
    return 0


@fn()
def lt_zext(a: u8, b: u8) -> u8:
    if u16(a) < u16(b):
        return 1
    return 0


@fn()
def ge8(a: u8, b: u8) -> u8:
    if a >= b:
        return 1
    return 0
