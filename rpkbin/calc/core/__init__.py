"""Headless core logic for rpk-calc (zero GUI dependencies)."""

from .config import CalcConfig
from .format_engine import FormatEngine, FxpSpec
from .fxp_model import (
    BitCellInfo,
    InspectorSnapshot,
    RpkFixed,
    RpkFixedArray,
    make_fxp,
    make_fxpi,
)
from .session import (
    ExecutionResult,
    MatrixSnapshot,
    WorkspaceRowSnapshot,
    WorkspaceSession,
)

__all__ = [
    "BitCellInfo",
    "CalcConfig",
    "ExecutionResult",
    "FormatEngine",
    "FxpSpec",
    "InspectorSnapshot",
    "MatrixSnapshot",
    "RpkFixed",
    "RpkFixedArray",
    "WorkspaceRowSnapshot",
    "WorkspaceSession",
    "make_fxp",
    "make_fxpi",
]
