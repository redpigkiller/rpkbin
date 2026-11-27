"""Explicit compilation settings and target-dependent pool validation."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .errors import ConfigError
from .target import MemLoc


@dataclass(frozen=True)
class CompileConfig:
    spill_pool: Mapping
    comments: bool
    park_depth: int = field(default=0, kw_only=True)
    park_policy: str = field(default="smallest", kw_only=True)

    def __post_init__(self):
        if type(self.park_depth) is not int or self.park_depth < 0:
            raise ConfigError(f"park_depth {self.park_depth!r}: expected nonnegative integer")
        if not isinstance(self.park_policy, str) or self.park_policy not in ("smallest", "stack_first", "slot_first"):
            raise ConfigError(f"park_policy {self.park_policy!r}: expected smallest, stack_first or slot_first")
        if not isinstance(self.spill_pool, Mapping) or len(self.spill_pool) > 1:
            raise ConfigError(f"spill_pool {self.spill_pool!r}: expected at most one data space")
        if type(self.comments) is not bool:
            raise ConfigError(f"comments {self.comments!r}: expected bool")
        for space, bounds in self.spill_pool.items():
            if not isinstance(space, str) or not isinstance(bounds, tuple) or len(bounds) != 2 or any(type(v) is not int for v in bounds) or bounds[0] < 0 or bounds[0] > bounds[1]:
                raise ConfigError(f"spill_pool {space!r}={bounds!r}: expected nonnegative inclusive interval")
        object.__setattr__(self, "spill_pool", MappingProxyType(dict(self.spill_pool)))


def _validate_config(target, config, functions):
    spaces = {s.name: s for s in target.spaces}
    locations = [loc for candidates in target.abi.arg_locs.values() for loc in candidates]
    locations.extend(target.abi.ret_locs.values())
    for fn in functions.values():
        locations.extend(fn.abi.get("args", ()))
        locations.append(fn.abi.get("ret"))
    for name, (low, high) in config.spill_pool.items():
        if name not in spaces or spaces[name].kind != "data" or high >= 1 << spaces[name].addr_bits:
            raise ConfigError(f"spill_pool {name!r}={low, high!r}: unknown data space or out of range")
        for loc in locations:
            if isinstance(loc, MemLoc) and loc.space == name and not (loc.addr + loc.n <= low or loc.addr > high):
                raise ConfigError(f"spill_pool {name!r}={low, high!r}: overlaps ABI location {loc!r}")
