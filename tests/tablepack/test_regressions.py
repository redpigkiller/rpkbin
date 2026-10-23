"""Regression coverage for numeric fidelity and malformed tablepack blobs.

The corrupt fixtures below are small, hand-written v1 payloads.  They avoid
depending on private encoder details while exercising the public decoder.
"""

import math
import struct
from enum import IntEnum, StrEnum

import numpy as np
import pytest

from rpkbin.tablepack import (
    Bool, Category, DecodeLimits, Enum, Float, Int, Schema, Str, Uint,
    iter_unpack, pack, unpack,
)
from rpkbin.tablepack._varint import encode_varint, zigzag_encode


def _raw_payload(schema, n_rows, *, body=b"", flags=b"", categories=b""):
    """Make an uncompressed, schema-external v1 payload for ``schema``."""
    payload = b"\0" + encode_varint(n_rows) + categories + flags + body
    return b"TPK\x01\0" + payload


@pytest.mark.parametrize("bits,value", [(64, 2**53 + 1), (32, 2**24 + 1)])
@pytest.mark.parametrize("verify", [False, True])
def test_float_rejects_inexact_integers(bits, value, verify):
    with pytest.raises(ValueError, match="row 0, column 'f'.*exact"):
        pack([{"f": value}], Schema(Float("f", bits=bits)), verify=verify)


def test_infer_rejects_big_integer_mixed_with_float():
    with pytest.raises(ValueError, match="candidate|float|represent"):
        pack([{"x": 2**53 + 1}, {"x": 1.5}])


def test_huge_integer_float_overflow_has_location():
    with pytest.raises(ValueError, match=r"row 0, column 'f'.*range|overflow"):
        pack([{"f": 10**1000}], Schema(Float("f")))


def _float_from_bits(bits):
    return struct.unpack(">d", struct.pack(">Q", bits))[0]


def _float_bits(value):
    return struct.unpack(">Q", struct.pack(">d", value))[0]


def test_float64_nan_payload_and_signed_zero_survive_verify():
    payload_nan = _float_from_bits(0x7FF8000000000001)
    rows = [{"f": payload_nan}, {"f": -0.0}]
    back = unpack(pack(rows, Schema(Float("f")), verify=True))
    assert _float_bits(back[0]["f"]) == 0x7FF8000000000001
    assert math.copysign(1.0, back[1]["f"]) == -1.0


def test_float32_rejects_nan_with_unrepresentable_payload():
    payload_nan = _float_from_bits(0x7FF8000000000001)
    with pytest.raises(ValueError, match=r"row 0, column 'f'.*NaN|payload|float32"):
        pack([{"f": payload_nan}], Schema(Float("f", bits=32)))


def test_float32_allows_nan_that_roundtrips_exactly():
    canonical_nan = float("nan")
    back = unpack(pack([{"f": canonical_nan}], Schema(Float("f", bits=32))))
    assert math.isnan(back[0]["f"])


def test_numpy_scalar_acceptance_and_integer_subclass_boundaries():
    schema = Schema(Bool("b"), Uint("u"), Int("i"), Float("f"), Str("s"))
    rows = [{"b": np.bool_(True), "u": np.int64(8), "i": np.int64(-3),
             "f": np.float32(1.5), "s": np.str_("text")}]
    assert unpack(pack(rows, schema)) == [
        {"b": True, "u": 8, "i": -3, "f": 1.5, "s": "text"}
    ]

    for column in (Uint("u"), Int("i"), Enum("e", [1, 2])):
        field = column.name
        value = np.bool_(True)
        with pytest.raises(TypeError):
            pack([{field: value}], Schema(column))


class Label(StrEnum):
    RED = "red"
    BLUE = "blue"


class Code(IntEnum):
    ONE = 1
    TWO = 2


@pytest.mark.parametrize(
    "column,values",
    [(Enum("e", [Label.RED, Label.BLUE]), [Label.RED, Label.BLUE]),
     (Enum("e", [Code.ONE, Code.TWO]), [Code.ONE, Code.TWO])],
)
def test_enum_accepts_enum_subclasses_and_decodes_builtin_values(column, values):
    back = unpack(pack([{"e": v} for v in values], Schema(column)))
    expected_type = str if isinstance(values[0], StrEnum) else int
    expected = [expected_type(v) for v in values]
    assert [row["e"] for row in back] == expected
    assert all(type(row["e"]) is expected_type for row in back)


def test_category_numpy_integer_and_subclass_are_consistent():
    class MyInt(int):
        pass

    rows = [{"c": np.int64(4)}, {"c": MyInt(4)}, {"c": 9}]
    assert unpack(pack(rows, Schema(Category("c")))) == [
        {"c": 4}, {"c": 4}, {"c": 9}
    ]


# Hand-built malformed payloads.  flags are one byte per row for these schemas.
@pytest.mark.parametrize(
    "schema,n_rows,flags,body,categories",
    [
        (Schema(Uint("u", bits=1)), 1, b"", b"\xff", b""),
        (Schema(Uint("u", delta=True)), 1, b"", encode_varint(zigzag_encode(-1)), b""),
        (Schema(Uint("u", repeat=True)), 1, b"\x01", b"", b""),
        (Schema(Uint("u", nullable=True, repeat=True)), 1, b"\x02", b"", b""),
        (Schema(Category("c")), 0, b"", b"", b"\x02\x00"),
        (Schema(Category("c")), 0, b"", b"", b"\x00\x01\x02x"),
        (Schema(Str("s")), 1, b"", b"\x02x", b""),
    ],
)
def test_malformed_v1_payloads_are_rejected(schema, n_rows, flags, body, categories):
    blob = _raw_payload(schema, n_rows, flags=flags, body=body, categories=categories)
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(blob, schema)


def test_trailing_uncompressed_payload_bytes_are_rejected():
    schema = Schema(Uint("u"))
    blob = _raw_payload(schema, 1, body=b"\x01\x00")
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(blob, schema)


def test_zero_row_category_string_truncation_is_rejected():
    schema = Schema(Category("c"))
    # string category tag, one item, claimed length two, only one byte follows
    blob = _raw_payload(schema, 0, categories=b"\0\x01\x02x")
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(blob, schema)


@pytest.mark.parametrize("bad_schema", [b"[]", b'{"columns":{}}', b'{"columns":[null]}'])
def test_invalid_embedded_schema_shape_is_reported_as_corrupt(bad_schema):
    payload = b"\x01" + encode_varint(len(bad_schema)) + bad_schema + b"\0"
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(b"TPK\x01\0" + payload)


def test_uint64_row_count_is_rejected_before_allocation():
    schema = Schema(Enum("e", ["only"]))
    blob = _raw_payload(schema, 2**64)
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(blob, schema)


COMPRESSORS = [None, "zlib", "lzma"]
try:
    import brotli  # noqa: F401
except ImportError:
    pass
else:
    COMPRESSORS.append("brotli")


@pytest.mark.parametrize("compress", COMPRESSORS)
def test_compressed_stream_trailing_junk_is_rejected(compress):
    blob = pack([{"u": 1}], Schema(Uint("u")), compress=compress)
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(blob + b"JUNK")


@pytest.mark.parametrize("compress", COMPRESSORS)
def test_decode_limits_cover_rows_cells_columns_schema_and_payload(compress):
    rows = [{"a": "x", "b": 300}, {"a": "y", "b": 301}]
    schema = Schema(Str("a"), Uint("b"))
    blob = pack(rows, schema, compress=compress)
    limits_to_try = [
        DecodeLimits(max_rows=1),
        DecodeLimits(max_cells=3),
        DecodeLimits(max_columns=1),
        DecodeLimits(max_schema_bytes=1),
        DecodeLimits(max_payload_bytes=1),
        DecodeLimits(max_integer_bytes=1),
    ]
    for limits in limits_to_try:
        with pytest.raises(ValueError, match="Corrupt"):
            unpack(blob, limits=limits)
        with pytest.raises(ValueError, match="Corrupt"):
            list(iter_unpack(blob, limits=limits))


@pytest.mark.parametrize("compress", COMPRESSORS)
def test_decode_limits_apply_to_large_row_count_without_large_allocation(compress):
    schema = Schema(Enum("e", ["only"]))
    blob = pack([{"e": "only"}] * 3, schema, compress=compress)
    limits = DecodeLimits(max_rows=2)
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(blob, limits=limits)


@pytest.mark.parametrize("compress", COMPRESSORS)
def test_zero_bit_enum_rows_are_not_inferred_from_payload_bytes(compress):
    rows = [{"e": "only"}] * 20
    schema = Schema(Enum("e", ["only"]))
    blob = pack(rows, schema, compress=compress, embed_schema=False)
    limits = DecodeLimits(max_payload_bytes=64, max_rows=20, max_cells=20)
    assert unpack(blob, schema, limits=limits) == rows
