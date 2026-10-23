# TablePack Quickstart: Turn a Table into Compact Bytes and Back

[繁體中文](../../zh/tablepack/README.md) · [Documentation Index](../README.md) · [Usage Scenarios](scenarios.md) · [API Reference](reference.md)

TablePack (`rpkbin.tablepack`) packs a table of rows (a list of dicts) into a small `bytes` blob and restores it exactly.

It solves this problem: **you have regular, repetitive tabular data (register maps, test vectors, lookup tables, logs) and want to store or ship it compactly, without hand-writing a new binary codec for every layout.** You describe each column once (or let TablePack infer it), and the library picks the bit-level packing.

It is a pure library, like MapBV: it never opens files. You hand it rows (from Excel, CSV, a database, or code) and get `bytes` back; where those bytes go is up to you.

> **Compatibility.** Requires Python 3.11 or newer (from `pyproject.toml`). The examples in these pages were run on Windows with Python 3.13; the code is pure Python, but other platforms were not exercised.

---

## Installation

```bash
# Core install: TablePack works, compressing with zlib from the standard library
pip install -e .

# Recommended: adds brotli >= 1.2, used by TablePack when its bounded-output API is available
pip install -e ".[table]"
```

Without a compatible brotli, `compress="auto"` (the default) silently falls back to zlib. Explicit `compress="brotli"` requires brotli >= 1.2. The compressor is recorded in the blob header, so a brotli blob can only be decoded where compatible brotli is installed.

---

## 5-Minute Quickstart

### 1. Zero configuration: pack rows without a schema

```python
import json
from rpkbin.tablepack import pack, unpack

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

blob = pack(rows)                         # no schema given: inferred, then embedded in the blob
assert unpack(blob) == rows               # the blob describes itself, nothing else is needed

as_json = json.dumps(rows).encode()
print(f"JSON {len(as_json)} bytes -> blob {len(blob)} bytes")
print(unpack(blob)[1])
```

Output (with brotli installed; the blob size differs slightly with zlib):

```text
JSON 12596 bytes -> blob 183 bytes
{'module': 'uart', 'addr': 4100, 'access': 'RO', 'reset': 1}
```

`unpack()` returns a list of dicts in the original row order. `None` values, integers, strings, and booleans come back with the same types.

### 2. Describe the columns yourself

When you know your data, a schema gives you control and smaller output. Each column is a typed object; mistakes (such as `Enum(..., repeat=True)`) are rejected when the schema is built, with a message naming the column.

```python
from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack

schema = Schema(
    Str("module", repeat=True),             # often equal to the previous row: repeats cost 1 bit
    Uint("addr", bits=16, delta=True),      # store the step from the previous row, not the address
    Enum("kind", ["RW", "RO", "W1C"]),      # a known set of values: costs 2 bits per row
    Str("note", nullable=True),             # may be None: costs 1 bit when absent
)
rows = [
    {"module": "uart", "addr": 0x10, "kind": "RW", "note": None},
    {"module": "uart", "addr": 0x14, "kind": "RO", "note": "status"},
    {"module": "spi", "addr": 0x40, "kind": "W1C", "note": None},
]

blob = pack(rows, schema)
assert unpack(blob) == rows               # schema is embedded by default
print(unpack(blob)[1])
```

Output:

```text
{'module': 'uart', 'addr': 20, 'kind': 'RO', 'note': 'status'}
```

### 3. Not sure which options to use?

This step reuses `rows` from step 2.

```python
from rpkbin.tablepack import infer, explain

schema = infer(rows)       # scores the whole table with the target compressor and embedded schema
print(schema)              # prints Schema(...) code you can paste and tweak
print(explain(rows))       # bytes spent per column
```

`infer` starts with a fast per-column zlib baseline, then measures up to eight whole-table candidates using the requested compressor and schema-embedding setting. This is a bounded search, not a guarantee of the global minimum. The baseline is always included and wins ties. `report=True` still reports the per-column zlib ranking; the chosen schema need not be its first entry.

See [Scenario 3](scenarios.md#scenario-3-see-where-the-bytes-go) for how to read this output.

---

## Key Points and Common Pitfalls

> [!NOTE]
> ### 1. Input shapes
> `pack()` accepts `list[dict]` (one dict per row) or `dict[str, sequence]` (one equal-length sequence per column, convenient when your data already lives in arrays; TablePack itself does not depend on NumPy or pandas). Every row must have exactly the schema's columns: a missing or extra key raises `ValueError` naming the row.

> [!IMPORTANT]
> ### 2. Embedded schema vs. smallest output
> By default the schema is stored inside the blob (a few dozen bytes) so `unpack(blob)` needs no other information. For the smallest output pass `embed_schema=False`, then give the same schema to `unpack(blob, schema)`. See [Scenario 4](scenarios.md#scenario-4-share-one-schema-and-skip-embedding-it).

> [!WARNING]
> ### 3. Types are checked, not coerced
> Values must match the column type: `True` is not accepted by a `Uint`, `"7"` is not accepted by an `Int`, and a negative number is not accepted by a `Uint`. Errors tell you the row and column. See [Scenario 8](scenarios.md#scenario-8-read-and-fix-errors).

> ### 4. Decoding limits
> Readers enforce configurable limits for rows, cells, payload and schema bytes, columns, and integer varint length. These bound decoded work and payload expansion; they are not an operating-system memory cap. See the [API reference](reference.md#decoding-limits).

---

## Next Steps

- **[Usage Scenarios](scenarios.md)**: tuning columns, comparing alternatives, column-oriented data, schema sharing, compression choices, build-time verification, and errors.
- **[API Reference](reference.md)**: every function, column type, option, default, exception, and the binary format.
