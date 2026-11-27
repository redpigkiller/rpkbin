import re
from pathlib import Path


def test_frontend_avoids_syntax_unsupported_by_firefox_esr_102():
    wave = Path(__file__).parents[2] / "rpkbin" / "wave"
    unsupported = re.compile(
        r":has\s*\(|\b(?:popover(?:target(?:action)?)?|showPopover|hidePopover|togglePopover)\b|"
        r"\b(?:anchor-name|anchor-scope|position-anchor|position-area|view-transition[\w-]*|"
        r"startViewTransition|subgrid|backdrop-filter)\b|"
        r"\b(?:toSorted|toReversed|toSpliced|findLast|findLastIndex|Object\.groupBy|color-mix)\s*\(|"
        r"@(?:starting-style|scope|position-try|container)\b",
        re.I,
    )
    violations = []
    assets = [asset for asset in wave.rglob("*") if asset.suffix in {".css", ".html", ".js"}]
    assert wave / "charts.js" in assets
    assert wave / "static" / "index.html" in assets
    for asset in assets:
        for line, source in enumerate(asset.read_text(encoding="utf-8").splitlines(), 1):
            if match := unsupported.search(source):
                violations.append(f"{asset.relative_to(wave)}:{line}: {match[0]}")
    assert not violations, "\n".join(violations)
