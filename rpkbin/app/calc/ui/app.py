"""Lightweight shared shell for Simple and Python Studio."""

from __future__ import annotations

import sys
from typing import Sequence

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core.config import CalcConfig
from .flow_layout import FlowLayout
from .i18n import UiText
from .icons import application_icon, make_icon
from .simple_calc_widget import SimpleCalcWidget
from .theme import build_stylesheet, get_palette, qt_palette, repolish, scaled_px, set_role


def __getattr__(name):
    if name == "CalcMainWindow":
        from .studio_window import CalcMainWindow

        return CalcMainWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def apply_window_always_on_top(window: QWidget, checked: bool) -> None:
    """Toggle top-most state on Windows without destroying the native HWND."""
    flags = window.windowFlags()
    if checked:
        flags |= Qt.WindowType.WindowStaysOnTopHint
    else:
        flags &= ~Qt.WindowType.WindowStaysOnTopHint
    window.overrideWindowFlags(flags)

    applied_native = False
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            hwnd = int(window.winId())
            if hwnd:
                HWND_TOPMOST = -1
                HWND_NOTOPMOST = -2
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                insert_after = HWND_TOPMOST if checked else HWND_NOTOPMOST
                res = ctypes.windll.user32.SetWindowPos(
                    hwnd,
                    ctypes.wintypes.HWND(insert_after),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
                applied_native = bool(res)
        except Exception:
            applied_native = False

    if not applied_native:
        was_visible = window.isVisible()
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        if was_visible:
            window.show()


class ActivePageStack(QStackedWidget):
    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint().expandedTo(page.minimumSize()) if page else super().minimumSizeHint()


class CalculatorWindow(QMainWindow):
    def __init__(self, mode=None, *, config_path=None):
        super().__init__()
        app = QApplication.instance()
        if app is not None and app.style().objectName().casefold() != "fusion":
            app.setStyle("Fusion")
        self.config_path = config_path
        self.config = CalcConfig.load(config_path)
        self._text = UiText(self.config.language, keep_english_terms=self.config.keep_english_terms)
        self.mode = None
        self.studio = None
        self._always_on_top = False
        self._expanded_simple_height: int | None = None
        self._saved_sidebar_width: int = scaled_px(250, self.config)
        self.setWindowTitle("rpk-calc")
        self.setWindowIcon(application_icon())

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.top_bar = QFrame(self)
        self.top_bar.setObjectName("TopShellBar")
        bar = FlowLayout(
            self.top_bar,
            margin=0,
            h_spacing=6,
            v_spacing=4,
            auto_height=True,
            single_row_max_w=560,
        )
        bar.setContentsMargins(8, 4, 8, 4)

        self.brand_label = QLabel("rpk-calc", self.top_bar)
        set_role(self.brand_label, "brand")
        bar.addWidget(self.brand_label)

        seg_frame = self._seg_frame = QFrame(self.top_bar)
        set_role(seg_frame, "segmentContainer")
        seg_layout = QHBoxLayout(seg_frame)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)

        self.mode_buttons = {}
        button_group = QButtonGroup(self)
        button_group.setExclusive(True)
        for key, label in (("simple", "Simple"), ("python", "Python")):
            button = QPushButton(seg_frame)
            button.setObjectName("CalcMode")
            set_role(button, "segmentBtn")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, page=key: self.switch_mode(page))
            button_group.addButton(button)
            self.mode_buttons[key] = button
            if key == "simple":
                self._text.bind(button, label)
            else:
                button.setText(label)
            seg_layout.addWidget(button)
        bar.addWidget(seg_frame)

        top_spacer = QWidget(self.top_bar)
        top_spacer.setFixedHeight(0)
        top_spacer.setMinimumWidth(0)
        bar.addWidget(top_spacer, stretch=True)

        actions_host = QWidget(self.top_bar)
        actions_row = QHBoxLayout(actions_host)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(6)
        actions_host.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.toggle_side_btn = QToolButton(actions_host)
        self.toggle_side_btn.setCheckable(True)
        self.toggle_side_btn.setChecked(True)
        self._text.bind(self.toggle_side_btn, "Show or hide the Variables & History sidebar", method="setToolTip")
        self._text.bind(self.toggle_side_btn, "Toggle sidebar", method="setAccessibleName")
        self.toggle_side_btn.clicked.connect(self._on_toggle_sidebar)
        actions_row.addWidget(self.toggle_side_btn)

        self.settings_button = QToolButton(actions_host)
        self._text.bind(self.settings_button, "Settings", method="setToolTip")
        self._text.bind(self.settings_button, "Settings", method="setAccessibleName")
        self.settings_button.clicked.connect(self.open_settings)
        actions_row.addWidget(self.settings_button)

        self.pin_top_btn = QToolButton(actions_host)
        self.pin_top_btn.setCheckable(True)
        self._text.bind(self.pin_top_btn, "Always on top\nKeep the calculator window above other windows.", method="setToolTip")
        self._text.bind(self.pin_top_btn, "Always on top", method="setAccessibleName")
        self.pin_top_btn.clicked.connect(self._on_toggle_pin_top)
        actions_row.addWidget(self.pin_top_btn)

        bar.addWidget(actions_host)

        layout.addWidget(self.top_bar)

        palette = get_palette(self.config.theme)
        self.setPalette(qt_palette(palette))
        self.setStyleSheet(build_stylesheet(palette, self.config))

        self.stack = ActivePageStack(self)
        layout.addWidget(self.stack, 1)

        self.simple = SimpleCalcWidget(self.config, self)
        self.simple.local_state_changed.connect(self._on_simple_local_state_changed)
        self.simple.limits_changed.connect(self._update_limits)
        if hasattr(self.simple, "sidebar_auto_toggled"):
            self.simple.sidebar_auto_toggled.connect(self._sync_sidebar_toggle_button)
        self.stack.addWidget(self.simple)

        preference = self.config.launch_mode
        self._refresh_shell_icons()
        self.switch_mode(mode or (self.config.last_mode if preference == "last" else preference))
        QTimer.singleShot(0, self._update_limits)

    def _refresh_shell_icons(self):
        palette = get_palette(self.config.theme)
        size = scaled_px(16, self.config)
        btn_size = scaled_px(28, self.config)
        for btn, icon_name, active in (
            (self.toggle_side_btn, "sidebar", self.toggle_side_btn.isChecked()),
            (self.settings_button, "settings", False),
            (self.pin_top_btn, "pin", self.pin_top_btn.isChecked()),
        ):
            color = palette.primary_fg if (btn.isCheckable() and active) else palette.text_secondary
            if btn.isCheckable() and active:
                set_role(btn, "segmentIconBtn")
            else:
                set_role(btn, "iconButton")
            repolish(btn)
            btn.setIcon(make_icon(icon_name, color, size))
            btn.setIconSize(QSize(size, size))
            btn.setFixedSize(btn_size, btn_size)
        for btn in self.mode_buttons.values():
            btn.ensurePolished()
            btn.setMinimumWidth(btn.sizeHint().width())
        self.brand_label.ensurePolished()
        self.brand_label.setMinimumWidth(self.brand_label.sizeHint().width())
        self.top_bar.layout().invalidate()
        self.top_bar.updateGeometry()

    def _sync_sidebar_toggle_button(self, visible: bool):
        visible = bool(visible)
        self.toggle_side_btn.blockSignals(True)
        self.toggle_side_btn.setChecked(visible)
        self.toggle_side_btn.blockSignals(False)
        self._refresh_shell_icons()

    def _on_toggle_sidebar(self, checked: bool):
        self.simple.mark_user_sidebar_toggle(checked)
        if not checked and self.simple.sidebar.isVisible():
            self._saved_sidebar_width = max(scaled_px(220, self.config), self.simple.sidebar.width())
        cur_w = self.width()
        self.simple.set_sidebar_visible(checked)
        self._refresh_shell_icons()
        self._update_limits()
        if self.simple.main_splitter.orientation() == Qt.Orientation.Horizontal:
            delta = self._saved_sidebar_width if checked else -self._saved_sidebar_width
            target_w = max(self.minimumWidth(), cur_w + delta)
        else:
            target_w = max(self.minimumWidth(), cur_w)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            target_w = min(target_w, max(self.minimumWidth(), screen.availableGeometry().width() - 24))
        self.resize(target_w, max(self.height(), self.minimumHeight()))
        QTimer.singleShot(0, self._update_limits)

    def _on_toggle_pin_top(self, checked: bool):
        checked = bool(checked)
        self._always_on_top = checked
        self.pin_top_btn.blockSignals(True)
        self.pin_top_btn.setChecked(checked)
        self.pin_top_btn.blockSignals(False)
        apply_window_always_on_top(self, checked)
        if self.studio is not None:
            self.studio._always_on_top = checked
            if hasattr(self.studio, "always_on_top_btn"):
                self.studio.always_on_top_btn.blockSignals(True)
                self.studio.always_on_top_btn.setChecked(checked)
                self.studio.always_on_top_btn.blockSignals(False)
            if hasattr(self.studio, "view_always_on_top_act"):
                self.studio.view_always_on_top_act.blockSignals(True)
                self.studio.view_always_on_top_act.setChecked(checked)
                self.studio.view_always_on_top_act.blockSignals(False)
        self._refresh_shell_icons()

    def _on_simple_local_state_changed(self):
        cfg = self.simple.engine.config
        prev = self.config
        self.config = cfg
        if self.studio is not None and cfg != prev:
            self.studio.controller.update_config(cfg)
        else:
            self._save()

    def _save(self):
        try:
            self.config.save(self.config_path)
        except OSError as exc:
            self.statusBar().showMessage(str(exc), 10_000)

    def _record_geometry(self):
        if self.mode is None:
            return
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        if self.mode == "simple":
            self.config.simple_window_geometry = geometry
        else:
            self.config.window_geometry = geometry

    def _create_studio(self):
        from ..controller import CalcController
        from .studio_window import CalcMainWindow

        controller = CalcController(self.config_path, self, config=self.config)
        self.studio = CalcMainWindow(controller, embedded=True)
        self.studio.setMinimumSize(920, 500)
        self.studio.controller.config_changed.connect(self._apply_preferences)
        self.studio.always_on_top_changed.connect(self._on_toggle_pin_top)
        self.stack.addWidget(self.studio)
        return self.studio

    def _show_python_loading_page(self) -> QWidget:
        loading = QWidget(self.stack)
        box = QVBoxLayout(loading)
        box.setContentsMargins(24, 24, 24, 24)
        box.addStretch(1)
        title = QLabel(self._text("Loading Python Studio..."), loading)
        set_role(title, "brand")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel(
            self._text("Initializing IPython / QtConsole workspace on first switch."),
            loading,
        )
        set_role(detail, "subtitle")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(title)
        box.addWidget(detail)
        box.addStretch(1)
        self.stack.addWidget(loading)
        self.stack.setCurrentWidget(loading)
        return loading

    def switch_mode(self, mode):
        if mode == self.mode:
            return
        self._record_geometry()
        if mode == "simple":
            page = self.simple
        elif self.studio is not None:
            page = self.studio
        else:
            self.mode_buttons["python"].setChecked(True)
            self.toggle_side_btn.setVisible(False)
            self.settings_button.setVisible(False)
            self.pin_top_btn.setVisible(False)
            loading = self._show_python_loading_page()
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
            try:
                page = self._create_studio()
            finally:
                QApplication.restoreOverrideCursor()
                self.stack.removeWidget(loading)
                loading.deleteLater()
        for index in range(self.stack.count()):
            child = self.stack.widget(index)
            policy = QSizePolicy.Policy.Preferred if child is page else QSizePolicy.Policy.Ignored
            child.setSizePolicy(policy, policy)
            child.setEnabled(child is page)
        self.mode = self.config.last_mode = mode
        self.mode_buttons[mode].setChecked(True)
        is_simple = mode == "simple"
        self.toggle_side_btn.setVisible(is_simple)
        self.settings_button.setVisible(is_simple)
        self.pin_top_btn.setVisible(is_simple)
        self.stack.setCurrentWidget(page)
        self._update_limits()
        geometry = self.config.simple_window_geometry if is_simple else self.config.window_geometry
        if not geometry or not self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii", "ignore"))):
            if is_simple:
                def_w = scaled_px(680 if self.toggle_side_btn.isChecked() else 420, self.config)
                def_h = scaled_px(545, self.config)
                screen = self.screen() or QApplication.primaryScreen()
                if screen is not None:
                    avail = screen.availableGeometry()
                    def_w = min(def_w, max(320, avail.width() - 24))
                    def_h = min(def_h, max(320, avail.height() - 36))
                self.resize(max(def_w, self.minimumWidth()), max(def_h, self.minimumHeight()))
            else:
                self.resize(1320, 820)
        self._update_limits()

    def _update_limits(self):
        if self.simple is not None:
            self.simple.sync_layout_cache()
            self.simple.left_pane.layout().invalidate()
            self.simple.left_pane.layout().activate()
            self.simple.layout().invalidate()
            self.simple.layout().activate()
            self.simple.updateGeometry()
        self.stack.updateGeometry()
        layout = self.centralWidget().layout()
        layout.invalidate()
        layout.activate()
        min_sz = layout.minimumSize()
        if self.mode == "simple" and self.simple is not None:
            self.simple.main_splitter.updateGeometry()
            left_min = self.simple.left_pane.minimumSizeHint().expandedTo(self.simple.left_pane.minimumSize())
            if (
                self.simple.sidebar.isVisibleTo(self.simple)
                and self.simple.main_splitter.orientation() == Qt.Orientation.Horizontal
            ):
                side_min = self.simple.sidebar.minimumSize()
                pane_min_w = left_min.width() + self.simple.main_splitter.handleWidth() + side_min.width()
            else:
                pane_min_w = left_min.width()
            top_min_w = self.top_bar.layout().minimumSize().width()
            min_w = max(top_min_w, pane_min_w)
            top_h = self.top_bar.layout().heightForWidth(max(self.width(), min_w, 300))
            min_h = top_h + left_min.height()
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                min_w = min(min_w, max(300, avail.width() - 24))
                min_h = min(min_h, max(240, avail.height() - 36))
            min_sz = QSize(min_w, min_h)
        max_w = 16777215
        if (
            self.mode == "simple"
            and self.simple is not None
            and getattr(self.config, "simple_keypad_size_mode", "expand") == "capped"
            and not self.simple.sidebar.isVisibleTo(self.simple)
        ):
            left_cap = self.simple.left_pane.maximumWidth()
            if left_cap < 16777215:
                max_w = max(min_sz.width(), left_cap)
        min_w_target = min(max(self.width(), min_sz.width()), max_w)
        if self.mode == "simple":
            if self.simple._panel_mode == "none":
                if self._expanded_simple_height is None and self.height() > min_sz.height():
                    self._expanded_simple_height = self.height()
                self.setMinimumSize(min_sz)
                self.setMaximumSize(QSize(max_w, 16777215))
                self.resize(min_w_target, min_sz.height())
                return
            if self._expanded_simple_height is not None:
                target_h = max(self._expanded_simple_height, min_sz.height())
                self._expanded_simple_height = None
                self.setMaximumSize(QSize(max_w, 16777215))
                self.resize(min_w_target, target_h)
                self.setMinimumSize(min_sz)
                return
        if self.width() < min_sz.width() or self.height() < min_sz.height() or self.width() > max_w:
            self.setMaximumSize(QSize(max_w, 16777215))
            self.resize(min_w_target, max(self.height(), min_sz.height()))
            self.setMinimumSize(min_sz)
        else:
            self.setMinimumSize(min_sz)
            self.setMaximumSize(QSize(max_w, 16777215))

    def _apply_preferences(self, config):
        self.config = config
        self._text.refresh(config.language, keep_english_terms=config.keep_english_terms)
        self.simple.apply_config(config)
        palette = get_palette(config.theme)
        self.setPalette(qt_palette(palette))
        self.setStyleSheet(build_stylesheet(palette, config))
        self._refresh_shell_icons()
        self.simple.refresh()
        self._update_limits()
        QTimer.singleShot(0, self._update_limits)

    def open_settings(self):
        from .settings_dialog import SettingsDialog

        controller = self.studio.controller if self.studio is not None else None
        dialog = SettingsDialog(self.config, self, config_path=self.config_path, controller=controller)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            config = dialog.result_config()
            if self.studio is not None:
                self.studio.controller.update_config(config)
            else:
                self._apply_preferences(config)
                self._save()

    def closeEvent(self, event):
        self._record_geometry()
        if self.studio is not None:
            self.config.horizontal_sizes = self.studio.h_splitter.sizes()
            self.config.vertical_sizes = (
                self.studio._inspector_sizes
                if self.config.inspector_collapsed
                else self.studio.v_splitter.sizes()
            )
            self.studio.controller.shutdown()
        self._save()
        if self.simple is not None:
            self.simple._uninstall_app_event_filter()
        super().closeEvent(event)


def launch(argv: Sequence[str] | None = None, *, mode=None, config_path=None) -> int:
    """Launch the desktop application in the requested or configured mode."""
    if sys.platform == "win32":
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id("rpkbin.app.calc")
    app = QApplication.instance() or QApplication(list(argv) if argv is not None else sys.argv)
    app.setWindowIcon(application_icon())
    win = CalculatorWindow(mode, config_path=config_path)
    win.show()
    return app.exec()
