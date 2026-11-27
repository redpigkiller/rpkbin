"""Phase 4 regression tests for Unit 9 Calc UI fixes (U9-01..U9-09, U9G-01..U9G-05, U8-03, U8-15)."""

from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QToolButton,
)

from rpkbin.app.calc.controller import CalcController
from rpkbin.app.calc.core import CalcConfig, WorkspaceSession, make_fxp
from rpkbin.app.calc.ui.app import CalculatorWindow
from rpkbin.app.calc.ui.i18n import tr
from rpkbin.app.calc.ui.inspector_widget import VariableInspectorWidget
from rpkbin.app.calc.ui.matrix_editor import MatrixEditorPage
from rpkbin.app.calc.ui.settings_dialog import SettingsDialog
from rpkbin.app.calc.ui.simple_calc_widget import ClickableBaseEdit, SimpleCalcWidget
from rpkbin.app.calc.ui.studio_window import CalcMainWindow
from rpkbin.app.calc.ui.theme import DARK_PALETTE, LIGHT_PALETTE, build_stylesheet, get_palette, scaled_px
from rpkbin.app.calc.ui.workspace_widget import WorkspaceWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _rel_luminance(hex_color: str) -> float:
    color = QColor(hex_color)
    channels = []
    for channel in (color.redF(), color.greenF(), color.blueF()):
        channels.append(channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(fg: str, bg: str) -> float:
    l1 = _rel_luminance(fg)
    l2 = _rel_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture
def stub_controller(tmp_path, monkeypatch):
    monkeypatch.setattr(CalcController, "start", lambda self: None)
    return CalcController(tmp_path / "settings.json")


def test_u9_01_matrix_page_deleted_console_executed(app, stub_controller):
    win = CalcMainWindow(stub_controller)
    session = WorkspaceSession(stub_controller.config, seed_demo_vars=False)
    session.user_ns["A"] = np.zeros((2, 2))
    snap = session.get_matrix_snapshot("A")
    stub_controller._snapshots = {"A": snap}
    win._open_or_focus_matrix_tab("A")
    app.processEvents()
    idx = win.left_tabs.indexOf(win._matrix_tabs["A"])
    assert idx >= 1
    win._on_tab_close_requested(idx)
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    # Must not raise RuntimeError on dangling QLineEdit references
    stub_controller.console_executed.emit("1 + 1")
    win.close()


def test_u9_02_u9g_02_simple_event_filter_copy_escape_return(app, tmp_path, monkeypatch):
    monkeypatch.setattr(CalcController, "start", lambda self: None)
    cfg = CalcConfig(language="en_US", startup_script="")
    cfg_path = tmp_path / "simple_cfg.json"
    cfg.save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        calc.paste("0x12")
        app.processEvents()

        # 1. Ctrl+C on selected read-only ClickableBaseEdit copies HEX text, not Decimal hero
        hex_edit = calc.row_frames["HEX"].findChild(ClickableBaseEdit)
        assert hex_edit is not None
        hex_edit.setFocus()
        hex_edit.selectAll()
        app.processEvents()
        QTest.keyClick(hex_edit, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert QGuiApplication.clipboard().text().strip().lower() == hex_edit.selectedText().strip().lower()

        # 2. Escape closes open fxp_drawer without clearing engine.raw_uint to 0
        assert calc.engine.raw_uint != 0
        before_raw = calc.engine.raw_uint
        calc._toggle_fxp_drawer(0)
        app.processEvents()
        assert calc.fxp_drawer.isVisible()
        QTest.keyClick(calc.fxp_drawer, Qt.Key.Key_Escape)
        app.processEvents()
        assert not calc.fxp_drawer.isVisible()
        assert calc.engine.raw_uint == before_raw

        # 3. Escape closes open add_var_box without clearing engine.raw_uint
        calc._toggle_add_var_box()
        app.processEvents()
        assert calc.add_var_box.isVisible()
        QTest.keyClick(calc.new_var_input, Qt.Key.Key_Escape)
        app.processEvents()
        assert not calc.add_var_box.isVisible()
        assert calc.engine.raw_uint == before_raw

        # 4. Return on topbar button or drawer button must not execute OperandEdit.equals()
        calls = []
        orig_equals = calc.equals
        calc.equals = lambda: calls.append("equals")
        try:
            win.settings_button.setFocus()
            app.processEvents()
            QTest.keyClick(win.settings_button, Qt.Key.Key_Return)
            app.processEvents()
            assert calls == []

            calc._toggle_fxp_drawer(0)
            app.processEvents()
            calc.cfg_apply_btn.setFocus()
            app.processEvents()
            QTest.keyClick(calc.cfg_apply_btn, Qt.Key.Key_Return)
            app.processEvents()
            assert calls == []
        finally:
            calc.equals = orig_equals
    finally:
        win.close()


def test_u9g_02_focus_qss_distinct_from_checked():
    for palette in (DARK_PALETTE, LIGHT_PALETTE):
        qss = build_stylesheet(palette)
        assert ":checked:focus" in qss
        # Distinct focus color/width exists after checked styling
        checked_pos = qss.find('QPushButton[calcRole="segmentBtn"]:checked')
        focus_pos = qss.find('QPushButton[calcRole="segmentBtn"]:checked:focus')
        assert checked_pos >= 0 and focus_pos > checked_pos


def test_u9_03_u9g_03_workspace_theme_refresh_and_legend_and_wcag(app, stub_controller):
    # 1. Exact WCAG 2.1 contrast thresholds for both palettes
    for palette in (DARK_PALETTE, LIGHT_PALETTE):
        assert palette.border_control
        for bg in (palette.bg_main, palette.bg_panel, palette.bg_header, palette.bg_hover, palette.bg_selected):
            for role_name in ("text_primary", "text_secondary", "text_muted", "accent", "danger", "warning", "success"):
                fg = getattr(palette, role_name)
                ratio = _contrast(fg, bg)
                assert ratio >= 4.5, f"{palette.name} {role_name}={fg} on {bg} contrast={ratio:.2f}"
        for role_name in ("accent", "danger", "warning", "success"):
            fg = getattr(palette, role_name)
            bg = getattr(palette, f"{role_name}_bg")
            assert _contrast(fg, bg) >= 4.5, f"{palette.name} {role_name} on {role_name}_bg"
        for bg in (palette.bg_input, palette.bg_panel, palette.bg_main):
            assert _contrast(palette.border_control, bg) >= 3.0, f"{palette.name} border_control on {bg}"

    # 2. Switching Dark -> Light updates WorkspaceWidget item foregrounds
    stub_controller.config = CalcConfig(theme="dark", startup_script="")
    ws = WorkspaceWidget(stub_controller)
    session = WorkspaceSession(stub_controller.config, seed_demo_vars=False)
    session.user_ns["sat_v"] = make_fxp(1.9, "Q4.4")
    ws.render_rows(session.list_variables())
    app.processEvents()
    fmt_item_dark = ws.table.item(0, 2)
    assert fmt_item_dark is not None
    dark_fg = fmt_item_dark.foreground().color().name()

    light_cfg = replace(stub_controller.config, theme="light")
    stub_controller.config = light_cfg
    ws.apply_appearance(light_cfg)
    app.processEvents()
    fmt_item_light = ws.table.item(0, 2)
    assert fmt_item_light is not None
    assert fmt_item_light.foreground().color().name() != dark_fg
    ws.close()

    # 3. _CompactLegend uses palette colors dynamically
    sc = SimpleCalcWidget(CalcConfig(theme="light"))
    light_p = get_palette("light")
    legend_dots = [lbl.styleSheet() for lbl in sc.findChildren(QLabel) if light_p.danger in lbl.styleSheet()]
    assert legend_dots, "Compact legend should use light palette colors"
    sc.close()


def test_u9_04_inspector_empty_badge_hidden(app, stub_controller):
    insp = VariableInspectorWidget(stub_controller)
    session = WorkspaceSession(stub_controller.config, seed_demo_vars=False)
    v = make_fxp(2.0, "Q4.4")
    v._saturated = True
    session.user_ns["sat_v"] = v
    insp.render_snapshot(session.get_inspector_snapshot("sat_v"))
    app.processEvents()
    assert not insp.status_badge.isHidden()

    insp.render_snapshot(None)
    app.processEvents()
    assert insp.status_badge.isHidden()
    assert insp.status_badge.property("statusKind") == ""
    insp.close()


def test_u9_05_inspector_bitgrid_radix_cache_key(app, stub_controller):
    insp = VariableInspectorWidget(stub_controller)
    insp.resize(640, 480)
    insp.show()
    session = WorkspaceSession(stub_controller.config, seed_demo_vars=False)
    session.user_ns["v"] = make_fxp(1.25, "Q4.4")
    insp.render_snapshot(session.get_inspector_snapshot("v"))
    app.processEvents()
    w4_col4 = insp.bit_grid_layout.columnMinimumWidth(4)

    session.user_ns["v"] = make_fxp(1.25, "Q5.3")
    insp.render_snapshot(session.get_inspector_snapshot("v"))
    app.processEvents()
    w5_col4 = insp.bit_grid_layout.columnMinimumWidth(4)
    assert w5_col4 > 0
    assert w4_col4 != w5_col4

    session.user_ns["v"] = make_fxp(1.25, "Q4.4")
    insp.render_snapshot(session.get_inspector_snapshot("v"))
    app.processEvents()
    assert insp.bit_grid_layout.columnMinimumWidth(4) == w4_col4
    insp.close()


def test_u9_06_u8_15_matrix_modified_reset_and_failed_tile(app, stub_controller, monkeypatch):
    monkeypatch.setattr(CalcController, "is_available", property(lambda self: True))
    fmt_calls = []
    slice_calls = []
    stub_controller.change_variable_format = lambda name, fmt: fmt_calls.append((name, fmt))
    stub_controller.set_array_slice = lambda name, idx: slice_calls.append((name, list(idx)))

    session = WorkspaceSession(stub_controller.config, seed_demo_vars=False)
    session.user_ns["arr"] = make_fxp(np.arange(12).reshape(2, 2, 3), "Q8.8")
    page = MatrixEditorPage("arr", stub_controller)
    page.render_snapshot(session.get_matrix_snapshot("arr"))
    page.show()
    app.processEvents()

    # Invalid format stays inline error and does not call controller
    page.fmt_edit.clear()
    QTest.keyClicks(page.fmt_edit, "invalid_fmt")
    QTest.keyClick(page.fmt_edit, Qt.Key.Key_Return)
    app.processEvents()
    assert fmt_calls == []
    assert not page.error_lbl.isHidden()

    # Valid format clears modified flag so external snapshot update updates fmt_edit
    page.fmt_edit.clear()
    QTest.keyClicks(page.fmt_edit, "Q4.12")
    QTest.keyClick(page.fmt_edit, Qt.Key.Key_Return)
    app.processEvents()
    assert fmt_calls == [("arr", "Q4.12")]
    assert not page.fmt_edit.isModified()

    # Valid slice commit clears modified flag
    page.slice_edit.clear()
    QTest.keyClicks(page.slice_edit, "1")
    QTest.keyClick(page.slice_edit, Qt.Key.Key_Return)
    app.processEvents()
    assert slice_calls == [("arr", [1])]
    assert not page.slice_edit.isModified()

    # U8-15: matrix_page_failed clears pending tile so retries are allowed
    model = page.model
    model._pending.add((0, 0))
    stub_controller.matrix_page_failed.emit("arr", 0, 0, model.revision)
    app.processEvents()
    assert (0, 0) not in model._pending
    page.close()


def test_u9_07_simple_studio_sync(app, tmp_path, monkeypatch):
    monkeypatch.setattr(CalcController, "start", lambda self: None)
    opened_dialogs = []
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: opened_dialogs.append(self.controller) or 0)

    cfg_path = tmp_path / "sync.json"
    CalcConfig(startup_script="").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        win.switch_mode("python")
        app.processEvents()
        assert win.studio is not None

        # 1. Studio Always on Top syncs back to pin_top_btn
        win.studio._toggle_always_on_top(True)
        app.processEvents()
        win.switch_mode("simple")
        app.processEvents()
        assert win.pin_top_btn.isChecked()

        # 2. Non-kernel local config state from SimpleCalcWidget propagates to studio.controller.config
        win.simple._toggle_fxp_drawer(0)
        win.simple.cfg_syntax_edit.setText("Q4.12")
        win.simple._apply_fxp_drawer()
        app.processEvents()
        assert win.studio.controller.config.simple_last_fxp == "Q4.12"

        # 3. open_settings() in simple mode passes studio.controller when studio exists
        win.open_settings()
        assert opened_dialogs and opened_dialogs[-1] is win.studio.controller

        # 4. studio.reset_layout() resizes parent CalculatorWindow to (1320, 820)
        win.resize(960, 620)
        win.studio.reset_layout()
        app.processEvents()
        assert (win.width(), win.height()) == (1320, 820)
    finally:
        win.close()


def test_u9_08_zh_tw_translations(app):
    # Stale settings key fixed
    msg_src = (
        "Available names follow the enabled Auto-import groups.\n"
        "Startup code runs at launch/restart or when you choose Reload and run.\n"
        "Saving alone does not run it. Imports and functions stay out of Workspace."
    )
    assert tr(msg_src, "zh_TW") != msg_src

    sc = SimpleCalcWidget(CalcConfig(language="zh_TW"))
    assert sc.cfg_signed_combo.itemText(0) != "Signed"
    assert sc.cfg_signed_combo.itemText(1) != "Unsigned"
    btn_texts = [b.text() for b in sc.findChildren(QPushButton)]
    assert "~ Invert" not in btn_texts
    assert "0 Zero" not in btn_texts
    assert "MSB↔LSB" not in btn_texts
    read_btns = [b for b in sc.findChildren(QPushButton) if b.toolTip() or b.accessibleName()]
    assert all(b.accessibleName() != "Read" and b.accessibleName() != "Write" for b in read_btns)
    sc.close()


def test_u9_09_u9g_05_responsive_16pt_160_and_studio_480(app, tmp_path, monkeypatch):
    monkeypatch.setattr(CalcController, "start", lambda self: None)
    # 1. Simple 16pt / 160% inside 776x764
    cfg_path = tmp_path / "big.json"
    CalcConfig(ui_font_size=16, code_font_size=16, ui_scale=160, startup_script="").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.resize(776, 764)
    win.show()
    app.processEvents()
    try:
        assert win.minimumWidth() <= 776
        assert win.width() <= 776
        calc = win.simple
        assert calc.left_pane.width() <= 776
        for child in calc.mid_bar.findChildren(QPushButton) + calc.mid_bar.findChildren(QToolButton):
            if child.isVisibleTo(calc.mid_bar):
                pos = child.mapTo(calc.mid_bar, QPoint(0, 0))
                assert pos.x() + child.width() <= calc.mid_bar.width() + 1
        for btn in calc._keypad_main_btns + calc._keypad_op_btns:
            if btn.isVisibleTo(calc.left_pane):
                pos = btn.mapTo(calc.left_pane, QPoint(0, 0))
                assert pos.x() + btn.width() <= calc.left_pane.width() + 1
        if calc.sidebar.isVisibleTo(win):
            pos = calc.sidebar.mapTo(win, QPoint(0, 0))
            assert pos.x() + calc.sidebar.width() <= win.width() + 1
        # Top bar and base label buttons must not clip at 16pt / 160%
        bar_w, bar_h = win.top_bar.width(), win.top_bar.height()
        assert bar_h >= win.top_bar.layout().heightForWidth(bar_w)
        assert win.brand_label.width() >= win.brand_label.sizeHint().width()
        for key, mode_btn in win.mode_buttons.items():
            assert mode_btn.width() >= mode_btn.sizeHint().width(), key
            pos = mode_btn.mapTo(win.top_bar, QPoint(0, 0))
            assert pos.x() + mode_btn.width() <= bar_w + 1
            assert pos.y() + mode_btn.height() <= bar_h + 1
        assert win.toggle_side_btn.width() == win.toggle_side_btn.height()
        assert win.toggle_side_btn.width() >= scaled_px(28, win.config)
        for btn in (win.toggle_side_btn, win.settings_button, win.pin_top_btn):
            pos = btn.mapTo(win.top_bar, QPoint(0, 0))
            assert pos.x() >= 0 and pos.x() + btn.width() <= bar_w + 1
            assert pos.y() >= 0 and pos.y() + btn.height() <= bar_h + 1
        for base_key, (lbl_btn, _) in calc.rows.items():
            assert lbl_btn.width() >= lbl_btn.sizeHint().width(), base_key
    finally:
        win.close()

    # Preserve 10pt/100% side-by-side at 560x460
    cfg10_path = tmp_path / "std.json"
    CalcConfig(ui_font_size=10, ui_scale=100, startup_script="").save(cfg10_path)
    win10 = CalculatorWindow("simple", config_path=cfg10_path)
    win10.resize(560, 460)
    win10.show()
    app.processEvents()
    try:
        assert win10.simple.sidebar.isVisibleTo(win10)
        assert win10.simple.left_pane.width() < 390
    finally:
        win10.close()

    # 2. Standalone CalcMainWindow at (480, 700)
    ctrl = CalcController(tmp_path / "studio480.json")
    session = WorkspaceSession(ctrl.config, seed_demo_vars=False)
    v = make_fxp(1.9, "Q4.4")
    v._saturated = True
    session.user_ns["sat_v"] = v
    studio = CalcMainWindow(ctrl)
    studio.resize(480, 700)
    studio.show()
    studio.inspector_widget.render_snapshot(session.get_inspector_snapshot("sat_v"))
    app.processEvents()
    try:
        assert studio.width() == 480
        insp = studio.inspector_widget
        assert insp.body_scroll.horizontalScrollBar().maximum() == 0
        vp_w = insp.body_scroll.viewport().width()
        for w in (insp.convert_btn, insp.fmt_edit, insp.preview_btn, insp.apply_conversion_btn):
            pos = w.mapTo(insp.body_scroll.viewport(), QPoint(0, 0))
            assert pos.x() >= 0 and pos.x() + w.width() <= vp_w + 1
    finally:
        studio.close()

    # 3. WorkspaceWidget FlowLayout at 480x700 under 12pt/125% and 16pt/160% with 'Python / NumPy'
    for font_pt, scale_pct in ((12, 125), (16, 160)):
        cfg_ws = CalcConfig(ui_font_size=font_pt, code_font_size=font_pt, ui_scale=scale_pct, startup_script="")
        ctrl_ws = CalcController(tmp_path / f"studio_{font_pt}_{scale_pct}.json")
        ctrl_ws.config = cfg_ws
        studio_ws = CalcMainWindow(ctrl_ws)
        studio_ws.resize(480, 700)
        studio_ws.show()
        ws = studio_ws.workspace_widget
        ws.type_filter.setCurrentIndex(4)  # "Python / NumPy"
        app.processEvents()
        try:
            fh = ws.filter_host
            assert fh.height() >= fh.layout().heightForWidth(fh.width())
            assert ws.type_filter.width() <= fh.width()
            for child in (ws.name_filter, ws.type_filter, ws.filter_count):
                r = child.geometry()
                assert r.x() >= 0 and r.y() >= 0
                assert r.right() < fh.width() + 1
                assert r.bottom() < fh.height() + 1
            header = ws.title_lbl.parentWidget()
            assert header.height() >= header.layout().heightForWidth(header.width())
        finally:
            studio_ws.close()


def test_u8_03_simple_shift_count_unscaled(app):
    sc = SimpleCalcWidget(CalcConfig())
    try:
        sc.format_combo.setCurrentText("Q4.4")
        sc._format_entered()
        sc.select_base("FXP")
        sc.paste("1")
        sc._operator("<<")
        sc._append("2")
        sc.equals()
        # In Q4.4 FXP mode, 1.0 is raw 16; shifting left by unscaled integer count 2 -> 64 (4.0)
        assert sc.engine.raw_uint == 64

        sc.paste("1")
        sc._operator("<<")
        sc._append("2")
        sc._operator("+")
        sc._append("1")
        sc.equals()
        # _push_literal_token after << must also keep count unscaled (2 + 1 = 3 -> 16 << 3 = 128)
        assert sc.engine.raw_uint == 128
    finally:
        sc.close()


def test_u9g_01_accessible_names_buddies_group_rename(app, stub_controller):
    cfg = CalcConfig(custom_import_groups=[{"name": "DSP", "enabled": True, "code": "import math"}])
    dlg = SettingsDialog(cfg, controller=stub_controller)
    dlg.show()
    app.processEvents()
    try:
        for form in dlg.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                lbl_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                if lbl_item and field_item and isinstance(lbl_item.widget(), QLabel) and field_item.widget():
                    assert lbl_item.widget().buddy() is field_item.widget()
                    assert field_item.widget().accessibleName()
        # Custom import group checkbox + keyboard rename path
        for chk in dlg.custom_groups_container.findChildren(QCheckBox):
            assert chk.accessibleName() or chk.text()
        rename_btns = [b for b in dlg.custom_groups_container.findChildren(QToolButton) if "rename" in b.accessibleName().lower()]
        assert rename_btns
    finally:
        dlg.close()

    sc = SimpleCalcWidget(CalcConfig())
    try:
        assert sc.new_var_input.accessibleName()
        assert sc.cfg_syntax_edit.accessibleName()
        assert sc.cfg_signed_combo.accessibleName()
        assert sc.cfg_quant_combo.accessibleName()
        assert sc.cfg_ovf_combo.accessibleName()
        assert sc.var_table.accessibleName()
        close_btns = [b for b in sc.fxp_drawer.findChildren(QPushButton) if b.text() == "✕"]
        assert close_btns and close_btns[0].accessibleName()
        for sym in ("×", "÷", "−", "+", "=", "⌫", "+/-", "<<", ">>", "%", "(", ")", "."):
            btns = [b for b in sc.findChildren(QPushButton) if b.text() == sym]
            assert btns and btns[0].accessibleName(), f"Missing accessibleName for keypad button {sym!r}"
    finally:
        sc.close()


def test_u9g_04_simple_history_tooltip_and_scroll(app):
    sc = SimpleCalcWidget(CalcConfig())
    try:
        sc.paste("12345678")
        sc._operator("+")
        sc._append("87654321")
        sc.equals()
        assert sc.hist_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        cards = sc.hist_cards_host.findChildren(QPushButton)
        assert cards
        assert "12345678" in cards[0].toolTip()
    finally:
        sc.close()
