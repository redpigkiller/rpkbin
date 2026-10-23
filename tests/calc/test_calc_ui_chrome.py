"""UI language, accessible icon controls and display-density regressions."""

import json
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton, QTextBrowser, QWidget

from rpkbin.calc.controller import CalcController
from rpkbin.calc.core import CalcConfig, WorkspaceSession
from rpkbin.calc.ui.app import CalcMainWindow
from rpkbin.calc.ui.i18n import tr
from rpkbin.calc.ui.icons import make_icon
from rpkbin.calc.ui.settings_dialog import SettingsDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    # Widget-only verification; real local-kernel behavior has its own suite.
    monkeypatch.setattr(CalcController, "start", lambda self: None)
    controller = CalcController(tmp_path / "settings.json")
    view = CalcMainWindow(controller)
    app.processEvents()
    yield view
    view.close()
    app.processEvents()


def test_legacy_language_config_keeps_unicode_user_content(tmp_path):
    path = tmp_path / "legacy.json"
    source = "# 使用者自己的程式\n數值 = 42"
    path.write_text(json.dumps({"language": "zh_TW", "startup_script": source,
                                "ui_font_family": "微軟正黑體"}), encoding="utf-8")
    cfg = CalcConfig.load(path)
    assert cfg.language == "zh_TW"
    assert cfg.startup_script == source
    assert cfg.ui_font_family == "微軟正黑體"
    assert tr("settings_tooltip", "zh_TW") != tr("settings_tooltip", "en_US")


@pytest.mark.parametrize("ratio", [1.0, 1.5, 2.0, 3.0])
def test_svg_icon_has_pixels_for_device_ratio(app, ratio):
    pixmap = make_icon("undo", "#94a3b8", 16).pixmap(QSize(16, 16), ratio)
    assert pixmap.width() >= round(16 * ratio)
    assert pixmap.deviceIndependentSize().width() == pytest.approx(16)
    image = pixmap.toImage()
    alphas = [image.pixelColor(x, y).alpha() for y in range(image.height()) for x in range(image.width())]
    assert max(alphas) > 0
    assert min(alphas) == 0


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_icon_controls_have_accessible_names_and_keyboard_actions(window, app, theme):
    window.controller.update_config(replace(window.controller.config, theme=theme))
    controls = [button for button in window.findChildren(QToolButton)
                if button.property("calcRole") == "iconButton"]
    assert len(controls) >= 15
    for button in controls:
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.accessibleName() and button.toolTip()
        assert not button.icon().isNull()
        assert button.iconSize().width() < button.width()
        assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
    triggered = []
    window.undo_btn.clicked.connect(lambda: triggered.append(True))
    window.undo_btn.setEnabled(True)
    QTest.keyClick(window.undo_btn, Qt.Key.Key_Space)
    assert triggered == [True]


def test_english_chrome_and_unicode_inspector_target(window, app):
    dialog = SettingsDialog(window.controller.config, window)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Appearance", "FXP format", "Auto-import", "Preferences"]
    window.console_widget.show_hints()
    app.processEvents()
    for parent in (window, dialog):
        # Font names and user-entered content are intentionally outside the copy check.
        for widget in parent.findChildren(QWidget):
            if widget in (dialog.path_edit, dialog.startup_path_edit):
                # User filesystem paths can contain any language.
                continue
            if not isinstance(widget, (QLabel, QToolButton, QLineEdit)):
                continue
            for text in (widget.toolTip(), widget.text()):
                assert not any("\u4e00" <= char <= "\u9fff" for char in text), text
    session = WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)
    session.execute("數值 = 42")
    window.inspector_widget.render_snapshot(session.get_inspector_snapshot("數值"))
    assert window.inspector_widget.target_badge.toolTip() == "數值"
    assert window.inspector_widget.card_real.title_lbl.text() == "Value"
    dialog.close()


def test_large_font_does_not_force_main_window_beyond_requested_width(window, app):
    cfg = replace(window.controller.config, ui_font_size=16, code_font_size=16, ui_scale=160)
    window.controller.update_config(cfg)
    window.resize(1320, 900)
    window.show()
    app.processEvents()
    assert window.minimumSizeHint().width() <= 1320
    assert window.width() == 1320
    assert window.console_widget.interrupt_btn.isVisible()
    assert window.inspector_widget.title_lbl.isVisible()


def test_number_display_help_explains_modes_without_kernel_mutation(window, app):
    window.display_help_action.trigger()
    app.processEvents()
    text = window._display_help_text.toPlainText()
    assert "1.23456789" in text and "1.234568" in text
    assert "significant digits" in text
    assert "copied values and raw bits keep their original precision" in text
    before = window.controller.transport.client
    window.controller.update_config(replace(window.controller.config, numeric_precision=2))
    window.display_help_action.trigger()
    assert "1.23" in window._display_help_text.toPlainText()
    assert "1.23E+0" in window._numeric_actions["scientific"].toolTip()
    assert window.controller.transport.client is before
    assert not window.controller._mutations


def test_stop_has_visible_label_and_explains_when_it_is_available(window):
    console = window.console_widget
    assert console.interrupt_btn.text() == "Idle"
    assert console.interrupt_btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    assert not console.interrupt_btn.isEnabled()
    assert "idle" in console.interrupt_btn.toolTip().lower()
    window.controller.transport.busy_changed.emit(True)
    assert console.interrupt_btn.isEnabled()
    assert "Request an interrupt" in console.interrupt_btn.toolTip()
    window.controller.transport.busy_changed.emit(False)
    assert not console.interrupt_btn.isEnabled()


def test_notation_settings_and_hints_explain_native_api_and_gui_scope(window, app):
    dialog = SettingsDialog(window.controller.config, window)
    assert set(dialog._radio_map) == {"q_format", "matlab", "custom"}
    assert dialog._radio_map["q_format"].isChecked()
    assert "Q4.12" in dialog.preview_lbl.text()
    assert "Q4.12" not in window.active_fmt_badge.text()
    assert "GUI FXP edits" in window.active_fmt_badge.text()
    assert "Native APyTypes" in window.active_fmt_badge.toolTip()
    window.console_widget.show_hints()
    text = window.console_widget._hint_dialog.findChild(QTextBrowser).toPlainText()
    assert "APyFixed.from_float" in text and "APyFixed(7, 4, 2)" in text
    assert "fxp(" not in text and "s10.12f" not in text
    dialog.close()


def test_custom_display_stays_out_of_inspector_and_matrix_format_editors(window, app):
    cfg = replace(window.controller.config, format_preset="custom", custom_signed_tpl="signed:{wl}/{fwl}")
    window.controller.update_config(cfg)
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    assert not session.execute("a = APyFixed.from_float(.25, bits=16, frac_bits=12)\n"
                               "A = APyFixedArray.from_float([[.25]], bits=16, frac_bits=12)").error_text
    scalar = session.get_inspector_snapshot("a")
    assert scalar.format_str == "signed:16/12"
    window.inspector_widget.render_snapshot(scalar)
    assert window.inspector_widget.fmt_edit.text() == "Q4.12"
    window._open_or_focus_matrix_tab("A")
    window._matrix_tabs["A"].render_snapshot(session.get_matrix_snapshot("A"))
    assert window._matrix_tabs["A"].fmt_edit.text() == "Q4.12"
