import math
import random
import struct
import warnings

import pytest

from rpkbin.tablepack import (
    Bool, Bytes, Category, Enum, Float, Int, Schema, Str, Uint,
    explain, infer, iter_unpack, pack, unpack,
)

COMPRESS = ["zlib", "lzma", None]
try:
    import brotli  # noqa: F401
    COMPRESS.append("brotli")
except ImportError:
    pass


SCHEMA = Schema(
    Str("module", repeat=True),
    Uint("addr", bits=16, repeat=True, delta=True),
    Uint("msb", bits=4, pack=True),
    Uint("lsb", bits=4, pack=True),
    Uint("nic", bits=16, nullable=True),
    Enum("kind", ["RW", "RWS", "RW1C", "RS", "RO"]),
    Category("lib"),
    Str("name"),
)

ROWS = [
    {"module": "sys", "addr": 0x10, "msb": 0, "lsb": 0, "nic": 0, "kind": "RW", "lib": "libA", "name": "locked"},
    {"module": "sys", "addr": 0x10, "msb": 1, "lsb": 1, "nic": None, "kind": "RO", "lib": "libA", "name": "remote"},
    {"module": "uart", "addr": 0x08, "msb": 15, "lsb": 2, "nic": 65535, "kind": "RS", "lib": "libB", "name": "名稱"},
]


@pytest.mark.parametrize("compress", COMPRESS)
@pytest.mark.parametrize("embed", [True, False])
def test_roundtrip_all_options(compress, embed):
    blob = pack(ROWS, SCHEMA, compress=compress, embed_schema=embed, verify=True)
    assert unpack(blob, None if embed else SCHEMA) == ROWS


def test_zero_config_and_columns_form():
    cols = {k: [r[k] for r in ROWS] for k in ROWS[0]}
    blob = pack(ROWS)
    assert unpack(blob) == ROWS
    assert unpack(blob, as_columns=True) == cols
    assert unpack(pack(cols)) == ROWS


def test_iter_unpack_and_deterministic():
    blob = pack(ROWS, SCHEMA)
    assert list(iter_unpack(blob)) == ROWS
    assert blob == pack(ROWS, SCHEMA)


def test_empty_table_with_schema():
    blob = pack([], SCHEMA)
    assert unpack(blob) == []
    with pytest.raises(ValueError, match="empty"):
        pack([])


def test_embedded_schema_mismatch_and_missing():
    blob = pack(ROWS, SCHEMA)
    other = Schema(Str("x"))
    with pytest.raises(ValueError, match="doesn't match"):
        unpack(blob, other)
    bare = pack(ROWS, SCHEMA, embed_schema=False)
    with pytest.raises(ValueError, match="no embedded schema"):
        unpack(bare)
    with pytest.raises(ValueError, match="explicit schema"):
        pack(ROWS, embed_schema=False)


def test_all_types_roundtrip():
    schema = Schema(
        Int("i", delta=True), Int("j"), Bool("b"), Bool("nb", nullable=True),
        Float("f"), Float("f32", bits=32, nullable=True, repeat=True),
        Bytes("raw", nullable=True), Category("n"), Category("s", nullable=True),
        Uint("u"), Uint("u8", bits=8), Uint("pk", bits=3, pack=True, nullable=True),
        Enum("e1", ["only"]), Enum("ei", [10, 20, 30], nullable=True),
    )
    rows = [
        {"i": -5, "j": -1, "b": True, "nb": None, "f": 1.5, "f32": 0.5, "raw": b"\x00\xff",
         "n": 7, "s": None, "u": 300, "u8": 255, "pk": 7, "e1": "only", "ei": 20},
        {"i": 100, "j": 2**70, "b": False, "nb": True, "f": -0.0, "f32": 0.5, "raw": None,
         "n": -3, "s": "x", "u": 0, "u8": 0, "pk": None, "e1": "only", "ei": None},
        {"i": -(2**40), "j": 0, "b": True, "nb": False, "f": float("inf"), "f32": None, "raw": b"",
         "n": 7, "s": "x", "u": 2**64, "u8": 1, "pk": 0, "e1": "only", "ei": 30},
    ]
    for compress in COMPRESS:
        back = unpack(pack(rows, schema, compress=compress, verify=True))
        assert back == rows
    # signed zero must survive
    assert math.copysign(1, unpack(pack(rows, schema))[1]["f"]) == -1


def test_nan_roundtrips_and_repeat_is_bit_exact():
    s = Schema(Float("f", repeat=True))
    rows = [{"f": 0.0}, {"f": -0.0}, {"f": float("nan")}, {"f": float("nan")}]
    back = unpack(pack(rows, s, verify=True))
    assert [struct.pack(">d", r["f"]) for r in back] == [struct.pack(">d", r["f"]) for r in rows]


def test_repeat_and_nullable_interplay():
    s = Schema(Uint("a", repeat=True, nullable=True, delta=True))
    vals = [None, None, 5, 5, None, None, 7, 7, 1, None, 1]
    rows = [{"a": v} for v in vals]
    assert unpack(pack(rows, s, verify=True)) == rows


def test_numpy_like_ints_accepted():
    class FakeInt:
        def __index__(self):
            return 42
    assert unpack(pack([{"a": FakeInt()}], Schema(Uint("a"))))[0]["a"] == 42


# ---------------------------------------------------------------- errors

def test_errors_carry_row_and_column():
    s = Schema(Uint("addr", bits=12))
    with pytest.raises(ValueError, match=r"row 1, column 'addr': 4096 doesn't fit in 12 bits"):
        pack([{"addr": 4095}, {"addr": 4096}], s)
    with pytest.raises(ValueError, match="negative"):
        pack([{"addr": -1}], s)
    with pytest.raises(TypeError, match="row 0, column 'addr'.*bool"):
        pack([{"addr": True}], s)
    with pytest.raises(ValueError, match="isn't nullable"):
        pack([{"addr": None}], s)
    with pytest.raises(ValueError, match="unknown value 'Z'"):
        pack([{"k": "Z"}], Schema(Enum("k", ["A", "B"])))
    with pytest.raises(ValueError, match="float32"):
        pack([{"f": 0.1}], Schema(Float("f", bits=32)))


def test_row_shape_errors():
    s = Schema(Uint("a"), Uint("b"))
    with pytest.raises(ValueError, match="row 1: missing column 'b'"):
        pack([{"a": 1, "b": 2}, {"a": 1}], s)
    with pytest.raises(ValueError, match="unexpected column"):
        pack([{"a": 1, "b": 2, "c": 3}], s)
    with pytest.raises(ValueError, match="different lengths"):
        pack({"a": [1, 2], "b": [1]}, s)
    with pytest.raises(ValueError, match="don't match schema"):
        pack({"a": [1]}, s)


def test_schema_validation():
    with pytest.raises(ValueError, match="pack=True requires bits"):
        Uint("a", pack=True)
    with pytest.raises(ValueError, match="repeat"):
        Enum("a", ["x"], repeat=True)
    with pytest.raises(ValueError, match="duplicate"):
        Enum("a", ["x", "x"])
    with pytest.raises(ValueError, match="Duplicate column"):
        Schema(Str("a"), Int("a"))
    with pytest.raises(ValueError, match="at least one"):
        Enum("a", [])
    with pytest.raises(ValueError, match="32 or 64"):
        Float("a", bits=16)
    with pytest.raises(TypeError):
        Uint("a", delta=True, bogus=1)


def test_schema_json_and_repr_roundtrip():
    assert Schema.from_json(SCHEMA.to_json()) == SCHEMA
    assert Schema.from_json(SCHEMA.to_json(indent=2)) == SCHEMA
    ns = {n: globals()[n] for n in ("Schema", "Uint", "Int", "Enum", "Category", "Str")}
    assert eval(repr(SCHEMA), ns) == SCHEMA
    with pytest.raises(ValueError, match="Unknown column type"):
        Schema.from_dict({"columns": [{"type": "nope", "name": "a"}]})


def test_corrupt_blobs():
    blob = pack(ROWS, SCHEMA, compress=None)
    with pytest.raises(ValueError, match="magic"):
        unpack(b"nope!")
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(blob[:-4])
    with pytest.raises(ValueError, match="version"):
        unpack(blob[:3] + b"\x09" + blob[4:])
    with pytest.raises(ValueError, match="Corrupt"):
        unpack(pack(ROWS, SCHEMA, compress="zlib")[:12])


def test_unknown_compress():
    with pytest.raises(ValueError, match="Unknown compress"):
        pack(ROWS, SCHEMA, compress="gzip")


def test_missing_brotli_message(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "brotli", None)
    blob = pack(ROWS, SCHEMA)  # auto falls back to zlib
    assert unpack(blob) == ROWS
    with pytest.raises(ImportError, match=r"rpkbin\[table\]"):
        pack(ROWS, SCHEMA, compress="brotli")


def test_category_warns_on_high_cardinality():
    rows = [{"s": f"v{i}"} for i in range(40)]
    with pytest.warns(UserWarning, match="distinct"):
        pack(rows, Schema(Category("s")))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        pack(rows)  # infer must not emit warnings
    with pytest.raises(TypeError):
        pack([{"s": 1}, {"s": "a"}], Schema(Category("s")))


# ----------------------------------------------------------------- infer

def test_infer_picks_sensible_types():
    rows = [
        {"mod": "m0", "addr": 100 + i, "flag": bool(i % 2), "t": "RW" if i % 3 else "RO",
         "x": None if i % 4 == 0 else float(i), "neg": i - 50, "blob": b"ab"}
        for i in range(100)
    ]
    # The quick baseline has a stable per-column report. Whole-table selection
    # may legitimately pick a different type when its final blob is smaller.
    schema, rep = infer(rows, report=True, optimize=False)
    kinds = [type(c) for c in schema]
    assert kinds[:3] == [Category, Uint, Bool] and kinds[4:] == [Float, Int, Bytes]
    assert kinds[3] in (Str, Category)  # either is legitimate for 2 repeating strings
    assert schema["x"].nullable
    assert rep["addr"][0][0] == schema["addr"]  # report is sorted, best first
    assert [s for _, s in rep["addr"]] == sorted(s for _, s in rep["addr"])
    assert unpack(pack(rows, schema, verify=True)) == rows
    assert unpack(pack(rows)) == rows


def test_infer_errors():
    with pytest.raises(TypeError, match="mixed"):
        infer([{"a": 1}, {"a": "x"}])
    with pytest.raises(ValueError, match="empty"):
        infer([])
    assert type(infer([{"a": None}, {"a": None}])["a"]) is Str


def test_explain_runs():
    text = explain(ROWS, SCHEMA, compress="zlib")
    assert "module" in text and "total: 3 rows" in text
    assert "total" in explain(ROWS)


# ------------------------------------------------------------------ fuzz

def _random_table(rng):
    kinds = ["uint", "int", "bool", "float", "str", "bytes", "cat", "enum"]
    cols, schema_cols = [], []
    for i in range(rng.randint(1, 8)):
        kind = rng.choice(kinds)
        nullable = rng.random() < 0.4
        name = f"c{i}"
        flags = {"nullable": nullable}
        if kind in ("uint", "int", "float", "str", "bytes") and rng.random() < 0.5:
            flags["repeat"] = True
        if kind == "uint":
            bits = rng.randint(1, 40)
            col = Uint(name, bits=bits, delta=rng.random() < 0.4, **flags)
            def gen(bits=bits):
                return rng.randrange(1 << bits)
        elif kind == "int":
            col = Int(name, delta=rng.random() < 0.5, **flags)
            def gen():
                return rng.randint(-10**6, 10**6)
        elif kind == "bool":
            flags.pop("repeat", None)
            col, gen = Bool(name, **flags), lambda: rng.random() < 0.5
        elif kind == "float":
            col, gen = Float(name, **flags), lambda: rng.choice([0.0, -0.0, 1.5, rng.random(), 1e300])
        elif kind == "str":
            col, gen = Str(name, **flags), lambda: rng.choice(["", "a", "é中", "x" * rng.randint(0, 300)])
        elif kind == "bytes":
            col, gen = Bytes(name, **flags), lambda: bytes(rng.randrange(256) for _ in range(rng.randint(0, 5)))
        elif kind == "cat":
            col, gen = Category(name, **flags), lambda: rng.choice(["a", "b", "c", "dd"])
        else:
            vals = rng.sample(range(100), rng.randint(1, 20))
            col, gen = Enum(name, vals, **flags), lambda vals=vals: rng.choice(vals)
        schema_cols.append(col)
        cols.append((name, gen, nullable))
    n = rng.randint(0, 40)
    rows = []
    for _ in range(n):
        rows.append({nm: (None if nl and rng.random() < 0.3 else g()) for nm, g, nl in cols})
    return rows, Schema(*schema_cols)


@pytest.mark.parametrize("seed", range(150))
def test_fuzz_roundtrip(seed):
    rng = random.Random(seed)
    rows, schema = _random_table(rng)
    blob = pack(rows, schema, compress=rng.choice(["zlib", None]), embed_schema=rng.random() < 0.5)
    got = unpack(blob, schema)  # passing the schema is also fine when embedded
    assert len(got) == len(rows)
    for a, b in zip(got, rows):
        for k in b:
            if isinstance(b[k], float):
                assert struct.pack(">d", a[k]) == struct.pack(">d", b[k])
            else:
                assert a[k] == b[k]
