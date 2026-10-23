# TablePack Usage Scenarios

[繁體中文](../../zh/tablepack/scenarios.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [API Reference](reference.md)

Scenarios run from a single minimal operation to composition and tuning. **Each code block is self-contained and can be run on its own** unless the text says otherwise. Sizes in outputs are example values: the schema-embedding and `compress="zlib"` ones are reproducible with the standard library; anything using the default compressor depends on whether brotli is installed.

---

## Scenario 1: Describe a table with every column type

**Task:** pack rows containing signed numbers, booleans, floats, and binary data, and get identical values back.

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

Columns are looked up by name, and the order of columns in the schema is the key order of the returned dicts. `Uint` is for values `>= 0`; use `Int` when negatives are possible. The full list of types and options is in the [reference](reference.md#column-types).

**Next:** you do not have to choose options by hand: see Scenario 2.

---

## Scenario 2: Let `infer` propose a schema, then save it

**Task:** get a first schema for data you have not analysed, review it, and keep it as JSON text.

```python
from rpkbin.tablepack import Schema, infer, pack, unpack

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "kind": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

schema = infer(rows)             # advisory: review it, it does not know what your data means
print(schema)

text = schema.to_json()          # plain JSON text: save it wherever you like
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

`infer` first ranks each column's encodings by standalone zlib size, then measures up to eight whole-table candidates using the requested compressor and embedding setting. This bounded search can select a schema other than the first per-column choices; it is advisory and does not guarantee the global minimum. The baseline is always included and wins ties. `bits=13` is only the width today's maximum needs, so a later table with larger addresses would be rejected. If a column's range is a hardware fact, write that width yourself. `infer` never proposes `Enum` because it cannot know the value set is closed; it may propose `Category` (learned value set) or plain `Str`.

`print(schema)` produces Python code you can paste into your own script and edit.

---

## Scenario 3: See where the bytes go

**Task:** find which columns dominate the size, and compare the alternatives `infer` considered.

```python
from rpkbin.tablepack import explain, infer

rows = [
    {"block": f"blk{i // 8:02d}", "offset": 4 * (i % 8), "width": 1 + i % 4,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 5 else 0xFF}
    for i in range(160)
]
print(explain(rows, compress="zlib"))

schema, report = infer(rows, report=True)
for col, size in report["offset"]:           # every candidate for one column, smallest first
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

The `bytes` figure for each column comes from packing that column alone, so the figures are a guide rather than an exact split: columns share flag bits, and each standalone blob carries its own header and compressor overhead, so here the total is smaller than the sum of the column sizes. `share` is relative to that sum. The report remains the per-column zlib ranking even though `infer` uses whole-table scoring for its final schema.

`describe()` shows the encoding of a column (`uint5` is a fixed 5-bit width, `+pack` stores it in the shared flag bits).

---

## Scenario 4: Share one schema and skip embedding it

**Task:** a producer script writes blobs and a consumer reads them; keep the blobs as small as possible.

```python
from rpkbin.tablepack import infer, pack, unpack, Schema

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "kind": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

# Producer: infer for the target codec and omit the schema from each blob.
schema = infer(rows, compress="brotli", embed_schema=False)
schema_text = schema.to_json()       # store this as an application-versioned artifact
small = pack(rows, schema, embed_schema=False, compress="brotli")
print(len(small))

# Consumer: load the schema artifact that matches the producer's application version.
consumer_schema = Schema.from_json(schema_text)
print(unpack(small, consumer_schema) == rows)
```

Output:

```text
84
True
```

The producer selects a schema by measuring a bounded set of whole-table candidates with the target codec and without embedding the schema. It stores that schema separately; the consumer must use the matching application-versioned schema. Version 1 has no schema fingerprint, so a mismatched external schema is not guaranteed to be detected. Embedding remains enabled by default and makes a blob self-describing.

If you pass a schema to `unpack` for a blob that **does** embed one, the two must be equal, otherwise `ValueError` is raised.

---

## Scenario 5: Column-oriented data and row streaming

**Task:** your data is already one list per column, or you want to process rows one at a time.

```python
from rpkbin.tablepack import pack, unpack, iter_unpack

columns = {"addr": [0x10, 0x14, 0x18], "name": ["ctrl", "status", "data"], "ro": [False, True, True]}
blob = pack(columns)                       # dict of equal-length sequences
print(unpack(blob, as_columns=True) == columns)
print(unpack(blob)[0])                     # list of row dicts by default

for row in iter_unpack(blob):              # one dict at a time instead of a full list
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

`iter_unpack` saves building the whole `list[dict]`; the blob is still bounded-decompressed and decoded column by column before yielding the first row. Reader limits constrain this work, but do not provide an operating-system memory cap.

---

## Scenario 6: Tune columns and measure the effect

**Task:** shrink a table by choosing better column options, and verify the gain with numbers.

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
    Category("module"),                         # value set learned from the data
    Uint("addr", bits=16, delta=True),          # store the step, not the address
    Uint("msb", bits=4, pack=True),             # tiny field: live in the shared flag bits
    Uint("lsb", bits=4, pack=True),
    Enum("access", ["RW", "RO", "W1C"]),        # known up front: no dictionary stored
    Uint("reset", bits=8, nullable=True),       # None costs 1 bit, no payload
)
for label, s in (("naive", naive), ("tuned", tuned)):
    print(label, len(pack(rows, s, compress="zlib", embed_schema=False)))
```

Output:

```text
naive 405
tuned 171
```

Rules of thumb (the numbers above come from `explain`/`pack`, not from guessing):

| Data looks like | Use |
| :--- | :--- |
| Counters, addresses, sorted IDs | `Uint(..., delta=True)` or `Int(..., delta=True)` |
| Same value as the row above (merged cells, grouped rows) | `repeat=True` on `Str`/`Uint`/`Int`/`Float`/`Bytes` |
| A few distinct strings, set known in advance | `Enum([...])` |
| A few distinct values, set not known in advance | `Category` |
| Narrow fields (a few bits) | `Uint(bits=N, pack=True)` |
| Sometimes missing | `nullable=True` (1 flag bit; absent values store nothing) |

`Category` warns (`UserWarning`) when a column with at least 16 rows has more than half as many distinct values as rows; that usually means `Str` is the better choice.

---

## Scenario 7: Choose the compressor and verify at build time

**Task:** compare compressors, and make a build script fail loudly if a table would not round-trip.

```python
from rpkbin.tablepack import Schema, Uint, Str, pack, unpack

schema = Schema(Str("k"), Uint("v"))
rows = [{"k": f"key{i % 7}", "v": i * i} for i in range(300)]
for c in ("auto", "brotli", "zlib", "lzma", None):
    print(f"{str(c):7s}", len(pack(rows, schema, compress=c)))
blob = pack(rows, schema, compress="zlib", verify=True)   # decodes and compares before returning
assert unpack(blob) == rows                               # codec is read from the header
```

Output with compatible brotli >= 1.2 installed (`pip install -e ".[table]"`); `auto` uses brotli here, and the exact sizes depend on the compressor versions:

```text
auto    765
brotli  765
zlib    808
lzma    829
None    2335
```

Without compatible brotli, `"auto"` uses zlib and `compress="brotli"` raises `ImportError` with an upgrade/install hint. Decoding a brotli blob also requires compatible brotli on the reading machine.

`verify=True` raises `ValueError("verify failed: ...")` if the decoded data differs from the input. It costs one extra decode, which is acceptable in a build script.

---

## Scenario 8: Read and fix errors

**Task:** understand what the validation errors say.

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

Row numbers start at 0. Wrong *types* (for example `True` for a `Uint`, or a `str` for an `Int`) raise `TypeError` with the same row/column prefix; value problems raise `ValueError`. When the problem is the schema rather than the data (for example `Uint("a", pack=True)` without `bits`), the error is raised when `Schema(...)` is built.

---

## Next Steps

- **[API Reference](reference.md)**: exact signatures, defaults, accepted value types, exceptions, and the blob format.
