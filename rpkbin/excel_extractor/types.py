"""Types system for the Excel extraction engine.

CellCondition is the internal representation of a cell constraint.
The Types class exposes user-facing constants that map onto CellConditions.
"""

from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class CellCondition:
    """Internal representation of a cell match condition.

    patterns    : frozenset of regex strings to match against the normalised
                  cell value string (OR semantics).  An empty frozenset means
                  "match only the empty string" (used for EMPTY).
    is_merged   : True  → cell must originate from a merge-expand.
                  False → cell must NOT be a merge-expand.
                  None  → don't care (either merged or non-merged is fine).
    """

    patterns: frozenset[str]
    is_merged: bool | None = None   # None = don't care

    @classmethod
    def from_pattern(cls, pattern: str, *, is_merged: bool = False) -> "CellCondition":
        if not pattern:
            return cls(frozenset(), is_merged)
        return cls(frozenset([pattern]), is_merged)
    
    def __or__(self, other: "CellCondition") -> "CellCondition":
        merged_patterns = self.patterns | other.patterns
        if self.is_merged == other.is_merged:
            is_merged = self.is_merged
        else:
            is_merged = None
        
        return CellCondition(patterns=merged_patterns, is_merged=is_merged)
    def __call__(self, n: int) -> list["CellCondition"]:
        """Syntactic sugar for repeating a condition n times in a row pattern."""
        if not isinstance(n, int) or n < 0:
            raise ValueError(f"Repeat count must be a non-negative integer, got {n!r}")
        return [self] * n


class Types:
    """Predefined cell-type constants.

    Usage
    -----
    Basic values
        Types.ANY        → any value (including empty)
        Types.STR        → any non-empty string (.+)
        Types.INT        → integer (42, -7)
        Types.POS_INT    → positive integer
        Types.NEG_INT    → negative integer
        Types.FLOAT      → float or int (3.14, 42)
        Types.NUM        → INT | FLOAT (any number)
        Types.SCIENTIFIC → scientific notation (1.5e3)
        Types.PERCENT    → percentage (12.5%)
        Types.BOOL       → boolean-like (true/false/yes/no/1/0)

    Number bases
        Types.HEX        → hex (0xFF)
        Types.BIN        → binary (0b1010)
        Types.OCT        → octal (0o17)

    Date / Time
        Types.DATE       → alias for DATE_ISO (YYYY-MM-DD)
        Types.DATE_ISO   → YYYY-MM-DD
        Types.DATE_SLASH → DD/MM/YYYY
        Types.DATE_TW    → ROC date (111/01/01)
        Types.DATETIME   → YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS
        Types.TIME       → alias for TIME_24H (HH:MM)
        Types.TIME_24H   → HH:MM

    Structural
        Types.MERGED     → from a merge cell
        Types.SPACE      → empty or whitespace-only string
        Types.EMPTY      → truly empty cell
        Types.BLANK      → SPACE (any "looks empty")
        Types.r(pattern) → custom regex
    """

    # --- basic value types ---
    ANY = CellCondition.from_pattern(pattern=r".*")
    STR = CellCondition.from_pattern(pattern=r".+")
    INT = CellCondition.from_pattern(pattern=r"[\+-]?\d+")
    POS_INT = CellCondition.from_pattern(pattern=r"\+?\d+")
    NEG_INT = CellCondition.from_pattern(pattern=r"-\d+")
    FLOAT = CellCondition.from_pattern(pattern=r"[\+-]?\d+(\.\d+)?")
    SCIENTIFIC = CellCondition.from_pattern(pattern=r"[\+-]?\d+(\.\d+)?([eE][\+-]?\d+)?")
    PERCENT = CellCondition.from_pattern(pattern=r"[\+-]?\d+(\.\d+)?%")
    BOOL = CellCondition.from_pattern(pattern=r"(?i)(true|false|yes|no|1|0)")

    # convenience aliases
    NUM = INT | FLOAT
    NONEMPTY = CellCondition.from_pattern(pattern=r".+")   # same as STR

    # --- number bases ---
    HEX = CellCondition.from_pattern(pattern=r"0[xX][0-9a-fA-F]+")
    BIN = CellCondition.from_pattern(pattern=r"0[bB][01]+")
    OCT = CellCondition.from_pattern(pattern=r"0[oO][0-7]+")

    # --- date / time ---
    DATE_ISO = CellCondition.from_pattern(pattern=r"\d{4}-\d{2}-\d{2}")
    DATE_SLASH = CellCondition.from_pattern(pattern=r"\d{2}/\d{2}/\d{4}")
    DATE_TW = CellCondition.from_pattern(pattern=r"\d{2,3}/\d{2}/\d{2}")
    DATETIME = CellCondition.from_pattern(pattern=r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?")

    TIME_24H = CellCondition.from_pattern(pattern=r"\d{2}:\d{2}")

    DATE = DATE_ISO     # default alias
    TIME = TIME_24H     # default alias

    # --- structural types ---
    MERGED = CellCondition.from_pattern(pattern=r".*", is_merged=True)
    SPACE = CellCondition.from_pattern(pattern=r"^\s*$")
    EMPTY = CellCondition.from_pattern(pattern="")
    BLANK = CellCondition.from_pattern(pattern=r"^\s*$")

    @staticmethod
    def r(pattern: str, is_merged: bool = False) -> CellCondition:
        """Create a CellCondition from a custom regex pattern."""
        return CellCondition.from_pattern(pattern=pattern, is_merged=is_merged)

