# TablePack Usage Scenarios

[繁體中文](../../zh/tablepack/scenarios.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [API Reference](reference.md)

Eight self-contained scenarios progressing from basic typed columns to schema inference, size breakdown, external schemas, streaming, column tuning, compression, and error handling. Sizes shown in outputs reflect `brotli >= 1.2` where `"auto"` or `"brotli"` is used; `compress="zlib"` and `compress=None` sizes are reproducible with the Python standard library.

## Scenario 1: Describe a table with every column type

Pack rows containing signed numbers, booleans, floats, and binary data, then restore the exact values.

```python
from rpkbin.tablepack import Schema, Uint, Int, Bool, Float, Bytes, Str, pack, unpack

schema = Schema(
    Str("sensor"),
    Int("temp_c10"),                 # signed integer, e.g. tenths of a degree
    Bool("valid"),
    Float("volt", bits=32),          # 32-bit float: only values exactly representable in float32 are accepted
    Bytes("raw", nullable=True),     # arbitrary binary payload, may be missing
)
rows = [
    {"sensor": "s1", "temp_c10": -125, "valid": True, "volt": 3.25, "raw": b"\x01\x02"},
    {"sensor": "s2", "temp_c10": 300, "valid": False, "volt": 0.5, "raw": None},
]
blob = pack(rows, schema)
back = unpack(blob)
assert back == rows
print(back[0])
```

Output:
```text
{'sensor': 's1', 'temp_c10': -125, 'valid': True, 'volt': 3.25, 'raw': b'\x01\x02'}
```

Columns are matched by name, and the schema column order sets the key order in returned dicts. Use `Uint` for values `>= 0` and `Int` when negatives are possible. See [Column and Column Types](reference.md#column-and-column-types).

## Scenario 2: Let `infer` propose a schema, then save it

Generate an initial schema for unanalyzed data, inspect the Python output, and serialize it to JSON.

```python
from rpkbin.tablepack import Schema, infer, pack, unpack

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "kind": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

schema = infer(rows)             # advisory: review before locking it in production
print(schema)

text = schema.to_json()
assert Schema.from_json(text) == schema
assert unpack(pack(rows, schema)) == rows
print(len(text), "characters of JSON")
```

Output:
```text
Schema(
    Str('module', repeat=True),
    Uint('addr', bits=13, delta=True),
    Str('kind'),
    Uint('reset', nullable=True, bits=4, delta=True),
)
209 characters of JSON
```

`infer` ranks per-column zlib candidates first, then scores up to eight whole-table candidates using the target compressor and embedding flag (the baseline is always included and wins ties). Notice `bits=13` reflects only the current sample's maximum; if a register address can reach 16 bits in hardware, widen `bits=16` manually. `infer` never proposes `Enum` because a sample cannot prove a closed value set.

## Scenario 3: See where the bytes go

Inspect each column's byte share with `explain()` and check all candidates ranked by `infer(..., report=True)`.

```python
from rpkbin.tablepack import explain, infer

rows = [
    {"block": f"blk{i // 8:02d}", "offset": 4 * (i % 8), "width": 1 + i % 4,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 5 else 0xFF}
    for i in range(160)
]
print(explain(rows, compress="zlib"))

schema, report = infer(rows, report=True)
for col, size in report["offset"]:
    print(f"{size:4d} B  {col.describe()}")
```

Output:
```text
column  encoding               bytes  share
block   str                       69    40%
offset  uint+delta                23    13%
width   uint                      22    13%
access  str                       31    18%
reset   uint8+pack+nullable       24    14%
total: 160 rows -> 110 bytes (compress=zlib, schema not embedded)
  18 B  uint+delta
  21 B  uint
  21 B  uint5
  21 B  uint5+pack
  31 B  category
```

Each column's `bytes` value comes from packing that column alone (so it includes per-blob header and compressor overhead), while `share` divides by the sum of those standalone sizes. `col.describe()` summarizes the encoding (`uint5` is fixed 5-bit width; `+pack` stores values in shared flag bits).

## Scenario 4: Share one schema and skip embedding it

Produce minimal blobs by storing one application-versioned schema outside the blobs.

```python
from rpkbin.tablepack import infer, pack, unpack, Schema

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "kind": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

schema = infer(rows, compress="brotli", embed_schema=False)
schema_text = schema.to_json()
small = pack(rows, schema, embed_schema=False, compress="brotli")
print(len(small))

consumer_schema = Schema.from_json(schema_text)
print(unpack(small, consumer_schema) == rows)
```

Output:
```text
84
True
```

Version 1 blobs carry no schema fingerprint, so consumers reading unembedded blobs must load the matching external schema. Passing a different `schema` to `unpack()` on a blob that *does* embed one raises `ValueError`.

## Scenario 5: Column-oriented data and row streaming

Pack from `dict[str, sequence]` directly and iterate decoded rows with `iter_unpack()`.

```python
from rpkbin.tablepack import pack, unpack, iter_unpack

columns = {"addr": [0x10, 0x14, 0x18], "name": ["ctrl", "status", "data"], "ro": [False, True, True]}
blob = pack(columns)
print(unpack(blob, as_columns=True) == columns)
print(unpack(blob)[0])

for row in iter_unpack(blob):
    print(row["name"], hex(row["addr"]))
```

Output:
```text
True
{'addr': 16, 'name': 'ctrl', 'ro': False}
ctrl 0x10
status 0x14
data 0x18
```

`iter_unpack()` avoids allocating the full `list[dict]` while still decoding all columns before yielding row 0. Validation and decompression happen when iteration starts, not when `iter_unpack()` is called.

## Scenario 6: Tune columns and measure the effect

Shrink output size by selecting column options matched to your data patterns.

```python
from rpkbin.tablepack import Schema, Uint, Enum, Category, Str, pack

rows = [
    {"module": "dma" if i < 100 else "gpio", "addr": 0x2000 + 4 * i, "msb": i % 16, "lsb": 0,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 3 else 0}
    for i in range(200)
]

naive = Schema(Str("module"), Uint("addr", bits=16), Uint("msb", bits=8), Uint("lsb", bits=8),
               Str("access"), Uint("reset", bits=8, nullable=True))
tuned = Schema(
    Category("module"),
    Uint("addr", bits=16, delta=True),
    Uint("msb", bits=4, pack=True),
    Uint("lsb", bits=4, pack=True),
    Enum("access", ["RW", "RO", "W1C"]),
    Uint("reset", bits=8, nullable=True),
)
for label, s in (("naive", naive), ("tuned", tuned)):
    print(label, len(pack(rows, s, compress="zlib", embed_schema=False)))
```

Output:
```text
naive 405
tuned 171
```

| Pattern | Recommended Column Option |
| :--- | :--- |
| Counters, addresses, monotonic IDs | `Uint(..., delta=True)` or `Int(..., delta=True)` |
| Repeated consecutive values | `repeat=True` on `Str`, `Uint`, `Int`, `Float`, or `Bytes` |
| Small closed set of known values | `Enum([...])` |
| Small open set of distinct values | `Category` |
| Narrow fields (few bits) | `Uint(bits=N, pack=True)` |
| Bounded integers mostly small in value | `Uint(bits=N, varint=True)` |
| Sparse `None` entries | `nullable=True` |

`Category` emits a `UserWarning` if a table has 16 or more rows and distinct values exceed half the row count (`Str` is usually better there).

## Scenario 7: Choose the compressor and verify at build time

Compare codecs and use `verify=True` in build scripts to catch any round-trip discrepancy before writing artifacts.

```python
from rpkbin.tablepack import Schema, Uint, Str, pack, unpack

schema = Schema(Str("k"), Uint("v"))
rows = [{"k": f"key{i % 7}", "v": i * i} for i in range(300)]
for c in ("auto", "brotli", "zlib", "lzma", None):
    print(f"{str(c):7s}", len(pack(rows, schema, compress=c)))
blob = pack(rows, schema, compress="zlib", verify=True)
assert unpack(blob) == rows
```

Output (with `brotli >= 1.2` installed):
```text
auto    765
brotli  765
zlib    808
lzma    829
None    2335
```

Without compatible brotli, `"auto"` uses `zlib` and `compress="brotli"` raises `ImportError`. `verify=True` runs one extra decode pass and raises `ValueError("verify failed: ...")` if any column differs.

## Scenario 8: Read and fix errors

Inspect exact error types and messages produced by schema and value validation.

```python
from rpkbin.tablepack import Schema, Uint, Enum, Float, pack

s = Schema(Uint("addr", bits=12), Enum("kind", ["RW", "RO"]))
cases = [
    ([{"addr": 4095, "kind": "RW"}, {"addr": 4096, "kind": "RW"}], s),
    ([{"addr": 1, "kind": "XX"}], s),
    ([{"addr": 1}], s),
    ([{"addr": 1, "kind": "RW", "extra": 0}], s),
    ([{"v": 0.1}], Schema(Float("v", bits=32))),
]
for rows, schema in cases:
    try:
        pack(rows, schema)
    except (ValueError, TypeError) as e:
        print(type(e).__name__ + ":", e)
```

Output:
```text
ValueError: row 1, column 'addr': 4096 doesn't fit in 12 bits
ValueError: row 0, column 'kind': unknown value 'XX'; known values: ['RW', 'RO']
ValueError: row 0: missing column 'kind'
ValueError: row 0: unexpected column(s) ['extra']
ValueError: row 0, column 'v': 0.1 isn't exactly representable as float32
```

Row indices are 0-based. Type mismatches (such as `True` for `Uint` or `str` for `Int`) raise `TypeError` with the same `row i, column '...'` prefix; out-of-range or invalid values raise `ValueError`. Schema construction errors (such as `Uint("a", pack=True)` without `bits`) fail immediately when creating `Schema(...)`.

## See Also

- [Quickstart](README.md)
- [API Reference](reference.md)
- [Documentation Index](../README.md)
