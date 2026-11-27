# `rpkbin.tablepack` Reference

Schema-driven compact binary codec packing tabular rows or columnar dicts/sequences directly into `bytes` without filesystem I/O.

## Public API

- `Schema(*columns: Column)` — Methods: `.to_dict()`, `.to_json(indent=None)`, `.from_dict(d)`, `.from_json(text)`; properties `.columns`, `.names`.
- Column types (all accept `*, nullable=False, repeat=False`):
  - `Uint(name, *, bits=None, varint=False, delta=False, pack=False, ...)`
  - `Int(name, *, delta=False, ...)`
  - `Bool(name, ...)`
  - `Float(name, *, bits=64, ...)` (`32` or `64`)
  - `Bytes(name, ...)`, `Str(name, ...)`
  - `Enum(name, values=(), ...)`
  - `Category(name, ...)`
  - Column methods: `.describe()`, `.to_dict()`.
- `pack(data, schema: Schema | None = None, *, embed_schema: bool = True, compress: str | None = 'auto', verify: bool = False) -> bytes`
- `unpack(blob: bytes, schema: Schema | None = None, *, as_columns: bool = False, limits: DecodeLimits = DecodeLimits(...))`
- `iter_unpack(blob: bytes, schema: Schema | None = None, *, limits: DecodeLimits = DecodeLimits(...)) -> Iterator[dict[str, Any]]` (note: validation runs lazily when iterating/calling `next()`).
- `infer(data, *, report: bool = False, compress: str | None = 'auto', embed_schema: bool = True, optimize: bool = True)`
- `explain(data, schema: Schema | None = None, *, compress: str | None = 'auto') -> str`
- `DecodeLimits(max_rows=1000000, max_cells=10000000, max_payload_bytes=67108864, max_schema_bytes=1048576, max_columns=4096, max_integer_bytes=8192)`

## Gotchas

- `pack(..., embed_schema=False)` omits schema metadata from `blob`; callers MUST pass the matching `schema` to `unpack()` / `iter_unpack()`.
- Passing a `Sequence` / `bytearray` / `memoryview` / column dict is supported; invalid `blob` types raise `TypeError` (`unpack(123)`), while truncated/corrupt payloads raise `ValueError`.
- Empty tables round-trip cleanly with inferred or explicit schemas; install `rpkbin[table]` if `compress="brotli"` is explicitly requested.

## Recipes

### 1. Explicit Schema Pack & Streaming Unpack

```python
from rpkbin.tablepack import Bool, Category, Schema, Uint, iter_unpack, pack, unpack

schema = Schema(
    Uint("cycle", delta=True, varint=True),
    Category("op"),
    Bool("ok", nullable=True),
)
rows = [
    {"cycle": 100, "op": "LD", "ok": True},
    {"cycle": 104, "op": "ST", "ok": None},
    {"cycle": 109, "op": "LD", "ok": False},
]
blob = pack(rows, schema, verify=True)
assert unpack(blob) == rows
assert list(iter_unpack(blob)) == rows
```

### 2. Schema Inference & Size Diagnostic Report

```python
from rpkbin.tablepack import explain, infer, pack

cols = {"addr": [0, 4, 8, 12], "tag": ["a", "b", "a", "c"]}
schema, report = infer(cols, report=True)
blob = pack(cols, schema)
assert len(blob) > 0 and "addr" in report
print(explain(cols, schema))
```
