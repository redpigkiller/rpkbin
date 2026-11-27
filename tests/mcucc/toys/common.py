"""Shared declarations for the deliberately small test machines."""

from rpkbin.mcucc.target import Form, I, R, T


def arithmetic(op, cls, width, immediate, length, convention):
    operand = I("k", width) if immediate else R("s", cls)
    key = operand.name
    operands = (R("d", cls), operand)
    bind = {"a": "d", "b": key}
    if op != "cmp":
        bind = {"dst": "d", **bind}
    writes = {"Z": "zero"}
    if op == "add":
        writes["C"] = "carry"
    elif op in ("sub", "cmp"):
        writes["C"] = convention
    return Form(op + ("_ri" if immediate else "_rr"), op, width, operands, bind,
                length, op + " {d}, " + ("#{k}" if immediate else "{s}"), flags_write=writes)


def control(call_bytes):
    forms = [Form("jmp", "jmp", 0, (T("t", rel_bits=8),), {}, 2, "jmp {t}")]
    for name, flag, value in (("jz", "Z", 1), ("jnz", "Z", 0), ("jc", "C", 1), ("jnc", "C", 0)):
        forms.append(Form(name, "br", 0, (T("t", rel_bits=8),), {}, 2, name + " {t}", flags_read={flag: value}))
    forms.extend((Form("jmpf", "jmp", 0, (T("t", abs_bits=12),), {}, 3, "jmpf {t}"),
                  Form("call", "call", 0, (T("t", abs_bits=12),), {}, call_bytes, "call {t}"),
                  Form("ret", "ret", 0, (), {}, 1, "ret")))
    return forms
