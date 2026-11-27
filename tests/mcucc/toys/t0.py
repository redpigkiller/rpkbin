from pathlib import Path
import sys

from rpkbin.mcucc.target import Abi, Cls, Flag, Form, I, M, MemLoc, R, Reg, Space, Target

sys.path.insert(0, str(Path(__file__).parent))
from common import control


def make_target():
    forms = [
        Form("ldi", "const", 8, (R("d", "W"), I("k", 8)), {"dst": "d", "a": "k"}, 2, "ldi {d}, #{k}"),
        Form("ld", "ld", 8, (R("d", "W"), M("m", "sram")), {"dst": "d", "a": "m"}, 2, "ld {d}, [{m}]"),
        Form("st", "st", 8, (M("m", "sram"), R("s", "W")), {"a": "m", "b": "s"}, 2, "st [{m}], {s}"),
    ]
    for op in ("add", "sub", "and", "or", "xor", "cmp"):
        for suffix, operand in (("i", I("k", 8)), ("m", M("m", "sram"))):
            bind = {"a": "d", "b": operand.name}
            if op != "cmp":
                bind = {"dst": "d", **bind}
            writes = {"Z": "zero"}
            if op in ("add", "sub", "cmp"):
                writes["C"] = "carry" if op == "add" else "borrow"
            source = "#{k}" if suffix == "i" else "[{m}]"
            forms.append(Form(op + "_" + suffix, op, 8, (R("d", "W"), operand), bind, 2,
                              op + " {d}, " + source, flags_write=writes))
    for op in ("shl", "shr"):
        forms.append(Form(op, op, 8, (R("d", "W"), I("n", 1, values=(1,))),
                          {"dst": "d", "a": "d", "b": "n"}, 1, op + " {d}",
                          flags_write={"Z": "zero", "C": "shift_out"}))
    forms.append(Form("not", "not", 8, (R("d", "W"),), {"dst": "d", "a": "d"}, 1, "not {d}", flags_write={"Z": "zero"}))
    forms.extend(control(2))
    return Target("t0", 8, (Space("rom", 12, "program"), Space("sram", 8, "data")),
                  (Reg("w", 8),), (), (Cls("W", ("w",)),), (Flag("Z"), Flag("C")), tuple(forms),
                  Abi({8: ("w", *(MemLoc("sram", n) for n in range(0x10, 0x14)))}, {8: "w"}, ()),
                  "invert_over_jump", ";", ".L{fn}_{n}", "{name}:")


def make_verified():
    from machine import verified
    return verified(make_target(), (("helper", "w", "w", (), False),))


TARGET, SIM, STUBS = make_verified()
