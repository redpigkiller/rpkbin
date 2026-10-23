# TablePack API & Semantics Reference

[繁體中文](../../zh/tablepack/reference.md) · [Documentation Index](../README.md) · [Usage Scenarios](scenarios.md)

Technical specification of the `rpkbin.tablepack` package: functions, schema and column types, accepted values, exceptions, and the binary format.

Import statement:
```python
from rpkbin.tablepack import (
    Schema, Uint, Int, Bool, Float, Bytes, Str, Enum, Category, DecodeLimits,
    pack, unpack, iter_unpack, infer, explain,
)
```

Requires Python 3.11+. brotli >= 1.2 is optional (`pip install -e ".[table]"`) and is used only when its bounded-output decoder API is available; `auto` falls back to zlib otherwise. Private (`_`-prefixed) modules and names are not part of the API.

---

## Functions

### `pack(data, schema=None, *, embed_schema=True, compress="auto", verify=False) -> bytes`
Encodes a table into a blob. Output is deterministic: the same input, schema, and options always give the same bytes (for a given compressor version).

| Name | Type | Description |
| :--- | :--- | :--- |
| `data` | `list[dict]` or `dict[str, sequence]` | One mapping per row, or one equal-length sequence per column. Any iterable of mappings is accepted. `str`/`bytes` are not valid column sequences. |
| `schema` | `Schema \| None` | Default `None`: a schema is inferred with `infer` and embedded. Needs at least one row, and `embed_schema=True`. |
| `embed_schema` | `bool` | Default `True`. Store the schema in the blob so `unpack(blob)` needs no schema. |
| `compress` | `str \| None` | Default `"auto"`: compatible brotli (>= 1.2) if available, otherwise zlib. Also `"brotli"`, `"zlib"`, `"lzma"`, or `None` (no compression). Recorded in the blob header. |
| `verify` | `bool` | Default `False`. Decode the result and compare it with the input. |

**Returns** `bytes`.

**Raises**
- `ValueError` — row/column mismatch (missing or unexpected column, columns of different length), a value outside its column's range, `None` in a non-nullable column, an unknown `Enum` value, an empty table with no schema, `embed_schema=False` with no schema, an unknown `compress` value, `verify=True` failing. Value errors name the row and column: `row 3, column 'addr': ...`.
- `TypeError` — a value of the wrong type for its column, a non-mapping row, a non-sequence column, or mixed types in a `Category` column.
- `ImportError` — `compress="brotli"` while compatible brotli (>= 1.2) is unavailable.

### `unpack(blob, schema=None, *, as_columns=False, limits=DecodeLimits()) -> list[dict] | dict[str, list]`
Decodes a blob made by `pack`.

| Name | Type | Description |
| :--- | :--- | :--- |
| `blob` | `bytes` | Blob from `pack`. |
| `schema` | `Schema \| None` | Required if the blob has no embedded schema. If the blob has one and you also pass one, they must be equal. |
| `as_columns` | `bool` | Default `False`. `True` returns `{column_name: list}` instead of a list of row dicts. |
| `limits` | `DecodeLimits` | Reader budgets for decompressed payload, schema, rows, cells, columns, and integer varints. |

**Returns** a list of dicts in the original row order (keys in schema column order), or a dict of lists. Values are Python `int`, `float`, `bool`, `str`, `bytes`, or `None`.

**Raises**
- `ValueError` — not a TablePack blob (bad magic), unsupported format version, corrupt or truncated data (`Corrupt or truncated tablepack blob`), a reader budget exceeded (reported as corrupt data), no embedded schema and none given, or a given schema that differs from the embedded one.
- `ImportError` — the blob was compressed with brotli but a compatible brotli decoder (>= 1.2) is unavailable.

### `iter_unpack(blob, schema=None, *, limits=DecodeLimits()) -> Iterator[dict]`
Same decoding and errors as `unpack`, yielding one row dict at a time. The blob is decoded column by column before the first row is yielded, so this avoids building the full row list, not the decoded columns.

### `infer(data, *, report=False, compress="auto", embed_schema=True, optimize=True) -> Schema | tuple[Schema, dict]`
Proposes a schema. It first ranks each column's encodings by standalone zlib size, then (with `optimize=True`) measures up to eight whole-table candidates using the requested compressor and schema-embedding setting. The baseline is always included and wins ties. This bounded search is advisory and does not guarantee the global minimum.

| Name | Type | Description |
| :--- | :--- | :--- |
| `data` | as for `pack` | Must contain at least one row. |
| `report` | `bool` | Default `False`. If `True`, returns `(schema, report)`, where `report[name]` is `[(Column, size_in_bytes), ...]` sorted smallest first. |
| `compress` | `str | None` | Default `"auto"`. Compressor used for whole-table scoring: `"auto"`, `"brotli"`, `"zlib"`, `"lzma"`, or `None`. |
| `embed_schema` | `bool` | Default `True`. Whether whole-table candidates include the schema when scored. |
| `optimize` | `bool` | Default `True`. Set `False` to return only the fast per-column baseline. |

Inference by value type (ignoring `None`):

| Values | Candidates |
| :--- | :--- |
| all `bool` | `Bool` |
| non-negative ints | `Uint` as varint / fixed width / `delta` / `pack` (when width is 16 bits or less), plus `Category` for low-cardinality data |
| ints with negatives | `Int` plain / `delta`, plus `Category` for low-cardinality data |
| ints and floats | `Float` 64-bit and 32-bit |
| all `str` | `Str`, plus `Category` for low-cardinality data |
| all bytes-like | `Bytes` |
| only `None` | `Str(nullable=True)` |

`nullable=True` is set when any value is `None`. `repeat=True` variants are tried when more than 10% of consecutive rows are equal. A column is low-cardinality when its distinct values are at most 60% of the non-null values. Whole-table optimization considers a bounded portfolio such as joint packed integers, repeat policies, and omitting `Category`; it does not enumerate all column combinations.

`infer` never proposes `Enum`.

The `report` remains the per-column zlib ranking, even when whole-table scoring selects a different candidate. `pack(data, schema=None)` uses the same inference with the target `compress` and `embed_schema` settings. `explain(data, schema=None)` normalizes the data once, then infers for the target compressor with `embed_schema=False` before measuring columns and total.

**Raises** `ValueError` for an empty table or a column where no candidate worked; `TypeError` for mixed or unsupported value types in one column.

### `explain(data, schema=None, *, compress="auto") -> str`
Returns a text table of the encoding and bytes per column, plus the total. If `schema` is omitted it is inferred using the target compressor and `embed_schema=False`. Each column's bytes come from packing that column alone (no embedded schema), so the figures are a guide: they include per-blob overhead and do not add up to the total. `share` is relative to their sum. Rows may be any iterable; normalization is reused for inference and measurement. Raises what `pack` and `infer` raise.

### `DecodeLimits`
Public frozen dataclass that controls reader work. Both `unpack` and `iter_unpack` accept `limits`; the default instance has these positive integer budgets:

| Field | Default | Meaning |
| :--- | ---: | :--- |
| `max_rows` | `1_000_000` | Maximum declared rows. |
| `max_cells` | `10_000_000` | Maximum decoded values (`rows * columns`). |
| `max_payload_bytes` | `67_108_864` (64 MiB) | Maximum decompressed payload bytes. |
| `max_schema_bytes` | `1_048_576` (1 MiB) | Maximum embedded schema JSON bytes. |
| `max_columns` | `4096` | Maximum schema columns. |
| `max_integer_bytes` | `8192` | Maximum bytes in an integer varint. |

Example: `unpack(blob, limits=DecodeLimits(max_rows=2_000_000))`. A limit violation is rejected as `ValueError` with the corrupt-blob prefix. Decompression is bounded before metadata and row/cell/flag/range/repeat/EOF validation. LZMA decoding also has a fixed 128 MiB decoder-memory limit (the V1 writer's 64 MiB dictionary remains readable). These budgets limit parser amplification; they are not an operating-system memory cap.

---

## `Schema`

`Schema(*columns)` or `Schema([columns])`: an ordered collection of columns with unique names, at least one column. Immutable after construction.

| Member | Description |
| :--- | :--- |
| `columns` | Tuple of `Column`. |
| `names` | List of column names in order. |
| `iter(schema)`, `len(schema)` | Iterate or count columns. |
| `schema[name]` | The column with that name; `KeyError` if absent. |
| `==`, `hash()` | Equal when the column tuples are equal. |
| `repr(schema)` | Python source that rebuilds the schema (only non-default options are shown). |
| `to_dict()` / `Schema.from_dict(d)` | Plain-dict form, `{"columns": [{"type": ..., "name": ..., ...}]}`. |
| `to_json(indent=None)` / `Schema.from_json(text)` | JSON text of the dict form. |

**Raises** `ValueError` for no columns, duplicate names, unknown column `type`, or bad options in `from_dict`/`from_json`; `TypeError` if an argument is not a `Column`.

---

## Column types

All columns are immutable dataclasses. `name` is the first, positional argument; every option is keyword-only except `Enum.values`. A bad combination raises `ValueError` naming the column when the object is created. Two options are common to all columns:

| Option | Default | Description |
| :--- | :--- | :--- |
| `nullable` | `False` | The value may be `None`. Costs 1 flag bit per row; an absent value stores nothing. |
| `repeat` | `False` | The value is often equal to the previous row's. Costs 1 flag bit per row; a repeated value is not stored again. Not supported by `Bool`, `Enum`, `Category`, or `Uint(pack=True)`. |

| Type | Options (besides the common ones) | Accepted input values | Storage |
| :--- | :--- | :--- | :--- |
| `Uint(name)` | `bits=None`, `varint=False`, `delta=False`, `pack=False` | Integers `>= 0` (anything with `__index__`, e.g. NumPy ints; `bool` is rejected) | Varint by default. `bits=N` bounds the value to `< 2**N` and, without `delta`/`varint`, stores it in `ceil(N/8)` big-endian bytes. `delta=True` stores the zigzag varint of the difference from the previous non-null value (starts from 0). `pack=True` (needs `bits`; cannot combine with `repeat`, `delta`, `varint`) stores the value in the shared flag bits. |
| `Int(name)` | `delta=False` | Integers (as above) | Zigzag varint of the value, or of the difference from the previous non-null value with `delta=True`. |
| `Bool(name)` | none | `True`/`False` and NumPy boolean scalars | 1 flag bit. |
| `Float(name)` | `bits=64` | `int` or `float` (not `bool`); returned as `float` | Big-endian IEEE 754. Integers must be exactly representable as float64. `bits=32` raises `ValueError` unless the value round-trips with its exact float64 bit pattern; representable NaNs are accepted, including their payload only when that pattern survives the round trip. Float64 NaNs and signed zero preserve their bit patterns. |
| `Bytes(name)` | none | `bytes`, `bytearray`, `memoryview`; returned as `bytes` | Varint length plus data. |
| `Str(name)` | none | `str`; returned as `str` | Varint length plus UTF-8. |
| `Enum(name, values)` | `values`: non-empty sequence, all `str` or all `int`, unique | One of `values`; `StrEnum`, `IntEnum`, NumPy integers, and other string/integer subclasses normalize to built-in `str`/`int` (bool is rejected as an integer) | Index in the shared flag bits, `ceil(log2(len(values)))` bits (0 bits for one value). |
| `Category(name)` | none | All `str` or all `int` (not mixed); `StrEnum`, `IntEnum`, NumPy integers, and other subclasses normalize to built-in values | Like `Enum`, but the sorted set of distinct values is learned from the data at pack time and stored once in the blob. Warns (`UserWarning`) when the table has 16 or more rows and distinct values exceed half the row count. |

For repeats, floats are compared by their bit pattern, so `0.0` then `-0.0` is not a repeat.

---

## Binary format

Version 1. All integers are unsigned LEB128 varints unless stated.

```text
"TPK" | version (1 byte) | codec (1 byte) | compressed payload
```

`codec`: `0` none, `1` zlib, `2` lzma, `3` brotli. The payload, before compression:

```text
flags (1 byte; bit 0 = schema embedded)
[ schema JSON length | schema JSON (UTF-8) ]      only if embedded
row count
Category tables: for each Category column, in schema order:
    type tag (0 = str, 1 = int) | value count | values
flag block: for every row, a big-endian bit field of ceil(total_flag_bits / 8) bytes
column data: for each column in schema order, its stored values for all rows
```

Per row, the flag bits hold, for each column in order: the `Enum`/`Category` index, a `Bool` value, a `Uint(pack=True)` value, the `nullable` present-bit, and the `repeat` same-as-previous bit. Values that do not live in flag bits go to that column's data section, so the same column's values sit next to each other, which helps the compressor. A blob with an unsupported version byte is rejected with `ValueError`. Version 1 does not store a schema fingerprint: when schema embedding is disabled, the application must select the matching external schema version.
