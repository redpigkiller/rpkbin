import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.register_alloc import (
    RegisterAllocationError,
    allocate_registers,
)


class _PairPreservationModel:
    _REGS = ("p0", "p1", "q0", "q1")

    def allocatable_registers(self):
        return self._REGS

    def is_physical_register(self, reg):
        return reg in self._REGS

    def fixed_register_hints(self):
        return True

    def register_width(self, reg):
        return 8

    def can_allocate(self, reg, width):
        return reg in self._REGS and width == 8

    def register_aliases(self):
        return ()

    def registers_overlap(self, lhs, rhs):
        return lhs == rhs

    def call_preservation_unit(self, reg):
        return {"p0": "pair_p", "p1": "pair_p", "q0": "pair_q", "q1": "pair_q"}.get(reg)

    def call_preservation_restore_clobbers(self, unit):
        return {
            "pair_p": ("p0", "p1"),
            "pair_q": ("q0", "q1"),
        }[unit]


class _NoPreservationModel:
    def allocatable_registers(self):
        return ("p0", "p1", "q0", "q1")

    def is_physical_register(self, reg):
        return reg in self.allocatable_registers()

    def fixed_register_hints(self):
        return True

    def register_width(self, reg):
        return 8

    def can_allocate(self, reg, width):
        return reg in self.allocatable_registers() and width == 8

    def register_aliases(self):
        return ()

    def registers_overlap(self, lhs, rhs):
        return lhs == rhs


def _call_pressure_function(*, fixed_left=False, clobbers=None):
    left = lir.VReg("left", 8, "q0" if fixed_left else None)
    right = lir.VReg("right", 8)
    result = lir.VReg("result", 8, "p0")
    return lir.Function(
        "f",
        (),
        (
            lir.Block(
                "entry",
                (
                    lir.Assign(left, lir.Const(1)),
                    lir.Assign(right, lir.Const(2)),
                    lir.CallAssign(
                        (result,),
                        lir.Call(
                            "callee",
                            (),
                            return_regs=("p0",),
                            clobbers=clobbers,
                        ),
                    ),
                ),
                lir.Return(
                    lir.BinOp(
                        "add",
                        lir.BinOp("add", left, right),
                        result,
                    )
                ),
            ),
        ),
    )


def _allocated_call(func, model):
    allocated, assignment = allocate_registers(func, model)
    stmt = allocated.blocks[0].statements[2]
    assert isinstance(stmt, lir.CallAssign)
    return stmt.call, assignment


def test_call_preservation_groups_values_away_from_return_restore_clobbers():
    call, assignment = _allocated_call(
        _call_pressure_function(),
        _PairPreservationModel(),
    )

    assert {assignment["left"], assignment["right"]} == {"q0", "q1"}
    assert assignment["result"] == "p0"
    assert call.preservation_units == ("pair_q",)


def test_single_return_call_expression_carries_the_plan():
    func = _call_pressure_function()
    call_assign = func.blocks[0].statements[2]
    call_stmt = lir.Assign(call_assign.targets[0], call_assign.call)
    single_return_func = lir.Function(
        func.name,
        func.params,
        (
            lir.Block(
                "entry",
                (*func.blocks[0].statements[:2], call_stmt),
                func.blocks[0].terminator,
            ),
        ),
    )

    allocated, _ = allocate_registers(
        single_return_func,
        _PairPreservationModel(),
    )
    allocated_stmt = allocated.blocks[0].statements[2]

    assert isinstance(allocated_stmt, lir.Assign)
    assert allocated_stmt.value.preservation_units == ("pair_q",)


def test_sequential_void_calls_each_carry_their_plan():
    value = lir.VReg("value", 8)
    call = lir.Call("callee", (), clobbers=None)
    func = lir.Function(
        "f",
        (),
        (
            lir.Block(
                "entry",
                (
                    lir.Assign(value, lir.Const(1)),
                    lir.CallStmt(call),
                    lir.CallStmt(call),
                ),
                lir.Return(value),
            ),
        ),
    )

    allocated, _ = allocate_registers(func, _PairPreservationModel())
    calls = [
        stmt.call
        for stmt in allocated.blocks[0].statements
        if isinstance(stmt, lir.CallStmt)
    ]

    assert [call.preservation_units for call in calls] == [
        ("pair_p",),
        ("pair_p",),
    ]


def test_call_preservation_does_not_preserve_fixed_values():
    with pytest.raises(RegisterAllocationError, match="Fixed register"):
        allocate_registers(
            _call_pressure_function(fixed_left=True),
            _PairPreservationModel(),
        )


def test_model_without_call_preservation_keeps_fail_closed_behavior():
    with pytest.raises(RegisterAllocationError, match="No register available"):
        allocate_registers(_call_pressure_function(), _NoPreservationModel())


def test_zero_preservation_allocation_remains_preferred():
    call, _ = _allocated_call(
        _call_pressure_function(clobbers=()),
        _PairPreservationModel(),
    )

    assert call.preservation_units == ()


def test_lir_rejects_invalid_call_preservation_units():
    func = _call_pressure_function(clobbers=())
    stmt = func.blocks[0].statements[2]
    bad_call = lir.Call(
        stmt.call.name,
        stmt.call.args,
        return_regs=stmt.call.return_regs,
        clobbers=stmt.call.clobbers,
        preservation_units=("pair_q", "pair_q"),
    )
    bad_stmt = lir.CallAssign(stmt.targets, bad_call)
    bad_func = lir.Function(
        func.name,
        func.params,
        (
            lir.Block(
                "entry",
                (*func.blocks[0].statements[:2], bad_stmt),
                func.blocks[0].terminator,
            ),
        ),
    )

    with pytest.raises(ValueError, match="invalid preservation units"):
        lir.validate_function(bad_func)
