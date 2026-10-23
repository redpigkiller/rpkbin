"""Offscreen session-history and UI-only Workspace filtering regressions."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from rpkbin.calc.core import CalcConfig, WorkspaceSession
from rpkbin.calc.ui.console_widget import CommandHistoryDialog, CommandWindowWidget, SessionCommandHistory
from rpkbin.calc.ui.workspace_widget import WorkspaceWidget


class FakeTransport(QObject):
    client_changed = Signal(object)
    ready_changed = Signal(bool)
    clear_console_requested = Signal()
    busy_changed = Signal(bool)
    manager = None

    def __init__(self):
        super().__init__()
        self.user_executions = 0

    def interrupt(self):
        pass

    def note_user_execution(self):
        self.user_executions += 1


class FakeController(QObject):
    workspace_changed = Signal(object)
    config_changed = Signal(object)
    execute_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.config = CalcConfig(startup_script="")
        self.selected_var = "Alpha"
        self.selected_names = []
        self.opened_names = []
        self.transport = FakeTransport()

    def select_variable(self, name):
        self.selected_var = name
        self.selected_names.append(name)

    def open_matrix_editor(self, name):
        self.opened_names.append(name)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def workspace(app):
    controller = FakeController()
    widget = WorkspaceWidget(controller)
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.execute('Alpha = 1\nbeta = np.array([1, 2])\nGamma = APyFixed.from_float(0.5, bits=8, frac_bits=4)\nDelta = APyFixedArray.from_float([0.5], bits=8, frac_bits=4)')
    widget.render_rows(session.list_variables())
    yield widget, controller
    widget.close()


def visible_names(widget):
    return [widget.table.item(row, 0).text() for row in range(widget.table.rowCount())]


@pytest.mark.parametrize("kind, expected", [
    ("array", {"beta", "Delta"}), ("scalar", {"Alpha", "Gamma"}),
    ("fxp", {"Gamma", "Delta"}), ("native", {"Alpha", "beta"}),
])
def test_workspace_type_filters_are_local(workspace, kind, expected):
    widget, controller = workspace
    widget.type_filter.setCurrentIndex(widget.type_filter.findData(kind))
    assert set(visible_names(widget)) == expected
    assert controller.selected_var == "Alpha"
    assert controller.selected_names == []


def test_workspace_name_and_type_filters_combine_case_insensitively(workspace):
    widget, _ = workspace
    widget.name_filter.setText("TA")
    assert set(visible_names(widget)) == {"beta", "Delta"}
    widget.type_filter.setCurrentIndex(widget.type_filter.findData("fxp"))
    assert visible_names(widget) == ["Delta"]
    assert widget.filter_count.text() == "1 / 4"


def test_filter_hidden_selection_restored_without_changing_inspector(workspace):
    widget, controller = workspace
    widget.name_filter.setText("beta")
    assert widget.table.currentRow() == -1
    assert not widget.table.selectedItems()
    assert widget._selected_row_snapshot() is None
    widget.render_rows(widget._rows)
    assert controller.selected_names == []
    widget.name_filter.clear()
    assert widget._selected_row_snapshot().name == "Alpha"
    assert controller.selected_names == []


def test_filtered_click_and_double_click_use_visible_row_mapping(workspace):
    widget, controller = workspace
    widget.name_filter.setText("beta")
    widget._on_cell_clicked(0, 0)
    widget._on_cell_double_clicked(0, 0)
    assert controller.selected_names == ["beta"]
    assert controller.opened_names == ["beta"]


def test_refresh_preserves_scroll_and_current_column(app):
    controller = FakeController()
    controller.selected_var = "v050"
    widget = WorkspaceWidget(controller)
    session = WorkspaceSession(controller.config, seed_demo_vars=False)
    session.user_ns.update({f"v{i:03d}": i for i in range(100)})
    widget.resize(420, 260)
    widget.show()
    rows = session.list_variables()
    widget.render_rows(rows)
    widget.table.setCurrentCell(50, 1)
    controller.selected_names.clear()
    app.processEvents()
    scrollbar = widget.table.verticalScrollBar()
    scrollbar.setValue(25)
    widget.render_rows(list(reversed(rows)))
    assert widget._selected_row_snapshot().name == "v050"
    assert widget.table.currentColumn() == 1
    assert scrollbar.value() == 25
    assert controller.selected_names == []
    widget.close()


def test_history_records_multiline_source_once_and_skips_blank():
    history = SessionCommandHistory()
    source = "for i in range(3):\n    print(i)\n"
    history.record(source)
    history.record(" \n ")
    assert history.commands == [source]
    assert history.byte_count == len(source.encode("utf-8"))


def test_history_bounds_utf8_bytes_count_and_oversized_commands():
    history = SessionCommandHistory()
    history.MAX_BYTES = 10
    history.MAX_COMMANDS = 2
    history.record("你好")
    history.record("12345")
    assert history.commands == ["12345"]
    history.record("x")
    history.record("y")
    assert history.commands == ["x", "y"]
    history.record("z" * 11)
    assert history.commands == ["x", "y"]
    assert history.byte_count == 2
    assert history.omitted == 3


def test_history_export_keeps_exact_multiline_unicode_and_order(tmp_path):
    history = SessionCommandHistory()
    history.record('name = "繁體中文"')
    history.record("for i in range(2):\n    print(i)")
    path = tmp_path / "history.py"
    history.export(str(path))
    text = path.read_text(encoding="utf-8")
    assert 'name = "繁體中文"\n\nfor i in range(2):\n    print(i)' in text
    compile(text, str(path), "exec")
    history.export(str(path), [1])
    assert "name =" not in path.read_text(encoding="utf-8")


def test_history_dialog_search_restore_never_executes(app):
    controller = FakeController()
    widget = CommandWindowWidget(controller)
    widget.term._show_interpreter_prompt(1)
    source = "for i in range(2):\n    print(i)"
    widget.term.executing.emit("a = 1")
    widget.term.executing.emit(source)
    dialog = CommandHistoryDialog(widget)
    dialog.search.setText("PRINT")
    assert dialog.commands.count() == 1
    assert dialog.preview.toPlainText() == source
    before = controller.transport.user_executions
    dialog.restore_selected()
    assert widget.term.input_buffer == source
    assert controller.transport.user_executions == before
    widget._history_dialog = dialog
    widget.clear_session_history()
    assert widget.command_history.commands == []
    assert dialog.commands.count() == 0
    assert dialog.preview.toPlainText() == ""
    assert not dialog.restore_btn.isEnabled()
    dialog.close()
    widget.close()


def test_new_kernel_client_clears_only_added_session_history(app):
    controller = FakeController()
    widget = CommandWindowWidget(controller)
    widget.term.executing.emit("x = 2")
    widget._attach_client(None)
    assert widget.command_history.commands == []
    assert widget.command_history.byte_count == 0
    widget.close()
