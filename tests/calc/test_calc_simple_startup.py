import os
import subprocess
import sys
from textwrap import dedent


def test_cli_simple_cold_start(tmp_path):
    code = dedent("""
        import sys
        from pathlib import Path
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from rpkbin.app.calc import cli
        from rpkbin.app.calc.core.config import CalcConfig
        from rpkbin.app.calc.ui import app

        path = Path(sys.argv[1])
        CalcConfig(launch_mode="python").save(path)
        original = app.CalculatorWindow

        class TestWindow(original):
            def __init__(self, mode, **kwargs):
                super().__init__(mode, config_path=path)
                QTimer.singleShot(0, self.exercise)

            def exercise(self):
                try:
                    assert self.mode == "simple" and self.studio is None
                    calc = self.simple
                    calc.input.setText("2+3*4")
                    calc.equals()
                    assert calc.engine.real == 14
                    calc.select_base("HEX")
                    assert calc.input.text() == "0xE"
                    calc._set_panel("bits")
                    calc.bits._buttons[0].click()
                    assert calc.engine.raw_uint == 15
                    calc.bit_range.setText("[5:2]")
                    calc.bit_value.setText("2")
                    calc.write_field()
                    assert calc.engine.raw_uint == 11
                    calc.read_field()
                    assert calc.bit_value.text() == "2"
                    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
                    self.settings_button.click()
                    assert not path.with_name("startup.py").exists()
                    forbidden = {"ipykernel", "qtconsole", "numpy", "matplotlib", "apytypes"}
                    assert forbidden.isdisjoint(name.split(".")[0] for name in sys.modules)
                    assert "rpkbin.app.calc.kernel_client" not in sys.modules
                except BaseException:
                    import traceback
                    traceback.print_exc()
                    QApplication.exit(1)
                else:
                    self.close()

        app.CalculatorWindow = TestWindow
        raise SystemExit(cli.main(["--simple"]))
    """)
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "settings.json")],
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_simple_ui_layout_collapse_and_keyboard_routing(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from dataclasses import replace
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "simple_ui.json"
    CalcConfig(launch_mode="simple").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        # 1. Dynamic format tooltips/placeholders with no hardcoded s16.12
        assert "s16.12" not in calc.format_combo.toolTip()
        assert "s16.12" not in calc.cfg_syntax_edit.placeholderText()
        assert "Q4.4" in calc.format_combo.toolTip()
        win._apply_preferences(replace(win.config, format_preset="matlab"))
        app.processEvents()
        assert "sfix8_En4" in calc.format_combo.toolTip()
        assert "sfix8_En4" in calc.cfg_syntax_edit.placeholderText()

        # Full error visibility with wordWrap
        assert calc.status.wordWrap()
        calc.format_combo.setEditText("invalid_fmt_xyz")
        calc._format_entered()
        assert calc.status.isVisible()
        assert "Enter a Q/MATLAB format" in calc.status.text()

        # 2. Side-by-side layout and aligned columns + unified base scroll
        assert calc.base_container.x() < calc.hero_frame.x()
        assert calc.base_scroll.isVisible()
        for base in ("HEX", "DEC", "OCT", "BIN", "REAL", "FXP"):
            assert calc.base_layout.indexOf(calc.row_frames[base]) >= 0
        for _ in range(6):
            calc._add_compare()
        app.processEvents()
        app.processEvents()
        assert calc.base_scroll.verticalScrollBar().maximum() > 0

        hex_val_x = calc.rows["HEX"][1].x()
        for base in ("DEC", "OCT", "BIN", "REAL", "FXP"):
            assert calc.rows[base][1].x() == hex_val_x
        for _, cmp_val, *_ in calc._comparison_rows:
            assert cmp_val.x() == hex_val_x

        # 3. Clean Collapse mode shrinks and restores window height
        expanded_h = win.height()
        calc._set_panel("none")
        app.processEvents()
        collapsed_h = win.height()
        assert collapsed_h < expanded_h - 150
        calc._set_panel("keypad")
        app.processEvents()
        assert win.height() == expanded_h

        # 4. Keypad NoFocus + automatic keyboard routing to calc.input
        for btn in calc._keypad_main_btns + calc._keypad_aux_btns:
            assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus
        calc.clear()
        calc.ms_btn.setFocus()
        QTest.keyClicks(calc.ms_btn, "7+5")
        assert calc.input.hasFocus()
        assert calc.input.text() == "7+5"
        QTest.keyClick(calc.ms_btn, Qt.Key.Key_Return)
        assert calc.engine.real == 12
    finally:
        win.close()
        app.processEvents()



def test_simple_ui_unified_fxp_format_bound_variables_and_both_height(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.core.format_engine import FxpSpec
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "unified.json"
    CalcConfig(launch_mode="simple").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple

        # 1. Unified row 0 inline FXP edit + mid-bar combo targeting selected FXP row
        calc.primary_fmt_edit.setText("Q4.4")
        calc.primary_fmt_edit.returnPressed.emit()
        assert calc.engine.primary_spec == FxpSpec(True, 8, 4)
        assert calc.format_combo.currentText() == "Q4.4"

        calc._add_compare()
        calc._change_compare(0, "Q8.8")
        calc.select_base("FXP", fxp_idx=1)
        assert calc.format_combo.currentText() == "Q8.8"
        calc.format_combo.setEditText("Q4.12")
        calc._format_entered()
        assert calc.engine.compare_specs[0] == FxpSpec(True, 16, 12)

        # 2. Format-bound variable store & recall
        calc.select_base("HEX")
        calc.paste("0x15")
        calc._store("A")
        assert calc.engine.variables["A"].source_kind == "HEX"

        calc.select_base("REAL")
        calc.paste("1.3")
        calc._store("B")
        assert calc.engine.variables["B"].source_kind == "REAL"

        calc.select_base("FXP", fxp_idx=0)
        calc._store("C")
        assert calc.engine.variables["C"].source_kind == "FXP"

        # Recalling HEX and REAL restores source base and exact value/bits
        calc.paste("9.5")
        calc._recall_variable(calc.engine.variables["A"])
        assert calc.base == "HEX" and calc.engine.raw_uint == 0x15

        calc._recall_variable(calc.engine.variables["B"])
        assert calc.base == "REAL" and calc.input.text() == "1.3"

        # Unset row 0 accepts FXP variable recall directly without prompt
        calc._remove_compare(0)
        calc._apply_primary_fmt_text("")
        assert calc.engine.primary_spec is None
        monkeypatch.setattr(
            calc,
            "_confirm_add_fxp_for_recall",
            lambda _spec: (_ for _ in ()).throw(AssertionError("unexpected confirmation prompt")),
        )
        calc._recall_variable(calc.engine.variables["C"])
        assert calc.engine.primary_spec == FxpSpec(True, 8, 4)
        assert calc.base == "FXP" and calc.selected_fxp_idx == 0

        # Different row 0 format prompts Yes/No before adding a comparison row
        calc._apply_primary_fmt_text("Q8.8")
        monkeypatch.setattr(calc, "_confirm_add_fxp_for_recall", lambda _spec: False)
        calc._recall_variable(calc.engine.variables["C"])
        assert calc.engine.compare_specs == []

        monkeypatch.setattr(calc, "_confirm_add_fxp_for_recall", lambda _spec: True)
        calc._recall_variable(calc.engine.variables["C"])
        assert calc.engine.compare_specs == [FxpSpec(True, 8, 4)]
        assert calc.selected_fxp_idx == 1

        # 3. Compact 'both' mode fits height <= 680 and 'bits' mode fills vertical space
        calc._set_panel("both")
        app.processEvents()
        assert win.height() <= 680
        calc._set_panel("bits")
        app.processEvents()
        assert calc.bit_panel.height() > 250
    finally:
        win.close()
        app.processEvents()



def test_simple_ui_windows_grouping_shift_wheel_click_and_badge_edit(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.core.format_engine import FxpSpec
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "simple_rows.json"
    CalcConfig(launch_mode="simple").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        assert not hasattr(calc, "row_sub_tags")

        # Windows Calculator grouping (no 32b padding)
        calc.paste("0x10015")
        assert calc.rows["HEX"][1].text() == "0x1 0015"
        assert calc.rows["DEC"][1].text() == "65557"
        assert calc.rows["OCT"][1].text() == "0o200 025"
        assert calc.rows["BIN"][1].text() == "0b 0001 0000 0000 0001 0101"

        # Full-row frame clicking switches cleanly even after typing without stray tokens
        calc.select_base("DEC")
        QTest.keyClicks(calc.input, "21")
        assert calc.input.isModified()
        QTest.mouseClick(calc.row_frames["HEX"], Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
        app.processEvents()
        assert calc.base == "HEX"
        assert not calc._tokens
        assert calc.input.text() == "0x15"

        QTest.mouseClick(calc.row_frames["BIN"], Qt.MouseButton.LeftButton, pos=QPoint(calc.row_frames["BIN"].width() - 2, 2))
        app.processEvents()
        assert calc.base == "BIN"
        assert calc.input.text() == "0b 0001 0101"

        # Shift+Wheel horizontal scroll on overflowing line edits
        calc.paste("0x12345678")
        bin_edit = calc.rows["BIN"][1]
        bin_edit.setFixedWidth(90)
        bin_edit.setCursorPosition(0)
        app.processEvents()
        wheel_ev = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ShiftModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(bin_edit, wheel_ev)
        assert bin_edit.cursorPosition() > 0

        # Double-clicking FXP badge activates inline QLineEdit to update FXP format
        badge = calc._primary_fmt_badge
        assert not badge.edit.isVisible()
        QTest.mouseDClick(badge.label, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert badge.edit.isVisible()
        badge.edit.setText("Q4.4")
        QTest.keyClick(badge.edit, Qt.Key.Key_Return)
        app.processEvents()
        assert calc.engine.primary_spec == FxpSpec(True, 8, 4)
        assert not badge.edit.isVisible()
        assert badge.label.text() == "Q4.4"
    finally:
        win.close()
        app.processEvents()



def test_simple_ui_responsive_sizing_and_i18n_sync(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from dataclasses import replace
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "responsive.json"
    CalcConfig(launch_mode="simple").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        if calc.sidebar.isVisible():
            win.toggle_side_btn.click()
            app.processEvents()
        assert not calc.sidebar.isVisible()

        # Shrink down cleanly (left_pane supports <=320px; offscreen top_bar uses wider bitmap fallback font)
        win.resize(330, 450)
        app.processEvents()
        assert calc.left_pane.minimumWidth() <= 320
        assert win.width() <= max(
            350,
            win.top_bar.minimumSizeHint().width(),
            calc.left_pane.minimumSizeHint().width(),
        )
        assert win.height() <= 470
        # Open sidebar to compress left_pane < 390px regardless of platform font metrics
        win.toggle_side_btn.click()
        app.processEvents()
        win.resize(560, 460)
        app.processEvents()
        assert calc.left_pane.width() < 390
        small_px = calc._keypad_main_btns[0].font().pixelSize()
        assert calc.shift_mode_lbl.text() == "Bit shift"
        assert list(calc._shift_mode_btns) == ["arithmetic", "logical", "rotate", "rotate_carry"]
        assert [b.text() for b in calc._keypad_aux_btns] == ["AND", "OR", "NOT", "NAND", "NOR", "XOR"]
        assert calc._keypad_grid.rowCount() == 6 and calc._keypad_grid.columnCount() == 5

        # Expand window -> keypad & hero fonts grow and shift/bitwise controls trigger cleanly
        win.toggle_side_btn.click()
        app.processEvents()
        win.resize(900, 760)
        app.processEvents()
        large_px = calc._keypad_main_btns[0].font().pixelSize()
        assert large_px > small_px
        calc._shift_mode_btns["logical"].click()
        assert calc.engine.shift_mode == "logical"

        # FXP drawer "Apply & Sync Settings" syncs quant/overflow back into CalculatorWindow.config
        calc.inline_cfg_btn.click()
        calc.cfg_quant_combo.setCurrentIndex(calc.cfg_quant_combo.findData("TRN"))
        calc.cfg_ovf_combo.setCurrentIndex(calc.cfg_ovf_combo.findData("WRAP"))
        calc._apply_fxp_drawer()
        app.processEvents()
        assert win.config.default_quant == "TRN" and win.config.default_overflow == "WRAP"

        # Dynamic settings sync: zh_TW translation + keep_english_terms + font size
        win._apply_preferences(
            replace(win.config, language="zh_TW", keep_english_terms=False, code_font_size=16)
        )
        app.processEvents()
        assert calc.tab_vars_btn.text() == "變數"
        assert calc.var_sto_btn.text() == "存"
        assert calc.var_rcl_btn.text() == "回填"
        assert calc.shift_mode_lbl.text() == "位元移位"
        assert calc._shift_mode_btns["arithmetic"].text() == "算術"
        assert calc._shift_mode_btns["logical"].text() == "邏輯"
        assert calc._shift_mode_btns["rotate"].text() == "循環"
        assert calc._shift_mode_btns["rotate_carry"].text().startswith("帶進位")
        assert calc.cfg_signed_combo.itemText(0) == "有號"
        assert calc.cfg_signed_combo.itemText(1) == "無號"
        assert calc._keypad_main_btns[0].font().pixelSize() >= large_px
        empty_families = calc.var_table.item(1, 1).font().families()
        assert any("JhengHei" in fam or "Segoe UI" in fam for fam in empty_families)
    finally:
        win.close()
        app.processEvents()



def test_simple_ui_variables_table_sto_rcl_and_compare_columns(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "vars_table.json"
    CalcConfig(launch_mode="simple").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        table = calc.var_table
        assert table.rowCount() >= 7
        cols = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
        assert cols == ["Var", "HEX", "DEC", "OCT", "BIN", "REAL", "FXP"]
        assert [table.item(1, c).text() for c in range(1, table.columnCount())] == ["-"] * 6
        assert calc._text("Empty") in table.item(1, 1).toolTip()
        assert not calc.var_sto_btn.isCheckable() and not calc.var_rcl_btn.isCheckable()
        assert not calc.var_sto_btn.isEnabled() and not calc.var_rcl_btn.isEnabled()

        # Select empty row A -> STO enabled, RCL still disabled until populated
        calc.select_base("DEC")
        calc.paste("21")
        table.selectRow(1)  # Row A
        app.processEvents()
        assert calc.var_sto_btn.isEnabled()
        assert not calc.var_rcl_btn.isEnabled()
        calc.var_sto_btn.click()
        app.processEvents()
        assert calc.var_rcl_btn.isEnabled()
        assert calc.engine.variables["A"] is not None
        assert table.item(1, 1).text() == "0x15"
        assert table.item(1, 2).text() == "21"
        assert "Source: DEC" in table.item(1, 0).toolTip()

        # Clicking outside the variables table clears row selection automatically
        QTest.mouseClick(calc.input, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert calc._selected_var_name() is None
        assert not calc.var_sto_btn.isEnabled() and not calc.var_rcl_btn.isEnabled()

        # Store REAL 3.5 into B and recall A and B sequentially
        calc.select_base("REAL")
        calc.paste("3.5")
        table.selectRow(2)  # Row B
        calc.var_sto_btn.click()
        app.processEvents()
        assert calc.engine.variables["B"] is not None
        assert table.item(2, 5).text() == "3.5"

        table.selectRow(1)  # Row A
        calc.var_rcl_btn.click()
        app.processEvents()
        assert calc.base == "DEC" and calc.engine.raw_uint == 21

        table.selectRow(2)  # Row B
        calc.var_rcl_btn.click()
        app.processEvents()
        assert calc.base == "REAL" and calc.input.text() == "3.5"

        # Dynamic FXP2/FXP3 columns & preserved column drag order
        calc._apply_primary_fmt_text("Q4.4")
        calc._add_compare()
        calc._change_compare(0, "Q8.8")
        app.processEvents()
        assert [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())] == [
            "Var", "HEX", "DEC", "OCT", "BIN", "REAL", "FXP", "FXP2"
        ]
        # Drag FXP2 before HEX (from index 7 -> visual index 1)
        header = table.horizontalHeader()
        header.moveSection(7, 1)
        calc._add_compare()
        app.processEvents()
        new_cols = [table.horizontalHeaderItem(header.logicalIndex(v)).text() for v in range(table.columnCount())]
        assert new_cols[:3] == ["Var", "FXP2", "HEX"]
        assert new_cols[-1] == "FXP3"
        assert [table.item(3, c).text() for c in range(1, table.columnCount())] == ["-"] * 8
    finally:
        win.close()
        app.processEvents()



def test_simple_calc_invalid_expr_and_recovery(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "recovery.json"
    CalcConfig(launch_mode="simple", language="en").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()

    def keypad_btn(label: str) -> QPushButton:
        for btn in win.simple.findChildren(QPushButton):
            if btn.text() == label:
                return btn
        raise AssertionError(f"Button {label!r} not found")

    try:
        calc = win.simple

        # (a) Consecutive binary operators replace cleanly instead of stacking
        for op in (">>", ">>", "<<", "<<", "NAND"):
            keypad_btn(op).click()
            app.processEvents()
        assert calc.expression.text() == "0 NAND"
        assert not calc.status.isVisible()

        keypad_btn("0").click()
        keypad_btn("=").click()
        app.processEvents()
        assert not calc.status.isVisible()
        assert calc.expression.text() == ""

        # (b) Dynamic C / CE button clears entry first when typing operand after operator, then expression
        assert calc.clear_entry_btn.text() == "C"
        keypad_btn("7").click()
        keypad_btn("+").click()
        keypad_btn("5").click()
        app.processEvents()
        assert calc.expression.text() == "7 +"
        assert calc.input.text() == "5"
        assert calc.clear_entry_btn.text() == "CE"

        calc.clear_entry_btn.click()
        app.processEvents()
        assert calc.input.text() == ""
        assert calc.expression.text() == "7 +"
        assert calc.clear_entry_btn.text() == "C"

        calc.clear_entry_btn.click()
        app.processEvents()
        assert calc.expression.text() == ""
        assert calc.input.text() == "0"

        def trigger_invalid():
            for key in ("(", "5", "+", "="):
                keypad_btn(key).click()
            app.processEvents()
            assert calc.status.isVisible()
            assert "Invalid expression" in calc.status.text()
            assert "(" in calc.expression.text()
            assert calc.clear_entry_btn.text() == "C"

        # (c.1) Recover from error via keypad digit click without getting trapped
        trigger_invalid()
        keypad_btn("6").click()
        app.processEvents()
        assert not calc.status.isVisible()
        assert calc.expression.text() == ""
        assert calc.input.text() == "6"
        keypad_btn("+").click()
        keypad_btn("1").click()
        keypad_btn("=").click()
        app.processEvents()
        assert not calc.status.isVisible()
        assert calc.input.text() == "7"

        # (c.2) Recover from error via Esc
        trigger_invalid()
        QTest.keyClick(calc.input, Qt.Key.Key_Escape)
        app.processEvents()
        assert not calc.status.isVisible()
        assert calc.expression.text() == ""
        assert calc.input.text() == "0"

        # (c.3) Recover from error via keypad backspace and hardware Backspace
        trigger_invalid()
        keypad_btn("⌫").click()
        app.processEvents()
        assert not calc.status.isVisible()
        assert calc.expression.text() == ""
        assert calc.input.text() == "0"

        trigger_invalid()
        calc.input.setFocus()
        QTest.keyClick(calc.input, Qt.Key.Key_Backspace)
        app.processEvents()
        assert not calc.status.isVisible()
        assert calc.expression.text() == ""
        assert calc.input.text() == "0"

        # (c.4) Recover from error via operator click and base switching
        trigger_invalid()
        keypad_btn("+").click()
        keypad_btn("4").click()
        keypad_btn("=").click()
        app.processEvents()
        assert not calc.status.isVisible()
        assert calc.input.text() == "4"

        trigger_invalid()
        calc.select_base("HEX")
        app.processEvents()
        assert calc.base == "HEX"
        assert not calc.status.isVisible()
        assert calc.expression.text() == ""

        # (c.5) Recover via C button during error and ignore unmatched ')'
        trigger_invalid()
        calc.clear_entry_btn.click()
        app.processEvents()
        assert not calc.status.isVisible()
        assert calc.expression.text() == ""
        keypad_btn(")").click()
        app.processEvents()
        assert calc.expression.text() == ""
    finally:
        win.close()
        app.processEvents()



def test_simple_calc_single_refresh_single_save_and_scaled_table_columns(tmp_path):
    from dataclasses import replace
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow
    from rpkbin.app.calc.ui.theme import scaled_px

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "perf_scale.json"
    CalcConfig(launch_mode="simple", language="en", ui_scale=100).save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()

    def keypad_btn(label: str) -> QPushButton:
        for btn in win.simple.findChildren(QPushButton):
            if btn.text() == label:
                return btn
        raise AssertionError(f"Button {label!r} not found")

    try:
        calc = win.simple
        if calc.sidebar.isVisible():
            win.toggle_side_btn.click()
            app.processEvents()
        assert not calc.sidebar.isVisible()

        counts = {"refresh": 0, "var": 0, "hist": 0, "state": 0}
        orig_refresh = calc.refresh
        orig_var = calc._refresh_var_table
        orig_hist = calc._refresh_history

        def wrap_refresh(*args, **kwargs):
            counts["refresh"] += 1
            return orig_refresh(*args, **kwargs)

        def wrap_var(*args, **kwargs):
            counts["var"] += 1
            return orig_var(*args, **kwargs)

        def wrap_hist(*args, **kwargs):
            counts["hist"] += 1
            return orig_hist(*args, **kwargs)

        calc.refresh = wrap_refresh
        calc._refresh_var_table = wrap_var
        calc._refresh_history = wrap_hist
        calc.local_state_changed.connect(lambda: counts.__setitem__("state", counts["state"] + 1))

        # 1a. Keypad digit & backspace each refresh once and rebuild sidebars 0 times
        counts.update(refresh=0, var=0, hist=0)
        keypad_btn("5").click()
        app.processEvents()
        assert calc.input.text() == "5"
        assert counts["refresh"] == 1 and counts["var"] == 0 and counts["hist"] == 0

        counts.update(refresh=0, var=0, hist=0)
        keypad_btn("⌫").click()
        app.processEvents()
        assert counts["refresh"] == 1 and counts["var"] == 0 and counts["hist"] == 0

        # 1b. Hardware keyboard typing refreshes once and rebuilds sidebars 0 times
        counts.update(refresh=0, var=0, hist=0)
        calc.input.setFocus()
        QTest.keyClick(calc.input, "3")
        app.processEvents()
        assert calc.input.text() == "3"
        assert counts["refresh"] == 1 and counts["var"] == 0 and counts["hist"] == 0

        # 1c. Equals updates history once without rebuilding variables; MS updates variables without history
        counts.update(refresh=0, var=0, hist=0)
        keypad_btn("+").click()
        keypad_btn("4").click()
        keypad_btn("=").click()
        app.processEvents()
        assert calc.input.text() == "7"
        assert counts["hist"] == 1 and counts["var"] == 0

        counts.update(refresh=0, var=0, hist=0)
        calc.ms_btn.click()
        app.processEvents()
        assert counts["var"] == 1 and counts["hist"] == 0

        # 2. Applying Primary FXP drawer emits local_state_changed exactly once
        counts["state"] = 0
        calc.inline_cfg_btn.click()
        calc.cfg_syntax_edit.setText("Q10.6")
        calc._apply_fxp_drawer()
        app.processEvents()
        assert counts["state"] == 1
        assert counts["var"] >= 1

        # 3. UI scale resizes untouched variable columns immediately & preserves manually resized columns
        win.toggle_side_btn.click()
        app.processEvents()
        table = calc.var_table
        header = table.horizontalHeader()
        hex_col = 1
        dec_col = 2
        assert table.columnWidth(hex_col) == scaled_px(86, win.config)
        header.resizeSection(dec_col, 160)
        app.processEvents()
        assert table.columnWidth(dec_col) == 160

        cfg_150 = replace(win.config, ui_scale=150)
        win._apply_preferences(cfg_150)
        app.processEvents()
        assert table.columnWidth(hex_col) == scaled_px(86, cfg_150)
        assert table.columnWidth(dec_col) == 160
    finally:
        win.close()
        app.processEvents()



def test_simple_calc_var_table_resize_drag_then_store(tmp_path):
    from dataclasses import replace
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow
    from rpkbin.app.calc.ui.theme import scaled_px

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "resize_drag_store.json"
    CalcConfig(launch_mode="simple", language="en", ui_scale=100).save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        if not calc.sidebar.isVisible():
            win.toggle_side_btn.click()
            app.processEvents()
        table = calc.var_table
        header = table.horizontalHeader()
        hex_col = 1
        dec_col = 2
        hex_default_w = scaled_px(86, win.config)
        assert table.columnWidth(hex_col) == hex_default_w

        # Resize DEC to 160 px, then drag DEC before HEX
        header.resizeSection(dec_col, 160)
        app.processEvents()
        header.moveSection(header.visualIndex(dec_col), header.visualIndex(hex_col))
        app.processEvents()

        def visual_headers() -> list[str]:
            return [table.horizontalHeaderItem(header.logicalIndex(v)).text() for v in range(table.columnCount())]

        assert visual_headers()[:3] == ["Var", "DEC", "HEX"]

        # Store value into M via MS -> order, widths, and cell alignment stay preserved
        calc.select_base("DEC")
        calc.paste("21")
        calc.ms_btn.click()
        app.processEvents()

        assert visual_headers() == ["Var", "DEC", "HEX", "OCT", "BIN", "REAL", "FXP"]
        assert table.columnWidth(dec_col) == 160
        assert table.columnWidth(hex_col) == hex_default_w
        assert table.item(0, header.logicalIndex(1)).text() == "21"
        assert table.item(0, header.logicalIndex(2)).text() == "0x15"

        # Adding compare column & scaling preserves moved order, manual width, and scales untouched columns
        calc._add_compare()
        app.processEvents()
        assert visual_headers()[:3] == ["Var", "DEC", "HEX"]
        assert visual_headers()[-1] == "FXP2"
        assert table.columnWidth(dec_col) == 160
        assert table.columnWidth(hex_col) == hex_default_w

        cfg_150 = replace(win.config, ui_scale=150)
        win._apply_preferences(cfg_150)
        app.processEvents()
        assert visual_headers()[:3] == ["Var", "DEC", "HEX"]
        assert table.columnWidth(dec_col) == 160
        assert table.columnWidth(hex_col) == scaled_px(86, cfg_150)
        fxp2_scaled_w = scaled_px(74, cfg_150)
        assert table.columnWidth(7) == fxp2_scaled_w

        # Removing then re-adding FXP2 re-initializes the newly created Qt column width at 150%
        calc._remove_compare(0)
        app.processEvents()
        assert table.columnCount() == 7
        calc._add_compare()
        app.processEvents()
        assert table.columnCount() == 8
        assert table.columnWidth(7) == fxp2_scaled_w
    finally:
        win.close()
        app.processEvents()


def test_simple_keypad_size_mode_and_half_height_ratio(tmp_path):
    import json
    from dataclasses import replace

    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.core.settings_transfer import export_preferences, merge_preferences, parse_preferences
    from rpkbin.app.calc.ui.app import CalculatorWindow
    from rpkbin.app.calc.ui.settings_dialog import SettingsDialog
    from rpkbin.app.calc.ui.theme import scaled_px

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "keypad_mode.json"
    CalcConfig(launch_mode="simple", simple_keypad_size_mode="expand").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        if calc.sidebar.isVisible():
            win.toggle_side_btn.click()
            app.processEvents()

        # ~50% keypad height proportion on default & expanded window
        ratio_default = calc.keypad.height() / max(1, calc.left_pane.height())
        assert 0.40 <= ratio_default <= 0.56

        win.resize(1040, 820)
        app.processEvents()
        expand_pad_h = calc.keypad.height()
        expand_top_h = calc.top_display_frame.height()
        expand_font_px = calc._keypad_main_btns[0].font().pixelSize()
        expand_btn_w = calc._digit_buttons["7"].width()
        expand_btn_h = calc._digit_buttons["7"].height()
        ratio_expanded = expand_pad_h / max(1, calc.left_pane.height())
        assert 0.42 <= ratio_expanded <= 0.55

        # Settings dialog exposes Simple keypad sizing mode and persists it
        dlg = SettingsDialog(win.config, win, config_path=cfg_path)
        idx = dlg.keypad_size_mode_combo.findData("capped")
        assert idx >= 0
        dlg.keypad_size_mode_combo.setCurrentIndex(idx)
        draft = dlg._draft_config()
        assert draft.simple_keypad_size_mode == "capped"
        dlg.close()

        # Roundtrip transfer envelope preserves simple_keypad_size_mode
        exported = export_preferences(draft)
        parsed = parse_preferences(json.dumps(exported))
        merged = merge_preferences(CalcConfig(), parsed)
        assert merged.simple_keypad_size_mode == "capped"
        draft.save(cfg_path)
        assert CalcConfig.load(cfg_path).simple_keypad_size_mode == "capped"

        # Applying "capped" with sidebar closed caps window width + button dimensions
        win._apply_preferences(replace(win.config, simple_keypad_size_mode="capped"))
        app.processEvents()
        btn7 = calc._digit_buttons["7"]
        left_cap = scaled_px(460, win.config)
        assert calc.left_pane.maximumWidth() == left_cap
        assert win.maximumWidth() <= left_cap + 2
        win.resize(1100, 820)
        app.processEvents()
        assert win.width() <= left_cap + 2
        assert calc.keypad.height() <= scaled_px(268, win.config) + 2
        assert calc._keypad_inner.width() <= left_cap + 2
        assert btn7.width() <= scaled_px(88, win.config) + 2
        assert btn7.height() <= scaled_px(32, win.config) + 2
        assert btn7.width() < expand_btn_w
        assert btn7.height() < expand_btn_h
        assert calc.keypad.height() < expand_pad_h
        assert calc.top_display_frame.height() > expand_top_h
        assert calc._keypad_main_btns[0].font().pixelSize() < expand_font_px

        # With sidebar open in "capped" mode, widening window expands Sidebar while left keypad pane stays capped
        win.toggle_side_btn.click()
        app.processEvents()
        assert win.maximumWidth() >= 1_000_000
        win.resize(1120, 820)
        app.processEvents()
        assert win.width() >= 1100
        assert calc.left_pane.width() <= left_cap + 2
        assert calc._keypad_inner.width() <= left_cap + 2
        assert btn7.width() <= scaled_px(88, win.config) + 2
        assert calc.sidebar.width() >= 600
    finally:
        win.close()
        app.processEvents()


def test_simple_unified_base_scroll_and_group_drag_reorder(tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "drag_rows.json"
    CalcConfig(launch_mode="simple").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        calc._add_compare()  # FXP2
        calc._add_compare()  # FXP3
        app.processEvents()

        def layout_labels() -> list[str]:
            labels = []
            for i in range(calc.base_layout.count()):
                w = calc.base_layout.itemAt(i).widget()
                if w is None:
                    continue
                for base, frame in calc.row_frames.items():
                    if frame is w:
                        labels.append(base)
                        break
                else:
                    for idx, row_tuple in enumerate(calc._comparison_rows):
                        if row_tuple[2] is w:
                            labels.append(f"FXP{idx + 2}")
                            break
            return labels

        assert layout_labels() == ["HEX", "DEC", "OCT", "BIN", "REAL", "FXP", "FXP2", "FXP3"]

        def drag_button_to_y(btn, target_host_y: int):
            start_local = btn.rect().center()
            start_global = btn.mapToGlobal(start_local)
            target_global = calc.base_host.mapToGlobal(QPoint(btn.width() // 2, target_host_y))
            end_local = btn.mapFromGlobal(target_global)
            QTest.mousePress(btn, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start_local)
            mid_local = QPoint(start_local.x(), (start_local.y() + end_local.y()) // 2)
            QTest.mouseMove(btn, mid_local)
            QTest.mouseMove(btn, end_local)
            QTest.mouseRelease(btn, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end_local)
            app.processEvents()
            assert start_global != target_global

        # 1. Drag DEC above HEX -> stays inside radix group; attempt to drag DEC past REAL stays above REAL
        hex_y = calc.row_frames["HEX"].geometry().top() + 2
        drag_button_to_y(calc.rows["DEC"][0], hex_y)
        assert layout_labels() == ["DEC", "HEX", "OCT", "BIN", "REAL", "FXP", "FXP2", "FXP3"]

        fxp2_y = calc._comparison_rows[0][2].geometry().bottom()
        drag_button_to_y(calc.rows["DEC"][0], fxp2_y)
        assert layout_labels() == ["HEX", "OCT", "BIN", "DEC", "REAL", "FXP", "FXP2", "FXP3"]

        # 2. REAL is fixed and cannot be dragged
        real_btn = calc.rows["REAL"][0]
        drag_button_to_y(real_btn, calc.row_frames["HEX"].geometry().top() + 2)
        assert layout_labels()[4] == "REAL"

        # 3. Drag FXP3 above FXP -> reorders within FXP group, and stays below REAL
        fxp_y = calc.row_frames["FXP"].geometry().top() + 2
        drag_button_to_y(calc._comparison_rows[1][4], fxp_y)
        assert layout_labels() == ["HEX", "OCT", "BIN", "DEC", "REAL", "FXP3", "FXP", "FXP2"]

        real_top_y = calc.row_frames["REAL"].geometry().top()
        drag_button_to_y(calc._comparison_rows[1][4], real_top_y)
        assert layout_labels() == ["HEX", "OCT", "BIN", "DEC", "REAL", "FXP3", "FXP", "FXP2"]

        # 4. Removing FXP2 preserves remaining order (FXP3 -> becomes FXP2, FXP stays second)
        calc._remove_compare(0)
        app.processEvents()
        assert layout_labels() == ["HEX", "OCT", "BIN", "DEC", "REAL", "FXP2", "FXP"]

        # 5. Horizontal selection drag on value edit still selects text instead of dragging the row
        calc.select_base("DEC")
        calc.paste("123456")
        app.processEvents()
        val_edit = calc.rows["DEC"][1]
        QTest.mousePress(val_edit, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(2, val_edit.height() // 2))
        QTest.mouseMove(val_edit, QPoint(val_edit.width() - 4, val_edit.height() // 2))
        QTest.mouseRelease(val_edit, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(val_edit.width() - 4, val_edit.height() // 2))
        app.processEvents()
        assert val_edit.hasSelectedText()
        assert layout_labels() == ["HEX", "OCT", "BIN", "DEC", "REAL", "FXP2", "FXP"]

        # 6. Normal click on base label still switches active base
        QTest.mouseClick(calc.rows["OCT"][0], Qt.MouseButton.LeftButton)
        app.processEvents()
        assert calc.base == "OCT"
    finally:
        win.close()
        app.processEvents()


def test_simple_bit_select_vertical_splitter_caps_at_fxp(tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "bit_split.json"
    CalcConfig(launch_mode="simple").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    win.resize(740, 760)
    app.processEvents()
    try:
        calc = win.simple

        def drag_vhandle(dy: int):
            handle = calc.left_vsplitter.handle(1)
            pos = handle.rect().center()
            target = QPoint(pos.x(), pos.y() + dy)
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
            QTest.mouseMove(handle, target)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
            app.processEvents()

        # Give keypad a moderate user-adjusted height before opening Bit Select
        drag_vhandle(100)

        for mode in ("both", "bits"):
            calc._set_panel(mode)
            app.processEvents()

            # Dragging downward shrinks Bit Select to give top_display_frame more room
            drag_vhandle(180)
            down_top_h = calc.top_display_frame.height()
            down_bit_h = calc.bit_panel.height()
            down_pad_h = calc.keypad.height()
            assert down_top_h > 250

            # Dragging upward way past top expands Bit Select and stops before covering FXP row
            drag_vhandle(-360)
            assert calc.bit_panel.height() > down_bit_h + 100
            if mode == "both":
                assert calc.keypad.height() == down_pad_h
            assert calc.top_display_frame.height() >= calc.top_display_frame.minimumHeight()
            assert calc.row_frames["FXP"].geometry().bottom() <= calc.base_scroll.viewport().height()

        # Reorder FXP3 above primary FXP, then drag upward to max -> both FXP3 and FXP stay visible
        calc._set_panel("both")
        calc._add_compare()  # FXP2
        calc._add_compare()  # FXP3
        app.processEvents()
        fxp3_btn = calc._comparison_rows[1][4]
        fxp_y = calc.row_frames["FXP"].geometry().top() + 2
        start_pt = fxp3_btn.rect().center()
        end_pt = fxp3_btn.mapFrom(calc.base_host, QPoint(calc.base_host.width() // 4, fxp_y))
        QTest.mousePress(fxp3_btn, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start_pt)
        QTest.mouseMove(fxp3_btn, QPoint(start_pt.x(), start_pt.y() - 12))
        QTest.mouseMove(fxp3_btn, end_pt)
        QTest.mouseRelease(fxp3_btn, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end_pt)
        app.processEvents()
        assert calc._fxp_row_order[:2] == [2, 0]

        drag_vhandle(180)
        drag_vhandle(-360)
        assert calc._comparison_rows[1][2].geometry().bottom() < calc.row_frames["FXP"].geometry().top()
        assert calc.row_frames["FXP"].geometry().bottom() <= calc.base_scroll.viewport().height()
    finally:
        win.close()
        app.processEvents()


def test_simple_responsive_stylesheet_deduped_on_resize(tmp_path):
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "resp_dedupe.json"
    CalcConfig(launch_mode="simple", simple_keypad_size_mode="expand").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        if calc.sidebar.isVisible():
            win.toggle_side_btn.click()
            app.processEvents()

        keypad_styles: list[str] = []
        input_styles: list[str] = []
        orig_kp_set = calc.keypad.setStyleSheet
        orig_in_set = calc.input.setStyleSheet
        calc.keypad.setStyleSheet = lambda css: (keypad_styles.append(css), orig_kp_set(css))[1]
        calc.input.setStyleSheet = lambda css: (input_styles.append(css), orig_in_set(css))[1]

        base_w = win.width()
        base_h = win.height()
        for dh in range(1, 16):
            win.resize(base_w, base_h + dh)
            app.processEvents()

        # 15 pixel-by-pixel height changes must NOT apply 15 keypad stylesheets or touch hero input
        assert len(keypad_styles) < 6
        assert len(keypad_styles) == len(set(keypad_styles))
        assert input_styles == []

        # Horizontal top_splitter drag updates hero input independently without touching keypad stylesheet
        kp_count_before = len(keypad_styles)
        calc.top_splitter.setSizes([max(180, calc.base_container.width() - 90), calc.hero_frame.width() + 90])
        calc._update_responsive_metrics()
        app.processEvents()
        assert len(keypad_styles) == kp_count_before
        assert len(input_styles) >= 1
    finally:
        win.close()
        app.processEvents()


def test_simple_primary_fxp_badge_tooltip_and_row_order_preserved(tmp_path):
    from dataclasses import replace

    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    en_tip = "This format determines the values shown in HEX, DEC, OCT, and BIN. Double-click to edit."
    zh_tip = "此格式決定 HEX、DEC、OCT、BIN 的顯示值。按兩下可編輯格式。"

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "fxp_badge_tip.json"
    CalcConfig(launch_mode="simple", language="en_US").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        # Unset primary format ("Int32") in en_US
        assert calc.engine.primary_spec is None
        assert calc._primary_fmt_badge.label.text() == "Int32"
        assert calc._primary_fmt_badge.toolTip() == en_tip
        assert calc._primary_fmt_badge.label.toolTip() == en_tip

        # Set primary format ("Q4.4") keeps primary tip; FXP/FXP2/FXP3 order & names unchanged
        calc._apply_primary_fmt_text("Q4.4")
        calc._add_compare()  # FXP2
        calc._add_compare()  # FXP3
        app.processEvents()
        assert calc._primary_fmt_badge.label.text() == "Q4.4"
        assert calc._primary_fmt_badge.toolTip() == en_tip
        assert calc._primary_fmt_badge.label.toolTip() == en_tip
        labels = [
            calc.base_layout.itemAt(i).widget().findChildren(type(calc.rows["HEX"][0]))[0].text()
            for i in range(calc.base_layout.count())
        ]
        assert labels == ["HEX", "DEC", "OCT", "BIN", "REAL", "FXP", "FXP2", "FXP3"]
        assert calc._comparison_rows[0][5].toolTip() == "Double-click to edit FXP format"

        # Switch language to zh_TW with set format and unset Int32
        win._apply_preferences(replace(win.config, language="zh_TW"))
        app.processEvents()
        assert calc._primary_fmt_badge.toolTip() == zh_tip
        assert calc._primary_fmt_badge.label.toolTip() == zh_tip

        calc._apply_primary_fmt_text("")
        app.processEvents()
        assert calc._primary_fmt_badge.label.text() == "Int32"
        assert calc._primary_fmt_badge.toolTip() == zh_tip
        assert calc._primary_fmt_badge.label.toolTip() == zh_tip

        # Switch back to en_US
        win._apply_preferences(replace(win.config, language="en_US"))
        app.processEvents()
        assert calc._primary_fmt_badge.toolTip() == en_tip
        assert calc._primary_fmt_badge.label.toolTip() == en_tip
    finally:
        win.close()
        app.processEvents()


def test_simple_hero_input_large_font_and_narrow_long_scroll(tmp_path):
    from dataclasses import replace

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "hero_font.json"
    CalcConfig(launch_mode="simple", ui_scale=100, code_font_size=10).save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        # 100% scale, default font/window starts around ~28 px without enlarging keypad/base rows
        assert 26 <= calc._hero_font_sig[0] <= 30
        assert calc.input.height() == calc._hero_font_sig[1] >= 42
        assert calc._keypad_main_btns[0].font().pixelSize() <= 16
        assert calc.rows["HEX"][1].fontInfo().pixelSize() <= 16

        # Narrowest window (sidebar closed)
        if calc.sidebar.isVisible():
            win.toggle_side_btn.click()
            app.processEvents()
        win.resize(320, 520)
        app.processEvents()
        assert bool(calc.input.alignment() & Qt.AlignmentFlag.AlignRight)

        # Long HEX paste + horizontal scrolling, cursor motion & selection
        calc._apply_primary_fmt_text("UQ64.0")
        calc.select_base("HEX")
        calc.paste("0x123456789ABCDEF0")
        app.processEvents()
        assert len(calc.input.text()) >= 18
        end_x = calc.input.cursorRect().x()
        calc.input.setFocus()
        QTest.keyClick(calc.input, Qt.Key.Key_Home)
        app.processEvents()
        home_x = calc.input.cursorRect().x()
        assert calc.input.cursorPosition() == 0
        assert home_x < end_x - 20
        QTest.keyClick(calc.input, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClick(calc.input, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        app.processEvents()
        assert calc.input.hasSelectedText() and calc.input.selectedText() == "0x"

        # Long decimal paste + horizontal scrolling & drag selection
        calc.select_base("REAL")
        calc.paste("-1234567890.1234567890123456")
        app.processEvents()
        dec_end_x = calc.input.cursorRect().x()
        QTest.keyClick(calc.input, Qt.Key.Key_Home)
        app.processEvents()
        dec_home_x = calc.input.cursorRect().x()
        assert dec_home_x < dec_end_x - 20
        y_mid = calc.input.height() // 2
        QTest.mousePress(calc.input, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(2, y_mid))
        QTest.mouseMove(calc.input, QPoint(calc.input.width() - 4, y_mid))
        QTest.mouseRelease(calc.input, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(calc.input.width() - 4, y_mid))
        app.processEvents()
        assert calc.input.hasSelectedText()

        # Bit Select modes on narrow window keep primary FXP row completely visible even at max drag
        for mode in ("bits", "both"):
            calc._set_panel(mode)
            app.processEvents()
            handle = calc.left_vsplitter.handle(1)
            pos = handle.rect().center()
            target = QPoint(pos.x(), pos.y() - 360)
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
            QTest.mouseMove(handle, target)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
            app.processEvents()
            assert calc.row_frames["FXP"].geometry().bottom() <= calc.base_scroll.viewport().height()
            assert calc.input.height() == calc._hero_font_sig[1]

        # 14 pt / 125% UI scale in ~776 px window caps hero font by ui_scale instead of code_font_size
        calc._set_panel("keypad")
        win._apply_preferences(replace(win.config, code_font_size=14, ui_scale=125))
        app.processEvents()
        win.resize(776, 666)
        app.processEvents()
        hero_cap_125 = round(34 * 1.25)
        assert calc._hero_font_sig[0] == hero_cap_125 == 42
        assert calc.input.height() <= 66
        if not calc.sidebar.isVisible():
            win.toggle_side_btn.click()
            app.processEvents()
            win.resize(776, 666)
            app.processEvents()
            assert calc._hero_font_sig[0] == hero_cap_125 == 42
            assert calc.input.height() <= 66
    finally:
        win.close()
        app.processEvents()


def test_simple_fxp_drawer_control_heights_aligned(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from dataclasses import replace
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "calc_config.json"
    CalcConfig(launch_mode="simple", language="zh_TW", theme="light").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        calc._toggle_fxp_drawer(0)
        app.processEvents()
        controls = (
            calc.cfg_syntax_edit,
            calc.cfg_signed_combo,
            calc.cfg_wl_spin,
            calc.cfg_fwl_spin,
            calc.cfg_quant_combo,
            calc.cfg_ovf_combo,
            calc.cfg_apply_btn,
        )
        heights = {ctrl.height() for ctrl in controls}
        assert len(heights) == 1 and next(iter(heights)) > 0

        # Dynamically switch theme, language, font size, and UI scale while drawer is open
        win._apply_preferences(
            replace(win.config, language="en_US", theme="dark", ui_scale=125, code_font_size=14)
        )
        app.processEvents()
        scaled_heights = {ctrl.height() for ctrl in controls}
        assert len(scaled_heights) == 1 and next(iter(scaled_heights)) > next(iter(heights))
        assert calc.fxp_drawer.height() >= calc.fxp_drawer.sizeHint().height()
    finally:
        win.close()
        app.processEvents()


def test_simple_int32_button_and_history_remove_keep_active_fxp(tmp_path):
    from dataclasses import replace

    from PySide6.QtWidgets import QApplication

    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "settings.json"
    CalcConfig(language="en_US").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.resize(680, 545)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        mid_layout = calc.mid_bar.layout()
        ms_idx = mid_layout.indexOf(calc.ms_btn)
        int32_idx = mid_layout.indexOf(calc.int32_btn)
        assert ms_idx >= 0 and int32_idx == ms_idx + 1
        assert calc.int32_btn.text() == "Int32"
        assert calc.int32_btn.height() == calc.ms_btn.height()
        combo_items = [calc.format_combo.itemText(i) for i in range(calc.format_combo.count())]
        assert "Int32" not in combo_items
        assert "Int32:" in calc.int32_btn.toolTip()

        win._apply_preferences(
            replace(win.config, language="zh_TW", ui_scale=125, code_font_size=14)
        )
        app.processEvents()
        assert "Int32" in calc.int32_btn.toolTip() and "重設" in calc.int32_btn.toolTip()
        assert calc.int32_btn.height() == calc.ms_btn.height()

        win._apply_preferences(
            replace(win.config, language="en_US", ui_scale=100, code_font_size=10)
        )
        app.processEvents()

        assert calc._apply_primary_fmt_text("Q4.4")
        app.processEvents()
        _, primary_key = calc._active_fxp_target()
        assert calc.engine.primary_spec is not None and primary_key
        assert any(item["format"] == primary_key for item in win.config.simple_fxp_history)

        # Removing primary FXP from history does not reset primary FXP back to Int32
        calc.del_fmt_btn.click()
        app.processEvents()
        assert not any(item["format"] == primary_key for item in win.config.simple_fxp_history)
        assert calc.format_combo.findData(primary_key) == -1
        assert not calc.del_fmt_btn.isEnabled()
        assert calc.pin_fmt_btn.isEnabled()
        assert calc.engine.primary_spec is not None
        assert calc._active_fxp_target()[1] == primary_key
        assert calc.primary_fmt_edit.text() != ""

        # Pinning an active format that was removed re-inserts and pins it in history
        calc.pin_fmt_btn.click()
        app.processEvents()
        assert any(
            item["format"] == primary_key and item["pinned"]
            for item in win.config.simple_fxp_history
        )

        # Removing a comparison row's format behaves identically (history only)
        calc._add_compare()
        calc.select_base("FXP", fxp_idx=1)
        calc.format_combo.setEditText("UQ8.4")
        calc._format_entered()
        app.processEvents()
        cmp_spec, cmp_key = calc._active_fxp_target()
        assert cmp_spec is calc.engine.compare_specs[0] and cmp_key != primary_key
        assert any(item["format"] == cmp_key for item in win.config.simple_fxp_history)
        calc.del_fmt_btn.click()
        app.processEvents()
        assert not any(item["format"] == cmp_key for item in win.config.simple_fxp_history)
        assert calc.engine.compare_specs[0] == cmp_spec
        assert calc.engine.primary_spec is not None

        # Clicking Int32 while on REAL resets primary FXP without changing active base or clearing history
        calc.select_base("REAL")
        calc.int32_btn.click()
        app.processEvents()
        assert calc.base == "REAL"
        assert calc.engine.primary_spec is None
        assert calc.primary_fmt_edit.text() == ""
        assert calc.primary_fmt_edit.placeholderText() == "Int32"
        assert any(item["format"] == primary_key for item in win.config.simple_fxp_history)
        combo_items = [calc.format_combo.itemText(i) for i in range(calc.format_combo.count())]
        assert "Int32" not in combo_items

        # Clicking Int32 while viewing FXP2 with primary drawer open resets primary FXP and refreshes drawer
        assert calc._apply_primary_fmt_text("Q4.4")
        calc._toggle_fxp_drawer(0)
        calc.select_base("FXP", fxp_idx=1)
        calc.int32_btn.click()
        app.processEvents()
        assert calc.engine.primary_spec is None
        assert calc.selected_fxp_idx == 0
        assert calc.fxp_drawer.isVisible() and calc.cfg_target_idx == 0
        assert calc.cfg_syntax_edit.text() == ""
        assert calc.cfg_wl_spin.value() == 32 and calc.cfg_fwl_spin.value() == 0

        # When primary is already Int32, switching from FXP2 via Int32 button syncs main input to primary FXP
        calc.select_base("REAL")
        calc.paste("0.6")
        app.processEvents()
        calc.select_base("FXP", fxp_idx=1)
        app.processEvents()
        fxp2_text = calc.input.text()
        assert fxp2_text != calc.rows["FXP"][1].text()
        calc.int32_btn.click()
        app.processEvents()
        assert calc.base == "FXP" and calc.selected_fxp_idx == 0
        assert calc.input.text() == calc.rows["FXP"][1].text()
        assert calc.input.text() != fxp2_text
    finally:
        win.close()
        app.processEvents()


def test_simple_panel_switch_preserves_window_and_keypad_height(tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])

    def flush():
        app.processEvents()
        QTest.qWait(15)
        app.processEvents()

    def mid_y(calc_widget):
        return calc_widget.mid_bar.mapTo(calc_widget, calc_widget.mid_bar.rect().topLeft()).y()

    def drag_vhandle(calc_widget, dy: int):
        handle = calc_widget.left_vsplitter.handle(1)
        pos = handle.rect().center()
        target = QPoint(pos.x(), pos.y() + dy)
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        QTest.mouseMove(handle, target)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
        flush()

    # 1) Tall window (800x760): window size, mid_bar position (keypad<->bits & both->bits), and keypad height
    cfg1 = tmp_path / "tall.json"
    CalcConfig(language="en_US").save(cfg1)
    win = CalculatorWindow("simple", config_path=cfg1)
    win.resize(800, 760)
    win.show()
    flush()
    try:
        calc = win.simple
        base_win = win.size()
        base_pad_h = calc.keypad.height()
        base_mid_y = mid_y(calc)
        assert base_pad_h > 0

        # Direct keypad <-> bits keeps mid_bar Y, window size, and keypad height unchanged
        calc._set_panel("bits")
        flush()
        assert win.size() == base_win
        assert mid_y(calc) == base_mid_y
        assert calc.bit_panel.height() > 200
        assert calc.row_frames["FXP"].geometry().bottom() <= calc.base_scroll.viewport().height()

        calc._set_panel("keypad")
        flush()
        assert win.size() == base_win
        assert mid_y(calc) == base_mid_y
        assert calc.keypad.height() == base_pad_h

        # keypad <-> both keeps window size and keypad height unchanged
        calc._set_panel("both")
        flush()
        assert win.size() == base_win
        assert calc.keypad.height() == base_pad_h
        assert calc.row_frames["FXP"].geometry().bottom() <= calc.base_scroll.viewport().height()
        both_mid_y = mid_y(calc)

        calc._set_panel("keypad")
        flush()
        assert win.size() == base_win
        assert calc.keypad.height() == base_pad_h
        assert mid_y(calc) == base_mid_y

        # both -> bits keeps mid_bar Y unchanged; bits -> keypad carries divider position without jumping
        calc._set_panel("both")
        flush()
        assert mid_y(calc) == both_mid_y
        calc._set_panel("bits")
        flush()
        assert win.size() == base_win
        assert mid_y(calc) == both_mid_y
        calc._set_panel("keypad")
        flush()
        assert win.size() == base_win
        assert mid_y(calc) == both_mid_y

        # Drag keypad back down, verify none <-> keypad and bits drag -> keypad carry-over
        drag_vhandle(calc, 100)
        dragged_pad_h = calc.keypad.height()
        dragged_mid_y = mid_y(calc)

        calc._set_panel("none")
        flush()
        assert win.width() == base_win.width()
        assert win.height() < base_win.height()

        calc._set_panel("keypad")
        flush()
        assert win.size() == base_win
        assert calc.keypad.height() == dragged_pad_h
        assert mid_y(calc) == dragged_mid_y

        calc._set_panel("bits")
        flush()
        assert mid_y(calc) == dragged_mid_y
        drag_vhandle(calc, -60)
        bits_dragged_y = mid_y(calc)
        assert bits_dragged_y < dragged_mid_y - 40
        calc._set_panel("none")
        flush()
        calc._set_panel("bits")
        flush()
        assert mid_y(calc) == bits_dragged_y
        calc._set_panel("keypad")
        flush()
        assert win.size() == base_win
        assert mid_y(calc) == bits_dragged_y
        assert calc.keypad.height() > dragged_pad_h + 40
    finally:
        win.close()
        app.processEvents()

    # 2) Capped mode (800x760): keypad<->bits matches, and oversize bits drag clamps to capped keypad max
    cfg_cap = tmp_path / "capped.json"
    CalcConfig(language="en_US", simple_keypad_size_mode="capped").save(cfg_cap)
    win_cap = CalculatorWindow("simple", config_path=cfg_cap)
    win_cap.resize(800, 760)
    win_cap.show()
    flush()
    try:
        calc_c = win_cap.simple
        cap_win = win_cap.size()
        cap_pad_h = calc_c.keypad.height()
        cap_mid_y = mid_y(calc_c)

        calc_c._set_panel("bits")
        flush()
        assert win_cap.size() == cap_win
        assert mid_y(calc_c) == cap_mid_y

        drag_vhandle(calc_c, -120)
        assert calc_c.bit_panel.height() > cap_pad_h + 80
        calc_c._set_panel("keypad")
        flush()
        assert win_cap.size() == cap_win
        assert calc_c.keypad.height() == cap_pad_h
    finally:
        win_cap.close()
        app.processEvents()

    # 3) Default compact window (680x545, 100%) and native scaled window (125% / 14pt)
    for name, w_cfg, start_size in (
        ("compact.json", CalcConfig(language="en_US"), (680, 545)),
        ("scaled.json", CalcConfig(language="en_US", ui_scale=125, code_font_size=14), None),
    ):
        cfg2 = tmp_path / name
        w_cfg.save(cfg2)
        win2 = CalculatorWindow("simple", config_path=cfg2)
        if start_size is not None:
            win2.resize(*start_size)
        win2.show()
        flush()
        try:
            calc2 = win2.simple
            base_win = win2.size()
            base_pad_h = calc2.keypad.height()
            base_mid_y = mid_y(calc2)

            calc2._set_panel("bits")
            flush()
            assert win2.size() == base_win
            assert mid_y(calc2) == base_mid_y
            assert calc2.row_frames["FXP"].geometry().bottom() <= calc2.base_scroll.viewport().height()

            calc2._set_panel("keypad")
            flush()
            assert win2.size() == base_win
            assert mid_y(calc2) == base_mid_y
            assert calc2.keypad.height() == base_pad_h

            calc2._set_panel("both")
            flush()
            assert win2.size() == base_win
            assert calc2.row_frames["FXP"].geometry().bottom() <= calc2.base_scroll.viewport().height()

            calc2._set_panel("keypad")
            flush()
            assert win2.size() == base_win
            assert mid_y(calc2) == base_mid_y
            assert calc2.keypad.height() == base_pad_h

            calc2._set_panel("none")
            flush()
            assert win2.width() == base_win.width()
            assert win2.height() < base_win.height()

            calc2._set_panel("keypad")
            flush()
            assert win2.size() == base_win
            assert calc2.keypad.height() == base_pad_h
        finally:
            win2.close()
            app.processEvents()


def test_simple_shift_wheel_scrolls_var_table_and_scroll_areas(tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QTableView
    from PySide6.QtGui import QStandardItem, QStandardItemModel

    from rpkbin.app.calc.core.config import CalcConfig
    from rpkbin.app.calc.ui.app import CalculatorWindow
    from rpkbin.app.calc.ui.theme import install_shift_horizontal_wheel

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "shift_wheel.json"
    CalcConfig(language="en_US").save(cfg_path)
    win = CalculatorWindow("simple", config_path=cfg_path)
    win.resize(680, 545)
    win.show()
    app.processEvents()
    try:
        calc = win.simple
        for idx in range(25):
            calc.engine.variables[f"v{idx}"] = None
        calc._refresh_var_table()
        app.processEvents()

        hbar = calc.var_table.horizontalScrollBar()
        vbar = calc.var_table.verticalScrollBar()
        assert hbar.maximum() > 0 and vbar.maximum() > 0

        def send_wheel(target, dy: int, modifiers):
            ev = QWheelEvent(
                QPointF(24, 24),
                QPointF(24, 24),
                QPoint(0, 0),
                QPoint(0, dy),
                Qt.MouseButton.NoButton,
                modifiers,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            QApplication.sendEvent(target, ev)
            app.processEvents()

        # Shift+Wheel scrolls var_table horizontally without moving vertical scrollbar
        send_wheel(calc.var_table.viewport(), -120, Qt.KeyboardModifier.ShiftModifier)
        assert hbar.value() > 0 and vbar.value() == 0

        # Scrolling back past 0 stays horizontal (no sudden vertical jump)
        send_wheel(calc.var_table.viewport(), 120, Qt.KeyboardModifier.ShiftModifier)
        send_wheel(calc.var_table.viewport(), 120, Qt.KeyboardModifier.ShiftModifier)
        assert hbar.value() == 0 and vbar.value() == 0

        # Regular Wheel (without Shift) still scrolls vertically
        send_wheel(calc.var_table.viewport(), -120, Qt.KeyboardModifier.NoModifier)
        assert vbar.value() > 0 and hbar.value() == 0

        # Shift+Wheel on bit_scroll when horizontally overflowing
        calc._set_panel("bits")
        calc.bits.setMinimumWidth(calc.bit_scroll.viewport().width() + 180)
        app.processEvents()
        bhbar = calc.bit_scroll.horizontalScrollBar()
        bvbar = calc.bit_scroll.verticalScrollBar()
        assert bhbar.maximum() > 0
        send_wheel(calc.bit_scroll.viewport(), -120, Qt.KeyboardModifier.ShiftModifier)
        assert bhbar.value() > 0 and bvbar.value() == 0

        # Shared filter works on QTableView (e.g. MatrixEditor table)
        view = QTableView()
        model = QStandardItemModel(10, 10, view)
        for r in range(10):
            for c in range(10):
                model.setItem(r, c, QStandardItem(f"{r},{c}"))
        view.setModel(model)
        install_shift_horizontal_wheel(view)
        view.resize(180, 140)
        view.show()
        app.processEvents()
        try:
            assert view.horizontalScrollBar().maximum() > 0 and view.verticalScrollBar().maximum() > 0
            send_wheel(view.viewport(), -120, Qt.KeyboardModifier.ShiftModifier)
            assert view.horizontalScrollBar().value() > 0 and view.verticalScrollBar().value() == 0
        finally:
            view.close()
    finally:
        win.close()
        app.processEvents()
