"""Explicit application-owned translations; user content is never translated."""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en_US": {
        "app_title": "rpk-calc — Engineering & Fixed-Point Calculator",
        "command_window": "Command Window",
        "workspace": "Workspace",
        "inspector": "Inspector",
        "settings_tooltip": "Settings\nAppearance, fonts, FXP defaults and auto-import.",
        "reset_tooltip": "Restart kernel and clear variables",
        "select_columns_tooltip": "Select Visible Columns (or right-click header)",
        "clear_workspace_tooltip": "Clear All Workspace Variables (clear)",
        "list_view_tooltip": "List View Mode (1-column vertical layout)",
        "grid_view_tooltip": "Grid View Mode (2-column compact layout)",
        "pin_real_tooltip": "Global FXP conversion preference: preserve numeric value; applies to the entire array",
        "pin_hex_tooltip": "Global FXP conversion preference: reinterpret raw bits; applies to the entire array",
        "copy_tooltip": "Copy value to clipboard",
        "invert_bits_tooltip": "Invert All Bits (~x)",
        "zero_bits_tooltip": "Zero All Bits (0)",
        "swap_bit_order_tooltip": "Toggle Bit Order (MSB -> LSB ⇄ LSB -> MSB)",
        "col_name": "Name",
        "col_value": "Value",
        "col_format": "Format",
        "col_hex": "Hex",
        "col_s2": "2's Int",
        "col_uint": "UInt",
        "col_c1": "1's Comp",
        "col_bin": "Binary",
        "col_wl_info": "Bits (WL/IWL/FWL)",
        "col_lsb_range": "LSB & Range",
        "col_shape": "Shape",
        "col_minmax": "Min .. Max",
        "tip_name": "Variable Name",
        "tip_value": "Real Value or Array Preview",
        "tip_format": "FXP display notation (Settings). Qm.n includes sign in m; total bits = m + n.",
        "tip_hex": "Hardware Raw Bits in Hexadecimal",
        "tip_s2": "Signed 2's Complement Integer",
        "tip_uint": "Unsigned Integer",
        "tip_c1": "1's Complement Integer",
        "tip_bin": "Raw Binary Bit String",
        "tip_wl_info": "WL: Word Length / IWL: Integer Word Length / FWL: Fractional Word Length",
        "tip_lsb_range": "LSB step size (2^-FWL) and representable [Min, Max] range",
        "tip_shape": "Matrix / Array Dimensions",
        "tip_minmax": "Minimum and Maximum elements",
        "ctx_open_matrix": "Open in Variable Editor",
        "ctx_inspect": "Inspect Variable",
        "ctx_copy_real": "Copy Real Value",
        "ctx_copy_hex": "Copy Hex",
        "ctx_copy_s2": "Copy 2's Int",
        "ctx_copy_bin": "Copy Binary",
        "ctx_rename": "Rename... (F2)",
        "ctx_duplicate": "Duplicate Variable",
        "ctx_to_fxp": "Convert to FXP...",
        "ctx_to_float": "Convert to Float",
        "ctx_delete": "Delete Variable (Del)",
        "lbl_real_value": "Real Value",
        "lbl_hex_value": "Hex (Raw Bits)",
        "lbl_s2_value": "2's Comp (Signed Int)",
        "lbl_uint_value": "Unsigned Int",
        "lbl_c1_value": "1's Comp",
        "no_var_selected": "Select a variable in the Workspace above to inspect or edit its FXP & bits",
    },
}



# Only exact application-owned label templates belong here. Explanations and
# action text still follow the UI language; interpolated user data is untouched.
ENGLISH_WORKSPACE_TERMS = frozenset({
    "Command Window", "Workspace", "Inspector", "Name", "Value", "Format",
    "Hex", "2's Int", "UInt", "1's Comp", "Binary", "Bits (WL/IWL/FWL)",
    "LSB & Range", "Shape", "Min .. Max", "Real Value", "Hex (Raw Bits)",
    "2's Comp (Signed Int)", "Unsigned Int",
    "Type:", "Format:", "FXP format", "Array format:", "Array format", "Dtype:",
    "Real", "2's", "Bits (MSB → LSB)", "Bits (LSB → MSB)",
    "Sign", "Integer", "Fraction", "Exponent", "exponent", "int", "frac", "Col {index}", "Row {index}",
    "Hex (integer value)", "All types", "Arrays", "Scalars", "Filter variable names",
    "Columns", "Open array", "{visible} / {total} shown",
    "{name} shape={shape}",
})


def t(source: str, lang: str = "en_US", *, keep_english_terms: bool = False, **values) -> str:
    """Translate a stable source template, then insert unmodified user values."""
    if lang == "zh_TW" and not (keep_english_terms and source in ENGLISH_WORKSPACE_TERMS):
        from .translations import app, console, inspector, matrix, settings, workspace
        for module in (app, console, inspector, matrix, settings, workspace):
            if source in module.ZH_TW:
                source = module.ZH_TW[source]
                break
    return source.format(**values) if values else source


def tr(key: str, lang: str = "en_US", *, keep_english_terms: bool = False) -> str:
    return t(STRINGS["en_US"].get(key, key), lang, keep_english_terms=keep_english_terms)


class UiText:
    """Explicit bindings for app-owned labels/actions, never a widget-tree walker.

    Dynamic data stays outside this registry and is rendered by its owning view.
    A refresh only calls registered setters; it does not rebuild any widgets.
    """

    def __init__(self, lang="en_US", *, keep_english_terms=False):
        self.lang = lang
        self.keep_english_terms = keep_english_terms
        self.bindings = []

    def __call__(self, source, **values):
        return t(source, self.lang, keep_english_terms=self.keep_english_terms, **values)

    def bind(self, target, source, method="setText"):
        self.bindings.append((target, source, method))
        getattr(target, method)(self(source))
        return target

    def refresh(self, lang, *, keep_english_terms=None):
        self.lang = lang
        if keep_english_terms is not None:
            self.keep_english_terms = keep_english_terms
        for target, source, method in self.bindings:
            getattr(target, method)(self(source))


def ask(parent, title, text, buttons, default, lang="en_US", *, icon=None):
    """A question whose application buttons follow the selected UI language."""
    from .theme import repolish
    from PySide6.QtWidgets import QDialogButtonBox, QMessageBox
    from PySide6.QtCore import Qt

    box = QMessageBox(parent)
    native_option = getattr(getattr(QMessageBox, "Option", None), "DontUseNativeDialog", None)
    if native_option is not None:  # Available since Qt 6.6; retain the declared 6.5 minimum.
        box.setOption(native_option, True)
    if parent is not None:
        box.setStyleSheet(parent.window().styleSheet())
    box.setIcon(QMessageBox.Icon.Question if icon is None else icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(buttons)
    if buttons == (QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel):
        # Set the child policy directly so an inherited Windows layout is rebuilt.
        # KDE ordering puts Apply before Cancel, preserving standard return values.
        box.findChild(QDialogButtonBox).setStyleSheet("QDialogButtonBox { button-layout: 2; }")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setDefaultButton(default)
    for name in ("Yes", "No", "Apply", "Cancel", "Ok", "Close"):
        button = box.button(getattr(QMessageBox.StandardButton, name))
        if button is not None:
            button.setText(t("OK" if name == "Ok" else name, lang))
            if name == "Apply":
                button.setProperty("calcRole", "primary")
                repolish(button)
    return box.exec()


def notify(parent, title, text, lang="en_US", *, warning=False):
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtCore import Qt

    icon = QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information
    box = QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.button(QMessageBox.StandardButton.Ok).setText(t("OK", lang))
    box.exec()


def prompt_text(parent, title, label, lang="en_US"):
    from PySide6.QtWidgets import QDialog, QInputDialog

    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setOkButtonText(t("OK", lang))
    dialog.setCancelButtonText(t("Cancel", lang))
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.textValue(), accepted
