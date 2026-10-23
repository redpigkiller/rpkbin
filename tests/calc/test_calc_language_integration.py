"""App-owned bilingual copy and state-preserving main-window changes."""

import os
from dataclasses import replace
from string import Formatter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from rpkbin.calc.controller import CalcController
from rpkbin.calc.core import CalcConfig, WorkspaceSession
from rpkbin.calc.core.config import KERNEL_CONFIG_FIELDS
from rpkbin.calc.ui.app import CalcMainWindow
from rpkbin.calc.ui.i18n import STRINGS, t
from rpkbin.calc.ui.settings_dialog import SettingsDialog
from rpkbin.calc.ui.translations import app, console, inspector, matrix, settings, workspace


def test_catalog_templates_preserve_every_parameter_and_shared_key():
    modules = (app, console, inspector, matrix, settings, workspace)
    formatter = Formatter()
    def fields(text):
        return sorted(field for _, field, _, _ in formatter.parse(text) if field is not None)

    sources = set()
    for module in modules:
        for source, translated in module.ZH_TW.items():
            assert translated.strip(), source
            assert fields(source) == fields(translated), source
            sources.add(source)
    assert set(STRINGS["en_US"].values()) <= sources
    assert "language" not in KERNEL_CONFIG_FIELDS
    assert t("Matrix: {name}", "zh_TW", name="my_{name}_變數") == "陣列：my_{name}_變數"


@pytest.fixture
def window(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setattr(CalcController, "start", lambda self: None)
    controller = CalcController(tmp_path / "settings.json")
    view = CalcMainWindow(controller)
    qt.processEvents()
    yield qt, view, controller
    view.close()
    qt.processEvents()


def test_language_switch_keeps_console_input_and_never_queues_kernel_config(window):
    qt, view, controller = window
    original_console = view.console_widget.term
    original_client = controller.transport.client
    original_inspector = view.inspector_widget
    original_console._show_interpreter_prompt(1)
    original_console.input_buffer = "np.array([1, 2]) + # 未完成"
    view.console_widget.command_history.record("數值 = 42")
    session = WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)
    session.execute("數值 = APyFixed.from_float(.25, bits=16, frac_bits=12)")
    original_inspector.render_snapshot(session.get_inspector_snapshot("數值"))
    original_inspector.card_real.edit.setText("0.125 +")
    original_inspector.card_real.edit.setModified(True)
    for language, menus, idle in (
        ("zh_TW", ["檔案", "編輯", "檢視", "說明"], "待命"),
        ("en_US", ["File", "Edit", "View", "Help"], "Idle"),
    ):
        controller.update_config(replace(controller.config, language=language))
        qt.processEvents()
        assert [action.text() for action in view.menuBar().actions()] == menus
        assert view.console_widget.interrupt_btn.text() == idle
        assert view.console_widget.term is original_console
        assert controller.transport.client is original_client
        assert original_console.input_buffer == "np.array([1, 2]) + # 未完成"
        assert view.console_widget.command_history.commands == ["數值 = 42"]
        assert view.inspector_widget is original_inspector
        assert original_inspector.card_real.edit.text() == "0.125 +"
        assert original_inspector.card_real.edit.isModified()
        assert not controller._mutations


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_checkbox_indicator_changes_visibly_when_toggled(window, theme):
    qt, view, controller = window
    assert qt.style().objectName().casefold() == "fusion"
    controller.update_config(replace(controller.config, theme=theme))
    dialog = SettingsDialog(controller.config, view)
    dialog.tabs.setCurrentIndex(2)
    dialog.show()
    qt.processEvents()
    checkbox = dialog._group_checks["basic_math"]
    checkbox.setChecked(False)
    qt.processEvents()
    unchecked = checkbox.grab().toImage()
    checkbox.setChecked(True)
    qt.processEvents()
    checked = checkbox.grab().toImage()
    changed = sum(unchecked.pixel(x, y) != checked.pixel(x, y)
                  for x in range(min(20, checked.width())) for y in range(checked.height()))
    assert changed > 5, "A checked preference must look different from an unchecked one"
    dialog.close()
