import pytest

from rpkbin.codegen.cegis import minimize_cegis


def test_minimize_cegis_reuses_counterexamples_across_budgets():
    domain = range(3)

    def propose(budget, examples):
        return next(
            (
                delta
                for delta in domain
                if delta <= budget
                and all((value + delta) % 3 == (value + 1) % 3 for value in examples)
            ),
            None,
        )

    def counterexample(delta):
        return next(
            (value for value in domain if (value + delta) % 3 != (value + 1) % 3),
            None,
        )

    result = minimize_cegis(range(3), propose, counterexample)

    assert result is not None
    assert (result.candidate, result.budget, result.counterexamples) == (1, 1, (0,))


def test_minimize_cegis_rejects_a_proposer_that_ignores_examples():
    with pytest.raises(RuntimeError, match="existing CEGIS counterexample"):
        minimize_cegis(
            (0,), lambda _budget, _examples: "bad", lambda _candidate: 0
        )
