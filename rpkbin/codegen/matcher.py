"""Tree matcher for expression rewrite patterns."""

from __future__ import annotations

from typing import Mapping

from .lir import BitOp, BinOp, Cmp, Const, Extend, FullExpr, MemLoad, SymbolAddr, VReg, Var


CaptureMap = dict[str, FullExpr]


def match_expr(pattern: Mapping[str, object], expr: FullExpr) -> CaptureMap | None:
    captures: CaptureMap = {}
    if _match_into(pattern, expr, captures):
        return captures
    return None


def _match_into(pattern: Mapping[str, object], expr: FullExpr, captures: CaptureMap) -> bool:
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


def build_expr(
    spec: Mapping[str, object],
    captures: Mapping[str, FullExpr],
    template: FullExpr | None = None,
    *,
    result_width: int | None = None,
    operand_width: int | None = None,
) -> FullExpr:
    """Build a replacement expression, preserving matched expression metadata.

    Pattern dictionaries deliberately omit machine types. The caller supplies
    a matched ``template`` (or explicit contexts), from which result and
    operand widths are inferred independently of replacement tree shape. The
    root replacement must retain the template result width; child operands use
    their own inferred contexts. Captured references retain their metadata.
    """
    rebuilt = _build_expr(spec, captures, template, result_width=result_width, operand_width=operand_width)
    expected_width = expression_result_width(template)
    actual_width = expression_result_width(rebuilt)
    if expected_width is not None and actual_width is not None and expected_width != actual_width:
        raise ValueError(
            "replacement result width mismatch: "
            f"expected={expected_width}, actual={actual_width}, spec={spec!r}"
        )
    return rebuilt


def _build_expr(
    spec: Mapping[str, object], captures: Mapping[str, FullExpr], template: FullExpr | None,
    *, result_width: int | None, operand_width: int | None,
) -> FullExpr:
    if "ref" in spec:
        name = str(spec["ref"])
        if name not in captures:
            raise ValueError(f"unknown capture reference: {name}")
        return captures[name]
    result_width = result_width if result_width is not None else _result_width(template)
    operand_width = (
        operand_width
        if operand_width is not None
        else _operand_width(template, captures, result_width)
    )
    if "const" in spec:
        return Const(int(spec["const"]), result_width)

    if "var" in spec:
        return Var(str(spec["var"]), result_width)

    op = spec.get("op")
    args = spec.get("args", [])
    if not isinstance(op, str):
        raise ValueError(f"replacement needs op/ref/const/var: {spec!r}")
    if not isinstance(args, list) or len(args) != 2:
        raise ValueError(f"replacement op requires two args: {spec!r}")

    if op.startswith("cmp_"):
        if not isinstance(template, Cmp):
            raise ValueError(
                "cannot infer comparison signedness without a comparison template"
            )
        left = _build_expr(args[0], captures, None, result_width=operand_width, operand_width=operand_width)
        right = _build_expr(args[1], captures, None, result_width=operand_width, operand_width=operand_width)
        return Cmp(
            op=op[4:],
            left=left,
            right=right,
            width=1,
            signed=template.signed,
        )
    if isinstance(template, Cmp):
        raise ValueError("cannot rebuild a comparison as an integer expression")
    left = _build_expr(args[0], captures, None, result_width=operand_width, operand_width=operand_width)
    right = _build_expr(args[1], captures, None, result_width=operand_width, operand_width=operand_width)
    return BinOp(
        op=op,
        left=left,
        right=right,
        width=result_width,
    )


def _result_width(template: FullExpr | None) -> int:
    width = expression_result_width(template)
    if width is not None:
        return width
    raise ValueError("cannot infer rewrite result width without a typed template")


def expression_result_width(expr: FullExpr | None) -> int | None:
    """Return a known LIR expression result width, including 1-bit booleans."""
    if isinstance(expr, Cmp):
        return 1
    if isinstance(expr, BitOp):
        return 1 if expr.kind == "test" else None
    if isinstance(expr, (Const, Var, VReg, BinOp, Extend, MemLoad, SymbolAddr)):
        return expr.width
    return None


def _operand_width(
    template: FullExpr | None,
    captures: Mapping[str, FullExpr],
    result_width: int,
) -> int:
    if isinstance(template, (BinOp, Cmp)):
        left_width = getattr(template.left, "width", None)
        right_width = getattr(template.right, "width", None)
        if left_width == right_width and isinstance(left_width, int):
            return left_width
    if isinstance(template, BitOp) and template.kind == "test":
        width = expression_result_width(template.var)
        if width is not None:
            return width
    captured_widths = {getattr(expr, "width", None) for expr in captures.values()}
    captured_widths.discard(None)
    if len(captured_widths) == 1:
        return captured_widths.pop()
    return result_width
