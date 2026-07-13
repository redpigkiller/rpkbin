from __future__ import annotations

import pytest

from rpkbin.codegen.hir import (
    HAssign,
    HBitSet,
    HCmp,
    HExit,
    HFragment,
    HFragmentBinding,
    HIf,
    HVar,
    UInt,
    hconst,
)
from rpkbin.codegen.hir_validate import HIRValidationError, validate_hfragment
from rpkbin.codegen.lower import lower_fragment
from rpkbin.codegen.pipeline import run_codegen_from_fragment
from rpkbin.codegen.register_alloc import (
    _fragment_unresolved_local_names,
    allocate_fragment_registers,
)
from rpkbin.codegen.toy_target import ToyTarget


class _PhaseRegModel:
    _WIDTHS = {
        "p": 8,
        "p_alias": 8,
        "q": 8,
        "s0": 8,
        "s1": 8,
    }

    def allocatable_registers(self):
        return ["s0", "s1"]

    def is_physical_register(self, reg: str) -> bool:
        return reg in self._WIDTHS

    def fixed_register_hints(self) -> bool:
        return True

    def register_width(self, reg: str) -> int:
        return self._WIDTHS[reg]

    def can_allocate(self, reg: str, width: int) -> bool:
        return self._WIDTHS.get(reg, 0) >= width

    def register_aliases(self):
        return [("pair_group", ["p", "p_alias"])]

    def registers_overlap(self, lhs: str, rhs: str) -> bool:
        return lhs == rhs or frozenset((lhs, rhs)) == frozenset(("p", "p_alias"))

    def spill_slots(self):
        return []


MODEL = _PhaseRegModel()


def _binding(name: str, reg: str, mode: str = "in") -> HFragmentBinding:
    return HFragmentBinding(name=name, ty=UInt(8), reg=reg, mode=mode)


def _fragment(bindings=(), scratch=(), body=()) -> HFragment:
    return HFragment(name="phase", bindings=bindings, scratch_regs=scratch, body=body)


def _cond():
    return HCmp("eq", hconst(1), hconst(1))


def test_phased_reuse_straight_line_is_accepted():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        body=(
            HAssign(HVar("y", UInt(8)), HVar("x", UInt(8))),
            HExit(),
        ),
    )

    validate_hfragment(frag, MODEL)


def test_phased_reuse_lowering_and_allocation_keep_same_physical_reg():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HAssign(HVar("y", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    validate_hfragment(frag, MODEL)
    lowered = lower_fragment(frag)
    allocated, assignment = allocate_fragment_registers(lowered, MODEL)

    assert [binding.reg for binding in lowered.bindings] == ["p", "p"]
    assert assignment["x"] == "p"
    assert assignment["y"] == "p"
    assert assignment["tmp"] == "s0"
    assert assignment["tmp"] != "p"
    assert _fragment_unresolved_local_names(allocated) == set()


def test_phased_reuse_pipeline_has_no_symbolic_fallback():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HAssign(HVar("y", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    result = run_codegen_from_fragment(frag, ToyTarget(), register_model=MODEL)

    assert result.register_assignment["x"] == "p"
    assert result.register_assignment["y"] == "p"
    assert result.register_assignment["tmp"] == "s0"
    asm = result.asm.format()
    assert "MOV s0, p" in asm
    assert "MOV p, s0" in asm
    assert "tmp" not in asm


@pytest.mark.parametrize(
    ("bindings", "match"),
    [
        (
            (
                _binding("x", "p", mode="in"),
                _binding("y", "p_alias", mode="out"),
            ),
            "overlaps",
        ),
        (
            (
                _binding("x", "p", mode="in"),
                _binding("z", "p", mode="in"),
            ),
            "exactly one 'in' binding and one 'out' binding",
        ),
        (
            (
                _binding("y", "p", mode="out"),
                _binding("z", "p", mode="out"),
            ),
            "exactly one 'in' binding and one 'out' binding",
        ),
        (
            (
                _binding("x", "p", mode="in"),
                _binding("io", "p", mode="inout"),
            ),
            "exactly one 'in' binding and one 'out' binding",
        ),
        (
            (
                _binding("x", "p", mode="in"),
                _binding("y", "p", mode="out"),
                _binding("z", "p", mode="in"),
            ),
            "exactly one 'in' binding and one 'out' binding",
        ),
        (
            (
                _binding("x", "p", mode="in"),
                _binding("y", "p", mode="out"),
                _binding("z", "p_alias", mode="in"),
            ),
            "overlaps",
        ),
    ],
)
def test_illegal_binding_shares_are_rejected(bindings, match):
    frag = _fragment(bindings=bindings, body=(HExit(),))

    with pytest.raises(HIRValidationError, match=match):
        validate_hfragment(frag, MODEL)


def test_scratch_sharing_phased_storage_is_rejected():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("p",),
        body=(HAssign(HVar("y", UInt(8)), HVar("x", UInt(8))), HExit()),
    )

    with pytest.raises(HIRValidationError, match="overlaps with interface"):
        validate_hfragment(frag, MODEL)


def test_scratch_overlapping_phased_storage_is_rejected():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("p_alias",),
        body=(HAssign(HVar("y", UInt(8)), HVar("x", UInt(8))), HExit()),
    )

    with pytest.raises(HIRValidationError, match="overlaps with interface"):
        validate_hfragment(frag, MODEL)


def test_local_fixed_hint_using_phased_storage_is_rejected():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HAssign(HVar("tmp", UInt(8), reg_hint="p"), hconst(1)),
            HAssign(HVar("y", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="scratch_regs"):
        validate_hfragment(frag, MODEL)


def test_read_after_consumption_is_rejected():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HAssign(HVar("y", UInt(8)), HVar("x", UInt(8))),
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="consumed input binding 'x'"):
        validate_hfragment(frag, MODEL)


def test_read_before_consumption_is_accepted():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HAssign(HVar("y", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    validate_hfragment(frag, MODEL)


def test_output_overwrite_consumes_input_even_without_reading_it():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        body=(
            HAssign(HVar("y", UInt(8)), hconst(1)),
            HExit(),
        ),
    )

    validate_hfragment(frag, MODEL)


def test_bitset_on_phased_output_before_ordinary_assignment_is_accepted():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        body=(
            HBitSet(HVar("y", UInt(8)), bit_idx=0, value=1),
            HExit(),
        ),
    )

    validate_hfragment(frag, MODEL)


def test_bitset_on_phased_output_consumes_paired_input():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HBitSet(HVar("y", UInt(8)), bit_idx=0, value=1),
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="consumed input binding 'x'"):
        validate_hfragment(frag, MODEL)


def test_bitset_on_non_phased_output_before_assignment_is_still_rejected():
    frag = _fragment(
        bindings=(_binding("y", "q", mode="out"),),
        body=(
            HBitSet(HVar("y", UInt(8)), bit_idx=0, value=1),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="read of unassigned out binding 'y'"):
        validate_hfragment(frag, MODEL)


def test_exit_without_output_assignment_is_rejected_for_phased_pair():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        body=(HExit(),),
    )

    with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
        validate_hfragment(frag, MODEL)


def test_if_both_branches_consume_then_reading_input_after_merge_is_rejected():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HIf(
                cond=_cond(),
                then_body=(HAssign(HVar("y", UInt(8)), hconst(1)),),
                else_body=(HAssign(HVar("y", UInt(8)), hconst(2)),),
            ),
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="consumed input binding 'x'"):
        validate_hfragment(frag, MODEL)


def test_if_one_fallthrough_branch_consumes_then_merge_is_conservatively_consumed():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HIf(
                cond=_cond(),
                then_body=(HAssign(HVar("y", UInt(8)), hconst(1)),),
                else_body=(),
            ),
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HAssign(HVar("y", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    with pytest.raises(HIRValidationError, match="consumed input binding 'x'"):
        validate_hfragment(frag, MODEL)


def test_if_exiting_consuming_branch_does_not_poison_fallthrough():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        scratch=("s0",),
        body=(
            HIf(
                cond=_cond(),
                then_body=(
                    HAssign(HVar("y", UInt(8)), hconst(1)),
                    HExit(),
                ),
                else_body=(),
            ),
            HAssign(HVar("tmp", UInt(8)), HVar("x", UInt(8))),
            HAssign(HVar("y", UInt(8)), HVar("tmp", UInt(8))),
            HExit(),
        ),
    )

    validate_hfragment(frag, MODEL)


def test_exit_branch_still_requires_output_assignment():
    frag = _fragment(
        bindings=(
            _binding("x", "p", mode="in"),
            _binding("y", "p", mode="out"),
        ),
        body=(
            HIf(
                cond=_cond(),
                then_body=(
                    HAssign(HVar("y", UInt(8)), hconst(1)),
                    HExit(),
                ),
                else_body=(HExit(),),
            ),
        ),
    )

    with pytest.raises(HIRValidationError, match="not definitely assigned before HExit"):
        validate_hfragment(frag, MODEL)
