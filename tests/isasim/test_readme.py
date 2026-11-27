"""Run every Python example sequentially and compare its printed result."""

import ast
import re
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("page", ["README.md", "scenarios.md"])
def test_readme_examples_are_self_contained(language, page):
    root = Path(__file__).resolve().parents[2]
    path = root / "docs" / language / "isasim" / page
    source = path.read_text(encoding="utf-8")
    blocks = list(
        re.finditer(r"^```(\w+)\n(.*?)^```", source, re.MULTILINE | re.DOTALL)
    )
    namespace = {"__name__": "__readme__"}
    executed = 0
    for index, block in enumerate(blocks):
        if block[1] != "python":
            continue
        tree = ast.parse(block[2])
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("tests")
            if isinstance(node, ast.Import):
                assert all(not item.name.startswith("tests") for item in node.names)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "tests/" not in node.value and "tests\\" not in node.value
        assert index + 1 < len(blocks) and blocks[index + 1][1] == "text"
        assert not source[block.end() : blocks[index + 1].start()].strip()
        stdout = StringIO()
        with redirect_stdout(stdout):
            eval(compile(tree, str(path), "exec"), namespace)
        assert stdout.getvalue() == blocks[index + 1][2]
        executed += 1
    assert executed >= 2
