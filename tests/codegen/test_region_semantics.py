import pytest

from rpkbin.codegen.lir import (
    Assign,
    BinOp,
    Block,
    BrIf,
    Call,
    CallStmt,
    Cmp,
    Const,
    Function,
    Jump,
    MultiReturn,
    Var,
)
from rpkbin.codegen.region_semantics import (
    RegionError,
    discover_bounded_regions,
    execute_region,
)


def _diamond(extra_statement=None):
    then_statements = [Assign(Var("out", 8), Const(1, 8))]
    if extra_statement is not None:
        then_statements.append(extra_statement)
    return Function(
        "f",
        (Var("x", 8),),
        (
            Block(
                "entry",
                (Assign(Var("out", 8), Const(0, 8)),),
                BrIf(Cmp("eq", Var("x", 8), Const(0, 8)), "then", "else"),
            ),
            Block("then", tuple(then_statements), Jump("merge")),
            Block(
                "else",
                (Assign(Var("out", 8), Var("x", 8)),),
                Jump("merge"),
            ),
            Block("merge", (), MultiReturn((Var("out", 8), Var("x", 8)))),
        ),
    )


def test_discovers_and_executes_pure_diamond_without_source_pattern():
    regions = discover_bounded_regions(_diamond(), max_blocks=3)
    region = next(
        region
        for region in regions
        if region.entry_label == "entry" and region.exit_label == "merge"
    )

    assert region.observed == ("out", "x")
    assert execute_region(region, {"x": 0, "out": 99}) == (1, 0)
    assert execute_region(region, {"x": 5, "out": 99}) == (5, 5)
    with pytest.raises(RegionError, match="missing initial value"):
        execute_region(region, {"x": 0})


def test_effectful_region_fails_closed():
    call = CallStmt(Call("side_effect", (), clobbers=()))
    regions = discover_bounded_regions(_diamond(call), max_blocks=3)
    assert not any(
        region.entry_label == "entry" and region.exit_label == "merge"
        for region in regions
    )


def test_executes_target_neutral_bitvector_expression():
    function = Function(
        "add",
        (Var("x", 8),),
        (
            Block(
                "entry",
                (Assign(Var("out", 8), BinOp("add", Var("x", 8), Const(3, 8))),),
                Jump("exit"),
            ),
            Block("exit", (), MultiReturn((Var("out", 8),))),
        ),
    )
    region = next(
        region
        for region in discover_bounded_regions(function, max_blocks=1)
        if region.entry_label == "entry"
    )
    assert execute_region(region, {"x": 0xFE, "out": 0}) == (1,)
