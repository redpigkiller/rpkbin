"""Draft-only preferences UI with explicit import preview and script consent."""
from __future__ import annotations

import copy
import html
import json
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFontComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..core.config import CalcConfig, DEFAULT_CONFIG_PATH
from ..core.auto_imports import parse_imports
from ..core.format_engine import FormatEngine, FxpSpec
from ..core.settings_transfer import export_preferences, merge_preferences, parse_preferences
from ..core.startup import StartupFile
from .i18n import UiText
from .theme import get_palette, qss_family, set_role


class _IgnoreWheel:
    """Let parent scroll areas handle wheel input; keyboard and arrows still work."""
    def wheelEvent(self, event):
        event.ignore()


class PreferenceCombo(_IgnoreWheel, QComboBox):
    pass


class PreferenceFontCombo(_IgnoreWheel, QFontComboBox):
    pass


class PreferenceSpin(_IgnoreWheel, QSpinBox):
    pass


class SettingsDialog(QDialog):
    """All edits, imports and reset affect a draft; Apply returns it to the owner."""
    def __init__(self, config: CalcConfig, parent: QWidget | None = None,
                 *, config_path: Path | str | None = None, controller=None) -> None:
        super().__init__(parent)
        controller = controller or getattr(parent, "controller", None)
        config_path = config_path or getattr(controller, "_config_path", None)
        self._working_cfg = copy.deepcopy(config)
        self._text = UiText(config.language)
        self._config_path = Path(config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
        self.controller = controller
        self.startup_store = StartupFile(self._config_path)
        self._startup_pending = None
        self._startup_revision = None
        self._startup_read_error = ""
        try:
            self.startup_store.initialize(config.startup_script)
            self._startup_revision = self.startup_store.revision()
        except (OSError, ValueError, UnicodeError) as exc:
            self._startup_read_error = str(exc)
        self.palette = get_palette(config.theme)
        self.setWindowTitle(self._text("Settings"))
        self.resize(820, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        self.tabs = QTabWidget(self)
        for builder, title in ((self._build_appearance_tab, "Appearance"),
                               (self._build_format_tab, "FXP format"),
                               (self._build_auto_import_tab, "Auto-import"),
                               (self._build_management_tab, "Preferences")):
            self.tabs.addTab(builder(), self._text(title))
        layout.addWidget(self.tabs, 1)
        buttons = QHBoxLayout()
        hint = self._label("Apply saves preferences. Startup code runs separately.")
        set_role(hint, "muted")
        self.apply_hint = hint
        buttons.addWidget(hint, 1)
        self.cancel_btn = self._button("Cancel", self.reject)
        self.save_btn = self._button("Apply", self._on_save)
        self.save_btn.setToolTip(self._text("Save preferences and apply changes"))
        set_role(self.save_btn, "primary")
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)
        self._update_preview()

    def _label(self, source, *, bold=False):
        label = QLabel(self._text(source), self)
        label.setWordWrap(True)
        if bold:
            label.setStyleSheet("font-weight: 700;")
        return label

    def _button(self, source, slot):
        button = QPushButton(self._text(source), self)
        button.clicked.connect(slot)
        return button

    def _scroll(self, page):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _table(self, headers, rows):
        # Rich labels adapt row heights to translated copy and the chosen font.
        def esc(source):
            return html.escape(self._text(source))
        markup = '<table cellspacing="0" cellpadding="5" width="100%"><tr>'
        markup += "".join(f'<th align="left">{esc(cell)}</th>' for cell in headers) + '</tr>'
        markup += "".join('<tr>' + ''.join(f'<td>{esc(cell)}</td>' for cell in row) + '</tr>'
                          for row in rows) + '</table>'
        label = QLabel(markup, self)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _build_appearance_tab(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.language_combo = PreferenceCombo(page)
        for label, key in (("English", "en_US"), ("繁體中文", "zh_TW")):
            self.language_combo.addItem(label, key)
        self.language_combo.setCurrentIndex(self.language_combo.findData(self._working_cfg.language))
        form.addRow(self._text("Language"), self.language_combo)
        self.english_terms_check = QCheckBox(self._text("Keep workspace terms in English"), page)
        self.english_terms_check.setChecked(self._working_cfg.keep_english_terms)
        form.addRow("", self.english_terms_check)
        self.theme_combo = PreferenceCombo(page)
        for label, key in (("Dark", "dark"), ("Light", "light")):
            self.theme_combo.addItem(self._text(label), key)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self._working_cfg.theme))
        form.addRow(self._text("Theme"), self.theme_combo)
        self.ui_font_combo = PreferenceFontCombo(page)
        self.code_font_combo = PreferenceFontCombo(page)
        self.code_font_combo.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        for control, family in ((self.ui_font_combo, self._working_cfg.ui_font_family),
                                (self.code_font_combo, self._working_cfg.code_font_family)):
            control.setCurrentFont(QFont(family))
            control.setMinimumContentsLength(18)
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.ui_font_size = PreferenceSpin(page)
        self.code_font_size = PreferenceSpin(page)
        self.scale_spin = PreferenceSpin(page)
        for control, low, high, value, suffix in (
                (self.ui_font_size, 8, 24, self._working_cfg.ui_font_size, " pt"),
                (self.code_font_size, 8, 32, self._working_cfg.code_font_size, " pt"),
                (self.scale_spin, 80, 160, self._working_cfg.ui_scale, " %")):
            control.setRange(low, high)
            control.setValue(value)
            control.setSuffix(suffix)
        self.scale_spin.setSingleStep(10)
        for source, control in (("Interface font", self.ui_font_combo),
                                ("Interface font size", self.ui_font_size),
                                ("Code and values font", self.code_font_combo),
                                ("Code and values font size", self.code_font_size),
                                ("Interface scale", self.scale_spin)):
            form.addRow(self._text(source), control)
        layout.addLayout(form)
        self.font_preview = self._label("Preview: Workspace / Variable Inspector")
        self.code_preview = QLabel('A = np.array([1.25, -0.03]); 0x385  0123456789', page)
        layout.addWidget(self.font_preview)
        layout.addWidget(self.code_preview)
        for control in (self.ui_font_combo, self.code_font_combo):
            control.currentFontChanged.connect(self._update_font_preview)
        for control in (self.ui_font_size, self.code_font_size, self.scale_spin):
            control.valueChanged.connect(self._update_font_preview)
        self._update_font_preview()
        layout.addWidget(self._label("Appearance and language apply without restarting the kernel.\n"
                                    "Window and panel sizes are remembered when you close the app."))
        layout.addStretch()
        return self._scroll(page)

    def _update_font_preview(self, *_):
        for label, combo, size in ((self.font_preview, self.ui_font_combo, self.ui_font_size),
                                   (self.code_preview, self.code_font_combo, self.code_font_size)):
            font = combo.currentFont()
            points = size.value() * self.scale_spin.value() / 100
            label.setStyleSheet(f"font-family: {qss_family(font.family())}; font-size: {points:g}pt;")

    def _build_format_tab(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(self._label("Format display notation", bold=True))
        self.preset_group = QButtonGroup(self)
        self._radio_map = {}
        for key, label in (("q_format", "Q-format"), ("matlab", "MATLAB short"),
                           ("custom", "Custom display template")):
            radio = QRadioButton(self._text(label), page)
            radio.setChecked(self._working_cfg.format_preset == key)
            self.preset_group.addButton(radio)
            self._radio_map[key] = radio
            layout.addWidget(radio)
        layout.addWidget(self._table(("Notation", "Example", "Meaning"), (
            ("Q-format", "Q4.12 / UQ4.12", "16 total bits; 12 fractional bits"),
            ("MATLAB short", "sfix16_En12 / ufix16_En12", "16 total bits; 12 fractional bits"),
        )))
        layout.addWidget(self._label("Qm.n: m includes sign; total bits = m + n. Q15 means Q1.15.\n"
                                    "Q conventions vary across tools. sfix/Q are signed; ufix/UQ are unsigned."))
        layout.addWidget(self._label("Display only. Data and APyTypes constructors are unchanged.\n"
                                    "Format editing accepts Q or MATLAB notation, not custom templates."))
        self.custom_frame = QFrame(page)
        set_role(self.custom_frame, "custom")
        form = QFormLayout(self.custom_frame)
        self.signed_tpl_edit = QLineEdit(self._working_cfg.custom_signed_tpl, page)
        self.unsigned_tpl_edit = QLineEdit(self._working_cfg.custom_unsigned_tpl, page)
        self.int_tpl_edit = QLineEdit(self._working_cfg.custom_int_tpl, page)
        for source, control in (("Signed fractional template", self.signed_tpl_edit),
                                ("Unsigned fractional template", self.unsigned_tpl_edit),
                                ("Integer template (FWL = 0)", self.int_tpl_edit)):
            form.addRow(self._text(source), control)
        form.addRow(self._label("Fields: {wl} total bits, {iwl} integer bits (includes sign), "
                               "{fwl} fractional bits, {q_prefix}, {s_or_u}, {S_or_U}"))
        self.custom_frame.setVisible(self._working_cfg.format_preset == "custom")
        layout.addWidget(self.custom_frame)
        self.preview_lbl = QLabel(page)
        self.preview_lbl.setWordWrap(True)
        set_role(self.preview_lbl, "preview", code=True)
        layout.addWidget(self.preview_lbl)
        for radio in self._radio_map.values():
            radio.toggled.connect(self._on_preset_toggled)
        for control in (self.signed_tpl_edit, self.unsigned_tpl_edit, self.int_tpl_edit):
            control.textChanged.connect(self._update_preview)
        layout.addWidget(self._label("GUI FXP conversion defaults", bold=True))
        layout.addWidget(self._label("Used by GUI Convert to FXP, numeric edits and format conversions.\n"
                                    "Native APyTypes constructors and arithmetic keep their own behavior."))
        defaults_form = QFormLayout()
        self.quant_combo = PreferenceCombo(page)
        self.ovf_combo = PreferenceCombo(page)
        for mode in ("RND", "RND_EVEN", "TRN", "TRN_ZERO"):
            self.quant_combo.addItem(mode, mode)
        for mode in ("SAT", "WRAP"):
            self.ovf_combo.addItem(mode, mode)
        self.quant_combo.setCurrentIndex(self.quant_combo.findData(self._working_cfg.default_quant))
        self.ovf_combo.setCurrentIndex(self.ovf_combo.findData(self._working_cfg.default_overflow))
        defaults_form.addRow(self._text("Quantization"), self.quant_combo)
        defaults_form.addRow(self._text("Overflow"), self.ovf_combo)
        layout.addLayout(defaults_form)
        layout.addWidget(self._table(("Option", "Behavior"), (
            ("RND", "Nearest; ties toward +infinity"),
            ("RND_EVEN", "Nearest; ties to even"),
            ("TRN", "Round toward -infinity (floor)"),
            ("TRN_ZERO", "Truncate toward zero"),
            ("SAT", "Clamp to the minimum or maximum"),
            ("WRAP", "Wrap within the available bits"),
        )))
        layout.addStretch()
        return self._scroll(page)

    def _build_auto_import_tab(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(self._label("Function packs", bold=True))
        layout.addWidget(self._label("Available in the console; hidden from Workspace. Changes apply after Apply."))
        self._group_checks = {}
        for key, name, functions in (
            ("basic_math", "Basic math", "sin, cos, tan, asin, acos, atan, exp, log, log2, log10, sqrt, pi, e"),
            ("linalg", "Linear algebra", "svd, eig, inv, pinv, norm, det, eye, zeros, ones, diag"),
            ("fitting_comm", "Fitting and communications", "polyfit, polyval, linfit, qfunc, erfc, erf"),
            ("fft", "FFT", "fft, ifft, fftshift, ifftshift"),
        ):
            checkbox = QCheckBox(self._text(name), page)
            checkbox.setChecked(self._working_cfg.auto_import_groups.get(key, True))
            checkbox.setMinimumHeight(28)
            checkbox.setAccessibleName(self._text(name))
            self._group_checks[key] = checkbox
            layout.addWidget(checkbox)
            details = QLabel(functions, page)
            details.setWordWrap(True)
            details.setContentsMargins(24, 0, 0, 6)
            set_role(details, "muted")
            layout.addWidget(details)
        layout.addWidget(self._label("Custom imports", bold=True))
        layout.addWidget(self._label("One import statement per line; installed packages only. Runs after Apply and at startup.\n"
                                    "Order: function packs → custom imports → startup.py. Helpers and other code belong in startup.py."))
        self.custom_imports_edit = QPlainTextEdit(self._working_cfg.custom_imports, page)
        self.custom_imports_edit.setAccessibleName(self._text("Custom imports"))
        self.custom_imports_edit.setPlaceholderText("import scipy as sp\nfrom scipy.signal import butter, sosfilt")
        self.custom_imports_edit.setMinimumHeight(100)
        set_role(self.custom_imports_edit, "code", code=True)
        layout.addWidget(self.custom_imports_edit)
        layout.addWidget(self._label("Explicit names and aliases are supported; wildcard and relative imports are not.\n"
                                    "Existing user variables and cached startup helpers keep their bindings; restart for a clean load."))
        self.import_status = self._label("Not run in this session.")
        self.import_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.import_status)
        if self.controller:
            self.controller.import_state_changed.connect(self._show_import_state)
            if self.controller.transport.ready:
                self._show_import_state(self.controller.import_error)
        layout.addWidget(self._label("Startup script", bold=True))
        layout.addWidget(self._label("np, apy, APyFixed, APyFixedArray, APyFloat and APyFloatArray are preloaded.\n"
                                    "Run at startup/restart, or choose Reload and run. Saving does not run code.\n"
                                    "Imports and helpers stay out of Workspace. Installed packages can be imported here."))
        self.startup_path_edit = QLineEdit(str(self.startup_store.path), page)
        self.startup_path_edit.setReadOnly(True)
        layout.addWidget(self.startup_path_edit)
        actions = QHBoxLayout()
        self.open_startup_btn = self._button("Open text editor", self._open_startup_editor)
        self.refresh_startup_btn = self._button("Refresh preview", self._refresh_startup_preview)
        self.reload_startup_btn = self._button("Reload and run", self._reload_startup)
        for button in (self.open_startup_btn, self.refresh_startup_btn, self.reload_startup_btn):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.startup_status = self._label("Not run in this session.")
        layout.addWidget(self.startup_status)
        self.startup_notice = self._label("Startup file notice: {notice}")
        notice = getattr(self.controller, "_startup_notice", "")
        self.startup_notice.setText(self._text("Startup file notice: {notice}", notice=notice))
        self.startup_notice.setVisible(bool(notice))
        layout.addWidget(self.startup_notice)
        self.startup_edit = QPlainTextEdit(page)
        self.startup_edit.setReadOnly(True)
        self.startup_edit.setMinimumHeight(150)
        layout.addWidget(self.startup_edit, 1)
        layout.addWidget(self._label("Preview only. External editor saves are not undone by Cancel. Apply does not run startup.py."))
        self._refresh_startup_preview()
        self.reload_startup_btn.setEnabled(bool(self.controller and self.controller.is_available))
        if self.controller:
            self.controller.startup_state_changed.connect(self._show_startup_state)
            self.controller.interaction_enabled.connect(self._startup_enabled)
            self._show_startup_state(self.controller.startup_state)
        return self._scroll(page)

    def _show_import_state(self, error):
        self.import_status.setText(self._text("Last import load failed: {error}", error=error)
                                   if error else self._text("Last import load succeeded."))

    def _startup_enabled(self, enabled):
        self.reload_startup_btn.setEnabled(enabled and self._startup_pending is None)

    def _refresh_startup_preview(self):
        try:
            source = self._startup_pending if self._startup_pending is not None else self.startup_store.read()
            self.startup_edit.setPlainText(source)
        except (OSError, ValueError, UnicodeError) as exc:
            self.startup_edit.setPlainText(str(exc))
            self.startup_status.setText(self._text("Could not read startup.py: {error}", error=str(exc)))

    def _open_startup_editor(self):
        # Windows .py associations may execute Python. Select an actual text
        # editor explicitly, never shell-open the script.
        try:
            if sys.platform == "win32":
                editor = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "notepad.exe")
                subprocess.Popen([editor, str(self.startup_store.path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-e", str(self.startup_store.path)])
            else:
                editor, _ = QFileDialog.getOpenFileName(self, self._text("Choose a text editor executable"))
                if editor:
                    subprocess.Popen([editor, str(self.startup_store.path)])
        except OSError as exc:
            self._warning(self._text("Could not open text editor: {error}", error=str(exc)))

    def _reload_startup(self):
        if self._startup_pending is not None:
            self._warning("Apply imported startup code before reloading.")
            return
        self._refresh_startup_preview()
        if self.controller and self.controller.reload_startup():
            self.startup_status.setText(self._text("Running startup.py..."))
            self.reload_startup_btn.setEnabled(False)

    def _show_startup_state(self, state):
        if state:
            self.startup_status.setText(self._text("Last run succeeded." if state.get("ok") else "Last run failed. See details below."))
            if state.get("error"):
                self.startup_edit.setPlainText(state["error"])

    def _build_management_tab(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(self._label("Settings file", bold=True))
        self.path_edit = QLineEdit(str(self._config_path), page)
        self.path_edit.setReadOnly(True)
        layout.addWidget(self.path_edit)
        self.open_folder_btn = self._button("Open settings folder", self._open_settings_folder)
        layout.addWidget(self.open_folder_btn)
        layout.addWidget(self._label("Transfer preferences", bold=True))
        layout.addWidget(self._label("Exports include preferences and startup code, not variables, history or window sizes.\n"
                                    "Import previews changes first; startup code is excluded unless you select it."))
        actions = QHBoxLayout()
        self.export_btn = self._button("Export preferences...", self._export_preferences)
        self.import_btn = self._button("Import preferences...", self._import_preferences)
        self.defaults_btn = self._button("Restore defaults", self._restore_defaults)
        self.defaults_btn.setToolTip(self._text("Restore defaults preserves your startup.py."))
        for button in (self.export_btn, self.import_btn, self.defaults_btn):
            actions.addWidget(button)
        layout.addLayout(actions)
        layout.addWidget(self._label("Import and Restore defaults only update this draft. Cancel discards them.\n"
                                    "Apply saves imported startup code without running it. Review it before reloading."))
        layout.addWidget(self._label("Restore defaults preserves your startup.py."))
        layout.addStretch()
        return self._scroll(page)

    def _open_settings_folder(self):
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._config_path.parent))):
                raise OSError("Could not open settings folder")
        except OSError:
            self._warning("Could not open settings folder.")

    def _warning(self, source):
        # Explicit application copy: Qt's built-in buttons follow system locale.
        box = QMessageBox(QMessageBox.Icon.Warning, self._text("Settings"), self._text(source),
                          QMessageBox.StandardButton.Ok, self)
        box.button(QMessageBox.StandardButton.Ok).setText(self._text("OK"))
        box.exec()

    def _selected_preset(self):
        return next((key for key, radio in self._radio_map.items() if radio.isChecked()), "q_format")

    def _on_preset_toggled(self):
        self.custom_frame.setVisible(self._selected_preset() == "custom")
        self._update_preview()

    def _update_preview(self):
        cfg = copy.deepcopy(self._working_cfg)
        cfg.format_preset = self._selected_preset()
        cfg.custom_signed_tpl = self.signed_tpl_edit.text().strip()
        cfg.custom_unsigned_tpl = self.unsigned_tpl_edit.text().strip()
        cfg.custom_int_tpl = self.int_tpl_edit.text().strip()
        values = [FormatEngine.format(FxpSpec(signed, 16, fractional), cfg)
                  for signed, fractional in ((True, 12), (False, 12), (True, 0))]
        self.preview_lbl.setText(self._text("Preview: signed → {signed} | unsigned → {unsigned} | integer → {integer}",
                                           signed=values[0], unsigned=values[1], integer=values[2]))

    def _draft_config(self, *, validate_imports=True):
        cfg = copy.deepcopy(self._working_cfg)
        cfg.language = self.language_combo.currentData()
        cfg.keep_english_terms = self.english_terms_check.isChecked()
        cfg.theme = self.theme_combo.currentData()
        cfg.ui_font_family = self.ui_font_combo.currentFont().family()
        cfg.code_font_family = self.code_font_combo.currentFont().family()
        cfg.ui_font_size = self.ui_font_size.value()
        cfg.code_font_size = self.code_font_size.value()
        cfg.ui_scale = self.scale_spin.value()
        cfg.format_preset = self._selected_preset()
        for key, control, fallback in (("custom_signed_tpl", self.signed_tpl_edit, "Q{iwl}.{fwl}"),
                                       ("custom_unsigned_tpl", self.unsigned_tpl_edit, "UQ{iwl}.{fwl}"),
                                       ("custom_int_tpl", self.int_tpl_edit, "{q_prefix}{wl}.0")):
            setattr(cfg, key, control.text().strip() or fallback)
        cfg.default_quant = self.quant_combo.currentData()
        cfg.default_overflow = self.ovf_combo.currentData()
        cfg.auto_import_groups = {key: checkbox.isChecked() for key, checkbox in self._group_checks.items()}
        cfg.custom_imports = self.custom_imports_edit.toPlainText()
        if validate_imports:
            parse_imports(cfg.custom_imports)
        cfg.startup_file = str(self.startup_store.path)
        cfg.startup_script = self._startup_pending if self._startup_pending is not None else self.startup_store.read()
        return cfg

    def _update_controls(self, cfg):
        self._working_cfg = copy.deepcopy(cfg)
        self.english_terms_check.setChecked(cfg.keep_english_terms)
        for control, value in ((self.language_combo, cfg.language), (self.theme_combo, cfg.theme),
                               (self.quant_combo, cfg.default_quant), (self.ovf_combo, cfg.default_overflow)):
            control.setCurrentIndex(control.findData(value))
        self.ui_font_combo.setCurrentFont(QFont(cfg.ui_font_family))
        self.code_font_combo.setCurrentFont(QFont(cfg.code_font_family))
        self.ui_font_size.setValue(cfg.ui_font_size)
        self.code_font_size.setValue(cfg.code_font_size)
        self.scale_spin.setValue(cfg.ui_scale)
        self._radio_map[cfg.format_preset].setChecked(True)
        self.signed_tpl_edit.setText(cfg.custom_signed_tpl)
        self.unsigned_tpl_edit.setText(cfg.custom_unsigned_tpl)
        self.int_tpl_edit.setText(cfg.custom_int_tpl)
        for key, checkbox in self._group_checks.items():
            checkbox.setChecked(cfg.auto_import_groups[key])
        self.custom_imports_edit.setPlainText(cfg.custom_imports)
        self._refresh_startup_preview()
        self._update_preview()

    def _export_preferences(self):
        path, _ = QFileDialog.getSaveFileName(self, self._text("Export preferences"), "calc-preferences.json", "JSON (*.json)")
        if not path:
            return
        try:
            payload = export_preferences(self._draft_config())
            Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError):
            self._warning("Could not export preferences.")

    def _import_preferences(self):
        path, _ = QFileDialog.getOpenFileName(self, self._text("Import preferences"), "", "JSON (*.json)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 2_000_000:
                raise ValueError("File too large")
            values = parse_preferences(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            self._warning("Invalid preferences file. Check its type, version and values.")
            return
        self._show_import_preview(values)

    def _show_import_preview(self, values):
        # Validate before opening the preview; no imported source is evaluated.
        try:
            draft = self._draft_config()
            merged = merge_preferences(draft, values)
        except (OSError, ValueError, UnicodeError) as exc:
            self._warning(str(exc))
            return
        preview = QDialog(self)
        preview.setWindowTitle(self._text("Preview preference changes"))
        preview.resize(680, 520)
        layout = QVBoxLayout(preview)
        layout.addWidget(self._label("Review changes. Nothing is saved or executed until Apply in Settings."))
        changes = QPlainTextEdit(preview)
        changes.setReadOnly(True)
        old = export_preferences(draft)["preferences"]
        new = export_preferences(merged)["preferences"]
        labels = {
            "language": "Language", "theme": "Theme", "format_preset": "Format display notation",
            "keep_english_terms": "Keep workspace terms in English",
            "ui_font_family": "Interface font", "ui_font_size": "Interface font size",
            "code_font_family": "Code and values font", "code_font_size": "Code and values font size",
            "ui_scale": "Interface scale", "default_quant": "Quantization", "default_overflow": "Overflow",
            "custom_signed_tpl": "Signed fractional template", "custom_unsigned_tpl": "Unsigned fractional template",
            "custom_int_tpl": "Integer template (FWL = 0)", "auto_import_groups": "Function packs",
            "custom_imports": "Custom imports",
            "numeric_display": "Number display", "numeric_precision": "Decimal places…",
            "inspector_view_mode": "Inspector layout", "inspector_pin_mode": "FXP pin",
            "bit_order": "Bit display order", "visible_columns": "Visible columns", "column_order": "Column order",
        }
        lines = []
        for key in sorted(values):
            if key == "startup_script":
                continue
            value = values[key] if key == "custom_imports" else new[key]
            if old[key] != value:
                lines.append(f"{self._text(labels[key])}: {old[key]!r} → {value!r}")
        changes.setPlainText("\n".join(lines) or self._text("No preference changes."))
        layout.addWidget(changes, 1)
        include_imports = QCheckBox(self._text("Include custom imports (runs after Apply)"), preview)
        include_imports.setChecked(False)
        include_imports.setEnabled("custom_imports" in values)
        layout.addWidget(include_imports)
        include = QCheckBox(self._text("Replace startup.py after Apply (does not run code)"), preview)
        include.setChecked(False)
        include.setEnabled("startup_script" in values)
        layout.addWidget(include)
        script = QPlainTextEdit(values.get("startup_script", ""), preview)
        script.setReadOnly(True)
        script.setPlaceholderText(self._text("No startup script in this file."))
        layout.addWidget(script, 1)
        buttons = QHBoxLayout()
        cancel = QPushButton(self._text("Cancel"), preview)
        accept = QPushButton(self._text("Import into draft"), preview)
        cancel.clicked.connect(preview.reject)
        accept.clicked.connect(preview.accept)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(accept)
        layout.addLayout(buttons)
        # Exposed for focused interaction tests and accessibility inspection.
        preview.include_startup = include
        preview.include_imports = include_imports
        preview.script_preview = script
        if preview.exec() == QDialog.DialogCode.Accepted:
            self._apply_import(values, include_startup=include.isChecked(), include_imports=include_imports.isChecked())
        preview.deleteLater()

    def _apply_import(self, values, *, include_startup=False, include_imports=False):
        merged = merge_preferences(self._draft_config(), values, include_startup=include_startup,
                                   include_imports=include_imports)
        if include_startup and "startup_script" in values:
            self._startup_pending = values["startup_script"]
            self.reload_startup_btn.setEnabled(False)
        self._update_controls(merged)

    def _restore_defaults(self):
        draft = self._draft_config(validate_imports=False)
        defaults = export_preferences(CalcConfig())["preferences"]
        self._startup_pending = None
        self._startup_enabled(bool(self.controller and self.controller.is_available))
        self._update_controls(merge_preferences(draft, defaults, include_imports=True))

    def _on_save(self):
        try:
            cfg = self._draft_config()
            if self._startup_pending is not None:
                self.startup_store.write(self._startup_pending, expected_revision=self._startup_revision)
            cfg.startup_script = ""
            self._working_cfg = cfg
        except (OSError, ValueError, UnicodeError) as exc:
            self._warning(str(exc))
            return
        self.accept()

    def result_config(self):
        return copy.deepcopy(self._working_cfg)
