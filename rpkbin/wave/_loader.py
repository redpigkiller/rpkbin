"""Safe execution and reload helpers for Wave Python definition files."""

from __future__ import annotations

import importlib
import importlib.machinery
import os
import runpy
import site
import sys
import sysconfig
import threading
from pathlib import Path
from typing import Any

_LOAD_LOCK = threading.RLock()
_BASELINE_MODULES: set[str] | None = None
_RPKBIN_ROOT = Path(__file__).resolve().parents[1]


def _system_roots() -> tuple[Path, ...]:
    roots: list[Path] = [_RPKBIN_ROOT]
    candidates: list[str] = [os.path.dirname(os.__file__)]
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = sysconfig.get_path(key)
        if value:
            candidates.append(value)
    for prefix in (sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix):
        if prefix:
            candidates.append(os.path.join(prefix, "Lib"))
            candidates.append(os.path.join(prefix, "lib"))
            candidates.append(os.path.join(prefix, "DLLs"))
    try:
        candidates.extend(site.getsitepackages())
    except AttributeError:
        pass
    user_site = site.getusersitepackages()
    if isinstance(user_site, str) and user_site:
        candidates.append(user_site)
    seen: set[Path] = set()
    for raw in candidates:
        try:
            resolved = Path(raw).resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)
    return tuple(roots)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_system_path(path: Path, roots: tuple[Path, ...]) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    if "site-packages" in lowered_parts or "dist-packages" in lowered_parts:
        return True
    return any(_is_under(path, root) for root in roots)


def _is_reloadable_candidate(name: str, module: Any, roots: tuple[Path, ...]) -> bool:
    if module is None or name == "__main__" or name == "rpkbin" or name.startswith("rpkbin."):
        return False
    spec = getattr(module, "__spec__", None)
    loader = getattr(spec, "loader", None)
    if loader is not None and isinstance(loader, importlib.machinery.ExtensionFileLoader):
        return False
    origin = getattr(spec, "origin", None) or getattr(module, "__file__", None)
    if isinstance(origin, str):
        if not origin.lower().endswith((".py", ".pyw")):
            return False
        try:
            resolved = Path(origin).resolve()
        except OSError:
            return False
        return not _is_system_path(resolved, roots)
    if origin in (None, "namespace") and hasattr(module, "__path__"):
        raw_paths = list(getattr(module, "__path__", ()) or ())
        if not raw_paths:
            return False
        try:
            resolved_paths = [Path(p).resolve() for p in raw_paths]
        except OSError:
            return False
        return all(not _is_system_path(p, roots) for p in resolved_paths)
    return False


def _evict_user_modules(baseline: set[str]) -> list[str]:
    roots = _system_roots()
    candidates: set[str] = set()
    non_reloadable: set[str] = set()
    for name, module in list(sys.modules.items()):
        if name in baseline:
            non_reloadable.add(name)
        elif _is_reloadable_candidate(name, module, roots):
            candidates.add(name)
        else:
            non_reloadable.add(name)
    evict = [
        name
        for name in candidates
        if not any(other.startswith(f"{name}.") for other in non_reloadable)
    ]
    for name in sorted(evict, key=len, reverse=True):
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    return evict


def run_definition_script(path: Path, *, reload_modules: bool = False) -> dict[str, Any]:
    """Run a trusted definition file, optionally reloading post-baseline user modules."""
    global _BASELINE_MODULES
    script_dir = str(path.parent)
    with _LOAD_LOCK:
        if _BASELINE_MODULES is None:
            _BASELINE_MODULES = set(sys.modules)
        if reload_modules:
            _evict_user_modules(_BASELINE_MODULES)
        else:
            importlib.invalidate_caches()
        inserted = not sys.path or sys.path[0] != script_dir
        if inserted:
            sys.path.insert(0, script_dir)
        try:
            return runpy.run_path(str(path), run_name="__wave_definition__")
        finally:
            if inserted and sys.path and sys.path[0] == script_dir:
                sys.path.pop(0)
