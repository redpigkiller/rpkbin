"""Mechanical contract checks and their independent positive/negative probes."""

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    "MCU.space",
    "MCU.reg",
    "Simulator.__init__",
    "MCU.describe",
    "Simulator.describe",
    "Simulator.next_breakpoint",
    "Simulator.initialized_ranges",
    "MCUDescription.__init__",
    "SpaceInfo.__init__",
    "RegisterInfo.__init__",
    "FieldInfo.__init__",
    "Assembly.records",
    "SourceRecord.__init__",
    "SpaceAccess.read_word",
    "helpers.rotate_left",
    "helpers.add_with_carry",
    "Simulator.load_hex",
    "Simulator.load_bin",
    "repl.run_repl",
]


def _terms():
    return (
        (Path(__file__).with_name("forbidden_terms.txt"))
        .read_text(encoding="utf-8")
        .splitlines()
    )


def _words(text, terms):
    for term in terms:
        pieces = re.split(r"[\s-]+", term)
        pattern = (
            r"(?<![A-Za-z0-9])"
            + r"[\s_-]+".join(map(re.escape, pieces))
            + r"(?![A-Za-z0-9])"
        )
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _scan(root, terms):
    failures = []
    files = list(Path(root).rglob("*"))
    for path in files:
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.resolve() in (
            Path(__file__).resolve(),
            Path(__file__).with_name("forbidden_terms.txt").resolve(),
        ):
            continue
        if _words("/".join(path.relative_to(root).parts), terms):
            failures.append(("H1", str(path)))
        if path.is_file():
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if _words(source, terms):
                failures.append(("H1", str(path)))
    return failures


def _literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left)
        right = _literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "chr"
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Constant)
        and type(node.args[0].value) is int
        and 0 <= node.args[0].value <= 0x10FFFF
    ):
        return chr(node.args[0].value)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                if (
                    part.conversion not in (-1, ord("s"))
                    or part.format_spec is not None
                ):
                    return None
                part = part.value
            value = _literal_string(part)
            if value is None:
                return None
            parts.append(value)
        return "".join(parts)
    return None


def _role_exception(path, tree):
    if path.resolve() != (ROOT / "rpkbin" / "isasim" / "mcu.py").resolve():
        return None
    definitions = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_ROLE_VALUES"
    ]
    if len(definitions) != 1 or not isinstance(definitions[0], ast.Tuple):
        return None
    for item in definitions[0].elts:
        if isinstance(item, ast.Constant) and item.value == "flag":
            return item
    return None


def _constants(root):
    paths = list(Path(root).rglob("*.py"))
    failures = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        exception = _role_exception(path, tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and type(node.value) is int
                and node.value in (255, 256, 65535, 65536)
            ):
                failures.append(("H2", str(path)))
            value = _literal_string(node)
            if (
                node is not exception
                and value is not None
                and re.fullmatch(r"sp|flags?|r\d+", value, re.IGNORECASE)
            ):
                failures.append(("H4", str(path)))
    return paths, failures


def _signature(fn):
    return [
        p.name
        for p in inspect.signature(fn).parameters.values()
        if any(part in p.name for part in ("bits", "size", "endian", "order"))
        and p.default is not inspect.Parameter.empty
        and p.default is not None
    ]


def _apis(module):
    found = {}
    seen = []

    def visit(obj, label):
        if any(obj is previous for previous in seen):
            return
        seen.append(obj)
        if inspect.isfunction(obj):
            if not obj.__name__.startswith("_"):
                found[label] = obj
        elif inspect.isclass(obj) and obj.__module__.startswith("rpkbin.isasim"):
            for name, member in inspect.getmembers(obj):
                if (
                    name == "__init__" or not name.startswith("_")
                ) and inspect.isfunction(member):
                    found[f"{label}.{name}"] = member

    for name in module.__all__:
        visit(getattr(module, name), name)
    for name in ("helpers", "repl", "cli", "state"):
        try:
            child = importlib.import_module(f"rpkbin.isasim.{name}")
        except ModuleNotFoundError as error:
            if error.name != f"rpkbin.isasim.{name}":
                raise
            continue
        for key, obj in vars(child).items():
            if not key.startswith("_"):
                visit(obj, f"{name}.{key}" if inspect.isfunction(obj) else key)
    return found


def test_actual_contract():
    package = ROOT / "rpkbin" / "isasim"
    assert (package / "__init__.py").exists()
    assert not _scan(package, _terms())
    assert not _scan(ROOT / "tests" / "isasim", _terms())
    paths, errors = _constants(package)
    assert paths
    assert not errors
    module = importlib.import_module("rpkbin.isasim")
    names = _apis(module)
    assert all(name in names for name in REQUIRED), names.keys()
    assert not {name: _signature(fn) for name, fn in names.items() if _signature(fn)}


@pytest.mark.parametrize(
    "text,name,hit",
    [
        ("# ACC", "ok.py", True),
        ("", "acc_helper.py", True),
        ("register_bank", "ok.md", True),
        ("psw", "ok.py", True),
        ("access accessor", "ok.py", False),
    ],
)
def test_word_scanner(tmp_path, text, name, hit):
    (tmp_path / name).write_text(text, encoding="utf-8")
    assert bool(_scan(tmp_path, ["acc", "psw", "register bank"])) is hit


@pytest.mark.parametrize(
    "source",
    ["255", "0xFF", "0xff", "256", "0x100", "65535", "0xFFFF", "65536", "0x10000"],
)
def test_numeric_violations(tmp_path, source):
    (tmp_path / "probe.py").write_text(source, encoding="utf-8")
    assert any(code == "H2" for code, _ in _constants(tmp_path)[1])


@pytest.mark.parametrize(
    "source", ["(1 << bits) - 1", "254", "1 << 8", '"255"', "255.0"]
)
def test_numeric_allowed(tmp_path, source):
    (tmp_path / "probe.py").write_text(source, encoding="utf-8")
    assert not _constants(tmp_path)[1]


@pytest.mark.parametrize(
    "source,hit",
    [
        ("def f(x, bits=8): pass", True),
        ('def f(x, *, order="big"): pass', True),
        ("class C:\n def __init__(self, size=16): pass", True),
        ("def f(x, bits): pass", False),
        ("def f(x, bits=None): pass", False),
        ("def _g(bits=8): pass", False),
        ("def f(x, nbits=None): pass", False),
    ],
)
def test_signature_scanner(tmp_path, source, hit):
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    functions = [
        v
        for k, v in vars(module).items()
        if not k.startswith("_") and inspect.isfunction(v)
    ]
    if hasattr(module, "C"):
        functions.append(module.C.__init__)
    assert any(_signature(fn) for fn in functions) is hit


@pytest.mark.parametrize(
    "value,hit",
    [(v, True) for v in ("sp", "SP", "r0", "R15", "flag", "flags")]
    + [(v, False) for v in ("flags.Z", "r", "sp1", "pc", "r{n}")],
)
def test_string_scanner(tmp_path, value, hit):
    (tmp_path / "probe.py").write_text(repr(value), encoding="utf-8")
    assert any(code == "H4" for code, _ in _constants(tmp_path)[1]) is hit


@pytest.mark.parametrize(
    "source",
    [
        "'fl' + 'ag'",
        "chr(102) + 'lag'",
        "f'{chr(102)}lag'",
        "_ROLE_VALUES = ('internal', 'input', 'output', 'inout', 'flag')",
    ],
)
def test_string_scanner_rejects_indirect_or_misplaced_exceptions(tmp_path, source):
    (tmp_path / "mcu.py").write_text(source, encoding="utf-8")
    assert any(code == "H4" for code, _ in _constants(tmp_path)[1])
