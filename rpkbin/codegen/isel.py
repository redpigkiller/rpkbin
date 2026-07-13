"""Instruction-selection entry points that delegate to a target object."""

from __future__ import annotations

from .asm import AsmFunction
from .ir import Function
from .lir import Fragment
from .target import Target


def select_instructions(func: Function, target: Target) -> AsmFunction:
    return target.select_instructions(func)


def select_fragment_instructions(fragment: Fragment, target) -> AsmFunction:
    """Delegates to ``target.select_fragment_instructions(fragment)``.

    Parameters
    ----------
    fragment:
        The lowered LIR Fragment to emit pseudo ASM for.
    target:
        A target object that may implement ``select_fragment_instructions``.

    Returns
    -------
    AsmFunction
        The pseudo-ASM output.

    Raises
    ------
    TypeError
        If *target* does not support fragment instruction selection.
    """
    selector = getattr(target, "select_fragment_instructions", None)
    if selector is None:
        raise TypeError(
            f"target {type(target).__name__!r} does not support fragment "
            f"instruction selection.  The target must implement "
            f"`select_fragment_instructions(self, fragment: Fragment) -> AsmFunction`."
        )
    return selector(fragment)
