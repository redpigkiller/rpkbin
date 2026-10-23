"""Offscreen layout and action contracts for narrow, large-font matrix pages."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QItemSelection, QItemSelectionModel, QObject, QPoint, QRect, Signal, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QSizePolicy

from rpkbin.calc.core import CalcConfig, WorkspaceSession, make_fxp
from rpkbin.calc.ui.matrix_editor import MatrixEditorPage
from rpkbin.calc.ui.theme import build_stylesheet, get_palette


class Controller(QObject):
    matrix_page_received = Signal(object, int)
    interaction_enabled = Signal(bool)
    config_changed = Signal(object)
    edit_finished = Signal(object, str)
    operation_finished = Signal(object, str)
    console_executed = Signal(str)
    statistics_ready = Signal(str, object)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.revision = 1
        self.is_available = True
        self.calls = []

    def request_matrix_page(self, *args):
        pass

    def copy_matrix(self, *args):
        self.calls.append(("copy", args))

    def request_preview(self, *args):
        self.calls.append(("preview", args))

    def paste_matrix(self, *args):
        self.calls.append(("paste", args))

    def request_statistics(self, *args):
        self.calls.append(("statistics", args))

    def select_variable(self, *args):
        pass

    def set_array_slice(self, *args):
        self.calls.append(("slice", args))

    def change_variable_format(self, *args):
        self.calls.append(("format", args))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page_factory(app):
    pages = []

    def create(cfg, *, fxp=True, width=480, height=900):
        controller = Controller(cfg)
        session = WorkspaceSession(cfg, seed_demo_vars=False)
        name = "array_with_a_long_descriptive_name_for_the_active_calculation"
        values = np.arange(24).reshape(2, 3, 4)
        session.user_ns[name] = make_fxp(values, "Q20.12") if fxp else values
        page = MatrixEditorPage(name, controller)
        page.setStyleSheet(build_stylesheet(get_palette(cfg.theme), cfg))
        page.render_snapshot(session.get_matrix_snapshot(name))
        page.resize(width, height)
        page.show()
        app.processEvents()
        pages.append(page)
        return page, controller

    yield create
    for page in pages:
        page.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("font_size,scale", [(10, 100), (16, 160)])
@pytest.mark.parametrize("fxp", [False, True])
def test_narrow_matrix_keeps_all_controls_inside_page(page_factory, theme, font_size, scale, fxp):
    cfg = CalcConfig(theme=theme, ui_font_size=font_size, code_font_size=font_size, ui_scale=scale)
    page, _ = page_factory(cfg, fxp=fxp)
    assert page.width() == 480
    assert page.minimumSizeHint().width() <= 480
    controls = [page.shape_lbl, page.fmt_lbl, page.fmt_edit, page.hint_lbl,
                page.slice_edit, page.jump_edit, page.table,
                page.copy_btn, page.preview_btn, page.selection_copy_btn,
                page.paste_btn, page.stats_btn, *page.mode_buttons.values()]
    for control in controls:
        assert control.isVisible(), control.objectName() or type(control).__name__
        position = control.mapTo(page, QPoint(0, 0))
        assert position.x() >= 0
        assert position.x() + control.width() <= page.width()
        assert position.y() >= 0
        assert position.y() + control.height() <= page.height()
        assert control.width() > 0 and control.height() > 0
    assert page.table.height() >= 100
    for button in page.mode_buttons.values():
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())
    assert page.fmt_edit.isEnabled() is fxp
    assert page.preview_btn.isEnabled() is fxp
    assert page.paste_btn.isEnabled()


def test_shape_and_navigation_have_separate_shrinkable_rows(page_factory):
    page, _ = page_factory(CalcConfig(ui_font_size=16, code_font_size=16, ui_scale=160))
    assert page.shape_lbl.wordWrap()
    shape_bottom = page.shape_lbl.mapTo(page, QPoint(0, page.shape_lbl.height())).y()
    assert shape_bottom <= page.fmt_edit.mapTo(page, QPoint(0, 0)).y()
    assert page.slice_edit.y() + page.slice_edit.height() <= page.jump_edit.y()
    assert page.fmt_edit.minimumWidth() == page.slice_edit.minimumWidth() == page.jump_edit.minimumWidth() == 0
    assert "Select to inspect; double-click to edit." in page.hint_lbl.text()
    assert "[:, :, 0]" in page.hint_lbl.text()
    assert "Full shape" in page.shape_lbl.toolTip()
    assert "current view:" in page.shape_lbl.toolTip()
    assert "[:, :, 0]" in page.shape_lbl.toolTip()


def test_matrix_actions_have_icons_and_distinct_accessible_english_names(page_factory):
    page, _ = page_factory(CalcConfig())
    expected = {
        page.copy_btn: "Copy entire matrix as TSV (up to 100,000 cells)",
        page.selection_copy_btn: "Copy selected cells as TSV (Ctrl+C)",
        page.paste_btn: "Paste TSV at the selected cell (Ctrl+V)",
        page.preview_btn: "Preview format conversion for the whole array",
        page.stats_btn: "Show statistics for the selected cells",
    }
    for button, label in expected.items():
        assert not button.icon().isNull()
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.toolTip() == label
        assert button.accessibleName() == label
        assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert page.copy_btn.icon().cacheKey() != page.selection_copy_btn.icon().cacheKey()


def test_icon_buttons_keep_matrix_operation_and_shortcut_semantics(page_factory, app):
    page, controller = page_factory(CalcConfig())
    selection = QItemSelection(page.model.index(0, 0), page.model.index(1, 1))
    page.table.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    page.copy_btn.click()
    page.selection_copy_btn.click()
    page.stats_btn.click()
    page.preview_btn.click()
    page.table.setCurrentIndex(page.model.index(1, 1))
    app.clipboard().setText("1\t2")
    page.paste_btn.click()
    name = page.var_name
    assert controller.calls == [
        ("copy", (name, "real")),
        ("copy", (name, "real", (0, 0, 2, 2))),
        ("statistics", (name, (0, 0, 2, 2))),
        ("preview", (name, "Q20.12")),
        ("paste", (name, 1, 1, "1\t2", "real")),
    ]
    shortcuts = page.findChildren(QShortcut)
    assert {shortcut.key().toString() for shortcut in shortcuts} == {
        QKeySequence(QKeySequence.StandardKey.Copy).toString(),
        QKeySequence(QKeySequence.StandardKey.Paste).toString(),
    }
    assert all(shortcut.context() == Qt.ShortcutContext.WidgetShortcut for shortcut in shortcuts)


def test_appearance_update_scales_icons_and_keeps_narrow_layout(page_factory, app):
    page, controller = page_factory(CalcConfig())
    before = page.copy_btn.iconSize().width()
    cfg = CalcConfig(theme="light", ui_font_size=16, code_font_size=16, ui_scale=160)
    controller.config = cfg
    page.apply_appearance(cfg)
    page.setStyleSheet(build_stylesheet(get_palette(cfg.theme), cfg))
    app.processEvents()
    assert page.copy_btn.iconSize().width() > before
    assert page.width() == 480
    assert page.minimumSizeHint().width() <= 480
    assert page.copy_btn.width() == page.selection_copy_btn.width() == page.stats_btn.width()
    for button in page.mode_buttons.values():
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())


def test_representation_buttons_reflow_without_losing_modes_or_selection(page_factory, app):
    page, _ = page_factory(CalcConfig(ui_font_size=16, code_font_size=16, ui_scale=160))
    page.mode_buttons["hex"].click()
    assert len({button.y() for button in page.mode_buttons.values()}) == 2
    page.resize(1400, 900)
    app.processEvents()
    assert len({button.y() for button in page.mode_buttons.values()}) == 1
    page.resize(480, 900)
    app.processEvents()
    assert page.width() == 480
    assert len({button.y() for button in page.mode_buttons.values()}) == 2
    assert all(button.isVisible() for button in page.mode_buttons.values())
    assert page.mode_buttons["hex"].isChecked()
    assert page.model.mode == "hex"


def test_normal_width_toolbar_is_compact_and_keeps_table_space(page_factory, app):
    page, _ = page_factory(CalcConfig(), width=900, height=600)
    assert page.width() == 900 and page.height() == 600
    assert page._toolbar_wide
    assert page._format_host.y() == page.shape_lbl.y()
    assert page._modes_host.y() == page._actions_host.y()
    assert page.slice_edit.y() == page.jump_edit.y()
    toolbar = page.findChild(QFrame, "PanelHeader")
    assert toolbar.height() <= 250
    assert page.table.height() >= 300
    assert page.shape_lbl.isVisible() and page.fmt_lbl.isVisible()
    assert page.fmt_lbl.width() >= page.fmt_lbl.fontMetrics().horizontalAdvance(page.fmt_lbl.text())
    assert all(button.isVisible() for button in page.mode_buttons.values())
    page.resize(480, 900)
    app.processEvents()
    assert not page._toolbar_wide
    assert page._modes_host.y() < page._actions_host.y()
    page.resize(900, 600)
    app.processEvents()
    assert page._toolbar_wide
    assert toolbar.height() <= 250
    assert page.table.height() >= 300


def test_slice_context_stays_visible_when_selected_slice_changes(page_factory):
    from dataclasses import replace

    page, _ = page_factory(CalcConfig())
    snapshot = replace(page._last_snapshot, slice_indices=(2,), slice_note="[:, :, 2]")
    page.render_snapshot(snapshot)
    assert "View: [:, :, 2]" in page.hint_lbl.text()
    assert "Select to inspect" in page.hint_lbl.text()
    assert "[:, :, 2]" in page.shape_lbl.toolTip()
    assert page.slice_edit.text() == "2"


@pytest.mark.parametrize("fxp", [False, True])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_mixed_code_and_ui_fonts_reserve_format_label_space_without_overlap(page_factory, fxp, theme):
    cfg = CalcConfig(theme=theme, ui_font_size=10, code_font_size=14, ui_scale=100)
    page, _ = page_factory(cfg, fxp=fxp, width=925, height=600)
    assert page._toolbar_wide
    label = page.fmt_lbl
    assert label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Preferred
    assert label.contentsRect().width() >= label.fontMetrics().horizontalAdvance(label.text())
    label_rect = QRect(label.mapTo(page, QPoint()), label.size())
    input_rect = QRect(page.fmt_edit.mapTo(page, QPoint()), page.fmt_edit.size())
    assert label_rect.right() < input_rect.left()
    assert not label_rect.intersects(input_rect)
    assert label_rect.top() <= input_rect.bottom() and input_rect.top() <= label_rect.bottom()
    minimum = label.minimumWidth()
    for _ in range(5):
        page._arrange_toolbar()
    assert label.minimumWidth() == minimum
