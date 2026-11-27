"""Enforce both standalone packages’ import boundaries."""

import ast
import sys
from importlib.util import find_spec, resolve_name
from pathlib import Path

import pytest


_FLOW = "rpkbin.flow"
_CORE = "rpkbin.jobcore"
_UNRESOLVED = "<unresolved dynamic import>"
_ROOT = Path(__file__).resolve().parents[2] / "rpkbin"


def _names(node, *keywords, position):
    if len(node.args) > position:
        return node.args[position]
    return next((item.value for item in node.keywords if item.arg in keywords), None)


def _literal(node):
    return node.value if isinstance(node, ast.Constant) else None


def _resolved(name, package):
    if not isinstance(name, str):
        return _UNRESOLVED
    try:
        return resolve_name(name, package)
    except ImportError:
        return _UNRESOLVED


def _unexpected_imports(source, package, allowed=_FLOW):
    nodes = list(ast.walk(ast.parse(source)))
    imports = set()
    loaders = {"__import__": "__import__", "import_module": "import_module"}

    for node in nodes:
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolved("." * node.level + (node.module or ""), package)
            if module == "rpkbin":
                imports.update(f"{module}.{alias.name}" for alias in node.names)
            else:
                imports.add(module)
            if module in ("importlib", "builtins"):
                for alias in node.names:
                    if alias.name in loaders:
                        loaders[alias.asname or alias.name] = loaders[alias.name]

    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            loader = loaders.get(function.id)
        elif isinstance(function, ast.Attribute):
            loader = loaders.get(function.attr)
        else:
            continue
        if loader is None:
            continue
        name = _literal(_names(node, "name", position=0))
        if loader == "import_module":
            target = _names(node, "package", position=1)
            target_package = package if isinstance(target, ast.Name) and target.id == "__package__" else _literal(target)
            imports.add(_resolved(name, target_package))
        else:
            level_node = _names(node, "level", position=4)
            level = 0 if level_node is None else _literal(level_node)
            if isinstance(name, str) and type(level) is int and level >= 0:
                module = _resolved("." * level + name, package) if level else name
                fromlist_node = _names(node, "fromlist", position=3)
                if module == "rpkbin" and isinstance(fromlist_node, (ast.List, ast.Tuple)) and fromlist_node.elts:
                    imports.update(
                        f"{module}.{item.value}" if isinstance(item, ast.Constant) and isinstance(item.value, str)
                        else _UNRESOLVED
                        for item in fromlist_node.elts
                    )
                else:
                    imports.add(module)
            else:
                imports.add(_UNRESOLVED)

    return {name for name in imports if not (
        name == allowed or name.startswith(allowed + ".") or name.split(".")[0] in sys.stdlib_module_names
    )}


def _scan_package(root, allowed=_FLOW):
    failures = {}
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        package = ".".join((allowed, *relative.parent.parts))
        if unexpected := _unexpected_imports(path.read_text(encoding="utf-8"), package, allowed):
            failures[relative.as_posix()] = sorted(unexpected)
    return failures


def test_flow_imports_only_allowed_dependencies():
    assert (_ROOT / "flow").is_dir()
    assert not _scan_package(_ROOT / "flow")


def test_jobcore_imports_only_allowed_dependencies():
    assert (_ROOT / "jobcore").is_dir()
    assert not _scan_package(_ROOT / "jobcore", _CORE)


@pytest.mark.parametrize(("source", "dependency"), [
    ("def factory():\n    from rpkbin.wave import Job\n    return Job", "rpkbin.wave"),
    ("import rpkbin.job_manager as jm\nmanager = jm.JobManager()", "rpkbin.job_manager"),
    ("from .. import flow as internal, wave as forwarded", "rpkbin.wave"),
    ("from ..jobcore import Job", "rpkbin.jobcore"),
    ("from rpkbin import Job as forwarded", "rpkbin.Job"),
    ("from rpkbin import *", "rpkbin.*"),
    ("import rpkbin as forwarded\nforwarded.Job", "rpkbin"),
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import rpkbin.jobcore", "rpkbin.jobcore"),
    ("import other_dependency", "other_dependency"),
    ("import importlib as il\nil.import_module('rpkbin.wave')", "rpkbin.wave"),
    ("from importlib import import_module as load\nload('..jobcore', package=__package__)", "rpkbin.jobcore"),
    ("__import__('rpkbin.job_manager')", "rpkbin.job_manager"),
    ("from builtins import __import__ as load\nload('rpkbin', fromlist=('wave',))", "rpkbin.wave"),
    ("__import__('', globals(), fromlist=('wave',), level=2)", "rpkbin.wave"),
    ("import importlib\nimportlib.import_module(target())", _UNRESOLVED),
    ("__import__('rpkbin', fromlist=target())", "rpkbin"),
])
def test_flow_import_boundary_rejects_forbidden_dependencies(source, dependency):
    assert _unexpected_imports(source, _FLOW) == {dependency}


def test_flow_import_boundary_accepts_local_and_standard_library_imports():
    source = """
from . import _session
from .. import flow
from collections.abc import Mapping
from importlib import import_module as load

load("._session", __package__)
__import__("_session", globals(), fromlist=("Flow",), level=1)
"""
    assert not _unexpected_imports(source, _FLOW)


@pytest.mark.parametrize("package", [_FLOW, _CORE])
def test_import_boundary_walks_nested_packages_and_initializers(tmp_path, package):
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "__init__.py").write_text("from . import nested", encoding="utf-8")
    (nested / "__init__.py").write_text("from ... import job_manager", encoding="utf-8")
    (nested / "delayed.py").write_text(
        "def load():\n    from ...wave import WaveServer", encoding="utf-8"
    )
    assert _scan_package(tmp_path, package) == {
        "nested/__init__.py": ["rpkbin.job_manager"],
        "nested/delayed.py": ["rpkbin.wave"],
    }


@pytest.mark.parametrize(("source", "dependency"), [
    ("def later():\n    from .. import flow", "rpkbin.flow"),
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .. import wave", "rpkbin.wave"),
    ("from rpkbin import WaveWorkspace", "rpkbin.WaveWorkspace"),
    ("from importlib import import_module as load\nload('..wave', __package__)", "rpkbin.wave"),
    ("__import__('rpkbin.job_manager')", "rpkbin.job_manager"),
])
def test_jobcore_rejects_sibling_and_forwarded_imports(source, dependency):
    assert _unexpected_imports(source, _CORE, _CORE) == {dependency}


def test_retired_package_is_unimportable():
    assert find_spec("rpkbin.job_manager") is None
