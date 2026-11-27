"""Deterministic labels, assembly templates and public reports."""

from dataclasses import replace

from .expand import MInstr, _TargetRef
from .target import I, M


def _label_blocks(function, blocks, order, target):
    referenced = {value.block for sequence in blocks.values() for m in sequence for value in m.values.values() if isinstance(value, _TargetRef)}
    labels, number = {order[0]: function.name}, 1
    for bid in order[1:]:
        if bid in referenced:
            labels[bid] = target.local_label.format(fn=function.name, n=number)
            number += 1
    result = []
    for bid in order:
        if bid in labels:
            result.append(MInstr(None, {}, _block=bid, _label=labels[bid], _target=target, _abi=function.abi))
        for instruction in blocks[bid]:
            result.append(replace(instruction, values={key: labels[value.block] if isinstance(value, _TargetRef) else value for key, value in instruction.values.items()}))
    return tuple(result)


def _render(instruction, target):
    if instruction._label is not None:
        return target.label_def.format(name=instruction._label)
    if instruction._asm is not None:
        return "    " + instruction._asm["rendered"]
    values = {}
    for operand in instruction.form.operands:
        value = instruction.values[operand.name]
        if isinstance(operand, I):
            value = str(value) if value < 10 else hex(value)
        elif isinstance(operand, M):
            bits = operand.bits or next(s.addr_bits for s in target.spaces if s.name == operand.space)
            value = "0x" + format(value.addr, f"0{(bits + 3) // 4}x")
        values[operand.name] = value
    return "    " + instruction.form.asm.format(**values)
