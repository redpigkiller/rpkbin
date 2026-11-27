"""Headless core logic for rpk-calc."""

from importlib import import_module

from .config import CalcConfig
from .format_engine import FormatEngine, FxpSpec
from .scalar_fxp import BitCellInfo

_MODEL_EXPORTS = ("InspectorSnapshot", "RpkFixed", "RpkFixedArray", "make_fxp", "make_fxpi")
_SESSION_EXPORTS = ("ExecutionResult", "MatrixSnapshot", "WorkspaceRowSnapshot", "WorkspaceSession")
__all__ = ["BitCellInfo", "CalcConfig", "FormatEngine", "FxpSpec", *_MODEL_EXPORTS, *_SESSION_EXPORTS]


def __getattr__(name):
    if name in _MODEL_EXPORTS:
        module = ".fxp_model"
    elif name in _SESSION_EXPORTS:
        module = ".session"
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
