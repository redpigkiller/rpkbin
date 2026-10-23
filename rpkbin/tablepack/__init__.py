"""
tablepack -- schema-driven table <-> compact bytes.

Describe columns once (or let ``infer`` guess), then ``pack`` / ``unpack``.
Pure library: it never reads files; you hand it rows and get bytes back.

    from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack

    schema = Schema(
        Str("module", repeat=True),
        Uint("addr", bits=16, delta=True),
        Enum("kind", ["RW", "RO", "W1C"]),
        Str("note", nullable=True),
    )
    blob = pack(rows, schema)      # rows: list[dict] or dict[str, list]
    rows = unpack(blob)            # schema is embedded by default

    blob = pack(rows)              # no schema: inferred and embedded

Optional dependency: ``pip install "rpkbin[table]"`` adds brotli, the default
compressor when available (falls back to zlib otherwise).
"""

from .codec import iter_unpack, pack, unpack
from .infer import explain, infer
from .limits import DecodeLimits
from .schema import Bool, Bytes, Category, Column, Enum, Float, Int, Schema, Str, Uint

__all__ = [
    "Schema", "Column", "Uint", "Int", "Bool", "Float", "Bytes", "Str", "Enum", "Category", "DecodeLimits",
    "pack", "unpack", "iter_unpack", "infer", "explain",
]
