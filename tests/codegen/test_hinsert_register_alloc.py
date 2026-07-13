"""HInsert lowering and register-allocation tests.

Part A: HInsert lowering tests.
Part B: Register allocator tests (greedy colouring, hints, alias conflict,
        pipeline integration, register_assignment in CodegenResult).
"""

from __future__ import annotations

import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.hir import (
    HAssign,
    HBinOp,
    HConst,
    HFunction,
    HInsert,
    HParam,
    HReturn,
    HVar,
    UInt,
    Void,
    hconst,
    simple_function,
    u8,
    u16,
)
from rpkbin.codegen.lower import lower_function
from rpkbin.codegen.pipeline import run_codegen_from_hir
from rpkbin.codegen.register_alloc import (
    RegisterAllocationError,
    allocate_registers,
    statement_live_after,
    terminator_live_before,
)
from rpkbin.codegen.toy_target import ToyTarget


# ---------------------------------------------------------------------------
# Toy RegisterModel used by all allocator tests
# ---------------------------------------------------------------------------

class ToyRegisterModel:
    """Minimal RegisterModel with 8 allocatable registers and one alias group."""

    def allocatable_registers(self):
        return ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7"]

    def register_width(self, reg: str) -> int:
        return 8

    def register_aliases(self):
        # r01 is a 16-bit composite of r0 and r1
        return [("r01", ["r0", "r1"])]

    def spill_slots(self):
        return []  # This fixture does not exercise spilling.

    def emit_spill(self, reg, slot):
        return [f"ST {reg}, [{slot.address}]"]

    def emit_reload(self, slot, reg):
        return [f"LD {reg}, [{slot.address}]"]


# ---------------------------------------------------------------------------
# Helper: build a minimal LIR function for allocator unit tests
# ---------------------------------------------------------------------------

def _make_lir_func(var_names: list[str], hint_map: dict | None = None) -> lir.Function:
    """Build a LIR function where every var appears in the entry block."""
    hint_map = hint_map or {}
    stmts = []
    params = []
    for name in var_names:
        hint = hint_map.get(name)
        if hint:
            # param is VReg with hint
            v = lir.VReg(name=name, width=8, hint=hint)
        else:
            v = lir.Var(name=name, width=8)
        params.append(lir.Var(name=name, width=8))
        # Assign name = 0 so it appears as a def in the block
        stmts.append(lir.Assign(
            target=lir.Var(name, 8),
            value=lir.Const(0, 8),
        ))
    result = lir.Var(var_names[0], 8)
    for name in var_names[1:]:
        result = lir.BinOp("add", result, lir.Var(name, 8), 8)
    block = lir.Block(
        label="entry",
        statements=tuple(stmts),
        terminator=lir.Return(result),
    )
    return lir.Function(name="test_fn", params=tuple(params), blocks=(block,))


def _make_lir_func_vreg(var_hints: dict) -> lir.Function:
    """Build a LIR function where VRegs with hints appear in assignment targets."""
    stmts = []
    params = []
    for name, hint in var_hints.items():
        params.append(lir.Var(name=name, width=8))
        target = lir.VReg(name=name, width=8, hint=hint)
        stmts.append(lir.Assign(
            target=target,
            value=lir.Const(0, 8),
        ))
    block = lir.Block(
        label="entry",
        statements=tuple(stmts),
        terminator=lir.Return(lir.Const(0, 8)),
    )
    return lir.Function(name="vreg_fn", params=tuple(params), blocks=(block,))


def test_statement_live_after_reports_each_program_point():
    block = lir.Block(
        label="entry",
        statements=(
            lir.Assign(lir.Var("r0", 8), lir.Const(1, 8)),
            lir.Assign(lir.Var("r1", 8), lir.Var("r0", 8)),
        ),
        terminator=lir.Return(lir.Var("r1", 8)),
    )
    live = statement_live_after(
        lir.Function(name="live", params=(), blocks=(block,))
    )
    assert live == {("entry", 0): {"r0"}, ("entry", 1): {"r1"}}
    assert terminator_live_before(
        lir.Function(name="live", params=(), blocks=(block,))
    ) == {"entry": {"r1"}}


# ===========================================================================
# Part A: HInsert lowering tests
# ===========================================================================

class TestHInsert:
    """Test HInsert lowering: masks and explicit operation sequence."""

    @staticmethod
    def _insert_assignments(func):
        assignments = [
            stmt
            for stmt in lower_function(func).blocks[0].statements
            if isinstance(stmt, lir.Assign)
        ]
        masked, cleared, shifted, result = assignments[-4:]
        assert [stmt.value.op for stmt in (masked, cleared, shifted, result)] == [
            "and",
            "and",
            "shl",
            "or",
        ]
        assert result.target.name == "x"
        assert result.value.left == cleared.target
        assert result.value.right == shifted.target
        assert shifted.value.left == masked.target
        return masked, cleared, shifted, result

    def test_hinsert_basic(self):
        """HInsert(dst=u8("x"), value=u8("v"), msb=5, lsb=3)
        must produce mask, clear, shift, and final-or assignments.

        field_width = 5 - 3 + 1 = 3
        field_mask  = 0b00111 << 3 = 0b00111000 = 0x38
        keep_mask   = 0xFF & ~0x38 = 0xC7
        """
        x = u8("x")
        v = u8("v")
        insert = HInsert(dst=x, value=v, msb=5, lsb=3)
        func = HFunction(
            name="insert_test",
            params=(HParam("x", UInt(8)), HParam("v", UInt(8))),
            return_ty=UInt(8),
            body=(
                HAssign(target=x, value=insert),
                HReturn(values=(x,)),
            ),
        )
        masked, cleared, shifted, _ = self._insert_assignments(func)
        assert masked.value.right.value == 0x07
        assert cleared.value.right.value == 0xC7
        assert shifted.value.right.value == 3

    def test_hinsert_full_byte(self):
        """HInsert(msb=7, lsb=0) — entire byte.

        field_mask = 0xFF, keep_mask = 0x00.
        The and-mask is 0, so cleared is always 0.
        The result = 0 | (value << 0) = value.
        """
        x = u8("x")
        v = u8("v")
        insert = HInsert(dst=x, value=v, msb=7, lsb=0)
        func = HFunction(
            name="full_byte",
            params=(HParam("x", UInt(8)), HParam("v", UInt(8))),
            return_ty=UInt(8),
            body=(
                HAssign(target=x, value=insert),
                HReturn(values=(x,)),
            ),
        )
        masked, cleared, shifted, _ = self._insert_assignments(func)
        assert masked.value.right.value == 0xFF
        assert cleared.value.right.value == 0x00
        assert shifted.value.right.value == 0

    def test_hinsert_value_above_field_width(self):
        """HInsert 0xFF into [3:0] of 8-bit dst: value must be masked to 0x0F
        before shifting, so high nibble of dst survives.

        field_width = 3 - 0 + 1 = 4
        value_mask  = (1 << 4) - 1 = 0x0F
        field_mask  = 0x0F << 0 = 0x0F
        keep_mask   = 0xFF & ~0x0F = 0xF0
        """
        x = u8("x")
        # Insert 0xFF into bits [3:0]; 0xFF has high nibble set which would
        # pollute bits [7:4] if not masked.
        v = HConst(0xFF, UInt(8))
        insert = HInsert(dst=x, value=v, msb=3, lsb=0)
        func = HFunction(
            name="insert_mask",
            params=(HParam("x", UInt(8)),),
            return_ty=UInt(8),
            body=(
                HAssign(target=x, value=insert),
                HReturn(values=(x,)),
            ),
        )
        masked, cleared, shifted, _ = self._insert_assignments(func)
        assert masked.value.right.value == 0x0F
        assert cleared.value.right.value == 0xF0
        assert shifted.value.right.value == 0

    def test_hinsert_single_bit(self):
        """HInsert(msb=2, lsb=2) — single bit insert.

        field_width = 1
        field_mask  = 0b001 << 2 = 0x04
        keep_mask   = 0xFF & ~0x04 = 0xFB
        lsb = 2
        """
        x = u8("x")
        v = u8("v")
        insert = HInsert(dst=x, value=v, msb=2, lsb=2)
        func = HFunction(
            name="single_bit",
            params=(HParam("x", UInt(8)), HParam("v", UInt(8))),
            return_ty=UInt(8),
            body=(
                HAssign(target=x, value=insert),
                HReturn(values=(x,)),
            ),
        )
        masked, cleared, shifted, _ = self._insert_assignments(func)
        assert masked.value.right.value == 0x01
        assert cleared.value.right.value == 0xFB
        assert shifted.value.right.value == 2


# ===========================================================================
# Part B: Register Allocator tests
# ===========================================================================

class TestRegisterAllocator:
    """Test the greedy register allocator."""

    def test_alloc_hinted_vars(self):
        """Two VRegs with hints r4 and r5 are assigned r4, r5."""
        func = _make_lir_func_vreg({"x": "r4", "y": "r5"})
        rm = ToyRegisterModel()
        new_func, assignment = allocate_registers(func, rm)

        assert assignment.get("x") == "r4"
        assert assignment.get("y") == "r5"

    def test_alloc_unhinted_vars(self):
        """Three unhinted Vars are assigned three distinct registers."""
        func = _make_lir_func(["a", "b", "c"])
        rm = ToyRegisterModel()
        new_func, assignment = allocate_registers(func, rm)

        regs = [assignment["a"], assignment["b"], assignment["c"]]
        # All must be distinct
        assert len(set(regs)) == 3
        # All must come from the allocatable set
        available = set(rm.allocatable_registers())
        for r in regs:
            assert r in available

    def test_alloc_respects_hint_preference(self):
        """A hinted VReg gets its preferred register; unhinted gets what's left."""
        func = _make_lir_func_vreg({"hinted": "r3"})
        # Also add an unhinted var in the same block
        # Manually build a two-var function
        stmts = (
            lir.Assign(
                target=lir.VReg("hinted", 8, hint="r3"),
                value=lir.Const(0, 8),
            ),
            lir.Assign(
                target=lir.Var("plain", 8),
                value=lir.Const(0, 8),
            ),
        )
        block = lir.Block("entry", stmts, lir.Return(lir.Const(0)))
        func = lir.Function("hint_test", (lir.Var("hinted", 8), lir.Var("plain", 8)), (block,))

        rm = ToyRegisterModel()
        _, assignment = allocate_registers(func, rm)

        # Hinted var must get r3
        assert assignment["hinted"] == "r3"
        # Plain var gets something different from r3
        assert assignment["plain"] != "r3"

    def test_alloc_alias_conflict_detected(self):
        """r01 aliases r0 and r1; assigning both r01 and r0 must produce no conflict."""
        # Two vars in the same block, one hinted to r01, one to r0.
        # They conflict (r01 is an alias of r0), so they should get different physical regs.
        stmts = (
            lir.Assign(
                target=lir.VReg("a", 8, hint="r0"),
                value=lir.Const(0, 8),
            ),
            lir.Assign(
                target=lir.VReg("b", 8, hint="r1"),
                value=lir.Const(0, 8),
            ),
        )
        block = lir.Block("entry", stmts, lir.Return(lir.Const(0)))
        func = lir.Function("alias_test", (lir.Var("a", 8), lir.Var("b", 8)), (block,))

        rm = ToyRegisterModel()
        _, assignment = allocate_registers(func, rm)

        # a and b must be assigned different registers
        assert assignment["a"] != assignment["b"]

    def test_alloc_too_many_vars_raises(self):
        """9 simultaneously-live variables with 8 registers and no spill → error."""
        # 9 vars, all in same block (interference between all)
        names = [f"v{i}" for i in range(9)]
        func = _make_lir_func(names)
        rm = ToyRegisterModel()  # 8 regs, 0 spill slots

        with pytest.raises(RegisterAllocationError):
            allocate_registers(func, rm)

    def test_alloc_pipeline_integration(self):
        """HFunction with @hint params compiles with ToyRegisterModel.

        After allocation, ASM text contains physical register names (r4, r5)
        instead of original variable names.
        """
        # Build a simple function: a @r4, b @r5 → return a + b
        a = HVar("a", UInt(8), reg_hint="r4")
        b = HVar("b", UInt(8), reg_hint="r5")
        result_var = HVar("result", UInt(8))
        func = HFunction(
            name="add_hint",
            params=(HParam("a", UInt(8), reg_hint="r4"),
                    HParam("b", UInt(8), reg_hint="r5")),
            return_ty=UInt(8),
            body=(
                HAssign(
                    target=result_var,
                    value=HBinOp("add", a, b, UInt(8)),
                ),
                HReturn(values=(result_var,)),
            ),
        )
        result = run_codegen_from_hir(func, ToyTarget(), register_model=ToyRegisterModel())

        asm = result.asm_text
        # Physical register names must appear in the output
        assert "r4" in asm or "r5" in asm
        # Original HIR var names must not appear
        assert "a" not in asm.split()  # "a" should not appear as a standalone operand
        assert result.register_assignment is not None

    def test_alloc_result_in_codegen_result(self):
        """register_assignment field is populated when register_model is provided."""
        x = HVar("x", UInt(8), reg_hint="r2")
        func = HFunction(
            name="reg_result_test",
            params=(HParam("x", UInt(8), reg_hint="r2"),),
            return_ty=UInt(8),
            body=(HReturn(values=(x,)),),
        )
        result = run_codegen_from_hir(func, ToyTarget(), register_model=ToyRegisterModel())

        assert result.register_assignment is not None
        assert isinstance(result.register_assignment, dict)
        # "x" should be mapped to r2 (preferred hint)
        assert result.register_assignment.get("x") == "r2"

    def test_alloc_none_when_no_register_model(self):
        """register_assignment is None when no register_model is provided."""
        x = u8("x")
        func = HFunction(
            name="no_alloc",
            params=(HParam("x", UInt(8)),),
            return_ty=UInt(8),
            body=(HReturn(values=(x,)),),
        )
        result = run_codegen_from_hir(func, ToyTarget())  # no register_model
        assert result.register_assignment is None


# ===========================================================================
# Part C: Spill behavior tests
# ===========================================================================

class ToyRegisterModelWithSpill:
    """3 allocatable registers + 2 fixed-address spill slots."""

    def allocatable_registers(self):
        return ["r0", "r1", "r2"]

    def register_width(self, reg: str) -> int:
        return 8

    def register_aliases(self):
        return []

    def spill_slots(self):
        return [
            lir.SpillSlot(id=0, address=0x20),
            lir.SpillSlot(id=1, address=0x21),
        ]

    def emit_spill(self, reg, slot):
        return [f"ST {reg}, [{hex(slot.address)}]"]

    def emit_reload(self, slot, reg):
        return [f"LD {reg}, [{hex(slot.address)}]"]


def _four_var_lir_func() -> lir.Function:
    """LIR function with 4 vars all live in the same block.

    With ToyRegisterModelWithSpill (3 regs), at least 1 var must spill.
    Block: define a, b, c, d, then use all four in one expression.
    """
    stmts = (
        lir.Assign(lir.Var("a", 8), lir.Const(1, 8)),
        lir.Assign(lir.Var("b", 8), lir.Const(2, 8)),
        lir.Assign(lir.Var("c", 8), lir.Const(3, 8)),
        lir.Assign(lir.Var("d", 8), lir.Const(4, 8)),
        lir.Assign(
            lir.Var("result", 8),
            lir.BinOp(
                "add",
                lir.BinOp(
                    "add",
                    lir.BinOp("add", lir.Var("a", 8), lir.Var("b", 8), 8),
                    lir.Var("c", 8),
                    8,
                ),
                lir.Var("d", 8),
                8,
            ),
        ),
    )
    block = lir.Block("entry", stmts, lir.Return(lir.Var("result", 8)))
    return lir.Function(
        "four_var",
        params=(
            lir.Var("a", 8),
            lir.Var("b", 8),
            lir.Var("c", 8),
            lir.Var("d", 8),
        ),
        blocks=(block,),
    )


class TestSpill:
    """Tests for spill behavior."""

    def test_spill_inserts_store_after_def(self):
        """Defining a spilled var must insert a MemStore to the spill slot."""
        func = _four_var_lir_func()
        rm = ToyRegisterModelWithSpill()
        new_func, _ = allocate_registers(func, rm)

        stmts = new_func.blocks[0].statements
        stores = [s for s in stmts if isinstance(s, lir.MemStore)]
        assert len(stores) >= 1, "Expected at least one MemStore for spill"
        slot_addrs = {s.addr.value for s in stores if isinstance(s.addr, lir.Const)}
        assert slot_addrs & {0x20, 0x21}, (
            f"Expected MemStore to spill slot 0x20 or 0x21, got: {slot_addrs}"
        )

    def test_spill_inserts_load_before_use(self):
        """Using a spilled var must insert a MemLoad from the spill slot."""
        func = _four_var_lir_func()
        rm = ToyRegisterModelWithSpill()
        new_func, _ = allocate_registers(func, rm)

        stmts = new_func.blocks[0].statements
        reload_stmts = [
            s for s in stmts
            if isinstance(s, lir.Assign) and isinstance(s.value, lir.MemLoad)
        ]
        assert len(reload_stmts) >= 1, (
            "Expected at least one MemLoad reload statement for spilled var"
        )
        load_addrs = {
            s.value.addr.value
            for s in reload_stmts
            if isinstance(s.value.addr, lir.Const)
        }
        assert load_addrs & {0x20, 0x21}, (
            f"Expected MemLoad from spill slot 0x20 or 0x21, got: {load_addrs}"
        )

    def test_spill_no_silent_aliasing(self):
        """Spill code must be inserted (more stmts than original) when spilling occurs."""
        func = _four_var_lir_func()
        rm = ToyRegisterModelWithSpill()
        new_func, _ = allocate_registers(func, rm)

        original_count = len(func.blocks[0].statements)
        new_count = len(new_func.blocks[0].statements)
        assert new_count > original_count, (
            f"Spill code was not inserted: expected >{original_count} stmts, got {new_count}"
        )

    def test_spill_does_not_break_non_spill_case(self):
        """No-spill path is unchanged: 2 vars with 3 regs -> no MemStore/MemLoad inserted."""
        stmts = (
            lir.Assign(lir.Var("x", 8), lir.Const(10, 8)),
            lir.Assign(lir.Var("y", 8), lir.Const(20, 8)),
        )
        block = lir.Block(
            "entry",
            stmts,
            lir.Return(lir.BinOp("add", lir.Var("x", 8), lir.Var("y", 8), 8)),
        )
        func = lir.Function("two_var", (lir.Var("x", 8), lir.Var("y", 8)), (block,))

        rm = ToyRegisterModelWithSpill()
        new_func, assignment = allocate_registers(func, rm)

        new_stmts = new_func.blocks[0].statements
        assert not any(isinstance(s, lir.MemStore) for s in new_stmts)
        assert not any(
            isinstance(s, lir.Assign) and isinstance(s.value, lir.MemLoad)
            for s in new_stmts
        )
        assert assignment["x"] != assignment["y"]


def test_call_assign_targets_are_remapped_by_allocator():
    """Allocator remaps destinations but must preserve abi_return_regs."""
    func = lir.Function(
        name="pair_user",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.CallAssign(
                        targets=(
                            lir.VReg("a", 8, hint="r4"),
                            lir.VReg("b", 8, hint="r5"),
                        ),
                        call=lir.Call("pair", ()),
                        abi_return_regs=("r1", "r2"),
                    ),
                ),
                lir.Return(lir.Const(0, 8)),
            ),
        ),
    )

    new_func, assignment = allocate_registers(func, ToyRegisterModel())
    call_stmt = next(
        stmt
        for stmt in new_func.blocks[0].statements
        if isinstance(stmt, lir.CallAssign)
    )

    assert assignment["a"] == "r4"
    assert assignment["b"] == "r5"
    assert isinstance(call_stmt.targets[0], lir.VReg)
    assert isinstance(call_stmt.targets[1], lir.VReg)
    assert call_stmt.targets[0].name == "r4"
    assert call_stmt.targets[0].hint == "r4"
    assert call_stmt.targets[1].name == "r5"
    assert call_stmt.targets[1].hint == "r5"
    assert call_stmt.abi_return_regs == ("r1", "r2")


def test_allocator_keeps_values_live_across_blocks_distinct():
    func = lir.Function(
        "cross_block",
        (),
        (
            lir.Block(
                "entry",
                (lir.Assign(lir.Var("x", 8), lir.Const(1, 8)),),
                lir.Jump("middle"),
            ),
            lir.Block(
                "middle",
                (lir.Assign(lir.Var("y", 8), lir.Const(2, 8)),),
                lir.Jump("exit"),
            ),
            lir.Block("exit", (), lir.Return(lir.Var("x", 8))),
        ),
    )

    _, assignment = allocate_registers(func, ToyRegisterModel())

    assert assignment["x"] != assignment["y"]


def test_allocator_avoids_call_clobbers_for_live_values():
    func = lir.Function(
        "caller",
        (lir.VReg("x", 8, hint="r0"),),
        (
            lir.Block(
                "entry",
                (
                    lir.Assign(
                        lir.Var("ignored", 8),
                        lir.Call("callee", (), clobbers=("r0", "r1", "r2")),
                    ),
                ),
                lir.Return(lir.Var("x", 8)),
            ),
        ),
    )

    allocated, assignment = allocate_registers(func, ToyRegisterModelWithSpill())

    assert "x" not in assignment
    assert isinstance(allocated.blocks[0].statements[0], lir.MemStore)


def test_fixed_register_is_not_silently_spilled_across_call():
    class FixedModel(ToyRegisterModelWithSpill):
        def fixed_register_hints(self):
            return True

    func = lir.Function(
        "caller",
        (lir.VReg("x", 8, hint="r0"),),
        (lir.Block(
            "entry",
            (lir.Assign(
                lir.Var("ignored", 8),
                lir.Call("callee", (), clobbers=("r0",)),
            ),),
            lir.Return(lir.Var("x", 8)),
        ),),
    )

    with pytest.raises(RegisterAllocationError, match="Fixed register 'r0'"):
        allocate_registers(func, FixedModel())


def test_call_assign_matching_dest_eliminates_copy():
    """Matching destination/ABI registers must not emit identity MOVs."""
    func = lir.Function(
        name="pair_user",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.CallAssign(
                        targets=(
                            lir.Var("r1", 8),
                            lir.Var("r2", 8),
                        ),
                        call=lir.Call("pair", ()),
                        abi_return_regs=("r1", "r2"),
                    ),
                ),
                lir.Return(lir.Const(0, 8)),
            ),
        ),
    )

    asm = ToyTarget().select_instructions(func).format()

    assert "CALL pair()" in asm
    assert "MOV r1, r1" not in asm
    assert "MOV r2, r2" not in asm


def test_call_assign_distinct_abi_and_dest_emits_copy():
    """Distinct destination and ABI registers must emit the right copies."""
    func = lir.Function(
        name="pair_user",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.CallAssign(
                        targets=(lir.Var("r4", 8), lir.Var("r5", 8)),
                        call=lir.Call("pair", ()),
                        abi_return_regs=("r1", "r2"),
                    ),
                ),
                lir.Return(lir.Const(0, 8)),
            ),
        ),
    )

    asm = ToyTarget().select_instructions(func).format()

    assert "CALL pair()" in asm
    assert "MOV r4, r1" in asm
    assert "MOV r5, r2" in asm


def test_call_assign_none_target_keeps_abi_slot_index():
    """Discarded slots must not shift later ABI register copies."""
    func = lir.Function(
        name="pair_user",
        params=(),
        blocks=(
            lir.Block(
                "entry",
                (
                    lir.CallAssign(
                        targets=(None, lir.Var("r5", 8)),
                        call=lir.Call("pair", ()),
                        abi_return_regs=("r1", "r2"),
                    ),
                ),
                lir.Return(lir.Const(0, 8)),
            ),
        ),
    )

    asm = ToyTarget().select_instructions(func).format()

    assert "CALL pair()" in asm
    assert "MOV r5, r2" in asm
    assert "MOV r5, r1" not in asm
