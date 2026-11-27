"""Regression tests for MapBV bug fixes (nested slices, cycles, const writes, etc.)."""

import ast
import os
import warnings
from pathlib import Path

import pytest

import rpkbin.mapbv as mbv
from rpkbin.mapbv import concat, const, var


class TestNestedSlices:
    """Nested slice indices use the root variable's coordinates."""

    def test_read(self):
        r = var("R", 16, 0x1234)
        assert r[15:8].value == 0x12
        assert r[15:8][11:8].value == 0x2
        assert r[15:8][15:12].value == 0x1

    def test_write(self):
        r = var("R", 16, 0x1234)
        r[15:8][11:8].value = 0xF
        assert r.value == 0x1F34

    def test_eval(self):
        r = var("R", 16, 0x1234)
        assert r[15:8][11:8].eval({"R": 0xABCD}) == 0xB

    def test_through_concat(self):
        a = var("A", 8, 0x12)
        b = var("B", 8, 0x34)
        bus = concat("BUS", a, b)
        assert bus[15:8][15:12].value == 0x1
        bus[15:8][15:12].value = 0xE
        assert a.value == 0xE2


class TestConstWrites:
    def test_direct_write_to_const_raises(self):
        with pytest.raises(TypeError, match="constant"):
            const(0, 4).value = 1

    def test_write_to_slice_of_const_raises(self):
        with pytest.raises(TypeError, match="constant"):
            const(0xF, 4)[1:0].value = 0

    def test_bus_write_differing_bits_warns_and_ignores(self):
        a = var("A", 4)
        bus = concat("BUS", a, const(0, 4))
        with pytest.warns(UserWarning, match="constant"):
            bus.value = 0xFF
        assert bus.value == 0xF0
        assert a.value == 0xF

    def test_bus_write_matching_bits_silent(self):
        a = var("A", 4)
        bus = concat("BUS", a, const(0, 4))
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            bus.value = 0xA0
        assert a.value == 0xA

    def test_bus_write_const_slice_part(self):
        a = var("A", 4)
        bus = concat("BUS", a, const(0x3, 2)[1:0])
        with pytest.warns(UserWarning):
            bus.value = 0x00
        assert bus.value == 0x03


class TestMisc:
    def test_empty_concat(self):
        with pytest.raises(ValueError, match="at least one"):
            concat("EMPTY")

    def test_slice_step_rejected(self):
        with pytest.raises(ValueError, match="step"):
            var("A", 8)[7:0:2]

    def test_bad_operand_type(self):
        with pytest.raises(TypeError):
            var("A", 8) & "x"
        with pytest.raises(TypeError):
            var("A", 8) | 1.5

    def test_identity_eq_and_hash(self):
        a, b = var("A", 4, 1), var("B", 4, 1)
        assert a != b
        assert a.value_eq(b)
        assert len({a, b}) == 2


class TestNoNegativeValues:
    def test_const_negative_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            const(-1, 8)

    def test_var_negative_rejected(self):
        with pytest.raises(ValueError):
            var("A", 8, -1)

    def test_write_negative_rejected(self):
        a = var("A", 8, 5)
        with pytest.raises(ValueError, match="negative"):
            a.value = -1
        assert a.value == 5

    def test_write_negative_to_slice_rejected(self):
        a = var("A", 8, 5)
        with pytest.raises(ValueError, match="negative"):
            a[3:0].value = -1
        assert a.value == 5


class TestStrictRange:
    def test_const_too_wide(self):
        with pytest.raises(ValueError, match="out of bounds"):
            const(0xFF, 4)

    def test_write_too_wide(self):
        r = var("R", 4, 3)
        with pytest.raises(ValueError, match="out of bounds"):
            r.value = 0x1F
        with pytest.raises(ValueError, match="out of bounds"):
            r[3:0].value = 0x1F
        assert r.value == 3

    def test_operand_literals(self):
        a = var("A", 4, 0xF)
        with pytest.raises(ValueError):
            a & 0x1FF
        with pytest.raises(ValueError):
            0x100 | a
        with pytest.raises(ValueError):
            a ^ -1
        with pytest.raises(ValueError):
            a << -1
        assert (a & 0xF).value == 0xF
        assert (a << 100).value == 0

    def test_eval_ctx_range(self):
        a = var("A", 8)
        with pytest.raises(ValueError):
            a.eval({"A": 0x1FF})
        with pytest.raises(ValueError):
            a.eval({"A": -1})
        assert a.eval({"A": 0xFF}) == 0xFF

    def test_bad_widths(self):
        for f in (lambda: var("A", 0), lambda: var("A", -1), lambda: const(0, 0)):
            with pytest.raises(ValueError, match="width must be > 0"):
                f()


class TestUsability:
    def test_bool(self):
        assert not var("A", 8, 0)
        assert var("A", 8, 1)

    def test_setitem(self):
        r = var("R", 16, 0x1234)
        r[7:4] = 0xF
        r[0] = 0
        assert r.value == 0x12F4 & ~1
        with pytest.raises(ValueError):
            r[3:0] = 0x10

    def test_eq_int_raises(self):
        a = var("A", 4, 5)
        with pytest.raises(TypeError, match="value"):
            a == 5
        assert (a == None) is False  # noqa: E711
        assert a == a

    def test_format(self):
        a = var("A", 8, 0xA)
        assert f"{a:x}" == "a"
        assert f"{a:08b}" == "00001010"
        assert f"{a:hex}" == "0x0A"
        assert f"{a:bin}" == "0b00001010"

    def test_slice_str_header(self):
        s = str(var("R", 16, 0x1234)[15:8])
        assert s.startswith("R[15:8] (0x12)")
        assert "[15:8][15:8]" not in s

    def test_reversed_slice_message(self):
        with pytest.raises(IndexError, match="reversed"):
            var("R", 16)[0:3]

    def test_link_part_types(self):
        a, b = var("A", 4), var("B", 4)
        with pytest.raises(TypeError, match="MapBV"):
            concat("X", 5)
        with pytest.raises(TypeError, match="MapBV"):
            concat("X", a & b)

    def test_numpy_ints(self):
        np = pytest.importorskip("numpy")
        a = var("A", 8, np.uint8(3))
        assert (a & np.uint8(1)).value == 1
        assert (np.uint8(1) & a).value == 1
        a.value = np.int64(5)
        assert a.value == 5
        assert const(np.uint8(3), 4).value == 3


class TestS1Regressions:
    def test_numpy_widths_and_slice_bounds(self):
        np = pytest.importorskip("numpy")
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            big = var("BIG", np.int64(64), 1)
            assert big.width == 64
            assert big.value == 1
            c64 = const(0, np.int64(64))
            assert c64.width == 64
            big63 = var("BIG63", np.int64(63), 1)
            assert big63.width == 63

        wide = var("WIDE", 256)
        sl = wide[np.uint8(255) : np.uint8(0)]
        assert sl.width == 256

        a = var("A", 8, 0b00001000)
        assert a[np.int64(3)].value == 1
        assert a[np.int64(2)].value == 0

    def test_bool_rejected_for_width_and_index(self):
        a = var("A", 8, 0xAA)
        with pytest.raises(TypeError):
            _ = a[True]
        with pytest.raises(TypeError):
            _ = a[True:0]
        with pytest.raises(TypeError):
            _ = a[1:False]
        with pytest.raises(TypeError):
            var("BAD", True)
        with pytest.raises(TypeError):
            const(0, True)

    def test_value_eq_numpy_non_int_and_expr(self):
        np = pytest.importorskip("numpy")
        a = var("A", 8, 5)
        b = var("B", 8, 0xFF)
        assert a.value_eq(np.uint8(5))
        assert not a.value_eq(np.int64(6))
        assert not a.value_eq(None)
        assert not a.value_eq("5")
        assert not a.value_eq(5.0)
        assert (b & 0x05).value_eq(0x05)
        assert a.value_eq(b & 0x05)

    def test_const_write_non_int_raises_typeerror(self):
        c = const(0, 4)
        with pytest.raises(TypeError, match="constant"):
            c.value = "abc"
        sl = const(0xF, 4)[1:0]
        with pytest.raises(TypeError, match="constant"):
            sl.value = "abc"
        with pytest.raises(TypeError, match="constant"):
            sl.value = 99


class TestReprExportsAndEncoding:
    def test_repr_mapbv_and_expr(self):
        r = var("R", 16, 0x1234)
        c = const(0x5, 4)
        s = r[7:0]
        bus = concat("BUS", r[15:8], c)
        expr = r & 0xFF
        inv = ~r[3:0]

        assert repr(r) == "<MapBV VAR R[15:0] value=0x1234>"
        assert repr(c) == "<MapBV CONST Constant[3:0] value=0x5>"
        assert repr(s) == "<MapBV SLICE R[7:0] value=0x34>"
        assert repr(bus) == "<MapBV VAR linked BUS[11:0] value=0x125>"
        assert "MapBVExpr" in repr(expr)
        assert "0xFF" in repr(expr)
        assert "0x0034" in repr(expr)
        assert "MapBVExpr" in repr(inv)
        assert "~R[3:0]" in repr(inv)

    def test_star_import_all_exports(self):
        assert mbv.__all__ == ["MapBV", "MapBVExpr", "const", "var", "concat"]
        ns = {}
        exec("from rpkbin.mapbv import *", ns)
        ns.pop("__builtins__", None)
        assert set(ns) == {"MapBV", "MapBVExpr", "const", "var", "concat"}

    def test_mapbv_no_bom_lf_and_py311_ast(self):
        path = Path(__file__).resolve().parents[2] / "rpkbin" / "mapbv.py"
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r" not in raw.replace(b"\r\n", b"")
        crlf_count = raw.count(b"\r\n")
        lf_count = raw.count(b"\n")
        assert crlf_count in (0, lf_count)
        if os.name != "nt":
            assert crlf_count == 0
        ast.parse(raw.decode("utf-8"), feature_version=(3, 11))


