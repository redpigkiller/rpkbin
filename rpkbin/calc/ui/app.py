"""Main application window for rpk-calc."""

from __future__ import annotations

import sys
from dataclasses import replace
from typing import Sequence

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QActionGroup, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QSplitter,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..controller import CalcController
from ..core.config import DEFAULT_CONFIG_PATH
from ..core.session import MatrixSnapshot
from .console_widget import CommandWindowWidget
from .i18n import UiText, ask, tr
from .icons import make_icon, style_icon_button
from .inspector_widget import VariableInspectorWidget
from .matrix_editor import MatrixEditorPage
from .numeric_display import format_numeric_text
from .settings_dialog import SettingsDialog
from .theme import build_stylesheet, get_palette, qt_palette, set_role
from .workspace_widget import WorkspaceWidget


class CalcMainWindow(QMainWindow):
    """3-pane MATLAB-inspired main window for rpk-calc."""

    def __init__(self, controller: CalcController | None = None) -> None:
        super().__init__()
        # Qt's Windows 11 style can paint identical checked/unchecked indicators
        # when combined with our palette/QSS. Use Qt's portable built-in style
        # so preference state remains visible (also covered by pixel tests).
        app = QApplication.instance()
        if app is not None and app.style().objectName().casefold() != "fusion":
            app.setStyle("Fusion")
        self.controller = controller or CalcController()
        self.palette = get_palette(self.controller.config.theme)
        self._matrix_tabs: dict[str, MatrixEditorPage] = {}
        self._appearance_key = None
        self._copy = UiText(self.controller.config.language, keep_english_terms=self.controller.config.keep_english_terms)
        self._last_status_source = "Ready"

        self.setWindowTitle(tr("app_title", self.controller.config.language, keep_english_terms=self.controller.config.keep_english_terms))
        self.resize(1320, 820)
        self.setStyleSheet(build_stylesheet(self.palette))
        self._build_view_menu()

        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 6, 8, 8)
        root_layout.setSpacing(6)

        # ── Top Application Bar ───────────────────────────────────────────
        top_bar = QFrame(central)
        top_bar.setObjectName("PanelHeader")
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(12, 5, 10, 5)
        tb_layout.setSpacing(10)

        brand_lbl = QLabel("rpk-calc")
        set_role(brand_lbl, "brand", code=True)
        tb_layout.addWidget(brand_lbl)

        self.active_fmt_badge = QLabel("", top_bar)
        set_role(self.active_fmt_badge, "badge", code=True)
        self.active_fmt_badge.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.active_fmt_badge.setMinimumWidth(0)
        tb_layout.addWidget(self.active_fmt_badge, 1)

        self.undo_btn = QToolButton(top_bar)
        self.undo_btn.setText("Undo edit")
        self.redo_btn = QToolButton(top_bar)
        self.redo_btn.setText("Redo edit")
        for button, action in ((self.undo_btn, self.controller.undo_edit), (self.redo_btn, self.controller.redo_edit)):
            button.setEnabled(False)
            button.setToolTip("GUI numeric/bit edits only; REPL, slice or structural changes clear this history. "
                              "Console Ctrl+Z remains text editing.")
            button.clicked.connect(action)
            tb_layout.addWidget(button)
        self.controller.undo_state_changed.connect(self._update_undo)
        self.controller.preview_ready.connect(self._show_preview)

        self.reset_btn = QToolButton(top_bar)
        self.reset_btn.setIcon(make_icon("reset", self.palette.text_secondary))
        self.reset_btn.setToolTip("Restart kernel (clears workspace)")
        self.reset_btn.clicked.connect(self._request_restart)
        tb_layout.addWidget(self.reset_btn)

        self.settings_btn = QToolButton(top_bar)
        self.settings_btn.setIcon(make_icon("settings", self.palette.text_secondary))
        self.settings_btn.setToolTip(
            tr("settings_tooltip", self.controller.config.language, keep_english_terms=self.controller.config.keep_english_terms)
        )
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        tb_layout.addWidget(self.settings_btn)

        root_layout.addWidget(top_bar)

        # ── Main Horizontal Splitter (Left: Command/Matrix Tabs | Right: Workspace + Inspector) ──
        self.h_splitter = QSplitter(Qt.Orientation.Horizontal, central)

        # Left Pane: Tabbed Command Window + Matrix Variable Editors
        self.left_tabs = QTabWidget(self.h_splitter)
        self.left_tabs.setTabsClosable(True)
        self.left_tabs.tabCloseRequested.connect(self._on_tab_close_requested)

        self.console_widget = CommandWindowWidget(self.controller, self.left_tabs)
        self.left_tabs.addTab(
            self.console_widget,
            tr("command_window", self.controller.config.language, keep_english_terms=self.controller.config.keep_english_terms),
        )
        # Hide the close button on Tab 0 (Command Window is permanent)
        self.left_tabs.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.RightSide, None
        )
        self.left_tabs.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.LeftSide, None
        )

        # Right Pane: Vertical Splitter (Top: Workspace | Bottom: Variable Inspector)
        self.v_splitter = QSplitter(Qt.Orientation.Vertical, self.h_splitter)

        self.workspace_widget = WorkspaceWidget(
            self.controller,
            on_ensure_inspector_visible=self._ensure_inspector_visible,
            parent=self.v_splitter,
        )
        self.inspector_widget = VariableInspectorWidget(
            self.controller, parent=self.v_splitter
        )

        self.v_splitter.addWidget(self.workspace_widget)
        self.v_splitter.addWidget(self.inspector_widget)
        self.v_splitter.setStretchFactor(0, 4)
        self.v_splitter.setStretchFactor(1, 6)
        self.v_splitter.setSizes([200, 560])
        self._inspector_sizes = self.controller.config.vertical_sizes or [200, 560]
        self.inspector_widget.collapsed_changed.connect(self._on_inspector_collapsed)

        self.h_splitter.addWidget(self.left_tabs)
        self.h_splitter.addWidget(self.v_splitter)
        self.h_splitter.setStretchFactor(0, 5)
        self.h_splitter.setStretchFactor(1, 5)
        self.h_splitter.setSizes([640, 660])

        root_layout.addWidget(self.h_splitter, 1)
        self.setCentralWidget(central)

        # Connect Controller Signals
        self.controller.matrix_tab_requested.connect(self._open_or_focus_matrix_tab)
        self.controller.matrix_updated.connect(self._on_matrix_updated)
        self.controller.workspace_changed.connect(self._prune_deleted_matrix_tabs)
        self.controller.config_changed.connect(self._apply_preferences)
        self.controller.matrix_renamed.connect(self._on_matrix_renamed)
        self.controller.interaction_enabled.connect(self._set_interaction_enabled)
        self.controller.transport.status_changed.connect(self._show_status)
        self.controller.format_error_occurred.connect(self._show_error)
        self.controller.clipboard_ready.connect(lambda text: QGuiApplication.clipboard().setText(text))
        self.console_widget.restart_requested.connect(self._request_restart)
        self.console_widget.exit_requested.connect(self.close)

        self._apply_preferences(self.controller.config)
        cfg = self.controller.config
        if cfg.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(cfg.window_geometry.encode("ascii", errors="ignore")))
        if cfg.horizontal_sizes:
            self.h_splitter.setSizes(cfg.horizontal_sizes)
        self.v_splitter.setSizes(self._inspector_sizes)
        self._on_inspector_collapsed(cfg.inspector_collapsed)
        self.controller.refresh_all()
        self._set_interaction_enabled(False)
        QTimer.singleShot(0, self.controller.start)

    def _build_view_menu(self):
        text = self._copy
        file_menu = text.bind(self.menuBar().addMenu(""), "File", "setTitle")
        text.bind(file_menu.addAction(""), "Exit").triggered.connect(self.close)
        edit_menu = text.bind(self.menuBar().addMenu(""), "Edit", "setTitle")
        self.undo_action = text.bind(edit_menu.addAction(""), "Undo numeric edit")
        self.redo_action = text.bind(edit_menu.addAction(""), "Redo numeric edit")
        self.undo_action.triggered.connect(self.controller.undo_edit)
        self.redo_action.triggered.connect(self.controller.redo_edit)
        edit_menu.addSeparator()
        text.bind(edit_menu.addAction(""), "Settings…").triggered.connect(self._open_settings_dialog)
        view = text.bind(self.menuBar().addMenu(""), "View", "setTitle")
        display = text.bind(view.addMenu(""), "Number display (view only)", "setTitle")
        self._display_menu = display
        self._numeric_actions = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for key, title in (("auto", "Full precision / automatic"), ("scientific", "Scientific notation"), ("fixed", "Fixed decimals")):
            action = text.bind(display.addAction(""), title)
            action.setCheckable(True)
            action.setChecked(key == self.controller.config.numeric_display)
            group.addAction(action)
            action.triggered.connect(lambda checked, key=key: self.controller.update_config(
                replace(self.controller.config, numeric_display=key)))
            self._numeric_actions[key] = action
        self._refresh_display_help()
        places = text.bind(display.addAction(""), "Decimal places…")
        places.triggered.connect(self._choose_decimal_places)
        display.setToolTipsVisible(True)
        text.bind(display, "Workspace and matrix display only; editors and copied values retain full precision.", "setToolTip")
        display.addSeparator()
        self.display_help_action = text.bind(display.addAction(""), "What do these options mean?")
        self.display_help_action.triggered.connect(self._show_display_help)
        self._display_help_dialog = None
        self.reset_layout_action = text.bind(view.addAction(""), "Reset layout")
        text.bind(self.reset_layout_action, "Restore window and panel sizes; keep variables, fonts and theme.", "setToolTip")
        self.reset_layout_action.triggered.connect(self.reset_layout)
        text.bind(view.addAction(""), "Session history").triggered.connect(lambda: self.console_widget.show_history())
        help_menu = text.bind(self.menuBar().addMenu(""), "Help", "setTitle")
        text.bind(help_menu.addAction(""), "Command hints").triggered.connect(lambda: self.console_widget.show_hints())

    def _refresh_display_help(self):
        cfg = self.controller.config
        text = self._copy
        sample = "1.23456789"
        descriptions = {
            "auto": text("Show the complete source number, e.g. 1.23456789. No rounding for display."),
            "scientific": text("Show powers of ten, e.g. {value}.", value=format_numeric_text(sample, replace(cfg, numeric_display='scientific'))),
            "fixed": text("Show {places} decimal places, e.g. {value}.", places=cfg.numeric_precision, value=format_numeric_text(sample, replace(cfg, numeric_display='fixed'))),
        }
        for key, action in self._numeric_actions.items():
            action.setToolTip(descriptions[key] + " " + text("Only Workspace and array display changes; stored values and copies stay exact."))
            action.setStatusTip(action.toolTip())

    def _show_display_help(self):
        if self._display_help_dialog is None:
            dialog = self._display_help_dialog = QDialog(self)
            self._copy.bind(dialog, "Number display", "setWindowTitle")
            dialog.resize(620, 390)
            layout = QVBoxLayout(dialog)
            self._display_help_text = QTextBrowser(dialog)
            layout.addWidget(self._display_help_text)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
            self._copy.bind(buttons.button(QDialogButtonBox.StandardButton.Close), "Close")
            buttons.rejected.connect(dialog.hide)
            layout.addWidget(buttons)
        cfg = self.controller.config
        sample = "1.23456789"
        scientific = format_numeric_text(sample, replace(cfg, numeric_display="scientific"))
        fixed = format_numeric_text(sample, replace(cfg, numeric_display="fixed"))
        text = self._copy
        self._display_help_text.setHtml(
            f"<h3>{text('One value, three views')}</h3><p>{text('Example')}: <code>{sample}</code></p>"
            f"<table cellpadding='7'><tr><th>{text('Display')}</th><th>{text('Result')}</th></tr>"
            f"<tr><td>{text('Full precision / automatic')}</td><td>{sample}</td></tr>"
            f"<tr><td>{text('Scientific notation')}</td><td>{scientific}</td></tr>"
            f"<tr><td>{text('Fixed decimals')}</td><td>{fixed}</td></tr></table>"
            f"<p>{text('Decimal places is currently {places}; it applies to Scientific and Fixed. It counts decimal places, not significant digits.', places=cfg.numeric_precision)}</p>"
            f"<p>{text('Only Workspace and array display changes. Calculations, Inspector, edit fields, copied values and raw bits keep their original precision.')}</p>"
            f"<p>{text('Reset layout restores window and panel sizes without clearing variables or changing fonts and theme.')}</p>")
        self._display_help_dialog.show()
        self._display_help_dialog.raise_()

    def _choose_decimal_places(self):
        dialog = QInputDialog(self)
        dialog.setWindowTitle(self._copy("Numeric display"))
        dialog.setLabelText(self._copy("Decimal places (1..17):"))
        dialog.setInputMode(QInputDialog.InputMode.IntInput)
        dialog.setIntRange(1, 17)
        dialog.setIntValue(self.controller.config.numeric_precision)
        dialog.setOkButtonText(self._copy("OK"))
        dialog.setCancelButtonText(self._copy("Cancel"))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.controller.update_config(replace(self.controller.config, numeric_precision=dialog.intValue()))

    def reset_layout(self):
        self.resize(1320, 820)
        self.h_splitter.setSizes([820, 460])
        self.inspector_widget.set_collapsed(False)
        self._inspector_sizes = [260, 500]
        self.v_splitter.setSizes(self._inspector_sizes)

    def _set_interaction_enabled(self, enabled):
        # Collapsing and view controls remain usable while Python is running.
        self.inspector_widget.body_widget.setEnabled(enabled)
        self.workspace_widget.setEnabled(self.controller.is_available)
        self._update_undo(self.controller.undo_state)

    def _update_undo(self, state):
        self.undo_btn.setEnabled(bool(self.controller.is_available and state.get("can_undo")))
        self.redo_btn.setEnabled(bool(self.controller.is_available and state.get("can_redo")))
        self.undo_action.setEnabled(self.undo_btn.isEnabled())
        self.redo_action.setEnabled(self.redo_btn.isEnabled())

    def _show_preview(self, preview):
        text = self._copy(
            "Target: {name} → {format}\nScope: {scope}\nRange: {range}\nLSB: {lsb}\n"
            "Checked: {checked_cells} / {total_cells}\nChanged values: {changed_values}; saturated: {saturated_cells}\n"
            "Discarded high bits: {discarded_high_bits_cells}\nExample: {before} → {after}\n\n"
            "Array format changes apply to the whole variable and clear GUI Undo history.",
            **dict(preview, scope=self._copy(preview["scope"]), **preview["example"]))
        if ask(self, self._copy("FXP conversion preview"), text,
                QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel, self.controller.config.language) == QMessageBox.StandardButton.Apply:
            self.controller.change_variable_format(preview["name"], preview["format"])

    def _show_error(self, error):
        if error:
            self._show_status(error)

    def _show_status(self, source):
        self._last_status_source = source
        self.statusBar().showMessage(self._copy(source))

    def _on_matrix_renamed(self, old, new):
        page = self._matrix_tabs.pop(old, None)
        if page is not None:
            page.var_name = page.model.name = new
            self._matrix_tabs[new] = page

    def _update_top_badge(self, cfg) -> None:
        pin_label = self._copy("Real Value" if cfg.inspector_pin_mode == "real" else "Hex (Raw Bits)")
        self.active_fmt_badge.setText(
            self._copy("GUI FXP edits: {quant}/{overflow}  ·  Pin: {pin}",
                       quant=cfg.default_quant, overflow=cfg.default_overflow, pin=pin_label)
        )
        self.active_fmt_badge.setToolTip(
            self.active_fmt_badge.text() + "\n" + self._copy("Defaults apply to GUI Convert to FXP, value edits and format conversions. "
            "Native APyTypes constructors and arithmetic keep their own behavior.")
        )

    def _apply_preferences(self, cfg):
        self._copy.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        self.setWindowTitle(tr("app_title", cfg.language, keep_english_terms=self.controller.config.keep_english_terms))
        self.left_tabs.setTabText(0, tr("command_window", cfg.language, keep_english_terms=self.controller.config.keep_english_terms))
        self._show_status(self._last_status_source)
        self._update_top_badge(cfg)
        self._refresh_display_help()
        for key, action in self._numeric_actions.items():
            action.setChecked(key == cfg.numeric_display)
        key = (cfg.theme, cfg.ui_font_family, cfg.ui_font_size,
               cfg.code_font_family, cfg.code_font_size, cfg.ui_scale, cfg.language, cfg.keep_english_terms)
        if key == self._appearance_key:
            return
        self._appearance_key = key
        self.palette = get_palette(cfg.theme)
        self.setPalette(qt_palette(self.palette))
        self.setStyleSheet(build_stylesheet(self.palette, cfg))
        for button, icon, tooltip in (
            (self.undo_btn, "undo", "Undo numeric edit\nGUI numeric/bit edits only; REPL, slice or structural changes clear this history. Console Ctrl+Z edits text."),
            (self.redo_btn, "redo", "Redo numeric edit\nRestore the last undone GUI numeric/bit edit."),
            (self.reset_btn, "reset", "Restart kernel\nClears all workspace variables."),
            (self.settings_btn, "settings", tr("settings_tooltip", cfg.language, keep_english_terms=self.controller.config.keep_english_terms)),
        ):
            style_icon_button(button, icon, self._copy(tooltip), self.palette.text_secondary, cfg)
        self.console_widget.apply_appearance(cfg)
        self.workspace_widget.apply_appearance(cfg)
        self.inspector_widget.apply_appearance(cfg)
        for page in self._matrix_tabs.values():
            page.apply_appearance(cfg)
        if self._display_help_dialog is not None and self._display_help_dialog.isVisible():
            scroll = self._display_help_text.verticalScrollBar().value()
            self._show_display_help()
            self._display_help_text.verticalScrollBar().setValue(scroll)
        if cfg.inspector_collapsed:
            self._on_inspector_collapsed(True)

    def _on_inspector_collapsed(self, collapsed):
        if collapsed:
            sizes = self.v_splitter.sizes()
            if len(sizes) == 2 and sizes[1] > 80:
                self._inspector_sizes = sizes
            height = self.inspector_widget.header_widget.sizeHint().height()
            self.inspector_widget.setMaximumHeight(height)
            self.v_splitter.setSizes([sum(self.v_splitter.sizes()), height])
        else:
            self.inspector_widget.setMaximumHeight(16777215)
            self.v_splitter.setSizes(self._inspector_sizes)

    def _ensure_inspector_visible(self) -> None:
        if self.controller.config.inspector_collapsed:
            self.inspector_widget.set_collapsed(False)
        sizes = self.v_splitter.sizes()
        if len(sizes) == 2 and sizes[1] < 60:
            total = sum(sizes) or 760
            self.v_splitter.setSizes([int(total * 0.4), int(total * 0.6)])

    def _open_or_focus_matrix_tab(self, var_name: str) -> None:
        if var_name not in self._matrix_tabs:
            page = MatrixEditorPage(var_name, self.controller, self.left_tabs)
            page.apply_appearance(self.controller.config)
            self._matrix_tabs[var_name] = page
            idx = self.left_tabs.addTab(page, self._copy("Matrix: {name}", name=var_name))
            self.left_tabs.setCurrentIndex(idx)
        else:
            page = self._matrix_tabs[var_name]
            idx = self.left_tabs.indexOf(page)
            if idx >= 0:
                self.left_tabs.setCurrentIndex(idx)

    def _on_matrix_updated(self, snap: MatrixSnapshot) -> None:
        page = self._matrix_tabs.get(snap.name)
        if page is not None:
            page.render_snapshot(snap)
            idx = self.left_tabs.indexOf(page)
            if idx >= 0:
                self.left_tabs.setTabText(
                    idx, f"{snap.name} ({snap.shape[0]}×{snap.shape[1]})"
                )

    def _prune_deleted_matrix_tabs(self, rows) -> None:
        existing = {r.name for r in rows if r.is_array}
        for name in list(self._matrix_tabs.keys()):
            if name not in existing:
                page = self._matrix_tabs.pop(name)
                idx = self.left_tabs.indexOf(page)
                if idx > 0:
                    self.left_tabs.removeTab(idx)
                page.deleteLater()

    def _on_tab_close_requested(self, index: int) -> None:
        if index <= 0:
            return
        widget = self.left_tabs.widget(index)
        for name, page in list(self._matrix_tabs.items()):
            if page is widget:
                self._matrix_tabs.pop(name, None)
                self.controller.close_matrix_editor(name)
                break
        self.left_tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()

    def _request_restart(self) -> None:
        answer = ask(self, self._copy("Restart kernel"),
            self._copy("Restart the kernel and clear all workspace variables?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No, self.controller.config.language)
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.restart_kernel()

    def closeEvent(self, event):
        self.controller.config.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.controller.config.horizontal_sizes = self.h_splitter.sizes()
        self.controller.config.vertical_sizes = (self._inspector_sizes if self.controller.config.inspector_collapsed
                                                else self.v_splitter.sizes())
        self.controller._save_config()
        self.controller.shutdown()
        super().closeEvent(event)

    def _open_settings_dialog(self) -> None:
        dlg = SettingsDialog(self.controller.config, self, config_path=self.controller._config_path or DEFAULT_CONFIG_PATH,
                             controller=self.controller)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            self.controller.update_config(dlg.result_config())


def launch(argv: Sequence[str] | None = None) -> int:
    """Launch the rpk-calc desktop application."""
    app = QApplication.instance() or QApplication(list(argv) if argv is not None else sys.argv)
    win = CalcMainWindow()
    win.show()
    return app.exec()
