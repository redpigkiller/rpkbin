"""Incremental decoding primitives shared by process and file readers."""

import codecs
import contextvars
import re

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
DECODE_REPLACEMENTS = contextvars.ContextVar("wave_decode_replacements", default=0)
REPLACE_HANDLER = "rpkbin.jobcore.output_replace"


def _counted_replace(error):
    replacement = codecs.replace_errors(error)
    DECODE_REPLACEMENTS.set(DECODE_REPLACEMENTS.get() + 1)
    return replacement


codecs.register_error(REPLACE_HANDLER, _counted_replace)


def _output_encoding(encoding):
    # Reject unknown and non-text codecs before a child can start.
    "".encode(encoding)
    name = codecs.lookup(encoding).name
    codecs.getincrementaldecoder(name)
    return name


def _decode_policy(value):
    if not isinstance(value, str) or value not in ("replace", "strict"):
        raise ValueError("decode_errors must be 'replace' or 'strict'")
    return value


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)

