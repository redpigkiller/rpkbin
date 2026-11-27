# `rpkbin.tablepack` API Reference

[繁體中文](../../zh/tablepack/reference.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [Usage Scenarios](scenarios.md)

Technical specification for `rpkbin.tablepack`: encoding and decoding functions, `DecodeLimits`, `Schema`, `Column` types, exceptions, and the V1 binary format.

```python
from rpkbin.tablepack import (
    Schema, Column, Uint, Int, Bool, Float, Bytes, Str, Enum, Category, DecodeLimits,
    pack, unpack, iter_unpack, infer, explain,
)
```

`__all__` exports those 16 symbols. Requires Python 3.11 or newer. `brotli >= 1.2` is optional (`pip install -e ".[table]"`) and is used only when `brotli.Decompressor.can_accept_more_data` is present; otherwise `"auto"` falls back to `zlib`. Private `_`-prefixed modules are internal.

## Functions

### `pack`

#### Syntax
```python
blob = pack(data, schema=None, *, embed_schema=True, compress="auto", verify=False)
```

#### Description
Encodes a table into deterministic V1 `bytes`. Output is identical for the same input, schema, options, and compressor version.

#### Input Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `data` | `list[dict] \| Iterable[Mapping] \| dict[str, sequence]` | required | One mapping per row, or one equal-length sequence per column. `str`, `bytes`, `bytearray`, and `memoryview` are rejected (`TypeError`) as column sequences; wrap binary items in a sequence (e.g., `[bytearray(b"ab")]`) for `Bytes` columns. |
| `schema` | `Schema \| None` | `None` | Column definitions. When `None`, infers a schema with `infer` and embeds it (`data` must have at least one row and one column, and `embed_schema` must be `True`). |

#### Name-Value Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `embed_schema` | `bool` | `True` | Store schema JSON inside the blob so `unpack(blob)` needs no external schema. |
| `compress` | `str \| None` | `"auto"` | Compression backend recorded in the header: `"auto"` (`brotli >= 1.2` if available, else `zlib`), `"brotli"`, `"zlib"`, `"lzma"`, or `None`. |
| `verify` | `bool` | `False` | Decode the packed blob and compare all columns against the input (`ValueError` on mismatch). |

#### Output
Returns `bytes`.

#### Raises
- `ValueError`: missing or unexpected columns; mismatched column lengths; empty table (`[]`, `[{}]`, `{}`, `{"a": []}`) with `schema=None`; `embed_schema=False` with `schema=None`; unknown `compress`; value out of bounds, `None` in non-nullable column, unknown `Enum` value, non-exact float, or invalid UTF-8 surrogate in `Str`/`Category` (prefixed with `row i, column '...':`); `verify=True` round-trip mismatch.
- `TypeError`: wrong cell value type (`bool` for `Uint`/`Int`/`Float`/`Enum`/`Category`); non-iterable `data` or non-mapping row; non-sequence column (including `str`, `bytes`, `bytearray`, `memoryview`); mixed `str`/`int` types in `Category`.
- `AttributeError`: `schema` is neither `None` nor a `Schema` instance (unlike `unpack`/`iter_unpack`, which validate `schema` with `TypeError`).
- `ImportError`: `compress="brotli"` when `brotli >= 1.2` (with bounded-output decoder) is unavailable.

### `unpack`

#### Syntax
```python
rows = unpack(blob, schema=None, *, as_columns=False, limits=DecodeLimits())
```

#### Description
Decodes a TablePack V1 blob into rows (`list[dict]`) or columns (`dict[str, list]`).

#### Input Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `blob` | `bytes \| bytearray \| memoryview` | required | Bytes-like V1 blob produced by `pack`. |
| `schema` | `Schema \| None` | `None` | Required when the blob has no embedded schema. If the blob embeds a schema and you also pass `schema`, both must be equal. |

#### Name-Value Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `as_columns` | `bool` | `False` | Return `{column_name: list}` instead of `list[dict]`. |
| `limits` | `DecodeLimits` | `DecodeLimits()` | Reader budgets bounding payload decompression, metadata, row/cell/column counts, and integer varints. |

#### Output
Returns `list[dict[str, Any]]` in original row order (keys in schema column order), or `dict[str, list[Any]]` when `as_columns=True`. Decoded values are `int`, `float`, `bool`, `str`, `bytes`, or `None` (`bytearray`/`memoryview` cells packed in `Bytes` return as `bytes`).

#### Raises
- `TypeError`: `blob` is not `bytes`, `bytearray`, or `memoryview` (checked before allocating `bytes(blob)`, e.g., `unpack(1_000_000_000)`); `schema` is neither `None` nor a `Schema`; `limits` is not a `DecodeLimits` instance.
- `ValueError`: invalid magic bytes (`b"TPK"`), unsupported format version, corrupt or truncated data (`Corrupt or truncated tablepack blob`), any `DecodeLimits` budget exceeded (reported with the corrupt-blob prefix), missing schema on a bare blob, or passed `schema != embedded_schema`.
- `ImportError`: blob uses `brotli` compression while compatible `brotli >= 1.2` is unavailable.

### `iter_unpack`

#### Syntax
```python
row_iter = iter_unpack(blob, schema=None, *, limits=DecodeLimits())
```

#### Description
Generator version of `unpack` that yields one row dict at a time.

Because `iter_unpack` is a generator function, calling `iter_unpack(bad_blob)` returns a generator object immediately and **defers argument checks, decompression, header parsing, and `DecodeLimits` validation until iteration begins** (e.g., the first `next()`, `for` loop, or `list()`). Once iteration starts, the entire blob is decoded column by column before the first row is yielded, so it avoids allocating the full `list[dict]`, not the decoded column arrays.

#### Input Arguments
Same as `unpack` (`blob`, `schema`, and keyword-only `limits`).

#### Output
Returns `Iterator[dict[str, Any]]`. Raises the same `TypeError`, `ValueError`, and `ImportError` as `unpack` when iteration starts.

### `infer`

#### Syntax
```python
schema = infer(data, *, report=False, compress="auto", embed_schema=True, optimize=True)
schema, report = infer(data, report=True, ...)
```

#### Description
Proposes a `Schema` for `data`. First ranks each column's candidate encodings using standalone zlib level-9 size. When `optimize=True` (default), it then scores up to eight whole-table candidate schemas using `compress` and `embed_schema` (the per-column baseline is always included and wins ties). `report` always reflects the per-column zlib ranking even when whole-table optimization picks another candidate. Inference is advisory and never proposes `Enum`.

#### Input Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `data` | `list[dict] \| Iterable[Mapping] \| dict[str, sequence]` | required | Non-empty table (at least one row and one column). |

#### Name-Value Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `report` | `bool` | `False` | Return `(schema, report)` where `report[name]` is `[(Column, zlib_bytes), ...]` sorted smallest first. |
| `compress` | `str \| None` | `"auto"` | Compressor used for whole-table scoring. |
| `embed_schema` | `bool` | `True` | Include embedded schema JSON when scoring whole-table candidates. |
| `optimize` | `bool` | `True` | Set `False` to return the fast per-column zlib baseline without whole-table scoring. |

Candidate selection by non-null value type (`nullable=True` if any `None` is present; `repeat=True` tried when `> 10%` of adjacent rows match; `Category` tried when distinct count `<= 60%` of non-null count):

| Non-null Values | Candidates Considered |
| :--- | :--- |
| All `bool` (or NumPy bool) | `Bool` |
| Non-negative ints (`__index__`) | `Uint` (varint, fixed `bits=N`, `delta=True`, and `pack=True` when `bits <= 16` and `not repeat`), plus `Category` if low cardinality |
| Signed ints (with negatives) | `Int` (plain, `delta=True`), plus `Category` if low cardinality |
| `numbers.Real` (ints and floats, excl. `bool`) | `Float` (64-bit, 32-bit) |
| All `str` | `Str`, plus `Category` if low cardinality |
| All `bytes` / `bytearray` / `memoryview` | `Bytes` |
| Only `None` | `Str(nullable=True)` |

#### Output
Returns `Schema`, or `(Schema, dict[str, list[tuple[Column, int]]])` when `report=True`. Raises `ValueError` for empty tables (`[]`, `[{}]`, `{}`, `{"a": []}`) or when no candidate fits; `TypeError` for mixed/unsupported types or invalid column sequence containers.

### `explain`

#### Syntax
```python
text = explain(data, schema=None, *, compress="auto")
```

#### Description
Formats a table showing each column's `describe()` encoding, standalone packed byte count (with `embed_schema=False`), share relative to the sum of standalone column sizes, and the actual whole-table packed total. Because standalone column blobs each carry their own header and compressor overhead, individual column bytes do not sum to the total. If `schema` is omitted, `infer` runs with `compress` and `embed_schema=False` using the already normalized columns.

#### Input Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `data` | `list[dict] \| Iterable[Mapping] \| dict[str, sequence]` | required | Table data (may be empty if an explicit `schema` is passed). |
| `schema` | `Schema \| None` | `None` | Optional explicit `Schema`; inferred if omitted (requires non-empty data). |

#### Name-Value Arguments
| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `compress` | `str \| None` | `"auto"` | Compressor used for column and total size measurement. |

#### Output
Returns formatted `str`. Raises the same errors as `pack` and `infer`.

## Classes

### `DecodeLimits`

Frozen dataclass specifying reader-side resource budgets (not stored in the V1 wire format).

#### Creation
```python
limits = DecodeLimits(
    max_rows=1_000_000,
    max_cells=10_000_000,
    max_payload_bytes=64 * 1024 * 1024,
    max_schema_bytes=1024 * 1024,
    max_columns=4096,
    max_integer_bytes=8192,
)
```

Every field must be a positive plain integer (`type(v) is int and v >= 1`); `0`, negative values, `bool`, `float`, or `None` raise `ValueError`.

#### Properties
| Property | Type | Meaning |
| :--- | :--- | :--- |
| `max_rows` | `int` | Default `1_000_000`. Maximum declared row count. |
| `max_cells` | `int` | Default `10_000_000`. Maximum decoded cell count (`rows * columns`). |
| `max_payload_bytes` | `int` | Default `67_108_864` (64 MiB). Maximum decompressed payload bytes. |
| `max_schema_bytes` | `int` | Default `1_048_576` (1 MiB). Maximum embedded schema JSON bytes. |
| `max_columns` | `int` | Default `4096`. Maximum schema column count. |
| `max_integer_bytes` | `int` | Default `8192`. Maximum bytes in an integer varint. |

#### Tips and Limitations
- Limit violations raise `ValueError` prefixed with `Corrupt or truncated tablepack blob`.
- Decompression is bounded before metadata, row/cell counts, flag bits, range checks, and EOF checks. LZMA decoding enforces a fixed 128 MiB decoder memory limit (V1's 64 MiB writer dictionary remains readable).
- These limits bound parser amplification; they are not an operating-system memory cap.

### `Schema`

Ordered, immutable collection of uniquely named `Column` objects (at least one column).

#### Creation
```python
schema = Schema(*columns)
schema = Schema([col1, col2, ...])
schema = Schema.from_dict(d)
schema = Schema.from_json(text)
```

#### Properties
| Property | Type | Meaning |
| :--- | :--- | :--- |
| `columns` | `tuple[Column, ...]` | Ordered tuple of columns. |
| `names` | `list[str]` | Column names in schema order. |

#### Object Functions
| Method / Operator | Purpose |
| :--- | :--- |
| `len(schema)`, `iter(schema)` | Return column count or iterate columns in order. |
| `schema[name]` | Look up a `Column` by name (`KeyError` if absent). |
| `name in schema`, `col in schema` | Membership test (`__contains__`) for `str` column name or exact `Column` object (`False` for other types). |
| `s1 == s2`, `hash(schema)` | Equality and hashing based on the `.columns` tuple. |
| `repr(schema)` | Valid Python `Schema(...)` expression showing non-default column options. |
| `schema.to_dict()`, `Schema.from_dict(d)` | Convert to/from `{"columns": [{"type": ..., "name": ..., ...}]}`. |
| `schema.to_json(indent=None)`, `Schema.from_json(text)` | Serialize/deserialize the dict form as UTF-8 JSON (`indent=None` uses compact `(",", ":")` separators). |

Raises `ValueError` for empty columns, duplicate column names, unknown `type`, or invalid column options in `from_dict`/`from_json`; `TypeError` if any element passed to `Schema(...)` is not a `Column`.

### `Column` and Column Types

`Column` is the frozen base dataclass for all typed columns. `name` (non-empty `str`) is positional; all options except `Enum.values` are keyword-only. Boolean options (`nullable`, `repeat`, `varint`, `delta`, `pack`) require exact `bool` (`type(v) is bool`, else `ValueError`). Invalid flag combinations fail immediately on construction with `ValueError`.

#### `Column` Properties
| Property | Type | Meaning |
| :--- | :--- | :--- |
| `name` | `str` | Non-empty column name. |
| `nullable` | `bool` | Default `False`. Allows `None` values (costs 1 shared flag bit per row; absent values write no payload). |
| `repeat` | `bool` | Default `False`. Elides values identical to the previous row (costs 1 shared flag bit per row). Forbidden (`ValueError`) on `Bool`, `Enum`, `Category`, and `Uint(pack=True)`. |

#### `Column` Object Functions
| Method | Purpose |
| :--- | :--- |
| `col.describe()` | Return compact encoding summary `<base>[+delta][+pack][+repeat][+nullable]`. For `Uint`, `<base>` is `f"uint{bits}"` only when `bits` is set and both `varint` and `delta` are `False`, otherwise `"uint"` (so `Uint("a", bits=12, varint=True).describe() == "uint"` and `Uint("a", bits=12, delta=True).describe() == "uint+delta"`). Other bases are `"int"`, `"bool"`, `f"float{bits}"`, `"bytes"`, `"str"`, `f"enum({len(values)})"`, and `"category"`. |
| `col.to_dict()` | Return `{"type": TYPE, "name": name, ...}` including only fields whose value differs from their default (plus `"values"` as a `list` for `Enum`). |
| `repr(col)` | Return Python constructor source omitting default options (plus `Enum.values`). |

#### Column Subclasses
| Type | Options (beyond `nullable`, `repeat`) | Accepted Input Values | Storage |
| :--- | :--- | :--- | :--- |
| `Uint(name)` | `bits=None`, `varint=False`, `delta=False`, `pack=False` | Non-negative `__index__` integers (`bool` rejected with `TypeError`) | Default (`bits=None`): unsigned LEB128 varint. `bits=N` (plain `int >= 1`): always checks `0 <= v < 2**N` on encode and decode; stores `ceil(N / 8)` big-endian bytes when `varint=False`, `delta=False`, and `pack=False`. `varint=True`: retains the `0 <= v < 2**N` bounds check while encoding as unsigned LEB128 varint instead of fixed bytes (`describe()` returns `"uint"`). `delta=True`: zigzag varint of difference from previous non-null value (starts at `0`; overrides fixed-width/varint storage while keeping `bits` check). `pack=True`: requires `bits`, forbids `repeat`/`delta`/`varint`, and stores `N` bits in the shared per-row flag integer. |
| `Int(name)` | `delta=False` | Any `__index__` integer (`bool` rejected) | Zigzag varint of value, or of difference from previous non-null value when `delta=True`. |
| `Bool(name)` | none (`repeat` forbidden) | `bool` and NumPy boolean scalars | 1 shared flag bit per row. |
| `Float(name)` | `bits=64` | `numbers.Real` (`int`, `float`, `Fraction`, NumPy float; `bool` rejected); decoded as `float` | Big-endian IEEE 754. Integers and `Real` values must convert to float64 without precision loss. `bits=32` rejects values whose exact float64 bit pattern changes on float32 round-trip (canonical NaN passes; custom NaN payloads fail). Float64 preserves NaN payloads and `-0.0`. For `repeat=True`, floats compare by IEEE bit pattern (`0.0` then `-0.0` is not a repeat). |
| `Bytes(name)` | none | `bytes`, `bytearray`, `memoryview`; decoded as `bytes` | Unsigned varint length followed by raw bytes. |
| `Str(name)` | none | `str` (must be valid UTF-8, no lone surrogates); decoded as `str` | Unsigned varint byte length followed by UTF-8 bytes. |
| `Enum(name, values)` | `values` (non-empty unique sequence of all `str` including `StrEnum`, or all `__index__` non-`bool` `int` including `IntEnum` and NumPy integers, normalized to `str`/`int`; `bool` in `values` raises `ValueError`; `repeat` forbidden) | Member of `values` (`StrEnum`, `IntEnum`, `__index__` normalize to `str`/`int`; `bool` row value raises `TypeError`) | Index in shared flag bits using `(len(values) - 1).bit_length()` bits (`0` bits for a 1-item `Enum`). |
| `Category(name)` | none (`repeat` forbidden) | All `str` (valid UTF-8) or all `__index__` `int` (`bool` rejected; no mixing) | Sorted distinct value dictionary stored once in the blob plus shared flag bits per row. Emits `UserWarning` at pack time if `rows >= 16` and `distinct > 0.5 * rows`. |

## Examples

### 1. Minimal Example: `Column.describe()`, `Column.to_dict()`, `Uint(..., varint=True)`, and `bytearray`/`memoryview`

Inspect column descriptors and round-trip bounded varint integers alongside `bytearray` and `memoryview` payloads.

```python
from rpkbin.tablepack import Schema, Uint, Bytes, Enum, pack, unpack

cols = [
    Uint("reg_val", bits=32, varint=True),
    Uint("flags", bits=4, pack=True, nullable=True),
    Bytes("payload"),
    Enum("mode", ["R", "W"]),
]
for c in cols:
    print(f"{c.describe():20s} -> {c.to_dict()}")

schema = Schema(cols)
assert "reg_val" in schema and cols[0] in schema

blob = pack(
    {"reg_val": [1, 255], "flags": [None, 9], "payload": [bytearray(b"\x01\x02"), memoryview(b"ok")], "mode": ["R", "W"]},
    schema,
)
print(unpack(memoryview(blob)))
```

Output:
```text
uint                 -> {'type': 'uint', 'name': 'reg_val', 'bits': 32, 'varint': True}
uint4+pack+nullable  -> {'type': 'uint', 'name': 'flags', 'nullable': True, 'bits': 4, 'pack': True}
bytes                -> {'type': 'bytes', 'name': 'payload'}
enum(2)              -> {'type': 'enum', 'name': 'mode', 'values': ['R', 'W']}
[{'reg_val': 1, 'flags': None, 'payload': b'\x01\x02', 'mode': 'R'}, {'reg_val': 255, 'flags': 9, 'payload': b'ok', 'mode': 'W'}]
```

### 2. Progressive Example: `unpack` `TypeError` and `iter_unpack` Deferred Validation

Verify immediate `TypeError` on `unpack`, `TypeError` on `bytearray` column containers, and deferred generator validation in `iter_unpack`.

```python
from rpkbin.tablepack import Schema, Uint, pack, unpack, iter_unpack

for bad_call in (lambda: unpack(1_000_000_000), lambda: pack({"u": bytearray(b"abc")}, Schema(Uint("u")))):
    try:
        bad_call()
    except TypeError as exc:
        print(f"Immediate TypeError: {exc}")

# iter_unpack returns a generator without validating until iteration starts.
gen = iter_unpack(b"not-a-blob")
print("Created:", type(gen).__name__)
try:
    next(gen)
except ValueError as exc:
    print(f"Deferred ValueError on next(): {exc}")
```

Output:
```text
Immediate TypeError: blob must be bytes-like
Immediate TypeError: Column 'u': expected a sequence of values, got bytearray
Created: generator
Deferred ValueError on next(): Not a tablepack blob (bad magic bytes)
```

## Binary Format

Version 1. All integers are unsigned LEB128 varints unless noted.

```text
"TPK" | version (1 byte) | codec (1 byte) | compressed payload
```

`codec` values: `0` none, `1` zlib, `2` lzma, `3` brotli. Uncompressed payload layout:

```text
flags (1 byte; bit 0 = schema embedded)
[ schema JSON length | schema JSON (UTF-8) ]      only if embedded
row count
Category tables (for each Category column in schema order):
    type tag (0 = str, 1 = int) | value count | values
flag block: ceil(total_flag_bits / 8) big-endian bytes per row
column data: stored values for all rows, one column after another
```

Unsupported version bytes raise `ValueError`. Version 1 does not store a schema fingerprint, so when `embed_schema=False`, the caller must supply the matching schema version.

## Tips and Limitations

- Column option mistakes fail when constructing `Column`/`Schema`, whereas data errors report the 0-based row and column name (`row i, column 'name': ...`).
- `iter_unpack` avoids building a full `list[dict]`, but still decodes all columns before yielding row 0.

## See Also

- [Quickstart](README.md)
- [Usage Scenarios](scenarios.md)
- [Documentation Index](../README.md)
