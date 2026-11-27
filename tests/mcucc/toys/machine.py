"""Independent executable test machine and dense instruction coding."""

from dataclasses import replace
from itertools import product

from rpkbin.isasim import MCU, Simulator
from rpkbin.mcucc.target import I, M, P, R, SimBinding, T


def verified(target, externs):
    names = {f.name: "status." + f.name for f in target.flags}
    for alias in target.aliases:
        names[alias.parts[0]] = alias.name + ".hi"
        names[alias.parts[1]] = alias.name + ".lo"
    machine = MCU(target.name)
    for space in target.spaces:
        machine.space(space.name, cell_bits=8, size=1 << space.addr_bits,
                      access="ro" if space.kind == "program" else "rw",
                      reset_state="initialized", init=0, is_program=space.kind == "program")
    machine.space("stack_mem", cell_bits=8, size=4096, access="rw", reset_state="initialized", init=0)
    covered = {p for a in target.aliases for p in a.parts}
    for alias in target.aliases:
        machine.reg(alias.name, bits=16, reset=0, fields={"hi": (15, 8), "lo": (7, 0)})
    for reg in target.regs:
        if reg.name not in covered:
            machine.reg(reg.name, bits=reg.bits, reset=0)
    machine.reg("status", bits=2, reset=0, fields={"Z": 0, "C": 1})
    machine.reg("pc", bits=12, reset=0, is_pc=True)
    machine.reg("sp", bits=16, reset=0)
    machine.on_undefined_opcode("stop")
    machine.on_out_of_range("stop")
    machine.on_write_to_readonly("stop")
    machine.on_uninitialized_read("stop")
    machine.instr("00000000", syntax="halt", cycles=1, name="halt")(lambda st: st.halt("fixture"))

    classes = {c.name: c.members for c in target.classes}
    spaces = {s.name: s for s in target.spaces}
    records = []
    per_form = {f.id: [] for f in target.forms}
    for form in target.forms:
        fixed = []
        variable = []
        for operand in form.operands:
            if isinstance(operand, (R, P)):
                fixed.append((operand.name, classes[operand.cls]))
            elif isinstance(operand, I) and operand.values is not None and len(operand.values) == 1:
                fixed.append((operand.name, operand.values))
            else:
                nb = (operand.bits if isinstance(operand, I) else
                      (operand.bits or spaces[operand.space].addr_bits) if isinstance(operand, M) else
                      operand.rel_bits or operand.abs_bits)
                variable.append((operand.name, nb))
        if len(variable) > 1:
            raise ValueError("toy coding expects at most one variable operand")
        for combination in product(*(values for _, values in fixed)):
            values = dict(zip((name for name, _ in fixed), combination))
            record = [form, values, variable[0] if variable else None, None]
            records.append(record)
            per_form[form.id].append(record)
    next_prefix = 1
    for length in sorted({f.bytes for f in target.forms}):
        cursor = next_prefix << (8 * (length - 1))
        for record in (r for r in records if r[0].bytes == length):
            form, values, variable, _ = record
            nb = variable[1] if variable else 0
            block = 1 << nb
            cursor = ((cursor + block - 1) // block) * block
            if cursor + block > 1 << (8 * length):
                raise ValueError("instruction coding capacity exceeded")
            record[3] = cursor
            binary = format(cursor, f"0{8 * length}b")
            pattern = binary[:-nb] + "k" * nb if nb else binary
            handler = _handler(form, values, variable, names)
            machine.instr(pattern, syntax=form.id + (" {k}" if variable else ""), cycles=1,
                          name=f"op_{len(machine._instructions)}")(handler)
            cursor += block
        next_prefix = (cursor + (1 << (8 * (length - 1))) - 1) >> (8 * (length - 1))
    # Dense one-cell counts, including halt: T0=5, T1=126, T2(8)=118, T2(16)=94.
    assert sum(1 for r in records if r[0].bytes == 1) + 1 < 256

    def encoder(form, entries):
        def encode(values):
            record = next((r for r in entries if all(values[k] == v for k, v in r[1].items())), None)
            if record is None:
                raise ValueError((form.id, values))
            variable = record[2]
            payload = 0
            if variable:
                payload = values[variable[0]] & ((1 << variable[1]) - 1)
            code = record[3] | payload
            return tuple((code >> (8 * i)) & 255 for i in reversed(range(form.bytes)))
        return encode
    forms = tuple(replace(f, encode=encoder(f, per_form[f.id])) for f in target.forms)
    target = replace(target, forms=forms)
    binding = SimBinding(lambda: Simulator(machine), "rom", "pc", (0,), names=names)
    stubs = _stubs(target, externs)
    return target, binding, stubs


def _handler(form, fixed, variable, names):
    operands = {o.name: o for o in form.operands}
    def execute(st, values):
        def regname(value):
            return names.get(value, value)
        def argument(key):
            operand = operands[form.bind[key]]
            value = values[operand.name]
            if isinstance(operand, (R, P)):
                return st.reg[regname(value)]
            if isinstance(operand, M) and not (form.op in ("ld", "st") and key == "a"):
                return st.mem[operand.space][value]
            return value
        def write(value):
            operand = operands[form.bind["dst"]]
            st.reg[regname(values[operand.name])] = value
        def push(value):
            pointer = st.reg["sp"]
            st.mem["stack_mem"].write_word(pointer, 2, value, "little")
            st.reg["sp"] = pointer + 2
        def pop():
            pointer = st.reg["sp"]
            if pointer < 2:
                st.fault("stack_underflow", "empty test stack")
            st.reg["sp"] = pointer - 2
            return st.mem["stack_mem"].read_word(pointer - 2, 2, "little")
        op = form.op
        if op in ("call", "jmp", "br"):
            operand = next(o for o in form.operands if isinstance(o, T))
            dest = values[operand.name]
            if operand.rel_bits:
                if dest >= 1 << (operand.rel_bits - 1):
                    dest -= 1 << operand.rel_bits
                dest += st.next_pc if operand.base == "end" else st.pc
            if op == "call":
                push(st.next_pc)
            if op != "br" or all(st.reg[regname(k)] == v for k, v in form.flags_read.items()):
                st.next_pc = dest
            return
        if op == "ret":
            st.next_pc = pop()
            return
        if op == "push":
            push(argument("a"))
            return
        if op == "pop":
            write(pop())
            return
        a = argument("a")
        b = argument("b") if "b" in form.bind else None
        if op in ("ld", "ld_ind"):
            operand = operands[form.bind["a"]]
            write(st.mem[operand.space][a])
            return
        if op in ("st", "st_ind"):
            operand = operands[form.bind["a"]]
            st.mem[operand.space][a] = b
            return
        if op in ("mov", "const"):
            result = a
        elif op == "add":
            result = a + b
        elif op in ("sub", "cmp"):
            result = a - b
        elif op == "and":
            result = a & b
        elif op == "or":
            result = a | b
        elif op == "xor":
            result = a ^ b
        elif op == "not":
            result = ~a
        elif op == "shl":
            result = a << b
        elif op == "shr":
            result = a >> b
        else:
            raise ValueError(op)
        result &= (1 << form.width) - 1
        if "dst" in form.bind:
            write(result)
        for flag, word in (form.flags_write or {}).items():
            if word == "zero":
                value = int(result == 0)
            elif word == "carry":
                value = int(a + b >= 1 << form.width)
            elif word == "borrow":
                value = int(a < b)
            elif word == "nborrow":
                value = int(a >= b)
            elif word == "borrow_msb0":
                high = 1 << (form.width - 1)
                value = int((a & high) == (b & high) and (a & high) != (result & high))
            elif word == "shift_out":
                value = (a >> (form.width - b if op == "shl" else b - 1)) & 1
            else:
                raise ValueError(word)
            st.reg[regname(flag)] = value
    if variable:
        def handler(st, k):
            execute(st, {**fixed, variable[0]: k})
    else:
        def handler(st):
            execute(st, fixed)
    return handler


def _stubs(target, externs):
    forms = {f.id: f for f in target.forms}
    def instruction(form_id, **values):
        return forms[form_id].encode(values)
    result = {}
    for name, arg, ret, clobbers, wide in externs:
        code = []
        if wide:
            code.extend(instruction("ldiw", d="x0", k=0x0101))
            code.extend(instruction("addw", d=arg, s="x0"))
        else:
            form_id = "xor_i" if "xor_i" in forms else "xor_ri"
            code.extend(instruction(form_id, d=arg, k=0x5a))
        returning = next((set(a.parts) for a in target.aliases if a.name == ret), {ret})
        for reg in clobbers:
            parts = next((set(a.parts) for a in target.aliases if a.name == reg), {reg})
            if returning & parts:
                continue
            code.extend(instruction("ldiw" if len(parts) == 2 else "ldi", d=reg, k=0xa5a5 if len(parts) == 2 else 0xa5))
        code.extend(instruction("ret"))
        result[name] = tuple(code)
    return result
