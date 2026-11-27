"""Local GUI debugger for rpkbin.isasim."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .session import DebugSession, relocate_breakpoints

if TYPE_CHECKING:
    from .server import create_server


def __getattr__(name: str) -> Any:
    if name == "create_server":
        from .server import create_server

        return create_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DebugSession", "create_server", "relocate_breakpoints"]
