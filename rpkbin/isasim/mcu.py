"""Declarative machine definitions and pattern validation."""

import hashlib as _hashlib
import importlib.util
import inspect
import os
import re
import string
import sys as _sys
from collections.abc import Mapping as _Mapping, Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from types import MappingProxyType as _MappingProxyType

from .errors import MCUDefinitionError, _is_int


def _error(name, value, expected):
    raise MCUDefinitionError(f"{name}={value!r}: expected {expected}")


def _number(name, value, low, high=None):
    if (
        not _is_int(value)
        or value < low
        or (high is not None and value >= high)
    ):
        _error(
            name, value, f"{low} <= value" + (f" < {high}" if high is not None else "")
        )


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_ROLE_VALUES = ("internal", "input", "output", "inout", "flag")


def _memory_regions(regions, size):
    if regions is None:
        return ()
    if not isinstance(regions, _Sequence) or isinstance(
        regions, (str, bytes, bytearray)
    ):
        _error("regions", regions, "sequence of (lo, hi, access) entries")
    normalized = []
    for entry in regions:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            _error("region", entry, "(lo, hi, access)")
        lo, hi, access = entry
        if type(lo) is not int or type(hi) is not int or not 0 <= lo < hi <= size:
            _error("region", entry, f"integer boundaries 0 <= lo < hi <= {size}")
        if access not in ("ro", "rw"):
            _error("region access", entry, "ro or rw")
        normalized.append((lo, hi, access))
    ordered = sorted(normalized, key=lambda entry: entry[0])
    for left, right in zip(ordered, ordered[1:]):
        if right[0] < left[1]:
            _error("regions", (left, right), "nonoverlapping regions")
    return tuple(normalized)


def _operand_bounds(width, signed):
    low = -(1 << (width - 1)) if signed else 0
    return low, (1 << (width - int(signed))) - 1


@_dataclass(frozen=True)
class Operand:
    """Encoding interpretation; relative operands contain target cell addresses."""

    signed: bool = False
    relative: str | None = None
    names: _Mapping[str, int] | None = None
    range: tuple[int, int] | None = None

    def __hash__(self):
        names = None if self.names is None else frozenset(self.names.items())
        return hash((self.signed, self.relative, names, self.range))

    def __post_init__(self):
        if type(self.signed) is not bool or self.relative not in (
            None,
            "pc",
            "next_pc",
        ):
            _error(
                "operand",
                (self.signed, self.relative),
                "bool signed and pc/next_pc/None relative",
            )

        if self.relative is not None and (
            self.names is not None or self.range is not None
        ):
            _error("operand", self, "names and range require relative=None")
        if self.names is not None:
            if not isinstance(self.names, _Mapping) or not self.names:
                _error("names", self.names, "nonempty mapping")
            names = dict(self.names)
            if any(
                not isinstance(key, str)
                or re.fullmatch(_IDENTIFIER, key) is None
                or type(value) is not int
                for key, value in names.items()
            ) or len(set(names.values())) != len(names):
                _error("names", names, "unique integer values keyed by identifiers")
            object.__setattr__(self, "names", _MappingProxyType(names))
        if self.range is not None and (
            not isinstance(self.range, tuple)
            or len(self.range) != 2
            or any(type(value) is not int for value in self.range)
            or self.range[0] > self.range[1]
        ):
            _error("range", self.range, "pair of integers (lo, hi), lo <= hi")

    def _validate_width(self, letter, width):
        lo, hi = _operand_bounds(width, self.signed)
        if self.names is not None:
            for name, value in self.names.items():
                if not lo <= value <= hi:
                    _error(f"operand {letter} names[{name!r}]", value, f"{lo}..{hi}")
        if self.range is not None and not lo <= self.range[0] <= self.range[1] <= hi:
            _error(f"operand {letter} range", self.range, f"{lo}..{hi}")


@_dataclass(frozen=True, slots=True)
class FieldInfo:
    name: str
    hi: int
    lo: int


@_dataclass(frozen=True, slots=True)
class RegisterInfo:
    name: str
    bits: int
    reset: int
    is_pc: bool
    role: str
    fields: tuple[FieldInfo, ...]


@_dataclass(frozen=True, slots=True)
class SpaceInfo:
    name: str
    cell_bits: int
    size: int
    access: str
    regions: tuple[tuple[int, int, str], ...]
    is_program: bool
    role: str
    reset_state: str


@_dataclass(frozen=True, slots=True)
class MemAliasInfo:
    name: str
    target: str
    space: str
    lo: int
    hi: int
    bit_hi: int | None
    bit_lo: int | None
    endian: str | None
    bit_order: str | None
    bits: int


_MEM_ALIAS_INT = r"(?:0[xX][0-9a-fA-F]+|[0-9]+)"
_MEM_ALIAS_CELL_RE = re.compile(
    rf"([A-Za-z_][A-Za-z0-9_]*)\[({_MEM_ALIAS_INT})\](?:\[({_MEM_ALIAS_INT})(?::({_MEM_ALIAS_INT}))?\])?"
)
_MEM_ALIAS_RANGE_RE = re.compile(
    rf"([A-Za-z_][A-Za-z0-9_]*)\[({_MEM_ALIAS_INT}):({_MEM_ALIAS_INT})\]"
)


def _parse_int_literal(text: str) -> int:
    return int(text, 16 if text.lower().startswith("0x") else 10)


def _parse_mem_alias(
    name: str,
    target: str,
    *,
    endian: str | None = None,
    bit_order: str | None = None,
    spaces: _Mapping[str, dict[str, object]],
    existing_names,
    error_cls: type[Exception] = MCUDefinitionError,
) -> MemAliasInfo:
    def fail(param: str, value: object, expected: str):
        raise error_cls(f"{param}={value!r}: expected {expected}")

    if (
        not isinstance(name, str)
        or re.fullmatch(_IDENTIFIER, name) is None
        or name == "irq"
        or name.startswith("_")
    ):
        fail(
            "mem_alias name",
            name,
            "identifier without a leading underscore other than irq",
        )
    if name in existing_names:
        fail("name", name, "globally unique identifier")
    if not isinstance(target, str):
        fail("mem_alias target", target, "target string")

    cleaned = target.strip()
    bit_hi: int | None = None
    bit_lo: int | None = None
    range_match = _MEM_ALIAS_RANGE_RE.fullmatch(cleaned)
    if range_match is not None:
        space, lo_text, hi_text = range_match.groups()
        meta = spaces.get(space)
        if meta is None:
            fail("mem_alias target", target, "declared memory space")
        lo = _parse_int_literal(lo_text)
        hi = _parse_int_literal(hi_text)
        size = meta["size"]
        if not 0 <= lo < hi <= size:
            fail("mem_alias target", target, f"half-open range 0 <= lo < hi <= {size}")
        bits = (hi - lo) * meta["cell_bits"]
    else:
        cell_match = _MEM_ALIAS_CELL_RE.fullmatch(cleaned)
        if cell_match is None:
            fail(
                "mem_alias target",
                target,
                "space[addr], space[addr][bit], space[addr][hi:lo], or space[lo:hi]",
            )
        space, addr_text, b1_text, b2_text = cell_match.groups()
        meta = spaces.get(space)
        if meta is None:
            fail("mem_alias target", target, "declared memory space")
        addr = _parse_int_literal(addr_text)
        size = meta["size"]
        if not 0 <= addr < size:
            fail("mem_alias target", target, f"address 0 <= addr < {size}")
        lo = addr
        hi = addr + 1
        cell_bits = meta["cell_bits"]
        if b1_text is not None:
            bit_hi = _parse_int_literal(b1_text)
            bit_lo = bit_hi if b2_text is None else _parse_int_literal(b2_text)
            if not 0 <= bit_lo <= bit_hi < cell_bits:
                fail(
                    "mem_alias target",
                    target,
                    f"bit bounds 0 <= lo <= hi < {cell_bits}",
                )
            bits = bit_hi - bit_lo + 1
        else:
            bits = cell_bits

    if endian not in (None, "big", "little"):
        fail("endian", endian, "'big', 'little', or None")
    if hi - lo > 1 and endian is None:
        fail("endian", endian, "'big' or 'little' for multi-cell target")
    if bit_order not in (None, "msb_first", "lsb_first"):
        fail("bit_order", bit_order, "'msb_first', 'lsb_first', or None")
    if bits > 1 and bit_order is None:
        fail("bit_order", bit_order, "'msb_first' or 'lsb_first' for multi-bit target")

    return MemAliasInfo(
        name=name,
        target=cleaned,
        space=space,
        lo=lo,
        hi=hi,
        bit_hi=bit_hi,
        bit_lo=bit_lo,
        endian=endian,
        bit_order=bit_order,
        bits=bits,
    )


@_dataclass(frozen=True, slots=True)
class MCUDescription:
    spaces: tuple[SpaceInfo, ...]
    registers: tuple[RegisterInfo, ...]
    irq_lines: tuple[str, ...]
    aliases: tuple[tuple[str, str], ...]
    has_instruction_kinds: bool


def _description_text(value):
    # Keep the string value without retaining attributes on a scalar subclass.
    return str.__str__(value if isinstance(value, str) else str(value))


class MCU:
    def __init__(self, name: str):
        if not isinstance(name, str) or not name:
            _error("name", name, "nonempty string")
        self._name = name
        self._spaces = {}
        self._registers = {}
        self._irqs = []
        self._aliases = {}
        self._mem_aliases: dict[str, MemAliasInfo] = {}
        self._instructions = []
        self._policies = {}
        self._interrupt = None
        self._validated = False

    def __repr__(self) -> str:
        return (
            f"MCU({self._name!r}, spaces={len(self._spaces)}, "
            f"registers={len(self._registers)}, instructions={len(self._instructions)})"
        )

    def describe(self) -> MCUDescription:
        """Return an immutable snapshot of the validated declaration."""
        if not self._validated:
            _error(
                "MCU", self._name, "current declaration to have completed validation"
            )
        return MCUDescription(
            spaces=tuple(
                SpaceInfo(
                    name=_description_text(space["name"]),
                    cell_bits=int.__int__(space["cell_bits"]),
                    size=int.__int__(space["size"]),
                    access=_description_text(space["access"]),
                    regions=tuple(
                        (lo, hi, _description_text(access))
                        for lo, hi, access in space.get("regions", ())
                    ),
                    is_program=space["is_program"],
                    role=_description_text(space["role"]),
                    reset_state=_description_text(space["reset_state"]),
                )
                for space in self._spaces.values()
            ),
            registers=tuple(
                RegisterInfo(
                    name=_description_text(reg["name"]),
                    bits=int.__int__(reg["bits"]),
                    reset=int.__int__(reg["reset"]),
                    is_pc=reg["is_pc"],
                    role=_description_text(reg["role"]),
                    fields=tuple(
                        FieldInfo(
                            _description_text(name), int.__int__(hi), int.__int__(lo)
                        )
                        for name, (hi, lo) in reg["fields"].items()
                    ),
                )
                for reg in self._registers.values()
            ),
            irq_lines=tuple(map(_description_text, self._irqs)),
            aliases=tuple(
                (_description_text(name), _description_text(target))
                for name, target in self._aliases.items()
            ),
            has_instruction_kinds=any(
                ins["kind"] is not None for ins in self._instructions
            ),
        )

    def _name_check(self, name):
        if (
            not isinstance(name, str)
            or re.fullmatch(_IDENTIFIER, name) is None
            or name == "irq"
        ):
            _error("name", name, "unique identifier other than irq")
        if (
            name in self._spaces
            or name in self._registers
            or name in self._irqs
            or name in self._aliases
            or name in self._mem_aliases
        ):
            _error("name", name, "globally unique identifier")

    def space(
        self,
        name,
        *,
        cell_bits,
        size,
        access,
        reset_state,
        init,
        is_program=False,
        role=None,
        regions=None,
    ):
        self._name_check(name)
        _number("cell_bits", cell_bits, 1)
        _number("size", size, 1)
        _number("init", init, 0, 1 << cell_bits)
        if access not in ("ro", "rw"):
            _error("access", access, "ro or rw")
        if reset_state not in ("initialized", "uninitialized"):
            _error("reset_state", reset_state, "initialized or uninitialized")
        if type(is_program) is not bool:
            _error("is_program", is_program, "bool")
        if role is None:
            role = "internal"
        if role not in ("internal", "input", "output", "inout"):
            _error("role", role, "internal/input/output/inout")
        region_map = _memory_regions(regions, size)
        self._spaces[name] = {
            "name": name,
            "cell_bits": cell_bits,
            "size": size,
            "access": access,
            "reset_state": reset_state,
            "init": init,
            "is_program": is_program,
            "role": role,
        }
        if region_map:
            self._spaces[name]["regions"] = region_map
        self._validated = False

    def reg(self, name, *, bits, reset, is_pc=False, fields=None, role=None):
        self._name_check(name)
        _number("bits", bits, 1)
        _number("reset", reset, 0, 1 << bits)
        if type(is_pc) is not bool:
            _error("is_pc", is_pc, "bool")
        if fields is None:
            fields = {}
        if not isinstance(fields, dict):
            _error("fields", fields, "dict")
        occupied = {}
        normalized = {}
        for key, value in fields.items():
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) is None
                or hasattr(int, key)
                or key == "irq"
            ):
                _error(
                    "field name",
                    key,
                    "identifier without int attribute collision or leading underscore",
                )
            if _is_int(value):
                high = low = value
            elif isinstance(value, tuple) and len(value) == 2:
                high, low = value
            else:
                _error(key, value, "bit number or (high, low)")
            if (
                not _is_int(high)
                or not _is_int(low)
                or not 0 <= low <= high < bits
            ):
                _error(key, value, f"0 <= low <= high < {bits}")
            for position in range(low, high + 1):
                if position in occupied:
                    _error("fields", (key, occupied[position]), "nonoverlapping fields")
                occupied[position] = key
            normalized[key] = (high, low)
        if role is None:
            role = "internal"
        if role not in _ROLE_VALUES:
            _error("role", role, "/".join(_ROLE_VALUES))
        self._registers[name] = {
            "name": name,
            "bits": bits,
            "reset": reset,
            "is_pc": is_pc,
            "fields": normalized,
            "role": role,
        }
        self._validated = False

    @property
    def aliases(self) -> _Mapping[str, str]:
        return _MappingProxyType(self._aliases)

    def alias(self, name: str, target: str) -> None:
        self._name_check(name)
        if name.startswith("_"):
            _error("alias name", name, "identifier without a leading underscore")
        parts = target.split(".") if isinstance(target, str) else ()
        meta = self._registers.get(parts[0]) if parts else None
        if (
            meta is None
            or meta["is_pc"]
            or len(parts) > 2
            or (len(parts) == 2 and parts[1] not in meta["fields"])
        ):
            _error(
                "alias target", target, "previously declared non-PC register or field"
            )
        self._aliases[name] = target
        self._validated = False

    @property
    def mem_aliases(self) -> _Mapping[str, MemAliasInfo]:
        """Return declared memory aliases in declaration order."""
        return _MappingProxyType(self._mem_aliases)

    def mem_alias(
        self,
        name: str,
        target: str,
        *,
        endian: str | None = None,
        bit_order: str | None = None,
    ) -> None:
        """Declare a named memory alias for a cell, bit-slice, or contiguous range."""
        existing = (
            set(self._spaces)
            | set(self._registers)
            | set(self._irqs)
            | set(self._aliases)
            | set(self._mem_aliases)
        )
        info = _parse_mem_alias(
            name,
            target,
            endian=endian,
            bit_order=bit_order,
            spaces=self._spaces,
            existing_names=existing,
            error_cls=MCUDefinitionError,
        )
        self._mem_aliases[name] = info
        self._validated = False

    def irq_line(self, name):
        self._name_check(name)
        self._irqs.append(name)
        self._validated = False

    def instr(
        self,
        pattern,
        *,
        syntax,
        cycles,
        name=None,
        operands=None,
        overrides=None,
        kind=None,
    ):
        _number("cycles", cycles, 0)
        if operands is not None and not isinstance(operands, dict):
            _error("operands", operands, "dict or None")

        if kind not in (None, "call", "return"):
            _error("kind", kind, "None, call or return")
        if isinstance(overrides, str):
            overrides = (overrides,)
        elif isinstance(overrides, _Sequence):
            overrides = tuple(overrides)

        def decorate(fn):
            self._instructions.append(
                {
                    "pattern": pattern,
                    "syntax": syntax,
                    "cycles": cycles,
                    "name": fn.__name__ if name is None else name,
                    "fn": fn,
                    "operands": {} if operands is None else dict(operands),
                    "overrides": overrides,
                    "kind": kind,
                }
            )
            self._validated = False
            return fn

        return decorate

    def interrupt_check(self, fn):
        if self._interrupt is not None:
            _error("interrupt_check", fn.__name__, "at most one")
        self._interrupt = fn
        self._validated = False
        return fn

    def _policy(self, key, action, choices, **extra):
        if key in self._policies:
            _error(key, action, "declared exactly once")
        if action not in choices:
            _error(key, action, choices)
        self._policies[key] = dict(action=action, **extra)
        self._validated = False

    def on_undefined_opcode(self, action, *, nop_cells=None, nop_cycles=None):
        if action == "nop":
            _number("nop_cells", nop_cells, 1)
            _number("nop_cycles", nop_cycles, 0)
        elif nop_cells is not None or nop_cycles is not None:
            _error("nop_cells/nop_cycles", (nop_cells, nop_cycles), "omitted for stop")
        self._policy(
            "on_undefined_opcode",
            action,
            ("stop", "nop"),
            nop_cells=nop_cells,
            nop_cycles=nop_cycles,
        )

    def on_out_of_range(self, action, *, read_value=None):
        if action == "ignore" and not _is_int(read_value):
            _error("read_value", read_value, "integer")
        if action == "stop" and read_value is not None:
            _error("read_value", read_value, "omitted for stop")
        self._policy(
            "on_out_of_range", action, ("stop", "ignore"), read_value=read_value
        )

    def on_write_to_readonly(self, action):
        self._policy("on_write_to_readonly", action, ("stop", "ignore"))

    def on_uninitialized_read(self, action):
        self._policy("on_uninitialized_read", action, ("stop", "warn", "allow"))

    def _validation_key(self):
        return (
            tuple(
                (k, tuple(v.items())) if isinstance(v, dict) else (k, v)
                for k, v in self._spaces.items()
            ),
            tuple(
                (
                    k,
                    tuple(
                        (rk, tuple(rv.items()) if isinstance(rv, dict) else rv)
                        for rk, rv in v.items()
                    )
                    if isinstance(v, dict)
                    else v,
                )
                for k, v in self._registers.items()
            ),
            tuple(self._irqs),
            tuple(self._aliases.items()),
            tuple(self._mem_aliases.items()),
            tuple(
                (k, tuple(v.items())) if isinstance(v, dict) else (k, v)
                for k, v in self._policies.items()
            ),
            self._interrupt,
            tuple(
                (
                    ins.get("name"),
                    ins.get("pattern"),
                    ins.get("syntax"),
                    ins.get("cycles"),
                    ins.get("fn"),
                    ins.get("overrides"),
                    ins.get("kind"),
                    tuple(
                        (
                            ok,
                            (
                                ov.signed,
                                ov.relative,
                                ov.range,
                                tuple(ov.names.items())
                                if isinstance(ov.names, dict)
                                else ov.names,
                            )
                            if isinstance(ov, Operand)
                            else ov,
                        )
                        for ok, ov in ins.get("operands", {}).items()
                    )
                    if isinstance(ins.get("operands"), dict)
                    else ins.get("operands"),
                )
                for ins in self._instructions
            ),
        )

    def _validate(self):
        if self._validated:
            try:
                key = self._validation_key()
            except Exception:
                key = None
            if key is not None and getattr(self, "_validated_key", None) == key:
                return
        self._validated = False
        self._validated_key = None
        programs = [s for s in self._spaces.values() if s["is_program"]]
        pcs = [r for r in self._registers.values() if r["is_pc"]]
        if len(programs) != 1:
            _error("program spaces", len(programs), "exactly one")
        if len(pcs) != 1:
            _error("pc registers", len(pcs), "exactly one")
        for policy in (
            "on_undefined_opcode",
            "on_out_of_range",
            "on_write_to_readonly",
            "on_uninitialized_read",
        ):
            if policy not in self._policies:
                _error(policy, None, "explicit declaration")
        if not self._instructions:
            _error("instruction count", 0, "at least one instruction")
        cell_bits = programs[0]["cell_bits"]
        names = []
        for ins in self._instructions:
            if (
                not isinstance(ins["name"], str)
                or not ins["name"]
                or ins["name"] in names
            ):
                _error("instruction name", ins["name"], "nonempty unique name")
            names.append(ins["name"])
            if not isinstance(ins["pattern"], str):
                _error("pattern", ins["pattern"], "string")
            pattern = ins["pattern"].replace(" ", "").replace("\t", "")
            if (
                not pattern
                or re.fullmatch(r"[01A-Za-z-]+", pattern) is None
                or len(pattern) % cell_bits
            ):
                _error(
                    "pattern",
                    ins["pattern"],
                    f"valid pattern divisible by cell_bits={cell_bits}",
                )
            letters = list(dict.fromkeys(c for c in pattern if c.isalpha()))
            operands = ins.get("operands", {})
            if not isinstance(operands, dict) or any(
                key not in letters or not isinstance(value, Operand)
                for key, value in operands.items()
            ):
                _error(
                    "operands", operands, "pattern letters mapped to Operand instances"
                )
            for letter, operand in operands.items():
                operand._validate_width(letter, pattern.count(letter))
            params = list(inspect.signature(ins["fn"]).parameters.values())
            actual = [p.name for p in params[1:]]
            missing = [c for c in letters if c not in actual]
            extra = [c for c in actual if c not in letters]
            if (
                not params
                or params[0].kind
                not in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
                or missing
                or extra
                or any(
                    p.kind not in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
                    for p in params[1:]
                )
            ):
                _error(
                    "semantic parameters",
                    actual,
                    f"first State parameter; missing={missing}, extra={extra}",
                )
            try:
                for _, field, _, _ in string.Formatter().parse(ins["syntax"]):
                    if field is not None and field not in letters:
                        _error("syntax field", field, letters)
                ins["syntax"].format(**dict.fromkeys(letters, 0))
            except (ValueError, KeyError, IndexError, TypeError) as error:
                _error("syntax", ins["syntax"], str(error))
            ins["compiled"] = pattern
            ins["length"] = len(pattern) // cell_bits
            ins["letters"] = letters
            extractors = {letter: [] for letter in letters}
            for position, char in enumerate(pattern):
                if char not in extractors:
                    continue
                cell = position // cell_bits
                shift = cell_bits - 1 - position % cell_bits
                chunks = extractors[char]
                if chunks and chunks[-1][0] == cell and chunks[-1][1] == shift + 1:
                    _, _, count, mask = chunks[-1]
                    chunks[-1] = (cell, shift, count + 1, (mask << 1) | 1)
                else:
                    chunks.append((cell, shift, 1, 1))
            # Preserve pattern order within fields, including split cell runs.
            ins["extractors"] = tuple(
                (letter, tuple(chunks)) for letter, chunks in extractors.items()
            )
            masks = []
            for start in range(0, len(pattern), cell_bits):
                mask = value = 0
                for character in pattern[start : start + cell_bits]:
                    mask = (mask << 1) | int(character in "01")
                    value = (value << 1) | int(character == "1")
                masks.append((mask, value))
            ins["masks"] = masks
        _compile_overrides(self._instructions)
        for index, left in enumerate(self._instructions):
            for right in self._instructions[index + 1 :]:
                if all(
                    (lv ^ rv) & lm & rm == 0
                    for (lm, lv), (rm, rv) in zip(left["masks"], right["masks"])
                ) and (
                    right["name"] not in left["covered"]
                    and left["name"] not in right["covered"]
                ):
                    _error(
                        "overlapping instructions",
                        (left["name"], right["name"]),
                        "disjoint fixed bits",
                    )
        self._program = programs[0]["name"]
        self._pc = pcs[0]["name"]
        self._validated = True
        try:
            self._validated_key = self._validation_key()
        except Exception:
            self._validated_key = None


def _compile_overrides(instructions):
    by_name = {ins["name"]: ins for ins in instructions}
    for ins in instructions:
        declared = ins["overrides"]
        if declared is None:
            continue
        name = ins["name"]
        if (
            not isinstance(declared, tuple)
            or not declared
            or any(
                not isinstance(target, str) or target not in by_name or target == name
                for target in declared
            )
            or len(set(declared)) != len(declared)
        ):
            _error(
                f"{name} overrides",
                declared,
                "nonempty unique existing instruction names other than itself",
            )
        for target in declared:
            other = by_name[target]
            if ins["length"] != other["length"]:
                _error(f"{name} overrides", target, f"same length {ins['length']}")
            if (
                not all(
                    mask & other_mask == other_mask
                    and value & other_mask == other_value
                    for (mask, value), (other_mask, other_value) in zip(
                        ins["masks"], other["masks"]
                    )
                )
                or ins["masks"] == other["masks"]
            ):
                _error(
                    f"{name} overrides",
                    target,
                    "compatible true superset of fixed bits",
                )

    # Every edge goes to a less specific pattern, so its closure is ready first.
    for ins in sorted(
        instructions,
        key=lambda item: sum(mask.bit_count() for mask, _ in item["masks"]),
    ):
        covered = set()
        for target in ins["overrides"] or ():
            covered.add(target)
            covered.update(by_name[target]["covered"])
        ins["covered"] = frozenset(covered)


def _load_python(path, prefix):
    module_name = (
        prefix + _hashlib.sha256(str(_Path(path).resolve()).encode("utf-8")).hexdigest()
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"path={path!r}: readable Python module required")
    module = importlib.util.module_from_spec(spec)
    present = module_name in _sys.modules
    previous = _sys.modules.get(module_name)
    _sys.modules[module_name] = module
    complete = False
    try:
        spec.loader.exec_module(module)
        complete = True
        return module
    finally:
        if not complete:
            if present:
                _sys.modules[module_name] = previous
            else:
                _sys.modules.pop(module_name, None)


def load_mcu(path: str | os.PathLike[str]) -> MCU:
    try:
        module = _load_python(path, "_isasim_description_")
    except Exception as error:
        raise MCUDefinitionError(f"path={path!r}: {error!r}") from error
    mcu = getattr(module, "mcu", None)
    if not isinstance(mcu, MCU):
        _error("mcu", mcu, "MCU instance")
    mcu._validate()
    try:
        mcu._description_sha256 = _hashlib.sha256(_Path(path).read_bytes()).hexdigest()
    except OSError as error:
        raise MCUDefinitionError(f"path={path!r}: {error}") from error
    return mcu
