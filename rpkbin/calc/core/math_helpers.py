"""Built-in engineering math, linear algebra, fitting, communication, and FFT helpers for rpk-calc."""

from __future__ import annotations

import math
import traceback
from pathlib import Path
import tokenize
from typing import Any

import apytypes as apy
import numpy as np

from .config import CalcConfig
from .auto_imports import apply_imports


def _to_numeric(x: Any) -> Any:
    """Convert APyFixed / APyFixedArray to float / numpy ndarray for floating-point math functions."""
    if isinstance(x, apy.APyFixed):
        return float(x)
    if isinstance(x, apy.APyFixedArray):
        return np.asarray(x.to_numpy(), dtype=float)
    if isinstance(x, (list, tuple)):
        return np.asarray(x)
    return x


def _wrap_unary(np_func):
    def _fn(x: Any) -> Any:
        res = np_func(_to_numeric(x))
        if isinstance(res, np.generic):
            return res.item()
        return res

    _fn.__name__ = getattr(np_func, "__name__", "func")
    return _fn


def qfunc(x: Any) -> Any:
    """Gaussian Q-function: Q(x) = 0.5 * erfc(x / sqrt(2))."""
    val = _to_numeric(x)
    if isinstance(val, np.ndarray):
        vec_erfc = np.vectorize(math.erfc)
        return 0.5 * vec_erfc(val / math.sqrt(2.0))
    return 0.5 * math.erfc(float(val) / math.sqrt(2.0))


def erfc_fn(x: Any) -> Any:
    """Complementary error function erfc(x)."""
    val = _to_numeric(x)
    if isinstance(val, np.ndarray):
        return np.vectorize(math.erfc)(val)
    return math.erfc(float(val))


def erf_fn(x: Any) -> Any:
    """Error function erf(x)."""
    val = _to_numeric(x)
    if isinstance(val, np.ndarray):
        return np.vectorize(math.erf)(val)
    return math.erf(float(val))


def linfit(x: Any, y: Any) -> np.ndarray:
    """1st-order linear least-squares fit returning array([slope, intercept])."""
    return np.polyfit(_to_numeric(x), _to_numeric(y), 1)


def polyfit_fn(x: Any, y: Any, deg: int) -> np.ndarray:
    """Polynomial least-squares fit of degree `deg`."""
    return np.polyfit(_to_numeric(x), _to_numeric(y), int(deg))


def polyval_fn(p: Any, x: Any) -> Any:
    """Evaluate polynomial `p` at `x`."""
    res = np.polyval(_to_numeric(p), _to_numeric(x))
    if isinstance(res, np.generic):
        return res.item()
    return res


def build_namespace_symbols(config: CalcConfig, *, run_startup=True) -> dict[str, Any]:
    """Build the dictionary of pre-loaded functions and modules based on CalcConfig."""

    symbols: dict[str, Any] = {
        "np": np,
        "apy": apy,
        "math": math,
        "APyFixed": apy.APyFixed,
        "APyFixedArray": apy.APyFixedArray,
        "APyFloat": apy.APyFloat,
        "APyFloatArray": apy.APyFloatArray,
    }

    groups = config.auto_import_groups

    if groups.get("basic_math", True):
        symbols.update(
            {
                "sin": _wrap_unary(np.sin),
                "cos": _wrap_unary(np.cos),
                "tan": _wrap_unary(np.tan),
                "asin": _wrap_unary(np.arcsin),
                "acos": _wrap_unary(np.arccos),
                "atan": _wrap_unary(np.arctan),
                "atan2": lambda y, x: np.arctan2(_to_numeric(y), _to_numeric(x)),
                "exp": _wrap_unary(np.exp),
                "log": _wrap_unary(np.log),
                "log2": _wrap_unary(np.log2),
                "log10": _wrap_unary(np.log10),
                "sqrt": _wrap_unary(np.sqrt),
                "deg2rad": _wrap_unary(np.deg2rad),
                "rad2deg": _wrap_unary(np.rad2deg),
                "pi": math.pi,
                "e": math.e,
            }
        )

    if groups.get("linalg", True):
        symbols.update(
            {
                "svd": lambda a, full_matrices=True: np.linalg.svd(
                    _to_numeric(a), full_matrices=full_matrices
                ),
                "eig": lambda a: np.linalg.eig(_to_numeric(a)),
                "inv": lambda a: np.linalg.inv(_to_numeric(a)),
                "pinv": lambda a: np.linalg.pinv(_to_numeric(a)),
                "norm": lambda a, ord=None: float(
                    np.linalg.norm(_to_numeric(a), ord=ord)
                ),
                "det": lambda a: np.linalg.det(_to_numeric(a)).item(),
                "eye": np.eye,
                "zeros": np.zeros,
                "ones": np.ones,
                "diag": np.diag,
                "arange": np.arange,
                "linspace": np.linspace,
            }
        )

    if groups.get("fitting_comm", True):
        symbols.update(
            {
                "polyfit": polyfit_fn,
                "polyval": polyval_fn,
                "linfit": linfit,
                "qfunc": qfunc,
                "erfc": erfc_fn,
                "erf": erf_fn,
            }
        )

    if groups.get("fft", True):
        symbols.update(
            {
                "fft": lambda a, n=None: np.fft.fft(_to_numeric(a), n=n),
                "ifft": lambda a, n=None: np.fft.ifft(_to_numeric(a), n=n),
                "fftshift": lambda a: np.fft.fftshift(_to_numeric(a)),
                "ifftshift": lambda a: np.fft.ifftshift(_to_numeric(a)),
            }
        )

    apply_imports(config.custom_imports, symbols)
    # Standalone callers may run startup here; WorkspaceSession uses its live namespace.
    if run_startup:
        apply_startup(config, symbols)
    return symbols


def apply_startup(config, symbols):
    """Execute user code in the prepared namespace with a useful traceback path."""
    try:
        source, filename = config.startup_script, "<calc-startup>"
        if config.startup_file:
            filename = config.startup_file
            with tokenize.open(Path(filename)) as stream:
                source = stream.read()
        if source.strip():
            exec(compile(source, filename, "exec"), symbols)
        symbols["_calc_startup_status"] = {"path": config.startup_file, "ok": True}
    except (Exception, SystemExit, KeyboardInterrupt):
        # Preserve a usable console, but make startup failures observable.
        symbols["_calc_startup_error"] = traceback.format_exc().strip()
        symbols["_calc_startup_status"] = {"path": config.startup_file, "ok": False}
