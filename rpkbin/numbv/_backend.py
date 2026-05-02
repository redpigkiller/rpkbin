"""Backend registry for NumBV.

Supported backends:
  ``"numpy"`` (default) — pure NumPy, always available.
  ``"jax"``             — XLA acceleration via JAX (CPU/GPU/TPU).
                          Requires:  pip install jax

Call :func:`set_backend` *once* at the top of your script, **before**
creating any NumBV objects.  Switching backends after creating objects is
undefined behaviour.

Example::

    import rpkbin.numbv as nbv
    nbv.set_backend("jax")   # enable XLA acceleration

    # All NumBV operations below will use JAX arrays.
    a = nbv.scalar(1.5, fmt=nbv.Format(16, 12))
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as _np

__all__ = ["set_backend", "get_backend", "get_xp", "mark_instantiated"]

# ---------------------------------------------------------------------------
# Global state (module-level singletons)
# ---------------------------------------------------------------------------

_BACKEND: str = "numpy"
_xp: Any = _np          # current array module
_jax_registered: bool = False   # guard against double-registration
_IS_INSTANTIATED: bool = False  # guard against switching after init


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mark_instantiated() -> None:
    """Internal. Mark that at least one NumBV object has been created.
    Used to warn if `set_backend` is called too late."""
    global _IS_INSTANTIATED
    _IS_INSTANTIATED = True


def set_backend(name: str) -> None:
    """Switch the array backend used by all NumBV operations.

    Parameters
    ----------
    name : ``"numpy"`` | ``"jax"``
        The backend to activate.

    Notes
    -----
    * **Must be called before creating any NumBV objects.**
    * JAX requires ``pip install jax``.
    * When JAX is selected, ``jax_enable_x64`` is automatically set to
      ``True`` so that ``int64`` / ``float64`` work correctly.
    * Registers ``NumBV`` as a JAX PyTree on first call, enabling
      transparent use with ``@jax.jit``, ``jax.vmap``, etc.

    Examples
    --------
    >>> import rpkbin.numbv as nbv
    >>> nbv.set_backend("jax")
    >>> fmt = nbv.Format(16, 12)
    >>> a = nbv.scalar(1.5, fmt=fmt)   # _raw is now a jax.Array
    """
    global _BACKEND, _xp, _jax_registered, _IS_INSTANTIATED

    if name not in ("numpy", "jax"):
        raise ValueError(
            f"Unknown backend {name!r}. "
            "Choose 'numpy' (default) or 'jax'."
        )

    if _IS_INSTANTIATED and name != _BACKEND:
        warnings.warn(
            f"set_backend('{name}') called after NumBV objects were already created. "
            "Changing backends now is undefined behaviour and may crash.",
            UserWarning,
            stacklevel=2,
        )

    if name == "numpy":
        _xp = _np

    elif name == "jax":
        try:
            import jax
        except ImportError:
            raise ImportError(
                "JAX is not installed. Install with:  pip install jax"
            ) from None

        # jax_enable_x64 MUST be set before any JAX computation.
        jax.config.update("jax_enable_x64", True)

        import jax.numpy as jnp
        _xp = jnp

        if not _jax_registered:
            _register_jax_pytree()
            _jax_registered = True

    _BACKEND = name


def get_backend() -> str:
    """Return the name of the currently active backend (``'numpy'`` or ``'jax'``)."""
    return _BACKEND


def get_xp() -> Any:
    """Return the active array module (``numpy`` or ``jax.numpy``)."""
    return _xp


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _register_jax_pytree() -> None:
    """Register NumBV with JAX's pytree system.

    After registration:
    * ``@jax.jit`` can trace through NumBV objects transparently.
    * ``jax.vmap`` / ``jax.grad`` work on pipelines that use NumBV.
    * ``_raw`` is the traced (dynamic) leaf; ``_fmt`` is the static
      compile-time constant used as a JIT cache key.

    Called lazily the first time ``set_backend("jax")`` is invoked to avoid
    circular imports (NumBV is defined in ``numbv.py``).
    """
    import jax

    # Deferred import to avoid circular dependency at module load time.
    from rpkbin.numbv import NumBV  # noqa: PLC0415

    # Use register_pytree_node (stable across all JAX versions):
    #   flatten:   NumBV  → ([_raw], fmt)   — _raw is traced, fmt is static
    #   unflatten: (fmt, [_raw_child]) → NumBV
    jax.tree_util.register_pytree_node(
        NumBV,
        lambda x: ([x._raw], x._fmt),
        lambda fmt, children: NumBV._from_raw(children[0], fmt),
    )
