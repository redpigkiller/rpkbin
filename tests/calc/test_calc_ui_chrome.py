"""UI language, accessible icon controls and display-density regressions."""

import json
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton, QWidget

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


def test_missing_code_font_falls_back_to_monospace_without_changing_preferences(window, app):
    cfg = replace(window.controller.config, code_font_family="rpkbin-nonexistent-font-family")
    window.controller.update_config(cfg)
    app.processEvents()
    metrics = QFontMetricsF(window.console_widget.term._control.font())
    assert metrics.horizontalAdvance("iiii") == pytest.approx(metrics.horizontalAdvance("WWWW"))
    assert window.controller.config.code_font_family == "rpkbin-nonexistent-font-family"


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
    text = window.console_widget._hint_pages["fxp"].toPlainText()
    assert "APyFixed.from_float" in text and "APyFixed(7, 4, 2)" in text
    assert "fxp(" not in text and "s10.12f" not in text
    dialog.close()


def test_reversible_custom_display_is_used_by_inspector_and_matrix_format_editors(window, app):
    cfg = replace(window.controller.config, format_preset="custom", custom_signed_tpl="signed:{wl}/{fwl}")
    window.controller.update_config(cfg)
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    assert not session.execute("a = APyFixed.from_float(.25, bits=16, frac_bits=12)\n"
                               "A = APyFixedArray.from_float([[.25]], bits=16, frac_bits=12)").error_text
    scalar = session.get_inspector_snapshot("a")
    assert scalar.format_str == "signed:16/12"
    window.inspector_widget.render_snapshot(scalar)
    assert window.inspector_widget.fmt_edit.text() == "signed:16/12"
    window._open_or_focus_matrix_tab("A")
    window._matrix_tabs["A"].render_snapshot(session.get_matrix_snapshot("A"))
    assert window._matrix_tabs["A"].fmt_edit.text() == "signed:16/12"


def _contrast(foreground, background):
    from PySide6.QtGui import QColor
    def luminance(color):
        c=QColor(color)
        values=[v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4
                for v in (c.redF(),c.greenF(),c.blueF())]
        return sum(v*w for v,w in zip(values,(0.2126,0.7152,0.0722)))
    high,low=sorted((luminance(foreground),luminance(background)),reverse=True)
    return (high+0.05)/(low+0.05)


@pytest.mark.parametrize("theme", ["light","dark"])
def test_action_and_card_colors_retain_text_contrast_and_visible_primary_fill(app, theme):
    from PySide6.QtWidgets import QPushButton, QVBoxLayout
    from rpkbin.calc.ui.theme import build_stylesheet, get_palette, representation_colors, set_role
    cfg=CalcConfig(theme=theme)
    palette=get_palette(theme)
    assert _contrast(palette.primary_fg,palette.primary_bg)>=4.5
    assert _contrast(palette.primary_bg,palette.bg_panel)>=4.5
    colors=representation_colors(palette)
    assert len(set(colors.values()))==6
    assert all(_contrast(fg,bg)>=4.5 for fg,bg in colors.values())
    holder=QWidget()
    holder.setStyleSheet(build_stylesheet(palette,cfg))
    layout=QVBoxLayout(holder)
    apply=QPushButton("Apply")
    cancel=QPushButton("Cancel")
    set_role(apply,"primary")
    layout.addWidget(apply)
    layout.addWidget(cancel)
    holder.show()
    app.processEvents()
    assert apply.grab().toImage().pixelColor(7,apply.height()//2).name()==palette.primary_bg
    assert cancel.grab().toImage().pixelColor(7,cancel.height()//2).name()!=palette.primary_bg
    holder.close()


def test_grid_is_new_default_and_left_button_and_saved_list_preference_still_works(window,app,tmp_path):
    assert CalcConfig().inspector_view_mode=="grid"
    view=window.inspector_widget
    assert view.grid_mode_btn.isChecked()
    window.resize(1200,850)
    window.show()
    app.processEvents()
    assert view.grid_mode_btn.x()<view.list_mode_btn.x()
    view.resize(240,500)
    view._layout_inspector_header()
    assert view.grid_mode_btn.x()<view.list_mode_btn.x()
    saved=tmp_path/"layout.json"
    CalcConfig(inspector_view_mode="list").save(saved)
    assert CalcConfig.load(saved).inspector_view_mode=="list"
    assert view.apply_conversion_btn.property("calcRole")=="primary"
    assert view.fmt_lbl.property("calcRole")=="sectionLabel"
    assert view.bit_title_lbl.property("calcRole")=="sectionLabel"
    assert {card.property("representation") for card in view._cards}=={"real","hex","s2","uint","c1","bin"}


@pytest.mark.parametrize("theme",["light","dark"])
def test_real_confirmation_uses_widget_dialog_and_colored_apply(window,app,theme):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    from rpkbin.calc.ui.i18n import ask
    from rpkbin.calc.ui.theme import get_palette
    window.controller.update_config(replace(window.controller.config,theme=theme))
    captured=[]
    def inspect():
        box=app.activeModalWidget()
        try:
            apply=box.button(QMessageBox.StandardButton.Apply)
            native_option = getattr(getattr(QMessageBox, "Option", None), "DontUseNativeDialog", None)
            captured.append((box.icon(),apply.property("calcRole"),
                             apply.grab().toImage().pixelColor(7,apply.height()//2).name(),
                             (box.testOption(native_option) if native_option is not None else True)))
        finally:
            box.button(QMessageBox.StandardButton.Cancel).click()
    QTimer.singleShot(30,inspect)
    answer=ask(window,"Conversion preview","Review before applying.",
               QMessageBox.StandardButton.Apply|QMessageBox.StandardButton.Cancel,
               QMessageBox.StandardButton.Cancel,icon=QMessageBox.Icon.Information)
    assert answer==QMessageBox.StandardButton.Cancel
    assert captured==[(QMessageBox.Icon.Information,"primary",get_palette(theme).primary_bg,True)]


def test_columns_and_preview_have_visible_short_labels(window):
    assert window.workspace_widget.cols_btn.toolButtonStyle()==Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    assert window.workspace_widget.cols_btn.text()=="Columns"
    view=window.inspector_widget
    assert view.preview_btn.toolButtonStyle()==Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    assert view.preview_btn.text()=="Preview"


@pytest.mark.parametrize("scale", [100, 125, 160])
def test_interface_and_value_fonts_use_requested_points_with_cjk_fallback(window, app, scale):
    cfg = replace(window.controller.config, ui_font_size=12, code_font_size=10, ui_scale=scale)
    window.controller.update_config(cfg)
    dialog = SettingsDialog(cfg, window)
    dialog.show()
    app.processEvents()
    for control in (dialog.language_combo, dialog.theme_combo, dialog.ui_font_size, dialog.ui_font_combo.lineEdit()):
        assert control.font().pointSizeF() == pytest.approx(12 * scale / 100, abs=.01)
        assert control.font().families()[0] == cfg.ui_font_family
        assert "Microsoft JhengHei UI" in control.font().families()
    assert dialog.signed_tpl_edit.font().pointSizeF() == pytest.approx(10 * scale / 100, abs=.01)
    assert "Microsoft JhengHei UI" in dialog.signed_tpl_edit.font().families()
    assert dialog.font_preview.font().pointSizeF() == pytest.approx(12 * scale / 100, abs=.01)
    assert dialog.code_preview.font().pointSizeF() == pytest.approx(10 * scale / 100, abs=.01)
    assert window.controller.config == cfg
    dialog.close()


@pytest.mark.parametrize("lang", ["en_US", "zh_TW"])
def test_template_fields_explain_sign_tokens_without_changing_templates(window, app, lang):
    cfg = replace(window.controller.config, language=lang)
    dialog = SettingsDialog(cfg, window)
    text = dialog.template_fields_hint.text()
    for token in ("{q_prefix}", "Q / UQ", "{s_or_u}", "s / u", "{S_or_U}", "S / U"):
        assert token in text
    assert dialog.template_fields_hint.property("calcRole") == "settingsHint"
    assert dialog.signed_tpl_edit.text() == cfg.custom_signed_tpl
    assert dialog.unsigned_tpl_edit.text() == cfg.custom_unsigned_tpl
    assert dialog.int_tpl_edit.text() == cfg.custom_int_tpl
    dialog.close()


@pytest.mark.parametrize("direction", ["h", "v"])
@pytest.mark.parametrize("side", [0, 1])
def test_splitter_grip_remains_visible_when_a_panel_is_dragged_closed(window, app, direction, side):
    window.resize(1300, 850)
    window.show()
    app.processEvents()
    splitter = window.h_splitter if direction == "h" else window.v_splitter
    sizes = [500, 500]
    sizes[side] = 0
    splitter.setSizes(sizes)
    app.processEvents()
    handle = splitter.handle(1)
    assert splitter.sizes()[side] == 0
    assert handle.isVisible() and handle.width() > 0 and handle.height() > 0
    assert "reopen" in handle.toolTip()
    image = handle.grab().toImage()
    # The grip differs from its background, including at a completely collapsed edge.
    center = image.pixelColor(image.width() // 2, image.height() // 2)
    edge = image.pixelColor(0, 0)
    assert center != edge
    splitter.setSizes([500, 500])
    app.processEvents()
    assert all(size > 0 for size in splitter.sizes())


def test_workspace_filters_use_ui_font_equal_height_and_explicit_count(window, app):
    cfg = replace(window.controller.config, language="zh_TW", keep_english_terms=True,
                  ui_font_size=13, code_font_size=9)
    window.controller.update_config(cfg)
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    session.user_ns.update(a=2, b=3)
    workspace = window.workspace_widget
    workspace.render_rows(session.list_variables())
    window.show()
    for _ in range(4):
        app.processEvents()
    assert workspace.name_filter.font().pointSizeF() == 13
    assert workspace.name_filter.font().families()[0] == cfg.ui_font_family
    assert workspace.name_filter.height() == workspace.type_filter.height()
    assert workspace.name_filter.placeholderText() == "Filter variable names"
    assert [workspace.type_filter.itemText(i) for i in range(3)] == ["All types", "Arrays", "Scalars"]
    assert workspace.filter_count.text() == "2 / 2 shown"
    old_width = workspace.type_filter.width()
    workspace.type_filter.setCurrentIndex(workspace.type_filter.findData("native"))
    for _ in range(3):
        app.processEvents()
    assert workspace.type_filter.width() > old_width
    workspace.name_filter.setText("a")
    assert workspace.filter_count.text() == "1 / 2 shown"
    window.controller.update_config(replace(cfg, keep_english_terms=False))
    assert workspace.name_filter.text() == "a"
    assert workspace.type_filter.currentData() == "native"
    assert workspace.filter_count.text() == "顯示 1 / 2 個"


def test_integer_template_guidance_is_conditional_and_does_not_rewrite_user_input(window):
    cfg = replace(window.controller.config, format_preset="custom", custom_int_tpl="u{wl}.0i")
    dialog = SettingsDialog(cfg, window)
    assert not dialog.integer_template_hint.isHidden()
    assert dialog.int_tpl_edit.text() == "u{wl}.0i"
    dialog.int_tpl_edit.setText("{s_or_u}{wl}.0i")
    assert dialog.integer_template_hint.isHidden()
    dialog.close()


def test_custom_import_and_startup_preview_have_independent_scrollbars(window, app):
    dialog = SettingsDialog(window.controller.config, window)
    dialog.tabs.setCurrentIndex(2)
    dialog.resize(820, 680)
    dialog.custom_imports_edit.setPlainText("\n".join(f"import math  # line {i}" for i in range(300)))
    dialog.startup_store.path.write_text("\n".join(f"# helper note {i}" for i in range(300)), encoding="utf-8")
    dialog._refresh_startup_preview()
    dialog.show()
    for _ in range(6):
        app.processEvents()
    for editor in (dialog.custom_imports_edit, dialog.startup_edit):
        bar = editor.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum())
        assert bar.value() > 0
    assert dialog.tabs.widget(2).verticalScrollBar().maximum() > 0
    assert not dialog.custom_imports_edit.isReadOnly()
    assert dialog.startup_edit.isReadOnly()
    dialog.close()


@pytest.mark.parametrize("language", ["en_US", "zh_TW"])
@pytest.mark.parametrize("font_size,scale", [(10, 100), (13, 100), (16, 160), (24, 160)])
def test_conversion_initial_size_fits_final_width_before_platform_show(window, app, language, font_size, scale):
    from rpkbin.calc.ui.conversion_dialog import FixedPointConversionDialog
    cfg = replace(window.controller.config, language=language, ui_font_size=font_size, ui_scale=scale)
    window.controller.update_config(cfg)
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    session.user_ns["a"] = 2
    window.inspector_widget.render_snapshot(session.get_inspector_snapshot("a"))
    dialog = FixedPointConversionDialog(window.inspector_widget)
    # Check before show(): Windows should not have to enlarge an undersized
    # native window after it has already received the requested geometry.
    dialog.ensurePolished()
    dialog.layout().activate()
    required = max(dialog.minimumSizeHint().height(), dialog.layout().totalHeightForWidth(dialog.width()))
    assert dialog.height() >= required
    assert dialog.width() >= dialog.minimumSizeHint().width()
    initial = dialog.size()
    dialog.show()
    for _ in range(4):
        app.processEvents()
    assert dialog.size() == initial
    assert dialog.rect().contains(dialog.apply_btn.geometry())
    assert dialog.rect().contains(dialog.cancel_btn.geometry())
    dialog.reject()


@pytest.mark.parametrize("language", ["en_US", "zh_TW"])
def test_primary_action_is_left_of_cancel_in_settings_conversion_and_import(window, app, language):
    from dataclasses import asdict
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QPushButton
    from rpkbin.calc.ui.conversion_dialog import FixedPointConversionDialog
    cfg = replace(window.controller.config, language=language)
    window.controller.update_config(cfg)
    settings = SettingsDialog(cfg, window)
    settings.show()
    app.processEvents()
    assert settings.save_btn.x() < settings.cancel_btn.x()
    assert settings.save_btn.nextInFocusChain() is settings.cancel_btn
    before = asdict(settings._draft_config())
    captured = []
    def inspect_import():
        preview = app.activeModalWidget()
        try:
            buttons = preview.findChildren(QPushButton)
            accept = next(b for b in buttons if b.property("calcRole") == "primary")
            cancel = next(b for b in buttons if b is not accept)
            captured.append((accept.x() < cancel.x(), accept.text()))
        finally:
            preview.reject()
    QTimer.singleShot(30, inspect_import)
    settings._show_import_preview({"theme": "light"})
    assert captured == [(True, "Use these settings" if language == "en_US" else "使用這些設定")]
    assert asdict(settings._draft_config()) == before
    settings.reject()
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    session.user_ns["a"] = 2
    window.inspector_widget.render_snapshot(session.get_inspector_snapshot("a"))
    dialog = FixedPointConversionDialog(window.inspector_widget)
    dialog.show()
    app.processEvents()
    assert dialog.apply_btn.x() < dialog.cancel_btn.x()
    assert dialog.apply_btn.nextInFocusChain() is dialog.cancel_btn
    dialog.cancel_btn.click()


@pytest.mark.parametrize("action", ["apply", "cancel", "escape", "enter"])
def test_confirmation_action_order_keeps_standard_results_and_cancel_default(window, app, action):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    from rpkbin.calc.ui.i18n import ask
    captured = []
    def inspect():
        box = app.activeModalWidget()
        apply = box.button(QMessageBox.StandardButton.Apply)
        cancel = box.button(QMessageBox.StandardButton.Cancel)
        captured.append((apply.x() < cancel.x(), box.defaultButton() is cancel))
        if action == "apply":
            apply.click()
        elif action == "cancel":
            cancel.click()
        else:
            QTest.keyClick(box, Qt.Key.Key_Escape if action == "escape" else Qt.Key.Key_Return)
    QTimer.singleShot(30, inspect)
    result = ask(window, "Preview", "Review the conversion.",
                 QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
                 QMessageBox.StandardButton.Cancel)
    assert captured == [(True, True)]
    assert result == (QMessageBox.StandardButton.Apply if action == "apply" else QMessageBox.StandardButton.Cancel)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_help_background_and_console_scroll_thumb_are_distinct_from_splitter(window, app, theme):
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider
    from rpkbin.calc.ui.theme import get_palette
    cfg = replace(window.controller.config, theme=theme)
    window.controller.update_config(cfg)
    window.resize(1300, 850)
    window.show()
    window.console_widget.term._control.setPlainText("\n".join(f"line {i}" for i in range(400)))
    settings = SettingsDialog(cfg, window)
    settings.show()
    for _ in range(5):
        app.processEvents()
    palette = get_palette(theme)
    hint = next(label for label in settings.findChildren(QLabel)
                if label.property("calcRole") == "settingsHint" and label.isVisible())
    assert hint.grab().toImage().pixelColor(5, hint.height()-3).name() == palette.bg_header
    assert palette.bg_header != palette.bg_panel
    assert _contrast(palette.text_secondary, palette.bg_header) >= 4.5
    bar = window.console_widget.term._control.verticalScrollBar()
    assert bar.isVisible() and bar.maximum() > 0
    option = QStyleOptionSlider()
    option.initFrom(bar)
    option.orientation = Qt.Orientation.Vertical
    option.minimum, option.maximum = bar.minimum(), bar.maximum()
    option.pageStep = bar.pageStep()
    option.sliderPosition = option.sliderValue = bar.value()
    thumb = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option,
                                      QStyle.SubControl.SC_ScrollBarSlider, bar)
    thumb_color = bar.grab().toImage().pixelColor(thumb.center()).name()
    handle = window.h_splitter.handle(1)
    separator_color = handle.grab().toImage().pixelColor(handle.width()//2, 5).name()
    assert thumb_color == palette.text_muted
    assert separator_color == palette.bg_main
    assert thumb_color != separator_color
    settings.reject()
