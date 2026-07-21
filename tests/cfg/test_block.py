"""Tests for rpkbin.cfg.block — Insn types and BasicBlock."""

import pytest
from rpkbin.cfg import Assignment, CallRef, OtherInsn, BasicBlock


class TestAssignment:
    def test_basic_fields(self):
        a = Assignment(lhs="x", rhs=["a", "b"], raw="x = a + b")
        assert a.lhs == "x"
        assert a.rhs == ["a", "b"]
        assert a.raw == "x = a + b"

    def test_defaults(self):
        a = Assignment(lhs="y")
        assert a.rhs == []
        assert a.raw == ""

    def test_repr(self):
        a = Assignment(lhs="x", rhs=["a"])
        assert "x" in repr(a)

    def test_no_constants_in_rhs(self):
        # Constants must NOT be in rhs — only variable names
        a = Assignment(lhs="flag", rhs=[], raw="flag = 1")
        assert a.rhs == []


class TestCallRef:
    def test_basic_fields(self):
        c = CallRef(callee="SUB_CHECK", raw="SUB_CHECK();")
        assert c.callee == "SUB_CHECK"
        assert c.raw == "SUB_CHECK();"

    def test_default_raw(self):
        c = CallRef(callee="foo")
        assert c.raw == ""

    def test_repr(self):
        c = CallRef(callee="bar")
        assert "bar" in repr(c)


class TestOtherInsn:
    def test_defaults(self):
        o = OtherInsn()
        assert o.raw == ""
        assert o.defs == set()
        assert o.uses == set()

    def test_explicit_defs_uses(self):
        o = OtherInsn(raw="NOP", defs={"r0"}, uses={"r1"})
        assert "r0" in o.defs
        assert "r1" in o.uses

    def test_repr(self):
        o = OtherInsn(raw="HALT")
        assert "HALT" in repr(o)


class TestBasicBlock:
    def test_id_required(self):
        bb = BasicBlock(id="entry")
        assert bb.id == "entry"

    def test_label_defaults_none(self):
        bb = BasicBlock(id="b1")
        assert bb.label is None

    def test_label_set(self):
        bb = BasicBlock(id="b1", label="IDLE")
        assert bb.label == "IDLE"

    def test_insns_default_empty(self):
        bb = BasicBlock(id="b1")
        assert bb.insns == []

    def test_insns_typed(self):
        bb = BasicBlock(id="b1", insns=[
            Assignment("x", []),
            CallRef("sub"),
            OtherInsn("NOP"),
        ])
        assert len(bb.insns) == 3
        assert isinstance(bb.insns[0], Assignment)
        assert isinstance(bb.insns[1], CallRef)
        assert isinstance(bb.insns[2], OtherInsn)

    def test_equality_uses_id(self):
        b1 = BasicBlock(id="x")
        b2 = BasicBlock(id="x")
        b3 = BasicBlock(id="y")
        assert b1 == b2
        assert b1 != b3

    def test_is_unhashable(self):
        bb = BasicBlock(id="a")
        with pytest.raises(TypeError):
            hash(bb)
        with pytest.raises(TypeError):
            {bb}

    def test_id_is_immutable_after_construction(self):
        bb = BasicBlock(id="a")
        with pytest.raises(AttributeError, match="immutable"):
            bb.id = "b"

    @pytest.mark.parametrize("block_id", ["", 1, None])
    def test_invalid_id_rejected(self, block_id):
        with pytest.raises(ValueError, match="non-empty string"):
            BasicBlock(id=block_id)  # type: ignore[arg-type]

    def test_repr(self):
        bb = BasicBlock(id="entry", label="IDLE")
        r = repr(bb)
        assert "entry" in r
        assert "IDLE" in r
