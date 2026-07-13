"""Tree matcher for expression rewrite patterns."""

from __future__ import annotations

from typing import Dict, Mapping, Optional

from .ir import BinOp, Cmp, Const, Expr, Var


CaptureMap = Dict[str, Expr]


def match_expr(pattern: Mapping[str, object], expr: Expr) -> Optional[CaptureMap]:
    captures: CaptureMap = {}
    if _match_into(pattern, expr, captures):
        return captures
    return None


def _match_into(pattern: Mapping[str, object], expr: Expr, captures: CaptureMap) -> bool:
    if "capture" in pattern:
        name = str(pattern["capture"])
        prior = captures.get(name)
        if prior is None:
            captures[name] = expr
            return True
        return prior == expr

    if "ref" in pattern:
        return captures.get(str(pattern["ref"])) == expr

    if "const" in pattern:
        return isinstance(expr, Const) and expr.value == int(pattern["const"])

    if "var" in pattern:
        return isinstance(expr, Var) and expr.name == str(pattern["var"])

    op = pattern.get("op")
    args = pattern.get("args", [])
    if not isinstance(args, list):
        raise TypeError("pattern args must be a list")

    if isinstance(expr, BinOp):
        if op != expr.op or len(args) != 2:
            return False
        return _match_into(args[0], expr.left, captures) and _match_into(
            args[1], expr.right, captures
        )

    if isinstance(expr, Cmp):
        cmp_op = f"cmp_{expr.op}"
        if op not in (expr.op, cmp_op) or len(args) != 2:
            return False
        return _match_into(args[0], expr.left, captures) and _match_into(
            args[1], expr.right, captures
        )

    return False


def build_expr(spec: Mapping[str, object], captures: Mapping[str, Expr]) -> Expr:
    if "ref" in spec:
        name = str(spec["ref"])
        if name not in captures:
            raise ValueError(f"unknown capture reference: {name}")
        return captures[name]

    if "const" in spec:
        return Const(int(spec["const"]))

    if "var" in spec:
        return Var(str(spec["var"]))

    op = spec.get("op")
    args = spec.get("args", [])
    if not isinstance(op, str):
        raise ValueError(f"replacement needs op/ref/const/var: {spec!r}")
    if not isinstance(args, list) or len(args) != 2:
        raise ValueError(f"replacement op requires two args: {spec!r}")

    left = build_expr(args[0], captures)
    right = build_expr(args[1], captures)
    if op.startswith("cmp_"):
        return Cmp(op=op[4:], left=left, right=right)
    return BinOp(op=op, left=left, right=right)
