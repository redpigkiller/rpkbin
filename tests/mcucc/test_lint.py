import ast
from dataclasses import dataclass
import importlib
import inspect
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
TERMS = Path(__file__).with_name("forbidden_terms.txt").read_text().splitlines()
EXCLUDED = {Path(__file__).resolve(), Path(__file__).with_name("forbidden_terms.txt").resolve()}


def forbidden(text):
    return [term for term in TERMS if re.search(
        r"(?<![a-zA-Z0-9])" + r"[\s_-]+".join(re.escape(word) for word in term.split())
        + r"(?![a-zA-Z0-9])", text, re.I)]


def ast_violations(source):
    result = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if type(node.value) is int and node.value in {255, 256, 65535, 65536}:
                result.append("H2")
            if isinstance(node.value, str) and re.fullmatch(r"sp|flags?|r\d+|x\d+|pc", node.value, re.I):
                result.append("H4")
    return result


def signature_violations(obj):
    if inspect.isclass(obj):
        obj = obj.__init__
    return [p.name for p in inspect.signature(obj).parameters.values()
            if any(word in p.name for word in ("bits", "size", "endian", "order"))
            and p.default not in (inspect.Parameter.empty, None)]


@pytest.mark.parametrize("term", TERMS)
def test_h1_negative_and_boundary(term):
    assert forbidden("_" + term.upper() + "-")
    assert not forbidden("q" + term.replace(" ", "") + "q")
    if " " in term:
        assert forbidden(term.replace(" ", "_-"))


def test_negative_ast_and_signature():
    for value in ("255", "0xff", "256", "65535", "65536"):
        assert "H2" in ast_violations("a = " + value)
    for value in ("SP", "flags", "R12", "x4", "pc"):
        assert "H4" in ast_violations("a = " + repr(value))
    assert signature_violations(lambda addr_bits=8: None) == ["addr_bits"]
    class Example:
        def __init__(self, size=8):
            pass
        def action(self, order="little"):
            pass
    assert signature_violations(Example) == ["size"]
    assert signature_violations(Example.action) == ["order"]
    @dataclass
    class DataExample:
        addr_bits: int = 8
    assert signature_violations(DataExample) == ["addr_bits"]


def test_project_neutrality():
    files = nodes = apis = 0
    for directory in (ROOT / "rpkbin" / "mcucc", ROOT / "tests" / "mcucc"):
        for path in sorted(directory.rglob("*")):
            if "__pycache__" in path.parts or path.suffix == ".pyc" or path.resolve() in EXCLUDED:
                continue
            assert not forbidden(str(path.relative_to(directory))), path
            if not path.is_file():
                continue
            files += 1
            source = path.read_text(encoding="utf-8")
            assert not forbidden(source), path
            if directory.name != "mcucc" or directory.parent.name != "rpkbin" or path.suffix != ".py":
                continue
            tree = ast.parse(source)
            nodes += sum(isinstance(n, ast.Constant) for n in ast.walk(tree))
            assert not ast_violations(source), path
            name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            if path.name == "__main__.py":
                continue
            module = importlib.import_module(name)
            for key, obj in vars(module).items():
                if key.startswith("_") or not (inspect.isfunction(obj) or inspect.isclass(obj)) or obj.__module__ != module.__name__:
                    continue
                apis += 1
                assert not signature_violations(obj), (name, key)
                if inspect.isclass(obj):
                    for method, fn in vars(obj).items():
                        if not method.startswith("_") and inspect.isfunction(fn):
                            apis += 1
                            assert not signature_violations(fn), (name, key, method)
    assert files > 0 and nodes > 0 and apis > 0
