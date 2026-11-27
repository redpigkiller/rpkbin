"""rpk-calc — Engineering & Fixed-Point (FXP) interactive calculator."""

from __future__ import annotations

__all__ = ["launch", "main", "fxp", "fxpi"]


def __getattr__(name):
    if name in {"fxp", "fxpi"}:
        from .core import public_fxp

        function = getattr(public_fxp, name)
        globals()[name] = function
        return function
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


def launch(*args, **kwargs):
    from .ui.app import launch as _launch

    return _launch(*args, **kwargs)


def main(*args, **kwargs):
    from .cli import main as _main

    return _main(*args, **kwargs)
