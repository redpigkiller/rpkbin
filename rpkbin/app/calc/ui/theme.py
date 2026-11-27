"""Theme palette and QSS stylesheet generator for rpk-calc (Dark & Light themes)."""

from __future__ import annotations

from dataclasses import dataclass
import re

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPalette, QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QApplication

from ..core.config import CalcConfig


@dataclass(frozen=True)
class ThemePalette:
    name: str
    bg_main: str
    bg_panel: str
    bg_header: str
    bg_input: str
    bg_hover: str
    bg_selected: str
    border: str
    border_focus: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_bg: str
    danger: str
    danger_bg: str
    warning: str
    warning_bg: str
    success: str
    success_bg: str
    primary_bg: str
    primary_fg: str
    border_control: str = ""


DARK_PALETTE = ThemePalette(
    name="dark",
    bg_main="#0f141c",
    bg_panel="#151c28",
    bg_header="#1b2434",
    bg_input="#0b1017",
    bg_hover="#232f44",
    bg_selected="#1e3a5f",
    border="#28344a",
    border_focus="#38bdf8",
    text_primary="#e2e8f0",
    text_secondary="#b8c7da",
    text_muted="#9aa9bf",
    accent="#38bdf8",
    accent_bg="#0c2d48",
    danger="#fb8484",
    danger_bg="#3b181e",
    warning="#fbbf24",
    warning_bg="#362812",
    success="#34d399",
    success_bg="#102e25",
    primary_bg="#38bdf8", primary_fg="#071825",
    border_control="#687e9e",
)

LIGHT_PALETTE = ThemePalette(
    name="light",
    bg_main="#f1f5f9",
    bg_panel="#ffffff",
    bg_header="#f8fafc",
    bg_input="#ffffff",
    bg_hover="#e2e8f0",
    bg_selected="#dbeafe",
    border="#cbd5e1",
    border_focus="#0369a1",
    text_primary="#0f172a",
    text_secondary="#334155",
    text_muted="#414f63",
    accent="#0369a1",
    accent_bg="#e0f2fe",
    danger="#b91c1c",
    danger_bg="#fee2e2",
    warning="#854d0e",
    warning_bg="#fef3c7",
    success="#046c4e",
    success_bg="#d1fae5",
    primary_bg="#0369a1", primary_fg="#ffffff",
    border_control="#64748b",
)


def representation_colors(p: ThemePalette):
    """Stable representation accents; labels and values also identify each field."""
    if p.name == "light":
        return {"real": ("#1d4ed8", "#eff6ff"), "hex": ("#6d28d9", "#f5f3ff"),
                "s2": ("#0f766e", "#f0fdfa"), "uint": ("#4338ca", "#eef2ff"),
                "c1": ("#475569", "#f8fafc"), "bin": ("#155e75", "#ecfeff")}
    return {"real": ("#7dd3fc", "#172a40"), "hex": ("#c4b5fd", "#29213a"),
            "s2": ("#5eead4", "#17312e"), "uint": ("#a5b4fc", "#20263d"),
            "c1": ("#cbd5e1", "#212a39"), "bin": ("#67e8f9", "#15303b")}


def get_palette(theme_name: str = "dark") -> ThemePalette:
    return LIGHT_PALETTE if theme_name.lower() == "light" else DARK_PALETTE


def qss_family(family: str) -> str:
    return '"' + family.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ") + '"'


def font_stack(requested: str, *, code=False) -> str:
    """Keep the selected face first and provide outline CJK fallbacks explicitly."""
    primary = code_font_family(requested) if code else requested
    families = [primary]
    if not code:
        families.append("Segoe UI")
    families += ["Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans CJK TC",
                 "Noto Sans TC", "PingFang TC"]
    return ", ".join(qss_family(family) for family in dict.fromkeys(families))


def code_font_family(requested: str) -> str:
    """Use the platform's fixed font when a configured family is unavailable."""
    if QGuiApplication.instance() is None or requested in QFontDatabase.families():
        return requested
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def scaled_px(value: int, cfg: CalcConfig) -> int:
    """Scale fixed control sizes with both layout zoom and chosen font metrics."""
    return max(1, round(value * cfg.ui_scale / 100 * max(cfg.ui_font_size, cfg.code_font_size) / 10))


def qt_palette(p: ThemePalette) -> QPalette:
    result = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, p.bg_panel), (QPalette.ColorRole.WindowText, p.text_primary),
        (QPalette.ColorRole.Base, p.bg_input), (QPalette.ColorRole.AlternateBase, p.bg_main),
        (QPalette.ColorRole.Text, p.text_primary), (QPalette.ColorRole.Button, p.bg_header),
        (QPalette.ColorRole.ButtonText, p.text_primary), (QPalette.ColorRole.Highlight, p.bg_selected),
        (QPalette.ColorRole.HighlightedText, p.text_primary), (QPalette.ColorRole.ToolTipBase, p.bg_header),
        (QPalette.ColorRole.ToolTipText, p.text_primary), (QPalette.ColorRole.PlaceholderText, p.text_muted),
    ):
        result.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.text_muted))
    return result


def build_stylesheet(p: ThemePalette, cfg: CalcConfig | None = None) -> str:
    """Build the application-wide Qt Stylesheet from a ThemePalette."""
    cfg = cfg or CalcConfig()
    ctrl_border = p.border_control or p.border
    style = f"""
    QMainWindow, QDialog {{
        background-color: {p.bg_main};
        color: {p.text_primary};
        font-family: "Segoe UI", "Microsoft JhengHei UI", sans-serif;
        font-size: 13px;
    }}
    QWidget {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        font-family: "Segoe UI", "Microsoft JhengHei UI", sans-serif;
        font-size: 13px;
    }}
    QScrollArea, QScrollArea > QWidget > QWidget {{
        background-color: {p.bg_panel};
    }}
    QHeaderView, QTableCornerButton::section {{
        background-color: {p.bg_header};
        border: none;
    }}
    QLabel {{ background-color: transparent; }}
    QFrame#PanelFrame {{
        background-color: {p.bg_panel};
        border: 1px solid {p.border};
        border-radius: 6px;
    }}
    QFrame#PanelHeader {{
        background-color: {p.bg_header};
        border-bottom: 1px solid {p.border};
        border-top-left-radius: 5px;
        border-top-right-radius: 5px;
    }}
    QLabel#PanelTitle {{
        font-weight: 600;
        font-size: 12px;
        color: {p.text_secondary};
        letter-spacing: 0.4px;
    }}
    QLineEdit, QPlainTextEdit {{
        background-color: {p.bg_input};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 4px;
        padding: 4px 8px;
        font-family: "Cascadia Mono", "Consolas", monospace;
        selection-background-color: {p.accent};
        selection-color: {p.bg_input};
    }}
    QLineEdit:focus, QPlainTextEdit:focus {{
        border: 2px solid {p.border_focus};
    }}
    QLineEdit[readOnly="true"] {{
        background-color: {p.bg_header};
        color: {p.text_secondary};
    }}
    QToolButton, QPushButton {{
        background-color: transparent;
        color: {p.text_secondary};
        border: 1px solid transparent;
        border-radius: 4px;
        padding: 4px 6px;
    }}
    QToolButton:hover, QPushButton:hover {{
        background-color: {p.bg_hover};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
    }}
    QToolButton[calcRole="iconButton"] {{ padding: 0px; }}
    QToolButton:pressed, QPushButton:pressed {{ background: {p.bg_selected}; border: 1px solid {p.border_focus}; }}
    QToolButton:disabled, QPushButton:disabled {{ color: {p.text_muted}; }}
    QToolButton:checked, QPushButton:checked {{
        background-color: {p.accent_bg};
        color: {p.accent};
        border: 1px solid {p.accent};
    }}
    QToolButton:focus, QPushButton:focus {{
        border: 2px solid {p.border_focus};
    }}
    QToolButton:checked:focus, QPushButton:checked:focus {{
        border: 2px dotted {p.text_primary};
    }}
    QTableWidget, QTableView {{
        background-color: {p.bg_panel};
        alternate-background-color: {p.bg_main};
        gridline-color: {p.border};
        border: none;
        selection-background-color: {p.bg_selected};
        selection-color: {p.text_primary};
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 12px;
    }}
    QHeaderView::section {{
        background-color: {p.bg_header};
        color: {p.text_secondary};
        border: none;
        border-right: 1px solid {p.border};
        border-bottom: 1px solid {p.border};
        padding: 5px 8px;
        font-weight: 600;
        font-size: 12px;
    }}
    QTabWidget::pane {{
        border: 1px solid {p.border};
        background-color: {p.bg_panel};
        border-radius: 4px;
    }}
    QTabBar::tab {{
        background-color: {p.bg_header};
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-bottom: none;
        padding: 6px 14px;
        margin-right: 2px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border-top: 1px solid {p.accent};
    }}
    QMenu {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border: 1px solid {p.border};
        padding: 4px;
    }}
    QMenuBar {{
        background-color: {p.bg_header}; color: {p.text_primary};
        border-bottom: 1px solid {p.border}; padding: 2px;
    }}
    QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
    QMenuBar::item:selected {{ background: {p.bg_selected}; color: {p.accent}; }}
    QMenu::item {{
        padding: 5px 22px 5px 12px;
        border-radius: 3px;
    }}
    QMenu::item:selected {{
        background-color: {p.bg_selected};
        color: {p.accent};
    }}
    QSplitter::handle {{ background-color: {p.bg_main}; }}
    QSplitter::handle:horizontal {{ border-left: 1px solid {p.border}; border-right: 1px solid {p.border}; }}
    QSplitter::handle:vertical {{ border-top: 1px solid {p.border}; border-bottom: 1px solid {p.border}; }}
    QScrollBar:vertical {{
        background: {p.bg_main};
        width: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {p.text_muted};
        min-height: 20px;
        border-radius: 4px;
    }}
    QScrollBar:horizontal {{
        background: {p.bg_main};
        height: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p.text_muted};
        min-width: 20px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {p.text_secondary}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0px;
        height: 0px;
        border: none;
        background: transparent;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QAbstractScrollArea::corner {{ background: {p.bg_panel}; }}
    """
    style += f"""
    QLabel[calcRole="brand"] {{ font-weight: 600; font-size: 14px; color: {p.accent}; }}
    QLabel[calcRole="secondary"] {{ color: {p.text_secondary}; font-size: 12px; }}
    QLabel[calcRole="muted"] {{ color: {p.text_muted}; font-size: 11px; }}
    QLabel[calcRole="target"] {{ font-weight: 700; color: {p.accent}; }}
    QLabel[calcRole="badge"] {{ color: {p.text_secondary}; background: {p.bg_input};
        border: 1px solid {p.border}; border-radius: 4px; padding: 2px 8px; font-size: 11px; }}
    QFrame[calcRole="valueCard"] {{ background: {p.bg_header}; border: 1px solid {p.border}; border-radius: 5px; }}
    QFrame[calcRole="valueCard"][pinned="true"] {{ border-color: {p.border_focus}; }}
    QLabel[calcRole="cardTitle"] {{ font-size: 11px; font-weight: 600; color: {p.text_secondary}; border: none; }}
    QLabel[calcRole="danger"] {{
        color: {p.danger}; background: {p.danger_bg}; border-radius: 6px;
    }}
    QFrame#CalcNibble {{ background: transparent; border: none; }}
    QWidget#SimpleCalc QScrollBar:vertical {{ width: 7px; }}
    QWidget#SimpleCalc QScrollBar:horizontal {{ height: 7px; }}
    QLabel[calcRole="error"] {{ color: {p.danger}; background: {p.danger_bg};
        padding: 4px 8px; border-radius: 4px; font-size: 11px; }}
    QLabel[calcRole="status"] {{ color: {p.text_secondary}; font-size: 11px; padding: 2px 6px; border-radius: 3px; }}
    QLabel[calcRole="status"][statusKind="saturated"] {{ color: {p.danger}; background: {p.danger_bg}; border: 1px solid {p.danger}; font-weight: 700; }}
    QLabel[calcRole="status"][statusKind="quant_err"] {{ color: {p.warning}; background: {p.warning_bg}; border: 1px solid {p.warning}; }}
    QLabel[calcRole="status"][statusKind="exact"] {{ color: {p.success}; background: {p.success_bg}; }}
    QScrollArea[calcRole="bits"] {{ background: {p.bg_input}; border: 1px solid {p.border}; border-radius: 5px; }}
    QLabel[calcRole="bitIndex"] {{ font-size: 10px; color: {p.text_secondary}; }}
    QLabel[calcRole="bitIndex"][bitRole="sign"] {{ color: {p.danger}; font-weight: 700; }}
    QLabel[calcRole="bitIndex"][bitRole="frac"] {{ color: {p.accent}; }}
    QLabel[calcRole="bitIndex"][bitRole="exponent"] {{ color: {p.warning}; }}
    QLabel[calcRole="radix"] {{ font-size: 18px; font-weight: 900; color: {p.warning}; padding: 10px 2px 0px 2px; }}
    QPushButton[calcRole="bit"] {{ background: {p.bg_header}; color: {p.text_secondary};
        border: 1px solid {p.border}; border-radius: 4px; padding: 0px; font-weight: 700; }}
    QPushButton[calcRole="bit"][bitRole="sign"] {{ border-color: {p.danger}; }}
    QPushButton[calcRole="bit"][bitValue="1"][bitRole="sign"] {{ background: {p.danger_bg}; color: {p.danger}; }}
    QPushButton[calcRole="bit"][bitValue="1"][bitRole="frac"] {{ background: {p.accent_bg}; color: {p.accent}; border-color: {p.accent}; }}
    QPushButton[calcRole="bit"][bitValue="1"][bitRole="int"] {{ background: {p.warning_bg}; color: {p.text_primary}; border-color: {p.warning}; }}
    QPushButton[calcRole="bit"][bitValue="1"][bitRole="exponent"] {{ background: {p.warning_bg}; color: {p.text_primary}; border-color: {p.warning}; }}
    QPushButton[calcRole="bit"]:hover {{ border-color: {p.text_primary}; }}
    QFrame[calcRole="custom"] {{ background: {p.bg_header}; border: 1px solid {p.border}; border-radius: 5px; }}
    QLabel[calcRole="preview"] {{ background: {p.bg_input}; color: {p.accent}; border: 1px solid {p.border}; border-radius: 4px; padding: 8px; }}
    QLabel[calcRole="status"][statusKind="plain"] {{ color: {p.primary_bg}; font-weight: 700; }}
    QLabel[calcRole="sectionLabel"] {{ color: {p.text_primary}; font-weight: 700;
        border-bottom: 1px solid {p.border}; padding: 2px 0px; }}
    QFrame[calcRole="valueCard"] QLineEdit {{ padding: 2px 6px; }}
    QPushButton[calcRole="primary"] {{ background: {p.primary_bg}; color: {p.primary_fg};
        border: 1px solid {p.primary_bg}; font-weight: 700; padding: 5px 12px; }}
    QPushButton[calcRole="primary"]:hover {{ background: {p.primary_bg}; color: {p.primary_fg}; border: 1px solid {p.primary_fg}; }}
    QPushButton[calcRole="primary"]:pressed {{ background: {p.primary_bg}; color: {p.primary_fg}; border: 2px solid {p.primary_fg}; }}
    QPushButton[calcRole="primary"]:focus {{ border: 2px dotted {p.primary_fg}; }}
    QPushButton[calcRole="primary"]:disabled {{ background: {p.bg_header}; color: {p.text_muted}; border: 1px solid {p.border}; }}
    QToolButton[calcRole="iconButton"][copyFeedback="true"] {{ background: {p.success_bg}; border: 1px solid {p.success}; }}
    QToolTip {{ color: {p.text_primary}; background: {p.bg_header}; border: 1px solid {p.border}; }}
    """
    for field, (accent, background) in representation_colors(p).items():
        style += f"""
        QFrame[calcRole="valueCard"][representation="{field}"] {{
            background: {background}; border-left: 3px solid {accent}; }}
        QFrame[calcRole="valueCard"][representation="{field}"] QLabel[calcRole="cardTitle"] {{ color: {accent}; }}
        """
    # Typography uses points throughout; only layout dimensions use scaled pixels.
    # Keep a small, consistent body/caption hierarchy instead of rounding font pixels.
    def font_size(match):
        authored = int(match[1])
        ratio = {10: .8, 11: .9, 12: .9, 13: 1., 14: 1.1}.get(authored, authored / 13)
        return f"font-size: {cfg.ui_font_size * ratio * cfg.ui_scale / 100:g}pt"
    style = re.sub(r"font-size: (\d+)px", font_size, style)
    style = re.sub(r"(\d+(?:\.\d+)?)px", lambda m: f"{max(1, round(float(m[1]) * cfg.ui_scale / 100)) if float(m[1]) else 0}px", style)
    ui_family = font_stack(cfg.ui_font_family)
    style = style.replace('"Segoe UI", "Microsoft JhengHei UI", sans-serif', ui_family)
    code_family = font_stack(cfg.code_font_family, code=True)
    style = style.replace('"Cascadia Mono", "Consolas", monospace', code_family)
    code_size = cfg.code_font_size * cfg.ui_scale / 100
    ui_size = cfg.ui_font_size * cfg.ui_scale / 100
    style += f"""
    QComboBox, QSpinBox, QHeaderView, QHeaderView::section {{
        font-family: {ui_family}; font-size: {ui_size:g}pt;
    }}
    QLabel[calcRole="settingsHint"] {{ color: {p.text_secondary};
        background: {p.bg_header}; border-left: 2px solid {p.border};
        border-radius: 3px; padding: 4px 6px;
        font-size: {ui_size * .9:g}pt; font-weight: 400;
    }}
    QSplitter::handle:hover {{ background: {p.bg_selected}; }}
    QLineEdit, QPlainTextEdit, QTableWidget, QTableView, QWidget[calcCode="true"] {{
        font-family: {code_family}; font-size: {code_size:g}pt;
    }}
    QLineEdit[calcRole="search"], QFontComboBox, QFontComboBox QLineEdit {{
        font-family: {ui_family}; font-size: {ui_size:g}pt;
    }}
    QFrame#TopShellBar {{
        background-color: {p.bg_header};
        border-bottom: 1px solid {p.border};
    }}
    QFrame[calcRole="segmentContainer"] {{
        background-color: {p.bg_main};
        border: 1px solid {p.border};
        border-radius: 7px;
    }}
    QPushButton[calcRole="segmentBtn"], QToolButton[calcRole="segmentIconBtn"] {{
        background-color: transparent;
        color: {p.text_secondary};
        border: 1px solid transparent;
        border-radius: 5px;
        padding: 2px 7px;
        min-height: 20px;
        font-weight: 600;
    }}
    QFrame#TopShellBar QPushButton[calcRole="segmentBtn"] {{
        padding: 2px 7px;
    }}
    QToolButton[calcRole="segmentIconBtn"] {{
        padding: 2px;
        min-width: 22px;
        min-height: 22px;
    }}
    QPushButton[calcRole="segmentBtn"]:hover, QToolButton[calcRole="segmentIconBtn"]:hover {{
        color: {p.text_primary};
        background-color: {p.bg_hover};
    }}
    QPushButton[calcRole="segmentBtn"]:checked, QToolButton[calcRole="segmentIconBtn"]:checked {{
        background-color: {p.primary_bg};
        color: {p.primary_fg};
        border: 1px solid {p.primary_bg};
    }}
    QPushButton[calcRole="segmentBtn"]:focus, QToolButton[calcRole="segmentIconBtn"]:focus {{
        border: 2px solid {p.border_focus};
    }}
    QPushButton[calcRole="segmentBtn"]:checked:focus, QToolButton[calcRole="segmentIconBtn"]:checked:focus {{
        border: 2px dotted {p.primary_fg};
    }}
    QWidget#SimpleCalc, QWidget#SimpleLeftPane {{
        background-color: {p.bg_main};
    }}
    QSplitter#SimpleMainSplitter::handle {{
        background-color: {p.border};
    }}
    QSplitter#TopDisplaySplitter::handle {{
        background-color: {p.border};
    }}
    QSplitter#LeftVerticalSplitter::handle {{
        background-color: {p.border};
        border-top: 1px solid {p.bg_panel};
        border-bottom: 1px solid {p.bg_main};
    }}
    QSplitter#LeftVerticalSplitter::handle:hover {{
        background-color: {p.primary_bg};
    }}
    QFrame#TopDisplayFrame {{
        background-color: {p.bg_panel};
        border-bottom: 1px solid {p.border};
    }}
    QFrame#HeroScreen {{
        background-color: {p.bg_main};
    }}
    QLineEdit#CalcInput {{
        background: transparent;
        border: none;
        font-family: {code_family};
        font-weight: 700;
        color: {p.text_primary};
        padding: 0px 2px;
    }}
    QLineEdit#CalcInput[calcAlert="true"] {{
        color: {p.danger};
        background: transparent;
    }}
    QLabel#HeroPrefix {{
        font-family: {code_family};
        font-size: {code_size * 0.9:g}pt;
        font-weight: 700;
        color: {p.text_secondary};
        background-color: {p.bg_panel};
        border: 1px solid {p.border};
        border-radius: 4px;
        padding: 1px 6px;
    }}
    QPushButton#HeroFmtBadge {{
        font-family: {code_family};
        font-size: {ui_size * 0.85:g}pt;
        font-weight: 600;
        color: {p.text_primary};
        background-color: {p.bg_panel};
        border: 1px solid {p.border};
        border-radius: 4px;
        padding: 1px 6px;
    }}
    QPushButton#HeroFmtBadge:hover {{
        border-color: {p.border_focus};
        background-color: {p.bg_hover};
    }}
    QLabel#QuantRuleBadge {{
        font-family: {code_family};
        font-size: {ui_size * 0.85:g}pt;
        color: {p.text_secondary};
        background-color: {p.bg_header};
        border: 1px solid {p.border};
        border-radius: 4px;
        padding: 1px 6px;
    }}
    QLabel#ClampBadge {{
        font-size: {ui_size * 0.85:g}pt;
        font-weight: 700;
        color: {p.danger};
        background-color: {p.danger_bg};
        border: 1px solid {p.danger};
        border-radius: 4px;
        padding: 2px 6px;
    }}
    QFrame#BaseRowsContainer {{
        background-color: {p.bg_panel};
        border: none;
    }}
    QScrollArea#BaseScrollArea, QScrollArea#BaseScrollArea > QWidget > QWidget,
    QScrollArea#CompareScrollArea, QScrollArea#CompareScrollArea > QWidget > QWidget {{
        background-color: transparent;
        border: none;
    }}
    QLineEdit[calcRole="compareFmtEdit"] {{
        background-color: {p.bg_input};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: 4px;
        font-family: {code_family};
        font-size: {ui_size * 0.8:g}pt;
        padding: 1px 4px;
    }}
    QLineEdit[calcRole="compareFmtEdit"]:focus {{
        border: 1px solid {p.border_focus};
    }}
    QFrame[calcRole="baseRow"] {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 5px;
    }}
    QWidget[calcRole="baseRowRight"] {{
        background: transparent;
    }}
    QFrame[calcRole="baseRow"]:hover {{
        background-color: {p.bg_hover};
    }}
    QFrame[calcRole="baseRow"][activeBase="true"] {{
        background-color: {p.bg_selected};
        border: 1px solid {p.border};
    }}
    QFrame[calcRole="baseRow"][draggingRow="true"] {{
        background-color: {p.bg_hover};
        border: 1px solid {p.border_focus};
    }}
    QFrame[calcRole="baseIndicator"] {{
        background-color: transparent;
        border-radius: 2px;
    }}
    QFrame[calcRole="baseIndicator"][activeBase="true"] {{
        background-color: {p.primary_bg};
    }}
    QPushButton[calcRole="baseLabelBtn"] {{
        background: transparent;
        border: none;
        font-weight: 700;
        font-size: {ui_size * 0.88:g}pt;
        color: {p.text_secondary};
        text-align: left;
        padding: 1px 3px;
    }}
    QPushButton[calcRole="baseLabelBtn"]:checked {{
        color: {p.text_primary};
        background: transparent;
        border: none;
    }}
    QLineEdit[calcRole="baseValueEdit"] {{
        background: transparent;
        border: none;
        font-family: {code_family};
        font-size: {code_size * 0.92:g}pt;
        color: {p.text_primary};
        padding: 0px 3px;
    }}
    QLineEdit[calcRole="baseValueEdit"][activeBase="true"] {{
        font-weight: 700;
    }}
    QLineEdit[calcRole="baseValueEdit"][calcAlert="true"] {{
        color: {p.danger};
    }}
    QLabel[calcRole="baseSubTag"] {{
        font-family: {code_family};
        font-size: {ui_size * 0.8:g}pt;
        color: {p.text_muted};
        background-color: {p.bg_main};
        border: 1px solid {p.border};
        border-radius: 4px;
        padding: 0px 5px;
    }}
    QLabel[calcRole="baseSubTag"][clickableTag="true"]:hover {{
        color: {p.text_primary};
        border-color: {p.border_focus};
        background-color: {p.bg_hover};
    }}
    QLabel[calcRole="baseSubTag"][activeBase="true"] {{
        color: {p.text_secondary};
    }}
    QFrame#MidControlBar {{
        background-color: {p.bg_header};
        border-bottom: 1px solid {p.border};
    }}
    QFrame#FxpConfigDrawer {{
        background-color: {p.bg_main};
        border-bottom: 1px solid {p.border};
    }}
    QFrame#FxpConfigDrawer QLineEdit {{
        padding: 3px 8px;
    }}
    QFrame#FxpConfigDrawer QPushButton[calcRole="primary"] {{
        padding: 3px 10px;
    }}
    QPushButton[calcRole="barActionBtn"] {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 6px;
        padding: 2px 5px;
        min-height: 22px;
        font-weight: 600;
    }}
    QPushButton[calcRole="barActionBtn"]:hover {{
        background-color: {p.bg_hover};
        border-color: {p.border_focus};
    }}
    QPushButton[calcRole="barActionBtn"]:focus {{
        border: 2px solid {p.border_focus};
    }}
    QPushButton[calcRole="shiftModeBtn"] {{
        background-color: {p.bg_panel};
        color: {p.text_muted};
        border: 1px solid {ctrl_border};
        border-radius: 5px;
        padding: 1px 4px;
        font-weight: 600;
    }}
    QPushButton[calcRole="shiftModeBtn"]:hover {{
        background-color: {p.bg_hover};
        color: {p.text_primary};
        border-color: {p.border_focus};
    }}
    QPushButton[calcRole="shiftModeBtn"]:checked {{
        background-color: {p.accent_bg};
        color: {p.accent};
        border-color: {p.accent};
    }}
    QPushButton[calcRole="shiftModeBtn"]:focus {{
        border: 2px solid {p.border_focus};
    }}
    QPushButton[calcRole="shiftModeBtn"]:checked:focus {{
        border: 2px dotted {p.text_primary};
    }}
    QComboBox QLineEdit {{
        border: none;
        background: transparent;
        padding: 0px;
        margin: 0px;
    }}
    QComboBox#SimpleFormatCombo {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 6px;
        padding: 2px 16px 2px 6px;
        font-family: {code_family};
        font-size: {code_size * 0.92:g}pt;
    }}
    QComboBox#SimpleFormatCombo:hover,
    QComboBox#SimpleFormatCombo:focus {{
        border: 2px solid {p.border_focus};
        background-color: {p.bg_hover};
    }}
    QComboBox#SimpleFormatCombo::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border: none;
        background: transparent;
    }}
    QComboBox#SimpleFormatCombo::down-arrow {{
        image: none;
        border: none;
        width: 0px;
        height: 0px;
    }}
    QComboBox#SimpleFormatCombo QAbstractItemView {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border: 1px solid {p.border};
        selection-background-color: {p.bg_selected};
        selection-color: {p.text_primary};
        outline: 0;
        padding: 2px;
    }}
    QWidget#CalcKeypad {{
        background-color: {p.bg_main};
    }}
    QWidget#CalcKeypad QPushButton[calcKey="bitwise"] {{
        background-color: {p.bg_panel};
        color: {p.text_secondary};
        border: 1px solid {ctrl_border};
        border-radius: 6px;
        font-family: {code_family};
        font-weight: 600;
        padding: 1px 2px;
    }}
    QWidget#CalcKeypad QPushButton[calcKey="digit"],
    QWidget#CalcKeypad QPushButton[calcKey="hex"] {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 6px;
        font-family: {code_family};
        font-weight: 700;
        padding: 1px 2px;
    }}
    QWidget#CalcKeypad QPushButton[calcKey="op"],
    QWidget#CalcKeypad QPushButton[calcKey="action"] {{
        background-color: {p.bg_header};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 6px;
        font-family: {code_family};
        font-weight: 600;
        padding: 1px 2px;
    }}
    QWidget#CalcKeypad QPushButton:hover {{
        background-color: {p.bg_hover};
        border-color: {p.border_focus};
    }}
    QWidget#CalcKeypad QPushButton:disabled {{
        background-color: {p.bg_main};
        color: {p.text_muted};
        border: 1px solid {p.border};
    }}
    QWidget#CalcKeypad QPushButton[calcRole="primary"] {{
        background-color: {p.primary_bg};
        color: {p.primary_fg};
        border: 1px solid {p.primary_bg};
        border-radius: 6px;
        font-family: {code_family};
        font-weight: 700;
        padding: 1px 2px;
    }}
    QFrame#BitPanel {{
        background-color: {p.bg_main};
        border-bottom: 1px solid {p.border};
    }}
    QToolButton[calcRole="simpleBit"] {{
        background-color: {p.bg_panel};
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-radius: 4px;
        font-family: {code_family};
        font-size: {code_size * 0.9:g}pt;
        font-weight: 700;
        padding: 0px;
    }}
    QToolButton[calcRole="simpleBit"][bitRole="sign"] {{
        border-color: {p.danger};
        color: {p.danger};
    }}
    QToolButton[calcRole="simpleBit"][bitRole="sign"][bitValue="1"] {{
        background-color: {p.danger_bg};
        color: {p.danger};
        border-color: {p.danger};
    }}
    QToolButton[calcRole="simpleBit"][bitRole="int"] {{
        border-color: {p.warning};
    }}
    QToolButton[calcRole="simpleBit"][bitRole="int"][bitValue="1"] {{
        background-color: {p.warning_bg};
        color: {p.text_primary};
        border-color: {p.warning};
    }}
    QToolButton[calcRole="simpleBit"][bitRole="frac"] {{
        border-color: {p.accent};
    }}
    QToolButton[calcRole="simpleBit"][bitRole="frac"][bitValue="1"] {{
        background-color: {p.accent_bg};
        color: {p.accent};
        border-color: {p.accent};
    }}
    QFrame#SimpleSidebar {{
        background-color: {p.bg_header};
        border-left: 2px solid {p.border};
    }}
    QFrame#SidebarHeader {{
        background-color: {p.bg_header};
        border-bottom: 1px solid {p.border};
    }}
    QFrame#SidebarFilterBar {{
        background-color: {p.bg_panel};
        border-bottom: 1px solid {p.border};
    }}
    QFrame[calcRole="customGroupCard"] {{
        background-color: {p.bg_panel};
        border: 1px solid {p.border};
        border-radius: 6px;
    }}
    QPushButton[calcRole="startupToggleBtn"] {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 5px;
        padding: 5px 10px;
        text-align: left;
        font-weight: 600;
    }}
    QPushButton[calcRole="startupToggleBtn"]:hover {{
        background-color: {p.bg_hover};
        border-color: {p.border_focus};
    }}
    QPushButton[calcRole="startupToggleBtn"]:focus {{
        border: 2px solid {p.border_focus};
    }}
    QPushButton[calcRole="startupToggleBtn"]:checked:focus {{
        border: 2px dotted {p.text_primary};
    }}
    QPushButton[calcRole="varModeBtn"] {{
        background-color: {p.bg_main};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 5px;
        padding: 2px 9px;
        min-height: 20px;
        font-size: {ui_size * 0.85:g}pt;
        font-weight: 600;
    }}
    QPushButton[calcRole="varModeBtn"]:hover {{
        background-color: {p.bg_hover};
        border-color: {p.border_focus};
    }}
    QPushButton[calcRole="varModeBtn"]:focus {{
        border: 2px solid {p.border_focus};
    }}
    QPushButton[calcRole="varModeBtn"]:disabled {{
        color: {p.text_muted};
        background-color: {p.bg_panel};
        border-color: {p.border};
    }}
    QTableWidget#SimpleVarTable {{
        background-color: {p.bg_panel};
        alternate-background-color: {p.bg_main};
        gridline-color: {p.border};
        border: none;
        selection-background-color: {p.bg_selected};
        selection-color: {p.text_primary};
        font-family: {code_family};
        font-size: {code_size * 0.88:g}pt;
        outline: 0;
    }}
    QTableWidget#SimpleVarTable::item {{
        padding: 2px 6px;
    }}
    QTableWidget#SimpleVarTable::item:selected {{
        background-color: {p.bg_selected};
        color: {p.text_primary};
    }}
    QTableWidget#SimpleVarTable QHeaderView::section {{
        background-color: {p.bg_header};
        color: {p.text_secondary};
        border: none;
        border-right: 1px solid {p.border};
        border-bottom: 1px solid {p.border};
        padding: 3px 6px;
        font-family: {ui_family};
        font-size: {ui_size * 0.85:g}pt;
        font-weight: 600;
    }}
    QPushButton[calcRole="cardBtn"] {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 5px;
        padding: 2px 7px;
        min-height: 20px;
        font-size: {ui_size * 0.85:g}pt;
        font-weight: 600;
    }}
    QPushButton[calcRole="cardBtn"]:hover {{
        background-color: {p.bg_hover};
        border-color: {p.border_focus};
    }}
    QPushButton[calcRole="cardBtn"]:focus {{
        border: 2px solid {p.border_focus};
    }}
    QPushButton[calcRole="histCard"] {{
        background-color: {p.bg_panel};
        color: {p.text_primary};
        border: 1px solid {ctrl_border};
        border-radius: 7px;
        padding: 6px 8px;
        text-align: left;
        font-family: {code_family};
    }}
    QPushButton[calcRole="histCard"]:hover {{
        background-color: {p.bg_hover};
        border-color: {p.border_focus};
    }}
    QPushButton[calcRole="histCard"]:focus {{
        border: 2px solid {p.border_focus};
    }}
    """
    return style


def set_role(widget, role: str, *, code=False):
    widget.setProperty("calcRole", role)
    if code:
        widget.setProperty("calcCode", True)


def repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class _ShiftHorizontalWheelFilter(QObject):
    """Redirect Shift+Wheel vertical delta to a scroll area's horizontal scrollbar."""

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        area = self.parent()
        if not isinstance(area, QAbstractScrollArea):
            return False
        if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            return False
        hbar = area.horizontalScrollBar()
        if (
            hbar is None
            or area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            or hbar.maximum() <= hbar.minimum()
        ):
            return False
        angle = event.angleDelta()
        pixel = event.pixelDelta()
        dy = angle.y()
        py = pixel.y()
        if (angle.x() != 0 or pixel.x() != 0) or (dy == 0 and py == 0):
            return False
        horizontal_event = QWheelEvent(
            event.position(),
            event.globalPosition(),
            QPoint(py, 0),
            QPoint(dy, 0),
            event.buttons(),
            event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier,
            event.phase(),
            event.inverted(),
        )
        QApplication.sendEvent(hbar, horizontal_event)
        event.accept()
        return True


def install_shift_horizontal_wheel(area: QAbstractScrollArea):
    """Install a Shift+Wheel horizontal scroll filter on ``area`` and its viewport."""
    existing = getattr(area, "_shift_hwheel_filter", None)
    if existing is not None:
        return existing
    filt = _ShiftHorizontalWheelFilter(area)
    area._shift_hwheel_filter = filt
    area.viewport().installEventFilter(filt)
    area.installEventFilter(filt)
    return filt
