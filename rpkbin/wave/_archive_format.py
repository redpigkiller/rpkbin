"""Archive encoding and limits shared by writer and readers."""

import codecs
import json

SCHEMA_VERSION = 4
BATCH_RECORDS = 256
BATCH_BYTES = 256 * 1024
PAGE_LIMIT = 100
PREVIEW_CHARS = 1024
CHUNK_CHARS = 64 * 1024
PAGE_BYTES = 256 * 1024
READER_SCAN_LIMIT = 1000
READER_SECONDS = .15
READER_TEXT_BYTES = 8 * 1024 * 1024


def _text_prefix(raw):
    # SQLite substr() returns NULL for an empty BLOB or a slice at its end.
    raw = b"" if raw is None else raw
    # A byte page may end inside a Unicode code point. Keep the remainder for
    # the next request; offsets for logs are bytes, never JavaScript indices.
    decoder = codecs.getincrementaldecoder("utf-8")("surrogatepass")
    text = decoder.decode(raw, final=False)
    return text, len(raw) - len(decoder.getstate()[0])


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
