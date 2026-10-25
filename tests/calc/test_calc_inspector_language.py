"""Language changes update app copy while preserving editable scientific data."""

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QItemSelection, QItemSelectionModel, QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from rpkbin.calc.core import CalcConfig, WorkspaceSession, make_fxp
from rpkbin.calc.ui.inspector_widget import VariableInspectorWidget
from rpkbin.calc.ui.matrix_editor import MatrixEditorPage
from rpkbin.calc.ui.theme import build_stylesheet, get_palette


class Controller(QObject):
    inspector_updated = Signal(object)
    config_changed = Signal(object)
    format_error_occurred = Signal(str)
    operation_finished = Signal(object, str)
    console_executed = Signal(str)
    edit_finished = Signal(object, str)
    matrix_page_received = Signal(object, int)
    interaction_enabled = Signal(bool)
    statistics_ready = Signal(str, object)

    def __init__(self, cfg):
        super().__init__()
        self.config = cfg
        self.revision = 1
        self.is_available = True
        self.calls = []

    def _save_config(self):
        pass

    def set_pin_mode(self, *args):
        self.calls.append(("pin", args))

    def set_inspector_view_mode(self, *args):
        pass

    def invert_target_bits(self):
        pass

    def clear_target_bits(self):
        pass

    def toggle_bit_order(self):
        pass

    def toggle_target_bit(self, *args):
        self.calls.append(("bit", args))

    def request_matrix_page(self, *args):
        pass

    def select_variable(self, *args):
        pass

    def edit_target_field(self, *args):
        self.calls.append(("edit", args))
        return True

    def change_variable_format(self, *args):
        self.calls.append(("format", args))
        return True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def views(app):
    cfg = CalcConfig()
    controller = Controller(cfg)
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    session.user_ns["Copy"] = make_fxp(0.3, "Q4.12")
    session.user_ns["array"] = make_fxp(np.arange(1200).reshape(100, 12), "Q16.12")
    inspector = VariableInspectorWidget(controller)
    inspector.render_snapshot(session.get_inspector_snapshot("Copy"))
    page = MatrixEditorPage("array", controller)
    page.render_snapshot(session.get_matrix_snapshot("array"))
    for widget in (inspector, page):
        widget.resize(600, 600)
        widget.show()
    app.processEvents()
    yield cfg, controller, session, inspector, page
    inspector.close()
    page.close()
    app.processEvents()


def switch(cfg, controller, language):
    cfg.language = language
    controller.config_changed.emit(cfg)


def test_inspector_switch_preserves_modified_inputs_bit_identity_and_user_name(views, app):
    cfg, controller, _, inspector, _ = views
    inspector.activateWindow()
    inspector.card_real.edit.setFocus()
    inspector.card_real.edit.setText("0.125 + 0.25")
    inspector.card_real.edit.setModified(True)
    inspector.fmt_edit.setText("Q8.8")
    inspector.fmt_edit.setModified(True)
    widgets = dict(inspector._bit_cells)
    inspector.body_scroll.verticalScrollBar().setValue(20)
    scroll = inspector.body_scroll.verticalScrollBar().value()
    app.processEvents()
    focus = QApplication.focusWidget()
    switch(cfg, controller, "zh_TW")
    app.processEvents()
    assert inspector.title_lbl.text() == "變數檢視器"
    assert inspector.target_badge.text() == "Copy"  # Never translate user data.
    assert inspector.card_real.edit.text() == "0.125 + 0.25"
    assert inspector.card_real.edit.isModified()
    assert inspector.fmt_edit.text() == "Q8.8"
    assert inspector.fmt_edit.isModified()
    assert QApplication.focusWidget() is focus
    assert inspector.body_scroll.verticalScrollBar().value() == scroll
    assert all(inspector._bit_cells[i][0] is record[0] for i, record in widgets.items())
    assert "權重" in inspector._bit_cells[0][2].toolTip()
    inspector._bit_cells[0][2].click()
    assert controller.calls[-1] == ("bit", (0,))
    switch(cfg, controller, "en_US")
    assert inspector.title_lbl.text() == "Inspector"
    assert inspector.card_real.edit.text() == "0.125 + 0.25"


@pytest.mark.parametrize("kind,text,expected", [
    ("saturated", "SATURATED", "已飽和"),
    ("untracked", "No conversion reference (error unknown)", "誤差未知"),
    ("exact", "Exact conversion", "精確轉換"),
    ("quant_err", "Quant Err: 1/999999999999999999999999999999999999 (last conversion)", "1/999999999999999999999999999999999999"),
])
def test_inspector_diagnostics_are_semantic_and_keep_exact_error(views, kind, text, expected):
    cfg, controller, _, inspector, _ = views
    snap = replace(inspector._last_snapshot, status_kind=kind, status_text=text, quant_err=None)
    inspector.render_snapshot(snap)
    switch(cfg, controller, "zh_TW")
    assert expected in inspector.status_badge.text()
    if kind == "saturated":
        assert snap.range_str in inspector.status_badge.text()
    if kind == "exact":
        assert snap.lsb_str in inspector.status_badge.text()
    switch(cfg, controller, "en_US")
    assert inspector.status_badge.text() == text


def test_native_fxp_transitions_and_errors_translate_without_touching_values(views):
    cfg, controller, session, inspector, page = views
    switch(cfg, controller, "zh_TW")
    session.user_ns["Copy"] = np.int8(-7)
    inspector.render_snapshot(session.get_inspector_snapshot("Copy"))
    assert inspector.format_host.isHidden()
    assert inspector.card_real.title_lbl.text() == "數值"
    assert "唯讀" in inspector.native_bits_label.text()
    assert inspector.card_real.edit.text() == "-7"
    assert not inspector._bit_cells
    session.user_ns["Copy"] = make_fxp(0.25, "Q4.12")
    inspector.render_snapshot(session.get_inspector_snapshot("Copy"))
    assert inspector.fmt_lbl.text() == "格式："
    assert inspector._bit_cells
    controller.edit_finished.emit(("Copy", None, "real", "broken"), "UserException: Copy")
    assert inspector.card_real.error_label.text() == "編輯失敗：UserException: Copy"
    switch(cfg, controller, "en_US")
    assert inspector.card_real.error_label.text() == "Edit failed: UserException: Copy"
    assert inspector.card_real.edit.text() == "broken"
    page.jump_edit.setText("bad")
    page._jump_to_cell()
    switch(cfg, controller, "zh_TW")
    assert "有效的列、欄" in page.error_lbl.text()


def test_matrix_language_retains_model_cache_selection_scroll_and_edits(views, app):
    cfg, controller, _, _, page = views
    page.mode_buttons["hex"].click()
    page.table.setCurrentIndex(page.model.index(20, 2))
    selection = QItemSelection(page.model.index(20, 2), page.model.index(22, 4))
    page.table.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    page.table.verticalScrollBar().setValue(15)
    page.fmt_edit.setText("Q4.12")
    page.fmt_edit.setModified(True)
    page.jump_edit.setText("32, 6")
    page.retry_edit.setText("pending input")
    page.model._cache[(0, 0)] = object()
    original_snapshot = page.model.snapshot
    cache = dict(page.model._cache)
    current = page.table.currentIndex()
    rectangle = page.selection_rectangle()
    scroll = page.table.verticalScrollBar().value()
    # Avoid requesting model cells containing our synthetic cache sentinel.
    page.hide()
    switch(cfg, controller, "zh_TW")
    assert page.fmt_lbl.text() == "陣列格式："
    assert page.model.headerData(2, Qt.Orientation.Horizontal) == "欄 2"
    assert page.model.headerData(20, Qt.Orientation.Vertical) == "列 20"
    assert page.model.snapshot is original_snapshot
    assert page.model._cache == cache
    assert page.table.currentIndex() == current
    assert page.selection_rectangle() == rectangle
    assert page.table.verticalScrollBar().value() == scroll
    assert page.model.mode == "hex"
    assert page.fmt_edit.text() == "Q4.12" and page.fmt_edit.isModified()
    assert page.jump_edit.text() == "32, 6"
    assert page.retry_edit.text() == "pending input"
    assert "貼上" in page.paste_btn.toolTip()
    switch(cfg, controller, "en_US")
    assert page.model.headerData(2, Qt.Orientation.Horizontal) == "Col 2"
    assert page.selection_rectangle() == rectangle
    page.model._cache.clear()


def test_matrix_native_dtype_and_retry_keep_user_exception_on_switch(views):
    cfg, controller, session, _, page = views
    session.user_ns["array"] = np.arange(48).reshape(8, 6)
    page.render_snapshot(session.get_matrix_snapshot("array"))
    controller.edit_finished.emit(("array", (2, 3), "real", "bad input"), "ValueError: Copy")
    switch(cfg, controller, "zh_TW")
    assert page.fmt_lbl.text() == "資料型別："
    assert not page.fmt_edit.isEnabled()
    assert "儲存格 (2, 3)" in page.error_lbl.text()
    assert "ValueError: Copy" in page.error_lbl.text()
    assert page.retry_edit.text() == "bad input"
    switch(cfg, controller, "en_US")
    assert page.fmt_lbl.text() == "Dtype:"
    assert page.error_lbl.text() == "Cell (2, 3) / real: ValueError: Copy"


def test_language_switch_does_not_replace_unsubmitted_inline_cell_editor(views, app):
    cfg, controller, session, _, page = views
    controller.matrix_page_received.emit(session.get_matrix_snapshot("array", window=(0, 0, 32, 16)), 1)
    controller.interaction_enabled.emit(True)
    page.activateWindow()
    index = page.model.index(1, 1)
    page.table.setCurrentIndex(index)
    page.table.edit(index)
    app.processEvents()
    editor = page.table.findChild(QLineEdit)
    assert editor is not None
    editor.setText("0.125 + 0.25")
    editor.setModified(True)
    editor.setCursorPosition(5)
    editor.setFocus()
    app.processEvents()
    switch(cfg, controller, "zh_TW")
    app.processEvents()
    assert page.table.findChild(QLineEdit) is editor
    assert editor.text() == "0.125 + 0.25"
    assert editor.isModified()
    assert editor.cursorPosition() == 5
    assert QApplication.focusWidget() is editor
    assert not controller.calls


def test_chinese_layout_remains_usable_at_narrow_width_and_large_scale(views, app):
    cfg, controller, _, inspector, page = views
    cfg.ui_font_size = cfg.code_font_size = 16
    cfg.ui_scale = 160
    switch(cfg, controller, "zh_TW")
    for widget in (inspector, page):
        widget.setStyleSheet(build_stylesheet(get_palette(cfg.theme), cfg))
        widget.apply_appearance(cfg)
        widget.resize(480, 900)
    for _ in range(3):
        app.processEvents()
    assert page.width() == 480
    assert page.minimumSizeHint().width() <= 480
    assert page.table.height() >= 100
    for button in page.mode_buttons.values():
        assert button.isVisible()
        assert button.width() > 0
    assert inspector.field_btn.isVisible()
    assert "編輯位元" in inspector.field_btn.text()
