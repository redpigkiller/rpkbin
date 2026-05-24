"""
parser.py - Built-in parser classes for Wave.

RegexParser   : stateless, extracts named groups from a log line
StatefulParser: stateful, maintains memory across log lines

Both implement the callable protocol (line: str) -> dict[str, str]
and are compatible with job.add_parser().
"""

from __future__ import annotations

import re
import warnings
from typing import Callable


class RegexParser:
    """Stateless parser: extracts named regex groups from each log line.

    Parameters
    ----------
    pattern:
        A regex string or compiled pattern.  Must contain at least one
        named group (``(?P<name>...)``).
    transform:
        Optional post-processing function ``(match_data: dict) -> dict``.
        Receives the matched groups (None values already filtered out)
        and returns the final dict to merge into ``job.parsed_data``.

    Example::

        job.add_parser(RegexParser(r"PROGRESS=(?P<progress>[\\d.]+)"))

        job.add_parser(RegexParser(
            r"PROGRESS=(?P<progress>[\\d.]+)",
            transform=lambda d: {"progress": f"{float(d['progress']):.1f}%"},
        ))
    """

    def __init__(
        self,
        pattern: str | re.Pattern,
        *,
        transform: Callable[[dict[str, str]], dict[str, str]] | None = None,
    ) -> None:
        self._re = re.compile(pattern) if isinstance(pattern, str) else pattern
        if not self._re.groupindex:
            raise ValueError(
                f"RegexParser pattern {pattern!r} has no named groups. "
                r"Use (?P<name>...) syntax, e.g. r'LOSS=(?P<loss>[\d.]+)'"
            )
        unnamed = self._re.groups - len(self._re.groupindex)
        if unnamed:
            warnings.warn(
                f"RegexParser pattern {pattern!r} has {unnamed} unnamed group(s). "
                "Only named groups ((?P<name>...)) are captured; "
                "unnamed groups are ignored.",
                UserWarning,
                stacklevel=2,
            )
        self._transform = transform

    def __call__(self, line: str) -> dict[str, str]:
        m = self._re.search(line)
        if m is None:
            return {}
        data = {k: v for k, v in m.groupdict().items() if v is not None}
        return self._transform(data) if self._transform else data

    def clone(self) -> "RegexParser":
        """Stateless: returns self (no state to reset)."""
        return self


class StatefulParser:
    """Stateful parser: maintains memory across log lines.

    Parameters
    ----------
    pattern:
        A regex string or compiled pattern with at least one named group.
    on_match:
        Optional. ``(match_data: dict, memory: dict) -> dict``.
        Called after a match; return the updates to merge into memory.
        Returning ``None`` is treated as ``{}`` (no memory update).
        Only called on a successful match.
    to_data:
        Required. ``(match_data: dict, memory: dict) -> dict``.
        Called after ``on_match`` (and memory update); returns the dict
        to merge into ``job.parsed_data``.
        If this raises, the memory update is rolled back.

    Memory semantics
    ----------------
    - Initial memory is ``{}``.
    - ``on_match`` only needs to return *changed* keys; the framework
      merges them into the existing memory (upsert, no deletion).
    - Memory is internal and never appears in ``parsed_data`` directly
      unless ``to_data`` explicitly writes it there.

    Example::

        # Warning counter
        job.add_parser(StatefulParser(
            r"(?P<warning>WARNING):",
            on_match=lambda m, mem: {"count": mem.get("count", 0) + 1},
            to_data=lambda m, mem: {"warning_count": str(mem["count"])},
        ))

        # Min-loss tracker
        job.add_parser(StatefulParser(
            r"LOSS=(?P<loss>[\\d.]+)",
            on_match=lambda m, mem: {
                "min": min(float(mem["min"]), float(m["loss"]))
                       if "min" in mem else float(m["loss"])
            },
            to_data=lambda m, mem: {"min_loss": f"{mem['min']:.4f}"},
        ))
    """

    def __init__(
        self,
        pattern: str | re.Pattern,
        *,
        on_match: Callable[[dict[str, str], dict], dict] | None = None,
        to_data: Callable[[dict[str, str], dict], dict],
    ) -> None:
        self._re = re.compile(pattern) if isinstance(pattern, str) else pattern
        if not self._re.groupindex:
            raise ValueError(
                f"StatefulParser pattern {pattern!r} has no named groups. "
                r"Use (?P<name>...) syntax, e.g. r'STATE=(?P<state>\w+)'"
            )
        unnamed = self._re.groups - len(self._re.groupindex)
        if unnamed:
            warnings.warn(
                f"StatefulParser pattern {pattern!r} has {unnamed} unnamed group(s). "
                "Only named groups are captured; unnamed groups are ignored.",
                UserWarning,
                stacklevel=2,
            )
        if not callable(to_data):
            raise TypeError("StatefulParser: 'to_data' must be callable")
        self._on_match = on_match
        self._to_data = to_data
        self._memory: dict = {}

    def __call__(self, line: str) -> dict[str, str]:
        m = self._re.search(line)
        if m is None:
            return {}
        match_data = {k: v for k, v in m.groupdict().items() if v is not None}

        # Phase 1: compute new memory on a private working copy.
        if self._on_match is not None:
            working_memory = dict(self._memory)
            updates = self._on_match(match_data, working_memory)
            new_memory = {**working_memory, **(updates or {})}
        else:
            new_memory = dict(self._memory)

        # Phase 2: produce parsed_data update
        # If to_data raises, new_memory is never committed
        result = self._to_data(match_data, new_memory)

        # Commit only after both phases succeed
        self._memory = new_memory
        return result if result is not None else {}

    def clone(self) -> "StatefulParser":
        """Return a new instance with the same config but reset memory."""
        return StatefulParser(
            self._re,
            on_match=self._on_match,
            to_data=self._to_data,
        )
