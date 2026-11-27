"""Public compilation errors."""


class McuccError(Exception):
    """Base for expected diagnostics."""


class SourceError(McuccError):
    """Invalid source language."""


class TargetError(McuccError):
    """Invalid machine declaration."""


class _EncodeLengthError(TargetError):
    """A target encoder returned a different number of cells than declared."""


class UnsupportedError(McuccError):
    """Missing operation or mechanism."""


class AllocationError(McuccError):
    """Insufficient storage resources."""


class ConfigError(McuccError):
    """Invalid compilation settings."""


class InternalError(McuccError):
    """Compiler invariant or independent verification failure."""
