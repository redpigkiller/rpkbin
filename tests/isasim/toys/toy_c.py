"""toy C：第三個結構刻意不同的測試用 ISA（只用於測試，不屬於套件）。

與 toy A、toy B 的差異：
- program cell 是 12 位元，大小 100（不是 2 的冪），PC 是 8 位元（可指向 program 範圍之外）。
- 資料 cell 是 5 位元，輸入埠 cell 是 3 位元；三個 space 的位寬互不相同。
- 指令長度 1、2、3 個 cell；有 don't-care 位元；欄位跨 cell 且位於不連續位置；
  有一個 12 位元欄位佔滿整個 cell；有號位移是 8 位元。
- 未定義指令採用 "nop" 策略且 nop_cells=2（多 cell 的 nop）。
- 只有 PC 與一個有 field 的 5 位元 register `w`，沒有 flag、沒有 stack、沒有 IRQ。
"""

from rpkbin.isasim import MCU, helpers


def make_mcu(*, pc_name="pc", oor="stop"):
    mcu = MCU("toy_c")
    mcu.space(
        "prog",
        cell_bits=12,
        size=100,
        access="ro",
        reset_state="uninitialized",
        init=0,
        is_program=True,
    )
    mcu.space(
        "mem",
        cell_bits=5,
        size=20,
        access="rw",
        reset_state="initialized",
        init=0,
    )
    mcu.space(
        "ports",
        cell_bits=3,
        size=2,
        access="ro",
        reset_state="initialized",
        init=0,
        role="input",
    )
    mcu.reg(pc_name, bits=8, reset=0, is_pc=True)
    mcu.reg("w", bits=5, reset=0, fields={"L": (2, 0), "H": (4, 3)})

    mcu.on_undefined_opcode("nop", nop_cells=2, nop_cycles=3)
    if oor == "stop":
        mcu.on_out_of_range("stop")
    else:
        mcu.on_out_of_range("ignore", read_value=31)
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("allow")

    @mcu.instr("0001 aaaaaaaa", syntax="LD [{a}]", cycles=2)
    def ld(st, a):
        st.reg["w"] = st.mem["mem"][a]

    @mcu.instr("0010 aaaaaaaa", syntax="ST [{a}]", cycles=2)
    def store(st, a):
        st.mem["mem"][a] = st.reg["w"]

    @mcu.instr("0011 -- iiiiii", syntax="ADDI {i}", cycles=1)
    def addi(st, i):
        res = helpers.add_with_carry(st.reg["w"], i, 0, 5)
        st.reg["w"] = res.result
        st.mem["mem"][0] = res.carry

    @mcu.instr("0100 0000 0000 rrrrrrrr ----", syntax="JZ {r}", cycles=2)
    def jz(st, r):
        if st.reg["w"] == 0:
            st.next_pc = st.next_pc + helpers.to_signed(r, 8)
            st.add_cycles(1)

    @mcu.instr("0101 000p 0000", syntax="IN {p}", cycles=1)
    def read_port(st, p):
        st.reg["w"] = st.mem["ports"][p]

    @mcu.instr("0110 aaaa 0000 aaaaaaaa ----", syntax="JMP {a}", cycles=2)
    def jmp(st, a):
        st.next_pc = a

    @mcu.instr("0111 hh 0 lll 00", syntax="WF {h}, {l}", cycles=1)
    def write_fields(st, h, l):
        st.reg["w.H"] = h
        st.reg["w.L"] = l

    @mcu.instr(
        "1000 0000 0000 aaaaaaaaaaaa vvvvv -------", syntax="STI [{a}], {v}", cycles=3
    )
    def store_imm(st, a, v):
        st.mem["mem"][a] = v

    @mcu.instr("1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("hlt")

    return mcu


mcu = make_mcu()
