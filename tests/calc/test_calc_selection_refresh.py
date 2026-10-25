"""Suppress transient disabled paints, never the asynchronous editing guard."""
import os
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from rpkbin.calc.controller import CalcController
from rpkbin.calc.core import WorkspaceSession, make_fxp
from rpkbin.calc.ui.app import CalcMainWindow


class PaintRecorder(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.states = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            self.states.append(watched.isEnabled())
        return False


def settle(app):
    for _ in range(4):
        app.processEvents()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ready_view(app, tmp_path, monkeypatch):
    controller = CalcController(tmp_path / "settings.json")
    monkeypatch.setattr(controller, "start", lambda: None)
    monkeypatch.setattr(controller, "_schedule", lambda: None)
    window = CalcMainWindow(controller)
    controller.transport.ready = True
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns.update(a=make_fxp(1.25, "Q6.10"), b=make_fxp(2.5, "Q6.10"),
                           c=make_fxp(3.5, "Q6.10"), plain=2)
    controller.rows = session.list_variables()
    controller.selected_var = "a"
    window.workspace_widget.render_rows(controller.rows)
    window.inspector_widget.render_snapshot(session.get_inspector_snapshot("a"))
    controller.interaction_enabled.emit(True)
    window.resize(1300, 850)
    window.show()
    settle(app)
    yield window, controller, session
    window.close()
    settle(app)


@pytest.mark.parametrize("target", ["a", "b", "plain"])
def test_fast_selection_keeps_input_disabled_without_disabled_button_paints(ready_view, app, target):
    window, controller, session = ready_view
    view = window.inspector_widget
    recorder = PaintRecorder(window)
    view.apply_conversion_btn.installEventFilter(recorder)
    view.card_real.edit.installEventFilter(recorder)
    controller.select_variable(target)
    assert not view.body_widget.isEnabled()
    QTest.mouseClick(view.apply_conversion_btn, Qt.MouseButton.LeftButton)
    settle(app)
    assert False not in recorder.states
    assert not controller._mutations
    view.render_snapshot(session.get_inspector_snapshot(target))
    controller.interaction_enabled.emit(True)
    settle(app)
    assert view.body_widget.isEnabled() and view.body_widget.updatesEnabled()
    assert view._last_snapshot.var_name == target
    assert recorder.states and all(recorder.states)


def test_slow_selection_releases_paint_hold_but_keeps_edit_guard(ready_view, app):
    window, controller, _ = ready_view
    view = window.inspector_widget
    controller.select_variable("b")
    assert not view.body_widget.updatesEnabled() and not view.body_widget.isEnabled()
    # Shorten only the fallback clock; verify real timer delivery, not a fixed sleep.
    view._selection_paint_timer.start(1)
    deadline = monotonic() + 2
    while not view.body_widget.updatesEnabled() and monotonic() < deadline:
        QTest.qWait(10)
    assert view.body_widget.updatesEnabled() and not view.body_widget.isEnabled()
    assert not controller._mutations


@pytest.mark.parametrize("state", ["busy", "disconnected", "out_of_sync"])
def test_real_kernel_unavailability_does_not_hide_disabled_state(ready_view, app, state):
    window, controller, _ = ready_view
    view = window.inspector_widget
    controller.select_variable("b")
    assert not view.body_widget.updatesEnabled()
    if state == "busy":
        controller.transport.busy = True
    elif state == "disconnected":
        controller.transport.ready = False
    else:
        controller._out_of_sync = True
    controller.interaction_enabled.emit(False)
    settle(app)
    assert view.body_widget.updatesEnabled() and not view.body_widget.isEnabled()


def test_queued_ready_paint_cannot_expose_intermediate_target(ready_view, app):
    window, controller, session = ready_view
    view = window.inspector_widget
    controller.select_variable("b")
    view.render_snapshot(session.get_inspector_snapshot("b"))
    controller.interaction_enabled.emit(True)
    # Select again before the deferred ready paint has run.
    controller.select_variable("c")
    settle(app)
    assert not view.body_widget.updatesEnabled() and not view.body_widget.isEnabled()
    view.render_snapshot(session.get_inspector_snapshot("c"))
    controller.interaction_enabled.emit(True)
    settle(app)
    assert view.body_widget.updatesEnabled() and view.body_widget.isEnabled()
    assert view._last_snapshot.var_name == "c"


def test_regular_edit_still_shows_disabled_feedback_immediately(ready_view):
    window, controller, _ = ready_view
    controller.edit_target_field("a", None, "real", "2")
    assert not window.inspector_widget.body_widget.isEnabled()
    assert window.inspector_widget.body_widget.updatesEnabled()
    assert len(controller._mutations) == 1
