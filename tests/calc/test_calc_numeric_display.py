"""Display precision never changes full-source numeric text used for edits/copies."""

import json
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMenu

from rpkbin.calc.core import CalcConfig, WorkspaceSession
from rpkbin.calc.core.config import KERNEL_CONFIG_FIELDS
from rpkbin.calc.ui.i18n import tr
from rpkbin.calc.ui.numeric_display import MAX_FIXED_CHARACTERS, format_numeric_text
from rpkbin.calc.ui import workspace_widget
from rpkbin.calc.ui.workspace_widget import WorkspaceWidget


@pytest.mark.parametrize("text", [
    "1.234567890123456789", "120000000000000000000000000001", "-0.0", "1e+99",
])
def test_auto_display_is_exact_source(text):
    assert format_numeric_text(text, CalcConfig()) == text


@pytest.mark.parametrize("mode, precision, text, expected", [
    ("fixed", 3, "1.23456", "1.235"),
    ("scientific", 3, "123.456", "1.235E+2"),
    ("fixed", 2, "1.225", "1.22"),
    ("fixed", 2, "1.235", "1.24"),
    ("fixed", 17, "123456789012345678901234567890", "123456789012345678901234567890.00000000000000000"),
    ("scientific", 17, "123456789012345678901234567890", "1.23456789012345679E+29"),
    ("scientific", 3, "0.00000", "0.000E+0"),
    ("scientific", 3, "-0", "-0.000E+0"),
    ("fixed", 3, "0e999999999", "0.000"),
    ("fixed", 3, "1e-999999999", "0.000"),
])
def test_numeric_modes_are_decimal_places_without_float(mode, precision, text, expected):
    assert format_numeric_text(text, CalcConfig(numeric_display=mode, numeric_precision=precision)) == expected


@pytest.mark.parametrize("text", [
    "nan", "NaN", "inf", "-Infinity", "[1.234, 5.678]", "1+2j", "(1+2j)",
    "'1.2345'", '"1.2345"', "name", "True", "0xFF", "1_000", "", "—",
])
@pytest.mark.parametrize("mode", ["fixed", "scientific"])
def test_nonfinite_non_numeric_and_preview_text_is_unchanged(text, mode):
    assert format_numeric_text(text, CalcConfig(numeric_display=mode)) == text


def test_huge_fixed_output_falls_back_to_bounded_scientific():
    output = format_numeric_text("1e999999999", CalcConfig(numeric_display="fixed", numeric_precision=17))
    assert output == "1.00000000000000000E+999999999"
    assert len(output) < MAX_FIXED_CHARACTERS


@pytest.mark.parametrize("display, precision, expected_display, expected_precision", [
    ("invalid", True, "auto", 6), ("fixed", -10, "fixed", 1),
    ("scientific", 99, "scientific", 17), (None, "7", "auto", 6),
])
def test_numeric_preferences_validate_and_load(tmp_path, display, precision, expected_display, expected_precision):
    direct = CalcConfig(numeric_display=display, numeric_precision=precision)
    assert (direct.numeric_display, direct.numeric_precision) == (expected_display, expected_precision)
    path = tmp_path / "calc.json"
    path.write_text(json.dumps({"numeric_display": display, "numeric_precision": precision}), encoding="utf-8")
    loaded = CalcConfig.load(path)
    assert (loaded.numeric_display, loaded.numeric_precision) == (expected_display, expected_precision)


def test_numeric_preferences_roundtrip_and_are_ui_only(tmp_path):
    cfg = CalcConfig(numeric_display="scientific", numeric_precision=17)
    path = tmp_path / "calc.json"
    cfg.save(path)
    assert CalcConfig.load(path) == cfg
    assert "numeric_display" not in KERNEL_CONFIG_FIELDS
    assert "numeric_precision" not in KERNEL_CONFIG_FIELDS


class FakeController(QObject):
    workspace_changed = Signal(object)
    config_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.config = CalcConfig(startup_script="", numeric_display="fixed", numeric_precision=2)
        self.selected_var = "a"

    def select_variable(self, name):
        self.selected_var = name


def test_workspace_rounded_display_preserves_tooltip_dto_and_copy(monkeypatch):
    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    widget = WorkspaceWidget(controller)
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns["a"] = 1.23456789
    row = session.list_variables()[0]
    widget.render_rows([row])
    value_column = 1
    assert widget.table.item(0, value_column).text() == "1.23"
    assert row.value_str in widget.table.item(0, value_column).toolTip()
    assert widget._selected_row_snapshot() is row
    assert row.value_str == "1.23456789"
    copied = []
    monkeypatch.setattr(widget, "_copy_text", copied.append)

    class CopyMenu(QMenu):
        def exec(self, _position):
            next(action for action in self.actions()
                 if action.text() == tr("ctx_copy_real", controller.config.language)).trigger()

    monkeypatch.setattr(workspace_widget, "QMenu", CopyMenu)
    position = widget.table.visualItemRect(widget.table.item(0, 0)).center()
    widget._show_row_context_menu(position)
    assert copied == ["1.23456789"]
    # The diagnostic marker is added after numeric rendering, without mutating source.
    marked = replace(row, is_fxp=True, is_array=True, is_saturated=True)
    widget.render_rows([marked])
    assert widget.table.item(0, value_column).text() == "[SAT] 1.23"
    assert marked.value_str == "1.23456789"
    widget.close()
    app.processEvents()
