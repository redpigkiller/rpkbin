"""Headless user contracts for bounded edits and explicit array operations."""

from fractions import Fraction
from rpkbin.calc.core.config import CalcConfig

import numpy as np
import pytest

from rpkbin.calc.core import WorkspaceSession
from rpkbin.calc.core.editing import (
    EditJournal,
    conversion_preview,
    native_integer_bits,
    paste_rectangle,
    selection_grid,
    selection_statistics,
)
from rpkbin.calc.core.fxp_model import make_fxp


@pytest.fixture
def session():
    return WorkspaceSession(CalcConfig(), seed_demo_vars=False)


def edit(session, journal, name, value, coord=None, field="real"):
    journal.apply([(name, coord)], lambda: session.update_target_field(name, coord, field, str(value)))


def test_two_scalar_edits_walk_backward_and_forward_and_clear(session):
    session.user_ns["a"] = 1000
    journal = EditJournal(session)
    edit(session, journal, "a", 1001)
    edit(session, journal, "a", 1002)
    assert journal.state() == {"can_undo": True, "can_redo": False, "undo_count": 2, "redo_count": 0}
    for expected in (1001, 1000):
        journal.replay()
        assert session.user_ns["a"] == expected
    for expected in (1001, 1002):
        journal.replay(redo=True)
        assert session.user_ns["a"] == expected
    journal.clear()
    assert journal.state() == {"can_undo": False, "can_redo": False, "undo_count": 0, "redo_count": 0}
    with pytest.raises(ValueError, match="Nothing to undo"):
        journal.replay()


@pytest.mark.parametrize("array", [False, True])
def test_undo_restores_fxp_conversion_reference_and_error(session, array):
    original = make_fxp([[0.1]] if array else 0.1, "Q4.4")
    session.user_ns["a"] = original
    coord = (0, 0) if array else None
    before = session.get_inspector_snapshot("a", coord)
    assert before.status_kind == "quant_err"
    journal = EditJournal(session)
    edit(session, journal, "a", 0.5, coord)
    assert session.get_inspector_snapshot("a", coord).status_kind == "exact"
    journal.replay()
    restored = session.get_inspector_snapshot("a", coord)
    assert (restored.real_str, restored.quant_err, restored.status_text) == (
        before.real_str, before.quant_err, before.status_text)
    journal.replay(redo=True)
    assert session.get_inspector_snapshot("a", coord).real_val == 0.5


@pytest.mark.parametrize("array", [False, True])
def test_native_numpy_dtype_survives_undo_and_redo(session, array):
    session.user_ns["a"] = np.array([[10]], dtype=np.int16) if array else np.int16(10)
    coord = (0, 0) if array else None
    journal = EditJournal(session)
    edit(session, journal, "a", 11, coord)
    for redo, expected in ((False, 10), (True, 11)):
        journal.replay(redo=redo)
        current = session.user_ns["a"][0, 0] if array else session.user_ns["a"]
        assert type(current) is np.int16
        assert current == expected


def test_single_large_array_cell_edit_does_not_copy_its_owner(session):
    class NoWholeArrayCopy(np.ndarray):
        def __deepcopy__(self, memo):
            raise AssertionError("A cell edit must not copy the entire array")

    large = np.zeros((2000, 1000), dtype=np.int64).view(NoWholeArrayCopy)
    session.user_ns["a"] = large
    journal = EditJournal(session)
    edit(session, journal, "a", 7, (1999, 999))
    assert session.user_ns["a"] is large
    journal.replay()
    assert large[1999, 999] == 0
    journal.replay(redo=True)
    assert large[1999, 999] == 7


@pytest.mark.parametrize("external_change", ["rebind", "alias_write", "dtype", "shape"])
def test_external_changes_refuse_undo_and_clear_history(session, external_change):
    array = np.array([[1, 2]], dtype=np.int16)
    session.user_ns.update(a=array, alias=array)
    journal = EditJournal(session)
    edit(session, journal, "a", 3, (0, 0))
    if external_change == "rebind":
        session.user_ns["a"] = array.copy()
    elif external_change == "alias_write":
        session.user_ns["alias"][0, 0] = 4
    elif external_change == "dtype":
        array.dtype = np.uint16
    else:
        array.shape = (2, 1)
    with pytest.raises(ValueError, match="history cleared"):
        journal.replay()
    assert not journal.state()["can_undo"]


def test_failure_rolls_back_cells_without_adding_journal_entry(session):
    array = np.array([[1, 2]], dtype=np.int16)
    session.user_ns["a"] = array
    journal = EditJournal(session)

    def fail_after_first_write():
        session.update_target_field("a", (0, 0), "real", "9")
        raise ValueError("second write failed")

    with pytest.raises(ValueError, match="second write failed"):
        journal.apply([("a", (0, 0)), ("a", (0, 1))], fail_after_first_write)
    np.testing.assert_array_equal(array, [[1, 2]])
    assert journal.state()["undo_count"] == 0


def test_readonly_edit_rejected_without_history(session):
    array = np.array([[1]], dtype=np.int8)
    array.flags.writeable = False
    session.user_ns["a"] = array
    journal = EditJournal(session)
    with pytest.raises(ValueError, match="read-only"):
        edit(session, journal, "a", 2, (0, 0))
    assert array[0, 0] == 1
    assert journal.state()["undo_count"] == 0


def test_new_edit_after_undo_discards_redo_branch(session):
    session.user_ns["a"] = 0
    journal = EditJournal(session)
    edit(session, journal, "a", 1)
    journal.replay()
    edit(session, journal, "a", 2)
    assert journal.state()["redo_count"] == 0
    with pytest.raises(ValueError, match="Nothing to redo"):
        journal.replay(redo=True)


def test_journal_keeps_only_latest_100_entries(session):
    session.user_ns["a"] = 0
    journal = EditJournal(session)
    assert journal.MAX_ENTRIES == 100
    for number in range(1, 102):
        edit(session, journal, "a", number)
    assert journal.state()["undo_count"] == 100
    for _ in range(100):
        journal.replay()
    assert session.user_ns["a"] == 1


def test_journal_rejects_oversized_before_and_after_scalar_state(session):
    journal = EditJournal(session)
    assert journal.MAX_BYTES == 8 * 1024 * 1024
    session.user_ns["a"] = "x" * (5 * 1024 * 1024)
    with pytest.raises(ValueError, match="8 MiB"):
        edit(session, journal, "a", "'small'")
    assert len(session.user_ns["a"]) == 5 * 1024 * 1024
    session.user_ns["a"] = "small"
    large = "y" * (5 * 1024 * 1024)
    with pytest.raises(ValueError, match="8 MiB"):
        journal.apply([("a", None)], lambda: session.user_ns.__setitem__("a", large))
    assert session.user_ns["a"] == "small"
    assert journal.state()["undo_count"] == 0


def test_journal_total_retained_bytes_evict_oldest_entries(session, monkeypatch):
    session.user_ns["a"] = "x" * 4096
    journal = EditJournal(session)
    monkeypatch.setattr(journal, "MAX_BYTES", 64 * 1024)
    for index in range(20):
        replacement = str(index) + "x" * 4096
        journal.apply([("a", None)], lambda replacement=replacement: session.user_ns.__setitem__("a", replacement))
    assert 0 < journal.state()["undo_count"] < 20
    assert sum(entry[2] for entry in journal.undo_stack) <= journal.MAX_BYTES


def test_empty_or_oversized_journal_target_batch_is_rejected_before_operation(session):
    session.user_ns["a"] = 1
    journal = EditJournal(session)
    for targets in ([], [("a", None)] * 100001):
        with pytest.raises(ValueError, match="100,000"):
            journal.apply(targets, lambda: pytest.fail("Invalid batches must not execute"))


@pytest.mark.parametrize("tsv,expected_error", [
    ("9\t10\n11", ValueError),
    ("9\t10\n11\t40000", (ValueError, OverflowError)),
    ("9\t10\n11\t1.5", ValueError),
    ("9\t10\n11\t'bad'", (ValueError, TypeError)),
])
def test_paste_validates_every_cell_before_commit(session, tsv, expected_error):
    array = np.array([[1, 2], [3, 4]], dtype=np.int16)
    session.user_ns["a"] = array
    journal = EditJournal(session)
    with pytest.raises(expected_error):
        paste_rectangle(session, journal, "a", 0, 0, tsv)
    np.testing.assert_array_equal(array, [[1, 2], [3, 4]])
    assert journal.state()["undo_count"] == 0


def test_paste_bounds_fail_before_any_cell_is_written(session):
    session.user_ns["a"] = np.zeros((2, 2), dtype=np.int8)
    journal = EditJournal(session)
    with pytest.raises(IndexError):
        paste_rectangle(session, journal, "a", 1, 1, "1\t2")
    assert not session.user_ns["a"].any()
    assert not journal.state()["can_undo"]


def test_paste_work_and_text_limits_reject_before_mutation(session):
    session.user_ns["a"] = np.zeros((1, 1), dtype=np.int8)
    journal = EditJournal(session)
    for text, message in (("0\t" * 100000 + "0", "100,000"),
                          ("0" * (8 * 1024 * 1024 + 1), "8 MiB")):
        with pytest.raises(ValueError, match=message):
            paste_rectangle(session, journal, "a", 0, 0, text)
    assert session.user_ns["a"][0, 0] == 0
    assert journal.state()["undo_count"] == 0


@pytest.mark.parametrize("fxp", [False, True])
def test_rectangular_paste_is_one_undo_and_preserves_other_cells(session, fxp):
    original = np.arange(12).reshape(3, 4)
    session.user_ns["a"] = make_fxp(original, "Q8.0") if fxp else original.copy()
    journal = EditJournal(session)
    paste_rectangle(session, journal, "a", 1, 1, "20\t21\n22\t23")
    expected = original.copy()
    expected[1:3, 1:3] = [[20, 21], [22, 23]]
    np.testing.assert_array_equal(np.asarray(session.user_ns["a"].to_numpy()) if fxp else session.user_ns["a"], expected)
    assert journal.state()["undo_count"] == 1
    journal.replay()
    np.testing.assert_array_equal(np.asarray(session.user_ns["a"].to_numpy()) if fxp else session.user_ns["a"], original)
    journal.replay(redo=True)
    np.testing.assert_array_equal(np.asarray(session.user_ns["a"].to_numpy()) if fxp else session.user_ns["a"], expected)


def test_raw_paste_requires_fxp_and_honors_strict_raw_range(session):
    session.user_ns["a"] = make_fxp([[0, 0]], "Q8.0")
    journal = EditJournal(session)
    with pytest.raises(ValueError, match="WL=8"):
        paste_rectangle(session, journal, "a", 0, 0, "FF\t100", "hex")
    assert session.user_ns["a"].to_bits() == [[0, 0]]
    paste_rectangle(session, journal, "a", 0, 0, "FF\t7F", "hex")
    assert session.user_ns["a"].to_bits() == [[255, 127]]
    session.user_ns["b"] = np.zeros((1, 2), dtype=np.int16)
    with pytest.raises(TypeError, match="FXP"):
        paste_rectangle(session, journal, "b", 0, 0, "FF", "hex")


def test_selection_grid_preserves_representation_and_limits_work(session):
    session.user_ns["a"] = make_fxp([[1, -1], [2, 3]], "Q8.0")
    assert selection_grid(session, "a", (0, 1, 2, 1), "hex") == [["0xFF"], ["0x03"]]
    session.user_ns["large"] = np.zeros((1000, 101))
    for operation in (selection_grid, selection_statistics):
        with pytest.raises(ValueError, match="100,000"):
            operation(session, "large", (0, 0, 1000, 101))


def test_statistics_skip_nonfinite_values_and_state_approximation(session):
    session.user_ns["a"] = np.array([[1.0, np.nan], [np.inf, 3.0]])
    result = selection_statistics(session, "a", (0, 0, 2, 2))
    assert result["count"] == 4
    assert result["finite_count"] == 2
    assert float(result["sum"]) == 4
    assert float(result["mean"]) == 2
    assert "approximate" in result["note"]
    session.user_ns["words"] = np.array([["a", "b"]])
    with pytest.raises(TypeError, match="numeric"):
        selection_statistics(session, "words", (0, 0, 1, 2))


@pytest.mark.parametrize("shape,indices", [((2, 3, 4), (2,)), ((2, 3, 4, 5), (2, 3))])
@pytest.mark.parametrize("fxp", [False, True])
def test_multidimensional_view_inspector_and_edit_share_selected_slice(session, shape, indices, fxp):
    values = np.arange(np.prod(shape)).reshape(shape)
    session.user_ns["a"] = make_fxp(values, "Q16.0") if fxp else values.copy()
    session.set_array_slice("a", indices)
    snapshot = session.get_matrix_snapshot("a")
    index = (1, 2) + indices
    assert snapshot.full_shape == shape
    assert snapshot.slice_indices == indices
    assert float(snapshot.cells_real[1][2]) == values[index]
    inspector = session.get_inspector_snapshot("a", (1, 2))
    assert inspector.display_title == f"a[{', '.join(map(str, index))}]"
    assert float(inspector.real_str if fxp else inspector.value_str) == values[index]
    journal = EditJournal(session)
    edit(session, journal, "a", 900, (1, 2))
    actual = session.user_ns["a"].to_numpy() if fxp else session.user_ns["a"]
    expected = values.copy()
    expected[index] = 900
    np.testing.assert_array_equal(actual, expected)
    session.set_array_slice("a", (0,) * len(indices))
    with pytest.raises(ValueError, match="slice or cell changed"):
        journal.replay()
    np.testing.assert_array_equal(session.user_ns["a"].to_numpy() if fxp else session.user_ns["a"], expected)


@pytest.mark.parametrize("indices", [(), (4,), (-1,), (True,), (1, 0), (1.0,)])
def test_slice_indices_reject_wrong_count_range_and_type(session, indices):
    session.user_ns["a"] = np.zeros((2, 3, 4))
    with pytest.raises(ValueError, match="zero-based index"):
        session.set_array_slice("a", indices)


@pytest.mark.parametrize("size", [4096, 4097])
def test_conversion_preview_bound_and_explicit_whole_variable_scope(session, size):
    original = np.arange(size, dtype=np.int16).reshape(1, size)
    session.user_ns["a"] = original
    preview = conversion_preview(session, "a", "Q8.0", coord=(0, size - 1))
    assert session.user_ns["a"] is original
    np.testing.assert_array_equal(original, np.arange(size).reshape(1, size))
    assert preview["total_cells"] == size
    assert preview["complete"] == (size <= 4096)
    assert preview["checked_cells"] == (size if size <= 4096 else 1)
    if size > 4096:
        assert "selected cell sample" in preview["scope"]
        assert "WHOLE variable" in preview["scope"]
        assert preview["example"]["before"] == str(size - 1)


def test_conversion_preview_reports_sat_wrap_truncation_and_real_precision(session):
    session.user_ns["a"] = make_fxp([[7.75, -8]], "Q6.2")
    original = session.user_ns["a"]
    before_bits = original.to_bits()
    sat = conversion_preview(session, "a", "Q4.0")
    assert sat["saturated_cells"] == 1
    assert sat["example"] == {"before": "7.75", "after": "7"}
    session.config.default_overflow = "WRAP"
    wrapped = conversion_preview(session, "a", "Q4.0")
    assert wrapped["saturated_cells"] == 0
    assert wrapped["example"]["after"] == "-8"
    raw = conversion_preview(session, "a", "Q4.0", pin="hex")
    assert raw["discarded_high_bits_cells"] == 2
    assert original.to_bits() == before_bits
    assert session.user_ns["a"] is original
    session.user_ns["b"] = make_fxp(Fraction(1, 8), "Q4.4")
    precision = conversion_preview(session, "b", "Q6.2")
    assert precision["changed_values"] == 1
    assert precision["lsb"] == "2**(-2)"


def test_complete_preview_includes_other_slices_but_large_sample_uses_current_slice(session):
    values = np.zeros((2, 2, 2), dtype=np.int16)
    values[1, 1, 1] = 999
    session.user_ns["a"] = values
    session.set_array_slice("a", (0,))
    complete = conversion_preview(session, "a", "Q8.0")
    assert complete["checked_cells"] == 8
    assert complete["saturated_cells"] == 1
    large = np.zeros((2, 2, 1025), dtype=np.int16)
    large[1, 1, 1024] = 999
    session.user_ns["b"] = large
    session.set_array_slice("b", (1024,))
    sampled = conversion_preview(session, "b", "Q8.0", coord=(1, 1))
    assert sampled["checked_cells"] == 1
    assert sampled["saturated_cells"] == 1
    assert sampled["example"]["before"] == "999"
    np.testing.assert_array_equal(session.user_ns["b"], large)


@pytest.mark.parametrize("value,width,signed,hex_text", [
    (np.int8(-1), 8, True, "0xFF"), (np.int16(-2), 16, True, "0xFFFE"),
    (np.uint8(255), 8, False, "0xFF"), (np.uint64(1 << 63), 64, False, "0x8000000000000000"),
])
def test_native_integer_bit_view_preserves_dtype_storage_contract(value, width, signed, hex_text):
    metadata = native_integer_bits(value)
    assert metadata["width"] == width
    assert metadata["signed"] is signed
    assert metadata["dtype"] == str(value.dtype)
    assert metadata["hex"] == hex_text
    assert len(metadata["binary"]) == width
    assert int(metadata["binary"], 2) == int(hex_text, 16)
    assert not any(key in metadata for key in ("fwl", "quant", "overflow", "pin"))


@pytest.mark.parametrize("value", [2, -(1 << 200), 1.0, np.float32(1), True, np.bool_(True)])
def test_native_bit_view_does_not_invent_width_for_other_types(value):
    assert native_integer_bits(value) is None


@pytest.mark.parametrize("array", [False, True])
def test_native_integer_inspector_exposes_read_only_dtype_bits_without_fxp_format(session, array):
    scalar = np.int16(-2)
    session.user_ns["a"] = np.array([[scalar]], dtype=np.int16) if array else scalar
    snapshot = session.get_inspector_snapshot("a", (0, 0) if array else None)
    assert snapshot.type_name == "int16"
    assert snapshot.native_bits == native_integer_bits(scalar)
    assert not hasattr(snapshot, "spec")
    with pytest.raises(TypeError, match="FXP"):
        session.update_target_field("a", (0, 0) if array else None, "hex", "FF")
