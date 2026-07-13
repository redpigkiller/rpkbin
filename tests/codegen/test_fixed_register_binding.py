from __future__ import annotations

import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.hir import HFunction, HParam, HReturn, HVar, UInt
from rpkbin.codegen.pipeline import run_codegen_from_hir
from rpkbin.codegen.register_alloc import RegisterAllocationError, allocate_registers
from rpkbin.codegen.toy_target import ToyTarget


class _FixedRegisterModel:
    _WIDTHS = {
        "lo": 8,
        "hi": 8,
        "wide": 16,
        "special": 8,
    }

    def allocatable_registers(self):
        return ["lo", "hi"]

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


class _FixedRegisterModelWithSpill(_FixedRegisterModel):
    def spill_slots(self):
        return [lir.SpillSlot(id=0, address=0x20)]


MODEL = _FixedRegisterModel()
SPILL_MODEL = _FixedRegisterModelWithSpill()


def _param_function(specs: list[tuple[str, int]], returned: tuple[str, ...]) -> lir.Function:
    params = tuple(lir.Var(name, width) for name, width in specs)
    values = tuple(lir.Var(name, width) for name, width in specs if name in returned)
    terminator = lir.Return(values[0]) if len(values) == 1 else lir.MultiReturn(values)
    return lir.Function(
        name="f",
        params=params,
        blocks=(lir.Block("entry", (), terminator),),
    )


def test_fixed_hint_assigns_exact_register():
    _, assignment = allocate_registers(
        _param_function([("x", 8)], ("x",)),
        MODEL,
        var_hints={"x": "lo"},
    )

    assert assignment["x"] == "lo"


def test_fixed_hint_can_use_physical_only_register_without_auto_assigning_it():
    _, assignment = allocate_registers(
        _param_function([("fixed", 8), ("plain", 8)], ("fixed", "plain")),
        MODEL,
        var_hints={"fixed": "special"},
    )

    assert assignment["fixed"] == "special"
    assert assignment["plain"] in {"lo", "hi"}
    assert assignment["plain"] != "special"


def test_fixed_hint_width_incompatible_errors():
    with pytest.raises(RegisterAllocationError, match="cannot hold 16-bit value"):
        allocate_registers(
            _param_function([("word", 16)], ("word",)),
            MODEL,
            var_hints={"word": "lo"},
        )


def test_fixed_hint_unknown_register_errors():
    with pytest.raises(RegisterAllocationError, match="not a valid physical register"):
        allocate_registers(
            _param_function([("x", 8)], ("x",)),
            MODEL,
            var_hints={"x": "bogus"},
        )


def test_live_fixed_overlap_errors():
    with pytest.raises(RegisterAllocationError, match="conflicts with"):
        allocate_registers(
            _param_function([("byte", 8), ("word", 16)], ("byte", "word")),
            MODEL,
            var_hints={"byte": "lo", "word": "wide"},
        )


def test_live_non_overlapping_fixed_values_are_accepted():
    _, assignment = allocate_registers(
        _param_function([("a", 8), ("b", 8)], ("a", "b")),
        MODEL,
        var_hints={"a": "lo", "b": "hi"},
    )

    assert assignment["a"] == "lo"
    assert assignment["b"] == "hi"


def test_non_live_fixed_values_can_reuse_same_register():
    _, assignment = allocate_registers(
        _param_function([("a", 8), ("b", 8)], ("b",)),
        MODEL,
        var_hints={"a": "special", "b": "special"},
    )

    assert assignment["a"] == "special"
    assert assignment["b"] == "special"


def test_fixed_value_live_across_clobbering_call_errors_instead_of_spilling():
    func = lir.Function(
        name="caller",
        params=(lir.Var("x", 8),),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), clobbers=("special",)),
                    ),
                ),
                lir.Return(lir.Var("x", 8)),
            ),
        ),
    )

    with pytest.raises(RegisterAllocationError, match="conflicts with 'special'"):
        allocate_registers(func, SPILL_MODEL, var_hints={"x": "special"})


def test_fixed_value_dead_before_clobbering_call_is_accepted():
    func = lir.Function(
        name="caller",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.Assign(lir.VReg("x", 8, hint="special"), lir.Const(1, 8)),
                    lir.Assign(lir.Var("y", 8), lir.Var("x", 8)),
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), clobbers=("special",)),
                    ),
                ),
                lir.Return(lir.Var("y", 8)),
            ),
        ),
    )

    _, assignment = allocate_registers(func, MODEL)

    assert assignment["x"] == "special"
    assert assignment["y"] in {"lo", "hi"}


def test_fixed_value_live_across_call_return_register_overlap_errors():
    func = lir.Function(
        name="caller",
        params=(lir.Var("x", 8),),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), return_regs=("special",), clobbers=()),
                    ),
                ),
                lir.Return(lir.Var("x", 8)),
            ),
        ),
    )

    with pytest.raises(RegisterAllocationError, match="conflicts with 'special'"):
        allocate_registers(func, SPILL_MODEL, var_hints={"x": "special"})


def test_pipeline_preserves_fixed_param_hint_via_allocator_var_hints():
    x = HVar("x", UInt(8))
    result = run_codegen_from_hir(
        HFunction(
            name="fixed_param",
            params=(HParam("x", UInt(8), reg_hint="special"),),
            return_ty=UInt(8),
            body=(HReturn(values=(x,)),),
        ),
        ToyTarget(),
        register_model=MODEL,
    )

    assert result.register_assignment["x"] == "special"
