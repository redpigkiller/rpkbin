"""Pluggable compression back-ends. The chosen one is recorded in the blob header."""

from __future__ import annotations

import lzma
import zlib

NONE, ZLIB, LZMA, BROTLI = 0, 1, 2, 3

_NAMES = {"none": NONE, "zlib": ZLIB, "lzma": LZMA, "brotli": BROTLI}
_MISSING_MSG = (
    "brotli is not installed. Install it with: pip install \"rpkbin[table]\" "
    "(or pack with compress='zlib')."
)


def _brotli():
    try:
        import brotli
    except ImportError:
        raise ImportError(_MISSING_MSG) from None
    if not hasattr(brotli.Decompressor, "can_accept_more_data"):
        raise ImportError("TablePack needs brotli >= 1.2 for bounded decoding; upgrade rpkbin[table].")
    return brotli


def resolve(method) -> int:
    """Turn a user-facing ``compress`` argument into a codec id."""
    if method is None:
        return NONE
    if method == "auto":
        try:
            _brotli()
        except ImportError:
            return ZLIB
        return BROTLI
    if method not in _NAMES:
        raise ValueError(
            f"Unknown compress={method!r}; use 'auto', 'brotli', 'zlib', 'lzma' or None"
        )
    return _NAMES[method]


def compress(data: bytes, codec: int) -> bytes:
    if codec == NONE:
        return data
    if codec == ZLIB:
        return zlib.compress(data, 9)
    if codec == LZMA:
        return lzma.compress(data, preset=9 | lzma.PRESET_EXTREME)
    if codec == BROTLI:
        return _brotli().compress(data, quality=11)
    raise ValueError(f"Unknown codec id {codec}")


def decompress(data: bytes, codec: int, *, max_output_bytes: int) -> bytes:
    """Read exactly one compressed stream, bounding decoded output as it grows."""
    if codec == NONE:
        if len(data) > max_output_bytes:
            raise ValueError("payload exceeds max_payload_bytes")
        return data
    if codec == ZLIB:
        decoder = zlib.decompressobj()
        out = decoder.decompress(data, max_output_bytes + 1)
        if len(out) > max_output_bytes or decoder.unconsumed_tail:
            raise ValueError("payload exceeds max_payload_bytes")
        if not decoder.eof or decoder.unused_data:
            raise ValueError("truncated stream or trailing compressed data")
        return out
    if codec == LZMA:
        # V1's writer uses a 64 MiB dictionary. Reject hostile XZ headers asking
        # for larger decoder allocations before producing even one output byte.
        decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
        out = decoder.decompress(data, max_length=max_output_bytes + 1)
        if len(out) > max_output_bytes:
            raise ValueError("payload exceeds max_payload_bytes")
        if not decoder.eof or decoder.unused_data:
            raise ValueError("truncated stream or trailing compressed data")
        return out
    if codec == BROTLI:
        decoder = _brotli().Decompressor()
        parts, total = [], 0
        while True:
            chunk = decoder.process(data, output_buffer_limit=min(65536, max_output_bytes - total + 1))
            data = b""
            total += len(chunk)
            if total > max_output_bytes:
                raise ValueError("payload exceeds max_payload_bytes")
            parts.append(chunk)
            if decoder.is_finished():
                return b"".join(parts)
            # A decoder may accept input while still holding output. Drain it
            # with empty input until finished; only no progress means EOF was
            # reached before the compressed stream was complete.
            if not chunk:
                raise ValueError("truncated brotli stream")
    raise ValueError(f"Unknown codec id {codec} (blob from a newer version?)")
