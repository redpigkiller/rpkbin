"""Simple calculator page and its hardware bit controls (aligned with prototype v4)."""

from __future__ import annotations

from dataclasses import replace
from decimal import InvalidOperation
from fractions import Fraction
from functools import partial
import keyword
import re

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from ..core.format_engine import FormatEngine, FxpSpec
from ..core.scalar_fxp import build_fxp_bit_cells
from ..local_engine import BASES, SimpleCalcEngine, VariableSnapshot, display_fraction
from .flow_layout import FlowLayout
from .i18n import UiText, ask, prompt_text
from .icons import make_icon
from .theme import (
    code_font_family, get_palette, install_shift_horizontal_wheel, repolish,
    scaled_px, set_role,
)


_BINARY_OPS = frozenset({"+", "-", "*", "/", "%", "&", "|", "^", "NAND", "NOR", "<<", ">>"})


def _scroll_line_edit_horizontal(edit: QLineEdit, event) -> bool:
    delta = event.angleDelta().x() or event.angleDelta().y()
    if delta == 0:
        return False
    text_len = len(edit.text())
    if not text_len:
        return False
    mid_y = max(1, edit.height() // 2)
    vis_left = edit.cursorPositionAt(QPoint(4, mid_y))
    vis_right = edit.cursorPositionAt(QPoint(max(4, edit.width() - 4), mid_y))
    if vis_left <= 0 and vis_right >= text_len:
        return False
    step = max(2, round(abs(delta) / 120 * 5))
    if delta < 0:
        edit.setCursorPosition(min(text_len, vis_right + step))
    else:
        edit.setCursorPosition(max(0, vis_left - step))
    return True


class OperandEdit(QLineEdit):
    pasted = Signal(str)
    escaped = Signal()
    equalsRequested = Signal()
    backspaceWhenEmpty = Signal()
    beforeTypeChar = Signal(str)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            self.pasted.emit(QGuiApplication.clipboard().text())
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        if event.key() == Qt.Key.Key_Backspace and not event.modifiers() and not self.text():
            self.backspaceWhenEmpty.emit()
            return
        mods = event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier & ~Qt.KeyboardModifier.KeypadModifier
        txt = event.text()
        if not mods and txt == "=":
            self.equalsRequested.emit()
            return
        if not mods and txt and len(txt) == 1 and txt in "0123456789abcdefABCDEF.+-*/%&|^~()<>":
            self.beforeTypeChar.emit(txt)
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) or abs(event.angleDelta().x()) > abs(event.angleDelta().y()):
            if _scroll_line_edit_horizontal(self, event):
                event.accept()
                return
        super().wheelEvent(event)


class BaseRowFrame(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class ClickableBaseEdit(QLineEdit):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            moved = (
                self._press_pos is not None
                and (event.position() - self._press_pos).manhattanLength() > QApplication.startDragDistance()
            )
            self._press_pos = None
            if not moved and not self.hasSelectedText():
                self.clicked.emit()

    def wheelEvent(self, event):
        if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) or abs(event.angleDelta().x()) > abs(event.angleDelta().y()):
            if _scroll_line_edit_horizontal(self, event):
                event.accept()
                return
        super().wheelEvent(event)


class DoubleClickLabel(QLabel):
    doubleClicked = Signal()

    def mousePressEvent(self, event):
        # Allow single-clicks on non-editing tags to bubble up to the base row frame.
        event.ignore()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _BadgeLineEdit(QLineEdit):
    cancelled = Signal()
    focusLost = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focusLost.emit()


class FxpFormatBadge(QWidget):
    """Subtle auto-sized FXP format pill that switches to inline edit on double-click."""

    submitted = Signal(str)

    def __init__(self, parent=None, *, placeholder: str = "Int32"):
        super().__init__(parent)
        self._placeholder = placeholder
        self._raw_text = ""
        self._editing = False
        self._cfg = None
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.label = DoubleClickLabel(placeholder, self)
        set_role(self.label, "baseSubTag", code=True)
        self.label.setProperty("clickableTag", True)
        self.label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.label.doubleClicked.connect(self.start_edit)
        layout.addWidget(self.label)

        self.edit = _BadgeLineEdit(self)
        set_role(self.edit, "compareFmtEdit", code=True)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.edit.returnPressed.connect(self._on_return_pressed)
        self.edit.cancelled.connect(lambda: self.finish_edit(commit=False))
        self.edit.focusLost.connect(lambda: self.finish_edit(commit=False))
        self.edit.hide()
        layout.addWidget(self.edit)

    def setToolTip(self, tip: str):
        super().setToolTip(tip)
        self.label.setToolTip(tip)

    def set_config(self, cfg):
        self._cfg = cfg
        if self._editing:
            self._sync_edit_width()

    def set_format_text(self, text: str, *, placeholder: str | None = None):
        if placeholder is not None:
            self._placeholder = placeholder
            self.edit.setPlaceholderText(placeholder)
        self._raw_text = text
        display_text = text or self._placeholder
        self.label.setText(display_text)
        self.label.adjustSize()
        if not self._editing and not self.edit.hasFocus():
            self.edit.setText(text)

    def set_active_base(self, active: bool):
        for w in (self.label, self.edit):
            if w.property("activeBase") != active:
                w.setProperty("activeBase", active)
                repolish(w)

    def _sync_edit_width(self):
        min_w = scaled_px(56, self._cfg) if self._cfg is not None else 56
        fm = self.edit.fontMetrics()
        txt = self.edit.text() or self._placeholder
        w = max(min_w, fm.horizontalAdvance(txt) + 18)
        self.edit.setFixedWidth(min(w, scaled_px(130, self._cfg) if self._cfg is not None else 130))

    def start_edit(self):
        self._editing = True
        self.edit.setText(self._raw_text)
        self._sync_edit_width()
        self.label.hide()
        self.edit.show()
        self.edit.setFocus()
        self.edit.selectAll()

    def _on_return_pressed(self):
        # Supports both interactive Enter after double-click and programmatic returnPressed tests.
        text = self.edit.text()
        self._editing = False
        self.edit.hide()
        self.label.show()
        self.submitted.emit(text)

    def finish_edit(self, *, commit: bool):
        if not self._editing:
            return
        self._editing = False
        text = self.edit.text()
        self.edit.hide()
        self.label.show()
        if commit:
            self.submitted.emit(text)
        else:
            self.edit.setText(self._raw_text)


class _CompactLabel(QLabel):
    """Single-line label that prefers its full width and elides cleanly when squeezed narrow."""

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:
        if self.textFormat() == Qt.TextFormat.RichText or self.width() >= super().minimumSizeHint().width():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        elided = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, self.width()))
        painter.drawText(self.rect(), int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), elided)


def _min_scaled(px: int, cfg, *, cap_factor: float = 1.45) -> int:
    """Scale pixel metrics conservatively for panel/control minimums so 160% zoom stays fit."""
    return min(scaled_px(px, cfg), round(px * cap_factor))


class _CompactLegend(QWidget):
    """Bit role color legend whose minimum width yields to narrow panes without RichText bloat."""

    def __init__(self, parent=None, cfg=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._text = UiText()
        self._items: list[tuple[QLabel, str, QLabel, str]] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for role, name in (("danger", "S"), ("warning", "Int"), ("accent", "Frac")):
            item = QHBoxLayout()
            item.setContentsMargins(0, 0, 0, 0)
            item.setSpacing(2)
            dot = QLabel("●", self)
            lbl = QLabel(name, self)
            set_role(lbl, "muted")
            item.addWidget(dot)
            item.addWidget(lbl)
            row.addLayout(item)
            self._items.append((dot, role, lbl, name))
        if cfg is not None:
            self.set_config(cfg)

    def set_config(self, cfg) -> None:
        self._text.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        palette = get_palette(cfg.theme)
        for dot, role, lbl, source in self._items:
            dot.setStyleSheet(f"color: {getattr(palette, role)};")
            lbl.setText(self._text(source))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())


class BitGrid(QWidget):
    """Windows Calculator style 4-Nibble bit selector with per-nibble index labels."""

    flipped = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setHorizontalSpacing(6)
        self._grid.setVerticalSpacing(6)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._groups: list[QFrame] = []
        self._buttons: dict[int, QToolButton] = {}
        self._order: list[int] = []
        self._compact = False
        self._cfg = None
        self._text = UiText()

    def set_config(self, cfg):
        self._cfg = cfg
        self._text.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        self._order = []

    def set_compact(self, compact: bool):
        if self._compact != compact:
            self._compact = compact
            self._order = []

    def render(self, cells):
        indices = [cell.bit_index for cell in cells]
        focused = next((index for index, button in self._buttons.items() if button.hasFocus()), None)
        if indices != self._order:
            for group in self._groups:
                self._grid.removeWidget(group)
                group.hide()
                group.deleteLater()
            self._groups.clear()
            self._buttons.clear()
            self._order = indices

            # Group into 4-bit nibbles in display order
            chunks = []
            current_chunk = []
            prev_nibble = None
            for cell in cells:
                nib_id = cell.bit_index // 4
                if prev_nibble is not None and nib_id != prev_nibble and current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = []
                current_chunk.append(cell)
                prev_nibble = nib_id
            if current_chunk:
                chunks.append(current_chunk)

            raw_w, raw_h = (24, 24) if self._compact else (24, 26)
            btn_w = scaled_px(raw_w, self._cfg)
            btn_h = scaled_px(raw_h, self._cfg)
            for chunk in chunks:
                group = QFrame(self)
                group.setObjectName("CalcNibble")
                group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                v_box = QVBoxLayout(group)
                v_box.setContentsMargins(1, 1, 1, 1)
                v_box.setSpacing(1)

                bit_row = QHBoxLayout()
                bit_row.setContentsMargins(0, 0, 0, 0)
                bit_row.setSpacing(1)
                for cell in chunk:
                    button = QToolButton(group)
                    set_role(button, "simpleBit")
                    button.clicked.connect(
                        lambda checked=False, index=cell.bit_index: self.flipped.emit(index)
                    )
                    button.setFixedSize(btn_w, btn_h)
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                    bit_row.addWidget(button)
                    self._buttons[cell.bit_index] = button
                    if cell.has_radix_after:
                        dot = QLabel(".", group)
                        set_role(dot, "target")
                        dot.setFixedSize(6, btn_h)
                        dot.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
                        bit_row.addWidget(dot)
                v_box.addLayout(bit_row)

                lo_idx = min(c.bit_index for c in chunk)
                idx_lbl = QLabel(str(lo_idx), group)
                set_role(idx_lbl, "muted")
                idx_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                v_box.addWidget(idx_lbl)
                self._groups.append(group)
            self._reflow()

        for cell in cells:
            button = self._buttons[cell.bit_index]
            button.setText(str(cell.value))
            role = cell.role
            val_str = str(cell.value)
            if button.property("bitRole") != role or button.property("bitValue") != val_str:
                button.setProperty("bitRole", role)
                button.setProperty("bitValue", val_str)
                repolish(button)
            button.setAccessibleName(
                self._text("Bit {index}, {value}, {role}", index=cell.bit_index, value=cell.value, role=cell.role)
            )
            radix = self._text(" · binary point") if cell.has_radix_after else ""
            button.setToolTip(f"{cell.label} · 2^{cell.weight_power}{radix}")
            if focused == cell.bit_index:
                button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _reflow(self):
        if not self._groups:
            return
        focus = self.focusWidget()
        # Windows Calculator style: up to 4 nibbles (16 bits) per row; wrap gracefully at narrow widths
        avail = max(120, self.width())
        item_w = max(group.sizeHint().width() for group in self._groups) + 6
        columns = min(4, max(1, avail // max(1, item_w)))
        for index, group in enumerate(self._groups):
            self._grid.removeWidget(group)
            self._grid.addWidget(group, index // columns, index % columns)
        if focus in self._buttons.values():
            focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()


class SimpleCalcWidget(QWidget):
    local_state_changed = Signal()
    limits_changed = Signal()
    sidebar_auto_toggled = Signal(bool)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.engine = SimpleCalcEngine(config)
        self._text = UiText(config.language, keep_english_terms=config.keep_english_terms)
        self.base = "REAL"
        self.selected_fxp_idx = 0  # 0 = primary FXP, >= 1 = compare_specs[idx - 1]
        self.cfg_target_idx = 0
        self.bit_order = "lsb" if config.bit_order == "lsb_first" else "msb"
        self._panel_mode = "keypad"
        self._pending_vsplit_mode: str | None = None
        self._saved_keypad_height: int | None = None
        self._saved_bits_height: int | None = None
        self._pending_carry_lower_h: int | None = None
        self._restoring_panel_split = False
        self._tokens: list[tuple[str, str]] = []
        self._operands: dict[str, Fraction] = {}
        self._replace_input = True
        self._suppress_edit_started = False
        self._comparison_rows = []
        self._compare_widgets: list[QWidget] = []
        self._radix_row_order: list[str] = list(BASES)
        self._fxp_row_order: list[int] = [0]
        self._drag_row_sources: dict[QObject, BaseRowFrame] = {}
        self._row_drag_state: dict[str, object] | None = None
        self._keypad_main_btns: list[QPushButton] = []
        self._keypad_op_btns: list[QPushButton] = []
        self._keypad_aux_btns: list[QPushButton] = []
        self._shift_mode_btns: dict[str, QPushButton] = {}
        self._keypad_dims_key = None
        self._keypad_font_sig: tuple[int, int, int, int] | None = None
        self._hero_dims_key = None
        self._hero_font_sig: tuple[int, int] | None = None
        self._error_text = ""
        self._error_key: tuple[str, dict] | None = None
        self._error_expr = ""
        self._var_col_order: list[str] = ["var", "HEX", "DEC", "OCT", "BIN", "REAL", "FXP"]
        self._user_resized_widths: dict[str, int] = {}
        self._programmatic_var_col_resize = False
        self._last_table_scale = config.ui_scale
        self._renaming_var: str | None = None
        self._auto_collapsed_narrow = False
        self._user_toggled_sidebar_narrow = False
        self._saved_main_h_sizes: list[int] | None = None
        self._hero_copy_timer = QTimer(self)
        self._hero_copy_timer.setSingleShot(True)
        self._hero_copy_timer.setInterval(400)
        self._hero_copy_timer.timeout.connect(self._refresh_icons)
        self._row_copy_timers: dict[str, QTimer] = {}

        self.setObjectName("SimpleCalc")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.setObjectName("SimpleMainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(scaled_px(6, config))
        root.addWidget(self.main_splitter, 1)

        # ── Left Column: Main Calculator ──
        self.left_pane = QWidget(self.main_splitter)
        self.left_pane.setObjectName("SimpleLeftPane")
        self.left_pane.setMinimumWidth(scaled_px(320, config))
        main = QVBoxLayout(self.left_pane)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self._main_layout = main
        self.main_splitter.addWidget(self.left_pane)

        self.left_vsplitter = QSplitter(Qt.Orientation.Vertical, self.left_pane)
        self.left_vsplitter.setObjectName("LeftVerticalSplitter")
        self.left_vsplitter.setChildrenCollapsible(False)
        self.left_vsplitter.setHandleWidth(scaled_px(6, config))
        main.addWidget(self.left_vsplitter, 1)

        self.top_display_frame = QFrame(self.left_vsplitter)
        self.top_display_frame.setObjectName("TopDisplayFrame")
        self.top_display_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        top_hbox = QHBoxLayout(self.top_display_frame)
        top_hbox.setContentsMargins(0, 0, 0, 0)
        top_hbox.setSpacing(0)
        self.top_splitter = QSplitter(Qt.Orientation.Horizontal, self.top_display_frame)
        self.top_splitter.setObjectName("TopDisplaySplitter")
        self.top_splitter.setChildrenCollapsible(False)
        self.top_splitter.setHandleWidth(scaled_px(5, config))
        self._build_base_rows(self.top_splitter)
        self._build_hero_screen(self.top_splitter)
        self.top_splitter.setStretchFactor(0, 5)
        self.top_splitter.setStretchFactor(1, 3)
        self.top_splitter.setSizes([scaled_px(265, config), scaled_px(155, config)])
        top_hbox.addWidget(self.top_splitter)
        self.left_vsplitter.addWidget(self.top_display_frame)

        self.lower_pane = QWidget(self.left_vsplitter)
        lower = QVBoxLayout(self.lower_pane)
        lower.setContentsMargins(0, 0, 0, 0)
        lower.setSpacing(0)
        self._lower_layout = lower
        self._build_mid_controls(lower)
        self._build_fxp_config_drawer(lower)

        self.lower_split = QSplitter(Qt.Orientation.Vertical, self.lower_pane)
        self.lower_split.setObjectName("LowerBitKeypadSplitter")
        self.lower_split.setChildrenCollapsible(False)
        self.lower_split.setHandleWidth(0)
        # Global theme QSplitter vertical handles add 1px top/bottom borders; strip them on this internal divider.
        self.lower_split.setStyleSheet(
            "QSplitter#LowerBitKeypadSplitter::handle:vertical { border: none; background: transparent; height: 0px; }"
        )
        self.bit_panel = self._build_bits()
        self.bit_panel.setParent(self.lower_split)
        self.keypad = self._build_keypad()
        self.keypad.setParent(self.lower_split)
        self.keypad.setObjectName("CalcKeypad")
        self.lower_split.addWidget(self.bit_panel)
        self.lower_split.addWidget(self.keypad)
        self.lower_split.setStretchFactor(0, 1)
        self.lower_split.setStretchFactor(1, 0)
        self.lower_split.handle(1).setEnabled(False)
        lower.addWidget(self.lower_split, 1)
        self.left_vsplitter.addWidget(self.lower_pane)
        self.left_vsplitter.splitterMoved.connect(self._on_left_vsplitter_moved)

        self._collapse_stretch_idx = main.count()
        main.addStretch(0)

        # ── Right Column: Sidebar (Variables | History) ──
        self.sidebar = QFrame(self.main_splitter)
        self.sidebar.setObjectName("SimpleSidebar")
        self.sidebar.setMinimumWidth(scaled_px(210, config))
        self.sidebar.setMaximumWidth(scaled_px(820, config))
        self.sidebar.setMinimumHeight(0)
        self.main_splitter.addWidget(self.sidebar)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes([scaled_px(430, config), scaled_px(250, config)])
        self._build_sidebar()

        if config.simple_last_fxp:
            self._run(lambda: self.engine.set_primary(config.simple_last_fxp))
        self.bits.set_config(config)
        self._apply_scale_metrics(config)
        self._refresh_formats(refresh_icons=False)
        self._set_panel("keypad", refresh=False)
        self.refresh(reset_input=True)
        self._refresh_sidebars()
        QTimer.singleShot(0, self._update_responsive_metrics)

        self._app_filter_installed = False

    def _install_app_event_filter(self):
        if self._app_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def _uninstall_app_event_filter(self):
        if not getattr(self, "_app_filter_installed", False):
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._app_filter_installed = False

    def showEvent(self, event):
        super().showEvent(event)
        self._install_app_event_filter()
        self.sync_layout_cache()
        self._sync_lower_pane_bounds()
        self._apply_pending_vsplit()
        self._update_responsive_metrics()

    def hideEvent(self, event):
        self._finish_row_drag()
        self._uninstall_app_event_filter()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self._drawer_height_sig = None
            self._sync_fxp_drawer_control_heights()
            if hasattr(self, "lower_pane"):
                self.sync_layout_cache()
                self._sync_lower_pane_bounds()

    # ── UI Builders ────────────────────────────────────────────────────

    def _build_hero_screen(self, parent_container):
        self.hero_frame = QFrame(self.top_display_frame)
        self.hero_frame.setObjectName("HeroScreen")
        self.hero_frame.setMinimumWidth(scaled_px(116, self.engine.config))
        hero = QVBoxLayout(self.hero_frame)
        hero.setContentsMargins(8, 5, 8, 5)
        hero.setSpacing(2)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(4)

        self.quant_rule_badge = QLabel("RND · SAT", self.hero_frame)
        self.quant_rule_badge.setObjectName("QuantRuleBadge")
        meta_row.addWidget(self.quant_rule_badge)
        meta_row.addStretch(1)

        self.copy_hero_btn = QToolButton(self.hero_frame)
        self.copy_hero_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.bind(
            self.copy_hero_btn,
            "Copy current value (with 0x/0b/0o prefix; REAL/FXP copies digits only)",
            method="setToolTip",
        )
        self._text.bind(self.copy_hero_btn, "Copy current value", method="setAccessibleName")
        self.copy_hero_btn.clicked.connect(self._copy_hero_value)
        meta_row.addWidget(self.copy_hero_btn)
        hero.addLayout(meta_row)

        self.status = QLabel(self.hero_frame)
        self.status.setObjectName("ClampBadge")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.status.hide()
        hero.addWidget(self.status)

        self.expression = QLabel(self.hero_frame)
        set_role(self.expression, "muted", code=True)
        self.expression.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.expression.setWordWrap(True)
        hero.addWidget(self.expression)

        hero.addStretch(1)

        num_row = QHBoxLayout()
        num_row.setContentsMargins(0, 2, 0, 0)
        num_row.setSpacing(4)

        self.input = OperandEdit(self.hero_frame)
        self.input.setObjectName("CalcInput")
        self._text.bind(self.input, "Calculator operand", method="setAccessibleName")
        self.input.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.input.setMaxLength(512)
        self.input.setMinimumWidth(48)
        self.input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.input.returnPressed.connect(self.equals)
        self.input.equalsRequested.connect(self.equals)
        self.input.escaped.connect(self.clear)
        self.input.backspaceWhenEmpty.connect(self._backspace_entry)
        self.input.beforeTypeChar.connect(self._on_before_type_char)
        self.input.textEdited.connect(self._edit_started)
        self.input.pasted.connect(self.paste)
        num_row.addWidget(self.input, 1)

        hero.addLayout(num_row)
        parent_container.addWidget(self.hero_frame)

    def _build_base_rows(self, parent_container):
        self.base_container = QFrame(self.top_display_frame)
        self.base_container.setObjectName("BaseRowsContainer")
        self.base_container.setMinimumWidth(scaled_px(180, self.engine.config))
        vbox = QVBoxLayout(self.base_container)
        vbox.setContentsMargins(5, 3, 5, 3)
        vbox.setSpacing(0)

        self.base_scroll = QScrollArea(self.base_container)
        self.base_scroll.setObjectName("BaseScrollArea")
        self.base_scroll.setWidgetResizable(True)
        self.base_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.base_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.base_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.base_host = QWidget(self.base_scroll)
        self.base_layout = QVBoxLayout(self.base_host)
        self.base_layout.setContentsMargins(0, 0, 0, 0)
        self.base_layout.setSpacing(1)
        self.base_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.base_scroll.setWidget(self.base_host)
        vbox.addWidget(self.base_scroll)

        self.rows = {}
        self.row_frames = {}
        self.row_indicators = {}
        self.row_copy_btns = {}
        self.row_right_boxes = {}

        lbl_w = scaled_px(38, self.engine.config)

        for base in (*BASES, "REAL", "FXP"):
            row_frame = BaseRowFrame(self.base_host)
            set_role(row_frame, "baseRow")
            row_frame.setCursor(Qt.CursorShape.PointingHandCursor)
            row_frame.clicked.connect(lambda b=base: self.select_base(b, fxp_idx=0))
            row = QHBoxLayout(row_frame)
            row.setContentsMargins(3, 0, 4, 0)
            row.setSpacing(3)

            indicator = QFrame(row_frame)
            set_role(indicator, "baseIndicator")
            indicator.setFixedSize(4, 13)
            indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            row.addWidget(indicator)

            button = QPushButton(base, row_frame)
            set_role(button, "baseLabelBtn")
            button.setCheckable(True)
            button.setFixedWidth(lbl_w)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if base == "DEC":
                self._text.bind(button, "Unscaled integer underlying active FXP format", method="setToolTip")
            button.clicked.connect(lambda checked=False, b=base: self.select_base(b, fxp_idx=0))
            row.addWidget(button)

            value = ClickableBaseEdit(row_frame)
            set_role(value, "baseValueEdit", code=True)
            value.setReadOnly(True)
            value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            value.setMinimumWidth(36)
            value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            value.setAccessibleName(base)
            value.clicked.connect(lambda b=base: self.select_base(b, fxp_idx=0))
            row.addWidget(value, 1)

            right_box = QWidget(row_frame)
            set_role(right_box, "baseRowRight")
            right_box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            right_row = QHBoxLayout(right_box)
            right_row.setContentsMargins(0, 0, 0, 0)
            right_row.setSpacing(max(4, scaled_px(4, self.engine.config)))

            if base == "FXP":
                self._primary_fmt_badge = FxpFormatBadge(right_box, placeholder="Int32")
                self._primary_fmt_badge.set_config(self.engine.config)
                self._primary_fmt_badge.submitted.connect(self._apply_primary_fmt_text)
                self._text.bind(
                    self._primary_fmt_badge,
                    "This format determines the values shown in HEX, DEC, OCT, and BIN. Double-click to edit.",
                    method="setToolTip",
                )
                self.primary_fmt_edit = self._primary_fmt_badge.edit
                self._text.bind(self.primary_fmt_edit, "Primary FXP format", method="setAccessibleName")
                right_row.addWidget(self._primary_fmt_badge)

                self.primary_cfg_btn = QToolButton(right_box)
                set_role(self.primary_cfg_btn, "iconButton")
                self.primary_cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self._text.bind(self.primary_cfg_btn, "Configure FXP format", method="setAccessibleName")
                self._text.bind(self.primary_cfg_btn, "Configure FXP format", method="setToolTip")
                self.primary_cfg_btn.clicked.connect(lambda: self._toggle_fxp_drawer(0))
                right_row.addWidget(self.primary_cfg_btn)

            copy_btn = QToolButton(right_box)
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setAccessibleName(self._text("Copy {base}", base=base))
            copy_btn.setToolTip(self._text("Copy {base}", base=base))
            copy_btn.clicked.connect(lambda checked=False, b=base, edit=value: self._copy_row_value(b, edit))
            right_row.addWidget(copy_btn)
            row.addWidget(right_box)

            self.rows[base] = (button, value)
            self.row_frames[base] = row_frame
            self.row_indicators[base] = indicator
            self.row_copy_btns[base] = copy_btn
            self.row_right_boxes[base] = right_box
            if base != "REAL":
                self._register_drag_row(row_frame, button)
            self.base_layout.addWidget(row_frame)

        parent_container.addWidget(self.base_container)

    def _build_mid_controls(self, parent_layout: QVBoxLayout):
        self.mid_bar = QFrame(self.lower_pane if hasattr(self, "lower_pane") else self.left_pane)
        self.mid_bar.setObjectName("MidControlBar")
        self.mid_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        controls = FlowLayout(self.mid_bar, margin=0, h_spacing=3, v_spacing=3)
        controls.setContentsMargins(4, 3, 4, 3)

        seg_frame = QFrame(self.mid_bar)
        set_role(seg_frame, "segmentContainer")
        seg_row = QHBoxLayout(seg_frame)
        seg_row.setContentsMargins(2, 2, 2, 2)
        seg_row.setSpacing(2)

        self.panel_mode_group = QButtonGroup(self)
        self.panel_mode_group.setExclusive(True)
        self.panel_mode_btns: dict[str, QToolButton] = {}
        for icon_name, mode, label in (
            ("keypad", "keypad", "Keypad only"),
            ("bits", "bits", "Bits only"),
            ("split_both", "both", "Keypad and bits"),
            ("chevron_up", "none", "Collapse panels"),
        ):
            btn = QToolButton(seg_frame)
            set_role(btn, "segmentIconBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._text.bind(btn, label, method="setAccessibleName")
            self._text.bind(btn, label, method="setToolTip")
            btn.clicked.connect(lambda checked=False, m=mode: self._set_panel(m))
            self.panel_mode_group.addButton(btn)
            self.panel_mode_btns[mode] = btn
            seg_row.addWidget(btn)
        controls.addWidget(seg_frame)

        self.ms_btn = QPushButton("MS", self.mid_bar)
        set_role(self.ms_btn, "barActionBtn", code=True)
        self.ms_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.bind(self.ms_btn, "Store in M", method="setAccessibleName")
        self._text.bind(self.ms_btn, "MS: Store current value into memory slot M", method="setToolTip")
        self.ms_btn.clicked.connect(lambda: self._store("M"))
        controls.addWidget(self.ms_btn)

        self.int32_btn = QPushButton("Int32", self.mid_bar)
        set_role(self.int32_btn, "barActionBtn", code=True)
        self.int32_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.bind(self.int32_btn, "Reset FXP to Int32", method="setAccessibleName")
        self._text.bind(
            self.int32_btn,
            "Int32: Reset primary FXP format to default 32-bit integer (Int32)",
            method="setToolTip",
        )
        self.int32_btn.clicked.connect(self._reset_primary_to_int32)
        controls.addWidget(self.int32_btn)

        self.format_combo = QComboBox(self.mid_bar)
        self.format_combo.setObjectName("SimpleFormatCombo")
        self.format_combo.setEditable(True)
        self.format_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.format_combo.setMinimumWidth(48)
        self.format_combo.setMaximumWidth(140)
        self.format_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.format_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._text.bind(self.format_combo, "Primary FXP format", method="setAccessibleName")
        self.format_combo.activated.connect(self._format_entered)
        self.format_combo.lineEdit().setMaxLength(512)
        self.format_combo.lineEdit().setPlaceholderText("FXP")
        self.format_combo.lineEdit().returnPressed.connect(self._format_entered)
        combo_overlay = QHBoxLayout(self.format_combo)
        combo_overlay.setContentsMargins(0, 0, 5, 0)
        combo_overlay.addStretch(1)
        combo_arrow = QLabel("▾", self.format_combo)
        set_role(combo_arrow, "muted")
        combo_arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        combo_overlay.addWidget(combo_arrow)
        controls.addWidget(self.format_combo, stretch=True)

        self.pin_fmt_btn = QToolButton(self.mid_bar)
        self.pin_fmt_btn.setCheckable(True)
        self.pin_fmt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.bind(self.pin_fmt_btn, "Pin or unpin the active FXP format", method="setToolTip")
        self._text.bind(self.pin_fmt_btn, "Pin FXP format", method="setAccessibleName")
        self.pin_fmt_btn.clicked.connect(self._pin_format)
        controls.addWidget(self.pin_fmt_btn)

        self.del_fmt_btn = QToolButton(self.mid_bar)
        self.del_fmt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.bind(self.del_fmt_btn, "Remove this FXP format from history list", method="setToolTip")
        self._text.bind(self.del_fmt_btn, "Remove FXP format", method="setAccessibleName")
        self.del_fmt_btn.clicked.connect(self._remove_format)
        controls.addWidget(self.del_fmt_btn)

        self.inline_cfg_btn = QToolButton(self.mid_bar)
        self.inline_cfg_btn.setCheckable(True)
        self.inline_cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.bind(self.inline_cfg_btn, "Expand or collapse FXP format & quantization drawer", method="setToolTip")
        self._text.bind(self.inline_cfg_btn, "Configure FXP format", method="setAccessibleName")
        self.inline_cfg_btn.clicked.connect(
            lambda: self._toggle_fxp_drawer(self.selected_fxp_idx if self.base == "FXP" else 0)
        )
        controls.addWidget(self.inline_cfg_btn)

        self.add_compare_btn = QPushButton(self.mid_bar)
        self._text.bind(self.add_compare_btn, "+Cmp")
        set_role(self.add_compare_btn, "barActionBtn")
        self.add_compare_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.bind(self.add_compare_btn, "Add FXP comparison", method="setAccessibleName")
        self._text.bind(self.add_compare_btn, "Add an FXP comparison row below", method="setToolTip")
        self.add_compare_btn.clicked.connect(self._add_compare)
        controls.addWidget(self.add_compare_btn)

        parent_layout.addWidget(self.mid_bar)

    def _build_fxp_config_drawer(self, parent_layout: QVBoxLayout):
        self.fxp_drawer = QFrame(self.lower_pane if hasattr(self, "lower_pane") else self.left_pane)
        self.fxp_drawer.setObjectName("FxpConfigDrawer")
        self.fxp_drawer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.fxp_drawer.hide()
        drawer = QVBoxLayout(self.fxp_drawer)
        drawer.setContentsMargins(10, 6, 10, 6)
        drawer.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel(self.fxp_drawer)
        self._text.bind(title, "FXP Format & Quantization")
        set_role(title, "cardTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.cfg_target_lbl = QLabel(self.fxp_drawer)
        self.cfg_target_lbl.setText(self._text("Target: {target}", target="FXP"))
        set_role(self.cfg_target_lbl, "muted", code=True)
        header.addWidget(self.cfg_target_lbl)
        close_btn = QPushButton("✕", self.fxp_drawer)
        set_role(close_btn, "cardBtn")
        close_btn.setFixedSize(24, 24)
        self._text.bind(close_btn, "Close fixed-point settings", method="setAccessibleName")
        self._text.bind(close_btn, "Close fixed-point settings", method="setToolTip")
        close_btn.clicked.connect(self._close_fxp_drawer)
        header.addWidget(close_btn)
        drawer.addLayout(header)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.cfg_syntax_edit = QLineEdit(self.fxp_drawer)
        self._text.bind(self.cfg_syntax_edit, "Enter FXP format", method="setPlaceholderText")
        self._text.bind(self.cfg_syntax_edit, "FXP format syntax", method="setAccessibleName")
        row1.addWidget(self.cfg_syntax_edit, 2)

        self.cfg_signed_combo = QComboBox(self.fxp_drawer)
        self.cfg_signed_combo.addItem(self._text("Signed"), True)
        self.cfg_signed_combo.addItem(self._text("Unsigned"), False)
        self._text.bind(self.cfg_signed_combo, "Signedness", method="setAccessibleName")
        row1.addWidget(self.cfg_signed_combo, 1)

        row1.addWidget(QLabel("WL:", self.fxp_drawer))
        self.cfg_wl_spin = QSpinBox(self.fxp_drawer)
        self.cfg_wl_spin.setRange(1, 128)
        self.cfg_wl_spin.setValue(8)
        row1.addWidget(self.cfg_wl_spin)

        row1.addWidget(QLabel("FWL:", self.fxp_drawer))
        self.cfg_fwl_spin = QSpinBox(self.fxp_drawer)
        self.cfg_fwl_spin.setRange(-128, 128)
        self.cfg_fwl_spin.setValue(4)
        row1.addWidget(self.cfg_fwl_spin)
        drawer.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        q_lbl = QLabel(self.fxp_drawer)
        self._text.bind(q_lbl, "Quantization:")
        row2.addWidget(q_lbl)
        self.cfg_quant_combo = QComboBox(self.fxp_drawer)
        for q in ("RND", "RND_EVEN", "TRN", "TRN_ZERO"):
            self.cfg_quant_combo.addItem(q, q)
        self._text.bind(self.cfg_quant_combo, "Quantization mode", method="setAccessibleName")
        row2.addWidget(self.cfg_quant_combo, 1)

        o_lbl = QLabel(self.fxp_drawer)
        self._text.bind(o_lbl, "Overflow:")
        row2.addWidget(o_lbl)
        self.cfg_ovf_combo = QComboBox(self.fxp_drawer)
        for o in ("SAT", "WRAP"):
            self.cfg_ovf_combo.addItem(o, o)
        self._text.bind(self.cfg_ovf_combo, "Overflow mode", method="setAccessibleName")
        row2.addWidget(self.cfg_ovf_combo, 1)

        self.cfg_apply_btn = QPushButton(self.fxp_drawer)
        self._text.bind(self.cfg_apply_btn, "Apply & Sync Settings")
        set_role(self.cfg_apply_btn, "primary")
        self.cfg_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cfg_apply_btn.clicked.connect(self._apply_fxp_drawer)
        row2.addWidget(self.cfg_apply_btn, 1)
        drawer.addLayout(row2)

        parent_layout.addWidget(self.fxp_drawer)
        self._sync_fxp_drawer_control_heights()

    def _sync_fxp_drawer_control_heights(self):
        if not hasattr(self, "cfg_apply_btn"):
            return
        cfg = self.engine.config
        sig = (
            cfg.ui_scale,
            cfg.ui_font_size,
            cfg.code_font_size,
            cfg.ui_font_family,
            cfg.code_font_family,
            cfg.theme,
            cfg.language,
            self.fxp_drawer.font().key(),
        )
        if getattr(self, "_drawer_height_sig", None) == sig:
            return
        self._drawer_height_sig = sig
        controls = (
            self.cfg_syntax_edit,
            self.cfg_signed_combo,
            self.cfg_wl_spin,
            self.cfg_fwl_spin,
            self.cfg_quant_combo,
            self.cfg_ovf_combo,
            self.cfg_apply_btn,
        )
        for ctrl in controls:
            ctrl.setMinimumHeight(0)
            ctrl.setMaximumHeight(16777215)
            ctrl.ensurePolished()
        target_h = max(ctrl.sizeHint().height() for ctrl in controls)
        for ctrl in controls:
            ctrl.setFixedHeight(target_h)
        if self.fxp_drawer.isVisible():
            self.fxp_drawer.updateGeometry()
            self._sync_lower_pane_bounds()

    def _build_bits(self):
        panel = QFrame(self.left_pane)
        panel.setObjectName("BitPanel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 2, 6, 3)
        layout.setSpacing(2)

        bar_host = QWidget(panel)
        bar = FlowLayout(bar_host, margin=0, h_spacing=4, v_spacing=2)
        title = _CompactLabel(bar_host)
        self._text.bind(title, "Bit Select")
        set_role(title, "cardTitle")
        bar.addWidget(title)

        self._bit_legend = _CompactLegend(bar_host, self.engine.config)
        bar.addWidget(self._bit_legend, stretch=True)

        inv_btn = QPushButton(bar_host)
        self._text.bind(inv_btn, "~ Invert")
        set_role(inv_btn, "cardBtn")
        self._text.bind(inv_btn, "Toggle every bit (0 ↔ 1)", method="setToolTip")
        self._text.bind(inv_btn, "~ Invert", method="setAccessibleName")
        inv_btn.clicked.connect(lambda: self._bit_action(lambda: self.engine.set_raw(~self.engine.raw_uint)))
        bar.addWidget(inv_btn)

        zero_btn = QPushButton(bar_host)
        self._text.bind(zero_btn, "0 Zero")
        set_role(zero_btn, "cardBtn")
        self._text.bind(zero_btn, "Set all bits to zero", method="setToolTip")
        self._text.bind(zero_btn, "0 Zero", method="setAccessibleName")
        zero_btn.clicked.connect(lambda: self._bit_action(lambda: self.engine.set_raw(0)))
        bar.addWidget(zero_btn)

        swap_btn = QPushButton(bar_host)
        self._text.bind(swap_btn, "MSB↔LSB")
        set_role(swap_btn, "cardBtn")
        self._text.bind(swap_btn, "Reverse bit display order (MSB first / LSB first)", method="setToolTip")
        self._text.bind(swap_btn, "MSB↔LSB", method="setAccessibleName")
        swap_btn.clicked.connect(self._reverse_bits)
        bar.addWidget(swap_btn)
        layout.addWidget(bar_host)

        self.bits = BitGrid(panel)
        self.bits.flipped.connect(
            lambda index: self._bit_action(lambda: self.engine.set_raw(self.engine.raw_uint ^ (1 << index)))
        )
        self.bit_scroll = QScrollArea(panel)
        self.bit_scroll.setWidgetResizable(True)
        self.bit_scroll.setWidget(self.bits)
        self.bit_scroll.setMinimumHeight(72)
        self.bit_scroll.setMaximumHeight(220)
        set_role(self.bit_scroll, "bits")
        install_shift_horizontal_wheel(self.bit_scroll)
        layout.addWidget(self.bit_scroll, 1)

        fields_host = QWidget(panel)
        fields = FlowLayout(fields_host, margin=0, h_spacing=4, v_spacing=3)
        range_lbl = _CompactLabel(fields_host)
        self._text.bind(range_lbl, "Bit field [hi:lo]:")
        set_role(range_lbl, "muted")
        fields.addWidget(range_lbl)

        self.bit_range = QLineEdit("[3:0]", fields_host)
        self._text.bind(self.bit_range, "Inclusive bit range", method="setAccessibleName")
        self.bit_range.setFixedWidth(58)
        self.bit_range.setMaxLength(12)
        fields.addWidget(self.bit_range)

        self.bit_value = QLineEdit(fields_host)
        self._text.bind(self.bit_value, "Unsigned bit field value", method="setAccessibleName")
        self._text.bind(self.bit_value, "Unsigned integer", method="setPlaceholderText")
        self.bit_value.setMaxLength(80)
        fields.addWidget(self.bit_value, stretch=True)

        read_btn = QPushButton(fields_host)
        self._text.bind(read_btn, "Read")
        set_role(read_btn, "cardBtn")
        self._text.bind(read_btn, "Read bit field", method="setAccessibleName")
        read_btn.clicked.connect(self.read_field)
        fields.addWidget(read_btn)

        write_btn = QPushButton(fields_host)
        self._text.bind(write_btn, "Write")
        set_role(write_btn, "cardBtn")
        self._text.bind(write_btn, "Write bit field", method="setAccessibleName")
        write_btn.clicked.connect(self.write_field)
        fields.addWidget(write_btn)

        layout.addWidget(fields_host)
        return panel

    _SHIFT_MODE_SPECS = (
        ("arithmetic", "Arith", "Arithmetic shift (sign-extends on >>)"),
        ("logical", "Logic", "Logical shift (zero-fills)"),
        ("rotate", "Rot", "Rotate circular (within bit width)"),
        ("rotate_carry", "Rot·C", "Rotate through carry (current carry={carry})"),
    )

    def _build_keypad(self):
        panel = QWidget(self.left_pane)
        outer = QHBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        inner = QWidget(panel)
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(inner)
        self._keypad_inner = inner

        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(6, 4, 6, 5)
        vbox.setSpacing(3)
        self._keypad_vbox = vbox

        # Row 0: Shift mode label ("Bit shift" / "位元移位") + non-collapsible 4-option selector
        shift_row = QHBoxLayout()
        shift_row.setSpacing(3)
        self._keypad_shift_row = shift_row
        self.shift_mode_lbl = QLabel(inner)
        self.shift_mode_lbl.setObjectName("ShiftModeLabel")
        set_role(self.shift_mode_lbl, "muted")
        self._text.bind(self.shift_mode_lbl, "Bit shift")
        self._text.bind(self.shift_mode_lbl, "Shift mode for << and >> operators", method="setToolTip")
        self.shift_mode_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.shift_mode_lbl.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        shift_row.addWidget(self.shift_mode_lbl, 0)

        self._shift_mode_group = QButtonGroup(inner)
        self._shift_mode_group.setExclusive(True)
        for mode, label_key, _tip_key in self._SHIFT_MODE_SPECS:
            btn = QPushButton(label_key, inner)
            set_role(btn, "shiftModeBtn")
            btn.setCheckable(True)
            btn.setChecked(mode == self.engine.shift_mode)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._shift_mode_group.addButton(btn)
            btn.clicked.connect(lambda checked=False, m=mode: self._set_shift_mode(m))
            self._shift_mode_btns[mode] = btn
            shift_row.addWidget(btn, 1)
        vbox.addLayout(shift_row)

        # Row 1: 6 Bitwise buttons directly above A << >> CE ⌫
        bit_row = QGridLayout()
        bit_row.setSpacing(3)
        self._keypad_bit_row = bit_row
        for col, (label, op_or_act, tip_key) in enumerate((
            ("AND", "&", None),
            ("OR", "|", None),
            ("NOT", "~", None),
            ("NAND", "NAND", "Bitwise NAND"),
            ("NOR", "NOR", "Bitwise NOR"),
            ("XOR", "^", None),
        )):
            btn = QPushButton(label, inner)
            btn.setProperty("calcKey", "bitwise")
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            if tip_key is not None:
                self._text.bind(btn, tip_key, method="setToolTip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if op_or_act == "~":
                btn.clicked.connect(self._press_not)
            else:
                btn.clicked.connect(partial(self._operator, op_or_act))
            self._keypad_aux_btns.append(btn)
            bit_row.addWidget(btn, 0, col)
        vbox.addLayout(bit_row)

        # Main 5x6 Grid (aligned with Windows 11 Programmer Calculator)
        grid = QGridLayout()
        grid.setSpacing(3)
        self._keypad_grid = grid
        self._digit_buttons: dict[str, QPushButton] = {}
        main_rows = (
            (("A", "hex"), ("<<", "op"), (">>", "op"), ("CE", "action"), ("⌫", "action")),
            (("B", "hex"), ("(", "op"), (")", "op"), ("%", "op"), ("÷", "op")),
            (("C", "hex"), ("7", "digit"), ("8", "digit"), ("9", "digit"), ("×", "op")),
            (("D", "hex"), ("4", "digit"), ("5", "digit"), ("6", "digit"), ("−", "op")),
            (("E", "hex"), ("1", "digit"), ("2", "digit"), ("3", "digit"), ("+", "op")),
            (("F", "hex"), ("+/-", "op"), ("0", "digit"), (".", "digit"), ("=", "primary")),
        )
        op_map = {"÷": "/", "×": "*", "−": "-"}
        acc_map = {
            "×": "Multiply",
            "÷": "Divide",
            "−": "Subtract",
            "+": "Add",
            "=": "Equals",
            "⌫": "Backspace",
            "+/-": "Toggle sign",
            "<<": "Shift left",
            ">>": "Shift right",
            "%": "Modulo",
            "(": "Left parenthesis",
            ")": "Right parenthesis",
            ".": "Decimal point",
            "CE": "Clear",
        }
        tip_map = {
            "+/-": "Toggle sign",
            "%": "Modulo operator (%)",
            "CE": "Clear current entry (CE); clears expression & error when C",
            "⌫": "Delete last character",
        }
        for r_idx, row_spec in enumerate(main_rows):
            grid.setRowStretch(r_idx, 1)
            for c_idx, (label, kind) in enumerate(row_spec):
                grid.setColumnStretch(c_idx, 1)
                if label == "=":
                    action = self.equals
                elif label == "CE":
                    action = self._ce_or_ac
                elif label == "⌫":
                    action = self._backspace_entry
                elif label == "+/-":
                    action = self._toggle_sign
                elif label in "0123456789ABCDEF.":
                    action = partial(self._append, label)
                else:
                    action = partial(self._operator, op_map.get(label, label))
                btn = QPushButton("C" if label == "CE" else label, inner)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                btn.setMinimumWidth(0)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                if label in acc_map:
                    self._text.bind(btn, acc_map[label], method="setAccessibleName")
                if label in tip_map:
                    self._text.bind(btn, tip_map[label], method="setToolTip")
                if label == "CE":
                    self.clear_entry_btn = btn
                if kind == "primary":
                    set_role(btn, "primary")
                else:
                    btn.setProperty("calcKey", kind)
                if label in "0123456789ABCDEF.":
                    self._digit_buttons[label] = btn
                if kind in ("digit", "primary"):
                    self._keypad_main_btns.append(btn)
                else:
                    self._keypad_op_btns.append(btn)
                btn.clicked.connect(lambda checked=False, fn=action: fn())
                grid.addWidget(btn, r_idx, c_idx)
        vbox.addLayout(grid, 1)
        self._update_shift_mode_buttons()
        return panel

    def _set_shift_mode(self, mode: str):
        self.engine.set_shift_mode(mode)
        self._update_shift_mode_buttons()
        self.input.setFocus()

    def _update_shift_mode_buttons(self):
        if not hasattr(self, "_shift_mode_btns"):
            return
        carry = self.engine.carry_bit
        for mode, label_key, tip_key in self._SHIFT_MODE_SPECS:
            btn = self._shift_mode_btns.get(mode)
            if btn is None:
                continue
            if mode == "rotate_carry":
                lbl = self._text("Rot·C ({carry})", carry=carry)
                tip = self._text(tip_key, carry=carry)
            else:
                lbl = self._text(label_key)
                tip = self._text(tip_key)
            btn.setText(lbl)
            btn.setAccessibleName(lbl)
            btn.setToolTip(tip)
            btn.setChecked(mode == self.engine.shift_mode)

    def _build_sidebar(self):
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)

        # Sidebar Header: [Variables | History] segmented control + [+ Add]
        header = QFrame(self.sidebar)
        header.setObjectName("SidebarHeader")
        h_row = QHBoxLayout(header)
        h_row.setContentsMargins(8, 5, 8, 5)
        h_row.setSpacing(6)

        seg_frame = QFrame(header)
        set_role(seg_frame, "segmentContainer")
        seg_layout = QHBoxLayout(seg_frame)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)

        self.side_tab_group = QButtonGroup(self)
        self.side_tab_group.setExclusive(True)
        self.tab_vars_btn = QPushButton(seg_frame)
        self._text.bind(self.tab_vars_btn, "Variables")
        set_role(self.tab_vars_btn, "segmentBtn")
        self.tab_vars_btn.setCheckable(True)
        self.tab_vars_btn.setChecked(True)
        self.tab_vars_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_vars_btn.clicked.connect(lambda: self._switch_side_tab(0))

        self.tab_hist_btn = QPushButton(seg_frame)
        self._text.bind(self.tab_hist_btn, "History")
        set_role(self.tab_hist_btn, "segmentBtn")
        self.tab_hist_btn.setCheckable(True)
        self.tab_hist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_hist_btn.clicked.connect(lambda: self._switch_side_tab(1))

        self.side_tab_group.addButton(self.tab_vars_btn)
        self.side_tab_group.addButton(self.tab_hist_btn)
        seg_layout.addWidget(self.tab_vars_btn)
        seg_layout.addWidget(self.tab_hist_btn)
        h_row.addWidget(seg_frame)
        h_row.addStretch(1)

        self.toggle_add_var_btn = QPushButton(header)
        self._text.bind(self.toggle_add_var_btn, "+ Add")
        set_role(self.toggle_add_var_btn, "cardBtn")
        self.toggle_add_var_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_add_var_btn.clicked.connect(self._toggle_add_var_box)
        h_row.addWidget(self.toggle_add_var_btn)
        side_layout.addWidget(header)

        # Inline Add Variable Box (hidden by default)
        self.add_var_box = QFrame(self.sidebar)
        self.add_var_box.setObjectName("SidebarFilterBar")
        self.add_var_box.hide()
        add_box_layout = QVBoxLayout(self.add_var_box)
        add_box_layout.setContentsMargins(8, 5, 8, 5)
        add_box_layout.setSpacing(4)

        add_top = QHBoxLayout()
        add_lbl = QLabel(self.add_var_box)
        self._text.bind(add_lbl, "Add variable (stores currently selected format & value)")
        set_role(add_lbl, "cardTitle")
        add_top.addWidget(add_lbl, 1)
        cancel_add_btn = QPushButton(self.add_var_box)
        self._text.bind(cancel_add_btn, "Cancel")
        set_role(cancel_add_btn, "cardBtn")
        cancel_add_btn.clicked.connect(self._hide_add_var_box)
        add_top.addWidget(cancel_add_btn)
        add_box_layout.addLayout(add_top)

        add_inp_row = QHBoxLayout()
        self.new_var_input = QLineEdit(self.add_var_box)
        self._text.bind(self.new_var_input, "Variable name, e.g. gain", method="setPlaceholderText")
        self._text.bind(self.new_var_input, "Variable name", method="setAccessibleName")
        self.new_var_input.returnPressed.connect(self._confirm_add_var)
        add_inp_row.addWidget(self.new_var_input, 1)
        confirm_add_btn = QPushButton(self.add_var_box)
        self._text.bind(confirm_add_btn, "Create")
        set_role(confirm_add_btn, "primary")
        confirm_add_btn.clicked.connect(self._confirm_add_var)
        add_inp_row.addWidget(confirm_add_btn)
        add_box_layout.addLayout(add_inp_row)
        self.add_var_error = QLabel(self.add_var_box)
        set_role(self.add_var_error, "error")
        self.add_var_error.setWordWrap(True)
        self.add_var_error.hide()
        add_box_layout.addWidget(self.add_var_error)
        side_layout.addWidget(self.add_var_box)

        # Stacked content: Page 0 = Variables, Page 1 = History
        self.side_stack = QStackedWidget(self.sidebar)
        self.side_stack.setMinimumHeight(0)
        side_layout.addWidget(self.side_stack, 1)

        # Page 0: Variables Table + STO/RCL bar
        vars_page = QWidget(self.side_stack)
        v_layout = QVBoxLayout(vars_page)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(0)

        action_bar = QFrame(vars_page)
        action_bar.setObjectName("SidebarFilterBar")
        self.var_action_bar = action_bar
        a_row = QHBoxLayout(action_bar)
        a_row.setContentsMargins(8, 5, 8, 5)
        a_row.setSpacing(6)

        self.var_sto_btn = QPushButton(action_bar)
        self._text.bind(self.var_sto_btn, "STO")
        self._text.bind(
            self.var_sto_btn,
            "Store current value into the selected variable",
            method="setToolTip",
        )
        set_role(self.var_sto_btn, "varModeBtn")
        self.var_sto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.var_sto_btn.clicked.connect(self._on_var_sto_clicked)
        a_row.addWidget(self.var_sto_btn)

        self.var_rcl_btn = QPushButton(action_bar)
        self._text.bind(self.var_rcl_btn, "RCL")
        self._text.bind(
            self.var_rcl_btn,
            "Recall the selected variable",
            method="setToolTip",
        )
        set_role(self.var_rcl_btn, "varModeBtn")
        self.var_rcl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.var_rcl_btn.clicked.connect(self._on_var_rcl_clicked)
        a_row.addWidget(self.var_rcl_btn)

        a_row.addStretch(1)

        self.var_clr_btn = QToolButton(action_bar)
        set_role(self.var_clr_btn, "iconButton")
        self.var_clr_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.bind(self.var_clr_btn, "Clear value / Delete custom variable", method="setToolTip")
        self._text.bind(self.var_clr_btn, "Clear value / Delete custom variable", method="setAccessibleName")
        self.var_clr_btn.clicked.connect(self._on_var_clr_clicked)
        a_row.addWidget(self.var_clr_btn)
        v_layout.addWidget(action_bar)

        self.var_table = QTableWidget(vars_page)
        self.var_table.setObjectName("SimpleVarTable")
        self._text.bind(self.var_table, "Variables table", method="setAccessibleName")
        self.var_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.var_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.var_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.var_table.setAlternatingRowColors(True)
        self.var_table.setWordWrap(False)
        self.var_table.verticalHeader().hide()
        self.var_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.var_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.var_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.var_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.var_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header_view = self.var_table.horizontalHeader()
        header_view.setSectionsMovable(True)
        header_view.setStretchLastSection(False)
        header_view.setMinimumSectionSize(scaled_px(44, self.engine.config))
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_view.sectionMoved.connect(self._on_var_col_moved)
        header_view.sectionResized.connect(self._on_var_col_resized)
        self.var_table.cellDoubleClicked.connect(self._on_var_cell_double_clicked)
        self.var_table.itemSelectionChanged.connect(self._update_var_action_buttons)
        self.var_table.customContextMenuRequested.connect(self._on_var_context_menu)
        for seq, slot in (
            (Qt.Key.Key_Return, self._on_var_rcl_clicked),
            (Qt.Key.Key_Enter, self._on_var_rcl_clicked),
            (Qt.Key.Key_F2, self._rename_selected_var),
            (Qt.Key.Key_Delete, self._on_var_clr_clicked),
        ):
            sc = QShortcut(QKeySequence(seq), self.var_table)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        install_shift_horizontal_wheel(self.var_table)
        v_layout.addWidget(self.var_table, 1)
        self.side_stack.addWidget(vars_page)

        # Page 1: History
        hist_page = QWidget(self.side_stack)
        h_layout = QVBoxLayout(hist_page)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        hist_bar = QFrame(hist_page)
        hist_bar.setObjectName("SidebarFilterBar")
        hb_row = QHBoxLayout(hist_bar)
        hb_row.setContentsMargins(8, 5, 8, 5)
        hb_lbl = QLabel(hist_bar)
        self._text.bind(hb_lbl, "Click any entry to recall it")
        set_role(hb_lbl, "muted")
        hb_row.addWidget(hb_lbl)
        hb_row.addStretch(1)
        clear_hist_btn = QPushButton(hist_bar)
        self._text.bind(clear_hist_btn, "Clear all")
        set_role(clear_hist_btn, "cardBtn")
        clear_hist_btn.clicked.connect(self._clear_history)
        hb_row.addWidget(clear_hist_btn)
        h_layout.addWidget(hist_bar)

        self.hist_scroll = QScrollArea(hist_page)
        self.hist_scroll.setWidgetResizable(True)
        self.hist_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.hist_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        install_shift_horizontal_wheel(self.hist_scroll)
        self.hist_scroll.setMinimumHeight(0)
        self.hist_cards_host = QWidget(self.hist_scroll)
        self.hist_cards_layout = QVBoxLayout(self.hist_cards_host)
        self.hist_cards_layout.setContentsMargins(6, 6, 8, 6)
        self.hist_cards_layout.setSpacing(6)
        self.hist_cards_layout.addStretch(1)
        self.hist_scroll.setWidget(self.hist_cards_host)
        h_layout.addWidget(self.hist_scroll, 1)
        self.side_stack.addWidget(hist_page)

    # ── Theme & Icon Refresh ──────────────────────────────────────────

    def apply_config(self, config):
        self.engine.config = config
        self.bit_order = "lsb" if config.bit_order == "lsb_first" else "msb"
        self.bits.set_config(config)
        self._primary_fmt_badge.set_config(config)
        self._text.refresh(config.language, keep_english_terms=config.keep_english_terms)
        if hasattr(self, "_bit_legend"):
            self._bit_legend.set_config(config)
        self.cfg_signed_combo.setItemText(0, self._text("Signed"))
        self.cfg_signed_combo.setItemText(1, self._text("Unsigned"))
        for base, copy_btn in self.row_copy_btns.items():
            tip = self._text("Copy {base}", base=base)
            copy_btn.setAccessibleName(tip)
            copy_btn.setToolTip(tip)
        self._drawer_height_sig = None
        self._sync_fxp_drawer_control_heights()
        self._apply_scale_metrics(config)
        self._rebuild_compare_rows()
        self._refresh_formats(refresh_icons=False)
        self._refresh_icons()
        self._update_shift_mode_buttons()
        self._keypad_dims_key = None
        self._hero_dims_key = None
        self._update_responsive_metrics()
        self.refresh()
        self._refresh_sidebars()
        self.limits_changed.emit()

    def _apply_scale_metrics(self, config):
        prev_scale = getattr(self, "_last_table_scale", config.ui_scale)
        self._last_table_scale = config.ui_scale
        if hasattr(self, "var_table"):
            self._programmatic_var_col_resize = True
            try:
                self.var_table.horizontalHeader().setMinimumSectionSize(scaled_px(44, config))
            finally:
                self._programmatic_var_col_resize = False
            if prev_scale != config.ui_scale:
                self._sync_var_table_columns(rescale=True)
        self.left_pane.setMinimumWidth(_min_scaled(320, config, cap_factor=1.35))
        capped = getattr(config, "simple_keypad_size_mode", "expand") == "capped"
        left_max_w = scaled_px(460, config) if capped else 16777215
        self.left_pane.setMaximumWidth(left_max_w)
        self.base_container.setMinimumWidth(_min_scaled(180, config, cap_factor=1.30))
        self.hero_frame.setMinimumWidth(_min_scaled(116, config, cap_factor=1.30))
        self.sidebar.setMinimumWidth(_min_scaled(210, config, cap_factor=1.24))
        self.sidebar.setMaximumWidth(16777215 if capped else scaled_px(820, config))
        if hasattr(self, "main_splitter"):
            self.main_splitter.setStretchFactor(0, 0 if capped else 1)
            self.main_splitter.setStretchFactor(1, 1 if capped else 0)
        self.main_splitter.setHandleWidth(scaled_px(5, config))
        self.top_splitter.setHandleWidth(scaled_px(5, config))
        if hasattr(self, "left_vsplitter"):
            self.left_vsplitter.setHandleWidth(scaled_px(6, config))
        self.format_combo.setMinimumWidth(_min_scaled(48, config, cap_factor=1.50))
        self.format_combo.setMaximumWidth(scaled_px(136, config))
        self._sync_base_label_widths(config)
        is_both = self._panel_mode == "both"
        main_h = _min_scaled(18 if is_both else 22, config, cap_factor=1.45)
        max_main_w = scaled_px(88, config) if capped else 16777215
        max_main_h = scaled_px(32, config) if capped else 16777215
        for btn in self._keypad_main_btns + self._keypad_op_btns:
            btn.setMinimumHeight(main_h)
            btn.setMaximumSize(max_main_w, max_main_h)
        aux_h = _min_scaled(16 if is_both else 19, config, cap_factor=1.45)
        max_aux_w = scaled_px(76, config) if capped else 16777215
        max_aux_h = scaled_px(25, config) if capped else 16777215
        for btn in self._keypad_aux_btns:
            btn.setMinimumHeight(aux_h)
            btn.setMaximumSize(max_aux_w, max_aux_h)
        shift_h = _min_scaled(15 if is_both else 18, config, cap_factor=1.45)
        max_shift_w = scaled_px(98, config) if capped else 16777215
        max_shift_h = scaled_px(22, config) if capped else 16777215
        for btn in self._shift_mode_btns.values():
            btn.setMinimumHeight(shift_h)
            btn.setMaximumSize(max_shift_w, max_shift_h)
        if hasattr(self, "_keypad_vbox"):
            self._keypad_vbox.setContentsMargins(6, 4, 6, 5)
            self._keypad_vbox.setSpacing(3)
            self._keypad_shift_row.setSpacing(3)
            self._keypad_bit_row.setSpacing(3)
            self._keypad_grid.setSpacing(3)
        if self._panel_mode == "both":
            self.bit_scroll.setMinimumHeight(_min_scaled(50, config, cap_factor=1.35))
            self.bit_scroll.setMaximumHeight(16777215)
        elif self._panel_mode == "bits":
            self.bit_scroll.setMinimumHeight(_min_scaled(86, config, cap_factor=1.35))
            self.bit_scroll.setMaximumHeight(16777215)
        else:
            self.bit_scroll.setMinimumHeight(_min_scaled(72, config, cap_factor=1.35))
            self.bit_scroll.setMaximumHeight(scaled_px(210, config))
        pad_max_w = left_max_w
        pad_max_h = scaled_px(268, config) if capped else 16777215
        if hasattr(self, "_keypad_inner"):
            self._keypad_inner.setMaximumSize(pad_max_w, pad_max_h)
        self.keypad.setMaximumHeight(pad_max_h)
        self._sync_lower_pane_bounds()
        self._sync_base_scroll_metrics()

    def _sync_base_label_widths(self, config):
        label_btns = [btn for btn, _ in self.rows.values()] + [
            row[4] for row in getattr(self, "_comparison_rows", ()) if len(row) > 4
        ]
        for btn in label_btns:
            btn.ensurePolished()
        lbl_w = max(
            _min_scaled(38, config, cap_factor=1.40),
            max((btn.sizeHint().width() for btn in label_btns), default=0),
        )
        for btn in label_btns:
            btn.setFixedWidth(lbl_w)
        return lbl_w

    def side_by_side_threshold(self) -> int:
        cfg = self.engine.config
        scale_factor = max(1.0, min(1.6, (cfg.ui_scale / 100.0) * (0.7 + 0.3 * cfg.ui_font_size / 10.0)))
        return round(530 * scale_factor)

    def mark_user_sidebar_toggle(self, visible: bool) -> None:
        if 0 < self.width() < self.side_by_side_threshold():
            self._user_toggled_sidebar_narrow = bool(visible)
            self._auto_collapsed_narrow = False
        else:
            self._user_toggled_sidebar_narrow = False
            self._auto_collapsed_narrow = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_metrics()

    def _update_responsive_metrics(self):
        w = self.width()
        narrow = 0 < w < self.side_by_side_threshold()
        if narrow:
            if (
                self.sidebar.isVisibleTo(self)
                and not self._user_toggled_sidebar_narrow
                and not self._auto_collapsed_narrow
            ):
                self._auto_collapsed_narrow = True
                self.sidebar.setVisible(False)
                self.sidebar_auto_toggled.emit(False)
                self.limits_changed.emit()
            if self.main_splitter.orientation() != Qt.Orientation.Vertical:
                sizes = self.main_splitter.sizes()
                if sum(sizes) > 0:
                    self._saved_main_h_sizes = sizes
                self.main_splitter.setOrientation(Qt.Orientation.Vertical)
        else:
            self._user_toggled_sidebar_narrow = False
            if self.main_splitter.orientation() != Qt.Orientation.Horizontal:
                self.main_splitter.setOrientation(Qt.Orientation.Horizontal)
                if self._saved_main_h_sizes and len(self._saved_main_h_sizes) == 2:
                    self.main_splitter.setSizes(self._saved_main_h_sizes)
            if self._auto_collapsed_narrow:
                self._auto_collapsed_narrow = False
                if not self.sidebar.isVisibleTo(self):
                    self.sidebar.setVisible(True)
                    self.sidebar_auto_toggled.emit(True)
                    self.limits_changed.emit()

        config = self.engine.config
        pad_box = self._keypad_inner if hasattr(self, "_keypad_inner") and self._keypad_inner.width() > 0 else self.keypad
        kw = pad_box.width() if hasattr(self, "keypad") and pad_box.width() > 0 else max(300, self.left_pane.width())
        kh = pad_box.height() if hasattr(self, "keypad") and pad_box.height() > 0 else 240
        hw = self.hero_frame.width() if hasattr(self, "hero_frame") and self.hero_frame.width() > 0 else 160
        size_mode = getattr(config, "simple_keypad_size_mode", "expand")
        base_code = config.code_font_size * 1.333 * (config.ui_scale / 100.0)
        base_ui = config.ui_font_size * 1.333 * (config.ui_scale / 100.0)

        keypad_dims_key = (kw, kh, config.ui_scale, config.code_font_size, config.ui_font_size, size_mode)
        if getattr(self, "_keypad_dims_key", None) != keypad_dims_key:
            self._keypad_dims_key = keypad_dims_key
            cell_w = max(36.0, kw / 5.2)
            cell_h = max(20.0, kh / 8.2)
            size_factor = min(cell_w / 72.0, cell_h / 34.0)
            if size_mode == "capped":
                clamped = max(0.85, min(1.06, size_factor))
                aux_scale = max(0.88, min(1.04, size_factor))
                shift_scale = max(0.88, min(1.02, size_factor))
                main_cap = max(18, round(base_code * 1.25))
                op_cap = max(16, round(base_code * 1.15))
                aux_cap = max(13, round(base_ui))
                shift_cap = max(12, round(base_ui * 0.95))
            else:
                clamped = max(0.85, min(1.85, size_factor))
                aux_scale = max(0.88, min(1.30, size_factor))
                shift_scale = max(0.88, min(1.22, size_factor))
                main_cap = max(32, round(base_code * 2.0))
                op_cap = max(28, round(base_code * 1.85))
                aux_cap = max(17, round(base_ui * 1.35))
                shift_cap = max(14, round(base_ui * 1.25))
            main_px = round(max(11, min(main_cap, base_code * 1.15 * clamped)))
            op_px = round(max(11, min(op_cap, base_code * 1.05 * clamped)))
            aux_px = round(max(10, min(aux_cap, base_ui * 0.90 * aux_scale)))
            shift_px = round(max(9, min(shift_cap, base_ui * 0.85 * shift_scale)))
            keypad_font_sig = (main_px, op_px, aux_px, shift_px)
            if getattr(self, "_keypad_font_sig", None) != keypad_font_sig:
                self._keypad_font_sig = keypad_font_sig
                self.keypad.setStyleSheet(
                    f'QPushButton[calcKey="digit"], QPushButton[calcRole="primary"] {{ font-size: {main_px}px; font-weight: 700; }}'
                    f'QPushButton[calcKey="hex"], QPushButton[calcKey="op"], QPushButton[calcKey="action"] {{ font-size: {op_px}px; font-weight: 600; }}'
                    f'QPushButton[calcKey="bitwise"] {{ font-size: {aux_px}px; font-weight: 600; }}'
                    f'QPushButton[calcRole="shiftModeBtn"] {{ font-size: {shift_px}px; font-weight: 600; }}'
                    f'QLabel#ShiftModeLabel {{ font-size: {shift_px}px; font-weight: 600; padding-right: 2px; }}'
                )
                self.shift_mode_lbl.ensurePolished()
                for btn in self._keypad_main_btns + self._keypad_op_btns + self._keypad_aux_btns + list(self._shift_mode_btns.values()):
                    btn.ensurePolished()

        hero_dims_key = (hw, config.ui_scale, config.code_font_size)
        if getattr(self, "_hero_dims_key", None) != hero_dims_key:
            self._hero_dims_key = hero_dims_key
            ui_scale = config.ui_scale / 100.0
            hero_factor = max(0.82, min(1.45, hw / (155.0 * ui_scale)))
            min_hero = round(18 * ui_scale)
            max_hero = round(34 * ui_scale)
            hero_px = round(max(min_hero, min(max_hero, base_code * 2.1 * hero_factor)))
            input_h = max(round(32 * ui_scale), round(hero_px * 1.35) + round(6 * ui_scale))
            hero_font_sig = (hero_px, input_h)
            if getattr(self, "_hero_font_sig", None) != hero_font_sig:
                self._hero_font_sig = hero_font_sig
                self.input.setStyleSheet(f"font-size: {hero_px}px; font-weight: 700;")
                self.input.ensurePolished()
                self.input.setFixedHeight(input_h)

    def _refresh_icons(self):
        palette = get_palette(self.engine.config.theme)
        sz = scaled_px(14, self.engine.config)
        btn_sz = max(24, scaled_px(24, self.engine.config))
        sm_sz = scaled_px(12, self.engine.config)
        sm_btn_sz = max(24, scaled_px(24, self.engine.config))

        self.copy_hero_btn.setProperty("copyFeedback", False)
        self.copy_hero_btn.setIcon(make_icon("copy", palette.text_secondary, sz))
        self.copy_hero_btn.setIconSize(QSize(sz, sz))
        self.copy_hero_btn.setFixedSize(btn_sz, btn_sz)
        self.copy_hero_btn.setToolTip(self._text("Copy current value (with 0x/0b/0o prefix; REAL/FXP copies digits only)"))
        self.copy_hero_btn.setAccessibleName(self._text("Copy current value"))
        set_role(self.copy_hero_btn, "iconButton")
        repolish(self.copy_hero_btn)

        self.primary_cfg_btn.setIcon(make_icon("edit", palette.text_secondary, sm_sz))
        self.primary_cfg_btn.setIconSize(QSize(sm_sz, sm_sz))
        self.primary_cfg_btn.setFixedSize(sm_btn_sz, sm_btn_sz)

        self.var_clr_btn.setIcon(make_icon("trash", palette.text_secondary, sm_sz))
        self.var_clr_btn.setIconSize(QSize(sm_sz, sm_sz))
        self.var_clr_btn.setFixedSize(btn_sz, btn_sz)
        set_role(self.var_clr_btn, "iconButton")

        for base, copy_btn in self.row_copy_btns.items():
            tip = self._text("Copy {base}", base=base)
            copy_btn.setProperty("copyFeedback", False)
            copy_btn.setIcon(make_icon("copy", palette.text_secondary, sm_sz))
            copy_btn.setIconSize(QSize(sm_sz, sm_sz))
            copy_btn.setFixedSize(sm_btn_sz, sm_btn_sz)
            copy_btn.setToolTip(tip)
            copy_btn.setAccessibleName(tip)
            set_role(copy_btn, "iconButton")
            repolish(copy_btn)

        icon_map = {
            "keypad": "keypad",
            "bits": "bits",
            "both": "split_both",
            "none": "chevron_up",
        }
        for mode, btn in self.panel_mode_btns.items():
            active = mode == self._panel_mode
            color = palette.primary_fg if active else palette.text_secondary
            btn.setIcon(make_icon(icon_map[mode], color, sz))
            btn.setIconSize(QSize(sz, sz))
            btn.setFixedSize(btn_sz, btn_sz)
            repolish(btn)

        _, active_name = self._active_fxp_target()
        has_hist = bool(active_name) and any(
            item["format"] == active_name
            for item in self.engine.config.simple_fxp_history
        )
        is_pinned = bool(active_name) and any(
            item["format"] == active_name and item["pinned"]
            for item in self.engine.config.simple_fxp_history
        )
        self.pin_fmt_btn.setEnabled(bool(active_name))
        self.pin_fmt_btn.setChecked(is_pinned)
        pin_color = palette.primary_fg if is_pinned else palette.text_secondary
        set_role(self.pin_fmt_btn, "segmentIconBtn" if is_pinned else "iconButton")
        self.pin_fmt_btn.setIcon(make_icon("pin", pin_color, sz))
        self.pin_fmt_btn.setIconSize(QSize(sz, sz))
        self.pin_fmt_btn.setFixedSize(btn_sz, btn_sz)
        repolish(self.pin_fmt_btn)

        target_idx = self.selected_fxp_idx if self.base == "FXP" else 0
        self.inline_cfg_btn.setChecked(self.fxp_drawer.isVisible() and self.cfg_target_idx == target_idx)
        self.del_fmt_btn.setEnabled(has_hist)

        for btn, icon_name in (
            (self.del_fmt_btn, "close"),
            (self.inline_cfg_btn, "edit"),
        ):
            set_role(btn, "iconButton")
            btn.setIcon(make_icon(icon_name, palette.text_secondary, sz))
            btn.setIconSize(QSize(sz, sz))
            btn.setFixedSize(btn_sz, btn_sz)
            repolish(btn)

    def set_sidebar_visible(self, visible: bool):
        self.sidebar.setVisible(visible)

    # ── Keyboard Routing & Calculator Actions ─────────────────────────

    def _is_other_editable_input(self, widget: QWidget | None) -> bool:
        if widget is None or widget is self.input:
            return False
        if isinstance(widget, QLineEdit):
            return not widget.isReadOnly()
        if isinstance(widget, QSpinBox):
            return True
        if isinstance(widget, QComboBox):
            return widget.isEditable()
        return False

    def _on_before_type_char(self, ch: str):
        if self._has_error():
            self._error_expr = ""
        if not self._replace_input or self.input.hasSelectedText():
            return
        if ch in "0123456789abcdefABCDEF.(~":
            self.input.clear()
        else:
            if ch in "+-" and self.input.text().strip() == "0":
                self.input.clear()
            else:
                self.input.end(False)
        self._replace_input = False

    def _is_in_var_selection_scope(self, obj: QWidget, event) -> bool:
        if hasattr(self, "var_action_bar") and (
            obj is self.var_action_bar or self.var_action_bar.isAncestorOf(obj)
        ):
            return True
        if obj is self.var_table or self.var_table.isAncestorOf(obj):
            vp = self.var_table.viewport()
            if obj is vp or vp.isAncestorOf(obj):
                pos = (
                    event.position().toPoint()
                    if obj is vp
                    else vp.mapFrom(obj, event.position().toPoint())
                )
                return self.var_table.indexAt(pos).isValid()
            return True
        return False

    def _register_drag_row(self, row_frame: BaseRowFrame, lbl_btn: QPushButton):
        self._drag_row_sources[row_frame] = row_frame
        self._drag_row_sources[lbl_btn] = row_frame

    def _row_group_and_key(self, row_frame: BaseRowFrame) -> tuple[str, str | int] | None:
        for base in BASES:
            if self.row_frames.get(base) is row_frame:
                return ("radix", base)
        if self.row_frames.get("FXP") is row_frame:
            return ("fxp", 0)
        for idx, row_tuple in enumerate(self._comparison_rows):
            if len(row_tuple) >= 3 and row_tuple[2] is row_frame:
                return ("fxp", idx + 1)
        return None

    def _frame_for_group_item(self, group: str, key: str | int) -> BaseRowFrame:
        if group == "radix":
            return self.row_frames[key]
        if key == 0:
            return self.row_frames["FXP"]
        return self._comparison_rows[int(key) - 1][2]

    def _ordered_base_row_frames(self) -> list[BaseRowFrame]:
        return (
            [self.row_frames[b] for b in self._radix_row_order if b in self.row_frames]
            + ([self.row_frames["REAL"]] if "REAL" in self.row_frames else [])
            + [
                self._frame_for_group_item("fxp", fid)
                for fid in self._fxp_row_order
                if fid == 0 or 1 <= int(fid) <= len(self._comparison_rows)
            ]
        )

    def _apply_base_row_order(self):
        if not hasattr(self, "base_layout"):
            return
        for pos, frame in enumerate(self._ordered_base_row_frames()):
            cur = self.base_layout.indexOf(frame)
            if cur != pos:
                if cur >= 0:
                    self.base_layout.takeAt(cur)
                self.base_layout.insertWidget(pos, frame)
        self.base_layout.activate()
        self._sync_base_scroll_metrics()

    def _finish_row_drag(self):
        state = getattr(self, "_row_drag_state", None)
        if state is None:
            return
        self._row_drag_state = None
        frame = state["frame"]
        if state["active"]:
            frame.setProperty("draggingRow", False)
            repolish(frame)

    def _reorder_dragged_row(self, row_frame: BaseRowFrame, global_pos):
        tag = self._row_group_and_key(row_frame)
        if tag is None:
            return
        group, key = tag
        order = self._radix_row_order if group == "radix" else self._fxp_row_order
        if key not in order or len(order) < 2:
            return
        vp = self.base_scroll.viewport()
        vp_y = vp.mapFromGlobal(global_pos).y()
        sb = self.base_scroll.verticalScrollBar()
        if sb.maximum() > 0:
            margin = max(8, scaled_px(12, self.engine.config))
            if vp_y < margin:
                sb.setValue(sb.value() - max(4, sb.singleStep()))
            elif vp_y > vp.height() - margin:
                sb.setValue(sb.value() + max(4, sb.singleStep()))
        host_y = self.base_host.mapFromGlobal(global_pos).y()
        idx = order.index(key)
        moved = False
        while idx > 0:
            prev_frame = self._frame_for_group_item(group, order[idx - 1])
            if host_y < prev_frame.geometry().center().y():
                order[idx], order[idx - 1] = order[idx - 1], order[idx]
                idx -= 1
                self._apply_base_row_order()
                moved = True
            else:
                break
        if not moved:
            while idx + 1 < len(order):
                next_frame = self._frame_for_group_item(group, order[idx + 1])
                if host_y > next_frame.geometry().center().y():
                    order[idx], order[idx + 1] = order[idx + 1], order[idx]
                    idx += 1
                    self._apply_base_row_order()
                else:
                    break

    def _handle_row_drag_event(self, obj, event, ev_type=None) -> bool:
        if ev_type is None:
            ev_type = event.type()
        if ev_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            row_frame = self._drag_row_sources.get(obj)
            if row_frame is not None and self._row_group_and_key(row_frame) is not None:
                self._finish_row_drag()
                self._row_drag_state = {
                    "source": obj,
                    "frame": row_frame,
                    "start_global": event.globalPosition().toPoint(),
                    "active": False,
                }
            return False

        state = self._row_drag_state
        if state is None:
            return False

        if ev_type in (QEvent.Type.WindowDeactivate, QEvent.Type.ApplicationDeactivate, QEvent.Type.Hide):
            self._finish_row_drag()
            return False

        if obj is not state["source"]:
            return False

        if ev_type == QEvent.Type.MouseMove:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._finish_row_drag()
                return False
            gpos = event.globalPosition().toPoint()
            if not state["active"]:
                dy = abs(gpos.y() - state["start_global"].y())
                if dy < max(6, QApplication.startDragDistance()):
                    return False
                state["active"] = True
                frame = state["frame"]
                frame.setProperty("draggingRow", True)
                repolish(frame)
                if isinstance(obj, QPushButton):
                    obj.setDown(False)
            self._reorder_dragged_row(state["frame"], gpos)
            return True

        if ev_type == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            was_active = bool(state["active"])
            self._finish_row_drag()
            return was_active

        return False

    def _is_protected_focus_widget(self, widget: QWidget | None) -> bool:
        if widget is None:
            return False
        if widget is not self and not self.isAncestorOf(widget):
            return True
        for container_name in ("fxp_drawer", "add_var_box", "sidebar"):
            container = getattr(self, container_name, None)
            if container is not None and (widget is container or container.isAncestorOf(widget)):
                return True
        return False

    def eventFilter(self, obj, event):
        if not self.isVisible():
            return False
        ev_type = event.type()
        if ev_type == QEvent.Type.Resize:
            if obj is getattr(self, "left_vsplitter", None):
                self._apply_pending_vsplit()
            elif obj in (self.keypad, getattr(self, "_keypad_inner", None), self.hero_frame):
                if (
                    obj is self.keypad
                    and self._panel_mode == "keypad"
                    and not self._restoring_panel_split
                    and self._pending_vsplit_mode is None
                    and not (hasattr(self, "fxp_drawer") and self.fxp_drawer.isVisible())
                    and self.keypad.height() > 0
                ):
                    self._saved_keypad_height = self.keypad.height()
                self._update_responsive_metrics()
            return False
        if self._row_drag_state is not None or (
            ev_type == QEvent.Type.MouseButtonPress and obj in self._drag_row_sources
        ):
            if self._handle_row_drag_event(obj, event, ev_type):
                return True
        if ev_type == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget):
            if (
                QApplication.activeModalWidget() is None
                and obj.window() is self.window()
                and hasattr(self, "var_table")
                and self._selected_var_name() is not None
                and not self._is_in_var_selection_scope(obj, event)
            ):
                self.var_table.clearSelection()
                self.var_table.setCurrentItem(None)
                self._update_var_action_buttons()
            return False
        if ev_type != QEvent.Type.KeyPress:
            return False
        if obj is self.input or not self.isEnabled():
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return super().eventFilter(obj, event)
        if not isinstance(obj, QWidget) or obj.window() is not self.window():
            return super().eventFilter(obj, event)

        key = event.key()
        text = event.text()
        mods = event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier & ~Qt.KeyboardModifier.KeypadModifier
        fw = QApplication.focusWidget()

        if not mods and key == Qt.Key.Key_Escape:
            if hasattr(self, "add_var_box") and self.add_var_box.isVisible():
                self._hide_add_var_box()
                return True
            if hasattr(self, "fxp_drawer") and self.fxp_drawer.isVisible():
                self._close_fxp_drawer()
                return True
            if self._is_other_editable_input(fw) or self._is_protected_focus_widget(fw):
                return super().eventFilter(obj, event)
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.clear()
            return True

        if self._is_other_editable_input(fw):
            return super().eventFilter(obj, event)

        if event.matches(QKeySequence.StandardKey.Copy) and isinstance(fw, QLineEdit) and fw.hasSelectedText():
            if fw is self.input:
                fw.copy()
                return True
            return super().eventFilter(obj, event)

        if self._is_protected_focus_widget(fw):
            return super().eventFilter(obj, event)

        if event.matches(QKeySequence.StandardKey.Paste):
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.paste(QGuiApplication.clipboard().text())
            return True
        if event.matches(QKeySequence.StandardKey.Copy):
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
            if self.input.hasSelectedText():
                self.input.copy()
            else:
                self._copy_hero_value()
            return True
        if not mods and (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or text == "="):
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.equals()
            return True
        if not mods and key == Qt.Key.Key_Delete:
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self._ce_or_ac()
            return True
        if not mods and key == Qt.Key.Key_Backspace:
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self._backspace_entry()
            return True
        if not mods and text and len(text) == 1 and text in "0123456789abcdefABCDEF.+-*/%&|^~()<>":
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
            QApplication.sendEvent(self.input, event)
            return True

        return super().eventFilter(obj, event)

    def _run(self, action):
        try:
            action()
        except (ValueError, InvalidOperation, OverflowError) as exc:
            self._error_key = None
            self._error_text = self._text(str(exc))
            self.refresh()
            return False
        self._set_error(None)
        return True

    def _has_error(self) -> bool:
        return bool(self._error_text or self._error_key)

    def _clear_expression(self):
        self._tokens.clear()
        self._operands.clear()

    def _mutate_input_text(self, fn):
        self._suppress_edit_started = True
        try:
            fn()
        finally:
            self._suppress_edit_started = False

    def _edit_started(self, *_):
        if self._suppress_edit_started:
            return
        self._replace_input = False
        self.engine.status_kind = "exact"
        self._set_error(None)
        self.refresh()

    def _append(self, text):
        had_error = self._has_error()
        self._set_error(None)
        self.engine.status_kind = "exact"
        if self._replace_input or had_error:
            self.input.clear()
            self._replace_input = False
        self._mutate_input_text(lambda: self.input.insert(text))
        self.refresh()
        self.input.setFocus()

    def _is_entry_clearable_only(self) -> bool:
        return (
            bool(self._tokens)
            and not self._has_error()
            and not self._replace_input
            and self.input.text().strip() not in ("", "0")
        )

    def _clear_entry(self):
        self.input.clear()
        self._set_error(None)
        self.engine.status_kind = "exact"
        self.refresh(reset_input=True)

    def _pop_last_token_or_clear(self):
        if self._tokens:
            tok, _ = self._tokens.pop()
            if tok.startswith("literal_"):
                self._operands.pop(tok, None)
            elif self._tokens and self._tokens[-1][0].startswith("literal_"):
                lit_key, lit_label = self._tokens.pop()
                self._operands.pop(lit_key, None)
                self.input.setText(lit_label)
                self._replace_input = False
                self.refresh()
                return
            self.refresh(reset_input=not self._tokens)
            return
        self._clear_entry()

    def _backspace_entry(self):
        if self._has_error():
            self._set_error(None)
            self.refresh(reset_input=True)
            self.input.setFocus()
            return
        if self._replace_input:
            self._pop_last_token_or_clear()
            self.input.setFocus()
            return
        if self.input.text():
            self._mutate_input_text(self.input.backspace)
            self.refresh()
        elif self._tokens:
            self._pop_last_token_or_clear()
        self.input.setFocus()

    def _toggle_sign(self):
        txt = self.input.text().strip()
        if txt.startswith("-"):
            self.input.setText(txt[1:])
        elif txt and txt != "0":
            self.input.setText("-" + txt)
        self._edit_started()

    def _press_not(self):
        self._bit_action(lambda: self.engine.set_raw(~self.engine.raw_uint))

    def _copy_hero_value(self):
        if (
            self.base == "FXP"
            and self.selected_fxp_idx > 0
            and self.selected_fxp_idx - 1 < len(self.engine.compare_specs)
        ):
            cmp_spec = self.engine.compare_specs[self.selected_fxp_idx - 1]
            text = display_fraction(self.engine.project(cmp_spec).quantized_value)
        else:
            text = self.engine.display(self.base)
        QGuiApplication.clipboard().setText(text)
        palette = get_palette(self.engine.config.theme)
        sz = scaled_px(15, self.engine.config)
        self.copy_hero_btn.setProperty("copyFeedback", True)
        self.copy_hero_btn.setIcon(make_icon("check", palette.success, sz))
        self.copy_hero_btn.setToolTip(self._text("Copied"))
        self.copy_hero_btn.setAccessibleName(self._text("Copied"))
        repolish(self.copy_hero_btn)
        self._hero_copy_timer.start()

    def _copy_row_value(self, base: str, edit: QLineEdit):
        QGuiApplication.clipboard().setText(edit.text())
        btn = self.row_copy_btns.get(base)
        if btn is None:
            return
        palette = get_palette(self.engine.config.theme)
        sm_sz = scaled_px(12, self.engine.config)
        btn.setProperty("copyFeedback", True)
        btn.setIcon(make_icon("check", palette.success, sm_sz))
        btn.setToolTip(self._text("Copied"))
        btn.setAccessibleName(self._text("Copied"))
        repolish(btn)
        timer = self._row_copy_timers.get(base)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(400)
            timer.timeout.connect(lambda b=base: self._reset_row_copy_feedback(b))
            self._row_copy_timers[base] = timer
        timer.start()

    def _reset_row_copy_feedback(self, base: str):
        btn = self.row_copy_btns.get(base)
        if btn is None:
            return
        palette = get_palette(self.engine.config.theme)
        sm_sz = scaled_px(12, self.engine.config)
        tip = self._text("Copy {base}", base=base)
        btn.setProperty("copyFeedback", False)
        btn.setIcon(make_icon("copy", palette.text_secondary, sm_sz))
        btn.setToolTip(tip)
        btn.setAccessibleName(tip)
        repolish(btn)

    def _is_shift_rhs_unscaled(self) -> bool:
        paren_inherited = [False]
        shift_active = [False]
        for token, _label in self._tokens:
            if token == "(":
                inherited = shift_active[-1]
                paren_inherited.append(inherited)
                shift_active.append(inherited)
            elif token == ")":
                if len(shift_active) > 1:
                    shift_active.pop()
                    paren_inherited.pop()
            elif token in ("<<", ">>"):
                shift_active[-1] = True
            elif token in ("&", "NAND", "^", "|", "NOR"):
                shift_active[-1] = paren_inherited[-1]
        return shift_active[-1]

    def _push_literal_token(self, text: str):
        key = f"literal_{len(self._operands)}"
        self._operands[key] = self.engine.parse_operand(text, self.base, unscaled=self._is_shift_rhs_unscaled())
        self._tokens.append((key, text))

    def _flush_operand(self):
        text = self.input.text().strip()
        if text:
            self._push_literal_token(text)
            self.input.clear()
            self._replace_input = False

    def _operator(self, op):
        had_error = self._has_error()
        self._set_error(None)
        if had_error and self._replace_input:
            self._clear_expression()
            self.input.setText(self.engine.display(self.base))
            self._replace_input = True

        if op == "(":
            if self._replace_input and not self._tokens:
                self.input.clear()
                self._replace_input = False
            elif self.input.text().strip():
                if not self._run(self._flush_operand):
                    return
                self._tokens.append(("*", "*"))
            elif self._tokens and (
                self._tokens[-1][0] == ")" or self._tokens[-1][0].startswith("literal_")
            ):
                self._tokens.append(("*", "*"))
            self._tokens.append(("(", "("))
            self.refresh()
            self.input.setFocus()
            return

        if op == ")":
            open_parens = sum(1 for k, _ in self._tokens if k == "(") - sum(
                1 for k, _ in self._tokens if k == ")"
            )
            if open_parens <= 0:
                self.input.setFocus()
                return
            if self.input.text().strip():
                if not self._run(self._flush_operand):
                    return
            elif not self._tokens or self._tokens[-1][0] in _BINARY_OPS or self._tokens[-1][0] == "(":
                self.input.setFocus()
                return
            self._tokens.append((")", ")"))
            self.refresh()
            self.input.setFocus()
            return

        if self.input.text().strip():
            if not self._run(self._flush_operand):
                return
            self._tokens.append((op, op))
            self.refresh()
            self.input.setFocus()
            return

        if not self._tokens:
            if op in ("+", "-"):
                self._append(op)
                return
            self._push_literal_token(self.engine.display(self.base))
            self._tokens.append((op, op))
            self.refresh()
            self.input.setFocus()
            return

        prev = self._tokens[-1][0]
        if prev == "(":
            if op in ("+", "-"):
                self._append(op)
            else:
                self.input.setFocus()
            return
        if prev in _BINARY_OPS:
            self._tokens[-1] = (op, op)
            self.refresh()
            self.input.setFocus()
            return
        self._tokens.append((op, op))
        self.refresh()
        self.input.setFocus()

    def equals(self):
        text = self.input.text()
        expr = " ".join([token for token, _ in self._tokens] + [text])
        operands = self._operands
        if text.strip():
            try:
                operand = self.engine.parse_operand(text, self.base, unscaled=self._is_shift_rhs_unscaled())
            except (ValueError, InvalidOperation):
                pass
            else:
                key = f"literal_{len(self._operands)}"
                expr = " ".join([token for token, _ in self._tokens] + [key])
                operands = {**self._operands, key: operand}
        caption = " ".join([label for _, label in self._tokens] + [text]).strip()
        if not caption:
            return
        if self._run(lambda: self.engine.evaluate(expr, self.base, operands=operands)):
            _, snapshot = self.engine.history[-1]
            self.engine.history[-1] = (caption, snapshot)
            self._clear_expression()
            self.refresh(reset_input=True)
            self._refresh_history()
        else:
            self._error_expr = caption
            self._clear_expression()
            self._replace_input = True
            self.refresh()

    def paste(self, text):
        self._error_expr = ""
        if self._run(lambda: self.engine.paste(text, self.base)):
            self._clear_expression()
            self.refresh(reset_input=True)

    def _ce_or_ac(self):
        if self._is_entry_clearable_only():
            self._clear_entry()
            return
        self.clear()

    def clear(self):
        self._set_error(None)
        self._clear_expression()
        self.engine.carry_bit = 0
        self._bit_action(lambda: self.engine.set_raw(0))

    def select_base(self, base: str, *, fxp_idx: int = 0):
        target_base = base if base in BASES or base in ("REAL", "FXP") else "FXP"
        had_error = self._has_error()
        self._set_error(None)
        text = self.input.text().strip()
        reset_input = True
        if self.input.isModified() and text and not (had_error and self._replace_input):
            if not self._tokens:
                if not self._run(lambda: self.engine.paste(text, self.base)):
                    return
            elif self._is_shift_rhs_unscaled():
                try:
                    shift_count = int(self.engine.parse_operand(text, self.base, unscaled=True))
                except (ValueError, InvalidOperation, OverflowError):
                    if not self._run(self._flush_operand):
                        self.refresh()
                        return
                else:
                    if target_base == "HEX":
                        converted = hex(shift_count)
                    elif target_base == "OCT":
                        converted = oct(shift_count)
                    elif target_base == "BIN":
                        converted = bin(shift_count)
                    else:
                        converted = str(shift_count)
                    self.input.setText(converted)
                    reset_input = False
            else:
                try:
                    temp = SimpleCalcEngine(self.engine.config)
                    temp.primary_spec = self.engine.primary_spec
                    temp.compare_specs = list(self.engine.compare_specs)
                    temp.paste(text, self.base)
                    if target_base == "FXP" and 0 < fxp_idx <= len(temp.compare_specs):
                        converted = display_fraction(temp.project(temp.compare_specs[fxp_idx - 1]).quantized_value)
                    else:
                        converted = temp.display(target_base)
                except (ValueError, InvalidOperation, OverflowError):
                    if not self._run(self._flush_operand):
                        self.refresh()
                        return
                else:
                    self.input.setText(converted)
                    reset_input = False
        self.base = target_base
        self.selected_fxp_idx = fxp_idx
        self._refresh_formats()
        self.refresh(reset_input=reset_input)
        self.input.setFocus()
        self.input.selectAll()

    def sync_layout_cache(self):
        """Invalidate nested panel layouts so minimumSizeHint stays in sync before window limits update."""
        for layout in (
            getattr(self, "_keypad_vbox", None),
            self.keypad.layout() if hasattr(self, "keypad") else None,
            self.bit_panel.layout() if hasattr(self, "bit_panel") else None,
            self.mid_bar.layout() if hasattr(self, "mid_bar") else None,
            getattr(self, "_lower_layout", None),
            self.base_host.layout() if hasattr(self, "base_host") else None,
            self.base_container.layout() if hasattr(self, "base_container") else None,
            self.hero_frame.layout() if hasattr(self, "hero_frame") else None,
            self.top_display_frame.layout() if hasattr(self, "top_display_frame") else None,
            getattr(self, "_main_layout", None),
        ):
            if layout is not None:
                layout.invalidate()
                layout.activate()
        for widget in (
            getattr(self, "mid_bar", None),
            getattr(self, "keypad", None),
            getattr(self, "bit_panel", None),
            getattr(self, "lower_split", None),
            getattr(self, "lower_pane", None),
            getattr(self, "base_host", None),
            getattr(self, "base_scroll", None),
            getattr(self, "base_container", None),
            getattr(self, "hero_frame", None),
            getattr(self, "top_splitter", None),
            getattr(self, "top_display_frame", None),
            getattr(self, "left_vsplitter", None),
        ):
            if widget is not None:
                widget.updateGeometry()

    def _lower_overhead_height(self) -> int:
        if hasattr(self, "mid_bar"):
            w = 0
            if hasattr(self, "lower_pane") and self.lower_pane.width() > 0:
                w = self.lower_pane.width()
            elif self.mid_bar.width() > 0:
                w = self.mid_bar.width()
            mid_h = self.mid_bar.heightForWidth(w) if w > 0 and self.mid_bar.hasHeightForWidth() else self.mid_bar.sizeHint().height()
        else:
            mid_h = scaled_px(32, self.engine.config)
        drawer_h = (
            self.fxp_drawer.sizeHint().height()
            if (hasattr(self, "fxp_drawer") and self.fxp_drawer.isVisible())
            else 0
        )
        return mid_h + drawer_h

    def _left_vsplitter_usable_height(self) -> int:
        sizes = self.left_vsplitter.sizes() if hasattr(self, "left_vsplitter") else []
        if len(sizes) == 2 and sum(sizes) > 0:
            return max(1, sum(sizes))
        cr_h = self.left_vsplitter.contentsRect().height()
        base_h = cr_h if cr_h > 0 else self.left_vsplitter.height()
        return max(1, base_h - self.left_vsplitter.handleWidth())

    def _top_min_height(self) -> int:
        return max(self.top_display_frame.minimumHeight(), self.top_display_frame.minimumSizeHint().height())

    def _target_both_keypad_height(self, usable_h: int) -> int:
        overhead_h = self._lower_overhead_height()
        top_min = self._top_min_height()
        bit_min = max(1, self.bit_panel.minimumSizeHint().height())
        pad_min = max(1, self.keypad.minimumSizeHint().height())
        pad_max = min(self.keypad.maximumHeight(), max(pad_min, usable_h - top_min - overhead_h - bit_min))
        want = self._saved_keypad_height
        if want is None or want <= 0:
            want = max(pad_min, (usable_h - overhead_h) // 2)
        return max(pad_min, min(want, pad_max))

    def _on_left_vsplitter_moved(self, _pos: int = 0, _index: int = 0):
        if self._restoring_panel_split:
            return
        sizes = self.left_vsplitter.sizes()
        if len(sizes) != 2:
            return
        overhead_h = self._lower_overhead_height()
        if self._panel_mode == "keypad":
            pad_h = sizes[1] - overhead_h
            if pad_h > 0:
                self._saved_keypad_height = max(self.keypad.minimumSizeHint().height(), pad_h)
            return
        if self._panel_mode == "bits":
            bit_h = sizes[1] - overhead_h
            if bit_h > 0:
                self._saved_bits_height = max(self.bit_panel.minimumSizeHint().height(), bit_h)
                pad_min = max(1, self.keypad.minimumSizeHint().height())
                pad_max = max(pad_min, self.keypad.maximumHeight())
                self._saved_keypad_height = max(pad_min, min(bit_h, pad_max))
            return
        if self._panel_mode == "both":
            usable_h = self._left_vsplitter_usable_height()
            bit_min = max(1, self.bit_panel.minimumSizeHint().height())
            target_pad = self._target_both_keypad_height(usable_h)
            min_lower = overhead_h + bit_min + target_pad
            lower_sizes = self.lower_split.sizes()
            if sizes[1] < min_lower:
                top_h = max(self._top_min_height(), usable_h - min_lower)
                lower_h = max(1, usable_h - top_h)
                self._restoring_panel_split = True
                try:
                    self.left_vsplitter.setSizes([top_h, lower_h])
                    self.lower_split.setSizes([max(bit_min, lower_h - overhead_h - target_pad), target_pad])
                finally:
                    self._restoring_panel_split = False
            elif len(lower_sizes) == 2 and lower_sizes[1] != target_pad:
                self._restoring_panel_split = True
                try:
                    self.lower_split.setSizes([max(bit_min, sizes[1] - overhead_h - target_pad), target_pad])
                finally:
                    self._restoring_panel_split = False

    def _set_panel(self, mode: str, *, refresh: bool = True):
        prev_mode = getattr(self, "_panel_mode", "keypad")
        prev_sizes = self.left_vsplitter.sizes() if hasattr(self, "left_vsplitter") else []
        prev_overhead = self._lower_overhead_height()
        has_open_split = (
            prev_mode != "none"
            and hasattr(self, "left_vsplitter")
            and self.left_vsplitter.height() >= scaled_px(180, self.engine.config)
            and len(prev_sizes) == 2
            and sum(prev_sizes) > 0
        )
        drawer_open = hasattr(self, "fxp_drawer") and self.fxp_drawer.isVisible()
        if (
            prev_mode == "keypad"
            and self.keypad.isVisible()
            and self.keypad.height() > 0
            and not drawer_open
        ):
            self._saved_keypad_height = self.keypad.height()
        elif prev_mode == "bits" and has_open_split:
            bit_h = prev_sizes[1] - prev_overhead
            if bit_h > 0:
                self._saved_bits_height = bit_h
        carry_from_open = (
            has_open_split
            and (
                (prev_mode in ("keypad", "both") and mode == "bits")
                or (prev_mode == "bits" and mode == "keypad")
            )
        )
        self._pending_carry_lower_h = prev_sizes[1] if carry_from_open else None
        self._restoring_panel_split = True
        try:
            self._panel_mode = mode
            self._pending_vsplit_mode = mode if mode in ("keypad", "bits", "both") else None
            if mode in self.panel_mode_btns:
                self.panel_mode_btns[mode].setChecked(True)
            show_bits = mode in ("bits", "both")
            show_pad = mode in ("keypad", "both")
            self.bit_panel.setVisible(show_bits)
            self.keypad.setVisible(show_pad)
            if hasattr(self, "lower_split"):
                self.lower_split.setVisible(show_bits or show_pad)
                self.lower_split.setStretchFactor(0, 1 if show_bits else 0)
                self.lower_split.setStretchFactor(1, 1 if mode == "keypad" else 0)
            self.keypad.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred if mode == "both" else QSizePolicy.Policy.Expanding,
            )
            self.lower_pane.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed if mode == "none" else QSizePolicy.Policy.Expanding,
            )
            self.top_display_frame.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Maximum if mode == "none" else QSizePolicy.Policy.Expanding,
            )
            self._main_layout.setStretch(0, 0 if mode == "none" else 1)
            self._main_layout.setStretch(self._collapse_stretch_idx, 1 if mode == "none" else 0)
            self.left_vsplitter.setStretchFactor(0, 1 if mode == "keypad" else 0)
            self.left_vsplitter.setStretchFactor(1, 1 if mode != "none" else 0)
            self.left_vsplitter.handle(1).setEnabled(mode != "none")
            self.bits.set_compact(mode == "both")
            self._apply_scale_metrics(self.engine.config)
            self._refresh_icons()
            if refresh:
                self.refresh()
            self.sync_layout_cache()
            self._sync_lower_pane_bounds()
            if prev_mode != "none":
                self._apply_pending_vsplit()
        finally:
            self._restoring_panel_split = False
        if refresh:
            self.limits_changed.emit()
        if prev_mode == "none" and mode in ("keypad", "bits", "both"):
            QTimer.singleShot(0, lambda expected=mode: self._reapply_panel_split(expected))

    def _reapply_panel_split(self, expected_mode: str):
        if self._panel_mode != expected_mode:
            return
        self._restoring_panel_split = True
        try:
            self._pending_vsplit_mode = expected_mode
            self._apply_pending_vsplit()
        finally:
            self._restoring_panel_split = False

    def _sync_lower_pane_bounds(self):
        if not hasattr(self, "lower_pane") or not hasattr(self, "left_vsplitter"):
            return
        config = self.engine.config
        mode = self._panel_mode
        capped = getattr(config, "simple_keypad_size_mode", "expand") == "capped"
        cap_lower = capped and mode == "keypad"
        self.left_vsplitter.setStretchFactor(0, 1 if mode == "keypad" else 0)
        self.left_vsplitter.setStretchFactor(1, 0 if (cap_lower or mode == "none") else 1)
        if not cap_lower:
            self.lower_pane.setMaximumHeight(16777215)
            return
        lower_max_h = self._lower_overhead_height() + scaled_px(268, config)
        self.lower_pane.setMaximumHeight(lower_max_h)
        avail_h = self.left_vsplitter.height()
        if avail_h >= scaled_px(180, config):
            usable_h = self._left_vsplitter_usable_height()
            sizes = self.left_vsplitter.sizes()
            if len(sizes) == 2 and sizes[1] > lower_max_h:
                target_lower = min(lower_max_h, max(1, usable_h - self._top_min_height()))
                self._restoring_panel_split = True
                try:
                    self.left_vsplitter.setSizes([max(1, usable_h - target_lower), target_lower])
                finally:
                    self._restoring_panel_split = False

    def _apply_pending_vsplit(self):
        mode = getattr(self, "_pending_vsplit_mode", None)
        if mode is None or not hasattr(self, "left_vsplitter"):
            return
        avail_h = self.left_vsplitter.height()
        if avail_h < scaled_px(180, self.engine.config):
            return
        cfg = self.engine.config
        usable_h = self._left_vsplitter_usable_height()
        overhead_h = self._lower_overhead_height()
        top_min = self._top_min_height()
        self._restoring_panel_split = True
        try:
            if mode == "keypad":
                min_pad = max(1, self.keypad.minimumSizeHint().height())
                max_pad = max(
                    min_pad,
                    min(self.keypad.maximumHeight(), usable_h - top_min - overhead_h),
                )
                carried_from_panel = self._pending_carry_lower_h is not None
                want_pad = (
                    (self._pending_carry_lower_h - overhead_h)
                    if carried_from_panel
                    else self._saved_keypad_height
                )
                if want_pad is not None:
                    target_pad = max(min_pad, min(want_pad, max_pad))
                    lower_h = min(self.lower_pane.maximumHeight(), overhead_h + target_pad)
                    top_h = max(top_min, usable_h - lower_h)
                    lower_h = max(1, usable_h - top_h)
                    if carried_from_panel and not (
                        hasattr(self, "fxp_drawer") and self.fxp_drawer.isVisible()
                    ):
                        self._saved_keypad_height = max(min_pad, lower_h - overhead_h)
                else:
                    top_h = max(top_min, (usable_h - overhead_h) // 2)
                    lower_h = min(max(1, usable_h - top_h), self.lower_pane.maximumHeight())
                    top_h = max(top_min, usable_h - lower_h)
                    lower_h = max(1, usable_h - top_h)
                    self._saved_keypad_height = max(1, lower_h - overhead_h)
                self.left_vsplitter.setSizes([top_h, lower_h])
            elif mode == "both":
                pad_h = self._target_both_keypad_height(usable_h)
                bit_min = max(1, self.bit_panel.minimumSizeHint().height())
                max_bit = max(bit_min, usable_h - top_min - overhead_h - pad_h)
                pref_bit = max(bit_min, scaled_px(148, cfg))
                bit_h = min(pref_bit, max_bit)
                lower_h = overhead_h + bit_h + pad_h
                top_h = max(top_min, usable_h - lower_h)
                lower_h = max(1, usable_h - top_h)
                self.left_vsplitter.setSizes([top_h, lower_h])
                self.lower_split.setSizes([max(bit_min, lower_h - overhead_h - pad_h), pad_h])
            elif mode == "bits":
                min_lower = overhead_h + max(1, self.bit_panel.minimumSizeHint().height())
                max_lower = max(min_lower, usable_h - top_min)
                carried_from_panel = self._pending_carry_lower_h is not None
                want_lower = self._pending_carry_lower_h
                if want_lower is None and self._saved_bits_height is not None:
                    want_lower = overhead_h + self._saved_bits_height
                if want_lower is None and self._saved_keypad_height is not None:
                    want_lower = overhead_h + self._saved_keypad_height
                if want_lower is None:
                    fxp_h = max(top_min, scaled_px(164, cfg))
                    want_lower = max(1, usable_h - fxp_h)
                lower_h = max(min_lower, min(want_lower, max_lower))
                top_h = max(top_min, usable_h - lower_h)
                lower_h = max(1, usable_h - top_h)
                if carried_from_panel or self._saved_bits_height is None:
                    self._saved_bits_height = max(1, lower_h - overhead_h)
                self.left_vsplitter.setSizes([top_h, lower_h])
        finally:
            self._restoring_panel_split = False
        self._pending_vsplit_mode = None
        self._pending_carry_lower_h = None

    def _reverse_bits(self):
        self.bit_order = "lsb" if self.bit_order == "msb" else "msb"
        self.engine.config.bit_order = f"{self.bit_order}_first"
        self.refresh()
        self.local_state_changed.emit()

    def _range(self):
        match = re.fullmatch(r"\[?\s*(\d{1,3})\s*:\s*(\d{1,3})\s*\]?", self.bit_range.text().strip())
        if not match:
            raise ValueError(self._text("Enter an inclusive bit range [high:low]"))
        return tuple(map(int, match.groups()))

    def read_field(self):
        if self._run(lambda: self.bit_value.setText(str(self.engine.read_field(*self._range())))):
            self.refresh()

    def write_field(self):
        self._bit_action(lambda: self.engine.write_field(*self._range(), int(self.bit_value.text())))

    def _bit_action(self, action):
        if self._run(action):
            self._clear_expression()
            self.refresh(reset_input=True)

    # ── FXP Format & Comparison Management ────────────────────────────

    def _active_fxp_target(self) -> tuple[FxpSpec | None, str]:
        if (
            self.base == "FXP"
            and self.selected_fxp_idx > 0
            and self.selected_fxp_idx - 1 < len(self.engine.compare_specs)
        ):
            spec = self.engine.compare_specs[self.selected_fxp_idx - 1]
            return spec, FormatEngine.canonical(spec)
        spec = self.engine.primary_spec
        return spec, (FormatEngine.canonical(spec) if spec is not None else "")

    def _record_fxp_history(self, spec: FxpSpec | None, *, update_primary: bool = True):
        config = self.engine.config
        name = FormatEngine.canonical(spec) if spec is not None else ""
        history = config.simple_fxp_history
        pinned = any(item["format"] == name and item["pinned"] for item in history)
        history[:] = [item for item in history if item["format"] != name]
        if name:
            history.insert(0, {"format": name, "pinned": pinned})
        recent = [item for item in history if not item["pinned"]][:6]
        history[:] = [item for item in history if item["pinned"]] + recent
        if update_primary:
            config.simple_last_fxp = name

    def _apply_primary_fmt_text(self, raw_text: str, *, emit_signal: bool = True, refresh_ui: bool = True) -> bool:
        text = "" if raw_text in ("Unset (Int32)", "未設定 (Int32)", "Int32") else str(raw_text).removeprefix("📌 ").strip()
        if not self._run(lambda: self.engine.set_primary(text or None)):
            return False
        self._record_fxp_history(self.engine.primary_spec, update_primary=True)
        self._clear_expression()
        if refresh_ui:
            self._refresh_formats()
            self.refresh(reset_input=True)
            self._refresh_var_table()
        if emit_signal:
            self.local_state_changed.emit()
        return True

    def _close_fxp_drawer(self):
        self.fxp_drawer.hide()
        self._sync_lower_pane_bounds()
        self._refresh_icons()
        self.limits_changed.emit()

    def _refresh_formats(self, *, refresh_icons: bool = True):
        config = self.engine.config
        ex_signed = FormatEngine.format(FxpSpec(True, 8, 4), config)
        ex_unsigned = FormatEngine.format(FxpSpec(False, 16, 8), config)
        is_cmp = self.base == "FXP" and 0 < self.selected_fxp_idx <= len(self.engine.compare_specs)
        target_label = f"FXP{self.selected_fxp_idx + 1}" if is_cmp else self._text("Primary FXP")
        self.format_combo.setToolTip(
            self._text(
                "Enter or choose {target} format (e.g. {ex1}, {ex2}), then press Enter",
                target=target_label,
                ex1=ex_signed,
                ex2=ex_unsigned,
            )
        )
        self.cfg_syntax_edit.setPlaceholderText(
            self._text("Format e.g. {ex1} / {ex2}", ex1=ex_signed, ex2=ex_unsigned)
        )
        if hasattr(self, "_primary_fmt_badge"):
            self._primary_fmt_badge.set_config(config)
            fmt_txt = FormatEngine.editable(self.engine.primary_spec, config) if self.engine.primary_spec is not None else ""
            self._primary_fmt_badge.set_format_text(fmt_txt, placeholder="Int32")

        active_spec, active_name = self._active_fxp_target()
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        for item in sorted(config.simple_fxp_history, key=lambda item: not item["pinned"]):
            disp = FormatEngine.editable(self.engine.parse_format(item["format"]), config)
            self.format_combo.addItem(("📌 " if item["pinned"] else "") + disp, item["format"])
        if active_spec is None:
            self.format_combo.setCurrentIndex(-1)
            self.format_combo.setEditText("")
        else:
            idx = self.format_combo.findData(active_name)
            self.format_combo.setCurrentIndex(idx if idx >= 0 else -1)
            self.format_combo.setEditText(FormatEngine.editable(active_spec, config))
        self.format_combo.lineEdit().setCursorPosition(0)
        self.format_combo.blockSignals(False)
        if refresh_icons:
            self._refresh_icons()

    def _format_entered(self, index=None):
        if isinstance(index, int) and index >= 0:
            data = self.format_combo.itemData(index)
            text = data if data is not None else self.format_combo.itemText(index)
        else:
            text = self.format_combo.currentText()
        text = "" if text in ("Unset (Int32)", "未設定 (Int32)", "Int32") else str(text).removeprefix("📌 ").strip()
        if self.base == "FXP" and 0 < self.selected_fxp_idx <= len(self.engine.compare_specs):
            cmp_idx = self.selected_fxp_idx - 1
            if self._run(lambda: self.engine.compare_specs.__setitem__(cmp_idx, self.engine.parse_format(text))):
                self._record_fxp_history(self.engine.compare_specs[cmp_idx], update_primary=False)
                self._refresh_formats()
                self.refresh(reset_input=True)
                self._refresh_var_table()
                self.local_state_changed.emit()
            return
        self._apply_primary_fmt_text(text)

    def _pin_format(self):
        active_spec, active_name = self._active_fxp_target()
        if not active_name or active_spec is None:
            self._refresh_icons()
            return
        history = self.engine.config.simple_fxp_history
        for item in history:
            if item["format"] == active_name:
                item["pinned"] = not item["pinned"]
                self._refresh_formats()
                self.local_state_changed.emit()
                return
        history.insert(0, {"format": active_name, "pinned": True})
        self._refresh_formats()
        self.local_state_changed.emit()

    def _remove_format(self):
        config = self.engine.config
        _, active_name = self._active_fxp_target()
        if not active_name:
            return
        prev_len = len(config.simple_fxp_history)
        config.simple_fxp_history[:] = [
            item for item in config.simple_fxp_history if item["format"] != active_name
        ]
        if len(config.simple_fxp_history) == prev_len:
            return
        self._refresh_formats()
        self.local_state_changed.emit()

    def _reset_primary_to_int32(self):
        switched_from_compare = self.base == "FXP" and self.selected_fxp_idx > 0
        if switched_from_compare:
            self.selected_fxp_idx = 0
        if self.engine.primary_spec is None:
            self._refresh_formats()
            self.refresh(reset_input=switched_from_compare)
            return
        if self._apply_primary_fmt_text("") and self.fxp_drawer.isVisible() and self.cfg_target_idx == 0:
            self._populate_fxp_drawer(0)

    def _populate_fxp_drawer(self, target_idx: int):
        spec = (
            self.engine.spec
            if target_idx == 0
            else self.engine.compare_specs[target_idx - 1]
        )
        self.cfg_target_lbl.setText(
            self._text("Target: {target}", target="FXP" if target_idx == 0 else f"FXP{target_idx + 1}")
        )
        self.cfg_syntax_edit.setText(
            "" if (target_idx == 0 and self.engine.primary_spec is None) else FormatEngine.editable(spec, self.engine.config)
        )
        self.cfg_signed_combo.setCurrentIndex(0 if spec.signed else 1)
        self.cfg_wl_spin.setValue(spec.wl)
        self.cfg_fwl_spin.setValue(spec.fwl)
        self.cfg_quant_combo.setCurrentText(self.engine.config.default_quant)
        self.cfg_ovf_combo.setCurrentText(self.engine.config.default_overflow)

    def _toggle_fxp_drawer(self, target_idx: int = 0):
        if self.fxp_drawer.isVisible() and self.cfg_target_idx == target_idx:
            self._close_fxp_drawer()
            return
        self.cfg_target_idx = target_idx
        self._populate_fxp_drawer(target_idx)
        self._sync_fxp_drawer_control_heights()
        self.fxp_drawer.show()
        self._sync_lower_pane_bounds()
        self._refresh_icons()
        self.limits_changed.emit()

    def _apply_fxp_drawer(self):
        syntax = self.cfg_syntax_edit.text().strip()
        if syntax:
            try:
                spec = self.engine.parse_format(syntax)
            except ValueError as exc:
                self._error_key = None
                self._error_expr = ""
                self._error_text = self._text(str(exc))
                self.refresh()
                return
        else:
            spec = FxpSpec(
                signed=bool(self.cfg_signed_combo.currentData()),
                wl=self.cfg_wl_spin.value(),
                fwl=self.cfg_fwl_spin.value(),
            )
        prev_config = self.engine.config
        self.engine.config = replace(
            self.engine.config,
            default_quant=self.cfg_quant_combo.currentData(),
            default_overflow=self.cfg_ovf_combo.currentData(),
        )
        if self.cfg_target_idx == 0:
            self.base = "FXP"
            self.selected_fxp_idx = 0
            if not self._apply_primary_fmt_text(
                FormatEngine.editable(spec, self.engine.config),
                emit_signal=False,
                refresh_ui=False,
            ):
                self.engine.config = prev_config
                return
        elif 0 < self.cfg_target_idx <= len(self.engine.compare_specs):
            idx = self.cfg_target_idx - 1
            self.engine.compare_specs[idx] = spec
            self._record_fxp_history(spec, update_primary=False)
        self.fxp_drawer.hide()
        self._sync_lower_pane_bounds()
        self._refresh_formats()
        self.refresh(reset_input=(self.cfg_target_idx == 0))
        self._refresh_var_table()
        self.local_state_changed.emit()
        self.limits_changed.emit()

    def _add_compare(self):
        default_spec = FxpSpec(True, 16, 12) if self.engine.spec == FxpSpec(True, 8, 4) else FxpSpec(True, 8, 4)
        self.engine.compare_specs.append(default_spec)
        self._fxp_row_order.append(len(self.engine.compare_specs))
        self._rebuild_compare_rows()
        self.refresh()
        self._refresh_var_table()
        self.limits_changed.emit()

    def _remove_compare(self, index: int):
        if 0 <= index < len(self.engine.compare_specs):
            self.engine.compare_specs.pop(index)
            target_id = index + 1
            self._fxp_row_order = [
                (fid - 1 if fid > target_id else fid)
                for fid in self._fxp_row_order
                if fid != target_id
            ]
            if self.selected_fxp_idx == index + 1:
                self.selected_fxp_idx = 0
                self.base = "FXP"
            elif self.selected_fxp_idx > index + 1:
                self.selected_fxp_idx -= 1
            self._rebuild_compare_rows()
            self._refresh_formats()
            self.refresh()
            self._refresh_var_table()
            self.limits_changed.emit()

    def _primary_rows_min_height(self) -> int:
        cfg = self.engine.config
        base_h = max(scaled_px(24, cfg), round(cfg.code_font_size * 1.333 * (cfg.ui_scale / 100.0)) + 10)
        ordered = self._ordered_base_row_frames()
        fxp_frame = self.row_frames.get("FXP")
        if fxp_frame is not None and fxp_frame in ordered:
            target_rows = ordered[: ordered.index(fxp_frame) + 1]
        else:
            target_rows = ordered
        rows_h = sum(max(base_h, f.sizeHint().height()) for f in target_rows)
        spacing = max(0, self.base_layout.spacing()) if hasattr(self, "base_layout") else 1
        return rows_h + max(0, len(target_rows) - 1) * spacing + 2

    def _sync_base_scroll_metrics(self):
        if not hasattr(self, "base_scroll"):
            return
        cfg = self.engine.config
        sample_row = self.row_frames.get("HEX")
        hint_h = sample_row.sizeHint().height() if sample_row is not None else 0
        row_h = max(hint_h, scaled_px(24, cfg), round(cfg.code_font_size * 1.333 * (cfg.ui_scale / 100.0)) + 10)
        total_rows = len(BASES) + 2 + len(self.engine.compare_specs)
        if self._panel_mode == "none":
            vis = min(total_rows, 7)
            h = vis * row_h + max(0, vis - 1) + 4
            self.base_scroll.setMinimumHeight(h)
            self.base_scroll.setMaximumHeight(h)
            self.top_display_frame.setMinimumHeight(0)
        elif self._panel_mode in ("bits", "both"):
            min_h = self._primary_rows_min_height()
            self.base_scroll.setMinimumHeight(min_h)
            self.base_scroll.setMaximumHeight(16777215)
            m = self.base_container.layout().contentsMargins()
            # Keep every row from top through primary FXP visible when Bit Select splitter drags up to max
            self.top_display_frame.setMinimumHeight(min_h + m.top() + m.bottom() + 1)
        else:
            min_vis = min(total_rows, 5)
            min_h = min_vis * row_h + max(0, min_vis - 1) + 4
            self.base_scroll.setMinimumHeight(min_h)
            self.base_scroll.setMaximumHeight(16777215)
            self.top_display_frame.setMinimumHeight(0)
        self.base_host.adjustSize()
        self.base_scroll.updateGeometry()

    def _rebuild_compare_rows(self):
        self._finish_row_drag()
        for row_tuple in self._comparison_rows:
            if len(row_tuple) >= 5:
                self._drag_row_sources.pop(row_tuple[2], None)
                self._drag_row_sources.pop(row_tuple[4], None)
        for w in self._compare_widgets:
            self.base_layout.removeWidget(w)
            w.hide()
            w.deleteLater()
        self._compare_widgets.clear()
        self._comparison_rows.clear()

        valid_ids = set(range(len(self.engine.compare_specs) + 1))
        self._fxp_row_order = [fid for fid in self._fxp_row_order if fid in valid_ids]
        for fid in range(len(self.engine.compare_specs) + 1):
            if fid not in self._fxp_row_order:
                self._fxp_row_order.append(fid)

        palette = get_palette(self.engine.config.theme)
        lbl_w = self._sync_base_label_widths(self.engine.config)
        sm_sz = scaled_px(12, self.engine.config)
        sm_btn_sz = max(24, scaled_px(24, self.engine.config))

        for index, spec in enumerate(self.engine.compare_specs):
            row_frame = BaseRowFrame(self.base_host)
            set_role(row_frame, "baseRow")
            row_frame.setCursor(Qt.CursorShape.PointingHandCursor)
            row_frame.clicked.connect(lambda i=index: self.select_base("FXP", fxp_idx=i + 1))
            row = QHBoxLayout(row_frame)
            row.setContentsMargins(3, 0, 4, 0)
            row.setSpacing(3)

            indicator = QFrame(row_frame)
            set_role(indicator, "baseIndicator")
            indicator.setFixedSize(4, 13)
            indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            row.addWidget(indicator)

            lbl_btn = QPushButton(f"FXP{index + 2}", row_frame)
            set_role(lbl_btn, "baseLabelBtn")
            lbl_btn.setCheckable(True)
            lbl_btn.setFixedWidth(lbl_w)
            lbl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl_btn.clicked.connect(lambda checked=False, i=index: self.select_base("FXP", fxp_idx=i + 1))
            row.addWidget(lbl_btn)

            value = ClickableBaseEdit(row_frame)
            set_role(value, "baseValueEdit", code=True)
            value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            value.setMinimumWidth(36)
            value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            value.setAccessibleName(self._text("FXP{index} value", index=index + 2))
            value.clicked.connect(lambda i=index: self.select_base("FXP", fxp_idx=i + 1))
            value.returnPressed.connect(lambda i=index, v=value: self._edit_compare(i, v.text()))
            row.addWidget(value, 1)

            right_box = QWidget(row_frame)
            set_role(right_box, "baseRowRight")
            right_box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            right_row = QHBoxLayout(right_box)
            right_row.setContentsMargins(0, 0, 0, 0)
            right_row.setSpacing(max(4, scaled_px(4, self.engine.config)))

            badge = FxpFormatBadge(right_box, placeholder="s8.4")
            badge.set_config(self.engine.config)
            badge.set_format_text(FormatEngine.editable(spec, self.engine.config))
            badge.edit.setAccessibleName(self._text("FXP{index} format", index=index + 2))
            badge.setToolTip(self._text("Double-click to edit FXP format"))
            badge.submitted.connect(lambda text, i=index: self._change_compare(i, text))
            right_row.addWidget(badge)

            cfg_btn = QToolButton(right_box)
            set_role(cfg_btn, "iconButton")
            cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cfg_btn.setIcon(make_icon("edit", palette.text_secondary, sm_sz))
            cfg_btn.setIconSize(QSize(sm_sz, sm_sz))
            cfg_btn.setFixedSize(sm_btn_sz, sm_btn_sz)
            cfg_btn.setAccessibleName(self._text("Configure FXP{index} format", index=index + 2))
            cfg_btn.setToolTip(self._text("Configure FXP{index} format", index=index + 2))
            cfg_btn.clicked.connect(lambda checked=False, i=index: self._toggle_fxp_drawer(i + 1))
            right_row.addWidget(cfg_btn)

            del_btn = QToolButton(right_box)
            set_role(del_btn, "iconButton")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setIcon(make_icon("close", palette.text_secondary, sm_sz))
            del_btn.setIconSize(QSize(sm_sz, sm_sz))
            del_btn.setFixedSize(sm_btn_sz, sm_btn_sz)
            del_btn.setAccessibleName(self._text("Remove FXP{index} row", index=index + 2))
            del_btn.setToolTip(self._text("Remove comparison row"))
            del_btn.clicked.connect(lambda checked=False, i=index: self._remove_compare(i))
            right_row.addWidget(del_btn)
            row.addWidget(right_box)

            self.base_layout.addWidget(row_frame)
            self._register_drag_row(row_frame, lbl_btn)
            self._compare_widgets.append(row_frame)
            self._comparison_rows.append((badge.edit, value, row_frame, indicator, lbl_btn, badge))

        self._sync_base_label_widths(self.engine.config)
        self._apply_base_row_order()
        if hasattr(self, "var_table"):
            self._sync_var_table_columns()

    def _change_compare(self, index, text):
        if self._run(lambda: self.engine.compare_specs.__setitem__(index, self.engine.parse_format(text))):
            self._record_fxp_history(self.engine.compare_specs[index], update_primary=False)
            if self.base == "FXP" and self.selected_fxp_idx == index + 1:
                self._refresh_formats()
            self.refresh()
            self._refresh_var_table()
            self.local_state_changed.emit()

    def _edit_compare(self, index, text):
        self._bit_action(
            lambda: self.engine.edit_projection(
                self.engine.compare_specs[index], self.engine.parse_operand(text, "REAL")
            )
        )

    # ── Sidebar Variable & History Actions ────────────────────────────

    def _set_error(self, key: str | None, **kwargs):
        if key is None:
            self._error_key = None
            self._error_text = ""
            self._error_expr = ""
        else:
            self._error_key = (key, kwargs)
            self._error_text = self._text(key, **kwargs)

    def _switch_side_tab(self, idx: int):
        self.side_stack.setCurrentIndex(idx)
        self.tab_vars_btn.setChecked(idx == 0)
        self.tab_hist_btn.setChecked(idx == 1)

    def _hide_add_var_box(self):
        self.add_var_box.hide()
        if hasattr(self, "add_var_error"):
            self.add_var_error.hide()

    def _toggle_add_var_box(self):
        vis = not self.add_var_box.isVisible()
        self.add_var_box.setVisible(vis)
        if vis:
            self.new_var_input.clear()
            if hasattr(self, "add_var_error"):
                self.add_var_error.hide()
            self.new_var_input.setFocus()
        elif hasattr(self, "add_var_error"):
            self.add_var_error.hide()

    def _confirm_add_var(self):
        name = self.new_var_input.text().strip()
        if self._rename_or_add(None, name):
            self._hide_add_var_box()
            self._select_var_row(name)

    def _rename_or_add(self, old: str | None, name: str) -> bool:
        if not name.isidentifier() or keyword.iskeyword(name) or (name != old and name in self.engine.variables):
            err = self._text("Variable name must be a valid, unique Python identifier")
            if old is None and hasattr(self, "add_var_error"):
                self.add_var_error.setText(err)
                self.add_var_error.show()
            else:
                self._set_error("Variable name must be a valid, unique Python identifier")
                self.refresh()
            return False
        if hasattr(self, "add_var_error"):
            self.add_var_error.hide()
        self._set_error(None)
        if old is None:
            self.engine.variables[name] = self._current_target_snapshot()
        elif old != name:
            self.engine.variables = {
                name if key == old else key: value for key, value in self.engine.variables.items()
            }
        self._refresh_var_table()
        self.refresh()
        return True

    def _current_target_snapshot(self):
        if (
            self.base == "FXP"
            and self.selected_fxp_idx > 0
            and self.selected_fxp_idx - 1 < len(self.engine.compare_specs)
        ):
            cmp_spec = self.engine.compare_specs[self.selected_fxp_idx - 1]
            res = self.engine.project(cmp_spec)
            return VariableSnapshot(cmp_spec, res.quantized_value, res.raw_uint, source_kind="FXP")
        return self.engine.snapshot(source_kind=self.base)

    def _store(self, name: str):
        self.engine.variables[name] = self._current_target_snapshot()
        self._refresh_var_table()

    def _recall(self, value, raw: bool):
        self._bit_action(lambda: self.engine.recall(value, raw=raw))

    def _confirm_add_fxp_for_recall(self, spec: FxpSpec) -> bool:
        from PySide6.QtWidgets import QMessageBox

        fmt_text = FormatEngine.format(spec, self.engine.config)
        reply = ask(
            self,
            self._text("Add FXP comparison"),
            self._text("No matching {fmt} row exists. Add it and load this variable?", fmt=fmt_text),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
            lang=self.engine.config.language,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _recall_variable(self, snapshot: VariableSnapshot):
        kind = getattr(snapshot, "source_kind", "FXP")
        if kind in BASES or kind == "REAL":
            self.base = kind
            self.selected_fxp_idx = 0
            self._bit_action(lambda: self.engine.recall(snapshot, raw=(kind != "REAL")))
            self._refresh_formats()
            self.input.setFocus()
            return

        if self.engine.primary_spec == snapshot.spec:
            self.base = "FXP"
            self.selected_fxp_idx = 0
            self._bit_action(lambda: self.engine.recall(snapshot, raw=False))
            self._refresh_formats()
            self.input.setFocus()
            return

        for idx, cmp_spec in enumerate(self.engine.compare_specs):
            if cmp_spec == snapshot.spec:
                self.base = "FXP"
                self.selected_fxp_idx = idx + 1
                self._bit_action(lambda s=cmp_spec: self.engine.edit_projection(s, snapshot.exact_fraction))
                self._refresh_formats()
                self.input.setFocus()
                return

        if self.engine.primary_spec is None:
            def _fill_unset():
                self.engine.set_primary(snapshot.spec)
                self.engine.recall(snapshot, raw=False)

            if self._run(_fill_unset):
                self._clear_expression()
                self.base = "FXP"
                self.selected_fxp_idx = 0
                self._record_fxp_history(snapshot.spec, update_primary=True)
                self._refresh_formats()
                self.refresh(reset_input=True)
                self._refresh_var_table()
                self.local_state_changed.emit()
                self.input.setFocus()
            return

        if not self._confirm_add_fxp_for_recall(snapshot.spec):
            return
        self.engine.compare_specs.append(snapshot.spec)
        self._rebuild_compare_rows()
        self.base = "FXP"
        self.selected_fxp_idx = len(self.engine.compare_specs)
        self._record_fxp_history(snapshot.spec, update_primary=False)
        self._bit_action(lambda: self.engine.edit_projection(snapshot.spec, snapshot.exact_fraction))
        self._refresh_formats()
        self._refresh_var_table()
        self.limits_changed.emit()
        self.local_state_changed.emit()
        self.input.setFocus()

    def _clear_or_delete_var(self, name: str):
        if name in ("M", "A", "B", "C", "D", "E", "F"):
            self.engine.variables[name] = None
        else:
            self.engine.variables.pop(name, None)
        self._refresh_var_table()

    def _selected_var_name(self) -> str | None:
        if not hasattr(self, "var_table"):
            return None
        sel_model = self.var_table.selectionModel()
        if sel_model is None:
            return None
        rows = sel_model.selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        item = self.var_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _select_var_row(self, name: str | None):
        if not hasattr(self, "var_table") or name is None:
            return
        for r in range(self.var_table.rowCount()):
            item = self.var_table.item(r, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == name:
                self.var_table.selectRow(r)
                return

    def _update_var_action_buttons(self):
        if not hasattr(self, "var_sto_btn"):
            return
        sel = self._selected_var_name()
        has_sel = sel is not None
        has_val = has_sel and (self.engine.variables.get(sel) is not None)
        is_preset = sel in ("M", "A", "B", "C", "D", "E", "F")
        self.var_sto_btn.setEnabled(has_sel)
        self.var_rcl_btn.setEnabled(has_val)
        self.var_clr_btn.setEnabled(has_sel and (not is_preset or has_val))

    def _on_var_sto_clicked(self):
        sel = self._selected_var_name()
        if sel is None:
            return
        self._store(sel)
        self._select_var_row(sel)

    def _on_var_rcl_clicked(self):
        sel = self._selected_var_name()
        if sel is None:
            return
        snapshot = self.engine.variables.get(sel)
        if snapshot is not None:
            self._recall_variable(snapshot)

    def _on_var_clr_clicked(self):
        sel = self._selected_var_name()
        if sel is None:
            return
        self._clear_or_delete_var(sel)

    def _on_var_cell_double_clicked(self, row: int, _col: int):
        item = self.var_table.item(row, 0)
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        snapshot = self.engine.variables.get(name)
        if snapshot is not None:
            self._recall_variable(snapshot)

    def _prompt_rename_var(self, name: str):
        if name == "M":
            return
        new_name, ok = prompt_text(
            self,
            self._text("Rename variable"),
            self._text("New variable name:"),
            default=name,
            lang=self.engine.config.language,
        )
        if ok and new_name.strip():
            self._rename_or_add(name, new_name.strip())

    def _rename_selected_var(self):
        sel = self._selected_var_name()
        if sel is not None:
            self._prompt_rename_var(sel)

    def _on_var_context_menu(self, pos: QPoint):
        item = self.var_table.itemAt(pos)
        if item is None:
            return
        self.var_table.selectRow(item.row())
        name = item.data(Qt.ItemDataRole.UserRole)
        snapshot = self.engine.variables.get(name)
        is_preset = name in ("M", "A", "B", "C", "D", "E", "F")

        menu = QMenu(self)
        act_sto = menu.addAction(self._text("Store here"))
        act_rcl = menu.addAction(self._text("Recall value")) if snapshot is not None else None
        act_rename = menu.addAction(self._text("Rename...")) if name != "M" else None
        act_clr = (
            menu.addAction(self._text("Clear value" if is_preset else "Delete variable"))
            if (snapshot is not None or not is_preset)
            else None
        )
        chosen = menu.exec(self.var_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_sto:
            self._store(name)
            self._select_var_row(name)
        elif chosen is act_rcl and snapshot is not None:
            self._recall_variable(snapshot)
        elif chosen is act_rename:
            self._prompt_rename_var(name)
        elif chosen is act_clr:
            self._clear_or_delete_var(name)

    def _expected_var_col_keys(self) -> list[str]:
        return [
            "var",
            "HEX",
            "DEC",
            "OCT",
            "BIN",
            "REAL",
            "FXP",
            *(f"FXP{i + 2}" for i in range(len(self.engine.compare_specs))),
        ]

    def _on_var_col_moved(self, _logical_idx: int, _old_visual: int, _new_visual: int):
        if self._programmatic_var_col_resize:
            return
        header = self.var_table.horizontalHeader()
        cols = getattr(self, "_var_table_cols", self._expected_var_col_keys())
        if header.count() == len(cols):
            self._var_col_order = [cols[header.logicalIndex(v)] for v in range(header.count())]

    def _on_var_col_resized(self, logical_idx: int, old_size: int, new_size: int):
        if self._programmatic_var_col_resize or old_size <= 0 or new_size <= 0 or old_size == new_size:
            return
        cols = getattr(self, "_var_table_cols", None)
        if cols and 0 <= logical_idx < len(cols):
            self._user_resized_widths[cols[logical_idx]] = new_size

    @staticmethod
    def _default_var_col_width(col_key: str, cfg) -> int:
        base_w = 50 if col_key == "var" else (112 if col_key == "BIN" else (86 if col_key in ("HEX", "OCT") else 74))
        return scaled_px(base_w, cfg)

    def _sync_var_table_columns(self, *, rescale: bool = False):
        if not hasattr(self, "var_table"):
            return
        cfg = self.engine.config
        expected = self._expected_var_col_keys()
        expected_set = set(expected)
        self._user_resized_widths = {k: v for k, v in self._user_resized_widths.items() if k in expected_set}
        visual_order = [k for k in self._var_col_order if k in expected]
        for k in expected:
            if k not in visual_order:
                visual_order.append(k)
        self._var_col_order = visual_order
        prev_cols = getattr(self, "_var_table_cols", None)
        prev_col_set = set(prev_cols or ())
        cols_changed = prev_cols != expected
        header = self.var_table.horizontalHeader()
        self._programmatic_var_col_resize = True
        try:
            if cols_changed:
                self._var_table_cols = list(expected)
                self.var_table.setColumnCount(len(expected))
                labels = [self._text("Var") if k == "var" else k for k in expected]
                self.var_table.setHorizontalHeaderLabels(labels)
                for idx in range(len(expected)):
                    header.setSectionResizeMode(idx, QHeaderView.ResizeMode.Interactive)
                for target_vis, col_key in enumerate(visual_order):
                    logical_idx = expected.index(col_key)
                    current_vis = header.visualIndex(logical_idx)
                    if current_vis != target_vis and current_vis >= 0:
                        header.moveSection(current_vis, target_vis)
            else:
                for idx, col_key in enumerate(expected):
                    h_item = self.var_table.horizontalHeaderItem(idx)
                    label = self._text("Var") if col_key == "var" else col_key
                    if h_item is not None and h_item.text() != label:
                        h_item.setText(label)

            for idx, col_key in enumerate(expected):
                if col_key in self._user_resized_widths:
                    target_w = self._user_resized_widths[col_key]
                    if cols_changed or self.var_table.columnWidth(idx) != target_w:
                        self.var_table.setColumnWidth(idx, target_w)
                elif col_key not in prev_col_set or rescale:
                    self.var_table.setColumnWidth(idx, self._default_var_col_width(col_key, cfg))
        finally:
            self._programmatic_var_col_resize = False

        for idx, col_key in enumerate(expected):
            h_item = self.var_table.horizontalHeaderItem(idx)
            if h_item is None:
                continue
            if col_key == "FXP":
                h_item.setToolTip(FormatEngine.format(self.engine.spec, cfg))
            elif col_key.startswith("FXP"):
                cmp_idx = int(col_key[3:]) - 2
                if 0 <= cmp_idx < len(self.engine.compare_specs):
                    h_item.setToolTip(FormatEngine.format(self.engine.compare_specs[cmp_idx], cfg))

    def _clear_history(self):
        self.engine.history.clear()
        self._refresh_history()

    def refresh(self, *, reset_input=False):
        if not hasattr(self, "side_stack"):
            return
        engine = self.engine
        cfg = engine.config

        # Hero metadata badge
        self.quant_rule_badge.setText(f"{cfg.default_quant} · {cfg.default_overflow}")

        expr_text = " ".join(label for _, label in self._tokens)
        if not expr_text and self._error_expr:
            expr_text = self._error_expr
        self.expression.setText(expr_text)
        err_msg = self._text(self._error_key[0], **self._error_key[1]) if self._error_key else self._error_text
        status_msg = err_msg or {
            "quantized": self._text("Quantized"),
            "saturated": self._text("Saturated"),
            "wrapped": self._text("Wrapped"),
        }.get(engine.status_kind, "")
        self.status.setText(status_msg)
        self.status.setVisible(bool(status_msg))
        if hasattr(self, "clear_entry_btn"):
            self.clear_entry_btn.setText("CE" if self._is_entry_clearable_only() else "C")

        has_alert = bool(status_msg)
        self.input.setProperty("calcAlert", has_alert)
        repolish(self.input)

        if reset_input:
            if not self._tokens:
                if self.base == "FXP" and self.selected_fxp_idx > 0 and self.selected_fxp_idx - 1 < len(engine.compare_specs):
                    cmp_spec = engine.compare_specs[self.selected_fxp_idx - 1]
                    self.input.setText(display_fraction(engine.project(cmp_spec).quantized_value))
                else:
                    self.input.setText(engine.display(self.base))
            else:
                self.input.setText("")
            self.input.deselect()
            self.input.setModified(False)
            self._replace_input = True

        # Base rows update (Windows Calculator style; no sub-tags)
        for base, (button, edit) in self.rows.items():
            is_active = (base == self.base) and (base != "FXP" or self.selected_fxp_idx == 0)
            button.setChecked(is_active)
            row_frame = self.row_frames[base]
            indicator = self.row_indicators[base]
            for widget in (row_frame, indicator, edit):
                if widget.property("activeBase") != is_active:
                    widget.setProperty("activeBase", is_active)
                    repolish(widget)
            row_alert = is_active and has_alert and base == "FXP"
            if edit.property("calcAlert") != row_alert:
                edit.setProperty("calcAlert", row_alert)
                repolish(edit)

            new_text = engine.display(base)
            if edit.text() != new_text:
                edit.setText(new_text)
                edit.setCursorPosition(0)

        primary_active = self.base == "FXP" and self.selected_fxp_idx == 0
        if hasattr(self, "_primary_fmt_badge"):
            self._primary_fmt_badge.set_active_base(primary_active)
            self._primary_fmt_badge.set_format_text(
                "" if engine.primary_spec is None else FormatEngine.editable(engine.primary_spec, cfg),
                placeholder="Int32",
            )

        # Comparison rows update
        for idx, (spec_cmp, row_tuple) in enumerate(zip(engine.compare_specs, self._comparison_rows)):
            fmt, edit = row_tuple[0], row_tuple[1]
            is_active = self.base == "FXP" and self.selected_fxp_idx == idx + 1
            if len(row_tuple) >= 5:
                row_frame, indicator, lbl_btn = row_tuple[2], row_tuple[3], row_tuple[4]
                lbl_btn.setChecked(is_active)
                for w in (row_frame, indicator, edit):
                    if w.property("activeBase") != is_active:
                        w.setProperty("activeBase", is_active)
                        repolish(w)
            if len(row_tuple) >= 6:
                badge = row_tuple[5]
                badge.set_active_base(is_active)
                badge.set_format_text(FormatEngine.editable(spec_cmp, cfg))
            elif not fmt.hasFocus():
                fmt.setText(FormatEngine.editable(spec_cmp, cfg))
            if not edit.hasFocus() or reset_input:
                cmp_text = display_fraction(engine.project(spec_cmp).quantized_value)
                if edit.text() != cmp_text or reset_input:
                    edit.setText(cmp_text)
                    edit.setCursorPosition(0)

        # Keypad enable/disable by radix
        radix = BASES.get(self.base, 10)
        for key, button in self._digit_buttons.items():
            button.setEnabled(self.base in ("REAL", "FXP") if key == "." else int(key, 16) < radix)

        # Defer heavy BitGrid widget construction/updates while hidden in keypad/none modes
        if self._panel_mode in ("bits", "both"):
            self.bits.render(build_fxp_bit_cells(engine.raw_uint, engine.spec, bit_order=self.bit_order))
        self._update_shift_mode_buttons()

    def _clear_layout(self, layout: QVBoxLayout):
        while layout.count() > 1:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _refresh_var_table(self):
        cfg = self.engine.config
        palette = get_palette(cfg.theme)
        selected_name = self._selected_var_name()
        self._sync_var_table_columns()
        cols = getattr(self, "_var_table_cols", self._expected_var_col_keys())
        items_list = list(self.engine.variables.items())

        code_px = max(10, round(cfg.code_font_size * 1.333 * (cfg.ui_scale / 100.0) * 0.9))
        empty_px = max(10, round(cfg.ui_font_size * 1.333 * (cfg.ui_scale / 100.0) * 0.92))
        self.var_table.verticalHeader().setDefaultSectionSize(max(scaled_px(22, cfg), code_px + 10, empty_px + 10))
        code_font = QFont()
        code_font.setFamilies([
            code_font_family(cfg.code_font_family),
            "Cascadia Mono",
            "Consolas",
            "Microsoft JhengHei UI",
        ])
        code_font.setPixelSize(code_px)
        code_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        name_font = QFont(code_font)
        name_font.setWeight(QFont.Weight.DemiBold)
        ui_empty_font = QFont()
        ui_empty_font.setFamilies([
            cfg.ui_font_family,
            "Segoe UI",
            "Microsoft JhengHei UI",
            "Microsoft JhengHei",
            "Noto Sans CJK TC",
        ])
        ui_empty_font.setPixelSize(empty_px)
        ui_empty_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        empty_brush = QColor(palette.text_muted)

        self.var_table.blockSignals(True)
        self.var_table.setRowCount(len(items_list))
        for row_idx, (name, snapshot) in enumerate(items_list):
            if snapshot is None:
                tip = f'{self._text("Empty")} — {self._text("Store current value into the selected variable")}'
                cells = {k: "-" for k in cols}
                cells["var"] = name
            else:
                view = SimpleCalcEngine(cfg)
                view.primary_spec = snapshot.spec
                view.set_raw(snapshot.raw_uint)
                source_kind = getattr(snapshot, "source_kind", "FXP")
                src_label = source_kind if source_kind in (*BASES, "REAL") else FormatEngine.format(snapshot.spec, cfg)
                real_disp = display_fraction(snapshot.exact_fraction)
                stored_fxp_disp = display_fraction(view.real)
                tip = self._text(
                    "Source: {src} · REAL: {real} · FXP ({fmt}): {fxp}",
                    src=src_label,
                    real=real_disp,
                    fmt=FormatEngine.format(snapshot.spec, cfg),
                    fxp=stored_fxp_disp,
                )
                proj_engine = SimpleCalcEngine(cfg)
                proj_engine.primary_spec = self.engine.primary_spec
                proj_engine.exact = snapshot.exact_fraction
                fxp_primary = display_fraction(proj_engine.project(self.engine.spec).quantized_value)
                cells = {
                    "var": name,
                    "HEX": view.display("HEX"),
                    "DEC": view.display("DEC"),
                    "OCT": view.display("OCT"),
                    "BIN": view.display("BIN"),
                    "REAL": real_disp,
                    "FXP": fxp_primary,
                }
                for cmp_idx, cmp_spec in enumerate(self.engine.compare_specs):
                    cells[f"FXP{cmp_idx + 2}"] = display_fraction(
                        proj_engine.project(cmp_spec).quantized_value
                    )

            for col_idx, col_key in enumerate(cols):
                item = QTableWidgetItem(cells.get(col_key, ""))
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setToolTip(tip)
                if col_key == "var":
                    item.setFont(name_font)
                    if name == "M":
                        item.setForeground(QColor(palette.accent))
                elif snapshot is None:
                    item.setFont(ui_empty_font)
                    item.setForeground(empty_brush)
                else:
                    item.setFont(code_font)
                self.var_table.setItem(row_idx, col_idx, item)
        self.var_table.blockSignals(False)

        if selected_name is not None:
            self._select_var_row(selected_name)
        self._update_var_action_buttons()

    def _refresh_history(self):
        cfg = self.engine.config
        self._clear_layout(self.hist_cards_layout)
        if not self.engine.history:
            empty_h = QLabel(self._text("No history yet"), self.hist_cards_host)
            set_role(empty_h, "muted")
            self.hist_cards_layout.insertWidget(0, empty_h)
        else:
            for expr, snapshot in reversed(self.engine.history):
                view = SimpleCalcEngine(cfg)
                view.primary_spec = snapshot.spec
                view.set_raw(snapshot.raw_uint)
                val_str = f"{display_fraction(snapshot.exact_fraction)} ({view.display('HEX')})"
                card_text = f"{expr}\n= {val_str}"
                h_btn = QPushButton(card_text, self.hist_cards_host)
                h_btn.setToolTip(card_text)
                h_btn.setAccessibleName(card_text)
                set_role(h_btn, "histCard", code=True)
                h_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                h_btn.clicked.connect(lambda checked=False, s=snapshot: self._recall(s, False))
                self.hist_cards_layout.insertWidget(self.hist_cards_layout.count() - 1, h_btn)

    def _refresh_sidebars(self):
        self._refresh_var_table()
        self._refresh_history()
