"""Consistent monochrome line icons rendered from SVG."""

# Includes adapted Feather (MIT) and Lucide (ISC) icon geometry.
# Copyright and license texts are in THIRD_PARTY_NOTICES.md at the repo root
# and in the installed distribution's .dist-info/licenses directory.

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .theme import scaled_px


_SVG_PATHS: dict[str, str] = {
    "undo": '<path d="M9 4 4 9l5 5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "redo": '<path d="m15 4 5 5-5 5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
    "history": '<path d="M3 4v6h6"/><path d="M3.5 10a9 9 0 1 1 1 7"/><path d="M12 7v5l3 2"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4.3 1.7L12 12v1"/><path d="M12 17h.01"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "paste": '<rect x="5" y="5" width="14" height="16" rx="2"/><rect x="9" y="3" width="6" height="4" rx="1"/>',
    "preview": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h6M14 2v6h6M14 2l6 6v3"/><circle cx="16" cy="16" r="4"/><path d="m19 19 3 3"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "statistics": '<path d="M19 4H5l7 8-7 8h14"/>',
    "copy_table": '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M3 8h14M8 3v14M12 21h7a2 2 0 0 0 2-2v-7"/>',
    "open_array": '<rect x="3" y="8" width="13" height="13" rx="2"/><path d="M3 13h13M8 8v13M14 3h7v7M21 3l-9 9"/>',
    # Gear / Settings
    "settings": (
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06'
        'a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09'
        'A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06'
        'a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09'
        'A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06'
        'a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09'
        'a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06'
        'a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09'
        'a1.65 1.65 0 0 0-1.51 1z"/>'
    ),
    # Counter-clockwise Reset arrow
    "reset": (
        '<polyline points="1 4 1 10 7 10"/>'
        '<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>'
    ),
    # Table header and a checked visible column.
    "columns": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/>'
        '<path d="M3 8h18M10 3v18m3-6 2 2 4-5"/>'
    ),
    # Trash / Clear Workspace
    "trash": (
        '<polyline points="3 6 5 6 21 6"/>'
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
        '<line x1="10" y1="11" x2="10" y2="17"/>'
        '<line x1="14" y1="11" x2="14" y2="17"/>'
        '<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'
    ),
    # List View (horizontal bars inside frame)
    "list_view": (
        '<line x1="8" y1="6" x2="21" y2="6"/>'
        '<line x1="8" y1="12" x2="21" y2="12"/>'
        '<line x1="8" y1="18" x2="21" y2="18"/>'
        '<line x1="3" y1="6" x2="3.01" y2="6"/>'
        '<line x1="3" y1="12" x2="3.01" y2="12"/>'
        '<line x1="3" y1="18" x2="3.01" y2="18"/>'
    ),
    # Grid View (2x2 geometric squares)
    "grid_view": (
        '<rect x="3" y="3" width="7" height="7" rx="1"/>'
        '<rect x="14" y="3" width="7" height="7" rx="1"/>'
        '<rect x="14" y="14" width="7" height="7" rx="1"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1"/>'
    ),
    # Geometric Thumbtack / Pin icon
    "pin": (
        '<path d="M12 17v5"/>'
        '<path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12'
        'a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1'
        ' 1 1 0 0 0 1-1V4a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v1a1 1 0 0 0 1 1 1 1 0 0 1 1 1z"/>'
    ),
    # Copy (overlapping rectangles)
    "copy": (
        '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
    ),
    # Swap horizontal arrows (MSB <-> LSB)
    "swap": (
        '<polyline points="17 2 21 6 17 10"/>'
        '<line x1="3" y1="6" x2="21" y2="6"/>'
        '<polyline points="7 22 3 18 7 14"/>'
        '<line x1="21" y1="18" x2="3" y2="18"/>'
    ),
}


def make_icon(name: str, color: str = "#94a3b8", size: int = 16) -> QIcon:
    """Render SVG at multiple device ratios so icons stay sharp across screens."""
    inner = _SVG_PATHS[name]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="1.7" '
        f'stroke-linecap="round" stroke-linejoin="round">{inner}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    icon = QIcon()
    for ratio in (1, 2, 3):
        pix = QPixmap(size * ratio, size * ratio)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, pix.width(), pix.height()))
        painter.end()
        pix.setDevicePixelRatio(ratio)
        icon.addPixmap(pix)
    return icon


def style_icon_button(button, name: str, tooltip: str, color: str, cfg, *, label: str | None = None) -> None:
    """One toolbar icon style, with a named keyboard-focusable action and tooltip."""
    size = scaled_px(16, cfg)
    button.setIcon(make_icon(name, color, size))
    button.setIconSize(QSize(size, size))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if label else
                              Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setText(label or tooltip.split("\n", 1)[0])
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip.split("\n", 1)[0])
    button.setFixedHeight(scaled_px(28, cfg))
    if label:
        button.setMinimumWidth(0)
        button.setMaximumWidth(16777215)
    else:
        button.setFixedWidth(scaled_px(28, cfg))
    button.setProperty("calcRole", "iconTextButton" if label else "iconButton")
