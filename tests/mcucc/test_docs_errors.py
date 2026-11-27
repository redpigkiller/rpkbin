"""Reproduce every diagnostic cited in either mcucc error table."""

from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.errors import (
    AllocationError, ConfigError, SourceError, TargetError, UnsupportedError,
)
from rpkbin.mcucc.target import Form, M, R, load_target


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "rpkbin" / "mcucc"
EXAMPLES = DOCS / "examples"
EXPECTED = {
    "first-width": SourceError,
    "statement": SourceError,
    "operation": SourceError,
    "recursive": SourceError,
    "target-width": TargetError,
    "address-kind": TargetError,
    "missing-form": UnsupportedError,
    "raw-code": UnsupportedError,
    "allocation": AllocationError,
    "pool-contiguous": AllocationError,
    "invalid-depth": ConfigError,
    "encoder-length": TargetError,
}
CLI = {"window": 2, "verify-driver": 2, "forms-failure": 1}


def _replace_encoder(target):
    forms = tuple(replace(form, encode=lambda values: ()) if form.id == "ret" else form
                  for form in target.forms)
    return replace(target, forms=forms)


def _api_failure(key):
    target = load_target(EXAMPLES / "target.py.txt")
    source = (EXAMPLES / "program.py.txt").read_text(encoding="utf-8")
    config = CompileConfig({"data": (96, 111)}, False)
    bodies = {
        "first-width": "    value = 1\n    return a + value",
        "statement": "    try:\n        return a\n    except:\n        return b",
        "operation": "    return a * b",
        "recursive": "    return example(a, b)",
        "missing-form": "    return a - b",
        "allocation": "    temporary = a + b\n    return temporary + a + b",
        "raw-code": '    asm("inksum {0}, {1}", ins=(a, b), outs=(a,), nbytes=1)\n    return a',
    }
    if key in bodies:
        source = ("from rpkbin.mcucc.lang import u8, asm\n\n"
                  "def example(a: u8, b: u8) -> u8:\n" + bodies[key] + "\n")
    if key == "invalid-depth":
        config = replace(config, park_depth=-1)
    if key == "target-width":
        target = replace(target, cell_bits=16)
    if key == "address-kind":
        invalid = Form("read_cell", "ld", 8, (R("d", "byte"), R("m", "byte")),
                       {"dst": "d", "a": "m"}, 2, "read {d}, [{m}]")
        target = replace(target, forms=(*target.forms, invalid))
    if key == "encoder-length":
        target = _replace_encoder(target)
    if key == "pool-contiguous":
        memory = M("m", "data")
        forms = (
            Form("read_cell", "ld", 8, (R("d", "byte"), memory),
                 {"dst": "d", "a": "m"}, 2, "read {d}, [{m}]"),
            Form("write_cell", "st", 8, (memory, R("s", "byte")),
                 {"a": "m", "b": "s"}, 2, "write [{m}], {s}"),
        )
        target = replace(target, forms=(*target.forms, *forms))
        config = replace(config, spill_pool={"data": (96, 96)})
        values = ("first", "second", "third", "fourth", "fifth")
        reads = [f"    {name} = mem.data[{24 + i}]" for i, name in enumerate(values)]
        source = "\n".join(("from rpkbin.mcucc.lang import mem, u8",
                            "def example() -> u8:", *reads,
                            "    return " + " + ".join(values)))
    result = compile_source(source, target, config)
    if key == "raw-code":
        assemble(result, target, 0)


def _cli_failure(key, tmp_path):
    target = EXAMPLES / "target.py.txt"
    source = EXAMPLES / "program.py.txt"
    if key in ("verify-driver", "forms-failure"):
        modified = tmp_path / "modified.py"
        target_source = target.read_text(encoding="utf-8")
        if key == "verify-driver":
            target_source += (
                '\nfrom dataclasses import replace\n'
                'TARGET = replace(TARGET, forms=tuple(form for form in TARGET.forms if form.op != "call"))\n'
            )
        else:
            target_source += (
                '\nfrom dataclasses import replace\n'
                'broken = tuple(replace(form, encode=lambda values: ()) if form.id == "ret" else form\n'
                '               for form in TARGET.forms)\n'
                'TARGET = replace(TARGET, forms=broken)\n'
            )
        modified.write_text(target_source, encoding="utf-8")
        target = modified
    if key == "forms-failure":
        args = ["check-forms", str(target), "--seeds", "1"]
    else:
        args = ["verify", str(source), "--target", str(target), "--spill-pool", "data:96-111"]
        if key == "verify-driver":
            args += ["--window", "256"]
    return subprocess.run([sys.executable, "-B", "-m", "rpkbin.mcucc", *args], cwd=tmp_path,
                          env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8"),
                          capture_output=True, encoding="utf-8", timeout=90)


def _documented(path):
    table = re.compile(r"^\| ([a-z][a-z-]+) \| `([^`\n]+)` \|", re.M)
    matches = table.findall(path.read_text(encoding="utf-8"))
    assert len(matches) == len(dict(matches)), path
    return dict(matches)


def test_error_tables_cover_their_entries():
    catalog = _documented(DOCS / "doc" / "errors.md")
    guide = _documented(DOCS / "AI-GUIDE.md")
    assert catalog.keys() == EXPECTED.keys() | CLI.keys()
    assert 8 <= len(guide) <= 12
    assert guide.items() <= catalog.items()


@pytest.mark.parametrize("key", (*EXPECTED, *CLI))
def test_each_documented_error_is_observable(key, tmp_path):
    if key in CLI:
        result = _cli_failure(key, tmp_path)
        assert result.returncode == CLI[key]
        actual = result.stdout if CLI[key] == 1 else result.stderr
        assert "Traceback" not in result.stderr
    else:
        with pytest.raises(EXPECTED[key]) as failure:
            _api_failure(key)
        actual = str(failure.value)
    fragment = _documented(DOCS / "doc" / "errors.md")[key]
    assert fragment in actual, (key, actual)
