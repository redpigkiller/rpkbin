"""Template AST node definitions.

Users build a Block by composing Row/EmptyRow/Group nodes.
These are pure data-holder classes; no matching logic lives here.

Repeat spec
-----------
repeat = 1          → exactly once
repeat = "?"        → 0 or 1
repeat = "+"        → 1 or more (greedy)
repeat = "*"        → 0 or more (greedy)
repeat = (2, 4)     → between 2 and 4 times (inclusive)
repeat = (N, None)  → N or more
"""

from __future__ import annotations
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from rpkbin.excel_extractor.types import CellCondition, Types

RepeatSpec = int | str | tuple[int, int | None]


def _parse_repeat(repeat: RepeatSpec) -> tuple[int, int | None]:
    """Normalise any repeat spec to (min, max).

    max = None means unbounded.
    """
    if isinstance(repeat, int):
        if repeat < 0:
            raise ValueError(f"Repeat must be non-negative, got {repeat}")
        return (repeat, repeat)
    if isinstance(repeat, str):
        table = {"?": (0, 1), "+": (1, None), "*": (0, None)}
        if repeat not in table:
            raise ValueError(f"Repeat string must be '?', '+' or '*', got {repeat!r}")
        return table[repeat]
    if isinstance(repeat, tuple):
        if len(repeat) != 2:
            raise ValueError("Repeat tuple must be (min, max) where max may be None")
        lo, hi = repeat
        if not isinstance(lo, int) or lo < 0:
            raise ValueError(f"Repeat min must be a non-negative int, got {lo!r}")
        if hi is not None and (not isinstance(hi, int) or hi < lo):
            raise ValueError(f"Repeat max must be None or an int >= min, got {hi!r}")
        return (lo, hi)
    raise TypeError(f"Unsupported repeat spec: {repeat!r}")


def _parse_pattern(pattern: list[Any]) -> list[CellCondition|str]:
    """Convert each element of a pattern list to a CellCondition.

    str  → literal match
    CellCondition → pass-through
    """
    result = []
    for item in pattern:
        if isinstance(item, list):
            result.extend(_parse_pattern(item))
        elif isinstance(item, (CellCondition, str)):
            result.append(item)
        else:
            raise TypeError(
                f"Invalid pattern element: {item!r}"
            )
    return result


@dataclass
class TemplateNode(ABC):
    """Abstract base for all template AST nodes.
    
    Notes:
        - `min_similarity` applies its threshold to all cells in the node (cannot be configured individually per cell).
        - `normalize=True` will lowercase and strip cell values before matching. Please use caution when using regular expressions (`Types.r`) since regex patterns containing uppercase characters may unexpectedly fail.
    """
    repeat: RepeatSpec = 1
    node_id: str | None = None
    normalize: bool = True
    min_similarity: float | None = None
    match_ratio: float | None = None
    repeat_range: tuple[int, int | None] = field(init=False)

    def __post_init__(self):
        self.repeat_range = _parse_repeat(self.repeat)
        if self.min_similarity is not None:
            if not (0.0 <= self.min_similarity <= 1.0):
                raise ValueError("min_similarity must be in [0.0, 1.0]")
        if self.match_ratio is not None:
            if not (0.0 <= self.match_ratio <= 1.0):
                raise ValueError("match_ratio must be in [0.0, 1.0]")

    def __or__(self, other: TemplateNode) -> AltNode:
        """Support Row(...) | Row(...) syntax."""
        for node in (self, other):
            if node.repeat_range != (1, 1):
                raise ValueError(
                    f"repeat={node.repeat!r} on {type(node).__name__} inside | is not allowed. "
                    "Set repeat on the AltNode itself gracefully, e.g. AltNode([Row(), Row()], repeat=...)."
                )
        left = self.alternatives if isinstance(self, AltNode) else [self]
        right = other.alternatives if isinstance(other, AltNode) else [other]
        return AltNode(alternatives=left + right)
    
    @abstractmethod
    def rules(self) -> list[CellCondition | str]:
        ...


@dataclass
class AltNode(TemplateNode):
    """Matches any one of the given alternatives (OR semantics).
    
    Created via the | operator on TemplateNode subclasses.
    e.g. Row(HEADER) | Row(SUBHEADER)
    """
    alternatives: list[TemplateNode] = field(default_factory=list)

    def rules(self) -> list[CellCondition | str]:
        # Return all alternatives' rules flattened
        # matcher must treat each as a separate candidate
        return [rule for alt in self.alternatives for rule in alt.rules()]
    
@dataclass
class Row(TemplateNode):
    """Horizontal pattern: matches one or more Excel rows."""
    pattern: list[Any] = field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()
        self._rules = _parse_pattern(self.pattern)

    def rules(self) -> list[CellCondition | str]:
        return self._rules

@dataclass
class EmptyRow(TemplateNode):
    """Matches one or more completely empty rows (syntactic sugar)."""
    allow_whitespace: bool = True

    def __post_init__(self):
        super().__post_init__()
        self._rules: list[CellCondition | str] = [Types.EMPTY | Types.SPACE if self.allow_whitespace else Types.EMPTY]

    def rules(self) -> list[CellCondition | str]:
        return self._rules
    
    def expand_width(self, width: int):
        if len(self._rules) > 1:
            raise ValueError(f"The EmptyRow has already been expanded (length = {len(self._rules)}).")
        self._rules *= width
    
@dataclass
class Group(TemplateNode):
    """Groups multiple Row/EmptyRow nodes for collective repetition."""
    children: list[TemplateNode] = field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()
        self.children = list(self.children)
    
    def rules(self) -> list[CellCondition | str]:
        result = []
        for child in self.children:
            result.extend(child.rules())
        return result

@dataclass
class Block:
    """Top-level template unit.
    
    Usage:
        Block(
            Row(HEADER, node_id="header", min_similarity=0.85),
            Row(FIELD, node_id="field", repeat="*"),
            block_id="my_table",
        )
    """
    children: list[TemplateNode] = field(default_factory=list)
    block_id: str | None = None
    orientation: Literal["vertical", "horizontal"] = "vertical"

    def __init__(
        self,
        *children: TemplateNode,
        block_id: str | None = None,
        orientation: Literal["vertical", "horizontal"] = "vertical",
    ):
        self.children = list(children)
        self.block_id = block_id
        self.orientation = orientation
        self._validate()
        self.width = self._infer_width(self.children)
        self._expand(self.children)

    def _validate(self):
        for child in self.children:
            if not isinstance(child, TemplateNode):
                raise TypeError(f"Block children must be TemplateNode, got {type(child)}")
    
    def _infer_width(self, nodes: list[TemplateNode], expected: int | None = None) -> int:
        """Infer block width from Row nodes, validate consistency."""
        for node in nodes:
            if isinstance(node, AltNode):
                widths = [self._infer_width([alt]) for alt in node.alternatives]
                if len(set(widths)) > 1:
                    raise ValueError(
                        f"AltNode alternatives have inconsistent widths: {widths}"
                    )
                w = widths[0]
            elif isinstance(node, Group):
                w = self._infer_width(node.children, expected)
            elif isinstance(node, EmptyRow):
                continue  # skip, will be expanded later
            else:
                w = len(node.rules())   # Row

            if expected is not None and w != expected:
                raise ValueError(
                    f"{type(node).__name__} has width {w}, expected {expected}"
                )
            expected = w

        if expected is None:
            raise ValueError("Block has no Row to infer width from")
        return expected

    def _expand(self, nodes: list[TemplateNode]) -> None:
        """Expand EmptyRow rules to match block width."""
        for node in nodes:
            if isinstance(node, AltNode):
                for alt in node.alternatives:
                    self._expand([alt])
            elif isinstance(node, Group):
                self._expand(node.children)
            elif isinstance(node, EmptyRow):
                node.expand_width(self.width)

    def __repr__(self) -> str:
        id_part = f"{self.block_id!r}, " if self.block_id else ""
        return f"Block({id_part}{self.orientation!r}, {len(self.children)} children)"
    