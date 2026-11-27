from rpkbin.isasim import MCU


def make_toy_r(*, out_of_range, ro):
    mcu = MCU("toy_r")
    mcu.space(
        "prog",
        cell_bits=8,
        size=4,
        access="ro",
        reset_state="uninitialized",
        init=0,
        is_program=True,
    )
    mcu.space("d", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=2, reset=0, is_pc=True)
    mcu.reg("x", bits=8, reset=0)
    mcu.on_undefined_opcode("stop")
    if out_of_range == "stop":
        mcu.on_out_of_range("stop")
    else:
        mcu.on_out_of_range("ignore", read_value=0)
    mcu.on_write_to_readonly(ro)  # "stop" | "ignore"
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0000 0001", syntax="W", cycles=1)
    def w(st):
        st.reg["x"] = 5
        st.mem["d"][1] = 7
        st.mem["d"][9]  # 超出範圍的讀取

    @mcu.instr("0000 0010", syntax="R", cycles=1)
    def r(st):
        st.mem["prog"][0] = 1  # 寫入唯讀 space

    @mcu.instr("0000 0011", syntax="H", cycles=1)
    def h(st):
        st.halt("h")

    return mcu
