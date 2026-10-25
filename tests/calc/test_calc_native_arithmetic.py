"""APyTypes itself is the oracle; GUI metadata must not redefine arithmetic."""
import operator
import warnings
from fractions import Fraction

import apytypes as apy
import numpy as np
import pytest

from rpkbin.calc.core import CalcConfig, FxpSpec, WorkspaceSession
from rpkbin.calc.core.editing import EditJournal
from rpkbin.calc.core.fxp_model import RpkFixed, RpkFixedArray, extract_spec, make_fxp


OPERATIONS = {
    "add": operator.add, "sub": operator.sub, "mul": operator.mul,
    "div": operator.truediv, "radd": lambda a, b: b + a,
    "rsub": lambda a, b: b - a, "rmul": lambda a, b: b * a,
    "rdiv": lambda a, b: b / a, "iadd": operator.iadd,
    "isub": operator.isub, "imul": operator.imul, "idiv": operator.itruediv,
}
FORMATS = [(3, 1, 2), (8, 4, 0xF1), (5, 8, 3), (4, -1, 2), (80, 40, (1 << 70) + 3)]


def signature(value, *, with_type=True):
    if isinstance(value, (apy.APyFixed, apy.APyFixedArray)):
        fields = (value.bits, value.int_bits, value.frac_bits,
                  getattr(value, "shape", None), value.to_bits())
    elif isinstance(value, np.ndarray):
        fields = (value.dtype.str, value.shape, [signature(item) for item in value.flat])
    elif isinstance(value, np.generic):
        fields = (value.dtype.str, value.tobytes())
    else:
        fields = (repr(value),)
    return (type(value), fields) if with_type else fields


def outcome(fn, value, operand):
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        try:
            result = ("result", signature(fn(value, operand)))
        except Exception as exc:
            # Python may name the runtime subclass in its diagnostic; the error
            # kind is the contract, not the exact message spelling.
            result = ("error", type(exc))
    return result, signature(value, with_type=False), tuple(w.category for w in captured)


@pytest.mark.parametrize("array", [False, True], ids=["scalar", "array"])
@pytest.mark.parametrize("bits,int_bits,raw", FORMATS)
@pytest.mark.parametrize("operation", OPERATIONS)
def test_metadata_subclass_arithmetic_matches_native_bits_format_type_and_errors(array, bits, int_bits, raw, operation):
    native_cls, gui_cls = (apy.APyFixedArray, RpkFixedArray) if array else (apy.APyFixed, RpkFixed)
    pattern = [[raw, 0]] if array else raw
    for operand in (1, -2, 1.0, True, np.int16(1), np.float32(1), 1 << 60, "unsupported"):
        native = native_cls(pattern, bits=bits, int_bits=int_bits)
        edited = gui_cls(pattern, bits=bits, int_bits=int_bits)
        expected = outcome(OPERATIONS[operation], native, operand)
        actual = outcome(OPERATIONS[operation], edited, operand)
        assert actual == expected, (operation, repr(operand), actual, expected)


@pytest.mark.parametrize("array", [False, True])
@pytest.mark.parametrize("operation", OPERATIONS)
def test_explicit_apy_operand_and_unsigned_gui_backing_use_native_rules(array, operation):
    native_cls, gui_cls = (apy.APyFixedArray, RpkFixedArray) if array else (apy.APyFixed, RpkFixed)
    # Unsigned WL=4 uses a signed five-bit APyTypes backing value with a guard bit.
    # Compare that actual backing format, without inventing unsigned APy arithmetic.
    pattern = [6, 0] if array else 6
    edited = gui_cls(pattern, bits=5, int_bits=3)
    edited.is_unsigned, edited.hw_wl = True, 4
    native = native_cls(pattern, bits=5, int_bits=3)
    operand = apy.APyFixed.from_float(1, bits=8, frac_bits=4)
    assert outcome(OPERATIONS[operation], edited, operand) == outcome(OPERATIONS[operation], native, operand)
    assert edited.is_unsigned and edited.hw_wl == 4


@pytest.mark.parametrize("array", [False, True])
@pytest.mark.parametrize("fn", [operator.neg, operator.pos, operator.abs], ids=["neg", "pos", "abs"])
def test_unary_operations_are_native_including_unsupported_cases(array, fn):
    native_cls, gui_cls = (apy.APyFixedArray, RpkFixedArray) if array else (apy.APyFixed, RpkFixed)
    pattern = [0xF1, 0] if array else 0xF1
    native = native_cls(pattern, bits=8, int_bits=4)
    edited = gui_cls(pattern, bits=8, int_bits=4)
    assert outcome(lambda a, _: fn(a), edited, None) == outcome(lambda a, _: fn(a), native, None)


@pytest.mark.parametrize("array", [False, True])
def test_copy_preserves_independent_gui_metadata_while_math_result_is_native(array):
    original = make_fxp([1.5, 0] if array else 1.5, FxpSpec(False, 4, 2))
    copied = original.copy()
    assert type(copied) is type(original)
    assert copied is not original and copied.__dict__.keys() == original.__dict__.keys()
    for name, value in original.__dict__.items():
        if isinstance(value, np.ndarray):
            np.testing.assert_array_equal(copied.__dict__[name], value)
            assert copied.__dict__[name] is not value
        else:
            assert copied.__dict__[name] == value
    assert extract_spec(copied) == FxpSpec(False, 4, 2)
    if array:
        copied.cell_sources = {(0,): (6, Fraction(3, 2), False)}
        assert original.cell_sources is None
    else:
        assert copied.target_real == Fraction(3, 2)
    result = original + apy.APyFixed.from_float(1, bits=8, frac_bits=4)
    assert type(result) is (apy.APyFixedArray if array else apy.APyFixed)
    assert not getattr(result, "is_unsigned", False)
    assert getattr(result, "target_real", None) is None


@pytest.mark.parametrize("array", [False, True])
def test_gui_unsigned_edit_copy_undo_and_bit_edit_keep_metadata(array):
    session = WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)
    session.user_ns["a"] = make_fxp([1.5] if array else 1.5, FxpSpec(False, 4, 2))
    session.user_ns["alias"] = session.user_ns["a"]
    owner = session.user_ns["a"]
    coord = (0, 0) if array else None
    journal = EditJournal(session)
    before = session.get_inspector_snapshot("a", coord)
    journal.apply([("a", coord)], lambda: session.update_target_field("a", coord, "real", "99"))
    after = session.get_inspector_snapshot("a", coord)
    assert after.spec == before.spec == FxpSpec(False, 4, 2)
    assert after.status_kind == "saturated" and after.uint_val == 15
    if array:
        assert session.user_ns["a"] is session.user_ns["alias"] is owner
    journal.replay()
    restored = session.get_inspector_snapshot("a", coord)
    assert restored.uint_val == before.uint_val and restored.status_kind == before.status_kind
    journal.replay(redo=True)
    assert session.get_inspector_snapshot("a", coord).status_kind == "saturated"
    duplicate = session.duplicate_variable("a")
    assert session.user_ns[duplicate] is not session.user_ns["a"]
    assert session.get_inspector_snapshot(duplicate, coord).uint_val == 15
    session.update_target_field("a", coord, "hex", "0x2")
    bits = session.get_inspector_snapshot("a", coord)
    assert bits.spec == before.spec and bits.uint_val == 2 and bits.status_kind == "untracked"
    assert session.get_inspector_snapshot(duplicate, coord).uint_val == 15
