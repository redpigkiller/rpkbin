"""Embedded qtconsole frontend; IPython executes in an owned subprocess."""

from __future__ import annotations

from pathlib import Path
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QTextBrowser, QToolButton, QVBoxLayout,
)
from qtconsole.rich_jupyter_widget import RichJupyterWidget

from ..controller import CalcController
from .icons import style_icon_button
from .i18n import UiText, notify
from .theme import code_font_family, get_palette, qss_family


class SessionCommandHistory:
    """Only explicitly submitted source, bounded by UTF-8 size and command count."""

    MAX_COMMANDS = 1000
    MAX_BYTES = 1024 * 1024

    def __init__(self):
        self.commands: list[str] = []
        self.byte_count = 0
        self.omitted = 0

    def record(self, source: str) -> None:
        if not source.strip():
            return
        size = len(source.encode("utf-8"))
        if size > self.MAX_BYTES:
            self.omitted += 1
            return  # Never truncate source into a different, potentially valid command.
        self.commands.append(source)
        self.byte_count += size
        while len(self.commands) > self.MAX_COMMANDS or self.byte_count > self.MAX_BYTES:
            self.byte_count -= len(self.commands.pop(0).encode("utf-8"))
            self.omitted += 1

    def clear(self) -> None:
        self.commands.clear()
        self.byte_count = 0
        self.omitted = 0

    def export(self, path: str, indices: list[int] | None = None) -> None:
        commands = self.commands if indices is None else [self.commands[i] for i in indices]
        text = "# rpk-calc session commands; IPython syntax may require IPython.\n\n"
        text += "\n\n".join(source.rstrip("\n") for source in commands)
        Path(path).write_text(text + "\n", encoding="utf-8")


class CommandHistoryDialog(QDialog):
    """Browse a session's input; restoring text never executes it."""

    def __init__(self, owner):
        super().__init__(owner.window())
        self.owner = owner
        self.copy = UiText(owner._cfg.language, keep_english_terms=owner._cfg.keep_english_terms)
        self.copy.bind(self, "Session command history", "setWindowTitle")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.copy.bind(self.search, "Search commands", "setPlaceholderText")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.summary = QLabel(self)
        layout.addWidget(self.summary)
        self.commands = QListWidget(self)
        layout.addWidget(self.commands, 1)
        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)
        buttons = QHBoxLayout()
        self.restore_btn = QPushButton("Use as input", self)
        self.export_selected_btn = QPushButton("Export selected", self)
        self.export_all_btn = QPushButton("Export all", self)
        for button, source in ((self.restore_btn, "Use as input"),
                               (self.export_selected_btn, "Export selected"),
                               (self.export_all_btn, "Export all")):
            self.copy.bind(button, source)
        buttons.addWidget(self.restore_btn)
        buttons.addWidget(self.export_selected_btn)
        buttons.addWidget(self.export_all_btn)
        layout.addLayout(buttons)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.copy.bind(close.button(QDialogButtonBox.StandardButton.Close), "Close")
        close.rejected.connect(self.hide)
        layout.addWidget(close)
        self.search.textChanged.connect(self.refresh)
        self.commands.currentItemChanged.connect(self._selection_changed)
        self.restore_btn.clicked.connect(self.restore_selected)
        self.export_selected_btn.clicked.connect(lambda: self._export(selected=True))
        self.export_all_btn.clicked.connect(lambda: self._export(selected=False))
        self.refresh()

    def refresh(self, *_args):
        current = self.commands.currentItem()
        index = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.commands.clear()
        history = self.owner.command_history
        query = self.search.text().casefold()
        for i, source in enumerate(history.commands):
            if query not in source.casefold():
                continue
            title = " ".join(source.split())
            item = QListWidgetItem(f"{i + 1}: {title[:120]}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.commands.addItem(item)
            if i == index:
                self.commands.setCurrentItem(item)
        if self.commands.currentRow() < 0 and self.commands.count():
            self.commands.setCurrentRow(self.commands.count() - 1)
        self._update_summary()
        self.export_all_btn.setEnabled(bool(history.commands))
        self._selection_changed()

    def _update_summary(self):
        history = self.owner.command_history
        self.summary.setText(self.copy(
            "{visible} / {total} commands; {omitted} omitted by session limits. Export is explicit; no automatic file writes.",
            visible=self.commands.count(), total=len(history.commands), omitted=history.omitted))

    def retranslate_ui(self, cfg):
        self.copy.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        self._update_summary()

    def _selection_changed(self, *_args):
        item = self.commands.currentItem()
        self.restore_btn.setEnabled(item is not None)
        self.export_selected_btn.setEnabled(item is not None)
        source = self.owner.command_history.commands[item.data(Qt.ItemDataRole.UserRole)] if item else ""
        self.preview.setPlainText(source)

    def restore_selected(self):
        item = self.commands.currentItem()
        if item is None:
            return
        self.owner.term.input_buffer = self.owner.command_history.commands[item.data(Qt.ItemDataRole.UserRole)]
        self.owner.term.setFocus()
        self.hide()

    def _export(self, *, selected):
        indices = None
        if selected:
            item = self.commands.currentItem()
            if item is None:
                return
            indices = [item.data(Qt.ItemDataRole.UserRole)]
        path, _ = QFileDialog.getSaveFileName(self, self.copy("Export command source"), "calc_session.py", self.copy("Python source (*.py)"))
        if path:
            try:
                self.owner.command_history.export(path, indices)
            except (OSError, UnicodeError) as exc:
                notify(self, self.copy("Export failed"), str(exc), self.copy.lang, warning=True)


class CommandWindowWidget(QFrame):
    restart_requested = Signal()
    exit_requested = Signal()

    def __init__(self, controller: CalcController, parent=None):
        super().__init__(parent)
        self.setObjectName("PanelFrame")
        self.controller = controller
        self._cfg = controller.config
        self.copy = UiText(controller.config.language, keep_english_terms=controller.config.keep_english_terms)
        self._running = False
        self.command_history = SessionCommandHistory()
        self._history_dialog = None
        self._hint_dialog = None
        self.palette = get_palette(controller.config.theme)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QFrame(self)
        header.setObjectName("PanelHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 5, 8, 5)
        row.addStretch()
        self.interrupt_btn = QToolButton(header)
        self.interrupt_btn.setText("Stop")
        self.interrupt_btn.setToolTip("Interrupt Python execution (Ctrl+C)")
        self.interrupt_btn.clicked.connect(controller.transport.interrupt)
        row.addWidget(self.interrupt_btn)
        self.history_btn = QToolButton(header)
        self.history_btn.setText("History")
        self.history_btn.setToolTip("Search this session's submitted commands; restore input or export source without executing.")
        self.history_btn.clicked.connect(self.show_history)
        row.addWidget(self.history_btn)
        self.hint_btn = QToolButton(header)
        self.hint_btn.setText("Hints")
        self.hint_btn.setToolTip("Command shortcuts and Python, NumPy, and FXP examples")
        self.hint_btn.setAccessibleName("Show command hints")
        self.hint_btn.clicked.connect(self.show_hints)
        row.addWidget(self.hint_btn)
        layout.addWidget(header)
        self.term = RichJupyterWidget(self)
        self.term.banner = ""
        # qtconsole 5.x controls both frontend and kernel banners with this flag.
        # Integration tests also cover reconnect/restart so no banner returns.
        self.term._display_banner = False
        self.term.buffer_size = 10000
        self.term.gui_completion = "droplist"
        self.term.custom_interrupt = True
        self.term.custom_restart = True
        self.apply_appearance(controller.config)
        self.term.setEnabled(False)
        layout.addWidget(self.term, 1)
        self.term.executing.connect(lambda _: controller.transport.note_user_execution())
        self.term.executing.connect(self._record_command)
        self.term.custom_interrupt_requested.connect(controller.transport.interrupt)
        self.term.custom_restart_requested.connect(self.restart_requested)
        self.term.exit_requested.connect(self.exit_requested)
        controller.execute_requested.connect(self.term.execute)
        controller.transport.client_changed.connect(self._attach_client)
        controller.transport.ready_changed.connect(self._set_ready)
        controller.transport.clear_console_requested.connect(self.clear_console)
        self._set_running(False)
        controller.transport.busy_changed.connect(self._set_running)
        self._hint_dialog = None

    def apply_appearance(self, cfg):
        self._cfg = cfg
        self.copy.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        previous_theme = self.palette.name
        self.palette = get_palette(cfg.theme)
        self.retranslate_ui(cfg)
        family = code_font_family(cfg.code_font_family)
        self.term.font_family = family
        self.term.font_size = round(cfg.code_font_size * cfg.ui_scale / 100)
        self.term.syntax_style = "monokai" if self.palette.name == "dark" else "default"
        self.term.style_sheet = f"""
            QTextEdit, QPlainTextEdit {{ background: {self.palette.bg_input};
                color: {self.palette.text_primary}; border: none;
                font-family: {qss_family(family)};
                font-size: {cfg.code_font_size * cfg.ui_scale / 100:g}pt; }}
            .in-prompt, .out-prompt {{ color: {self.palette.accent}; }}
            .error {{ color: {self.palette.danger}; }}
        """
        if previous_theme != self.palette.name:
            # qtconsole retains syntax formats for submitted cells. Refresh them
            # as well as the live input; otherwise dark colors survive on white.
            highlighter = self.term._highlighter
            highlighting_on = highlighter.highlighting_on
            highlighter.highlighting_on = True
            try:
                highlighter.rehighlight()
            finally:
                highlighter.highlighting_on = highlighting_on

    def _set_running(self, running):
        self._running = bool(running)
        tooltip = self.copy(
            "Stop execution (Ctrl+C)\nRequest an interrupt; native operations may finish before it takes effect."
            if self._running else "Idle\nNo Python execution to interrupt.")
        style_icon_button(self.interrupt_btn, "stop", tooltip,
                          self.palette.text_secondary, self._cfg,
                          label=self.copy("Stop" if self._running else "Idle"))
        self.interrupt_btn.setAccessibleName(self.copy("Stop execution" if self._running else "Kernel idle"))
        self.interrupt_btn.setEnabled(self._running)

    def _set_ready(self, ready):
        self.term.setEnabled(ready)
        if not ready:
            self._set_running(False)

    def retranslate_ui(self, cfg):
        self._cfg = cfg
        self.copy.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        for button, icon, tooltip in (
            (self.history_btn, "history", "Session history\nSearch submitted commands, restore input or export source without executing."),
            (self.hint_btn, "help", "Command hints\nPython, NumPy, FXP examples and keyboard shortcuts."),
        ):
            style_icon_button(button, icon, self.copy(tooltip), self.palette.text_secondary, cfg)
        self._set_running(self._running)
        if self._history_dialog is not None:
            self._history_dialog.retranslate_ui(cfg)
        if self._hint_dialog is not None:
            scrollbar = self._hint_text.verticalScrollBar()
            position = scrollbar.value()
            self._hint_dialog.setWindowTitle(self.copy("Command hints"))
            self._hint_text.setHtml(self._hints_html())
            scrollbar.setValue(position)

    def _hints_html(self):
        """Short app-owned descriptions; executable source stays language-neutral."""
        sections = [
            ("Python / NumPy", "NumPy is preloaded as np.", [
                ("Python numbers", "a = 2\nb = 0.5"),
                ("Fixed-width NumPy numbers", "np.int8(2)\nnp.float64(0.5)"),
                ("Array and mean", "x = np.array([1.0, 2.0, 3.0])\nnp.mean(x)"),
            ]),
            ("APyTypes / Bits", "APyFixed, APyFixedArray, APyFloat, APyFloatArray and apy are preloaded.", [
                ("Numeric value", "a = APyFixed.from_float(-0.03, bits=16, frac_bits=12)"),
                ("Raw bits: 7 represents 1.75", "raw = APyFixed(7, 4, 2)"),
                ("Fixed-point array", "A = APyFixedArray.from_float(\n    [0.25, -0.125], bits=16, frac_bits=12)"),
                ("Floating-point value", "b = APyFloat.from_float(0.25, exp_bits=8, man_bits=23)"),
                ("Floating-point array", "B = APyFloatArray.from_float(\n    [0.25, -0.125], exp_bits=8, man_bits=23)"),
            ]),
            ("Workspace commands", "Select a variable to inspect it; double-click an array to open its editor. Indices start at 0.", [
                ("List variables", "whos"), ("Clear console", "clc"),
                ("Clear variables", "clear"),
            ]),
            ("Keyboard and controls", "Native constructors keep APyTypes rounding and overflow behavior; Settings defaults apply to GUI edits.", [
                ("Run input", "Enter"), ("Insert a line", "Ctrl+Enter"),
                ("Complete names", "Tab"), ("Browse command history", "↑ / ↓"),
                ("Interrupt execution", "Stop / Ctrl+C"),
                ("Fresh kernel; clears variables", "Restart"),
                ("Theme, fonts and interface scale", "Settings → Appearance"),
            ]),
            ("Plot in the console", "Plots appear below the command.", [
                ("Sine wave", "%matplotlib inline\nimport matplotlib.pyplot as plt\nt = np.linspace(0, 2 * np.pi, 200)\nplt.plot(t, np.sin(t))\nplt.show()"),
            ]),
        ]
        result = ['<style>td { padding: 5px 8px; } th { text-align:left; }</style>']
        for title, premise, rows in sections:
            result += [f"<h3>{escape(self.copy(title))}</h3><p>{escape(self.copy(premise))}</p>",
                       f"<table border='0' cellspacing='0'><tr><th>{escape(self.copy('Purpose'))}</th><th>{escape(self.copy('Example / action'))}</th></tr>"]
            for label, source in rows:
                # Only control names are translated; submitted Python never is.
                if title == "Keyboard and controls":
                    source = self.copy(source)
                result.append(f"<tr><td>{escape(self.copy(label))}</td><td><pre>{escape(source)}</pre></td></tr>")
            result.append("</table>")
        return "".join(result)

    def show_hints(self):
        if self._hint_dialog is None:
            self._hint_dialog = QDialog(self.window())
            self._hint_dialog.setWindowTitle(self.copy("Command hints"))
            self._hint_dialog.resize(760, 560)
            layout = QVBoxLayout(self._hint_dialog)
            self._hint_text = QTextBrowser(self._hint_dialog)
            self._hint_text.setHtml(self._hints_html())
            layout.addWidget(self._hint_text)
        self._hint_dialog.show()
        self._hint_dialog.raise_()

    def _attach_client(self, client):
        # The owned transport emits a new client only when starting a fresh kernel.
        self._set_running(False)
        self.clear_session_history()
        self.term.kernel_manager = self.controller.transport.manager
        self.term.kernel_client = client

    def _record_command(self, source):
        self.command_history.record(source)
        if self._history_dialog is not None and self._history_dialog.isVisible():
            self._history_dialog.refresh()

    def clear_session_history(self):
        self.command_history.clear()
        if self._history_dialog is not None:
            self._history_dialog.refresh()

    def show_history(self):
        if self._history_dialog is None:
            self._history_dialog = CommandHistoryDialog(self)
        self._history_dialog.refresh()
        self._history_dialog.show()
        self._history_dialog.raise_()

    def clear_console(self):
        self.term.clear(keep_input=False)
