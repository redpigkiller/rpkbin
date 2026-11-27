"""Choose the width of ordered comparisons before instruction selection."""

from .errors import UnsupportedError
from .ir import Imm, Instr, VReg, _ir_defs_uses
from .opsem import _BORROW_FLAGS, _MSB0_FLAGS


def _msb0(value, width, instructions, before, depth=0):
    if depth > 4:
        return False
    if isinstance(value, Imm):
        return value.value < 1 << (width - 1)
    for index in range(before - 1, -1, -1):
        definition = instructions[index]
        if definition.dst != value:
            if value in _ir_defs_uses(definition)[0]:
                return False
            continue
        op, args = definition.op, definition.args
        if op == "const":
            return args[0].value < 1 << (width - 1)
        if op == "zext":
            return args[0].width < width
        if op == "and":
            return any(isinstance(arg, Imm) and arg.value < 1 << (width - 1)
                       or _msb0(arg, width, instructions, index, depth + 1)
                       for arg in args)
        if op == "shr":
            return isinstance(args[1], Imm) and args[1].value >= 1
        if op == "mov":
            return _msb0(args[0], width, instructions, index, depth + 1)
        return False
    return False


def _widen_comparisons(function, target):
    branch_flags = {flag for form in target.forms if form.op == "br" for flag in form.flags_read}
    available = {8: set(), 16: set()}
    for form in target.forms:
        if form.op == "cmp" and form.width in available:
            available[form.width].update(word for flag, word in (form.flags_write or {}).items()
                                         if flag in branch_flags and word in _BORROW_FLAGS)
    free = {width: bool(words - _MSB0_FLAGS) for width, words in available.items()}
    conditional = {width: bool(words & _MSB0_FLAGS) for width, words in available.items()}
    next_id = max((v.id for v in function._vregs), default=-1) + 1
    for block in function.blocks:
        branch = block.term
        if branch.op != "br" or branch.meta["cond"] in ("eq", "ne"):
            continue
        width = branch.args[0].width
        if free[width]:
            continue
        if conditional[width] and all(_msb0(arg, width, block.instrs, len(block.instrs))
                                      for arg in branch.args):
            branch.meta["msb0_ok"] = True
            continue
        if width == 8 and (free[16] or conditional[16]):
            args = []
            for arg in branch.args:
                if isinstance(arg, Imm):
                    args.append(Imm(arg.value, 16))
                else:
                    extended = VReg(next_id, 16)
                    next_id += 1
                    function._vregs.append(extended)
                    block.instrs.append(Instr("zext", extended, (arg,), {"line": branch.meta["line"]}))
                    args.append(extended)
            branch.args = tuple(args)
            if conditional[16]:
                branch.meta["msb0_ok"] = True
            continue
        if conditional[width]:
            raise UnsupportedError(f"{function.name}:{branch.meta['line']}: "
                                   f"{branch.meta['cond']} ({width}): "
                                   "此目標的有序比較只在運算元最高位為 0 時成立（無證明）")
