"""Debugger state, expressions, and access matching."""

from __future__ import annotations

import ast as _ast
from dataclasses import dataclass as _dataclass
import math as _math
import re as _re

from . import helpers as _helpers
from .errors import DebugError, IsaSimError, StateAccessError, TargetError, _is_int


DEBUG_HELPER_NAMES = (
    "to_signed",
    "to_unsigned",
    "sign_extend",
    "bit",
    "parity",
)
DEBUG_NAMESPACE_NAMES = frozenset({"r", "m", "i"})
DEBUG_SCALAR_NAMES = frozenset({"pc", "cycle"})
DEBUG_RESERVED_NAMES = DEBUG_NAMESPACE_NAMES | DEBUG_SCALAR_NAMES
CONDITION_MAX_BITS = 1 << 16
CONDITION_MAX_DEPTH = 1 << 8
CONDITION_INT_METHODS = frozenset({"bit_count", "bit_length"})


@_dataclass(frozen=True, slots=True)
class ConditionTarget:
    """Resolved debugger target inside one of the r/m/i namespaces."""

    namespace: str
    name: str
    kind: str  # "reg" | "scalar" | "space"
    fields: frozenset[str] = frozenset()

    def expr(self) -> str:
        """Return an unambiguous core lookup expression for this target."""
        escaped = self.name.replace("\\", "\\\\").replace('"', '\\"')
        return f'{self.namespace}["{escaped}"]'


@_dataclass(frozen=True, slots=True)
class _ConstExpr:
    value: int


@_dataclass(frozen=True, slots=True)
class _BuiltinExpr:
    name: str  # "pc" | "cycle"


@_dataclass(frozen=True, slots=True)
class _TargetExpr:
    namespace: str  # "r" | "m" | "i"
    name: str


@_dataclass(frozen=True, slots=True)
class _SpaceReadExpr:
    space: str
    addr: object


@_dataclass(frozen=True, slots=True)
class _UnaryExpr:
    op: type[_ast.unaryop]
    operand: object


@_dataclass(frozen=True, slots=True)
class _BinExpr:
    op: type[_ast.operator]
    left: object
    right: object


@_dataclass(frozen=True, slots=True)
class _BoolExpr:
    is_and: bool
    values: tuple[object, ...]


@_dataclass(frozen=True, slots=True)
class _IfExpr:
    test: object
    body: object
    orelse: object


@_dataclass(frozen=True, slots=True)
class _CompareStep:
    op: type[_ast.cmpop]
    comparator: object  # _Expr or tuple[_Expr, ...] for In / NotIn


@_dataclass(frozen=True, slots=True)
class _CompareExpr:
    left: object
    steps: tuple[_CompareStep, ...]


@_dataclass(frozen=True, slots=True)
class _HelperCallee:
    name: str


@_dataclass(frozen=True, slots=True)
class _MethodCallee:
    receiver: object
    method: str


@_dataclass(frozen=True, slots=True)
class _IfCallee:
    test: object
    body: object
    orelse: object


@_dataclass(frozen=True, slots=True)
class _CallExpr:
    callee: object
    args: tuple[object, ...]
    keywords: tuple[tuple[str, object], ...]


@_dataclass(frozen=True, slots=True)
class _ConditionPlan:
    source: str
    root: object


@_dataclass(frozen=True, slots=True)
class _Candidate:
    sort: str  # "namespace" | "reg" | "space" | "int"
    label: str
    node: object
    fields: frozenset[str] = frozenset()
    reg_name: str | None = None


_COMPARE_OPS = {
    _ast.Eq: lambda a, b: a == b,
    _ast.NotEq: lambda a, b: a != b,
    _ast.Lt: lambda a, b: a < b,
    _ast.LtE: lambda a, b: a <= b,
    _ast.Gt: lambda a, b: a > b,
    _ast.GtE: lambda a, b: a >= b,
}

_SIMPLE_BIN_OPS = {
    _ast.Add: lambda a, b: a + b,
    _ast.Sub: lambda a, b: a - b,
    _ast.FloorDiv: lambda a, b: a // b,
    _ast.Mod: lambda a, b: a % b,
    _ast.RShift: lambda a, b: a >> b,
    _ast.BitAnd: lambda a, b: a & b,
    _ast.BitOr: lambda a, b: a | b,
    _ast.BitXor: lambda a, b: a ^ b,
}


def _check_int_bits(value: int, *, context: str = "value") -> int:
    if type(value) not in (int, bool):
        raise DebugError(f"{context}={value!r}: expected int/bool")
    if value.bit_length() > CONDITION_MAX_BITS:
        raise DebugError(
            f"{context} exceeds condition resource limit of {CONDITION_MAX_BITS} bits"
        )
    return value


def _check_bit_width(bits: int, *, minimum: int = 1, label: str = "bits") -> int:
    if not _is_int(bits) or bits < minimum:
        raise DebugError(f"{label}={bits!r}: expected integer >= {minimum}")
    if bits > CONDITION_MAX_BITS:
        raise DebugError(
            f"{label}={bits!r}: exceeds condition limit of {CONDITION_MAX_BITS} bits"
        )
    return bits


def _eval_pow(base: int, exp: int) -> int:
    if exp < 0:
        raise DebugError(f"exponent={exp!r}: negative exponent is not supported")
    if exp == 0 or base == 1:
        return 1
    if base == 0:
        return 0
    if base == -1:
        return -1 if (exp & 1) else 1
    mag = abs(base)
    if exp >= CONDITION_MAX_BITS or exp * _math.log2(mag) > CONDITION_MAX_BITS + 2:
        raise DebugError(
            f"power result exceeds condition resource limit of {CONDITION_MAX_BITS} bits"
        )
    return _check_int_bits(base**exp, context="power result")


def _eval_lshift(left: int, shift: int) -> int:
    if shift < 0:
        raise DebugError(f"shift={shift!r}: negative shift count")
    if shift > CONDITION_MAX_BITS or (
        left != 0 and left.bit_length() + shift > CONDITION_MAX_BITS
    ):
        raise DebugError(
            f"left-shift result exceeds condition resource limit of {CONDITION_MAX_BITS} bits"
        )
    return _check_int_bits(left << shift, context="left-shift result")


def _eval_mult(left: int, right: int) -> int:
    if left != 0 and right != 0:
        if (left.bit_length() - 1) + (right.bit_length() - 1) + 1 > CONDITION_MAX_BITS:
            raise DebugError(
                f"product exceeds condition resource limit of {CONDITION_MAX_BITS} bits"
            )
    return _check_int_bits(left * right, context="product")


def _call_to_unsigned(value: int, bits: int) -> int:
    _check_bit_width(bits, minimum=1, label="bits")
    return _check_int_bits(_helpers.to_unsigned(value, bits))


def _call_to_signed(value: int, bits: int) -> int:
    _check_bit_width(bits, minimum=1, label="bits")
    return _check_int_bits(_helpers.to_signed(value, bits))


def _call_sign_extend(value: int, from_bits: int, to_bits: int) -> int:
    _check_bit_width(from_bits, minimum=1, label="from_bits")
    _check_bit_width(to_bits, minimum=1, label="to_bits")
    return _check_int_bits(_helpers.sign_extend(value, from_bits, to_bits))


def _call_bit(value: int, n: int) -> int:
    return _helpers.bit(value, n)


def _call_parity(value: int) -> int:
    return _helpers.parity(value)


_HELPER_SPECS = {
    "to_unsigned": (("value", "bits"), _call_to_unsigned),
    "to_signed": (("value", "bits"), _call_to_signed),
    "sign_extend": (("value", "from_bits", "to_bits"), _call_sign_extend),
    "bit": (("value", "n"), _call_bit),
    "parity": (("value",), _call_parity),
}


def _bind_helper_args(
    name: str,
    params: tuple[str, ...],
    args: list[int],
    keywords: dict[str, int],
) -> list[int]:
    if len(args) > len(params):
        raise DebugError(
            f"{name}() takes {len(params)} argument(s) but {len(args)} were given"
        )
    bound: dict[str, int] = dict(zip(params, args))
    for key, val in keywords.items():
        if key not in params:
            raise DebugError(f"{name}() got an unexpected keyword argument {key!r}")
        if key in bound:
            raise DebugError(f"{name}() got multiple values for argument {key!r}")
        bound[key] = val
    missing = [param for param in params if param not in bound]
    if missing:
        raise DebugError(
            f"{name}() missing {len(missing)} required argument(s): {', '.join(missing)}"
        )
    return [bound[param] for param in params]


def _dedup_candidates(candidates: list[_Candidate]) -> list[_Candidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[_Candidate] = []
    for cand in candidates:
        key = (cand.sort, cand.label)
        if key not in seen:
            seen.add(key)
            unique.append(cand)
    return unique


class _ConditionBinder:
    """Validate an AST against the condition grammar and bind MCU targets."""

    _USABLE_SORTS = frozenset({"reg", "int"})

    def __init__(self, sim, source: str, *, allow_shorthand: bool) -> None:
        self.sim = sim
        self.source = source
        self.allow_shorthand = allow_shorthand

    def _fail(self, message: str) -> None:
        raise DebugError(f"expr={self.source!r}: {message}")

    def _segment(self, node: _ast.AST) -> str:
        return _ast.get_source_segment(self.source, node) or "<expr>"

    def compile(self) -> _ConditionPlan:
        if not isinstance(self.source, str):
            raise DebugError(f"expr={self.source!r}: expected str")
        try:
            tree = _ast.parse(self.source, mode="eval")
        except (SyntaxError, ValueError, RecursionError) as error:
            raise DebugError(f"expr={self.source!r}: {error}") from error
        root = self._bind_expr(tree.body, depth=0)
        return _ConditionPlan(self.source, root)

    def _check_depth(self, depth: int) -> None:
        if depth > CONDITION_MAX_DEPTH:
            self._fail(
                f"expression nesting depth exceeds limit of {CONDITION_MAX_DEPTH}"
            )

    def _target_candidate(self, target: ConditionTarget) -> _Candidate:
        label = target.expr()
        if target.kind == "space":
            return _Candidate("space", label, target.name)
        sort = "reg" if target.kind == "reg" else "int"
        return _Candidate(
            sort=sort,
            label=label,
            node=_TargetExpr(target.namespace, target.name),
            fields=target.fields,
            reg_name=target.name if target.kind == "reg" else None,
        )

    def _bind_expr(self, node: _ast.expr, depth: int) -> object:
        candidates = self._resolve_candidates(node, depth)
        usable = _dedup_candidates(
            [cand for cand in candidates if cand.sort in self._USABLE_SORTS]
        )
        if len(usable) == 1:
            return usable[0].node
        if len(usable) > 1:
            choices = " or ".join(f"'{cand.label}'" for cand in usable)
            self._fail(f"ambiguous target '{self._segment(node)}' (use {choices})")
        for sort, detail in (
            ("space", "memory space {label} requires [address]"),
            ("namespace", "namespace {label!r} requires a target name"),
        ):
            match = next((cand for cand in candidates if cand.sort == sort), None)
            if match is not None:
                self._fail(detail.format(label=match.label))
        self._fail(f"invalid target '{self._segment(node)}'")

    def _bind_callee(self, node: _ast.expr, depth: int) -> object:
        self._check_depth(depth)
        if isinstance(node, _ast.Name):
            if node.id in DEBUG_HELPER_NAMES:
                return _HelperCallee(node.id)
            self._fail(f"unsupported function call '{node.id}'")
        if isinstance(node, _ast.Attribute):
            if node.attr not in CONDITION_INT_METHODS:
                self._fail(f"unsupported method call '.{node.attr}'")
            receiver = self._bind_expr(node.value, depth + 1)
            return _MethodCallee(receiver, node.attr)
        if isinstance(node, _ast.IfExp):
            test = self._bind_expr(node.test, depth + 1)
            body = self._bind_callee(node.body, depth + 1)
            orelse = self._bind_callee(node.orelse, depth + 1)
            return _IfCallee(test, body, orelse)
        self._fail(f"unsupported call target '{self._segment(node)}'")

    def _resolve_name(self, node: _ast.Name) -> list[_Candidate]:
        name = node.id
        if not self.allow_shorthand:
            if name in DEBUG_NAMESPACE_NAMES:
                return [_Candidate("namespace", name, name)]
            if name in DEBUG_SCALAR_NAMES:
                return [_Candidate("int", name, _BuiltinExpr(name))]
            if name in DEBUG_HELPER_NAMES:
                self._fail(f"helper {name!r} must be called as {name}(...)")
            self._fail(f"unknown name {name!r}")

        candidates: list[_Candidate] = []
        if name in DEBUG_NAMESPACE_NAMES:
            target = self.sim._resolve_mcu_target(name)
            if target is not None:
                candidates.append(self._target_candidate(target))
            candidates.append(_Candidate("namespace", name, name))
            return candidates
        if name in DEBUG_SCALAR_NAMES:
            candidates.append(_Candidate("int", name, _BuiltinExpr(name)))
            target = self.sim._resolve_mcu_target(name)
            if target is not None and target.kind == "space":
                candidates.append(self._target_candidate(target))
            return candidates
        target = self.sim._resolve_mcu_target(name)
        if target is not None:
            return [self._target_candidate(target)]
        if name in DEBUG_HELPER_NAMES:
            self._fail(f"helper {name!r} must be called as {name}(...)")
        self._fail(f"unknown name {name!r}")

    def _resolve_attribute(self, node: _ast.Attribute, depth: int) -> list[_Candidate]:
        base_cands = self._resolve_candidates(node.value, depth + 1)
        attr = node.attr
        next_cands: list[_Candidate] = []
        for cand in base_cands:
            if cand.sort == "namespace":
                target = self.sim._resolve_namespace_target(cand.label, attr)
                if target is not None:
                    next_cands.append(self._target_candidate(target))
            elif cand.sort == "reg" and attr in cand.fields and cand.reg_name is not None:
                next_cands.append(
                    _Candidate(
                        "int",
                        f"{cand.label}.{attr}",
                        _TargetExpr("r", f"{cand.reg_name}.{attr}"),
                    )
                )
        if not next_cands:
            space_cand = next((c for c in base_cands if c.sort == "space"), None)
            if space_cand is not None:
                self._fail(f"memory space {space_cand.label} requires [address]")
            self._fail(f"unknown target or attribute '{self._segment(node)}'")
        return next_cands

    def _resolve_subscript(self, node: _ast.Subscript, depth: int) -> list[_Candidate]:
        base_cands = self._resolve_candidates(node.value, depth + 1)
        is_str_key = (
            isinstance(node.slice, _ast.Constant) and type(node.slice.value) is str
        )
        addr_expr: object | None = None
        if not is_str_key and any(cand.sort == "space" for cand in base_cands):
            addr_expr = self._bind_expr(node.slice, depth + 1)

        next_cands: list[_Candidate] = []
        for cand in base_cands:
            if cand.sort == "namespace" and is_str_key:
                target = self.sim._resolve_namespace_target(
                    cand.label, node.slice.value
                )
                if target is not None:
                    next_cands.append(self._target_candidate(target))
            elif cand.sort == "space" and not is_str_key:
                assert addr_expr is not None
                next_cands.append(
                    _Candidate(
                        "int",
                        f"{cand.label}[{self._segment(node.slice)}]",
                        _SpaceReadExpr(str(cand.node), addr_expr),
                    )
                )
        if not next_cands:
            ns_cand = next((c for c in base_cands if c.sort == "namespace"), None)
            if ns_cand is not None and not is_str_key:
                self._fail(f"expected string literal target for {ns_cand.label!r}")
            self._fail(f"unknown or invalid subscript '{self._segment(node)}'")
        return next_cands

    def _resolve_candidates(self, node: _ast.expr, depth: int) -> list[_Candidate]:
        self._check_depth(depth)
        if isinstance(node, _ast.Constant):
            value = node.value
            if type(value) not in (int, bool):
                self._fail(
                    f"unsupported literal {value!r}; only integer and boolean literals are allowed"
                )
            if value.bit_length() > CONDITION_MAX_BITS:
                self._fail(
                    f"integer literal exceeds condition resource limit of {CONDITION_MAX_BITS} bits"
                )
            return [_Candidate("int", "", _ConstExpr(value))]
        if isinstance(node, _ast.Name):
            return self._resolve_name(node)
        if isinstance(node, _ast.Attribute):
            return self._resolve_attribute(node, depth)
        if isinstance(node, _ast.Subscript):
            return self._resolve_subscript(node, depth)
        if isinstance(node, _ast.UnaryOp):
            if not isinstance(node.op, (_ast.UAdd, _ast.USub, _ast.Invert, _ast.Not)):
                self._fail(f"unsupported unary operator {type(node.op).__name__}")
            return [
                _Candidate(
                    "int",
                    "",
                    _UnaryExpr(
                        type(node.op), self._bind_expr(node.operand, depth + 1)
                    ),
                )
            ]
        if isinstance(node, _ast.BinOp):
            if isinstance(node.op, _ast.Div):
                self._fail("operator '/' is not supported; use '//' for integer division")
            if type(node.op) not in _SIMPLE_BIN_OPS and not isinstance(
                node.op, (_ast.Mult, _ast.Pow, _ast.LShift)
            ):
                self._fail(f"unsupported binary operator {type(node.op).__name__}")
            return [
                _Candidate(
                    "int",
                    "",
                    _BinExpr(
                        type(node.op),
                        self._bind_expr(node.left, depth + 1),
                        self._bind_expr(node.right, depth + 1),
                    ),
                )
            ]
        if isinstance(node, _ast.BoolOp):
            return [
                _Candidate(
                    "int",
                    "",
                    _BoolExpr(
                        isinstance(node.op, _ast.And),
                        tuple(self._bind_expr(v, depth + 1) for v in node.values),
                    ),
                )
            ]
        if isinstance(node, _ast.IfExp):
            return [
                _Candidate(
                    "int",
                    "",
                    _IfExpr(
                        self._bind_expr(node.test, depth + 1),
                        self._bind_expr(node.body, depth + 1),
                        self._bind_expr(node.orelse, depth + 1),
                    ),
                )
            ]
        if isinstance(node, _ast.Compare):
            left = self._bind_expr(node.left, depth + 1)
            steps: list[_CompareStep] = []
            last_idx = len(node.ops) - 1
            for idx, (op, comp) in enumerate(zip(node.ops, node.comparators)):
                if isinstance(op, (_ast.In, _ast.NotIn)):
                    if idx != last_idx:
                        self._fail(
                            "'in'/'not in' may only appear as the final comparison"
                        )
                    if not isinstance(comp, (_ast.Tuple, _ast.Set)):
                        self._fail(
                            "right-hand side of 'in'/'not in' must be a tuple or set literal"
                        )
                    self._check_depth(depth + 1)
                    elts = tuple(self._bind_expr(elt, depth + 2) for elt in comp.elts)
                    steps.append(_CompareStep(type(op), elts))
                elif type(op) in _COMPARE_OPS:
                    steps.append(_CompareStep(type(op), self._bind_expr(comp, depth + 1)))
                else:
                    self._fail(f"unsupported comparison operator {type(op).__name__}")
            return [_Candidate("int", "", _CompareExpr(left, tuple(steps)))]
        if isinstance(node, _ast.Call):
            callee = self._bind_callee(node.func, depth + 1)
            for arg in node.args:
                if isinstance(arg, _ast.Starred):
                    self._fail("starred call arguments are not supported")
            seen_kw: set[str] = set()
            for kw in node.keywords:
                if kw.arg is None:
                    self._fail("'**kwargs' is not supported")
                if kw.arg in seen_kw:
                    self._fail(f"keyword argument repeated: {kw.arg!r}")
                seen_kw.add(kw.arg)
            args = tuple(self._bind_expr(arg, depth + 1) for arg in node.args)
            keywords = tuple(
                (kw.arg, self._bind_expr(kw.value, depth + 1)) for kw in node.keywords
            )
            return [_Candidate("int", "", _CallExpr(callee, args, keywords))]
        if isinstance(node, (_ast.JoinedStr, _ast.FormattedValue)):
            self._fail("f-strings are not supported in conditions")
        if isinstance(node, _ast.Lambda):
            self._fail("lambda expressions are not supported in conditions")
        if isinstance(node, _ast.NamedExpr):
            self._fail("assignment expressions (':=') are not supported in conditions")
        if isinstance(
            node, (_ast.ListComp, _ast.SetComp, _ast.GeneratorExp, _ast.DictComp)
        ):
            self._fail("comprehensions are not supported in conditions")
        if isinstance(node, (_ast.Tuple, _ast.Set)):
            self._fail("tuple/set literals are only allowed as the right side of 'in'/'not in'")
        self._fail(f"unsupported syntax {type(node).__name__}")


def _eval_node(sim, node: object) -> int:
    if isinstance(node, _ConstExpr):
        return node.value
    if isinstance(node, _BuiltinExpr):
        return sim.state.pc if node.name == "pc" else sim._cycle
    if isinstance(node, _TargetExpr):
        if node.namespace == "r":
            return _check_int_bits(sim._reg_read(node.name), context=node.name)
        if node.namespace == "m":
            info = sim._mem_aliases.get(node.name)
            if info is None:
                raise DebugError(f"target={node.name!r}: unknown memory alias")
            _check_bit_width(info.bits, minimum=1, label=node.name)
            return _check_int_bits(sim._mem_alias_read(info), context=node.name)
        if node.name not in sim._irq:
            raise DebugError(f"irq={node.name!r}: unknown line")
        return sim._irq[node.name]
    if isinstance(node, _SpaceReadExpr):
        addr = _eval_node(sim, node.addr)
        return _check_int_bits(
            sim._mem_read(node.space, addr, peek=True), context=node.space
        )
    if isinstance(node, _UnaryExpr):
        val = _eval_node(sim, node.operand)
        if node.op is _ast.Not:
            return not val
        if node.op is _ast.UAdd:
            return +val
        if node.op is _ast.USub:
            return -val
        return _check_int_bits(~val, context="bitwise inversion")
    if isinstance(node, _BinExpr):
        left = _eval_node(sim, node.left)
        right = _eval_node(sim, node.right)
        if node.op is _ast.Mult:
            return _eval_mult(left, right)
        if node.op is _ast.Pow:
            return _eval_pow(left, right)
        if node.op is _ast.LShift:
            return _eval_lshift(left, right)
        return _check_int_bits(_SIMPLE_BIN_OPS[node.op](left, right))
    if isinstance(node, _BoolExpr):
        result: int = True if node.is_and else False
        for item in node.values:
            result = _eval_node(sim, item)
            if node.is_and and not result:
                return result
            if not node.is_and and result:
                return result
        return result
    if isinstance(node, _IfExpr):
        branch = node.body if _eval_node(sim, node.test) else node.orelse
        return _eval_node(sim, branch)
    if isinstance(node, _CompareExpr):
        left = _eval_node(sim, node.left)
        for step in node.steps:
            if step.op in (_ast.In, _ast.NotIn):
                members = tuple(_eval_node(sim, elt) for elt in step.comparator)
                matched = left in members
                ok = matched if step.op is _ast.In else not matched
                if not ok:
                    return False
            else:
                right = _eval_node(sim, step.comparator)
                if not _COMPARE_OPS[step.op](left, right):
                    return False
                left = right
        return True
    if isinstance(node, _CallExpr):
        callee = node.callee
        while isinstance(callee, _IfCallee):
            callee = callee.body if _eval_node(sim, callee.test) else callee.orelse
        if isinstance(callee, _MethodCallee):
            receiver = _eval_node(sim, callee.receiver)
            args = [_eval_node(sim, arg) for arg in node.args]
            keywords = {k: _eval_node(sim, v) for k, v in node.keywords}
            if args or keywords:
                raise DebugError(f"{callee.method}() takes no arguments")
            return getattr(int, callee.method)(receiver)
        assert isinstance(callee, _HelperCallee)
        args = [_eval_node(sim, arg) for arg in node.args]
        keywords = {k: _eval_node(sim, v) for k, v in node.keywords}
        params, fn = _HELPER_SPECS[callee.name]
        ordered = _bind_helper_args(callee.name, params, args, keywords)
        return fn(*ordered)
    raise DebugError(f"unexpected condition node {type(node).__name__}")


class _Debug:
    def _debug_init(self):
        self._debug_points = []
        self._next_debug = 1
        self._displays = {}
        self._next_display = 1
        self._observations = []

    def _resolve_namespace_target(
        self, namespace: str, name: object
    ) -> ConditionTarget | None:
        """Resolve a target inside r/m/i using core MCU and simulator rules."""
        if not isinstance(name, str):
            return None
        if namespace == "r":
            try:
                parts, meta = self._reg_parts(name)
            except StateAccessError:
                return None
            if len(parts) == 2:
                return ConditionTarget("r", name, "scalar")
            return ConditionTarget("r", name, "reg", frozenset(meta["fields"]))
        if namespace == "m":
            if name in self._mcu._spaces:
                return ConditionTarget("m", name, "space")
            if name in self._mem_aliases:
                return ConditionTarget("m", name, "scalar")
            return None
        if namespace == "i":
            if name in self._irq:
                return ConditionTarget("i", name, "scalar")
            return None
        return None

    def _resolve_mcu_target(self, name: object) -> ConditionTarget | None:
        """Resolve an unqualified MCU/simulator target across r, m, and i."""
        if not isinstance(name, str) or "." in name:
            return None
        for namespace in ("r", "m", "i"):
            target = self._resolve_namespace_target(namespace, name)
            if target is not None:
                return target
        return None

    def _compile_condition(
        self, expr: str, *, allow_shorthand: bool = False
    ) -> _ConditionPlan:
        return _ConditionBinder(
            self, expr, allow_shorthand=allow_shorthand
        ).compile()

    def _evaluate_plan(self, plan: _ConditionPlan) -> int:
        try:
            result = _eval_node(self, plan.root)
        except DebugError as error:
            if str(error).startswith(f"expr={plan.source!r}:"):
                raise
            raise DebugError(f"expr={plan.source!r}: {error}") from error
        except (
            TypeError,
            ValueError,
            ZeroDivisionError,
            OverflowError,
            RecursionError,
            IsaSimError,
        ) as error:
            raise DebugError(f"expr={plan.source!r}: {error}") from error
        if not isinstance(result, int):
            raise DebugError(
                f"expr={plan.source!r}: result={result!r}, expected int/bool"
            )
        return result

    def evaluate(self, expr: str) -> int:
        plan = self._compile_condition(expr, allow_shorthand=False)
        return self._evaluate_plan(plan)

    def _point(self, **entry):
        entry["id"] = self._next_debug
        self._next_debug += 1
        self._debug_points.append(entry)
        return entry["id"]

    def _add_breakpoint_plan(
        self,
        where: int | str,
        *,
        condition: str | None = None,
        plan: _ConditionPlan | None = None,
        temporary: bool = False,
    ) -> int:
        addresses = None
        if isinstance(where, str) and self._symbol_address(where) is None:
            addresses = self._source_addresses(where)
        if addresses is None:
            addresses = (self._where(where),)
        return self._point(
            kind="break",
            addresses=addresses,
            condition=condition,
            plan=plan,
            temporary=temporary,
        )

    def add_breakpoint(
        self, where: int | str, *, condition: str | None = None, temporary: bool = False
    ) -> int:
        plan = (
            None
            if condition is None
            else self._compile_condition(condition, allow_shorthand=False)
        )
        return self._add_breakpoint_plan(
            where, condition=condition, plan=plan, temporary=temporary
        )

    def _watch_target(self, target):
        match = _re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_]*)\[(0x[0-9a-fA-F]+|[0-9]+):(0x[0-9a-fA-F]+|[0-9]+)\]",
            target,
        )
        if not match:
            kind, name, addr = self._target(target)
            if kind == "reg":
                parts, meta = self._reg_parts(name)
                high, low = (
                    meta["fields"][parts[1]]
                    if len(parts) == 2
                    else (meta["bits"] - 1, 0)
                )
                return kind, parts[0], (low, high, len(parts) == 2)
            if kind == "irq":
                name = f"irq.{name}"
            return kind, name, addr
        space, lo, hi = match.groups()
        lo = int(lo, 16 if lo.startswith("0x") else 10)
        hi = int(hi, 16 if hi.startswith("0x") else 10)
        if (
            space not in self._mcu._spaces
            or not 0 <= lo < hi <= self._mcu._spaces[space]["size"]
        ):
            raise TargetError(f"target={target!r}: invalid range")
        return "mem", space, (lo, hi)

    def _add_watchpoint_plan(
        self,
        target: str,
        *,
        kind: str,
        condition: str | None = None,
        plan: _ConditionPlan | None = None,
    ) -> int:
        if kind not in ("r", "w", "rw"):
            raise DebugError(f"kind={kind!r}: expected r/w/rw")
        mem_alias_name = None
        resolved = None
        if isinstance(target, str) and target in self._mem_aliases:
            self._validate_mem_alias_target(target, error_cls=DebugError)
            mem_alias_name = target
        else:
            resolved = self._watch_target(target)
        return self._point(
            kind="watch",
            target=target,
            resolved=resolved,
            access=kind,
            condition=condition,
            plan=plan,
            mem_alias_name=mem_alias_name,
        )

    def add_watchpoint(
        self, target: str, *, kind: str, condition: str | None = None
    ) -> int:
        plan = (
            None
            if condition is None
            else self._compile_condition(condition, allow_shorthand=False)
        )
        return self._add_watchpoint_plan(
            target, kind=kind, condition=condition, plan=plan
        )

    def delete(self, which: int | str) -> None:
        if which == "all":
            self._debug_points.clear()
            return
        if not _is_int(which) or not any(
            point["id"] == which for point in self._debug_points
        ):
            raise DebugError(f"id={which!r}: unknown breakpoint/watchpoint")
        self._debug_points[:] = [
            point for point in self._debug_points if point["id"] != which
        ]

    def list_breakpoints(self) -> list[str]:
        result = []
        for point in self._debug_points:
            if point["kind"] == "watch":
                line = f"{point['id']}: watch {point['target']} {point['access']}"
            else:
                targets = ", ".join(map(str, point["addresses"]))
                suffix = " temporary" if point["temporary"] else ""
                line = f"{point['id']}: break {targets}{suffix}"
            if point["condition"]:
                line += f" if {point['condition']}"
            result.append(line)
        return result

    def add_display(self, expr: str) -> int:
        self._compile_condition(expr, allow_shorthand=False)
        number = self._next_display
        self._next_display += 1
        self._displays[number] = expr
        return number

    def remove_display(self, display_id: int) -> None:
        if not _is_int(display_id) or display_id not in self._displays:
            raise DebugError(f"display_id={display_id!r}: unknown display")
        del self._displays[display_id]

    def next_breakpoint(self) -> int | None:
        """Return the first breakpoint matching the current state at S0."""
        point = self._matching_breakpoint()
        return None if point is None else point["id"]

    def _matching_breakpoint(self):
        for point in self._debug_points:
            if (
                point["kind"] == "break"
                and self.state.pc in point["addresses"]
                and (point["plan"] is None or self._evaluate_plan(point["plan"]))
            ):
                return point
        return None

    def _breakpoint(self):
        point = self._matching_breakpoint()
        if point is None:
            return None
        if point["temporary"]:
            self._debug_points.remove(point)
        return {"id": point["id"], "pc": self.state.pc}

    def _observe(self, kind, name, addr, access, old, new):
        if self._phase == "B":
            self._observations.append((kind, name, addr, access, old, new))

    def _mem_alias_step_cells(self, info):
        first_write = {}
        for k, n, addr, access, old, _ in self._observations:
            if (
                k == "mem"
                and n == info.space
                and info.lo <= addr < info.hi
                and access == "w"
                and addr not in first_write
            ):
                first_write[addr] = old
        mem = self._memory[info.space]
        before = [first_write.get(addr, mem[addr]) for addr in range(info.lo, info.hi)]
        after = list(mem[info.lo : info.hi])
        return before, after

    def _mem_alias_watchpoint(self, point, info, pc):
        if not any(
            k == "mem"
            and n == info.space
            and info.lo <= a < info.hi
            and access in point["access"]
            for k, n, a, access, _, _ in self._observations
        ):
            return None
        is_multi = info.hi - info.lo > 1
        if is_multi:
            before_cells, after_cells = self._mem_alias_step_cells(info)
            running = list(before_cells)
            alias_old_w = self._pack_mem_alias_cells(info, before_cells)
            alias_new_w = self._pack_mem_alias_cells(info, after_cells)
        for k, n, a, access, old, new in self._observations:
            if k != "mem" or n != info.space or not info.lo <= a < info.hi:
                continue
            if not is_multi:
                if access not in point["access"]:
                    continue
                alias_old = (
                    None if old is None else self._pack_mem_alias_cells(info, (old,))
                )
                alias_new = (
                    None if new is None else self._pack_mem_alias_cells(info, (new,))
                )
                if access == "w" and alias_old is not None and alias_old == alias_new:
                    continue
            elif access == "w":
                running[a - info.lo] = new
                if access not in point["access"]:
                    continue
                if alias_old_w == alias_new_w:
                    continue
                alias_old = alias_old_w
                alias_new = alias_new_w
            else:
                if access not in point["access"]:
                    continue
                snap = list(running)
                snap[a - info.lo] = new
                alias_old = None
                alias_new = self._pack_mem_alias_cells(info, snap)
            if point["plan"] is None or self._evaluate_plan(point["plan"]):
                return {
                    "id": point["id"],
                    "target": point["target"],
                    "access": access,
                    "pc": pc,
                    "old": alias_old,
                    "new": alias_new,
                }
            break
        return None

    def _watchpoint(self, pc):
        for point in self._debug_points:
            if point["kind"] != "watch":
                continue
            alias_name = point.get("mem_alias_name")
            if alias_name is not None:
                info = self._mem_aliases.get(alias_name)
                if info is None:
                    raise DebugError(f"target={alias_name!r}: unknown memory alias")
                hit = self._mem_alias_watchpoint(point, info, pc)
                if hit is not None:
                    return hit
                continue
            kind, name, addr = point["resolved"]
            for k, n, a, access, old, new in self._observations:
                if (kind, name) != (k, n) or access not in point["access"]:
                    continue
                if kind == "reg":
                    low, high, is_field = addr
                    if high < a[0] or a[1] < low:
                        continue
                    if is_field:
                        mask = (1 << (high - low + 1)) - 1
                        old = None if old is None else (old >> low) & mask
                        new = (new >> low) & mask
                elif isinstance(addr, tuple):
                    if not addr[0] <= a < addr[1]:
                        continue
                elif a != addr:
                    continue
                if point["plan"] is None or self._evaluate_plan(point["plan"]):
                    return {
                        "id": point["id"],
                        "target": point["target"],
                        "access": access,
                        "pc": pc,
                        "old": old,
                        "new": new,
                    }
                break
        return None
