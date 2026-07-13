"""Comprehensive tests for MapBV based on the public API (var, const, concat).

This test suite rigorously validates the behavior of MapBV variables, constants,
slices, and concatenated variables, as well as logic operations and symbolic evaluation.
"""

import warnings

import pytest
from rpkbin.mapbv import MapBV, const, var, concat


# ═══════════════════════════════════════════════════════════════════════════
# Object Creation (Factory Functions)
# ═══════════════════════════════════════════════════════════════════════════


class TestFactory:
    def test_var_creation(self):
        reg = var("REG0", 16)
        assert reg.name == "REG0"
        assert reg.width == 16
        assert reg.kind == "VAR"
        assert reg.value == 0
        assert not reg.is_const
        assert not reg.is_linked

    def test_var_initial_value(self):
        reg = var("REG0", 16, 0xABCD)
        assert reg.value == 0xABCD

    def test_const_creation(self):
        c = const(0, 2)
        assert c.name == "Constant"
        assert c.width == 2
        assert c.kind == "CONST"
        assert c.value == 0
        assert c.is_const

    def test_const_value_masked(self):
        # const() auto-masks the value to the specified width
        c = const(0xFF, 4)
        assert c.value == 0xF

    def test_concat_creation(self):
        reg = var("REG", 8, 0xAB)
        pad = const(0, 4)
        c = concat("WORD", reg, pad)
        assert c.name == "WORD"
        assert c.width == 12
        assert c.kind == "VAR"
        assert c.is_linked
        assert c.value == 0xAB0


# ═══════════════════════════════════════════════════════════════════════════
# Slicing (& bit-level access)
# ═══════════════════════════════════════════════════════════════════════════


class TestSlicing:
    def test_slice_read(self):
        reg = var("R", 16, 0xABCD)
        assert reg[7:0].value == 0xCD
        assert reg[15:8].value == 0xAB
        assert reg[3:0].value == 0xD
        # Single bit slicing
        assert reg[3].value == 1
        assert reg[2].value == 1
        assert reg[1].value == 0

    def test_slice_write(self):
        reg = var("R", 16, 0x0000)
        reg[7:4].value = 0xF
        assert reg.value == 0x00F0

    def test_slice_properties(self):
        reg = var("R", 16)
        s = reg[7:0]
        assert s.width == 8
        assert s.kind == "SLICE"
        assert s.name == "R[7:0]"


# ═══════════════════════════════════════════════════════════════════════════
# Linking and Detaching
# ═══════════════════════════════════════════════════════════════════════════


class TestLinkingAndDetach:
    def test_link_read_live(self):
        reg0 = var("REG0", 16, 0x5)
        reg1 = var("REG1", 16, 0x2)
        pad = const(0, 4)
        sram = var("SRAM", 12)

        # LINK: reg0[3:0] (4) + pad (4) + reg1[3:0] (4) = 12 bits
        sram.link(reg0[3:0], pad, reg1[3:0])
        assert sram.is_linked
        # 0x5 << 8 | 0x0 << 4 | 0x2 = 0x502
        assert sram.value == 0x502

    def test_link_write_distributes(self):
        reg0 = var("REG0", 16)
        reg1 = var("REG1", 16)
        sram = var("SRAM", 8)
        sram.link(reg0[3:0], reg1[3:0])

        sram.value = 0xAB
        assert reg0.value == 0xA
        assert reg1.value == 0xB

    def test_link_rejects_overlapping_writable_bits(self):
        reg = var("REG", 4)
        with pytest.raises(ValueError, match="overlapping"):
            concat("DUP", reg[3:0], reg[3:0])

    def test_detach_snapshots_value(self):
        reg = var("REG", 8, 0x42)
        sram = var("SRAM", 8)
        sram.link(reg)

        assert sram.value == 0x42
        sram.detach()
        assert not sram.is_linked
        assert sram.value == 0x42

        # Changing source no longer affects sram
        reg.value = 0xFF
        assert sram.value == 0x42

    def test_detach_noop_if_not_linked(self):
        a = var("A", 8, 0x42)
        a.detach()
        assert a.value == 0x42


# ═══════════════════════════════════════════════════════════════════════════
# Logic and Shift Operations
# ═══════════════════════════════════════════════════════════════════════════


class TestLogicAndShiftOps:
    def test_and(self):
        a = var("A", 8, 0xFF)
        b = var("B", 8, 0x0F)
        assert (a & b).value_eq(0x0F)

    def test_or(self):
        a = var("A", 8, 0xF0)
        assert (a | 0x0F).value_eq(0xFF)

    def test_xor(self):
        a = var("A", 8, 0xFF)
        b = var("B", 8, 0x0F)
        assert (a ^ b).value_eq(0xF0)

    def test_invert(self):
        a = var("A", 8, 0x0F)
        assert (~a).value_eq(0xF0)

    def test_lshift(self):
        a = var("A", 8, 0x0F)
        assert (a << 4).value_eq(0xF0)
        # Shift beyond width masks out
        a.value = 0xFF
        assert (a << 4).value_eq(0xF0)

    def test_rshift(self):
        a = var("A", 8, 0xF0)
        assert (a >> 4).value_eq(0x0F)

    def test_complex_expr(self):
        reg0 = var("REG0", 16, 0xABCD)
        reg1 = var("REG1", 16, 0x00FF)
        full_logic = (reg0 & 0x0F) | (reg1 ^ const(0xFF, 16))
        assert full_logic.value_eq(0x000D)

    def test_reverse_ops(self):
        a = var("A", 8, 0xAB)
        assert (0x0F & a).value_eq(0x0B)
        assert (0x0F | a).value_eq(0xAF)
        assert (0x0F ^ a).value_eq(0xA4)

    def test_oversized_int_operands_are_masked_consistently(self):
        a = var("A", 4, 0xA)
        assert (a & 0x100).value_eq(0)
        assert (a | 0x100).value_eq(0xA)
        assert (a ^ 0x100).value_eq(0xA)

    def test_slice_ops(self):
        a = var("A", 16, 0xABCD)
        assert (a[7:0] & 0x0F).value_eq(0x0D)
        assert (~a[3:0]).value_eq(0x02)  # ~0xD = ~1101 = 0010 = 0x2
        assert (a[7:0] << 4).value_eq(0xD0)


# ═══════════════════════════════════════════════════════════════════════════
# Symbolic Evaluation
# ═══════════════════════════════════════════════════════════════════════════


class TestEvaluation:
    def test_symbolic_eval(self):
        reg0 = var("REG0", 16, 0x5)
        reg1 = var("REG1", 16, 0x2)
        sram = concat("SRAM", reg0[3:0], const(0, 4), reg1[3:0])

        # True value is 0x502
        assert sram.value == 0x502

        # Evaluate with hypothetical values
        simulated = sram.eval({"REG0": 0xA, "REG1": 0x3})
        assert simulated == 0xA03

        # Real values remain unchanged
        assert sram.value == 0x502

    def test_expr_eval(self):
        a = var("A", 8)
        b = var("B", 8)
        assert (a & b).eval({"A": 0xFF, "B": 0x0F}) == 0x0F

    def test_eval_fallback_to_current(self):
        reg = var("REG", 8, 0x42)
        assert reg.eval({}) == 0x42


# ═══════════════════════════════════════════════════════════════════════════
# Equality Checking and Hashing
# ═══════════════════════════════════════════════════════════════════════════


class TestEqualityAndHashing:
    def test_identity_eq(self):
        a = var("A", 8, 0x42)
        b = var("B", 8, 0x42)
        assert a != b  # Different identity
        assert a == a

    def test_value_eq(self):
        a = var("A", 8, 0x42)
        assert a.value_eq(0x42)
        assert not a.value_eq(0x43)

        b = var("B", 8, 0x42)
        assert a.value_eq(b)

    def test_hash_identity(self):
        a = var("A", 8, 0x42)
        b = var("B", 8, 0x42)
        d = {a: "first", b: "second"}
        assert d[a] == "first"
        assert d[b] == "second"

    def test_int_equality(self):
        a = var("A", 8, 0x42)
        assert int(a) == 0x42
        assert int(a[7:0]) == 0x42


# ═══════════════════════════════════════════════════════════════════════════
# String Formatting and Conversions
# ═══════════════════════════════════════════════════════════════════════════


class TestFormattingAndConversion:
    def test_linked_layout_uses_destination_ranges(self):
        high = var("HIGH", 8, 0xA0)[7:4]
        low = var("LOW", 8, 0x03)[1:0]
        layout = str(concat("WORD", high, const(0, 2), low))
        assert "[7:4] 0xA  <- HIGH[7:4]" in layout
        assert "[3:2] 0x0  <- Constant" in layout
        assert "[1:0] 0x3  <- LOW[1:0]" in layout

    def test_len_returns_width(self):
        assert len(var("A", 16)) == 16
        assert len(var("A", 16)[7:0]) == 8
        assert len(const(0, 4) & var("B", 4)) == 4

    def test_int_conversion(self):
        assert int(var("A", 8, 0x42)) == 0x42
        assert int(var("A", 16, 0xABCD)[7:0]) == 0xCD

    def test_to_hex_bin(self):
        a = var("A", 16, 0x00FF)
        assert a.to_hex() == "0x00FF"
        assert a.to_bin() == "0b0000000011111111"

    def test_format_specifiers(self):
        a = var("A", 8, 0xAB)
        assert f"{a:hex}" == "0xAB"
        assert f"{a:x}" == "0xAB"
        assert f"{a:bin}" == "0b10101011"
        assert f"{a:d}" == "171"


# ═══════════════════════════════════════════════════════════════════════════
# Exceptions and Warnings
# ═══════════════════════════════════════════════════════════════════════════


class TestExceptionsAndWarnings:
    def test_invalid_name(self):
        with pytest.raises(ValueError, match="must be a valid Python identifier"):
            var("123bad", 8)

    def test_value_out_of_bounds(self):
        with pytest.raises(ValueError, match="out of bounds"):
            var("A", 8, 0xFFF)

    def test_invalid_slice_range(self):
        reg = var("R", 8)
        with pytest.raises(IndexError, match="out of bounds"):
            reg[2:5]  # high < low is technically valid in syntax but we check parent bounds in MapBV logic

        with pytest.raises(IndexError, match="out of bounds"):
            reg[8:0]  # out of 8-bit width range (high=7 max)

    def test_link_width_mismatch(self):
        sram = var("SRAM", 8)
        a = var("A", 4)
        with pytest.raises(ValueError, match="width mismatch"):
            sram.link(a)

    def test_link_circular_reference(self):
        a = var("A", 8)
        b = var("B", 8)
        with pytest.raises(ValueError, match="Circular link"):
            a.link(b)
            # Cannot link b back to a
            b.link(a)

    def test_slice_link_not_supported(self):
        reg = var("REG", 16)
        with pytest.raises(TypeError, match="Cannot call link.*on a SLICE"):
            reg[7:0].link(var("A", 8))

    def test_const_link_not_supported(self):
        with pytest.raises(TypeError, match="only VAR"):
            const(0, 8).link(var("A", 8))

    def test_write_to_const_warns(self):
        c = const(0x0, 4)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            c.value = 0xF
            assert len(w) == 1
            assert "constant" in str(w[0].message).lower()
        # Value remains unchanged
        assert c.value == 0x0

    def test_relink_warns(self):
        sram = var("SRAM", 8)
        a = var("A", 8)
        b = var("B", 8)
        sram.link(a)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sram.link(b)
            assert len(w) == 1
            assert "already linked" in str(w[0].message).lower()
