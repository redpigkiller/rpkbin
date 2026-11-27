from pathlib import Path
import sys

from rpkbin.mcucc.target import Abi, Cls, Flag, Form, I, M, R, Reg, Space, Target

sys.path.insert(0, str(Path(__file__).parent))
from common import arithmetic, control


def make_target():
    forms = [
        Form("mov", "mov", 8, (R("d", "g"), R("s", "g")), {"dst": "d", "a": "s"}, 1, "mov {d}, {s}"),
        Form("ldi", "const", 8, (R("d", "g"), I("k", 8)), {"dst": "d", "a": "k"}, 2, "ldi {d}, #{k}"),
        Form("ld", "ld", 8, (R("d", "g"), M("m", "sram")), {"dst": "d", "a": "m"}, 2, "ld {d}, [{m}]"),
        Form("st", "st", 8, (M("m", "sram"), R("s", "g")), {"a": "m", "b": "s"}, 2, "st [{m}], {s}"),
    ]
    for op in ("add", "sub", "and", "or", "xor"):
        forms.extend(arithmetic(op, "g", 8, immediate, 2 if immediate else 1, "borrow") for immediate in (False, True))
    forms.append(Form("not", "not", 8, (R("d", "g"),), {"dst": "d", "a": "d"}, 1, "not {d}", flags_write={"Z": "zero"}))
    for op in ("shl", "shr"):
        forms.append(Form(op, op, 8, (R("d", "g"), I("n", 1, values=(1,))),
                          {"dst": "d", "a": "d", "b": "n"}, 1, op + " {d}", flags_write={"Z": "zero", "C": "shift_out"}))
    forms.extend(arithmetic("cmp", "g", 8, immediate, 2 if immediate else 1, "borrow") for immediate in (False, True))
    forms.extend(control(3))
    members = tuple("r" + str(i) for i in range(4))
    return Target("t1", 8, (Space("rom", 12, "program"), Space("sram", 8, "data")),
                  tuple(Reg(r, 8) for r in members), (), (Cls("g", members),), (Flag("Z"), Flag("C")), tuple(forms),
                  Abi({8: members}, {8: "r0"}, ()), "invert_over_jump", ";", ".L{fn}_{n}", "{name}:")


def make_verified():
    from machine import verified
    return verified(make_target(), (("helper_a", "r0", "r0", ("r2", "r3"), False),
                                    ("helper_b", "r0", "r0", ("r1", "r2", "r3"), False)))


TARGET, SIM, STUBS = make_verified()
