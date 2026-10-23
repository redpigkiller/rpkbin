"""Variable Inspector with representation cards and responsive hardware bit controls."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..controller import CalcController
from ..core.format_engine import FormatEngine
from ..core.fxp_model import BitCellInfo, InspectorSnapshot, PlainValueSnapshot
from .i18n import UiText, prompt_text, tr
from .icons import make_icon, style_icon_button
from .theme import get_palette, repolish, scaled_px, set_role


class _InspectorTitleButton(QToolButton):
    """Keep the preferred title width while allowing its header to wrap rows."""

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setWidth(0)
        return size


class _ValueFieldCard(QFrame):
    """Single representation card (Real Value, Hex, 2's Comp, Unsigned, 1's Comp) with optional Pin and Copy buttons."""

    def __init__(
        self,
        field_id: str,
        title: str,
        controller: CalcController,
        *,
        pin_key: str | None = None,
        read_only: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.field_id = field_id
        self.pin_key = pin_key
        self.controller = controller
        self._text = UiText(controller.config.language, keep_english_terms=controller.config.keep_english_terms)
        self._error_source = None
        self.palette = get_palette(controller.config.theme)
        self._last_committed_text: str = ""
        self._target = None
        self._failed_target = None
        self._force_refresh = False

        set_role(self, "valueCard")

        v_layout = QVBoxLayout(self)
        v_layout.setContentsMargins(8, 5, 8, 6)
        v_layout.setSpacing(3)

        # Top row: Label + optional Pin icon + Copy icon
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)

        self.title_lbl = QLabel(title, self)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        set_role(self.title_lbl, "cardTitle")
        top_row.addWidget(self.title_lbl, 1)

        self.pin_btn: QToolButton | None = None
        if pin_key is not None:
            self.pin_btn = QToolButton(self)
            self.pin_btn.setCheckable(True)
            self.pin_btn.setFixedSize(22, 20)
            tip_key = "pin_real_tooltip" if pin_key == "real" else "pin_hex_tooltip"
            self.pin_btn.setToolTip(tr(tip_key, controller.config.language, keep_english_terms=self.controller.config.keep_english_terms))
            self.pin_btn.clicked.connect(
                lambda _checked, pk=pin_key: self.controller.set_pin_mode(pk)
            )
            top_row.addWidget(self.pin_btn)

        self.copy_btn = QToolButton(self)
        self.copy_btn.setFixedSize(22, 20)
        self.copy_btn.setIcon(make_icon("copy", self.palette.text_secondary, 13))
        self.copy_btn.setToolTip(tr("copy_tooltip", controller.config.language, keep_english_terms=self.controller.config.keep_english_terms))
        self.copy_btn.clicked.connect(self._copy_value)
        top_row.addWidget(self.copy_btn)

        v_layout.addLayout(top_row)

        # Value input
        self.edit = QLineEdit(self)
        self.edit.setMinimumWidth(0)
        self.edit.setReadOnly(read_only)
        if not read_only:
            self.edit.editingFinished.connect(self._on_edit_finished)
        v_layout.addWidget(self.edit)
        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        set_role(self.error_label, "error")
        self.error_label.hide()
        v_layout.addWidget(self.error_label)
        controller.edit_finished.connect(self._edit_result)

        self.update_pin_visual(controller.config.inspector_pin_mode)

    def update_pin_visual(self, active_pin: str) -> None:
        if self.pin_btn is None or self.pin_key is None:
            return
        is_pinned = active_pin == self.pin_key and not self.property("nativeValue")
        self.pin_btn.setChecked(is_pinned)
        color = self.palette.accent if is_pinned else self.palette.text_muted
        self.pin_btn.setIcon(make_icon("pin", color, scaled_px(16, self.controller.config)))
        self.setProperty("pinned", is_pinned)
        repolish(self)

    def set_value_text(self, text: str) -> None:
        self._last_committed_text = text
        if self._failed_target != self._target:
            self._failed_target = None
            self.error_label.hide()
        if self._force_refresh or (not self.edit.hasFocus() and not self.edit.isModified() and self._failed_target is None):
            self.edit.setText(text)
            self._force_refresh = False

    def _edit_result(self, args, error):
        name, coord, field, text = args
        target = (name, tuple(coord) if coord is not None else None)
        if target != self._target or field != self.field_id:
            return
        self._failed_target = target if error else None
        self._error_source = ("Edit failed: {error}", {"error": error}) if error else None
        self.error_label.setText(self._text("Edit failed: {error}", error=error) if error else "")
        self.error_label.setVisible(bool(error))
        if error:
            self.edit.setText(text)

    def _on_edit_finished(self) -> None:
        if self.edit.isReadOnly():
            return
        if not self.edit.isModified() and self._failed_target is None:
            self.edit.setText(self._last_committed_text)
            return
        new_text = self.edit.text().strip()
        if not new_text:
            self._error_source = ("Enter a value", {})
            self.error_label.setText(self._text("Enter a value"))
            self.error_label.show()
            return
        if new_text == self._last_committed_text:
            self._failed_target = None
            self.error_label.hide()
            return
        if self._target is None:
            return
        name, coord = self._target
        ok = self.controller.edit_target_field(
            name,
            coord,
            self.field_id,
            new_text,
        )
        self.edit.setModified(False)
        if not ok:
            self.edit.setText(self._last_committed_text)

    def _copy_value(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(self.edit.text())

    def retranslate_ui(self, cfg, title):
        self._text.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        self.title_lbl.setText(title)
        self.edit.setAccessibleName(title)
        self.copy_btn.setToolTip(self._text("Copy {title}", title=title))
        self.copy_btn.setText(self.copy_btn.toolTip())
        self.copy_btn.setAccessibleName(self.copy_btn.toolTip())
        if self.pin_btn:
            key = "pin_real_tooltip" if self.pin_key == "real" else "pin_hex_tooltip"
            self.pin_btn.setToolTip(tr(key, cfg.language, keep_english_terms=self.controller.config.keep_english_terms))
            self.pin_btn.setText(self.pin_btn.toolTip())
            self.pin_btn.setAccessibleName(self.pin_btn.toolTip())
        if self._error_source:
            source, values = self._error_source
            self.error_label.setText(self._text(source, **values))


class VariableInspectorWidget(QFrame):
    """Bottom-right Variable Inspector with List/Grid layout, Real/Hex Pin icons, and Bit Strip."""
    collapsed_changed = Signal(bool)

    def __init__(
        self,
        controller: CalcController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PanelFrame")
        self.controller = controller
        self._text = UiText(controller.config.language, keep_english_terms=controller.config.keep_english_terms)
        self._error_source = None
        self.palette = get_palette(controller.config.theme)
        self._last_snapshot: InspectorSnapshot | PlainValueSnapshot | None = None
        self._format_failure = None
        self._bit_cells = {}
        self._bit_data = []
        self._bit_layout_key = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 1. Panel Header Bar ───────────────────────────────────────────
        header = self.header_widget = QFrame(self)
        header.setObjectName("PanelHeader")
        h_layout = self._header_layout = QGridLayout(header)
        self._header_stacked = None
        h_layout.setContentsMargins(10, 5, 8, 5)
        h_layout.setSpacing(6)

        self.title_lbl = _InspectorTitleButton(header)
        self.title_lbl.setText(tr("inspector", controller.config.language, keep_english_terms=self.controller.config.keep_english_terms))
        self.title_lbl.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.title_lbl.setCheckable(True)
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.title_lbl.clicked.connect(lambda: self.set_collapsed(not self.title_lbl.isChecked()))
        h_layout.addWidget(self.title_lbl, 0, 0)

        self.target_badge = QLabel("", header)
        set_role(self.target_badge, "target", code=True)
        self.target_badge.setMinimumWidth(0)
        self.target_badge.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        h_layout.addWidget(self.target_badge, 0, 1)

        self.status_badge = QLabel("", header)
        self.status_badge.setWordWrap(True)
        self.status_badge.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_role(self.status_badge, "status")

        # List View vs Grid View monochrome icon buttons
        self.list_mode_btn = QToolButton(header)
        self.list_mode_btn.setCheckable(True)
        self.list_mode_btn.setIcon(make_icon("list_view", self.palette.text_secondary))
        self.list_mode_btn.setToolTip(
            tr("list_view_tooltip", controller.config.language, keep_english_terms=self.controller.config.keep_english_terms)
        )
        self.list_mode_btn.clicked.connect(
            lambda: self.controller.set_inspector_view_mode("list")
        )
        h_layout.addWidget(self.list_mode_btn, 0, 2)

        self.grid_mode_btn = QToolButton(header)
        self.grid_mode_btn.setCheckable(True)
        self.grid_mode_btn.setIcon(make_icon("grid_view", self.palette.text_secondary))
        self.grid_mode_btn.setToolTip(
            tr("grid_view_tooltip", controller.config.language, keep_english_terms=self.controller.config.keep_english_terms)
        )
        self.grid_mode_btn.clicked.connect(
            lambda: self.controller.set_inspector_view_mode("grid")
        )
        h_layout.addWidget(self.grid_mode_btn, 0, 3)

        main_layout.addWidget(header)

        # ── 2. Body Container (Scrollable if window is small) ─────────────
        body_scroll = self.body_scroll = QScrollArea(self)
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.body_widget = QWidget(body_scroll)
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(8)
        body_layout.addWidget(self.status_badge)

        # Format Bar
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(6)
        fmt_lbl = self.fmt_lbl = QLabel("Format:")
        set_role(fmt_lbl, "secondary")
        fmt_row.addWidget(fmt_lbl)

        self.fmt_edit = QLineEdit(self.body_widget)
        self.fmt_edit.setMinimumWidth(0)
        self.fmt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.fmt_edit.setToolTip(
            "FXP format changes apply to the entire variable or array.\n"
            "Qm.n includes sign in m; total bits = m + n (e.g. Q4.12). MATLAB short notation is also accepted.\n"
            "Custom display templates do not change format input syntax.\n"
            "Pin chooses whether to preserve numeric values or raw bits. For native values, use Convert to FXP or APyFixed.from_float()."
        )
        self.fmt_edit.returnPressed.connect(self._on_format_committed)
        self.fmt_edit.setPlaceholderText("Format; Enter to apply")
        fmt_row.addWidget(self.fmt_edit)
        self.preview_btn = QToolButton(self.body_widget)
        self.preview_btn.setText("Preview")
        self.preview_btn.clicked.connect(lambda: self.controller.request_preview(
            self._last_snapshot.var_name, self.fmt_edit.text()) if self._last_snapshot else None)
        fmt_row.addWidget(self.preview_btn)

        self.meta_info_lbl = QLabel("", self.body_widget)
        set_role(self.meta_info_lbl, "muted", code=True)
        self.meta_info_lbl.setWordWrap(True)
        self.meta_info_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        fmt_row.addStretch(1)
        body_layout.addLayout(fmt_row)
        body_layout.addWidget(self.meta_info_lbl)

        # Inline error label (hidden when no error)
        self.error_lbl = QLabel("", self.body_widget)
        set_role(self.error_lbl, "error")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setVisible(False)
        body_layout.addWidget(self.error_lbl)

        # 5 Value Cards (arranged in List or 2-column Grid)
        lang = controller.config.language
        self.card_real = _ValueFieldCard(
            "real", tr("lbl_real_value", lang, keep_english_terms=self.controller.config.keep_english_terms), controller, pin_key="real"
        )
        self.card_hex = _ValueFieldCard(
            "hex", tr("lbl_hex_value", lang, keep_english_terms=self.controller.config.keep_english_terms), controller, pin_key="hex"
        )
        self.card_s2 = _ValueFieldCard(
            "s2", tr("lbl_s2_value", lang, keep_english_terms=self.controller.config.keep_english_terms), controller
        )
        self.card_uint = _ValueFieldCard(
            "uint", tr("lbl_uint_value", lang, keep_english_terms=self.controller.config.keep_english_terms), controller
        )
        self.card_c1 = _ValueFieldCard(
            "c1", tr("lbl_c1_value", lang, keep_english_terms=self.controller.config.keep_english_terms), controller, read_only=True
        )
        self._cards = [
            self.card_real,
            self.card_hex,
            self.card_s2,
            self.card_uint,
            self.card_c1,
        ]
        self.card_bin = _ValueFieldCard("bin", "Binary", controller, read_only=True)
        self._cards.append(self.card_bin)

        self.cards_container = QWidget(self.body_widget)
        self.cards_grid = QGridLayout(self.cards_container)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(6)
        body_layout.addWidget(self.cards_container)
        self.native_bits_label = QLabel(self.body_widget)
        self.native_bits_label.setWordWrap(True)
        self.native_bits_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.native_bits_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_layout.addWidget(self.native_bits_label)

        # ── 3. Hardware bits, grouped in fours at the available width ────
        bit_header = QHBoxLayout()
        bit_header.setSpacing(6)
        self.bit_title_lbl = QLabel("Bits (MSB → LSB)", self.body_widget)
        self.bit_title_lbl.setWordWrap(True)
        self.bit_title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_role(self.bit_title_lbl, "secondary")
        bit_header.addWidget(self.bit_title_lbl, 1)

        legend_lbl = self.legend_lbl = QLabel(self.body_widget)
        legend_lbl.setTextFormat(Qt.TextFormat.RichText)
        legend_lbl.setWordWrap(True)
        legend_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body_layout.addLayout(bit_header)
        body_layout.addWidget(legend_lbl)
        self.bit_actions_host = QWidget(self.body_widget)
        bit_actions = self.bit_actions_layout = QGridLayout(self.bit_actions_host)
        bit_actions.setContentsMargins(0, 0, 0, 0)
        bit_actions.setSpacing(6)

        self.inv_btn = QToolButton(self.body_widget)
        self.inv_btn.setText("Invert")
        self.inv_btn.setAccessibleName("Invert all bits")
        self.inv_btn.setToolTip("Toggle every hardware bit (0 ↔ 1); preserve the FXP format")
        self.inv_btn.clicked.connect(self.controller.invert_target_bits)
        bit_actions.addWidget(self.inv_btn, 0, 0)

        self.zero_btn = QToolButton(self.body_widget)
        self.zero_btn.setText("Zero")
        self.zero_btn.setAccessibleName("Set all bits to zero")
        self.zero_btn.setToolTip("Set every hardware bit to 0; keep the variable and its FXP format")
        self.zero_btn.clicked.connect(self.controller.clear_target_bits)
        bit_actions.addWidget(self.zero_btn, 0, 1)
        self.field_btn = QToolButton(self.body_widget)
        self.field_btn.setText("Edit bit range")
        self.field_btn.setAccessibleName("Edit bit range")
        self.field_btn.setToolTip("Write inclusive bits [high:low] as an unsigned integer; preserve every other bit")
        self.field_btn.clicked.connect(self._edit_bit_field)
        bit_actions.addWidget(self.field_btn, 0, 2)

        self.swap_order_btn = QToolButton(self.body_widget)
        self.swap_order_btn.setIcon(make_icon("swap", self.palette.text_secondary, 14))
        self.swap_order_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.swap_order_btn.setToolTip(tr("swap_bit_order_tooltip", lang, keep_english_terms=self.controller.config.keep_english_terms))
        self.swap_order_btn.clicked.connect(self.controller.toggle_bit_order)
        bit_header.addWidget(self.swap_order_btn)
        body_layout.addWidget(self.bit_actions_host)
        self.bit_help_lbl = QLabel("Click a bit to toggle 0 ↔ 1.", self.body_widget)
        self.bit_help_lbl.setWordWrap(True)
        self.bit_help_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_role(self.bit_help_lbl, "muted")
        body_layout.addWidget(self.bit_help_lbl)

        # Scrollable Bit Grid Frame (prevents >64 bits from squishing other panels)
        self.bit_scroll = QScrollArea(self.body_widget)
        self.bit_scroll.setWidgetResizable(True)
        self.bit_scroll.setMinimumHeight(88)
        self.bit_scroll.setMaximumHeight(185)
        set_role(self.bit_scroll, "bits")

        self.bit_grid_host = QWidget(self.bit_scroll)
        self.bit_grid_layout = QGridLayout(self.bit_grid_host)
        self.bit_grid_layout.setContentsMargins(8, 6, 8, 6)
        self.bit_grid_layout.setSpacing(3)
        self.bit_grid_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.bit_scroll.setWidget(self.bit_grid_host)
        self.bit_scroll.viewport().installEventFilter(self)
        self.body_scroll.viewport().installEventFilter(self)

        body_layout.addWidget(self.bit_scroll)
        body_layout.addStretch(1)

        body_scroll.setWidget(self.body_widget)
        main_layout.addWidget(body_scroll, 1)

        self._apply_cards_layout(controller.config.inspector_view_mode)
        self.controller.inspector_updated.connect(self.render_snapshot)
        self.controller.config_changed.connect(self._on_config_changed)
        self.controller.format_error_occurred.connect(self._on_format_error)
        self.controller.operation_finished.connect(self._operation_result)
        self.controller.console_executed.connect(lambda _: self._reset_errors())
        self._update_bit_legend()
        self.set_collapsed(controller.config.inspector_collapsed, save=False)
        self.render_snapshot(None)
        self.retranslate_ui(controller.config)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._layout_bit_cells)
        return super().eventFilter(watched, event)

    def _native_bits_text(self, bits):
        if not bits:
            return ""
        if bits.get("kind") == "python_int":
            text = self._text("Python int: no fixed width; negative values use a minus sign.")
            if bits["truncated"]:
                text += " " + self._text("Representation preview truncated; use hex(value) or bin(value) in the REPL for the full value.")
            return text
        if bits.get("kind") == "float":
            binary, exponent = bits["binary"], bits["exp_bits"]
            return self._text("{dtype} (E={exp_bits}, F={man_bits}, bias={bias}): sign | exponent | fraction = {groups}; {classification}",
                              dtype=bits["dtype"], groups=f"{binary[0]} | {binary[1:1 + exponent]} | {binary[1 + exponent:]}",
                              exp_bits=bits["exp_bits"], man_bits=bits["man_bits"],
                              bias=bits["bias"], classification=self._text(bits["classification"]))
        return self._text("Native {dtype} bits (read-only): {hex} / {binary} [{width} bits, {signed}]",
                          dtype=bits["dtype"], hex=bits["hex"], binary=bits["binary"], width=bits["width"],
                          signed=self._text("signed" if bits["signed"] else "unsigned"))

    def _diagnostic_text(self, snap):
        if self._text.lang != "zh_TW":
            return snap.status_text
        if snap.status_kind == "saturated":
            return self._text("SATURATED [Range: {range}]", range=snap.range_str)
        if snap.status_kind == "quant_err":
            # Preserve the kernel's exact decimal/fraction diagnostic, including
            # values that cannot be represented by the binary64 approximation.
            error = snap.status_text.removeprefix("Quant Err: ").removesuffix(" (last conversion)")
            return self._text("Quant Err: {error} (last conversion)", error=error)
        if snap.status_kind == "exact":
            return self._text("Exact conversion (LSB = {lsb})", lsb=snap.lsb_str)
        return self._text("No conversion reference (error unknown)")

    def _translate_bit_cell(self, cell):
        _, _, button, dot = self._bit_cells[cell.bit_index]
        metadata = self._last_snapshot.native_bits if isinstance(self._last_snapshot, PlainValueSnapshot) else None
        if metadata and metadata.get("kind") == "float":
            editable = metadata["editable"]
            action = self._text("Click to toggle; preserve format and bias" if editable else "Read-only")
            text = self._text("Bit {index}, {role}, value {value}: {action}",
                              index=cell.bit_index, role=self._text(cell.role), value=cell.value, action=action)
            button.setAccessibleName(text)
            button.setToolTip(text)
            button.setEnabled(editable)
            return
        button.setEnabled(True)
        button.setAccessibleName(self._text("Bit {index}, {role}, value {value}; toggle bit",
                                           index=cell.bit_index, role=self._text(cell.role), value=cell.value))
        sign_note = self._text(" (sign bit, negative weight)") if cell.role == "sign" else ""
        button.setToolTip(self._text("Bit {index}{sign_note}\nWeight: 2^{weight}\nClick to toggle 0 ↔ 1",
                                    index=cell.bit_index, sign_note=sign_note, weight=cell.weight_power))
        dot.setAccessibleName(self._text("Binary radix point"))

    def retranslate_ui(self, cfg):
        """Refresh only application copy, preserving every editable value/widget."""
        self._text.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        self.title_lbl.setText(tr("inspector", cfg.language, keep_english_terms=self.controller.config.keep_english_terms))
        self.title_lbl.setToolTip(self._text("Collapse Variable Inspector" if self.title_lbl.isChecked() else "Expand Variable Inspector"))
        self.title_lbl.setAccessibleName(self.title_lbl.toolTip())
        for button, key in ((self.list_mode_btn, "list_view_tooltip"), (self.grid_mode_btn, "grid_view_tooltip")):
            button.setToolTip(tr(key, cfg.language, keep_english_terms=self.controller.config.keep_english_terms))
            button.setText(button.toolTip())
            button.setAccessibleName(button.toolTip())
        self.fmt_edit.setToolTip(self._text(
            "FXP format changes apply to the entire variable or array.\n"
            "Qm.n includes sign in m; total bits = m + n (e.g. Q4.12). MATLAB short notation is also accepted.\n"
            "Custom display templates do not change format input syntax.\n"
            "Pin chooses whether to preserve numeric values or raw bits. For native values, use Convert to FXP or APyFixed.from_float()."))
        self.fmt_edit.setPlaceholderText(self._text("Format; Enter to apply"))
        self.fmt_edit.setAccessibleName(self._text("FXP format"))
        self.preview_btn.setText(self._text("Preview"))
        self.preview_btn.setToolTip(self._text("Preview FXP conversion\nReview changes before applying the format to the entire variable."))
        self.preview_btn.setAccessibleName(self.preview_btn.toolTip())
        for button, label, accessible, tooltip in (
            (self.inv_btn, "Invert", "Invert all bits", "Toggle every hardware bit (0 ↔ 1); preserve the FXP format"),
            (self.zero_btn, "Zero", "Set all bits to zero", "Set every hardware bit to 0; keep the variable and its FXP format"),
            (self.field_btn, "Edit bit range", "Edit bit range", "Write inclusive bits [high:low] as an unsigned integer; preserve every other bit"),
        ):
            button.setText(self._text(label))
            button.setAccessibleName(self._text(accessible))
            button.setToolTip(self._text(tooltip))
        self.bit_help_lbl.setText(self._text("Click a bit to toggle 0 ↔ 1."))
        self._update_bit_legend()
        snap = self._last_snapshot
        plain = isinstance(snap, PlainValueSnapshot)
        self.fmt_lbl.setText(self._text("Type:" if plain else "Format:"))
        keys = ("lbl_real_value", "lbl_hex_value", "lbl_s2_value", "lbl_uint_value", "lbl_c1_value", "Binary")
        for card, key in zip(self._cards, keys):
            card.retranslate_ui(cfg, self._text("Value") if plain and card is self.card_real else tr(key, cfg.language, keep_english_terms=self.controller.config.keep_english_terms))
        if plain:
            self.meta_info_lbl.setText(self._plain_metadata_text(snap))
            self.native_bits_label.setText(self._native_bits_text(snap.native_bits))
            self._plain_bit_controls(snap)
        elif snap is None:
            self.meta_info_lbl.setText(tr("no_var_selected", cfg.language, keep_english_terms=self.controller.config.keep_english_terms))
        else:
            self.meta_info_lbl.setText(self._text("Whole variable: {name}  |  WL={wl}, IWL={iwl}, FWL={fwl}  |  Range: {range}",
                                                name=snap.var_name, wl=snap.spec.wl, iwl=snap.spec.iwl,
                                                fwl=snap.spec.fwl, range=snap.range_str))
            self.status_badge.setText(self._diagnostic_text(snap))
            self.status_badge.setToolTip(self._text("Error compares this value with the input to its last tracked conversion. "
                "It does not measure accumulated arithmetic error or the precision of the original input."))
        if self._error_source:
            self.error_lbl.setText(self._text(self._error_source[0], **self._error_source[1]))
        for cell in self._bit_data:
            self._translate_bit_cell(cell)
        is_msb = cfg.bit_order == "msb_first"
        self.bit_title_lbl.setText(self._text("Bits (MSB → LSB)" if is_msb else "Bits (LSB → MSB)"))
        self.swap_order_btn.setToolTip(self._text("Reverse bit display order; currently {order}", order="MSB → LSB" if is_msb else "LSB → MSB"))
        self.swap_order_btn.setAccessibleName(self.swap_order_btn.toolTip())
        self._layout_bit_actions()
        self._layout_inspector_header()

    def _update_bit_legend(self):
        metadata = self._last_snapshot.native_bits if isinstance(self._last_snapshot, PlainValueSnapshot) else None
        middle = "Exponent" if metadata and metadata.get("kind") == "float" else "Integer"
        self.legend_lbl.setText(
            f'<span style="color:{self.palette.danger}">{self._text("Sign")}</span> · '
            f'<span style="color:{self.palette.warning}">{self._text(middle)}</span> · '
            f'<span style="color:{self.palette.accent}">{self._text("Fraction")}</span>')

    def _reset_errors(self):
        self._format_failure = None
        self.error_lbl.hide()
        for card in self._cards:
            card._failed_target = None
            card.error_label.hide()

    def _operation_result(self, request, error):
        if request.get("action") == "reformat" and self._last_snapshot:
            name, text = request["args"]
            if name == self._last_snapshot.var_name:
                self._format_failure = (name, text) if error else None
                if error:
                    self.fmt_edit.setText(text)
                    self._on_format_error(error)
                else:
                    self.error_lbl.hide()

    def _edit_bit_field(self):
        text, ok = prompt_text(self, self._text("Edit bit range"), self._text("high:low = unsigned integer (e.g. 7:4 = 0xA)"), self.controller.config.language)
        if ok:
            try:
                span, value = text.split("=")
                high, low = (int(item.strip(), 0) for item in span.split(":"))
                self.controller.set_bit_field(high, low, int(value.strip(), 0))
            except ValueError:
                self._on_format_error("Enter high:low = integer, for example 7:4 = 0xA", owned=True)

    def set_collapsed(self, collapsed: bool, *, save=True):
        self.title_lbl.setChecked(not collapsed)
        self.title_lbl.setArrowType(Qt.ArrowType.RightArrow if collapsed else Qt.ArrowType.DownArrow)
        self.title_lbl.setToolTip(self._text("Expand Variable Inspector" if collapsed else "Collapse Variable Inspector"))
        self.title_lbl.setAccessibleName(self.title_lbl.toolTip())
        self.body_scroll.setVisible(not collapsed)
        if save:
            self.controller.config.inspector_collapsed = collapsed
            self.controller._save_config()
            self.collapsed_changed.emit(collapsed)

    def apply_appearance(self, cfg):
        self.palette = get_palette(cfg.theme)
        for card in self._cards:
            card.palette = self.palette
            style_icon_button(card.copy_btn, "copy", f"Copy {card.title_lbl.text()}", self.palette.text_secondary, cfg)
            if card.pin_btn:
                style_icon_button(card.pin_btn, "pin", card.pin_btn.toolTip(), self.palette.text_secondary, cfg)
            card.update_pin_visual(cfg.inspector_pin_mode)
        for button, icon, tooltip in (
            (self.list_mode_btn, "list_view", tr("list_view_tooltip")),
            (self.grid_mode_btn, "grid_view", tr("grid_view_tooltip")),
            (self.swap_order_btn, "swap", tr("swap_bit_order_tooltip")),
            (self.preview_btn, "preview", "Preview FXP conversion\nReview changes before applying the format to the entire variable."),
        ):
            style_icon_button(button, icon, tooltip, self.palette.text_secondary, cfg)
        self.bit_scroll.setMinimumHeight(scaled_px(88, cfg))
        self.bit_scroll.setMaximumHeight(scaled_px(185, cfg))
        self._update_bit_legend()
        self._bit_layout_key = None
        self.render_snapshot(self._last_snapshot)
        self.retranslate_ui(cfg)
        QTimer.singleShot(0, self._layout_bit_cells)

    def _apply_cards_layout(self, view_mode: str) -> None:
        is_list = view_mode != "grid"
        self.list_mode_btn.setChecked(is_list)
        self.grid_mode_btn.setChecked(not is_list)

        # Remove cards from grid without deleting
        while self.cards_grid.count():
            self.cards_grid.takeAt(0)

        if is_list:
            for r_idx, card in enumerate(self._cards):
                self.cards_grid.addWidget(card, r_idx, 0, 1, 2)
        else:
            # 2-column compact Grid layout
            self.cards_grid.addWidget(self.card_real, 0, 0)
            self.cards_grid.addWidget(self.card_hex, 0, 1)
            self.cards_grid.addWidget(self.card_s2, 1, 0)
            self.cards_grid.addWidget(self.card_uint, 1, 1)
            self.cards_grid.addWidget(self.card_c1, 2, 0, 1, 2)
            self.cards_grid.addWidget(self.card_bin, 3, 0, 1, 2)

    def _on_config_changed(self, cfg) -> None:
        self._apply_cards_layout(cfg.inspector_view_mode)
        self.card_real.update_pin_visual(cfg.inspector_pin_mode)
        self.card_hex.update_pin_visual(cfg.inspector_pin_mode)
        self.retranslate_ui(cfg)
        self._layout_bit_cells()

    def _on_format_error(self, msg: str, *, owned=False) -> None:
        if msg:
            self._error_source = (msg, {}) if owned else ("Operation failed: {error}", {"error": msg})
            self.error_lbl.setText(self._text(self._error_source[0], **self._error_source[1]))
            self.error_lbl.setVisible(True)
        elif self._format_failure is None:
            self.error_lbl.setVisible(False)

    def _on_format_committed(self) -> None:
        if self._last_snapshot is None:
            return
        new_fmt = self.fmt_edit.text().strip()
        current = (FormatEngine.editable(self._last_snapshot.spec, self.controller.config)
                   if isinstance(self._last_snapshot, InspectorSnapshot) else "")
        if not new_fmt or new_fmt == current:
            return
        ok = self.controller.change_variable_format(
            self._last_snapshot.var_name, new_fmt
        )
        if not ok and self._last_snapshot is not None:
            self.fmt_edit.setText(current)

    def render_snapshot(self, snap: InspectorSnapshot | PlainValueSnapshot | None) -> None:
        if self._format_failure and (snap is None or snap.var_name != self._format_failure[0]):
            self._format_failure = None
            self.error_lbl.hide()
        previous = self._last_snapshot
        type_changed = ((type(previous), getattr(previous, "type_name", None)) !=
                        (type(snap), getattr(snap, "type_name", None)))
        self._last_snapshot = snap
        self.cards_container.setVisible(snap is not None)
        for card in self._cards:
            target = (snap.var_name, snap.coord) if snap is not None else None
            if type_changed or target != card._target or (snap and getattr(card, "_display_target", None) != snap.display_title):
                card._force_refresh = True
                card.edit.setModified(False)
                card._failed_target = None
                card.error_label.hide()
            card._target = target
            card._display_target = snap.display_title if snap else None
        plain = isinstance(snap, PlainValueSnapshot)
        self.preview_btn.setVisible(snap is not None and not plain)
        self.field_btn.setVisible(snap is not None and not plain)
        bits = snap.native_bits if plain else None
        self.native_bits_label.setVisible(bits is not None)
        self.native_bits_label.setText(self._native_bits_text(bits))
        self.fmt_lbl.setText(self._text("Type:" if plain else "Format:"))
        for card in (self.card_real, self.card_hex):
            card.setProperty("nativeValue", plain)
            card.update_pin_visual(self.controller.config.inspector_pin_mode)
        for card in self._cards[1:]:
            card.setVisible(not plain and card is not self.card_bin)
        for widget in (self.bit_scroll, self.bit_title_lbl, self.legend_lbl, self.bit_help_lbl,
                       self.bit_actions_host, self.inv_btn,
                       self.zero_btn, self.swap_order_btn):
            widget.setVisible(snap is not None and not plain)
        self.card_real.edit.setReadOnly(snap is None or (plain and not snap.editable))
        if self.card_real.pin_btn is not None:
            self.card_real.pin_btn.setVisible(not plain)
        self.fmt_edit.setEnabled(snap is not None and not plain)
        self.card_real.title_lbl.setText(self._text("Value") if plain else
                                         tr("lbl_real_value", self.controller.config.language, keep_english_terms=self.controller.config.keep_english_terms))
        if plain:
            self.target_badge.setText(snap.display_title)
            self.target_badge.setToolTip(snap.display_title)
            self.status_badge.setText(snap.type_name)
            self.status_badge.setProperty("statusKind", "plain")
            self.status_badge.setToolTip("")
            repolish(self.status_badge)
            self.meta_info_lbl.setText(self._plain_metadata_text(snap))
            self.meta_info_lbl.setWordWrap(True)
            if not self.fmt_edit.hasFocus() and not self.fmt_edit.isModified():
                self.fmt_edit.setText(snap.type_name)
            self.card_real.set_value_text(snap.value_str)
            metadata = snap.native_bits
            self.card_real.copy_btn.setEnabled(not (metadata and metadata.get("truncated", False)))
            for card, key in ((self.card_hex, "hex"), (self.card_bin, "binary")):
                card.setVisible(metadata is not None)
                card.edit.setReadOnly(True)
                card.set_value_text(metadata[key] if metadata else "")
                card.copy_btn.setEnabled(bool(metadata) and not metadata.get("truncated", False))
            self.card_hex.pin_btn.setVisible(False)
            self._plain_bit_controls(snap)
            return
        self.card_hex.pin_btn.setVisible(True)
        self.card_real.copy_btn.setEnabled(True)
        self.card_hex.copy_btn.setEnabled(True)
        self.card_hex.retranslate_ui(self.controller.config, tr("lbl_hex_value", self.controller.config.language,
                                                               keep_english_terms=self.controller.config.keep_english_terms))
        self._update_bit_legend()
        self.bit_help_lbl.setText(self._text("Click a bit to toggle 0 ↔ 1."))
        if snap is None:
            self.target_badge.clear()
            self.target_badge.setToolTip("")
            self.status_badge.setText("")
            self.meta_info_lbl.setText(
                tr("no_var_selected", self.controller.config.language, keep_english_terms=self.controller.config.keep_english_terms)
            )
            for c in self._cards:
                c.set_value_text("")
                c.edit.setReadOnly(True)
            for button in (self.inv_btn, self.zero_btn, self.swap_order_btn):
                button.setEnabled(False)
            self._clear_bit_cells()
            return

        for card in self._cards:
            card.edit.setReadOnly(card in (self.card_c1, self.card_bin))
        for button in (self.inv_btn, self.zero_btn, self.swap_order_btn):
            button.setEnabled(True)

        self.target_badge.setText(snap.display_title)
        self.target_badge.setToolTip(snap.display_title)
        if not self.fmt_edit.hasFocus() and not self.fmt_edit.isModified() and self._format_failure is None:
            self.fmt_edit.setText(FormatEngine.editable(snap.spec, self.controller.config))

        self.meta_info_lbl.setText(
            self._text("Whole variable: {name}  |  WL={wl}, IWL={iwl}, FWL={fwl}  |  Range: {range}",
                       name=snap.var_name, wl=snap.spec.wl, iwl=snap.spec.iwl, fwl=snap.spec.fwl, range=snap.range_str)
        )

        # Status badge semantic styling
        self.status_badge.setText(self._diagnostic_text(snap))
        self.status_badge.setProperty("statusKind", snap.status_kind if snap.status_kind in
                                     ("saturated", "quant_err", "exact") else "untracked")
        self.status_badge.setToolTip(
            self._text("Error compares this value with the input to its last tracked conversion. "
            "It does not measure accumulated arithmetic error or the precision of the original input.")
        )
        repolish(self.status_badge)

        self.card_real.set_value_text(snap.real_str)
        self.card_hex.set_value_text(snap.hex_str)
        self.card_s2.set_value_text(snap.s2_str)
        self.card_uint.set_value_text(snap.uint_str)
        self.card_c1.set_value_text(snap.c1_str)

        self._render_bit_cells(snap.bits_msb_to_lsb)

    def _plain_metadata_text(self, snapshot):
        metadata = snapshot.native_bits or {}
        if metadata.get("editable"):
            return self._text("APyFloat: bit edits preserve format and bias. Numeric and format edits use the REPL.")
        if metadata.get("kind") == "float":
            return self._text("Stored floating-point bits (read-only); viewing does not convert the value.")
        return self._text("Native value · use APyFixed.from_float() or Convert to FXP for fixed-point controls")

    def _plain_bit_controls(self, snapshot):
        metadata = snapshot.native_bits
        title = (self._text("Hex (integer value)") if metadata and metadata.get("kind") == "python_int" else
                 tr("lbl_hex_value", self.controller.config.language, keep_english_terms=self.controller.config.keep_english_terms))
        self.card_hex.retranslate_ui(self.controller.config, title)
        self.card_bin.retranslate_ui(self.controller.config, self._text("Binary"))
        floating = bool(metadata and metadata.get("kind") == "float")
        for widget in (self.bit_scroll, self.bit_title_lbl, self.legend_lbl, self.bit_help_lbl,
                       self.swap_order_btn):
            widget.setVisible(floating)
        self._update_bit_legend()
        if floating:
            self.bit_help_lbl.setText(self._text("Click to toggle; preserve format and bias" if metadata["editable"] else "Read-only"))
            bits = []
            for index in range(metadata["width"] - 1, -1, -1):
                role = ("sign" if index == metadata["width"] - 1 else
                        "exponent" if index >= metadata["man_bits"] else "frac")
                label = ("S" if role == "sign" else f"E{index - metadata['man_bits']}" if role == "exponent" else f"F{index}")
                bits.append(BitCellInfo(index, label, int(metadata["binary"][-1 - index]), role, None, False))
            self._render_bit_cells(bits)
        else:
            self._clear_bit_cells()

    def _clear_bit_cells(self) -> None:
        while self.bit_grid_layout.count():
            item = self.bit_grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._bit_cells.clear()
        self._bit_data = []
        self._bit_layout_key = None

    def _render_bit_cells(self, bits_msb_to_lsb: list[BitCellInfo]) -> None:
        active = {cell.bit_index for cell in bits_msb_to_lsb}
        for index in set(self._bit_cells) - active:
            box, *_ = self._bit_cells.pop(index)
            self.bit_grid_layout.removeWidget(box)
            box.deleteLater()
        self._bit_data = bits_msb_to_lsb
        for cell in bits_msb_to_lsb:
            if cell.bit_index not in self._bit_cells:
                box = QWidget(self.bit_grid_host)
                box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                outer = QHBoxLayout(box)
                outer.setContentsMargins(0, 0, 0, 0)
                outer.setSpacing(0)
                column = QVBoxLayout()
                column.setContentsMargins(0, 0, 0, 0)
                column.setSpacing(1)
                label = QLabel(box)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                set_role(label, "bitIndex")
                column.addWidget(label)
                button = QPushButton(box)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                set_role(button, "bit", code=True)
                button.clicked.connect(lambda _checked=False, index=cell.bit_index:
                                       self.controller.toggle_target_bit(index))
                column.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
                outer.addLayout(column)
                dot = QLabel(".", box)
                dot.setAccessibleName(self._text("Binary radix point"))
                set_role(dot, "radix")
                outer.addWidget(dot)
                self._bit_cells[cell.bit_index] = (box, label, button, dot)
            _, label, button, _ = self._bit_cells[cell.bit_index]
            label.setText(cell.label)
            label.setProperty("bitRole", cell.role)
            button.setText(str(cell.value))
            button.setFixedSize(scaled_px(26, self.controller.config), scaled_px(26, self.controller.config))
            button.setProperty("bitRole", cell.role)
            button.setProperty("bitValue", str(cell.value))
            self._translate_bit_cell(cell)
            repolish(label)
            repolish(button)
        self._layout_bit_cells()

    def _layout_bit_cells(self):
        self._layout_inspector_header()
        if not self._bit_data:
            return
        cfg = self.controller.config
        is_msb = self.controller.config.bit_order == "msb_first"
        ordered = self._bit_data if is_msb else list(reversed(self._bit_data))
        self.bit_title_lbl.setText(self._text("Bits (MSB → LSB)" if is_msb else "Bits (LSB → MSB)"))
        self.swap_order_btn.setToolTip(self._text("Reverse bit display order; currently {order}", order="MSB → LSB" if is_msb else "LSB → MSB"))
        self.swap_order_btn.setAccessibleName(self.swap_order_btn.toolTip())
        for cell in ordered:
            dot = self._bit_cells[cell.bit_index][3]
            fwl = self._last_snapshot.spec.fwl if isinstance(self._last_snapshot, InspectorSnapshot) else 0
            dot.setVisible(cell.has_radix_after if is_msb else 0 < fwl < len(ordered) and cell.bit_index == fwl - 1)
        margins = self.bit_grid_layout.contentsMargins()
        available = max(1, self.bit_scroll.viewport().width() - margins.left() - margins.right())
        cell_width = max(record[0].sizeHint().width() for record in self._bit_cells.values())
        base_width = max(max(label.sizeHint().width(), button.width())
                         for _, label, button, _ in self._bit_cells.values())
        gap = scaled_px(8, cfg)
        spacing = self.bit_grid_layout.horizontalSpacing()
        columns = 4
        for candidate in (16, 8, 4):
            group_gaps = (candidate - 1) // 4
            needed = candidate * cell_width + (candidate - 1 + group_gaps) * spacing + group_gaps * gap
            if needed <= available:
                columns = candidate
                break
        key = (columns, is_msb, tuple(cell.bit_index for cell in ordered), cell_width, gap, base_width)
        if key != self._bit_layout_key:
            self._bit_layout_key = key
            while self.bit_grid_layout.count():
                self.bit_grid_layout.takeAt(0)
            for column in range(20):
                self.bit_grid_layout.setColumnMinimumWidth(column, 0)
            # Align actual hardware nibbles, including a partial highest nibble.
            # Leading MSB slots are empty layout space, never invented bits.
            padded_bits = (max(cell.bit_index for cell in ordered) // 4 + 1) * 4
            visible_columns = min(columns, padded_bits)
            for column in range(visible_columns):
                self.bit_grid_layout.setColumnMinimumWidth(column + column // 4, base_width)
            for column in range(4, visible_columns + visible_columns // 4 - 1, 5):
                self.bit_grid_layout.setColumnMinimumWidth(column, gap)
            for cell in ordered:
                position = padded_bits - 1 - cell.bit_index if is_msb else cell.bit_index
                row, column = divmod(position, columns)
                self.bit_grid_layout.addWidget(self._bit_cells[cell.bit_index][0], row, column + column // 4)
            self.bit_grid_layout.activate()
        self._layout_bit_actions()

    def _layout_inspector_header(self):
        if not hasattr(self, "grid_mode_btn"):
            return
        needed = (self.title_lbl.sizeHint().width() + self.list_mode_btn.width()
                  + self.grid_mode_btn.width() + 50)
        stacked = needed > self.width()
        if stacked == self._header_stacked:
            return
        self._header_stacked = stacked
        placements = (
            ((self.title_lbl, 0, 0, 4), (self.target_badge, 1, 0, 2),
             (self.list_mode_btn, 1, 2, 1), (self.grid_mode_btn, 1, 3, 1)) if stacked else
            ((self.title_lbl, 0, 0, 1), (self.target_badge, 0, 1, 1),
             (self.list_mode_btn, 0, 2, 1), (self.grid_mode_btn, 0, 3, 1)))
        for widget, row, column, span in placements:
            self._header_layout.addWidget(widget, row, column, 1, span)
        self._header_layout.setColumnStretch(0, 0)
        self._header_layout.setColumnStretch(1, 1)
        self._header_layout.activate()

    def _layout_bit_actions(self):
        available = max(1, self.body_scroll.viewport().width() - 20)
        self.field_btn.setText(self._text("Edit bit range"))
        if self.field_btn.sizeHint().width() > available:
            self.field_btn.setText(self._text("Edit bit\nrange"))
        buttons = [self.inv_btn, self.zero_btn, self.field_btn]
        widths = [button.sizeHint().width() for button in buttons]
        columns = 3 if sum(widths) + 12 <= available else 2 if 2 * max(widths) + 6 <= available else 1
        if getattr(self, "_action_columns", None) == columns:
            return
        self._action_columns = columns
        for position, button in enumerate(buttons):
            self.bit_actions_layout.addWidget(button, position // columns, position % columns)
        self.bit_actions_layout.activate()
