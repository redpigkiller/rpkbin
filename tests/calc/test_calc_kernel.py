"""Real local-kernel integration tests, including the Qt event loop and widgets."""

from __future__ import annotations

import os
import time
import copy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("qtconsole")
pytest.importorskip("ipykernel")
from PySide6.QtWidgets import QApplication, QTextBrowser
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest

from rpkbin.calc.controller import CalcController
from rpkbin.calc.ui.app import CalcMainWindow


def wait_for(app, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for calculator state")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def gui(app, tmp_path):
    controller = CalcController(config_path=tmp_path / "settings.json")
    window = CalcMainWindow(controller)
    errors = []
    controller.format_error_occurred.connect(lambda error: errors.append(error) if error else None)
    window.show()
    try:
        wait_for(app, lambda: controller.transport.ready or bool(errors))
        assert controller.transport.ready, errors
        wait_for(app, lambda: controller._pending is None and not controller._dirty)
        yield window, controller, errors
    finally:
        window.close()
        app.processEvents()


def run(app, controller, code):
    done = []
    controller.console_executed.connect(done.append)
    try:
        controller.execute_command(code)
        wait_for(app, lambda: bool(done))
        wait_for(app, lambda: controller._pending is None and not controller._dirty
                 and not controller.transport.busy)
        return done[-1]
    finally:
        controller.console_executed.disconnect(done.append)


def test_live_language_change_does_not_reconfigure_or_restart_kernel(app, gui, monkeypatch):
    from dataclasses import replace

    window, controller, errors = gui
    run(app, controller, '資料 = APyFixedArray.from_float([[.25, -.125]], bits=16, frac_bits=12)')
    controller.open_matrix_editor("資料")
    settled(app, controller)
    page = window._matrix_tabs["資料"]
    original = (controller.transport.client, controller.transport.manager, page.model, controller.revision)
    window.console_widget.term.input_buffer = "np.array([ # 未完成"
    history = list(window.console_widget.command_history.commands)
    requests = []
    original_request = controller.transport.request
    def record_request(request):
        requests.append(request)
        return original_request(request)
    monkeypatch.setattr(controller.transport, "request", record_request)
    for language in ("zh_TW", "en_US"):
        controller.update_config(replace(controller.config, language=language))
        for _ in range(3):
            app.processEvents()
        # A layout repaint may lazily read an uncached matrix page. It must
        # never reconfigure, mutate or restart the scientific workspace.
        assert all(request["op"] == "page" for request in requests)
        assert not controller._mutations
        assert (controller.transport.client, controller.transport.manager, page.model, controller.revision) == original
        assert window.console_widget.term.input_buffer == "np.array([ # 未完成"
        assert window.console_widget.command_history.commands == history
        assert window._matrix_tabs["資料"] is page
    window.console_widget.term.input_buffer = ""
    assert not errors


def test_native_ipython_and_python_rebinding(app, gui):
    window, controller, errors = gui
    metrics = QFontMetricsF(window.console_widget.term._control.font())
    assert metrics.horizontalAdvance("iiii") == pytest.approx(metrics.horizontalAdvance("WWWW"))
    run(app, controller, 'x = APyFixed.from_float(0.1, bits=10, frac_bits=12)\nx = 1.0')
    row = next(row for row in controller.rows if row.name == "x")
    assert row.format_str == "float"
    assert window.inspector_widget.card_hex.edit.isReadOnly()
    assert window.inspector_widget.card_s2.isHidden()
    run(app, controller, "total = 0\nfor i in range(4):\n    total += i\ntotal")
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "6"
    result = run(app, controller, "%time 1+2")
    assert result["content"]["status"] == "ok"
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "3"
    assert not errors


def test_kernel_native_apy_namespace_and_gui_conversion_boundary(app, gui):
    window, controller, errors = gui
    config = copy.deepcopy(controller.config)
    config.default_quant = "TRN"
    config.default_overflow = "SAT"
    mutate(app, controller, lambda: controller.update_config(config))
    result = run(app, controller, """assert APyFixed is apy.APyFixed
assert APyFixedArray is apy.APyFixedArray
assert APyFloat is apy.APyFloat
assert APyFloatArray is apy.APyFloatArray
assert 'fxp' not in globals() and 'fxpi' not in globals()
raw = APyFixed(7, bits=6, frac_bits=2)
real = APyFixed.from_float(7, bits=6, frac_bits=2)
array = APyFixedArray.from_float([[0.25, 0.5]], bits=6, frac_bits=2)
floating = APyFloat.from_float(0.25, exp_bits=8, man_bits=23)
assert raw.to_bits() == 7 and float(raw) == 1.75
assert real.to_bits() == 28 and float(real) == 7
assert array.to_bits() == [[1, 2]]
assert float(floating) == 0.25
assert APyFixed.from_float(0.375, bits=4, frac_bits=2).to_bits() == 2
assert APyFixed.from_float(3, bits=4, frac_bits=2).to_bits() == 12""")
    assert result["content"]["status"] == "ok"
    controller.select_variable("raw")
    settled(app, controller)
    inspector = window.inspector_widget
    assert inspector.fmt_edit.text() == "Q4.2"
    assert inspector.card_hex.edit.text() == "0x07"
    assert inspector.card_real.edit.text() == "1.75"
    assert inspector.status_badge.property("statusKind") == "untracked"
    mutate(app, controller, lambda: controller.edit_target_field("raw", None, "real", "0.375"))
    assert inspector.card_hex.edit.text() == "0x01"
    assert inspector.card_real.edit.text() == "0.25"
    assert inspector.status_badge.property("statusKind") == "quant_err"
    assert not errors


def test_late_iopub_execute_input_cannot_reopen_completed_execution(app):
    from rpkbin.calc.core.config import CalcConfig
    from rpkbin.calc.kernel_client import KernelTransport
    transport = KernelTransport(CalcConfig())
    transport.note_user_execution()
    transport._handle_reply({"msg_type": "execute_reply", "parent_header": {"msg_id": "cell1"},
                             "content": {"status": "ok"}})
    assert not transport.busy
    transport._handle_publication({"msg_type": "execute_input", "parent_header": {"msg_id": "cell1"}})
    assert not transport.busy
    transport.note_user_execution()
    transport._handle_publication({"msg_type": "execute_input", "parent_header": {"msg_id": "cell1"}})
    assert transport.busy
    transport._handle_reply({"msg_type": "execute_reply", "parent_header": {"msg_id": "cell2"},
                             "content": {"status": "ok"}})
    assert not transport.busy


def test_native_values_hide_fxp_controls_and_explicit_conversion_ignores_pin(app, gui):
    window, controller, errors = gui
    run(app, controller, "a=2\nA=np.array([255], dtype=np.uint8)")
    controller.open_matrix_editor("A")
    settled(app, controller)
    inspector = window.inspector_widget
    assert inspector.status_badge.text() == "uint8"
    assert inspector.card_hex.edit.isReadOnly() and inspector.bit_scroll.isHidden()
    assert not inspector.fmt_edit.isEnabled()
    page = window._matrix_tabs["A"]
    controller.interaction_enabled.emit(True)
    assert not page.fmt_edit.isEnabled()
    mutate(app, controller, lambda: controller.edit_target_field("A", (0, 0), "real", "128"))
    run(app, controller, "str(A.dtype)")
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "'uint8'"
    controller.set_pin_mode("hex")
    mutate(app, controller, lambda: controller.convert_to_fxp("a", "Q4.4"))
    controller.select_variable("a")
    settled(app, controller)
    assert inspector.card_real.edit.text() == "2"
    assert inspector.card_hex.edit.text() == "0x20"
    wait_for(app, lambda: inspector.fmt_edit.isEnabled())
    controller.select_variable("A")
    settled(app, controller)
    assert not inspector.fmt_edit.isEnabled() and inspector.card_hex.edit.isReadOnly()
    mutate(app, controller, lambda: controller.convert_to_fxp("A", "UQ8.4"))
    wait_for(app, lambda: page.fmt_edit.isEnabled())
    assert inspector.card_hex.edit.text() == "0x800"
    assert page.fmt_lbl.text() == "Array format:"
    assert not errors
    run(app, controller, "a=2")
    assert inspector.card_hex.edit.isReadOnly() and not inspector.fmt_edit.isEnabled()
    controller.toggle_target_bit(15)  # A stale/out-of-band edit must not quantize a native int.
    wait_for(app, lambda: any("FXP" in error for error in errors))
    settled(app, controller)
    run(app, controller, "a")
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "2"


def settled(app, controller):
    wait_for(app, lambda: controller._pending is None and not controller._dirty
             and not controller._mutations and not controller._pages and controller.is_available)


def test_apyfloat_bit_clicks_cross_kernel_and_undo_without_changing_format(app, gui):
    window, controller, errors = gui
    run(app, controller, "f = APyFloat.from_float(1.5, exp_bits=5, man_bits=2, bias=7)")
    controller.select_variable("f")
    settled(app, controller)
    inspector = window.inspector_widget
    assert inspector.card_hex.edit.text() == "0x1E"
    wait_for(app, lambda: inspector._bit_cells[7][2].isEnabled())
    revision = controller.revision
    inspector._bit_cells[7][2].click()
    wait_for(app, lambda: controller.revision > revision)
    settled(app, controller)
    assert inspector.card_real.edit.text() == "-1.5"
    assert inspector.card_hex.edit.text() == "0x9E"
    mutate(app, controller, controller.undo_edit)
    assert inspector.card_hex.edit.text() == "0x1E"
    mutate(app, controller, controller.redo_edit)
    assert inspector.card_hex.edit.text() == "0x9E"
    # User execution observes the edited object and clears edit history normally.
    run(app, controller, "assert type(f) is APyFloat\nassert (f.exp_bits, f.man_bits, f.bias, f.to_bits()) == (5, 2, 7, 0x9E)")
    assert not controller.undo_state["can_undo"]
    run(app, controller, "n = np.float32(-0.0)")
    controller.select_variable("n")
    settled(app, controller)
    assert inspector.card_hex.edit.text() == "0x80000000"
    assert all(not button.isEnabled() for _, _, button, _ in inspector._bit_cells.values())
    run(app, controller, "x = -255")
    controller.select_variable("x")
    settled(app, controller)
    assert inspector.card_hex.edit.text() == "-0xff"
    assert inspector.card_bin.edit.text() == "-0b11111111"
    assert not errors


def mutate(app, controller, operation):
    revision = controller.revision
    operation()
    wait_for(app, lambda: controller.revision > revision)
    settled(app, controller)


def test_gui_edit_undo_redo_and_repl_history_boundary(app, gui):
    window, controller, errors = gui
    run(app, controller, "x = 0.1")
    mutate(app, controller, lambda: controller.convert_to_fxp("x", "Q4.4"))
    controller.select_variable("x")
    settled(app, controller)
    original = window.inspector_widget.card_real.edit.text()
    mutate(app, controller, lambda: controller.edit_target_field("x", None, "real", "0.5"))
    assert window.undo_btn.isEnabled()
    mutate(app, controller, controller.undo_edit)
    assert window.inspector_widget.card_real.edit.text() == original
    assert window.inspector_widget.status_badge.property("statusKind") == "quant_err"
    assert window.redo_btn.isEnabled()
    mutate(app, controller, controller.redo_edit)
    assert window.inspector_widget.card_real.edit.text() == "0.5"
    run(app, controller, "x")
    assert not controller.undo_state["can_undo"] and not controller.undo_state["can_redo"]
    assert not window.undo_btn.isEnabled()
    assert not errors


def test_matrix_slice_paste_undo_selection_and_exact_display(app, gui):
    from dataclasses import replace
    from PySide6.QtCore import QItemSelection, QItemSelectionModel
    window, controller, errors = gui
    run(app, controller, "A=np.arange(24,dtype=np.int16).reshape(2,3,4)")
    controller.open_matrix_editor("A")
    settled(app, controller)
    page = window._matrix_tabs["A"]
    mutate(app, controller, lambda: controller.set_array_slice("A", [2]))
    index = page.model.index(0, 0)
    wait_for(app, lambda: page.model.data(index) != "…")
    assert page.model.data(index) == "2"
    assert "0, 0, 2" in window.inspector_widget.target_badge.text()
    mutate(app, controller, lambda: controller.paste_matrix("A", 0, 0, "100\t101\n102\t103"))
    wait_for(app, lambda: page.model.data(index) != "…")
    assert page.model.data(index) == "100"
    mutate(app, controller, controller.undo_edit)
    wait_for(app, lambda: page.model.data(index) != "…")
    assert page.model.data(index) == "2"
    selection = QItemSelection(index, page.model.index(1, 1))
    page.table.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    assert page.selection_rectangle() == (0, 0, 2, 2)
    controller.refresh_all()
    settled(app, controller)
    assert page.selection_rectangle() == (0, 0, 2, 2)
    clipboard = []
    controller.clipboard_ready.connect(clipboard.append)
    controller.update_config(replace(controller.config, numeric_display="scientific", numeric_precision=3))
    assert page.model.data(index) == "2.000E+0"
    assert page.model.data(index, Qt.ItemDataRole.EditRole) == "2"
    controller.copy_matrix("A", "real", page.selection_rectangle())
    wait_for(app, lambda: bool(clipboard))
    assert clipboard[-1] == "2\t6\n14\t18"
    assert not errors


def test_preview_statistics_native_bits_and_session_history(app, gui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window, controller, errors = gui
    run(app, controller, 'n = np.uint8(255)\nx = APyFixed.from_float(1.0, bits=8, frac_bits=4)\nA = np.array([[1.0, np.nan], [3.0, 5.0]])')
    history = window.console_widget.command_history
    count = len(history.commands)
    controller.select_variable("n")
    settled(app, controller)
    assert "0xFF" in window.inspector_widget.native_bits_label.text()
    assert window.inspector_widget.card_hex.edit.isReadOnly()
    previews = []
    controller.preview_ready.connect(previews.append)
    monkeypatch.setattr("rpkbin.calc.ui.app.ask", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    controller.select_variable("x")
    settled(app, controller)
    revision = controller.revision
    controller.request_preview("x", "Q4.8")
    wait_for(app, lambda: bool(previews))
    settled(app, controller)
    assert previews[-1]["changed_values"] == 0
    assert controller.revision == revision
    summaries = []
    controller.statistics_ready.connect(lambda name, result: summaries.append((name, result)))
    controller.request_statistics("A", (0, 0, 2, 2))
    wait_for(app, lambda: bool(summaries))
    assert summaries[-1][1]["count"] == 4
    assert summaries[-1][1]["finite_count"] == 3
    assert summaries[-1][1]["mean"] == "3.0"
    assert len(history.commands) == count
    assert not errors


def test_rejected_cell_input_is_preserved_until_corrected(app, gui):
    window, controller, errors = gui
    run(app, controller, "A=np.array([[7]],dtype=np.uint8)")
    controller.open_matrix_editor("A")
    settled(app, controller)
    page = window._matrix_tabs["A"]
    card = window.inspector_widget.card_real
    controller.edit_target_field("A", (0, 0), "real", "999")
    wait_for(app, lambda: bool(errors))
    settled(app, controller)
    assert card.edit.text() == "999" and not card.error_label.isHidden()
    assert page.retry_edit.text() == "999" and not page.retry_edit.isHidden()
    mutate(app, controller, lambda: controller.edit_target_field("A", (0, 0), "real", "8"))
    assert card.error_label.isHidden() and page.retry_edit.isHidden()
    assert card.edit.text() == "8"


def test_reset_layout_preserves_namespace_theme_and_font(app, gui):
    window, controller, errors = gui
    run(app, controller, "sentinel=123")
    cfg = controller.config
    before = (cfg.theme, cfg.ui_font_size, cfg.code_font_size, cfg.ui_scale)
    window.inspector_widget.set_collapsed(True)
    window.resize(900, 600)
    window.reset_layout()
    app.processEvents()
    assert not window.inspector_widget.body_scroll.isHidden()
    assert (cfg.theme, cfg.ui_font_size, cfg.code_font_size, cfg.ui_scale) == before
    assert any(row.name == "sentinel" for row in controller.rows)
    assert not errors


def test_format_preview_focus_change_is_read_only_and_apply_is_explicit(app, gui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window, controller, errors = gui
    run(app, controller, 'A = APyFixedArray.from_float([[0.125]], bits=8, frac_bits=4)')
    controller.open_matrix_editor("A")
    settled(app, controller)
    page = window._matrix_tabs["A"]
    previews = []
    controller.preview_ready.connect(previews.append)
    monkeypatch.setattr("rpkbin.calc.ui.app.ask", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    revision = controller.revision
    page.fmt_edit.setText("Q4.8")
    page.fmt_edit.editingFinished.emit()
    page.preview_btn.click()
    wait_for(app, lambda: bool(previews))
    settled(app, controller)
    assert controller.revision == revision
    assert page._last_snapshot.format_str == "Q4.4"
    monkeypatch.setattr("rpkbin.calc.ui.app.ask", lambda *a, **k: QMessageBox.StandardButton.Apply)
    page.fmt_edit.setText("Q4.8")
    page.preview_btn.click()
    wait_for(app, lambda: controller.revision > revision)
    settled(app, controller)
    assert page._last_snapshot.format_str == "Q4.8"
    assert not controller.undo_state["can_undo"]  # Whole-array format is structural.
    assert not errors


def test_bit_field_is_undoable_and_invalid_raw_input_is_atomic(app, gui):
    window, controller, errors = gui
    run(app, controller, "x = 128")
    mutate(app, controller, lambda: controller.convert_to_fxp("x", "UQ8.0"))
    controller.select_variable("x")
    settled(app, controller)
    mutate(app, controller, lambda: controller.set_bit_field(3, 0, 15))
    assert window.inspector_widget.card_hex.edit.text() == "0x8F"
    mutate(app, controller, controller.undo_edit)
    assert window.inspector_widget.card_hex.edit.text() == "0x80"
    controller.edit_target_field("x", None, "hex", "100")
    wait_for(app, lambda: bool(errors))
    settled(app, controller)
    assert window.inspector_widget.card_hex.edit.text() == "100"  # Failed source is editable.
    assert next(row.hex_str for row in controller.rows if row.name == "x") == "0x80"


def test_inspector_refresh_without_user_modification_never_writes_stale_text(app, gui):
    window, controller, errors = gui
    run(app, controller, "x=2.5\ny=100.0")
    controller.select_variable("x")
    settled(app, controller)
    card = window.inspector_widget.card_real
    revision = controller.revision
    card.edit.setText("1.25")  # Simulate an old, untouched display during a refresh.
    card.edit.setModified(False)
    card._on_edit_finished()
    app.processEvents()
    assert controller.revision == revision
    assert card.edit.text() == "2.5"
    card.edit.setText("3.25")
    card.edit.setModified(True)
    mutate(app, controller, card._on_edit_finished)
    assert next(row.value_str for row in controller.rows if row.name == "x") == "3.25"
    controller.select_variable("y")
    settled(app, controller)
    assert card.edit.text() == "100.0"
    assert not errors


def test_fxp_pin_bit_edits_share_ipython_namespace(app, gui):
    window, controller, errors = gui
    run(app, controller, 'x = APyFixed.from_float(-0.03, bits=10, frac_bits=12)')
    controller.set_pin_mode("hex")
    mutate(app, controller, lambda: controller.change_variable_format("x", "Q10.0"))
    assert window.inspector_widget.card_real.edit.text() == "-123"
    assert window.inspector_widget.card_hex.edit.text() == "0x385"
    mutate(app, controller, lambda: controller.toggle_target_bit(0))
    assert window.inspector_widget.card_hex.edit.text() == "0x384"
    run(app, controller, "int(x.to_bits())")
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "900"
    assert not errors


def test_conversion_diagnostics_reach_gui_without_false_exactness(app, gui):
    window, controller, errors = gui
    run(app, controller, 'from rpkbin.calc.core.fxp_model import make_fxp as _make_fxp\nx=_make_fxp(0.1,"Q4.50")\ny=x+1\nz=_make_fxp("0x20","Q4.4")\nA=_make_fxp([0.125],"Q4.4")+1')
    inspector = window.inspector_widget
    controller.select_variable("x")
    settled(app, controller)
    assert inspector.status_badge.property("statusKind") == "quant_err"
    assert "last conversion" in inspector.status_badge.text()
    for name in ("y", "z", "A"):
        controller.select_variable(name)
        settled(app, controller)
        assert inspector.status_badge.property("statusKind") == "untracked"
        assert "unknown" in inspector.status_badge.text()
    mutate(app, controller, lambda: controller.edit_target_field("A", (0, 0), "real", "0.1"))
    assert inspector.status_badge.property("statusKind") == "quant_err"
    mutate(app, controller, lambda: controller.edit_target_field("A", (0, 0), "real", "0.125"))
    assert inspector.status_badge.property("statusKind") == "exact"
    assert "Exact conversion" in inspector.status_badge.text()
    mutate(app, controller, lambda: controller.toggle_target_bit(0))
    assert inspector.status_badge.property("statusKind") == "untracked"
    assert "GUI FXP edits:" in window.active_fmt_badge.text()
    assert "Native APyTypes" in window.active_fmt_badge.toolTip()
    run(app, controller, "n=2")
    controller.select_variable("n")
    settled(app, controller)
    assert inspector.status_badge.property("statusKind") == "plain"
    assert inspector.status_badge.toolTip() == ""
    assert not errors


def test_complex_matrix_native_edit_and_saturation_view(app, gui):
    window, controller, errors = gui
    run(app, controller, "v = fft([1,2,3,4])")
    controller.open_matrix_editor("v")
    settled(app, controller)
    page = window._matrix_tabs["v"]
    index = page.model.index(0, 1)
    wait_for(app, lambda: page.model.data(index) != "…")
    assert complex(page.model.data(index)) == -2+2j
    mutate(app, controller, lambda: page.model.setData(index, "5-6j"))
    run(app, controller, "v[1]")
    assert complex(next(row.value_str for row in controller.rows if row.name == "ans")) == 5-6j
    assert window.inspector_widget.card_hex.isHidden()
    assert not window.inspector_widget.fmt_edit.isEnabled()
    run(app, controller, 'from rpkbin.calc.core.fxp_model import make_fxp as _make_fxp\nA = _make_fxp([[0.25, 0.1]], "Q-2.12")')
    controller.open_matrix_editor("A")
    settled(app, controller)
    assert "SATURATED" in window.inspector_widget.status_badge.text()
    controller.select_variable("A", (0, 1))
    settled(app, controller)
    assert "Quant Err" in window.inspector_widget.status_badge.text()
    assert not errors


def test_large_matrix_is_virtual_and_requests_bounded_pages(app, gui):
    window, controller, errors = gui
    run(app, controller, "A = np.arange(4000000).reshape(1000000,4)")
    controller.open_matrix_editor("A")
    settled(app, controller)
    page = window._matrix_tabs["A"]
    assert page.model.rowCount() == 1_000_000
    assert page.model.columnCount() == 4
    assert page._last_snapshot.cells_real == []
    index = page.model.index(999999, 3)
    wait_for(app, lambda: page.model.data(index) != "…")
    assert page.model.data(index) == "3999999"
    for row in range(0, 320, 32):
        index = page.model.index(row, 0)
        wait_for(app, lambda index=index: page.model.data(index) != "…")
    assert len(page.model._cache) <= page.model.MAX_CACHED_PAGES
    assert sum(len(value.cells_real) * len(value.cells_real[0])
               for value in page.model._cache.values()) <= 8 * 32 * 16
    assert not errors


def test_workspace_saturation_badges_use_current_bounded_diagnostics(app, gui):
    from PySide6.QtGui import QColor
    from rpkbin.calc.core.config import ALL_WORKSPACE_COLUMNS
    window, controller, errors = gui
    workspace = window.workspace_widget
    value_col = ALL_WORKSPACE_COLUMNS.index("value")

    def value_item(name):
        index = next(i for i, row in enumerate(workspace._rows) if row.name == name)
        return workspace.table.item(index, value_col)

    run(app, controller, 'from rpkbin.calc.core.fxp_model import make_fxp as _make_fxp\nA=_make_fxp([99.0]+[0.0]*4096,"Q4.4")\nB=_make_fxp([99,0],"Q4.4")')
    controller.select_variable("A")
    settled(app, controller)
    assert "SATURATED" in window.inspector_widget.status_badge.text()
    assert value_item("A").text().startswith("[SAT?]")
    assert value_item("A").foreground().color() != QColor(workspace.palette.danger)
    assert "4096" in value_item("A").toolTip()
    assert value_item("B").text().startswith("[SAT]")
    assert value_item("B").foreground().color() == QColor(workspace.palette.danger)
    run(app, controller, 'from rpkbin.calc.core.fxp_model import make_fxp as _make_fxp\nA[0]=_make_fxp(0,"Q4.4")\nB[0]=B[1]')
    controller.select_variable("A")
    settled(app, controller)
    assert "unknown" in window.inspector_widget.status_badge.text()
    assert value_item("A").text().startswith("[SAT?]")
    assert value_item("B").text().startswith("[SAT?]")
    assert value_item("B").foreground().color() != QColor(workspace.palette.danger)
    controller.select_variable("B")
    settled(app, controller)
    mutate(app, controller, lambda: controller.edit_target_field("B", (0, 0), "real", "0"))
    assert not value_item("B").text().startswith("[SAT")
    assert next(row for row in controller.rows if row.name == "B").is_saturated is False
    # Status annotations belong to the view, not the numeric copy payload.
    assert not next(row for row in controller.rows if row.name == "A").value_str.startswith("[SAT?")
    assert not errors


def test_native_array_gui_sources_cross_kernel_boundary(app, gui):
    window, controller, errors = gui
    run(app, controller, 'small=APyFixedArray.from_float([0],bits=8,frac_bits=4)\nlarge=APyFixedArray.from_float(np.zeros(4097),bits=8,frac_bits=4)\nalias=small')
    for name in ("small", "large"):
        controller.select_variable(name)
        settled(app, controller)
        mutate(app, controller, lambda name=name: controller.edit_target_field(name, (0, 0), "real", "99"))
        assert "SATURATED" in window.inspector_widget.status_badge.text()
    rows = {row.name: row for row in controller.rows}
    assert rows["small"].is_saturated is True
    assert rows["alias"].is_saturated is True
    assert rows["large"].is_saturated is None
    run(app, controller, 'assert type(small) is APyFixedArray and alias is small\nsmall[0]=APyFixed.from_float(0,bits=8,frac_bits=4)')
    controller.select_variable("small")
    settled(app, controller)
    assert "unknown" in window.inspector_widget.status_badge.text()
    assert next(row for row in controller.rows if row.name == "small").is_saturated is None
    assert not errors


def test_fast_selection_keeps_latest_inspector(app, gui):
    window, controller, errors = gui
    run(app, controller, "A=np.arange(20).reshape(4,5)\nB=123")
    controller.open_matrix_editor("A")
    settled(app, controller)
    for row, col in ((0,0), (1,2), (3,4)):
        controller.select_variable("A", (row, col))
    controller.select_variable("B")
    settled(app, controller)
    assert controller.selected_var == "B"
    assert window.inspector_widget.target_badge.text() == "B"
    assert window.inspector_widget.target_badge.toolTip() == "B"
    assert window.inspector_widget.card_real.edit.text() == "123"
    assert not errors


def test_errors_preserve_stdout_and_gui_recovers(app, gui):
    window, controller, errors = gui
    result = run(app, controller, 'print("diagnostic-before-error"); 1/0')
    assert result["content"]["ename"] == "ZeroDivisionError"
    text = window.console_widget.term._control.toPlainText()
    assert "diagnostic-before-error" in text
    assert "ZeroDivisionError" in text
    run(app, controller, "I = np.array([1,2],dtype=np.int16)")
    controller.edit_target_field("I", (0,0), "real", "1.5")
    wait_for(app, lambda: any("without loss" in error for error in errors))
    settled(app, controller)
    run(app, controller, "I.tolist()")
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "[1, 2]"


def test_live_loop_interrupt_and_shutdown(app, gui):
    window, controller, errors = gui
    ticks = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    done = []
    controller.console_executed.connect(done.append)
    try:
        controller.execute_command('print("loop-ready",flush=True)\nwhile True:\n    pass')
        wait_for(app, lambda: "loop-ready" in window.console_widget.term._control.toPlainText()
                 and controller.transport.busy)
        before = len(ticks)
        wait_for(app, lambda: len(ticks) >= before + 5)
        assert not done
        assert not window.inspector_widget.body_widget.isEnabled()
        assert window.console_widget.interrupt_btn.isEnabled()
        window.console_widget.interrupt_btn.click()
        wait_for(app, lambda: bool(done))
        assert done[-1]["content"]["ename"] == "KeyboardInterrupt"
        settled(app, controller)
        assert not window.console_widget.interrupt_btn.isEnabled()
        assert "idle" in window.console_widget.interrupt_btn.toolTip().lower()
        run(app, controller, "1+2")
        assert next(row.value_str for row in controller.rows if row.name == "ans") == "3"
        process = controller.transport.manager.provisioner.process
        window.close()
        assert process.poll() is not None
    finally:
        timer.stop()


def test_restart_recovers_after_kernel_exit(app, gui):
    window, controller, errors = gui
    run(app, controller, "old_value=42")
    process = controller.transport.manager.provisioner.process
    process.kill()
    wait_for(app, lambda: not controller.transport.ready)
    assert not window.inspector_widget.body_widget.isEnabled()
    controller.restart_kernel()
    wait_for(app, lambda: controller.transport.ready)
    settled(app, controller)
    assert "old_value" not in {row.name for row in controller.rows}
    assert controller.transport.manager.provisioner.process.pid != process.pid
    run(app, controller, "value=7")
    assert next(row.value_str for row in controller.rows if row.name == "value") == "7"


def test_clear_rename_copy_and_reload_preserve_user_values(app, gui):
    window, controller, errors = gui
    run(app, controller, "A=np.array([[1.25,2.5]])\nsin=3")
    controller.open_matrix_editor("A")
    settled(app, controller)
    mutate(app, controller, lambda: controller.rename_variable("A", "B"))
    assert "B" in window._matrix_tabs and "A" not in window._matrix_tabs
    copied = []
    controller.clipboard_ready.connect(copied.append)
    controller.copy_matrix("B", "real")
    wait_for(app, lambda: bool(copied))
    assert copied[-1] == "1.25\t2.5"
    config = controller.config
    config.default_quant = "TRN"
    mutate(app, controller, lambda: controller.update_config(config))
    assert next(row.value_str for row in controller.rows if row.name == "sin") == "3"
    run(app, controller, "clear")
    assert controller.rows == []
    assert not window._matrix_tabs
    run(app, controller, "cos(0)")
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "1.0"
    assert not errors


def test_clc_clears_screen_but_preserves_workspace(app, gui):
    window, controller, errors = gui
    run(app, controller, 'value=7\nprint("old-output-marker")')
    assert "old-output-marker" in window.console_widget.term._control.toPlainText()
    run(app, controller, "clc")
    assert "old-output-marker" not in window.console_widget.term._control.toPlainText()
    assert next(row.value_str for row in controller.rows if row.name == "value") == "7"
    run(app, controller, "value+1")
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "8"
    assert not errors


def test_restart_while_running_discards_old_replies(app, gui):
    window, controller, errors = gui
    controller.execute_command("while True:\n    pass")
    wait_for(app, lambda: controller.transport.busy)
    old_process = controller.transport.manager.provisioner.process
    controller.restart_kernel()
    wait_for(app, lambda: controller.transport.ready)
    settled(app, controller)
    assert old_process.poll() is not None
    assert not controller.rows
    run(app, controller, "fresh=9")
    assert {row.name for row in controller.rows} == {"fresh"}
    assert not errors


def test_copy_limit_and_python_empty_values(app, gui):
    window, controller, errors = gui
    run(app, controller, "A=np.zeros((1001,100))\nempty=np.array([])\nz=np.array(3)")
    controller.copy_matrix("A", "real")
    wait_for(app, lambda: any("100,000" in error for error in errors))
    controller.select_variable("empty")
    settled(app, controller)
    assert window.inspector_widget.card_real.edit.isReadOnly()
    controller.open_matrix_editor("empty")
    settled(app, controller)
    assert window._matrix_tabs["empty"].model.columnCount() == 0
    controller.select_variable("z")
    settled(app, controller)
    assert window.inspector_widget.card_real.edit.text() == "3"


def test_completion_and_startup_script_error_are_observable(app, gui):
    window, controller, errors = gui
    messages = []
    channel = controller.transport.client.shell_channel
    channel.message_received.connect(messages.append)
    text = "np.lins"
    message_id = controller.transport.client.complete(text, len(text))
    wait_for(app, lambda: any(message.get("parent_header", {}).get("msg_id") == message_id for message in messages))
    reply = next(message["content"] for message in messages if message.get("parent_header", {}).get("msg_id") == message_id)
    assert "np.linspace" in {text[:reply["cursor_start"]]+match+text[reply["cursor_end"]:] for match in reply["matches"]}
    controller.startup_store.path.write_text("# first line\nraise RuntimeError('startup-probe-error')", encoding="utf-8")
    controller.reload_startup()
    wait_for(app, lambda: any("startup-probe-error" in error for error in errors))
    settled(app, controller)
    assert str(controller.startup_store.path) in controller.startup_state['error']
    assert 'line 2' in controller.startup_state['error']
    run(app, controller, "1+2")
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "3"


def test_file_startup_reload_shares_namespace_and_clear_does_not_rerun(app, gui, tmp_path):
    window, controller, errors = gui
    marker = tmp_path / 'count.txt'
    source = (f'from pathlib import Path\nwith Path({str(marker)!r}).open("a") as _f:\n    _f.write("x")\n'
              'from math import prod as fft\ndef scale(x):\n    return x * gain\n')
    controller.startup_store.path.write_text(source, encoding='utf-8')
    run(app, controller, 'gain = 3\nkeep = 42')
    assert not marker.exists()  # Saving, executing Python and snapshots do not reload.
    mutate(app, controller, controller.reload_startup)
    run(app, controller, 'result = scale(2)\nassert result == 6\nassert fft([2,3]) == 6')
    assert not {'Path', 'fft', 'scale'} & {row.name for row in controller.rows}
    config = copy.deepcopy(controller.config)
    config.auto_import_groups['fft'] = False
    mutate(app, controller, lambda: controller.update_config(config))
    assert marker.read_text() == 'x'
    run(app, controller, 'clear')
    assert marker.read_text() == 'x'
    run(app, controller, 'gain = 4\nresult = scale(2)\nassert result == 8')
    controller.restart_kernel()
    wait_for(app, lambda: controller.transport.ready)
    settled(app, controller)
    assert marker.read_text() == 'xx'
    run(app, controller, 'gain = 5\nassert scale(2) == 10')
    assert not errors


def test_interrupted_startup_reply_recovers_without_kernel_restart(app, gui):
    window, controller, errors = gui
    client = controller.transport.client
    controller.startup_store.path.write_text('raise KeyboardInterrupt()', encoding='utf-8')
    mutate(app, controller, controller.reload_startup)
    assert any('KeyboardInterrupt' in error for error in errors)
    assert not controller.startup_state['ok']
    errors.clear()
    controller.startup_store.path.write_text('from math import prod as fft', encoding='utf-8')
    mutate(app, controller, controller.reload_startup)
    run(app, controller, 'assert fft([2,3]) == 6\n1+2')
    assert controller.transport.client is client
    assert controller.startup_state['ok']
    assert not errors


def test_custom_import_apply_reports_errors_recovers_and_does_not_run_startup(app, gui, tmp_path):
    window, controller, errors = gui
    client = controller.transport.client
    counter = tmp_path / "custom-import-startup-count.txt"
    controller.startup_store.path.write_text(
        f"from pathlib import Path\nwith Path({str(counter)!r}).open('a') as f:\n    f.write('x')", encoding="utf-8")
    mutate(app, controller, controller.reload_startup)
    run(app, controller, "keep = 17")
    config = copy.deepcopy(controller.config)
    config.custom_imports = "from math import prod as product\nimport rpk_calc_nonexistent_package"
    mutate(app, controller, lambda: controller.update_config(config))
    assert "rpk_calc_nonexistent_package" in controller.import_error
    assert any("ModuleNotFoundError" in error for error in errors)
    assert counter.read_text() == "x"
    assert controller.transport.client is client
    assert run(app, controller, "assert product([2,3]) == 6\nassert keep == 17")["content"]["status"] == "ok"
    assert not {"product", "Path"} & {row.name for row in controller.rows}
    # A setup warning must not masquerade as a failed edit acknowledgement.
    edits = []
    controller.edit_finished.connect(lambda args, error: edits.append(error))
    run(app, controller, "x = APyFixed.from_float(0.25, bits=8, frac_bits=4)")
    mutate(app, controller, lambda: controller.edit_target_field("x", None, "real", "0.5"))
    assert edits == [""]
    assert run(app, controller, "assert float(x) == 0.5")["content"]["status"] == "ok"
    config = copy.deepcopy(controller.config)
    config.custom_imports = "from math import prod as product"
    mutate(app, controller, lambda: controller.update_config(config))
    assert controller.import_error == ""
    assert counter.read_text() == "x"
    run(app, controller, "clear")
    assert run(app, controller, "assert product([2,3]) == 6")["content"]["status"] == "ok"
    assert counter.read_text() == "x"


def test_live_appearance_preserves_namespace_input_and_script_side_effects(app, gui, tmp_path):
    window, controller, errors = gui
    console = window.console_widget.term
    assert console._control.toPlainText().strip() == "In [1]:"
    assert not hasattr(window.console_widget, "clear_btn")
    counter = tmp_path / "startup-count.txt"
    config = copy.deepcopy(controller.config)
    controller.startup_store.path.write_text(f"from pathlib import Path\nwith Path({str(counter)!r}).open('a') as _f:\n    _f.write('x')", encoding="utf-8")
    mutate(app, controller, controller.reload_startup)
    run(app, controller, 'A = APyFixedArray.from_float([[0.25, 0.1]], bits=10, frac_bits=12)\nkeep = 42')
    controller.open_matrix_editor("A")
    settled(app, controller)
    assert window.workspace_widget.open_array_btn.isVisible()
    console.input_buffer = "pending_text = 9"
    before_text = console._control.toPlainText()
    revision = controller.revision
    process = controller.transport.manager.provisioner.process
    for theme in ("light", "dark", "light"):
        config = copy.deepcopy(controller.config)
        config.theme = theme
        config.ui_font_family = "Arial"
        config.code_font_family = "Consolas"
        config.ui_font_size = 12
        config.code_font_size = 14
        config.ui_scale = 120
        controller.update_config(config)
        app.processEvents()
        settled(app, controller)
        assert controller._pending is None and not controller._mutations
        assert controller.revision == revision
        assert controller.transport.manager.provisioner.process is process
        assert console._control.toPlainText() == before_text
        assert counter.read_text() == "x"
        assert window._matrix_tabs["A"].palette.name == theme
        assert window.inspector_widget.palette.name == theme
        assert window.inspector_widget.card_real.palette.name == theme
        assert console._control.font().pointSizeF() == pytest.approx(16.8)
        assert window.workspace_widget.table.verticalHeader().defaultSectionSize() >= 40
        from pygments.styles import get_style_by_name
        from pygments.token import Number
        token_cursor = console._control.document().find("0.25")
        assert not token_cursor.isNull()
        block = token_cursor.block()
        position = token_cursor.selectionStart() - block.position()
        foreground = next(item.format.foreground().color().name() for item in block.layout().formats()
                          if item.start <= position < item.start + item.length)
        style = get_style_by_name("default" if theme == "light" else "monokai")
        assert foreground == "#" + style.style_for_token(Number.Float)["color"]
    # GUI quantization defaults update without rerunning startup or changing APyTypes.
    config.default_quant = "TRN"
    mutate(app, controller, lambda: controller.update_config(config))
    assert counter.read_text() == "x"
    console.input_buffer = ""
    run(app, controller, 'int(APyFixed.from_float(0.375, bits=4, frac_bits=2).to_bits())')
    assert next(row.value_str for row in controller.rows if row.name == "ans") == "2"
    run(app, controller, "quant_probe = 0.375")
    mutate(app, controller, lambda: controller.convert_to_fxp("quant_probe", "Q2.2"))
    assert next(row.hex_str for row in controller.rows if row.name == "quant_probe") == "0x1"
    assert next(row.value_str for row in controller.rows if row.name == "keep") == "42"
    assert not errors


def test_inspector_collapse_hints_and_layout_persistence(app, gui):
    window, controller, errors = gui
    assert window.inspector_widget.target_badge.text() == ""
    QTest.mouseClick(window.inspector_widget.title_lbl, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.inspector_widget.body_scroll.isHidden()
    assert controller.config.inspector_collapsed
    run(app, controller, "A=np.array([1,2,3])")
    wait_for(app, lambda: window.workspace_widget.open_array_btn.isEnabled())
    QTest.mouseClick(window.workspace_widget.open_array_btn, Qt.MouseButton.LeftButton)
    settled(app, controller)
    assert not window.inspector_widget.body_scroll.isHidden()
    assert "A" in window._matrix_tabs
    assert not controller.config.inspector_collapsed
    QTest.mouseClick(window.console_widget.hint_btn, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.console_widget._hint_dialog.isVisible()
    assert "clc" in window.console_widget._hint_dialog.findChild(QTextBrowser).toPlainText()
    window.console_widget._hint_dialog.close()
    window.resize(1440, 900)
    window.h_splitter.setSizes([800, 600])
    window.v_splitter.setSizes([300, 500])
    app.processEvents()
    window.close()
    from rpkbin.calc.core.config import CalcConfig
    saved = CalcConfig.load(controller._config_path)
    assert saved.window_geometry
    assert saved.horizontal_sizes[0] > saved.horizontal_sizes[1]
    assert saved.vertical_sizes[1] > saved.vertical_sizes[0] > 0
    assert not errors


def test_appearance_dialog_cancel_and_save_are_isolated(app, gui):
    from rpkbin.calc.ui.settings_dialog import SettingsDialog
    window, controller, errors = gui
    before = copy.deepcopy(controller.config)
    dialog = SettingsDialog(controller.config, window)
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("light"))
    dialog.ui_font_size.setValue(16)
    dialog.code_font_size.setValue(18)
    dialog.scale_spin.setValue(130)
    dialog.reject()
    assert controller.config == before
    dialog._on_save()
    config = dialog.result_config()
    assert (config.theme, config.ui_font_size, config.code_font_size, config.ui_scale) == ("light", 16, 18, 130)
    controller.update_config(config)
    app.processEvents()
    assert window.palette.name == "light"
    assert not controller._mutations
    assert not errors


def test_matplotlib_inline_image_reaches_rich_console(app, gui):
    import base64
    from PySide6.QtGui import QImage

    window, controller, errors = gui
    images = []

    def collect(message):
        encoded = message.get("content", {}).get("data", {}).get("image/png")
        if encoded:
            images.append(encoded)

    channel = controller.transport.client.iopub_channel
    channel.message_received.connect(collect)
    try:
        result = run(app, controller, "%matplotlib inline\nimport matplotlib.pyplot as plt\n"
                     "t = np.linspace(0, 2 * np.pi, 200)\n"
                     "plt.plot(t, np.sin(t))\nplt.show()")
        assert result["content"]["status"] == "ok"
        wait_for(app, lambda: bool(images))
        image = QImage.fromData(base64.b64decode(images[-1]))
        assert not image.isNull() and image.width() > 100 and image.height() > 100
        wait_for(app, lambda: any(fmt.isImageFormat() for fmt in
                                 window.console_widget.term._control.document().allFormats()))
        assert not errors
    finally:
        channel.message_received.disconnect(collect)
