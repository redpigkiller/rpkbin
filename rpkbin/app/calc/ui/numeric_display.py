"""Bounded, read-only numeric formatting; never feed this output to edits or copies."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re

from ..core.config import CalcConfig


_REAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
MAX_FIXED_CHARACTERS = 256


def format_numeric_text(text: str, cfg: CalcConfig) -> str:
    """Format one finite real using decimal places; auto/non-scalars retain source.

    Decimal parses the displayed source directly so arbitrary Python integers
    never pass through binary float. Fixed output falls back to scientific when
    its estimated length exceeds the view's character budget.
    """
    mode = cfg.numeric_display
    if mode not in ("scientific", "fixed") or not _REAL_TEXT.fullmatch(text.strip()):
        return text
    precision = cfg.numeric_precision
    if type(precision) is not int or not 1 <= precision <= 17:
        precision = 6
    try:
        number = Decimal(text.strip())
        if not number.is_finite():
            return text
        if number.is_zero() and mode == "scientific":
            return f"{'-' if number.is_signed() else ''}0.{precision * '0'}E+0"
        if mode == "fixed":
            integer_digits = 1 if number.is_zero() else max(1, number.adjusted() + 1)
            estimated_length = integer_digits + precision + 1 + int(number.is_signed())
            if estimated_length > MAX_FIXED_CHARACTERS:
                mode = "scientific"
        with localcontext() as context:
            context.rounding = ROUND_HALF_EVEN
            return format(number, f".{precision}{'E' if mode == 'scientific' else 'f'}")
    except (InvalidOperation, ValueError, OverflowError):
        return text
