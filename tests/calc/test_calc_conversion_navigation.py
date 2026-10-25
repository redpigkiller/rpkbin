"""One Inspector conversion path, explicit type changes and read-only navigation."""

import os
from dataclasses import asdict
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import apytypes as apy
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMenu

from rpkbin.calc.controller import CalcController
from rpkbin.calc.core import CalcConfig, WorkspaceSession, make_fxp
from rpkbin.calc.core.config import ALL_WORKSPACE_COLUMNS
from rpkbin.calc.core.format_engine import FormatEngine
from rpkbin.calc.core.fxp_model import InspectorSnapshot
from rpkbin.calc.kernel_runtime import CalcKernelRuntime
from rpkbin.calc.protocol import PROTOCOL_VERSION, inspector_from_dict, inspector_to_dict
from rpkbin.calc.ui.console_widget import CommandWindowWidget
from rpkbin.calc.ui.inspector_widget import VariableInspectorWidget
from rpkbin.calc.ui.workspace_widget import WorkspaceWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui(app, tmp_path, monkeypatch):
    controller = CalcController(tmp_path / "settings.json")
    controller.revision = 7
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    inspector = VariableInspectorWidget(controller)
    inspector.resize(600, 650)
    inspector.show()
    requests = []
    monkeypatch.setattr(controller, "_mutate", lambda *args, **kw: requests.append((args, kw)) or True)
    yield inspector, controller, session, requests
    inspector.close()
    app.processEvents()


def select(inspector, controller, session, value):
    session.user_ns["a"] = value
    controller.selected_var = "a"
    inspector.render_snapshot(session.get_inspector_snapshot("a"))


@pytest.mark.parametrize("value,capable", [
    (2, True), (0.25, True), (np.int16(2), True),
    (np.array([[0.25, 0.5]]), True), ([1, 2], True),
    (np.array([[1, 2]], dtype=object), False),
    (np.array([[1, 2j]]), False), ([1, "two"], False),
    (complex(2), False), (apy.APyFloat.from_float(0.25, exp_bits=8, man_bits=23), False),
])
def test_capability_describes_whole_variable_and_survives_transport(ui, value, capable):
    inspector, controller, session, _ = ui
    select(inspector, controller, session, value)
    snapshot = inspector._last_snapshot
    assert snapshot.can_convert_to_fxp is capable
    assert inspector_from_dict(inspector_to_dict(snapshot)).can_convert_to_fxp is capable
    assert inspector.convert_btn.isEnabled() is capable
    assert inspector.format_host.isHidden()
    assert not inspector.fmt_edit.isEnabled()


def test_native_conversion_preview_forces_numeric_pin_and_preserves_draft(ui, monkeypatch):
    inspector, controller, session, requests = ui
    controller.config.inspector_pin_mode = "hex"
    select(inspector, controller, session, 0.25)
    inspector.convert_btn.click()
    dialog = inspector._conversion_dialog
    dialog.fmt_edit.setText("Q4.8")
    previews = []
    monkeypatch.setattr(controller, "request_preview", lambda *args, **kw: previews.append((args, kw)))
    dialog.preview_btn.click()
    assert previews == [(("a", FormatEngine.parse("Q4.8")), {"pin_mode": "real"})]
    inspector.render_snapshot(session.get_inspector_snapshot("a"))
    assert dialog.fmt_edit.text() == "Q4.8"
    dialog.apply_btn.click()
    assert requests == [(("reformat", "a", "Q4.8"), {"pin_mode": "real", "expected_revision": 7})]
    assert inspector._conversion_dialog is None



def test_fxp_format_uses_existing_pin_and_float_target_is_explicit(ui, monkeypatch):
    inspector, controller, session, requests = ui
    controller.config.inspector_pin_mode = "hex"
    select(inspector, controller, session, make_fxp(0.25, "Q4.12"))
    inspector.fmt_edit.setText("Q8.8")
    inspector._on_format_committed()
    assert requests[-1] == (("reformat", "a", "Q8.8"), {"pin_mode": "hex", "expected_revision": 7})
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr("rpkbin.calc.ui.inspector_widget.ask", lambda *args: QMessageBox.StandardButton.Apply)
    inspector.convert_btn.click()
    assert requests[-1] == (("to_float", "a"), {"expected_revision": 7})


def test_equivalent_and_invalid_format_do_not_send_mutations(ui):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, make_fxp([0.25, 0.5], "Q4.12"))
    inspector.fmt_edit.setText("sfix16_En12")
    inspector._on_format_committed()
    assert not requests
    inspector.fmt_edit.setText("invalid-format")
    inspector.fmt_edit.setModified(True)
    inspector._on_format_committed()
    assert not requests
    assert inspector.fmt_edit.text() == "invalid-format"
    assert inspector.fmt_edit.isModified()
    assert not inspector.error_lbl.isHidden()
    assert isinstance(session.get_inspector_snapshot("a"), InspectorSnapshot)


@pytest.mark.parametrize("change", ["revision", "pin", "quant", "overflow"])
def test_pending_conversion_requires_review_after_source_or_policy_changes(ui, change):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, make_fxp(0.25, "Q4.12"))
    inspector.fmt_edit.setText("Q8.8")
    inspector._begin_conversion_edit()
    if change == "revision":
        controller.revision += 1
    elif change == "pin":
        controller.config.inspector_pin_mode = "hex"
    elif change == "quant":
        controller.config.default_quant = "TRN"
    else:
        controller.config.default_overflow = "WRAP"
    inspector._on_format_committed()
    assert not requests
    assert "changed" in inspector.error_lbl.text()
    assert inspector.fmt_edit.text() == "Q8.8"


def test_custom_display_format_remains_round_trippable_and_language_preserves_input(ui):
    inspector, controller, session, requests = ui
    controller.config.format_preset = "custom"
    controller.config.custom_signed_tpl = "signed:{wl}/{fwl}"
    select(inspector, controller, session, make_fxp(0.25, "Q4.12"))
    assert inspector.fmt_edit.text() == "signed:16/12"
    inspector.fmt_edit.setText("Q8.8")
    inspector.fmt_edit.setModified(True)
    controller.config.language = "zh_TW"
    inspector.retranslate_ui(controller.config)
    assert inspector.convert_btn.text() == "轉為 float…"
    assert inspector.fmt_edit.text() == "Q8.8"
    assert inspector.fmt_edit.isModified()
    assert not requests


def test_workspace_shortcuts_only_navigate_and_keep_array_editor(ui, monkeypatch):
    inspector, controller, session, requests = ui
    session.user_ns.update(a=0.25, A=np.array([[1, 2]]))
    workspace = WorkspaceWidget(controller)
    controller.rows = session.list_variables()
    workspace.render_rows(controller.rows)
    navigation, opened, selected = [], [], []
    workspace.inspect_requested.connect(lambda *args: navigation.append(args))
    monkeypatch.setattr(controller, "select_variable", selected.append)
    monkeypatch.setattr(controller, "open_matrix_editor", opened.append)
    try:
        rows = {row.name: index for index, row in enumerate(workspace._visible_rows)}
        workspace._on_cell_double_clicked(rows["a"], ALL_WORKSPACE_COLUMNS.index("value"))
        workspace._on_cell_double_clicked(rows["A"], ALL_WORKSPACE_COLUMNS.index("value"))
        workspace._on_cell_double_clicked(rows["A"], ALL_WORKSPACE_COLUMNS.index("format"))
        assert navigation == [("a", "value"), ("A", "format")]
        assert opened == ["A"] and selected == ["a", "A"]
        menus = []
        class MenuProbe(QMenu):
            def exec(self, *_args):
                menus.append(self)
        monkeypatch.setattr("rpkbin.calc.ui.workspace_widget.QMenu", MenuProbe)
        workspace.show()
        app = QApplication.instance()
        app.processEvents()
        item = workspace.table.item(rows["a"], 0)
        workspace._show_row_context_menu(workspace.table.visualItemRect(item).center())
        action = next(a for a in menus[0].actions() if a.text() == "Convert / adjust format in Inspector…")
        action.trigger()
        assert navigation[-1] == ("a", "format")
        assert not requests
    finally:
        workspace.close()


def test_navigation_waits_for_correct_snapshot_and_preserves_console_draft(ui, app):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, 0.25)
    console = CommandWindowWidget(controller)
    console.term._show_interpreter_prompt(1)
    console.term.input_buffer = "np.array([ # draft"
    cursor = console.term._control.textCursor()
    cursor.movePosition(cursor.MoveOperation.Left, cursor.MoveMode.MoveAnchor, 3)
    console.term._control.setTextCursor(cursor)
    position = cursor.position()
    inspector.body_widget.setEnabled(False)
    inspector.focus_target("a", "format")
    assert inspector._pending_focus == ("a", "format")
    inspector.body_widget.setEnabled(True)
    inspector.activateWindow()
    inspector._interaction_changed(True)
    app.processEvents()
    assert inspector.convert_btn.hasFocus()
    assert console.term.input_buffer == "np.array([ # draft"
    assert console.term._control.textCursor().position() == position
    controller.selected_var = "other"
    inspector.focus_target("a", "value")
    assert inspector._pending_focus is None
    assert not requests
    console.close()


def test_kernel_atomically_rejects_stale_conversion_without_losing_undo():
    shell = SimpleNamespace(user_ns={}, events=SimpleNamespace(register=lambda *_args: None),
                            register_magic_function=lambda *_args: None)
    runtime = CalcKernelRuntime(shell, asdict(CalcConfig(startup_script="")))
    runtime.session.user_ns["a"] = 2
    runtime.revision = 4
    request = {"protocol": PROTOCOL_VERSION, "build": runtime.build,
               "op": "mutate", "action": "reformat", "args": ["a", "Q4.4"],
               "kwargs": {"pin_mode": "real"}, "selected_var": "a", "expected_revision": 3}
    before = runtime.journal.state()
    reply = runtime.dispatch(request).data
    assert "Workspace changed" in reply["error"]
    assert runtime.session.user_ns["a"] == 2
    assert type(runtime.session.user_ns["a"]) is int
    assert runtime.revision == 4 and runtime.journal.state() == before
    request["expected_revision"] = 4
    assert not runtime.dispatch(request).data.get("error")
    assert isinstance(runtime.session.user_ns["a"], apy.APyFixed)



def test_fxp_cell_in_a_python_list_does_not_enable_whole_array_conversion(ui):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, [make_fxp(0.25, "Q4.12"), "text"])
    assert isinstance(inspector._last_snapshot, InspectorSnapshot)
    assert not inspector._last_snapshot.whole_variable_fxp
    assert not inspector.convert_btn.isEnabled()
    assert not inspector.apply_conversion_btn.isEnabled()
    assert not requests


@pytest.mark.parametrize("change", [None, "revision", "pin", "quant", "overflow", "same_format"])
def test_preview_apply_checks_captured_source_and_policy(ui, monkeypatch, change):
    from PySide6.QtWidgets import QMessageBox
    from rpkbin.calc.core.editing import conversion_preview
    from rpkbin.calc.ui.app import CalcMainWindow
    from rpkbin.calc.ui.i18n import UiText

    inspector, controller, session, requests = ui
    select(inspector, controller, session, make_fxp(0.25, "Q4.12"))
    controller.rows = session.list_variables()
    target = "sfix16_En12" if change == "same_format" else "Q8.8"
    preview = dict(conversion_preview(session, "a", target), revision=controller.revision)
    errors = []
    holder = SimpleNamespace(controller=controller, _copy=UiText(), _show_error=errors.append)
    def accept(*_args, **_kwargs):
        if change == "revision":
            controller.revision += 1
        elif change == "pin":
            controller.config.inspector_pin_mode = "hex"
        elif change == "quant":
            controller.config.default_quant = "TRN"
        elif change == "overflow":
            controller.config.default_overflow = "WRAP"
        return QMessageBox.StandardButton.Apply
    monkeypatch.setattr("rpkbin.calc.ui.app.ask", accept)
    CalcMainWindow._show_preview(holder, preview)
    if change is None:
        assert requests == [(("reformat", "a", target), {"pin_mode": "real", "expected_revision": 7})]
        assert not errors
    elif change == "same_format":
        assert not requests and not errors
    else:
        assert not requests and "changed" in errors[-1]


@pytest.mark.parametrize("persistent", [False, True])
def test_temporary_cleanup_retries_windows_locks_without_crashing(ui, monkeypatch, persistent):
    _, controller, _, _ = ui
    attempts, sleeps = [], []
    def cleanup():
        attempts.append(True)
        if persistent or len(attempts) < 3:
            raise PermissionError("history.sqlite is still locked")
    controller.transport._temporary = SimpleNamespace(cleanup=cleanup)
    monkeypatch.setattr("rpkbin.calc.kernel_client.time.sleep", sleeps.append)
    if persistent:
        with pytest.warns(RuntimeWarning, match="temporary files"):
            controller.transport._cleanup_temporary()
    else:
        controller.transport._cleanup_temporary()
    assert len(attempts) == (4 if persistent else 3)
    assert sum(sleeps) <= 0.31
    assert controller.transport._temporary is None


@pytest.mark.parametrize("change", ["revision", "quant", "pin"])
def test_native_dialog_rejects_stale_sources_and_keeps_invalid_format(ui, change):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, 2)
    inspector.convert_btn.click()
    dialog = inspector._conversion_dialog
    dialog.fmt_edit.setText("bad-format")
    dialog.apply_btn.click()
    assert not requests and not dialog.apply_btn.isEnabled()
    assert not dialog.preview_btn.isEnabled()
    assert dialog.fmt_edit.text() == "bad-format"
    dialog.fmt_edit.setText("Q4.4")
    if change == "revision":
        controller.revision += 1
    elif change == "quant":
        controller.config.default_quant = "TRN"
    else:
        controller.config.inspector_pin_mode = "hex"
    dialog.apply_btn.click()
    if change == "pin":  # Native-to-FXP never uses the global FXP pin.
        assert requests == [(("reformat", "a", "Q4.4"), {"pin_mode": "real", "expected_revision": 7})]
    else:
        assert not requests and "reopen" in dialog.error_lbl.text()
        dialog.reject()


def test_native_dialog_language_refresh_keeps_input_and_cancel_is_read_only(ui):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, 2)
    inspector.convert_btn.click()
    dialog = inspector._conversion_dialog
    dialog.fmt_edit.setText("Q8.8")
    controller.config.language = "zh_TW"
    inspector.retranslate_ui(controller.config)
    assert dialog.windowTitle() == "轉為 FXP…"
    assert dialog.fmt_edit.text() == "Q8.8"
    dialog.cancel_btn.click()
    assert inspector._conversion_dialog is None and not requests


def test_return_to_python_float_has_no_bit_buttons_and_details_reset_for_new_source(ui):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, make_fxp(2, "Q4.12"))
    assert inspector._bit_cells
    select(inspector, controller, session, 2.0)
    assert not inspector._bit_cells and inspector.bit_scroll.isHidden()
    assert not inspector.native_details_toggle.isHidden()
    assert inspector.card_hex.isHidden() and inspector.card_bin.isHidden()
    inspector.native_details_toggle.click()
    assert not inspector.card_hex.isHidden() and not inspector.card_bin.isHidden()
    assert inspector.card_hex.edit.isReadOnly() and inspector.card_bin.edit.isReadOnly()
    assert inspector.card_hex.edit.text() == "0x4000000000000000"
    select(inspector, controller, session, 2)
    select(inspector, controller, session, 2.0)
    assert not inspector.native_details_toggle.isChecked()
    assert inspector.card_hex.isHidden() and not requests


def test_float_confirmation_cancel_and_changed_source_are_read_only(ui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    inspector, controller, session, requests = ui
    select(inspector, controller, session, make_fxp(2, "Q4.12"))
    monkeypatch.setattr("rpkbin.calc.ui.inspector_widget.ask", lambda *args: QMessageBox.StandardButton.Cancel)
    inspector.convert_btn.click()
    assert not requests
    def stale(*args):
        controller.revision += 1
        return QMessageBox.StandardButton.Apply
    monkeypatch.setattr("rpkbin.calc.ui.inspector_widget.ask", stale)
    inspector.convert_btn.click()
    assert not requests and not inspector.error_lbl.isHidden()


def test_first_conversion_requires_an_explicit_format_and_remembers_only_success(ui):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, 2)
    inspector.convert_btn.click()
    dialog = inspector._conversion_dialog
    assert dialog.fmt_edit.text() == ""
    assert not dialog.preview_btn.isEnabled() and not dialog.apply_btn.isEnabled()
    dialog.fmt_edit.setText("Q8.8")
    assert dialog.preview_btn.isEnabled() and dialog.apply_btn.isEnabled()
    dialog.apply_btn.click()
    assert inspector._recent_format == ""  # Queued is not an acknowledgement.
    controller.operation_finished.emit({"action":"reformat", "args":["a", "Q8.8"]}, "failed")
    assert inspector._recent_format == ""
    controller.operation_finished.emit({"action":"reformat", "args":["a", "Q8.8"]}, "")
    assert inspector._recent_format == "Q8.8"
    inspector.convert_btn.click()
    assert inspector._conversion_dialog.fmt_edit.text() == "Q8.8"
    inspector._conversion_dialog.reject()


def test_inline_cards_reflow_without_committing_or_losing_drafts(ui, app):
    inspector, controller, session, requests = ui
    select(inspector, controller, session, make_fxp(2,"Q4.12"))
    inspector.resize(1400,650)
    app.processEvents()
    card=inspector.card_real
    assert card._inline
    editor=card.edit
    editor.setText("2**-3")
    editor.setModified(True)
    editor.setCursorPosition(2)
    inspector.resize(360,650)
    for _ in range(3):
        app.processEvents()
    assert not card._inline
    assert card.edit is editor and editor.text()=="2**-3" and editor.isModified()
    assert editor.cursorPosition()==2 and not requests
    inspector.resize(1400,650)
    for _ in range(3):
        app.processEvents()
    assert card._inline and editor.text()=="2**-3" and not requests


def test_copy_feedback_tracks_language_and_resets_without_numeric_edits(ui, app):
    from PySide6.QtTest import QTest
    inspector,controller,session,requests=ui
    select(inspector,controller,session,make_fxp(2,"Q4.12"))
    card=inspector.card_real
    card._copy_timer.setInterval(40)
    card.copy_btn.click()
    assert app.clipboard().text()=="2"
    assert card.copy_btn.property("copyFeedback") is True
    assert card.copy_btn.toolTip()=="Copied"
    controller.config.language="zh_TW"
    inspector.retranslate_ui(controller.config)
    assert card.copy_btn.toolTip()=="已複製"
    from time import monotonic
    deadline = monotonic() + 2
    while card._copy_timer.isActive() and monotonic() < deadline:
        QTest.qWait(10)
    assert not card._copy_timer.isActive()
    assert card.copy_btn.property("copyFeedback") is False
    assert "複製" in card.copy_btn.toolTip() and not requests


def test_custom_input_is_normalized_before_preview_and_mutation(ui,monkeypatch):
    inspector,controller,session,requests=ui
    controller.config.format_preset="custom"
    controller.config.custom_signed_tpl="S{iwl}|{fwl}"
    select(inspector,controller,session,2)
    inspector.convert_btn.click()
    dialog=inspector._conversion_dialog
    dialog.fmt_edit.setText("S6|10")
    queued=[]
    monkeypatch.setattr(controller,"_queue_read",lambda request:queued.append(request) or True)
    dialog.preview_btn.click()
    assert queued[-1]["format"]=="Q6.10" and queued[-1]["pin"]=="real"
    dialog.apply_btn.click()
    assert requests[-1]==(("reformat","a","Q6.10"),{"pin_mode":"real","expected_revision":7})


@pytest.mark.parametrize("array,same_format,expects_warning", [
    (False, False, False), (True, False, True), (True, True, False),
])
def test_preview_array_undo_warning_matches_actual_scope(ui, monkeypatch, array, same_format, expects_warning):
    from PySide6.QtWidgets import QMessageBox
    from rpkbin.calc.core.editing import conversion_preview
    from rpkbin.calc.ui.app import CalcMainWindow
    from rpkbin.calc.ui.i18n import UiText

    inspector, controller, session, requests = ui
    value = make_fxp([[1.25]] if array else 1.25, "Q6.10")
    select(inspector, controller, session, value)
    controller.rows = session.list_variables()
    preview = dict(conversion_preview(session, "a", "Q6.10" if same_format else "Q8.8"),
                   revision=controller.revision)
    texts = []
    def cancel(_parent, _title, text, *_args, **_kwargs):
        texts.append(text)
        return QMessageBox.StandardButton.Cancel
    monkeypatch.setattr("rpkbin.calc.ui.app.ask", cancel)
    holder = SimpleNamespace(controller=controller, _copy=UiText(), _show_error=lambda _: None)
    CalcMainWindow._show_preview(holder, preview)
    assert ("clear GUI Undo history" in texts[0]) is expects_warning
    assert not requests


@pytest.mark.parametrize("lang", ["en_US", "zh_TW"])
@pytest.mark.parametrize("point_size", [7, 12])
def test_list_fields_align_both_edges_preserve_draft_and_reset_for_grid(ui, app, lang, point_size):
    from PySide6.QtCore import QPoint
    inspector, controller, session, requests = ui
    font = inspector.font()
    font.setPointSize(point_size)
    inspector.setFont(font)
    controller.config.language = lang
    controller.config.inspector_view_mode = "list"
    select(inspector, controller, session, make_fxp(1.25, "Q6.10"))
    inspector.retranslate_ui(controller.config)
    inspector._apply_cards_layout("list")
    inspector.resize(900, 650)
    for _ in range(6):
        app.processEvents()
    cards = [c for c in inspector._cards if not c.isHidden()]
    assert all(c._inline for c in cards)
    lefts = {c.edit.mapTo(inspector, QPoint(0, 0)).x() for c in cards}
    rights = {c.edit.mapTo(inspector, QPoint(c.edit.width(), 0)).x() for c in cards}
    assert len(lefts) == 1 and len(rights) == 1
    editor = inspector.card_real.edit
    editor.setText("2**-3")
    editor.setModified(True)
    editor.setCursorPosition(2)
    # A 300px panel can still fit one-line fields with compact CJK fallback fonts.
    # Exercise a genuinely narrow panel; retain all layout and draft assertions.
    inspector.resize(200, 650)
    for _ in range(6):
        app.processEvents()
    assert not any(c._inline for c in cards)
    assert editor.text() == "2**-3" and editor.isModified() and editor.cursorPosition() == 2
    controller.config.inspector_view_mode = "grid"
    inspector._apply_cards_layout("grid")
    inspector.resize(900, 650)
    for _ in range(6):
        app.processEvents()
    assert all(c._list_title_width == 0 and c.title_lbl.minimumWidth() == 0 for c in cards)
    assert inspector.card_c1.edit.isReadOnly()
    assert "Read-Only" not in inspector.card_c1.title_lbl.text()
    assert not requests
