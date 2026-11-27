from rpkbin.isasim import MCU


def make_mcu(*, pc_name="pc"):
    mcu = MCU("toy_b")
    mcu.space(
        "prog",
        cell_bits=8,
        size=64,
        access="ro",
        reset_state="uninitialized",
        init=0,
        is_program=True,
    )
    mcu.space(
        "inp",
        cell_bits=1,
        size=4,
        access="ro",
        reset_state="initialized",
        init=0,
        role="input",
    )
    mcu.space(
        "outp",
        cell_bits=1,
        size=4,
        access="rw",
        reset_state="initialized",
        init=0,
        role="output",
    )
    # stack：cell 0 是深度計數，cell 1..4 是返回位址
    mcu.space(
        "stack", cell_bits=6, size=5, access="rw", reset_state="initialized", init=0
    )
    mcu.reg(pc_name, bits=6, reset=0, is_pc=True)  # 這是唯一的 register

    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0000 0000", syntax="NOP", cycles=1)
    def nop(st):
        pass

    @mcu.instr("0000 0001", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("hlt")

    @mcu.instr("0000 0010", syntax="RET", cycles=2)
    def ret(st):
        depth = st.mem["stack"][0]
        if depth == 0:
            st.fault("stack_underflow", "RET with empty stack")
        st.next_pc = st.mem["stack"][depth]
        st.mem["stack"][0] = depth - 1

    @mcu.instr("0001 00oo", syntax="SET {o}", cycles=1)
    def set_out(st, o):
        st.mem["outp"][o] = 1

    @mcu.instr("0001 01oo", syntax="CLR {o}", cycles=1)
    def clr_out(st, o):
        st.mem["outp"][o] = 0

    @mcu.instr("0010 0000 00aaaaaa", syntax="JMP {a}", cycles=2)
    def jmp(st, a):
        st.next_pc = a

    @mcu.instr("0011 0000 00aaaaaa", syntax="CALL {a}", cycles=2)
    def call(st, a):
        depth = st.mem["stack"][0]
        if depth == 4:
            st.fault("stack_overflow", "CALL with full stack")
        st.mem["stack"][depth + 1] = st.next_pc
        st.mem["stack"][0] = depth + 1
        st.next_pc = a

    @mcu.instr("0100 00ii 00aaaaaa", syntax="JIN {i}, {a}", cycles=2)
    def jin(st, i, a):
        if st.mem["inp"][i] == 1:
            st.next_pc = a

    return mcu


mcu = make_mcu()
