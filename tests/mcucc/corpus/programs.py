from rpkbin.mcucc.lang import u8, u16, Ptr, mem

P = Ptr("sram")
SLOT = 0x30

def add2(a: u8, b: u8) -> u8:
    return a + b

def inc(a: u8) -> u8:
    return a + 1

def rsub(a: u8, b: u8) -> u8:
    return b - a

def three(a: u8, b: u8, c: u8) -> u8:
    return a + c

def fold(a: u8) -> u8:
    return a + 3 + 4

def mix(a: u8, b: u8) -> u8:
    return (a & 0x0F) | (b ^ 0x30)

def maxu(a: u8, b: u8) -> u8:
    if a < b:
        return b
    return a

def clamp(a: u8, hi: u8) -> u8:
    if a > hi:
        return hi
    return a

def abs_diff(a: u8, b: u8) -> u8:
    if a < b:
        return b - a
    return a - b

def count_down(n: u8) -> u8:
    s: u8 = 0
    while n != 0:
        s = s + n
        n = n - 1
    return s

def shifty(a: u8) -> u8:
    return (a << 2) ^ (a >> 1)

def bits(a: u8, b: u8) -> u8:
    if a == b or (a < 0x10 and b != 0):
        return a & b
    return a | b

def many(a: u8, b: u8, c: u8) -> u8:
    d = a + b
    e = b ^ c
    f = c - a
    g = d & e
    h = e | f
    return (g + h) ^ (d - f)

def mem_inc() -> u8:
    mem.sram[SLOT] = mem.sram[SLOT] + 1
    return mem.sram[SLOT]

def mem_sum(p: P, n: u8) -> u8:
    s: u8 = 0
    for i in range(n & 7):
        s = s + mem.sram[p + P(i)]
    return s

def load(p: P) -> u8:
    return mem.sram[p]

def getc() -> u8:
    return mem.sram[SLOT]

def add16(a: u16, b: u16) -> u16:
    return a + b

def lo_plus(a: u16, b: u8) -> u8:
    return u8(a) + b

def hi_of(a: u16) -> u8:
    return u8(a >> 8)

def widen(a: u8, b: u8) -> u16:
    return (u16(a) << 8) | u16(b)

def cmp16(a: u16, b: u16) -> u8:
    if a < b:
        return 1
    return 0
