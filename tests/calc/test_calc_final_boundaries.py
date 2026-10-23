"""Cross-feature regressions for exact copy data and read-only previews."""

import numpy as np
import pytest

from rpkbin.calc.core import CalcConfig, WorkspaceSession, make_fxp
from rpkbin.calc.core.editing import conversion_preview


@pytest.fixture
def session():
    return WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)


def test_workspace_fxp_copy_source_never_round_trips_through_float(session):
    value = (1 << 100) + 1
    session.user_ns["x"] = make_fxp(value, "Q128.0")
    assert session.list_variables()[0].value_str == str(value)


def test_conversion_preview_compares_numeric_values_not_text_spelling(session):
    session.user_ns["x"] = 1.0
    result = conversion_preview(session, "x", "Q4.4")
    assert result["changed_values"] == 0
    assert session.user_ns["x"] == 1.0 and type(session.user_ns["x"]) is float


@pytest.mark.parametrize("array,title", [
    (np.array(3), "A[()]"), (np.array([1, 2]), "A[0]"),
    (np.array([[1, 2]]), "A[0, 0]"),
    (np.zeros((1, 1, 2, 3)), "A[0, 0, 1, 2]"),
])
def test_inspector_title_uses_actual_python_indices(session, array, title):
    session.user_ns["A"] = array
    if array.ndim > 2:
        session.set_array_slice("A", [1, 2])
    assert session.get_inspector_snapshot("A", (0, 0)).display_title == title


def test_slice_follows_rename_and_duplicate_and_is_removed_on_delete(session):
    session.user_ns["A"] = np.zeros((2, 2, 3))
    session.set_array_slice("A", [2])
    session.rename_variable("A", "B")
    assert session.get_matrix_snapshot("B", include_cells=False).slice_indices == (2,)
    name = session.duplicate_variable("B")
    assert session.get_matrix_snapshot(name, include_cells=False).slice_indices == (2,)
    session.delete_variable("B")
    assert "B" not in session.array_slices
