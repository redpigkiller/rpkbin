"""Execute the user-facing command blocks against a clean copy of the examples."""

import inspect
import os
from pathlib import Path
import re
import runpy
import shlex
import shutil
import subprocess
import sys
from urllib.parse import unquote

import pytest
from test_lint import ast_violations, signature_violations


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "rpkbin" / "mcucc"
REQUIRED = (
    "README.md", "AI-GUIDE.md", "CHANGELOG.md", "doc/language.md",
    "doc/target.md", "doc/guarantees.md", "doc/errors.md", "examples/README.md",
)
COMMAND = re.compile(r"^```bash exec\n(.*?)^```\n", re.M | re.S)
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")


def documents():
    return dict.fromkeys((*(DOCS / name for name in REQUIRED), *sorted(DOCS.rglob("*.md"))))


def test_document_inventory_links_and_length():
    for path in documents():
        assert path.is_file(), path
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"reference[/\\]|docs[/\\]0", source, re.I), path
        for url in LINK.findall(source):
            filename, _, anchor = unquote(url.strip("<>")).partition("#")
            if "://" in filename or filename.startswith("mailto:"):
                continue
            local = path.parent / filename if filename else path
            assert local.is_file(), (path, url)
            if anchor:
                headings = re.findall(r"^#+ (.+)$", local.read_text(encoding="utf-8"), re.M)
                slugs = {re.sub(r"[^\w -]", "", h.lower()).replace(" ", "-") for h in headings}
                assert anchor in slugs, (path, url)
    ai_guide = (DOCS / "AI-GUIDE.md").read_text(encoding="utf-8")
    assert len(ai_guide.splitlines()) <= 200


def test_ai_golden_example_matches_example_files():
    source = (DOCS / "AI-GUIDE.md").read_text(encoding="utf-8")
    golden = source.split("## 黃金範例\n", 1)[1].split("\n## ", 1)[0]
    for language, filename in (("python", "program.py.txt"), ("text", "program.asm"),
                               ("json", "report.json")):
        block = re.search(rf"^```{language}\n(.*?)^```$", golden, re.M | re.S)
        assert block and block[1] == (DOCS / "examples" / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", ("target.py.txt", "program.py.txt"))
def test_example_python_templates_follow_ast_and_signature_rules(filename):
    path = DOCS / "examples" / filename
    assert not ast_violations(path.read_text(encoding="utf-8"))
    namespace = runpy.run_path(str(path))
    for name, value in namespace.items():
        if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == namespace["__name__"]:
            assert not signature_violations(value), (path, name)


def _stream(example, stream, path):
    file_marker = re.search(rf"^<!-- {stream}: ([^\n]+) -->$", example, re.M)
    text_block = re.search(rf"^```text {stream}\n(.*?)^```$", example, re.M | re.S)
    assert not (file_marker and text_block), (path, stream)
    if file_marker:
        return (path.parent / file_marker[1]).read_text(encoding="utf-8")
    return text_block[1] if text_block else ""


@pytest.mark.parametrize("guide", ("README.md", "AI-GUIDE.md"))
def test_standard_flow_has_all_executable_steps(guide):
    source = (DOCS / guide).read_text(encoding="utf-8")
    commands = [shlex.split(block[1]) for block in COMMAND.finditer(source)]
    phases = [args[args.index("rpkbin.mcucc") + 1] for args in commands if "rpkbin.mcucc" in args]
    start = phases.index("lint-target")
    assert phases[start:start + 4] == ["lint-target", "check-forms", "compile", "verify"]


def test_execute_all_marked_commands(tmp_path):
    shutil.copytree(DOCS / "examples", tmp_path / "examples")
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    total = 0
    for path in documents():
        source = path.read_text(encoding="utf-8")
        commands = list(COMMAND.finditer(source))
        for index, block in enumerate(commands):
            following = source[block.end():commands[index + 1].start()
                               if index + 1 < len(commands) else len(source)]
            # One portable command per fence. Do not pass untrusted Markdown to a shell.
            lines = block[1].splitlines()
            assert len(lines) == 1, (path, lines)
            arguments = shlex.split(lines[0])
            assert arguments[:2] == ["python", "-B"], (path, arguments)
            status = re.search(r"^<!-- exit: (\d+) -->$", following, re.M)
            assert status is not None, (path, lines)
            result = subprocess.run([sys.executable, *arguments[1:]], cwd=tmp_path, env=env,
                                    capture_output=True, encoding="utf-8", timeout=90)
            assert (result.returncode, result.stdout, result.stderr) == (
                int(status[1]), _stream(following, "stdout", path), _stream(following, "stderr", path)
            ), (path, lines, result)
            total += 1
    assert total >= 8
