"""Numeric representations preserve storage, and APyFloat edits preserve format."""

import json

import apytypes as apy
import numpy as np
import pytest

from rpkbin.calc.core import CalcConfig, WorkspaceSession
from rpkbin.calc.core.editing import EditJournal
from rpkbin.calc.core.representations import numeric_representation
from rpkbin.calc.protocol import inspector_from_dict, inspector_to_dict


@pytest.fixture
def session():
    return WorkspaceSession(CalcConfig(), seed_demo_vars=False)


@pytest.mark.parametrize("value", [0, 255, -1, (1 << 200) + 3, -((1 << 200) + 3)])
def test_python_integer_representation_has_no_fixed_width_or_twos_complement(
    session, value
):
    session.user_ns["x"] = value
    snapshot = session.get_inspector_snapshot("x")
    representation = snapshot.native_bits
    assert representation["width"] is None
    assert representation["hex"] == hex(value)
    assert representation["binary"] == bin(value)
    assert session.user_ns["x"] is value
    with pytest.raises(TypeError, match="FXP"):
        session.toggle_target_bit("x", None, 0)
    assert session.user_ns["x"] is value


def test_huge_integer_preview_is_bounded_without_changing_value(session):
    value = -(1 << 100_000)
    session.user_ns["x"] = value
    snapshot = session.get_inspector_snapshot("x")
    assert snapshot.native_bits["truncated"]
    assert snapshot.native_bits["magnitude_bits"] == 100_001
    assert len(snapshot.native_bits["binary"]) < 80
    assert snapshot.native_bits["hex"].endswith("…")
    assert "100001" in snapshot.value_str
    assert session.list_variables()[0].format_str == "int"
    assert session.user_ns["x"] is value


@pytest.mark.parametrize(
    "dtype,uint_type,patterns",
    [
        (np.float16, np.uint16, [0, 0x8000, 1, 0x3E00, 0x7C00, 0xFC00, 0x7C01, 0x7E55]),
        (
            np.float32,
            np.uint32,
            [
                0,
                0x80000000,
                1,
                0x3FC00000,
                0x7F800000,
                0xFF800000,
                0x7F800001,
                0x7FC01234,
            ],
        ),
        (
            np.float64,
            np.uint64,
            [
                0,
                0x8000000000000000,
                1,
                0x3FF8000000000000,
                0x7FF0000000000000,
                0xFFF0000000000000,
                0x7FF0000000000001,
                0x7FF8000000001234,
            ],
        ),
    ],
)
def test_numpy_float_inspection_retains_exact_bits_and_special_classification(
    session, dtype, uint_type, patterns
):
    classifications = [
        "Positive zero",
        "Negative zero",
        "Subnormal",
        "Normal",
        "Positive infinity",
        "Negative infinity",
        "NaN",
        "NaN",
    ]
    for raw, classification in zip(patterns, classifications):
        scalar = np.array([raw], dtype=uint_type).view(dtype)[0]
        before = scalar.tobytes()
        session.user_ns["x"] = scalar
        snapshot = session.get_inspector_snapshot("x")
        representation = snapshot.native_bits
        assert int(representation["hex"], 16) == raw
        assert int(representation["binary"], 2) == raw
        assert representation["classification"] == classification
        assert representation["width"] == scalar.dtype.itemsize * 8
        assert representation["editable"] is False
        decoded = inspector_from_dict(
            json.loads(json.dumps(inspector_to_dict(snapshot), allow_nan=False))
        )
        assert decoded == snapshot
        with pytest.raises(TypeError, match="FXP"):
            session.toggle_target_bit("x", None, 0)
        assert scalar.tobytes() == before and session.user_ns["x"] is scalar


def test_python_float_and_big_endian_numpy_cell_preserve_their_actual_formats(session):
    session.user_ns["x"] = -0.0
    assert (
        session.get_inspector_snapshot("x").native_bits["hex"] == "0x8000000000000000"
    )
    session.user_ns["a"] = np.array([[1.5]], dtype=">f4")
    snapshot = session.get_inspector_snapshot("a", (0, 0))
    assert snapshot.native_bits["hex"] == "0x3FC00000"
    assert snapshot.native_bits["width"] == 32
    assert snapshot.native_bits["editable"] is False


@pytest.mark.parametrize(
    "value", [True, np.bool_(True), 1 + 2j, np.complex64(1), np.longdouble(1)]
)
def test_other_numeric_types_do_not_get_a_guessed_binary_layout(value):
    assert numeric_representation(value) is None


@pytest.mark.parametrize("bias", [7, 15])
@pytest.mark.parametrize("raw", [0, 0x80, 1, 0x3E, 0x7C, 0xFC, 0x7D, 0xFF])
def test_apyfloat_bit_edit_undo_redo_retains_payload_signed_zero_and_custom_bias(
    session, bias, raw
):
    original = apy.APyFloat.from_bits(raw, exp_bits=5, man_bits=2, bias=bias)
    session.user_ns["f"] = original
    snapshot = session.get_inspector_snapshot("f")
    assert snapshot.editable is False  # Its numeric field is not an implicit cast.
    assert snapshot.native_bits["editable"] is True
    assert int(snapshot.native_bits["hex"], 16) == raw
    journal = EditJournal(session)
    for bit in (7, 0):
        journal.apply(
            [("f", None)], lambda bit=bit: session.toggle_target_bit("f", None, bit)
        )
    for redo, expected in [
        (False, raw ^ 128),
        (False, raw),
        (True, raw ^ 128),
        (True, raw ^ 129),
    ]:
        journal.replay(redo=redo)
        current = session.user_ns["f"]
        assert type(current) is apy.APyFloat
        assert (current.exp_bits, current.man_bits, current.bias) == (5, 2, bias)
        assert current.to_bits() == expected
    assert original.to_bits() == raw


@pytest.mark.parametrize("bit", [-1, 8, True, 1.5])
def test_invalid_apyfloat_bit_edit_is_atomic_and_does_not_add_undo(session, bit):
    value = apy.APyFloat.from_bits(0x7D, exp_bits=5, man_bits=2, bias=7)
    session.user_ns["f"] = value
    journal = EditJournal(session)
    with pytest.raises(ValueError, match="Bit index"):
        journal.apply([("f", None)], lambda: session.toggle_target_bit("f", None, bit))
    assert session.user_ns["f"] is value
    assert not journal.state()["can_undo"]


def test_apyfloat_external_rebinding_refuses_stale_undo_and_numeric_edits(session):
    value = apy.APyFloat.from_bits(0x7D, exp_bits=5, man_bits=2)
    session.user_ns["f"] = value
    journal = EditJournal(session)
    journal.apply([("f", None)], lambda: session.toggle_target_bit("f", None, 0))
    rebound = apy.APyFloat.from_bits(
        session.user_ns["f"].to_bits(), exp_bits=5, man_bits=2
    )
    session.user_ns["f"] = rebound
    with pytest.raises(ValueError, match="outside this edit"):
        journal.replay()
    for field in ("real", "hex", "bin"):
        with pytest.raises(TypeError, match="REPL"):
            session.update_target_field("f", None, field, "1")
    assert session.user_ns["f"] is rebound


def test_apyfloat_outside_binary64_or_with_more_precision_is_not_displayed_as_inf_or_zero(
    session,
):
    for value in [
        apy.APyFloat(sign=0, exp=2000, man=1, exp_bits=15, man_bits=2, bias=7),
        apy.APyFloat(sign=1, exp=1, man=1, exp_bits=15, man_bits=2),
        apy.APyFloat(sign=0, exp=15, man=1, exp_bits=5, man_bits=60),
    ]:
        session.user_ns["f"] = value
        snapshot = session.get_inspector_snapshot("f")
        assert value.is_finite
        assert "0b" in snapshot.value_str and "× 2^" in snapshot.value_str
        assert snapshot.native_bits["classification"] == "Normal"


def test_apyfloat_array_support_is_not_implied_by_scalar_controls(session):
    array = apy.APyFloatArray.from_float([1.5], exp_bits=5, man_bits=2)
    session.user_ns["a"] = array
    assert session.get_inspector_snapshot("a").native_bits is None
    with pytest.raises(TypeError):
        session.toggle_target_bit("a", None, 0)
    assert session.user_ns["a"] is array


def test_representation_source_changes_invalidate_kernel_build(monkeypatch):
    from pathlib import Path
    from rpkbin.calc.protocol import build_id, validate_envelope, PROTOCOL_VERSION

    before = build_id()
    original = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: (
            original(path)
            + (b"\n# changed" if path.name == "representations.py" else b"")
        ),
    )
    after = build_id()
    assert before != after
    with pytest.raises(ValueError, match="builds differ"):
        validate_envelope({"protocol": PROTOCOL_VERSION, "build": before}, after)
