"""toy C 的獨立參考模型（不使用 isasim 框架，只用標準函式庫）。

它依 toy C 的指令定義與 docs/01 的執行規則「重新寫一遍」，用來和框架逐步比對。
刻意寫成最直接的形式（if/elif、dict），不共用框架的任何程式碼。
"""

PROG_SIZE = 100
MEM_SIZE = 20
PORT_SIZE = 2
PC_MASK = (1 << 8) - 1
W_MASK = (1 << 5) - 1
MEM_MASK = (1 << 5) - 1


class StepOutcome:
    def __init__(
        self,
        stop,
        text=None,
        cells=None,
        writes=None,
        warns=None,
        consumed=0,
        detail=None,
    ):
        self.stop = stop  # "step_done" | "halted" | "out_of_range"
        self.text = text
        self.cells = cells
        self.writes = writes or []
        self.warns = warns or []
        self.consumed = consumed
        self.detail = detail


class Ref:
    def __init__(self, prog, oor="stop", mem=None, ports=None, w=0, pc=0):
        self.prog = dict(prog)  # 位址 -> 12 位元值；沒有的位址儲存值為 0
        self.oor = oor
        self.mem = list(mem) if mem is not None else [0] * MEM_SIZE
        self.ports = list(ports) if ports is not None else [0] * PORT_SIZE
        self.w = w
        self.pc = pc
        self.cycle = 0
        self.steps = 0
        self.fetch_out_of_range = False

    def cell(self, addr):
        return self.prog.get(addr, 0)

    def fetch(self, addr):
        """回傳 (值, None) 或 (None, 錯誤位址)。程式空間超出範圍的取指。"""
        if addr >= PROG_SIZE:
            if self.oor == "ignore":
                self.fetch_out_of_range = True
                return 31, None
            return None, addr
        return self.cell(addr), None

    # ---- 解碼：回傳 ("op", name, fields, length) | ("undef",) | ("oor", addr) ----
    def decode(self, pc):
        c0, bad = self.fetch(pc)
        if bad is not None:
            return ("oor", bad)
        n = c0 >> 8
        if n == 1:
            return ("op", "LD", {"a": c0 & 0xFF}, 1)
        if n == 2:
            return ("op", "ST", {"a": c0 & 0xFF}, 1)
        if n == 3:
            return ("op", "ADDI", {"i": c0 & 0x3F}, 1)
        if n == 4:
            if c0 != 0x400:
                return ("undef",)
            c1, bad = self.fetch(pc + 1)
            if bad is not None:
                return ("oor", bad)
            return ("op", "JZ", {"r": c1 >> 4}, 2)
        if n == 5:
            if ((c0 >> 5) & 7) != 0 or (c0 & 0xF) != 0:
                return ("undef",)
            return ("op", "IN", {"p": (c0 >> 4) & 1}, 1)
        if n == 6:
            if (c0 & 0xF) != 0:
                return ("undef",)
            c1, bad = self.fetch(pc + 1)
            if bad is not None:
                return ("oor", bad)
            return ("op", "JMP", {"a": (((c0 >> 4) & 0xF) << 8) | (c1 >> 4)}, 2)
        if n == 7:
            if ((c0 >> 5) & 1) != 0 or (c0 & 3) != 0:
                return ("undef",)
            return ("op", "WF", {"h": (c0 >> 6) & 3, "l": (c0 >> 2) & 7}, 1)
        if n == 8:
            if c0 != 0x800:
                return ("undef",)
            c1, bad = self.fetch(pc + 1)
            if bad is not None:
                return ("oor", bad)
            c2, bad = self.fetch(pc + 2)
            if bad is not None:
                return ("oor", bad)
            return ("op", "STI", {"a": c1, "v": c2 >> 7}, 3)
        if n == 15:
            if c0 != 0xFFF:
                return ("undef",)
            return ("op", "HLT", {}, 1)
        return ("undef",)

    # ---- 一個 step ----
    def step(self):
        self.fetch_out_of_range = False
        d = self.decode(self.pc)
        if d[0] == "oor":
            return StepOutcome(
                "out_of_range",
                detail={"space": "prog", "addr": d[1], "access": "fetch"},
            )
        if d[0] == "undef":
            cells = [self.cell(a) for a in (self.pc, self.pc + 1) if a < PROG_SIZE]
            self.pc = (self.pc + 2) & PC_MASK
            self.cycle += 3
            self.steps += 1
            return StepOutcome(
                "step_done",
                "<undefined>",
                cells,
                [],
                (["out_of_range"] if self.fetch_out_of_range else [])
                + ["undefined_opcode"],
                3,
            )

        _, name, f, length = d
        cells = [self.cell(self.pc + i) for i in range(length)]
        next_pc = self.pc + length
        consumed = {
            "LD": 2,
            "ST": 2,
            "ADDI": 1,
            "JZ": 2,
            "IN": 1,
            "JMP": 2,
            "WF": 1,
            "STI": 3,
            "HLT": 1,
        }[name]
        writes = []
        warns = ["out_of_range"] if self.fetch_out_of_range else []
        halted = False
        # 先算所有結果，再在最後決定是否「中止」；本 toy 的 stop 策略只會在語意存取出界時發生
        stop_detail = None

        def mem_read(a):
            nonlocal stop_detail
            if a >= MEM_SIZE:
                if self.oor == "stop":
                    stop_detail = {"space": "mem", "addr": a, "access": "read"}
                    return 0
                warns.append("out_of_range")
                return 31
            return self.mem[a]

        def mem_write(a, v):
            nonlocal stop_detail
            if a >= MEM_SIZE:
                if self.oor == "stop":
                    stop_detail = {"space": "mem", "addr": a, "access": "write"}
                    return
                warns.append("out_of_range")
                return
            v &= MEM_MASK
            writes.append(("mem", "mem", a, self.mem[a], v, 5, False))
            self.mem[a] = v

        def set_w(v):
            v &= W_MASK
            writes.append(("reg", "w", None, self.w, v, 5, False))
            self.w = v

        saved = (self.w, list(self.mem))
        if name == "LD":
            v = mem_read(f["a"])
            if stop_detail is None:
                set_w(v)
            text = f"LD [{f['a']:d}]"
        elif name == "ST":
            mem_write(f["a"], self.w)
            text = f"ST [{f['a']:d}]"
        elif name == "ADDI":
            full = (self.w & W_MASK) + (f["i"] & W_MASK)
            set_w(full & W_MASK)
            mem_write(0, full >> 5)
            text = f"ADDI {f['i']:d}"
        elif name == "JZ":
            if self.w == 0:
                r = f["r"]
                r = r - 256 if r >= 128 else r
                next_pc = next_pc + r
                consumed += 1
            text = f"JZ {f['r']:d}"
        elif name == "IN":
            set_w(self.ports[f["p"]])
            text = f"IN {f['p']:d}"
        elif name == "JMP":
            next_pc = f["a"]
            text = f"JMP {f['a']:d}"
        elif name == "WF":
            old = self.w
            h, l = f["h"], f["l"] & 7
            writes.append(("reg", "w.H", None, (old >> 3) & 3, h & 3, 2, True))
            self.w = ((h & 3) << 3) | (old & 7)
            old2 = self.w
            writes.append(("reg", "w.L", None, old2 & 7, l, 3, True))
            self.w = (old2 & 0x18) | l
            text = f"WF {f['h']:d}, {f['l']:d}"
        elif name == "STI":
            mem_write(f["a"], f["v"])
            text = f"STI [{f['a']:d}], {f['v']:d}"
        else:  # HLT
            halted = True
            text = "HLT"

        if stop_detail is not None:
            # 中止型停止：還原本 step 的所有寫入，PC、cycle 不變
            self.w, self.mem = saved[0], saved[1]
            return StepOutcome("out_of_range", detail=stop_detail)

        self.pc = next_pc & PC_MASK
        self.cycle += consumed
        self.steps += 1
        return StepOutcome(
            "halted" if halted else "step_done", text, cells, writes, warns, consumed
        )
