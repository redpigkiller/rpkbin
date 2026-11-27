from rpkbin.isasim import MCU, helpers


def make_mcu(
    *,
    undefined="stop",
    out_of_range="stop",
    uninit="stop",
    with_irq=False,
    pc_name="pc",
):
    mcu = MCU("toy_a")
    mcu.space(
        "prog",
        cell_bits=16,
        size=256,
        access="ro",
        reset_state="uninitialized",
        init=0,
        is_program=True,
    )
    mcu.space(
        "data", cell_bits=16, size=64, access="rw", reset_state="uninitialized", init=0
    )
    mcu.reg(pc_name, bits=8, reset=0, is_pc=True)
    for n in range(4):
        mcu.reg(f"r{n}", bits=16, reset=0)
    mcu.reg("flags", bits=3, reset=0, fields={"Z": 0, "N": 1, "C": 2})

    if undefined == "stop":
        mcu.on_undefined_opcode("stop")
    else:  # "nop"
        mcu.on_undefined_opcode("nop", nop_cells=1, nop_cycles=1)
    if out_of_range == "stop":
        mcu.on_out_of_range("stop")
    else:  # "ignore"
        mcu.on_out_of_range("ignore", read_value=0x00AA)
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read(uninit)  # "stop" | "warn" | "allow"

    def set_flags(st, res, carry):
        st.reg["flags.Z"] = int(res == 0)
        st.reg["flags.N"] = helpers.bit(res, 15)
        st.reg["flags.C"] = carry

    @mcu.instr("0001 dd 00 iiiiiiii", syntax="LDI r{d}, {i}", cycles=1)
    def ldi(st, d, i):
        st.reg[f"r{d}"] = i

    @mcu.instr("0010 dd ss 00000000", syntax="ADD r{d}, r{s}", cycles=1)
    def add(st, d, s):
        res = helpers.add_with_carry(st.reg[f"r{d}"], st.reg[f"r{s}"], 0, 16)
        st.reg[f"r{d}"] = res.result
        set_flags(st, res.result, res.carry)

    @mcu.instr("0011 dd ss 00000000", syntax="SUB r{d}, r{s}", cycles=1)
    def sub(st, d, s):
        res = helpers.sub_with_borrow(st.reg[f"r{d}"], st.reg[f"r{s}"], 0, 16)
        st.reg[f"r{d}"] = res.result
        set_flags(st, res.result, res.borrow)

    @mcu.instr("0100 dd 00 aaaaaaaa", syntax="LD r{d}, [{a}]", cycles=2)
    def ld(st, d, a):
        st.reg[f"r{d}"] = st.mem["data"][a]

    @mcu.instr("0101 ss 00 aaaaaaaa", syntax="ST r{s}, [{a}]", cycles=2)
    def store(st, s, a):
        st.mem["data"][a] = st.reg[f"r{s}"]

    @mcu.instr("0110 0000 rrrrrrrr", syntax="JNZ {r:#04x}", cycles=1)
    def jnz(st, r):
        if st.reg["flags.Z"] == 0:
            st.next_pc = st.next_pc + helpers.to_signed(r, 8)
            st.add_cycles(1)

    @mcu.instr("0111 0000 aaaaaaaa", syntax="JMP {a}", cycles=2)
    def jmp(st, a):
        st.next_pc = a

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("hlt")

    if with_irq:
        mcu.irq_line("irq0")

        @mcu.interrupt_check
        def check(st):
            if st.irq["irq0"] == 1:
                st.irq["irq0"] = 0
                st.reg["r3"] = st.pc
                st.next_pc = 0x40
                return ("IRQ0", 3)
            return None

    return mcu


mcu = make_mcu()
