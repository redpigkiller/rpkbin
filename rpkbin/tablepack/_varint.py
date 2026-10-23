"""Variable-length integer helpers (LEB128 varint + zigzag)."""

from __future__ import annotations


def encode_varint(n: int) -> bytes:
    """Unsigned LEB128 encoding; small numbers cost fewer bytes."""
    if n < 0:
        raise ValueError("varint cannot encode a negative number")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(data: bytes, pos: int, *, max_bytes: int | None = None) -> tuple[int, int]:
    """Reverse of :func:`encode_varint`. Returns ``(value, next_pos)``."""
    result = shift = 0
    start = pos
    while True:
        if max_bytes is not None and pos - start >= max_bytes:
            raise ValueError("varint exceeds byte limit")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def zigzag_encode(n: int) -> int:
    """Map signed ints to unsigned so negatives stay small as varints."""
    return (n << 1) if n >= 0 else ((-n << 1) - 1)


def zigzag_decode(n: int) -> int:
    return (n >> 1) if (n & 1) == 0 else -((n + 1) >> 1)
