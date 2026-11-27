# `rpkbin.tablepack`

[繁體中文](../../zh/tablepack/README.md) · [Documentation Index](../README.md) · [Usage Scenarios](scenarios.md) · [API Reference](reference.md)

`rpkbin.tablepack` packs tabular rows (`list[dict]`) or column sequences (`dict[str, sequence]`) into compact `bytes` blobs using a typed column schema and restores the original values.

TablePack is a pure library with no file I/O: you pass rows or column sequences and get `bytes` back.

## Installation

```bash
# Core install: compresses with zlib from the Python standard library
pip install -e .

# Recommended: adds brotli >= 1.2 for TablePack's bounded-output decoder
pip install -e ".[table]"
```

Without `brotli >= 1.2`, `compress="auto"` (the default) falls back to `zlib`. Explicit `compress="brotli"` requires `brotli >= 1.2`. The selected codec is stored in the blob header, so reading a brotli blob requires `brotli >= 1.2` on the decoding machine.

## Syntax

```python
blob = pack(data, schema=None, *, embed_schema=True, compress="auto", verify=False)
rows = unpack(blob, schema=None, *, as_columns=False, limits=DecodeLimits())
row_iter = iter_unpack(blob, schema=None, *, limits=DecodeLimits())
schema = infer(data, *, report=False, compress="auto", embed_schema=True, optimize=True)
summary = explain(data, schema=None, *, compress="auto")
```

## Description

- `blob = pack(data, schema=None, ...)` encodes rows or equal-length column sequences into deterministic bytes; when `schema` is omitted, `infer` chooses and embeds a schema automatically.
- `rows = unpack(blob, schema=None, ...)` decodes a `bytes`, `bytearray`, or `memoryview` blob back into `list[dict]` (or `dict[str, list]` with `as_columns=True`).
- `row_iter = iter_unpack(blob, schema=None, ...)` returns a generator that decodes columns and validates the blob when iteration begins, yielding one row dict at a time.
- `schema = infer(data, ...)` ranks per-column zlib sizes and scores up to eight whole-table candidates to propose a `Schema`.
- `summary = explain(data, schema=None, ...)` formats a per-column encoding and isolated byte-size table alongside the whole-table total.

## Examples

### 1. Minimal Example: Pack and Unpack Without a Schema

Pack 200 register-like rows with no manual schema; TablePack infers and embeds the schema inside the blob.

```python
import json
from rpkbin.tablepack import pack, unpack

rows = [
    {"module": "uart" if i < 120 else "spi", "addr": 0x1000 + 4 * i,
     "access": ("RW", "RO", "W1C")[i % 3], "reset": None if i % 4 == 0 else i % 16}
    for i in range(200)
]

blob = pack(rows)
assert unpack(blob) == rows

as_json = json.dumps(rows).encode()
print(f"JSON {len(as_json)} bytes -> blob {len(blob)} bytes")
print(unpack(blob)[1])
```

Output (with `brotli >= 1.2` installed; `zlib` sizes differ slightly):
```text
JSON 12596 bytes -> blob 183 bytes
{'module': 'uart', 'addr': 4100, 'access': 'RO', 'reset': 1}
```

### 2. Progressive Example: Explicit Schema, `infer`, and `explain`

Define column encodings explicitly with `Schema`, inspect the byte breakdown with `explain()`, and compare with `infer()`.

```python
from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack, infer, explain

rows = [
    {"module": "uart", "addr": 0x10, "kind": "RW", "note": None},
    {"module": "uart", "addr": 0x14, "kind": "RO", "note": "status"},
    {"module": "spi", "addr": 0x40, "kind": "W1C", "note": None},
]

schema = Schema(
    Str("module", repeat=True),
    Uint("addr", bits=16, delta=True),
    Enum("kind", ["RW", "RO", "W1C"]),
    Str("note", nullable=True),
)

blob = pack(rows, schema)
assert unpack(blob) == rows
print("Row 1:", unpack(blob)[1])
print("Inferred:", repr(infer(rows)))
print(explain(rows, schema, compress="zlib"))
```

Output:
```text
Row 1: {'module': 'uart', 'addr': 20, 'kind': 'RO', 'note': 'status'}
Inferred: Schema(
    Str('module'),
    Uint('addr'),
    Str('kind'),
    Str('note', nullable=True),
)
column  encoding        bytes  share
module  str+repeat         27    30%
addr    uint+delta         18    20%
kind    enum(3)            18    20%
note    str+nullable       25    28%
total: 3 rows -> 37 bytes (compress=zlib, schema not embedded)
```

## Tips and Limitations

- **Input shapes**: `pack()` accepts `list[dict]` (any iterable of mappings) or `dict[str, sequence]` (equal-length column sequences). `str`, `bytes`, `bytearray`, and `memoryview` are rejected as column containers (`TypeError`), though `bytes`, `bytearray`, and `memoryview` are accepted as cell values in `Bytes` columns. Every row must contain the exact columns of the schema.
- **Embedded schema vs. smallest output**: By default `embed_schema=True` stores the schema JSON inside the blob so `unpack(blob)` needs no extra arguments. For smaller blobs, pass `embed_schema=False` and pass the matching schema to `unpack(blob, schema)`. See [Scenario 4](scenarios.md#scenario-4-share-one-schema-and-skip-embedding-it).
- **Strict typing**: Values are checked, not coerced (`True` is rejected by `Uint` and `Int`; `"7"` is rejected by `Int`; negative integers are rejected by `Uint`). Errors include row and column names. See [Scenario 8](scenarios.md#scenario-8-read-and-fix-errors).
- **Decoding limits**: Readers enforce configurable `DecodeLimits` for rows, cells, payload bytes, schema bytes, columns, and integer varint length to bound parser amplification (they are not an operating-system memory cap). See [`DecodeLimits`](reference.md#decodelimits).
- **Compatibility**: Requires Python `>= 3.11` (`pyproject.toml`). Pure Python with no network calls; examples were verified on Windows with Python 3.13.

## See Also

- [Usage Scenarios](scenarios.md)
- [API Reference](reference.md)
- [Documentation Index](../README.md)
