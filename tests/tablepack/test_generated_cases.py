"""Reproducible register-like fixtures, independent of local workbooks."""

import importlib.util
import random
import struct

import pytest

from rpkbin.tablepack import Bool, Bytes, Category, Float, Int, Schema, Uint, pack, unpack


@pytest.mark.parametrize("seed", range(32))
def test_generated_mixed_register_roundtrip(seed):
    rng = random.Random(seed)
    widths = [1, 3, 7, 8, 13, 32, 64, 128]
    width = rng.choice(widths)
    schema = Schema(
        Uint("packed", bits=width, pack=True, nullable=True),
        Uint("reset", bits=128, delta=bool(seed & 1), repeat=True, nullable=True),
        Int("offset", delta=bool(seed & 2), repeat=True, nullable=True),
        Float("gain", bits=32, repeat=True, nullable=True),
        Bool("enable", nullable=True), Category("domain", nullable=True),
        Bytes("raw", repeat=True, nullable=True),
    )
    rows = []
    for i in range(24):
        r = {
            "packed": None if i % 5 == 0 else rng.getrandbits(width),
            "reset": rng.choice([None, 0, 2**127 + 3, 2**128 - 1]),
            "offset": rng.choice([None, -2**80, 0, 2**80]),
            "gain": rng.choice([None, -0.0, 0.0, .5, float("nan")]),
            "enable": rng.choice([None, True, False]),
            "domain": rng.choice([None, "bus", "core"]),
            "raw": rng.choice([None, b"", b"\x00\xff"]),
        }
        if rows and i % 3:
            for name in ("reset", "offset", "gain", "raw"):
                r[name] = rows[-1][name]
        rows.append(r)
    codecs = [None, "zlib", "lzma"]
    if importlib.util.find_spec("brotli"):
        codecs.append("brotli")
    compression = codecs[seed % len(codecs)]
    blob = pack(rows, schema, embed_schema=bool(seed & 4), compress=compression, verify=True)
    back = unpack(blob, schema)
    for a, b in zip(rows, back):
        for name in a:
            if isinstance(a[name], float):
                assert struct.pack(">d", a[name]) == struct.pack(">d", b[name])
            else:
                assert a[name] == b[name]
