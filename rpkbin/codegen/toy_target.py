"""Public fake target used for tests and examples.

This target is intentionally simple and not modeled after any real MCU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .asm import AsmFunction, Instruction, instr, label
from .lir import (
    CallStmt, Extend, Fragment, FragmentExit, FullExpr, VReg,
    BrCmp, CallAssign, MultiReturn, MemLoad, MemStore, BitOp, SymbolAddr, InlineAsmExpr,
)
from .ir import Assign, BinOp, Block, BrIf, Cmp, Const, Function, Jump, Return, Var


BIN_OPCODES = {
    "add": "ADD",
    "sub": "SUB",
    "mul": "MUL",
    "and": "AND",
    "or": "OR",
    "xor": "XOR",
    "shl": "SHL",
    "shr": "SHR",
}

BRANCH_OPCODES = {
    "eq": "BEQ",
    "ne": "BNE",
    "lt": "BLT",
    "le": "BLE",
    "gt": "BGT",
    "ge": "BGE",
}


@dataclass
class ToyTarget:
    name: str = "toy"

    def select_instructions(self, func: Function) -> AsmFunction:
        selector = _ToySelector()
        return AsmFunction(func.name, tuple(selector.lower_function(func)))

    def select_fragment_instructions(self, fragment: Fragment) -> AsmFunction:
        selector = _ToyFragmentSelector()
        return AsmFunction(fragment.name, tuple(selector.lower_fragment(fragment)))


@dataclass
class _ToySelector:
    instructions: List[Instruction] = field(default_factory=list)
    temp_id: int = 0

    def lower_function(self, func: Function) -> List[Instruction]:
        for block in func.blocks:
            self._lower_block(block)
        return self.instructions

    def _lower_block(self, block: Block) -> None:
        self.instructions.append(label(block.label))
        for stmt in block.statements:
            if isinstance(stmt, Assign):
                self._emit_expr_into(stmt.value, stmt.target.name)
            elif isinstance(stmt, CallStmt):
                args_str = ", ".join(self._operand_for(a) for a in stmt.call.args)
                self.instructions.append(instr("CALL", f"{stmt.call.name}({args_str})"))
            elif isinstance(stmt, CallAssign):
                self._emit_call_assign(stmt)
            elif isinstance(stmt, MemStore):
                addr_str = self._operand_for(stmt.addr)
                val_str = self._operand_for(stmt.value)
                self.instructions.append(instr("MOV", f"[{addr_str}]", val_str))
            elif isinstance(stmt, BitOp):
                var_str = self._operand_for(stmt.var)
                if stmt.kind == "set":
                    self.instructions.append(instr("BSET", var_str, f"#{stmt.bit_idx}"))
                elif stmt.kind == "clr":
                    self.instructions.append(instr("BCLR", var_str, f"#{stmt.bit_idx}"))
                else:
                    raise TypeError(f"BitOp kind {stmt.kind!r} is not a statement")
            else:
                raise TypeError(f"unsupported statement: {stmt!r}")
        self._lower_terminator(block.terminator)

    def _lower_terminator(self, term) -> None:
        if isinstance(term, BrIf):
            self._emit_branch(term.cond, term.true_label, term.false_label)
        elif isinstance(term, Jump):
            self.instructions.append(instr("JMP", term.label))
        elif isinstance(term, Return):
            operand = self._operand_for(term.value)
            self.instructions.append(instr("RET", operand))
        elif isinstance(term, BrCmp):
            # Compare-and-branch: emit CMP then conditional branch
            left = self._operand_for(term.left)
            right = self._operand_for(term.right)
            opcode = BRANCH_OPCODES.get(term.op)
            if opcode is None:
                raise ValueError(f"unsupported BrCmp op: {term.op!r}")
            self.instructions.append(instr("CMP", left, right))
            self.instructions.append(instr(opcode, term.true_label))
            self.instructions.append(instr("JMP", term.false_label))
        elif isinstance(term, MultiReturn):
            # Emit a RET for each return value
            for val in term.values:
                operand = self._operand_for(val)
                self.instructions.append(instr("RET", operand))
        else:
            raise TypeError(f"unsupported terminator: {term!r}")

    def _emit_branch(self, cond: FullExpr, true_label: str, false_label: str) -> None:
        if isinstance(cond, Cmp):
            left = self._operand_for(cond.left)
            right = self._operand_for(cond.right)
            opcode = BRANCH_OPCODES.get(cond.op)
            if opcode is None:
                raise ValueError(f"unsupported comparison op: {cond.op!r}")
            self.instructions.append(instr("CMP", left, right))
            self.instructions.append(instr(opcode, true_label))
            self.instructions.append(instr("JMP", false_label))
            return

        if isinstance(cond, BitOp) and cond.kind == "test":
            var_str = self._operand_for(cond.var)
            self.instructions.append(instr("BTEST", var_str, f"#{cond.bit_idx}"))
            self.instructions.append(instr("BNE", true_label))
            self.instructions.append(instr("JMP", false_label))
            return

        value = self._operand_for(cond)
        self.instructions.append(instr("CMP", value, "#0"))
        self.instructions.append(instr("BNE", true_label))
        self.instructions.append(instr("JMP", false_label))

    def _emit_expr_into(self, expr: FullExpr, dest: str) -> None:
        if isinstance(expr, Const):
            self.instructions.append(instr("MOV", dest, f"#{expr.value}"))
        elif isinstance(expr, Var):
            if expr.name != dest:
                self.instructions.append(instr("MOV", dest, expr.name))
        elif isinstance(expr, VReg):
            vreg_name = f"%{expr.name}"
            if vreg_name != dest:
                self.instructions.append(instr("MOV", dest, vreg_name))
        elif isinstance(expr, BinOp):
            self._emit_expr_into(expr.left, dest)
            opcode = BIN_OPCODES.get(expr.op)
            if opcode is None:
                raise ValueError(f"unsupported binary op: {expr.op!r}")
            self.instructions.append(instr(opcode, dest, self._operand_for(expr.right)))
        elif isinstance(expr, Cmp):
            self.instructions.append(instr("MOV", dest, "#0"))
            self.instructions.append(instr("CMP", self._operand_for(expr.left), self._operand_for(expr.right)))
            self.instructions.append(instr(f"SET_{expr.op.upper()}", dest))
        elif isinstance(expr, Extend):
            src = self._operand_for(expr.value)
            src_width = getattr(expr.value, "width", expr.width)
            if src_width == expr.width:
                if src != dest:
                    self.instructions.append(instr("MOV", dest, src))
            else:
                opcode = "SEXT" if expr.kind == "sext" else "ZEXT"
                self.instructions.append(instr(opcode, dest, src))
        elif isinstance(expr, MemLoad):
            addr_str = self._operand_for(expr.addr)
            self.instructions.append(instr("MOV", dest, f"[{addr_str}]"))
        elif isinstance(expr, BitOp) and expr.kind == "test":
            # BitOp("test") as an expression value — emit into dest as 0/1
            var_str = self._operand_for(expr.var)
            self.instructions.append(instr("MOV", dest, "#0"))
            self.instructions.append(instr("BTEST", var_str, f"#{expr.bit_idx}"))
            self.instructions.append(instr("SET_NE", dest))
        elif isinstance(expr, SymbolAddr):
            self.instructions.append(instr("MOV", dest, f"&{expr.name}"))
        elif isinstance(expr, InlineAsmExpr):
            # Raw passthrough: emit the text verbatim, no opcode prefix.
            # HInlineAsm("NOP") must appear as "NOP" in the pseudo-ASM output,
            # not "ASM NOP" or any other wrapped form.
            self.instructions.append(Instruction(opcode=expr.text, operands=()))
        else:
            raise TypeError(f"unsupported expression: {expr!r}")

    def _operand_for(self, expr: FullExpr) -> str:
        if isinstance(expr, Const):
            return f"#{expr.value}"
        if isinstance(expr, Var):
            return expr.name
        if isinstance(expr, VReg):
            return f"%{expr.name}"
        if isinstance(expr, SymbolAddr):
            return f"&{expr.name}"
        temp = self._new_temp()
        self._emit_expr_into(expr, temp)
        return temp

    def _emit_call_assign(self, stmt: CallAssign) -> None:
        args_str = ", ".join(self._operand_for(a) for a in stmt.call.args)
        if stmt.abi_return_regs:
            self.instructions.append(instr("CALL", f"{stmt.call.name}({args_str})"))
            for target, src in zip(stmt.targets, stmt.abi_return_regs):
                if target is None:
                    continue
                dest = self._call_target_name(target)
                if dest != src:
                    self.instructions.append(instr("MOV", dest, src))
            return

        targets = ", ".join(
            self._operand_for(t) if t is not None else "_"
            for t in stmt.targets
        )
        self.instructions.append(
            instr("CALL", f"({targets})", f"{stmt.call.name}({args_str})")
        )

    @staticmethod
    def _call_target_name(target) -> str:
        if isinstance(target, (Var, VReg)):
            return target.name
        raise TypeError(f"unsupported call target: {target!r}")

    def _new_temp(self) -> str:
        name = f"t{self.temp_id}"
        self.temp_id += 1
        return name


# ---------------------------------------------------------------------------
# Fragment instruction selector (pseudo-ASM for fragments)
# ---------------------------------------------------------------------------

@dataclass
class _ToyFragmentSelector(_ToySelector):
    """Extension of ``_ToySelector`` for ``Fragment`` pseudo-ASM emission.

    Differences from ``_ToySelector``:

    * ``FragmentExit`` produces no instructions.
    * VReg operands use their ``hint`` (physical register name) rather than
      ``%name``.
    * Assignment destinations follow the same rule: binding VReg targets
      use the physical register name.
    """

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def lower_fragment(self, fragment: Fragment) -> List[Instruction]:
        for block in fragment.blocks:
            self._lower_block(block)
        return self.instructions

    # ------------------------------------------------------------------
    # Block lowering — overrides parent to use physical-reg destinations
    # ------------------------------------------------------------------

    def _lower_block(self, block: Block) -> None:
        self.instructions.append(label(block.label))
        for stmt in block.statements:
            if isinstance(stmt, Assign):
                dest = self._target_name(stmt.target)
                self._emit_expr_into(stmt.value, dest)
            elif isinstance(stmt, CallStmt):
                args_str = ", ".join(self._operand_for(a) for a in stmt.call.args)
                self.instructions.append(instr("CALL", f"{stmt.call.name}({args_str})"))
            elif isinstance(stmt, CallAssign):
                self._emit_call_assign(stmt)
            elif isinstance(stmt, MemStore):
                addr_str = self._operand_for(stmt.addr)
                val_str = self._operand_for(stmt.value)
                self.instructions.append(instr("MOV", f"[{addr_str}]", val_str))
            elif isinstance(stmt, BitOp):
                var_str = self._operand_for(stmt.var)
                if stmt.kind == "set":
                    self.instructions.append(instr("BSET", var_str, f"#{stmt.bit_idx}"))
                elif stmt.kind == "clr":
                    self.instructions.append(instr("BCLR", var_str, f"#{stmt.bit_idx}"))
                else:
                    raise TypeError(f"BitOp kind {stmt.kind!r} is not a statement")
            else:
                raise TypeError(f"unsupported statement: {stmt!r}")
        self._lower_terminator(block.terminator)

    # ------------------------------------------------------------------
    # Terminator lowering — FragmentExit produces no instructions
    # ------------------------------------------------------------------

    def _lower_terminator(self, term) -> None:
        if isinstance(term, FragmentExit):
            return
        super()._lower_terminator(term)

    # ------------------------------------------------------------------
    # Operand formatting — VReg with hint uses physical register name
    # ------------------------------------------------------------------

    def _operand_for(self, expr) -> str:
        if isinstance(expr, VReg) and expr.hint:
            return expr.hint
        return super()._operand_for(expr)

    # ------------------------------------------------------------------
    # Expression emission — VReg with hint target uses physical reg name
    # ------------------------------------------------------------------

    def _emit_expr_into(self, expr, dest: str) -> None:
        if isinstance(expr, VReg):
            reg_name = expr.hint if expr.hint else f"%{expr.name}"
            if reg_name != dest:
                self.instructions.append(instr("MOV", dest, reg_name))
            return
        super()._emit_expr_into(expr, dest)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _target_name(target) -> str:
        """Return the physical register name for VReg bindings, else the var name."""
        if isinstance(target, VReg) and target.hint:
            return target.hint
        return target.name
