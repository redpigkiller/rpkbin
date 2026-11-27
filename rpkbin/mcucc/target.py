"""Declarative machine model and strict loading validation."""

from dataclasses import dataclass, fields
import re
import runpy
from string import Formatter
from types import MappingProxyType

from .errors import TargetError
from .opsem import _MSB0_FLAGS


def _fail(path, value, expected):
    raise TargetError(f"{path}: {value!r}; expected {expected}")


class _DataType(type):
    def __call__(model, *args, **kwargs):
        try:
            return super().__call__(*args, **kwargs)
        except TypeError as exc:
            raise TargetError(f"{model.__name__}: {exc}; expected declared fields") from exc


@dataclass(frozen=True)
class Space(metaclass=_DataType):
    name: str
    addr_bits: int
    kind: str


@dataclass(frozen=True)
class Reg(metaclass=_DataType):
    name: str
    bits: int


@dataclass(frozen=True)
class Alias(metaclass=_DataType):
    name: str
    parts: tuple


@dataclass(frozen=True)
class Cls(metaclass=_DataType):
    name: str
    members: tuple


@dataclass(frozen=True)
class Flag(metaclass=_DataType):
    name: str


@dataclass(frozen=True)
class R(metaclass=_DataType):
    name: str
    cls: str


@dataclass(frozen=True)
class I(metaclass=_DataType):  # noqa: E742
    name: str
    bits: int
    signed: bool = False
    values: tuple | None = None


@dataclass(frozen=True)
class M(metaclass=_DataType):
    name: str
    space: str
    bits: int | None = None


@dataclass(frozen=True)
class P(metaclass=_DataType):
    name: str
    cls: str
    space: str


@dataclass(frozen=True)
class T(metaclass=_DataType):
    name: str
    rel_bits: int | None = None
    abs_bits: int | None = None
    base: str = "end"


@dataclass(frozen=True)
class Form(metaclass=_DataType):
    id: str
    op: str
    width: int
    operands: tuple
    bind: dict
    bytes: int
    asm: str
    flags_read: dict | None = None
    flags_write: dict | None = None
    clobbers: tuple = ()
    encode: object = None


@dataclass(frozen=True)
class MemLoc(metaclass=_DataType):
    space: str
    addr: int
    n: int = 1


@dataclass(frozen=True)
class RegLoc:
    name: str


@dataclass(frozen=True)
class SlotLoc:
    kind: str
    key: int
    first: int
    n: int


@dataclass(frozen=True)
class Abi(metaclass=_DataType):
    arg_locs: dict
    ret_locs: dict
    callee_saved: tuple


@dataclass(frozen=True)
class SimBinding(metaclass=_DataType):
    make_sim: object
    program_space: str
    pc: str
    halt_cells: tuple
    names: dict | None = None
    init: object = None


@dataclass(frozen=True)
class Target(metaclass=_DataType):
    name: str
    cell_bits: int
    spaces: tuple
    regs: tuple
    aliases: tuple
    classes: tuple
    flags: tuple
    forms: tuple
    abi: Abi
    long_branch: str
    comment: str
    local_label: str
    label_def: str

    def __post_init__(self):
        _validate(self)


_ARGS = MappingProxyType({
    **{op: ("dst", "a", "b") for op in ("add", "sub", "and", "or", "xor", "shl", "shr")},
    **{op: ("dst", "a") for op in ("mov", "const", "not", "neg", "ld", "ld_ind")},
    **{op: ("a", "b") for op in ("cmp", "st", "st_ind")},
    "push": ("a",), "pop": ("dst",),
    **{op: () for op in ("jmp", "br", "call", "ret")},
})


def _seq(target, name, seen=()):
    if name in seen:
        _fail("target.aliases.parts", (*seen, name), "acyclic aliases")
    if name in {r.name for r in target.regs}:
        return (name,)
    alias = next((a for a in target.aliases if a.name == name), None)
    if alias is None:
        _fail("target.register", name, "existing register or alias")
    return tuple(r for p in alias.parts for r in _seq(target, p, (*seen, name)))


def _width(target, name):
    return len(_seq(target, name)) * target.cell_bits


def units(target, loc):
    if isinstance(loc, RegLoc):
        loc = loc.name
    if isinstance(loc, SlotLoc):
        return frozenset(("slot", loc.kind, loc.key, i) for i in range(loc.first, loc.first + loc.n))
    if isinstance(loc, MemLoc):
        return frozenset((loc.space, loc.addr + i) for i in range(loc.n))
    return frozenset(_seq(target, loc))


def part(target, loc, which):
    if which not in ("hi", "lo"):
        _fail("location.part", which, "hi or lo")
    if isinstance(loc, SlotLoc):
        if loc.n != 2:
            _fail("location.part", loc, "two-cell slot")
        return SlotLoc(loc.kind, loc.key, loc.first + int(which == "hi"), 1)
    if isinstance(loc, RegLoc):
        return RegLoc(part(target, loc.name, which))
    if isinstance(loc, MemLoc):
        if loc.n != 2:
            _fail("location.part", loc, "two-cell location")
        return MemLoc(loc.space, loc.addr + int(which == "hi"))
    sequence = _seq(target, loc)
    if len(sequence) != 2:
        _fail("location.part", loc, "two-cell alias")
    return sequence[0 if which == "hi" else -1]


def _fields(template, path):
    try:
        parsed = tuple(Formatter().parse(template))
    except (ValueError, TypeError) as exc:
        raise TargetError(f"{path}: {template!r}; expected a valid template") from exc
    if any(spec or conversion for _, _, spec, conversion in parsed):
        _fail(path, template, "plain named placeholders")
    return tuple(field for _, field, _, _ in parsed if field is not None)


def _integer(path, value, minimum):
    if type(value) is not int or value < minimum:
        _fail(path, value, f"integer >= {minimum}")


def _expect(path, value, expected):
    if not isinstance(value, expected) or (expected is int and type(value) is not int):
        _fail(path, value, expected.__name__)


def _tuple_of(path, value, expected):
    _expect(path, value, tuple)
    for index, item in enumerate(value):
        _expect(f"{path}[{index}]", item, expected)


def _declarations(target):
    for name in ("name", "long_branch", "comment", "local_label", "label_def"):
        _expect("target." + name, getattr(target, name), str)
    _expect("target.cell_bits", target.cell_bits, int)
    for field, model in zip(fields(target)[2:8], (Space, Reg, Alias, Cls, Flag, Form)):
        _tuple_of("target." + field.name, getattr(target, field.name), model)
    for alias in target.aliases:
        _tuple_of("target.aliases.parts", alias.parts, str)
    for cls in target.classes:
        _tuple_of("target.classes.members", cls.members, str)
    for reg in target.regs:
        _expect("target.regs.bits", reg.bits, int)
    for form in target.forms:
        path = f"target.forms[{form.id!r}]"
        for name in ("id", "op", "asm"):
            _expect(path + "." + name, getattr(form, name), str)
        _expect(path + ".width", form.width, int)
        _expect(path + ".bind", form.bind, dict)
        for key, value in form.bind.items():
            _expect(path + ".bind.key", key, str)
            _expect(path + ".bind.value", value, str)
        _tuple_of(path + ".clobbers", form.clobbers, str)
        for name, value_type in (("flags_read", int), ("flags_write", str)):
            mapping = getattr(form, name)
            if mapping is not None:
                _expect(path + "." + name, mapping, dict)
                for key, value in mapping.items():
                    _expect(path + "." + name + ".key", key, str)
                    _expect(path + "." + name + ".value", value, value_type)
        _expect(path + ".operands", form.operands, tuple)
        for index, operand in enumerate(form.operands):
            subpath = path + f".operands[{index}]"
            if not isinstance(operand, (R, I, M, P, T)):
                _fail(subpath, operand, "R/I/M/P/T operand")
            _expect(subpath + ".name", operand.name, str)
            if isinstance(operand, (R, P)):
                _expect(subpath + ".cls", operand.cls, str)
            if isinstance(operand, (M, P)):
                _expect(subpath + ".space", operand.space, str)
            if isinstance(operand, I) and operand.values is not None:
                _tuple_of(subpath + ".values", operand.values, int)
    _expect("target.abi", target.abi, Abi)
    _tuple_of("target.abi.callee_saved", target.abi.callee_saved, str)
    for name in ("arg_locs", "ret_locs"):
        mapping = getattr(target.abi, name)
        _expect("target.abi." + name, mapping, dict)
        for width, value in mapping.items():
            _expect("target.abi." + name + ".width", width, int)
            if name == "arg_locs":
                _expect("target.abi.arg_locs.locations", value, tuple)
            for location in value if name == "arg_locs" else (value,):
                if isinstance(location, MemLoc):
                    _expect("target.abi.location.space", location.space, str)
                    _expect("target.abi.location.addr", location.addr, int)
                    _expect("target.abi.location.n", location.n, int)
                elif not isinstance(location, str):
                    _fail("target.abi.location", location, "register or MemLoc")


def _validate(target):
    _declarations(target)
    if not isinstance(target.name, str) or not target.name:
        _fail("target.name", target.name, "nonempty string")
    if target.cell_bits != 8:
        _fail("target.cell_bits", target.cell_bits, "8")
    groups = (target.spaces, target.regs, target.aliases, target.classes, target.flags)
    seen = set()
    for group in groups:
        for item in group:
            if not isinstance(item.name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item.name) or item.name in seen:
                _fail("target.names", item.name, "unique identifier")
            seen.add(item.name)
    spaces = {s.name: s for s in target.spaces}
    registers = {r.name for r in (*target.regs, *target.aliases)}
    classes = {c.name: c for c in target.classes}
    for space in target.spaces:
        _integer("target.spaces.addr_bits", space.addr_bits, 1)
        if space.kind not in ("data", "program"):
            _fail("target.spaces.kind", space.kind, "data or program")
    if sum(s.kind == "program" for s in target.spaces) != 1 or not any(s.kind == "data" for s in target.spaces):
        _fail("target.spaces", target.spaces, "one program space and at least one data space")
    for reg in target.regs:
        if reg.bits != target.cell_bits:
            _fail("target.regs.bits", reg.bits, target.cell_bits)
    for alias in target.aliases:
        seq = _seq(target, alias.name)
        if len(alias.parts) < 2 or len(seq) != len(set(seq)):
            _fail("target.aliases.parts", alias.parts, "at least two parts with distinct units")
    for cls in target.classes:
        if not cls.members or any(m not in registers for m in cls.members) or len({_width(target, m) for m in cls.members}) != 1:
            _fail("target.classes.members", cls.members, "nonempty registers of equal width")
    form_ids = set()
    known_flags = {f.name for f in target.flags}
    for form in target.forms:
        path = f"target.forms[{form.id!r}]"
        if not isinstance(form.id, str) or not form.id or form.id in form_ids:
            _fail(path + ".id", form.id, "unique nonempty form id")
        form_ids.add(form.id)
        if form.op not in _ARGS:
            _fail(path + ".op", form.op, tuple(_ARGS))
        _integer(path + ".bytes", form.bytes, 1)
        if form.width not in ((0,) if form.op in ("jmp", "br", "call", "ret") else (8, 16)):
            _fail(path + ".width", form.width, "operation width")
        operands = {o.name: o for o in form.operands}
        if len(operands) != len(form.operands) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", o) for o in operands):
            _fail(path + ".operands", form.operands, "distinct operand names")
        if set(form.bind) != set(_ARGS[form.op]) or set(form.bind.values()) != {
            o.name for o in form.operands if not isinstance(o, T)
        }:
            _fail(path + ".bind", form.bind, _ARGS[form.op])
        if form.op in ("ld", "st", "ld_ind", "st_ind"):
            address = operands[form.bind["a"]]
            expected = P if form.op.endswith("_ind") else M
            if not isinstance(address, expected):
                _fail(path + ".operands." + address.name, address, f"{expected.__name__} address operand")
        refs = _fields(form.asm, path + ".asm")
        required = {o.name for o in form.operands if not isinstance(o, I) or o.values is None or len(o.values) != 1}
        if set(refs) - operands.keys() or required - set(refs):
            _fail(path + ".asm", form.asm, f"placeholders {sorted(required)!r}")
        for operand in form.operands:
            subpath = path + ".operands." + operand.name
            if isinstance(operand, (R, P)):
                if operand.cls not in classes:
                    _fail(subpath + ".cls", operand.cls, "existing class")
                ow = _width(target, classes[operand.cls].members[0])
                if isinstance(operand, P):
                    if operand.space not in spaces or spaces[operand.space].kind != "data" or ow != spaces[operand.space].addr_bits:
                        _fail(subpath, operand, "pointer class matching data address width")
                elif ow != form.width:
                    _fail(subpath, operand, f"register width {form.width}")
            elif isinstance(operand, I):
                _integer(subpath + ".bits", operand.bits, 1)
                if operand.signed is not False or operand.bits > form.width:
                    _fail(subpath, operand, "unsigned immediate no wider than operation")
                if operand.values is not None and (not operand.values or any(type(v) is not int or not 0 <= v < 1 << operand.bits for v in operand.values)):
                    _fail(subpath + ".values", operand.values, "values within immediate range")
            elif isinstance(operand, M):
                if operand.space not in spaces or spaces[operand.space].kind != "data":
                    _fail(subpath + ".space", operand.space, "data space")
                nb = spaces[operand.space].addr_bits if operand.bits is None else operand.bits
                _integer(subpath + ".bits", nb, 1)
                if nb > spaces[operand.space].addr_bits:
                    _fail(subpath + ".bits", nb, "no wider than space")
            elif isinstance(operand, T):
                if (operand.rel_bits is None) == (operand.abs_bits is None) or operand.base not in ("start", "end"):
                    _fail(subpath, operand, "one relative or absolute range and valid base")
                _integer(subpath + ".bits", operand.rel_bits if operand.rel_bits is not None else operand.abs_bits, 1)
            else:
                _fail(subpath, operand, "R/I/M/P/T operand")
        nt = sum(isinstance(o, T) for o in form.operands)
        if nt != int(form.op in ("jmp", "br", "call")):
            _fail(path + ".operands", form.operands, "target operand only for jump/branch/call")
        reads, writes = form.flags_read or {}, form.flags_write or {}
        if (form.op == "br" and len(reads) != 1) or (form.op != "br" and reads) or any(k not in known_flags or v not in (0, 1) for k, v in reads.items()):
            _fail(path + ".flags_read", reads, "one declared flag for branch only")
        if any(k not in known_flags or v not in ("zero", "carry", "borrow", "nborrow", "borrow_msb0",
                                                "nborrow_msb0", "shift_out", "undef") for k, v in writes.items()):
            _fail(path + ".flags_write", writes, "declared flag and semantic word")
        if form.op not in ("sub", "cmp") and any(v in _MSB0_FLAGS for v in writes.values()):
            _fail(path + ".flags_write", writes, f"conditional borrow semantics require sub or cmp, got {form.op!r}")
        if any(c not in registers for c in form.clobbers):
            _fail(path + ".clobbers", form.clobbers, "existing registers")
        if form.encode is not None and not callable(form.encode):
            _fail(path + ".encode", form.encode, "callable or None")
    def check_loc(loc, width):
        if isinstance(loc, str):
            if loc not in registers or _width(target, loc) != width:
                _fail("target.abi.location", loc, f"register of width {width}")
        elif isinstance(loc, MemLoc):
            count = (width + target.cell_bits - 1) // target.cell_bits
            if loc.space not in spaces or spaces[loc.space].kind != "data" or loc.n != count or not 0 <= loc.addr or loc.addr + loc.n > 1 << spaces[loc.space].addr_bits:
                _fail("target.abi.location", loc, f"valid data address spanning {count} cells for width {width}")
        else:
            _fail("target.abi.location", loc, "register or MemLoc")
    for width, locs in target.abi.arg_locs.items():
        if width not in (8, 16):
            _fail("target.abi.arg_locs", width, "8 or 16")
        for loc in locs:
            check_loc(loc, width)
    for width, loc in target.abi.ret_locs.items():
        if width not in (8, 16):
            _fail("target.abi.ret_locs", width, "8 or 16")
        check_loc(loc, width)
    if any(p not in registers for p in target.abi.callee_saved):
        _fail("target.abi.callee_saved", target.abi.callee_saved, "existing registers")
    pushes = {o.cls for f in target.forms if f.op == "push" for o in f.operands if isinstance(o, R)}
    pops = {o.cls for f in target.forms if f.op == "pop" for o in f.operands if isinstance(o, R)}
    if pushes != pops:
        _fail("target.forms.stack", (sorted(pushes), sorted(pops)), "paired push/pop classes")
    if target.long_branch not in ("none", "invert_over_jump"):
        _fail("target.long_branch", target.long_branch, "none or invert_over_jump")
    if target.long_branch == "invert_over_jump":
        long_jumps = [f for f in target.forms if f.op == "jmp" and any(isinstance(o, T) and o.abs_bits for o in f.operands)]
        pairs = {(key, val) for f in target.forms if f.op == "br" for key, val in (f.flags_read or {}).items()}
        if not long_jumps or not pairs or any((k, 1 - v) not in pairs for k, v in pairs):
            _fail("target.long_branch", target.long_branch, "opposite branches and absolute jump")
    if not {"jmp", "ret"} <= {f.op for f in target.forms}:
        _fail("target.forms", tuple(f.op for f in target.forms), "jump and return forms")
    if not isinstance(target.comment, str) or not target.comment:
        _fail("target.comment", target.comment, "nonempty prefix")
    if sorted(_fields(target.local_label, "target.local_label")) != ["fn", "n"]:
        _fail("target.local_label", target.local_label, "one fn and one n placeholder")
    if not re.fullmatch(r"[A-Za-z_.][A-Za-z0-9_.]*", target.local_label.format(fn="sample", n=1)):
        _fail("target.local_label", target.local_label, "valid label identifier")
    if _fields(target.label_def, "target.label_def") != ("name",):
        _fail("target.label_def", target.label_def, "one name placeholder")


def load_target_file(path):
    try:
        namespace = runpy.run_path(str(path))
    except (OSError, SyntaxError, ImportError, TypeError, ValueError) as exc:
        raise TargetError(f"target file {str(path)!r}: {exc}") from exc
    target = namespace.get("TARGET")
    if not isinstance(target, Target):
        _fail("TARGET", target, "Target instance")
    _validate(target)
    binding, stubs = namespace.get("SIM"), namespace.get("STUBS")
    if binding is not None and not isinstance(binding, SimBinding):
        _fail("SIM", binding, "SimBinding or None")
    if stubs is not None and (not isinstance(stubs, dict) or any(not isinstance(k, str) or not isinstance(v, tuple) or any(type(c) is not int or not 0 <= c < 1 << target.cell_bits for c in v) for k, v in stubs.items())):
        _fail("STUBS", stubs, "mapping of names to cell tuples")
    return target, binding, stubs


def load_target(path):
    return load_target_file(path)[0]
