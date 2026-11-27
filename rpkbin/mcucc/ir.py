"""Virtual-register control flow and its machine-independent interpreter."""

from copy import deepcopy
from dataclasses import dataclass, field

from .errors import InternalError, UnsupportedError
from .opsem import condition_holds, evaluate


@dataclass(frozen=True)
class VReg:
    id: int
    width: int
    name_hint: str | None = None


@dataclass(frozen=True)
class Imm:
    value: int
    width: int


@dataclass
class Instr:
    op: str
    dst: object
    args: tuple
    meta: dict = field(default_factory=dict)


@dataclass
class Block:
    id: int
    instrs: list
    term: object = None


@dataclass
class Function:
    name: str
    params: list
    ret_width: object
    blocks: list
    abi: dict
    _vregs: list = field(default_factory=list)
    _filename: str = "<source>"
    _external: bool = False


def _successors(block):
    term = block.term
    if term.op == "jmp":
        return (term.meta["target"],)
    if term.op == "br":
        return tuple(dict.fromkeys((term.meta["true"], term.meta["false"])))
    return ()


def _ir_defs_uses(instruction):
    if instruction.op == "entry":
        return set(instruction.args), set()
    definitions = {instruction.dst} if instruction.dst is not None else set()
    definitions.update(instruction.meta.get("outs", ()))
    return definitions, {v for v in instruction.args if isinstance(v, VReg)}


def _live(function):
    ins = {b.id: set() for b in function.blocks}
    outs = {b.id: set() for b in function.blocks}
    changed = True
    while changed:
        changed = False
        for block in reversed(function.blocks):
            after = set().union(*(ins[s] for s in _successors(block)))
            before = set(after)
            for instruction in reversed((*block.instrs, block.term)):
                defined, used = _ir_defs_uses(instruction)
                before = (before - defined) | used
            if before != ins[block.id] or after != outs[block.id]:
                ins[block.id], outs[block.id], changed = before, after, True
    points = {}
    for block in function.blocks:
        live = set(outs[block.id])
        sequence = (*block.instrs, block.term)
        for index in reversed(range(len(sequence))):
            points[block.id, index] = set(live)
            defined, used = _ir_defs_uses(sequence[index])
            live = (live - defined) | used
    return ins, outs, points


def ir_eval(function, args, memory, functions):
    state = deepcopy(memory)
    budget = [200000]
    def execute(current, values):
        if len(values) != len(current.params):
            raise ValueError(f"IR {current.name}: argument count mismatch")
        regs = {p: v & ((1 << p.width) - 1) for p, v in zip(current.params, values)}
        blocks = {b.id: b for b in current.blocks}
        block = current.blocks[0]
        def value(arg):
            return arg.value if isinstance(arg, Imm) else regs[arg]
        while True:
            for instruction in block.instrs:
                budget[0] -= 1
                if budget[0] < 0:
                    raise TimeoutError(f"IR {current.name}: step budget exhausted")
                op = instruction.op
                if op == "entry":
                    continue
                operands = [value(a) for a in instruction.args]
                width = instruction.dst.width if instruction.dst is not None else 8
                if op == "load":
                    result = state[instruction.meta["space"]][operands[0]]
                elif op == "store":
                    state[instruction.meta["space"]][operands[0]] = operands[1]
                    continue
                elif op == "call":
                    result = execute(functions[instruction.meta["callee"]], operands)
                elif op in ("trunc", "zext"):
                    result = operands[0]
                elif op == "hi":
                    result = operands[0] >> 8
                elif op == "pack":
                    result = (operands[0] << 8) | operands[1]
                elif op == "asm":
                    raise UnsupportedError(f"IR {current.name}: assembly reference not available")
                else:
                    result = evaluate(op, width, *operands)
                if instruction.dst is not None:
                    regs[instruction.dst] = result & ((1 << width) - 1)
            budget[0] -= 1
            if budget[0] < 0:
                raise TimeoutError(f"IR {current.name}: step budget exhausted")
            term = block.term
            if term.op == "ret":
                return value(term.args[0]) if term.args else None
            if term.op == "jmp":
                block = blocks[term.meta["target"]]
            elif term.op == "br":
                a, b = (value(v) for v in term.args)
                edge = "true" if condition_holds(term.meta["cond"], a, b, term.args[0].width) else "false"
                block = blocks[term.meta[edge]]
            else:
                raise InternalError(f"IR {current.name}: unknown terminator {term.op!r}")
    return execute(function, args), state
