from rpkbin.codegen import lir
from rpkbin.codegen.rewrite import rewrite_function


def test_rewrite_preserves_signed_comparison_semantics():
    comparison = lir.Cmp(
        "lt", lir.Var("x", 8), lir.Const(0, 8), signed=True
    )
    function = lir.Function(
        "signed_cmp",
        (lir.Var("x", 8),),
        (
            lir.Block("entry", (), lir.BrIf(comparison, "negative", "done")),
            lir.Block("negative", (), lir.Jump("done")),
            lir.Block("done", (), lir.Return(lir.Var("x", 8))),
        ),
    )

    rewritten = rewrite_function(function, ()).function

    assert rewritten.blocks[0].terminator.cond.signed is True
