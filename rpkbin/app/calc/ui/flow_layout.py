"""Responsive flow layout for wrapping toolbar buttons and form controls cleanly."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLabel, QLayout, QLayoutItem, QSizePolicy


class FlowLayout(QLayout):
    """Wrap child widgets across rows when available width narrows.

    Optionally designates one or more widgets to expand and consume leftover
    horizontal space when space permits (keeping narrow wrapping intact).
    """

    def __init__(
        self,
        parent=None,
        *,
        margin: int = 0,
        h_spacing: int = 4,
        v_spacing: int = 4,
        auto_height: bool = False,
        single_row_max_w: int = 0,
    ):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._stretch_widgets: set[int] = set()
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._auto_height = auto_height
        self._single_row_max_w = single_row_max_w
        self._last_min_w: int | None = None
        self.setContentsMargins(margin, margin, margin, margin)
        if auto_height and parent is not None:
            policy = parent.sizePolicy()
            policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
            policy.setHeightForWidth(True)
            parent.setSizePolicy(policy)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def addWidget(self, widget, *, stretch: bool = False) -> None:  # type: ignore[override]
        if stretch:
            self._stretch_widgets.add(id(widget))
        super().addWidget(widget)

    def setHorizontalSpacing(self, spacing: int) -> None:
        self._h_spacing = spacing

    def setVerticalSpacing(self, spacing: int) -> None:
        self._v_spacing = spacing

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            w = item.widget()
            if w is not None:
                self._stretch_widgets.discard(id(w))
            return item
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), apply_geometry=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        total_h = self._do_layout(rect, apply_geometry=True)
        parent = self.parentWidget()
        if (
            self._auto_height
            and parent is not None
            and rect.width() > 0
            and (self._last_min_w != rect.width() or total_h != parent.minimumHeight())
        ):
            self._last_min_w = rect.width()
            if total_h != parent.minimumHeight():
                parent.setMinimumHeight(total_h)
                parent.updateGeometry()

    def sizeHint(self) -> QSize:
        width = 0
        height = 0
        visible_count = 0
        for item in self._items:
            if not self._is_item_visible(item):
                continue
            sz = self._item_pref_size(item)
            width += sz.width()
            height = max(height, sz.height())
            visible_count += 1
        if visible_count > 1:
            width += self._h_spacing * (visible_count - 1)
        margins = self.contentsMargins()
        size = QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())
        parent = self.parentWidget()
        if self._auto_height and parent is not None and parent.width() > 0:
            wrapped_h = self.heightForWidth(parent.width())
            if wrapped_h > size.height():
                size.setHeight(wrapped_h)
        return size

    def minimumSize(self) -> QSize:
        size = QSize(0, 0)
        for item in self._items:
            if not self._is_item_visible(item):
                continue
            item_min = item.minimumSize()
            if item_min.isEmpty():
                item_min = self._item_pref_size(item)
            size = size.expandedTo(item_min)
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        if self._single_row_max_w > 0:
            single_w = self.sizeHint().width()
            if single_w <= self._single_row_max_w:
                size.setWidth(max(size.width(), single_w))
        parent = self.parentWidget()
        if self._auto_height and parent is not None and parent.width() > 0:
            wrapped_h = self.heightForWidth(parent.width())
            if wrapped_h > size.height():
                size.setHeight(wrapped_h)
        return size

    def _is_item_visible(self, item: QLayoutItem) -> bool:
        w = item.widget()
        if w is None:
            return not item.isEmpty()
        parent = self.parentWidget()
        return w.isVisibleTo(parent) if parent is not None else not w.isHidden()

    def _item_pref_size(self, item: QLayoutItem) -> QSize:
        w = item.widget()
        hint = item.sizeHint()
        if w is not None and id(w) in self._stretch_widgets and not isinstance(w, QLabel):
            if w.minimumWidth() == 0 and w.maximumHeight() > 0:
                return QSize(min(hint.width(), 140), hint.height())
            min_w = w.minimumWidth() or min(80, hint.width())
            return QSize(max(min_w, min(hint.width(), 140)), hint.height())
        if w is not None and w.minimumWidth() > 0:
            hint = QSize(max(hint.width(), w.minimumWidth()), hint.height())
        if w is not None and w.minimumHeight() > 0:
            hint = QSize(hint.width(), max(hint.height(), w.minimumHeight()))
        return hint

    def _do_layout(self, rect: QRect, *, apply_geometry: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        avail_w = max(0, effective.width())
        rows: list[list[tuple[QLayoutItem, QSize]]] = []
        current_row: list[tuple[QLayoutItem, QSize]] = []
        row_x = 0

        for item in self._items:
            if not self._is_item_visible(item):
                continue
            sz = self._item_pref_size(item)
            step_w = min(sz.width(), avail_w) if avail_w > 0 else sz.width()
            needed = step_w if not current_row else row_x + self._h_spacing + step_w
            if current_row and avail_w > 0 and needed > avail_w:
                rows.append(current_row)
                current_row = [(item, QSize(step_w, sz.height()))]
                row_x = step_w
            else:
                row_x = needed
                current_row.append((item, QSize(step_w, sz.height())))
        if current_row:
            rows.append(current_row)

        y = effective.y()
        for r_idx, row_items in enumerate(rows):
            if r_idx > 0:
                y += self._v_spacing
            row_h = max(sz.height() for _, sz in row_items)
            used_w = sum(sz.width() for _, sz in row_items) + max(0, len(row_items) - 1) * self._h_spacing
            extra_w = max(0, avail_w - used_w) if avail_w > 0 else 0
            stretch_indices = [
                idx for idx, (it, _) in enumerate(row_items)
                if it.widget() is not None and id(it.widget()) in self._stretch_widgets
            ]
            per_stretch = extra_w // len(stretch_indices) if stretch_indices else 0
            rem = extra_w % len(stretch_indices) if stretch_indices else 0

            x = effective.x()
            for idx, (item, sz) in enumerate(row_items):
                w = sz.width()
                if idx in stretch_indices:
                    pos_idx = stretch_indices.index(idx)
                    w += per_stretch + (1 if pos_idx < rem else 0)
                    max_w = item.maximumSize().width()
                    if max_w < 16777215:
                        w = min(w, max_w)
                if apply_geometry:
                    item_y = y + max(0, (row_h - sz.height()) // 2)
                    item.setGeometry(QRect(QPoint(x, item_y), QSize(w, sz.height())))
                x += w + self._h_spacing
            y += row_h

        total_h = (y - effective.y()) + margins.top() + margins.bottom()
        return total_h
