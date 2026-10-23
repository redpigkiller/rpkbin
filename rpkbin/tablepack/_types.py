"""Scalar normalisation without importing optional array libraries."""

import operator


def _is_bool(value) -> bool:
    cls = type(value)
    return isinstance(value, bool) or (
        cls.__module__ == "numpy" and cls.__name__ in ("bool", "bool_")
    )


def _type_name(value) -> str:
    cls = type(value)
    return cls.__name__ if cls.__module__ == "builtins" else f"{cls.__module__}.{cls.__name__}"


def _as_int(value) -> int:
    if _is_bool(value):
        raise TypeError(f"expected int, got {_type_name(value)}")
    try:
        return operator.index(value)
    except TypeError:
        raise TypeError(f"expected int, got {_type_name(value)}") from None


def _enum_value(value):
    if isinstance(value, str):
        return str.__str__(value)
    return _as_int(value)
