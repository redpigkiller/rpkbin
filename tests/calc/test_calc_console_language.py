"""Console and Workspace localization preserves the live Python session and data."""

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from rpkbin.calc.controller import CalcController
from rpkbin.calc.core import WorkspaceSession
from rpkbin.calc.ui.console_widget import CommandWindowWidget
from rpkbin.calc.ui.workspace_widget import WorkspaceWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def console(app, tmp_path):
    controller = CalcController(tmp_path / "settings.json")
    view = CommandWindowWidget(controller)
    view.term._show_interpreter_prompt(1)
    yield view, controller
    view.close()


def switch(view, controller, language):
    controller.config = replace(controller.config, language=language)
    view.apply_appearance(controller.config)


def test_stop_tracks_execution_across_language_and_ready_changes(console):
    view, controller = console
    assert view.interrupt_btn.text() == "Idle"
    assert not view.interrupt_btn.isEnabled()
    controller.transport.busy_changed.emit(True)
    assert view.interrupt_btn.text() == "Stop"
    switch(view, controller, "zh_TW")
    assert view.interrupt_btn.text() == "停止"
    assert view.interrupt_btn.isEnabled()
    assert "Ctrl+C" in view.interrupt_btn.toolTip()
    assert "原生" in view.interrupt_btn.toolTip()
    controller.transport.ready_changed.emit(False)
    assert not view.interrupt_btn.isEnabled()
    assert view.interrupt_btn.text() == "待命"
    switch(view, controller, "en_US")
    assert view.interrupt_btn.text() == "Idle"
    assert view.interrupt_btn.accessibleName() == "Kernel idle"


def test_language_preserves_command_input_history_search_and_preview(console):
    view, controller = console
    source = '名稱 = "使用者 English"\nprint(名稱)'
    view.term.input_buffer = source
    view.command_history.record("x = 1")
    view.command_history.record(source)
    view.show_history()
    dialog = view._history_dialog
    dialog.search.setText("名稱")
    preview = dialog.preview.toPlainText()
    dialog.preview.setFocus()
    cursor = dialog.preview.textCursor()
    cursor.setPosition(2)
    dialog.preview.setTextCursor(cursor)
    selected = dialog.commands.currentItem().data(Qt.ItemDataRole.UserRole)
    term = view.term
    for lang in ("zh_TW", "en_US", "zh_TW"):
        switch(view, controller, lang)
        assert view.term is term
        assert view.term.input_buffer == source
        assert view.command_history.commands == ["x = 1", source]
        assert dialog.search.text() == "名稱"
        assert dialog.preview.toPlainText() == preview
        assert dialog.preview.textCursor().position() == 2
        assert dialog.commands.currentItem().data(Qt.ItemDataRole.UserRole) == selected
    assert dialog.windowTitle() == "本次命令歷史"
    assert dialog.restore_btn.text() == "帶入輸入區"
    assert "筆命令" in dialog.summary.text()


def test_hints_are_short_tables_with_native_executable_examples(console, app):
    view, controller = console
    view.show_hints()
    app.processEvents()
    text = view._hint_text
    assert text.document().rootFrame().childFrames()  # QTextTable frames
    plain = text.toPlainText()
    for source in ("np.int8(2)", "np.float64(0.5)", "np.array([1.0, 2.0, 3.0])",
                   "APyFixed.from_float", "APyFixed(7, 4, 2)", "APyFloat.from_float", "%matplotlib inline"):
        assert source in plain
    assert "Raw bits: 7 represents 1.75" in plain
    assert "fxp(" not in plain and "s10.12f" not in plain and "np.float(" not in plain
    scrollbar = text.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum() // 2)
    position = scrollbar.value()
    switch(view, controller, "zh_TW")
    assert text is view._hint_text
    assert scrollbar.value() == position
    assert "原始位元：7 代表實際數值 1.75" in text.toPlainText()
    assert "APyFixed(7, 4, 2)" in text.toPlainText()
    namespace = {}
    exec("import numpy as np\nfrom apytypes import APyFixed, APyFixedArray, APyFloat, APyFloatArray", namespace)
    table = text.document().rootFrame().childFrames()[1]
    for row in range(1, table.rows()):
        cell = table.cellAt(row, 1)
        cursor = cell.firstCursorPosition()
        cursor.setPosition(cell.lastCursorPosition().position(), QTextCursor.MoveMode.KeepAnchor)
        source = cursor.selectedText().replace("\u2029", "\n").replace("\u2028", "\n")
        exec(source, namespace)
    assert float(namespace["raw"]) == 1.75


def test_workspace_translation_keeps_filters_selection_raw_values_and_markers(app, tmp_path):
    controller = CalcController(tmp_path / "settings.json")
    view = WorkspaceWidget(controller)
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.execute('資料 = APyFixedArray.from_float([0.25, -0.125], bits=16, frac_bits=12)\nother = 2')
    controller.selected_var = "資料"
    view.render_rows(session.list_variables())
    view.name_filter.setText("資料")
    view.type_filter.setCurrentIndex(view.type_filter.findData("fxp"))
    view.table.setCurrentCell(0, 1)
    value = view.table.item(0, 1).text()
    for language in ("zh_TW", "en_US", "zh_TW"):
        switch(view, controller, language)
        assert view.name_filter.text() == "資料"
        assert view.type_filter.currentData() == "fxp"
        assert view.table.currentColumn() == 1
        assert controller.selected_var == "資料"
        assert view.table.item(0, 0).text() == "資料"
        assert view.table.item(0, 1).text() == value
    assert view.name_filter.placeholderText() == "篩選變數名稱"
    assert view.type_filter.itemText(0) == "全部型別"
    assert "SAT?：" in view.table.item(0, 1).toolTip()
    view.close()
