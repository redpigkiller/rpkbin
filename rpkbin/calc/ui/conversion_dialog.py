"""Explicit native-to-FXP conversion using the existing controller operations."""
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout

from ..core.format_engine import FormatEngine
from .i18n import UiText
from .icons import style_icon_button
from .theme import get_palette, set_role


class FixedPointConversionDialog(QDialog):
    def __init__(self, inspector):
        super().__init__(inspector.window())
        self.inspector = inspector
        self.controller = inspector.controller
        self.name = inspector._last_snapshot.var_name
        self.stamp = inspector._conversion_stamp()
        self.copy = UiText()
        self._error = None
        layout = QVBoxLayout(self)
        self.scope_lbl = QLabel(self)
        layout.addWidget(self.scope_lbl)
        row = QHBoxLayout()
        self.format_lbl = QLabel(self)
        row.addWidget(self.format_lbl)
        self.fmt_edit = QLineEdit(inspector._recent_format, self)
        row.addWidget(self.fmt_edit, 1)
        self.preview_btn = QToolButton(self)
        self.preview_btn.clicked.connect(self.preview)
        row.addWidget(self.preview_btn)
        layout.addLayout(row)
        self.policy_lbl = QLabel(self)
        self.policy_lbl.setWordWrap(True)
        set_role(self.policy_lbl, "muted")
        layout.addWidget(self.policy_lbl)
        self.error_lbl = QLabel(self)
        self.error_lbl.setWordWrap(True)
        set_role(self.error_lbl, "error")
        self.error_lbl.hide()
        layout.addWidget(self.error_lbl)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton(self)
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.clicked.connect(self.reject)
        self.apply_btn = QPushButton(self)
        set_role(self.apply_btn, "primary")
        self.apply_btn.clicked.connect(self.apply)
        self.apply_btn.setDefault(True)
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.cancel_btn)
        self.setTabOrder(self.apply_btn, self.cancel_btn)
        layout.addLayout(buttons)
        self.fmt_edit.editingFinished.connect(self._validate_finished_input)
        self.fmt_edit.textChanged.connect(self._update_validity)
        self.retranslate_ui(self.controller.config)
        # Wrapped text needs a height measured at the actual initial width.
        # Settle style/layout constraints before Windows creates the native window.
        self.ensurePolished()
        layout.activate()
        minimum = self.minimumSizeHint()
        width = max(470, minimum.width())
        self.resize(width, max(minimum.height(), layout.totalHeightForWidth(width)))

    def retranslate_ui(self, cfg):
        self.copy.refresh(cfg.language, keep_english_terms=cfg.keep_english_terms)
        self.setWindowTitle(self.copy("Convert to FXP…"))
        self.scope_lbl.setText(self.copy("Whole variable: {name}", name=self.name))
        self.format_lbl.setText(self.copy("Format:"))
        self.fmt_edit.setAccessibleName(self.copy("FXP format"))
        self.fmt_edit.setPlaceholderText(self.copy("Enter format"))
        self.fmt_edit.setToolTip(self.copy("Choose the word length and fractional bits explicitly; no format is inferred."))
        self.policy_lbl.setText(self.copy("Whole variable · preserve {mode} · {quant}/{overflow}",
            mode=self.copy("Real Value"), quant=cfg.default_quant, overflow=cfg.default_overflow))
        style_icon_button(self.preview_btn, "preview", self.copy("Preview"), get_palette(cfg.theme).text_secondary, cfg, label=self.copy("Preview"))
        self.cancel_btn.setText(self.copy("Cancel"))
        self.apply_btn.setText(self.copy("Apply"))
        self._update_validity()
        if self._error:
            self.error_lbl.setText(self.copy(self._error[0], **self._error[1]))

    def _update_validity(self, *_args):
        self.fmt_edit.setToolTip(self.copy("Enter a Q/MATLAB format or a uniquely reversible current custom template."))
        try:
            FormatEngine.parse_input(self.fmt_edit.text(), self.controller.config)
            valid = True
        except ValueError as exc:
            valid = False
            self.fmt_edit.setToolTip(self.copy(str(exc)))
        if valid and self.stamp == self.inspector._conversion_stamp():
            self._error = None
            self.error_lbl.hide()
        self.preview_btn.setEnabled(valid)
        self.apply_btn.setEnabled(valid)

    def _validate_finished_input(self):
        if self.fmt_edit.text().strip():
            self._validated()

    def _validated(self):
        if self.stamp != self.inspector._conversion_stamp():
            self._error = ("Workspace or conversion settings changed; reopen the conversion dialog.", {})
        else:
            try:
                spec = FormatEngine.parse_input(self.fmt_edit.text(), self.controller.config)
                self._error = None
                self.error_lbl.hide()
                return spec
            except ValueError as exc:
                self._error = ("Operation failed: {error}", {"error": self.copy(str(exc))})
        self.error_lbl.setText(self.copy(self._error[0], **self._error[1]))
        self.error_lbl.show()
        return None

    def preview(self):
        spec = self._validated()
        if spec is not None:
            self.controller.request_preview(self.name, spec, pin_mode="real")

    def apply(self):
        spec = self._validated()
        if spec is not None and self.controller.convert_to_fxp(self.name, spec,
                expected_revision=self.controller.revision):
            self.accept()
