"""A visible grip for Qt's existing panel dragging and collapse behavior."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QSplitter, QSplitterHandle


class _PanelHandle(QSplitterHandle):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.ButtonText))
        cx, cy = self.width() / 2, self.height() / 2
        for offset in (-4, 0, 4):
            x, y = (cx, cy + offset) if self.orientation() == Qt.Orientation.Horizontal else (cx + offset, cy)
            painter.drawEllipse(QPointF(x, y), 1, 1)
        painter.end()


class PanelSplitter(QSplitter):
    def createHandle(self):
        return _PanelHandle(self.orientation(), self)
