"""Pattern schema and loading helpers.

The public schema is intentionally plain dictionaries so tests and AI tooling
can generate patterns without depending on a YAML package.  Real YAML loading is
an optional adapter around the same schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Mapping


PatternSpec = Mapping[str, Any]


@dataclass(frozen=True)
class RewritePattern:
    """One structural rewrite rule.

    ``cost_delta`` is compatibility metadata reserved for a future selector;
    the current ordered rewrite pass does not use it to choose rules.
    """

    name: str
    match: PatternSpec
    replace: PatternSpec
    cost_delta: int = 0  # Metadata only; currently not used for selection.

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RewritePattern":
        if data.get("kind", "rewrite") != "rewrite":
            raise ValueError(f"unsupported pattern kind: {data.get('kind')!r}")
        return cls(
            name=str(data["name"]),
            match=data["match"],
            replace=data["replace"],
            cost_delta=int(data.get("cost_delta", 0)),
        )


def load_patterns_from_dicts(items: Iterable[Mapping[str, Any]]) -> List[RewritePattern]:
    return [RewritePattern.from_dict(item) for item in items]


def load_patterns_from_yaml(path: Path) -> List[RewritePattern]:
    """Load YAML patterns through the optional ruamel.yaml dependency."""

    try:
        from ruamel.yaml import YAML  # type: ignore
    except ImportError as exc:
        raise RuntimeError("install ruamel.yaml to load pattern YAML files") from exc

    yaml = YAML(typ="safe")
    loaded = yaml.load(path.read_text(encoding="utf-8")) or []
    if not isinstance(loaded, list):
        raise ValueError(f"pattern file must contain a list: {path}")
    return load_patterns_from_dicts(loaded)
