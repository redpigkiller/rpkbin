from dataclasses import replace
from pathlib import Path
import sys

from rpkbin.mcucc.target import Abi, Alias, Cls, Flag, Form, I, M, P, R, Reg, Space, Target

sys.path.insert(0, str(Path(__file__).parent))
from common import arithmetic, control


def make_target(addr_bits, *, conditional_compare=False, extra_registers=False):
    if addr_bits not in (8, 16):
        raise ValueError(addr_bits)
    comparison_word = "borrow_msb0" if conditional_compare else "nborrow"
    forms = [
        Form("mov", "mov", 8, (R("d", "b"), R("s", "b")), {"dst": "d", "a": "s"}, 1, "mov {d}, {s}"),
        Form("ldi", "const", 8, (R("d", "b"), I("k", 8)), {"dst": "d", "a": "k"}, 2, "ldi {d}, #{k}"),
        Form("ld", "ld", 8, (R("d", "b"), M("m", "sram")), {"dst": "d", "a": "m"}, 1 + addr_bits // 8, "ld {d}, [{m}]"),
        Form("st", "st", 8, (M("m", "sram"), R("s", "b")), {"a": "m", "b": "s"}, 1 + addr_bits // 8, "st [{m}], {s}"),
        Form("ldind", "ld_ind", 8, (R("d", "b"), P("p", "ptr", "sram")), {"dst": "d", "a": "p"}, 1, "ld {d}, [{p}]"),
        Form("stind", "st_ind", 8, (P("p", "ptr", "sram"), R("s", "b")), {"a": "p", "b": "s"}, 2, "st [{p}], {s}"),
    ]
    for op in ("add", "sub", "and", "or", "xor"):
        for immediate in (False, True):
            form = arithmetic(op, "ba", 8, immediate, 1 if op == "add" and not immediate else 2, comparison_word)
            if not immediate:
                form = replace(form, operands=(R("d", "ba"), R("s", "b")))
            forms.append(form)
    forms.append(Form("not", "not", 8, (R("d", "ba"),), {"dst": "d", "a": "d"}, 2, "not {d}", flags_write={"Z": "zero"}))
    for op in ("shl", "shr"):
        forms.append(Form(op, op, 8, (R("d", "ba"), I("n", 1, values=(1,))), {"dst": "d", "a": "d", "b": "n"}, 2,
                          op + " {d}", flags_write={"Z": "zero", "C": "shift_out"}))
    forms.extend((arithmetic("cmp", "b", 8, False, 2, comparison_word),
                  arithmetic("cmp", "ba", 8, True, 2, comparison_word)))
    forms.extend((Form("movw", "mov", 16, (R("d", "w"), R("s", "w")), {"dst": "d", "a": "s"}, 1, "movw {d}, {s}"),
                  Form("ldiw", "const", 16, (R("d", "w"), I("k", 16)), {"dst": "d", "a": "k"}, 3, "ldiw {d}, #{k}")))
    for op in ("add", "sub", "cmp"):
        form = arithmetic(op, "w", 16, False, 1, comparison_word)
        forms.append(replace(form, id=op + "w", asm=op + "w {d}, {s}"))
    forms.extend((Form("push", "push", 16, (R("s", "w"),), {"a": "s"}, 1, "push {s}"),
                  Form("pop", "pop", 16, (R("d", "w"),), {"dst": "d"}, 1, "pop {d}")))
    forms.extend(control(3))
    members = tuple("r" + str(i) for i in range(8 if extra_registers else 6))
    aliases = (Alias("x0", ("r1", "r0")), Alias("x1", ("r3", "r2")))
    if extra_registers:
        aliases += (Alias("x2", ("r7", "r6")),)
    words = tuple(alias.name for alias in aliases)
    return Target("t2", 8, (Space("rom", 12, "program"), Space("sram", addr_bits, "data")),
                  tuple(Reg(r, 8) for r in members), aliases,
                  (Cls("b", members), Cls("ba", members[:4]), Cls("w", words),
                   Cls("ptr", members if addr_bits == 8 else words)),
                  (Flag("Z"), Flag("C")), tuple(forms), Abi({8: ("r0", "r1", "r4", "r5"), 16: ("x0", "x1")}, {8: "r0", 16: "x0"}, ()),
                  "invert_over_jump", ";", ".L{fn}_{n}", "{name}:")


def make_verified(addr_bits, *, conditional_compare=False, extra_registers=False):
    from machine import verified
    return verified(make_target(addr_bits, conditional_compare=conditional_compare,
                                extra_registers=extra_registers),
                    (("helper", "r0", "r0", ("r1", "r2", "r3", "r4", "r5"), False),
                     ("h16", "x1", "x1", ("x0", "x1", "r4", "r5"), True)))


TARGET, SIM, STUBS = make_verified(8)
