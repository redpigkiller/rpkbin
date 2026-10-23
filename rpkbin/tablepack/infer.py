"""
infer / explain: help choose a schema and see where the bytes go.

``infer`` first ranks plausible encodings for each column with zlib level 9.
With optimization enabled it also measures a bounded set of whole-table
schemas using the requested compressor and schema embedding setting. The
reported per-column list remains that fast zlib ranking; the returned schema
may be a different candidate when its real whole-table payload is smaller.

Inference is advisory: review the result, since it cannot know what your data
means (for example, a column that is "always < 16" today but not tomorrow).
"""

from __future__ import annotations

import numbers
import zlib

from . import _compress
from ._types import _as_int, _is_bool
from .codec import _encode_payload, _to_columns, pack
from .schema import Bool, Bytes, Category, Column, Float, Int, Schema, Str, Uint

__all__ = ["infer", "explain"]


def infer(data, *, report: bool = False, compress: str | None = "auto",
          embed_schema: bool = True, optimize: bool = True):
    """Propose a :class:`Schema` for ``data`` (rows or named columns).

    ``report=True`` returns ``(schema, report)``. The report maps each column
    to candidate encodings sorted by their individual zlib level-9 size. When
    ``optimize=True`` (the default), the chosen schema is instead selected by
    measuring up to eight distinct whole-table schemas with ``compress`` and
    ``embed_schema``; therefore the winner need not be the first report entry.
    Set ``optimize=False`` to return the fast per-column baseline without
    whole-table measurements.
    """
    names, cols, n = _to_columns(data, None)
    if n == 0 or not names:
        raise ValueError("Can't infer a schema from an empty table")
    schema, rep = _infer_columns(
        names, cols, with_report=True, compress=compress,
        embed_schema=embed_schema, optimize=optimize,
    )
    return (schema, rep) if report else schema


def _infer_columns(names, cols, with_report: bool = False, *, compress: str | None = "auto",
                   embed_schema: bool = True, optimize: bool = True):
    """Infer from already normalized columns; keep the historical positional arg."""
    codec = _compress.resolve(compress)
    chosen, rep = [], {}
    for name in names:
        results = []
        for cand in _candidates(name, cols[name]):
            size = _measure(cand, cols[name])
            if size is not None:
                results.append((cand, size))
        if not results:
            raise ValueError(f"Column '{name}': no candidate encoding worked, check the data")
        results.sort(key=lambda r: r[1])  # stable: earlier (simpler) candidate wins ties
        chosen.append(results[0][0])
        rep[name] = results

    baseline = Schema(*chosen)
    if optimize:
        chosen_schema = _optimize_schema(names, cols, rep, baseline, codec, embed_schema)
    else:
        chosen_schema = baseline
    return (chosen_schema, rep) if with_report else chosen_schema


def _measure(col: Column, values: list) -> int | None:
    """Fast per-column baseline score: zlib level 9, with no embedded schema."""
    try:
        payload = _encode_payload([col.name], {col.name: values}, len(values),
                                  Schema(col), embed=False, warn=False)
    except (ValueError, TypeError):
        return None  # candidate doesn't fit this data
    return len(zlib.compress(payload, 9))


def _optimize_schema(names, cols, report, baseline, codec, embed_schema):
    """Compare a small, deduplicated portfolio; never enumerate combinations."""
    candidates: list[Schema] = []
    seen: set[tuple[Column, ...]] = set()

    def add(columns):
        key = tuple(columns)
        if key not in seen and len(candidates) < 8:
            seen.add(key)
            candidates.append(Schema(*key))

    base = list(baseline)
    # The baseline must always be measured and is the tie-break winner.
    add(base)

    nonpack = [_best(report[n], lambda c: not (isinstance(c, Uint) and c.pack)) for n in names]
    add(nonpack)

    # Pack all eligible narrow Uint columns as one joint layout. This can win
    # even when each packed column loses its isolated zlib comparison.
    packed = []
    any_packed = False
    for n, fallback in zip(names, nonpack):
        variant = _best(report[n], lambda c: isinstance(c, Uint) and c.pack)
        packed.append(variant or fallback)
        any_packed |= variant is not None
    if any_packed:
        add(packed)

    # Evaluate repeat policy as table-wide alternatives. Use only candidates
    # already validated by per-column measurement.
    repeat_off = [_best(report[n], lambda c: c.repeat is False) or fallback
                  for n, fallback in zip(names, nonpack)]
    repeat_on = [_best(report[n], lambda c: c.repeat is True) or fallback
                 for n, fallback in zip(names, nonpack)]
    if any(c.repeat for c in repeat_on):
        add(repeat_off)
        add(repeat_on)
        if any_packed:
            packed_off = [(_best(report[n], lambda c: isinstance(c, Uint) and c.pack) or fallback)
                          for n, fallback in zip(names, repeat_off)]
            add(packed_off)

    # A Category can save bytes alone but its embedded dictionary and shared
    # flag layout can change the table-wide result. Include a no-Category case.
    no_category = [
        _best(report[n], lambda c: not isinstance(c, Category)) or fallback
        for n, fallback in zip(names, nonpack)
    ]
    add(no_category)

    # Baseline is intentionally first, so stable min preserves it on ties.
    measured = []
    for schema in candidates:
        try:
            payload = _encode_payload(names, cols, len(next(iter(cols.values()))), schema,
                                      embed=embed_schema, warn=False)
            size = 5 + len(_compress.compress(payload, codec))
        except (ValueError, TypeError):
            # Invalid float widths and other non-fitting proposals are not
            # choices; the baseline remains measured and valid.
            continue
        measured.append((schema, size))
    return min(measured, key=lambda item: item[1])[0] if measured else baseline


def _best(results, predicate):
    return next((col for col, _ in results if predicate(col)), None)


def _is_int(v) -> bool:
    if _is_bool(v):
        return False
    try:
        _as_int(v)
    except TypeError:
        return False
    return True


def _candidates(name: str, values: list) -> list[Column]:
    non_null = [v for v in values if v is not None]
    nullable = len(non_null) < len(values)
    if not non_null:
        return [Str(name, nullable=True)]

    n = len(values)
    same = sum(1 for a, b in zip(values, values[1:]) if a == b)
    repeats = [False, True] if n > 1 and same / (n - 1) > 0.1 else [False]
    # Bytes-like values are intentionally not hashed here: bytearray and
    # memoryview are valid inputs but are unhashable or need normalization.
    if all(isinstance(v, str) for v in non_null):
        low_cardinality = len(set(non_null)) <= 0.6 * len(non_null)
    elif all(_is_int(v) for v in non_null):
        low_cardinality = len(set(_as_int(v) for v in non_null)) <= 0.6 * len(non_null)
    else:
        low_cardinality = False

    out: list[Column] = []
    if all(_is_bool(v) for v in non_null):
        out.append(Bool(name, nullable=nullable))
    elif all(_is_int(v) for v in non_null):
        normalized = [_as_int(v) for v in non_null]
        if min(normalized) >= 0:
            bits = max(1, int(max(normalized)).bit_length())
            for r in repeats:
                kw = dict(nullable=nullable, repeat=r)
                out += [Uint(name, **kw), Uint(name, bits=bits, **kw),
                        Uint(name, bits=bits, delta=True, **kw)]
                if not r and bits <= 16:
                    out.append(Uint(name, bits=bits, pack=True, nullable=nullable))
        else:
            for r in repeats:
                out += [Int(name, nullable=nullable, repeat=r),
                        Int(name, delta=True, nullable=nullable, repeat=r)]
        if low_cardinality:
            out.append(Category(name, nullable=nullable))
    elif all(isinstance(v, numbers.Real) and not _is_bool(v) for v in non_null):
        for r in repeats:
            out += [Float(name, nullable=nullable, repeat=r),
                    Float(name, bits=32, nullable=nullable, repeat=r)]
    elif all(isinstance(v, str) for v in non_null):
        out += [Str(name, nullable=nullable, repeat=r) for r in repeats]
        if low_cardinality:
            out.append(Category(name, nullable=nullable))
    elif all(isinstance(v, (bytes, bytearray, memoryview)) for v in non_null):
        out += [Bytes(name, nullable=nullable, repeat=r) for r in repeats]
    else:
        kinds = sorted({type(v).__name__ for v in non_null})
        raise TypeError(f"Column '{name}': can't infer an encoding for mixed/unsupported types {kinds}")
    return out


def explain(data, schema: Schema | None = None, *, compress: str | None = "auto") -> str:
    """Return a table showing each column's isolated size and table total."""
    names, cols, n = _to_columns(data, schema.names if schema is not None else None)
    codec = _compress.resolve(compress)
    if schema is None:
        schema = _infer_columns(names, cols, compress=compress, embed_schema=False)
    total = len(pack(cols, schema, embed_schema=False, compress=compress))

    sizes = [
        len(pack({c.name: cols[c.name]}, Schema(c), embed_schema=False, compress=compress))
        for c in schema
    ]
    denom = sum(sizes) or 1
    width = max(len("column"), *(len(c.name) for c in schema))
    enc_w = max(len("encoding"), *(len(c.describe()) for c in schema))
    lines = [f"{'column':<{width}}  {'encoding':<{enc_w}}  {'bytes':>7}  {'share':>5}"]
    for c, s in zip(schema, sizes):
        lines.append(f"{c.name:<{width}}  {c.describe():<{enc_w}}  {s:>7}  {s * 100 // denom:>4}%")
    lines.append(f"total: {n} rows -> {total} bytes (compress={_codec_name(codec)}, schema not embedded)")
    return "\n".join(lines)


def _codec_name(codec: int) -> str:
    return {v: k for k, v in _compress._NAMES.items()}[codec]
