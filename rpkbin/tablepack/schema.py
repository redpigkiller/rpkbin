"""
Schema description: one typed column object per table column.

Columns are immutable and validate themselves on construction, so a bad
combination (e.g. ``Enum(..., delta=True)``) fails immediately with a message
naming the column.

Column types:
    Uint      non-negative integer (bits=N fixed width, varint, delta, pack)
    Int       signed integer (zigzag varint, optional delta)
    Bool      True / False (costs one flag bit)
    Float     IEEE float, 64-bit by default, 32-bit if lossless
    Bytes     arbitrary binary data
    Str       text
    Enum      one of a fixed, known-ahead-of-time set of str/int values
    Category  like Enum, but the value set is learned from the data at pack time

Flags (where applicable):
    nullable  value may be None (1 extra flag bit per row)
    repeat    value is often identical to the previous row; repeated values
              are not stored again (1 extra flag bit per row)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, Iterator

from ._types import _enum_value

__all__ = [
    "Schema", "Column", "Uint", "Int", "Bool", "Float", "Bytes", "Str", "Enum", "Category",
]

_REGISTRY: dict[str, type[Column]] = {}


def _register(cls):
    _REGISTRY[cls.TYPE] = cls
    return cls


def _is_plain_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass(frozen=True, repr=False)
class Column:
    """Base class: a named column. Use a concrete subclass."""

    TYPE: ClassVar[str] = ""
    # Flags a subclass does not support; passing them raises ValueError.
    _FORBIDDEN: ClassVar[tuple[str, ...]] = ()

    name: str
    nullable: bool = field(default=False, kw_only=True)
    repeat: bool = field(default=False, kw_only=True)

    def __post_init__(self):
        for option in ("nullable", "repeat", "varint", "delta", "pack"):
            if hasattr(self, option) and type(getattr(self, option)) is not bool:
                raise ValueError(f"Column '{self.name}': {option} must be bool")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError(f"Column name must be a non-empty string, got {self.name!r}")
        for flag in self._FORBIDDEN:
            if getattr(self, flag):
                raise ValueError(f"Column '{self.name}': {flag}=True is not supported for {self.TYPE}")
        self._validate()

    def _validate(self) -> None:
        pass

    def describe(self) -> str:
        """Short human-readable encoding summary, e.g. ``uint16+delta``."""
        parts = [self._describe_base()]
        for flag in ("delta", "pack", "repeat", "nullable"):
            if getattr(self, flag, False):
                parts.append(flag)
        return "+".join(parts)

    def _describe_base(self) -> str:
        return self.TYPE

    # Fields always emitted (and shown positionally in repr), even at default.
    _ALWAYS: ClassVar[tuple[str, ...]] = ()

    def _set_fields(self):
        """Yield (field_name, value, always) for fields that matter for output."""
        for f in fields(self):
            if f.name == "name":
                continue
            value = getattr(self, f.name)
            always = f.name in self._ALWAYS
            if always or value != f.default:
                yield f.name, (list(value) if isinstance(value, tuple) else value), always

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.TYPE, "name": self.name}
        for fname, value, _ in self._set_fields():
            out[fname] = value
        return out

    def __repr__(self) -> str:
        items = list(self._set_fields())
        args = [repr(self.name)]
        args += [repr(v) for _, v, always in items if always]
        args += [f"{k}={v!r}" for k, v, always in items if not always]
        return f"{type(self).__name__}({', '.join(args)})"

@_register
@dataclass(frozen=True, repr=False)
class Uint(Column):
    """Non-negative integer.

    With no options the value is a varint (small numbers are small). ``bits``
    sets a fixed width (rounded up to whole bytes) and bounds the value.
    ``delta`` stores the difference from the previous row. ``pack`` embeds the
    value in the shared per-row flag bits instead of its own byte-aligned slot
    (needs ``bits``; best for small fields such as a 4-bit field).
    """

    TYPE: ClassVar[str] = "uint"
    bits: int | None = field(default=None, kw_only=True)
    varint: bool = field(default=False, kw_only=True)
    delta: bool = field(default=False, kw_only=True)
    pack: bool = field(default=False, kw_only=True)

    def _validate(self):
        n = self.name
        if self.bits is not None and (not _is_plain_int(self.bits) or self.bits < 1):
            raise ValueError(f"Column '{n}': bits must be a positive int, got {self.bits!r}")
        if self.pack:
            if self.bits is None:
                raise ValueError(f"Column '{n}': pack=True requires bits")
            if self.repeat or self.delta or self.varint:
                raise ValueError(f"Column '{n}': pack can't combine with repeat/delta/varint")

    def _describe_base(self):
        return f"uint{self.bits}" if self.bits and not self.varint and not self.delta else "uint"


@_register
@dataclass(frozen=True, repr=False)
class Int(Column):
    """Signed integer, stored as a zigzag varint. ``delta`` stores row-to-row differences."""

    TYPE: ClassVar[str] = "int"
    delta: bool = field(default=False, kw_only=True)


@_register
@dataclass(frozen=True, repr=False)
class Bool(Column):
    """True / False, stored in the shared flag bits (1 bit)."""

    TYPE: ClassVar[str] = "bool"
    _FORBIDDEN: ClassVar[tuple[str, ...]] = ("repeat",)


@_register
@dataclass(frozen=True, repr=False)
class Float(Column):
    """IEEE float. ``bits=32`` is only accepted for values exactly representable in float32."""

    TYPE: ClassVar[str] = "float"
    bits: int = field(default=64, kw_only=True)

    def _validate(self):
        if type(self.bits) is not int or self.bits not in (32, 64):
            raise ValueError(f"Column '{self.name}': Float bits must be 32 or 64, got {self.bits!r}")

    def _describe_base(self):
        return f"float{self.bits}"


@_register
@dataclass(frozen=True, repr=False)
class Bytes(Column):
    """Arbitrary binary data."""

    TYPE: ClassVar[str] = "bytes"


@_register
@dataclass(frozen=True, repr=False)
class Str(Column):
    """UTF-8 text."""

    TYPE: ClassVar[str] = "str"


@_register
@dataclass(frozen=True, repr=False)
class Enum(Column):
    """One of a fixed set of str (or int) values, stored in the shared flag bits."""

    TYPE: ClassVar[str] = "enum"
    _FORBIDDEN: ClassVar[tuple[str, ...]] = ("repeat",)
    _ALWAYS: ClassVar[tuple[str, ...]] = ("values",)
    values: tuple = ()

    def __post_init__(self):
        try:
            vals = tuple(_enum_value(v) for v in self.values)
        except (TypeError, ValueError):
            raise ValueError(f"Column '{self.name}': Enum values must be all str or all int") from None
        object.__setattr__(self, "values", vals)
        super().__post_init__()

    def _validate(self):
        n, vals = self.name, self.values
        if not vals:
            raise ValueError(f"Column '{n}': Enum needs at least one value")
        if len(set(vals)) != len(vals):
            raise ValueError(f"Column '{n}': duplicate values in Enum {list(vals)}")
        if not (all(isinstance(v, str) for v in vals) or all(_is_plain_int(v) for v in vals)):
            raise ValueError(f"Column '{n}': Enum values must be all str or all int")

    def _describe_base(self):
        return f"enum({len(self.values)})"


@_register
@dataclass(frozen=True, repr=False)
class Category(Column):
    """Like :class:`Enum`, but the value set (str or int) is learned from the
    data at pack time and stored once in the blob."""

    TYPE: ClassVar[str] = "category"
    _FORBIDDEN: ClassVar[tuple[str, ...]] = ("repeat",)


class Schema:
    """An ordered collection of uniquely named columns."""

    def __init__(self, *columns: Column):
        if len(columns) == 1 and isinstance(columns[0], (list, tuple)):
            columns = tuple(columns[0])
        if not columns:
            raise ValueError("Schema needs at least one column")
        seen = set()
        for c in columns:
            if not isinstance(c, Column):
                raise TypeError(f"Schema expects Column objects, got {c!r}")
            if c.name in seen:
                raise ValueError(f"Duplicate column name '{c.name}'")
            seen.add(c.name)
        self._columns = tuple(columns)

    @property
    def columns(self) -> tuple[Column, ...]:
        return self._columns

    @property
    def names(self) -> list[str]:
        return [c.name for c in self._columns]

    def __iter__(self) -> Iterator[Column]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def __getitem__(self, name: str) -> Column:
        for c in self._columns:
            if c.name == name:
                return c
        raise KeyError(name)

    def __eq__(self, other) -> bool:
        return isinstance(other, Schema) and self._columns == other._columns

    def __hash__(self) -> int:
        return hash(self._columns)

    def __repr__(self) -> str:
        body = "".join(f"    {c!r},\n" for c in self._columns)
        return f"Schema(\n{body})"

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"columns": [c.to_dict() for c in self._columns]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Schema:
        if not isinstance(d, dict) or set(d) != {"columns"} or not isinstance(d["columns"], list):
            raise ValueError("Schema dict must look like {'columns': [...]}") from None
        items = d["columns"]
        columns = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise ValueError("Each column spec must be a dict with a string type")
            item = dict(item)
            tname = item.pop("type", None)
            ctype = _REGISTRY.get(tname)
            if ctype is None:
                raise ValueError(f"Unknown column type {tname!r}")
            try:
                columns.append(ctype(**item))
            except TypeError as e:
                raise ValueError(f"Bad column spec {item!r}: {e}") from None
        return cls(*columns)

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, indent=indent,
            separators=None if indent else (",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> Schema:
        return cls.from_dict(json.loads(text))
