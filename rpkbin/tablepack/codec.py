"""
pack / unpack: schema-driven ``rows <-> bytes`` codec.

Blob layout (before the optional compression wrapper):

    b"TPK" | version(1) | codec(1) | compressed(payload)

    payload = flags(1: bit0 = schema embedded)
              [varint len | schema JSON]            if embedded
              varint n_rows
              per Category column: type tag, varint count, values
              flag block: shared flag bits for every row, back to back
              per column: its byte-aligned value slots for all rows

Enum / Category / Bool / ``Uint(pack=True)`` values and the nullable / repeat
markers all live in one shared per-row flag integer, so they cost bits, not bytes.
"""

from __future__ import annotations

import numbers
import math
import struct
import warnings
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from itertools import repeat as repeat_value
from typing import Any

from . import _compress
from ._varint import decode_varint, encode_varint, zigzag_decode, zigzag_encode
from ._types import _as_int, _enum_value, _is_bool, _type_name
from .limits import DEFAULT_LIMITS, DecodeLimits
from .schema import Bool, Category, Enum, Schema, Uint

__all__ = ["pack", "unpack", "iter_unpack"]

MAGIC = b"TPK"
VERSION = 1

_UNSET = object()
_CORRUPT = "Corrupt or truncated tablepack blob"


# ---------------------------------------------------------------------------
# Input normalisation: rows (list of dicts) or columns (dict of sequences)
# ---------------------------------------------------------------------------

def _to_columns(data, names: list[str] | None) -> tuple[list[str], dict[str, list], int]:
    """Return (names, {name: values}, n_rows). ``names`` (from a schema) fixes order."""
    if isinstance(data, Mapping):
        cols = {}
        for k, v in data.items():
            if isinstance(v, (str, bytes)) or not isinstance(v, Iterable):
                raise TypeError(f"Column '{k}': expected a sequence of values, got {type(v).__name__}")
            cols[k] = list(v)
        if names is not None:
            missing = [n for n in names if n not in cols]
            extra = [k for k in cols if k not in names]
            if missing or extra:
                raise ValueError(f"Columns don't match schema (missing {missing}, unexpected {extra})")
            cols = {n: cols[n] for n in names}
        lengths = {len(v) for v in cols.values()}
        if len(lengths) > 1:
            raise ValueError(f"Columns have different lengths: { {k: len(v) for k, v in cols.items()} }")
        return list(cols), cols, lengths.pop() if lengths else 0

    rows = data if isinstance(data, (list, tuple)) else list(data)
    if names is None:
        names = list(rows[0]) if rows else []
    cols = {n: [] for n in names}
    ncols = len(names)
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise TypeError(f"row {i}: expected a mapping, got {type(row).__name__}")
        try:
            vals = [row[n] for n in names]
        except KeyError as e:
            raise ValueError(f"row {i}: missing column {e.args[0]!r}") from None
        if len(row) != ncols:
            raise ValueError(f"row {i}: unexpected column(s) {[k for k in row if k not in cols]}")
        for n, v in zip(names, vals):
            cols[n].append(v)
    return names, cols, len(rows)


# ---------------------------------------------------------------------------
# Per-column plan: where its bits live in the shared flags integer
# ---------------------------------------------------------------------------

class _Plan:
    __slots__ = (
        "col", "name", "kind", "nullable", "repeat", "values", "index", "val_type",
        "idx_off", "idx_bits", "val_off", "val_bits", "null_off", "rep_off", "inline",
    )


def _learn_category(name: str, values: list, warn: bool) -> list:
    try:
        present = [_enum_value(v) for v in values if v is not None]
    except TypeError:
        raise TypeError(f"Column '{name}': Category values must be all str or all int") from None
    if present and all(isinstance(v, str) for v in present):
        pass
    elif present and all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        pass
    elif present:
        raise TypeError(f"Column '{name}': Category values must be all str or all int")
    uniq = sorted(set(present))
    if warn and len(values) >= 16 and len(uniq) / len(values) > 0.5:
        warnings.warn(
            f"Column '{name}' has {len(uniq)} distinct values in {len(values)} rows; "
            f"Category encoding may not help, consider Str.",
            stacklevel=4,
        )
    return uniq


def _build_plans(schema: Schema, categories: dict[str, list]) -> tuple[list[_Plan], int]:
    plans, offset = [], 0
    for col in schema:
        p = _Plan()
        p.col, p.name, p.nullable, p.repeat = col, col.name, col.nullable, col.repeat
        p.kind, p.values, p.index, p.val_type = col.TYPE, None, None, None
        p.idx_off = p.idx_bits = p.val_off = p.val_bits = p.null_off = p.rep_off = 0

        if isinstance(col, (Enum, Category)):
            p.kind = "enum"
            p.values = list(col.values) if isinstance(col, Enum) else categories[col.name]
            p.index = {v: i for i, v in enumerate(p.values)}
            p.val_type = type(p.values[0]) if p.values else str
            p.idx_bits = (len(p.values) - 1).bit_length() if len(p.values) > 1 else 0
            p.idx_off, offset = offset, offset + p.idx_bits
        elif isinstance(col, Bool):
            p.val_bits, p.val_off, offset = 1, offset, offset + 1
        elif isinstance(col, Uint) and col.pack:
            p.val_bits, p.val_off, offset = col.bits, offset, offset + col.bits
        p.inline = p.kind in ("enum", "bool") or (isinstance(col, Uint) and col.pack)

        if col.nullable:
            p.null_off, offset = offset, offset + 1
        if col.repeat:
            p.rep_off, offset = offset, offset + 1
        plans.append(p)
    return plans, (offset + 7) // 8


# ---------------------------------------------------------------------------
# Value validation / single-value encode & decode
# ---------------------------------------------------------------------------

def _prep(p: _Plan, v):
    """Validate ``v`` against the column and return its normalised form."""
    col = p.col
    k = p.kind
    if k == "uint":
        v = _as_int(v)
        if v < 0:
            raise ValueError(f"{v} is negative; Uint needs a value >= 0 (use Int)")
        if col.bits is not None and v >> col.bits:
            raise ValueError(f"{v} doesn't fit in {col.bits} bits")
        return v
    if k == "int":
        return _as_int(v)
    if k == "float":
        if _is_bool(v) or not isinstance(v, numbers.Real):
            raise TypeError(f"expected a number, got {_type_name(v)}")
        try:
            f = float(v)
        except (OverflowError, ValueError):
            raise ValueError("value is out of range for float64") from None
        if isinstance(v, numbers.Integral):
            if not math.isfinite(f) or int(f) != _as_int(v):
                raise ValueError("integer isn't exactly representable as float64")
        elif f == f and f != v:
            raise ValueError("value isn't exactly representable as float64")
        if col.bits == 32:
            try:
                back = struct.unpack(">f", struct.pack(">f", f))[0]
            except OverflowError:
                raise ValueError(f"{v} is out of range for float32") from None
            if struct.pack(">d", back) != struct.pack(">d", f):
                raise ValueError(f"{v} isn't exactly representable as float32")
        return f
    if k == "bool":
        if not _is_bool(v):
            raise TypeError(f"expected bool, got {_type_name(v)}")
        return bool(v)
    if k == "str":
        if not isinstance(v, str):
            raise TypeError(f"expected str, got {type(v).__name__}")
        return str.__str__(v)
    if k == "bytes":
        if not isinstance(v, (bytes, bytearray, memoryview)):
            raise TypeError(f"expected bytes, got {type(v).__name__}")
        return bytes(v)
    if k == "enum":
        v = _enum_value(v)
        if type(v) is not p.val_type:
            raise TypeError(f"expected {p.val_type.__name__}, got {type(v).__name__}")
        if v not in p.index:
            known = p.values if len(p.values) <= 10 else [*p.values[:10], "..."]
            raise ValueError(f"unknown value {v!r}; known values: {known}")
        return v
    raise AssertionError(k)


def _same(p: _Plan, a, b) -> bool:
    """Is ``b`` identical to previous value ``a``? Bit-exact for floats."""
    if a is _UNSET:
        return False
    if a is None or b is None:
        return a is b
    if p.kind == "float":
        return struct.pack(">d", a) == struct.pack(">d", b)
    return a == b


def _encode_value(p: _Plan, v, base: int) -> bytes:
    col, k = p.col, p.kind
    if k == "uint":
        if col.delta:
            return encode_varint(zigzag_encode(v - base))
        if col.varint or col.bits is None:
            return encode_varint(v)
        return v.to_bytes((col.bits + 7) // 8, "big")
    if k == "int":
        return encode_varint(zigzag_encode(v - base if col.delta else v))
    if k == "float":
        return struct.pack(">f" if col.bits == 32 else ">d", v)
    if k in ("str", "bytes"):
        b = v.encode("utf-8") if k == "str" else v
        return encode_varint(len(b)) + b
    raise AssertionError(k)


def _decode_value(p: _Plan, data: bytes, pos: int, base: int, limits: DecodeLimits):
    col, k = p.col, p.kind
    if k == "uint":
        if col.delta:
            d, pos = decode_varint(data, pos, max_bytes=limits.max_integer_bytes)
            return base + zigzag_decode(d), pos
        if col.varint or col.bits is None:
            return decode_varint(data, pos, max_bytes=limits.max_integer_bytes)
        n = (col.bits + 7) // 8
        if pos + n > len(data):
            raise IndexError
        return int.from_bytes(data[pos:pos + n], "big"), pos + n
    if k == "int":
        d, pos = decode_varint(data, pos, max_bytes=limits.max_integer_bytes)
        d = zigzag_decode(d)
        return (base + d if col.delta else d), pos
    if k == "float":
        fmt, n = (">f", 4) if col.bits == 32 else (">d", 8)
        if pos + n > len(data):
            raise IndexError
        return struct.unpack(fmt, data[pos:pos + n])[0], pos + n
    if k in ("str", "bytes"):
        n, pos = decode_varint(data, pos, max_bytes=10)
        if pos + n > len(data):
            raise IndexError
        raw = data[pos:pos + n]
        return (raw.decode("utf-8") if k == "str" else bytes(raw)), pos + n
    raise AssertionError(k)


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------

def _encode_payload(names: list[str], cols: dict[str, list], n: int, schema: Schema,
                    embed: bool, warn: bool = True) -> bytes:
    categories = {
        c.name: _learn_category(c.name, cols[c.name], warn)
        for c in schema if isinstance(c, Category)
    }
    plans, flag_bytes = _build_plans(schema, categories)

    out = bytearray()
    out.append(1 if embed else 0)
    if embed:
        sj = schema.to_json().encode("utf-8")
        out += encode_varint(len(sj)) + sj
    out += encode_varint(n)
    for c in schema:
        if isinstance(c, Category):
            vals = categories[c.name]
            is_int = bool(vals) and isinstance(vals[0], int)
            out.append(1 if is_int else 0)
            out += encode_varint(len(vals))
            for v in vals:
                if is_int:
                    out += encode_varint(zigzag_encode(v))
                else:
                    b = v.encode("utf-8")
                    out += encode_varint(len(b)) + b

    # Column-major body: each column's values are contiguous, which compresses
    # far better than interleaving columns row by row.
    flags_rows = [0] * n
    bodies = []
    for p in plans:
        series = cols[p.name]
        body = bytearray()
        prev, base = _UNSET, 0
        for i in range(n):
            v = series[i]
            try:
                if v is None:
                    if not p.nullable:
                        raise ValueError("got None but the column isn't nullable")
                else:
                    v = _prep(p, v)
            except (ValueError, TypeError) as e:
                raise type(e)(f"row {i}, column '{p.name}': {e}") from None

            present = v is not None
            flags = 0
            if p.nullable and present:
                flags |= 1 << p.null_off
            same = False
            if p.repeat:
                same = _same(p, prev, v)
                if same:
                    flags |= 1 << p.rep_off

            if present:
                if p.kind == "enum":
                    flags |= p.index[v] << p.idx_off
                elif p.kind == "bool":
                    flags |= int(v) << p.val_off
                elif p.inline:  # Uint(pack=True)
                    flags |= v << p.val_off
                elif not same:
                    body += _encode_value(p, v, base)
                if p.kind in ("uint", "int"):
                    base = v
            prev = v
            flags_rows[i] |= flags
        bodies.append(body)

    if flag_bytes:
        for f in flags_rows:
            out += f.to_bytes(flag_bytes, "big")
    for body in bodies:
        out += body
    return bytes(out)

def _wrap(payload: bytes, codec: int) -> bytes:
    return MAGIC + bytes([VERSION, codec]) + _compress.compress(payload, codec)


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------

def _read_payload(blob: bytes, schema: Schema | None, limits: DecodeLimits):
    """Parse the header. Returns (schema, plans, flag_bytes, n_rows, data, pos)."""
    if not isinstance(limits, DecodeLimits):
        raise TypeError("limits must be a DecodeLimits instance")
    if schema is not None and not isinstance(schema, Schema):
        raise TypeError("schema must be a Schema instance")
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise TypeError("blob must be bytes-like")
    blob = bytes(blob)
    if len(blob) < 5 or blob[:3] != MAGIC:
        raise ValueError("Not a tablepack blob (bad magic bytes)")
    if blob[3] != VERSION:
        raise ValueError(f"Unsupported tablepack format version {blob[3]}")
    try:
        data = _compress.decompress(blob[5:], blob[4], max_output_bytes=limits.max_payload_bytes)
    except ImportError:
        raise
    except Exception as e:
        raise ValueError(f"{_CORRUPT}: {e}") from None

    try:
        if data[0] not in (0, 1):
            raise ValueError("unknown payload flags")
        embedded = data[0]
        pos = 1
        if embedded:
            import json
            n, pos = decode_varint(data, pos, max_bytes=10)
            if n > limits.max_schema_bytes or pos + n > len(data):
                raise ValueError("invalid schema length or max_schema_bytes exceeded")
            spec = json.loads(data[pos:pos + n].decode("utf-8"))
            if (not isinstance(spec, dict) or not isinstance(spec.get("columns"), list)
                    or len(spec["columns"]) > limits.max_columns):
                raise ValueError("invalid schema or max_columns exceeded")
            emb = Schema.from_dict(spec)
            pos += n
    except (IndexError, ValueError, TypeError, OverflowError, RecursionError) as e:
        raise ValueError(f"{_CORRUPT}: {e}") from None

    if embedded:
        if schema is not None and schema != emb:
            raise ValueError(
                "The schema you passed doesn't match the schema embedded in the blob. "
                "Omit it to use the embedded one."
            )
        schema = emb
    elif schema is None:
        raise ValueError("This blob has no embedded schema; pass the schema you packed it with.")

    try:
        if len(schema) > limits.max_columns:
            raise ValueError("max_columns exceeded")
        n_rows, pos = decode_varint(data, pos, max_bytes=10)
        if n_rows > limits.max_rows:
            raise ValueError("max_rows exceeded")
        if n_rows * len(schema) > limits.max_cells:
            raise ValueError("max_cells exceeded")
        categories = {}
        for c in schema:
            if isinstance(c, Category):
                is_int = data[pos]
                if is_int not in (0, 1):
                    raise ValueError("invalid Category type tag")
                cnt, pos = decode_varint(data, pos + 1, max_bytes=10)
                if cnt > n_rows:
                    raise ValueError("Category has more values than rows")
                vals = []
                for _ in range(cnt):
                    if is_int:
                        z, pos = decode_varint(data, pos, max_bytes=limits.max_integer_bytes)
                        vals.append(zigzag_decode(z))
                    else:
                        ln, pos = decode_varint(data, pos, max_bytes=10)
                        if pos + ln > len(data):
                            raise ValueError("truncated Category string")
                        vals.append(data[pos:pos + ln].decode("utf-8"))
                        pos += ln
                if vals != sorted(set(vals)):
                    raise ValueError("Category values must be sorted and unique")
                categories[c.name] = vals
        plans, flag_bytes = _build_plans(schema, categories)
    except (IndexError, struct.error, ValueError, TypeError, OverflowError) as e:
        raise ValueError(f"{_CORRUPT}: {e}") from None
    return schema, plans, flag_bytes, n_rows, data, pos


def _decode_columns(plans: list[_Plan], flag_bytes: int, n_rows: int,
                    data: bytes, pos: int, limits: DecodeLimits) -> list[list]:
    """Decode the column-major body into one list of values per column."""
    try:
        # Reject impossible lengths before allocating any row-sized container.
        minimum_body = 0
        for p in plans:
            if not p.inline and not p.nullable and not p.repeat:
                if p.kind == "float":
                    minimum_body += p.col.bits // 8
                elif p.kind == "uint" and p.col.bits and not (p.col.delta or p.col.varint):
                    minimum_body += (p.col.bits + 7) // 8
                else:
                    minimum_body += 1
        if pos + n_rows * (flag_bytes + minimum_body) > len(data):
            raise ValueError("body too short for declared rows")
        flags_rows = None
        if flag_bytes:
            flags_rows = []
            used_bits = sum(p.idx_bits + p.val_bits + int(p.nullable) + int(p.repeat) for p in plans)
            for i in range(n_rows):
                flags = int.from_bytes(data[pos:pos + flag_bytes], "big")
                if flags >> used_bits:
                    raise ValueError("nonzero unused flag bits")
                flags_rows.append(flags)
                pos += flag_bytes

        result = []
        for p in plans:
            col: list = []
            prev, base = _UNSET, 0
            for flags in (flags_rows if flags_rows is not None else repeat_value(0, n_rows)):
                present = (not p.nullable) or bool(flags >> p.null_off & 1)
                if p.inline and not present:
                    inline_value = ((flags >> p.idx_off) & ((1 << p.idx_bits) - 1)) if p.kind == "enum" else ((flags >> p.val_off) & ((1 << p.val_bits) - 1))
                    if inline_value:
                        raise ValueError("absent inline value has nonzero bits")
                if p.repeat and flags >> p.rep_off & 1:
                    if prev is _UNSET or present != (prev is not None):
                        raise ValueError("invalid repeat state")
                    v = prev
                elif p.kind == "enum":
                    v = p.values[(flags >> p.idx_off) & ((1 << p.idx_bits) - 1)] if present else None
                elif p.kind == "bool":
                    v = bool(flags >> p.val_off & 1) if present else None
                elif p.inline:
                    v = (flags >> p.val_off) & ((1 << p.val_bits) - 1) if present else None
                elif not present:
                    v = None
                else:
                    v, pos = _decode_value(p, data, pos, base, limits)
                if p.kind == "uint" and v is not None:
                    if v < 0 or (p.col.bits is not None and v >> p.col.bits):
                        raise ValueError(f"decoded Uint outside column '{p.name}' range")
                if v is not None and p.kind in ("uint", "int"):
                    base = v
                prev = v
                col.append(v)
            result.append(col)
        if pos != len(data):
            raise ValueError("trailing payload data")
        return result
    except (IndexError, struct.error, ValueError, OverflowError) as e:
        raise ValueError(f"{_CORRUPT}: {e}") from None

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pack(data, schema: Schema | None = None, *, embed_schema: bool = True,
         compress: str | None = "auto", verify: bool = False) -> bytes:
    """Encode a table into bytes.

    Args:
        data: ``list[dict]`` (one dict per row) or ``dict[str, sequence]`` (one
            sequence per column).
        schema: column description. If omitted, one is inferred with
            :func:`tablepack.infer` and embedded in the blob.
        embed_schema: store the schema inside the blob so :func:`unpack` needs
            no schema argument. Set False for the smallest possible output
            (you must then pass the same schema to :func:`unpack`).
        compress: ``"auto"`` (brotli if installed, else zlib), ``"brotli"``,
            ``"zlib"``, ``"lzma"`` or ``None``. Recorded in the blob header.
        verify: decode the result and compare it with the input (raises
            ``ValueError`` on mismatch). Handy in build scripts.
    """
    codec = _compress.resolve(compress)
    names, cols, n = _to_columns(data, schema.names if schema is not None else None)

    if schema is None:
        if n == 0:
            raise ValueError("Can't infer a schema from an empty table; pass a schema")
        if not embed_schema:
            raise ValueError("embed_schema=False needs an explicit schema")
        from .infer import _infer_columns
        schema = _infer_columns(names, cols, compress=compress, embed_schema=embed_schema)

    payload = _encode_payload(names, cols, n, schema, embed_schema)
    blob = _wrap(payload, codec)

    if verify:
        # These budgets derive from trusted input we just encoded, rather than
        # refusing a large valid table because the reader's defaults are lower.
        verify_limits = replace(
            DEFAULT_LIMITS, max_rows=max(DEFAULT_LIMITS.max_rows, n),
            max_cells=max(DEFAULT_LIMITS.max_cells, n * len(schema)),
            max_columns=max(DEFAULT_LIMITS.max_columns, len(schema)),
            max_payload_bytes=max(DEFAULT_LIMITS.max_payload_bytes, len(payload)),
            max_schema_bytes=max(DEFAULT_LIMITS.max_schema_bytes, len(schema.to_json().encode("utf-8"))),
            max_integer_bytes=max(DEFAULT_LIMITS.max_integer_bytes, len(payload)),
        )
        back = unpack(blob, None if embed_schema else schema, as_columns=True, limits=verify_limits)
        for c in schema:
            if len(back[c.name]) != n or not all(
                _loosely_equal(c.TYPE, a, b) for a, b in zip(cols[c.name], back[c.name])
            ):
                raise ValueError(f"verify failed: column '{c.name}' did not round-trip")
    return blob


def _loosely_equal(kind: str, a, b) -> bool:
    """Input-vs-decoded equality tolerant of int/float and bytes-like input types."""
    if a is None or b is None:
        return a is b
    if kind == "float":
        if isinstance(a, numbers.Integral) and not _is_bool(a):
            return math.isfinite(b) and b.is_integer() and _as_int(a) == int(b)
        if not isinstance(a, float) and a == a and a != b:
            return False
        return struct.pack(">d", float(a)) == struct.pack(">d", b)
    if kind in ("int", "uint"):
        return _as_int(a) == b
    if kind == "bytes":
        return bytes(a) == b
    return a == b


def iter_unpack(blob: bytes, schema: Schema | None = None, *,
                limits: DecodeLimits = DEFAULT_LIMITS) -> Iterator[dict[str, Any]]:
    """Yield rows one at a time as dicts.

    The blob is decoded column by column first, so this saves building the full
    ``list[dict]``, not the decoded column data itself.
    """
    schema, plans, flag_bytes, n_rows, data, pos = _read_payload(blob, schema, limits)
    names = [p.name for p in plans]
    for row in zip(*_decode_columns(plans, flag_bytes, n_rows, data, pos, limits)):
        yield dict(zip(names, row))


def unpack(blob: bytes, schema: Schema | None = None, *, as_columns: bool = False,
           limits: DecodeLimits = DEFAULT_LIMITS):
    """Decode a blob made by :func:`pack`.

    Returns ``list[dict]`` in the original row order, or
    ``dict[str, list]`` when ``as_columns=True``.
    """
    schema, plans, flag_bytes, n_rows, data, pos = _read_payload(blob, schema, limits)
    names = [p.name for p in plans]
    cols = _decode_columns(plans, flag_bytes, n_rows, data, pos, limits)
    if as_columns:
        return dict(zip(names, cols))
    return [dict(zip(names, row)) for row in zip(*cols)]
