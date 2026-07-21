"""Focused regressions for generic rewrite safety and metadata preservation."""

import pytest

from rpkbin.codegen import lir
from rpkbin.codegen.matcher import build_expr
from rpkbin.codegen.patterns import load_patterns_from_dicts
from rpkbin.codegen.rewrite import RewriteConvergenceError, rewrite_function


def _function_with_value(value: lir.FullExpr) -> lir.Function:
    return lir.Function(
        "rewrite_case",
        (),
        (
            lir.Block(
                "entry",
                (lir.Assign(lir.Var("result", getattr(value, "width", 8)), value),),
                lir.Return(lir.Var("result", getattr(value, "width", 8))),
            ),
        ),
    )


def test_wildcard_rewrite_cannot_erase_effectful_call():
    call_value = lir.BinOp("add", lir.Call("observe", ()), lir.Const(1), 8)
    function = _function_with_value(call_value)
    patterns = load_patterns_from_dicts(
        [
            {
                "name": "replace_any_expression",
                "match": {"capture": "value"},
                "replace": {"const": 0},
            }
        ]
    )

    result = rewrite_function(function, patterns)

    assert result.function.blocks[0].statements[0].value == call_value


def test_rebuild_preserves_16_bit_signed_comparison_metadata():
    comparison = lir.Cmp("lt", lir.Var("x", 16), lir.Const(7, 16), 1, signed=True)
    patterns = load_patterns_from_dicts(
        [
            {
                "name": "signed_compare_rewrite",
                "match": {"op": "cmp_lt", "args": [{"capture": "x"}, {"const": 7}]},
                "replace": {"op": "cmp_eq", "args": [{"ref": "x"}, {"const": 0}]},
            }
        ]
    )

    result = rewrite_function(_function_with_value(comparison), patterns)
    rewritten = result.function.blocks[0].statements[0].value

    assert rewritten == lir.Cmp("eq", lir.Var("x", 16), lir.Const(0, 16), 1, signed=True)


def test_rebuild_preserves_16_bit_integer_result_and_operands():
    expression = lir.BinOp("mul", lir.Var("x", 16), lir.Const(2, 16), 16)
    patterns = load_patterns_from_dicts(
        [
            {
                "name": "mul_to_shift",
                "match": {"op": "mul", "args": [{"capture": "x"}, {"const": 2}]},
                "replace": {"op": "shl", "args": [{"ref": "x"}, {"const": 1}]},
            }
        ]
    )

    rewritten = rewrite_function(_function_with_value(expression), patterns).function.blocks[0].statements[0].value

    assert rewritten == lir.BinOp("shl", lir.Var("x", 16), lir.Const(1, 16), 16)


def test_shape_changing_rebuild_uses_leaf_result_width_context():
    rebuilt = build_expr(
        {"op": "add", "args": [{"ref": "x"}, {"const": 0}]},
        {"x": lir.Var("x", 16)},
        template=lir.Var("original", 16),
    )

    assert rebuilt == lir.BinOp("add", lir.Var("x", 16), lir.Const(0, 16), 16)


def test_nested_new_comparison_without_signed_template_fails_closed():
    expression = lir.BinOp("add", lir.Var("x", 16), lir.Const(1, 16), 16)
    patterns = load_patterns_from_dicts(
        [
            {
                "name": "introduce_comparison",
                "match": {"op": "add", "args": [{"capture": "x"}, {"const": 1}]},
                "replace": {
                    "op": "add",
                    "args": [
                        {"op": "cmp_lt", "args": [{"ref": "x"}, {"const": 0}]},
                        {"const": 0},
                    ],
                },
            }
        ]
    )

    with pytest.raises(ValueError, match="comparison signedness"):
        rewrite_function(_function_with_value(expression), patterns)


def test_cyclic_rewrites_fail_with_convergence_error():
    patterns = load_patterns_from_dicts(
        [
            {
                "name": "one_to_two",
                "match": {"const": 1},
                "replace": {"const": 2},
            },
            {
                "name": "two_to_one",
                "match": {"const": 2},
                "replace": {"const": 1},
            },
        ]
    )

    with pytest.raises(RewriteConvergenceError, match="rewrite did not converge.*one_to_two.*two_to_one"):
        rewrite_function(_function_with_value(lir.Const(1)), patterns)


def test_non_repeating_growth_hits_step_budget_with_diagnostics():
    patterns = load_patterns_from_dicts(
        [
            {
                "name": "grow_one",
                "match": {"const": 1},
                "replace": {"op": "add", "args": [{"const": 1}, {"const": 0}]},
            }
        ]
    )

    with pytest.raises(
        RewriteConvergenceError,
        match=r"step budget exceeded.*steps=4.*last_rule=grow_one.*expression_nodes=.*seen_states=",
    ):
        rewrite_function(_function_with_value(lir.Const(1)), patterns, max_steps=4)


def test_exponential_growth_hits_node_budget_before_step_budget():
    patterns = load_patterns_from_dicts(
        [
            {
                "name": "duplicate_one",
                "match": {"const": 1},
                "replace": {"op": "add", "args": [{"const": 1}, {"const": 1}]},
            }
        ]
    )

    with pytest.raises(
        RewriteConvergenceError,
        match=r"node budget exceeded.*steps=.*last_rule=duplicate_one.*expression_nodes=.*max_nodes=30",
    ):
        rewrite_function(_function_with_value(lir.Const(1)), patterns, max_steps=20, max_nodes=30)


def test_cmp_to_const_uses_boolean_result_width():
    comparison = lir.Cmp("lt", lir.Var("x", 16), lir.Const(7, 16), 1, signed=True)
    patterns = load_patterns_from_dicts(
        [{"name": "compare_to_false", "match": {"capture": "value"}, "replace": {"const": 0}}]
    )

    rewritten = rewrite_function(_function_with_value(comparison), patterns).function.blocks[0].statements[0].value

    assert rewritten == lir.Const(0, 1)


def test_cmp_to_captured_ref_rejects_result_width_mismatch():
    comparison = lir.Cmp("lt", lir.Var("x", 16), lir.Const(7, 16), 1, signed=True)
    patterns = load_patterns_from_dicts(
        [
            {
                "name": "compare_to_value",
                "match": {"op": "cmp_lt", "args": [{"capture": "x"}, {"const": 7}]},
                "replace": {"ref": "x"},
            }
        ]
    )

    with pytest.raises(ValueError, match=r"expected=1, actual=16"):
        rewrite_function(_function_with_value(comparison), patterns)


def test_bit_test_rewrite_uses_boolean_result_width():
    bit_test = lir.BitOp("test", lir.Var("port", 8), 3)
    patterns = load_patterns_from_dicts(
        [{"name": "test_to_true", "match": {"capture": "value"}, "replace": {"const": 1}}]
    )

    rewritten = rewrite_function(_function_with_value(bit_test), patterns).function.blocks[0].statements[0].value

    assert rewritten == lir.Const(1, 1)


def test_direct_pure_ref_build_preserves_captured_metadata():
    captured = lir.Var("x", 16)

    assert build_expr({"ref": "x"}, {"x": captured}) is captured


def test_max_steps_allows_exactly_one_transition_before_stability():
    patterns = load_patterns_from_dicts(
        [{"name": "one_to_two", "match": {"const": 1}, "replace": {"const": 2}}]
    )

    rewritten = rewrite_function(_function_with_value(lir.Const(1)), patterns, max_steps=1)

    assert rewritten.function.blocks[0].statements[0].value == lir.Const(2, 8)


@pytest.mark.parametrize("keyword", ["max_steps", "max_nodes"])
@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "4"])
def test_rewrite_budget_validation_rejects_non_positive_or_non_integer(keyword, invalid):
    with pytest.raises((TypeError, ValueError), match="positive integer"):
        rewrite_function(_function_with_value(lir.Const(1)), (), **{keyword: invalid})


def test_cycle_error_trace_is_local_to_the_failing_expression():
    patterns = load_patterns_from_dicts(
        [
            {"name": "zero_to_nine", "match": {"const": 0}, "replace": {"const": 9}},
            {"name": "one_to_two", "match": {"const": 1}, "replace": {"const": 2}},
            {"name": "two_to_one", "match": {"const": 2}, "replace": {"const": 1}},
        ]
    )
    function = lir.Function(
        "trace_isolation",
        (),
        (
            lir.Block(
                "entry",
                (
                    lir.Assign(lir.Var("first"), lir.Const(0)),
                    lir.Assign(lir.Var("second"), lir.Const(1)),
                ),
                lir.Return(lir.Var("first")),
            ),
        ),
    )

    with pytest.raises(RewriteConvergenceError) as exc_info:
        rewrite_function(function, patterns)

    message = str(exc_info.value)
    assert "one_to_two -> two_to_one" in message
    assert "zero_to_nine" not in message
