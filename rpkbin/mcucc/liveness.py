"""Virtual liveness, definition-point interference and mention counts."""

from .ir import VReg, _ir_defs_uses, _live
from .target import units


def _analyze(function, points=None):
    if points is None:
        points = _live(function)[2]
    mentions, reasons = {}, {}
    for block in function.blocks:
        for index, instruction in enumerate((*block.instrs, block.term)):
            values = (*instruction.args, instruction.dst, *instruction.meta.get("outs", ()))
            for value in values:
                if isinstance(value, VReg):
                    mentions[value] = mentions.get(value, 0) + 1
            defined, _ = _ir_defs_uses(instruction)
            for value in sorted(defined, key=lambda v: v.id):
                for other in sorted(points[block.id, index] - {value}, key=lambda v: v.id):
                    edge = tuple(sorted((value, other), key=lambda v: v.id))
                    reasons.setdefault(edge, []).append((instruction, value, other))
    return points, mentions, reasons


def _compatible(target, reasons, assignments):
    # V independently rebuilds every definition point; no allocator graph is passed to V.
    from .verify import _copy_match
    for (left, right), points in reasons.items():
        if left not in assignments or right not in assignments:
            continue
        if units(target, assignments[left]) & units(target, assignments[right]):
            if not all(instruction is not None and _copy_match(target, instruction, defined, other, assignments)
                       for instruction, defined, other in points):
                return False
    return True
