"""Tests for Groups 1, 2, and 3 approved calc improvements."""

import os
import numpy as np
import apytypes as apy
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from rpkbin.app.calc.controller import CalcController
from rpkbin.app.calc.core.config import ALL_WORKSPACE_COLUMNS, CalcConfig
from rpkbin.app.calc.core.editing import selection_statistics
from rpkbin.app.calc.core.format_engine import FormatEngine
from rpkbin.app.calc.core.fxp_model import make_fxp, update_scalar_from_field
from rpkbin.app.calc.core.session import WorkspaceSession
from rpkbin.app.calc.ui.studio_window import CalcMainWindow
from rpkbin.app.calc.ui.inspector_widget import VariableInspectorWidget
from rpkbin.app.calc.ui.settings_dialog import SettingsDialog
from rpkbin.app.calc.ui.workspace_widget import WorkspaceWidget

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ─────────────────────────────────────────────────────────────────────────────
# 1A. column_order Persistence & WorkspaceWidget Header Reordering
# ─────────────────────────────────────────────────────────────────────────────

def test_column_order_persistence_and_normalization(tmp_path):
    cfg_file = tmp_path / "settings.json"
    controller = CalcController(cfg_file)

    # Reorder columns with a custom list (including missing ones that get normalized)
    custom_order = ["hex", "format", "name"]
    controller.set_column_order(custom_order)

    # Controller config must have normalized order (starting with custom, containing all 12)
    assert controller.config.column_order[:3] == ["hex", "format", "name"]
    assert len(controller.config.column_order) == len(ALL_WORKSPACE_COLUMNS)
    assert set(controller.config.column_order) == set(ALL_WORKSPACE_COLUMNS)

    # Verify persistence to disk
    reloaded = CalcConfig.load(cfg_file)
    assert reloaded.column_order == controller.config.column_order


def test_workspace_widget_column_order_restoration_and_drag(qapp, tmp_path):
    cfg_file = tmp_path / "settings.json"
    custom_order = list(ALL_WORKSPACE_COLUMNS)
    # Swap first two columns: "value", "name", ...
    custom_order[0], custom_order[1] = custom_order[1], custom_order[0]
    cfg = CalcConfig(column_order=custom_order)
    cfg.save(cfg_file)

    controller = CalcController(cfg_file)
    widget = WorkspaceWidget(controller)
    h_header = widget.table.horizontalHeader()

    # Visual index of column 0 ("name") should be 1, visual index of column 1 ("value") should be 0
    logical_name = ALL_WORKSPACE_COLUMNS.index("name")
    logical_value = ALL_WORKSPACE_COLUMNS.index("value")
    assert h_header.visualIndex(logical_name) == 1
    assert h_header.visualIndex(logical_value) == 0

    # Simulate sectionMoved (user drag)
    # Move visual section 0 to visual section 2
    h_header.moveSection(0, 2)
    widget._on_section_moved(0, 0, 2)

    # Check controller config was updated
    current_visual_order = [ALL_WORKSPACE_COLUMNS[h_header.logicalIndex(vis)]
                            for vis in range(h_header.count())]
    assert controller.config.column_order == current_visual_order


# ─────────────────────────────────────────────────────────────────────────────
# 1B. Always-on-top Toggle in CalcMainWindow
# ─────────────────────────────────────────────────────────────────────────────

def test_always_on_top_toggle(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(CalcController, "start", lambda self: None)
    controller = CalcController(tmp_path / "settings.json")
    win = CalcMainWindow(controller)

    assert win._always_on_top is False
    assert not win.always_on_top_btn.isChecked()
    assert not win.view_always_on_top_act.isChecked()
    assert not bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    # Toggle on
    win._toggle_always_on_top(True)
    assert win._always_on_top is True
    assert win.always_on_top_btn.isChecked() is True
    assert win.view_always_on_top_act.isChecked() is True
    assert bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is True

    # Calling again with same value is idempotent
    win._toggle_always_on_top(True)
    assert win._always_on_top is True

    # Toggle off
    win._toggle_always_on_top(False)
    assert win._always_on_top is False
    assert win.always_on_top_btn.isChecked() is False
    assert win.view_always_on_top_act.isChecked() is False
    assert bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is False

    win.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1C. Unsigned FXP s2 Editing & Bit Range Pre-fill
# ─────────────────────────────────────────────────────────────────────────────

def test_unsigned_fxp_s2_editing():
    spec = FormatEngine.parse("UQ8.0")
    source = make_fxp(0, spec)

    # In-range negative number with WRAP: -1 in 8-bit unsigned is 255
    res = update_scalar_from_field(source, "s2", "-1", overflow="WRAP")
    assert res.to_bits() == 255
    assert res.is_saturated is False

    # In-range negative number with SAT: [-128, 127] -> -1 is valid, not saturated
    res = update_scalar_from_field(source, "s2", "-1", overflow="SAT")
    assert res.to_bits() == 255
    assert res.is_saturated is False

    # Out of range negative number with SAT: -200 clamped to -128 (0x80)
    res = update_scalar_from_field(source, "s2", "-200", overflow="SAT")
    assert res.to_bits() == 128
    assert res.is_saturated is True

    # Out of range positive number with SAT: 200 clamped to 127 (0x7F)
    res = update_scalar_from_field(source, "s2", "200", overflow="SAT")
    assert res.to_bits() == 127
    assert res.is_saturated is True

    # Out of range negative number with WRAP: -200 & 0xFF = 56
    res = update_scalar_from_field(source, "s2", "-200", overflow="WRAP")
    assert res.to_bits() == 56
    assert res.is_saturated is False

    # Signed FXP remains supported
    spec_signed = FormatEngine.parse("Q8.0")
    source_signed = make_fxp(0, spec_signed)
    res_signed = update_scalar_from_field(source_signed, "s2", "-1", overflow="SAT")
    assert res_signed.to_bits() == 255


def test_edit_bit_range_prefill(qapp, tmp_path, monkeypatch):
    controller = CalcController(tmp_path / "settings.json")
    inspector = VariableInspectorWidget(controller)

    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns["x"] = make_fxp("0xA5", "Q4.4")
    snap = session.get_inspector_snapshot("x")
    inspector.render_snapshot(snap)

    # Verify snapshot properties
    assert inspector._last_snapshot is snap
    assert snap.spec.wl == 8
    assert snap.uint_val == 0xA5

    # Check prompt_text receives default="7:0 = 0xA5"
    captured_default = []
    from rpkbin.app.calc.ui import inspector_widget
    def mock_prompt(parent, title, label, default="", lang="en_US"):
        captured_default.append(default)
        return "7:4 = 0x5", True

    monkeypatch.setattr(inspector_widget, "prompt_text", mock_prompt)
    inspector._edit_bit_field()
    assert captured_default == ["7:0 = 0xA5"]


# ─────────────────────────────────────────────────────────────────────────────
# 1D. SettingsDialog Live Language Preview and Cancel Restoration
# ─────────────────────────────────────────────────────────────────────────────

def test_settings_dialog_live_language_preview_and_reject(qapp, tmp_path):
    cfg = CalcConfig(language="en_US", keep_english_terms=False)
    dialog = SettingsDialog(cfg, config_path=tmp_path / "settings.json")

    assert dialog.windowTitle() == "Settings"
    assert dialog.tabs.tabText(0) == "Appearance"
    assert dialog.save_btn.text() == "Apply"
    assert dialog.cancel_btn.text() == "Cancel"

    # Preview Traditional Chinese
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("zh_TW"))
    qapp.processEvents()

    assert dialog.windowTitle() == "設定"
    assert dialog.tabs.tabText(0) == "外觀"
    assert dialog.save_btn.text() == "套用"
    assert dialog.cancel_btn.text() == "取消"
    # Working config remains untouched until save
    assert cfg.language == "en_US"

    # Reject / Cancel
    dialog.reject()
    qapp.processEvents()

    assert dialog.windowTitle() == "Settings"
    assert dialog.tabs.tabText(0) == "Appearance"
    assert cfg.language == "en_US"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Vectorized selection_statistics for 1D, 2D, and 3D
# ─────────────────────────────────────────────────────────────────────────────

def test_selection_statistics_1d_numpy():
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    session.user_ns["v1"] = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    stats = selection_statistics(session, "v1", (0, 1, 1, 3))  # indices 1..3: 20, 30, 40
    assert stats["count"] == 3
    assert stats["finite_count"] == 3
    assert float(stats["sum"]) == 90.0
    assert float(stats["mean"]) == 30.0
    assert float(stats["min"]) == 20.0
    assert float(stats["max"]) == 40.0


def test_selection_statistics_2d_numpy_with_nan():
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    session.user_ns["v2"] = np.array([[1.0, 2.0], [np.nan, 4.0]])
    stats = selection_statistics(session, "v2", (0, 0, 2, 2))
    assert stats["count"] == 4
    assert stats["finite_count"] == 3
    assert float(stats["sum"]) == 7.0
    assert float(stats["mean"]) == pytest.approx(7.0 / 3.0)


def test_selection_statistics_3d_numpy():
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    session.user_ns["v3"] = np.arange(24, dtype=float).reshape(2, 3, 4)
    stats = selection_statistics(session, "v3", (0, 1, 2, 2))
    # Slice: [0:2, 1:3, 0] -> elements v3[0,1,0]=4, v3[0,2,0]=8, v3[1,1,0]=16, v3[1,2,0]=20
    assert stats["count"] == 4
    assert stats["finite_count"] == 4
    assert float(stats["sum"]) == 4 + 8 + 16 + 20
    assert float(stats["min"]) == 4.0
    assert float(stats["max"]) == 20.0


def test_selection_statistics_1d_apy():
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    session.user_ns["a1"] = apy.APyFixedArray.from_float([0.25, 0.5, 0.75, 1.0], bits=16, frac_bits=8)
    stats = selection_statistics(session, "a1", (0, 1, 1, 2))  # 0.5, 0.75
    assert stats["count"] == 2
    assert stats["finite_count"] == 2
    assert float(stats["sum"]) == pytest.approx(1.25)
    assert float(stats["mean"]) == pytest.approx(0.625)


def test_selection_statistics_2d_apy():
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    session.user_ns["a2"] = apy.APyFixedArray.from_float([[1.0, 2.0], [3.0, 4.0]], bits=16, frac_bits=4)
    stats = selection_statistics(session, "a2", (0, 0, 2, 2))
    assert stats["count"] == 4
    assert stats["finite_count"] == 4
    assert float(stats["sum"]) == 10.0
    assert float(stats["mean"]) == 2.5


def test_selection_statistics_3d_apy():
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    arr = apy.APyFixedArray.from_float(np.arange(24, dtype=float).reshape(2, 3, 4), bits=16, frac_bits=4)
    session.user_ns["a3"] = arr
    stats = selection_statistics(session, "a3", (0, 1, 2, 2))
    assert stats["count"] == 4
    assert stats["finite_count"] == 4
    assert float(stats["sum"]) == 4 + 8 + 16 + 20


def test_apy_fixed_array_vectorized_quantization():
    raw_vals = [-3.875, -1.25, 0.0, 0.625, 1.875, 7.5]
    arr_apy = apy.APyFixedArray.from_float(raw_vals, bits=16, frac_bits=8)

    for quant in ("RND", "TRN"):
        for overflow in ("SAT", "WRAP"):
            res_arr = make_fxp(arr_apy, "Q4.2", quant=quant, overflow=overflow)
            res_scalars = [
                make_fxp(apy.APyFixed.from_float(v, bits=16, frac_bits=8), "Q4.2", quant=quant, overflow=overflow)
                for v in raw_vals
            ]

            expected_bits = np.array([s.to_bits() for s in res_scalars], dtype=object)
            expected_sats = np.array([s.is_saturated for s in res_scalars], dtype=bool)
            expected_targets = np.array([s.target_real for s in res_scalars], dtype=object)

            np.testing.assert_array_equal(res_arr.quantized_bits, expected_bits)
            np.testing.assert_array_equal(res_arr.saturated_mask, expected_sats)
            np.testing.assert_array_equal(res_arr.target_values, expected_targets)



# ─────────────────────────────────────────────────────────────────────────────
# 4. Regressions: Always-on-top visibility & bit cell single-row flattening
# ─────────────────────────────────────────────────────────────────────────────

def test_toggle_always_on_top_keeps_window_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(CalcController, 'start', lambda self: None)
    app = QApplication.instance() or QApplication([])
    controller = CalcController(tmp_path / 'settings.json')
    win = CalcMainWindow(controller)
    try:
        win.show()
        app.processEvents()
        assert win.isVisible()

        # Toggling on should not hide the window
        win._toggle_always_on_top(True)
        app.processEvents()
        assert win.isVisible()
        assert win._always_on_top is True and bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is True

        # Toggling off should keep it visible
        win._toggle_always_on_top(False)
        app.processEvents()
        assert win.isVisible()
        assert win._always_on_top is False and bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) is False
    finally:
        win.close()
        app.processEvents()


def test_fixed_point_q2_4_flattens_bits_without_premature_wrapping(tmp_path, monkeypatch):
    monkeypatch.setattr(CalcController, 'start', lambda self: None)
    app = QApplication.instance() or QApplication([])
    controller = CalcController(tmp_path / 'settings.json')
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns['x'] = make_fxp('1.75', 'Q2.4')
    snapshot = session.get_inspector_snapshot('x')

    widget = VariableInspectorWidget(controller)
    try:
        widget.resize(360, 400)
        widget.show()
        widget.render_snapshot(snapshot)
        for _ in range(3):
            app.processEvents()

        # At width 360, Q2.4 (6 bits padded to 8) fits in 8 columns and must not wrap to 2 rows
        assert widget._bit_layout_key is not None
        assert widget._bit_layout_key[0] == 8
        y_positions = {box.pos().y() for box, *_ in widget._bit_cells.values()}
        assert len(y_positions) == 1, f'Expected all bits on 1 row, got y positions: {y_positions}'
    finally:
        widget.close()
        app.processEvents()


def test_fixed_point_q4_5_9_bits_flattens_on_single_row(tmp_path, monkeypatch):
    monkeypatch.setattr(CalcController, 'start', lambda self: None)
    app = QApplication.instance() or QApplication([])
    controller = CalcController(tmp_path / 'settings.json')
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns['y'] = make_fxp('2.0', 'Q4.5')
    snapshot = session.get_inspector_snapshot('y')

    widget = VariableInspectorWidget(controller)
    try:
        widget.resize(480, 400)
        widget.show()
        widget.render_snapshot(snapshot)
        for _ in range(3):
            app.processEvents()

        # At width 480, Q4.5 (9 bits padded to 12) fits in 12 columns on 1 row
        assert widget._bit_layout_key is not None
        assert widget._bit_layout_key[0] == 12
        y_positions = {box.pos().y() for box, *_ in widget._bit_cells.values()}
        assert len(y_positions) == 1, f'Expected all 9 bits on 1 row, got y positions: {y_positions}'
        # MSB bit 8 (S) must start flush at the left margin (no ghost placeholder indent)
        assert widget._bit_cells[8][0].pos().x() == widget.bit_grid_layout.contentsMargins().left()
    finally:
        widget.close()
        app.processEvents()
