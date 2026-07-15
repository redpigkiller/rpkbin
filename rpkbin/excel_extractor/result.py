"""Result data structures returned by the Excel extraction engine."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CellMatch:
    row: int
    col: int
    value: str
    is_merged: bool


@dataclass
class RowMatch:
    row: int
    cells: list[CellMatch]
    node_id: str | None


@dataclass
class BlockMatch:
    start: tuple[int, int]
    end: tuple[int, int]
    rows: list[RowMatch]
    block_id: str | None
    sheet_name: str = ""


@dataclass
class MatchOutput:
    blocks: list[BlockMatch]

    def get_blocks_by_id(self, block_id: str) -> list[BlockMatch]:
        return [b for b in self.blocks if b.block_id == block_id]

    def get_blocks_by_sheet(self, sheet_name: str) -> list[BlockMatch]:
        return [b for b in self.blocks if b.sheet_name == sheet_name]


@dataclass
class MatchOptions:
    """Options that control the behaviour of match_template().
    """
    max_matched_sheets: int = 0       # 0 for all, positive for specified number of matched sheets

    def __post_init__(self):
        if not isinstance(self.max_matched_sheets, int) or self.max_matched_sheets < 0:
            raise ValueError("max_matched_sheets must be a non-negative integer")
