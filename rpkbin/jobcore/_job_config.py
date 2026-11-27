"""Validation shared by Job definitions, sources and settings."""

import math


def _positive(value, name, *, zero=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (value < 0 if zero else value <= 0)
    ):
        raise ValueError(
            f"{name} must be a finite {'nonnegative' if zero else 'positive'} number"
        )
    return value


def _integer(value, name, *, minimum=None):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or (minimum is not None and value < minimum)
    ):
        raise ValueError(
            f"{name} must be an integer"
            + (f" >= {minimum}" if minimum is not None else "")
        )
    return value
