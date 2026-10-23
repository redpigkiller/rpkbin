"""Offscreen bit controls retain hardware identity across responsive layouts."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, Signal, Qt
from PySide6.QtWidgets import QApplication

from rpkbin.calc.core import CalcConfig, WorkspaceSession
from rpkbin.calc.core.fxp_model import make_fxp
from rpkbin.calc.ui.inspector_widget import VariableInspectorWidget
from rpkbin.calc.ui.theme import build_stylesheet, get_palette


class Controller(QObject):
    inspector_updated = Signal(object)
    config_changed = Signal(object)
    format_error_occurred = Signal(str)
    operation_finished = Signal(object, str)
    console_executed = Signal(str)
    edit_finished = Signal(object, str)

    def __init__(self, cfg):
        super().__init__()
        self.config = cfg
        self.calls = []

    def _save_config(self):
        pass

    def set_pin_mode(self, mode):
        self.calls.append(("pin", mode))

    def set_inspector_view_mode(self, mode):
        self.config.inspector_view_mode = mode
        self.config_changed.emit(self.config)

    def invert_target_bits(self):
        self.calls.append(("invert",))

    def clear_target_bits(self):
        self.calls.append(("zero",))

    def toggle_bit_order(self):
        self.config.bit_order = "lsb_first" if self.config.bit_order == "msb_first" else "msb_first"
        self.config_changed.emit(self.config)

    def toggle_target_bit(self, index):
        self.calls.append(("bit", index))

    def set_bit_field(self, high, low, value):
        self.calls.append(("field", high, low, value))

    def edit_target_field(self, *args):
        self.calls.append(("edit", *args))
        return True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def settle(app):
    # Viewport resizes and their queued layout requests take separate Qt turns.
    for _ in range(3):
        app.processEvents()


@pytest.fixture
def inspector_factory(app):
    widgets = []

    def create(*, bits=128, theme="dark", font=10, scale=100, width=480, fwl=5):
        cfg = CalcConfig(theme=theme, ui_font_size=font, code_font_size=font, ui_scale=scale)
        controller = Controller(cfg)
        session = WorkspaceSession(cfg, seed_demo_vars=False)
        pattern = ((1 << (bits - 1)) | 0x55) & ((1 << bits) - 1)
        session.user_ns["a"] = make_fxp(hex(pattern), f"Q{bits - fwl}.{fwl}")
        snapshot = session.get_inspector_snapshot("a")
        widget = VariableInspectorWidget(controller)
        widget.setStyleSheet(build_stylesheet(get_palette(theme), cfg))
        widget.apply_appearance(cfg)
        widget.render_snapshot(snapshot)
        widget.resize(width, 900)
        widget.show()
        settle(app)
        widgets.append(widget)
        return widget, controller, snapshot

    yield create
    for widget in widgets:
        widget.close()
    settle(app)


def ordered_indices(widget):
    positions = []
    for index, (box, *_) in widget._bit_cells.items():
        row, column, _, _ = widget.bit_grid_layout.getItemPosition(widget.bit_grid_layout.indexOf(box))
        positions.append((row, column, index))
    return [index for _, _, index in sorted(positions)]


@pytest.mark.parametrize("bits", [128, 256])
@pytest.mark.parametrize("font,scale", [(10, 100), (16, 160)])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_wide_bit_formats_fit_narrow_viewport_without_horizontal_scroll(inspector_factory, bits, font, scale, theme):
    widget, _, _ = inspector_factory(bits=bits, font=font, scale=scale, theme=theme)
    assert widget.width() == 480
    assert len(widget._bit_cells) == bits
    assert widget.bit_scroll.horizontalScrollBar().maximum() == 0
    assert widget.bit_scroll.verticalScrollBar().maximum() > 0
    assert widget.bit_scroll.height() <= widget.bit_scroll.maximumHeight()
    columns = widget._bit_layout_key[0]
    assert columns in (4, 8, 16)
    assert ordered_indices(widget) == list(reversed(range(bits)))
    viewport_width = widget.bit_scroll.viewport().width()
    for index, (box, _, button, _) in widget._bit_cells.items():
        x = box.mapTo(widget.bit_grid_host, QPoint(0, 0)).x()
        assert x >= 0 and x + box.width() <= viewport_width
        assert f"Bit {index}," in button.accessibleName()
        assert button.text() in ("0", "1")


def test_bit_buttons_and_unsubmitted_input_survive_resize_and_refresh(inspector_factory, app):
    widget, controller, snapshot = inspector_factory(bits=256)
    identities = {index: record[2] for index, record in widget._bit_cells.items()}
    edit = widget.card_real.edit
    edit.setFocus()
    edit.setText("0.125 +")
    edit.setModified(True)
    for width in (900, 480, 1200, 480):
        widget.resize(width, 900)
        settle(app)
    widget.render_snapshot(snapshot)
    settle(app)
    assert edit.hasFocus()
    assert edit.text() == "0.125 +" and edit.isModified()
    assert controller.calls == []
    assert {index: record[2] for index, record in widget._bit_cells.items()} == identities
    edit.setModified(False)
    button = identities[255]
    widget.body_scroll.ensureWidgetVisible(widget.bit_scroll)
    button.setFocus()
    settle(app)
    widget.resize(900, 900)
    settle(app)
    assert button.hasFocus()
    assert widget._bit_cells[255][2] is button


def test_bit_order_and_radix_reflow_keep_hardware_click_identity(inspector_factory, app):
    widget, controller, _ = inspector_factory(bits=128, fwl=5)
    buttons = {index: record[2] for index, record in widget._bit_cells.items()}
    assert [index for index, record in widget._bit_cells.items() if record[3].isVisible()] == [5]
    widget.swap_order_btn.click()
    settle(app)
    assert "LSB → MSB" in widget.bit_title_lbl.text()
    assert ordered_indices(widget) == list(range(128))
    assert [index for index, record in widget._bit_cells.items() if record[3].isVisible()] == [4]
    widget.resize(900, 900)
    settle(app)
    assert {index: record[2] for index, record in widget._bit_cells.items()} == buttons
    buttons[127].click()
    buttons[0].click()
    assert controller.calls == [("bit", 127), ("bit", 0)]
    assert widget.swap_order_btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly


def test_bit_action_labels_help_and_colored_legend_explain_semantics(inspector_factory):
    widget, controller, _ = inspector_factory(font=16, scale=160)
    assert widget.inv_btn.text() == "Invert"
    assert widget.zero_btn.text() == "Zero"
    assert widget.field_btn.text().replace("\n", " ") == "Edit bit range"
    assert "keep the variable" in widget.zero_btn.toolTip()
    assert "every hardware bit" in widget.inv_btn.toolTip()
    assert "[high:low]" in widget.field_btn.toolTip()
    assert "Click a bit to toggle" in widget.bit_help_lbl.text()
    palette = get_palette(controller.config.theme)
    for color in (palette.danger, palette.warning, palette.accent):
        assert color in widget.legend_lbl.text()
    assert "■" not in widget.legend_lbl.text()
    for button in (widget.inv_btn, widget.zero_btn, widget.field_btn):
        assert button.isVisible()
        assert button.width() >= max(button.fontMetrics().horizontalAdvance(line) for line in button.text().splitlines())
    widget.inv_btn.click()
    widget.zero_btn.click()
    assert controller.calls == [("invert",), ("zero",)]


def test_legend_colors_update_with_theme_without_replacing_bit_buttons(inspector_factory, app):
    widget, controller, _ = inspector_factory(theme="dark")
    button = widget._bit_cells[127][2]
    controller.config.theme = "light"
    widget.setStyleSheet(build_stylesheet(get_palette("light"), controller.config))
    widget.apply_appearance(controller.config)
    settle(app)
    assert get_palette("light").danger in widget.legend_lbl.text()
    assert widget._bit_cells[127][2] is button


def test_bit_help_and_actions_are_hidden_until_an_fxp_value_is_selected(app):
    controller = Controller(CalcConfig())
    widget = VariableInspectorWidget(controller)
    for control in (widget.bit_help_lbl, widget.legend_lbl, widget.bit_actions_host, widget.bit_scroll):
        assert control.isHidden()
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns["a"] = 3
    widget.render_snapshot(session.get_inspector_snapshot("a"))
    for control in (widget.bit_help_lbl, widget.legend_lbl, widget.bit_actions_host, widget.bit_scroll):
        assert control.isHidden()
    assert widget._bit_cells == {}
    widget.close()
    settle(app)


def test_edit_bit_range_action_keeps_inclusive_high_low_contract(inspector_factory, monkeypatch):
    from rpkbin.calc.ui import inspector_widget

    widget, controller, _ = inspector_factory()
    monkeypatch.setattr(inspector_widget, "prompt_text", lambda *args: ("7:4 = 0xA", True))
    widget.field_btn.click()
    assert controller.calls == [("field", 7, 4, 10)]


@pytest.mark.parametrize("fwl", [-3, 0, 128, 160])
def test_radix_is_absent_when_binary_point_is_outside_hardware_bits(inspector_factory, fwl):
    widget, _, _ = inspector_factory(fwl=fwl)
    assert not any(record[3].isVisible() for record in widget._bit_cells.values())


def test_shrinking_word_length_keeps_remaining_bit_widgets_and_removes_old_cells(inspector_factory, app):
    widget, controller, _ = inspector_factory(bits=256)
    old_scroll_range = widget.bit_scroll.verticalScrollBar().maximum()
    original = widget._bit_cells[0][2]
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns["a"] = make_fxp("0xA5", "Q4.4")
    widget.render_snapshot(session.get_inspector_snapshot("a"))
    settle(app)
    assert len(widget._bit_cells) == 8
    assert widget._bit_cells[0][2] is original
    assert ordered_indices(widget) == list(reversed(range(8)))
    assert widget.bit_scroll.verticalScrollBar().maximum() < old_scroll_range
    assert widget.bit_grid_host.minimumSizeHint().height() < 150


def test_value_card_titles_keep_readable_width_next_to_pin_and_copy(inspector_factory):
    widget, _, _ = inspector_factory(bits=128, width=480)
    for card in widget._cards:
        if card.isHidden():
            continue
        label = card.title_lbl
        assert label.isVisible()
        assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
        assert label.height() > 0


@pytest.mark.parametrize("kind", ["int", "numpy_int", "float", "apyfloat"])
def test_numeric_representations_copy_without_enabling_native_bit_edits(inspector_factory, app, kind):
    import apytypes as apy
    import numpy as np

    widget, controller, _ = inspector_factory(bits=8, fwl=4)
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    values = {"int": -255, "numpy_int": np.int16(-2), "float": np.float32(1.5),
              "apyfloat": apy.APyFloat.from_float(1.5, exp_bits=5, man_bits=2, bias=7)}
    session.user_ns["x"] = values[kind]
    snapshot = session.get_inspector_snapshot("x")
    widget.render_snapshot(snapshot)
    settle(app)
    assert widget.card_hex.isVisible() and widget.card_bin.isVisible()
    assert widget.card_hex.edit.isReadOnly() and widget.card_bin.edit.isReadOnly()
    assert not widget.card_real.pin_btn.isVisible() and not widget.card_hex.pin_btn.isVisible()
    assert not widget.inv_btn.isVisible() and not widget.zero_btn.isVisible() and not widget.field_btn.isVisible()
    assert not widget.fmt_edit.isEnabled()
    for card, key in ((widget.card_hex, "hex"), (widget.card_bin, "binary")):
        card.copy_btn.click()
        assert app.clipboard().text() == snapshot.native_bits[key]
    if kind in ("float", "apyfloat"):
        assert len(widget._bit_cells) == snapshot.native_bits["width"]
        assert all(button.isEnabled() == (kind == "apyfloat") for _, _, button, _ in widget._bit_cells.values())
        assert not any(dot.isVisible() for _, _, _, dot in widget._bit_cells.values())
        assert "Exponent" in widget.legend_lbl.text()
        controller.calls.clear()
        widget._bit_cells[0][2].click()
        assert controller.calls == ([("bit", 0)] if kind == "apyfloat" else [])
    else:
        assert not widget._bit_cells
    if kind == "apyfloat":
        assert widget.card_real.edit.isReadOnly()
        assert widget.card_real.edit.text() == "1.5"


def test_float_language_and_type_switch_preserve_values_and_restore_fxp_controls(inspector_factory, app):
    import apytypes as apy
    from dataclasses import replace

    widget, controller, fxp_snapshot = inspector_factory(bits=8, fwl=4)
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns["f"] = apy.APyFloat.from_bits(0x7D, exp_bits=5, man_bits=2)
    widget.render_snapshot(session.get_inspector_snapshot("f"))
    button = widget._bit_cells[0][2]
    controller.calls.clear()
    cfg = replace(controller.config, language="zh_TW", keep_english_terms=False)
    controller.config = cfg
    widget.retranslate_ui(cfg)
    settle(app)
    assert "非數值" in widget.native_bits_label.text()
    assert "指數" in widget.legend_lbl.text()
    assert widget.card_hex.edit.text() == "0x7D"
    assert widget._bit_cells[0][2] is button
    assert controller.calls == []
    widget.render_snapshot(fxp_snapshot)
    settle(app)
    assert widget.card_bin.isHidden()
    assert widget.card_hex.pin_btn.isVisible()
    assert widget.inv_btn.isVisible() and widget.zero_btn.isVisible()
    assert widget.fmt_edit.isEnabled()
    assert all(button.isEnabled() for _, _, button, _ in widget._bit_cells.values())
    assert "整數" in widget.legend_lbl.text()


def test_large_int_truncation_disables_copy_of_incomplete_previews(inspector_factory, app):
    widget, controller, _ = inspector_factory(bits=8, fwl=4)
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns["x"] = 1 << 100_000
    widget.render_snapshot(session.get_inspector_snapshot("x"))
    settle(app)
    assert "truncated" in widget.native_bits_label.text()
    assert not widget.card_hex.copy_btn.isEnabled()
    assert not widget.card_bin.copy_btn.isEnabled()
    assert not widget.card_real.copy_btn.isEnabled()


def test_rebinding_native_value_to_apyfloat_clears_stale_numeric_input(inspector_factory, app):
    import apytypes as apy

    widget, controller, fxp_snapshot = inspector_factory(bits=8, fwl=4)
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns["x"] = 1
    widget.render_snapshot(session.get_inspector_snapshot("x"))
    assert widget.card_hex.edit.accessibleName() == "Hex (integer value)"
    card = widget.card_real
    card.edit.setText("invalid previous input")
    card.edit.setModified(True)
    card._failed_target = card._target
    session.user_ns["x"] = apy.APyFloat.from_float(1.5, exp_bits=5, man_bits=2)
    widget.render_snapshot(session.get_inspector_snapshot("x"))
    settle(app)
    assert card.edit.text() == "1.5" and card.edit.isReadOnly()
    assert card.error_label.isHidden()
    controller.calls.clear()
    card.edit.editingFinished.emit()
    assert not controller.calls
    assert widget.card_hex.edit.accessibleName() == "Hex (Raw Bits)"
    widget.render_snapshot(fxp_snapshot)
    assert widget.card_hex.title_lbl.text() == "Hex (Raw Bits)"


@pytest.mark.parametrize("font,scale", [(10, 100), (16, 160)])
def test_inspector_header_title_has_preferred_readable_width_without_blocking_narrow_resize(inspector_factory, app, font, scale):
    widget, _, _ = inspector_factory(font=font, scale=scale)
    for width in (900, 480):
        widget.resize(width, 900)
        settle(app)
        assert widget.width() == width
        assert widget.title_lbl.width() >= widget.title_lbl.fontMetrics().horizontalAdvance(widget.title_lbl.text())


def assert_hardware_nibbles_are_not_split(widget, bits, msb_first):
    groups = {}
    for index, (box, *_) in widget._bit_cells.items():
        row, column, _, _ = widget.bit_grid_layout.getItemPosition(widget.bit_grid_layout.indexOf(box))
        groups.setdefault((row, column // 5), []).append(index)
    hardware_groups = {}
    for visual_group in groups.values():
        nibble_ids = {index // 4 for index in visual_group}
        assert len(nibble_ids) == 1, f"Unrelated hardware nibbles grouped together: {visual_group}"
        nibble = next(iter(nibble_ids))
        assert nibble not in hardware_groups, f"Hardware nibble {nibble} split across rows/groups"
        hardware_groups[nibble] = sorted(visual_group)
    assert hardware_groups == {
        nibble: list(range(nibble * 4, min(bits, nibble * 4 + 4)))
        for nibble in range((bits + 3) // 4)
    }
    assert ordered_indices(widget) == (list(reversed(range(bits))) if msb_first else list(range(bits)))


@pytest.mark.parametrize("bits", [1, 3, 6, 10, 17])
@pytest.mark.parametrize("msb_first", [True, False])
@pytest.mark.parametrize("width,font,scale", [(480, 16, 160), (1200, 10, 100)])
def test_partial_highest_nibble_stays_hardware_aligned_across_resizes(
        inspector_factory, app, bits, msb_first, width, font, scale):
    fwl = 5 if bits > 5 else 1 if bits > 1 else 0
    widget, controller, snapshot = inspector_factory(bits=bits, fwl=fwl, width=width, font=font, scale=scale)
    if not msb_first:
        widget.swap_order_btn.click()
        settle(app)
    identities = {index: record[2] for index, record in widget._bit_cells.items()}
    assert len(identities) == bits
    assert_hardware_nibbles_are_not_split(widget, bits, msb_first)
    expected_raw = snapshot.uint_val
    assert sum(int(button.text()) << index for index, button in identities.items()) == expected_raw
    expected_radix = ([fwl if msb_first else fwl - 1] if 0 < fwl < bits else [])
    assert [index for index, record in widget._bit_cells.items() if record[3].isVisible()] == expected_radix
    focused = identities[bits - 1]
    widget.body_scroll.ensureWidgetVisible(widget.bit_scroll)
    widget.bit_scroll.ensureWidgetVisible(focused)
    focused.setFocus()
    settle(app)
    widget.resize(900 if width == 480 else 480, 900)
    settle(app)
    assert_hardware_nibbles_are_not_split(widget, bits, msb_first)
    assert widget.bit_scroll.horizontalScrollBar().maximum() == 0
    assert focused.hasFocus()
    assert {index: record[2] for index, record in widget._bit_cells.items()} == identities
    assert sum(int(button.text()) << index for index, button in identities.items()) == expected_raw
    for index in dict.fromkeys((0, bits - 1)):
        identities[index].click()
    assert controller.calls == [("bit", index) for index in dict.fromkeys((0, bits - 1))]


def test_bit_direction_title_receives_width_alongside_order_button(inspector_factory, app):
    widget, controller, _ = inspector_factory()
    title = widget.bit_title_lbl
    assert title.width() >= title.fontMetrics().horizontalAdvance(title.text())
    controller.toggle_bit_order()
    settle(app)
    assert "LSB → MSB" in title.text()
    assert title.width() >= title.fontMetrics().horizontalAdvance(title.text())
