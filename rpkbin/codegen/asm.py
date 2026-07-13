"""Target-neutral pseudo assembly model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple


@dataclass(frozen=True)
class Instruction:
    opcode: str
    operands: Tuple[str, ...] = ()
    verbatim: bool = False

    def format(self) -> str:
        if self.opcode.endswith(":"):
            return self.opcode
        if not self.operands:
            return self.opcode
        return f"{self.opcode} " + ", ".join(self.operands)


@dataclass(frozen=True)
class AsmFunction:
    name: str
    instructions: Sequence[Instruction]
    instruction_indent: str = ""

    def format(self) -> str:
        return "\n".join(
            instr.format()
            if instr.verbatim or instr.opcode.endswith(":")
            else self.instruction_indent + instr.format()
            for instr in self.instructions
        )


def label(name: str) -> Instruction:
    return Instruction(f"{name}:")


def instr(opcode: str, *operands: str) -> Instruction:
    return Instruction(opcode, tuple(operands))


def format_asm(instructions: Iterable[Instruction]) -> str:
    return "\n".join(instruction.format() for instruction in instructions)
