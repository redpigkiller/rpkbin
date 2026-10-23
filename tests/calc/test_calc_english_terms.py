"""English label preference never translates code/data or rebuilds live views."""

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from rpkbin.calc.controller import CalcController
from rpkbin.calc.core import CalcConfig, WorkspaceSession
from rpkbin.calc.core.config import KERNEL_CONFIG_FIELDS
from rpkbin.calc.ui.app import CalcMainWindow
from rpkbin.calc.ui.i18n import UiText, t, tr
from rpkbin.calc.ui.matrix_editor import MatrixEditorPage


def test_exact_owned_labels_only_use_english_policy():
    assert tr("col_name", "zh_TW") == "名稱"
    assert tr("col_name", "zh_TW", keep_english_terms=True) == "Name"
    assert t("Format:", "zh_TW", keep_english_terms=True) == "Format:"
    assert t("Edit", "zh_TW", keep_english_terms=True) == "編輯"
    assert t("Copy {title}", "zh_TW", keep_english_terms=True, title="Name_名稱") == "複製Name_名稱"
    assert t("Matrix: {name}", "zh_TW", keep_english_terms=True, name="Name_Format_命令視窗") == "陣列：Name_Format_命令視窗"
    assert "keep_english_terms" not in KERNEL_CONFIG_FIELDS


@pytest.fixture
def window(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setattr(CalcController, "start", lambda self: None)
    controller = CalcController(tmp_path / "settings.json")
    controller.config = replace(controller.config, language="zh_TW")
    view = CalcMainWindow(controller)
    qt.processEvents()
    yield qt, controller, view
    view.close()
    qt.processEvents()


def test_live_policy_updates_headers_and_cards_without_replacing_user_state(window):
    qt, controller, view = window
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    session.execute('名稱 = APyFixed.from_float(.25, bits=8, frac_bits=4)\nA = APyFixedArray.from_float([[.25]], bits=8, frac_bits=4)')
    controller.selected_var = "名稱"
    workspace = view.workspace_widget
    workspace.render_rows(session.list_variables())
    inspector = view.inspector_widget
    inspector.render_snapshot(session.get_inspector_snapshot("名稱"))
    inspector.card_real.edit.setText("0.125 + # 未完成")
    inspector.card_real.edit.setModified(True)
    original_item = workspace.table.item(0, 0)
    console = view.console_widget.term
    console._show_interpreter_prompt(1)
    console.input_buffer = '名稱 = "Name_Format_命令視窗"'
    view.console_widget.command_history.record('名稱 = "Value"')
    page = MatrixEditorPage("A", controller)
    page.render_snapshot(session.get_matrix_snapshot("A"))
    view._matrix_tabs["A"] = page
    view.left_tabs.addTab(page, "A")
    model = page.model
    page.table.setCurrentIndex(model.index(0, 0))
    assert workspace.title_lbl.text() == "工作區"
    assert inspector.card_real.title_lbl.text() == tr("lbl_real_value", "zh_TW")
    for policy in (True, False, True):
        controller.update_config(replace(controller.config, keep_english_terms=policy))
        qt.processEvents()
        assert view.left_tabs.tabText(0) == ("Command Window" if policy else "命令視窗")
        assert workspace.title_lbl.text() == ("Workspace" if policy else "工作區")
        assert workspace.table.horizontalHeaderItem(0).text() == ("Name" if policy else "名稱")
        assert inspector.title_lbl.text() == ("Inspector" if policy else "變數檢視器")
        assert inspector.card_real.title_lbl.text() == ("Real Value" if policy else tr("lbl_real_value", "zh_TW"))
        assert inspector.card_s2.title_lbl.text() == ("2's Comp (Signed Int)" if policy else "二補數（有號整數）")
        assert page.fmt_lbl.text() == ("Array format:" if policy else "陣列格式：")
        assert model.headerData(0, Qt.Orientation.Horizontal) == ("Col 0" if policy else "欄 0")
        assert view.menuBar().actions()[1].text() == "編輯"
        assert workspace.table.item(0, 0) is original_item
        assert workspace.table.currentRow() >= 0
        assert inspector.target_badge.text() == "名稱"
        assert inspector.card_real.edit.text() == "0.125 + # 未完成"
        assert inspector.card_real.edit.isModified()
        assert view.console_widget.term is console
        assert console.input_buffer == '名稱 = "Name_Format_命令視窗"'
        assert view.console_widget.command_history.commands == ['名稱 = "Value"']
        assert page.model is model
        assert page.table.currentIndex() == model.index(0, 0)
        assert not controller._mutations


def test_binding_refresh_changes_policy_without_changing_language(window):
    label = QLabel()
    copy = UiText("zh_TW")
    copy.bind(label, "Name")
    assert label.text() == "名稱"
    copy.refresh("zh_TW", keep_english_terms=True)
    assert label.text() == "Name"
    copy.refresh("en_US", keep_english_terms=False)
    assert label.text() == "Name"
