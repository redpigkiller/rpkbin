"""rpkbin.numbv — Bit-exact fixed-point simulator."""

from rpkbin.numbv.core import (
    # Core classes
    Format,
    NumBV,
    # Factory
    scalar,
    array,
    zeros,
    ones,
    full,
    from_bits,
    # Function-level arithmetic
    add,
    sub,
    mul,
    neg,
    # Reduction / DSP
    dot,
    mac,
    sum,
    # Format helpers
    infer_add_format,
    infer_mul_format,
)
# Re-export sum under nbv namespace without shadowing Python builtins
from rpkbin.numbv import core as nbv  # noqa: F401
from rpkbin.numbv._backend import set_backend, get_backend

__all__ = [
    # NumBV
    "Format", "NumBV",
    "scalar", "array", "zeros", "ones", "full", "from_bits",
    "add", "sub", "mul", "neg", "sum",
    "dot", "mac",
    "infer_add_format", "infer_mul_format",
    "nbv",
    # Backend control
    "set_backend", "get_backend",
]
