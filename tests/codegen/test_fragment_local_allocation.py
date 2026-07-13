from __future__ import annotations

import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.hir import (
    HAssign,
    HBinOp,
    HConst,
    HExit,
    HFragment,
    HFragmentBinding,
    HVar,
    UInt,
)
from rpkbin.codegen.hir_validate import HIRValidationError, validate_hfragment
from rpkbin.codegen.lower import lower_fragment
from rpkbin.codegen.pipeline import run_codegen_from_fragment
from rpkbin.codegen.register_alloc import (
    RegisterAllocationError,
    allocate_fragment_registers,
)
from rpkbin.codegen.toy_target import ToyTarget


class _FragmentModel:
    _WIDTHS = {
        "bind_in": 8,
        "bind_out": 8,
        "lo": 8,
        "hi": 8,
        "wide": 16,
        "phys_only": 8,
    }

    def allocatable_registers(self):
        return ["lo", "hi", "wide"]

    def is_physical_register(self, reg: str) -> bool:
        return reg in self._WIDTHS

    def fixed_register_hints(self) -> bool:
        return True

    def register_width(self, reg: str) -> int:
        return self._WIDTHS[reg]

    def can_allocate(self, reg: str, width: int) -> bool:
        return self._WIDTHS.get(reg, 0) >= width

    def register_aliases(self):
        return [("wide", ["lo", "hi"])]

    def registers_overlap(self, lhs: str, rhs: str) -> bool:
        return lhs == rhs or frozenset((lhs, rhs)) in {
            frozenset(("wide", "lo")),
            frozenset(("wide", "hi")),
        }

    def spill_slots(self):
        return []


class _FragmentModelWithSpill(_FragmentModel):
    def spill_slots(self):
        return [lir.SpillSlot(id=0, address=0x20)]


MODEL = _FragmentModel()
SPILL_MODEL = _FragmentModelWithSpill()


def _binding(name: str, reg: str, width: int = 8, mode: str = "in") -> HFragmentBinding:
    return HFragmentBinding(name=name, ty=UInt(width), reg=reg, mode=mode)


def _fragment(bindings=(), scratch=(), body=()) -> HFragment:
    return HFragment(name="frag", bindings=bindings, scratch_regs=scratch, body=body)


def _names_in_expr(expr) -> set[str]:
    if isinstance(expr, (lir.Var, lir.VReg)):
        return {expr.name}
    if isinstance(expr, lir.BinOp):
        return _names_in_expr(expr.left) | _names_in_expr(expr.right)
    if isinstance(expr, lir.Cmp):
        return _names_in_expr(expr.left) | _names_in_expr(expr.right)
    if isinstance(expr, lir.Extend):
        return _names_in_expr(expr.value)
    if isinstance(expr, lir.MemLoad):
        return _names_in_expr(expr.addr)
    if isinstance(expr, lir.BitOp):
        return _names_in_expr(expr.var)
    if isinstance(expr, lir.Call):
        names: set[str] = set()
        for arg in expr.args:
            names |= _names_in_expr(arg)
        return names
    return set()


def _names_in_fragment(fragment: lir.Fragment) -> set[str]:
    names: set[str] = set()
    for block in fragment.blocks:
        for stmt in block.statements:
            if isinstance(stmt, lir.Assign):
                names |= _names_in_expr(stmt.target)
                names |= _names_in_expr(stmt.value)
            elif isinstance(stmt, lir.CallStmt):
                names |= _names_in_expr(stmt.call)
            elif isinstance(stmt, lir.CallAssign):
                for target in stmt.targets:
                    if target is not None:
                        names |= _names_in_expr(target)
                names |= _names_in_expr(stmt.call)
            elif isinstance(stmt, lir.MemStore):
                names |= _names_in_expr(stmt.addr)
                names |= _names_in_expr(stmt.value)
            elif isinstance(stmt, lir.BitOp):
                names |= _names_in_expr(stmt.var)
        term = block.terminator
        if isinstance(term, lir.BrIf):
            names |= _names_in_expr(term.cond)
        elif isinstance(term, lir.BrCmp):
            names |= _names_in_expr(term.left)
            names |= _names_in_expr(term.right)
        elif isinstance(term, lir.Return):
            names |= _names_in_expr(term.value)
        elif isinstance(term, lir.MultiReturn):
            for value in term.values:
                names |= _names_in_expr(value)
    return names


def test_fragment_local_allocates_from_scratch_not_binding_reg():
    frag = _fragment(
        bindings=(
            _binding("src", "bind_in"),
            _binding("dst", "bind_out", mode="out"),
        ),
        scratch=("lo",),
        body=(
            HAssign(HVar("tmp", UInt(8)), HVar("src", UInt(8))),
            HAssign(HVar("dst", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    lowered = lower_fragment(frag)
    allocated, assignment = allocate_fragment_registers(lowered, MODEL)
    result = run_codegen_from_fragment(frag, ToyTarget(), register_model=MODEL)

    assert assignment["tmp"] == "lo"
    assert assignment["tmp"] not in {"bind_in", "bind_out"}
    assert "tmp" not in _names_in_fragment(allocated)
    assert "tmp" not in result.asm.format()


def test_fragment_local_does_not_use_undeclared_physical_reg():
    frag = _fragment(
        bindings=(_binding("dst", "bind_out", mode="out"),),
        scratch=("lo",),
        body=(
            HAssign(HVar("tmp", UInt(8), reg_hint="phys_only"), HConst(1, UInt(8))),
            HAssign(HVar("dst", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="scratch_regs"):
        validate_hfragment(frag, MODEL)


def test_two_live_fragment_locals_use_two_non_overlapping_scratch_regs():
    frag = _fragment(
        bindings=(_binding("dst", "bind_out", mode="out"),),
        scratch=("lo", "hi"),
        body=(
            HAssign(HVar("lhs", UInt(8)), HConst(1, UInt(8))),
            HAssign(HVar("rhs", UInt(8)), HConst(2, UInt(8))),
            HAssign(
                HVar("dst", UInt(8)),
                HBinOp("add", HVar("lhs", UInt(8)), HVar("rhs", UInt(8)), UInt(8)),
            ),
            HExit(),
        ),
    )

    result = run_codegen_from_fragment(frag, ToyTarget(), register_model=MODEL)

    assert {
        result.register_assignment["lhs"],
        result.register_assignment["rhs"],
    } == {"lo", "hi"}
    assert "lhs" not in result.asm.format()
    assert "rhs" not in result.asm.format()


def test_overlapping_scratch_regs_are_rejected():
    frag = _fragment(
        scratch=("wide", "lo"),
        body=(HExit(),),
    )

    with pytest.raises(HIRValidationError, match="aliases|overlaps"):
        validate_hfragment(frag, MODEL)


def test_scratch_reg_overlapping_binding_reg_is_rejected():
    frag = _fragment(
        bindings=(_binding("word", "wide", width=16),),
        scratch=("lo",),
        body=(HExit(),),
    )

    with pytest.raises(HIRValidationError, match="overlaps with interface"):
        validate_hfragment(frag, MODEL)


def test_local_fixed_hint_to_scratch_reg_is_accepted():
    frag = _fragment(
        bindings=(_binding("dst", "bind_out", mode="out"),),
        scratch=("lo", "hi"),
        body=(
            HAssign(HVar("tmp", UInt(8), reg_hint="hi"), HConst(1, UInt(8))),
            HAssign(HVar("dst", UInt(8)), HVar("tmp", UInt(8), reg_hint="hi")),
            HExit(),
        ),
    )

    result = run_codegen_from_fragment(frag, ToyTarget(), register_model=MODEL)

    assert result.register_assignment["tmp"] == "hi"
    assert "tmp" not in result.asm.format()


def test_local_fixed_hint_outside_scratch_is_rejected():
    frag = _fragment(
        scratch=("lo",),
        body=(
            HAssign(HVar("tmp", UInt(8), reg_hint="bind_in"), HConst(1, UInt(8))),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="scratch_regs"):
        validate_hfragment(frag, MODEL)


def test_local_fixed_hint_width_incompatible_is_rejected():
    frag = _fragment(
        scratch=("lo", "phys_only"),
        body=(
            HAssign(HVar("tmp", UInt(16), reg_hint="lo"), HConst(1, UInt(16))),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="cannot hold 16-bit value"):
        validate_hfragment(frag, MODEL)


def test_no_scratch_available_for_local_raises_compile_error():
    frag = _fragment(
        bindings=(
            _binding("src", "bind_in"),
            _binding("dst", "bind_out", mode="out"),
        ),
        scratch=(),
        body=(
            HAssign(HVar("tmp", UInt(8)), HVar("src", UInt(8))),
            HAssign(HVar("dst", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    with pytest.raises(RegisterAllocationError, match="No register available"):
        run_codegen_from_fragment(frag, ToyTarget(), register_model=MODEL)


def test_fragment_pressure_does_not_spill_even_if_base_model_has_spill_slots():
    frag = _fragment(
        bindings=(_binding("dst", "bind_out", mode="out"),),
        scratch=("lo",),
        body=(
            HAssign(HVar("lhs", UInt(8)), HConst(1, UInt(8))),
            HAssign(HVar("rhs", UInt(8)), HConst(2, UInt(8))),
            HAssign(
                HVar("dst", UInt(8)),
                HBinOp("add", HVar("lhs", UInt(8)), HVar("rhs", UInt(8)), UInt(8)),
            ),
            HExit(),
        ),
    )

    with pytest.raises(RegisterAllocationError, match="No register available"):
        run_codegen_from_fragment(frag, ToyTarget(), register_model=SPILL_MODEL)
