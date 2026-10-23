"""rpk-calc — Engineering & Fixed-Point (FXP) interactive calculator."""

from __future__ import annotations

__all__ = ["launch", "main"]


def launch(*args, **kwargs):
    from .ui.app import launch as _launch

    return _launch(*args, **kwargs)


def main(*args, **kwargs):
    from .cli import main as _main

    return _main(*args, **kwargs)
