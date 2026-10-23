"""Generated fixtures only: no Excel or external source data is used here."""

from fractions import Fraction
import importlib.util
import sys
from types import SimpleNamespace

import pytest

from rpkbin.tablepack import DecodeLimits, Float, Schema, Str, Uint, pack, unpack
from rpkbin.tablepack import codec


CODECS = [None, "zlib", "lzma"]
if importlib.util.find_spec("brotli"):
    CODECS.append("brotli")


@pytest.mark.parametrize("compression", CODECS)
def test_large_output_is_drained_with_exact_payload_budget(compression):
    rows = [{"text": "abcdefgh" * 32768}]
    schema = Schema(Str("text"))
    payload_size = len(pack(rows, schema, compress=None)) - 5
    blob = pack(rows, schema, compress=compression)
    assert unpack(blob, limits=DecodeLimits(max_payload_bytes=payload_size)) == rows
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(blob, limits=DecodeLimits(max_payload_bytes=payload_size - 1))


def test_v1_nullable_repeat_delta_golden_blob():
    # This literal follows the original V1 writer: [None, None, 5, 5, None, 7].
    # The two non-repeated integers are zigzag deltas +5 and +2.
    blob = b"TPK\x01\x00\x00\x06\x00\x02\x01\x03\x00\x01\x0a\x04"
    schema = Schema(Uint("u", nullable=True, repeat=True, delta=True))
    rows = [{"u": v} for v in [None, None, 5, 5, None, 7]]
    assert unpack(blob, schema) == rows
    assert pack(rows, schema, embed_schema=False, compress=None) == blob


@pytest.mark.parametrize("value", [2**53 + 1, Fraction(1, 3)])
def test_verifier_independently_rejects_a_lossy_encoder(monkeypatch, value):
    original = codec._prep

    def intentionally_lossy(plan, value):
        return float(value) if plan.kind == "float" else original(plan, value)

    monkeypatch.setattr(codec, "_prep", intentionally_lossy)
    with pytest.raises(ValueError, match="verify failed"):
        pack({"f": [value]}, Schema(Float("f")), verify=True)


def test_fraction_cannot_silently_lose_precision():
    with pytest.raises(ValueError, match="exactly representable"):
        pack({"f": [Fraction(1, 3)]}, Schema(Float("f")))
    assert unpack(pack({"f": [Fraction(1, 2)]}, Schema(Float("f")))) == [{"f": .5}]


@pytest.mark.parametrize("payload", [b"\x02\x00", b"\x00" + b"\x80" * 12])
def test_unknown_flags_and_excessive_row_varint(payload):
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(b"TPK\x01\x00" + payload, Schema(Uint("u")))


@pytest.mark.parametrize("value", [0, -1, True, 1.5, None])
def test_decode_budget_is_a_positive_plain_integer(value):
    with pytest.raises(ValueError):
        DecodeLimits(max_rows=value)


@pytest.mark.parametrize("compression", CODECS)
def test_every_truncation_of_a_small_blob_is_rejected(compression):
    schema = Schema(Str("s"), Uint("u", nullable=True, repeat=True, delta=True))
    blob = pack({"s":["a", "hello", ""], "u":[None, 5, 5]}, schema, compress=compression)
    for end in range(len(blob)):
        with pytest.raises(ValueError):
            unpack(blob[:end], schema)


def test_legacy_brotli_falls_back_and_explicit_mode_explains_upgrade(monkeypatch):
    monkeypatch.setitem(sys.modules, "brotli", SimpleNamespace(Decompressor=type("Legacy", (), {})))
    schema = Schema(Uint("u"))
    blob = pack({"u":[1,2]}, schema, compress="auto")
    assert blob[4] == 1  # V1 zlib codec id
    assert unpack(blob) == [{"u":1}, {"u":2}]
    with pytest.raises(ImportError, match="1.2|upgrade"):
        pack({"u":[1]}, schema, compress="brotli")


def test_non_bytes_blob_is_rejected_before_bytes_allocation():
    with pytest.raises(TypeError, match="bytes-like"):
        unpack(1_000_000_000)
