"""Solver-free control loop for bounded CEGIS searches."""

from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Optional, Tuple, TypeVar


CandidateT = TypeVar("CandidateT")
ExampleT = TypeVar("ExampleT")
BudgetT = TypeVar("BudgetT")


@dataclass(frozen=True)
class CegisResult(Generic[CandidateT, ExampleT, BudgetT]):
    candidate: CandidateT
    budget: BudgetT
    counterexamples: Tuple[ExampleT, ...]


def minimize_cegis(
    budgets: Iterable[BudgetT],
    propose: Callable[[BudgetT, Tuple[ExampleT, ...]], Optional[CandidateT]],
    counterexample: Callable[[CandidateT], Optional[ExampleT]],
) -> Optional[CegisResult[CandidateT, ExampleT, BudgetT]]:
    """Return the first verified candidate from caller-ordered cost budgets.

    ``propose`` owns the solver/search implementation.  It returns ``None``
    only when the current budget is proved infeasible; unknown/incomplete
    solver results must raise.  ``counterexample`` returns ``None`` only after
    independently verifying the candidate.
    """

    examples = []
    for budget in budgets:
        while (candidate := propose(budget, tuple(examples))) is not None:
            failed = counterexample(candidate)
            if failed is None:
                return CegisResult(candidate, budget, tuple(examples))
            if failed in examples:
                raise RuntimeError(
                    "proposer returned a candidate rejected by an existing "
                    "CEGIS counterexample"
                )
            examples.append(failed)
    return None
